# SPDX-License-Identifier: MPL-2.0
"""Field support, quantitative bounds and temporary layer isolation in the Qt view."""
from __future__ import annotations

import os
import warnings
from copy import deepcopy

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import numpy as np
import pytest
from shapely.geometry import LineString, box

from etlayout.gui.viewer import VECTOR_FIELDS, LayoutView
from etlayout.models import PhysicalLayer, PlanarMesh, SimulationResult
from etlayout.visualization.palettes import rgba
from etlayout.visualization.sampling import FieldSampler
from etlayout.visualization.support import field_support


@pytest.fixture
def view(qtbot):
    widget = LayoutView()
    widget.resize(700, 500)
    qtbot.addWidget(widget)
    return widget


def _result():
    points = np.array([[x, y] for offset in (0., 100., 1000., 10000.)
                       for x, y in ((offset, 0.), (offset + 1., 0.), (offset, 1.))]) * 1e-6
    mesh = PlanarMesh(points, np.arange(12).reshape(4, 3),
                      {"layer:a": np.array([0, 1, 2]), "layer:b": np.array([3])})
    return SimulationResult(mesh, fields={"a": {
        "potential": np.array([10., 20., 30., 1000., 2000., np.nan,
                                np.nan, np.nan, np.nan, 9999., 9999., 9999.]),
        "joule_density": np.array([4., np.nan, np.inf, 9999.]),
        "current_density": np.array([[3., 4.], [np.nan, np.nan], [np.nan, np.nan], [9999., 9999.]]),
    }})


@pytest.mark.parametrize("field,expected_range", [("potential", (10., 30.)), ("joule_density", (4., 4.)),
                                                  ("current_density", (5., 5.))])
def test_only_valid_active_triangles_define_field_bounds_and_scale(view, field, expected_range):
    result = _result()
    details = []
    view.legend_details.connect(details.append)
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        view.show_field(result, "a", field, vectors=field == "current_density")
    assert not [warning for warning in caught if issubclass(warning.category, RuntimeWarning)]
    bounds = view.overlay_item.boundingRect()
    assert (bounds.left(), bounds.top(), bounds.width(), bounds.height()) == pytest.approx((0., -1., 1., 1.))
    assert (details[-1]["low"], details[-1]["high"]) == expected_range
    if field == "current_density":
        assert view.vector_item is not None
        assert view.vector_item.boundingRect() == bounds
    if field == "potential":
        sampler = FieldSampler(result)
        assert np.isfinite(sampler.sample("a", field, np.array([[.1e-6, .1e-6]]))[0])
        assert np.isnan(sampler.sample("a", field, np.array([[100.1e-6, .1e-6]]))[0])


def test_no_defined_triangles_clear_old_overlay_and_fit_returns_false(view):
    result = _result()
    view.show_field(result, "a", "potential")
    assert view.overlay_item is not None
    result.fields["a"]["potential"][2] = np.nan
    details = []
    view.legend_details.connect(details.append)
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        view.show_field(result, "a", "potential")
    assert not [warning for warning in caught if issubclass(warning.category, RuntimeWarning)]
    assert view.overlay_item is None and view.vector_item is None
    assert details[-1] is None
    assert not view.fit_field()


def test_arrows_represent_vectors_not_conductance_tensor_components(view):
    result = _result()
    components = result.fields["a"]["current_density"].copy()
    result.fields["a"]["sheet_conductance"] = components
    view.show_field(result, "a", "sheet_conductance", vectors=True)
    assert view.overlay_item is not None
    assert view.vector_item is None
    assert "tensor norm" in view.legend
    assert VECTOR_FIELDS == {"electric_field", "sheet_current", "current_density", "thermal_gradient"}
    for field in VECTOR_FIELDS:
        result.fields["a"][field] = components
        view.show_field(result, "a", field, vectors=True)
        assert view.vector_item is not None
    view.show_field(result, "a", "sheet_conductance", vectors=True)
    assert view.vector_item is None


