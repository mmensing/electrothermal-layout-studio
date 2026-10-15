# SPDX-License-Identifier: MPL-2.0
"""Same-run layers, linked navigation and quantitative comparison display state."""
from __future__ import annotations

import json
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import numpy as np
import pytest
from PySide6.QtCore import QPointF, Qt
from PySide6.QtTest import QTest

from etlayout.gui.comparison import MapComparisonDialog
from etlayout.models import PlanarMesh, SimulationResult
from etlayout.visualization.support import display_limits, field_support


@pytest.fixture
def comparison(qtbot):
    mesh = PlanarMesh(
        np.array([[0., 0.], [1., 0.], [0., 1.], [10., 0.], [11., 0.], [10., 1.]]) * 1e-6,
        np.array([[0, 1, 2], [3, 4, 5]]),
        {"layer:m1": np.array([0, 1]), "layer:m2": np.array([0, 1]), "heat:left": np.array([0])})
    result = SimulationResult(mesh, fields={
        "m1": {"potential": np.array([1., 2., 3., 4., 5., 6.]), "contact_density": np.array([1., 2.])},
        "m2": {"potential": np.array([10., 20., 30., 100., 110., 120.]), "contact_density": np.array([4., 8.])}},
        configuration={"layers": [{"id": "m1", "name": "Lower"}, {"id": "m2", "name": "Upper"}]},
        converged=True)
    dialog = MapComparisonDialog([("Shared run", result)])
    qtbot.addWidget(dialog)
    dialog.show()
    qtbot.waitUntil(lambda: dialog.has_comparison)
    return dialog


def center(view):
    return view.mapToScene(view.viewport().rect().center())


def test_single_run_defaults_to_distinct_layers_and_shared_limits(comparison):
    assert [combo.currentData() for combo in comparison.layers] == ["m1", "m2"]
    assert comparison.selected_result(0) is comparison.selected_result(1)
    assert comparison.limits == (1., 120.)
    assert all(view.overlay_item is not None for view in comparison.views)
    assert comparison.link_views.isChecked()


def test_identical_constant_fields_remain_valid_automatic_comparisons(comparison, tmp_path):
    result = comparison.selected_result(0)
    for fields in result.fields.values():
        fields['potential'] = np.full(len(result.mesh.points), 2.5)
    comparison.update_maps()
    assert comparison.has_comparison and comparison.limits == (2.5, 2.5)
    for view in comparison.views:
        assert view.overlay_item is not None
        assert view.field_limits == (2.5, 2.5)
        assert not view.field_manual_limits
    comparison.export_to(tmp_path / 'constant.svg')
    metadata = json.loads((tmp_path / 'constant.svg.json').read_text(encoding='utf-8'))
    assert metadata['limits'] == [2.5, 2.5] and not metadata['manual_limits']
    comparison.range_control.lock.setChecked(True)
    assert not comparison.has_comparison
    assert 'Minimum must be smaller' in comparison.legend.text()


@pytest.mark.parametrize("logarithmic", [False, True])
def test_one_ulp_shared_auto_range_preserves_fields_and_manual_override(comparison, tmp_path, logarithmic):
    result = comparison.selected_result(0)
    raw_limits = (301.62441496841313, 301.6244149684132)
    original = {}
    for layer, value in zip(("m1", "m2"), raw_limits):
        result.fields[layer]["potential"] = np.full(len(result.mesh.points), value)
        original[layer] = result.fields[layer]["potential"].copy()
    comparison.logarithmic.setChecked(logarithmic)
    comparison.update_maps()
    expected = display_limits(raw_limits, logarithmic=logarithmic)
    assert comparison.has_comparison and comparison.limits == expected
    assert all(view.field_limits == expected and not view.field_manual_limits for view in comparison.views)
    for layer, value in zip(("m1", "m2"), raw_limits):
        assert field_support(result, layer, "potential").limits == (value, value)
        np.testing.assert_array_equal(result.fields[layer]["potential"], original[layer])
    comparison.export_to(tmp_path / "roundoff.svg")
    metadata = json.loads((tmp_path / "roundoff.svg.json").read_text(encoding="utf-8"))
    assert metadata["limits"] == list(expected) and not metadata["manual_limits"]
    comparison.range_control.minimum.setText(f"{raw_limits[0]:.17g}")
    comparison.range_control.maximum.setText(f"{raw_limits[1]:.17g}")
    comparison.range_control.lock.setChecked(True)
    assert comparison.has_comparison and comparison.limits == raw_limits
    assert all(view.field_limits == raw_limits and view.field_manual_limits for view in comparison.views)


