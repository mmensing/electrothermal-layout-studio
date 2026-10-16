# SPDX-License-Identifier: MPL-2.0
"""Run the standard spawned desktop workflow and inspect measured Jobs timings."""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--offscreen", action="store_true")
    parser.add_argument("--output", type=Path, default=ROOT / ".artifacts/reporting-desktop")
    args = parser.parse_args()
    if args.offscreen:
        os.environ["QT_QPA_PLATFORM"] = "offscreen"
    from desktop_acceptance_support import isolate_acceptance_settings, prepare_desktop_fonts
    from PySide6.QtCore import QTimer
    from PySide6.QtWidgets import QApplication

    from etlayout.examples import make_example
    from etlayout.gui.main_window import MainWindow

    args.output.mkdir(parents=True, exist_ok=True)
    isolate_acceptance_settings(args.output)
    app = QApplication.instance() or QApplication([])
    prepare_desktop_fonts(app, args.output)
    project = make_example("E1", args.output / "inputs")
    window = MainWindow(project, recover=False, database_path=args.output / "materials.sqlite3")
    window.recovery_path = args.output / "recovery.etl"
    window.resize(1280, 800)
    window.show()
    state = {"started": False, "errors": []}
    window.error = lambda title, detail: state["errors"].append([title, str(detail)])
    started = time.perf_counter()

    def poll():
        try:
            if state["errors"]:
                raise AssertionError(state["errors"])
            if time.perf_counter()-started > 90:
                raise AssertionError("Desktop timing workflow exceeded 90 s")
            if not state["started"]:
                if window.prepare_job or window.job:
                    return
                state["started"] = True
                window.run()
                return
            if window.job:
                assert window.guided.job_cancel.isEnabled()
                return
            assert window.result and window.result.converged
            window.guided.open_drawer(1)
            text = window.guided.job_timings.text()
            assert all(label+":" in text for label in ("Preparation", "Mesh", "Numerical", "Total")), text
            assert "included in Numerical" in window.guided.job_timings.toolTip()
            assert not window.guided.job_cancel.isEnabled()
            window.grab().save(str(args.output / "jobs-timings.png"))
            (args.output / "verification.json").write_text(json.dumps({"timing_text": text,
                "execution": window.result.execution, "native": not args.offscreen,
                "viewport": [window.width(), window.height()]}, indent=2), encoding="utf-8")
            state["success"] = True
            timer.stop()
            window.dirty = False
            app.quit()
        except Exception as error:
            state["error"] = str(error)
            timer.stop()
            if window.job:
                window.cancel_job()
            window.dirty = False
            app.quit()

    timer = QTimer()
    timer.timeout.connect(poll)
    timer.start(100)
    app.exec()
    if not state.get("success"):
        raise RuntimeError(state.get("error", "Desktop timing workflow did not complete"))
    print("Spawned desktop calculation and readable Jobs timing evidence passed")


if __name__ == "__main__":
    main()
