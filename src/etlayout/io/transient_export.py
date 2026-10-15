# SPDX-License-Identifier: MPL-2.0
"""Time histories with SI columns and source-linked, shared-time-axis figures."""
from __future__ import annotations

import csv
from pathlib import Path

import numpy as np

from etlayout.io.project import _json_dump

HISTORY_COLUMNS = {
    "time_s": "time_s", "time_step": "time_step_s",
    "temperature_max": "temperature_max_K", "temperature_average": "temperature_average_K",
    "thermal_power": "thermal_power_W", "electrical_power": "electrical_power_W",
    "heat_out": "heat_out_W", "thermal_storage_rate": "thermal_storage_rate_W",
    "thermal_stored_energy": "thermal_stored_energy_J", "input_energy": "input_energy_J",
    "output_energy": "output_energy_J", "energy_balance_error": "energy_balance_error_J",
    "nonlinear_iterations": "nonlinear_iterations",
}


def _axis_scale(values, unit):
    """Readable engineering axes; the stored history remains in base SI units."""
    values = np.asarray(values, dtype=float)
    finite = np.abs(values[np.isfinite(values)])
    peak = float(np.max(finite, initial=0.))
    exponent = int(3 * np.floor(np.log10(peak) / 3)) if peak else 0
    exponent = max(-12, min(9, exponent))
    if unit == "s":
        exponent = min(exponent, 0)
    prefix = {-12: "p", -9: "n", -6: "µ", -3: "m", 0: "", 3: "k", 6: "M", 9: "G"}[exponent]
    return 10. ** exponent, prefix + unit


def export_transient_history(result, directory, *, plots=True):
    history = result.transient
    if history is None:
        raise ValueError("This result has no transient history")
    root = Path(directory)
    root.mkdir(parents=True, exist_ok=True)
    metrics = result.metrics
    context = {"initialization": metrics.get("transient_initialization", {}),
               "segment_start_time_s": metrics.get("transient_start_time", float(history.times[0])),
               "segment_end_time_s": float(history.times[-1]),
               "requested_stop_time_s": metrics.get("transient_requested_stop_time",
                   result.configuration.get("transient", {}).get("stop_time")),
               "energy_scope": metrics.get("transient_energy_scope", "whole_run"),
               "peak_scope": metrics.get("transient_peak_scope", "whole_run"),
               "energy_reference_offset_J": metrics.get("transient_energy_reference_offset", 0.),
               "initial_input_energy_J": history.rows[0].get("input_energy"),
               "initial_output_energy_J": history.rows[0].get("output_energy"),
               "initial_stored_energy_J": history.rows[0].get("thermal_stored_energy"),
               "stop_reason": metrics.get("transient_stop_reason", "stop_time" if history.completed else "failed_step"),
               "periodic_converged": metrics.get("transient_periodic_converged", False),
               "periodic_cycles": metrics.get("transient_periodic_cycles", 0)}
    metadata = {"schema_version": 1, "configuration_hash": result.configuration_hash,
                "physics_hash": result.physics_hash, "context": context,
                "execution": getattr(result, "execution", {}),
                "completed": history.completed, "snapshot_times_s": history.times.tolist(),
                "column_units": HISTORY_COLUMNS, "rows": history.rows,
                "drive_time_convention": "right limit at segment start; left limit at integration-step endpoints",
                "model": "Transient heat capacity with quasi-static electrical solves; no RLC dynamics"}
    (root / "transient_history.json").write_text(_json_dump(metadata), encoding="utf-8")
    sidecar = {key: value for key, value in metadata.items() if key not in {"rows", "snapshot_times_s"}}
    sidecar["csv_file"] = "transient_history.csv"
    (root / "transient_history.metadata.json").write_text(_json_dump(sidecar), encoding="utf-8")
    with (root / "transient_history.csv").open("w", newline="", encoding="utf-8-sig") as stream:
        writer = csv.writer(stream)
        writer.writerow([*HISTORY_COLUMNS.values(), "drives_json", "terminals_json", "configuration_hash"])
        for row in history.rows:
            writer.writerow([*(row.get(key, "") for key in HISTORY_COLUMNS),
                             _json_dump(row.get("drives", {})), _json_dump(row.get("terminals", {})),
                             result.configuration_hash])
    files = ["transient_history.json", "transient_history.csv", "transient_history.metadata.json"]
    if plots:
        from etlayout.io.export import _style
        _style()
        import matplotlib.pyplot as plt
        from matplotlib.ticker import MaxNLocator
        times = np.asarray([row["time_s"] for row in history.rows])
        time_scale, time_unit = _axis_scale(times, "s")
        fig, axes = plt.subplots(3, 1, sharex=True, figsize=(7.1, 6.8), layout="constrained")
        groups = [(("temperature_max", "Peak", "#1F6F5F", "-"),
                   ("temperature_average", "Mean", "#6F6F6F", "--")),
                  (("thermal_power", "Heat input", "#1F6F5F", "-"),
                   ("heat_out", "Heat out", "#6F6F6F", "--")),
                  (("input_energy", "Input", "#1F6F5F", "-"),
                   ("thermal_stored_energy", "Stored", "#6F6F6F", "--"),
                   ("output_energy", "Out", "#111111", ":"))]
        for ax, group, quantity, unit in zip(axes, groups, ("Temperature", "Power", "Energy"), ("K", "W", "J")):
            scale, display_unit = ((1., "K") if unit == "K" else
                _axis_scale([row.get(key, np.nan) for row in history.rows for key, *_ in group], unit))
            for key, label, color, style in group:
                values = np.asarray([row.get(key, np.nan) for row in history.rows], dtype=float)
                ax.plot(times / time_scale, values / scale, label=label, color=color, linestyle=style, linewidth=1.5,
                        drawstyle="steps-pre" if unit == "W" else "default")
            ax.set_ylabel(f"{quantity} ({display_unit})")
            ax.yaxis.set_major_locator(MaxNLocator(nbins=5))
            ax.grid(axis="y", alpha=.15)
            ax.legend(frameon=False, loc="upper left", ncols=len(group))
            # Reserve enough headroom for the in-panel key, including flat-top
            # pulse traces whose maximum extends beneath its labels.
            ax.margins(x=.02, y=.25)
        axes[-1].set_xlabel(f"Time ({time_unit})")
        axes[-1].xaxis.set_major_locator(MaxNLocator(nbins=6))
        caption = "Transient thermal response"
        if context["energy_scope"] == "cumulative_from_origin":
            caption += " · resumed segment\nEnergy is cumulative from the original run"
        if context["stop_reason"] == "periodic_convergence":
            caption += " · periodic convergence"
        if not history.completed:
            caption += " · incomplete"
        axes[0].set_title(caption, loc="left")
        for extension in ("svg", "png"):
            filename = "transient_history." + extension
            fig.savefig(root / filename, dpi=220)
            files.append(filename)
        plt.close(fig)
    return files
