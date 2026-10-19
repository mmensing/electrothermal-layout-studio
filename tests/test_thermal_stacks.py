# SPDX-License-Identifier: MPL-2.0
"""Independent resistance/capacity limits and conservative deposition checks."""
from copy import deepcopy

import numpy as np
import pytest
from solver_helpers import all_region, rectangle

from etlayout.geometry.prepare import prepare_geometry
from etlayout.io.project import load_project, physics_hash, project_from_dict, save_project
from etlayout.materials.database import reference_materials
from etlayout.models import (
    HeatSource,
    Interface,
    PhysicalLayer,
    Project,
    RegionSpec,
    SimulationError,
    Terminal,
    ThermalBoundary,
    TransientSettings,
)
from etlayout.solvers import electrical_heat, solve, solve_electrical, solve_thermal
from etlayout.technology.validation import validate_project

pytestmark = pytest.mark.numerical


def contact_fixture():
    project, mesh = rectangle(layers=("a", "b", "device"))
    project.layers[-1].role = "thermal"
    project.solver.mode = "electrothermal"
    project.solver.relaxation = 1.
    project.terminals = [Terminal("top", "a", value=1.), Terminal("bottom", "b", kind="ground")]
    project.interfaces = [Interface("contact", "a", "b", electrical_kind="lumped", electrical_value=10.)]
    project.thermal_boundaries = [ThermalBoundary("sink_" + key, key, kind="resistance", value=100.)
                                  for key in ("a", "b", "device")]
    for prefix, key in (("terminal", "top"), ("terminal", "bottom"), ("interface", "contact"),
                        *(("thermal", "sink_" + key) for key in ("a", "b", "device"))):
        all_region(mesh, prefix, key)
    return project, mesh


@pytest.mark.parametrize("fraction", [0., .25, .5, 1.])
def test_calibrated_partition_preserves_electrical_loss_and_heat(fraction):
    project, mesh = contact_fixture()
    project.interfaces[0].heat_to_layer_a_fraction = fraction
    result = solve(project, mesh)
    assert result.converged
    assert result.metrics["electrical_power"] == pytest.approx(.1, rel=1e-12)
    assert result.metrics["thermal_power"] == pytest.approx(.1, rel=1e-12)
    assert result.metrics["thermal_balance_error"] < 1e-8
    assert result.fields["a"]["temperature"] == pytest.approx(300 + 10 * fraction, abs=2e-5)
    assert result.fields["b"]["temperature"] == pytest.approx(310 - 10 * fraction, abs=2e-5)
    assert result.analysis["heat_deposition"]["contact"]["deposited_by_layer"] == pytest.approx(
        {key: .1 * share for key, share in (("a", fraction), ("b", 1-fraction)) if share})


def test_named_device_depth_one_way_and_coupled_preserve_loss_and_archive(tmp_path):
    project, mesh = contact_fixture()
    project.interfaces[0].purpose = "device_load"
    project.interfaces[0].heat_layer_id = "device"
    electrical = solve_electrical(project, mesh)
    heat = electrical_heat(electrical)
    assert np.dot(heat["device"], mesh.areas) == pytest.approx(.1, rel=1e-12)
    thermal = solve_thermal(project, mesh, heat=heat)
    coupled = solve(project, mesh)
    assert thermal.converged and coupled.converged
    assert coupled.fields["device"]["temperature"] == pytest.approx(310., abs=2e-5)
    assert coupled.fields["a"]["temperature"] == pytest.approx(300., abs=2e-5)
    assert coupled.metrics["contact_power"] == pytest.approx(.1, rel=1e-12)
    assert coupled.analysis["loss_budget"]["categories"]["device_load"] == pytest.approx(.1, rel=1e-12)
    target = tmp_path / "device-depth.etl"
    save_project(project, target, coupled)
    reopened, results = load_project(target)
    assert reopened.interfaces[0].heat_layer_id == "device"
    assert results.analysis["heat_deposition"] == coupled.analysis["heat_deposition"]
    assert physics_hash(reopened) == physics_hash(project)


