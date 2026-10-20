# SPDX-License-Identifier: MPL-2.0
"""Immutable snapshots, atomic failure and real Qt worker responsiveness."""
from __future__ import annotations

import threading
import time
from pathlib import Path

import numpy as np
import pytest
from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QApplication, QFileDialog, QMessageBox

from etlayout.gui.main_window import MainWindow
from etlayout.io import project as archive_io
from etlayout.io.identity import document_id
from etlayout.io.saving import SaveOrder, SaveSnapshot, SaveSuperseded, freeze_result_arrays
from etlayout.models import PlanarMesh, Project, SimulationError, SimulationResult


def case():
    project = Project(name="snapshot before edit")
    mesh = PlanarMesh(np.array([[0., 0.], [1., 0.], [0., 1.]]), np.array([[0, 1, 2]]),
                      {"layer:m": np.array([0])})
    result = SimulationResult(mesh, fields={"m": {"temperature": np.array([300., 301., 302.])}},
        converged=True, configuration=project.to_dict(), configuration_hash=archive_io.configuration_hash(project))
    return project, result


def test_snapshot_isolates_mutable_aliases_and_shares_only_immutable_buffers():
    project, result = case()
    alias = result.fields["m"]["temperature"]
    alias.setflags(write=False)  # Its writable owner still exists: not safe to share.
    snapshot = SaveSnapshot.capture(project, result, [result], revision=7)
    alias.setflags(write=True)
    alias[:] = 900.
    project.name = "later"
    result.metrics["later"] = 42
    assert snapshot.project.name == "snapshot before edit"
    assert "later" not in snapshot.result.metrics
    assert snapshot.result.fields["m"]["temperature"].tolist() == [300., 301., 302.]
    assert snapshot.history[0] is snapshot.result
    assert snapshot.revision == 7 and snapshot.document_id == document_id(project)
    with pytest.raises(ValueError):
        snapshot.result.fields["m"]["temperature"].setflags(write=True)
    freeze_result_arrays(result)
    second = SaveSnapshot.capture(project, result)
    original = result.fields["m"]["temperature"]
    saved = second.result.fields["m"]["temperature"]
    assert np.shares_memory(original, saved)
    with pytest.warns(DeprecationWarning):
        original.shape = (3, 1)
    assert saved.shape == (3,)  # Independent array descriptors, same immutable bytes.


@pytest.mark.parametrize("fault", ["serialization", "verification", "replacement", "budget", "invalid", "cancel"])
def test_save_failures_keep_previous_archive(tmp_path, monkeypatch, fault):
    project, result = case()
    path = tmp_path / "durable.etlayout"
    archive_io.save_project(project, path, result)
    before = path.read_bytes()
    if fault == "serialization":
        monkeypatch.setattr(np, "savez_compressed", lambda *a, **k: (_ for _ in ()).throw(OSError("disk write")))
    elif fault == "verification":
        monkeypatch.setattr(archive_io, "inspect_archive_size", lambda *a, **k: (_ for _ in ()).throw(SimulationError("verify failure")))
    elif fault == "replacement":
        monkeypatch.setattr(archive_io.os, "replace", lambda *a: (_ for _ in ()).throw(PermissionError("destination busy")))
    elif fault == "budget":
        monkeypatch.setattr(archive_io, "MAX_ARCHIVE_BYTES", 1)
    elif fault == "invalid":
        result.fields["m"]["temperature"][0] = np.inf
    cancelled = threading.Event()
    def progress(fraction, message):
        if fault == "cancel" and fraction >= .2:
            cancelled.set()
    with pytest.raises((OSError, SimulationError)):
        archive_io.save_project(project, path, result, progress=progress, cancel=cancelled.is_set)
    assert path.read_bytes() == before
    assert not list(tmp_path.glob("*.tmp"))
    monkeypatch.undo()
    assert archive_io.load_project(path)[1].fields["m"]["temperature"].tolist() == [300., 301., 302.]