def test_linked_navigation_follows_either_panel_and_can_be_disabled(comparison):
    left, right = comparison.views
    left.scale(2., 2.)
    left.centerOn(QPointF(4., -.4))
    left.viewport_changed.emit()
    assert right.transform().m11() == pytest.approx(left.transform().m11())
    assert abs(center(right).x() - center(left).x()) < 3 / left.transform().m11()
    right.scale(1.5, 1.5)
    right.viewport_changed.emit()
    assert left.transform().m11() == pytest.approx(right.transform().m11())
    comparison.link_views.setChecked(False)
    old = right.transform().m11()
    left.scale(2., 2.)
    left.viewport_changed.emit()
    assert right.transform().m11() == old


def test_manual_shared_limits_reject_invalid_values_and_auto_recovers(comparison, tmp_path):
    scale = comparison.range_control
    scale.minimum.setText("2 V")
    scale.maximum.setText("40 V")
    scale.lock.setChecked(True)
    assert comparison.limits == (2., 40.)
    assert "Manual shared" in comparison.legend.text()
    scale.maximum.setText("1 V")
    scale.range_changed.emit()
    assert not comparison.has_comparison
    assert not comparison.export_button.isEnabled()
    assert "Manual range not applied" in comparison.legend.text()
    with pytest.raises(ValueError, match="No valid field comparison"):
        comparison.export_to(tmp_path / "invalid.svg")
    scale.auto_button.click()
    assert comparison.has_comparison
    assert comparison.limits == (1., 120.)


def test_panel_regions_restrict_shared_range_and_rendered_support(comparison):
    for combo in comparison.regions:
        index = combo.findData("heat:left")
        assert index >= 0
        combo.setCurrentIndex(index)
    assert comparison.limits == (1., 30.)
    for view, label in zip(comparison.views, comparison.bounds_labels):
        assert view.field_region == "heat:left"
        assert view.overlay_item.sceneBoundingRect().right() == pytest.approx(1.)
        assert "1 displayed / 2 layer elements" in label.text()


def test_colormap_smoothing_and_manual_range_reach_export(comparison, tmp_path):
    comparison.colormap.setCurrentIndex(comparison.colormap.findData("magma"))
    comparison.smooth.setChecked(True)
    assert comparison.range_control.current_colormap == "magma"
    assert all(view.field_colormap == "magma" for view in comparison.views)
    comparison.range_control.minimum.setText("1")
    comparison.range_control.maximum.setText("100")
    comparison.range_control.lock.setChecked(True)
    comparison.export_to(tmp_path / "comparison.svg")
    comparison.export_to(tmp_path / "comparison.png")
    svg = (tmp_path / "comparison.svg").read_text(encoding="utf-8")
    assert "Manual shared color scale" in svg
    assert "Potential" in svg
    assert (tmp_path / "comparison.png").stat().st_size > 1000


def test_palette_and_smoothing_preserve_navigation_and_keyboard_pan_links(comparison, qtbot):
    left, right = comparison.views
    left.scale(2., 2.)
    left.centerOn(QPointF(4., -.4))
    previous_scale, previous_center = left.transform().m11(), center(left)
    comparison.colormap.setCurrentIndex(comparison.colormap.findData("viridis"))
    comparison.smooth.setChecked(True)
    assert left.transform().m11() == pytest.approx(previous_scale)
    assert abs(center(left).x() - previous_center.x()) < 3 / previous_scale
    left.setFocus()
    previous_x = center(left).x()
    QTest.keyClick(left, Qt.Key.Key_Right)
    qtbot.waitUntil(lambda: center(left).x() > previous_x)
    assert abs(center(right).x() - center(left).x()) < 3 / left.transform().m11()


def test_comparison_controls_fit_compact_window(comparison, qtbot):
    comparison.resize(1280, 800)
    qtbot.waitUntil(lambda: comparison.width() >= 1280)
    for control in [comparison.field, comparison.colormap, comparison.logarithmic,
                    comparison.smooth, comparison.link_views, comparison.export_button,
                    comparison.range_control, *comparison.layers, *comparison.regions]:
        assert control.isVisible()
        assert comparison.rect().contains(control.mapTo(comparison, control.rect().topLeft()))
        assert comparison.rect().contains(control.mapTo(comparison, control.rect().bottomRight()))
    assert all(view.viewport().height() > 300 for view in comparison.views)
    assert all(entry.width() >= 100 for entry in (comparison.range_control.minimum, comparison.range_control.maximum))


