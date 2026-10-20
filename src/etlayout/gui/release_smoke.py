# SPDX-License-Identifier: MPL-2.0
"""Bounded real workflow verification used only by explicit release smoke mode."""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

from PySide6.QtCore import QEventLoop, Qt, QTimer
from PySide6.QtGui import QFont, QRawFont
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QDialog, QDialogButtonBox, QLineEdit

from etlayout.gui.example_library import ExampleLibraryDialog
from etlayout.io.project import physics_hash
from etlayout.provenance import build_info, result_build_info


def run_workflow_smoke(window, project_path, output, *, timeout=120.):
    """Open a solved E1 through the default worker and inspect real workflow state.

    This does not change user preferences, fake numerical results or establish
    clean-machine compatibility. The caller owns isolated QA settings and shutdown.
    """
    output = Path(output).resolve()
    output.mkdir(parents=True, exist_ok=True)
    started = time.monotonic()
    receipt = {"status": "failed", "frozen": bool(getattr(sys, "frozen", False)),
               "build": build_info(), "qpa": QApplication.platformName(),
               "checks": [], "captures": [],
               "limitations": "Programmatic native build-host workflow checks; not physical DPI, screen-reader or clean-machine certification."}
    heartbeat = QTimer(window)
    heartbeat.setInterval(20)
    ticks = []
    heartbeat.timeout.connect(lambda: ticks.append(time.monotonic()))
    heartbeat.start()

    def check(name, condition):
        if not condition:
            raise AssertionError(name)
        receipt["checks"].append(name)

    def settle():
        while (window.prepare_job or window.job or window.hierarchy_jobs
               or window.history_opener.jobs or window.history_workflow.loader.jobs
               or getattr(window, "save_job", None)):
            QApplication.processEvents(QEventLoop.ProcessEventsFlag.AllEvents, 50)
            if time.monotonic() - started > timeout:
                raise TimeoutError("Workflow release smoke exceeded its finite time budget")
            time.sleep(.01)
        QApplication.processEvents(QEventLoop.ProcessEventsFlag.AllEvents, 50)

    def capture(name, widget=None):
        widget = window if widget is None else widget
        if widget is window:
            window.resize(1280, 800)
        QApplication.processEvents(QEventLoop.ProcessEventsFlag.AllEvents, 50)
        picture = widget.grab()
        check("Captured " + name, picture.save(str(output / (name + ".png"))))
        receipt["captures"].append({"file": name + ".png",
            "logical_size": [widget.width(), widget.height()],
            "pixels": [picture.width(), picture.height()], "dpr": widget.devicePixelRatioF()})

    try:
        for bold in (False, True):
            font = QFont(QApplication.font())
            font.setBold(bold)
            raw = QRawFont.fromFont(font)
            check("Readable scientific glyphs " + ("bold" if bold else "regular"),
                  raw.isValid() and all(raw.supportsCharacter(ord(c)) for c in "ETLayout0123456789µΩ²Δ°C×−→≤≥"))
        check("Default eager opening selected", not window.history_lazy_enabled)
        window.open_project(project_path)
        check("Project opening queued a worker", bool(window.history_opener.jobs))
        settle()
        check("Default opening restored actual solved E1", window.result is not None
              and window.result.converged and abs(window.result.metrics["effective_resistance"] / .084 - 1) < 1e-8)
        recorded = result_build_info(window.result)
        check("Loaded result keeps exact frozen implementation receipt",
              recorded.get("implementation_sha256") == receipt["build"]["implementation_sha256"]
              and recorded.get("edition") == receipt["build"]["edition"])
        project, result = window.project, window.result
        fingerprint = physics_hash(project)
        window.guided.navigate("results", force=True)
        check("Missing mesh evidence offers a guarded action", window.guided.verify_mesh.isEnabled()
              and "Mesh: not established" in window.guided.result_evidence.text())
        window.guided.page_scroll.ensureWidgetVisible(window.guided.verify_mesh)
        capture("frozen-workflow-results")
        study_errors = []
        original_view = window.capture_view_state()

        def cancel_mesh_study():
            dialog = QApplication.activeModalWidget()
            try:
                check("Mesh action opens the existing study dialog",
                      isinstance(dialog, QDialog) and dialog.windowTitle() == "Mesh convergence study")
                sizes = dialog.findChild(QLineEdit, "mesh_study_sizes")
                check("Mesh study retains three editable source mesh levels",
                      sizes is not None and len(sizes.text().split(",")) == 3)
                capture("frozen-workflow-mesh-study", dialog)
                cancel = dialog.findChild(QDialogButtonBox).button(QDialogButtonBox.StandardButton.Cancel)
                QTest.mouseClick(cancel, Qt.MouseButton.LeftButton)
            except Exception as error:
                study_errors.append(error)
                if dialog is not None:
                    dialog.reject()

        QTimer.singleShot(0, cancel_mesh_study)
        QTest.mouseClick(window.guided.verify_mesh, Qt.MouseButton.LeftButton)
        if study_errors:
            raise study_errors[0]
        check("Cancelled study preserves result, fields and working physics without solving",
              window.result is result and window.job is None
              and window.capture_view_state() == original_view and physics_hash(window.project) == fingerprint)
        window.inspect_result(result)
        settle()
        window.guided.navigate("results", force=True)
        check("Historical inspection disables physical verification", bool(window.inspection_context)
              and not window.guided.verify_mesh.isEnabled())
        historical_view = window.capture_view_state()
        check("Direct historical verification is also rejected without changing context",
              window.guided.verify_result("mesh") is False and window.job is None
              and window.capture_view_state() == historical_view and window.result is result)
        window.return_to_project()
        settle()
        check("Returning restores working context", window.project is project and window.result is result
              and not window.inspection_context and physics_hash(window.project) == fingerprint)
        browser_errors = []

        def choose_compact_example():
            dialog = QApplication.activeModalWidget()
            try:
                check("Native example chooser opened", isinstance(dialog, ExampleLibraryDialog))
                dialog.select_example("E21")
                dialog.preset.setCurrentIndex(dialog.preset.findData("compact"))
                dialog.resize(680, 480)
                QApplication.processEvents(QEventLoop.ProcessEventsFlag.AllEvents, 50)
                check("Browser reads packaged reference metadata and requested compact inputs",
                      dialog.open_button.isEnabled() and dialog.selection()["preset"] == "compact"
                      and "4 × 60" in dialog.inputs.toPlainText()
                      and dialog.detail_tabs.currentWidget() is dialog.inputs)
                capture("frozen-workflow-browser", dialog)
                QTest.mouseClick(dialog.open_button, Qt.MouseButton.LeftButton)
            except Exception as error:
                browser_errors.append(error)
                if dialog is not None:
                    dialog.reject()

        QTimer.singleShot(0, choose_compact_example)
        window.browse_example_library()
        if browser_errors:
            raise browser_errors[0]
        settle()
        check("Opening an example leads to Mesh and Checks without solving", window.guided.stage == "mesh"
              and window.result is None and window.geometry is not None and window.job is None)
        capture("frozen-workflow-example-ready")
        check("Qt event loop stayed active during the journey", len(ticks) > 0)
        receipt["heartbeat_ticks"] = len(ticks)
        receipt["status"] = "passed"
    except Exception as error:
        receipt["error"] = str(error)
    finally:
        heartbeat.stop()
        receipt["elapsed_seconds"] = time.monotonic() - started
        (output / "receipt.json").write_text(json.dumps(receipt, indent=2) + "\n", encoding="utf-8")
    return receipt["status"] == "passed"