def test_fit_field_ignores_distant_geometry_and_terminal_labels(view, qtbot):
    view.set_geometries({"a": box(0., 0., 1e-6, 1e-6), "b": box(.01, 0., .0101, .0001)},
                        terminals={"distant label": LineString([(.01, 0.), (.01, .0001)])})
    view.show()
    qtbot.waitExposed(view)
    view.show_field(_result(), "a", "potential")
    view.fit_all()
    overview_scale = view.transform().m11()
    assert view.fit_field()
    assert view.transform().m11() > 1000 * overview_scale
    visible_bounds = view.mapToScene(view.viewport().rect()).boundingRect()
    assert visible_bounds.contains(view.overlay_item.boundingRect())
    assert visible_bounds.width() < 5.
    assert visible_bounds.height() < 5.
    view.show_field(None, "", "")
    assert not view.fit_field()


def _set_layers(view):
    layers = [PhysicalLayer("a", "A", "copper"), PhysicalLayer("b", "B", "copper"),
              PhysicalLayer("c", "Hidden C", "copper", visible=False)]
    geometry = {layer.id: box(index * 2e-6, 0., (index * 2 + 1) * 1e-6, 1e-6)
                for index, layer in enumerate(layers)}
    terminals = {"terminal_" + key: LineString([shape.bounds[:2], (shape.bounds[0], shape.bounds[3])])
                 for key, shape in geometry.items()}
    view.set_geometries(geometry, layers, terminals)
    return layers, geometry, terminals


def _assert_visible(view, geometry, terminals):
    assert {key: all(item.isVisible() for item in items) for key, items in view.layer_items.items()} == geometry
    for items in view.terminal_items.values():
        assert len(items) == 2  # Marker and label follow the same visibility rule.
        assert len({item.isVisible() for item in items}) == 1
    assert {key: all(item.isVisible() for item in items) for key, items in view.terminal_items.items()} == terminals


def test_focus_and_visibility_changes_restore_base_geometry_and_terminals(view):
    layers, _, _ = _set_layers(view)
    mapping = {"terminal_" + layer.id: layer.id for layer in layers}
    view.set_layer_focus("a", mapping)
    _assert_visible(view, {"a": True, "b": False, "c": False},
                    {"terminal_a": True, "terminal_b": False, "terminal_c": False})
    view.set_layer_visible("a", False)
    view.set_layer_visible("b", True)
    _assert_visible(view, {"a": False, "b": False, "c": False},
                    {"terminal_a": False, "terminal_b": False, "terminal_c": False})
    view.set_layer_focus("")
    _assert_visible(view, {"a": False, "b": True, "c": False},
                    {"terminal_a": False, "terminal_b": True, "terminal_c": False})
    assert [layer.visible for layer in layers] == [True, True, False]
    view.set_layer_visible("a", True)
    _assert_visible(view, {"a": True, "b": True, "c": False},
                    {"terminal_a": True, "terminal_b": True, "terminal_c": False})


def test_focus_and_terminal_mapping_persist_through_scene_refresh(view):
    layers, geometry, terminals = _set_layers(view)
    view.set_layer_focus("b", {"terminal_" + layer.id: layer.id for layer in layers})
    view.set_geometries(geometry, layers, terminals, preserve_view=True)
    _assert_visible(view, {"a": False, "b": True, "c": False},
                    {"terminal_a": False, "terminal_b": True, "terminal_c": False})
    view.set_layer_focus("")
    _assert_visible(view, {"a": True, "b": True, "c": False},
                    {"terminal_a": True, "terminal_b": True, "terminal_c": False})


def test_focus_without_terminal_ownership_hides_ambiguous_markers(view):
    _set_layers(view)
    view.set_layer_focus("a")
    assert all(not item.isVisible() for items in view.terminal_items.values() for item in items)
    view.set_layer_focus("", {"terminal_a": "a", "terminal_b": "b", "terminal_c": "c"})
    _assert_visible(view, {"a": True, "b": True, "c": False},
                    {"terminal_a": True, "terminal_b": True, "terminal_c": False})


