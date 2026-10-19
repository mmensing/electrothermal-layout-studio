# SPDX-License-Identifier: MPL-2.0
"""Observable result interpretation, background inspection and frame identity."""
import os
from copy import deepcopy
from types import SimpleNamespace

import pytest
from gui_test_helpers import _answer_dialog, _fill, _idle
from gui_test_helpers import guided_window as guided_window
from gui_test_helpers import solved_e1 as solved_e1
from PySide6.QtCore import QPointF, Qt, QTimer
from solver_helpers import all_region, rectangle

from etlayout.gui.transient import TimeNavigation
from etlayout.models import ThermalBoundary, TransientSettings
from etlayout.solvers import solve


def use_solved(window, qtbot, solved_e1):
    project, result = deepcopy(solved_e1)
    window.set_project(project, result)
    _idle(window, qtbot)
    window.guided.navigate("results")
    return result


def test_background_same_support_readout_is_readonly_and_uses_real_process(guided_window, qtbot, solved_e1):
    window = guided_window
    result = use_solved(window, qtbot, solved_e1)
    before = window.project.to_dict(), window.mesh, window.dirty, tuple(window.run_history)
    window.probe(50e-6, 5e-6)
    beats = []
    timer = QTimer(window)
    timer.setInterval(10)
    timer.timeout.connect(lambda: beats.append(True))
    timer.start()
    window.inspect_engineering_support(2e-6)
    assert window.job.process.pid != os.getpid()
    _idle(window, qtbot)
    timer.stop()
    assert beats and window.result is result
    report = window.engineering_panel.support_report
    assert report["support_area_m2"] == pytest.approx(4e-12, rel=1e-12)
    assert report["view_context"] == window.engineering_context()
    assert (window.project.to_dict(), window.mesh, window.dirty, tuple(window.run_history)) == before
    assert window.engineering_panel.locate_button.isEnabled()
    window.guided.panels.set_visible("drawer", False)
    window.resize(1120, 760)
    qtbot.wait(50)
    assert window.height() == 760
    window.engineering_panel.use_probe.setChecked(True)
    window.engineering_panel.window_side.setText("invalid")
    window.engineering_panel.request_support()
    assert window.job is None and window.engineering_panel.support_error.text()
    window.engineering_panel.window_side.setText("2")
    window.load_difference_result(deepcopy(result))
    assert window.view_mode.currentData() == "difference"
    window.select_engineering_quantity("sheet_current")
    assert window.field_combo.currentData() == "sheet_current"
    assert window.view_mode.currentData() == "original" and window.view.result is result
    window.view.scale(20., 20.)
    window.view.centerOn(-10000., -10000.)
    window.locate_engineering_support(report)
    x, y = report["crowding"]["peak_xy"]
    assert window.view.viewport().rect().contains(window.view.mapFromScene(QPointF(x * 1e6, -y * 1e6)))
    assert window.project.to_dict() == before[0]
    # A new run with identical settings/time must not reuse an old report.
    window.show_result(deepcopy(result))
    assert not window.engineering_panel.locate_button.isEnabled()
    window.select_engineering_quantity("potential")
    assert "another recorded state" in window.engineering_panel.support_error.text()


def test_historical_support_inspection_preserves_working_setup(guided_window, qtbot, solved_e1):
    window = guided_window
    result = use_solved(window, qtbot, solved_e1)
    working = window.project, window.result
    window.inspect_result(deepcopy(result))
    _idle(window, qtbot)
    assert window.inspection_context and window.model_locked()
    window.inspect_engineering_support()
    _idle(window, qtbot)
    assert window.engineering_panel.support_report
    assert window.inspection_context["project"] is working[0]
    window.return_to_project()
    _idle(window, qtbot)
    assert (window.project, window.result) == working


@pytest.mark.parametrize("answer", ["Apply", "Discard", "Stay"])
def test_draft_decision_precedes_engineering_inspection(guided_window, qtbot, solved_e1, answer):
    window = guided_window
    use_solved(window, qtbot, solved_e1)
    window.guided.navigate("connections")
    window.guided.edit_model("terminals", "source")
    before = window.project.terminals[0].name
    _fill(window, "name", "pending source")
    editor = window.guided.editor
    _answer_dialog(answer)
    window.inspect_engineering_support()
    if answer == "Stay":
        assert window.job is None and window.engineering_panel.support_report is None
        assert window.guided.editor is editor and editor.has_changes()
        assert window.project.terminals[0].name == before
        window.guided.discard_draft()
    else:
        _idle(window, qtbot)
        assert window.engineering_panel.support_report
        assert window.project.terminals[0].name == ("pending source" if answer == "Apply" else before)
        assert window.guided.resolve_draft()


