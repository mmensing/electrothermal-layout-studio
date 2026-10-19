# SPDX-License-Identifier: MPL-2.0
"""Temporal navigation preserves physical time, field support and export context."""
from __future__ import annotations

import csv
import json
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import numpy as np
import pytest
from PySide6.QtCore import QPointF, Qt

from etlayout.gui.comparison import MapComparisonDialog
from etlayout.gui.transient import TimeHistoryPanel, TimeNavigation
from etlayout.models import PlanarMesh, SimulationResult, TransientHistory
from etlayout.visualization.history import history_field_limits, local_history


def history_result(times=(0., 1., 2.)):
    mesh = PlanarMesh(
        np.array([[0., 0.], [1., 0.], [0., 1.], [3., 0.], [5., 0.], [3., 2.]]) * 1e-6,
        np.array([[0, 1, 2], [3, 4, 5]]),
        {"layer:metal": np.array([0, 1]), "heat:left": np.array([0])})
    configuration = {"layers": [{"id": "metal", "name": "Metal"}]}
    snapshots = [SimulationResult(mesh, fields={"metal": {"temperature": np.array([300. + 10*t] * 3 + [400. + 20*t] * 3)}},
                                  metrics={"time_s": t, "temperature_max": 400. + 20*t},
                                  configuration=configuration, configuration_hash="run", converged=True) for t in times]
    result = SimulationResult(mesh, fields=snapshots[-1].fields, configuration=configuration,
                              configuration_hash="run", metrics={"transient_peak_time": times[1]}, converged=True)
    result.transient = TransientHistory(np.asarray(times), snapshots,
                                        [{"time_s": t, "temperature_max": 400. + 20*t} for t in times], True)
    return result


def test_local_trace_mean_and_probe_follow_exact_region_support():
    result = history_result()
    times, mean = local_history(result, "metal", reduction="mean")
    np.testing.assert_array_equal(times, [0, 1, 2])
    np.testing.assert_allclose(mean, [380, 398, 416])  # The second triangle has four times the area.
    _, selected = local_history(result, "metal", region="heat:left", reduction="mean")
    np.testing.assert_allclose(selected, [300, 310, 320])
    _, point = local_history(result, "metal", region="heat:left", probe=(.2e-6, .2e-6))
    np.testing.assert_allclose(point, selected)
    _, excluded = local_history(result, "metal", region="heat:left", probe=(3.2e-6, .2e-6))
    assert np.isnan(excluded).all()
    assert history_field_limits(result, "metal", "temperature") == (300, 440)
    assert history_field_limits(result, "metal", "temperature", region="heat:left") == (300, 320)


def test_direct_time_and_peak_navigation_use_nearest_retained_frame(qtbot):
    widget = TimeNavigation()
    qtbot.addWidget(widget)
    widget.set_result(history_result())
    widget.time_input.setText("800 ms")
    qtbot.keyClick(widget.time_input, Qt.Key.Key_Return)
    assert widget.slider.value() == 1
    assert "nearest retained" in widget.label.toolTip()
    widget.slider.setValue(0)
    widget.jump_to_peak()
    assert widget.slider.value() == 1
    widget.time_input.setText("bad")
    widget.jump_to_input()
    assert widget.slider.value() == 1
    assert "finite number" in widget.time_input.toolTip()


def test_drag_selection_coalesces_and_release_flushes_final_frame(qtbot):
    widget = TimeNavigation()
    qtbot.addWidget(widget)
    widget.set_result(history_result())
    selections = []
    widget.snapshot_changed.connect(selections.append)
    widget.slider.setSliderDown(True)
    widget.slider.setValue(0)
    widget.slider.setValue(1)
    assert widget.displayed_metrics["time_s"] == 2.  # Canvas has not accepted a coalesced frame yet.
    widget.slider.setValue(2)
    assert not selections
    widget.slider.setSliderDown(False)
    assert selections == [2]
    qtbot.wait(60)
    assert selections == [2]
    widget.slider.setValue(1)
    assert selections == [2, 1]  # Programmatic and keyboard navigation remain synchronous.


