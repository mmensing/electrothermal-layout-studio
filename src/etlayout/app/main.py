# SPDX-License-Identifier: MPL-2.0
"""Desktop entry point; safe under Windows multiprocessing and PyInstaller."""
from __future__ import annotations

import argparse
import logging
import multiprocessing
import sys
import tempfile
import time
from pathlib import Path


def _close_smoke_window(window, application):
    """Stop only this QA window's workers before releasing its owned local data."""
    from PySide6.QtCore import QCoreApplication, QEvent

    from etlayout.gui.jobs import ComputeJob

    window.autosave_timer.stop()
    window.cancel_job()
    window.cancel_geometry()
    if getattr(window, "project_opening", None) is not None:
        window.cancel_catalog()
        window.history_workflow.cancel()
        deadline = time.monotonic() + 30.
        while window.history_opener.jobs or window.history_workflow.loader.jobs:
            application.processEvents()
            if time.monotonic() > deadline:
                raise RuntimeError("Smoke archive worker did not stop; retaining QA files")
            time.sleep(.01)
    save_job = getattr(window, "save_job", None)
    if save_job is not None:
        window.cancel_saving()
        if not save_job.wait(30000):
            raise RuntimeError("Smoke save worker did not stop; retaining QA files")
        application.processEvents()
    for job in window.findChildren(ComputeJob):
        job.cancel()
        job.cleanup()
        if job.process.pid is not None:
            job.process.join(timeout=2.)
            if job.process.is_alive():
                raise RuntimeError("Smoke computation did not stop; retaining QA files")
        if job._receiver is not None:
            job._receiver.join(timeout=2.)
            if job._receiver.is_alive():
                raise RuntimeError("Smoke receiver did not stop; retaining QA files")
    window.guided.discard_draft()
    window.dirty = False
    if window.inspection_context:
        window.inspection_context["dirty"] = False
    if not window.close():
        raise RuntimeError("Smoke window refused to close; retaining QA files")
    window.settings.sync()
    window.deleteLater()
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    application.processEvents()


