# SPDX-License-Identifier: MPL-2.0
"""Presentation state, region scope and explicit derived-result workflows."""
from __future__ import annotations

import os
from copy import deepcopy
from dataclasses import replace

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import numpy as np
import pytest
from PySide6.QtCore import QPointF

from etlayout.examples import make_example
from etlayout.gui.main_window import MainWindow
from etlayout.io.project import physics_hash
from etlayout.models import LayoutSource, PhysicalLayer, PlanarMesh, RegionSpec, SimulationResult
from etlayout.visualization.difference import make_field_difference


def select(combo, value):
    index = combo.findData(value)
    assert index >= 0, value
    combo.setCurrentIndex(index)


@pytest.fixture
def two_regions(tmp_path):
    project = make_example("E1", tmp_path / "examples")
    project.layout = LayoutSource()
    project.terminals = []
    project.thermal_boundaries = []
    points = np.array([[0, 0], [100, 0], [0, 100], [200, 0], [300, 0], [200, 100]], dtype=float) * 1e-6
    triangles = np.array([[0, 1, 2], [3, 4, 5]])
    geometry = RegionSpec(polygons=[{"exterior": points[ids].tolist(), "holes": []} for ids in triangles])
    project.layers = [PhysicalLayer("metal", "Two metal islands", "copper", geometry=geometry),
                      PhysicalLayer("substrate", "Substrate", "copper", role="thermal", geometry=geometry)]
    mesh = PlanarMesh(points, triangles, {"layer:metal": np.array([0, 1]),
        "layer:substrate": np.array([0, 1]), "heat:patch": np.array([0])})
    result = SimulationResult(mesh=mesh, fields={
        "metal": {"potential": np.array([0., 1., 2., 10., 11., 12.]),
                  "temperature": np.array([300., 301., 302., 310., 311., 312.]),
                  "current_density": np.array([[3., 4.], [6., 8.]])},
        "substrate": {"temperature": np.array([300., 300.1, 300.2, 300.3, 300.4, 300.5])}},
        configuration=project.to_dict(), configuration_hash=physics_hash(project), converged=True,
        metrics={"thermal_reference": 300., "temperature_max": 312.})
    return project, result


@pytest.fixture
def window(qtbot, tmp_path, two_regions):
    project, result = two_regions
    widget = MainWindow(project, result, database_path=tmp_path / "materials.sqlite3", recover=False)
    widget.recovery_path = tmp_path / "recovery.etl"
    widget.autosave_timer.stop()
    widget.errors = []
    widget.error = lambda title, message: widget.errors.append((title, str(message)))

    def close(window):
        window.cancel_geometry()
        window.dirty = False
        if window.inspection_context:
            window.inspection_context["dirty"] = False

    qtbot.addWidget(widget, before_close_func=close)
    widget.show()
    qtbot.waitUntil(lambda: widget.prepare_job is None, timeout=30000)
    assert widget.geometry is not None
    assert not widget.errors
    return widget


def test_region_controls_maps_probes_profiles_statistics_and_model_scope(window):
    before = physics_hash(window.project)
    select(window.field_region, "component:1")
    assert window.legend_scale.details["high"] == 2.
    assert "1 elements" in window.selection_statistics.text()
    window.probe(220e-6, 20e-6)
    assert "outside active region" in window.console.toPlainText()
    window.probe(20e-6, 20e-6)
    assert "Potential: 0.6 V" in window.console.toPlainText()
    window.on_line_drawn("profile", [[10e-6, 10e-6], [230e-6, 10e-6]])
    profile = window.profiles.profiles[-1]
    assert np.isfinite(profile["values"][0]) and np.isnan(profile["values"][-1])
    assert profile["provenance"]["region"] == "component:1"
    select(window.field_layer, "substrate")
    select(window.field_layer, "metal")
    assert window.field_region.currentData() == "component:1"
    assert physics_hash(window.project) == before and not window.dirty


def test_manual_range_and_colormap_are_applied_and_invalid_range_clears_map(window):
    select(window.colormap_combo, "viridis")
    window.legend_scale.minimum.setText("0 V")
    window.legend_scale.maximum.setText("5 V")
    window.legend_scale.lock.setChecked(True)
    assert window.legend_scale.details["colormap"] == "viridis"
    assert window.legend_scale.details["high"] == 5.
    window.legend_scale.maximum.setText("-1 V")
    window.update_overlay()
    assert window.view.overlay_item is None
    assert "Manual range not applied" in window.field_hint.text()
    assert window.legend_scale.lock.isChecked()
    window.legend_scale.maximum.setText("6 V")
    window.update_overlay()
    assert window.view.overlay_item is not None and window.legend_scale.details["high"] == 6.
    window.legend_scale.auto_button.click()
    assert window.legend_scale.details["high"] == 12.


