# SPDX-License-Identifier: MPL-2.0
"""Render actual local-refinement receipts as small maintained figures and data."""

from __future__ import annotations

import argparse
import csv
import json
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt


def render(source, output):
    report = json.loads(source.read_text(encoding="utf-8"))
    output.mkdir(parents=True, exist_ok=True)
    plt.rcParams.update(
        {
            "font.family": "DejaVu Sans",
            "font.size": 8.5,
            "axes.labelsize": 8.5,
            "axes.titlesize": 9.5,
            "axes.spines.top": False,
            "axes.spines.right": False,
            "axes.linewidth": 0.65,
            "svg.fonttype": "none",
            "savefig.facecolor": "white",
        }
    )
    fig, axes = plt.subplots(1, 3, figsize=(8.3, 2.65), constrained_layout=True)
    titles = {
        "sharp_L": "Sharp corner",
        "rounded_L": "2 \u00b5m rounded corner",
        "finite_contact": "Finite contact, $L_T$ = 2 \u00b5m",
    }
    with (output / "local-mesh.csv").open("w", encoding="utf-8", newline="") as stream:
        writer = csv.writer(stream, lineterminator="\n")
        writer.writerow(
            [
                "case",
                "factor",
                "elements",
                "dofs",
                "run_seconds",
                "support_area_m2",
                "Kpeak_A_per_m",
                "Kmean_A_per_m",
                "Joule_W",
                "Tmax_K",
                "Tmean_K",
                "raw_peak_status",
            ]
        )
        for ax, case in zip(axes, report["cases"]):
            receipt = case["receipt"]
            support = receipt["layers"]["metal"]
            metrics = support["metrics"]
            for i, level in enumerate(receipt["levels"]):
                writer.writerow(
                    [
                        case["case"],
                        level["factor"],
                        level["elements"],
                        level["dofs"],
                        level["elapsed_s"],
                        support["area_m2"],
                        *[
                            metrics[key]["values"][i]
                            for key in (
                                "raw_sheet_current_max_A_per_m",
                                "sheet_current_average_A_per_m",
                                "joule_power_W",
                                "temperature_max_K",
                                "temperature_average_K",
                            )
                        ],
                        metrics["raw_sheet_current_max_A_per_m"]["status"],
                    ]
                )
            dofs = [level["dofs"] / 1000 for level in receipt["levels"]]
            peak = [value / 1000 for value in metrics["raw_sheet_current_max_A_per_m"]["values"]]
            ax.plot(dofs, peak, "o-", color="#167c80", linewidth=1.5, markersize=4)
            ax.set(title=titles[case["case"]], xlabel="Combined DOFs (thousands)")
            ax.set_ylabel("Raw peak sheet current (kA/m)")
            ax.grid(axis="y", color="#dddddd", linewidth=0.5)
            status = metrics["raw_sheet_current_max_A_per_m"]["status"]
            label = {
                "singular_peak_unverified": "Singular peak: unverified",
                "mesh_converged": "Mesh converged; unqualified",
                "refine_required": "Further refinement required",
            }[status]
            ax.text(
                0.02,
                0.97,
                label,
                transform=ax.transAxes,
                va="top",
                fontsize=7.5,
                bbox={"facecolor": "white", "edgecolor": "none", "alpha": 0.9},
            )
            ax.margins(y=0.24)
    for suffix in ("png", "svg"):
        path = output / ("local-mesh." + suffix)
        fig.savefig(path, dpi=180)
        if suffix == "svg":
            path.write_bytes((
                "\n".join(line.rstrip() for line in path.read_text(encoding="utf-8").splitlines()) + "\n",
                encoding="utf-8",
            )
    plt.close(fig)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    render(args.source, args.output)
