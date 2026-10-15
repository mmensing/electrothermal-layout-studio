# SPDX-License-Identifier: MPL-2.0
"""Failure evidence from real layouts, numerical solves, and persisted searches."""
import json
from copy import deepcopy
from pathlib import Path

import klayout.db as kdb
import pytest
from test_power_optimization import example

from etlayout.io.project import file_hash
from etlayout.models import (
    DesignVariable,
    DeviceGroup,
    MeasurementSpec,
    OptimizationExperiment,
    Project,
    RegionSpec,
    SimulationError,
    SupportSelector,
    Terminal,
)
from etlayout.optimization import export_finalist, parameter_candidates, run_experiment


def _one_candidate(project, width):
    experiment = project.power.experiments[0]
    experiment.max_evaluations = 2
    variable = experiment.variables[0]
    variable.values = [width]
    variable.minimum = min(variable.minimum, width)
    variable.maximum = max(variable.maximum, width)
    return experiment


@pytest.mark.parametrize("compliance,expected_status", [(.003, "feasible_provisional"), (.001, "service_failure")])
def test_fixed_voltage_minimum_service_and_independent_compliance(tmp_path, compliance, expected_status):
    project = example(tmp_path)
    experiment = project.power.experiments[0]
    experiment.max_evaluations = 1
    experiment.scenarios = experiment.scenarios[:1]
    scenario = experiment.scenarios[0]
    scenario.service_mode = "fixed_voltage"
    scenario.required_current = .01
    scenario.compliance_voltage = compliance
    scenario.settings = {"terminals.right.value": .002}

    history = run_experiment(project, "width", tmp_path / "search")
    row = history["rows"][0]
    result = row["scenarios"][0]
    resistance = project.materials["copper"].resistivity.value * 10e-6 / (2e-6 * 1e-6)
    assert result["service"]["delivered_current"] == pytest.approx(.002 / resistance, rel=1e-8)
    assert result["service"]["delivered_current"] > scenario.required_current * 2
    assert result["service"]["voltage"] == pytest.approx(.002, abs=1e-12)
    assert result["metrics"]["electrical_power"] == pytest.approx(.002**2 / resistance, rel=1e-8)
    assert row["status"] == expected_status
    assert result["service"]["status"] == ("satisfied" if expected_status == "feasible_provisional" else "failed")
    assert not history["verified"] and not history["published"]


def test_real_scenario_losses_use_declared_weights(tmp_path):
    project = example(tmp_path)
    experiment = project.power.experiments[0]
    experiment.max_evaluations = 1
    experiment.objectives[0].scenario_aggregation = "weighted_mean"
    experiment.scenarios[0].weight = 1
    experiment.scenarios[1].weight = 3
    history = run_experiment(project, "width", tmp_path / "search")
    row = history["rows"][0]
    resistance = project.materials["copper"].resistivity.value * 10e-6 / (2e-6 * 1e-6)
    expected = (.01**2 + 3 * .02**2) / 4 * resistance
    assert row["status"] == "feasible_provisional"
    assert row["objectives"]["loss"] == pytest.approx(expected, rel=1e-8)


def test_cutting_source_footprint_is_not_a_low_loss_candidate(tmp_path):
    project = example(tmp_path)
    _one_candidate(project, .5e-6)
    history = run_experiment(project, "width", tmp_path / "search")
    candidate = history["rows"][1]
    assert history["rows"][0]["status"] == "feasible_provisional"
    assert candidate["status"] == "rule_violation"
    assert any(v["rule_id"] == "terminal_support" for v in candidate["violations"])
    assert not candidate["scenarios"] and not candidate["objectives"]
    assert candidate["id"] not in history["pareto"]


@pytest.mark.parametrize("protected_kind", ["gate", "cell"])
def test_legal_route_cannot_change_fixed_gate_or_active_cell_support(tmp_path, protected_kind):
    project = example(tmp_path)
    experiment = _one_candidate(project, 4e-6)
    if protected_kind == "gate":
        # A mask-defined gate follows edited geometry unless explicitly frozen.
        # Its central footprint remains disjoint from the source/drain electrodes.
        layout = kdb.Layout()
        layout.read(project.layout.path)
        layout.cell("BUS").shapes(layout.layer(2, 0)).insert(kdb.Box(4000, 0, 6000, 4000))
        layout.write(project.layout.path)
        project.layout.sha256 = file_hash(project.layout.path)
        project.power.recipes[0].source_hash = project.layout.sha256
        project.terminals.append(Terminal("gate", "metal", RegionSpec(
            sources=["1/0", "2/0"], operation="intersection"), "gate", 1.))
    else:
        project.power.devices = [DeviceGroup("cell", SupportSelector(
            layers=["metal"], geometry=RegionSpec(polygons=[{"bounds": [4e-6, 0, 6e-6, 4e-6]}])),
            active_area=4e-12, active_width=2e-6)]
        experiment.protected_devices = ["cell"]
    history = run_experiment(project, "width", tmp_path / "search")
    baseline, candidate = history["rows"]
    assert baseline["status"] == "feasible_provisional", baseline
    assert candidate["status"] == "service_failure", candidate
    assert "Fixed active device" in candidate["reason"]
    assert not candidate["scenarios"] and not candidate["objectives"]
    assert candidate["id"] not in history["pareto"]