def _picture_pixel(view, x, y):
    from PySide6.QtGui import QImage, QPainter
    image = QImage(120, 120, QImage.Format.Format_ARGB32)
    image.fill(0xffffffff)
    painter = QPainter(image)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    painter.translate(10., 110.)
    painter.scale(100., 100.)
    painter.drawPicture(0, 0, view.overlay_item.picture)
    painter.end()
    color = image.pixelColor(round(10 + 100 * x), round(110 - 100 * y))
    return np.array([color.redF(), color.greenF(), color.blueF()])


@pytest.mark.parametrize("logarithmic", [False, True])
def test_smooth_nodal_picture_interpolates_scalar_before_mapping_color(view, logarithmic):
    result = _result()
    result.fields["a"]["potential"][:3] = [1., 10., 100.] if logarithmic else [10., 20., 30.]
    view.show_field(result, "a", "potential", logarithmic=logarithmic, colormap="viridis", smooth=True)
    value = .6 * result.fields["a"]["potential"][0] + .2 * result.fields["a"]["potential"][1:3].sum()
    fraction = np.log10(value) / 2. if logarithmic else (value - 10.) / 20.
    np.testing.assert_allclose(_picture_pixel(view, .2, .2), rgba(fraction, "viridis")[:3], atol=.025)
    assert view.field_smooth
    view.show_field(result, "a", "joule_density", smooth=True)
    assert not view.field_smooth


def test_region_scopes_picture_mesh_metadata_and_preserves_manual_limits(view):
    result = _result()
    result.mesh.regions["heat:active"] = np.array([0])
    details = []
    view.legend_details.connect(details.append)
    view.show_field(result, "a", "potential", region="heat:active", colormap="magma", limits=(0., 100.))
    assert view.field_limits == (0., 100.) and view.field_manual_limits
    assert view.field_region == "heat:active" and view.field_colormap == "magma"
    assert details[-1]["elements"] == 1 and details[-1]["nodes"] == 3
    view.show_mesh(result.mesh, layer="a", region="heat:active")
    assert view.mesh_item.boundingRect() == view.overlay_item.boundingRect()
    view.show_mesh(result.mesh, layer="b")
    assert view.mesh_item.boundingRect().left() == pytest.approx(10000.)
    view.show_field(result, "a", "current_density", vectors=True)
    assert "normalized direction arrows" in view.legend


def test_log_masks_triangle_with_any_nonpositive_vertex(view):
    result = _result()
    result.fields["a"]["potential"][0] = -1.
    view.show_field(result, "a", "potential", logarithmic=True, smooth=True)
    assert view.overlay_item is None and view.field_limits is None


@pytest.mark.parametrize("smooth", [False, True])
def test_adjacent_triangles_do_not_render_false_white_mesh_seams(view, smooth):
    mesh = PlanarMesh(np.array([[0., 0.], [1., 0.], [0., 1.], [1., 1.]]) * 1e-6,
                      np.array([[0, 1, 2], [1, 3, 2]]), {"layer:a": np.array([0, 1])})
    result = SimulationResult(mesh, fields={"a": {"potential": np.ones(4)}})
    view.show_field(result, "a", "potential", smooth=smooth, colormap="magma")
    expected = rgba(.5, "magma")[:3]
    for x, y in [(.5, .5), (.2, .8), (.8, .2), (.25, .5)]:
        np.testing.assert_allclose(_picture_pixel(view, x, y), expected, atol=.004)


@pytest.mark.parametrize("logarithmic", [False, True])
def test_automatic_constant_map_uses_neutral_palette_midpoint(view, logarithmic):
    result = _result()
    result.fields["a"]["potential"][:3] = 10.
    view.show_field(result, "a", "potential", logarithmic=logarithmic, colormap="coolwarm", smooth=True)
    assert view.field_limits == (10., 10.) and not view.field_manual_limits
    np.testing.assert_allclose(_picture_pixel(view, .2, .2), rgba(.5, "coolwarm")[:3], atol=.004)


