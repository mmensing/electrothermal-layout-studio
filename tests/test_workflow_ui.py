# SPDX-License-Identifier: MPL-2.0
"""Workflow commands remain reachable without permanently occupying the canvas."""
from __future__ import annotations

import os
from copy import deepcopy

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from gui_test_helpers import solved_e1 as solved_e1
from PySide6.QtCore import Qt
from PySide6.QtTest import QTest

from etlayout.gui.main_window import MainWindow


@pytest.fixture
def window(qtbot, tmp_path, solved_e1):
    project, result = deepcopy(solved_e1)
    widget = MainWindow(project, result, database_path=tmp_path / "materials.sqlite3", recover=False)
    widget.autosave_timer.stop()
    widget.recovery_path = tmp_path / "recovery.etl"
    widget.error = lambda title, message: pytest.fail(f"{title}: {message}")
    qtbot.addWidget(widget, before_close_func=lambda w: (w.cancel_geometry(), setattr(w, "dirty", False)))
    widget.resize(1280, 800)
    widget.show()
    qtbot.waitUntil(lambda: widget.prepare_job is None, timeout=30000)
    return widget


def test_workflow_stages_share_stable_shell(window, qtbot):
    from etlayout.workflow import STAGES
    guide = window.guided
    assert guide.stage == "results"
    guide.drawer.hide()
    qtbot.wait(50)
    assert window.view.height() >= 350
    for stage in STAGES:
        QTest.mouseClick(guide.stage_buttons[stage.key], Qt.MouseButton.LeftButton)
        qtbot.wait(20)
        assert guide.stage == stage.key
        assert guide.inspector.isVisible()
        assert all(not getattr(window, name + "_dock").isVisible() for name in ("design", "config", "display", "analysis"))
        assert sum(control.isChecked() for control in guide.stage_buttons.values()) == 1
    assert window.field_layer.isVisible() and window.field_region.isVisible()
    assert window.legend_scale.color_bar.isVisible()


def test_purpose_tools_keyboard_navigation_and_range_persist(window, qtbot):
    from etlayout.workflow import STAGES
    window.legend_scale.minimum.setText("0.001")
    window.legend_scale.maximum.setText("0.1")
    window.legend_scale.lock.setChecked(True)
    for index, stage in enumerate(STAGES, 1):
        QTest.keyClick(window, getattr(Qt.Key, f"Key_{index}"), Qt.KeyboardModifier.ControlModifier)
        qtbot.waitUntil(lambda expected=stage.key: window.guided.stage == expected)
    assert window.legend_scale.lock.isChecked()
    assert window.legend_scale.minimum.text() == "0.001"
    assert window.view.field_limits == (0.001, 0.1)
    assert not window.tool_combo.view().isRowHidden(window.tool_combo.findData("profile"))
    assert window.tool_combo.view().isRowHidden(window.tool_combo.findData("terminal_rect"))
    window.guided.navigate("connections")
    assert not window.tool_combo.view().isRowHidden(window.tool_combo.findData("terminal_rect"))
    assert window.tool_combo.view().isRowHidden(window.tool_combo.findData("profile"))


def test_repeated_analysis_actions_and_settings_stay_reachable(window, qtbot):
    window.show_analysis_page(window.profiles)
    assert window.guided.canvas_tabs.currentWidget() is window.profiles
    window.guided.canvas_tabs.setCurrentIndex(0)
    window.show_analysis_page(window.profiles)
    assert window.guided.canvas_tabs.currentWidget() is window.profiles
    window.guided.drawer.hide()
    window.probe(10e-6, 10e-6)
    assert not window.guided.drawer.isVisible()
    assert window.statusBar().currentMessage()
    window.show_display_settings()
    qtbot.wait(50)
    assert window.guided.stage == "results" and window.colormap_combo.isVisible()
    assert window.guided.analysis.isVisible() and window.guided.save_button.isVisible()
    assert set(window.guided.menus) == {"Edit", "Model", "Simulation", "Results", "Studies", "View"}
