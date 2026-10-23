# SPDX-License-Identifier: MPL-2.0
"""Literal solid geometry, closed surfaces and explicit finite-return inputs."""
import json
from copy import deepcopy
from pathlib import Path

import numpy as np
import pytest
from shapely.geometry import Point, box
from shapely.ops import unary_union

from etlayout.examples import rectangle
from etlayout.geometry.prepare import geometry_region, prepare_geometry
from etlayout.models import (
    Cancelled,
    Interface,
    Material,
    PhysicalLayer,
    Project,
    PropertyModel,
    SimulationError,
)
from etlayout.power.field_snapshot import (
    EPSILON_0,
    MU_0,
    build_field_snapshot,
    normalize_loop_entries,
    triangulate_conductors,
)


def plates():
    project = Project(materials={"metal": Material("metal", "Synthetic conductor")})
    project.layers = [PhysicalLayer("low", "Lower plate", "metal", thickness=1e-6, geometry=rectangle(0,0,10,10)),
                      PhysicalLayer("high", "Upper plate", "metal", thickness=1e-6, z=3e-6, geometry=rectangle(0,0,10,10))]
    project.parasitics = {"schema_version": 1, "background_relative_permittivity": 1.}
    return project


def closed_path_project():
    project = Project(materials={"metal": Material("metal", "Synthetic nonmagnetic conductor",
        resistivity=PropertyModel(value=2e-8), permeability=PropertyModel(value=MU_0))})
    xy = [(1,1), (11,1), (11,11), (1,11), (1,1)]
    segments, shapes = [], []
    for (x0,y0),(x1,y1) in zip(xy, xy[1:]):
        horizontal = y0 == y1
        direction = [0.,1.,0.] if horizontal else [1.,0.,0.]
        shapes.append(box(min(x0,x1)-(0 if horizontal else 1), min(y0,y1)-(1 if horizontal else 0),
                          max(x0,x1)+(0 if horizontal else 1), max(y0,y1)+(1 if horizontal else 0)))
        segments.append({"start_m": [x0*1e-6,y0*1e-6,1e-6], "end_m": [x1*1e-6,y1*1e-6,1e-6],
                         "width_m": 2e-6, "height_m": 2e-6, "width_direction": direction,
                         "support_ids": ["layer:wire"]})
    shape = unary_union(shapes)
    # The finite bars themselves define the test source, not a box around them.
    from shapely import affinity
    source = affinity.scale(shape, xfact=1e-6, yfact=1e-6, origin=(0,0))
    project.layers = [PhysicalLayer("wire", "Finite closed conductor", "metal", thickness=2e-6,
                                    geometry=geometry_region(source))]
    project.parasitics = {"schema_version": 1, "background_relative_permeability": 1.,
                          "material_sources": {"metal": "Synthetic explicit nonmagnetic μr=1 test assumption"},
                          "loops": [{"id": "power_loop", "segments": segments}]}
    return project


def test_plate_snapshot_and_closed_surfaces_are_deterministic_and_source_is_unchanged():
    project = plates()
    before = deepcopy(project.to_dict())
    first = build_field_snapshot(project, kind="capacitance")
    second = build_field_snapshot(project, kind="capacitance")
    assert first == second and len(first.components) == 2
    assert project.to_dict() == before
    assert first.source_context["reference"] == {"id": "environment", "kind": "infinity"}
    copied = first.source_context
    copied["reference"]["kind"] = "altered"
    assert first.source_context["reference"]["kind"] == "infinity"
    coarse = triangulate_conductors(first, 20e-6)
    fine = triangulate_conductors(first, 5e-6)
    assert len(coarse.surfaces) == 2 and coarse.mesh_sha256 != fine.mesh_sha256
    assert coarse.geometry_sha256 == fine.geometry_sha256 == first.geometry_sha256
    for surface in coarse.surfaces:
        assert len(surface.triangles) == 12
        xyz = np.asarray(surface.vertices_m)[np.asarray(surface.triangles)]
        # Independent divergence theorem; origin translation improves precision.
        xyz -= xyz.reshape(-1,3).min(axis=0)
        volume = sum(np.dot(a,np.cross(b,c))/6 for a,b,c in xyz)
        assert volume == pytest.approx(100e-18, rel=1e-12)
    json.dumps(first.to_dict(), allow_nan=False)


