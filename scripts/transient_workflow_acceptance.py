# SPDX-License-Identifier: MPL-2.0
"""Exercise multilayer time inspection, saved-state replay and time studies in Qt."""
from __future__ import annotations

import argparse
import json
import multiprocessing
import os
import time
from pathlib import Path


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=Path(".artifacts/transient-workflow"))
    parser.add_argument("--offscreen", action="store_true")
    parser.add_argument("--scale", type=float, default=1.)
    args = parser.parse_args()
    os.environ["QT_SCALE_FACTOR"] = str(args.scale)
    if args.offscreen:
        os.environ["QT_QPA_PLATFORM"] = "offscreen"
    else:
        os.environ.pop("QT_QPA_PLATFORM", None)
    import numpy as np
    from desktop_acceptance_support import prepare_desktop_fonts
    from PySide6.QtCore import QSettings
    from PySide6.QtGui import QFont
    from PySide6.QtTest import QTest
    from PySide6.QtWidgets import QApplication

    from etlayout.examples import make_example
    from etlayout.gui.comparison import MapComparisonDialog
    from etlayout.gui.main_window import MainWindow
    from etlayout.io.project import load_project, save_project

    output = args.output.resolve() / f"{'offscreen' if args.offscreen else 'native'}-{args.scale:g}x"
    output.mkdir(parents=True, exist_ok=True)
    QSettings.setDefaultFormat(QSettings.Format.IniFormat)
    QSettings.setPath(QSettings.Format.IniFormat, QSettings.Scope.UserScope, str(output / "settings"))
    app = QApplication([])
    app.setStyle("Fusion")
    prepare_desktop_fonts(app, output)
    app.setFont(QFont("Segoe UI", 9))
    project = make_example("E19", output / "inputs")
    project.thresholds = {"transient_peak_temperature": 301.}
    window = MainWindow(project, database_path=output / "materials.sqlite3", recover=False)
    window.autosave_timer.stop()
    window.recovery_path = output / "recovery.etl"
    errors = []
    window.error = lambda title, message: errors.append(f"{title}: {message}")
    receipt = {"capture": "Live source app QWidget.grab", "platform": app.platformName(),
               "scale": args.scale, "checks": [], "screens": [],
               "limits": "No installed-package, physical-DPI, screen-reader or multiple-monitor claim."}

    def idle():
        started = time.monotonic()
        while window.job or window.prepare_job or window.hierarchy_jobs:
            assert time.monotonic() - started < 180, "Worker timeout"
            QTest.qWait(40)
        assert not errors, errors
        QTest.qWait(100)

    def capture(name, widget=None):
        target = widget or window
        QTest.qWait(120)
        assert target.grab().save(str(output / (name + ".png")))
        active = window.guided.canvas_tabs.currentWidget()
        receipt["screens"].append({"name": name,
                                   "active_canvas_tab": window.guided.canvas_tabs.tabText(window.guided.canvas_tabs.currentIndex()),
                                   "active_canvas_size": [active.width(), active.height()],
                                   "map_visible": window.view.isVisible()})
        print(name, flush=True)

    try:
        window.resize(1280, 800)
        window.show()
        window.resize(1280, 800)
        idle()
        window.run()
        idle()
        result = window.result
        assert result and result.converged
        window.field_combo.setCurrentIndex(window.field_combo.findData("temperature"))
        window.field_layer.setCurrentIndex(window.field_layer.findData("receiver"))
        window.history_range.setChecked(True)
        window.time_navigation.select_time(690e-6)
        window.show_analysis_page(window.time_history)
        window.time_history.scope_selector.setCurrentIndex(window.time_history.scope_selector.findData("selection"))
        window.fit_result_layer()
        # The guided workspace gives a time-history graph its own canvas tab;
        # a hidden layout view's retained size says nothing about usable space.
        assert window.guided.canvas_tabs.currentWidget() is window.time_history
        assert window.time_history.isVisible() and window.time_history.height() >= 350
        assert window.time_history.trace_values.max() > 300
        capture("01-receiver-history")
        window.time_history.export_local_history_to(output / "receiver-history.csv")
        point = result.mesh.centroids[result.mesh.elements("receiver")[0]]
        window.probe(*point)
        window.time_history.scope_selector.setCurrentIndex(window.time_history.scope_selector.findData("probe"))
        window.show_analysis_page(window.time_history)
        window.time_navigation.select_time(400e-6)
        assert window.time_history.probe == tuple(point)
        capture("02-probe-history")
        dialog = MapComparisonDialog([("E19 run", result)], window)
        dialog.resize(1280, 800)
        dialog.show()
        for side, layer in enumerate(("heater", "receiver")):
            dialog.layers[side].setCurrentIndex(dialog.layers[side].findData(layer))
        dialog.field.setCurrentIndex(dialog.field.findData("temperature"))
        dialog.history_range.setChecked(True)
        dialog.time_navigations[0].select_time(400e-6)
        assert dialog.selected_result(0).metrics["time_s"] == dialog.selected_result(1).metrics["time_s"]
        capture("03-synchronized-layers", dialog)
        dialog.reject()
        receipt["checks"].append("E19 worker solve; receiver/probe histories; stable range; linked layer times")

        # A new saved-state run must persist its initial-field identity, and F5
        # after archive reopening must reproduce the exact same trajectory.
        window.project.transient.initialization = "uniform"
        window.project.transient.initial_state_digest = ""
        window.start_job("solve", {"initial_result": result, "resume": False})
        idle()
        warm = window.result
        assert window.project.transient.initialization == "saved"
        assert not any(issue.key == "setup.initial_state" for issue in window.guided.assessment.blockers)
        archive = output / "warm-state.etl"
        save_project(window.project, archive, warm)
        loaded_project, loaded = load_project(archive)
        window.set_project(loaded_project, loaded)
        idle()
        window.run()
        idle()
        replay = window.result
        assert replay.configuration_hash == warm.configuration_hash
        assert not any(issue.key == "setup.initial_state" for issue in window.guided.assessment.blockers)
        for layer in warm.fields:
            np.testing.assert_allclose(replay.fields[layer]["temperature"], warm.fields[layer]["temperature"], equal_nan=True)
        receipt["checks"].append("Saved-state worker run and archive-reopened F5 replay preserve thermal fields and identity")
        receipt["checks"].append("Validated saved-state evidence remains ready after initial solve and archive-reopened replay")
        window.show_analysis_page(window.results_page)
        capture("04-saved-state-replayed")

        project = make_example("E17", output / "study-inputs")
        window.set_project(project)
        idle()
        window.start_job("time_convergence", {"steps": [project.transient.time_step, project.transient.time_step / 2]})
        idle()
        assert len(window.studies) == 2 and all(row.result and row.result.converged for row in window.studies)
        assert window.study_panel.metric_combo.currentData() == "transient_peak_temperature"
        receipt["checks"].append("Background time-step convergence completes with peak-temperature comparison")
        capture("05-time-convergence")
        (output / "observations.json").write_text(json.dumps(receipt, indent=2), encoding="utf-8")
        print("Transient workflow acceptance passed", flush=True)
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
