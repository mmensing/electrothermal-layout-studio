# SPDX-License-Identifier: MPL-2.0
"""Real saved fields exercise draft gates, stale reads and complete desktop saves."""
from __future__ import annotations

import threading
from copy import deepcopy
from pathlib import Path

import numpy as np
import pytest
from gui_test_helpers import _answer_dialog, _fill, _idle
from gui_test_helpers import guided_window as guided_window
from gui_test_helpers import solved_e1 as solved_e1
from PySide6.QtWidgets import QFileDialog

from etlayout.gui.history_workflow import is_record
from etlayout.io.project import load_project, load_run_history, save_project


def opened(window, qtbot, source):
    project, result = window.load_project_data(source, lazy=True)
    assert result is None and project._history_store.stats.resident_bytes == 0
    window.set_project(project, result, source)
    qtbot.waitUntil(lambda: window.result is not None and not window.history_workflow.loader.jobs, timeout=30000)
    _idle(window, qtbot)
    return window.history_workflow.store


@pytest.fixture
def history_archive(tmp_path, solved_e1):
    project, result = deepcopy(solved_e1)
    path = tmp_path / "recorded.etlayout"
    save_project(project, path, result, history=[result, result], archive_format=3)
    return path, result


def test_gui_catalog_inspection_return_and_partial_browse_save_keep_every_run(
        guided_window, qtbot, history_archive, tmp_path, monkeypatch):
    window = guided_window
    source, original = history_archive
    store = opened(window, qtbot, source)
    assert len(window.run_history) == 3 and all(is_record(item) for item in window.run_history)
    assert "cache" in window.history_panel.memory_summary.text()
    assert "Stored converged" in window.history_panel.list.item(1).text()
    working = window.project, window.result, window.capture_view_state()
    window.inspect_result(store.history[0])
    qtbot.waitUntil(lambda: window.inspection_context is not None and not window.history_workflow.loader.jobs)
    assert window.model_locked() and window.history_workflow.record.run_id == store.history[0].run_id
    window.return_to_project()
    _idle(window, qtbot)
    assert window.project is working[0] and window.result is working[1]
    assert window.capture_view_state()["transform"] == working[2]["transform"]
    destination = tmp_path / "complete-after-browse.etlayout"
    monkeypatch.setattr(QFileDialog, "getSaveFileName", lambda *a, **k: (str(destination), ""))
    assert window.save_as(archive_format=3)
    qtbot.waitUntil(lambda: window.save_job is None, timeout=30000)
    saved_project, saved_result = load_project(destination)
    history = load_run_history(destination)
    assert saved_project.name == window.project.name
    assert len(history) == 2  # Current is separate; unchanged saves do not grow history.
    for result in [saved_result, *history]:
        assert result.configuration_hash == original.configuration_hash
        for layer, fields in original.fields.items():
            for field, values in fields.items():
                np.testing.assert_array_equal(result.fields[layer][field], values)
    assert not window.dirty


@pytest.mark.parametrize("answer", ["Apply", "Discard", "Stay"])
def test_real_draft_decision_precedes_lazy_read(guided_window, qtbot, history_archive, answer):
    window, guided = guided_window, guided_window.guided
    store = opened(window, qtbot, history_archive[0])
    guided.navigate("connections")
    guided.edit_model("terminals", "source")
    original_name = window.project.terminals[0].name
    _fill(window, "name", "draft terminal")
    editor = guided.editor
    before = (window.project, window.result, window.mesh, guided.stage, window.view.transform())
    generation = window.history_workflow.loader.generation
    _answer_dialog(answer)
    accepted = window.inspect_result(store.history[0])
    if answer == "Stay":
        assert accepted is False
        assert window.history_workflow.loader.generation == generation
        assert not window.history_workflow.loader.jobs
        assert (window.project, window.result, window.mesh, guided.stage, window.view.transform()) == before
        assert guided.editor is editor and editor.has_changes() and window.inspection_context is None
        guided.discard_draft()
    else:
        qtbot.waitUntil(lambda: window.inspection_context is not None and not window.history_workflow.loader.jobs)
        _idle(window, qtbot)
        window.return_to_project()
        _idle(window, qtbot)
        assert window.project.terminals[0].name == ("draft terminal" if answer == "Apply" else original_name)
        assert guided.resolve_draft()
        if guided.editor:
            assert not guided.editor.has_changes()


