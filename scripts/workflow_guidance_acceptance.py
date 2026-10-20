# SPDX-License-Identifier: MPL-2.0
"""Capture real example selection and verification guidance at a chosen Qt scale.

Use a disposable output directory. Font checks reject missing-glyph captures.
This verifies Qt widget/application scaling, not clean-machine or physical DPI.
"""
from __future__ import annotations

import argparse
import json
import os
import platform
import time
from pathlib import Path


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--scale", type=float, default=1.)
    parser.add_argument("--offscreen", action="store_true")
    args = parser.parse_args(argv)
    os.environ["QT_SCALE_FACTOR"] = str(args.scale)
    if args.offscreen:
        os.environ["QT_QPA_PLATFORM"] = "offscreen"
    else:
        os.environ.pop("QT_QPA_PLATFORM", None)
    from desktop_acceptance_support import (
        isolate_acceptance_settings,
        prepare_desktop_fonts,
        wait_desktop_events,
    )
    from PySide6.QtCore import QCoreApplication, QEvent, Qt
    from PySide6.QtTest import QTest
    from PySide6.QtWidgets import QApplication, QDialogButtonBox

    from etlayout.examples import make_example
    from etlayout.gui.example_library import ExampleLibraryDialog
    from etlayout.gui.main_window import MainWindow
    from etlayout.gui.theme import apply_widget_theme
    from etlayout.simulation.runner import run_project

    started = time.monotonic()
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=True)
    isolate_acceptance_settings(output)
    app = QApplication.instance() or QApplication([])
    app.setStyle("Fusion")
    prepare_desktop_fonts(app, output)
    receipt = {"platform": platform.platform(), "qpa": app.platformName(), "qt_scale_factor": args.scale,
               "checks": [], "captures": [],
               "limitations": "Real Qt widgets and native/offscreen backend as recorded; not screen-reader, physical DPI, multi-monitor or clean-machine certification."}
    window = dialog = None

    def capture(widget, name):
        wait_desktop_events()
        picture = widget.grab()
        assert picture.save(str(output / (name + ".png")))
        receipt["captures"].append({"file": name + ".png",
                                    "logical_size": [widget.width(), widget.height()],
                                    "pixels": [picture.width(), picture.height()],
                                    "dpr": widget.devicePixelRatioF()})

    try:
        dialog = ExampleLibraryDialog(identifier="E21")
        apply_widget_theme(dialog, False)
        dialog.resize(680, 480)
        dialog.show()
        dialog.preset.setCurrentIndex(dialog.preset.findData("large"))
        wait_desktop_events()
        assert "64 × 1000" in dialog.inputs.toPlainText()
        assert dialog.detail_tabs.currentWidget() is dialog.inputs
        selected = dialog.examples.currentItem()
        assert selected is not None and dialog.examples.viewport().rect().contains(dialog.examples.visualItemRect(selected))
        assert "Large/stress cases are opt-in" in dialog.resource_note.text()
        for control in (dialog.open_button, dialog.buttons.button(QDialogButtonBox.StandardButton.Cancel)):
            assert control.isVisible() and dialog.rect().contains(control.mapTo(dialog, control.rect().bottomRight()))
        capture(dialog, "compact-requested-long-finger-inputs")
        dialog.detail_tabs.setCurrentWidget(dialog.details)
        capture(dialog, "compact-model-and-use")
        dialog.select_example("E39")
        assert dialog.scenario.currentData() == "continuous_reference"
        assert "drain 2 A" in dialog.inputs.toPlainText()
        dialog.resize(980, 700)
        capture(dialog, "recommended-sic-inputs")
        dialog.search.setFocus()
        QTest.keyClicks(dialog.search, "E2")
        assert dialog.examples.count() == 1
        QTest.keyClick(dialog.search, Qt.Key.Key_Escape)
        assert not dialog.isVisible()
        receipt["checks"].append("Requested presets/scenarios, visible selected row after compact resize, fixed actions, model tab, exact-ID keyboard search and Escape")

        project = make_example("E1", output / "inputs")
        result = run_project(project)
        assert result.converged
        window = MainWindow(project, result, database_path=output / "materials.sqlite3", recover=False)
        window.autosave_timer.stop()
        window.resize(1280, 800)
        window.show()
        deadline = time.monotonic() + 30
        while window.prepare_job or window.hierarchy_jobs:
            if time.monotonic() > deadline:
                raise TimeoutError("Geometry preparation did not finish")
            wait_desktop_events()
        window.guided.navigate("results", force=True)
        assert window.result is result and window.guided.verify_mesh.isEnabled()
        assert "Mesh: not established" in window.guided.result_evidence.text()
        window.guided.page_scroll.ensureWidgetVisible(window.guided.verify_mesh)
        capture(window, "actual-e1-verification-guidance")
        receipt["checks"].append({"name": "Actual E1 electrical result; truthful missing-mesh evidence with guarded direct action",
                                   "effective_resistance_ohm": result.metrics["effective_resistance"]})
        receipt["status"] = "passed"
        receipt["elapsed_seconds"] = time.monotonic() - started
        (output / "receipt.json").write_text(json.dumps(receipt, indent=2), encoding="utf-8")
        print(json.dumps({"status": receipt["status"], "qpa": app.platformName(), "captures": len(receipt["captures"])}))
    finally:
        if dialog is not None:
            dialog.close()
            dialog.deleteLater()
        if window is not None:
            window.cancel_geometry()
            window.guided.discard_draft()
            window.dirty = False
            window.close()
            window.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        app.processEvents()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
