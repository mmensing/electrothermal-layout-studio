# SPDX-License-Identifier: MPL-2.0
"""Native Qt controls use actual physical calculations and typed persistence."""
import json
from typing import Any

import pytest
from test_power_optimization import example

from etlayout.examples import make_example
from etlayout.gui.forms import ModelDialog
from etlayout.gui.power_forms import ModelCollectionDialog, StructuredValueDialog
from etlayout.gui.power_workbench import PowerWorkbench
from etlayout.io.project import load_project, save_project
from etlayout.models import (
    MeasurementSpec,
    ObjectiveSpec,
    PhysicalPort,
    PortLanding,
    SupportSelector,
    Terminal,
)
from etlayout.simulation.execution import ExecutionSettings


def test_nested_port_editor_preserves_typed_landings_and_json_rejects_nonfinite(qtbot, tmp_path):
    collection = ModelCollectionDialog(PhysicalPort, [PhysicalPort("source", [PortLanding("landing", .2, .5)])])
    qtbot.addWidget(collection)
    collection.accept()
    assert collection.entries[0].landings[0].resistance == .2
    duplicate = ModelCollectionDialog(PhysicalPort, [PhysicalPort("same"), PhysicalPort("same")])
    qtbot.addWidget(duplicate)
    duplicate.accept()
    assert "unique" in duplicate.error.text() and duplicate.result() == 0
    editor = StructuredValueDialog({"width": 2e-6}, dict[str, Any], "parameters")
    qtbot.addWidget(editor)
    editor.editor.setPlainText('{"width":1e999}')
    editor.accept()
    assert editor.result() == 0 and editor.error.text()
    editor.editor.setPlainText('{"width":4e-6,"profile":[1,2]}')
    editor.accept()
    assert editor.value == {"width": 4e-6, "profile": [1, 2]}
    project = make_example("E1", tmp_path / "input")
    project.power.ports = collection.entries
    save_project(project, tmp_path / "nested.etl")
    reopened, _ = load_project(tmp_path / "nested.etl")
    assert reopened.power.ports == collection.entries


def test_measurement_units_and_probe_role_are_editable_without_code(qtbot):
    measurement = ModelDialog(MeasurementSpec("neck", selector=SupportSelector(layers=["metal"])))
    qtbot.addWidget(measurement)
    measurement.widgets["averaging_length"].setText("2 µm")
    measurement.accept()
    assert measurement.model.averaging_length == pytest.approx(2e-6)
    terminal = ModelDialog(Terminal("sense", "metal", kind="probe"))
    qtbot.addWidget(terminal)
    assert terminal.widgets["kind"].currentData() == "probe"
    assert not terminal.widgets["value"].isEnabled()


def test_workbench_real_worker_analysis_and_multiport(qtbot, tmp_path):
    project = make_example("E1", tmp_path / "input")
    project.power.ports = [PhysicalPort("positive", [PortLanding("drain")], value=.02),
                           PhysicalPort("negative", [PortLanding("source")], kind="ground")]
    project.power.measurements = [MeasurementSpec("loss", "joule_density", SupportSelector(layers=["metal"]), "integral")]
    window = PowerWorkbench(project, execution=ExecutionSettings(cpu_threads=1, study_workers=1))
    qtbot.addWidget(window)
    window.show()
    window.open_multiport()
    assert window.multiport_workflow.isVisible()
    assert window.multiport_workflow.selected_ports() == ["positive", "negative"]
    assert window.multiport_workflow.reference.currentData() == "negative"
    window.start("power", {})
    assert window.job is not None and window.cancel.isEnabled()
    qtbot.waitUntil(lambda: window.job is None, timeout=90000)
    assert window.result is not None and window.result.converged, window.status.text()
    record = window.analysis_tables["measurements"].records[0]
    assert record["value"] == pytest.approx(.02**2 / .084, rel=1e-9)
    window.extract_multiport()
    qtbot.waitUntil(lambda: window.job is None, timeout=90000)
    assert window.multiport_report is not None, window.status.text()
    assert window.matrix.rowCount() == 2
    assert window.multiport_report["resistance_matrix"][0][0] == pytest.approx(.084)
    assert "fidelity verified per port" in window.multiport_workflow.preview_status.text()
    from etlayout.power.multiport import export_multiport
    exported = export_multiport(window.multiport_report, tmp_path / "reviewed.cir", "spice")
    assert window.multiport_workflow.preview.toPlainText() == exported.read_text(encoding="utf-8")
    assert "p1 = positive" in window.multiport_workflow.preview.toPlainText()
    window.analysis_map.show_field(window.result, "metal", "potential")
    assert window.analysis_map.overlay_item is not None
    window.invalidate_result()
    assert window.analysis_map.overlay_item is None and window.matrix.rowCount() == 0
    assert not window.multiport_workflow.preview.toPlainText()
    window.reference_temperature.setText("310")
    window.accept()
    assert window.project.power.thermal_reference_temperature == 310