def test_late_worker_cannot_replace_newer_or_externally_changed_destination(tmp_path):
    path = tmp_path / "durable.etlayout"
    project = Project(name="first")
    archive_io.save_project(project, path)
    expected = SaveOrder.signature(path)
    older, newer = SaveOrder.reserve(path), SaveOrder.reserve(path)
    project.name = "newer"
    archive_io.save_project(project, path, commit=lambda temporary, target: SaveOrder.commit(newer, expected, temporary, target))
    newer_bytes = path.read_bytes()
    with pytest.raises(SaveSuperseded, match="newer"):
        archive_io.save_project(Project(name="older"), path,
            commit=lambda temporary, target: SaveOrder.commit(older, expected, temporary, target))
    assert path.read_bytes() == newer_bytes
    ticket = SaveOrder.reserve(path)
    with pytest.raises(SaveSuperseded, match="changed"):
        archive_io.save_project(Project(), path,
            commit=lambda temporary, target: SaveOrder.commit(ticket, expected, temporary, target))
    assert path.read_bytes() == newer_bytes


def test_queued_snapshot_cannot_replace_archive_changed_before_worker_start(tmp_path, qtbot):
    from etlayout.gui.saving import SaveJob, SaveRequest

    path = tmp_path / "durable.etlayout"
    archive_io.save_project(Project(name="original"), path)
    snapshot = SaveSnapshot.capture(Project(name="queued stale revision"), revision=2)
    request = SaveRequest(snapshot, path, "document epoch", 1, ticket=SaveOrder.reserve(path))
    archive_io.save_project(Project(name="newer external revision"), path)
    newer_bytes = path.read_bytes()
    cancelled, saved = [], []
    worker = SaveJob(request)
    worker.cancelled.connect(lambda request, message: cancelled.append(message))
    worker.saved.connect(lambda *args: saved.append(args))
    worker.run()
    assert len(cancelled) == 1 and "destination changed" in cancelled[0]
    assert saved == [] and path.read_bytes() == newer_bytes
    assert archive_io.load_project(path)[0].name == "newer external revision"


@pytest.fixture
def saving_window(qtbot, tmp_path):
    window = MainWindow(Project(name="first revision"), database_path=tmp_path / "materials.sqlite3", recover=False)
    window.recovery_path = tmp_path / "recovery.etlayout"
    window.autosave_timer.stop()
    window.errors = []
    window.error = lambda title, message: window.errors.append((title, str(message)))
    window.show()
    yield window
    window.cancel_saving()
    qtbot.waitUntil(lambda: window.save_job is None, timeout=30000)
    window._dirty = False
    if window.inspection_context:
        window.inspection_context["dirty"] = False
    window.close()
    from PySide6.QtCore import QCoreApplication, QEvent
    from PySide6.QtWidgets import QApplication
    window.deleteLater()
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    QApplication.processEvents()


def test_gui_worker_is_responsive_and_new_edits_remain_dirty(saving_window, qtbot, tmp_path, monkeypatch):
    import etlayout.gui.saving as gui_saving

    window = saving_window
    entered, release = threading.Event(), threading.Event()
    real_writer = gui_saving.save_project
    workers = []
    def delayed(*args, **kwargs):
        workers.append(threading.get_ident())
        entered.set()
        assert release.wait(10)
        return real_writer(*args, **kwargs)
    monkeypatch.setattr(gui_saving, "save_project", delayed)
    ticks = []
    timer = QTimer(window)
    timer.setInterval(10)
    timer.timeout.connect(lambda: ticks.append(time.monotonic()))
    timer.start()
    window.path = tmp_path / "saved.etlayout"
    window.dirty = True
    assert window.save()
    qtbot.waitUntil(entered.is_set)
    assert window.save()
    assert window._save_queue == []  # Repeating the same revision does not recompress it.
    window.project.name = "edited during compression"
    window.dirty = True
    qtbot.wait(150)
    assert len(ticks) >= 5
    release.set()
    qtbot.waitUntil(lambda: window.save_job is None, timeout=30000)
    timer.stop()
    assert workers and workers[0] != threading.get_ident()
    assert window.dirty and window.project.name == "edited during compression"
    assert archive_io.load_project(window.path)[0].name == "first revision"
    assert window.save()
    qtbot.waitUntil(lambda: window.save_job is None, timeout=30000)
    assert not window.dirty
    assert archive_io.load_project(window.path)[0].name == "edited during compression"
    assert not window.errors


