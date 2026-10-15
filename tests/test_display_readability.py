# SPDX-License-Identifier: MPL-2.0
"""Quantitative precision and screen-space labels, independent of UI structure."""
import os
import runpy
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import numpy as np
import pytest
from PySide6.QtCore import QRectF
from shapely.geometry import box

from etlayout.gui.field_scale import FieldScaleControl
from etlayout.gui.viewer import LayoutView
from etlayout.io.export import export_field_plot
from etlayout.models import PlanarMesh, SimulationResult
from etlayout.visualization.formatting import distinct_labels, scale_ticks, series_context_range
from etlayout.visualization.support import display_limits, field_support


@pytest.mark.parametrize("values", [
    (301.62441, 301.62442, 301.62443), (1e-40, 1.5e-40, 2e-40),
    (-0., 0.), (1., np.nextafter(1., 2.)), (1e10, 1.5e10, 2e10),
])
def test_scale_labels_distinguish_distinct_values(values):
    labels = distinct_labels(values)
    assert len(set(labels)) == len(set(values))
    assert all(np.isfinite(float(label)) for label in labels)
    if values == (-0., 0.):
        assert labels == ("0", "0")


@pytest.mark.parametrize("logarithmic", [False, True])
def test_one_ulp_scale_uses_two_exact_endpoint_ticks(logarithmic):
    low, high = 300., np.nextafter(300., 301.)
    fractions, labels = scale_ticks(low, high, logarithmic=logarithmic)
    assert fractions == (0., 1.)
    assert len(set(labels)) == 2


@pytest.mark.parametrize("values", [(1e-40, 2e-40), (-1e-40, 1e-40), (0., np.nextafter(0., 1.)),
                                    (-2., -1.), (-1., 1.), (1., 2.), (0., 0.), (np.nan, np.inf)])
def test_contextual_series_range_preserves_resolved_tiny_signed_and_ordinary_signals(values):
    assert series_context_range(values) is None


@pytest.mark.parametrize("value", [0.238095238095225, -0.238095238095225, 1e-40])
def test_contextual_series_range_is_only_a_display_viewport(value):
    original = np.array([value, value + 128 * np.spacing(abs(value))])
    low, high = series_context_range(original)
    assert low < original.min() <= original.max() < high
    assert high - low >= abs(value) * .001
    np.testing.assert_array_equal(original, [value, value + 128 * np.spacing(abs(value))])


def test_study_context_keeps_exact_curves_failed_points_and_export_values(qtbot, tmp_path, monkeypatch):
    import csv

    from PySide6.QtWidgets import QFileDialog

    from etlayout.gui.plots import StudyPanel
    from etlayout.simulation.sweep import SweepRow

    panel = StudyPanel()
    qtbot.addWidget(panel)
    values = [.238095238095225, .238095238095240]
    rows = [SweepRow(index + 1., _thermal_result((0., 0., 0.))) for index in range(2)]
    for row, value in zip(rows, values):
        row.result.metrics = {"total_current": value}
        row.result.converged = True
    panel.set_rows(rows)
    panel.metric_combo.setCurrentIndex(panel.metric_combo.findData("total_current"))
    panel.refresh()
    np.testing.assert_array_equal(panel.plot.listDataItems()[0].getData()[1], values)
    low, high = panel.plot.viewRange()[1]
    assert low < min(values) <= max(values) < high
    assert "numerical data are unchanged" in panel.summary.text()
    axis = panel.plot.getAxis("left")
    ticks = axis.tickStrings([.238, .2381, .2382], 1000., .0001)
    assert ticks == ["238", "238.1", "238.2"]
    path = tmp_path / "study.csv"
    monkeypatch.setattr(QFileDialog, "getSaveFileName", lambda *args: (str(path), "CSV"))
    panel.export_csv()
    with path.open(encoding="utf-8", newline="") as stream:
        exported = list(csv.DictReader(stream))
    assert [float(row["total_current"]) for row in exported] == values
    rows[-1].result.metrics["total_current"] = .4
    rows[-1].result.converged = False
    panel.refresh()
    assert "numerical data are unchanged" not in panel.summary.text()
    np.testing.assert_array_equal(panel.plot.listDataItems()[1].getData()[1], [.4])


def test_narrow_ranges_remain_editable_and_compact_ticks_do_not_collide(qtbot):
    scale = FieldScaleControl()
    qtbot.addWidget(scale)
    scale.resize(260, 130)
    scale.set_details({"field": "temperature", "unit": "K", "low": 301.62441496841313,
                       "high": 301.6244149684132})
    scale.show()
    qtbot.waitExposed(scale)
    assert scale.minimum.text() != scale.maximum.text()
    scale.lock.setChecked(True)
    assert scale.validated_limits() == (301.62441496841313, 301.6244149684132)
    _, labels = scale_ticks(301.62441496841313, 301.6244149684132)
    assert len(set(labels)) >= 2
    assert scale.color_bar.minimumHeight() == 62
    assert labels[0] in scale.color_bar.toolTip() and labels[-1] in scale.color_bar.toolTip()


