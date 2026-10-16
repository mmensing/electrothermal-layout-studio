# SPDX-License-Identifier: MPL-2.0
"""Observable resource review and unresolved editing without external file access."""

import json
from pathlib import Path

import pytest
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QDialog

from etlayout.examples import make_example
from etlayout.gui.resources import ResourceReviewDialog, open_reviewed_project
from etlayout.io.project import physics_hash
from etlayout.io.resources import inspect_project

pytestmark = pytest.mark.desktop


def external_metadata(tmp_path):
    project = make_example("E1", tmp_path / "example")
    project.layout.path = r"\\example.invalid\layouts\device.gds"
    project.layout.sha256 = ""
    path = tmp_path / "external.json"
    path.write_text(json.dumps(project.to_dict()), encoding="utf-8")
    return path


def test_dialog_grants_are_per_resource_with_separate_network_opt_in(qtbot, tmp_path):
    inspection = inspect_project(external_metadata(tmp_path))
    dialog = ResourceReviewDialog(inspection)
    qtbot.addWidget(dialog)
    assert not dialog.network.isChecked()
    assert dialog.table.item(0, 0).checkState() == Qt.CheckState.Unchecked
    dialog.table.item(0, 0).setCheckState(Qt.CheckState.Checked)
    policy = dialog.policy()
    assert policy.approved_resources == frozenset({"layout"})
    assert not policy.allow_network and not policy.allow_outside_root
    dialog.network.setChecked(True)
    assert dialog.policy().allow_network


def test_unresolved_open_preserves_metadata_and_blocks_preparation(qtbot, tmp_path, monkeypatch):
    from etlayout.gui.main_window import MainWindow

    path = external_metadata(tmp_path)

    def leave_unresolved(dialog):
        dialog.action = "unresolved"
        return QDialog.DialogCode.Accepted

    monkeypatch.setattr(ResourceReviewDialog, "exec", leave_unresolved)
    opened = open_reviewed_project(path)
    project, result = opened
    assert result is None and project._resource_unresolved
    assert project.layers and project.terminals
    original_hash = physics_hash(project)
    window = MainWindow(project, recover=False, database_path=tmp_path / "materials.sqlite3")
    qtbot.addWidget(window)
    window.autosave_timer.stop()
    window.recovery_path = tmp_path / "owned-recovery.etl"
    assert window.prepare_job is None
    assert not window.guided.assessment.ready
    assert not window.run_action.isEnabled()
    window.before_edit()
    window.project.notes = "A review note without layout authorization"
    window.changed(geometry=False)
    assert window.project.notes and physics_hash(window.project) == original_hash
    assert window.prepare_job is None
    window.dirty = False
    window.close()


def test_cancelled_review_keeps_existing_document(qtbot, tmp_path, monkeypatch):
    from etlayout.gui.main_window import MainWindow

    path = external_metadata(tmp_path)
    monkeypatch.setattr(ResourceReviewDialog, "exec", lambda dialog: QDialog.DialogCode.Rejected)
    assert open_reviewed_project(path) is None
    window = MainWindow(recover=False, database_path=tmp_path / "materials.sqlite3")
    qtbot.addWidget(window)
    window.autosave_timer.stop()
    window.recovery_path = tmp_path / "owned-recovery.etl"
    original = window.project
    monkeypatch.setattr(window, "error", lambda *args: None)
    window.open_project(path)
    assert window.project is original and not window.dirty
    window.close()


def test_relink_is_explicit_and_does_not_mutate_original_metadata(tmp_path):
    path = external_metadata(tmp_path)
    original = path.read_bytes()
    replacement = make_example("E2", tmp_path / "replacement")
    inspection = inspect_project(path)
    relinked = inspection.relink(replacement.layout.path)
    from etlayout.io.project import resolve_project
    from etlayout.io.resources import ExternalResourcePolicy

    project, result = resolve_project(
        relinked, external_policy=ExternalResourcePolicy(approved_resources=frozenset({"layout"}))
    )
    assert Path(project.layout.path) == Path(replacement.layout.path)
    assert result is None and project.layout.sha256
    assert path.read_bytes() == original
