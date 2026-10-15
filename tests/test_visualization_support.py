# SPDX-License-Identifier: MPL-2.0
"""Map, profile and export support must describe the same physical triangles."""
from __future__ import annotations

from copy import deepcopy

import numpy as np
import pytest

from etlayout.models import PlanarMesh, SimulationResult
from etlayout.visualization.difference import make_field_difference
from etlayout.visualization.palettes import COLORMAPS, get_colormap, rgba
from etlayout.visualization.sampling import FieldSampler
from etlayout.visualization.support import display_limits, field_support, region_elements, region_options


@pytest.fixture
def result():
    points = np.array([[0., 0.], [1., 0.], [0., 1.], [1., 1.],
                       [3., 0.], [4., 0.], [3., 1.], [20., 0.], [21., 0.], [20., 1.]])
    mesh = PlanarMesh(points, np.array([[0, 1, 2], [1, 3, 2], [4, 5, 6], [7, 8, 9]]),
                      {"layer:a": np.array([0, 1, 2]), "layer:b": np.array([3]),
                       "heat:heater": np.array([1, 2, 3]), "interface:other": np.array([3])})
    return SimulationResult(mesh, fields={"a": {
        "potential": np.array([0., 1., 2., np.nan, 3., 4., 5., 999., 999., 999.]),
        "temperature": np.array([290., 310., 320., np.nan, 330., 340., 350., 999., 999., 999.]),
        "joule_density": np.array([10., np.inf, 40., 999.]),
        "electric_field": np.array([[3., 4.], [np.nan, 1.], [8., 6.], [999., 999.]]),
    }}, metrics={"thermal_reference": 300.},
        configuration={"heat_sources": [{"id": "heater", "name": "Main heater"}]})


def test_regions_intersect_layers_name_configuration_and_split_connected_islands(result):
    options = dict(region_options(result, "a"))
    assert options[""] == "All solved regions"
    assert options["heat:heater"] == "Heat source · Main heater"
    assert "interface:other" not in options
    assert not any(key.startswith("layer:") for key in options)
    np.testing.assert_array_equal(region_elements(result, "a", "heat:heater"), [1, 2])
    np.testing.assert_array_equal(region_elements(result, "a", "component:1"), [0, 1])
    np.testing.assert_array_equal(region_elements(result, "a", "component:2"), [2])
    for key in ("missing", "component:0", "component:three", "component:3", "layer:b"):
        with pytest.raises(ValueError):
            region_elements(result, "a", key)


def test_point_contact_does_not_merge_connected_regions():
    mesh = PlanarMesh(np.array([[0, 0], [1, 0], [0, 1], [-1, 0], [0, -1]]),
                      np.array([[0, 1, 2], [0, 3, 4]]), {"layer:a": np.array([0, 1])})
    result = SimulationResult(mesh)
    assert [key for key, _ in region_options(result, "a")] == ["", "component:1", "component:2"]


def test_trimmed_difference_support_preserves_source_component_identity(result):
    result.configuration["visualization"] = {"source_components": {"a": {
        "component:1": [0, 1], "component:2": [2],
    }}}
    result.mesh.regions["layer:a"] = np.array([2])
    options = dict(region_options(result, "a"))
    assert "component:2" in options and "component:1" not in options
    np.testing.assert_array_equal(region_elements(result, "a", "component:2"), [2])
    assert len(region_elements(result, "a", "component:1")) == 0
    support = field_support(result, "a", "potential", region="component:2")
    assert support.limits == (3., 5.)


def test_nodal_support_excludes_partial_nan_and_inactive_extremes(result):
    support = field_support(result, "a", "potential")
    assert support.nodal
    np.testing.assert_array_equal(support.indices, [0, 2])
    np.testing.assert_array_equal(support.nodes, [0, 1, 2, 4, 5, 6])
    np.testing.assert_array_equal(support.values, [1., 4.])
    assert support.limits == (0., 5.)
    assert support.bounds == (0., 0., 4., 1.)
    named = field_support(result, "a", "potential", region="heat:heater")
    assert named.limits == (3., 5.)
    assert named.bounds == (3., 0., 4., 1.)
    assert len(named.data) == len(result.mesh.points)


def test_log_domain_and_region_filters_match_probes_and_profiles(result):
    support = field_support(result, "a", "temperature_rise", logarithmic=True)
    np.testing.assert_array_equal(support.indices, [2])
    assert support.limits == (30., 50.)
    sampler = FieldSampler(result)
    coordinates = np.array([[.1, .1], [.9, .9], [3.2, .2], [2., .2], [20.1, .1]])
    values = sampler.sample("a", "temperature_rise", coordinates, logarithmic=True)
    np.testing.assert_allclose(values, [np.nan, np.nan, 36., np.nan, np.nan], equal_nan=True)
    _, profile, _ = sampler.profile("a", "potential", [0., 0.], [3., 0.], count=4,
                                    region="component:2")
    np.testing.assert_allclose(profile, [np.nan, np.nan, np.nan, 3.], equal_nan=True)


