# SPDX-License-Identifier: MPL-2.0
"""Real Qt workflows, forms, interpolation, exports and Windows spawn jobs."""
from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from copy import deepcopy

import numpy as np
import pytest
from PySide6.QtCore import QPoint, Qt
from PySide6.QtTest import QTest
from shapely.geometry import Polygon

from etlayout.examples import make_example
from etlayout.gui.export import export_html_report, export_view
from etlayout.gui.forms import ModelDialog, RegionDialog, region_summary
from etlayout.gui.main_window import MainWindow
from etlayout.models import PhysicalLayer, RegionSpec
from etlayout.simulation.runner import run_project
from etlayout.visualization.sampling import FieldSampler, cross_section_intervals


@pytest.fixture
def project(tmp_path):
    return make_example("E1", tmp_path / "examples")


@pytest.fixture
def window(qtbot, project, tmp_path):
    window = MainWindow(project, database_path=tmp_path / "materials.sqlite3", recover=False)
    window.recovery_path = tmp_path / "recovery.etlayout"
    window.errors = []
    window.error = lambda title, error: window.errors.append((title, str(error)))
    window.show()
    def clean_close(widget):
        if widget.job:
            widget.job.cancel()
        widget.dirty = False
    qtbot.addWidget(window, before_close_func=clean_close)
    qtbot.waitUntil(lambda: window.prepare_job is None, timeout=30000)
    assert window.geometry is not None
    yield window
    if window.job:
        window.job.cancel()
    window.dirty = False


def test_typed_physical_layer_form_converts_micrometers(qtbot):
    layer = PhysicalLayer("metal", "Metal", "copper", thickness=2e-6)
    dialog = ModelDialog(layer, choices={"material_id": ["copper", "gold"]})
    qtbot.addWidget(dialog)
    dialog.widgets["thickness"].setText("3.5")
    dialog.widgets["z"].setText("12")
    dialog.widgets["material_id"].setCurrentText("gold")
    dialog.accept()
    assert dialog.model.thickness == pytest.approx(3.5e-6)
    assert dialog.model.z == pytest.approx(12e-6)
    assert layer.thickness == 2e-6
    assert dialog.model.material_id == "gold"


def test_region_editor_roundtrip_holes_and_masks(qtbot):
    spec = RegionSpec(sources=["2/0", "1/0"], operation="difference", exclude_sources=["3/0"],
                      polygons=[{"exterior": [[0, 0], [10e-6, 0], [10e-6, 10e-6], [0, 10e-6]],
                                 "holes": [[[2e-6, 2e-6], [3e-6, 2e-6], [3e-6, 3e-6]]]}])
    dialog = RegionDialog(spec, ["1/0", "2/0", "3/0"])
    qtbot.addWidget(dialog)
    dialog.shape.setCurrentIndex(1)
    dialog.accept()
    assert dialog.region.sources == spec.sources
    assert dialog.region.operation == "difference"
    assert dialog.region.exclude_sources == ["3/0"]
    assert "(2/0 difference 1/0) minus (3/0)" in region_summary(dialog.region)
    assert dialog.region.polygons[0]["holes"] == spec.polygons[0]["holes"]
    reopened = RegionDialog(dialog.region, ["1/0", "2/0", "3/0"])
    qtbot.addWidget(reopened)
    reopened.mask_tabs.setCurrentIndex(1)
    excluded = next(reopened.exclusions.item(index) for index in range(reopened.exclusions.count())
                    if reopened.exclusions.item(index).text() == "1/0")
    excluded.setCheckState(Qt.CheckState.Checked)
    reopened.accept()
    assert set(reopened.region.exclude_sources) == {"1/0", "3/0"}
    assert dialog.region.exclude_sources == ["3/0"]  # reopening edits a draft


