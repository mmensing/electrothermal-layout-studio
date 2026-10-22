# SPDX-License-Identifier: MPL-2.0
"""Readable headlines; display precision never changes acceptance or stored fields."""
from math import isfinite

from etlayout.gui.workspace import engineering


def result_headlines(result):
    if result is None:
        return "No selected result"
    metrics = result.metrics
    lines = [f"Selected time: {engineering(metrics['time_s'], 's')}"] if "time_s" in metrics else []
    for key, label, unit in (("total_current", "Delivered current", "A"),
                             ("voltage_drop", "Voltage drop", "V"),
                             ("effective_resistance", "Effective resistance", "Ω"),
                             ("electrical_power", "Electrical power", "W")):
        if key in metrics:
            lines.append(f"{label}: {engineering(metrics[key], unit)}")
    if "temperature_max" in metrics:
        try:
            temperature = float(metrics["temperature_max"])
            text = f"{temperature:.1f} K" if isfinite(temperature) else "Unavailable"
        except (TypeError, ValueError):
            text = "Unavailable"
        label = "Selected-time peak temperature" if "time_s" in metrics else "Peak temperature"
        lines.append(f"{label}: {text}")
    return "\n".join(lines) or "Open Metrics & loss budget for this run."
