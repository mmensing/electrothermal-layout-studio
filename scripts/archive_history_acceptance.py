# SPDX-License-Identifier: MPL-2.0
"""Real GUI archive/history workflows, with owned preferences and readable captures.

Application preferences and recovery use owned paths. File-chooser input is
injected through a scoped adapter: Qt file dialogs have separate native
persistence. This is source desktop acceptance, not frozen-build or physical
mixed-monitor certification.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import multiprocessing
import os
import platform
import subprocess
import time
from pathlib import Path


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=Path("artifacts/desktop-archive-history"))
    parser.add_argument("--offscreen", action="store_true")
    parser.add_argument("--scale", type=float, default=1.)
    args = parser.parse_args(argv)
    os.environ["QT_SCALE_FACTOR"] = str(args.scale)
    if args.offscreen:
        os.environ["QT_QPA_PLATFORM"] = "offscreen"
    from desktop_acceptance_support import (
        isolate_acceptance_settings,
        prepare_desktop_fonts,
        wait_desktop_events,
    )
    from PySide6.QtCore import QCoreApplication, QEvent, QTimer
    from PySide6.QtTest import QTest
    from PySide6.QtWidgets import QApplication, QFileDialog, QMessageBox

    from etlayout.examples import make_example
    from etlayout.gui.history_loading import HistorySettingsDialog
    from etlayout.gui.main_window import MainWindow
    from etlayout.io.project import load_project, load_run_history, physics_hash, save_project

    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=True)
    isolate_acceptance_settings(output)
    app = QApplication.instance() or QApplication([])
    app.setStyle("Fusion")
    prepare_desktop_fonts(app, output)
    root = Path(__file__).resolve().parents[1]
    source_digest = hashlib.sha256()
    for source in sorted((root / "src").rglob("*.py")) + [Path(__file__).resolve()]:
        source_digest.update(source.relative_to(root).as_posix().encode() + b"\0" + source.read_bytes() + b"\0")
    receipt = {"revision": subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip(),
        "source_tree_sha256": source_digest.hexdigest(), "platform": platform.platform(),
        "qpa": app.platformName(), "qt_scale_factor": args.scale, "checks": [], "captures": [],
        "file_chooser": "Process-scoped user-input adapter; native file-dialog persistence is not exercised",
        "limitations": "Source widgets and Qt application scaling; no frozen-build, clean-machine, physical DPI or mixed-monitor certification."}
    started = time.monotonic()
    window = None
    expected_modal = None
    monitor_error = None
    phase = "initializing"

    def record(name, **data):
        nonlocal phase
        phase = name
        receipt["checks"].append({"name": name, "elapsed_seconds": time.monotonic() - started, **data})
        (output / "receipt.json").write_text(json.dumps(receipt, indent=2), encoding="utf-8")
        print(name, flush=True)

    def monitor():
        nonlocal monitor_error
        modal = app.activeModalWidget()
        expired = time.monotonic() - started > 600
        unexpected = modal is not None and not (expected_modal and isinstance(modal, expected_modal))
        if not (expired or unexpected) or monitor_error:
            return
        monitor_error = {"phase": phase, "expired": expired,
            "dialog": type(modal).__name__ if modal else "", "title": modal.windowTitle() if modal else "",
            "message": modal.text() if isinstance(modal, QMessageBox) else ""}
        receipt["unexpected_failure"] = monitor_error
        if modal:
            modal.grab().save(str(output / "unexpected-dialog.png"))
            modal.reject()  # Never approve an unexpected transition.

    def check():
        if monitor_error:
            raise AssertionError(monitor_error)

    def idle(timeout=180., *, verify=True):
        deadline = time.monotonic() + timeout
        while (window.job or window.prepare_job or window.hierarchy_jobs or window.save_job or window._save_queue
               or window.history_opener.jobs or window.history_workflow.loader.jobs
               or (window.guided.power and window.guided.power.job)):
            if verify:
                check()
            if time.monotonic() > deadline:
                raise TimeoutError("Archive/history workers did not finish: " + window.console.toPlainText())
            wait_desktop_events(40)
        wait_desktop_events(100)
        if verify:
            check()
            assert not window.guided.last_error, window.guided.last_error_detail

    def capture(name):
        wait_desktop_events(100)
        picture = window.grab()
        assert picture.save(str(output / (name + ".png")))
        receipt["captures"].append({"file": name + ".png", "logical_size": [window.width(), window.height()],
            "image_pixels": [picture.width(), picture.height()], "dpr": window.devicePixelRatioF(),
            "canvas_logical": [window.view.width(), window.view.height()],
            "available_screen_logical": [window.screen().availableGeometry().width(), window.screen().availableGeometry().height()]})

    def save_copy(destination):
        # Script user input only; preserve the real menu/save worker/verification.
        # Instantiating QFileDialog would write QtProject's native dialog history
        # independently of the application's explicitly file-backed settings.
        original = QFileDialog.getSaveFileName
        QFileDialog.getSaveFileName = lambda *args, **kwargs: (str(destination), "Electrothermal project (*.etlayout)")
        try:
            window.guided.actions["file.save_v3"].trigger()
        finally:
            QFileDialog.getSaveFileName = original
        idle()
        assert destination.is_file()

    watchdog = QTimer(app)
    watchdog.setInterval(100)
    watchdog.timeout.connect(monitor)
    watchdog.start()
    try:
        project = make_example("E1", output / "inputs")
        project.solver.mode = "electrical"
        window = MainWindow(project, database_path=output / "materials.sqlite3", recover=False)
        window.autosave_timer.stop()
        window.recovery_path = output / "recovery.etlayout"
        window.resize(1280, 800)
        window.show()
        idle()
        window.run()
        idle()
        first = window.result
        assert first and first.converged
        assert abs(first.metrics["resistance"] - .084) < .084 * .005
        record("Configured E1 solved through the GUI", resistance_ohm=first.metrics["resistance"])
        drive = next(terminal for terminal in window.project.terminals if terminal.kind == "voltage")
        window.guided.navigate("conditions")
        window.guided.open_editor("terminals", drive)
        value_editor = window.guided.editor.form.widgets["value"]
        value_editor.setFocus()
        value_editor.selectAll()
        QTest.keyClicks(value_editor, "30 mV")
        window.guided.editor.apply_button.click()
        idle()
        assert next(terminal for terminal in window.project.terminals if terminal.id == drive.id).value == .03
        window.run()
        idle()
        second = window.result
        assert second is not first and second.converged and len(window.run_history) == 2
        assert abs(second.metrics["resistance"] - .084) < .084 * .005
        original = output / "two-operating-points.etlayout"
        save_copy(original)
        record("Two actual E1 operating points saved with shared archive objects", retained=len(window.run_history))

        expected_modal = HistorySettingsDialog
        def accept_settings():
            if monitor_error:
                return
            dialog = app.activeModalWidget()
            if isinstance(dialog, HistorySettingsDialog):
                dialog.enabled.setChecked(True)
                dialog.cache_mib.setValue(256)
                dialog.accept()
            else:
                QTimer.singleShot(20, accept_settings)
        QTimer.singleShot(20, accept_settings)
        window.guided.actions["edit.history"].trigger()
        expected_modal = None
        assert window.history_lazy_enabled
        window.open_project(original)
        idle()
        store = window.history_workflow.store
        assert store and len(store.records) == 3 and store.stats.resident_bytes > 0
        working_project, working_result = window.project, window.result
        working_physics = physics_hash(window.project)
        window.guided.navigate("results")
        window.guided.open_drawer(3)
        record("On-demand opening loads current fields and displays metadata history", records=len(store.records),
               resident_array_bytes=store.stats.resident_bytes, private_copy_bytes=store.stats.private_copy_bytes)
        capture("compact-on-demand-history")
        selected = store.history[0]
        window.inspect_result(selected)
        idle()
        assert window.inspection_context and window.project is not working_project
        assert window.result.configuration_hash == first.configuration_hash
        assert window.inspection_context["project"] is working_project
        window.return_to_project()
        idle()
        assert window.project is working_project and window.result is working_result
        assert physics_hash(window.project) == working_physics
        record("Historical inspection and return preserve the applied working setup")

        transient_project, transient = load_project(root / "tests" / "fixtures" / "legacy_transient_e18.etl")
        mixed = output / "mixed-transient.etlayout"
        save_project(transient_project, mixed, transient, history=[first, second], archive_format=3)
        mixed_hash = hashlib.sha256(mixed.read_bytes()).hexdigest()
        window.open_project(mixed)
        idle()
        store = window.history_workflow.store
        current = store.record(store.current_run_id)
        assert len(current.times) == len(transient.transient.times)
        assert window.result.transient is None and window.time_navigation.slider.value() == len(current.times) - 1
        assert len(window.time_history.trace_times) == len(transient.transient.rows)
        window.time_navigation.slider.setValue(0)
        idle()
        assert window.time_navigation.slider.value() == 0
        assert window.result.metrics["time_s"] == current.times[0]
        middle = len(current.times) // 2
        window.time_navigation.slider.setValue(middle)
        idle()
        window.field_combo.setCurrentIndex(window.field_combo.findData("temperature"))
        window.guided.panels.set_visible("drawer", False)
        window.view.fit_all()
        capture("compact-transient-exact-frame")
        window.guided.canvas_tabs.setCurrentIndex(4)
        capture("compact-transient-step-trace")
        window.guided.canvas_tabs.setCurrentIndex(0)
        record("Metadata step traces and exact stored transient frames loaded on demand", frames=len(current.times),
               selected_index=middle, selected_time_s=current.times[middle], resident_array_bytes=store.stats.resident_bytes)
        complete = output / "complete-after-partial-browse.etlayout"
        save_copy(complete)
        reopened, saved = load_project(complete)
        history = load_run_history(complete)
        assert saved.transient and len(saved.transient.snapshots) == len(transient.transient.snapshots)
        assert saved.transient.times.tolist() == transient.transient.times.tolist()
        assert len(history) == 2
        assert [item.configuration_hash for item in history] == [first.configuration_hash, second.configuration_hash]
        assert physics_hash(reopened) == physics_hash(transient_project)
        assert hashlib.sha256(mixed.read_bytes()).hexdigest() == mixed_hash
        record("Save As after partial browsing preserves every retained run and transient frame", history=len(history),
               frames=len(saved.transient.snapshots), original_source_unchanged=True)
        receipt["status"] = "passed"
    except BaseException as error:
        receipt["status"] = "failed"
        receipt["error"] = f"{type(error).__name__}: {error}"
        if window:
            window.grab().save(str(output / "failure.png"))
            receipt["console"] = window.console.toPlainText()
        raise
    finally:
        expected_modal = None
        if window is not None:
            window.history_workflow.cancel()
            window.cancel_geometry()
            window.cancel_saving()
            if window.job:
                window.cancel_job()
            idle(verify=False)
            window.guided.discard_draft()
            window.dirty = False
            if window.inspection_context:
                window.inspection_context["dirty"] = False
            assert window.close()
            window.deleteLater()
            QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
            app.processEvents()
        watchdog.stop()
        receipt["elapsed_seconds"] = time.monotonic() - started
        (output / "receipt.json").write_text(json.dumps(receipt, indent=2), encoding="utf-8")
    return 0


if __name__ == "__main__":
    multiprocessing.freeze_support()
    raise SystemExit(main())
