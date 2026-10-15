# SPDX-License-Identifier: MPL-2.0
"""Real native compact Mesh review, Apply/Undo and background local-study journey."""
from __future__ import annotations

import argparse
import hashlib
import json
import multiprocessing
import os
import platform
import time
from pathlib import Path


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=Path(".artifacts/local-refinement"))
    parser.add_argument("--scale", type=float, default=1.)
    parser.add_argument("--offscreen", action="store_true", help="Run the real Qt workflow headlessly for CI")
    args = parser.parse_args()
    os.environ["QT_SCALE_FACTOR"] = str(args.scale)
    if args.offscreen:
        os.environ["QT_QPA_PLATFORM"] = "offscreen"
    else:
        os.environ.pop("QT_QPA_PLATFORM", None)
    from desktop_acceptance_support import prepare_desktop_fonts
    from PySide6.QtCore import QSettings, Qt, QTimer
    from PySide6.QtTest import QTest
    from PySide6.QtWidgets import QApplication, QPushButton

    from etlayout.examples import make_example
    from etlayout.gui.main_window import MainWindow
    from etlayout.gui.refinement import RefinementReviewDialog
    from etlayout.io.project import configuration_hash

    root = args.output.resolve()
    root.mkdir(parents=True, exist_ok=True)
    QSettings.setDefaultFormat(QSettings.Format.IniFormat)
    QSettings.setPath(QSettings.Format.IniFormat, QSettings.Scope.UserScope, str(root / "settings"))
    app = QApplication([])
    app.setStyle("Fusion")
    prepare_desktop_fonts(app, root)
    project = make_example("E1", root / "inputs")
    imported = Path(project.layout.path)
    source_hash = hashlib.sha256(imported.read_bytes()).hexdigest()
    baseline = configuration_hash(project)
    window = MainWindow(project, database_path=root / "materials.sqlite3", recover=False)
    window.recovery_path = root / "recovery.etl"
    window.autosave_timer.stop()
    window.resize(1280, 800)
    window.show()
    ticks = []
    timer = QTimer()
    timer.setInterval(20)
    timer.timeout.connect(lambda: ticks.append(time.monotonic()))
    timer.start()
    captures, errors = [], []
    phase = ["apply"]

    def capture(widget, name):
        for _ in range(3):
            app.processEvents()
            QTest.qWait(60)
        path = root / (name + ".png")
        if not widget.grab().save(str(path)):
            raise AssertionError("Screenshot capture failed")
        captures.append(str(path))

    def wait(predicate, timeout=60.):
        start = time.monotonic()
        while not predicate():
            app.processEvents()
            QTest.qWait(20)
            if errors:
                raise errors[0]
            if time.monotonic() - start > timeout:
                raise TimeoutError("Native workflow did not finish")
        app.processEvents()

    def operate_dialog():
        dialog = app.activeModalWidget()
        if not isinstance(dialog, RefinementReviewDialog) or dialog.property("acceptance_operated"):
            return
        dialog.setProperty("acceptance_operated", True)
        try:
            assert configuration_hash(window.project) == baseline
            assert dialog.apply_button.isEnabled()
            capture(dialog, "02-review-" + phase[0])
            dialog.preview()
            dialog.preview()
            assert len([item for item in window.view.scene().items()
                        if item.data(0) == "local-refinement-preview"]) == 1
            assert configuration_hash(window.project) == baseline
            dialog.elements.setValue(1)
            assert not dialog.run_button.isEnabled()
            assert "exceeds budget" in dialog.status.text()
            dialog.elements.setValue(dialog.plan.max_elements)
            if phase[0] == "apply":
                QTest.mouseClick(dialog.apply_button, Qt.MouseButton.LeftButton)
            else:
                dialog.factors.setText("1,0.5")
                assert dialog.run_button.isEnabled()
                QTest.mouseClick(dialog.run_button, Qt.MouseButton.LeftButton)
        except BaseException as error:
            errors.append(error)
            dialog.reject()

    dialog_timer = QTimer()
    dialog_timer.setInterval(80)
    dialog_timer.timeout.connect(operate_dialog)
    dialog_timer.start()
    try:
        wait(lambda: not window.prepare_job and not window.hierarchy_jobs)
        window.guided.navigate("mesh")
        capture(window, "01-compact-mesh")
        button = next(item for item in window.findChildren(QPushButton)
                      if item.text() == "Suggest local refinement…")
        QTest.mouseClick(button, Qt.MouseButton.LeftButton)
        wait(lambda: bool(window.project.mesh.refinements) and not window.job and not window.prepare_job)
        assert not any(item.data(0) == "local-refinement-preview" for item in window.view.scene().items())
        assert window.project.mesh.max_size == project.mesh.max_size
        assert window.guided.document.revision == 1
        capture(window, "03-applied-sizing")
        window.undo()
        wait(lambda: not window.prepare_job and not window.job)
        assert configuration_hash(window.project) == baseline
        phase[0] = "study"
        button = next(item for item in window.findChildren(QPushButton)
                      if item.text() == "Suggest local refinement…")
        QTest.mouseClick(button, Qt.MouseButton.LeftButton)
        wait(lambda: len(window.studies) == 2 and not window.job)
        assert not any(item.data(0) == "local-refinement-preview" for item in window.view.scene().items())
        assert all(row.result and row.result.converged and not row.error for row in window.studies)
        assert all(abs(row.metrics["effective_resistance"] / .084 - 1) < 1e-6 for row in window.studies)
        assert all(row.result.configuration["mesh"]["max_size"] == project.mesh.max_size for row in window.studies)
        assert configuration_hash(window.project) == baseline
        assert hashlib.sha256(imported.read_bytes()).hexdigest() == source_hash
        assert ticks and len(ticks) > 20
        capture(window, "04-local-study")
        receipt = {"platform": platform.platform(), "qpa": app.platformName(), "qt_scale_factor": args.scale,
                   "logical_window": [window.width(), window.height()], "heartbeats": len(ticks),
                   "checked": ["real spawned guidance", "read-only replaceable temporary preview", "preview cleanup on completion", "budget rejection", "Apply transaction", "Undo",
                               "real two-level local study", "analytic R=0.084 ohm", "source file preserved"],
                   "runs": [{"elements": row.result.mesh.statistics["elements"], "metrics": row.metrics,
                             "elapsed_seconds": row.result.elapsed_seconds} for row in window.studies],
                   "captures": captures, "limits": ("Offscreen source acceptance" if args.offscreen else
                   "Native source execution on this Windows host") + "; not packaged or clean-machine verification, physical DPI or screen-reader qualification."}
        (root / "receipt.json").write_text(json.dumps(receipt, ensure_ascii=False, indent=2), encoding="utf-8")
        print(json.dumps({"checks": len(receipt["checked"]), "heartbeats": len(ticks), "captures": len(captures)}))
    finally:
        dialog_timer.stop()
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
