# SPDX-License-Identifier: MPL-2.0
import numpy as np
import pytest
from shapely.geometry import Polygon

from etlayout.geometry.prepare import prepare_geometry
from etlayout.meshing import generate_mesh
from etlayout.models import (
    HeatSource,
    Interface,
    MeshSettings,
    PhysicalLayer,
    Project,
    RefinementRegion,
    RegionSpec,
    SimulationError,
    Terminal,
    ThermalBoundary,
)


def rect(x0, y0, x1, y1):
    return {"exterior": [[v * 1e-6 for v in p] for p in [[x0,y0],[x1,y0],[x1,y1],[x0,y1]]]}


def assert_conforming(mesh, geometry):
    for prefix, group in [("layer", geometry.layers), ("interface", geometry.interfaces),
                          ("terminal", geometry.terminals), ("thermal", geometry.thermal_boundaries),
                          ("heat", geometry.heat_sources), ("refinement", geometry.refinements)]:
        for key, shape in group.items():
            if shape.area == 0:
                continue
            elements = mesh.regions[prefix + ":" + key]
            assert mesh.areas[elements].sum() == pytest.approx(shape.area, rel=2e-8, abs=1e-27)
            for triangle in mesh.triangles[elements]:
                polygon = Polygon(mesh.points[triangle])
                assert polygon.difference(shape).area < max(shape.area * 1e-9, 1e-28)


def test_conforming_holes_interfaces_and_boundary_terminals():
    shape = rect(0,0,30,20)
    shape["holes"] = [rect(12,7,18,13)["exterior"]]
    project = Project(layers=[PhysicalLayer("a", "A", "cu", geometry=RegionSpec(polygons=[shape])),
                              PhysicalLayer("b", "B", "cu", z=2e-6, geometry=RegionSpec(polygons=[rect(0,0,10,20)]))],
                      interfaces=[Interface("via", "a", "b", RegionSpec(polygons=[rect(3,6,7,14)]), "specific")],
                      terminals=[Terminal("left", "a", RegionSpec(line=[[0., 3e-6],[0., 17e-6]]), "ground"),
                                 Terminal("right", "a", RegionSpec(polygons=[rect(27,4,30,16)]), "voltage", 1.)],
                      thermal_boundaries=[ThermalBoundary("sink", "b", RegionSpec(line=[[0.,0.],[10e-6,0.]]), "temperature",300.)],
                      heat_sources=[HeatSource("heater", "a", RegionSpec(polygons=[rect(20,3,25,7)]), "total",1.)],
                      mesh=MeshSettings(max_size=4e-6, min_size=.2e-6, contact_size=1e-6,
                                        refinements=[RefinementRegion("fine", RegionSpec(polygons=[rect(20,10,25,15)]), .6e-6)]))
    geometry = prepare_geometry(project)
    mesh = generate_mesh(project, geometry)
    assert_conforming(mesh, geometry)
    assert mesh.statistics["minimum_angle_deg"] > 5
    nodes = mesh.points[mesh.terminal_nodes["left"]]
    assert np.allclose(nodes[:,0], 0, atol=1e-15)
    assert nodes[:,1].min() == pytest.approx(3e-6)
    assert nodes[:,1].max() == pytest.approx(17e-6)
    assert len(mesh.boundary_nodes["sink"]) > 2


def test_mesh_point_contact_does_not_add_triangle_bridge():
    shape = RegionSpec(polygons=[rect(0,0,10,5), rect(10,5,15,10)])
    project = Project(layers=[PhysicalLayer("a", "A", "cu", geometry=shape)],
                      mesh=MeshSettings(max_size=3e-6, min_size=.2e-6, contact_size=1e-6))
    geometry = prepare_geometry(project)
    mesh = generate_mesh(project, geometry)
    assert_conforming(mesh, geometry)
    lower = mesh.centroids[:,0] < 10e-6
    nodes_a, nodes_b = np.unique(mesh.triangles[lower]), np.unique(mesh.triangles[~lower])
    assert len(np.intersect1d(nodes_a, nodes_b)) == 1


def test_budget_failure_is_actionable():
    project = Project(layers=[PhysicalLayer("a", "A", "cu", geometry=RegionSpec(polygons=[rect(0,0,100,100)]))],
                      mesh=MeshSettings(max_size=.5e-6,min_size=.1e-6,max_elements=10))
    with pytest.raises(SimulationError, match="budget"):
        generate_mesh(project, prepare_geometry(project))


def test_entered_and_database_roundoff_does_not_create_slivers():
    imported = RegionSpec(polygons=[rect(0,0,100,30)])
    entered = RegionSpec(polygons=[{"exterior": [[0,0],[10e-6,0],[10e-6,30e-6],[0,30e-6]]}])
    project = Project(layers=[PhysicalLayer("a", "A", "cu", geometry=imported)],
                      terminals=[Terminal("pad", "a", entered, "ground"),
                                 Terminal("drain", "a", RegionSpec(line=[[100e-6,0],[100e-6,30e-6]]), "voltage", 1)],
                      mesh=MeshSettings(max_size=7e-6, min_size=.5e-6, contact_size=2e-6))
    geometry = prepare_geometry(project)
    mesh = generate_mesh(project, geometry)
    assert_conforming(mesh, geometry)
    assert mesh.statistics["minimum_angle_deg"] > 20
    assert mesh.boundary_edges["terminal:drain"].shape[1] == 2


def test_embedded_floating_probe_and_point_touch_support():
    shape = RegionSpec(polygons=[rect(0,0,20,10), rect(20,10,30,20)])
    project = Project(layers=[PhysicalLayer("a", "A", "cu", geometry=shape)],
                      terminals=[Terminal("probe", "a", RegionSpec(line=[[8e-6,3e-6],[14e-6,7e-6]]), "floating"),
                                 Terminal("edge", "a", RegionSpec(line=[[20e-6,0],[20e-6,10e-6]]), "ground")],
                      mesh=MeshSettings(max_size=3e-6,min_size=.2e-6,contact_size=1e-6))
    geometry = prepare_geometry(project)
    mesh = generate_mesh(project, geometry)
    assert_conforming(mesh, geometry)
    assert len(mesh.boundary_edges["terminal:probe"]) > 2
    support = mesh.regions["terminal_support:edge"]
    assert np.all(mesh.centroids[support, 0] < 20e-6)
    edge_pairs = mesh.boundary_edges["terminal:edge"]
    assert np.sum(np.linalg.norm(np.diff(mesh.points[edge_pairs], axis=1)[:,0,:], axis=1)) == pytest.approx(10e-6)
