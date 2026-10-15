# SPDX-License-Identifier: MPL-2.0
"""Exercise real power/routing desktop states and capture native/offscreen evidence."""
from __future__ import annotations

import argparse
import json
import multiprocessing
import os
import platform
import runpy
import time
from pathlib import Path


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=Path(".artifacts/power-routing/gui-acceptance"))
    parser.add_argument("--offscreen", action="store_true")
    parser.add_argument("--scale", type=float, default=1.)
    parser.add_argument("--history", type=Path, help="Optional existing real experiment history to inspect")
    args = parser.parse_args()
    os.environ["QT_SCALE_FACTOR"] = str(args.scale)
    if args.offscreen:
        os.environ["QT_QPA_PLATFORM"] = "offscreen"
    else:
        os.environ.pop("QT_QPA_PLATFORM", None)
    import psutil
    from PySide6.QtCore import QCoreApplication, QEvent, QTimer
    from PySide6.QtGui import QFont
    from PySide6.QtTest import QTest
    from PySide6.QtWidgets import QApplication

    from etlayout.gui.power_workbench import PowerWorkbench
    from etlayout.io.project import load_project, save_project
    from etlayout.power.analysis import export_power_report
    from etlayout.simulation.execution import ExecutionSettings

    root = Path(__file__).resolve().parents[1]
    output = args.output.resolve() / f"{'offscreen' if args.offscreen else 'native'}-{args.scale:g}x"
    output.mkdir(parents=True, exist_ok=True)
    fixture = runpy.run_path(str(root / "examples" / "power_routing" / "generate.py"))
    project = fixture["build_project"](output / "inputs")
    project.power.ports[0].value = .01
    app = QApplication([])
    app.setStyle("Fusion")
    app.setFont(QFont("Segoe UI", 9))
    execution = ExecutionSettings()
    window = PowerWorkbench(project, execution=execution)
    window.resize(1120, 760)
    window.show()
    receipt = {"platform": platform.platform(), "qt_platform": app.platformName(), "python": platform.python_version(),
        "capture": "Live source QWidget.grab", "scale": args.scale, "execution": execution.to_dict(),
        "checks": [], "screens": [],
        "limitations": "Native source application only; no frozen executable, physical DPI or screen-reader claim."}
    ticks, memory = [], []
    timer = QTimer()
    def heartbeat():
        ticks.append(time.perf_counter())
        process = psutil.Process()
        owned = [process, *process.children(recursive=True)]
        total = 0
        for child in owned:
            try:
                total += child.memory_info().rss
            except psutil.NoSuchProcess:
                pass
        memory.append(total)
    timer.timeout.connect(heartbeat)
    timer.start(50)
    started = time.perf_counter()
    destroyed = []
    window.destroyed.connect(lambda: destroyed.append("workbench"))
    timer.destroyed.connect(lambda: destroyed.append("heartbeat"))

    def idle():
        clock = time.perf_counter()
        while window.job:
            if time.perf_counter() - clock > 240:
                window.cancel_job()
                raise AssertionError("Power worker timeout")
            QTest.qWait(40)
        QTest.qWait(120)

    def capture(name):
        QTest.qWait(180)
        assert window.grab().save(str(output / (name + ".png")))
        receipt["screens"].append(name + ".png")
        print(name, flush=True)

    try:
        capture("01-setup")
        window.start("power", {})
        idle()
        assert window.result and window.result.converged, window.status.text()
        assert window.analysis["contacts"], "No physical contact loading records"
        receipt["checks"].append("Coupled power solve completed in a separate process")
        receipt["mesh"] = {"nodes": len(window.result.mesh.points), "triangles": len(window.result.mesh.triangles)}
        receipt["contact_count"] = len(window.analysis["contacts"])
        window.analysis_views.setCurrentWidget(window.analysis_tables["contacts"])
        window.analysis_tables["contacts"].selectRow(0)
        QTest.qWait(100)
        assert window.analysis_map.overlay_item is not None
        capture("02-contact-loading")
        export_power_report(window.result, output / "analysis")
        save_project(window.project, output / "analysis" / "reopened.etl", window.result)
        _, restored = load_project(output / "analysis" / "reopened.etl")
        assert restored.analysis["contacts"] == window.result.analysis["contacts"]
        receipt["checks"].append("Per-contact map, exact geometry highlight, CSV/JSON export and archive reopen")
        window.extract_multiport()
        idle()
        assert window.multiport_report, window.status.text()
        receipt["multiport_validation"] = window.multiport_report["validation"]
        capture("03-passive-multiport")
        window.routing_output.setText(str(output / "candidate.oas"))
        window.routing_parameters.setText('{"bus.width":6e-6,"strap.width":6e-6,"vias.columns":6}')
        window.generate_candidate()
        idle()
        assert window.candidate and window.candidate["report"]["valid"], window.status.text()
        receipt["routing"] = window.candidate["report"]
        if window.routing_table.rowCount():
            window.routing_table.selectRow(0)
        capture("04-routing-preview")
        receipt["checks"].append("Generated OASIS candidate with protected geometry/rules and physical round-trip checks")
        if args.history:
            window.load_history(args.history)
            window.tabs.setCurrentIndex(3)
            window.status.setText("Inspecting recorded experiment evidence; project compatibility is checked on resume/export.")
            window.pareto_only.setChecked(True)
            if window.candidates.rowCount():
                window.candidates.selectRow(0)
            capture("05-experiment-history")
            receipt["inspected_history"] = str(args.history.resolve())
            receipt["history_status"] = window.history["status"]
            receipt["verified_candidates"] = window.history.get("verified", [])
        receipt["heartbeat_ticks"] = len(ticks)
        receipt["max_sampled_process_tree_rss_bytes"] = max(memory, default=0)
        receipt["elapsed_seconds"] = time.perf_counter() - started
        assert len(ticks) > 5
        receipt["checks"].append("Qt heartbeat remained active during compute operations")
    finally:
        timer.stop()
        window.close()
        # This harness pumps QTest events rather than QApplication.exec().
        # Release widgets while QApplication still exists; otherwise deferred
        # graphics cleanup is left to binding/interpreter shutdown order.
        window.deleteLater()
        timer.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        app.processEvents()
        receipt["destroyed_before_exit"] = destroyed
        (output / "observations.json").write_text(json.dumps(receipt, indent=2), encoding="utf-8")
    assert sorted(destroyed) == ["heartbeat", "workbench"], receipt
    print("PASS: all real source GUI acceptance assertions completed", flush=True)
    return 0


if __name__ == "__main__":
    multiprocessing.freeze_support()
    raise SystemExit(main())