def test_repeated_save_as_autosave_queue_and_owned_cleanup(saving_window, qtbot, tmp_path, monkeypatch):
    import etlayout.gui.saving as gui_saving

    window = saving_window
    real_writer = gui_saving.save_project
    entered, release = threading.Event(), threading.Event()
    count = []
    def delayed(*args, **kwargs):
        count.append(1)
        if len(count) == 1:
            entered.set()
            assert release.wait(10)
        return real_writer(*args, **kwargs)
    monkeypatch.setattr(gui_saving, "save_project", delayed)
    window.path = tmp_path / "first.etlayout"
    window.dirty = True
    assert window.save()
    qtbot.waitUntil(entered.is_set)
    window.project.notes = "latest generation"
    window.dirty = True
    assert window.save()
    destination = tmp_path / "save-as.etlayout"
    monkeypatch.setattr(QFileDialog, "getSaveFileName", lambda *a, **k: (str(destination), ""))
    assert window.save_as()
    window.autosave()
    assert len(window._save_queue) == 2
    release.set()
    qtbot.waitUntil(lambda: window.save_job is None and not window._save_queue, timeout=30000)
    assert window.path == destination and not window.dirty
    assert archive_io.load_project(destination)[0].notes == "latest generation"
    assert window.recovery_path.is_file()  # Recovery completed after durable save; not lost by stale cleanup.
    assert window.save()
    qtbot.waitUntil(lambda: window.save_job is None and not window._save_queue, timeout=30000)
    assert not window.recovery_path.exists()
    assert not window.errors


def test_save_before_transition_waits_and_cancellation_keeps_dirty(saving_window, qtbot, tmp_path, monkeypatch):
    window = saving_window
    window.path = tmp_path / "saved.etlayout"
    window.dirty = True
    transitions = []
    monkeypatch.setattr(QMessageBox, "question", lambda *a, **k: QMessageBox.StandardButton.Save)
    assert not window.confirm_discard(lambda: transitions.append("continued"))
    assert transitions == []
    qtbot.waitUntil(lambda: transitions == ["continued"], timeout=30000)
    assert window.path.is_file() and not window.dirty
    window.dirty = True
    assert window.save()
    window.cancel_saving()
    qtbot.waitUntil(lambda: window.save_job is None, timeout=30000)
    # Cancellation can race the final atomic commit; either an explicitly saved
    # revision or the previous intact archive is permissible, never partial data.
    assert archive_io.load_project(window.path)[0].name == "first revision"


def test_receiver_freezes_result_inside_study_but_preserves_frozen_domain_objects():
    from dataclasses import dataclass

    from etlayout.gui.jobs import _freeze_received_results

    @dataclass(frozen=True)
    class FrozenEnvelope:
        label: str
        result: object
    _, result = case()
    envelope = FrozenEnvelope("stable domain identity", result)
    graph = {"study": (envelope,)}
    _freeze_received_results(graph)
    assert graph["study"][0] is envelope
    with pytest.raises(ValueError):
        result.fields["m"]["temperature"][0] = 900.



def test_close_cancels_worker_then_preserves_unsaved_recovery(saving_window, qtbot, tmp_path, monkeypatch):
    import etlayout.gui.saving as gui_saving

    window = saving_window
    window.path = tmp_path / "existing.etlayout"
    archive_io.save_project(window.project, window.path)
    durable = window.path.read_bytes()
    window.dirty = True
    store = window._recovery_owner()
    prior = store.snapshot(window.project, revision=window.document_revision)
    entered, release = threading.Event(), threading.Event()
    real_writer = gui_saving.save_project
    def delayed(*args, **kwargs):
        entered.set()
        assert release.wait(10)
        return real_writer(*args, **kwargs)
    monkeypatch.setattr(gui_saving, "save_project", delayed)
    questions = []
    def answer(parent, title, *args, **kwargs):
        questions.append(title)
        return QMessageBox.StandardButton.Yes if title == "Project save in progress" else QMessageBox.StandardButton.Cancel
    monkeypatch.setattr(QMessageBox, "question", answer)
    assert window.save()
    qtbot.waitUntil(entered.is_set)
    assert not window.close()
    assert window.save_job is not None and window.isVisible()
    release.set()
    qtbot.waitUntil(lambda: window.save_job is None and "Unsaved project" in questions, timeout=30000)
    assert window.isVisible() and window.dirty
    assert window.path.read_bytes() == durable
    assert store.snapshot_path(prior).is_file()


