# SPDX-License-Identifier: MPL-2.0
"""Regressions for the independently reproduced 2026-10-11 workflow audit."""
import io
import json
import zipfile
from contextlib import redirect_stderr, redirect_stdout
from copy import deepcopy
from pathlib import Path

import numpy as np
import pytest
from solver_helpers import rectangle

from etlayout.cli.main import main
from etlayout.examples import make_example
from etlayout.io.export import export_results, export_study
from etlayout.io.project import (
    configuration_hash,
    load_project,
    load_run_history,
    physics_hash,
    save_json,
    save_project,
)
from etlayout.models import SimulationError, SweepSpec, Terminal
from etlayout.simulation.execution import ExecutionSettings
from etlayout.simulation.runner import prepare_mesh, run_project
from etlayout.simulation.sweep import mesh_refinement_study, run_sweep, set_parameter, sweep_values
from etlayout.solvers import solve_electrical


def test_loaded_integer_float_field_preserves_fractional_sweep(tmp_path):
    project = make_example("E1", tmp_path / "input")
    project.terminals[1].value = 1
    save_json(project, tmp_path / "integer.json")
    loaded, _ = load_project(tmp_path / "integer.json")
    rows = run_sweep(loaded, SweepSpec("terminals.drain.value", [.25, .5, .75], "electrical"))
    assert [row.value for row in rows] == [row.applied_value for row in rows] == [.25, .5, .75]
    for row in rows:
        assert row.result.converged and not row.error
        assert row.result.terminals["drain"]["voltage"] == row.value
        assert row.result.metrics["sweep_applied_value"] == row.value
        assert row.result.terminals["drain"]["current"] == pytest.approx(row.value / .084, rel=1e-8)


@pytest.mark.parametrize("value", [1.5, True, None, float("nan"), float("inf")])
def test_integer_sweep_settings_reject_lossy_or_invalid_values(value):
    project, _ = rectangle()
    with pytest.raises(SimulationError):
        set_parameter(project, "solver.max_iterations", value)
    assert project.solver.max_iterations == 100


def test_optional_numeric_parameter_can_be_enabled_and_disabled():
    project, _ = rectangle()
    assert set_parameter(project, "layers.metal.mesh_size", 2e-6) == 2e-6
    assert set_parameter(project, "layers.metal.mesh_size", None) is None


def test_resistance_ignores_isolated_bias_and_allows_explicit_kelvin_pair():
    project, mesh = rectangle(layers=("metal", "isolated"))
    mesh.terminal_nodes["bias"] = mesh.terminal_nodes["left"]
    project.terminals.append(Terminal("bias", "isolated", kind="voltage", value=-5.))
    result = solve_electrical(project, mesh)
    assert result.converged
    assert result.metrics["effective_resistance"] == pytest.approx(.2, rel=1e-11)
    assert result.metrics["voltage_drop"] == pytest.approx(1.)
    assert result.metrics["resistance_terminal_pair"] == ["left", "right"]
    assert result.metrics["resistance_current_terminal"] == "right"
    project.solver.resistance_terminals = ["left", "bias"]
    with pytest.raises(SimulationError, match="same electrical network"):
        solve_electrical(project, mesh)


def test_multiple_driven_networks_require_explicit_measurement():
    project, mesh = rectangle(layers=("metal", "other"))
    mesh.terminal_nodes.update(other_left=mesh.terminal_nodes["left"], other_right=mesh.terminal_nodes["right"])
    project.terminals.extend([Terminal("other_left", "other", kind="ground"),
                              Terminal("other_right", "other", value=2.)])
    result = solve_electrical(project, mesh)
    assert result.converged and result.metrics["effective_resistance"] is None
    assert any("ambiguous" in d.message for d in result.diagnostics)
    project.solver.resistance_terminals = ["other_left", "other_right"]
    project.solver.resistance_current_terminal = "other_right"
    chosen = solve_electrical(project, mesh)
    assert chosen.metrics["effective_resistance"] == pytest.approx(.2, rel=1e-11)
    assert chosen.metrics["resistance_current"] == pytest.approx(10.)


def test_mesh_study_keeps_undefined_multiport_resistance(monkeypatch):
    project, mesh = rectangle(layers=("metal", "other"))
    mesh.terminal_nodes.update(other_left=mesh.terminal_nodes["left"], other_right=mesh.terminal_nodes["right"])
    project.terminals.extend([Terminal("other_left", "other", kind="ground"),
                              Terminal("other_right", "other", value=2.)])
    monkeypatch.setattr("etlayout.simulation.sweep.run_project",
                        lambda candidate, **kwargs: solve_electrical(candidate, mesh))
    rows = mesh_refinement_study(project, [1e-6, .5e-6], execution=ExecutionSettings(study_workers=1))
    assert len(rows) == 2 and all(row.result.converged for row in rows)
    assert rows[-1].metrics["effective_resistance"] is None
    assert "effective_resistance_absolute_change" not in rows[-1].metrics
    assert rows[-1].metrics["electrical_power_relative_change"] == 0


