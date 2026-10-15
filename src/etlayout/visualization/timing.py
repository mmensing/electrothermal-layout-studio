# SPDX-License-Identifier: MPL-2.0
"""Explicit temporal identity for snapshots and paired field comparisons."""
from __future__ import annotations

from copy import deepcopy


def snapshot_metadata(result):
    """Configuration hashes identify runs; time and drives identify a frame."""
    if result.metrics.get("time_s") is None:
        return {}
    return {"time_s": result.metrics["time_s"],
            "applied_drives": deepcopy(result.metrics.get("applied_drives", result.metrics.get("drive_values", {}))),
            "drive_time_convention": result.metrics.get("drive_time_convention")}


def difference_time_label(provenance):
    """Describe both timestamps without implying temporal alignment."""
    if "current_time_s" not in provenance and "comparison_time_s" not in provenance:
        return ""
    labels = []
    for label, key in (("Current", "current_time_s"), ("comparison", "comparison_time_s")):
        value = provenance.get(key)
        labels.append(f"{label} t = {value:.9g} s" if value is not None else f"{label} steady state")
    return "; ".join(labels)