def test_named_device_depth_transient_retains_physical_heat_and_storage():
    project, mesh = contact_fixture()
    project.interfaces[0].heat_layer_id = "device"
    project.solver.mode = "thermal"
    project.solver.thermal_include_joule = True
    project.transient = TransientSettings(enabled=True, stop_time=5e-9, time_step=1e-9, output_interval=1e-9)
    result = solve(project, mesh)
    assert result.converged and result.transient.completed
    capacity = 8960 * 385 * .5e-6 * 20e-12
    expected_rise = 10 * (1 - (1 + 1e-9 / (100 * capacity)) ** -5)
    assert result.metrics["temperature_max"] == pytest.approx(300 + expected_rise, abs=1e-8)
    assert result.metrics["thermal_power"] == pytest.approx(.1, rel=1e-12)
    assert result.metrics["thermal_storage_rate"] > 0
    assert result.metrics["thermal_balance_error"] < 1e-8


@pytest.mark.parametrize("fraction,target,match", [(-.1, "", "fraction"), (1.1, "", "fraction"),
    (float("nan"), "", "fraction"), (.2, "device", "alternatives"), (.5, "missing", "missing")])
def test_deposition_configuration_rejects_invalid_or_ambiguous_settings(fraction, target, match):
    project, mesh = contact_fixture()
    project.interfaces[0].heat_to_layer_a_fraction = fraction
    project.interfaces[0].heat_layer_id = target
    with pytest.raises(SimulationError, match=match):
        validate_project(project)


def test_named_heat_target_cannot_clip_or_renormalize_missing_support():
    project, mesh = contact_fixture()
    project.interfaces[0].heat_layer_id = "device"
    mesh.regions["layer:device"] = mesh.elements("device")[:2]
    with pytest.raises(SimulationError, match="complete interface support"):
        solve_electrical(project, mesh)


def test_legacy_default_deposition_preserves_physics_identity():
    project, _ = contact_fixture()
    legacy = project.to_dict()
    for interface in legacy["interfaces"]:
        interface.pop("heat_to_layer_a_fraction")
        interface.pop("heat_layer_id")
    assert physics_hash(project_from_dict(legacy)) == physics_hash(project)
    changed = deepcopy(project)
    changed.interfaces[0].heat_to_layer_a_fraction = .4
    assert physics_hash(changed) != physics_hash(project)
    changed.interfaces[0].heat_layer_id = "device"
    assert physics_hash(changed) != physics_hash(project)


@pytest.mark.parametrize("kz,thickness", [(1.4, 1e-6), (3., 2e-6), (.2, 5e-6)])
def test_explicit_dielectric_equals_effective_uniform_series_resistance(kz, thickness):
    effective, mesh = rectangle(layers=("bottom", "top"))
    effective.materials["film"] = deepcopy(effective.materials["copper"])
    effective.materials["film"].id = "film"
    effective.materials["film"].thermal_conductivity_z = kz
    effective.heat_sources = [HeatSource("heater", "top", value=1e6)]
    effective.thermal_boundaries = [ThermalBoundary("sink", "bottom", kind="temperature", value=300.)]
    effective.interfaces = [Interface("gap", "bottom", "top", thermal_kind="stack", material_id="film", height=thickness)]
    for prefix, key in (("interface", "gap"), ("thermal", "sink"), ("heat", "heater")):
        all_region(mesh, prefix, key)
    reference = solve_thermal(effective, mesh)
    explicit = deepcopy(effective)
    explicit.layers.append(PhysicalLayer("film", "Passivation", "film", role="insulator", thickness=thickness))
    mesh.regions["layer:film"] = mesh.elements("top").copy()
    explicit.interfaces = [Interface("bf", "bottom", "film", thermal_kind="stack"),
                           Interface("ft", "film", "top", thermal_kind="stack")]
    for key in ("bf", "ft"):
        all_region(mesh, "interface", key)
    resolved = solve_thermal(explicit, mesh)
    expected = 300 + 1e6 * (2 * .5e-6 / (2 * 10.) + thickness / kz)
    assert reference.converged and resolved.converged
    assert reference.metrics["temperature_max"] == pytest.approx(expected, abs=2e-9)
    assert resolved.metrics["temperature_max"] == pytest.approx(expected, abs=2e-9)
    assert resolved.metrics["heat_out"] == pytest.approx(reference.metrics["heat_out"], rel=1e-10)
    assert "film" in resolved.fields and "potential" not in resolved.fields["film"]


