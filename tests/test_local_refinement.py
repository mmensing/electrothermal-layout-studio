# SPDX-License-Identifier: MPL-2.0
"""Independent local-sizing contracts and a uniform analytical resistor study."""
from copy import deepcopy

import numpy as np
import pytest
from shapely.geometry import box

from etlayout.examples import make_example
from etlayout.geometry.prepare import geometry_region, prepare_geometry, resolve_region
from etlayout.io.project import configuration_hash, load_project, save_project
from etlayout.meshing.refinement import (
    RefinementPlan,
    apply_refinement_plan,
    local_study_metrics,
    screen_resources,
    suggest_refinements,
)
from etlayout.models import (
    DeviceGroup,
    ElectricalModel,
    Interface,
    Material,
    MeasurementSpec,
    MeshSettings,
    PhysicalLayer,
    PhysicalPort,
    PortLanding,
    Project,
    PropertyModel,
    SimulationError,
    SupportSelector,
    Terminal,
    ThermalBoundary,
)
from etlayout.simulation.execution import ExecutionSettings
from etlayout.simulation.runner import run_project
from etlayout.simulation.sweep import mesh_refinement_study
from etlayout.visualization.sampling import metric_unit


def contact_project():
    geometry = geometry_region(box(0, 0, 100e-6, 20e-6))
    material = Material("metal", "Synthetic conductor", resistivity=PropertyModel(value=1e-6))
    return Project(materials={material.id: material}, layers=[
        PhysicalLayer("lower", "Lower", material.id, 1e-6, geometry=geometry),
        PhysicalLayer("upper", "Upper", material.id, 2e-6, z=2e-6, geometry=geometry)],
        interfaces=[Interface("contact", "lower", "upper", geometry_region(box(80e-6, 0, 100e-6, 20e-6)),
                              "specific", 1e-10)],
        mesh=MeshSettings(max_size=20e-6, min_size=5e-6, contact_size=10e-6))


@pytest.mark.fast
def test_transfer_length_uses_sheet_conductance_and_exact_support():
    project = contact_project()
    before = project.to_dict()
    plan = suggest_refinements(project)
    contacts = [item for item in plan.suggestions if item.purpose == "Contact transfer"]
    assert [item.length for item in contacts] == pytest.approx([10e-6, np.sqrt(2) * 10e-6])
    geometry = prepare_geometry(project)
    for item in contacts:
        support = resolve_region(item.geometry, {}, geometry.domain)
        assert support.equals(geometry.interfaces["contact"])
        assert item.size == pytest.approx(item.length / 4)
    restored = RefinementPlan.from_dict(plan.to_dict())
    selected = [restored.suggestions[0].id]
    candidate = apply_refinement_plan(project, restored, selected)
    assert candidate.mesh.min_size == pytest.approx(2.5e-6)
    assert candidate.mesh.max_size == project.mesh.max_size
    assert len(candidate.mesh.refinements) == 1
    assert project.to_dict() == before


@pytest.mark.fast
def test_gate_length_is_not_finger_width_and_unsupported_bias_is_explicit():
    project = contact_project()
    project.layers[0].electrical = ElectricalModel(kind="hemt", gate_regions=["gate"])
    project.terminals.append(Terminal("gate", "lower", geometry_region(box(45e-6, 0, 46e-6, 20e-6)), "gate", 1.))
    plan = suggest_refinements(project)
    channel = next(item for item in plan.suggestions if item.purpose == "Gated channel")
    assert channel.length == pytest.approx(1e-6)
    assert channel.size == pytest.approx(.25e-6)
    assert any("solve the biased HEMT" in value for value in plan.warnings)


@pytest.mark.fast
@pytest.mark.parametrize("mutation,match", [
    (lambda p, plan: setattr(p.layers[0], "thickness", 3e-6), "different configuration"),
    (lambda p, plan: setattr(plan, "max_memory_bytes", 0), "memory budget"),
    (lambda p, plan: setattr(plan.suggestions[0], "size", float("nan")), "positive finite"),
    (lambda p, plan: plan.suggestions.append(deepcopy(plan.suggestions[0])), "unique"),
])
def test_invalid_or_stale_plans_are_rejected(mutation, match):
    project = contact_project()
    plan = suggest_refinements(project)
    mutation(project, plan)
    with pytest.raises(SimulationError, match=match):
        apply_refinement_plan(project, plan)