def test_peak_and_last_actions_show_actual_cooling_frames_and_run_peak(qtbot):
    project, mesh = rectangle(nx=3)
    project.solver.mode = "thermal"
    project.solver.initial_temperature = 310.
    project.materials["copper"].density = 2000.
    project.materials["copper"].specific_heat = 1000.
    project.thermal_boundaries = [ThermalBoundary("sink", "metal", value=1.)]
    all_region(mesh, "thermal", "sink")
    project.transient = TransientSettings(enabled=True, stop_time=.3, time_step=.1, output_interval=.1)
    result = solve(project, mesh)
    assert result.converged and result.transient.completed
    navigation = TimeNavigation()
    qtbot.addWidget(navigation)
    navigation.set_result(result)
    navigation.resize(430, 160)
    navigation.show()
    qtbot.wait(30)
    assert navigation.width() == 430
    navigation.snapshot_changed.connect(lambda index: navigation.set_frame_metrics(result.transient.snapshots[index].metrics))
    assert "Displayed 300 ms" in navigation.temperature_summary.text()
    assert "Run peak 310 K at 0 s" in navigation.temperature_summary.text()
    qtbot.mouseClick(navigation.peak_button, Qt.MouseButton.LeftButton)
    assert navigation.slider.value() == 0
    assert "Displayed 0 s: Tmax 310 K" in navigation.temperature_summary.text()
    qtbot.mouseClick(navigation.last_button, Qt.MouseButton.LeftButton)
    assert navigation.slider.value() == len(result.transient.times)-1
    assert navigation.displayed_metrics["temperature_max"] < 310.
    # Retained metadata may omit a peak spatial field: show this, never invent it.
    navigation.set_record(SimpleNamespace(times=result.transient.times[1:].tolist(), metrics=result.metrics))
    assert navigation.peak_button.text() == "Nearest to peak"
    assert "peak spatial field not retained" in navigation.temperature_summary.text()


def test_superseded_inspection_completion_cannot_replace_current_state(guided_window, qtbot, solved_e1):
    from etlayout.power.engineering import scoped_engineering
    window = guided_window
    result = use_solved(window, qtbot, solved_e1)
    report = scoped_engineering(result, "metal", view_context=window.engineering_context())
    window.show_result(deepcopy(result))
    window.operation, window.job_context = "engineering-support", None
    window.job_finished(report)
    assert window.engineering_panel.support_report is None
    assert "changed during inspection" in window.engineering_panel.support_error.text()
    window.clear_analysis()
    assert window.engineering_panel.report is None
    assert not window.engineering_panel.inspect_button.isEnabled()


def test_evidence_button_opens_real_unavailable_receipt_without_claiming_convergence(guided_window, qtbot, solved_e1):
    from PySide6.QtWidgets import QApplication, QLabel

    from etlayout.meshing.physical import local_mesh_verification
    from etlayout.simulation.sweep import SweepRow

    window = guided_window
    result = use_solved(window, qtbot, solved_e1)
    receipt = local_mesh_verification(window.project, [SweepRow(1., result)], None, [])
    assert receipt["status"] == "unavailable"
    result.analysis["local_mesh_verification"] = receipt
    window.render_result_metrics(result)
    window.show_analysis_page(window.results_page)
    window.engineering_panel.tabs.setCurrentIndex(0)
    seen = []
    def close_confidence():
        dialog = QApplication.activeModalWidget()
        assert dialog.objectName() == "local_mesh_confidence"
        assert any(receipt["reason"] == label.text() for label in dialog.findChildren(QLabel))
        seen.append(True)
        dialog.accept()
    QTimer.singleShot(0, close_confidence)
    qtbot.mouseClick(window.engineering_panel.mesh_details, Qt.MouseButton.LeftButton)
    assert seen