@pytest.mark.parametrize("count", [2.5, True, float("nan"), float("inf"), 0])
def test_sweep_count_is_never_truncated(count):
    with pytest.raises(SimulationError, match="whole number"):
        sweep_values(0, 1, count)


def test_cli_range_count_rejects_fraction(tmp_path, capsys):
    project = make_example("E1", tmp_path / "input")
    source = tmp_path / "project.etl"
    save_project(project, source)
    assert main(["sweep", str(source), "--parameter", "terminals.drain.value",
                 "--range", "0", "1", "2.5"]) == 2
    assert "whole number" in capsys.readouterr().err


def test_kelvin_voltage_pair_uses_selected_drive_current():
    project, mesh = rectangle(nx=20)
    for name, x in [("sense_a", 2e-6), ("sense_b", 8e-6)]:
        mesh.terminal_nodes[name] = np.flatnonzero(np.isclose(mesh.points[:, 0], x, rtol=0, atol=1e-14))
        project.terminals.append(Terminal(name, "metal", kind="floating"))
    project.solver.resistance_terminals = ["sense_a", "sense_b"]
    project.solver.resistance_current_terminal = "right"
    result = solve_electrical(project, mesh)
    assert result.converged
    assert result.metrics["effective_resistance"] == pytest.approx(.12, rel=1e-11)


def test_portable_result_reexport_keeps_embedded_layout(tmp_path):
    project = make_example("E1", tmp_path / "source")
    result = run_project(project)
    save_project(project, tmp_path / "portable.etl", result)
    Path(project.layout.path).unlink()
    loaded, restored = load_project(tmp_path / "portable.etl")
    assert restored.configuration["layout"]["path"] == loaded.layout.path
    assert configuration_hash(restored.configuration) == restored.configuration_hash
    export_results(restored, tmp_path / "export", plots=False)
    exported, _ = load_project(tmp_path / "export" / "simulation.etl")
    assert Path(exported.layout.path).is_file()
    assert run_project(exported).metrics["effective_resistance"] == pytest.approx(.084, rel=1e-8)


def test_direct_solver_retains_layout_identity_before_portable_save(tmp_path):
    project = make_example("E1", tmp_path / "source")
    _, mesh = prepare_mesh(project)
    project.layout.sha256 = ""
    result = solve_electrical(project, mesh)
    assert result.configuration["layout"]["sha256"]
    save_project(project, tmp_path / "portable.etl", result)
    Path(project.layout.path).unlink()
    loaded, restored = load_project(tmp_path / "portable.etl")
    assert restored.physics_hash == physics_hash(loaded)
    assert restored.configuration_hash == result.configuration_hash


def test_physics_hash_excludes_annotations_but_not_physical_edits(tmp_path):
    project = make_example("E1", tmp_path / "source")
    result = run_project(project)
    edited = deepcopy(project)
    edited.name, edited.notes = "Renamed", "New annotation"
    edited.layers[0].visible, edited.layers[0].color, edited.layers[0].opacity = False, "#123456", .1
    edited.layers[0].name = "Display label"
    edited.materials["copper"].notes = "Annotation"
    edited.terminals[0].name = "Ground label"
    edited.sweeps = []
    edited.thresholds = {"current_density_max": 1.}
    assert configuration_hash(edited) != result.configuration_hash
    assert physics_hash(edited) == result.physics_hash
    save_project(edited, tmp_path / "renamed.etl", result)
    restored, saved = load_project(tmp_path / "renamed.etl")
    assert restored.name == "Renamed" and saved.configuration_hash == result.configuration_hash
    assert any("Engineering threshold" in d.message for d in saved.diagnostics)
    assert not any("Engineering threshold" in d.message for d in result.diagnostics)
    edited.layers[0].thickness *= 2
    assert physics_hash(edited) != result.physics_hash
    with pytest.raises(SimulationError, match="different physical setup"):
        save_project(edited, tmp_path / "wrong.etl", result)


@pytest.mark.parametrize("value", [None, float("nan"), "300"])
def test_invalid_typed_project_cannot_replace_saved_archive(tmp_path, value):
    project = make_example("E1", tmp_path / "source")
    target = tmp_path / "saved.etl"
    save_project(project, target)
    previous = target.read_bytes()
    project.solver.initial_temperature = value
    with pytest.raises(SimulationError):
        save_project(project, target)
    assert target.read_bytes() == previous


