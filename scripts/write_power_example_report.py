# SPDX-License-Identifier: MPL-2.0
"""Publish compact measured provenance and a reproducible reference table.

Raw receipts, solved fields and logs remain artifacts. This report retains actual
values and commands, not a second catalog of independent operating assumptions.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path


def compact(row, directory):
    keys = ("name", "status", "converged", "mesh_verified", "model_failures", "unavailable", "reason", "error",
            "phase", "response", "sharing", "phases", "peak_process_tree_RSS_bytes", "process_wall_seconds",
            "hardware", "limits", "execution", "source_fingerprint", "source_unchanged_during_run", "mesh_factor", "mode",
            "export_reimport", "configuration")
    result = {key: row[key] for key in keys if key in row}
    if "execution" in result:
        result["execution"] = {key: value for key, value in result["execution"].items() if key != "libraries"}
    project_path = directory / row["name"] / "inputs" / (row["name"] + ".etl")
    if project_path.is_file():
        from etlayout.io.project import file_hash, load_project, physics_hash
        project, _ = load_project(project_path)
        result["input_identity"] = dict(project_archive_sha256=file_hash(project_path),
            layout_sha256=project.layout.sha256, physics_hash=physics_hash(project))
    if "metrics" in row:
        result["metrics"] = {key: value for key, value in row["metrics"].items() if key in {
            "electrical_power", "temperature_max", "current_balance_error", "power_balance_error", "thermal_balance_error"}}
        result["measurements"] = {key: {field: record[field] for field in ("value", "unit", "status", "within_limits") if field in record}
            for key, record in row["measurements"].items() if key in {
                "routing_loss", "contact_via_loss", "device_loss", "regularized_hotspot", "temperature_rise", "transport_field"}}
        result["mesh"] = {key: value for key, value in row["mesh"].items() if isinstance(value, (int, float, str))}
        result["loss_budget"] = row["loss_budget"]
    return result


def render(rows, target):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    plt.rcParams.update({"font.family": "DejaVu Sans", "svg.fonttype": "none", "font.size": 11})
    figure, ax = plt.subplots(figsize=(10., 3.6))
    figure.subplots_adjust(left=.02, right=.98, bottom=.04, top=.96)
    ax.set(xlim=(0, 1), ylim=(0, 1))
    ax.axis("off")
    ax.text(.01, .96, "Power reference calculations", ha="left", va="top", fontsize=16, weight="semibold", color="#18211E")
    ax.text(.01, .86, "Coupled electrothermal · four CPUs per job · synthetic assumptions", ha="left", fontsize=10, color="#55635D")
    headings = [("Family / die (mm)", .01, "left"), ("I (A)", .46, "right"), ("R (mΩ)", .59, "right"),
                ("Max T (K)", .72, "right"), ("Calc (s)", .86, "right"), ("RSS (GiB)", .99, "right")]
    for label, x, alignment in headings:
        ax.text(x, .73, label, ha=alignment, weight="semibold", color="#18211E")
    ax.plot([.01, .99], [.69, .69], color="#98A69F", lw=.8)
    for index, row in enumerate(rows):
        y = .61 - index * .085
        code = row["name"].split("_")[0]
        dimensions = " × ".join(f"{value / 1000:g}" for value in row["configuration"]["die_um"])
        values = [f"{code} · {dimensions}", f"{row['response']['delivered_current_A']:.1f}",
                  f"{row['response']['effective_on_state_resistance_ohm'] * 1000:.2f}",
                  f"{row['metrics']['temperature_max']:.1f}", f"{row['phases']['calculation_seconds']:.1f}",
                  f"{row['peak_process_tree_RSS_bytes'] / 1024**3:.2f}"]
        for value, (_, x, alignment) in zip(values, headings, strict=True):
            ax.text(x, y, value, ha=alignment, color="#18211E")
    ax.plot([.01, .99], [.21, .21], color="#98A69F", lw=.8)
    ax.text(.01, .14, "All rows are converged reference solves; individual receipts remain provisional.", fontsize=9, color="#55635D")
    ax.text(.01, .07, "Observed mesh acceptance and shared-host performance details: power-devices.json and guide.", fontsize=9, color="#55635D")
    target.parent.mkdir(parents=True, exist_ok=True)
    figure.savefig(target.with_suffix(".svg"), metadata={"Date": None})
    figure.savefig(target.with_suffix(".png"), dpi=150)
    plt.close(figure)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--references", type=Path, required=True)
    parser.add_argument("--matrix", type=Path, required=True)
    parser.add_argument("--comparisons", type=Path, required=True)
    parser.add_argument("--output", type=Path, default=Path("docs/example-provenance/power-devices.json"))
    parser.add_argument("--table", type=Path, default=Path("docs/images/power-examples/reference-calculations"))
    args = parser.parse_args()
    references = json.loads((args.references / "summary.json").read_text(encoding="utf-8"))
    if len(references) != 5 or any(row["status"] != "converged_provisional" or not row["source_unchanged_during_run"] for row in references):
        raise ValueError("Five actual converged, model-valid frozen-source reference receipts are required")
    comparisons = {path.stem: json.loads(path.read_text()) for path in sorted(args.comparisons.glob("*.json"))}
    # Host paths are artifact locations, not portable application resources.
    for comparison in comparisons.values():
        comparison["receipts"] = [str(Path(path).relative_to(Path.cwd())) for path in comparison["receipts"]]
    matrix = {path.parent.name: [compact(row, path.parent) for row in json.loads(path.read_text(encoding="utf-8"))]
              for path in sorted(args.matrix.glob("*/summary.json"))}
    report = dict(schema_version=1, purpose="Maintained measured example provenance, not calibrated device ratings",
                  performance_context="Four CPUs per benchmark job; source/desktop/package verification may run concurrently on this host. RSS is sampled every 0.1 s. Times are observations, not portable speedup claims.",
                  references=[compact(row, args.references) for row in references], matrix=matrix, comparisons=comparisons,
                  reproduction={"references": "python scripts/benchmark_power_examples.py --examples E20 E21 E22 E23 E24 --timeout 1500 --output .artifacts/power-release-reference --save-fields",
                      "matrix": "Use each receipt's preset, variant, current, stress, thermal, mode and mesh_factor with scripts/benchmark_power_examples.py; the guide lists comparison levels and large attempts.",
                      "report": "python scripts/write_power_example_report.py --references .artifacts/power-release-reference --matrix .artifacts/power-release-matrix --comparisons .artifacts/power-release-comparisons"})
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2, ensure_ascii=False, allow_nan=False) + "\n", encoding="utf-8")
    render(references, args.table)
    print("Measured provenance and reference table written")


if __name__ == "__main__":
    main()
