# SPDX-License-Identifier: MPL-2.0
"""Fabrication via rules must depend on geometry, not interface grouping."""
from copy import deepcopy

import pytest
from shapely.affinity import rotate
from shapely.geometry import GeometryCollection, LineString, Polygon, box

from etlayout.geometry.prepare import prepare_geometry
from etlayout.materials.database import reference_materials
from etlayout.models import Interface, PhysicalLayer, Project, RegionSpec
from etlayout.routing.geometry import as_spec
from etlayout.routing.rules import validate_rules


def test_floating_point_area_reduction_does_not_reject_valid_polygonal_via_array():
    from shapely.ops import unary_union
    project = via_project()
    shapes = [box((9.5 + 4 * i) * 1e-6, 2.5e-6, (10.5 + 4 * i) * 1e-6, 3.5e-6) for i in range(6)]
    project.interfaces = [Interface('array', 'a', 'b', as_spec(unary_union(shapes)), model_kind='physical_via')]
    project.power.rules.via_size = 1e-6
    # Enlarge the immutable landings to include this complete array.
    for layer in project.layers:
        layer.geometry = as_spec(box(-1e-6, -1e-6, 40e-6, 10e-6))
    assert not validate_rules(project)


def via_project():
    landing = as_spec(box(-10e-6, -10e-6, 10e-6, 10e-6))
    project = Project(materials=reference_materials(), layers=[
        PhysicalLayer('a', 'A', 'copper', geometry=landing),
        PhysicalLayer('b', 'B', 'copper', z=2e-6, geometry=deepcopy(landing)),
        PhysicalLayer('c', 'C', 'copper', z=4e-6, geometry=deepcopy(landing))])
    project.power.rules.via_size = .5e-6
    return project


@pytest.mark.parametrize('grouped', [True, False])
@pytest.mark.parametrize('rule', ['pitch', 'spacing'])
def test_vias_on_transition_enforce_rules_across_interface_groupings(grouped, rule):
    project = via_project()
    a, b = box(0, 0, 1e-6, 1e-6), box(1.2e-6, 0, 2.2e-6, 1e-6)
    project.interfaces = ([Interface('array', 'a', 'b', as_spec(a.union(b)), model_kind='physical_via')]
                          if grouped else [Interface('first', 'a', 'b', as_spec(a), model_kind='physical_via'),
                                           Interface('second', 'b', 'a', as_spec(b), model_kind='physical_via')])
    project.power.rules.via_pitch = 2e-6 if rule == 'pitch' else 0
    project.power.rules.via_spacing = .3e-6 if rule == 'spacing' else 0
    issues = [item for item in validate_rules(project) if item['rule_id'] == 'via_' + rule]
    assert len(issues) == 1
    assert issues[0]['bounds'] == pytest.approx([0, 0, 2.2e-6, 1e-6])
    # An identical XY via on a different fabrication transition is unrelated.
    project.interfaces[-1].layer_b = 'c'
    if not grouped:
        assert not any(item['rule_id'] == 'via_' + rule for item in validate_rules(project))


@pytest.mark.parametrize('shape,valid', [
    (rotate(box(0, 0, .3e-6, 2e-6), 45, origin=(0, 0)), False),
    (rotate(box(0, 0, 1e-6, 2e-6), 45, origin=(0, 0)), True),
    (Polygon([(0, 0), (2e-6, 0), (2e-6, 2e-6), (1.85e-6, 2e-6),
              (1.85e-6, .3e-6), (.15e-6, .3e-6), (.15e-6, 2e-6), (0, 2e-6)]), False)])
def test_via_minimum_width_uses_actual_rotated_or_notched_geometry(shape, valid):
    project = via_project()
    project.interfaces = [Interface('via', 'a', 'b', as_spec(shape), model_kind='physical_via')]
    violations = validate_rules(project)
    assert any(item['rule_id'] == 'via_size' for item in violations) is not valid


@pytest.mark.parametrize('shape', [GeometryCollection(), LineString([(0, 0), (1e-6, 0)])])
def test_unsupported_via_shapes_fail_explicitly(shape):
    project = via_project()
    project.interfaces = [Interface('via', 'a', 'b', RegionSpec(full_domain=True), model_kind='physical_via')]
    geometry = prepare_geometry(project)
    geometry.interfaces['via'] = shape
    assert any(item['rule_id'] == 'unsupported_via_shape' for item in validate_rules(project, geometry))
