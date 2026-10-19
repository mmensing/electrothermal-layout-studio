# SPDX-License-Identifier: MPL-2.0
"""GUI, CLI, study and optimization clients consume one thermal acceptance record."""
import json
from copy import deepcopy

import pytest

from etlayout.cli.main import main
from etlayout.examples import make_example
from etlayout.io.project import annotated_result, load_project, save_project
from etlayout.models import HeatSource, RegionSpec, SweepSpec, TemperatureLimit, ThermalBoundary
from etlayout.simulation.runner import run_project
from etlayout.simulation.sweep import run_sweep


def real_project(tmp_path):
    project = make_example("E1", tmp_path / "layout")
    project.solver.mode = "thermal"
    layer = project.layers[0].id
    project.heat_sources = [HeatSource("heater", layer, RegionSpec(full_domain=True), value=1000.)]
    project.thermal_boundaries = [ThermalBoundary("sink", layer, value=100., ambient=300.)]
    project.temperature_limits = [TemperatureLimit(maximum_temperature=305.)]
    return project


@pytest.mark.numerical
def test_cli_engineering_exit_code_preserves_actual_fields_and_reports(tmp_path, capsys):
    project = real_project(tmp_path)
    path = tmp_path / "input.etl"
    save_project(project, path)
    output = tmp_path / "result"
    assert main(["solve", str(path), "--no-plots", "--output", str(output)]) == 3
    payload = json.loads(capsys.readouterr().out)
    assert payload["converged"] and not payload["metrics"]["engineering_accepted"]
    _, restored = load_project(output / "simulation.etl")
    assert restored.fields and restored.converged
    assert restored.metrics["thermal_limit_compliance"]["status"] == "failed"
    assert "FAILED engineering acceptance" in (output / "report.html").read_text(encoding="utf-8")


@pytest.mark.numerical
def test_sweep_retains_rejected_diagnostic_rows_at_configured_operating_points(tmp_path):
    project = real_project(tmp_path)
    rows = run_sweep(project, SweepSpec("heat_sources.heater.value", [100., 1000.], mode="thermal"))
    assert not rows[0].error and rows[0].result.metrics["temperature_max"] == pytest.approx(301.)
    assert "maximum-temperature" in rows[1].error
    assert rows[1].result.converged
    assert rows[1].result.metrics["temperature_max"] == pytest.approx(310.)
    assert rows[1].result.metrics["thermal_limit_compliance"]["status"] == "failed"


@pytest.mark.numerical
def test_equal_service_optimizer_excludes_thermal_failure_and_keeps_archive(tmp_path):
    from test_power_optimization import example

    from etlayout.optimization import run_experiment
    project = example(tmp_path)
    project.solver.mode = "electrothermal"
    project.thermal_boundaries = [ThermalBoundary("sink", "metal", value=1e6)]
    project.temperature_limits = [TemperatureLimit(maximum_temperature=300.)]
    experiment = project.power.experiments[0]
    experiment.max_evaluations = 1
    experiment.scenarios = experiment.scenarios[:1]
    history = run_experiment(project, experiment.id, tmp_path / "search")
    baseline = history["rows"][0]
    assert baseline["status"] == "thermal_limit_failure"
    row = baseline["scenarios"][0]
    assert row["converged"] and row["service"]["status"] == "satisfied"
    assert row["metrics"]["thermal_limit_compliance"]["status"] == "failed"
    assert row["archive_sha256"]
    assert not history["verified"] and not history["published"]
    _, restored = load_project(row["archive"])
    assert restored.converged and not restored.metrics["engineering_accepted"]