@pytest.mark.fast
@pytest.mark.parametrize("budget", ["max_elements", "max_dofs", "max_memory_bytes"])
def test_planning_budget_blocks_expensive_candidates(budget):
    project = contact_project()
    plan = suggest_refinements(project)
    setattr(plan, budget, 1)
    candidate = apply_refinement_plan(project, plan)
    with pytest.raises(SimulationError, match="exceeds budget"):
        screen_resources(candidate, plan)


@pytest.mark.fast
def test_sharing_uses_like_sized_groups_and_has_no_fabricated_fallback():
    project = contact_project()
    project.power.devices = [DeviceGroup("a", active_width=1e-3), DeviceGroup("b", active_width=1e-3)]
    plan = suggest_refinements(project)
    plan.hotspot_measurements = []
    class Result:
        analysis = {"devices": {"a": {"status": "available", "current": 1., "source_voltage": .01},
                                "b": {"status": "available", "current": 2., "source_voltage": .02}}}
    metrics = local_study_metrics(project, Result(), plan)
    assert metrics["device_current_sharing_error"] == pytest.approx(2 / 3)
    assert metrics["device_source_voltage_spread"] == pytest.approx(.01)
    plan.sharing_groups.append("missing")
    with pytest.raises(SimulationError, match="missing"):
        local_study_metrics(project, Result(), plan)


@pytest.mark.numerical
def test_local_refinement_preserves_uniform_analytical_resistor_and_hotspot(tmp_path):
    project = make_example("E1", tmp_path / "layout")
    clipped = geometry_region(box(20.3e-6, .7e-6, 48.8e-6, 8.9e-6).difference(box(31e-6, 3e-6, 34e-6, 6e-6)))
    project.power.devices.extend([DeviceGroup("inner", SupportSelector(layers=["metal"], geometry=clipped)),
                                  DeviceGroup("outer", SupportSelector(devices=["inner"]))])
    project.power.measurements.append(MeasurementSpec("averaged_j", "metal_current_density",
        SupportSelector(layers=["metal"]), averaging_length=10e-6))
    project.power.measurements.extend([
        MeasurementSpec("direct", "metal_current_density", SupportSelector(layers=["metal"], geometry=clipped), averaging_length=10e-6),
        MeasurementSpec("nested", "metal_current_density", SupportSelector(devices=["outer"]), averaging_length=10e-6)])
    source = run_project(project)
    assert source.converged
    plan = suggest_refinements(project, source)
    assert any(item.purpose == "Averaged hotspot" for item in plan.suggestions)
    direct = resolve_region(next(item.geometry for item in plan.suggestions if item.id == "hotspot-direct"), {})
    nested = resolve_region(next(item.geometry for item in plan.suggestions if item.id == "hotspot-nested"), {})
    assert direct.symmetric_difference(nested).area < 1e-24
    assert direct.difference(resolve_region(clipped, {})).area < 1e-24
    before = project.to_dict()
    rows = mesh_refinement_study(project, [1., .5], local_plan=plan,
                                execution=ExecutionSettings(study_workers=1))
    assert all(row.result and row.result.converged and not row.error for row in rows)
    assert rows[1].result.mesh.statistics["elements"] > rows[0].result.mesh.statistics["elements"]
    assert rows[1].metrics["effective_resistance"] == pytest.approx(.084, rel=1e-6)
    assert rows[1].metrics["effective_resistance_relative_change"] < 1e-6
    assert rows[1].metrics["regularized_metal_current_density:averaged_j_relative_change"] < 1e-6
    assert rows[1].metrics["power_balance_error"] < project.solver.power_balance_tolerance
    assert project.to_dict() == before
    assert rows[1].result.configuration["mesh"]["max_size"] == project.mesh.max_size
    save_project(project, tmp_path / "source.etl", source)
    restored, loaded = load_project(tmp_path / "source.etl")
    assert configuration_hash(restored) == configuration_hash(project)
    compatible_plan = suggest_refinements(restored, loaded)
    assert any(item.purpose == "Averaged hotspot" for item in compatible_plan.suggestions)
    assert metric_unit("regularized_metal_current_density:averaged_j") == "A/m²"