def test_difference_mode_survives_presentation_changes_and_keeps_paired_provenance(window):
    other = deepcopy(window.result)
    other.configuration_hash = "other-run"
    other.converged = False
    other.fields["metal"]["potential"] -= .5
    window.load_difference_result(other, "Lower bias")
    assert window.view_mode.currentData() == "difference"
    derived = window.display_result
    assert derived.metrics == {} and derived.terminals == {}
    assert window.legend_scale.details["low"] == pytest.approx(-.5)
    select(window.colormap_combo, "magma")
    window.isolate_layer.setChecked(False)
    window.smooth_field.setChecked(True)
    select(window.field_region, "heat:patch")
    assert window.display_result is derived
    assert window.view_mode.currentText() == "Current − comparison"
    assert "Current − comparison" in window.legend.toolTip()
    metadata = window.displayed_provenance()
    assert metadata["sources"][1]["configuration_hash"] == "other-run"
    assert [source["converged"] for source in metadata["sources"]] == [True, False]
    assert metadata["converged"] is False and derived.converged is False
    assert "NOT CONVERGED" in window.field_hint.text()
    assert metadata["region"] == "heat:patch"
    assert "Original run metrics (not differences)" in window.key_metrics.text()
    select(window.view_mode, "original")
    assert window.display_result is None
    assert window.legend_scale.details["high"] == 2.
    assert window.displayed_provenance()["converged"] is True
    assert "NOT CONVERGED" not in window.field_hint.text()
    select(window.view_mode, "difference")
    assert window.display_result is derived
    assert "NOT CONVERGED" in window.field_hint.text()


def test_inspection_restores_layer_region_range_palette_and_viewport(window, qtbot):
    select(window.field_region, "component:2")
    select(window.colormap_combo, "cividis")
    window.legend_scale.minimum.setText("8")
    window.legend_scale.maximum.setText("14")
    window.legend_scale.lock.setChecked(True)
    window.view.scale(1.7, 1.7)
    window.view.centerOn(QPointF(230, -30))
    before = window.capture_view_state()
    other = deepcopy(window.result)
    other.configuration["name"] = "Inspected run"
    other.configuration_hash = "inspected-run"
    window.inspect_result(other)
    qtbot.waitUntil(lambda: window.prepare_job is None, timeout=30000)
    select(window.field_layer, "substrate")
    select(window.field_combo, "temperature")
    select(window.colormap_combo, "magma")
    window.return_to_project()
    qtbot.waitUntil(lambda: window.prepare_job is None, timeout=30000)
    after = window.capture_view_state()
    for key in ("layer", "field", "regions", "colormap", "range", "transform"):
        assert after[key] == before[key]
    assert after["center"] == pytest.approx(before["center"], abs=1.5 / window.view.transform().m11())
    assert not window.dirty


def test_difference_derived_temperature_and_magnitudes(two_regions):
    _, current = two_regions
    other = deepcopy(current)
    other.metrics["thermal_reference"] = 295.
    difference = make_field_difference(current, other, "metal", "temperature_rise")
    np.testing.assert_allclose(difference.fields["metal"]["temperature"], -5.)
    assert difference.metrics == {"thermal_reference": 0.}
    assert difference.configuration["visualization"]["thermal_references_K"] == [300., 295.]
    other.fields["metal"]["current_density"] *= .5
    difference = make_field_difference(current, other, "metal", "current_density")
    np.testing.assert_allclose(difference.fields["metal"]["current_density"], [2.5, 5.])
    assert difference.configuration["visualization"]["quantity"] == "difference of magnitudes"


def test_difference_never_bridges_comparison_holes(two_regions):
    _, current = two_regions
    other = deepcopy(current)
    # A smaller comparison triangle covers the first centroid but not the full
    # current triangle. Center-only resampling would incorrectly paint its edges.
    points = np.array([[20, 20], [55, 20], [20, 55]], dtype=float) * 1e-6
    other.mesh = replace(other.mesh, points=points, triangles=np.array([[0, 1, 2]]),
                         regions={"layer:metal": np.array([0])})
    other.fields = {"metal": {"current_density": np.array([[3., 4.]])}}
    with pytest.raises(ValueError, match="No complete mesh triangles"):
        make_field_difference(current, other, "metal", "current_density")


def test_difference_keeps_original_component_ids_when_first_island_is_missing(two_regions):
    from etlayout.visualization.support import field_support
    _, current = two_regions
    other = deepcopy(current)
    other.mesh.regions["layer:metal"] = np.array([1])
    derived = make_field_difference(current, other, "metal", "potential")
    assert not len(field_support(derived, "metal", "potential", region="component:1").indices)
    np.testing.assert_array_equal(field_support(derived, "metal", "potential", region="component:2").indices, [1])
