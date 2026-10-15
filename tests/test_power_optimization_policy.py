# SPDX-License-Identifier: MPL-2.0
"""Reject invalid experiment intent and resolve physical contact length scales."""
import json
from copy import deepcopy

import numpy as np
import pytest
from test_power_optimization import example

from etlayout.models import (
    Interface,
    MeasurementSpec,
    RefinementRegion,
    RegionSpec,
    SimulationError,
    SupportSelector,
)
from etlayout.optimization import _implementation_identity, run_experiment, validate_experiment


@pytest.mark.parametrize('allowed,feasible', [(.5, False), (1., True)])
def test_actual_exceedance_fraction_controls_equal_service_feasibility(tmp_path, allowed, feasible):
    project = example(tmp_path)
    experiment = project.power.experiments[0]
    experiment.max_evaluations = 1
    experiment.scenarios = experiment.scenarios[:1]
    project.power.measurements.append(MeasurementSpec('fraction', 'potential', aggregation='exceedance',
        threshold=-1., threshold_units='V', max_exceedance_fraction=allowed))
    experiment.constraints = ['fraction']
    history = run_experiment(project, experiment.id, tmp_path / 'fraction-search')
    baseline = history['rows'][0]
    report = baseline['scenarios'][0]['measurements']['fraction']
    assert report['value'] == pytest.approx(1.)
    assert report['within_limits'] is feasible
    assert (baseline['status'] == 'constraint_failure') is not feasible
    if not feasible:
        assert not history['verified'] and not history['published']


@pytest.mark.parametrize('legacy', [True, False])
def test_exceedance_constraint_requires_explicit_fraction_and_migration(tmp_path, legacy):
    project = example(tmp_path)
    experiment = project.power.experiments[0]
    spec = MeasurementSpec('fraction', 'potential', aggregation='exceedance', threshold=.1)
    if legacy:
        spec.limit, spec.threshold, spec.max_exceedance_fraction = .1, None, .5
    project.power.measurements.append(spec)
    experiment.constraints = ['fraction']
    with pytest.raises(SimulationError, match='Legacy/ambiguous' if legacy else 'explicit limits'):
        validate_experiment(project, experiment)


@pytest.mark.parametrize("case,diagnostic", [
    ("transient", "steady"), ("thermal_only", "electrical"),
    ("no_scenarios", "scenarios"), ("no_objectives", "objectives"),
    ("zero_budget", "budgets"), ("bad_mesh_factors", "descending"),
    ("nan_mesh_change", "uncertainty"), ("duplicate_measurement", "Duplicate measurement"),
    ("bad_objective_direction", "direction"), ("unlimited_constraint", "explicit limits"),
    ("same_port", "distinct"), ("scenario_geometry_edit", "active geometry"),
    ("scenario_material_edit", "outside"), ("duplicate_scenario", "unique"),
    ("stale_recipe", "stale routing"), ("variable_recipe", "enabled"),
    ("missing_parameter", "absent recipe"), ("bad_kind", "variable kind"),
    ("empty_choices", "explicit choices"), ("nan_bounds", "bounds"),
    ("stale_device", "protected device"),
])
def test_invalid_intent_cannot_enter_numerical_search(tmp_path, case, diagnostic):
    project = example(tmp_path)
    experiment = project.power.experiments[0]
    if case == "transient":
        project.transient.enabled = True
    elif case == "thermal_only":
        project.solver.mode = "thermal"
    elif case == "no_scenarios":
        experiment.scenarios = []
    elif case == "no_objectives":
        experiment.objectives = []
    elif case == "zero_budget":
        experiment.max_evaluations = 0
    elif case == "bad_mesh_factors":
        experiment.refinement_factors = [1., 2.]
    elif case == "nan_mesh_change":
        experiment.maximum_mesh_change = np.nan
    elif case == "duplicate_measurement":
        project.power.measurements.append(deepcopy(project.power.measurements[0]))
    elif case == "bad_objective_direction":
        experiment.objectives[0].direction = "unrecognized"
    elif case == "unlimited_constraint":
        experiment.constraints = ["loss"]
    elif case == "same_port":
        experiment.scenarios[0].reference_port = experiment.scenarios[0].service_port
    elif case == "scenario_geometry_edit":
        experiment.scenarios[0].settings = {"layers.metal.thickness": 1e-12}
    elif case == "scenario_material_edit":
        experiment.scenarios[0].settings = {"materials.copper.resistivity.value": 1e-12}
    elif case == "duplicate_scenario":
        experiment.scenarios.append(deepcopy(experiment.scenarios[0]))
    elif case == "stale_recipe":
        experiment.recipe_ids.append("missing")
    elif case == "variable_recipe":
        experiment.variables[0].recipe_id = "missing"
    elif case == "missing_parameter":
        experiment.variables[0].parameter = "invented"
    elif case == "bad_kind":
        experiment.variables[0].kind = "arbitrary_python"
    elif case == "empty_choices":
        experiment.variables[0].kind = "choice"
        experiment.variables[0].values = []
    elif case == "nan_bounds":
        experiment.variables[0].minimum = np.nan
    elif case == "stale_device":
        experiment.protected_devices = ["missing"]
    with pytest.raises(SimulationError, match=diagnostic):
        validate_experiment(project, experiment)


