# SPDX-License-Identifier: MPL-2.0
"""Capture actual Qt result widgets and exercised interactions, before/after a change.

This development harness uses real converged solver archives and the application's
widgets. Offscreen captures do not verify native window chrome, display scaling,
screen readers, or an installed package. No screenshots or fields are synthesized.
"""
from __future__ import annotations

import argparse
import json
import multiprocessing
import os
import time
from pathlib import Path


def main(argv=None):
    multiprocessing.freeze_support()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=Path(".artifacts/result-visualization-audit"))
    parser.add_argument("--phase", choices=["before", "after"], default="before")
    parser.add_argument("--cache", type=Path, default=Path(".artifacts/example-gallery-cache"))
    parser.add_argument("--offscreen", action="store_true")
    args = parser.parse_args(argv)
    if args.offscreen:
        os.environ["QT_QPA_PLATFORM"] = "offscreen"
    from desktop_acceptance_support import prepare_desktop_fonts
    from PySide6.QtCore import QSettings, Qt
    from PySide6.QtTest import QTest
    from PySide6.QtWidgets import QApplication, QDockWidget, QMessageBox, QTabWidget

    from etlayout.examples import make_example
    from etlayout.gui.main_window import MainWindow
    from etlayout.io.project import load_project, save_project
    from etlayout.simulation.runner import run_project

    root = args.output.resolve()
    output = root / args.phase
    output.mkdir(parents=True, exist_ok=True)
    QSettings.setDefaultFormat(QSettings.Format.IniFormat)
    QSettings.setPath(QSettings.Format.IniFormat, QSettings.Scope.UserScope, str(output / "settings"))
    application = QApplication.instance() or QApplication(["result-visualization-acceptance"])
    application.setStyle("Fusion")
    prepare_desktop_fonts(application, output)
    window = None
    checkpoints = []
    errors = []

    def archive(name):
        path = args.cache.resolve() / (name + ".etl")
        if path.is_file():
            project, result = load_project(path)
            if result and result.converged and project.solver.mode == "electrothermal":
                return path, project, result
        project = make_example(name, output / "examples")
        project.solver.mode = "electrothermal"
        result = run_project(project)
        assert result.converged, name
        path = output / (name + ".etl")
        save_project(project, path, result)
        return path, project, result

    def wait_idle(timeout=120.):
        start = time.monotonic()
        while window.job or window.prepare_job or window.hierarchy_jobs:
            if time.monotonic() - start > timeout:
                raise TimeoutError("Desktop geometry workers did not finish")
            modal = QApplication.activeModalWidget()
            if isinstance(modal, QMessageBox):
                message = modal.text()
                modal.reject()
                raise RuntimeError(message)
            QTest.qWait(35)
        assert window.geometry is not None, window.console.toPlainText()
        QTest.qWait(160)

    def select(combo, value):
        index = combo.findData(value)
        assert index >= 0, (value, [combo.itemData(i) for i in range(combo.count())])
        combo.setCurrentIndex(index)
        QTest.qWait(100)
        assert combo.currentData() == value

    def record(step, filename, **details):
        QTest.qWait(160)
        assert window.grab().save(str(output / filename))
        view_file = filename.replace(".png", "-canvas.png")
        assert window.view.grab().save(str(output / view_file))
        counts = {str(key): {"items": len(items), "visible": sum(item.isVisible() for item in items),
                            "opacity": [float(item.opacity()) for item in items]}
                  for key, items in window.view.layer_items.items()}
        checkpoint = {"step": step, "screenshot": filename, "canvas_screenshot": view_file,
                      "window_size": [window.width(), window.height()],
                      "window_minimum_hint": [window.minimumSizeHint().width(), window.minimumSizeHint().height()],
                      "dock_minimum_hints": {dock.windowTitle(): [dock.minimumSizeHint().width(), dock.minimumSizeHint().height()]
                                             for dock in window.findChildren(QDockWidget)},
                      "project": window.project.name, "project_mode": window.project.solver.mode,
                      "configuration_hash": window.result.configuration_hash,
                      "selected_layer": window.field_layer.currentData(),
                      "selected_field": window.field_combo.currentData(),
                      "rendered_layer": window.view.field_layer, "rendered_field": window.view.field_key,
                      "legend": window.view.legend, "legend_details": window.legend_scale.details,
                      "field_hint": window.field_hint.text() if hasattr(window, "field_hint") else None,
                      "isolate_layer": window.isolate_layer.isChecked() if hasattr(window, "isolate_layer") else None,
                      "map_present": window.view.overlay_item is not None,
                      "view_transform_scale": window.view.transform().m11(),
                      "scene_layers": counts, **details}
        checkpoints.append(checkpoint)
        receipt = {"phase": args.phase, "qt_platform": application.platformName(),
                   "window_size": [window.width(), window.height()],
                   "capture_method": "Fresh QWidget.grab() after QComboBox.setCurrentIndex and QTest event settling; live application signals and rendering",
                   "limits": "Development-widget evidence; native window chrome, OS display scaling, screen-reader behavior and installed package not verified.",
                   "checkpoints": checkpoints, "errors": errors}
        (output / "observations.json").write_text(json.dumps(receipt, indent=2, ensure_ascii=False), encoding="utf-8")
        print(f"Captured {filename}: {output}", flush=True)

    try:
        _, project, result = archive("E12_via_ladder")
        window = MainWindow(project, result, database_path=output / "materials.sqlite3", recover=False)
        window.recovery_path = output / "recovery.etlayout"
        window.autosave_timer.stop()
        window.resize(1560, 980)
        window.apply_theme(False)
        window.show()
        wait_idle()
        window.view.fit_all()
        record("E12 initial converged result", "01-e12-initial.png")
        select(window.field_layer, "m1")
        select(window.field_combo, "current_density")
        record("E12 current density on m1", "02-e12-density-m1.png")
        previous_field = window.field_combo.currentData()
        select(window.field_layer, "m2")
        record("E12 layer selector m1 → m2", "03-e12-density-m2.png",
               requested_layer="m2", prior_field=previous_field,
               field_preserved=window.field_combo.currentData() == previous_field)
        if args.phase == "after":
            QTest.mouseClick(window.fit_layer_button, Qt.MouseButton.LeftButton,
                             pos=window.fit_layer_button.rect().center())
            record("E12 fit selected middle riser", "03b-e12-fit-m2.png")
            QTest.mouseClick(window.isolate_layer, Qt.MouseButton.LeftButton,
                             pos=window.isolate_layer.rect().center())
            window.view.fit_all()
            record("E12 other layers visible as context", "03c-e12-context.png")
            parent = window.layer_tree.parentWidget()
            for tabs in window.findChildren(QTabWidget):
                index = tabs.indexOf(parent)
                if index >= 0:
                    QTest.mouseClick(tabs.tabBar(), Qt.MouseButton.LeftButton,
                                     pos=tabs.tabBar().tabRect(index).center())
                    break
            item = next(window.layer_tree.topLevelItem(index)
                        for index in range(window.layer_tree.topLevelItemCount())
                        if window.layer_tree.topLevelItem(index).data(0, Qt.ItemDataRole.UserRole) == "m1")
            window.layer_tree.scrollToItem(item)
            QTest.qWait(80)
            QTest.mouseClick(window.layer_tree.viewport(), Qt.MouseButton.LeftButton,
                             pos=window.layer_tree.visualItemRect(item).center())
            assert window.field_layer.currentData() == "m1"
            record("E12 physical layer tree click selects m1 result", "03d-e12-tree-m1.png")
            window.isolate_layer.setChecked(True)
            QTest.mouseClick(window.mask_toggle, Qt.MouseButton.LeftButton,
                             pos=window.mask_toggle.rect().center())
            assert window._show_masks and not window.isolate_layer.isEnabled()
            assert any(item.isVisible() for items in window.view.layer_items.values() for item in items)
            assert window.field_combo.currentData() == "current_density"
            record("E12 source masks retain geometry context", "03d2-e12-source-masks.png")
            QTest.mouseClick(window.mask_toggle, Qt.MouseButton.LeftButton,
                             pos=window.mask_toggle.rect().center())
            select(window.field_layer, "substrate")
            assert window.field_combo.currentData() == "current_density"
            assert window.view.overlay_item is None
            record("E12 incompatible substrate preserves density selection", "03e-e12-unavailable.png")
            if not window.isolate_layer.isChecked():
                window.isolate_layer.setChecked(True)
        thermal_path, _, _ = archive("E10_thermal_stack")
        window.open_project(str(thermal_path))
        wait_idle()
        window.view.fit_all()
        select(window.field_layer, "substrate")
        select(window.field_combo, "temperature")
        record("E10 substrate temperature", "04-e10-substrate-temperature.png")
        select(window.field_layer, "metal")
        record("E10 layer selector substrate → metal", "05-e10-metal-temperature.png",
               requested_layer="metal", field_preserved=window.field_combo.currentData() == "temperature")
        if args.phase == "after":
            select(window.field_layer, "substrate")
            window.resize(1280, 800)
            QTest.qWait(200)
            window.view.fit_all()
            record("E10 temperature at compact size", "06-e10-compact.png", requested_window_size=[1280, 800])
            assert window.width() == 1280 and window.height() == 800
            window.apply_theme(True)
            QTest.qWait(750)
            record("E10 temperature in dark theme", "07-e10-dark.png")
            record("E10 dark theme repeated full-window capture", "08-e10-dark-repeated.png")
        (output / "error.json").unlink(missing_ok=True)
        return 0
    except Exception as error:
        errors.append(str(error))
        (output / "error.json").write_text(json.dumps({"errors": errors}, indent=2), encoding="utf-8")
        raise
    finally:
        if window:
            window.cancel_job()
            window.cancel_geometry()
            window.dirty = False
            window.close()
            QTest.qWait(100)


if __name__ == "__main__":
    raise SystemExit(main())