@pytest.mark.parametrize("selector", [SupportSelector(regions=["mesh-region"]), SupportSelector(devices=["cell"]),
                                      SupportSelector(layers=["missing"])])
def test_unsupported_or_empty_protected_selectors_are_rejected(tmp_path, selector):
    project = example(tmp_path)
    project.power.devices = [DeviceGroup("cell", selector, active_area=1e-12)]
    project.power.experiments[0].protected_devices = ["cell"]
    with pytest.raises(SimulationError, match="Protected"):
        run_experiment(project, "width", tmp_path / "search")


def _interrupt_after_baseline(project, directory):
    cancelled = False

    def progress(_fraction, message):
        nonlocal cancelled
        if message == "Refining baseline":
            cancelled = True

    return run_experiment(project, "width", directory, progress=progress, cancel=lambda: cancelled)


def test_interrupt_after_real_solve_preserves_evidence_and_resumes_refinement(tmp_path):
    project = example(tmp_path)
    project.power.experiments[0].max_evaluations = 1
    history = _interrupt_after_baseline(project, tmp_path / "search")
    assert history["status"] == "cancelled" and history["termination_reason"] == "user_cancelled"
    assert len(history["rows"]) == 1 and history["rows"][0]["status"] == "feasible_provisional"
    evidence = history["rows"][0]["scenarios"][0]
    archive = Path(evidence["archive"])
    before = (archive.stat().st_mtime_ns, file_hash(archive))
    assert json.loads((tmp_path / "search" / "history.json").read_text())["status"] == "cancelled"
    assert not list((tmp_path / "search").rglob("*.tmp"))
    resumed = run_experiment(project, "width", tmp_path / "search")
    assert resumed["status"] == "complete" and resumed["exports_status"] == "complete"
    assert len(resumed["rows"]) == 1
    assert resumed["rows"][0]["verification"]["status"] == "mesh_resolved"
    assert (archive.stat().st_mtime_ns, file_hash(archive)) == before


@pytest.mark.parametrize("changed_field", ["scenario", "solver", "mesh_policy"])
def test_interrupted_history_rejects_changed_physics_or_verification_policy(tmp_path, changed_field):
    project = example(tmp_path)
    project.power.experiments[0].max_evaluations = 1
    history = _interrupt_after_baseline(project, tmp_path / "search")
    assert history["rows"][0]["scenarios"][0]["converged"]
    changed = deepcopy(project)
    if changed_field == "scenario":
        changed.power.experiments[0].scenarios[0].required_current *= 1.1
    elif changed_field == "solver":
        changed.solver.linear_tolerance *= .1
    else:
        changed.power.experiments[0].refinement_factors = [1., .4]
    with pytest.raises(SimulationError, match="cache identity"):
        run_experiment(changed, "width", tmp_path / "search")
    assert json.loads((tmp_path / "search" / "history.json").read_text())["cache_key"] == history["cache_key"]


@pytest.fixture(scope="module")
def published_search(tmp_path_factory):
    directory = tmp_path_factory.mktemp("published-search")
    project = example(directory)
    _one_candidate(project, 4e-6)
    history = run_experiment(project, "width", directory / "search")
    assert len(history["published"]) == 1, history
    assert history["verified"] == history["published"]
    return project, history, directory


@pytest.mark.parametrize("artifact_kind", ["project", "archive", "generated_layout", "published_layout", "export_report"])
def test_resume_rejects_changed_cached_numerical_or_export_artifacts(published_search, artifact_kind):
    project, history, directory = published_search
    row = next(row for row in history["rows"] if row["id"] in history["published"])
    export_dir = Path(history["exports"][row["id"]]["directory"])
    paths = {"project": Path(row["project_path"]), "archive": Path(row["scenarios"][0]["archive"]),
             "generated_layout": Path(row["layout_path"]), "published_layout": next(export_dir.glob("*.gds")),
             "export_report": export_dir / "verification.json"}
    path = paths[artifact_kind]
    original = path.read_bytes()
    try:
        path.write_bytes(original + b"changed after verification")
        with pytest.raises(SimulationError, match="artifact is missing or changed"):
            run_experiment(project, "width", directory / "search")
    finally:
        path.write_bytes(original)
    assert file_hash(path) in ([row["project_sha256"], row["layout_sha256"], row["scenarios"][0]["archive_sha256"]]
                              + list(history["exports"][row["id"]]["asset_sha256"].values()))


