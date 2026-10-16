# SPDX-License-Identifier: MPL-2.0
"""Deferred archive authority, real scoped exports and cancellable Qt workers."""
from __future__ import annotations

import json
import threading
import zipfile

import numpy as np
import pytest
from PySide6.QtCore import Qt, QTimer
from test_lazy_history_store import saved_history as saved_history

from etlayout.gui.export_dialog import ExportDialog
from etlayout.io.archive_v3 import ArchiveV3Reader
from etlayout.io.history_store import open_history_catalog
from etlayout.io.project import load_project, load_run_history
from etlayout.io.saving import SaveCancelled
from etlayout.io.sharing import ExportRequest, execute_export
from etlayout.models import SimulationError


def provider_for(store, calls):
    def provide():
        calls.append("archive")
        ids = tuple(record.run_id for record in store.records)
        owner = store.capture_history(ids)
        return owner.references[0], owner.references[1:], owner
    return provide


@pytest.fixture
def corrupt_unrelated(saved_history, tmp_path):
    source, _ = saved_history
    with ArchiveV3Reader(source) as reader:
        broken = reader._descriptor(reader.records[1]["id"])["fields"]["m"]["temperature"]["$ref"]
        member = reader.object_name(broken)
    target = tmp_path / "unrelated-corruption.etlayout"
    with zipfile.ZipFile(source) as archive, zipfile.ZipFile(target, "w") as output:
        for entry in archive.infolist():
            if entry.filename != member:
                output.writestr(entry, archive.read(entry))
    return target


@pytest.mark.parametrize("mode,formats", [("data", ("csv",)), ("figure", ("png",))])
def test_healthy_selected_export_does_not_acquire_corrupt_unrelated_history(qtbot, corrupt_unrelated, tmp_path, mode, formats):
    calls = []
    with open_history_catalog(corrupt_unrelated) as store, store.acquire(store.current_run_id) as current:
        dialog = ExportDialog(store.resolve_project(), current,
            initial=ExportRequest(mode=mode, layers=("m",), fields=("temperature",), formats=formats),
            archive_provider=provider_for(store, calls))
        qtbot.addWidget(dialog)
        directory = tmp_path / mode
        dialog.directory.setText(str(directory))
        dialog.review()
        assert calls == [] and dialog.reviewed is not None and dialog.export_button.isEnabled()
        dialog.perform_export()
        assert calls == [] and dialog.exported["mode"] == mode
        manifest = json.loads((directory / "selection.manifest.json").read_text())
        assert not manifest["contains_complete_experiment"] and "run history" in manifest["excluded"]
        assert (directory / ("selection." + formats[0])).is_file()


def test_archive_corruption_is_visible_only_after_explicit_archive_review(qtbot, corrupt_unrelated, tmp_path):
    calls = []
    with open_history_catalog(corrupt_unrelated) as store, store.acquire(store.current_run_id) as current:
        dialog = ExportDialog(store.resolve_project(), current,
            initial=ExportRequest(mode="data", layers=("m",), fields=("temperature",), formats=("csv",)),
            archive_provider=provider_for(store, calls))
        qtbot.addWidget(dialog)
        dialog.directory.setText(str(tmp_path / "output"))
        assert calls == []
        dialog.mode_combo.setCurrentIndex(dialog.mode_combo.findData("archive"))
        dialog.review()
        assert calls == ["archive"] and dialog.reviewed is None
        assert "unavailable" in dialog.preview.toPlainText().lower()
        assert not dialog.export_button.isEnabled() and dialog.archive_job is None


def test_dialog_reject_releases_its_preview_source_generation(qtbot, saved_history, tmp_path):
    source, _ = saved_history
    store = open_history_catalog(source)
    with store.acquire(store.current_run_id) as current:
        dialog = ExportDialog(store.resolve_project(), current,
            initial=ExportRequest(mode="archive", formats=("etl",)), archive_provider=provider_for(store, []))
        qtbot.addWidget(dialog)
        dialog.directory.setText(str(tmp_path / "output"))
        dialog.review()
        private = store._snapshot_path
        store.close()
        assert private.exists()
        dialog.reject()
        assert not private.parent.exists()
        dialog.release_archive_sources()