@pytest.mark.parametrize("unit,value", [("°C", "150"), ("K", "423.15")])
def test_global_dialog_celsius_kelvin_equivalence_and_cancel_isolated(qtbot, tmp_path, unit, value):
    from etlayout.gui.temperature_limits import TemperatureLimitsDialog
    project = real_project(tmp_path)
    before = deepcopy(project.to_dict())
    dialog = TemperatureLimitsDialog(project)
    qtbot.addWidget(dialog)
    dialog.enabled.setChecked(True)
    dialog.units.setCurrentText(unit)
    dialog.maximum.setText(value)
    dialog.policy.setCurrentIndex(dialog.policy.findData("stop_transient"))
    dialog.accept()
    assert dialog.limits[0].maximum_temperature == pytest.approx(423.15)
    assert dialog.limits[0].evaluation == "stop_transient"
    assert project.to_dict() == before
    cancel = TemperatureLimitsDialog(project)
    qtbot.addWidget(cancel)
    cancel.maximum.setText("1")
    cancel.reject()
    assert project.to_dict() == before


def test_results_gui_exposes_engineering_failure_without_hiding_converged_fields(qtbot, tmp_path):
    from etlayout.gui.main_window import MainWindow
    project = real_project(tmp_path)
    result = run_project(project)
    window = MainWindow(project, database_path=tmp_path / "db.sqlite", recover=False)
    qtbot.addWidget(window, before_close_func=lambda widget: setattr(widget, "dirty", False))
    window.show()
    qtbot.waitUntil(lambda: window.prepare_job is None, timeout=30000)
    window.show_result(result)
    assert "FAILED — Maximum temperature exceeded" in window.result_status.text()
    assert "Numerical convergence: yes" in window.result_status.text()
    assert "Rise" in window.result_status.text()
    window.guided.navigate("results", force=True)
    assert "FAILED — Maximum temperature exceeded" in window.guided.result_evidence.text()
    assert window.guided.issue_table.rowCount() >= 1
    assert "Maximum temperature limit:" in window.guided.issue_table.item(0, 1).text()
    assert window.result.fields and window.result.converged
    assert any("Maximum temperature limit:" in d.message for d in window.result.diagnostics)
    window.dirty = False


@pytest.mark.numerical
def test_cooled_frame_annotations_retain_runwide_failure(tmp_path):
    from test_transient import thermal_fixture

    from etlayout.models import TransientDrive, Waveform
    from etlayout.solvers import solve
    project, mesh = thermal_fixture(dt=.05, stop=2.)
    project.transient.drives = [TransientDrive("heat_sources.heater.value", Waveform(kind="pulse", high=100., low=0., width=.1, period=0.))]
    project.temperature_limits = [TemperatureLimit(maximum_temperature=305.)]
    result = solve(project, mesh)
    last = result.transient.snapshots[-1]
    assert last.metrics["thermal_limit_compliance"]["status"] == "passed"
    annotated = annotated_result(last, project, run=result)
    assert annotated.metrics["thermal_limit_compliance"]["status"] == "failed"
    assert not annotated.metrics["engineering_accepted"]


@pytest.mark.numerical
def test_fixed_temperature_electrical_state_is_not_claimed_as_a_thermal_solve(tmp_path):
    project = real_project(tmp_path)
    project.solver.mode = "electrical"
    project.temperature_limits[0].maximum_temperature = 1.
    result = run_project(project)
    assert result.converged and result.metrics["engineering_accepted"]
    assert result.metrics["thermal_limit_compliance"]["status"] == "not_applicable"


@pytest.mark.numerical
def test_cli_transient_early_stop_uses_engineering_code_and_reopens_partial_run(tmp_path, capsys):
    project = real_project(tmp_path)
    project.transient.enabled = True
    project.transient.stop_time = .1
    project.transient.time_step = .01
    project.transient.output_interval = .1
    project.temperature_limits[0].evaluation = "stop_transient"
    target = tmp_path / "transient.etl"
    save_project(project, target)
    output = tmp_path / "transient-result"
    assert main(["solve", str(target), "--no-plots", "--output", str(output)]) == 3
    assert json.loads(capsys.readouterr().out)["converged"]
    _, result = load_project(output / "simulation.etl")
    assert result.converged and not result.transient.completed
    assert result.metrics["transient_stop_reason"] == "maximum_temperature"
    assert result.transient.times[-1] < project.transient.stop_time
    assert result.metrics["thermal_limit_compliance"]["status"] == "failed"
