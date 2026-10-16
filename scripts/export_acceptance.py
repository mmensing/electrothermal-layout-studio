# SPDX-License-Identifier: MPL-2.0
"""Real E1 solve, modal export review and PNG/PDF/data/archive journeys."""
from __future__ import annotations

import argparse
import json
import multiprocessing
import os
import time
from pathlib import Path


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=Path(".artifacts/export-acceptance"))
    parser.add_argument("--scale", type=float, default=1.)
    parser.add_argument("--offscreen", action="store_true")
    args = parser.parse_args()
    os.environ["QT_SCALE_FACTOR"] = str(args.scale)
    if args.offscreen:
        os.environ["QT_QPA_PLATFORM"] = "offscreen"
    else:
        os.environ.pop("QT_QPA_PLATFORM", None)
    from desktop_acceptance_support import isolate_acceptance_settings, prepare_desktop_fonts
    from PySide6.QtCore import Qt, QTimer
    from PySide6.QtTest import QTest
    from PySide6.QtWidgets import QApplication

    from etlayout.examples import make_example
    from etlayout.gui.export_dialog import ExportDialog
    from etlayout.gui.main_window import MainWindow
    from etlayout.io.project import configuration_hash, file_hash, load_project
    from etlayout.simulation.runner import run_project

    root = args.output.resolve()
    root.mkdir(parents=True, exist_ok=True)
    # Relative destination labels keep personal workstation paths out of captures.
    os.chdir(root)
    isolate_acceptance_settings(root)
    app = QApplication([])
    app.setStyle("Fusion")
    prepare_desktop_fonts(app, root)
    project = make_example("E1", root / "inputs")
    before_hash = configuration_hash(project)
    source_hash = file_hash(project.layout.path)
    result = run_project(project)
    assert result.converged and abs(result.metrics["effective_resistance"] / .084 - 1) < 1e-6
    window = MainWindow(project, result, database_path=root / "materials.sqlite3", recover=False)
    window.recovery_path = root / "recovery.etl"
    window.autosave_timer.stop()
    window.resize(1280, 800)
    window.show()
    captured, errors, exports = [], [], []
    mode = ["figure"]

    def capture(widget, name):
        app.processEvents()
        QTest.qWait(40)
        path = root / (name + ".png")
        image = widget.grab()
        assert image.save(str(path))
        captured.append({"name": name, "logical_size": [widget.width(), widget.height()],
                         "pixel_size": [image.width(), image.height()], "device_pixel_ratio": widget.devicePixelRatioF()})

    def operate():
        dialog = QApplication.activeModalWidget()
        if not isinstance(dialog, ExportDialog) or dialog.property("operated"):
            return
        dialog.setProperty("operated", True)
        try:
            dialog.directory.setText("output-" + mode[0])
            dialog.stem.setText("E1-" + mode[0])
            if mode[0] == "figure":
                for name in ("png", "pdf", "svg", "html"):
                    dialog.formats[name].setChecked(True)
                dialog.dpi.setValue(180)
                dialog.title.setText("E1 · fixed-temperature potential")
                dialog.tabs.setCurrentIndex(0)
                capture(dialog, "01-figure-selection")
                dialog.tabs.setCurrentIndex(1)
                capture(dialog, "02-figure-options")
            dialog.review()
            assert dialog.export_button.isEnabled(), dialog.preview.toPlainText()
            capture(dialog, "03-review-" + mode[0])
            # Keyboard navigation remains available; a changed stem revokes review.
            dialog.stem.setFocus()
            QTest.keyClick(dialog.stem, Qt.Key.Key_End)
            QTest.keyClicks(dialog.stem, "-checked")
            assert not dialog.export_button.isEnabled()
            dialog.review()
            QTest.mouseClick(dialog.export_button, Qt.MouseButton.LeftButton)
            assert dialog.exported is not None
            exports.append(dialog.exported)
        except BaseException as error:
            errors.append(str(error))
            dialog.reject()

    timer = QTimer()
    timer.setInterval(40)
    timer.timeout.connect(operate)
    timer.start()
    try:
        started = time.monotonic()
        while window.prepare_job or window.job or window.hierarchy_jobs:
            if time.monotonic() - started > 90:
                raise TimeoutError("Window geometry did not finish")
            QTest.qWait(30)
        window.guided.navigate("results")
        capture(window, "00-source-results")
        for item in ("figure", "data", "archive"):
            mode[0] = item
            window.share_export(item)
            assert not errors, errors
        from PySide6.QtCore import QSize
        from PySide6.QtPdf import QPdfDocument
        pdf = QPdfDocument()
        assert pdf.load(str(root / "output-figure" / "E1-figure-checked.pdf")) == QPdfDocument.Error.None_
        assert pdf.render(0, QSize(1100, 778)).save(str(root / "04-vector-pdf-preview.png"))
        assert configuration_hash(window.project) == before_hash
        assert source_hash == file_hash(project.layout.path)
        assert result.configuration_hash == window.result.configuration_hash
        restored, archived = load_project(root / "output-archive" / "E1-archive-checked.etl")
        assert archived.configuration_hash == result.configuration_hash and restored.layout.sha256 == project.layout.sha256
        receipt = {"status": "passed", "qpa": app.platformName(), "scale_factor": args.scale,
            "effective_resistance_ohm": result.metrics["effective_resistance"], "configuration_hash": result.configuration_hash,
            "source_sha256": source_hash, "captures": captured, "exports": exports,
            "checks": ["configured E1 calculation", "three real modal workflows", "review invalidation", "keyboard entry", "PNG/PDF/SVG/HTML generation", "selected CSV/NPZ/VTU", "complete archive reopen", "unchanged physics/source/fields"],
            "limitations": "Source Windows Qt acceptance; not frozen/clean-machine, physical monitor DPI, browser report rendering or screen-reader certification"}
        (root / "receipt.json").write_text(json.dumps(receipt, indent=2, ensure_ascii=False), encoding="utf-8")
        print(json.dumps({"status": receipt["status"], "checks": len(receipt["checks"]), "captures": len(captured)}))
    finally:
        timer.stop()
        window.cancel_geometry()
        if window.job:
            window.cancel_job()
        window.guided.discard_draft()
        window.dirty = False
        window.close()
    return 0


if __name__ == "__main__":
    multiprocessing.freeze_support()
    raise SystemExit(main())
