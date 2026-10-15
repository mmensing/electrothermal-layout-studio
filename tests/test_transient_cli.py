# SPDX-License-Identifier: MPL-2.0
"""CLI time overrides are typed, validated and reflected in exported history."""
from __future__ import annotations

import json

import pytest

from etlayout.cli.main import main
from etlayout.examples import make_example
from etlayout.io.project import load_project, save_project
from etlayout.models import TransientDrive, Waveform


@pytest.fixture
def project_path(tmp_path):
    project = make_example("E1", tmp_path / "inputs")
    project.solver.mode = "electrothermal"
    project.transient.drives = [TransientDrive("terminals.drain.value", Waveform(kind="constant", value=.1))]
    path = tmp_path / "input.etl"
    save_project(project, path)
    return path


def test_transient_cli_overrides_time_grid_and_nested_drive_settings(project_path, tmp_path, capsys):
    output = tmp_path / "output"
    code = main(["transient", str(project_path), "--output", str(output), "--no-plots",
                 "--stop-time", "0.0002", "--time-step", "0.00007", "--output-interval", "0.0001",
                 "--set", "transient.max_steps=20", "--set", "transient.drives.0.waveform.value=0.05"])
    captured = capsys.readouterr()
    assert code == 0, captured.err
    data = json.loads(captured.out)
    assert data["converged"]
    project, result = load_project(output / "simulation.etl")
    assert project.transient.enabled and project.transient.max_steps == 20
    assert project.transient.time_step == .00007
    assert project.transient.drives[0].waveform.value == .05
    assert result.transient.times == pytest.approx([0., .0001, .0002])
    assert result.transient.rows[-1]["drives"]["terminals.drain.value"] == .05
    assert (output / "transient_history.csv").is_file()
    assert not (output / "transient_history.png").exists()


@pytest.mark.parametrize("setting", ["transient.enabled=1", "transient.max_steps=2.5", "transient.max_steps=false", "transient.time_step=null", "transient.time_step=NaN", "transient.drives.0.waveform.value=true"])
def test_transient_cli_rejects_invalid_typed_overrides(project_path, tmp_path, capsys, setting):
    output = tmp_path / "invalid"
    assert main(["transient", str(project_path), "--output", str(output), "--no-plots", "--set", setting]) == 2
    assert "etlayout:" in capsys.readouterr().err
    assert not output.exists()


def test_transient_cli_rejects_nonpositive_time_flag(project_path, tmp_path, capsys):
    assert main(["transient", str(project_path), "-o", str(tmp_path / "invalid"), "--stop-time", "0"]) == 2
    assert "finite and positive" in capsys.readouterr().err


def test_solve_command_honors_saved_transient_enable_setting(project_path, tmp_path, capsys):
    output = tmp_path / "output"
    assert main(["solve", str(project_path), "-o", str(output), "--no-plots",
                 "--set", "transient.enabled=true", "--set", "transient.stop_time=0.0001",
                 "--set", "transient.time_step=0.00005", "--set", "transient.output_interval=0.0001"]) == 0
    assert json.loads(capsys.readouterr().out)["converged"]
    _, result = load_project(output / "simulation.etl")
    assert result.transient.times == pytest.approx([0., .0001])


def test_operation_flag_overrides_saved_transient_without_discarding_waveforms(project_path, tmp_path, capsys):
    project, _ = load_project(project_path)
    project.transient.enabled = True
    save_project(project, project_path)
    output = tmp_path / "steady"
    assert main(["solve", str(project_path), "--operation", "steady", "--mode", "electrical",
                 "--no-plots", "-o", str(output)]) == 0, capsys.readouterr().err
    saved, result = load_project(output / "simulation.etl")
    assert not saved.transient.enabled and saved.transient.drives
    assert result.transient is None
    assert result.metrics["effective_resistance"] == pytest.approx(.084, rel=.01)


def test_explicit_transient_electrical_scope_has_actionable_error(project_path, tmp_path, capsys):
    assert main(["solve", str(project_path), "--operation", "transient", "--mode", "electrical",
                 "--no-plots", "-o", str(tmp_path / "invalid")]) == 2
    assert "requires thermal or electrothermal" in capsys.readouterr().err
    assert not (tmp_path / "invalid").exists()


