# SPDX-License-Identifier: MPL-2.0
"""Render small maintained figures/tables from measured verify_crowding output."""

from __future__ import annotations

import argparse
import csv
import json
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt


def render(source, output):
    data = json.loads(source.read_text(encoding="utf-8"))
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
    with (output / "crowding-mesh.csv").open("w", newline="", encoding="utf-8") as stream:
        writer = csv.writer(stream)
        writer.writerow(
            [
                "case",
                "mesh_m",
                "elements",
                "current_A",
                "resistance_ohm",
                "Joule_W",
                "contact_W",
                "Tmax_K",
                "local_mean_K_A_per_m",
                "local_peak_K_A_per_m",
                "local_Joule_W",
                "local_Tmax_K",
                "local_Taverage_K",
                "current_balance_fraction",
                "power_balance_fraction",
                "thermal_balance_fraction",
                "elapsed_s",
                "preparation_s",
                "meshing_s",
                "numerical_s",
                "linear_solve_s",
                "reporting_s",
                "peak_working_set_bytes_cumulative",
            ]
        )
        for case in data["cases"]:
            for row in case["meshes"]:
                m, local = row["metrics"], row["local"]
                timings = row.get("execution", {}).get("timings_seconds", {})
                writer.writerow(
                    [
                        case["case"],
                        row["mesh_size_m"],
                        row["elements"],
                        m["total_current"],
                        m["effective_resistance"],
                        m["joule_power"],
                        m["contact_power"],
                        m["temperature_max"],
                        local["sheet_current_average_A_per_m"],
                        local["raw_sheet_current_max_A_per_m"],
                        local["joule_power_W"],
                        local["temperature_max_K"],
                        local["temperature_average_K"],
                        m["current_balance_error"],
                        m["power_balance_error"],
                        m["thermal_balance_error"],
                        row["elapsed_s"],
                        *[
                            timings.get(k)
                            for k in ("preparation", "meshing", "numerical", "sparse_linear_solves", "reporting")
                        ],
                        row["process_peak_working_set_bytes"],
                    ]
                )
    fig, axes = plt.subplots(2, 2, figsize=(178 / 25.4, 112 / 25.4), layout="constrained")
    series = [("sharp_L", "Sharp L", "#404040", "s"), ("rounded_L", "Rounded L (2 µm)", "#1F6F5F", "o")]
    for key, label, color, marker in series:
        case = next(c for c in data["cases"] if c["case"] == key)
        x = [r["mesh_size_m"] * 1e6 for r in case["meshes"]]
        ys = [
            [r["local"]["raw_sheet_current_max_A_per_m"] * 1e-3 for r in case["meshes"]],
            [r["local"]["joule_power_W"] * 1e3 for r in case["meshes"]],
            [r["metrics"]["effective_resistance"] * 1e3 for r in case["meshes"]],
            [r["local"]["temperature_max_K"] - 300 for r in case["meshes"]],
        ]
        for ax, y in zip(axes.flat, ys):
            ax.plot(x, y, color=color, marker=marker, markersize=4, lw=1.4, label=label)
    titles = [
        "Raw sheet-current peak (kA/m)",
        "Power in fixed window (mW)",
        "Effective resistance (mΩ)",
        "Local maximum rise (K)",
    ]
    for ax, title in zip(axes.flat, titles):
        ax.set_title(title, loc="left", pad=8)
        ax.set_xticks([4, 2, 1])
        ax.invert_xaxis()
        ax.set_xlabel("Maximum mesh size (µm; finer →)")
        ax.tick_params(labelsize=8)
    axes[0, 1].legend(frameon=False, fontsize=8, loc="best")
    fig.savefig(output / "crowding-convergence.svg")
    fig.savefig(output / "crowding-convergence.png", dpi=200)
    plt.close(fig)
    with (output / "corner-radius.csv").open("w", newline="", encoding="utf-8") as stream:
        writer = csv.writer(stream)
        writer.writerow(
            [
                "radius_m",
                "current_A",
                "Kmax_A_per_m",
                "Kaverage_A_per_m",
                "crowding_factor",
                "local_Joule_W",
                "Tmax_K",
                "peak_mesh_status",
            ]
        )
        for row in data["radius_study"]:
            m = row["finest"]
            writer.writerow(
                [
                    row["radius_m"],
                    row["current_A"],
                    m["raw_sheet_current_max_A_per_m"],
                    m["sheet_current_average_A_per_m"],
                    m["crowding_factor"],
                    m["joule_power_W"],
                    m["temperature_max_K"],
                    row["verification"]["metrics"]["raw_sheet_current_max"]["status"],
                ]
            )
    fig, axes = plt.subplots(1, 2, figsize=(178 / 25.4, 65 / 25.4), layout="constrained")
    for index, (color, marker, label) in enumerate(
        (("#8D8D88", "s", "4 µm mesh"), ("#404040", "^", "2 µm mesh"), ("#1F6F5F", "o", "1 µm mesh"))
    ):
        x = [r["radius_m"] * 1e6 for r in data["radius_study"]]
        for ax, key, factor in (
            (axes[0], "raw_sheet_current_max", 1e-3),
            (axes[1], "local_joule_power", 1e3),
        ):
            ax.plot(
                x,
                [r["verification"]["metrics"][key]["values"][index] * factor for r in data["radius_study"]],
                color=color,
                marker=marker,
                markersize=4,
                lw=1.2,
                label=label,
            )
    for ax, title in zip(axes, ("Raw peak at equal 0.25 A (kA/m)", "Fixed-window Joule power (mW)")):
        ax.set_title(title, loc="left")
        ax.set_xlabel("Inner corner radius (µm)")
    axes[1].legend(frameon=False, fontsize=8)
    fig.savefig(output / "corner-radius.svg")
    fig.savefig(output / "corner-radius.png", dpi=200)
    plt.close(fig)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    render(args.source, args.output)
