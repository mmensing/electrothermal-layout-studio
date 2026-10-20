# SPDX-License-Identifier: MPL-2.0
"""Observable next-step guidance uses the existing document and study workflows."""
from copy import deepcopy

import pytest
from gui_test_helpers import _click, _idle
from gui_test_helpers import guided_window as guided_window
from gui_test_helpers import solved_e1 as solved_e1
from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QApplication, QDialog, QDialogButtonBox, QLineEdit, QPushButton

from etlayout.examples import make_example
from etlayout.io.project import configuration_hash
from etlayout.simulation.runner import run_project

pytestmark = pytest.mark.desktop


def _current(window, source):
    project, result = source
    window.set_project(deepcopy(project), result)
    assert window.result is result
    window.guided.navigate("results", force=True)
    return result


def test_successful_example_open_retires_empty_results_for_actionable_review(guided_window, qtbot):
    window = guided_window
    window.guided.navigate("results")
    window.load_example("E25")
    _idle(window, qtbot)
    assert window.project.name == "E25_stepped_width_series"
    assert window.guided.stage == "mesh"
    assert window.result is None and window.mesh is None and window.job is None
    assert "Check current inputs" in window.guided.description.text()
    assert window.guided.primary.text() == "Generate / update mesh"


def test_mesh_action_opens_existing_dialog_with_same_result_support(guided_window, qtbot, solved_e1):
    window = guided_window
    result = _current(window, solved_e1)
    _idle(window, qtbot)
    window.field_combo.setCurrentIndex(window.field_combo.findData("temperature"))
    before = window.capture_view_state()
    physics = configuration_hash(window.project)
    assert "Mesh: not established" in window.guided.result_evidence.text()
    assert window.guided.verify_mesh.isEnabled()
    observed = []

    def cancel():
        dialog = QApplication.activeModalWidget()
        assert isinstance(dialog, QDialog) and dialog.windowTitle() == "Mesh convergence study"
        observed.append(dialog.findChild(QLineEdit, "mesh_study_sizes").text())
        dialog.findChild(QDialogButtonBox).rejected.emit()
    QTimer.singleShot(0, cancel)
    _click(window, window.guided.verify_mesh)
    assert observed and len(observed[0].split(",")) == 3
    assert window.result is result and configuration_hash(window.project) == physics
    assert window.capture_view_state() == before and window.job is None
    assert "Mesh: not established" in window.guided.result_evidence.text()
    assert "map region or raw pixel is not automatically verified" in window.guided.verification_context.text()


def test_history_and_stale_physics_cannot_start_verification(guided_window, qtbot, solved_e1):
    window = guided_window
    result = _current(window, solved_e1)
    _idle(window, qtbot)
    window.project.layers[0].thickness *= 2
    window.guided.refresh()
    assert not window.guided.verify_mesh.isEnabled()
    assert "different physical inputs" in window.guided.verify_mesh.toolTip()
    assert window.guided.verify_result("mesh") is False
    assert window.job is None
    working = window.project
    window.inspect_result(result)
    _idle(window, qtbot)
    assert window.inspection_context and window.inspection_context["project"] is working
    assert not window.guided.verify_mesh.isEnabled()
    assert "historical runs are read-only" in window.guided.verify_mesh.toolTip()
    before = window.capture_view_state()
    assert window.guided.verify_result("mesh") is False
    assert window.result is result and window.capture_view_state() == before and window.job is None
    window.return_to_project()
    _idle(window, qtbot)
    assert window.project is working


def test_transient_time_action_uses_real_transient_configuration(guided_window, qtbot, tmp_path):
    window = guided_window
    project = make_example("E17", tmp_path / "thermal-rc")
    result = run_project(project)
    assert result.converged and result.transient is not None
    _current(window, (project, result))
    _idle(window, qtbot)
    assert window.guided.verify_time.isVisible() and window.guided.verify_time.isEnabled()
    observed = []

    def cancel():
        dialog = QApplication.activeModalWidget()
        assert dialog.windowTitle() == "Time-step convergence"
        steps = dialog.findChild(QLineEdit).text()
        observed.append(steps)
        dialog.findChild(QDialogButtonBox).rejected.emit()
    before = window.capture_view_state()
    QTimer.singleShot(0, cancel)
    _click(window, window.guided.verify_time)
    assert len(observed[0].split(",")) == 3
    assert window.result is result and window.job is None
    assert window.capture_view_state() == before


def test_reopening_model_browser_reuses_the_visible_widget(guided_window, qtbot):
    window = guided_window
    guided = window.guided
    before = configuration_hash(window.project)
    browser = window.design_browser
    for _ in range(2):
        guided.actions["view.browser"].trigger()
        _idle(window, qtbot)
        assert guided.canvas_tabs.currentWidget() is browser
        assert browser.isVisible()
        guided.canvas_tabs.setCurrentIndex(0)
    guided.navigate("layout")
    control = next(item for item in guided.page.findChildren(QPushButton)
                   if item.text() == "Browse hierarchy / instances")
    _click(window, control)
    assert guided.canvas_tabs.currentWidget() is browser
    assert sum(guided.canvas_tabs.widget(i) is browser for i in range(guided.canvas_tabs.count())) == 1
    assert configuration_hash(window.project) == before