@pytest.mark.parametrize("when", ["before", "during"])
def test_changed_source_layout_is_rejected_instead_of_reusing_its_pinned_identity(tmp_path, monkeypatch, when):
    from etlayout.parasitic_examples import make_field_reference
    from etlayout.power import field_snapshot
    project = make_field_reference("capacitance",tmp_path / "source")
    path = Path(project.layout.path)
    before = deepcopy(project.to_dict())
    if when == "before":
        path.write_bytes(path.read_bytes()+b"\x00\x00")
    else:
        prepare = field_snapshot.prepare_geometry
        def change_after_read(*args,**kwargs):
            geometry = prepare(*args,**kwargs)
            path.write_bytes(path.read_bytes()+b"\x00\x00")
            return geometry
        monkeypatch.setattr(field_snapshot,"prepare_geometry",change_after_read)
    with pytest.raises(SimulationError,match="content changed"):
        build_field_snapshot(project,kind="capacitance")
    assert project.to_dict() == before


def test_unhashed_source_is_pinned_in_snapshot_without_mutating_the_caller(tmp_path):
    from etlayout.io.project import configuration_hash, file_hash
    from etlayout.parasitic_examples import make_field_reference
    project = make_field_reference("capacitance",tmp_path / "source")
    expected = file_hash(project.layout.path)
    project.layout.sha256 = ""
    before = deepcopy(project.to_dict())
    snapshot = build_field_snapshot(project,kind="capacitance")
    pinned = deepcopy(project)
    pinned.layout.sha256 = expected
    assert snapshot.source_context["layout_sha256"] == expected
    assert snapshot.source_context["configuration_hash"] == configuration_hash(pinned)
    assert project.to_dict() == before


def test_hole_is_preserved_in_a_closed_outward_surface():
    project = plates()
    project.layers = [project.layers[0]]
    project.layers[0].geometry = geometry_region(box(0,0,10e-6,10e-6).difference(box(2e-6,2e-6,8e-6,8e-6)))
    snapshot = build_field_snapshot(project, kind="capacitance")
    assert len(snapshot.solids[0].holes_m) == 1
    mesh = triangulate_conductors(snapshot, 10e-6)
    surface = mesh.surfaces[0]
    xyz = np.asarray(surface.vertices_m)[np.asarray(surface.triangles)]
    volume = sum(np.dot(a,np.cross(b,c))/6 for a,b,c in xyz)
    assert volume == pytest.approx(64e-18, rel=1e-12)
    # No cap triangle may fill the hole at either horizontal surface.
    for triangle in xyz:
        if np.ptp(triangle[:,2]) == 0:
            assert not box(2e-6,2e-6,8e-6,8e-6).contains(Point(*triangle.mean(axis=0)[:2]))


def test_tall_wall_refinement_resolves_the_long_direction_without_preserving_slivers():
    project = plates()
    project.layers = [project.layers[0]]
    project.layers[0].thickness = 1e-3
    snapshot = build_field_snapshot(project, kind="capacitance")
    mesh = triangulate_conductors(snapshot, 25e-6, max_panels=20000)
    triangles = np.asarray(mesh.surfaces[0].vertices_m)[np.asarray(mesh.surfaces[0].triangles)]
    edges = np.linalg.norm(triangles-np.roll(triangles,1,axis=1),axis=2)
    # Uniform 4-way refinement alone would preserve the original aspect100.
    assert (edges.max(axis=1)/edges.min(axis=1)).max() < 3.
    assert edges.max() <= 25e-6*(1+1e-12)
    volume = sum(np.dot(a,np.cross(b,c))/6 for a,b,c in triangles)
    assert volume == pytest.approx(1e-13,rel=1e-12)


def test_equivalent_surface_size_representations_do_not_trigger_an_extra_mesh_level_or_budget_failure(tmp_path):
    from etlayout.gui.inputs import parse_number
    from etlayout.parasitic_examples import make_field_reference
    project = make_field_reference("capacitance",tmp_path / "source")
    snapshot = build_field_snapshot(project,kind="capacitance")
    sizes = [50e-6,parse_number("50","µm"),parse_number("50 µm","µm"),parse_number("50000 nm","µm")]
    meshes = [triangulate_conductors(snapshot,size,max_panels=2000) for size in sizes]
    assert len({mesh.mesh_sha256 for mesh in meshes}) == 1
    assert all(sum(len(surface.triangles) for surface in mesh.surfaces) == 1920 for mesh in meshes)


