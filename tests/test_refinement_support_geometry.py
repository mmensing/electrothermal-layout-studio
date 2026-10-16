# SPDX-License-Identifier: MPL-2.0
"""Sizing support uses physical metadata; only explicit mesh regions use cells."""
from types import SimpleNamespace

import numpy as np
import pytest
from shapely import polygons, union_all
from shapely.geometry import Polygon, box
from solver_helpers import rectangle

from etlayout.geometry.prepare import geometry_region
from etlayout.meshing.refinement import _physical_selector_support
from etlayout.models import (
    DeviceGroup,
    Interface,
    PhysicalPort,
    PortLanding,
    SimulationResult,
    SupportSelector,
    Terminal,
)


@pytest.mark.fast
@pytest.mark.parametrize("nx", [4, 13])
@pytest.mark.parametrize("selection", ["direct", "nested", "union", "intersection", "region", "port", "contact", "island", "component", "mesh_region", "filters"])
def test_exact_sizing_support_preserves_selector_categories_across_source_meshes(nx, selection):
    project, mesh = rectangle(nx=nx, ny=3, layers=("lower", "upper"))
    domain = box(0, 0, 10e-6, 2e-6)
    a = box(.3e-6, .1e-6, 4.7e-6, 1.9e-6).difference(box(1e-6, .5e-6, 2e-6, 1.5e-6))
    b = Polygon([(6e-6, .2e-6), (9.7e-6, .5e-6), (8e-6, 1.9e-6)])
    clip = box(3.3e-6, 0, 9.9e-6, 2e-6)
    landing = box(.2e-6, .1e-6, 1.3e-6, 1.9e-6)
    via_left, via_right = box(0, 0, 5e-6, 2e-6), box(7.5e-6, 0, 10e-6, 2e-6)
    project.layers[0].net_id, project.layers[1].net_id = "N1", "N2"
    project.power.devices = [
        DeviceGroup("a", SupportSelector(layers=["lower"], geometry=geometry_region(a))),
        DeviceGroup("b", SupportSelector(layers=["lower"], geometry=geometry_region(b))),
        DeviceGroup("nested", SupportSelector(devices=["a"], geometry=geometry_region(clip)))]
    project.terminals = [Terminal("landing", "lower", geometry_region(landing))]
    project.interfaces = [Interface("via", "lower", "upper", geometry_region(via_left.union(via_right)))]
    project.power.ports = [PhysicalPort("feed", [PortLanding("landing")])]
    geometry = SimpleNamespace(domain=domain, layers={"lower": domain, "upper": domain},
        interfaces={"via": via_left.union(via_right)}, terminals={"landing": landing},
        thermal_boundaries={}, heat_sources={}, refinements={})
    result = SimulationResult(mesh, fields={layer: {"potential": np.zeros(len(mesh.points))}
                                          for layer in ("lower", "upper")})
    result.analysis["contacts"] = {"via-left": {"interface_id": "via",
        "elements": np.flatnonzero(mesh.centroids[:, 0] < 5e-6).tolist()}}
    mesh.regions["terminal:landing"] = np.flatnonzero(mesh.centroids[:, 0] < 1.3e-6)
    # This tag deliberately means full adjacent cells, not the zero-area edge.
    mesh.regions["terminal_support:left"] = np.array([0, 1])
    expected = {"lower": a}
    if selection == "direct":
        selector = SupportSelector(layers=["lower"], geometry=geometry_region(a))
    elif selection == "nested":
        selector, expected = SupportSelector(devices=["nested"]), {"lower": a.intersection(clip)}
    elif selection == "union":
        selector, expected = SupportSelector(devices=["a", "b"]), {"lower": a.union(b)}
    elif selection == "intersection":
        selector = SupportSelector(devices=["a", "b"], geometry=geometry_region(clip))
        expected = {"lower": a.union(b).intersection(clip)}
    elif selection == "region":
        # Region tags select the XY footprint on both overlapping sheets.
        selector, expected = SupportSelector(regions=["terminal:landing"]), {"lower": landing, "upper": landing}
    elif selection == "port":
        selector, expected = SupportSelector(ports=["feed"]), {"lower": landing}
    elif selection == "contact":
        selector = SupportSelector(contacts=["via"])
        expected = {"lower": via_left.union(via_right), "upper": via_left.union(via_right)}
    elif selection == "island":
        selector, expected = SupportSelector(contacts=["via-left"]), {"lower": via_left, "upper": via_left}
    elif selection == "component":
        geometry.layers["lower"] = via_left.union(via_right)
        mesh.regions["layer:lower"] = np.flatnonzero((mesh.centroids[:, 0] < 5e-6) | (mesh.centroids[:, 0] > 7.5e-6))
        selector, expected = SupportSelector(layers=["lower"], regions=["component:1"]), {"lower": via_left}
    elif selection == "mesh_region":
        selector = SupportSelector(layers=["lower"], regions=["terminal_support:left"])
        expected = {"lower": union_all(polygons(mesh.points[mesh.triangles[[0, 1]]]))}
    else:
        selector = SupportSelector(materials=["copper"], nets=["N1"])
        expected = {"lower": domain}
    support = _physical_selector_support(project, result, geometry, selector)
    for layer in ("lower", "upper"):
        actual = support.get(layer)
        if layer not in expected:
            assert actual is None or actual.is_empty
        else:
            assert actual.equals(expected[layer])
            assert actual.area == pytest.approx(expected[layer].area, rel=1e-13)
            assert actual.boundary.hausdorff_distance(expected[layer].boundary) < 1e-20
