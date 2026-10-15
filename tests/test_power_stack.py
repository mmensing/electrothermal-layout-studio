# SPDX-License-Identifier: MPL-2.0
"""Literal-stack consistency and independently integrated thermal conductance."""
import copy

import pytest

from etlayout.geometry.prepare import prepare_geometry
from etlayout.materials.database import reference_materials
from etlayout.meshing.generate import generate_mesh
from etlayout.models import Interface, PhysicalLayer, Project, RegionSpec, SimulationError, ThermalBoundary
from etlayout.solvers import solve_thermal
from etlayout.technology.validation import validate_project


def rect(bounds):
    return RegionSpec(polygons=[dict(bounds=bounds)])


def stack():
    footprint = rect([0, 0, 10e-6, 10e-6])
    project = Project(materials=reference_materials(), layers=[
        PhysicalLayer("lower", "Lower", "copper", thickness=1e-6, z=0., geometry=copy.deepcopy(footprint)),
        PhysicalLayer("upper", "Upper", "copper", thickness=1e-6, z=2e-6, geometry=copy.deepcopy(footprint))])
    project.mesh.max_size = 1e-6
    project.mesh.min_size = .1e-6
    project.mesh.contact_size = .5e-6
    project.interfaces = [Interface("via", "lower", "upper", rect([4e-6, 4e-6, 6e-6, 6e-6]),
                                    electrical_kind="via", material_id="copper", height=1e-6,
                                    thermal_kind="conductance", thermal_value=1e7,
                                    model_kind="physical_via", height_mode="stack", enclosure=.5e-6)]
    return project


def test_stack_height_recomputed_and_stale_explicit_height_rejected():
    project = stack()
    prepare_geometry(project)
    assert project.interfaces[0].height == pytest.approx(1e-6)
    project.layers[1].z = 4e-6
    prepare_geometry(project)
    assert project.interfaces[0].height == pytest.approx(3e-6)
    project.interfaces[0].height_mode = "explicit"
    project.layers[0].thickness = 2e-6
    with pytest.raises(SimulationError, match="disagrees"):
        prepare_geometry(project)


@pytest.mark.parametrize("change,match", [
    (lambda p: setattr(p.layers[1], "z", .5e-6), "volumes overlap"),
    (lambda p: setattr(p.interfaces[0], "layer_b", "missing"), "missing physical endpoint"),
    (lambda p: setattr(p.interfaces[0], "enclosure", 5e-6), "enclosure"),
    (lambda p: setattr(p.power.rules, "transitions", [["lower", "other"]]), "illegal layer transition"),
    (lambda p: setattr(p.interfaces[0], "geometry", rect([-1e-6, 4e-6, 1e-6, 6e-6])), "ROI truncates"),
])
def test_illegal_physical_connections_are_rejected(change, match):
    project = stack()
    change(project)
    with pytest.raises(SimulationError, match=match):
        prepare_geometry(project)


def test_declared_dielectric_replacement_and_illegal_conductor_crossing():
    project = stack()
    project.layers.append(PhysicalLayer("oxide", "Oxide", "sio2", thickness=1e-6, z=1e-6,
                                        role="insulator", geometry=rect([0, 0, 10e-6, 10e-6])))
    with pytest.raises(SimulationError, match="replaces_layers"):
        prepare_geometry(project)
    project.interfaces[0].replaces_layers = ["oxide"]
    geometry = prepare_geometry(project)
    assert geometry.layers["oxide"].area == pytest.approx(96e-12)
    assert geometry.layers["oxide"].intersection(geometry.interfaces["via"]).area < 1e-25
    project.layers[-1].role = "conductor"
    with pytest.raises(SimulationError, match="unintentionally intersects conductor"):
        prepare_geometry(project)


def test_parallel_dielectric_thermal_path_is_removed_under_via_actual_solve():
    project = stack()
    project.interfaces.append(Interface("dielectric", "lower", "upper", RegionSpec(full_domain=True),
                                        thermal_kind="conductance", thermal_value=1e6))
    project.thermal_boundaries = [ThermalBoundary("cold", "lower", kind="temperature", value=300.),
                                  ThermalBoundary("hot", "upper", kind="temperature", value=310.)]
    geometry = prepare_geometry(project)
    assert geometry.interfaces["dielectric"].area == pytest.approx(96e-12)
    assert geometry.interfaces["via"].area == pytest.approx(4e-12)
    result = solve_thermal(project, generate_mesh(project, geometry))
    exact = 10 * (96e-12 * 1e6 + 4e-12 * 1e7)
    assert result.converged
    assert result.analysis["reservoirs"]["cold"]["heat_out"] == pytest.approx(exact, rel=2e-9)
    assert result.analysis["reservoirs"]["hot"]["heat_out"] == pytest.approx(-exact, rel=2e-9)


def test_physical_contact_zero_height_is_distinct_from_bulk_via():
    project = stack()
    project.layers[1].z = 1e-6
    project.interfaces[0].model_kind = "physical_contact"
    project.interfaces[0].electrical_kind = "specific"
    prepare_geometry(project)
    assert project.interfaces[0].height == 0
    assert not any(d.severity == "error" for d in validate_project(project, raise_errors=False))
    project.interfaces[0].model_kind = "physical_via"
    with pytest.raises(SimulationError, match="positive surface separation"):
        prepare_geometry(project)


def test_duplicate_and_mixed_double_connection_rejected_effective_legacy_retained():
    project = stack()
    duplicate = copy.deepcopy(project.interfaces[0])
    duplicate.id = "duplicate"
    project.interfaces.append(duplicate)
    with pytest.raises(SimulationError, match="double-define"):
        prepare_geometry(project)
    duplicate.model_kind = "effective"
    with pytest.raises(SimulationError, match="double-defines"):
        prepare_geometry(project)
    project.interfaces[0].model_kind = "effective"
    project.layers[1].z = 0.
    geometry = prepare_geometry(project)
    assert any("volumes" in d.message for d in geometry.diagnostics)