@pytest.mark.parametrize("specific_resistance,resolved", [(1e-12, True), (1e-15, False)])
def test_actual_distributed_contact_verification_tracks_transfer_length(tmp_path, specific_resistance, resolved):
    project = example(tmp_path)
    experiment = project.power.experiments[0]
    experiment.max_evaluations = 1
    experiment.scenarios = experiment.scenarios[:1]
    experiment.maximum_mesh_change = 1.
    upper = deepcopy(project.layers[0])
    upper.id, upper.name, upper.z = "upper", "Upper return", 2e-6
    upper.mesh_size = 1e-6
    project.layers.append(upper)
    project.terminals[1].layer_id = "upper"
    project.interfaces = [Interface("transfer", "metal", "upper", RegionSpec(full_domain=True),
        electrical_kind="specific", electrical_value=specific_resistance)]
    project.mesh.refinements = [RefinementRegion("original", RegionSpec(full_domain=True), 1e-6)]
    project.power.measurements.append(MeasurementSpec("physical_hotspot", "metal_current_density",
        SupportSelector(layers=["metal"]), "maximum", averaging_length=2e-6))
    history = run_experiment(project, "width", tmp_path/"search")
    baseline = history["rows"][0]
    assert baseline["status"] == "feasible_provisional", baseline
    verification = baseline["verification"]
    contacts = [r for r in verification["guidance"] if r.get("interface") == "transfer"]
    assert len(contacts) == 2
    rho = project.materials["copper"].resistivity.value
    expected_length = np.sqrt(specific_resistance / (rho / project.layers[0].thickness))
    for guidance in contacts:
        assert guidance["transfer_length_m"] == pytest.approx(expected_length)
        assert guidance["resolved"] == resolved
        if resolved:
            assert guidance["target_size_m"] <= expected_length/4
    assert any("hotspot" in r for r in verification["guidance"])
    if not resolved:
        assert verification["status"] == "mesh_uncertain"
    assert not history["published"]
    for level in verification["levels"]:
        scenario = level["scenarios"][0]
        assert scenario["service"]["delivered_current"] == pytest.approx(.01, rel=1e-7)
        assert scenario["metrics"]["power_balance_error"] < 1e-7


def test_frozen_identity_matches_source_and_rejects_unstamped_or_changed_builds(tmp_path):
    (tmp_path/"data").mkdir()
    module = tmp_path/"numerics.py"
    module.write_text("value = 1\n", encoding="utf-8")
    original = _implementation_identity(tmp_path)
    with pytest.raises(SimulationError, match="lacks implementation"):
        _implementation_identity(tmp_path, frozen=True)
    stamp = tmp_path/"data"/"implementation.json"
    stamp.write_text(json.dumps({"source_sha256": original}), encoding="utf-8")
    assert _implementation_identity(tmp_path, frozen=True) == original
    module.write_text("value = 2\n", encoding="utf-8")
    assert _implementation_identity(tmp_path) != original
    stamp.write_text(json.dumps({"source_sha256": "frozen:0.3.0"}), encoding="utf-8")
    with pytest.raises(SimulationError, match="invalid"):
        _implementation_identity(tmp_path, frozen=True)