def test_stale_region_clears_comparison_instead_of_leaving_old_maps(comparison):
    comparison.regions[0].addItem("Removed region", "heat:removed")
    comparison.regions[0].setCurrentIndex(comparison.regions[0].count() - 1)
    assert not comparison.has_comparison
    assert not comparison.export_button.isEnabled()
    assert all(view.overlay_item is None for view in comparison.views)
    assert "Unknown result region" in comparison.legend.text()


def test_comparison_export_preserves_both_sources_and_exact_display_state(comparison, qtbot, tmp_path):
    from copy import deepcopy

    other = deepcopy(comparison.selected_result(0))
    other.configuration_hash = 'comparison-hash'
    comparison.selected_result(0).configuration_hash = 'current-hash'
    dialog = MapComparisonDialog([('Current', comparison.selected_result(0)), ('Comparison', other)])
    qtbot.addWidget(dialog)
    dialog.layers[1].setCurrentIndex(dialog.layers[1].findData('m2'))
    dialog.regions[0].setCurrentIndex(dialog.regions[0].findData('heat:left'))
    dialog.colormap.setCurrentIndex(dialog.colormap.findData('magma'))
    dialog.smooth.setChecked(True)
    dialog.logarithmic.setChecked(True)
    dialog.range_control.minimum.setText('2')
    dialog.range_control.maximum.setText('100')
    dialog.range_control.lock.setChecked(True)
    dialog.link_views.setChecked(False)
    dialog.shared_extent.setChecked(False)
    dialog.views[0].scale(1.5, 1.5)
    path = tmp_path / 'comparison.svg'
    dialog.export_to(path)
    metadata = json.loads(path.with_suffix('.svg.json').read_text(encoding='utf-8'))
    assert metadata['field'] == 'potential' and metadata['unit'] == 'V'
    assert metadata['limits'] == [2., 100.] and metadata['manual_limits']
    assert metadata['logarithmic'] and metadata['colormap'] == 'magma'
    assert not metadata['linked_navigation'] and not metadata['shared_extent']
    assert [panel['configuration_hash'] for panel in metadata['panels']] == ['current-hash', 'comparison-hash']
    assert [panel['layer'] for panel in metadata['panels']] == ['m1', 'm2']
    assert [panel['region'] for panel in metadata['panels']] == ['heat:left', '']
    assert all(panel['smooth'] for panel in metadata['panels'])
    for panel, view in zip(metadata['panels'], dialog.views):
        rectangle = view.mapToScene(view.viewport().rect()).boundingRect()
        assert panel['viewport_bounds_um'] == pytest.approx([
            rectangle.left(), -rectangle.bottom(), rectangle.right(), -rectangle.top()])
        assert panel['viewport_transform'][0] == pytest.approx(view.transform().m11())


def test_comparison_discloses_snapshot_time_and_drives_even_with_identical_hashes(comparison, tmp_path):
    from copy import deepcopy
    first = comparison.selected_result(0)
    second = deepcopy(first)
    first.configuration_hash = second.configuration_hash = "same-transient-run"
    first.metrics.update(time_s=.0001, applied_drives={"heat_sources.heater.value": 1.},
                         drive_time_convention="left_limit_at_step_end")
    second.metrics.update(time_s=.0002, applied_drives={"heat_sources.heater.value": 0.},
                          drive_time_convention="left_limit_at_step_end")
    comparison.results = [("Selected snapshot", first), ("Later snapshot", second)]
    for side, source in enumerate(comparison.sources):
        source.blockSignals(True)
        source.clear()
        source.addItems(["Selected snapshot", "Later snapshot"])
        source.setCurrentIndex(side)
        source.blockSignals(False)
        comparison.source_changed(side)
    assert "t = 0.0001 s" in comparison.bounds_labels[0].text()
    assert "t = 0.0002 s" in comparison.bounds_labels[1].text()
    comparison.export_to(tmp_path / "timed.svg")
    metadata = json.loads((tmp_path / "timed.svg.json").read_text())
    assert [panel["time_s"] for panel in metadata["panels"]] == [.0001, .0002]
    assert metadata["panels"][0]["applied_drives"] == {"heat_sources.heater.value": 1.}
    assert metadata["panels"][1]["drive_time_convention"] == "left_limit_at_step_end"
    assert "t = 0.0001 s" in (tmp_path / "timed.svg").read_text()
    assert "t = 0.0002 s" in (tmp_path / "timed.svg").read_text()
