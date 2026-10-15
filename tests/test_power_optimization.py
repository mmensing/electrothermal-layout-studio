# SPDX-License-Identifier: MPL-2.0
"""Actual meshing, solver and file export tests; no simulated search results."""
from copy import deepcopy
from pathlib import Path

import klayout.db as kdb
import numpy as np
import pytest

from etlayout.io.project import file_hash
from etlayout.materials.database import reference_materials
from etlayout.models import (
    DesignVariable,
    LayoutSource,
    MeasurementSpec,
    ObjectiveSpec,
    OperatingScenario,
    OptimizationExperiment,
    PhysicalLayer,
    Project,
    RegionSpec,
    RoutingRecipe,
    RoutingRules,
    SimulationError,
    SupportSelector,
    Terminal,
)
from etlayout.optimization import (
    export_finalist,
    parameter_candidates,
    pareto_front,
    run_experiment,
    validate_experiment,
)


def example(tmp_path):
    layout = kdb.Layout()
    layout.dbu = .001
    cell = layout.create_cell("BUS")
    cell.shapes(layout.layer(1, 0)).insert(kdb.Box(0, 1000, 10000, 3000))
    path = tmp_path / "source.gds"
    layout.write(str(path))
    project = Project(layout=LayoutSource(str(path), "BUS", file_hash(path)), materials=reference_materials())
    project.layers = [PhysicalLayer("metal", "Metal", "copper", thickness=1e-6,
                                    geometry=RegionSpec(sources=["1/0"]), loss_role="routing_metal")]
    project.terminals = [Terminal("left", "metal", RegionSpec(line=[[0, 1e-6], [0, 3e-6]]), kind="ground"),
                         Terminal("right", "metal", RegionSpec(line=[[10e-6, 1e-6], [10e-6, 3e-6]]), value=.1)]
    project.mesh.max_size = 1.5e-6
    project.mesh.min_size = .3e-6
    project.mesh.contact_size = .75e-6
    project.power.rules = RoutingRules(minimum_width={"metal": .5e-6})
    project.power.recipes = [RoutingRecipe("bus", layer_id="metal", mask="1/0",
         edit_region=RegionSpec(polygons=[{"bounds": [0, 0, 10e-6, 4e-6]}]),
         parameters={"width": 2e-6, "center": 2e-6}, source_hash=project.layout.sha256)]
    project.power.measurements = [MeasurementSpec("loss", "joule_density", SupportSelector(layers=["metal"]), "integral")]
    experiment = OptimizationExperiment("width", recipe_ids=["bus"],
        variables=[DesignVariable("width", "bus", "width", minimum=2e-6, maximum=4e-6, values=[2e-6, 4e-6])],
        scenarios=[OperatingScenario("nominal", service_port="right", reference_port="left", required_current=.01),
                   OperatingScenario("high", service_port="right", reference_port="left", required_current=.02)],
        objectives=[ObjectiveSpec("loss")], max_evaluations=3, maximum_mesh_change=.08)
    project.power.experiments = [experiment]
    return project


def test_real_equal_service_search_refines_exports_and_resumes(tmp_path):
    project = example(tmp_path)
    history = run_experiment(project, "width", tmp_path/"search")
    assert history["status"] == "complete", history
    baseline = history["rows"][0]
    assert baseline["status"] == "feasible_provisional", baseline
    # rho=1.72e-8, L=10um, W=2um, t=1um; uniform strip's exact loss.
    rho = project.materials["copper"].resistivity.value
    expected = .02**2*rho*10e-6/(2e-6*1e-6)
    assert baseline["objectives"]["loss"] == pytest.approx(expected, rel=.002)
    assert any(row["status"] == "duplicate" for row in history["rows"])
    assert len(history["verified"]) == 1, history
    best = next(r for r in history["rows"] if r["id"] in history["verified"])
    assert best["objectives"]["loss"] < expected * .7
    for level in best["verification"]["levels"]:
        for scenario in level["scenarios"]:
            assert scenario["service"]["status"] == "satisfied"
            assert scenario["service"]["delivered_current"] == pytest.approx(scenario["service"]["required_current"], rel=1e-7)
    assert history["exports"][best["id"]]["status"] == "verified_export"
    export = Path(history["exports"][best["id"]]["directory"])
    assert list(export.glob("*.gds")) and list(export.glob("*.oas"))
    assert run_experiment(project, "width", tmp_path/"search") == history
    changed = deepcopy(project)
    changed.power.experiments[0].scenarios[0].required_current *= 2
    with pytest.raises(SimulationError, match="identity"):
        run_experiment(changed, "width", tmp_path/"search")
    with pytest.raises(SimulationError, match="verified"):
        export_finalist(project, history, "baseline", tmp_path/"reject")