def test_element_vectors_and_empty_support(result):
    support = field_support(result, "a", "electric_field", region="heat:heater")
    assert not support.nodal and support.limits == (10., 10.)
    np.testing.assert_array_equal(support.indices, [2])
    np.testing.assert_allclose(support.values, [10.])
    empty = field_support(result, "a", "joule_density", region="interface:other")
    assert empty.limits is None and empty.bounds is None
    assert not len(empty.indices) and not len(empty.nodes) and not len(empty.values)


def test_difference_display_limits_are_symmetric_only_for_automatic_linear_diverging_maps():
    provenance = {"kind": "difference"}
    assert display_limits((-.5, 2.), provenance=provenance, colormap="coolwarm") == (-2., 2.)
    assert display_limits((-.5, 2.), provenance=provenance, colormap="teal") == (-.5, 2.)
    assert display_limits((-.5, 2.), provenance=provenance, colormap="coolwarm", limits=(-3., 1.)) == (-3., 1.)
    assert display_limits((.5, 2.), provenance=provenance, colormap="coolwarm", logarithmic=True) == (.5, 2.)
    low, high = display_limits((0., 0.), provenance=provenance, colormap="coolwarm")
    assert low < 0 < high and low == -high
    for limits in [(2., 1.), (0., np.inf), (np.nan, 1.)]:
        with pytest.raises(ValueError):
            display_limits((0., 1.), limits=limits)
    with pytest.raises(ValueError, match="greater than"):
        display_limits((1., 1.), limits=(1., 1.))
    assert display_limits((1., 1.)) == (1., 1.)


@pytest.mark.parametrize("value,logarithmic", [(301.62441496841313, False), (301.62441496841313, True),
    (1e-40, False), (1e-40, True), (-1e-40, False), (1e100, False)])
def test_auto_roundoff_padding_is_relative_and_manual_bounds_remain_exact(value, logarithmic):
    raw = (value, np.nextafter(value, np.inf))
    low, high = display_limits(raw, logarithmic=logarithmic)
    assert low < raw[0] < raw[1] < high
    assert np.isfinite([low, high]).all()
    assert (high - low) / max(abs(low), abs(high)) < (.003 if logarithmic else 3e-6)
    assert display_limits(raw, limits=raw, logarithmic=logarithmic) == raw
    assert display_limits((value, value), logarithmic=logarithmic) == (value, value)


@pytest.mark.parametrize("limits", [(0., 1e-40), (1e-40, 2e-40), (1e-20, 1.0000001e-20),
                                   (np.nextafter(0., 1.), 2 * np.nextafter(0., 1.))])
def test_meaningful_tiny_and_subnormal_auto_spans_are_not_padded(limits):
    assert display_limits(limits) == limits


def test_auto_roundoff_padding_remains_finite_at_float_extremes():
    maximum = np.finfo(float).max
    for raw in [(np.nextafter(maximum, 0.), maximum), (-maximum, np.nextafter(-maximum, 0.))]:
        low, high = display_limits(raw)
        assert np.isfinite([low, high]).all() and low <= raw[0] < raw[1] <= high


@pytest.mark.parametrize("current_ok,other_ok", [(True, True), (True, False), (False, True), (False, False)])
def test_difference_provenance_retains_each_source_convergence(result, current_ok, other_ok):
    result.converged = current_ok
    comparison = deepcopy(result)
    comparison.converged = other_ok
    derived = make_field_difference(result, comparison, "a", "potential", "Comparison")
    assert derived.converged is (current_ok and other_ok)
    assert [source["converged"] for source in derived.configuration["visualization"]["sources"]] == [current_ok, other_ok]


@pytest.mark.parametrize("key", list(COLORMAPS))
def test_palette_rgba_matches_export_colormap_and_masks_missing(key):
    values = np.array([-1., 0., .25, .5, 1., 2., np.nan])
    colors = rgba(values, key)
    assert colors.shape == (7, 4)
    assert np.isfinite(colors).all() and ((colors >= 0.) & (colors <= 1.)).all()
    np.testing.assert_allclose(colors[1:5], get_colormap(key)(values[1:5]))
    np.testing.assert_array_equal(colors[0], colors[1])
    np.testing.assert_array_equal(colors[4], colors[5])
    assert colors[-1, 3] == 0.
    with pytest.raises(ValueError):
        get_colormap("unknown")
