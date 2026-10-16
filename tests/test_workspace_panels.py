# SPDX-License-Identifier: MPL-2.0
"""Workspace visibility never edits the physical document or resets the camera."""
from __future__ import annotations

import json
from copy import deepcopy

import pytest
from gui_test_helpers import _click, _fill
from gui_test_helpers import guided_window as guided_window
from PySide6.QtCore import QPointF, Qt
from PySide6.QtGui import QAction
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication
from shapely.geometry import box

from etlayout.gui.viewer import geometry_region
from etlayout.gui.workspace_panels import SETTINGS_KEY, validated_panel_state
from etlayout.io.project import configuration_hash, physics_hash
from etlayout.simulation.runner import run_project


@pytest.mark.fast
@pytest.mark.parametrize("value", [None, "{", [], {}, {"version": 2},
    {"version": True, "visible": {}},
    {"version": 1, "visible": ["left", "right", "drawer"]},
    {"version": 1, "visible": {"left": True, "right": True, "drawer": "yes"}},
    {"version": 1, "visible": {"left": True, "right": True, "drawer": True},
     "horizontal": [1, -2, 3], "vertical": [2, 3]},
    {"version": 1, "visible": {"left": True, "right": True, "drawer": True},
     "horizontal": [True, 2, 3], "vertical": [2, 3]},
    {"version": 1, "visible": {"left": True, "right": True, "drawer": True},
     "horizontal": [1, 2, 3], "vertical": [2, 10001]}])
def test_corrupt_panel_preferences_rejected(value):
    assert validated_panel_state(value) is None


@pytest.mark.parametrize("name", ["left", "right", "drawer"])
def test_panels_toggle_independently_and_restore_affordances(guided_window, name):
    window = guided_window
    guided = window.guided
    guided.panels.restore_default()
    before = window.project.to_dict()
    editor = guided.editor
    action = guided.actions["view.drawer" if name == "drawer" else "view." + name + "_panel"]
    action.trigger()
    QApplication.processEvents()
    assert not guided.panels.visible(name)
    assert not action.isChecked()
    assert all(guided.panels.visible(other) for other in guided.panels.widgets if other != name)
    assert guided.left_toggle.isVisible() and guided.right_toggle.isVisible() and guided.drawer_toggle.isVisible()
    _click(window, {"left": guided.left_toggle, "right": guided.right_toggle,
                    "drawer": guided.drawer_toggle}[name])
    assert guided.panels.visible(name) and action.isChecked()
    assert window.project.to_dict() == before and guided.editor is editor


def test_panels_preserve_actual_result_and_view_state(guided_window):
    window = guided_window
    result = run_project(window.project)
    assert result.converged
    window.show_result(result)
    guided = window.guided
    window.colormap_combo.setCurrentIndex(window.colormap_combo.findData("viridis"))
    window.field_combo.setCurrentIndex(window.field_combo.findData("current_density"))
    window.legend_scale.minimum.setText("0 mA/µm²")
    window.legend_scale.maximum.setText("2 mA/µm²")
    window.legend_scale.lock.setChecked(True)
    assert window.legend_scale.validated_limits() == pytest.approx((0, 2e9))
    window.view.scale(4, 4)
    window.view.centerOn(QPointF(30, -4))
    QApplication.processEvents()
    view = window.view
    expected = (view.field_key, view.field_layer, view.field_region, view.field_colormap,
                view.field_log, view.field_limits, window.transient_frame, view.result,
                window.result, window.mesh, window.field_combo.currentData(),
                window.field_layer.currentData(), window.field_region.currentData())
    original = window.project.to_dict()
    config, physical = configuration_hash(window.project), physics_hash(window.project)
    history, revision = list(window.run_history), guided.document.revision
    transform = view.transform()
    center = view.mapToScene(view.viewport().rect().center())
    area = view.viewport().width() * view.viewport().height()
    guided.panels.maximize_canvas()
    QApplication.processEvents()
    assert view.viewport().width() * view.viewport().height() > area * 1.7
    assert view.hasFocus()
    guided.panels.maximize_canvas()
    QApplication.processEvents()
    assert view.transform() == transform
    restored = view.mapToScene(view.viewport().rect().center())
    assert abs(center.x() - restored.x()) * transform.m11() <= 2
    assert abs(center.y() - restored.y()) * transform.m22() <= 2
    assert expected == (view.field_key, view.field_layer, view.field_region, view.field_colormap,
        view.field_log, view.field_limits, window.transient_frame, view.result,
        window.result, window.mesh, window.field_combo.currentData(),
        window.field_layer.currentData(), window.field_region.currentData())
    assert window.project.to_dict() == original
    assert (configuration_hash(window.project), physics_hash(window.project)) == (config, physical)
    assert window.run_history == history and guided.document.revision == revision
    assert not window.job and not window.prepare_job


