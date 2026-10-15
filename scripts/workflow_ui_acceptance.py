# SPDX-License-Identifier: MPL-2.0
"""Capture and exercise the native seven-stage workspace at compact window sizes.

Runs against actual converged E12/E10 results; solves them if the cache is absent.
Without --offscreen it uses native Qt. Scale is QT_SCALE_FACTOR, not a physical
monitor change. Captures use QWidget.grab; no screen-reader claim is made.
"""
from __future__ import annotations

import argparse
import json
import multiprocessing
import os
import time
from pathlib import Path


def main(argv=None):
    started = time.monotonic()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=Path(".artifacts/workflow-ui"))
    parser.add_argument("--cache", type=Path, default=Path(".artifacts/example-gallery-cache"))
    parser.add_argument("--offscreen", action="store_true")
    parser.add_argument("--scale", type=float, default=1.)
    args = parser.parse_args(argv)
    os.environ["QT_SCALE_FACTOR"] = str(args.scale)
    if args.offscreen:
        os.environ["QT_QPA_PLATFORM"] = "offscreen"
    else:
        os.environ.pop("QT_QPA_PLATFORM", None)
    from desktop_acceptance_support import prepare_desktop_fonts
    from PySide6.QtCore import QSettings, Qt
    from PySide6.QtTest import QTest
    from PySide6.QtWidgets import QApplication

    from etlayout.examples import make_example
    from etlayout.gui.main_window import MainWindow
    from etlayout.io.project import load_project, save_project
    from etlayout.simulation.runner import run_project

    output = args.output.resolve() / f"{'offscreen' if args.offscreen else 'native'}-{args.scale:g}x"
    output.mkdir(parents=True, exist_ok=True)
    QSettings.setDefaultFormat(QSettings.Format.IniFormat)
    QSettings.setPath(QSettings.Format.IniFormat, QSettings.Scope.UserScope, str(output / "settings"))
    settings = QSettings("etlayout", "Electrothermal Layout Studio")
    settings.setFallbacksEnabled(False)
    settings.clear()
    app = QApplication([])
    app.setStyle("Fusion")
    prepare_desktop_fonts(app, output)
    receipt = {"qpa": app.platformName(), "scale": args.scale, "requested_size": [1280, 800],
               "available_screen_logical": [app.primaryScreen().availableGeometry().width(), app.primaryScreen().availableGeometry().height()],
               "capture": "QWidget.grab of the live source application; Qt widget signals and QTest keyboard events",
               "limits": "No installed-package, screen-reader, physical DPI or multiple-monitor claims.",
               "benchmark": {"performed_by": "Automated Qt/QTest script", "keyboard_stage_transitions": 4,
                   "modal_dialogs": 0, "wrong_target_corrections": None, "help_required": None,
                   "note": "Known scripted targets; no human usability or task-time improvement claim."},
               "checkpoints": []}
    window = None

    def size_for_capture():
        screen = app.primaryScreen().availableGeometry()
        if screen.width() < 1280 or screen.height() < 800:
            # The benchmark is a client-area render at a documented logical
            # size, not proof that the whole scaled window fits this monitor.
            window.setMinimumSize(1280, 800)
            receipt["capture_exceeds_available_screen"] = True
        window.resize(1280, 800)

    def archived(name):
        path = args.cache / (name + ".etl")
        if path.is_file():
            return load_project(path)
        project = make_example(name, output / "inputs")
        project.solver.mode = "electrothermal"
        result = run_project(project)
        assert result.converged
        path.parent.mkdir(parents=True, exist_ok=True)
        save_project(project, path, result)
        return project, result

    def idle():
        start = time.monotonic()
        while window.prepare_job or window.job or window.hierarchy_jobs:
            assert time.monotonic() - start < 150, "Worker timeout"
            QTest.qWait(40)
        assert window.geometry is not None, (
            f"{window.workflow_hint.text()}\n{window.console.toPlainText()}"
        )
        QTest.qWait(150)

    def select(combo, data):
        index = combo.findData(data)
        assert index >= 0, data
        combo.setCurrentIndex(index)
        QTest.qWait(40)

    def shortcut(key, modifiers):
        # Other independently running native acceptance windows may take focus.
        # Exercise the real shortcut only after this benchmark is active again.
        window.raise_()
        window.activateWindow()
        window.windowHandle().requestActivate()
        assert QTest.qWaitForWindowActive(window, 1500), "The benchmark window could not acquire keyboard focus"
        QTest.qWait(60)
        QTest.keyClick(window, key, modifiers)
        QTest.qWait(80)

    def capture(name):
        QTest.qWait(150)
        assert window.width() == 1280 and window.height() == 800, window.size()
        picture = window.grab()
        assert picture.save(str(output / f"{name}.png"))
        receipt["checkpoints"].append({"name": name, "file": name + ".png",
            "logical_size": [window.width(), window.height()], "pixel_size": [picture.width(), picture.height()],
            "dpr": window.devicePixelRatioF(), "workflow": window.guided.stage,
            "map_size": [window.view.width(), window.view.height()],
            "canvas_page": window.guided.canvas_tabs.tabText(window.guided.canvas_tabs.currentIndex()),
            "drawer_visible": window.guided.drawer.isVisible(),
            "layer": window.field_layer.currentData(), "field": window.field_combo.currentData(),
            "configuration_hash": window.result.configuration_hash if window.result else ""})
        (output / "observations.json").write_text(json.dumps(receipt, indent=2), encoding="utf-8")
        print(name, window.view.size(), flush=True)

    try:
        project, result = archived("E12_via_ladder")
        window = MainWindow(project, result, database_path=output / "materials.sqlite3", recover=False)
        window.recovery_path = output / "recovery.etl"
        window.autosave_timer.stop()
        size_for_capture()
        window.apply_theme(False)
        window.show()
        # Native Qt may initially clamp the window to the screen's available
        # logical height at increased scaling; test the requested client area.
        size_for_capture()
        window.activateWindow()
        idle()
        # Windows can deliver its initial available-screen clamp after show();
        # request the benchmark client area once that native event has settled.
        size_for_capture()
        QTest.qWait(150)
        select(window.field_layer, "m2")
        select(window.field_combo, "current_density")
        window.fit_result_layer()
        capture("02-inspect-after")
        assert window.view.height() >= 350, window.view.size()
        for index, (number, stage, label) in enumerate([(1, "layout", "layout"), (2, "layers", "setup"), (6, "run", "solve")]):
            shortcut(getattr(Qt.Key, f"Key_{number}"), Qt.KeyboardModifier.ControlModifier)
            assert window.guided.stage == stage
            assert window.view.result is None, "Setup stages must display model geometry by default"
            capture(f"0{index + 3}-{label}")
        shortcut(Qt.Key.Key_7, Qt.KeyboardModifier.ControlModifier)
        assert window.guided.stage == "results"
        window.show_display_settings()
        select(window.colormap_combo, "cividis")
        window.legend_scale.minimum.setText("0.2")  # mA/µm²
        window.legend_scale.maximum.setText("8")
        window.legend_scale.lock.setChecked(True)
        window.log_scale.setChecked(True)
        assert window.view.field_limits == (2e8, 8e9)
        shortcut(Qt.Key.Key_C, Qt.KeyboardModifier.AltModifier)
        assert window.colormap_combo.hasFocus()
        window.fit_result_layer()
        capture("06-appearance-range")
        window.on_line_drawn("profile", [[184e-6, 15e-6], [184e-6, 130e-6]])
        assert window.guided.canvas_tabs.currentWidget() is window.profiles
        window.guided.canvas_tabs.setCurrentIndex(0)
        window.show_analysis_page(window.profiles)
        assert window.guided.canvas_tabs.currentWidget() is window.profiles
        window.fit_result_layer()
        capture("07-profile-drawer")
        window.guided.canvas_tabs.setCurrentIndex(0)
        window.guided.drawer.hide()
        window.show_analysis_page(window.history_panel)
        assert window.guided.drawer.isVisible() and window.guided.drawer.currentWidget() is window.history_panel
        capture("08-run-history")
        window.dirty = False
        window.close()
        window = None
        project, result = archived("E10_thermal_stack")
        window = MainWindow(project, result, database_path=output / "materials.sqlite3", recover=False)
        window.recovery_path = output / "recovery2.etl"
        window.autosave_timer.stop()
        size_for_capture()
        window.apply_theme(False)
        window.show()
        size_for_capture()
        idle()
        size_for_capture()
        QTest.qWait(150)
        select(window.field_combo, "temperature")
        select(window.colormap_combo, "magma")
        window.smooth_field.setChecked(True)
        window.fit_result_layer()
        capture("09-thermal-stack")
        receipt["completed"] = True
        print("Workflow acceptance passed", flush=True)
    finally:
        if window:
            window.cancel_geometry()
            window.dirty = False
            window.close()
        app.processEvents()
        receipt["automated_elapsed_seconds"] = time.monotonic() - started
        (output / "observations.json").write_text(json.dumps(receipt, indent=2), encoding="utf-8")


if __name__ == "__main__":
    multiprocessing.freeze_support()
    main()