def test_rapid_real_reads_reject_late_completion_and_cancel_preserves_view(
        guided_window, qtbot, history_archive, monkeypatch):
    window = guided_window
    store = opened(window, qtbot, history_archive[0])
    entered, release = threading.Event(), threading.Event()
    first, second = store.history
    acquire = store.acquire
    def delayed(run_id, **options):
        if run_id == first.run_id:
            entered.set()
            if not release.wait(10):
                raise RuntimeError("test read was not released")
        return acquire(run_id, **options)
    monkeypatch.setattr(store, "acquire", delayed)
    try:
        window.inspect_result(first)
        qtbot.waitUntil(entered.is_set)
        window.inspect_result(second)
        qtbot.waitUntil(lambda: window.history_workflow.record.run_id == second.run_id)
        displayed = window.result
        release.set()
        qtbot.waitUntil(lambda: not window.history_workflow.loader.jobs)
        assert window.result is displayed and window.history_workflow.record.run_id == second.run_id
        window.history_workflow.cancel()
        assert window.result is displayed
        assert store.stats.pinned_bytes > 0
    finally:
        release.set()
        qtbot.waitUntil(lambda: not window.history_workflow.loader.jobs, timeout=30000)


def test_history_read_failure_is_visible_and_does_not_replace_canvas(
        guided_window, qtbot, history_archive, monkeypatch):
    window = guided_window
    store = opened(window, qtbot, history_archive[0])
    before = window.project, window.result, window.mesh, window.capture_view_state()
    def failed(*args, **kwargs):
        raise OSError("simulated archive device read failure")
    monkeypatch.setattr(store, "acquire", failed)
    window.inspect_result(store.history[0])
    qtbot.waitUntil(lambda: not window.history_workflow.loader.jobs)
    assert (window.project, window.result, window.mesh) == before[:3]
    assert window.capture_view_state()["transform"] == before[3]["transform"]
    assert "device read failure" in window.guided.last_error_detail
    assert window.guided.drawer.currentIndex() == 0


def test_render_failure_rolls_back_context_and_keeps_the_prior_pin(
        guided_window, qtbot, history_archive, monkeypatch):
    window = guided_window
    store = opened(window, qtbot, history_archive[0])
    before = window.project, window.result, window.mesh, window.capture_view_state()
    pinned = store.stats.pinned_bytes
    render = window.render_result
    calls = []
    def fail_once(result):
        calls.append(result)
        if len(calls) == 1:
            raise RuntimeError("display preparation failed")
        return render(result)
    monkeypatch.setattr(window, "render_result", fail_once)
    window.inspect_result(store.history[0])
    qtbot.waitUntil(lambda: not window.history_workflow.loader.jobs)
    _idle(window, qtbot)
    assert (window.project, window.result, window.mesh) == before[:3]
    assert window.inspection_context is None
    assert window.capture_view_state()["transform"] == before[3]["transform"]
    assert store.stats.pinned_bytes == pinned
    assert "display preparation failed" in window.guided.last_error_detail