def test_panel_preferences_reopen_and_corruption_fallback(guided_window):
    window = guided_window
    panels = window.guided.panels
    panels.set_visible("left", False)
    panels.set_visible("drawer", False)
    saved = window.settings.value(SETTINGS_KEY)
    assert validated_panel_state(saved)["visible"] == {"left": False, "right": True, "drawer": False}
    panels.restore_default()
    window.settings.setValue(SETTINGS_KEY, saved)
    panels.restore_preferences()
    assert not panels.visible("left") and not panels.visible("drawer") and panels.visible("right")
    window.settings.setValue(SETTINGS_KEY, '{"version": 99}')
    panels.restore_preferences()
    assert all(panels.visible(name) for name in panels.widgets)
    assert validated_panel_state(window.settings.value(SETTINGS_KEY))


def test_hide_focused_inspector_preserves_dirty_editor_and_selection(guided_window):
    window = guided_window
    guided = window.guided
    _click(window, guided.stage_buttons["layers"])
    _fill(window, "thickness", "3.5")
    editor = guided.editor
    assert editor.has_changes()
    original, revision = deepcopy(window.project.to_dict()), guided.document.revision
    field = editor.form.widgets["thickness"]
    assert field.hasFocus()
    guided.panels.toggle("right")
    assert window.view.hasFocus() and editor is guided.editor and editor.has_changes()
    window.reset_workspace_layout()
    assert guided.inspector.isVisible() and editor is guided.editor and editor.has_changes()
    assert field.text() == "3.5"
    assert window.project.to_dict() == original and guided.document.revision == revision
    _click(window, editor.cancel_button)


def test_keyboard_focus_and_unique_shortcuts(guided_window):
    window = guided_window
    window.activateWindow()
    window.guided.panels.focus_canvas()
    QTest.keyClick(window.view, Qt.Key.Key_L, Qt.KeyboardModifier.ControlModifier | Qt.KeyboardModifier.ShiftModifier)
    QApplication.processEvents()
    assert not window.guided.panels.visible("left")
    QTest.keyClick(window.view, Qt.Key.Key_L, Qt.KeyboardModifier.ControlModifier | Qt.KeyboardModifier.ShiftModifier)
    assert window.guided.panels.visible("left")
    shortcuts = [action.shortcut().toString() for action in window.findChildren(QAction) if not action.shortcut().isEmpty()]
    assert len(shortcuts) == len(set(shortcuts))
    window.guided.panels.open_drawer(1)
    assert window.guided.drawer.currentIndex() == 1 and window.guided.drawer.isVisible()
    window.guided.panels.set_visible("drawer", False)
    window.guided.open_drawer(3)
    assert window.guided.drawer.currentIndex() == 3 and window.guided.drawer.isVisible()


def test_explicit_fit_roi_changes_only_camera(guided_window):
    window = guided_window
    window.project.display_roi = geometry_region(box(10e-6, 0, 30e-6, 10e-6))
    before = json.dumps(window.project.to_dict(), sort_keys=True)
    window.view.scale(3, 3)
    transform = window.view.transform()
    window.guided.actions["view.selection"].trigger()
    assert window.view.transform() != transform
    assert json.dumps(window.project.to_dict(), sort_keys=True) == before


def test_new_window_restores_local_preferences_without_changing_document(guided_window, qtbot, tmp_path):
    from etlayout.gui.main_window import MainWindow

    original = guided_window.project.to_dict()
    guided_window.guided.panels.set_visible("left", False)
    guided_window.guided.panels.set_visible("right", False)
    guided_window.guided.panels.set_visible("drawer", False)
    other = MainWindow(database_path=tmp_path / "second-materials.sqlite3", recover=False)
    qtbot.addWidget(other)
    other.recovery_path = tmp_path / "second-recovery.etlayout"
    other.autosave_timer.stop()
    try:
        other.show()
        QApplication.processEvents()
        assert not other.guided.panels.visible("left") and not other.guided.panels.visible("right")
        assert other.guided.left_toggle.isVisible() and other.guided.right_toggle.isVisible()
        assert other.guided.panels.maximize_action.isChecked()
        other.guided.panels.maximize_canvas()
        assert all(other.guided.panels.visible(name) for name in other.guided.panels.widgets)
        assert guided_window.project.to_dict() == original
    finally:
        other.dirty = False
        other.close()


def test_corrupt_legacy_named_workspace_does_not_crash(guided_window):
    from PySide6.QtCore import QTimer
    from PySide6.QtWidgets import QInputDialog

    window = guided_window
    window.settings.setValue("layouts/Old/guided_stage", "layout")
    for key in ("guided_panels", "guided_drawer", "geometry", "state"):
        window.settings.setValue("layouts/Old/" + key, "invalid old Qt state")
    original = window.project.to_dict()

    def choose():
        dialog = QApplication.activeModalWidget()
        assert isinstance(dialog, QInputDialog)
        dialog.accept()

    QTimer.singleShot(0, choose)
    window.restore_workspace_layout()
    assert all(window.guided.panels.visible(name) for name in window.guided.panels.widgets)
    assert window.project.to_dict() == original
    assert "corrupt workspace preference" in window.statusBar().currentMessage()
