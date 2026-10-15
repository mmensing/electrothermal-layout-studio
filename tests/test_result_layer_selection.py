# SPDX-License-Identifier: MPL-2.0
"""Real multilayer result maps retain quantity, support and view-only state."""
from __future__ import annotations

import os
from copy import deepcopy

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import numpy as np
import pytest
from PySide6.QtCore import QPoint, QRect, Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QPushButton, QTabWidget

from etlayout.examples import make_example
from etlayout.gui.main_window import MainWindow
from etlayout.io.project import physics_hash
from etlayout.simulation.runner import run_project
from etlayout.visualization.sampling import scalar_field


@pytest.fixture(scope="module")
def solved_ladder(tmp_path_factory):
    project = make_example("E12", tmp_path_factory.mktemp("result-layers") / "examples")
    project.solver.mode = "electrothermal"
    result = run_project(project)
    assert result.converged
    return project, result


@pytest.fixture
def mapped(qtbot, tmp_path, solved_ladder):
    project, result = deepcopy(solved_ladder)
    window = MainWindow(project, result, database_path=tmp_path / "materials.sqlite3", recover=False)
    window.recovery_path = tmp_path / "recovery.etlayout"
    window.autosave_timer.stop()
    window.errors = []
    window.error = lambda title, message: window.errors.append((title, str(message)))

    def close(widget):
        widget.cancel_job()
        widget.cancel_geometry()
        if widget.inspection_context:
            widget.inspection_context["dirty"] = False
        widget.dirty = False

    qtbot.addWidget(window, before_close_func=close)
    window.show()
    qtbot.waitUntil(lambda: window.prepare_job is None and not window.hierarchy_jobs, timeout=30000)
    assert window.geometry is not None
    assert not window.errors
    yield window


def select(combo, value):
    index = combo.findData(value)
    assert index >= 0, value
    combo.setCurrentIndex(index)
    assert combo.currentData() == value


def assert_map(window, layer, field):
    assert window.view.overlay_item is not None
    assert window.view.result is window.result
    assert (window.view.field_layer, window.view.field_key) == (layer, field)
    assert window.legend_scale.details["layer"] == layer
    assert window.legend_scale.details["field"] == field
    values = scalar_field(window.result, layer, field)
    indices = window.result.mesh.elements(layer)
    if field in {"potential", "temperature", "temperature_rise"}:
        values = values[np.unique(window.result.mesh.triangles[indices])]
    else:
        values = values[indices]
    assert window.legend_scale.details["low"] == pytest.approx(np.nanmin(values))
    assert window.legend_scale.details["high"] == pytest.approx(np.nanmax(values))
    assert window.legend.text().startswith(layer + " · ")


def test_density_layer_changes_use_each_layers_fields_without_editing_project(mapped):
    before = physics_hash(mapped.project)
    result = mapped.result
    select(mapped.field_combo, "current_density")
    ranges = []
    for layer in ("m1", "m2", "m3"):
        select(mapped.field_layer, layer)
        assert mapped.field_combo.currentData() == "current_density"
        assert_map(mapped, layer, "current_density")
        ranges.append(mapped.legend_scale.details["high"])
    assert len(set(ranges)) > 1  # Actual layer data, rather than a relabelled first map.
    assert mapped.result is result
    assert physics_hash(mapped.project) == before
    assert not mapped.dirty
    assert not mapped.errors


def test_unavailable_density_preserves_selection_and_rejects_profile_then_recovers(mapped):
    select(mapped.field_layer, "m1")
    select(mapped.field_combo, "current_density")
    select(mapped.field_layer, "substrate")
    assert mapped.field_combo.currentData() == "current_density"
    assert not mapped.field_combo.model().item(mapped.field_combo.currentIndex()).isEnabled()
    assert "unavailable" in mapped.field_hint.text()
    assert "substrate" in mapped.legend.text()
    assert mapped.view.overlay_item is None
    assert not mapped.fit_layer_button.isEnabled()
    profiles = len(mapped.profiles.profiles)
    mapped.on_line_drawn("profile", [[0., 16e-6], [180e-6, 16e-6]])
    assert len(mapped.profiles.profiles) == profiles
    assert mapped.errors[-1][0] == "No field on this layer"
    select(mapped.field_layer, "m1")
    assert_map(mapped, "m1", "current_density")
    select(mapped.field_layer, "substrate")
    select(mapped.field_combo, "temperature")
    assert_map(mapped, "substrate", "temperature")
    assert mapped.fit_layer_button.isEnabled()
    assert not mapped.dirty