def test_snapshot_does_not_copy_removed_history_or_runtime_resource_inspection():
    from etlayout.io.resources import ResourceAccessError

    class RuntimeHandoff(list):
        def __deepcopy__(self, memo):
            raise AssertionError("Runtime/removed history was copied into a persisted snapshot")
    project, result = case()
    project._resource_history = RuntimeHandoff([result])
    project._resource_inspection = RuntimeHandoff(["not persisted"])
    snapshot = SaveSnapshot.capture(project, result, history=[result])
    assert not hasattr(snapshot.project, "_resource_history")
    assert not hasattr(snapshot.project, "_resource_inspection")
    assert snapshot.history[0] is snapshot.result
    assert snapshot.document_id == document_id(project)
    assert archive_io.configuration_hash(snapshot.project) == archive_io.configuration_hash(project)
    project._resource_unresolved = True
    with pytest.raises(ResourceAccessError, match="unresolved"):
        SaveSnapshot.capture(project, result)


def test_source_changed_after_snapshot_cannot_silently_rebind_archive(tmp_path):
    from etlayout.examples import make_example

    project = make_example("E1", tmp_path / "source")
    source = Path(project.layout.path)
    project.layout.sha256 = archive_io.file_hash(source)
    target = tmp_path / "durable.etlayout"
    archive_io.save_project(project, target)
    durable = target.read_bytes()
    snapshot = SaveSnapshot.capture(project, revision=1)
    source.write_bytes(source.read_bytes() + b"\x00\x00")
    with pytest.raises(SimulationError, match="no longer matches the save snapshot"):
        archive_io.save_project(snapshot.project, target)
    assert target.read_bytes() == durable
    assert archive_io.load_project(target)[0].layout.sha256 == project.layout.sha256


@pytest.fixture(scope="module")
def historical_e1(tmp_path_factory):
    from etlayout.examples import make_example
    from etlayout.simulation.runner import run_project

    project = make_example("E1", tmp_path_factory.mktemp("history-save-E1"))
    result = run_project(project)
    assert result.converged
    return result


def _inspect_synthetic_history(window, qtbot, historical):
    window.inspect_result(historical)
    qtbot.waitUntil(lambda: not window.prepare_job and not window.hierarchy_jobs, timeout=30000)
    QApplication.processEvents()
    assert window.inspection_context is not None
    return (window.inspection_context, window.project, window.result, window.path,
            window.capture_view_state())


def _assert_historical_context(window, before):
    context, project, result, path, view = before
    assert window.inspection_context is context
    assert window.project is project and window.result is result and window.path is path
    assert window.capture_view_state() == view


@pytest.mark.parametrize("decision", ["cancel", "save_as_cancel"])
def test_cancelled_navigation_preserves_historical_context(saving_window, qtbot, monkeypatch, decision, historical_e1):
    window = saving_window
    window.dirty = True
    before = _inspect_synthetic_history(window, qtbot, historical_e1)
    choice = QMessageBox.StandardButton.Cancel if decision == "cancel" else QMessageBox.StandardButton.Save
    monkeypatch.setattr(QMessageBox, "question", lambda *a, **k: choice)
    monkeypatch.setattr(QFileDialog, "getSaveFileName", lambda *a, **k: ("", ""))
    transitions = []
    assert not window.confirm_discard(lambda: transitions.append("continued"))
    _assert_historical_context(window, before)
    assert window.inspection_context["dirty"] and transitions == []
    assert window.save_job is None and window._save_queue == []


