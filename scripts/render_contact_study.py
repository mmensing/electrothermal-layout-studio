# SPDX-License-Identifier: MPL-2.0
"""Render measured TLM resistance and independent-reference discrepancies."""

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
            "svg.hashsalt": "etlayout-tlm",
        }
    )
    fig, (ax, residual) = plt.subplots(1, 2, figsize=(7.2, 2.8), constrained_layout=True)
    with (output / "tlm-mesh.csv").open("w", encoding="utf-8", newline="") as stream:
        writer = csv.writer(stream, lineterminator="\n")
        writer.writerow(
            [
                "mesh_factor",
                "gap_m",
                "elements",
                "current_A",
                "Rterminal_ohm",
                "Rdeembedded_ohm",
                "Rreference_ohm",
                "Pmetal_W",
                "Pfeed_W",
                "Pfilm_W",
                "Pinterface_W",
                "run_seconds",
                "preparation_seconds",
                "meshing_seconds",
                "numerical_seconds",
                "reporting_seconds",
                "Rs_fit_ohm_per_square",
                "Rc_fit_ohm",
                "rho_c_fit_ohm_m2",
                "LT_fit_m",
                "study_wall_seconds",
                "process_high_water_bytes_cumulative",
            ]
        )
        for index, level in enumerate(data["levels"]):
            report = level["report"]
            fit = report["fit"]
            observations = report["observations"]
            gap = [o["gap_m"] * 1e6 for o in observations]
            residual.plot(
                gap,
                [
                    (o["deembedded_resistance_ohm"] - o["analytical_resistance_ohm"]) * 1000
                    for o in observations
                ],
                "o-",
                color=("#adb6bd", "#697783", "#167c80")[index],
                label=f"Mesh factor {level['factor']:g}",
                linewidth=1.3,
                markersize=3.5,
            )
            for o in observations:
                timings = o["execution"].get("timings_seconds", {})
                writer.writerow(
                    [
                        level["factor"],
                        o["gap_m"],
                        o["elements"],
                        o["delivered_current_A"],
                        o["terminal_resistance_ohm"],
                        o["deembedded_resistance_ohm"],
                        o["analytical_resistance_ohm"],
                        o["metal_loss_W"],
                        o["external_feed_loss_W"],
                        o["film_loss_W"],
                        o["interface_only_loss_W"],
                        o["elapsed_seconds"],
                        *[timings.get(k) for k in ("preparation", "meshing", "numerical", "reporting")],
                        *[fit[k] for k in ("Rs_ohm_per_square", "Rc_ohm", "rho_c_ohm_m2", "LT_m")],
                        level["wall_seconds"],
                        level["process_high_water_bytes_cumulative"],
                    ]
                )
    finest = data["levels"][-1]["report"]
    observations = finest["observations"]
    gap = [o["gap_m"] * 1e6 for o in observations]
    ax.plot(
        gap,
        finest["reference"]["resistance_ohm"],
        "--",
        color="#697783",
        label="Independent finite-contact limit",
    )
    ax.plot(gap, finest["fit"]["predictions_ohm"], color="#167c80", label="Fitted line")
    ax.scatter(
        gap,
        [o["deembedded_resistance_ohm"] for o in observations],
        s=23,
        facecolors="white",
        edgecolors="#1c252b",
        label="FEM, metal/feed deembedded",
        zorder=3,
    )
    ax.set(xlabel="Contact gap (\u00b5m)", ylabel="Resistance (\u03a9)", title="Four actual 1 mA coupons")
    ax.legend(frameon=False, fontsize=7.2)
    residual.axhline(0, color="#697783", linewidth=0.6)
    residual.set(
        xlabel="Contact gap (\u00b5m)",
        ylabel="FEM \u2212 independent limit (m\u03a9)",
        title="Mesh and finite-metal effects",
    )
    residual.legend(frameon=False, fontsize=7.5)
    for axis in (ax, residual):
        axis.grid(axis="y", color="#dddddd", linewidth=0.5)
    for suffix in ("png", "svg"):
        path = output / ("tlm-convergence." + suffix)
        fig.savefig(path, dpi=180, metadata={"Date": None} if suffix == "svg" else None)
        if suffix == "svg":
            path.write_bytes(
                (
                    "\n".join(line.rstrip() for line in path.read_text(encoding="utf-8").splitlines()) + "\n"
                ).encode("utf-8")
            )
    plt.close(fig)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    render(args.source, args.output)
