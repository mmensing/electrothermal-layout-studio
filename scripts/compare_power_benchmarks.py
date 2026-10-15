# SPDX-License-Identifier: MPL-2.0
"""Compare two routing variants at equal service using two measured mesh levels.

Consumes benchmark receipts and their actual input projects; never substitutes
geometry, changes drive values, or promotes a single converged solve to verified.
"""
from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))


def compare(paths, tolerance=.03):
    from etlayout.geometry.layout import LayoutDocument
    from etlayout.io.project import load_project
    from etlayout.routing.geometry import canonical_geometry_id

    rows = [json.loads(path.read_text(encoding="utf-8")) for path in paths]
    if len(rows) not in (2, 4):
        raise ValueError("Supply two levels of one variant, or coarse/fine levels of two variants")
    incomplete = [row for row in rows if row["status"] != "converged_provisional"]
    if incomplete:
        return dict(status="unverified", equal_service_checked=False, mesh_sensitivity=[], effects={},
            acceptance_errors=["A required level has no converged model-valid result"],
            incomplete_levels=[{key: row[key] for key in ("name", "status", "phase", "reason", "error", "model_failures") if key in row}
                               for row in incomplete], receipts=[str(path.resolve()) for path in paths])
    projects = [load_project(path.parent / "inputs" / (row["name"] + ".etl"))[0]
                for path, row in zip(paths, rows, strict=True)]
    errors = []
    reference = rows[0]["configuration"]
    for row, project in zip(rows, projects, strict=True):
        if row["status"] != "converged_provisional" or not row.get("source_unchanged_during_run"):
            errors.append("Every mesh level must have a converged, model-valid, unchanged-source receipt")
        if row.get("source_fingerprint") != rows[0].get("source_fingerprint"):
            errors.append("Calculation sources differ")
        if not all(row.get("export_reimport", {}).get(key) for key in ("valid", "masks_identical", "groups_preserved")):
            errors.append("Export/reimport identity is unverified")
        for key in ("family", "requested_current_A", "die_um", "total_gate_width_m", "active_area_m2",
                    "reservoir_temperature_K", "attach_resistance_area_m2_K_W", "stress"):
            if row["configuration"].get(key) != reference.get(key):
                errors.append("Unequal service, active configuration or environment: " + key)
        if abs(row["response"]["delivered_current_A"] / reference["requested_current_A"] - 1) > project.solver.current_balance_tolerance:
            errors.append("Delivered current does not meet requested service")
    if reference["stress"]:
        errors.append("Stress cases are excluded from verified comparisons")
    first = projects[0]
    protected = first.power.rules.protected_masks
    geometries = [LayoutDocument(project.layout.path).extract(project.layout.cell) for project in projects]
    for geometry in geometries[1:]:
        if any(canonical_geometry_id(geometry[key]) != canonical_geometry_id(geometries[0][key]) for key in protected):
            errors.append("Protected active geometry changed")
    for project in projects[1:]:
        if (project.materials != first.materials or project.power.devices != first.power.devices or
                project.thermal_boundaries != first.thermal_boundaries or
                [(layer.id, layer.electrical, layer.thickness, layer.z) for layer in project.layers] !=
                [(layer.id, layer.electrical, layer.thickness, layer.z) for layer in first.layers] or
                [asdict(item) for item in project.interfaces if item.electrical_kind in {"specific", "load"}] !=
                [asdict(item) for item in first.interfaces if item.electrical_kind in {"specific", "load"}]):
            errors.append("Device parameters, thickness or thermal environment differ")

    def metrics(row):
        values = {"resistance_ohm": row["response"]["effective_on_state_resistance_ohm"],
                  "terminal_power_W": row["metrics"]["electrical_power"],
                  "routing_loss_W": row["measurements"]["routing_loss"]["value"],
                  "regularized_hotspot_rise_K": row["measurements"]["regularized_hotspot"]["value"] - reference["reservoir_temperature_K"]}
        for key, value in row["sharing"].items():
            values[key + "_sharing_error"] = value["maximum_relative_loading_deviation"]
            values[key + "_source_spread_V"] = value["source_potential_spread_V"]
        return values

    values = [metrics(row) for row in rows]
    sensitivity = []
    for index in range(0, len(rows), 2):
        assert rows[index]["mesh_factor"] > rows[index + 1]["mesh_factor"], "Supply coarse then fine for each variant"
        change = {}
        for key in values[index]:
            absolute = abs(values[index + 1][key] - values[index][key])
            normalized = absolute if key.endswith("sharing_error") else absolute / max(abs(values[index + 1][key]), 1e-12)
            change[key] = dict(absolute=absolute, normalized=normalized, accepted=normalized <= tolerance)
        sensitivity.append(change)
    resolved = not errors and all(item["accepted"] for group in sensitivity for item in group.values())
    effects = {}
    if len(rows) == 4:
        for key in values[1]:
            difference = values[1][key] - values[3][key]
            uncertainty = sensitivity[0][key]["absolute"] + sensitivity[1][key]["absolute"]
            effects[key] = dict(baseline_fine=values[1][key], alternative_fine=values[3][key],
                               baseline_minus_alternative=difference, observed_mesh_uncertainty=uncertainty,
                               improvement_exceeds_mesh_uncertainty=resolved and difference > uncertainty)
    return dict(status="mesh_resolved" if resolved else "provisional", acceptance_errors=sorted(set(errors)),
                maximum_mesh_change=tolerance, mesh_sensitivity=sensitivity, effects=effects,
                equal_service_checked=not errors, receipts=[str(path.resolve()) for path in paths],
                limitation="Two levels bound observed mesh sensitivity; no experimental calibration or unique overall winner.")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("receipts", nargs="+", type=Path, help="Coarse/fine of one variant, or baseline coarse/fine and alternative coarse/fine")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if len(args.receipts) not in (2, 4):
        parser.error("Supply two or four measured receipts")
    result = compare(args.receipts)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    print(result["status"])
    return int(bool(result["acceptance_errors"]))


if __name__ == "__main__":
    raise SystemExit(main())
