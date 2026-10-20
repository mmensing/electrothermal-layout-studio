# SPDX-License-Identifier: MPL-2.0
"""Repeat real source-desktop ownership/workers/history/save/recovery lifecycles.

This diagnostic journey does not certify a fix for an intermittent native crash.
Receipts and native faulthandler logs are generated artifacts, never source data.
"""
from __future__ import annotations

import argparse
import faulthandler
import gc
import hashlib
import json
import multiprocessing
import os
import platform
import subprocess
import time
from copy import deepcopy
from pathlib import Path


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=Path("artifacts/desktop-scientific-lifecycle"))
    parser.add_argument("--offscreen", action="store_true")
    parser.add_argument("--cycles", type=int, default=4)
    args = parser.parse_args(argv)
    if not 1 <= args.cycles <= 100:
        parser.error("--cycles must be between 1 and 100")
    if args.offscreen:
        os.environ["QT_QPA_PLATFORM"] = "offscreen"
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=True)
    fault_log = (output / "native-faults.log").open("w", encoding="utf-8")
    faulthandler.enable(file=fault_log, all_threads=True)
    faulthandler.dump_traceback_later(120, repeat=True, file=fault_log)

    import psutil
    import shiboken6
    from desktop_acceptance_support import (
        isolate_acceptance_settings,
        prepare_desktop_fonts,
        wait_desktop_events,
    )
    from PySide6.QtCore import QCoreApplication, QEvent, QTimer, qVersion
    from PySide6.QtWidgets import QApplication, QMessageBox

    from etlayout.examples import make_example
    from etlayout.gui.jobs import ComputeJob
    from etlayout.gui.main_window import MainWindow
    from etlayout.io.project import load_project, save_json
    from etlayout.simulation.runner import run_project

    isolate_acceptance_settings(output)
    app = QApplication.instance() or QApplication([])
    prepare_desktop_fonts(app, output)
    process = psutil.Process()
    peak_sampled_rss = 0
    started = time.perf_counter()
    source_root = Path(__file__).resolve().parents[1]
    source_digest = hashlib.sha256()
    for source in [*sorted((source_root / "src").rglob("*.py")), Path(__file__).resolve()]:
        source_digest.update(source.relative_to(source_root).as_posix().encode() + b"\0" + source.read_bytes() + b"\0")
    receipt = {"version": 1, "source_tree_sha256": source_digest.hexdigest(), "revision": subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip(),
        "python": platform.python_version(), "platform": platform.platform(), "qt": qVersion(),
        "shiboken": shiboken6.__version__, "qpa": app.platformName(), "cpu_count": psutil.cpu_count(),
        "cycles_requested": args.cycles, "cycles_completed": 0, "checks": [], "status": "running",
        "scope": "Actual source Qt application and real subprocess/thread/archive operations",
        "limitations": "No frozen-build or clean-machine certification; no claim that absence of a crash proves its cause or a fix."}
    window = None
    phase = "initialization"

    def sample_resources():
        nonlocal peak_sampled_rss
        owned = [process, *process.children(recursive=True)]
        resident = 0
        for child in owned:
            try:
                resident += child.memory_info().rss
            except psutil.Error:
                pass
        peak_sampled_rss = max(peak_sampled_rss, resident)

    def record(name, **data):
        nonlocal phase
        phase = name
        sample_resources()
        faulthandler.cancel_dump_traceback_later()
        faulthandler.dump_traceback_later(120, repeat=True, file=fault_log)
        receipt["checks"].append({"name": name, "elapsed_seconds": time.perf_counter() - started, **data})
        receipt.update(peak_sampled_process_tree_rss=peak_sampled_rss, last_phase=phase)
        (output / "receipt.json").write_text(json.dumps(receipt, indent=2), encoding="utf-8")
        print(name, flush=True)

    def idle(timeout=90.):
        deadline = time.monotonic() + timeout
        while (window.job or window.prepare_job or window.hierarchy_jobs or window.save_job or window._save_queue
               or window.history_opener.jobs or window.history_workflow.loader.jobs
               or any(not getattr(job, "_cleanup_complete", job.done and not job.process.is_alive()
                                   and not (job._receiver and job._receiver.is_alive()))
                      for job in window.findChildren(ComputeJob))):
            modal = app.activeModalWidget()
            if modal is not None:
                modal.grab().save(str(output / "unexpected-dialog.png"))
                raise AssertionError(f"Unexpected modal during {phase}: {modal.windowTitle()}")
            if time.monotonic() >= deadline:
                raise TimeoutError(f"Owned workers did not stop during {phase}")
            sample_resources()
            wait_desktop_events(30)
        wait_desktop_events(40)
        if window.guided.last_error:
            raise AssertionError(window.guided.last_error_detail or window.guided.last_error)

    try:
        project = make_example("E1", output / "layouts")
        transient_project = make_example("E17", output / "layouts")
        transient = run_project(transient_project)
        assert transient.converged and transient.transient.completed
        for cycle in range(args.cycles):
            phase = f"cycle {cycle + 1}: open"
            window = MainWindow(deepcopy(project), database_path=output / "materials.sqlite3", recover=False)
            # Observe and dismiss actual error modals so a failed asynchronous
            # operation yields a bounded receipt instead of nesting forever.
            original_error = window.error
            def observed_error(title, error, original=original_error):
                receipt.setdefault("reported_errors", []).append({"title": title, "detail": str(error)})
                def dismiss():
                    modal = app.activeModalWidget()
                    if isinstance(modal, QMessageBox):
                        modal.grab().save(str(output / "reported-error.png"))
                        modal.accept()
                QTimer.singleShot(0, dismiss)
                original(title, error)
            window.error = observed_error
            window.autosave_timer.stop()
            window.resize(1280, 850)
            window.show()
            idle()
            record(phase)
            window.run()
            idle()
            assert window.result and window.result.converged
            first = window.result.configuration_hash
            window.project.terminals[-1].value *= 1.1
            window.run()
            idle()
            assert window.result.converged and window.result.configuration_hash != first
            assert len(window.run_history) >= 2
            record(f"cycle {cycle + 1}: two real solves", native_worker_children=len(window.findChildren(ComputeJob)))

            window.dirty = True
            window.autosave()
            idle()
            assert window.recovery_path.is_file()
            record(f"cycle {cycle + 1}: durable recovery snapshot")
            legacy = output / f"cycle-{cycle + 1}-legacy.etlayout"
            assert window._queue_save(legacy, archive_format=2)
            idle()
            target = output / f"cycle-{cycle + 1}.etlayout"
            window.path = target
            window.dirty = True
            assert window._queue_save(target, archive_format=3)
            idle()
            _, saved = load_project(target)
            assert saved and saved.converged
            window.history_lazy_enabled = False
            window.open_project(target)
            idle()
            assert window.result and window.result.converged
            assert window.history_workflow.store is None and len(window.run_history) >= 2
            record(f"cycle {cycle + 1}: default eager schema-3 open", current_hash=window.result.configuration_hash)
            window.open_project(legacy)
            idle()
            assert window.result and window.result.converged and window.history_workflow.store is None
            assert len(window.run_history) >= 2
            record(f"cycle {cycle + 1}: legacy eager schema-2 open")
            metadata = output / f"cycle-{cycle + 1}.json"
            metadata_project = deepcopy(window.project)
            metadata_layout = output / f"cycle-{cycle + 1}.gds"
            metadata_layout.write_bytes(Path(metadata_project.layout.path).read_bytes())
            metadata_project.layout.path = str(metadata_layout)
            save_json(metadata_project, metadata)
            window.open_project(metadata)
            idle()
            assert window.path == metadata and window.result is None
            record(f"cycle {cycle + 1}: metadata JSON open")
            window.history_lazy_enabled = True
            window.open_project(target)
            idle()
            store = window.history_workflow.store
            assert store and store.history
            for _ in range(3):
                window.inspect_result(store.history[0])
                idle()
                assert window.inspection_context is not None
                window.return_to_project()
                idle()
            record(f"cycle {cycle + 1}: repeated saved-history inspection and return")
            if cycle == args.cycles - 1:
                window.guided.navigate("results", force=True)
                window.grab().save(str(output / "lifecycle-history.png"))

            window.set_project(deepcopy(transient_project), deepcopy(transient))
            idle()
            window.select_snapshot(0)
            window.select_snapshot(len(transient.transient.snapshots) - 1)
            assert window.result.converged
            window.set_project(deepcopy(project))
            idle()
            record(f"cycle {cycle + 1}: project switch and transient frames")
            window.run()
            assert window.job and window.job.process.is_alive()
            pid = window.job.process.pid
            answered = []
            def approve_owned_close(acknowledged=answered):
                modal = app.activeModalWidget()
                if isinstance(modal, QMessageBox) and modal.windowTitle() == "Computation running":
                    modal.done(QMessageBox.StandardButton.Yes)
                    acknowledged.append(True)
                elif not acknowledged:
                    QTimer.singleShot(10, approve_owned_close)
            QTimer.singleShot(10, approve_owned_close)
            assert not window.close()  # Existing cancellation safeguard requires a second close.
            idle()
            assert answered and not psutil.pid_exists(pid)
            record(f"cycle {cycle + 1}: cancel and close during actual subprocess work")
            window.dirty = False
            if cycle == args.cycles - 1:
                window.grab().save(str(output / "lifecycle-final.png"))
            assert window.close()
            window.deleteLater()
            QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
            app.processEvents()
            assert not shiboken6.isValid(window)
            window = None
            collected = gc.collect()
            receipt["cycles_completed"] = cycle + 1
            record(f"cycle {cycle + 1}: native deletion and Python GC", collected=collected)
        receipt["status"] = "passed"
    except BaseException as error:
        receipt.update(status="failed", error=f"{type(error).__name__}: {error}", last_phase=phase)
        if window is not None and shiboken6.isValid(window):
            window.grab().save(str(output / "failure.png"))
        raise
    finally:
        # Failure diagnostics must not create a second orphan/shutdown failure.
        if window is not None and shiboken6.isValid(window):
            modal = app.activeModalWidget()
            if modal is not None:
                modal.reject()
            window.cancel_saving()
            window.cancel_geometry()
            window.history_workflow.cancel()
            window.history_opener.cancel()
            if window.job:
                window.cancel_job()
            deadline = time.monotonic() + 30
            while (window.job or window.save_job or window._save_queue or window.history_opener.jobs
                   or window.history_workflow.loader.jobs) and time.monotonic() < deadline:
                wait_desktop_events(30)
            window.dirty = False
            if window.inspection_context:
                window.inspection_context["dirty"] = False
            window.close()
            window.deleteLater()
            QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
            app.processEvents()
        receipt["elapsed_seconds"] = time.perf_counter() - started
        receipt["peak_sampled_process_tree_rss"] = peak_sampled_rss
        receipt["memory_sampling"] = "Sum of owned parent/descendant RSS sampled during event-loop waits and phase boundaries, not an exact process-tree high-water mark"
        (output / "receipt.json").write_text(json.dumps(receipt, indent=2), encoding="utf-8")
        faulthandler.cancel_dump_traceback_later()
        faulthandler.disable()
        fault_log.close()
    return 0


if __name__ == "__main__":
    multiprocessing.freeze_support()
    raise SystemExit(main())
