# SPDX-License-Identifier: MPL-2.0
"""Recovery ownership and interrupted-write contracts, including real processes."""
from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

from etlayout.io.identity import document_id
from etlayout.io.project import configuration_hash, load_project, physics_hash, save_json, save_project
from etlayout.io.recovery import RecoveryStore
from etlayout.models import Project


@pytest.fixture
def stores(tmp_path):
    first, second = RecoveryStore(tmp_path), RecoveryStore(tmp_path)
    yield first, second
    first.close()
    second.close()


def test_document_identity_is_persisted_but_not_scientific(tmp_path):
    project = Project()
    before = configuration_hash(project), physics_hash(project)
    identity = document_id(project)
    for name, writer in [("project.etlayout", save_project), ("project.json", save_json)]:
        path = tmp_path / name
        writer(project, path)
        reopened, _ = load_project(path)
        assert document_id(reopened) == identity
        assert (configuration_hash(reopened), physics_hash(reopened)) == before
    assert document_id(Project()) != identity


def test_two_unsaved_and_same_document_sessions_do_not_delete_each_other(stores):
    first, second = stores
    project, other = Project(name="A"), Project(name="B")
    a = first.snapshot(project, revision=1)
    b = second.snapshot(other, revision=2)
    same = second.snapshot(project, revision=3)
    newer = first.snapshot(project, revision=4)
    assert len({a.snapshot, b.snapshot, same.snapshot, newer.snapshot}) == 4
    assert first.entries() == []  # All sessions are active.
    assert not first.cleanup(document_id(project), 1)
    assert not first.snapshot_path(a).exists()
    for entry in (b, same, newer):
        assert first.snapshot_path(entry).is_file()
    second.close()
    assert {e.snapshot_id for e in first.entries()} == {b.snapshot_id, same.snapshot_id}
    with pytest.raises(Exception, match="another"):
        first.cleanup(document_id(project), 100, session_id=second.session_id)


def test_corrupt_index_receipt_and_interrupted_index_are_independent(stores, monkeypatch):
    import etlayout.io.recovery as recovery

    first, second = stores
    old = first.snapshot(Project(), revision=1)
    (first.root / "index.json").write_text("{interrupted")
    (first.root / "broken.receipt.json").write_text("not json")
    assert first.entries(include_active=True) == [old]
    original = recovery.atomic_json
    def interrupted(path, payload):
        if path.name == "index.json":
            raise OSError("simulated interrupted index write")
        return original(path, payload)
    monkeypatch.setattr(recovery, "atomic_json", interrupted)
    with pytest.raises(OSError, match="interrupted"):
        first.snapshot(Project(name="new independently recoverable"), revision=2)
    assert first.snapshot_path(old).is_file()
    monkeypatch.setattr(recovery, "atomic_json", original)
    assert len(second.entries(include_active=True)) == 2
    assert json.loads((first.root / "index.json").read_text())["format"] == 1


def test_failed_snapshot_or_deletion_preserves_valid_recovery(stores, monkeypatch):
    first, _ = stores
    project = Project()
    old = first.snapshot(project, revision=2)
    def failing(*args, **kwargs):
        raise OSError("disk full")
    with pytest.raises(OSError):
        first.snapshot(project, revision=3, writer=failing)
    assert first.entries(include_active=True) == [old]
    unlink = Path.unlink
    def denied(path, *args, **kwargs):
        if path.name == old.snapshot:
            raise PermissionError("in use")
        return unlink(path, *args, **kwargs)
    monkeypatch.setattr(Path, "unlink", denied)
    assert first.cleanup(document_id(project), 2) == ["in use"]
    assert first.entries(include_active=True)[0].status == "deletion_failed"
    assert load_project(first.snapshot_path(old))[0].name == project.name


def test_legacy_shared_snapshot_import_is_once_and_old_project_still_opens(tmp_path):
    legacy = tmp_path / "recovery.etlayout"
    save_project(Project(name="older format"), legacy)
    store = RecoveryStore(tmp_path / "recovery")
    try:
        store.import_legacy(legacy)
        entry, = store.entries()
        assert load_project(store.snapshot_path(entry))[0].name == "older format"
        assert store.discard(entry) == []
        store.import_legacy(legacy)
        assert store.entries() == []  # Discarded old shared file cannot revive.
        assert legacy.exists()  # Migration never deletes an unowned old archive.
    finally:
        store.close()