def test_pending_drag_cannot_select_a_frame_from_a_replaced_result(qtbot):
    widget = TimeNavigation()
    qtbot.addWidget(widget)
    widget.set_result(history_result())
    selections = []
    widget.snapshot_changed.connect(selections.append)
    widget.slider.setSliderDown(True)
    widget.slider.setValue(0)
    widget.set_result(None)
    widget.slider.setSliderDown(False)
    qtbot.wait(60)
    assert not selections


def test_local_trace_context_preserves_probe_and_exports_selected_samples(qtbot, tmp_path):
    panel = TimeHistoryPanel()
    qtbot.addWidget(panel)
    panel.set_result(history_result())
    panel.set_context("metal", "heat:left", probe=(.2e-6, .2e-6))
    panel.scope_selector.setCurrentIndex(panel.scope_selector.findData("probe"))
    panel.set_context("metal", "heat:left")
    assert panel.probe == (.2e-6, .2e-6)
    np.testing.assert_allclose(panel.trace_values, [300, 310, 320])
    target = tmp_path / "local.csv"
    panel.export_local_history_to(target)
    with target.open(encoding="utf-8", newline="") as handle:
        rows = list(csv.DictReader(handle))
    assert [float(row["temperature_K"]) for row in rows] == pytest.approx([300, 310, 320])
    assert all(row["region"] == "heat:left" and row["reduction"] == "probe" for row in rows)
    panel.set_context("metal", "")
    assert panel.probe is None
    assert not len(panel.trace_times)


def test_comparison_links_nearest_actual_times_and_can_decouple(qtbot, tmp_path):
    first, second = history_result(), history_result((0., .8, 1.6, 2.4))
    dialog = MapComparisonDialog([("A", first), ("B", second)])
    qtbot.addWidget(dialog)
    dialog.time_navigations[0].select_time(1.)
    assert dialog.selected_result(0).metrics["time_s"] == 1.
    assert dialog.selected_result(1).metrics["time_s"] == .8
    assert "right: 0.8 s" in dialog.legend.text()
    assert dialog.time_navigations[0].displayed_metrics["time_s"] == 1.
    assert dialog.time_navigations[1].displayed_metrics["time_s"] == .8
    dialog.link_times.setChecked(False)
    dialog.time_navigations[0].select_time(0.)
    assert dialog.selected_result(1).metrics["time_s"] == .8
    assert "Independent times" in dialog.legend.text()
    dialog.history_range.setChecked(True)
    assert dialog.limits == (300, 448)
    dialog.time_navigations[1].select_time(0.)
    assert dialog.limits == (300, 448)
    output = tmp_path / "comparison.svg"
    dialog.export_to(output)
    metadata = json.loads(output.with_suffix(".svg.json").read_text(encoding="utf-8"))
    assert metadata["history_range"]
    assert not metadata["linked_times"]
    assert [panel["time_s"] for panel in metadata["panels"]] == [0, 0]


def test_click_history_requests_physical_time_for_nearest_field(qtbot):
    panel = TimeHistoryPanel()
    qtbot.addWidget(panel)
    panel.resize(350, 620)
    panel.set_result(history_result())
    panel.show()
    qtbot.waitUntil(lambda: panel.plot.viewport().height() > 200)
    panel.plot.setRange(xRange=(0, 2), yRange=(390, 450), padding=0)
    scene_position = panel.plot.plotItem.vb.mapViewToScene(QPointF(1.2, 410))
    position = panel.plot.mapFromScene(scene_position)
    with qtbot.waitSignal(panel.time_selected) as signal:
        qtbot.mouseClick(panel.plot.viewport(), Qt.MouseButton.LeftButton, pos=position)
    assert signal.args[0] == pytest.approx(1.2, abs=.02)
