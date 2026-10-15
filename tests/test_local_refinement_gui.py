# SPDX-License-Identifier: MPL-2.0
"""Observable review/apply/cancel behavior in the existing compact workspace."""
import os
from copy import deepcopy

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from gui_test_helpers import _idle
from gui_test_helpers import guided_window as guided_window
from gui_test_helpers import solved_e1 as solved_e1
from PySide6.QtCore import Qt, QTimer
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QPushButton
from shapely.geometry import box

from etlayout.gui.refinement import RefinementReviewDialog
from etlayout.io.project import configuration_hash
from etlayout.meshing.refinement import apply_refinement_plan, suggest_refinements


def test_review_preview_cancel_and_apply_share_the_document(guided_window, qtbot):
    window = guided_window
    guided = window.guided
    guided.navigate("mesh")
    before = configuration_hash(window.project)
    plan = suggest_refinements(window.project, geometry=window.geometry)
    previews = []
    dialog = RefinementReviewDialog(window.project, plan, window, geometry=window.geometry,
        apply=lambda p, selected: guided.document.transact("Applied local mesh sizing", lambda project:
                                 apply_refinement_plan(project, p, selected)),
        run=lambda *_args: None, preview=previews.append)
    qtbot.addWidget(dialog)
    dialog.show()
    dialog.preview()
    assert previews and not previews[0].is_empty
    assert configuration_hash(window.project) == before
    dialog.reject()
    assert configuration_hash(window.project) == before
    assert not window.dirty
    dialog.show()
    assert dialog.apply_button.isEnabled() and dialog.run_button.isEnabled()
    old_maximum = window.project.mesh.max_size
    QTest.mouseClick(dialog.apply_button, Qt.MouseButton.LeftButton)
    _idle(window, qtbot)
    assert configuration_hash(window.project) != before
    assert window.project.mesh.max_size == old_maximum
    assert window.project.mesh.refinements
    assert guided.document.revision == 1
    assert window.dirty
    window.undo()
    _idle(window, qtbot)
    assert configuration_hash(window.project) == before


def test_review_budgets_and_factor_errors_block_study(guided_window, qtbot):
    window = guided_window
    plan = suggest_refinements(window.project, geometry=window.geometry)
    dialog = RefinementReviewDialog(window.project, plan, window, geometry=window.geometry,
                                  apply=lambda *_args: None, run=lambda *_args: None, preview=lambda *_args: None)
    qtbot.addWidget(dialog)
    dialog.show()
    before = configuration_hash(window.project)
    dialog.elements.setValue(1)
    assert not dialog.apply_button.isEnabled() and not dialog.run_button.isEnabled()
    assert "exceeds budget" in dialog.status.text()
    dialog.elements.setValue(plan.max_elements)
    for factors in ("1,0.5,0.75", "1,nan,0.5", "1,-0.25,0.5", "1,0.75,inf,0.5"):
        dialog.factors.setText(factors)
        assert not dialog.run_button.isEnabled()
        assert dialog.apply_button.isEnabled()
    assert configuration_hash(window.project) == before
    dialog.reject()


def preview_items(window):
    return [item for item in window.view.scene().items() if item.data(0) == "local-refinement-preview"]