def test_on_demand_cooled_pulse_keeps_the_whole_run_temperature_failure(guided_window, qtbot, tmp_path, monkeypatch):
    from test_lazy_history_store import track_arrays

    from etlayout.examples import make_example
    from etlayout.io.history_store import open_history_catalog
    from etlayout.io.project import save_project
    from etlayout.models import TemperatureLimit
    from etlayout.simulation.runner import run_project

    project = make_example("E18", tmp_path / "pulsed-input")
    project.temperature_limits = [TemperatureLimit(maximum_temperature=310.)]
    result = run_project(project)
    assert result.converged and result.transient.completed
    report = result.metrics["thermal_limit_compliance"]
    assert report["status"] == "failed"
    assert result.metrics["temperature_max"] < 310.
    assert result.transient.snapshots[-1].metrics["thermal_limit_compliance"]["status"] == "passed"
    source = tmp_path / "cooled-failed-run.etlayout"
    save_project(project, source, result, archive_format=3)
    arrays = track_arrays(monkeypatch)
    with open_history_catalog(source) as catalog:
        detached = catalog.thermal_compliance(catalog.current_run_id)
        assert detached == report and arrays == []
        detached["limits"].clear()
        assert catalog.thermal_compliance(catalog.current_run_id) == report
        assert catalog.stats.resident_bytes == catalog.stats.pinned_bytes == 0
    window = guided_window
    working, loaded = window.load_project_data(source, lazy=True)
    window.set_project(working, loaded, source)
    qtbot.waitUntil(lambda: window.result is not None or bool(window.guided.last_error_detail), timeout=30000)
    assert window.result is not None, window.guided.last_error_detail
    qtbot.waitUntil(lambda: not window.history_workflow.loader.jobs)
    store = window.history_workflow.store
    assert store.current_run_id == window.history_workflow.record.run_id
    assert window.result.transient is None
    assert window.result.metrics["thermal_limit_compliance"]["status"] == "passed"
    def check_run_failure():
        axis = next(row for row in window.engineering_panel.report["axes"] if row["axis"] == "Temperature limits")
        assert axis["status"] == "failed" and axis["evidence"] == report
        assert "Maximum temperature exceeded" in window.result_status.text()
        assert "Whole-run peak" in window.key_metrics.text()
        assert window.engineering_panel.report["run_peak_temperature_K"] == result.metrics["transient_peak_temperature"]
    check_run_failure()
    window.time_navigation.slider.setValue(0)
    qtbot.waitUntil(lambda: not window.history_workflow.loader.jobs)
    assert window.result.metrics["time_s"] == 0.
    check_run_failure()
    qtbot.mouseClick(window.time_navigation.last_button, Qt.MouseButton.LeftButton)
    qtbot.waitUntil(lambda: not window.history_workflow.loader.jobs)
    assert window.result.metrics["time_s"] == result.transient.times[-1]
    check_run_failure()
    window.history_workflow.full("trace")
    qtbot.waitUntil(lambda: not window.history_workflow.loader.jobs)
    assert window.result.transient is not None
    check_run_failure()


def test_applying_a_physical_draft_cannot_inspect_an_invalidated_result(guided_window, qtbot, solved_e1):
    window = guided_window
    use_solved(window, qtbot, solved_e1)
    window.guided.navigate("connections")
    window.guided.edit_model("terminals", "drain")
    _fill(window, "value", "125 mV")
    _answer_dialog("Apply")
    window.inspect_engineering_support()
    _idle(window, qtbot)
    assert window.result is None and window.job is None
    assert window.engineering_panel.support_report is None
    assert window.project.terminals[1].value == .125
    assert "Run an analysis" in window.engineering_panel.support_error.text()


def test_engineering_labels_keep_literal_names_and_errors(qtbot, solved_e1):
    from etlayout.gui.engineering import EngineeringPanel
    from etlayout.power.engineering import engineering_summary, scoped_engineering

    panel = EngineeringPanel()
    qtbot.addWidget(panel)
    _, result = deepcopy(solved_e1)
    report = engineering_summary(result)
    report["family"] = "<b>family</b>"
    panel.set_report(report)
    support = scoped_engineering(result, "metal")
    support.update(layer_id="<b>layer</b>", region="<i>region</i>")
    support["crowding"]["nearby_boundaries"] = [{"kind": "interface_edge", "id": "<b>contact</b>"}]
    panel.set_support_report(support)
    panel.support_error.setText("<b>failure</b>")
    for label in (panel.heading, panel.support_description, panel.support_error, panel.crowding):
        assert label.textFormat() == Qt.TextFormat.PlainText
    assert "<b>family</b>" in panel.heading.text()
    assert "<b>layer</b> / <i>region</i>" in panel.support_description.text()
    assert "<b>contact</b>" in panel.crowding.text()
    assert panel.support_error.text() == "<b>failure</b>"