def test_invalid_low_loss_under_delivery_never_enters_pareto(tmp_path):
    project = example(tmp_path)
    project.power.experiments[0].max_evaluations = 2
    for scenario in project.power.experiments[0].scenarios:
        scenario.service_mode = "fixed_voltage"
        scenario.settings = {"terminals.right.value": 1e-6}
    history = run_experiment(project, "width", tmp_path/"search")
    assert all(r["status"] == "service_failure" for r in history["rows"]), history
    assert not history["pareto"] and not history["verified"]
    for row in history["rows"]:
        assert row["scenarios"][0]["service"]["delivered_current"] < .01


def test_cancelled_search_is_atomic_and_resumable(tmp_path):
    project = example(tmp_path)
    project.power.experiments[0].max_evaluations = 1
    history = run_experiment(project, "width", tmp_path/"search", cancel=lambda: True)
    assert history["status"] == "cancelled"
    assert not list((tmp_path/"search").glob("*.tmp"))
    restored = run_experiment(project, "width", tmp_path/"search")
    assert restored["status"] == "complete" and len(restored["rows"]) == 1


def test_failed_geometry_and_missing_selectors_are_preserved(tmp_path):
    project = example(tmp_path)
    project.power.experiments[0].variables[0].values = [.1e-6]
    project.power.experiments[0].variables[0].minimum = .1e-6
    project.power.experiments[0].max_evaluations = 2
    project.power.measurements[0].selector.layers = ["absent"]
    history = run_experiment(project, "width", tmp_path/"search")
    assert history["rows"][0]["status"] == "stale_selector", history
    assert history["rows"][1]["status"] == "rule_violation"
    assert history["rows"][1]["violations"] and not history["pareto"]


def test_seeded_grid_is_bounded_and_feasible_first_pareto():
    project = Project()
    experiment = OptimizationExperiment("x", max_evaluations=5, seed=123,
        variables=[DesignVariable("n", "r", "rows", "integer", 1, 100),
                   DesignVariable("w", "r", "width", minimum=1e-6, maximum=100e-6)])
    values = list(parameter_candidates(project, experiment))
    assert len(values) == 4 and values == list(parameter_candidates(project, experiment))
    assert all(type(v["n"]) is int and 1 <= v["n"] <= 100 for v in values)
    objectives = [ObjectiveSpec("a"), ObjectiveSpec("b", "max")]
    rows = [{"id": "invalid-low", "status": "service_failure", "objectives": {"a": 0., "b": 100.}},
            {"id": "valid", "status": "feasible_provisional", "objectives": {"a": 2., "b": 3.}},
            {"id": "dominated", "status": "feasible_provisional", "objectives": {"a": 3., "b": 1.}},
            {"id": "tradeoff", "status": "mesh_uncertain", "objectives": {"a": 1., "b": 2.}},
            {"id": "nan", "status": "feasible_provisional", "objectives": {"a": np.nan, "b": 4.}}]
    assert pareto_front(rows, objectives) == ["valid", "tradeoff"]


@pytest.mark.parametrize("mutation", ["fixed_zero", "report_thickness", "raw_corner", "mesh_policy", "active_area", "fractional_via"])
def test_invalid_experiment_contracts_fail_before_search(tmp_path, mutation):
    project = example(tmp_path)
    experiment = project.power.experiments[0]
    if mutation == "fixed_zero":
        experiment.scenarios[0].service_mode = "fixed_voltage"
        experiment.scenarios[0].required_current = 0
    elif mutation == "report_thickness":
        project.power.measurements[0].quantity = "effective_current_density"
    elif mutation == "raw_corner":
        project.power.measurements[0].quantity = "metal_current_density"
        project.power.measurements[0].aggregation = "maximum"
    elif mutation == "mesh_policy":
        experiment.refinement_factors = [1.]
    elif mutation == "active_area":
        experiment.scenarios[0].settings = {"layers.metal.thickness": 2e-6}
    elif mutation == "fractional_via":
        experiment.variables[0].kind = "integer"
    with pytest.raises(SimulationError):
        validate_experiment(project, experiment)