def test_export_rejects_stale_setup_before_creating_files(published_search, tmp_path):
    project, history, _directory = published_search
    changed = deepcopy(project)
    changed.solver.initial_temperature += 1.
    destination = tmp_path / "invalid-export"
    with pytest.raises(SimulationError, match="Export setup differs"):
        export_finalist(changed, history, history["published"][0], destination)
    assert not destination.exists()


def test_raw_corner_peak_constraint_prevents_unjustified_mesh_verification(tmp_path):
    project = example(tmp_path)
    experiment = _one_candidate(project, 4e-6)
    # Widening keeps the original short electrodes. Their ends create current
    # crowding whose raw peak is mesh dependent even while integrated loss is stable.
    project.power.measurements.append(MeasurementSpec("corner_peak", "metal_current_density",
        SupportSelector(layers=["metal"]), "maximum", limit=1e12))
    experiment.constraints = ["corner_peak"]
    experiment.maximum_mesh_change = .01
    history = run_experiment(project, "width", tmp_path / "search")
    baseline, candidate = history["rows"]
    evidence = candidate["verification"]
    assert baseline["verification"]["status"] == "mesh_resolved"
    assert candidate["objectives"]["loss"] < baseline["objectives"]["loss"]
    assert candidate["status"] == "mesh_uncertain"
    assert evidence["status"] == "mesh_uncertain"
    peak_changes = [value for key, value in evidence["relative_changes"].items() if key.endswith(":measurement:corner_peak")]
    assert max(peak_changes) > experiment.maximum_mesh_change
    assert all(scenario["measurements"]["corner_peak"]["within_limits"]
               for level in evidence["levels"] for scenario in level["scenarios"])
    assert candidate["objectives"] == evidence["levels"][-1]["objectives"]
    assert candidate["coarse_objectives"] == evidence["levels"][0]["objectives"]
    assert not history["verified"] and not history["published"]


def test_final_history_baseline_uses_refined_objectives_and_preserves_coarse_evidence(tmp_path):
    project = example(tmp_path)
    project.power.experiments[0].max_evaluations = 1
    # Short electrodes introduce a real mesh-dependent spreading resistance.
    for terminal in project.terminals:
        x = terminal.geometry.line[0][0]
        terminal.geometry.line = [[x, 1.5e-6], [x, 2.5e-6]]
    history = run_experiment(project, "width", tmp_path / "search")
    baseline = history["rows"][0]
    evidence = baseline["verification"]
    coarse, fine = evidence["levels"][0]["objectives"], evidence["levels"][-1]["objectives"]
    assert abs(coarse["loss"] - fine["loss"]) > fine["loss"] * 1e-6
    assert baseline["objectives"] == fine == evidence["objectives"]
    assert baseline["coarse_objectives"] == coarse
    assert history["pareto"] == ["baseline"]


def test_combined_grid_rejects_overflow_and_large_legal_sampling_is_deterministic():
    project = Project()
    experiment = OptimizationExperiment("huge", max_evaluations=7, seed=714,
        variables=[DesignVariable("rows", "vias", "rows", "integer", 1, 10**10),
                   DesignVariable("columns", "vias", "columns", "integer", 1, 10**10)])
    with pytest.raises(SimulationError, match="64-bit indexing"):
        list(parameter_candidates(project, experiment))
    experiment.variables[1].maximum = 10**5
    candidates = list(parameter_candidates(project, experiment))
    assert candidates == list(parameter_candidates(project, experiment))
    assert len(candidates) == 6 and len({tuple(sorted(c.items())) for c in candidates}) == 6
    assert {"rows": 10**10, "columns": 10**5} in candidates
    assert all(type(value) is int for candidate in candidates for value in candidate.values())
    experiment.variables[0].maximum = 10**12 + 1
    with pytest.raises(SimulationError, match=r"10\^12"):
        list(parameter_candidates(project, experiment))


def test_wall_time_deadline_is_a_resumable_interruption_not_a_failed_candidate(tmp_path):
    project = example(tmp_path)
    project.power.experiments[0].max_wall_seconds = 1e-12
    history = run_experiment(project, "width", tmp_path / "search")
    assert history["status"] == "interrupted"
    assert history["termination_reason"] == "wall_time_budget"
    assert not history["rows"] and not history["pareto"]
    assert not list((tmp_path / "search").rglob("*.tmp"))
    assert run_experiment(project, "width", tmp_path / "search")["status"] == "interrupted"
