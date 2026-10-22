# SPDX-License-Identifier: MPL-2.0
"""Bounded real workflow verification used only by explicit release smoke mode."""
from __future__ import annotations

import gc
import json
import sys
import time
from pathlib import Path

from PySide6.QtCore import QCoreApplication, QEvent, QEventLoop, QSettings, Qt, QTimer
from PySide6.QtGui import QFont, QRawFont
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QDialog, QDialogButtonBox, QLineEdit, QMessageBox
from shiboken6 import isValid

from etlayout.gui.example_library import ExampleLibraryDialog
from etlayout.io.project import physics_hash
from etlayout.provenance import build_info, result_build_info


def _check_two_window_recovery(window, project_path, output, check, settle, receipt):
    """Recover two real inactive sessions, then use each window's owned readers."""
    from etlayout.app.main import _close_smoke_window
    from etlayout.gui import main_window
    from etlayout.gui.recovery import RecoveryDialog
    from etlayout.io.recovery import RecoveryStore

    qa_root = Path(window.settings.fileName()).resolve().parent.parent
    check("Recovery QA storage is isolated before constructing another window",
          window.settings.format() == QSettings.Format.IniFormat
          and not window.settings.fallbacksEnabled() and output.is_relative_to(qa_root)
          and Path(window.database.path).resolve().is_relative_to(qa_root)
          and window.recovery_store is not None
          and window.recovery_store.root.resolve().is_relative_to(qa_root)
          and not window._recovered_windows)
    gc_state = (gc.isenabled(), gc.get_threshold(), gc.get_debug())
    check("Automatic cyclic GC is enabled for two-window recovery", gc_state[0])
    original_store = window.recovery_store
    store = RecoveryStore(output / "two-window-recovery")
    sessions, dialogs, callback_errors = [], [], []
    actual_database = main_window.MaterialDatabase

    def local_database(path=None):
        # Recovery creates subsequent windows with database_path=None. Select an
        # actual QA SQLite database without touching the user's platformdirs data.
        return actual_database(path if path is not None else output / "recovery-materials.sqlite3")

    try:
        for revision in (1, 2):
            session = RecoveryStore(store.root)
            sessions.append(session)
            session.snapshot(window.project, window.result, history=window.run_history,
                             revision=revision, original_path=project_path)
            session.close()  # A real released OS lease makes this session recoverable.
        check("Two inactive recovery sessions retain real solved snapshots", len(store.entries()) == 2)
        window.recovery_store = store

        def select_recovery():
            dialog = QApplication.activeModalWidget()
            try:
                check("Actual recovery chooser lists both interrupted sessions",
                      isinstance(dialog, RecoveryDialog) and dialog.table.rowCount() == 2)
                dialogs.append(dialog)
                dialog.table.selectAll()
                check("Both recovery rows are selected", len(dialog.selected()) == 2)
                QTest.mouseClick(dialog.actions["Recover selected"], Qt.MouseButton.LeftButton)
            except Exception as error:
                callback_errors.append(error)
                if dialog is not None:
                    dialog.reject()

        main_window.MaterialDatabase = local_database
        try:
            QTimer.singleShot(0, select_recovery)
            window.offer_recovery()
        finally:
            main_window.MaterialDatabase = actual_database
        if callback_errors:
            raise callback_errors[0]
        recovered = list(window._recovered_windows)
        check("Recover selected creates two actual MainWindows", len(recovered) == 2
              and recovered[0] is window and recovered[1] is not window)
        other = recovered[1]
        other.autosave_timer.stop()
        other.guided.layout_editor.cache_dir = output / "recovery-owned-layouts"
        settle(*recovered)
        check("Both recovered windows use only QA settings, databases and recovery stores",
              all(item.settings.format() == QSettings.Format.IniFormat
                  and not item.settings.fallbacksEnabled()
                  and Path(item.settings.fileName()).resolve().is_relative_to(qa_root)
                  and Path(item.database.path).resolve().is_relative_to(qa_root)
                  and item.recovery_store.root.resolve().is_relative_to(qa_root)
                  for item in recovered))
        check("Recovery retains independent actual solved projects and fields",
              window.project is not other.project and window.result is not other.result
              and all(item.result is not None and item.result.converged
                      and abs(item.result.metrics["effective_resistance"] / .084 - 1) < 1e-8
                      and item.dirty for item in recovered))
        workers = []
        for item in recovered:
            def discard_recovered_draft():
                dialog = QApplication.activeModalWidget()
                try:
                    check("Recovered edits receive the actual discard guard",
                          isinstance(dialog, QMessageBox) and dialog.windowTitle() == "Unsaved project")
                    QTest.mouseClick(dialog.button(QMessageBox.StandardButton.Discard), Qt.MouseButton.LeftButton)
                except Exception as error:
                    callback_errors.append(error)
                    if dialog is not None:
                        dialog.reject()
            QTimer.singleShot(0, discard_recovered_draft)
            item.open_project(project_path)
            if callback_errors:
                raise callback_errors[0]
            check("Recovered window queues its own real opening worker", bool(item.history_opener.jobs))
            workers.append(item.history_opener.jobs[0])
        check("Recovered windows have independent asynchronous readers", workers[0] is not workers[1]
              and window.history_opener is not other.history_opener)
        settle(*recovered)
        check("Both asynchronous opens committed the requested clean solved project",
              all(item.path is not None and item.path.resolve() == Path(project_path).resolve()
                  and not item.dirty and not item.guided.last_error
                  and item.result is not None and item.result.converged
                  and result_build_info(item.result).get("implementation_sha256")
                      == receipt["build"]["implementation_sha256"]
                  for item in recovered))
        for item in recovered:
            working, solved = item.project, item.result
            peer = other if item is window else window
            peer_context = (peer.project, peer.result, peer.inspection_context)
            item.inspect_result(item.run_history[0])
            settle(*recovered)
            check("Recovered window inspects real history without changing its peer",
                  bool(item.inspection_context) and item.result is not None
                  and (peer.project, peer.result, peer.inspection_context) == peer_context)
            item.return_to_project()
            settle(*recovered)
            check("Recovered window returns to its own working setup and result",
                  item.project is working and item.result is solved and not item.inspection_context
                  and (peer.project, peer.result, peer.inspection_context) == peer_context)
        check("Both recovery readers stop before native window closure",
              all(not item.history_opener.jobs and not item.history_workflow.loader.jobs
                  and item.prepare_job is None and item.job is None and not item.hierarchy_jobs
                  for item in recovered))
        receipt["recovery"] = {"windows": 2, "inactive_sessions": 2, "opening_workers": 2,
            "history_returns": 2, "storage": "Explicit QA INI, SQLite and recovery directories",
            "session_fixture": "Real snapshots and released OS session leases"}
    finally:
        main_window.MaterialDatabase = actual_database
        window.recovery_store = original_store
        for item in reversed(window._recovered_windows):
            if item is not window and isValid(item):
                _close_smoke_window(item, QApplication.instance())
                check("Recovered secondary window completed native deferred deletion", not isValid(item))
        window._recovered_windows.clear()
        for dialog in dialogs:
            if isValid(dialog):
                dialog.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        for session in sessions:
            session.close()
        store.close()
    check("Automatic cyclic GC remains enabled and unchanged after recovery",
          (gc.isenabled(), gc.get_threshold(), gc.get_debug()) == gc_state)


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

    def settle(*windows):
        windows = windows or (window,)
        while True:
            QApplication.processEvents(QEventLoop.ProcessEventsFlag.AllEvents, 50)
            if time.monotonic() - started > timeout:
                raise TimeoutError("Workflow release smoke exceeded its finite time budget")
            if not any(item.prepare_job or item.job or item.hierarchy_jobs
                       or item.history_opener.jobs or item.history_workflow.loader.jobs
                       or item.save_job or item._save_queue or item.project_opening.pending
                       for item in windows):
                return
            time.sleep(.01)

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

    def reveal_verification(context):
        QApplication.processEvents(QEventLoop.ProcessEventsFlag.AllEvents, 50)
        toggle = window.guided.verification_toggle
        window.guided.page_scroll.ensureWidgetVisible(toggle)
        check(context + " verification disclosure is visible and usable",
              toggle.isVisible() and toggle.isEnabled())
        if not toggle.isChecked():
            QTest.mouseClick(toggle, Qt.MouseButton.LeftButton)
        QApplication.processEvents(QEventLoop.ProcessEventsFlag.AllEvents, 50)
        check(context + " verification details open through the real disclosure",
              toggle.isChecked() and window.guided.verify_mesh.isVisible())

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
        check("Verification details initially leave result headlines uncluttered",
              not window.guided.verification_toggle.isChecked())
        reveal_verification("Working setup")
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
        check("Mesh action completed an actual study dialog",
              "Mesh study retains three editable source mesh levels" in receipt["checks"])
        check("Cancelled study preserves result, fields and working physics without solving",
              window.result is result and window.job is None
              and window.capture_view_state() == original_view and physics_hash(window.project) == fingerprint)
        window.inspect_result(result)
        settle()
        window.guided.navigate("results", force=True)
        reveal_verification("Historical")
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
        _check_two_window_recovery(window, project_path, output, check, settle, receipt)
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
