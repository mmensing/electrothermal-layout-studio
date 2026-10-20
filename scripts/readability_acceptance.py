# SPDX-License-Identifier: MPL-2.0
"""Capture real compact-window labels and precision-sensitive result scales.

This uses the native application, nominal E15 worker solve and shipped E21
geometry. QT_SCALE_FACTOR checks are simulated scaling, not monitor-DPI testing.
"""
from __future__ import annotations

import argparse
import json
import multiprocessing
import os
from pathlib import Path


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=Path(".artifacts/readability"))
    parser.add_argument("--scale", type=float, default=1.)
    parser.add_argument("--offscreen", action="store_true")
    args = parser.parse_args(argv)
    os.environ["QT_SCALE_FACTOR"] = str(args.scale)
    if args.offscreen:
        os.environ["QT_QPA_PLATFORM"] = "offscreen"
    else:
        os.environ.pop("QT_QPA_PLATFORM", None)
    from desktop_acceptance_support import (
        assert_desktop_project,
        close_desktop_window,
        isolate_acceptance_settings,
        prepare_desktop_fonts,
        wait_desktop_events,
        wait_desktop_workers,
    )
    from PySide6.QtTest import QTest
    from PySide6.QtWidgets import QApplication

    from etlayout.gui.main_window import MainWindow
    from etlayout.io.project import load_project

    output = args.output.resolve() / f"{'offscreen' if args.offscreen else 'native'}-{args.scale:g}x"
    output.mkdir(parents=True, exist_ok=True)
    isolate_acceptance_settings(output)
    app = QApplication.instance() or QApplication([])
    app.setStyle("Fusion")
    prepare_desktop_fonts(app, output)
    root = Path(__file__).resolve().parents[1]
    receipt = {"qpa": app.platformName(), "scale": args.scale, "captures": [],
               "scope": "Source application on native host; no physical DPI or clean-machine certification"}
    window = None

    def wait_idle(timeout=180.):
        wait_desktop_workers(window, timeout=timeout)
        assert window.geometry is not None, window.console.toPlainText()
        wait_desktop_events(200)

    def capture(name):
        QTest.qWait(250)
        image = window.grab()
        assert image.save(str(output / (name + ".png")))
        receipt["captures"].append({"name": name, "logical_size": [window.width(), window.height()],
            "pixel_size": [image.width(), image.height()], "device_pixel_ratio": window.devicePixelRatioF(),
            "project": window.project.name, "path": str(window.path),
            "configuration_hash": window.result.configuration_hash if window.result else None,
            "legend": window.view.legend, "range": window.view.field_limits,
            "callouts": sum(item.label_visible for item in window.view.callout_items.values()),
            "terminals": sum(items[1].label_visible for items in window.view.terminal_items.values() if len(items) > 1)})

    try:
        project, _ = load_project(root / "examples/E21_long_finger_gan.etl")
        window = MainWindow(project, database_path=output / "materials.sqlite3", recover=False)
        window.recovery_path = output / "recovery.etl"
        window.autosave_timer.stop()
        window.resize(1280, 800)
        window.show()
        wait_idle()
        window.view.fit_all()
        capture("01-E21-compact-overview")
        window.view.scale(2., 2.)
        capture("02-E21-zoomed-fingers")
        thermal_path = root / "examples/E15_thermal_crosstalk.etl"
        thermal_project, _ = load_project(thermal_path)
        window.open_project(thermal_path)
        wait_idle()
        assert_desktop_project(window, thermal_path, thermal_project)
        window.start_job("solve")
        wait_idle()
        assert window.result is not None and window.result.converged
        window.field_combo.setCurrentIndex(window.field_combo.findData("temperature"))
        window.view.fit_field()
        capture("03-E15-narrow-temperature-scale")
        window.field_combo.setCurrentIndex(window.field_combo.findData("temperature_rise"))
        window.legend_scale.use_auto()
        window.update_overlay()
        capture("04-E15-temperature-rise")
        window.dirty = False
        straight_path = root / "examples/E1_straight.etl"
        straight_project, _ = load_project(straight_path)
        window.open_project(straight_path)
        wait_idle()
        assert_desktop_project(window, straight_path, straight_project)
        window.start_job("convergence", {"sizes": [window.project.mesh.max_size, window.project.mesh.max_size / 2]})
        wait_idle()
        assert len(window.study_panel.rows) == 2 and all(row.result and row.result.converged for row in window.study_panel.rows)
        window.study_panel.metric_combo.setCurrentIndex(window.study_panel.metric_combo.findData("total_current"))
        window.study_panel.refresh()
        capture("05-E1-nearconstant-study")
        receipt["study_currents_A"] = [row.result.metrics["total_current"] for row in window.study_panel.rows]
        receipt["study_display_range"] = window.study_panel.plot.viewRange()[1]
        assert sorted(window.study_panel.plot.listDataItems()[0].getData()[1].tolist()) == sorted(receipt["study_currents_A"])
        assert "numerical data are unchanged" in window.study_panel.summary.text()
        receipt["status"] = "passed"
    except Exception as error:
        receipt["status"] = "failed"
        receipt["error"] = f"{type(error).__name__}: {error}"
        raise
    finally:
        try:
            if window is not None:
                close_desktop_window(window)
        except Exception as error:
            receipt["status"] = "failed"
            receipt["cleanup_error"] = f"{type(error).__name__}: {error}"
            raise
        finally:
            (output / "receipt.json").write_text(json.dumps(receipt, indent=2), encoding="utf-8")
    print(json.dumps(receipt, indent=2))
    return 0


if __name__ == "__main__":
    multiprocessing.freeze_support()
    raise SystemExit(main())