@pytest.mark.parametrize("logarithmic", [False, True])
@pytest.mark.parametrize("smooth", [False, True])
def test_automatic_one_ulp_map_is_neutral_without_changing_raw_extrema(view, logarithmic, smooth):
    result = _result()
    low, high = 301.62441496841313, 301.6244149684132
    result.fields["a"]["potential"][:3] = [low, high, low]
    raw = result.fields["a"]["potential"].copy()
    view.show_field(result, "a", "potential", logarithmic=logarithmic, colormap="viridis", smooth=smooth)
    assert view.field_limits[0] < low < high < view.field_limits[1]
    assert not view.field_manual_limits
    assert field_support(result, "a", "potential").limits == (low, high)
    np.testing.assert_array_equal(result.fields["a"]["potential"], raw)
    for x, y in [(.2, .2), (.6, .1)]:
        np.testing.assert_allclose(_picture_pixel(view, x, y), rgba(.5, "viridis")[:3], atol=.006)


@pytest.mark.parametrize("smooth", [False, True])
def test_tiny_factor_two_field_retains_quantitative_palette_contrast(view, smooth):
    result = _result()
    result.fields["a"]["potential"][:3] = [1e-40, 2e-40, 1e-40]
    view.show_field(result, "a", "potential", colormap="magma", smooth=smooth)
    assert view.field_limits == (1e-40, 2e-40)
    fraction = .2 if smooth else 1 / 3
    np.testing.assert_allclose(_picture_pixel(view, .2, .2), rgba(fraction, "magma")[:3], atol=.025)


def test_explicit_one_ulp_log_range_keeps_scalar_gradient_and_manual_bounds(view):
    result = _result()
    low, high = 300., np.nextafter(300., 301.)
    result.fields["a"]["potential"][:3] = [low, high, low]
    raw = result.fields["a"]["potential"].copy()
    view.show_field(result, "a", "potential", logarithmic=True, colormap="magma", smooth=True, limits=(low, high))
    assert view.field_limits == (low, high) and view.field_manual_limits
    np.testing.assert_allclose(_picture_pixel(view, .2, .2), rgba(.2, "magma")[:3], atol=.025)
    np.testing.assert_array_equal(result.fields["a"]["potential"], raw)


def test_mesh_uses_preserved_difference_component_ids(view):
    result = _result()
    result.configuration["visualization"] = {"source_components": {"a": {"component:3": [0]}}}
    result.mesh.regions["layer:a"] = np.array([0])
    view.show_field(result, "a", "potential", region="component:3")
    view.show_mesh(result.mesh, layer="a", region="component:3")
    assert view.mesh_item.boundingRect() == view.overlay_item.boundingRect()


def test_field_picture_cache_reuses_limits_and_invalidates_for_result_and_array(view, monkeypatch):
    result = _result()
    build = view._build_field_picture
    calls = []
    def counted(*args):
        calls.append(1)
        return build(*args)
    monkeypatch.setattr(view, "_build_field_picture", counted)
    view.show_field(result, "a", "potential", smooth=True)
    first = view.overlay_item.picture
    view.show_field(result, "a", "potential", smooth=True)
    assert view.overlay_item.picture is first and len(calls) == 1
    view.show_field(result, "a", "potential", smooth=True, colormap="magma")
    assert len(calls) == 2
    result.fields["a"]["potential"] = result.fields["a"]["potential"].copy()
    view.show_field(result, "a", "potential", smooth=True)
    assert len(calls) == 3
    other = deepcopy(result)
    view.show_field(other, "a", "potential", smooth=True)
    assert len(calls) == 4 and len(view._field_cache) == 1
    view._field_cache_entries = 2
    for high in [40., 50., 60.]:
        view.show_field(other, "a", "potential", limits=(0., high))
    assert len(view._field_cache) == 2
    assert view._field_cache_bytes == sum(p.size() for p in view._field_cache.values())
    view.show_field(None, "", "")
    assert not view._field_cache and view._field_cache_bytes == 0


def test_viewport_signal_covers_zoom_pan_and_fit(view, qtbot):
    from PySide6.QtCore import QPointF
    view.set_geometries({"a": box(0., 0., 100e-6, 100e-6)})
    with qtbot.waitSignal(view.viewport_changed):
        view.scale(2., 2.)
    with qtbot.waitSignal(view.viewport_changed):
        view.centerOn(QPointF(20., -20.))
    with qtbot.waitSignal(view.viewport_changed):
        view.fit_all()