def test_real_process_concurrent_autosaves_crash_and_active_owner(tmp_path):
    code = """
import sys,time
from pathlib import Path
from etlayout.io.recovery import RecoveryStore
from etlayout.models import Project
store=RecoveryStore(sys.argv[1])
project=Project(name=sys.argv[2])
for revision in range(3):
    store.snapshot(project,revision=revision)
Path(sys.argv[3]).write_text(store.session_id)
time.sleep(45)
"""
    processes = []
    try:
        for label in ("A", "B"):
            ready = tmp_path / (label + ".ready")
            processes.append(subprocess.Popen([sys.executable, "-c", code, str(tmp_path / "recovery"), label, str(ready)], env=os.environ.copy()))
        deadline = time.monotonic() + 20
        while not all((tmp_path / (label + ".ready")).exists() for label in ("A", "B")):
            assert all(p.poll() is None for p in processes)
            assert time.monotonic() < deadline
            time.sleep(.025)
        store = RecoveryStore(tmp_path / "recovery")
        try:
            assert len(store.entries(include_active=True)) == 2
            assert store.entries() == []
            processes[0].kill()
            processes[0].wait(timeout=10)
            recovered, = store.entries()
            assert recovered.name == "A"
            assert load_project(store.snapshot_path(recovered))[0].name == "A"
            assert processes[1].poll() is None
            assert store.discard(recovered) == []
            assert store.entries() == []
        finally:
            store.close()
    finally:
        for process in processes:
            if process.poll() is None:
                process.kill()
            process.wait(timeout=10)


def test_reopen_same_document_keeps_recovery_revision_monotonic(qtbot, tmp_path, recovery_widgets):
    from copy import deepcopy

    from etlayout.gui.main_window import MainWindow

    window = MainWindow(Project(), database_path=tmp_path / "materials.sqlite3", recover=False)
    window.autosave_timer.stop()
    recovery_widgets.append(window)
    store = RecoveryStore(tmp_path / "isolated-recovery")
    try:
        window.dirty = True
        old = store.snapshot(window.project, revision=window.document_revision)
        replacement = deepcopy(window.project)
        replacement._resource_history = ["consumed lightweight handoff"]
        window.set_project(replacement, history=[])
        assert not hasattr(window.project, "_resource_history") and window.run_history == []
        window.project.notes = "new editing visit"
        window.dirty = True
        store.snapshot(window.project, revision=window.document_revision)
        latest, = store.entries(include_active=True)
        assert latest.revision > old.revision
        assert load_project(store.snapshot_path(latest))[0].notes == "new editing visit"
    finally:
        store.close()
        window._dirty = False
        window.close()


@pytest.fixture
def recovery_widgets(qtbot):
    """Own windows explicitly so deferred Qt destruction finishes before qapp."""
    from PySide6.QtCore import QCoreApplication, QEvent
    from PySide6.QtWidgets import QApplication
    from shiboken6 import isValid

    widgets = []
    yield widgets
    for widget in reversed(widgets):
        if not isValid(widget):
            continue
        if hasattr(widget, "_dirty"):
            widget._dirty = False
        widget.close()
        widget.deleteLater()
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    QApplication.processEvents()


def test_recovery_discard_requires_confirmation(qtbot, stores, recovery_widgets):
    from gui_test_helpers import _answer_dialog

    from etlayout.gui.recovery import RecoveryDialog

    first, observer = stores
    entry = first.snapshot(Project(name="confirmed discard"), revision=1)
    first.close()
    dialog = RecoveryDialog(observer, lambda entry: True)
    recovery_widgets.append(dialog)
    dialog.show()
    assert not dialog.actions["Recover selected"].isEnabled()
    dialog.table.selectRow(0)
    assert dialog.actions["Recover selected"].isEnabled()
    assert not dialog.actions["Open original"].isEnabled()
    _answer_dialog("No")
    dialog.discard()
    assert observer.snapshot_path(entry).is_file() and dialog.table.rowCount() == 1
    _answer_dialog("Yes")
    dialog.discard()
    assert not observer.snapshot_path(entry).exists() and dialog.table.rowCount() == 0
    dialog.close()


@pytest.mark.parametrize("field,value", [
    ("document_id", 12), ("session_id", None), ("snapshot_id", []),
    ("saved_at", float("nan")), ("saved_at", float("inf")), ("saved_at", 1e300),
    ("saved_at", True), ("saved_at", 10**1000), ("revision", True), ("status", []),
    ("name", "\ud800"), ("original_path", "\udfff"),
])
def test_malformed_receipt_cannot_hide_an_intact_snapshot(stores, field, value):
    import uuid
    from dataclasses import asdict

    store, _ = stores
    intact = store.snapshot(Project(), revision=1)
    identity = str(uuid.uuid4())
    payload = {**asdict(intact), "snapshot_id": identity, "snapshot": identity + ".etlayout", field: value}
    (store.root / (identity + ".receipt.json")).write_text(json.dumps(payload), encoding="utf-8")
    assert store.entries(include_active=True) == [intact]
    assert store.issues