def test_actual_via_is_exposed_once_and_joined_without_internal_surfaces():
    project = plates()
    project.materials["gap"] = Material("gap", "Synthetic homogeneous vacuum region", category="dielectric",
                                        permittivity=PropertyModel(value=EPSILON_0))
    project.layers.insert(1, PhysicalLayer("gap", "Gap", "gap", z=1e-6, thickness=2e-6, role="insulator", geometry=rectangle(0,0,10,10)))
    project.interfaces = [Interface("via", "low", "high", rectangle(3,3,7,7), electrical_kind="via", material_id="metal",
                                    model_kind="physical_via", height_mode="stack", replaces_layers=["gap"])]
    project.parasitics["material_sources"] = {"gap": "Synthetic vacuum εr=1 geometry test"}
    prepared = prepare_geometry(deepcopy(project))
    footprint, low, high = prepared.physical_connections["via"]
    assert [low,high] == pytest.approx([1e-6,3e-6]) and footprint.area == pytest.approx(16e-12)
    assert prepared.layers["gap"].area == pytest.approx(84e-12)
    snapshot = build_field_snapshot(project, kind="capacitance")
    assert len(snapshot.components) == 1 and any(s.origin_id == "interface:via" for s in snapshot.solids)
    surface = triangulate_conductors(snapshot, 20e-6).surfaces[0]
    xyz = np.asarray(surface.vertices_m)[np.asarray(surface.triangles)]
    volume = sum(np.dot(a,np.cross(b,c))/6 for a,b,c in xyz)
    assert volume == pytest.approx(232e-18, rel=1e-12)


@pytest.mark.parametrize("damage,expected", [("background", "background"), ("overlap", "overlap"),
                                            ("effective", "Effective"), ("nonlinear", "sheet/HEMT"),
                                            ("anisotropic", "anisotropic"), ("unknown", "Unknown")])
def test_unsupported_capacitance_geometry_is_rejected_without_inventing_a_model(damage, expected):
    project = plates()
    if damage == "background":
        project.parasitics.pop("background_relative_permittivity")
    elif damage == "overlap":
        project.layers[1].z = .5e-6
    elif damage == "effective":
        project.interfaces = [Interface("unphysical", "low", "high", rectangle(0,0,10,10), electrical_kind="ideal")]
    elif damage == "nonlinear":
        project.layers[0].electrical.kind = "hemt"
    elif damage == "anisotropic":
        project.materials["metal"].electrical_anisotropy = [1.,2.]
    else:
        project.parasitics["silently_ignored"] = 1
    with pytest.raises(SimulationError, match=expected):
        build_field_snapshot(project, kind="capacitance")


def test_dielectric_requires_own_electromagnetic_source_and_homogeneous_valid_coefficient():
    project = plates()
    project.materials["gap"] = Material("gap", "Explicit gap", category="dielectric")
    project.layers.append(PhysicalLayer("gap", "Gap", "gap", z=1e-6, thickness=2e-6, role="insulator", geometry=rectangle(0,0,10,10)))
    with pytest.raises(SimulationError, match="specify permittivity"):
        build_field_snapshot(project, kind="capacitance")
    project.materials["gap"].permittivity = PropertyModel(value=2*EPSILON_0)
    with pytest.raises(SimulationError, match="separate material_sources"):
        build_field_snapshot(project, kind="capacitance")
    project.parasitics["material_sources"] = {"gap": "Synthetic εr=2 model"}
    with pytest.raises(SimulationError, match="heterogeneous"):
        build_field_snapshot(project, kind="capacitance")
    project.parasitics["background_relative_permittivity"] = 2.
    snapshot = build_field_snapshot(project, kind="capacitance")
    assert next(m for m in snapshot.materials if m.id == "gap").field_source == "Synthetic εr=2 model"
    with pytest.raises(SimulationError, match="validity"):
        build_field_snapshot(project, kind="capacitance", temperature_K=1000.)


def test_explicit_finite_closed_return_is_verified_against_actual_source_volume():
    project = closed_path_project()
    before = deepcopy(project.to_dict())
    snapshot = build_field_snapshot(project, kind="loop_rl")
    assert len(snapshot.loops) == 1 and len(snapshot.loops[0].segments) == 4
    for segment in snapshot.loops[0].segments:
        assert segment.solid_ids and segment.conductivity_S_per_m == pytest.approx(5e7)
    assert snapshot.loops[0].segments[-1].end_m == snapshot.loops[0].segments[0].start_m
    assert project.to_dict() == before


