# SPDX-License-Identifier: MPL-2.0
"""Unavailable comparison fields cannot retain or export an older map."""
from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import numpy as np
import pytest

from etlayout.gui.comparison import MapComparisonDialog
from etlayout.models import PlanarMesh, SimulationResult


def result(fields=None):
    mesh = PlanarMesh(
        points=np.array([[0., 0.], [1e-6, 0.], [0., 1e-6]]),
        triangles=np.array([[0, 1, 2]]), regions={"layer:metal": np.array([0])})
    return SimulationResult(mesh, fields={"metal": fields or {
        "potential": np.array([1., 2., 3.]), "contact_density": np.array([0.])}},
        configuration={"layers": [{"id": "metal", "name": "Metal"}]}, converged=True)


def assert_empty(dialog):
    assert dialog.limits is None
    assert not dialog.has_comparison
    assert not dialog.export_button.isEnabled()
    assert dialog.scale.isHidden()
    assert all(view.overlay_item is None and not view.field_key for view in dialog.views)
    assert all(not label.text() for label in dialog.bounds_labels)


def test_zero_field_on_log_scale_clears_previous_maps_and_blocks_export(qtbot, tmp_path):
    dialog = MapComparisonDialog([("A", result()), ("B", result())])
    qtbot.addWidget(dialog)
    assert all(view.overlay_item is not None for view in dialog.views)
    dialog.logarithmic.setChecked(True)
    dialog.field.setCurrentIndex(dialog.field.findData("contact_density"))
    assert dialog.field.currentData() == "contact_density"
    assert "No finite positive values" in dialog.legend.text()
    assert_empty(dialog)
    destination = tmp_path / "invalid.svg"
    with pytest.raises(ValueError, match="No valid field comparison"):
        dialog.export_to(destination)
    assert not destination.exists()
    # Switching back to linear scale restores this same, valid zero-valued field.
    dialog.logarithmic.setChecked(False)
    assert dialog.field.currentData() == "contact_density"
    assert all(view.field_key == "contact_density" and view.overlay_item is not None for view in dialog.views)
    assert dialog.limits == (0., 0.)
    assert dialog.export_button.isEnabled()
    dialog.export_to(tmp_path / "zero-field.svg")
    assert (tmp_path / "zero-field.svg").stat().st_size > 100


def test_unavailable_field_preserves_quantity_and_clears_both_maps(qtbot):
    thermal = result({"temperature": np.array([300., 301., 302.])})
    dialog = MapComparisonDialog([("A", result()), ("B", result()), ("Thermal only", thermal)])
    qtbot.addWidget(dialog)
    assert dialog.field.currentData() == "potential"
    dialog.sources[1].setCurrentIndex(2)
    assert dialog.field.currentData() == "potential"
    assert "unavailable" in dialog.field.currentText()
    assert "Potential is unavailable" in dialog.legend.text()
    assert_empty(dialog)
    dialog.sources[1].setCurrentIndex(1)
    assert dialog.field.currentData() == "potential"
    assert "unavailable" not in dialog.field.currentText()
    assert dialog.has_comparison


@pytest.mark.parametrize("values,logarithmic", [
    (np.array([np.nan, np.nan, np.nan]), False),
    (np.array([np.nan, np.nan, np.nan]), True),
    (np.array([-2., -1., 0.]), True),
    (np.array([-2., 1., 0.]), True),  # A positive node cannot make the triangle mean positive.
])
def test_one_side_without_readable_values_invalidates_entire_comparison(qtbot, values, logarithmic):
    first, second = result(), result()
    dialog = MapComparisonDialog([("A", first), ("B", second)])
    qtbot.addWidget(dialog)
    dialog.logarithmic.setChecked(logarithmic)
    second.fields["metal"]["potential"] = values
    dialog.update_maps()
    assert "B" in dialog.legend.text()
    assert_empty(dialog)


def test_no_mesh_elements_clear_old_bounds_and_maps(qtbot):
    first, second = result(), result()
    dialog = MapComparisonDialog([("A", first), ("B", second)])
    qtbot.addWidget(dialog)
    second.mesh.regions["layer:metal"] = np.array([], dtype=int)
    dialog.update_maps()
    assert "No mesh elements" in dialog.legend.text()
    assert_empty(dialog)


@pytest.mark.parametrize("logarithmic", [False, True])
def test_partial_missing_triangle_cannot_expand_shared_scale_or_extent(qtbot, logarithmic):
    mesh = PlanarMesh(
        points=np.array([[0., 0.], [1., 0.], [0., 1.],
                         [100., 0.], [101., 0.], [100., 1.]]) * 1e-6,
        triangles=np.array([[0, 1, 2], [3, 4, 5]]),
        regions={"layer:metal": np.array([0, 1])})
    partial = SimulationResult(mesh, fields={"metal": {
        "potential": np.array([1., 2., 3., 1e12, np.nan, 1e12])}},
        configuration={"layers": [{"id": "metal", "name": "Metal"}]}, converged=True)
    dialog = MapComparisonDialog([("A", partial), ("B", partial)])
    qtbot.addWidget(dialog)
    dialog.logarithmic.setChecked(logarithmic)
    assert dialog.has_comparison
    assert dialog.limits == (1., 3.)
    for view, label in zip(dialog.views, dialog.bounds_labels):
        assert view.overlay_item.sceneBoundingRect().right() == pytest.approx(1.)
        # A generous scene rect permits panning, while the fitted viewport and
        # reported field bounds must exclude the unsupported distant triangle.
        assert view.mapToScene(view.viewport().rect()).boundingRect().right() < 2.
        assert "1 displayed / 2 layer elements" in label.text()
