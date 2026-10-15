# SPDX-License-Identifier: MPL-2.0
"""Exercise pulse setup, worker solve, time navigation, archive and exports in Qt."""
from __future__ import annotations

import argparse
import json
import multiprocessing
import os
import time
from pathlib import Path


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=Path(".artifacts/transient-ui"))
    parser.add_argument("--offscreen", action="store_true")
    parser.add_argument("--scale", type=float, default=1.)
    args = parser.parse_args()
    os.environ["QT_SCALE_FACTOR"] = str(args.scale)
    if args.offscreen:
        os.environ["QT_QPA_PLATFORM"] = "offscreen"
    else:
        os.environ.pop("QT_QPA_PLATFORM", None)
    from PySide6.QtCore import QSettings
    from PySide6.QtGui import QFont, QFontDatabase
    from PySide6.QtTest import QTest
    from PySide6.QtWidgets import QApplication, QTabWidget

    from etlayout.examples import make_example
    from etlayout.gui.export import export_html_report, export_view
    from etlayout.gui.main_window import MainWindow
    from etlayout.gui.transient import TransientDialog
    from etlayout.io.export import export_results
    from etlayout.io.project import load_project, save_project

    output = args.output.resolve() / f"{'offscreen' if args.offscreen else 'native'}-{args.scale:g}x"
    output.mkdir(parents=True, exist_ok=True)
    QSettings.setDefaultFormat(QSettings.Format.IniFormat)
    QSettings.setPath(QSettings.Format.IniFormat, QSettings.Scope.UserScope, str(output / "settings"))
    settings = QSettings("etlayout", "Electrothermal Layout Studio")
    settings.setFallbacksEnabled(False)
    settings.clear()
    app = QApplication([])
    app.setStyle("Fusion")
    if args.offscreen:
        for filename in ("segoeui.ttf", "segoeuib.ttf"):
            font = Path(os.environ.get("WINDIR", "C:/Windows")) / "Fonts" / filename
            if font.is_file():
                QFontDatabase.addApplicationFont(str(font))
    app.setFont(QFont("Segoe UI", 9))
    receipt = {"qpa": app.platformName(), "scale": args.scale, "capture": "QWidget.grab of live source app",
               "limits": "No installed-package, physical DPI, multiple-monitor or screen-reader claims.", "checkpoints": []}
    project = make_example("E18", output / "inputs")
    window = MainWindow(project, database_path=output / "materials.sqlite3", recover=False)
    window.autosave_timer.stop()
    window.recovery_path = output / "recovery.etl"
    errors = []
    window.error = lambda title, message: errors.append(f"{title}: {message}")

    def idle():
        start = time.monotonic()
        while window.job or window.prepare_job or window.hierarchy_jobs:
            assert time.monotonic() - start < 180, "Worker timed out"
            QTest.qWait(40)
        assert not errors, errors
        QTest.qWait(120)

    def capture(name, widget=None):
        widget = widget or window
        QTest.qWait(120)
        picture = widget.grab()
        assert picture.save(str(output / (name + ".png")))
        receipt["checkpoints"].append({"name": name, "logical_size": [widget.width(), widget.height()],
            "pixel_size": [picture.width(), picture.height()], "map_height": window.view.height(),
            "time_s": window.view_source_result().metrics.get("time_s") if window.result else None})
        print(name, flush=True)

    try:
        window.resize(1280, 800)
        window.show()
        window.resize(1280, 800)
        idle()
        window.guided.navigate("run")
        capture("01-solve-workflow")
        dialog = TransientDialog(window.project, window)
        dialog.show()
        capture("02-timing", dialog)
        dialog.findChild(QTabWidget).setCurrentIndex(1)
        capture("03-waveforms", dialog)
        dialog.reject()
        window.run_action.trigger()
        idle()
        assert window.result and window.result.converged and window.result.transient.completed
        window.guided.navigate("results")
        window.guided.drawer.hide()
        window.field_combo.setCurrentIndex(window.field_combo.findData("temperature"))
        window.colormap_combo.setCurrentIndex(window.colormap_combo.findData("magma"))
        window.legend_scale.minimum.setText("300")
        window.legend_scale.maximum.setText("325")
        window.legend_scale.lock.setChecked(True)
        window.time_navigation.slider.setValue(0)
        window.fit_result_layer()
        capture("04-initial-temperature")
        assert window.view.height() >= 350, window.view.size()
        times = window.result.transient.times
        index = min(range(len(times)), key=lambda i: abs(times[i] - window.result.metrics["transient_peak_time"]))
        window.time_navigation.slider.setValue(index)
        assert window.view.result is window.result.transient.snapshots[index]
        assert window.view.field_limits == (300., 325.)
        capture("05-pulse-temperature")
        window.show_analysis_page(window.time_history)
        capture("06-time-history")
        window.time_history.metric_selector.setCurrentIndex(4)
        capture("07-stored-energy")
        export_view(window.view, output / "selected-map.svg", window.legend.text(), provenance=window.displayed_provenance())
        export_view(window.view, output / "selected-map.png", window.legend.text(), provenance=window.displayed_provenance())
        export_html_report(project, window.view_source_result(), window.view, output / "selected-report.html",
                           window.legend.text(), provenance=window.displayed_provenance())
        export_results(window.result, output / "full-export")
        export_results(window.view_source_result(), output / "selected-export", plots=False,
                       layers=[window.field_layer.currentData()], field="temperature", provenance=window.displayed_provenance())
        archive = output / "transient.etl"
        save_project(project, archive, window.result)
        loaded_project, loaded_result = load_project(archive)
        assert len(loaded_result.transient.snapshots) == len(times)
        receipt["result"] = {"configuration_hash": loaded_result.configuration_hash,
                             "steps": loaded_result.metrics["transient_steps"], "snapshots": len(times),
                             "peak_temperature_K": loaded_result.metrics["transient_peak_temperature"],
                             "peak_time_s": loaded_result.metrics["transient_peak_time"],
                             "selected_time_s": float(times[index])}
        window.guided.drawer.hide()
        window.guided.canvas_tabs.setCurrentIndex(0)
        window.set_project(loaded_project, loaded_result)
        idle()
        window.field_combo.setCurrentIndex(window.field_combo.findData("temperature"))
        window.fit_result_layer()
        capture("08-reopened-final-time")
        (output / "observations.json").write_text(json.dumps(receipt, indent=2), encoding="utf-8")
        print("Transient acceptance passed", flush=True)
    finally:
        window.cancel_geometry()
        if window.job:
            window.cancel_job()
        window.dirty = False
        window.close()
        app.processEvents()


if __name__ == "__main__":
    multiprocessing.freeze_support()
    main()