def test_window_geometry_drawing_undo_and_persistence(window, qtbot, tmp_path):
    assert window.geometry is not None
    assert window.view.layer_items
    original = deepcopy(window.project)
    # Actual viewport drag emits the same ROI region used by the GUI controller.
    window.set_tool("roi_rect")
    viewport = window.view.viewport()
    start = QPoint(viewport.width() // 3, viewport.height() // 3)
    end = QPoint(viewport.width() * 2 // 3, viewport.height() * 2 // 3)
    QTest.mousePress(viewport, Qt.MouseButton.LeftButton, pos=start)
    QTest.mouseMove(viewport, end)
    QTest.mouseRelease(viewport, Qt.MouseButton.LeftButton, pos=end)
    assert window.project.roi is None
    assert window.guided.pending_region is not None
    QTest.mouseClick(window.guided.use_selection, Qt.MouseButton.LeftButton)
    assert window.project.roi is not None
    assert window.undo_history
    window.undo()
    assert window.project.roi is None
    window.redo()
    assert window.project.roi is not None
    window.undo()
    window.path = tmp_path / "gui-project.etlayout"
    assert window.save()
    qtbot.waitUntil(lambda: window.save_job is None and not window._save_queue, timeout=30000)
    window.autosave_timer.stop()
    assert window.path.exists()
    window.open_project(window.path)
    assert window.project.layers[0].id == original.layers[0].id


def test_gui_real_fields_profiles_crosssection_and_export(window, qtbot, tmp_path):
    result = run_project(window.project)
    assert result.converged
    window.show_result(result)
    assert window.field_combo.count() > 1
    window.field_combo.setCurrentIndex(window.field_combo.findData("potential"))
    assert "V" in window.legend.text()
    assert window.view.overlay_item is not None
    layer = window.project.layers[0].id
    bounds = window.geometry.layers[layer].bounds
    y = (bounds[1] + bounds[3]) / 2
    cut = [[bounds[0], y], [bounds[2], y]]
    window.on_line_drawn("profile", cut)
    assert len(window.profiles.profiles) == 1
    assert np.isfinite(window.profiles.profiles[0]["values"]).sum() > 390
    window.on_line_drawn("cross_section", cut)
    assert layer in window.cross_section.intervals
    window.probe((bounds[0] + bounds[2]) / 2, y)
    assert "Potential" in window.console.toPlainText()
    export_view(window.view, tmp_path / "map.svg", window.legend.text())
    export_view(window.view, tmp_path / "map.png", window.legend.text())
    export_html_report(window.project, result, window.view, tmp_path / "report.html", window.legend.text())
    assert (tmp_path / "map.svg").stat().st_size > 1000
    assert "data:image/png;base64," in (tmp_path / "report.html").read_text(encoding="utf-8")
    assert not window.errors


def test_field_sampler_does_not_bridge_holes_and_is_linear(project):
    result = run_project(project)
    layer = project.layers[0].id
    sampler = FieldSampler(result)
    points = np.asarray([[50e-6, 5e-6], [50e-6, 30e-6]])
    values = sampler.sample(layer, "potential", points)
    assert np.isfinite(values[0])
    assert np.isnan(values[1])
    polygon = Polygon([(0, 0), (10, 0), (10, 10), (0, 10)], [[(4, 4), (6, 4), (6, 6), (4, 6)]])
    intervals = cross_section_intervals(polygon, [0, 5], [10, 5])
    assert intervals == [(0., 4.), (6., 10.)]


def test_spawn_worker_completes_without_freezing_gui(window, qtbot):
    window.start_job("solve")
    assert window.job is not None
    assert window.cancel_action.isEnabled()
    qtbot.waitUntil(lambda: window.job is None, timeout=90000)
    assert not window.errors
    assert window.result is not None and window.result.converged
    assert window.run_action.isEnabled()
    assert not window.cancel_action.isEnabled()


def test_cancel_worker_returns_control(window, qtbot):
    window.start_job("solve")
    window.cancel_job()
    assert window.job is None
    assert window.run_action.isEnabled()
    assert "cancelled" in window.console.toPlainText()


def test_electrical_to_thermal_preserves_explicit_joule_option(window):
    window.show_result(run_project(window.project))
    window.guided.analysis.setCurrentIndex(window.guided.analysis.findData("thermal"))
    assert not window.project.solver.thermal_include_joule
    window.guided.navigate("conditions")
    assert window.thermal_joule.isVisible()
    window.thermal_joule.setChecked(True)
    assert window.project.solver.thermal_include_joule
    assert window.thermal_joule.isChecked()


def test_theme_toggle_and_nondefault_thermal_reference(window):
    from etlayout.visualization.sampling import FIELD_LABELS, scalar_field
    window.apply_theme(True)
    window.apply_theme(False)
    result = run_project(window.project)
    layer = window.project.layers[0].id
    result.fields[layer]["temperature"] = np.full(len(result.mesh.points), 360.)
    result.metrics["thermal_reference"] = 350.
    result.configuration["solver"]["initial_temperature"] = 300.
    assert np.all(scalar_field(result, layer, "temperature_rise") == 10.)
    assert "tensor norm" in FIELD_LABELS["sheet_conductance"][0]


def test_terminal_edge_tool_snaps_to_boundary(window):
    from PySide6.QtCore import QPointF
    coordinate = window.view.snap_boundary(QPointF(.01, -5.))
    assert coordinate.x() == pytest.approx(0., abs=1e-9)
    assert coordinate.y() == pytest.approx(-5.)


def test_side_by_side_maps_use_actual_different_geometries_and_shared_scale(qtbot, project, tmp_path):
    from etlayout.gui.comparison import MapComparisonDialog
    from etlayout.visualization.sampling import scalar_field
    first = run_project(project)
    bent = make_example("E2", tmp_path / "bent")
    bent.layers[0].id = "other_metal"
    for terminal in bent.terminals:
        terminal.layer_id = "other_metal"
    for boundary in bent.thermal_boundaries:
        boundary.layer_id = "other_metal"
    second = run_project(bent)
    dialog = MapComparisonDialog([("Straight metal", first), ("Bent / tapered metal", second)])
    qtbot.addWidget(dialog)
    dialog.show()
    qtbot.wait(30)
    assert dialog.layers[0].currentData() == "metal"
    assert dialog.layers[1].currentData() == "other_metal"
    dialog.field.setCurrentIndex(dialog.field.findData("potential"))
    expected = np.concatenate((scalar_field(first, "metal", "potential"), scalar_field(second, "other_metal", "potential")))
    assert dialog.limits == pytest.approx((np.nanmin(expected), np.nanmax(expected)))
    assert dialog.views[0].overlay_item is not None
    assert dialog.views[1].overlay_item is not None
    assert "µm" in dialog.bounds_labels[0].text()
    dialog.export_to(tmp_path / "two-maps.svg")
    dialog.export_to(tmp_path / "two-maps.png")
    assert (tmp_path / "two-maps.svg").stat().st_size > 5000
    assert (tmp_path / "two-maps.png").stat().st_size > 1000


def test_mesh_study_dialog_sends_selected_real_profiles(window, qtbot):
    from PySide6.QtCore import QTimer
    from PySide6.QtWidgets import QApplication, QDialogButtonBox, QLineEdit
    window.show_result(run_project(window.project))
    window.field_combo.setCurrentIndex(window.field_combo.findData("potential"))
    window.on_line_drawn("profile", [[1e-6, 5e-6], [99e-6, 5e-6]])
    def accept_dialog():
        dialog = QApplication.activeModalWidget()
        dialog.findChild(QLineEdit, "mesh_study_sizes").setText("10, 5")
        dialog.findChild(QDialogButtonBox).button(QDialogButtonBox.StandardButton.Ok).click()
    QTimer.singleShot(50, accept_dialog)
    window.run_convergence_dialog()
    qtbot.waitUntil(lambda: window.job is None, timeout=90000)
    assert not window.errors
    assert len(window.studies) == 2
    assert all(row.result and row.result.converged for row in window.studies)
    refined = window.studies[-1].result
    assert len(refined.metrics["mesh_profiles"]) == 1
    assert refined.metrics["profile_0_common_samples"] >= 399
    assert refined.metrics["profile_0_relative_l2_change"] < .01
    assert len(window.profiles.profiles) == 3