def test_cli_resume_continues_absolute_time_and_energy(project_path, tmp_path, capsys):
    first, second = tmp_path / "first", tmp_path / "second"
    base_args = ["transient", str(project_path), "--time-step", "0.00005",
                 "--output-interval", "0.00005", "--no-plots"]
    assert main([*base_args, "--stop-time", "0.0001", "-o", str(first)]) == 0, capsys.readouterr().err
    assert main([*base_args, "--stop-time", "0.0002", "--resume", str(first / "simulation.etl"),
                 "-o", str(second)]) == 0, capsys.readouterr().err
    _, prior = load_project(first / "simulation.etl")
    _, continued = load_project(second / "simulation.etl")
    assert continued.transient.times[0] == pytest.approx(.0001)
    assert continued.transient.times[-1] == pytest.approx(.0002)
    assert continued.metrics["transient_input_energy"] > prior.metrics["transient_input_energy"]
    assert continued.metrics["transient_initialization"]["kind"] == "resume"
    capsys.readouterr()
    assert main(["validate", str(second / "simulation.etl")]) == 0
    preflight = json.loads(capsys.readouterr().out)["transient_resources"]
    assert preflight["start_time_s"] == pytest.approx(.0001)
    assert preflight["steps"] == 2 and preflight["output_snapshots"] == 3
    # Replaying a saved continuation must reuse its initial snapshot, not silently
    # return to uniform temperature or continue again from the final field.
    replay = tmp_path / "replay"
    assert main(["solve", str(second / "simulation.etl"), "--no-plots", "-o", str(replay)]) == 0, capsys.readouterr().err
    replay_project, replayed = load_project(replay / "simulation.etl")
    assert replay_project.transient.initialization == "resume"
    assert replayed.configuration_hash == continued.configuration_hash
    assert replayed.transient.times == pytest.approx(continued.transient.times)
    assert replayed.metrics["transient_input_energy"] == pytest.approx(continued.metrics["transient_input_energy"])
    warm = tmp_path / "warm"
    assert main(["solve", str(second / "simulation.etl"), "--initial-state", str(first / "simulation.etl"),
                 "--no-plots", "-o", str(warm)]) == 0, capsys.readouterr().err
    warm_project, warmed = load_project(warm / "simulation.etl")
    assert warm_project.transient.initialization == "saved"
    assert warmed.transient.times[0] == 0.


def test_time_refine_cli_keeps_cadence_and_exports_absolute_metrics(tmp_path, capsys):
    project = make_example("E17", tmp_path / "input")
    project.transient.stop_time = 400e-6
    path = tmp_path / "time-study.etl"
    save_project(project, path)
    output = tmp_path / "study"
    assert main(["time-refine", str(path), "--steps", "0.000025,0.0000125",
                 "-o", str(output)]) == 0, capsys.readouterr().err
    rows = json.loads((output / "study.json").read_text(encoding="utf-8"))
    assert len(rows) == 2 and all(row["converged"] for row in rows)
    assert all(row["time_study_output_interval_s"] == 50e-6 for row in rows)
    assert rows[1]["transient_peak_temperature"] > rows[0]["transient_peak_temperature"]
    assert rows[1]["transient_input_energy"] == pytest.approx(2e-6)
    assert rows[1]["transient_peak_temperature_absolute_change"] > 0


def test_validate_cli_reports_pre_mesh_transient_resource_estimate(project_path, capsys):
    assert main(["validate", str(project_path), "--operation", "transient"]) == 0
    output = json.loads(capsys.readouterr().out)
    assert output["transient_resources"]["steps"] > 0
    assert output["transient_resources"]["estimated_history_bytes"] is None


def test_validate_resume_without_original_history_fails_actionably(project_path, capsys):
    project, _ = load_project(project_path)
    project.transient.enabled = True
    project.transient.initialization = "resume"
    save_project(project, project_path)
    assert main(["validate", str(project_path)]) == 2
    captured = capsys.readouterr()
    assert "Resume validation requires the original saved history" in captured.err
    assert not captured.out
