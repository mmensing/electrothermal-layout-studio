# SPDX-License-Identifier: MPL-2.0
"""Exercise real Qt visualization controls and record fresh source-app evidence.

Use --offscreen for headless development. Without it, Qt's native Windows QPA is
used. --scale sets QT_SCALE_FACTOR and records the actual device pixel ratio;
this does not claim a physical monitor/DPI change or screen-reader verification.
"""
from __future__ import annotations

import argparse
import json
import multiprocessing
import os
import time
from copy import deepcopy
from pathlib import Path


def main(argv=None):
    multiprocessing.freeze_support()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=Path(".artifacts/visualization-workbench"))
    parser.add_argument("--cache", type=Path, default=Path(".artifacts/example-gallery-cache"))
    parser.add_argument("--offscreen", action="store_true")
    parser.add_argument("--scale", type=float, default=1.)
    args = parser.parse_args(argv)
    if args.scale <= 0:
        parser.error("--scale must be positive")
    os.environ["QT_SCALE_FACTOR"] = str(args.scale)
    if args.offscreen:
        os.environ["QT_QPA_PLATFORM"] = "offscreen"
    else:
        os.environ.pop("QT_QPA_PLATFORM", None)

    import numpy as np
    from PySide6.QtCore import QPoint, QPointF, QSettings, Qt
    from PySide6.QtGui import QFont, QFontDatabase, QWheelEvent
    from PySide6.QtTest import QTest
    from PySide6.QtWidgets import QApplication, QDockWidget, QLabel, QMessageBox

    from etlayout.examples import make_example
    from etlayout.gui.comparison import MapComparisonDialog
    from etlayout.gui.export import export_html_report, export_view
    from etlayout.gui.main_window import MainWindow
    from etlayout.io.export import export_field_plot, export_results
    from etlayout.io.project import load_project, save_project
    from etlayout.simulation.runner import run_project
    from etlayout.visualization.support import field_support

    output = args.output.resolve() / (f"{'offscreen' if args.offscreen else 'native'}-{args.scale:g}x")
    output.mkdir(parents=True, exist_ok=True)
    QSettings.setDefaultFormat(QSettings.Format.IniFormat)
    QSettings.setPath(QSettings.Format.IniFormat, QSettings.Scope.UserScope, str(output / "settings"))
    isolated_settings = QSettings("etlayout", "Electrothermal Layout Studio")
    isolated_settings.setFallbacksEnabled(False)
    isolated_settings.clear()
    isolated_settings.sync()
    app = QApplication(["visualization-workbench-acceptance"])
    app.setStyle("Fusion")
    if args.offscreen:
        # Explicit font loading repairs the offscreen platform's font discovery;
        # native runs use the actual native font database unmodified.
        for filename in ("segoeui.ttf", "segoeuib.ttf", "segoeuisl.ttf"):
            font = Path(os.environ.get("WINDIR", "C:/Windows")) / "Fonts" / filename
            if font.is_file():
                QFontDatabase.addApplicationFont(str(font))
    app.setFont(QFont("Segoe UI", 9))
    window = comparison = None
    receipt = {"qpa": app.platformName(), "requested_scale": args.scale,
               "requested_wide_size": [1560, 980], "requested_compact_size": [1280, 800],
               "screens": [{"name": screen.name(), "device_pixel_ratio": screen.devicePixelRatio(),
                            "available_logical_size": [screen.availableGeometry().width(), screen.availableGeometry().height()],
                            "logical_dpi": screen.logicalDotsPerInch()} for screen in app.screens()],
               "capture_method": "Fresh QWidget.grab of actual source application; QTest mouse/wheel/shortcut events and live widget selection signals",
               "bounds": "No screen-reader, installed binary, native chrome, multiple-monitor or physical monitor-DPI claims.",
               "checkpoints": [], "findings": [], "errors": []}

    def save_receipt():
        (output / "observations.json").write_text(json.dumps(receipt, indent=2, ensure_ascii=False), encoding="utf-8")

    def select(combo, value):
        index = combo.findData(value)
        assert index >= 0, (value, [combo.itemData(i) for i in range(combo.count())])
        combo.setCurrentIndex(index)
        QTest.qWait(80)
        assert combo.currentData() == value

    def archive(name):
        path = args.cache.resolve() / (name + ".etl")
        if path.is_file():
            project, result = load_project(path)
            if result and result.converged and project.solver.mode == "electrothermal":
                return path, project, result
        project = make_example(name, args.output.resolve() / "inputs")
        project.solver.mode = "electrothermal"
        result = run_project(project)
        assert result.converged, name
        path.parent.mkdir(parents=True, exist_ok=True)
        save_project(project, path, result)
        return path, project, result

    def click(widget):
        QTest.mouseClick(widget, Qt.MouseButton.LeftButton, pos=widget.rect().center())
        QTest.qWait(80)

    def wait_idle(timeout=150):
        started = time.monotonic()
        while window.job or window.prepare_job or window.hierarchy_jobs:
            if time.monotonic() - started > timeout:
                raise TimeoutError("Application workers did not finish")
            modal = QApplication.activeModalWidget()
            if isinstance(modal, QMessageBox):
                message = modal.text()
                modal.reject()
                raise RuntimeError(message)
            QTest.qWait(35)
        assert window.geometry is not None, (
            f"{window.workflow_hint.text()}\n{window.console.toPlainText()}"
        )
        QTest.qWait(250)

    def record(name, widget=None, **details):
        widget = widget or window
        QTest.qWait(300)
        image = widget.grab()
        assert image.save(str(output / f"{name}.png"))
        state = {"name": name, "file": name + ".png", "window_size": [widget.width(), widget.height()],
                 "pixel_size": [image.width(), image.height()], "device_pixel_ratio": widget.devicePixelRatioF(),
                 "minimum_hint": [widget.minimumSizeHint().width(), widget.minimumSizeHint().height()],
                 "field": window.field_combo.currentData(), "layer": window.field_layer.currentData(),
                 "region": window.field_region.currentData(), "colormap": window.colormap_combo.currentData(),
                 "view_mode": window.view_mode.currentData(), "map_present": window.view.overlay_item is not None,
                 "field_limits": window.view.field_limits, "field_hint": window.field_hint.text(),
                 "statistics": window.selection_statistics.text(), "legend": window.legend.text(),
                 "docks": {dock.windowTitle(): [dock.minimumSizeHint().width(), dock.minimumSizeHint().height()]
                           for dock in window.findChildren(QDockWidget)},
                 "central_widgets": {name: {"size": [control.width(), control.height()],
                                               "minimum": [control.minimumSizeHint().width(), control.minimumSizeHint().height()],
                                               "height_for_width": control.heightForWidth(control.width())}
                                     for name, control in [("central", window.centralWidget()),
                                                           ("controls", window.result_controls), ("hint", window.field_hint),
                                                           ("view", window.view), ("legend_scale", window.legend_scale),
                                                           ("legend", window.legend), ("statistics", window.selection_statistics)]},
                 **details}
        if window.view.overlay_item is not None:
            bounds = window.view.mapFromScene(window.view.overlay_item.sceneBoundingRect()).boundingRect()
            state["overlay_pixel_bounds"] = [bounds.x(), bounds.y(), bounds.width(), bounds.height()]
            state["viewport_size"] = [window.view.viewport().width(), window.view.viewport().height()]
            state["full_overlay_in_viewport"] = window.view.viewport().rect().adjusted(-2, -2, 2, 2).contains(bounds)
        receipt["checkpoints"].append(state)
        save_receipt()
        print(f"Captured {name}: {state['window_size']} / DPR {state['device_pixel_ratio']}", flush=True)

    def manual(control, low, high):
        factor = (control.details or control._input_details or {}).get("display_factor", 1.)
        control.minimum.setText(str(low * factor))
        control.maximum.setText(str(high * factor))
        control.lock.setChecked(True)
        control.range_changed.emit()
        QTest.qWait(120)

    try:
        _, project, result = archive("E12_via_ladder")
        assert result and result.converged
        comparison_path = args.output.resolve() / "E12-comparison-0.2A.etl"
        if comparison_path.is_file():
            comparison_project, comparison_result = load_project(comparison_path)
        else:
            comparison_project = deepcopy(project)
            comparison_project.name = "Via ladder at 0.2 A"
            terminal = next(item for item in comparison_project.terminals if item.kind == "current")
            terminal.value = .2
            comparison_result = run_project(comparison_project)
            assert comparison_result.converged
            save_project(comparison_project, comparison_path, comparison_result)
        window = MainWindow(project, result, database_path=output / "materials.sqlite3", recover=False)
        window.recovery_path = output / "recovery.etlayout"
        window.autosave_timer.stop()
        window.error = lambda title, message: receipt["errors"].append(f"{title}: {message}")
        window.resize(1560, 980)
        window.apply_theme(False)
        window.show()
        window.activateWindow()
        wait_idle()
        select(window.field_layer, "m2")
        select(window.field_combo, "current_density")
        select(window.colormap_combo, "cividis")
        click(window.fit_layer_button)
        record("01-density-cividis")

        manual(window.legend_scale, 8e9, 2e8)
        assert not window.legend_scale.valid and window.view.overlay_item is None
        record("02-invalid-manual-range")
        manual(window.legend_scale, 2e8, 8e9)
        assert window.legend_scale.valid and window.view.field_limits == (2e8, 8e9)
        window.log_scale.setChecked(True)
        record("03-manual-log-range")
        export_view(window.view, output / "manual-map.png", window.legend.text(), provenance=window.displayed_provenance())
        export_view(window.view, output / "manual-map.svg", window.legend.text(), provenance=window.displayed_provenance())
        click(window.legend_scale.auto_button)

        region = "interface:via12"
        select(window.field_region, region)
        window.log_scale.setChecked(False)
        click(window.fit_layer_button)
        support = field_support(result, "m2", "current_density", region=region)
        inside = result.mesh.centroids[support.indices[0]]
        outside_id = next(index for index in result.mesh.elements("m2") if index not in set(support.indices))
        outside = result.mesh.centroids[outside_id]
        window.probe(*inside)
        window.probe(*outside)
        assert "outside active region" in window.console.toPlainText()
        window.on_line_drawn("profile", [inside.tolist(), outside.tolist()])
        profile = window.profiles.profiles[-1]
        assert np.isfinite(profile["values"]).any() and np.isnan(profile["values"]).any()
        record("04-interface-region-profile", inside_m=inside.tolist(), outside_m=outside.tolist(),
               finite_profile_samples=int(np.isfinite(profile["values"]).sum()))

        # Keyboard checks use actual Qt shortcut/focus processing, alongside a
        # structural buddy census; findings are retained rather than hidden.
        buddies = {label.text(): label.buddy().accessibleName() for label in window.findChildren(QLabel) if label.buddy()}
        focus = []
        window.field_layer.setFocus()
        for _ in range(9):
            current = app.focusWidget()
            focus.append(current.accessibleName() or current.objectName() or type(current).__name__ if current else None)
            QTest.keyClick(current or window, Qt.Key.Key_Tab)
            QTest.qWait(25)
        shortcuts = {}
        for key, name, target in [(Qt.Key.Key_L, "Alt+L result layer", window.field_layer),
                                  (Qt.Key.Key_R, "Alt+R result region", window.field_region),
                                  (Qt.Key.Key_C, "Alt+C color map", window.colormap_combo)]:
            QTest.keyClick(window, key, Qt.KeyboardModifier.AltModifier)
            QTest.qWait(60)
            shortcuts[name] = target.hasFocus()
        receipt["keyboard"] = {"label_buddies": buddies, "tab_sequence": focus, "shortcuts": shortcuts}
        if not all(shortcuts.values()):
            receipt["findings"].append({"keyboard_shortcuts": shortcuts})

        comparison = MapComparisonDialog([(project.name, result)], window)
        comparison.resize(1280, 800)
        comparison.show()
        QTest.qWait(250)
        select(comparison.layers[0], "m1")
        select(comparison.layers[1], "m2")
        select(comparison.field, "current_density")
        select(comparison.colormap, "magma")
        manual(comparison.range_control, 1e8, 1e10)
        assert comparison.has_comparison
        left, right = comparison.views
        before = left.transform().m11()
        center = left.viewport().rect().center()
        wheel = QWheelEvent(QPointF(center), QPointF(left.viewport().mapToGlobal(center)), QPoint(), QPoint(0, 120),
                            Qt.MouseButton.NoButton, Qt.KeyboardModifier.NoModifier, Qt.ScrollPhase.NoScrollPhase, False)
        app.sendEvent(left.viewport(), wheel)
        QTest.qWait(100)
        assert left.transform().m11() > before and abs(left.transform().m11() - right.transform().m11()) < 1e-9
        linked_scale = left.transform().m11()
        click(comparison.fit_button)
        record("05-same-run-linked-layers", comparison, linked_zoom_scale=linked_scale,
               comparison_limits=comparison.limits, comparison_palette=comparison.colormap.currentData())
        comparison.export_to(output / "same-run-comparison.png")
        comparison.export_to(output / "same-run-comparison.svg")
        comparison.close()

        select(window.field_region, "")
        window.load_difference_result(comparison_result, comparison_project.name)
        assert window.display_result and window.view.overlay_item is not None
        difference_hash = window.display_result.configuration_hash
        select(window.colormap_combo, "grayscale")
        window.isolate_layer.setChecked(False)
        window.isolate_layer.setChecked(True)
        window.mesh_toggle.setChecked(True)
        window.mesh_toggle.setChecked(False)
        select(window.colormap_combo, "coolwarm")
        assert window.display_result.configuration_hash == difference_hash and window.view_mode.currentData() == "difference"
        click(window.fit_layer_button)
        window.resize(1280, 800)
        QTest.qWait(200)
        click(window.fit_layer_button)
        record("06-difference-compact", requested_size=[1280, 800])
        if window.size().width() != 1280 or window.size().height() != 800:
            receipt["findings"].append({"compact_size": [window.width(), window.height()], "requested": [1280, 800]})
        export_results(window.display_result, output / "selected-difference", layers=["m2"], field="current_density",
                       colormap="coolwarm", provenance=window.displayed_provenance())
        export_html_report(window.project, window.display_result, window.view, output / "difference-report.html",
                            window.legend.text(), provenance=window.displayed_provenance())
        export_view(window.view, output / "difference-map.png", window.legend.text(), provenance=window.displayed_provenance())

        thermal_path, _, _ = archive("E10_thermal_stack")
        window.open_project(str(thermal_path))
        wait_idle()
        select(window.field_layer, "substrate")
        select(window.field_combo, "temperature_rise")
        select(window.colormap_combo, "teal")
        window.smooth_field.setChecked(True)
        click(window.fit_layer_button)
        window.resize(1280, 800)
        QTest.qWait(200)
        click(window.fit_layer_button)
        record("07-temperature-rise-smooth-compact", requested_size=[1280, 800])
        export_view(window.view, output / "smooth-temperature-map.png", window.legend.text())
        export_field_plot(window.result, "substrate", "temperature_rise", output / "temperature-rise-plot.png")
        window.apply_theme(True)
        QTest.qWait(750)
        record("08-temperature-rise-dark")
        select(window.colormap_combo, "viridis")
        record("09-temperature-viridis-dark")
        receipt["completed"] = True
        save_receipt()
        if receipt["errors"]:
            raise RuntimeError(receipt["errors"])
        print(f"Completed: {output}", flush=True)
        return 0
    except Exception as error:
        receipt["errors"].append(f"{type(error).__name__}: {error}")
        if window:
            receipt["workflow_hint"] = window.workflow_hint.text()
            receipt["console"] = window.console.toPlainText()
        receipt["completed"] = False
        save_receipt()
        raise
    finally:
        if comparison:
            comparison.close()
        if window:
            window.cancel_job()
            window.cancel_geometry()
            window.dirty = False
            window.close()
            QTest.qWait(100)


if __name__ == "__main__":
    raise SystemExit(main())
