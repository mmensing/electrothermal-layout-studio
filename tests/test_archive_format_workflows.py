# SPDX-License-Identifier: MPL-2.0
"""Format choice stays explicit across Qt saves, recovery and CLI conversion."""
from __future__ import annotations

import json
import zipfile

import pytest
from PySide6.QtWidgets import QFileDialog

from etlayout.cli.main import main
from etlayout.gui.main_window import MainWindow
from etlayout.gui.saving import SaveJob, SaveRequest
from etlayout.io.project import load_project, save_project
from etlayout.io.recovery import RecoveryStore
from etlayout.io.saving import SaveSnapshot
from etlayout.models import Project, SimulationError


def schema(path):
    with zipfile.ZipFile(path) as archive:
        return json.loads(archive.read("project.json"))["schema_version"]


@pytest.mark.parametrize("archive_format", [2, 3])
def test_explicit_cli_conversion_is_verified_new_destination(tmp_path, capsys, archive_format):
    source, destination = tmp_path / "source.etl", tmp_path / "converted.etlayout"
    save_project(Project(name="portable draft"), source)
    original = source.read_bytes()
    assert main(["archive-convert", str(source), str(destination), "--archive-format", str(archive_format)]) == 0
    capsys.readouterr()
    assert schema(destination) == archive_format
    assert load_project(destination)[0].name == "portable draft"
    assert source.read_bytes() == original
    assert main(["archive-convert", str(source), str(source), "--archive-format", str(archive_format)]) != 0
    capsys.readouterr()
    assert source.read_bytes() == original


@pytest.mark.parametrize("invalid", [True, 0, 1, 4, "3"])
def test_snapshot_rejects_ambiguous_format(invalid):
    with pytest.raises(SimulationError, match="format"):
        SaveSnapshot.capture(Project(), archive_format=invalid)


def test_qt_format_choice_and_subsequent_save_preserve_container_version(tmp_path, qtbot, monkeypatch):
    window = MainWindow(Project(name="format choice"), database_path=tmp_path / "materials.sqlite3", recover=False)
    window.autosave_timer.stop()
    errors = []
    window.error = lambda title, error: errors.append((title, str(error)))
    destination = tmp_path / "chosen.etlayout"
    monkeypatch.setattr(QFileDialog, "getSaveFileName", lambda *args, **kwargs: (str(destination), ""))
    try:
        assert window.save_as(archive_format=3)
        qtbot.waitUntil(lambda: window.save_job is None, timeout=30000)
        assert errors == [] and schema(destination) == 3
        assert window.project._archive_format == 3
        window.project.name = "updated metadata"
        window.document_revision += 1
        window.dirty = True
        assert window.save()
        qtbot.waitUntil(lambda: window.save_job is None, timeout=30000)
        assert errors == [] and schema(destination) == 3
        assert load_project(destination)[0].name == "updated metadata"
        legacy = tmp_path / "legacy.etlayout"
        monkeypatch.setattr(QFileDialog, "getSaveFileName", lambda *args, **kwargs: (str(legacy), ""))
        assert window.save_as(archive_format=2)
        qtbot.waitUntil(lambda: window.save_job is None, timeout=30000)
        assert schema(legacy) == 2 and schema(destination) == 3
    finally:
        window.cancel_saving()
        qtbot.waitUntil(lambda: window.save_job is None, timeout=30000)
        window._dirty = False
        window.close()
        window.deleteLater()


def test_recovery_remains_schema_two_for_opted_in_document(tmp_path):
    store = RecoveryStore(tmp_path / "recovery")
    try:
        snapshot = SaveSnapshot.capture(Project(name="opted in"), revision=1, archive_format=3)
        request = SaveRequest(snapshot, tmp_path / "unused.etlayout", "epoch", 1, recovery_store=store)
        job = SaveJob(request)
        entries, failures = [], []
        job.saved.connect(lambda request, entry: entries.append(entry))
        job.failed.connect(lambda request, error: failures.append(error))
        job.run()
        assert failures == [] and len(entries) == 1
        assert schema(store.snapshot_path(entries[0])) == 2
    finally:
        store.close()