@pytest.mark.parametrize("exit_method", ["reject", "close", "accept"])
def test_actual_review_replaces_temporary_preview_and_cleans_every_exit(guided_window, solved_e1, qtbot, exit_method):
    window = guided_window
    window.show_result(deepcopy(solved_e1[1]))
    window.guided.navigate("mesh")
    _idle(window, qtbot)
    qtbot.wait(50)  # Complete the stage's deferred layout before recording the camera.
    window.view.show_region(box(20e-6, 2e-6, 40e-6, 8e-6), "Retained annotation")
    annotations = list(window.view.annotations)
    transform = window.view.transform()
    center = window.view.mapToScene(window.view.viewport().rect().center())
    result, history = window.result, tuple(window.run_history)
    context, project = window.inspection_context, configuration_hash(window.project)
    plan = suggest_refinements(window.project, window.result, geometry=window.geometry)
    observed = {}

    def operate():
        dialog = QApplication.activeModalWidget()
        try:
            observed["dialog"] = isinstance(dialog, RefinementReviewDialog)
            dialog.preview()
            observed["first_count"] = len(preview_items(window))
            dialog.preview()
            observed["second_count"] = len(preview_items(window))
            observed["annotations"] = window.view.annotations == annotations
            getattr(dialog, exit_method)()
        finally:
            if dialog and dialog.isVisible():
                dialog.reject()

    QTimer.singleShot(0, operate)
    window.guided.review_refinements({"plan": plan.to_dict(), "geometry": window.geometry})
    assert observed == {"dialog": True, "first_count": 1, "second_count": 1, "annotations": True}
    assert preview_items(window) == []
    assert window.view.annotations == annotations
    assert all(item.scene() is window.view.scene() for item in annotations)
    assert window.view.transform() == transform
    assert window.view.mapToScene(window.view.viewport().rect().center()) == center
    assert configuration_hash(window.project) == project
    assert window.result is result and tuple(window.run_history) == history
    assert window.inspection_context is context


@pytest.mark.parametrize("initialization", ["saved", "resume"])
def test_dc_local_study_retains_dormant_saved_state_after_analysis_switch(guided_window, qtbot, initialization):
    window = guided_window
    guided = window.guided
    window.project.transient.initialization = initialization
    window.project.transient.initial_state_digest = "a" * 64
    guided.analysis.setCurrentIndex(guided.analysis.findData("electrothermal_transient"))
    _idle(window, qtbot)
    guided.analysis.setCurrentIndex(guided.analysis.findData("electrical"))
    _idle(window, qtbot)
    assert not window.project.transient.enabled
    assert window.project.transient.initialization == initialization
    guided.navigate("mesh")
    before = configuration_hash(window.project)
    plan = suggest_refinements(window.project, geometry=window.geometry)
    observed = {}

    def operate():
        dialog = QApplication.activeModalWidget()
        try:
            observed["dialog"] = isinstance(dialog, RefinementReviewDialog)
            dialog.factors.setText("1,0.5")
            dialog.preview()
            observed["enabled"] = dialog.run_button.isEnabled()
            QTest.mouseClick(dialog.run_button, Qt.MouseButton.LeftButton)
            observed["started"] = window.job is not None
        finally:
            if dialog and dialog.isVisible():
                dialog.reject()

    QTimer.singleShot(0, operate)
    guided.review_refinements({"plan": plan.to_dict(), "geometry": window.geometry})
    assert observed == {"dialog": True, "enabled": True, "started": True}
    assert preview_items(window) == []
    _idle(window, qtbot)
    assert len(window.studies) == 2
    assert all(row.result and row.result.converged and not row.error for row in window.studies)
    for row in window.studies:
        assert row.metrics["effective_resistance"] == pytest.approx(.084, rel=1e-6)
        assert not row.result.configuration["transient"]["enabled"]
        assert row.result.configuration["transient"]["initialization"] == initialization
    assert configuration_hash(window.project) == before


def test_real_refinement_worker_errors_open_the_mesh_issue_context(guided_window, qtbot):
    window = guided_window
    guided = window.guided
    plan = suggest_refinements(window.project, geometry=window.geometry)
    plan.max_memory_bytes = 1
    cases = [("refinement-guidance", {"hotspot_layer": "missing-layer"}, "Hotspot layer"),
             ("local-refine", {"sizes": [1., .5], "local_plan": plan.to_dict()}, "exceeds budget")]
    for operation, argument, message in cases:
        guided.navigate("run")
        window.start_job(operation, argument)
        _idle(window, qtbot)
        assert message in guided.last_error
        assert guided.last_error_stage == "mesh"
        assert guided.drawer.currentIndex() == 0
        assert guided.issue_table.item(0, 2).text() == "mesh"
        assert message in guided.issue_table.item(0, 1).text()
        controls = guided.issue_table.cellWidget(0, 3)
        button = next(item for item in controls.findChildren(QPushButton) if item.text() == "Open setup")
        QTest.mouseClick(button, Qt.MouseButton.LeftButton)
        assert guided.stage == "mesh"
        assert message in guided.next_reason.text()