def test_complete_archive_worker_is_responsive_and_preserves_all_unbrowsed_runs(qtbot, saved_history, tmp_path, monkeypatch):
    import etlayout.io.sharing as sharing

    source, originals = saved_history
    store = open_history_catalog(source)
    entered, release = threading.Event(), threading.Event()
    thread_ids = []
    original_save = sharing.save_project
    def delayed(*args, **kwargs):
        thread_ids.append(threading.get_ident())
        entered.set()
        assert release.wait(10)
        return original_save(*args, **kwargs)
    monkeypatch.setattr(sharing, "save_project", delayed)
    with store.acquire(store.current_run_id) as current:
        dialog = ExportDialog(store.resolve_project(), current,
            initial=ExportRequest(mode="archive", formats=("etl",)), archive_provider=provider_for(store, []))
        qtbot.addWidget(dialog)
        dialog.directory.setText(str(tmp_path / "output"))
        dialog.review()
        ticks = []
        heartbeat = QTimer(dialog)
        heartbeat.setInterval(10)
        heartbeat.timeout.connect(lambda: ticks.append(1))
        heartbeat.start()
        dialog.perform_export()
        qtbot.waitUntil(entered.is_set, timeout=10000)
        private = store._snapshot_path
        store.close()
        assert private.exists() and dialog.archive_job is not None
        qtbot.wait(80)
        assert len(ticks) >= 3 and len(thread_ids) == 1
        assert thread_ids[0] != threading.get_ident()
        release.set()
        qtbot.waitUntil(lambda: dialog.archive_job is None, timeout=30000)
        heartbeat.stop()
        assert dialog.exported and not private.parent.exists()
        destination = tmp_path / "output" / "selection.etl"
        with zipfile.ZipFile(destination) as archive:
            assert json.loads(archive.read("project.json"))["schema_version"] == 3
        _, actual = load_project(destination)
        history = load_run_history(destination)
        assert len(history) == 2
        for result, expected in zip([actual, *history], originals, strict=True):
            np.testing.assert_array_equal(result.fields["m"]["temperature"], expected.fields["m"]["temperature"])


@pytest.mark.parametrize("closing", ["cancel", "close", "escape"])
def test_cancel_or_close_waits_for_worker_and_preserves_previous_files(qtbot, saved_history, tmp_path, monkeypatch, closing):
    import etlayout.io.sharing as sharing

    source, _ = saved_history
    store = open_history_catalog(source)
    entered, release = threading.Event(), threading.Event()
    real = sharing.save_project
    def delayed(*args, **kwargs):
        entered.set()
        assert release.wait(10)
        return real(*args, **kwargs)
    monkeypatch.setattr(sharing, "save_project", delayed)
    directory = tmp_path / "output"
    directory.mkdir()
    old_archive = source.read_bytes()
    old_manifest = b'{"mode":"archive","previous":true}'
    (directory / "selection.etl").write_bytes(old_archive)
    (directory / "selection.manifest.json").write_bytes(old_manifest)
    with store.acquire(store.current_run_id) as current:
        dialog = ExportDialog(store.resolve_project(), current,
            initial=ExportRequest(mode="archive", formats=("etl",)), archive_provider=provider_for(store, []))
        qtbot.addWidget(dialog)
        dialog.show()
        dialog.directory.setText(str(directory))
        dialog.overwrite.setChecked(True)
        dialog.review()
        dialog.perform_export()
        qtbot.waitUntil(entered.is_set, timeout=10000)
        store.close()
        private = store._snapshot_path
        if closing == "close":
            assert not dialog.close()
            assert dialog.isVisible() and dialog.archive_job is not None
        elif closing == "escape":
            qtbot.keyClick(dialog, Qt.Key.Key_Escape)
            assert dialog.isVisible() and dialog.archive_job is not None
        else:
            dialog.cancel_export.click()
        release.set()
        qtbot.waitUntil(lambda: dialog.archive_job is None, timeout=30000)
        assert dialog.exported is None
        assert (directory / "selection.etl").read_bytes() == old_archive
        assert (directory / "selection.manifest.json").read_bytes() == old_manifest
        assert not list(directory.glob(".etlayout-export-*")) and not private.parent.exists()
        if closing != "cancel":
            assert not dialog.isVisible()
        else:
            assert "cancelled" in dialog.preview.toPlainText().lower()
            dialog.reject()


