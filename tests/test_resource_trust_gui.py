# SPDX-License-Identifier: MPL-2.0
"""Observable resource review and unresolved editing without external file access."""

import json
from pathlib import Path

import pytest
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QDialog

from etlayout.examples import make_example
from etlayout.gui.resources import ResourceReviewDialog, open_reviewed_project
from etlayout.io.project import physics_hash, save_project
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


@pytest.fixture(scope="module")
def resolved_reference(tmp_path_factory):
    from etlayout.simulation.runner import run_project

    project = make_example("E1", tmp_path_factory.mktemp("resource-reference"))
    return project, run_project(project)


@pytest.mark.parametrize("physical_edit", [False, True], ids=["notes-only", "physical-setting"])
def test_review_authorizes_current_edits_against_held_archive(
    qtbot, tmp_path, monkeypatch, resolved_reference, physical_edit
):
    from etlayout.gui.main_window import MainWindow

    reference, original_result = resolved_reference
    path = tmp_path / "container" / "review.etlayout"
    save_project(reference, path, original_result, embed_layout=False, history=[original_result])
    inspection = inspect_project(path)
    original_bytes = inspection.archive_data
    project = inspection.unresolved_project()
    window = MainWindow(project, recover=False, database_path=tmp_path / "materials.sqlite3")
    qtbot.addWidget(window, before_close_func=lambda widget: setattr(widget, "dirty", False))
    window.autosave_timer.stop()
    window.recovery_path = tmp_path / "owned-recovery.etl"
    window.path = path
    errors = []
    monkeypatch.setattr(window, "error", lambda title, error: errors.append((title, str(error))))
    monkeypatch.setattr(window, "confirm_discard", lambda: pytest.fail("Resource review must not reopen the document"))
    window.before_edit()
    window.project.notes = "Unsaved notes survive authorization"
    if physical_edit:
        window.project.layers[0].thickness *= 1.2
    window.changed(geometry=False)
    edited = window.project.to_dict()
    undo = list(window.undo_history)
    path.write_bytes(b"The selected container changed after metadata inspection")

    def authorize(dialog):
        assert dialog.inspection.archive_data is original_bytes
        assert dialog.inspection.project.notes == edited["notes"]
        dialog.table.item(0, 0).setCheckState(Qt.CheckState.Checked)
        dialog.action = "authorize"
        return QDialog.DialogCode.Accepted

    monkeypatch.setattr(ResourceReviewDialog, "exec", authorize)
    window.review_project_resources()
    qtbot.waitUntil(lambda: window.prepare_job is None, timeout=30000)
    assert errors == []
    assert window.project.notes == edited["notes"] and window.dirty
    assert window.project.layers[0].thickness == edited["layers"][0]["thickness"]
    assert window.path == path and window.undo_history == undo
    assert not getattr(window.project, "_resource_unresolved", False)
    assert window.geometry is not None
    assert any(run.configuration_hash == original_result.configuration_hash for run in window.run_history)
    if physical_edit:
        assert window.result is None
        assert physics_hash(window.project) != original_result.physics_hash
    else:
        assert window.result is not None
        assert window.result.configuration_hash == original_result.configuration_hash
        assert window.result.physics_hash == original_result.physics_hash
    monkeypatch.setattr(window, "confirm_discard", lambda *args, **kwargs: True)
    window.dirty = False
    window.close()


def test_cancel_same_document_review_keeps_unsaved_edits_and_context(qtbot, tmp_path, monkeypatch):
    from etlayout.gui.main_window import MainWindow

    inspection = inspect_project(external_metadata(tmp_path))
    project = inspection.unresolved_project()
    window = MainWindow(project, recover=False, database_path=tmp_path / "materials.sqlite3")
    qtbot.addWidget(window, before_close_func=lambda widget: setattr(widget, "dirty", False))
    window.autosave_timer.stop()
    window.recovery_path = tmp_path / "owned-recovery.etl"
    window.before_edit()
    project.notes = "Keep notes and this physical edit after cancelling review"
    project.layers[0].thickness *= 1.2
    window.changed(geometry=False)
    before, view, undo = project.to_dict(), window.capture_view_state(), list(window.undo_history)
    monkeypatch.setattr(window, "confirm_discard", lambda: pytest.fail("Review is not a project transition"))
    monkeypatch.setattr(ResourceReviewDialog, "exec", lambda dialog: QDialog.DialogCode.Rejected)
    window.review_project_resources()
    assert window.project is project and window.project.to_dict() == before and window.dirty
    assert window.project._resource_inspection is inspection and window.prepare_job is None
    assert window.capture_view_state() == view and window.undo_history == undo
    assert window.result is None and window.inspection_context is None
    monkeypatch.setattr(window, "confirm_discard", lambda *args, **kwargs: True)
    window.dirty = False
    window.close()