def test_explicit_geometry_view_survives_refresh_and_layer_changes(mapped):
    select(mapped.field_combo, "")
    assert mapped.view.overlay_item is None
    mapped.refresh_project(preserve_view=True, refresh_geometry=False)
    assert mapped.field_combo.currentData() == ""
    select(mapped.field_layer, "m3")
    assert mapped.field_combo.currentData() == ""
    assert mapped.view.overlay_item is None
    assert "Layout geometry only" in mapped.field_hint.text()
    assert not mapped.fit_layer_button.isEnabled()
    assert not mapped.isolate_layer.isEnabled()
    assert all(item.isVisible() for items in mapped.view.layer_items.values() for item in items)
    assert not mapped.dirty


def test_isolation_hides_other_geometry_and_terminals_then_restores_context(mapped):
    flags = {layer.id: layer.visible for layer in mapped.project.layers}
    select(mapped.field_layer, "m1")
    select(mapped.field_combo, "current_density")
    assert mapped.isolate_layer.isChecked()
    for layer, items in mapped.view.layer_items.items():
        assert all(item.isVisible() == (layer == "m1") for item in items)
    assert all(item.isVisible() for item in mapped.view.terminal_items["source"])
    assert all(not item.isVisible() for item in mapped.view.terminal_items["drain"])
    mapped.isolate_layer.setChecked(False)
    assert all(item.isVisible() for items in mapped.view.layer_items.values() for item in items)
    assert all(item.isVisible() for items in mapped.view.terminal_items.values() for item in items)
    assert {layer.id: layer.visible for layer in mapped.project.layers} == flags
    assert not mapped.dirty