def test_background_validation_failure_remains_visible_and_releases_sources(qtbot, saved_history, tmp_path, monkeypatch):
    import etlayout.io.sharing as sharing

    source, _ = saved_history
    store = open_history_catalog(source)
    def reject_source(*args, **kwargs):
        raise SimulationError("Stored source hash changed; reopen an intact archive")
    monkeypatch.setattr(sharing, "save_project", reject_source)
    with store.acquire(store.current_run_id) as current:
        dialog = ExportDialog(store.resolve_project(), current,
            initial=ExportRequest(mode="archive", formats=("etl",)), archive_provider=provider_for(store, []))
        qtbot.addWidget(dialog)
        dialog.directory.setText(str(tmp_path / "output"))
        dialog.review()
        dialog.perform_export()
        store.close()
        private = store._snapshot_path
        qtbot.waitUntil(lambda: dialog.archive_job is None, timeout=30000)
        assert "source hash changed" in dialog.preview.toPlainText().lower()
        assert dialog.exported is None and not dialog.export_button.isEnabled()
        assert not private.parent.exists()


def test_cancel_checkpoint_before_bundle_publication_keeps_durable_artifacts(saved_history, tmp_path):
    source, _ = saved_history
    project, result = load_project(source)
    project._archive_format = 3
    directory = tmp_path / "durable"
    request = ExportRequest(mode="archive", formats=("etl",))
    execute_export(result, directory, request, project=project)
    before = {path.name: path.read_bytes() for path in directory.iterdir()}
    cancel = threading.Event()
    def progress(fraction, message):
        if fraction >= .95:
            cancel.set()
    request = ExportRequest(mode="archive", formats=("etl",), overwrite=True)
    with pytest.raises(SaveCancelled):
        execute_export(result, directory, request, project=project, cancel=cancel.is_set, progress=progress)
    assert {path.name: path.read_bytes() for path in directory.iterdir()} == before


def test_cancel_before_worker_start_releases_queued_source_without_field_reads(qtbot, saved_history, tmp_path, monkeypatch):
    from etlayout.gui.archive_export import ArchiveExportJob
    from etlayout.io.saving import SaveSnapshot

    source, _ = saved_history
    store = open_history_catalog(source)
    project = store.resolve_project()
    owner = store.pin_generation()
    snapshot = SaveSnapshot.capture(project, owner.references[0], owner.references[1:], archive_format=3, source_lease=owner)
    private = store._snapshot_path
    store.close()
    array_reads = []
    real = owner.references[0].reader.read_array
    def observed(digest, **kwargs):
        array_reads.append(digest)
        return real(digest, **kwargs)
    monkeypatch.setattr(owner.references[0].reader, "read_array", observed)
    job = ArchiveExportJob(snapshot, tmp_path / "cancelled", ExportRequest(mode="archive", formats=("etl",)))
    outcomes = []
    job.cancelled.connect(outcomes.append)
    job.cancel()
    with qtbot.waitSignal(job.finished, timeout=10000):
        job.start()
    assert len(outcomes) == 1 and array_reads == []
    assert owner.closed and not private.parent.exists() and not (tmp_path / "cancelled").exists()
    job.deleteLater()