def test_complete_export_uses_stored_refs_without_loading_unselected_fields(
        guided_window, qtbot, history_archive, tmp_path):
    from etlayout.io.sharing import ExportRequest, execute_export

    window = guided_window
    store = opened(window, qtbot, history_archive[0])
    misses = store.stats.misses
    archived, retained, owner = window.history_workflow.export_inputs(window.result, window.run_history)
    try:
        manifest = execute_export(archived, tmp_path / "complete-export", ExportRequest(mode="archive", formats=("etl",)),
                                  project=window.project, history=retained)
        assert manifest["confidential"] and manifest["history_runs"] == 2
        _, result = load_project(tmp_path / "complete-export" / manifest["files"][0])
        assert result.configuration_hash == window.result.configuration_hash
        assert len(load_run_history(tmp_path / "complete-export" / manifest["files"][0])) == 2
        assert store.stats.misses == misses  # Writer validates refs independently; it never populates the GUI cache.
    finally:
        owner.close()


def test_standard_open_runs_catalog_worker_and_keeps_the_loaded_project(guided_window, qtbot, history_archive):
    window = guided_window
    window.history_lazy_enabled = True
    source = history_archive[0]
    window.open_project(source)
    qtbot.waitUntil(lambda: window.history_workflow.store is not None and window.result is not None
                    and not window.history_opener.jobs and not window.history_workflow.loader.jobs, timeout=30000)
    assert window.path == source and len(window.run_history) == 3
    assert window.history_workflow.store.stats.pinned_bytes > 0
    assert window.project.name == window.history_workflow.store.project_metadata.name


def test_explicitly_removed_current_is_not_resurrected_during_save(guided_window, qtbot, history_archive, tmp_path):
    window = guided_window
    store = opened(window, qtbot, history_archive[0])
    window.history_workflow.new_result()
    window.result = None
    window.run_history = list(store.history)
    result, history, owner = window.history_workflow.save_inputs(window.project, None, window.run_history)
    try:
        assert result is None and len(history) == 2
        destination = tmp_path / "removed-current.etlayout"
        save_project(window.project, destination, result, history=history, archive_format=3)
        assert load_project(destination)[1] is None
        assert len(load_run_history(destination)) == 2
    finally:
        owner.close()


def test_navigation_cancels_pending_history_without_a_late_results_jump(
        guided_window, qtbot, history_archive, monkeypatch):
    window = guided_window
    store = opened(window, qtbot, history_archive[0])
    entered, release = threading.Event(), threading.Event()
    acquire = store.acquire
    def delayed(run_id, **options):
        entered.set()
        if not release.wait(10):
            raise RuntimeError("test read was not released")
        return acquire(run_id, **options)
    monkeypatch.setattr(store, "acquire", delayed)
    previous = window.project, window.result
    try:
        window.inspect_result(store.history[0])
        qtbot.waitUntil(entered.is_set)
        assert window.guided.navigate("layers") is not False
        release.set()
        qtbot.waitUntil(lambda: not window.history_workflow.loader.jobs, timeout=30000)
        assert window.guided.stage == "layers" and window.inspection_context is None
        assert (window.project, window.result) == previous
    finally:
        release.set()
        qtbot.waitUntil(lambda: not window.history_workflow.loader.jobs, timeout=30000)


def test_explicit_full_trace_scan_preserves_the_selected_stored_time(guided_window, qtbot, tmp_path):
    project, transient = load_project(Path(__file__).parent / "fixtures" / "legacy_transient_e18.etl")
    source = tmp_path / "transient.etlayout"
    save_project(project, source, transient, archive_format=3)
    window = guided_window
    opened(window, qtbot, source)
    assert window.result.transient is None
    window.time_navigation.slider.setValue(0)
    qtbot.waitUntil(lambda: not window.history_workflow.loader.jobs)
    assert window.result.metrics["time_s"] == transient.transient.times[0]
    window.history_workflow.full("trace")
    qtbot.waitUntil(lambda: not window.history_workflow.loader.jobs)
    assert window.result.transient is not None
    assert window.time_navigation.slider.value() == 0
    assert window.time_history.context()["selected_time"] == transient.transient.times[0]


