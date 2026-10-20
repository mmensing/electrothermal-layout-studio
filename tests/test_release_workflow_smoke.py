# SPDX-License-Identifier: MPL-2.0
"""The release protocol exercises real opening, history and study/browser actions."""
import json
import runpy
from pathlib import Path

from gui_test_helpers import guided_window as guided_window
from gui_test_helpers import solved_e1 as solved_e1

from etlayout.gui.release_smoke import run_workflow_smoke
from etlayout.io.project import save_project


def test_release_journey_uses_actual_fields_workers_and_dialogs(guided_window, solved_e1, qapp, tmp_path):
    support = runpy.run_path(str(Path(__file__).resolve().parents[1] / "scripts/desktop_acceptance_support.py"))
    previous_font = qapp.font()
    output = tmp_path / "release-journey"
    try:
        support["prepare_desktop_fonts"](qapp, output)
        project, result = solved_e1
        archive = tmp_path / "actual-e1.etl"
        save_project(project, archive, result)
        assert run_workflow_smoke(guided_window, archive, output), (output / "receipt.json").read_text(encoding="utf-8")
        receipt = json.loads((output / "receipt.json").read_text(encoding="utf-8"))
        assert receipt["status"] == "passed" and not receipt["frozen"]
        assert receipt["heartbeat_ticks"] > 0
        assert {item["file"] for item in receipt["captures"]} == {
            "frozen-workflow-results.png", "frozen-workflow-mesh-study.png",
            "frozen-workflow-browser.png", "frozen-workflow-example-ready.png"}
        assert guided_window.result is None and guided_window.guided.stage == "mesh"
    finally:
        qapp.setFont(previous_font)
