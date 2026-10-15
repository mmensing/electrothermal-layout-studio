# SPDX-License-Identifier: MPL-2.0
"""Public command workflows perform real solves, generation and verified search."""
import json
from pathlib import Path

import numpy as np
import pytest
from test_power_optimization import example

from etlayout.cli.main import main
from etlayout.examples import make_example
from etlayout.io.project import load_project, save_project
from etlayout.models import MeasurementSpec, PhysicalPort, PortLanding, SupportSelector


def test_cli_power_solve_exports_scoped_numerical_evidence(tmp_path, capsys):
    project = make_example("E1", tmp_path / "input")
    project.power.measurements = [MeasurementSpec("selected_loss", "joule_density",
        SupportSelector(layers=["metal"]), "integral")]
    saved = tmp_path / "project.etl"
    save_project(project, saved)
    output = tmp_path / "analysis"
    assert main(["power", str(saved), "--output", str(output), "--no-plots", "--cpu-threads", "1"]) == 0
    report = json.loads(capsys.readouterr().out)
    measurement = report["measurements"]["selected_loss"]
    assert measurement["status"] == "available" and measurement["unit"] == "W"
    assert measurement["value"] == pytest.approx(.02**2 / .084, rel=1e-9)
    assert report["loss_budget"]["total_power"] == pytest.approx(measurement["value"])
    _, restored = load_project(output / "simulation.etl")
    assert restored.analysis["measurements"]["selected_loss"] == measurement
    assert (output / "measurements.csv").is_file()


@pytest.mark.parametrize("format_name,suffix", [("json", "json"), ("csv", "csv"), ("spice", "cir")])
def test_cli_passive_multiport_real_matrix_and_exports(tmp_path, capsys, format_name, suffix):
    project = make_example("E1", tmp_path / "input")
    project.power.ports = [PhysicalPort("positive", [PortLanding("drain", .01)], value=.02),
                           PhysicalPort("negative", [PortLanding("source")], kind="ground")]
    saved = tmp_path / "project.etl"
    save_project(project, saved)
    output = tmp_path / ("matrix." + suffix)
    assert main(["multiport", str(saved), "--ports", "positive, negative", "--reference", "negative",
                 "--format", format_name, "--output", str(output), "--cpu-threads", "1"]) == 0
    report = json.loads(capsys.readouterr().out)
    assert np.asarray(report["conductance_matrix"]) == pytest.approx(np.array([[1., -1.], [-1., 1.]]) / .094)
    assert output.stat().st_size > 50
    assert report["validation"]["factorizations"] == 1


def test_cli_routing_and_optimization_share_backend_and_resume(tmp_path, capsys):
    project = example(tmp_path)
    saved = tmp_path / "project.etl"
    save_project(project, saved)
    output = tmp_path / "candidate.oas"
    assert main(["routing", str(saved), "--action", "generate", "--parameters", '{"bus.width":4e-6}',
                 "--output", str(output)]) == 0
    generated = json.loads(capsys.readouterr().out)
    assert generated["valid"] and generated["provenance"]["export_reimport_verified"]
    assert generated["geometry_changes"]["metal"]["added_area_m2"] == pytest.approx(20e-12)
    assert (tmp_path / "candidate.etl").is_file()
    assert main(["routing", str(tmp_path / "candidate.etl"), "--action", "export", "--output", str(tmp_path / "copied.gds")]) == 0
    copied = json.loads(capsys.readouterr().out)
    assert copied["valid"] and copied["geometry_changes"]["metal"]["added_area_m2"] == 0
    assert copied["geometry_changes"]["metal"]["removed_area_m2"] == 0
    assert main(["routing", str(saved), "--action", "validate", "--output", str(tmp_path / "rules")]) == 0
    assert json.loads(capsys.readouterr().out)["valid"]
    assert (tmp_path / "rules" / "routing-validation.json").is_file()
    history_dir = tmp_path / "search"
    args = ["optimize", str(saved), "--experiment", "width", "--output", str(history_dir), "--cpu-threads", "1"]
    assert main(args) == 0
    history = json.loads(capsys.readouterr().out)
    assert history["status"] == "complete" and len(history["verified"]) == 1
    selected = next(row for row in history["rows"] if row["id"] in history["verified"])
    for scenario in selected["verification"]["levels"][-1]["scenarios"]:
        assert scenario["service"]["status"] == "satisfied"
    assert main(args) == 0
    assert json.loads(capsys.readouterr().out) == history
    assert main([*args, "--export-candidate", history["verified"][0], "--export-directory", str(tmp_path / "selected")]) == 0
    exported = json.loads(capsys.readouterr().out)
    assert exported["status"] == "verified_export"
    assert main([*args, "--export-candidate", "baseline", "--export-directory", str(tmp_path / "forbidden")]) == 2
    assert "verified" in capsys.readouterr().err


def test_cli_invalid_parameters_and_missing_ports_are_actionable(tmp_path, capsys):
    project = make_example("E1", tmp_path / "input")
    saved = tmp_path / "project.etl"
    save_project(project, saved)
    assert main(["multiport", str(saved), "--output", str(tmp_path / "bad")]) == 2
    assert "physical port" in capsys.readouterr().err
    assert main(["routing", str(saved), "--parameters", "{bad}"]) == 2
    assert "etlayout:" in capsys.readouterr().err
    assert not Path("{bad}").exists()