def test_physical_layer_tree_click_selects_the_same_density_quantity(mapped, qtbot):
    select(mapped.field_combo, "current_density")
    QTest.mouseClick(mapped.guided.stage_buttons["layout"], Qt.MouseButton.LeftButton)
    browse = next(control for control in mapped.guided.page.findChildren(QPushButton)
                  if control.text() == "Browse hierarchy / instances")
    mapped.guided.page_scroll.ensureWidgetVisible(browse)
    QTest.mouseClick(browse, Qt.MouseButton.LeftButton)
    parent = mapped.layer_tree.parentWidget()
    tabs = next(tabs for tabs in mapped.guided.canvas_tabs.findChildren(QTabWidget) if tabs.indexOf(parent) >= 0)
    tabs.setCurrentWidget(parent)
    qtbot.waitUntil(mapped.layer_tree.isVisible)
    item = next(mapped.layer_tree.topLevelItem(i) for i in range(mapped.layer_tree.topLevelItemCount())
                if mapped.layer_tree.topLevelItem(i).data(0, Qt.ItemDataRole.UserRole) == "m3")
    mapped.layer_tree.scrollToItem(item)
    qtbot.wait(50)  # Let the newly shown tab lay out and scroll before locating the row.
    visible_row = mapped.layer_tree.visualItemRect(item).intersected(mapped.layer_tree.viewport().rect())
    assert not visible_row.isEmpty()
    target = QPoint(visible_row.left() + min(50, visible_row.width() // 2), visible_row.center().y())
    assert mapped.layer_tree.itemAt(target) is item
    QTest.mouseClick(mapped.layer_tree.viewport(), Qt.MouseButton.LeftButton,
                     pos=target)
    assert mapped.field_layer.currentData() == "m3"
    assert mapped.field_combo.currentData() == "current_density"
    # Setup browsing keeps geometry clear; Results renders the selected layer
    # with the retained quantity and the same authoritative run.
    assert mapped.guided.stage == "layout" and mapped.view.overlay_item is None
    QTest.mouseClick(mapped.guided.stage_buttons["results"], Qt.MouseButton.LeftButton)
    assert_map(mapped, "m3", "current_density")
    assert not mapped.dirty


def test_locked_range_is_shared_between_compatible_layers_but_resets_when_unavailable(mapped):
    select(mapped.field_layer, "m1")
    select(mapped.field_combo, "current_density")
    mapped.legend_scale.minimum.setText("0")
    mapped.legend_scale.maximum.setText("20")  # mA/µm²; physical limit remains 2e10 A/m².
    mapped.legend_scale.lock.setChecked(True)
    select(mapped.field_layer, "m2")
    assert mapped.legend_scale.lock.isChecked()
    assert mapped.legend_scale.details["low"] == 0.
    assert mapped.legend_scale.details["high"] == 2e10
    select(mapped.field_layer, "substrate")
    assert not mapped.legend_scale.lock.isChecked()
    assert mapped.legend_scale.details is None
    select(mapped.field_layer, "m3")
    assert_map(mapped, "m3", "current_density")


def test_source_mask_context_is_visible_and_does_not_change_physical_result_layer(mapped):
    before = physics_hash(mapped.project)
    select(mapped.field_layer, "m2")
    select(mapped.field_combo, "current_density")
    mapped.mask_toggle.setChecked(True)
    assert not mapped.isolate_layer.isEnabled()
    assert set(mapped.view.layer_items) == set(mapped.masks)
    assert all(item.isVisible() for items in mapped.view.layer_items.values() for item in items)
    assert "Source masks show geometry only" in mapped.field_hint.text()
    mapped.layer_tree.itemClicked.emit(mapped.layer_tree.topLevelItem(0), 0)
    assert mapped.field_layer.currentData() == "m2"
    assert_map(mapped, "m2", "current_density")
    mapped.mask_toggle.setChecked(False)
    assert mapped.isolate_layer.isEnabled()
    assert all(not item.isVisible() for item in mapped.view.layer_items["m1"])
    assert physics_hash(mapped.project) == before
    assert not mapped.dirty


def test_result_controls_remain_visible_after_compact_resize_and_fit_uses_active_layer_bounds(mapped, qtbot):
    # Reflow must recover after both wide and compact views, without relying on
    # another inspector tab's minimum width to enlarge the window.
    for width, height in ((1586, 992), (1280, 800), (1586, 992), (1280, 800)):
        mapped.resize(width, height)
        mapped.guided.page_scroll.verticalScrollBar().setValue(0)
        qtbot.wait(50)
        viewport = mapped.guided.page_scroll.viewport()
        for control in (mapped.field_layer, mapped.field_combo, mapped.field_region,
                        mapped.fit_layer_button, mapped.isolate_layer):
            assert control.isVisibleTo(mapped)
            bounds = QRect(control.mapTo(mapped, QPoint(0, 0)), control.size())
            assert mapped.rect().contains(bounds)
            assert mapped.result_controls.rect().contains(QRect(control.mapTo(mapped.result_controls, QPoint(0, 0)),
                                                              control.size()))
            assert viewport.rect().contains(QRect(control.mapTo(viewport, QPoint(0, 0)), control.size()))
        for control in (mapped.colormap_combo, mapped.smooth_field, mapped.legend_scale.lock,
                        mapped.legend_scale.auto_button, mapped.legend_scale.minimum, mapped.legend_scale.maximum):
            mapped.guided.page_scroll.ensureWidgetVisible(control)
            qtbot.wait(10)
            assert control.isVisibleTo(mapped)
            assert control.height() >= control.sizeHint().height(), control.accessibleName()
            assert viewport.rect().contains(QRect(control.mapTo(viewport, QPoint(0, 0)), control.size()))
            appearance = mapped.display_tabs.widget(0)
            assert appearance.rect().contains(QRect(control.mapTo(appearance, QPoint(0, 0)), control.size()))
    select(mapped.field_layer, "m2")
    select(mapped.field_combo, "current_density")
    bounds = mapped.view.overlay_item.sceneBoundingRect()
    assert (bounds.left(), bounds.top(), bounds.right(), bounds.bottom()) == pytest.approx((168., -144., 200., 0.))
    before = physics_hash(mapped.project)
    QTest.mouseClick(mapped.fit_layer_button, Qt.MouseButton.LeftButton)
    visible_scene = mapped.view.mapToScene(mapped.view.viewport().rect()).boundingRect()
    assert visible_scene.contains(bounds)
    assert mapped.view.transform().m11() > 0
    assert physics_hash(mapped.project) == before
    assert not mapped.dirty