def test_failed_index_rebuild_still_exposes_valid_receipts(stores, monkeypatch):
    store, _ = stores
    intact = store.snapshot(Project(), revision=1)
    def denied(entries):
        raise PermissionError("index unavailable")
    monkeypatch.setattr(store, "_publish", denied)
    assert store.entries(include_active=True) == [intact]
    assert "independent snapshots" in store.last_error
    assert load_project(store.snapshot_path(intact))[0].name == "Untitled"


def test_interrupted_legacy_catalogue_does_not_revive_discarded_snapshot(tmp_path, monkeypatch):
    source = tmp_path / "recovery.etlayout"
    save_project(Project(name="legacy interrupted"), source)
    store = RecoveryStore(tmp_path / "recovery")
    try:
        publish = store._publish
        def interrupted(entries):
            raise OSError("interrupted after receipt")
        monkeypatch.setattr(store, "_publish", interrupted)
        with pytest.raises(OSError):
            store.import_legacy(source)
        assert not (store.root / "legacy-imported.json").exists()
        monkeypatch.setattr(store, "_publish", publish)
        store.import_legacy(source)
        entry, = store.entries()
        assert store.discard(entry) == []
        store.import_legacy(source)
        assert store.entries() == [] and source.exists()
    finally:
        store.close()


def test_unwritable_recovery_cache_does_not_prevent_manual_project_save(qtbot, tmp_path, monkeypatch, recovery_widgets):
    import etlayout.gui.main_window as gui

    def unavailable(*args, **kwargs):
        raise PermissionError("recovery directory unavailable")
    monkeypatch.setattr(gui, "RecoveryStore", unavailable)
    window = gui.MainWindow(Project(), database_path=tmp_path / "materials.sqlite3", recover=False)
    window.autosave_timer.stop()
    recovery_widgets.append(window)
    assert window.recovery_store is None
    assert "Recovery unavailable" in window.recovery_status.text()
    window.dirty = True
    window.autosave()
    assert "Recovery failed" in window.recovery_status.text()
    destination = tmp_path / "manual.etlayout"
    window.path = destination
    assert window.save()
    qtbot.waitUntil(lambda: not window.save_job and not window._save_queue, timeout=30000)
    assert load_project(destination)[0].name == window.project.name
    assert not window.dirty
    window._dirty = False
    window.close()


def test_legacy_document_identity_respects_local_path_case_semantics(tmp_path):
    from etlayout.io.identity import restore_document_id

    first, second = Project(), Project()
    restore_document_id(first, None, tmp_path / "Project.JSON")
    restore_document_id(second, None, tmp_path / "project.json")
    assert (document_id(first) == document_id(second)) == (os.name == "nt")


def test_metadata_only_and_unresolved_open_preserve_persisted_document_identity(tmp_path):
    from etlayout.io.resources import inspect_project
    from etlayout.models import LayoutSource

    project = Project(layout=LayoutSource(r"\\example.invalid\layouts\device.gds"))
    identity = document_id(project)
    path = tmp_path / "unresolved.json"
    save_json(project, path)
    inspection = inspect_project(path)
    assert document_id(inspection.project) == identity
    assert document_id(inspection.unresolved_project()) == identity


def test_recovery_original_availability_does_not_access_unc(qtbot, stores, monkeypatch, recovery_widgets):
    from etlayout.gui.recovery import RecoveryDialog

    first, observer = stores
    original = r"\\example.invalid\private\original.etlayout"
    first.snapshot(Project(), revision=1, original_path=original)
    first.close()
    accesses = []
    for name in ("exists", "is_file", "stat", "open"):
        original_method = getattr(Path, name)
        def probe(self, *args, _name=name, _method=original_method, **kwargs):
            if str(self).casefold().startswith(r"\\example.invalid".casefold()):
                accesses.append(_name)
                raise AssertionError("Original location was accessed without selection/authorization")
            return _method(self, *args, **kwargs)
        monkeypatch.setattr(Path, name, probe)
    dialog = RecoveryDialog(observer, lambda entry: True)
    recovery_widgets.append(dialog)
    dialog.table.selectRow(0)
    assert dialog.actions["Open original"].isEnabled()
    assert accesses == []
