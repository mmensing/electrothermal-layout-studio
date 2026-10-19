# SPDX-License-Identifier: MPL-2.0
"""Engineering temperature acceptance, independent of numerical convergence.

Evaluate accepted physical states only. A maximum of a continuous P1 field is
attained at a vertex of its exact (possibly clipped) support; no centroid,
colormap, nodal smoothing or averaging participates in this decision.
"""
from __future__ import annotations

import math
from copy import deepcopy

import numpy as np

from etlayout.models import Diagnostic, SupportSelector

PREFIX = "Maximum temperature limit:"


def validate_temperature_limits(project):
    errors = []
    seen = set()
    layers = {layer.id for layer in project.layers}
    for limit in project.temperature_limits:
        def error(message, limit_id=limit.id):
            errors.append(Diagnostic("error", message, limit_id))
        if not limit.id or limit.id in seen:
            error("Temperature limits require unique nonempty IDs")
        seen.add(limit.id)
        if not math.isfinite(limit.maximum_temperature) or limit.maximum_temperature <= 0:
            error("Maximum temperature must be finite and above zero kelvin; store K, not Celsius")
        if limit.evaluation not in {"fail_after", "stop_transient"}:
            error("Temperature limit evaluation must be fail_after or stop_transient")
        if len(set(limit.layer_ids)) != len(limit.layer_ids) or set(limit.layer_ids) - layers:
            error("Temperature limit layer IDs must be unique and refer to existing physical layers")
        if limit.geometry is not None:
            from etlayout.geometry.prepare import resolve_region
            try:
                if limit.geometry.line:
                    error("Temperature limits require area support, not a zero-area line")
                elif not (limit.geometry.full_domain or limit.geometry.sources):
                    if resolve_region(limit.geometry, {}).is_empty:
                        error("Temperature limit region is empty; select an area or remove its region constraint")
            except (ValueError, RuntimeError) as exc:
                error(f"Invalid temperature limit region: {exc}")
    return errors


def evaluate_temperature_limits(project, result, *, time_s=None, _support_cache=None):
    enabled = [limit for limit in project.temperature_limits if limit.enabled]
    report = {"version": 1, "status": "disabled", "limits": [],
              "first_violation": None, "stopped_early": False,
              "peak_verification": "raw P1 maximum; mesh/time verification is independent"}
    if not enabled:
        return report
    if "thermal_power" not in result.metrics or not any("temperature" in fields for fields in result.fields.values()):
        report["status"] = "not_applicable"
        return report
    cache = {} if _support_cache is None else _support_cache
    for limit in enabled:
        selected = {layer: result.mesh.elements(layer) for layer, fields in result.fields.items()
                    if "temperature" in fields and (not limit.layer_ids or layer in limit.layer_ids)}
        clips = dict.fromkeys(selected)
        if limit.geometry is not None:
            from etlayout.power.analysis import selected_support
            selected, clips = selected_support(project, result,
                SupportSelector(layers=list(selected), geometry=limit.geometry), _cache=cache)
        peak = None
        missing_support = False
        for layer, ids in selected.items():
            local = result.element_fields.get(layer, {}).get("temperature")
            if local is None:
                nodal = result.fields.get(layer, {}).get("temperature")
                if nodal is None:
                    continue
                local = np.asarray(nodal)[result.mesh.triangles]
            values = np.asarray(local)[ids]
            xy = result.mesh.points[result.mesh.triangles[ids]]
            if clips.get(layer) is not None:
                from etlayout.power.analysis import _patches
                patches, values, _ = _patches(result, ids, local, clips[layer], _cache=cache)
                xy = np.asarray([np.asarray(p.exterior.coords)[:3] for p in patches])
            if not values.size or not np.all(np.isfinite(values)):
                missing_support = True
                continue
            index = np.unravel_index(np.argmax(values), values.shape)
            actual = float(values[index])
            if peak is None or actual > peak["temperature"]:
                peak = {"temperature": actual, "layer_id": layer,
                        "location": xy[index].tolist(), "time_s": time_s,
                        "reference_temperature": result.metrics.get("thermal_reference"),
                        "reference": result.analysis.get("thermal_reference", {}),
                        "temperature_rise": actual - result.metrics["thermal_reference"]
                            if result.metrics.get("thermal_reference") is not None else None}
        record = {"id": limit.id, "maximum_temperature": limit.maximum_temperature,
                  "evaluation": limit.evaluation, "status": "unavailable", "peak": peak}
        if peak is not None and not missing_support:
            record.update(status="failed" if peak["temperature"] >= limit.maximum_temperature else "passed",
                          excess=max(0., peak["temperature"] - limit.maximum_temperature))
            if record["status"] == "failed" and report["first_violation"] is None:
                report["first_violation"] = deepcopy(record)
        report["limits"].append(record)
    report["status"] = ("failed" if any(r["status"] == "failed" for r in report["limits"]) else
                        "unavailable" if any(r["status"] == "unavailable" for r in report["limits"]) else "passed")
    return report


def accumulate_temperature_limits(previous, current):
    """Keep each limit's highest accepted temperature and earliest observed failure."""
    if previous is None:
        return deepcopy(current)
    combined = deepcopy(previous)
    for old, new in zip(combined["limits"], current["limits"]):
        if new["peak"] is not None and (old["peak"] is None or new["peak"]["temperature"] > old["peak"]["temperature"]):
            old.update(deepcopy(new))
        if new["status"] == "unavailable":
            old["status"] = "unavailable"
    combined["first_violation"] = previous["first_violation"] or deepcopy(current["first_violation"])
    combined["status"] = "failed" if combined["first_violation"] else current["status"]
    return combined


def stop_requested(report):
    return any(row["status"] == "failed" and row["evaluation"] == "stop_transient"
               for row in report["limits"])


def finalize_temperature_limits(project, result):
    report = result.metrics.get("thermal_limit_compliance") if result.transient else None
    if report is None:
        report = evaluate_temperature_limits(project, result, time_s=result.metrics.get("time_s"))
    result.metrics["thermal_limit_compliance"] = report
    result.metrics["engineering_accepted"] = bool(result.converged and report["status"] not in {"failed", "unavailable"})
    result.diagnostics = [d for d in result.diagnostics if not d.message.startswith(PREFIX)]
    if report["status"] in {"failed", "unavailable"}:
        for row in report["limits"]:
            if row["status"] == "failed":
                peak = row["peak"]
                time_text = f" at accepted time {peak['time_s']:.9g} s (sampled state, not a localized crossing)" if peak["time_s"] is not None else ""
                result.diagnostics.append(Diagnostic("error", f"{PREFIX} {row['id']}: "
                    f"{peak['temperature']:.9g} K >= {row['maximum_temperature']:.9g} K; "
                    f"excess {row['excess']:.9g} K on {peak['layer_id']}{time_text}. "
                    "Engineering acceptance failed; numerical convergence and retained fields are unchanged.",
                    peak["layer_id"], [*peak["location"], *peak["location"]]))
            elif row["status"] == "unavailable":
                result.diagnostics.append(Diagnostic("error", f"{PREFIX} {row['id']}: no finite thermal support; "
                    "inspect its layer/region selection. Engineering acceptance cannot be established.", row["id"]))