def test_equivalent_units_join_only_machine_roundoff_and_do_not_repair_a_physical_gap():
    project = closed_path_project()
    records = deepcopy(project.parasitics["loops"])
    records[0]["segments"][0]["start_m"][0] = 1000*1e-9
    normalized = normalize_loop_entries(records)
    assert normalized[0]["segments"][-1]["end_m"] == normalized[0]["segments"][0]["start_m"]
    assert records != normalized  # Caller input is never rewritten.
    project.parasitics["loops"] = records
    snapshot = build_field_snapshot(project, kind="loop_rl")
    assert snapshot.loops[0].segments[-1].end_m == snapshot.loops[0].segments[0].start_m
    records[0]["segments"][0]["start_m"][0] += 1e-15
    with pytest.raises(SimulationError,match="close"):
        normalize_loop_entries(records)


def test_rl_background_permittivity_is_preserved_and_omitted_conductors_are_rejected():
    project = closed_path_project()
    default = build_field_snapshot(project, kind="loop_rl")
    assert any("defaults explicitly to vacuum" in text for text in default.source_context["assumptions"])
    project.parasitics["background_relative_permittivity"] = 4.
    explicit = build_field_snapshot(project, kind="loop_rl")
    assert explicit.relative_permittivity == 4.
    assert explicit.geometry_sha256 == default.geometry_sha256 and explicit.field_model_hash != default.field_model_hash
    assert not any("defaults explicitly to vacuum" in text for text in explicit.source_context["assumptions"])
    project.layers.append(PhysicalLayer("omitted", "Unassigned finite metal", "metal", thickness=2e-6,
                                        geometry=rectangle(20,20,22,22)))
    with pytest.raises(SimulationError, match="Every conductive component"):
        build_field_snapshot(project, kind="loop_rl")


def test_tabulated_property_never_silently_uses_a_clamped_endpoint():
    project = closed_path_project()
    project.materials["metal"].resistivity = PropertyModel(kind="table", temperatures=[400.,500.],
        values=[2e-8,3e-8], minimum_temperature=200., maximum_temperature=600.)
    with pytest.raises(SimulationError, match="tabulated validity"):
        build_field_snapshot(project, kind="loop_rl", temperature_K=300.)
    assert build_field_snapshot(project, kind="loop_rl", temperature_K=450.).loops[0].segments[0].conductivity_S_per_m == pytest.approx(4e7)


@pytest.mark.parametrize("damage,expected", [("open", "close"), ("oversize", "cross-section"),
                                            ("reference", "Unknown"), ("missing_mu", "specify permeability"),
                                            ("magnetic", "nonmagnetic"), ("shared", "separate connected"),
                                            ("plane", "whole connected source"), ("invalid_support", "support ID")])
def test_invalid_return_or_material_is_rejected_before_external_rl_execution(damage, expected):
    project = closed_path_project()
    loop = project.parasitics["loops"][0]
    if damage == "open":
        loop["segments"][-1]["end_m"][1] += 1e-6
    elif damage == "oversize":
        loop["segments"][0]["width_m"] *= 4
    elif damage == "reference":
        project.parasitics["ground"] = "some_node"
    elif damage == "missing_mu":
        project.materials["metal"].permeability = None
    elif damage == "magnetic":
        project.materials["metal"].permeability.value *= 2
    elif damage == "plane":
        project.layers[0].geometry = rectangle(0,0,12,12)
    elif damage == "invalid_support":
        loop["segments"][0]["support_ids"] = [{"layer": "wire"}]
    else:
        second = deepcopy(loop)
        second["id"] = "second"
        project.parasitics["loops"].append(second)
    with pytest.raises(SimulationError, match=expected):
        build_field_snapshot(project, kind="loop_rl")


def test_surface_resources_and_cancellation_are_hard_limits():
    snapshot = build_field_snapshot(plates(), kind="capacitance")
    with pytest.raises(SimulationError, match="budget"):
        triangulate_conductors(snapshot, 1e-6, max_panels=10)
    with pytest.raises(Cancelled):
        triangulate_conductors(snapshot, 20e-6, cancel=lambda: True)
    with pytest.raises(Cancelled):
        build_field_snapshot(plates(), kind="capacitance", cancel=lambda: True)