@pytest.mark.numerical
def test_coupled_local_study_measures_conservative_uniform_load_sharing():
    # Intentionally equipotential electrodes define an independent uniform
    # area-specific-load limit, not a routing example or prescribed cell currents.
    project = contact_project()
    support = geometry_region(box(0, 0, 100e-6, 20e-6))
    project.interfaces = [Interface("load", "lower", "upper", support, "load", 1e-7,
                                   thermal_kind="conductance", thermal_value=1e7, purpose="device_load")]
    project.terminals = [Terminal("d", "lower", support, "floating"), Terminal("s", "upper", support, "floating")]
    project.power.ports = [PhysicalPort("D", [PortLanding("d")], "voltage", .1),
                           PhysicalPort("S", [PortLanding("s")], "ground", 0.)]
    project.power.devices = [DeviceGroup(identifier, SupportSelector(layers=["lower"], geometry=
                            geometry_region(box(x0, 0, x1, 20e-6))), active_area=1e-9,
                            source_port="S", drain_port="D") for identifier, x0, x1 in
                            (("left", 0., 50e-6), ("right", 50e-6, 100e-6))]
    project.thermal_boundaries = [ThermalBoundary("backside", "upper", support, "resistance_area", 1e-6, 300.)]
    project.solver.mode = "electrothermal"
    project.power.measurements = [MeasurementSpec("thermal_hotspot", "temperature",
        SupportSelector(layers=["lower"]), averaging_length=20e-6)]
    plan = suggest_refinements(project)
    rows = mesh_refinement_study(project, [1., .5], local_plan=plan,
                                execution=ExecutionSettings(cpu_threads=2, study_workers=2))
    for row in rows:
        assert row.result and row.result.converged and not row.error
        assert row.metrics["effective_resistance"] == pytest.approx(50., rel=1e-9)
        assert row.metrics["total_current"] == pytest.approx(.002, rel=1e-9)
        assert row.metrics["electrical_power"] == pytest.approx(.0002, rel=1e-9)
        assert row.metrics["device_current_sharing_error"] < 1e-9
        assert row.metrics["device_source_voltage_spread"] < 1e-12
        assert row.metrics["regularized_temperature:thermal_hotspot"] == pytest.approx(300.105, abs=1e-4)
        assert row.metrics["thermal_balance_error"] < project.solver.thermal_balance_tolerance
        assert row.metrics["power_balance_error"] < project.solver.power_balance_tolerance
        assert row.metrics["study_execution"]["actual_workers"] == 1
        for record in row.result.analysis["devices"].values():
            assert record["current"] == pytest.approx(.001, rel=1e-9)
    assert rows[1].metrics["device_current_sharing_error_absolute_change"] < 1e-9


@pytest.mark.fast
@pytest.mark.parametrize("factors", [[1.], [1., 1.], [1., .5, .75], [1., .9, .8, .7, .6], [1., float("nan")]])
def test_local_study_is_bounded_and_monotonic(factors):
    project = contact_project()
    plan = suggest_refinements(project)
    with pytest.raises(SimulationError):
        mesh_refinement_study(project, factors, local_plan=plan)


@pytest.mark.fast
def test_empty_and_unknown_selection_is_rejected():
    project = contact_project()
    plan = suggest_refinements(project)
    for selected in ([], ["unknown"]):
        with pytest.raises(SimulationError, match="Select at least one"):
            apply_refinement_plan(project, plan, selected)


@pytest.mark.fast
def test_unsupported_plan_version_has_migration_message():
    with pytest.raises(SimulationError, match="supported version"):
        RefinementPlan.from_dict({"version": 2})