def main(argv=None):
    multiprocessing.freeze_support()
    parser = argparse.ArgumentParser(description="Electrothermal Layout Studio desktop")
    parser.add_argument("project", nargs="?", help="Project archive (.etl/.etlayout)")
    from etlayout.cli.resources import policy_from_args, resource_arguments
    resource_arguments(parser)
    parser.add_argument("--no-recovery", action="store_true")
    parser.add_argument("--smoke-test", action="store_true", help="Start Qt, render a frame, and exit")
    parser.add_argument("--screenshot", help="Save the rendered window to PNG before smoke-test exit")
    parser.add_argument("--editor-smoke-output", help=argparse.SUPPRESS)
    parser.add_argument("--workflow-smoke-output", help=argparse.SUPPRESS)
    args = parser.parse_args(argv)
    if args.editor_smoke_output and not (args.smoke_test and args.project):
        parser.error("--editor-smoke-output requires --smoke-test and an E1 project fixture")
    if args.workflow_smoke_output and not (args.smoke_test and args.project):
        parser.error("--workflow-smoke-output requires --smoke-test and a solved E1 project fixture")
    if args.workflow_smoke_output and args.editor_smoke_output:
        parser.error("Select one release smoke workflow per process")
    from PySide6.QtCore import QTimer
    from PySide6.QtWidgets import QApplication

    from etlayout.gui.main_window import MainWindow
    from etlayout.io.project import load_project, load_run_history
    editor_output = Path(args.editor_smoke_output).resolve() if args.editor_smoke_output else None
    workflow_output = Path(args.workflow_smoke_output).resolve() if args.workflow_smoke_output else None
    # delete=False prevents finalization from removing files under a worker after
    # abnormal cleanup. Successful owned shutdown explicitly removes this folder.
    previous_tempdir = tempfile.tempdir
    temporary = tempfile.TemporaryDirectory(prefix="etlayout-smoke-", delete=False) if args.smoke_test and not (editor_output or workflow_output) else None
    qa_root = editor_output or workflow_output or (Path(temporary.name) if temporary else None)
    aliases = None
    window = application = None
    construction_started = False
    safe_cleanup = False
    try:
        if qa_root:
            import etlayout.gui.layout_editor as layout_editor
            import etlayout.gui.main_window as main_window
            from etlayout.gui.editor_smoke import acceptance_settings_factory, acceptance_standard_paths

            aliases = (main_window.QSettings, layout_editor.QSettings,
                       main_window.QStandardPaths, layout_editor.QStandardPaths)
            factory = acceptance_settings_factory(qa_root)
            main_window.QSettings = factory
            layout_editor.QSettings = factory
            paths = acceptance_standard_paths(qa_root)
            main_window.QStandardPaths = paths
            layout_editor.QStandardPaths = paths
            # The standard embedded-layout reader uses tempfile.gettempdir().
            # Scope that cache to owned QA data for the lifetime of this mode.
            cache = qa_root / "cache"
            cache.mkdir(parents=True, exist_ok=True)
            tempfile.tempdir = str(cache)
        application = QApplication.instance() or QApplication(sys.argv[:1])
        application.setApplicationName("Electrothermal Layout Studio")
        application.setOrganizationName("etlayout")
        application.setStyle("Fusion")
        logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
        policy = policy_from_args(args)
        project, result = (load_project(args.project, external_policy=policy)
                           if args.project and args.smoke_test and not workflow_output else (None, None))
        construction_started = True
        window = MainWindow(project, result, database_path=qa_root / "materials.sqlite3" if qa_root else None,
                            recover=not args.no_recovery and not args.smoke_test)
        if qa_root:
            window.autosave_timer.stop()
            window.recovery_path = qa_root / "recovery.etl"
            window.guided.layout_editor.cache_dir = qa_root / "owned-layouts"
        if not args.smoke_test:
            geometry = window.settings.value("geometry")
            state = window.settings.value("state")
            if geometry:
                window.restoreGeometry(geometry)
            if state:
                window.restoreState(state)
        if args.project and not args.smoke_test:
            window.open_project(args.project, external_policy=policy)
        if args.project and args.smoke_test and not workflow_output:
            window.path = Path(args.project)
            window.run_history = load_run_history(args.project, external_policy=policy)
            if result:
                window.remember_result(result)
            else:
                window.history_panel.set_results(window.run_history)
        window.show()
        if args.smoke_test:
            deadline = time.monotonic() + 60.
            capture_settled = False
            def finish():
                nonlocal capture_settled
                busy = window.prepare_job or window.job or window.hierarchy_jobs
                if busy and time.monotonic() < deadline:
                    QTimer.singleShot(100, finish)
                    return
                failed = bool(busy or (project and project.layers and window.geometry is None))
                if args.screenshot and not failed and not capture_settled:
                    # Worker completion can queue another inspector rebuild/polish.
                    # Let Qt lay out the resulting widgets before recording pixels.
                    capture_settled = True
                    QTimer.singleShot(150, finish)
                    return
                if failed:
                    print("Desktop smoke test failed: geometry was not ready within 60 seconds.", file=sys.stderr)
                elif workflow_output:
                    from etlayout.gui.release_smoke import run_workflow_smoke
                    failed = not run_workflow_smoke(window, args.project, workflow_output)
                    if failed:
                        print(f"Desktop workflow smoke test failed; see {workflow_output / 'receipt.json'}", file=sys.stderr)
                elif editor_output:
                    from etlayout.gui.editor_smoke import run_editor_smoke
                    failed = not run_editor_smoke(window, editor_output)
                    if failed:
                        print(f"Desktop editor smoke test failed; see {editor_output / 'receipt.json'}", file=sys.stderr)
                if args.screenshot:
                    Path(args.screenshot).parent.mkdir(parents=True, exist_ok=True)
                    window.grab().save(args.screenshot)
                window.dirty = False
                application.exit(1 if failed else 0)
            QTimer.singleShot(250, finish)
        return application.exec()
    finally:
        try:
            if args.smoke_test and window is not None:
                _close_smoke_window(window, application)
                window = None
                # MaterialDatabase retains no connection attribute; collecting
                # unreachable per-operation sqlite connection cycles releases
                # only unreferenced resources, never externally owned handles.
                import gc
                gc.collect()
                safe_cleanup = True
            elif not construction_started:
                safe_cleanup = True
        finally:
            if aliases is not None:
                (main_window.QSettings, layout_editor.QSettings,
                 main_window.QStandardPaths, layout_editor.QStandardPaths) = aliases
            if qa_root:
                tempfile.tempdir = previous_tempdir
            if temporary is not None:
                if safe_cleanup:
                    temporary.cleanup()
                else:
                    print(f"Smoke QA files retained because shutdown was incomplete: {temporary.name}", file=sys.stderr)



if __name__ == "__main__":
    raise SystemExit(main())
