# SPDX-License-Identifier: MPL-2.0
"""Shared entry point for windowed and console Windows executables."""
from __future__ import annotations

import multiprocessing
import os
import sys
from pathlib import Path


def main():
    # The windowed bootloader does not attach stdout/stderr. Libraries which
    # print diagnostics still require real Python stream objects.
    for name in ("stdout", "stderr"):
        if getattr(sys, name) is None:
            setattr(sys, name, open(os.devnull, "w", encoding="utf-8"))
    multiprocessing.freeze_support()
    arguments = sys.argv[1:]
    if arguments and arguments[0] == "--worker-smoke":
        return worker_smoke(arguments[1:])
    if arguments and arguments[0] in {"gui", "--gui"}:
        from etlayout.app.main import main as gui_main
        return gui_main(arguments[1:])
    if not arguments or arguments[0] in {"--smoke-test", "--no-recovery", "--screenshot"}:
        from etlayout.app.main import main as gui_main
        return gui_main(arguments)
    if len(arguments) == 1 and Path(arguments[0]).suffix.lower() in {".etl", ".etlayout"}:
        from etlayout.app.main import main as gui_main
        return gui_main(arguments)
    from etlayout.cli.main import main as cli_main
    return cli_main(arguments)


def worker_smoke(arguments):
    """Exercise the same frozen spawn worker used by native GUI simulation."""
    import argparse
    import json
    import time

    from PySide6.QtCore import QTimer
    from PySide6.QtWidgets import QApplication

    from etlayout.gui.jobs import ComputeJob
    from etlayout.io.project import load_project
    from etlayout.simulation.execution import ExecutionSettings

    parser = argparse.ArgumentParser(description="Native packaging worker verification")
    parser.add_argument("project")
    parser.add_argument("--output", required=True)
    parser.add_argument("--time-study", action="store_true")
    args = parser.parse_args(arguments)
    application = QApplication([])
    project, _ = load_project(args.project)
    execution = ExecutionSettings()
    job = ComputeJob(project, "time_convergence" if args.time_study else "solve",
                     {"steps": [project.transient.time_step, project.transient.time_step / 2]} if args.time_study else None,
                     execution=execution)
    started = time.perf_counter()
    state = {"success": False}
    ticks = []
    heartbeat = QTimer()
    heartbeat.setInterval(20)
    heartbeat.timeout.connect(lambda: ticks.append(time.perf_counter()))
    heartbeat.start()
    from etlayout.gui.execution_settings import ExecutionSettingsDialog
    from etlayout.gui.guide import guide_path
    dialog = ExecutionSettingsDialog(execution)
    dialog.accept()
    assert dialog.settings == execution
    guide = guide_path()
    assert "Electrothermal" in guide.read_text(encoding="utf-8")
    state.update(execution_settings=execution.to_dict(), offline_help=True)

    def finish(result):
        results = [row.result for row in result] if args.time_study else [result]
        state.update(success=all(value and value.converged for value in results) and len(ticks) >= 5,
                     metrics=[value.metrics if value else None for value in results],
                     execution=[value.execution if value else None for value in results], heartbeat_ticks=len(ticks),
                     elapsed_seconds=time.perf_counter() - started)
        application.quit()

    def failure(message, details):
        state.update(error=message, details=details)
        application.quit()

    def timeout():
        job.cancel()
        state.update(error="Frozen GUI simulation worker timed out after 120 seconds")
        application.quit()

    job.finished.connect(finish)
    job.failed.connect(failure)
    QTimer.singleShot(120000, timeout)
    job.start()
    application.exec()
    heartbeat.stop()
    dialog.close()
    path = Path(args.output)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(state, indent=2), encoding="utf-8")
    return 0 if state["success"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