def test_history_validation_and_readable_setup(qtbot, tmp_path):
    project = example(tmp_path)
    window = PowerWorkbench(project)
    qtbot.addWidget(window)
    assert "\n" not in window.setup_table.item(0, 1).text()
    malformed = tmp_path / "malformed.json"
    malformed.write_text('{"rows": [7], "experiment_id": "width"}', encoding="utf-8")
    with pytest.raises(ValueError, match="candidate rows"):
        window.load_history(malformed)
    assert window.history is None


def test_workbench_real_routing_preview_and_verified_history(qtbot, tmp_path):
    window = PowerWorkbench(example(tmp_path), execution=ExecutionSettings(cpu_threads=1, study_workers=1))
    qtbot.addWidget(window)
    window.routing_output.setText(str(tmp_path / "candidate.oas"))
    window.routing_parameters.setText('{"bus.width":4e-6}')
    window.generate_candidate()
    qtbot.waitUntil(lambda: window.job is None, timeout=90000)
    assert window.candidate and window.candidate["report"]["valid"], window.status.text()
    assert any(row["rule_id"] == "added" for row in window.routing_table.records)
    window.experiment_output.setText(str(tmp_path / "search"))
    window.run_experiment()
    qtbot.waitUntil(lambda: window.job is None, timeout=120000)
    assert window.history and window.history["status"] == "complete", window.status.text()
    assert window.history["verified"]
    assert window.candidates.rowCount() == len(window.history["rows"])
    window.pareto_only.setChecked(True)
    assert {row["id"] for row in window.candidates.records} == set(window.history["pareto"])
    window.candidates.selectRow(0)
    assert "verification" in json.loads(window.candidate_details.toPlainText())
    window.load_history(tmp_path / "search" / "history.json")
    assert "width" in window.history_identity.text()
    assert not window.pareto_plot.y_objective.isEnabled()
    points = window.pareto_plot.points.points()
    assert {point.data() for point in points} == {row["id"] for row in window.history["rows"] if row.get("objectives")}
    assert "One objective" in window.pareto_plot.note.text()


def test_pareto_plot_uses_real_two_objective_solutions_and_selects_evidence(qtbot, tmp_path):
    project = example(tmp_path)
    project.power.measurements.append(MeasurementSpec("current_rms", "metal_current_density",
        SupportSelector(layers=["metal"]), "rms"))
    project.power.experiments[0].objectives.append(ObjectiveSpec("current_rms"))
    window = PowerWorkbench(project, execution=ExecutionSettings(cpu_threads=1, study_workers=1))
    qtbot.addWidget(window)
    window.experiment_output.setText(str(tmp_path / "search"))
    window.run_experiment()
    qtbot.waitUntil(lambda: window.job is None, timeout=120000)
    assert window.history and window.history["status"] == "complete", window.status.text()
    panel = window.pareto_plot
    assert panel.resolution.currentText() == "refined"
    assert panel.x_objective.currentText() == "loss" and panel.y_objective.currentText() == "current_rms"
    assert panel.y_objective.isEnabled()
    assert "mA/µm²" in panel.metadata("current_rms")[0] and "worst" in panel.metadata("current_rms")[0]
    rows = {row["id"]: row for row in window.history["rows"]}
    points = panel.points.points()
    assert len(points) == 2 and panel.front_ids == set(window.history["verified"])
    for point in points:
        row = rows[point.data()]
        fine = row["verification"]["levels"][-1]["objectives"]
        assert point.pos().x() == fine["loss"]
        assert point.pos().y() == fine["current_rms"] * 1e-9
        assert point.symbol() == ("s" if row["status"] == "verified_feasible" else "o")
    panel.resolution.setCurrentText("coarse")
    for point in panel.points.points():
        coarse = rows[point.data()]["verification"]["levels"][0]["objectives"]
        assert point.pos().x() == coarse["loss"] and point.pos().y() == coarse["current_rms"] * 1e-9
        assert point.symbol() == "o"
    panel.resolution.setCurrentText("refined")
    window.pareto_only.setChecked(True)
    baseline = next(point for point in panel.points.points() if point.data() == "baseline")
    panel.points.sigClicked.emit(panel.points, [baseline], None)
    assert not window.pareto_only.isChecked()
    assert json.loads(window.candidate_details.toPlainText())["id"] == "baseline"
    panel.x_objective.setCurrentText("current_rms")
    assert "One objective" in panel.note.text()
    panel.y_objective.setCurrentText("loss")
    assert panel.points.points()[0].pos().x() == rows["baseline"]["objectives"]["current_rms"] * 1e-9
    panel.export_to(tmp_path / "pareto.svg")
    panel.export_to(tmp_path / "pareto.png")
    assert "<svg" in (tmp_path / "pareto.svg").read_text(encoding="utf-8")
    assert (tmp_path / "pareto.png").stat().st_size > 1000


def test_workbench_cancels_actual_owned_process(qtbot, tmp_path):
    window = PowerWorkbench(make_example("E1", tmp_path / "input"))
    qtbot.addWidget(window)
    window.start("power", {})
    job = window.job
    assert job is not None
    window.cancel_job()
    assert window.job is None and not job.process.is_alive()
    assert "Cancelled" in window.status.text()
    assert not window.cancel.isEnabled()
