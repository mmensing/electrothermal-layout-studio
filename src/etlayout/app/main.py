# SPDX-License-Identifier: MPL-2.0
"""Desktop entry point; safe under Windows multiprocessing and PyInstaller."""
from __future__ import annotations

import argparse
import logging
import multiprocessing
import sys
import time
from pathlib import Path


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
    args = parser.parse_args(argv)
    if args.editor_smoke_output and not (args.smoke_test and args.project):
        parser.error("--editor-smoke-output requires --smoke-test and an E1 project fixture")
    from PySide6.QtCore import QSettings, QTimer
    from PySide6.QtWidgets import QApplication

    from etlayout.gui.main_window import MainWindow
    from etlayout.io.project import load_project, load_run_history
    editor_output = Path(args.editor_smoke_output).resolve() if args.editor_smoke_output else None
    if editor_output:
        editor_output.mkdir(parents=True, exist_ok=True)
        QSettings.setDefaultFormat(QSettings.Format.IniFormat)
        QSettings.setPath(QSettings.Format.IniFormat, QSettings.Scope.UserScope, str(editor_output / "settings"))
    application = QApplication.instance() or QApplication(sys.argv[:1])
    application.setApplicationName("Electrothermal Layout Studio")
    application.setOrganizationName("etlayout")
    application.setStyle("Fusion")
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    policy = policy_from_args(args)
    project, result = (load_project(args.project, external_policy=policy)
                       if args.project and args.smoke_test else (None, None))
    window = MainWindow(project, result, database_path=editor_output / "materials.sqlite3" if editor_output else None,
                        recover=not args.no_recovery and not args.smoke_test)
    if editor_output:
        window.autosave_timer.stop()
        window.recovery_path = editor_output / "recovery.etl"
        window.guided.layout_editor.cache_dir = editor_output / "owned-layouts"
    if not args.smoke_test:
        geometry = window.settings.value("geometry")
        state = window.settings.value("state")
        if geometry:
            window.restoreGeometry(geometry)
        if state:
            window.restoreState(state)
    if args.project and not args.smoke_test:
        window.open_project(args.project, external_policy=policy)
    if args.project and args.smoke_test:
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
            elif editor_output:
                from etlayout.gui.editor_smoke import run_editor_smoke
                failed = not run_editor_smoke(window, editor_output)
                if failed:
                    print(f"Desktop editor smoke test failed; see {editor_output / 'receipt.json'}", file=sys.stderr)
            if args.screenshot:
                Path(args.screenshot).parent.mkdir(parents=True, exist_ok=True)
                window.grab().save(args.screenshot)
            window.dirty = False
            window.close()
            application.exit(1 if failed else 0)
        QTimer.singleShot(250, finish)
    return application.exec()


if __name__ == "__main__":
    raise SystemExit(main())
