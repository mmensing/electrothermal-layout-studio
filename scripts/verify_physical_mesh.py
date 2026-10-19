# SPDX-License-Identifier: MPL-2.0
"""Measure bounded local meshes, archive identity and optional native confidence UI."""

from __future__ import annotations

import argparse
import csv
import json
import os
import time
from pathlib import Path

from etlayout.io.project import load_project, physics_hash, save_project
from etlayout.meshing.refinement import suggest_refinements
from etlayout.simulation.runner import clear_caches, run_project
from etlayout.simulation.sweep import mesh_refinement_study
from etlayout.validation.crowding import benchmark_project


def verify(output):
    output.mkdir(parents=True, exist_ok=True)
    report = {"version": 1, "cases": []}
    for case, purpose in (
        ("sharp_L", "Reentrant / neck boundary"),
        ("rounded_L", "Rounded boundary"),
        ("finite_contact", "Contact transfer"),
    ):
        print("Measuring " + case, flush=True)
        clear_caches()
        project = benchmark_project(case, 4e-6)
        if case == "finite_contact":
            project.interfaces[0].electrical_value = 4e-14
        original = project.to_dict()
        baseline = run_project(project)
        plan = suggest_refinements(project, baseline, field_driven=True)
        selected = [suggestion.id for suggestion in plan.suggestions if suggestion.purpose == purpose]
        rows = mesh_refinement_study(project, [1.0, 0.5, 0.25], local_plan=plan, selected=selected)
        if any(not row.result or not row.result.converged or row.error for row in rows):
            raise RuntimeError("Numerical/local measurement failure: " + repr([row.error for row in rows]))
        if project.to_dict() != original:
            raise RuntimeError("Local verification mutated its source setup")
        result = rows[-1].result
        receipt = result.analysis["local_mesh_verification"]
        if receipt["status"] != "available":
            raise RuntimeError(str(receipt))
        started = time.perf_counter()
        save_project(project_from_result(result), output / (case + ".etl"), result)
        save_seconds = time.perf_counter() - started
        started = time.perf_counter()
        restored, loaded = load_project(output / (case + ".etl"))
        reopen_seconds = time.perf_counter() - started
        if physics_hash(restored) != result.physics_hash:
            raise RuntimeError("Reopen physical identity mismatch")
        if loaded.analysis["local_mesh_verification"] != receipt:
            raise RuntimeError("Confidence receipt was not retained")
        report["cases"].append(
            {
                "case": case,
                "specific_contact_resistance_ohm_m2": project.interfaces[0].electrical_value
                if project.interfaces
                else None,
                "baseline_elements": len(baseline.mesh.triangles),
                "plan": plan.to_dict(),
                "receipt": receipt,
                "save_seconds": save_seconds,
                "reopen_seconds": reopen_seconds,
                "archive_bytes": (output / (case + ".etl")).stat().st_size,
            }
        )
    (output / "verification.json").write_text(json.dumps(report, indent=2, allow_nan=False), encoding="utf-8")
    with (output / "mesh-summary.csv").open("w", newline="", encoding="utf-8") as stream:
        writer = csv.writer(stream)
        writer.writerow(
            [
                "case",
                "factor",
                "elements",
                "dofs",
                "run_s",
                "Kmax_A_per_m",
                "local_Joule_W",
                "local_Tmax_K",
                "peak_status",
            ]
        )
        for case in report["cases"]:
            receipt = case["receipt"]
            metrics = receipt["layers"]["metal"]["metrics"]
            for index, level in enumerate(receipt["levels"]):
                writer.writerow(
                    [
                        case["case"],
                        level["factor"],
                        level["elements"],
                        level["dofs"],
                        level["elapsed_s"],
                        metrics["raw_sheet_current_max_A_per_m"]["values"][index],
                        metrics["joule_power_W"]["values"][index],
                        metrics["temperature_max_K"]["values"][index],
                        metrics["raw_sheet_current_max_A_per_m"]["status"],
                    ]
                )
    return report


def project_from_result(result):
    from etlayout.io.project import project_from_dict

    return project_from_dict(result.configuration)


def capture(report, output):
    from desktop_acceptance_support import prepare_desktop_fonts, wait_desktop_events
    from PySide6.QtWidgets import QApplication

    from etlayout.gui.refinement import MeshVerificationDialog

    app = QApplication.instance() or QApplication([])
    app.setStyle("Fusion")
    prepare_desktop_fonts(app, output)
    dialog = MeshVerificationDialog(report["cases"][0]["receipt"])
    for width, name in ((1050, "mesh-confidence"), (760, "mesh-confidence-compact")):
        dialog.resize(width, 620)
        dialog.show()
        wait_desktop_events(150)
        dialog.grab().save(str(output / (name + ".png")))
    dialog.close()
    dialog.deleteLater()
    app.processEvents()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--capture", action="store_true")
    parser.add_argument(
        "--capture-only",
        action="store_true",
        help="Render existing verification.json; performs no new numerical checks",
    )
    parser.add_argument("--offscreen", action="store_true")
    args = parser.parse_args()
    if args.offscreen:
        os.environ["QT_QPA_PLATFORM"] = "offscreen"
    else:
        os.environ.pop("QT_QPA_PLATFORM", None)
    if args.capture_only:
        print("UI capture only: reusing recorded calculations; no new numerical checks", flush=True)
        report = json.loads((args.output / "verification.json").read_text(encoding="utf-8"))
    else:
        report = verify(args.output)
    if args.capture:
        capture(report, args.output)


if __name__ == "__main__":
    main()