@pytest.mark.parametrize("fails", [False, True], ids=["durable", "write_failure"])
def test_save_before_navigation_preserves_history_until_durable(saving_window, qtbot, tmp_path, monkeypatch, fails, historical_e1):
    import etlayout.gui.saving as gui_saving

    window = saving_window
    window.path = tmp_path / "working.etlayout"
    archive_io.save_project(window.project, window.path)
    durable = window.path.read_bytes()
    window.project.notes = "working edit to persist"
    window.dirty = True
    working = window.project
    before = _inspect_synthetic_history(window, qtbot, historical_e1)
    monkeypatch.setattr(QMessageBox, "question", lambda *a, **k: QMessageBox.StandardButton.Save)
    if fails:
        monkeypatch.setattr(gui_saving, "save_project", lambda *a, **k: (_ for _ in ()).throw(OSError("injected save failure")))
    transitions = []
    assert not window.confirm_discard(lambda: transitions.append(window.inspection_context))
    _assert_historical_context(window, before)
    assert transitions == []
    qtbot.waitUntil(lambda: window.save_job is None and not window._save_queue, timeout=30000)
    if not fails:
        qtbot.waitUntil(lambda: bool(transitions), timeout=30000)
    _assert_historical_context(window, before)
    assert window.inspection_context["project"] is working
    if fails:
        assert transitions == [] and window.inspection_context["dirty"]
        assert window.inspection_context["path"].read_bytes() == durable
        assert "injected save failure" in window.errors[-1][1]
    else:
        assert transitions == [before[0]] and not window.inspection_context["dirty"]
        loaded, _ = archive_io.load_project(window.inspection_context["path"])
        assert loaded.name == working.name and loaded.notes == "working edit to persist"
        assert not window.errors


def test_gui_saves_opened_json_as_portable_archive_without_replacing_metadata(saving_window, qtbot, tmp_path, monkeypatch):
    from etlayout.examples import make_example

    window = saving_window
    project = make_example("E1", tmp_path / "json-layout")
    source = tmp_path / "metadata.json"
    archive_io.save_json(project, source)
    original = source.read_bytes()
    window.open_project(source)
    qtbot.waitUntil(lambda: not window.history_opener.jobs and not window.prepare_job and not window.hierarchy_jobs, timeout=30000)
    assert window.path == source and window.project.name == project.name
    window.project.notes = "edited through the standard GUI workflow"
    window.dirty = True
    destination = source.with_suffix(".etlayout")
    defaults = []
    def choose(parent, title, suggested, *args, **kwargs):
        defaults.append(Path(suggested))
        return str(destination), ""
    monkeypatch.setattr(QFileDialog, "getSaveFileName", choose)
    assert window.save()
    qtbot.waitUntil(lambda: not window.save_job and not window._save_queue, timeout=30000)
    assert source.read_bytes() == original
    assert defaults == [destination] and window.path == destination and not window.dirty
    loaded, _ = archive_io.load_project(destination)
    assert loaded.notes == window.project.notes and Path(loaded.layout.path).is_file()
    metadata, _ = archive_io.load_project(source)
    assert document_id(metadata) == document_id(loaded)
    assert metadata.notes != loaded.notes and metadata.name == project.name
    assert not window.errors


def test_archive_writer_rejects_json_without_replacing_metadata(tmp_path):
    source = tmp_path / "metadata.JSON"
    archive_io.save_json(Project(name="original metadata"), source)
    original = source.read_bytes()
    with pytest.raises(SimulationError, match="save_json"):
        archive_io.save_project(Project(name="archive must not replace metadata"), source)
    assert source.read_bytes() == original
    assert archive_io.load_project(source)[0].name == "original metadata"


@pytest.mark.parametrize("chosen", ["", "metadata.json", "portable"])
def test_save_as_cancel_json_rejection_and_default_suffix(saving_window, qtbot, tmp_path, monkeypatch, chosen):
    window = saving_window
    metadata = tmp_path / "metadata.json"
    archive_io.save_json(window.project, metadata)
    original = metadata.read_bytes()
    window.path = metadata
    window.dirty = True
    destination = str(tmp_path / chosen) if chosen else ""
    monkeypatch.setattr(QFileDialog, "getSaveFileName", lambda *a, **k: (destination, ""))
    accepted = window.save()
    if chosen == "portable":
        assert accepted
        qtbot.waitUntil(lambda: not window.save_job and not window._save_queue, timeout=30000)
        assert window.path == tmp_path / "portable.etlayout" and not window.dirty
        assert archive_io.load_project(window.path)[0].name == window.project.name
    else:
        assert not accepted and window.path == metadata and window.dirty
        assert window.save_job is None and window._save_queue == []
        assert bool(window.errors) == bool(chosen)
    assert metadata.read_bytes() == original
