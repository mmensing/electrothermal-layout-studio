# SPDX-License-Identifier: MPL-2.0
"""Native verification of side-by-side maps and selected-profile mesh studies."""
from __future__ import annotations

import argparse
import json
import multiprocessing
import os
import time
from pathlib import Path


def main(argv=None):
    multiprocessing.freeze_support()
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", default=".artifacts/gui-comparison-native")
    parser.add_argument("--offscreen", action="store_true")
    args = parser.parse_args(argv)
    if args.offscreen:
        os.environ["QT_QPA_PLATFORM"] = "offscreen"
    from PySide6.QtCore import QTimer
    from PySide6.QtTest import QTest
    from PySide6.QtWidgets import QApplication, QDialogButtonBox, QLineEdit, QMessageBox

    from etlayout.examples import make_example
    from etlayout.gui.comparison import MapComparisonDialog
    from etlayout.gui.main_window import MainWindow
    from etlayout.io.export import export_study
    root = Path(args.output).resolve()
    root.mkdir(parents=True, exist_ok=True)
    application = QApplication([])
    application.setStyle("Fusion")
    first = make_example("E1", root / "examples")
    second = make_example("E2", root / "examples")
    window = MainWindow(first, database_path=root / "materials.sqlite3", recover=False)
    window.recovery_path = root / "recovery.etlayout"
    window.apply_theme(False)
    window.show()
    errors = []
    guard = QTimer(window)
    def check_messages():
        dialog = QApplication.activeModalWidget()
        if isinstance(dialog, QMessageBox):
            errors.append(dialog.text())
            dialog.reject()
    guard.timeout.connect(check_messages)
    guard.start(100)
    def wait():
        started = time.monotonic()
        while window.job or window.prepare_job or window.hierarchy_jobs:
            assert time.monotonic() - started < 120
            QTest.qWait(35)
        assert not errors, errors
    try:
        wait()
        assert window.geometry is not None, window.console.toPlainText()
        window.start_job("solve")
        wait()
        window.field_combo.setCurrentIndex(window.field_combo.findData("potential"))
        window.on_line_drawn("profile", [[1e-6, 5e-6], [99e-6, 5e-6]])
        def configure_refinement():
            dialog = QApplication.activeModalWidget()
            dialog.findChild(QLineEdit, "mesh_study_sizes").setText("10, 5")
            dialog.findChild(QDialogButtonBox).button(QDialogButtonBox.StandardButton.Ok).click()
        QTimer.singleShot(80, configure_refinement)
        window.run_convergence_dialog()
        wait()
        assert len(window.studies) == 2
        metrics = window.studies[-1].result.metrics
        assert metrics["profile_0_common_samples"] > 395
        assert metrics["profile_0_relative_l2_change"] < .01
        export_study(window.studies, root / "mesh-study", "mesh_max_size_m")
        metric_index = window.study_panel.metric_combo.findData("profile_0_relative_l2_change")
        assert metric_index >= 0
        window.study_panel.metric_combo.setCurrentIndex(metric_index)
        QTest.qWait(100)
        window.grab().save(str(root / "mesh-profile-convergence.png"))
        window.analysis_tabs.setCurrentWidget(window.profiles)
        QTest.qWait(100)
        window.grab().save(str(root / "converged-profiles.png"))
        window.start_job("compare", [first, second])
        wait()
        assert len(window.studies) == 2 and all(row.result and row.result.converged for row in window.studies)
        limits = []
        def capture_comparison():
            dialog = QApplication.activeModalWidget()
            assert isinstance(dialog, MapComparisonDialog)
            dialog.field.setCurrentIndex(dialog.field.findData("current_density"))
            dialog.update_maps()
            dialog.grab().save(str(root / "side-by-side-dialog.png"))
            dialog.export_to(root / "side-by-side-current.svg")
            dialog.export_to(root / "side-by-side-current.png")
            limits.extend(dialog.limits)
            dialog.accept()
        QTimer.singleShot(600, capture_comparison)
        window.compare_maps_dialog()
        assert limits and not errors
        receipt = {"platform": application.platformName(), "mesh_profile_common_samples": metrics["profile_0_common_samples"],
                   "mesh_profile_relative_l2_change": metrics["profile_0_relative_l2_change"],
                   "side_by_side_current_density_limits_A_per_m2": limits,
                   "designs": [row.name for row in window.studies], "completed": True, "failures": errors}
        (root / "verification.json").write_text(json.dumps(receipt, indent=2), encoding="utf-8")
        print(json.dumps(receipt, indent=2))
    finally:
        if window.job:
            window.cancel_job()
        window.cancel_geometry()
        window.dirty = False
        window.close()
        application.processEvents()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