def _thermal_result(rise):
    points = np.array([[0., 0.], [1., 0.], [0., 1.]]) * 1e-6
    mesh = PlanarMesh(points, np.array([[0, 1, 2]]), {"layer:metal": np.array([0])})
    return SimulationResult(mesh, fields={"metal": {"temperature": np.array(rise) + 300.}},
                            metrics={"thermal_reference": 300.})


@pytest.mark.parametrize("rise", [(0., 0., 0.), (0., np.spacing(300.), 0.), (1e-8, 2e-8, 1e-8)])
def test_temperature_rise_uses_operand_precision_only_for_roundoff(rise, qtbot):
    result = _thermal_result(rise)
    raw = result.fields["metal"]["temperature"].copy()
    support = field_support(result, "metal", "temperature_rise")
    view = LayoutView()
    qtbot.addWidget(view)
    details = []
    view.legend_details.connect(details.append)
    view.show_field(result, "metal", "temperature_rise")
    low, high = view.field_limits
    if max(rise) < 1e-10:
        assert low < support.limits[0] <= support.limits[1] < high
        assert np.ptp(support.element_data[support.indices]) / (high - low) < .02
        assert "Raw values are preserved" in details[-1]["precision_note"]
    else:
        assert view.field_limits == support.limits
        assert not details[-1]["precision_note"]
    assert display_limits(support.limits, limits=(-1e-9, 1e-9), resolution_scale=support.resolution_scale) == (-1e-9, 1e-9)
    np.testing.assert_array_equal(raw, result.fields["metal"]["temperature"])


def test_exported_narrow_temperature_ticks_and_data_remain_distinct(tmp_path):
    result = _thermal_result((1.624410, 1.624420, 1.624430))
    raw = result.fields["metal"]["temperature"].copy()
    path = export_field_plot(result, "metal", "temperature", tmp_path / "narrow.svg")
    source = path.read_text(encoding="utf-8")
    # The full contour range is only 20 µK, but tick labels remain quantitative.
    assert "301.62441" in source and "301.62442" in source
    np.testing.assert_array_equal(raw, result.fields["metal"]["temperature"])


def test_terminal_labels_reflow_without_moving_physical_anchors_and_thin_outlines(qtbot):
    view = LayoutView()
    qtbot.addWidget(view)
    view.resize(700, 500)
    geometry = box(0., 0., 500e-6, 250e-6)
    terminals = {f"finger {index}": box((index + 1) * 10e-6, 20e-6, (index + 1) * 10e-6 + .6e-6, 220e-6)
                 for index in range(24)}
    view.set_geometries({"gate": geometry}, terminals=terminals)
    view.show()
    qtbot.waitExposed(view)
    view.fit_all()
    anchors = {key: items[1].pos() for key, items in view.terminal_items.items()}

    def visible_boxes():
        return [QRectF(view.viewportTransform().map(item.pos()), item.box.size()).translated(item.box.x(), item.box.y())
                for items in view.terminal_items.values() for item in items[1:] if item.label_visible]

    boxes = visible_boxes()
    assert boxes
    assert all(not first.intersects(second) for index, first in enumerate(boxes) for second in boxes[index + 1:])
    assert all(items[0].pen().widthF() < 1. for items in view.terminal_items.values())
    view.scale(2., 2.)
    assert {key: items[1].pos() for key, items in view.terminal_items.items()} == anchors
    boxes = visible_boxes()
    assert all(not first.intersects(second) for index, first in enumerate(boxes) for second in boxes[index + 1:])
    view.show_callouts([{"id": "contact", "label": "Actual gate contact", "point": (10.3e-6, 120e-6)}])
    assert (view.callout_items["contact"].pos().x(), view.callout_items["contact"].pos().y()) == pytest.approx((10.3, -120.))
    assert not view.terminal_items["finger 0"][1].label_visible


@pytest.mark.packaging
def test_frozen_deployment_environment_removes_source_and_plugin_paths(monkeypatch):
    builder = runpy.run_path(str(Path(__file__).resolve().parents[1] / "scripts/build_windows.py"))
    for key in ("PYTHONPATH", "PYTHONHOME", "VIRTUAL_ENV", "QT_PLUGIN_PATH", "QT_QPA_PLATFORM_PLUGIN_PATH", "COVERAGE_PROCESS_START"):
        monkeypatch.setenv(key, "development-only-sentinel")
    environment = builder["isolated_environment"]()
    assert "development-only-sentinel" not in str(environment)
    assert environment["QT_QPA_PLATFORM"] == "windows"
    assert all("python" not in part.casefold() and ".venv" not in part.casefold() for part in environment["PATH"].split(os.pathsep))


@pytest.mark.packaging
def test_extracted_deployment_refuses_archive_traversal(tmp_path):
    import zipfile

    builder = runpy.run_path(str(Path(__file__).resolve().parents[1] / "scripts/build_windows.py"))
    archive_path = tmp_path / "unsafe.zip"
    with zipfile.ZipFile(archive_path, "w") as archive:
        archive.writestr("../unsafe.dll", b"unsafe path")
    with pytest.raises(RuntimeError, match="unsafe extraction path"):
        builder["verify_extracted_archive"](archive_path, tmp_path, builder["isolated_environment"]())