def test_reviewed_resources_keep_the_edited_working_document(guided_window, qtbot, tmp_path, solved_e1):
    from etlayout.gui.history_opening import catalog_inspection
    from etlayout.io.history_store import open_history_catalog
    from etlayout.io.resources import ExternalResourcePolicy

    project, result = deepcopy(solved_e1)
    folder = tmp_path / "external-project"
    folder.mkdir()
    source = folder / "project.etlayout"
    save_project(project, source, result, archive_format=3, embed_layout=False)
    policy = ExternalResourcePolicy(allow_outside_root=True)
    store = open_history_catalog(source, external_policy=policy)
    try:
        inspection = catalog_inspection(store)
    finally:
        store.close()
    window = guided_window
    working = inspection.unresolved_project()
    window.set_project(working, None, source)
    working.name = "edited metadata retained while reviewing resources"
    window.dirty = True
    undo, redo = window.undo_history, window.redo_history
    undo.append(deepcopy(working))
    redo.append(deepcopy(working))
    working.layout.instance_path = [7, 3]
    working.layout.dbu_um = .002
    from etlayout.simulation.sweep import SweepRow
    retained_studies = [SweepRow(.02, result)]
    window.studies = retained_studies
    view = window.capture_view_state()
    window._catalog_revision = window.document_revision
    window._catalog_review_restore = working
    window.history_opener.submit(source, window.document_epoch, policy=policy, inspection=inspection)
    qtbot.waitUntil(lambda: window.history_workflow.store is not None and not window.history_opener.jobs
                    and not window.history_workflow.loader.jobs, timeout=30000)
    assert window.project is working and window.project.name.startswith("edited metadata")
    assert window.dirty and window.undo_history is undo and len(undo) == 1
    assert window.redo_history is redo and len(redo) == 1
    assert window.project.layout.instance_path == [7, 3] and window.project.layout.dbu_um == .002
    assert window.studies is retained_studies
    assert not getattr(window.project, "_resource_unresolved", False)
    assert window.capture_view_state()["transform"] == view["transform"]


def test_catalog_commit_waits_for_a_save_started_after_open_request(
        guided_window, qtbot, history_archive, tmp_path, monkeypatch):
    from etlayout.gui import history_opening, saving

    window = guided_window
    window.history_lazy_enabled = True
    original = window.project
    copied, permit_catalog = threading.Event(), threading.Event()
    saving_started, permit_save = threading.Event(), threading.Event()
    open_catalog, save = history_opening.open_history_catalog, saving.save_project
    def delayed_catalog(*args, **options):
        store = open_catalog(*args, **options)
        copied.set()
        if not permit_catalog.wait(10):
            store.close()
            raise RuntimeError("catalog test was not released")
        return store
    def delayed_save(*args, **options):
        saving_started.set()
        if not permit_save.wait(10):
            raise RuntimeError("save test was not released")
        return save(*args, **options)
    monkeypatch.setattr(history_opening, "open_history_catalog", delayed_catalog)
    monkeypatch.setattr(saving, "save_project", delayed_save)
    destination = tmp_path / "working-copy.etlayout"
    try:
        window.open_project(history_archive[0])
        qtbot.waitUntil(copied.is_set)
        assert window._queue_save(destination, archive_format=3)
        qtbot.waitUntil(saving_started.is_set)
        permit_catalog.set()
        qtbot.waitUntil(lambda: window._pending_catalog is not None)
        qtbot.wait(60)
        assert window.project is original and window.save_job is not None
        permit_save.set()
        qtbot.waitUntil(lambda: window.project is not original and window.save_job is None
                        and not window.history_opener.jobs and not window.history_workflow.loader.jobs, timeout=30000)
        assert load_project(destination)[0].name == original.name
        assert window.path == history_archive[0]
    finally:
        permit_catalog.set()
        permit_save.set()
        qtbot.waitUntil(lambda: window.save_job is None and not window.history_opener.jobs
                        and not window.history_workflow.loader.jobs, timeout=30000)