def test_history_keeps_each_design_and_embedded_source(tmp_path):
    projects = [make_example(name, tmp_path / name) for name in ("E1", "E2")]
    runs = [run_project(project) for project in projects]
    path = tmp_path / "history.etl"
    save_project(projects[0], path, runs[0], history=runs)
    for project in projects:
        Path(project.layout.path).unlink()
    history = load_run_history(path)
    assert [r.configuration_hash for r in history] == [r.configuration_hash for r in runs]
    assert all(Path(r.configuration["layout"]["path"]).is_file() for r in history)
    save_project(load_project(path)[0], tmp_path / "again.etl", load_project(path)[1], history=history)
    assert len(load_run_history(tmp_path / "again.etl")) == 2


def test_changed_history_source_cannot_replace_archive(tmp_path):
    projects = [make_example(name, tmp_path / name) for name in ("E1", "E2")]
    runs = [run_project(project) for project in projects]
    target = tmp_path / "history.etl"
    save_project(projects[0], target, runs[0], history=runs)
    previous = target.read_bytes()
    # The old original path still exists but now points to another design.
    Path(projects[1].layout.path).write_bytes(Path(projects[0].layout.path).read_bytes())
    with pytest.raises(SimulationError, match="different physical setup"):
        save_project(projects[0], target, runs[0], history=runs)
    assert target.read_bytes() == previous
    restored = load_run_history(target)
    assert restored[1].configuration_hash == runs[1].configuration_hash
    assert restored[1].configuration["layout"]["path"] != projects[1].layout.path


def test_legacy_archive_without_new_measurement_or_hash_fields_loads(tmp_path):
    project = make_example("E1", tmp_path / "source")
    result = run_project(project)
    path = tmp_path / "new.etl"
    save_project(project, path, result)
    with zipfile.ZipFile(path) as archive:
        members = {name: archive.read(name) for name in archive.namelist()}
    metadata = json.loads(members["project.json"])
    rm = json.loads(members["results/metadata.json"])
    for config in (metadata["project"], rm["configuration"]):
        for name in ("resistance_terminals", "resistance_current_terminal"):
            config["solver"].pop(name)
    rm.pop("physics_hash")
    rm["configuration_hash"] = configuration_hash(rm["configuration"])
    members["project.json"] = json.dumps(metadata).encode()
    members["results/metadata.json"] = json.dumps(rm).encode()
    with zipfile.ZipFile(tmp_path / "legacy.etl", "w") as archive:
        for name, content in members.items():
            archive.writestr(name, content)
    loaded, saved = load_project(tmp_path / "legacy.etl")
    assert saved.converged and saved.physics_hash == physics_hash(loaded)
    save_project(loaded, tmp_path / "resaved.etl", saved)
    assert load_project(tmp_path / "resaved.etl")[1].converged


def test_cli_validation_returns_failure_for_connectivity_errors(tmp_path):
    project = make_example("E1", tmp_path)
    project.terminals = project.terminals[1:]
    project.terminals[0].kind, project.terminals[0].value = "current", .1
    save_json(project, tmp_path / "invalid.json")
    out = io.StringIO()
    with redirect_stdout(out):
        status = main(["validate", str(tmp_path / "invalid.json")])
    assert status == 2
    assert any(d["severity"] == "error" for d in json.loads(out.getvalue())["connectivity"]["diagnostics"])


def test_cli_saved_sweep_honors_explicit_mode_and_exports_applied_values(tmp_path, monkeypatch):
    project = make_example("E1", tmp_path)
    project.sweeps[0].mode, project.sweeps[0].values = "electrothermal", [.02]
    save_json(project, tmp_path / "sweep.json")
    captured = []

    def inspect_rows(rows, *args):
        captured.extend(rows)
        return "inspected"

    monkeypatch.setattr("etlayout.io.export.export_study", inspect_rows)
    with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
        status = main(["sweep", str(tmp_path / "sweep.json"), "--mode", "electrical"])
    assert status == 0 and captured[0].result.configuration["solver"]["mode"] == "electrical"
    assert captured[0].applied_value == .02
    # Keep the exported-table contract independent of potentially expensive plots.
    monkeypatch.setattr("matplotlib.figure.Figure.savefig", lambda *args, **kwargs: None)
    export_study(captured, tmp_path / "study", project.sweeps[0].parameter)
    record = json.loads((tmp_path / "study" / "study.json").read_text())[0]
    assert record["requested_value"] == record["applied_value"] == .02