def physical_stack():
    footprint = RegionSpec(polygons=[dict(bounds=[0, 0, 10e-6, 10e-6])])
    project = Project(materials=reference_materials(), layers=[
        PhysicalLayer("bottom", "Bottom", "copper", 1e-6, 0., geometry=deepcopy(footprint)),
        PhysicalLayer("film", "SiN", "sin", 1e-6, 1e-6, "insulator", geometry=deepcopy(footprint)),
        PhysicalLayer("top", "Top", "copper", 1e-6, 2e-6, geometry=deepcopy(footprint))])
    project.interfaces = [Interface("lower", "bottom", "film", deepcopy(footprint), thermal_kind="stack"),
                          Interface("upper", "film", "top", deepcopy(footprint), thermal_kind="stack")]
    return project


def test_effective_gap_cannot_duplicate_explicit_film_and_legacy_bridge_is_diagnosed():
    project = physical_stack()
    project.interfaces.append(Interface("bridge", "bottom", "top", RegionSpec(full_domain=True),
                                       material_id="sin", height=1e-6, thermal_kind="stack"))
    with pytest.raises(SimulationError, match="duplicates explicit thermal layer film"):
        prepare_geometry(project)
    project.interfaces[-1].thermal_kind = "conductance"
    geometry = prepare_geometry(project)
    assert any("bypasses explicit layer film" in diagnostic.message for diagnostic in geometry.diagnostics)
    project.interfaces.pop()
    geometry = prepare_geometry(project)
    assert not any("bypasses" in diagnostic.message for diagnostic in geometry.diagnostics)


def test_named_heat_target_participates_in_preparation_identity_without_remeshing():
    from etlayout.simulation.runner import _geometry_key, _mesh_key

    project = physical_stack()
    before_geometry, before_mesh = _geometry_key(project), _mesh_key(project)
    project.interfaces[0].heat_layer_id = "top"
    assert _geometry_key(project) != before_geometry
    assert _mesh_key(project) == before_mesh
    project.layers[-1].geometry = RegionSpec(polygons=[dict(bounds=[0, 0, 5e-6, 10e-6])])
    with pytest.raises(SimulationError, match="complete interface support"):
        prepare_geometry(project)


def test_explicit_film_has_lateral_anisotropy_and_conservative_heat_capacity():
    from etlayout.solvers.fem import Space
    from etlayout.solvers.thermal import heat_capacity

    project, mesh = rectangle(layers=("metal", "film"))
    film = project.layers[-1]
    film.role, film.thickness = "insulator", 2e-6
    material = deepcopy(project.materials["copper"])
    material.id, material.density, material.specific_heat = "dielectric", 2200., 703.
    material.thermal_anisotropy, material.thermal_conductivity_z = [.2, 2.], 1.4
    project.materials[material.id] = material
    film.material_id = material.id
    space = Space(project, mesh, electrical=False)
    capacity = heat_capacity(project, space)
    expected = mesh.areas.sum() * (8960. * 385. * .5e-6 + 2200. * 703. * 2e-6)
    assert capacity.sum() == pytest.approx(expected, rel=1e-13)
    electrical_space = Space(project, mesh, electrical=True)
    assert set(electrical_space.layers) == {"metal"}
