# SPDX-License-Identifier: MPL-2.0
"""Read-only engineering interpretation of authoritative recorded results.

This module performs no solve, changes no acceptance policy and owns no Qt
objects. Independent evidence axes never turn a converged field into a physical
qualification. Scoped quantities use the existing exact-support measurements.
"""
from __future__ import annotations

import math
from copy import deepcopy

import numpy as np
from shapely import polygons, union_all

from etlayout.io.project import project_from_dict
from etlayout.models import MeasurementSpec, RegionSpec, SimulationError, SupportSelector, checkpoint


def _number(value):
    return isinstance(value, (int, float, np.number)) and not isinstance(value, bool) and math.isfinite(value)


def engineering_summary(result, *, run_metrics=None, completed=None):
    """Summarize one displayed physical state and explicitly scoped run metadata."""
    project = project_from_dict(result.configuration)
    metrics = result.metrics
    run = run_metrics if run_metrics is not None else metrics
    axes = [{"axis": "Numerical solve", "status": "passed" if result.converged else "failed",
             "detail": "Recorded PDE convergence for the displayed physical state; independent of engineering limits."}]
    balances = []
    for key, tolerance in (("current_balance_error", "current_balance_tolerance"),
                           ("power_balance_error", "power_balance_tolerance"),
                           ("thermal_balance_error", "thermal_balance_tolerance")):
        if _number(metrics.get(key)):
            limit = getattr(project.solver, tolerance)
            balances.append(dict(metric=key, value=metrics[key], limit=limit, passed=metrics[key] <= limit))
    axes.append({"axis": "Conservation", "status": "passed" if balances and all(row["passed"] for row in balances)
                 else "failed" if balances else "unavailable", "detail": "Displayed-state residuals against recorded solver criteria.",
                 "checks": balances})
    compliance = run.get("thermal_limit_compliance", metrics.get("thermal_limit_compliance", {}))
    axes.append({"axis": "Temperature limits", "status": compliance.get("status", "not_configured"),
                 "detail": "Accepted physical states; limit compliance does not alter numerical convergence.",
                 "evidence": deepcopy(compliance)})
    violations = []
    seeds = []
    checked_temperature = False
    for layer in project.layers:
        ids = result.mesh.elements(layer.id)
        if not len(ids) or layer.id not in result.fields:
            continue
        material = project.materials[layer.material_id]
        if material.reference or any(word in (material.notes + " " + material.source).lower()
                                     for word in ("uncalibrated", "synthetic", "representative", "illustrative")):
            seeds.append(material.id)
        local = result.element_fields.get(layer.id, {}).get("temperature")
        if local is None and "temperature" in result.fields[layer.id]:
            local = np.asarray(result.fields[layer.id]["temperature"])[result.mesh.triangles]
        if local is None:
            continue
        checked_temperature = True
        values = np.asarray(local)[ids]
        if not np.all(np.isfinite(values)):
            violations.append(dict(layer=layer.id, reason="Nonfinite active temperature support"))
            continue
        low, high = float(values.min()), float(values.max())
        laws = [("thermal conductivity", material.thermal_conductivity)] if "thermal_power" in metrics else []
        if layer.role == "conductor":
            if layer.electrical.kind == "bulk":
                laws.append(("bulk resistivity", material.resistivity))
            elif layer.electrical.kind == "sheet":
                laws.append(("sheet resistance", layer.electrical.sheet_resistance))
        for name, law in laws:
            if low < law.minimum_temperature or high > law.maximum_temperature:
                violations.append(dict(layer=layer.id, material=material.id, property=name,
                    minimum_temperature=low, maximum_temperature=high,
                    declared_range=[law.minimum_temperature, law.maximum_temperature]))
    axes.append({"axis": "Material/model range", "status": "outside_declared_range" if violations else "within_declared_ranges" if checked_temperature else "unavailable",
                 "detail": "Displayed-state declared property ranges only; no validation of high-field transport, breakdown or full 3D heat flow.",
                 "violations": violations, "physical_qualification": False})
    mesh = result.analysis.get("local_mesh_verification", {})
    axes.append({"axis": "Mesh confidence", "status": "selected_metric_evidence" if mesh.get("status") == "available" else "not_established",
                 "detail": mesh.get("recommendation") or "A single generated mesh does not establish convergence; raw peaks remain unqualified.",
                 "evidence": deepcopy(mesh)})
    time_study = {key: value for key, value in run.items() if key.startswith("time_study_")
                  or key.startswith("transient_") and key.endswith(("_relative_change", "_absolute_change"))}
    transient = "time_s" in metrics or "transient_peak_temperature" in run
    axes.append({"axis": "Time confidence", "status": "study_available" if time_study else "not_established" if transient else "not_applicable",
                 "detail": "Recorded time-refinement changes are evidence, not an automatic certification.", "evidence": time_study})
    if completed is None and result.transient is not None:
        completed = result.transient.completed
    stop = run.get("transient_stop_reason")
    axes.append({"axis": "Requested duration", "status": "complete" if completed is True else "stopped_early" if completed is False
                 else "recorded_stop" if transient and stop else "not_applicable" if not transient else "unavailable",
                 "detail": str(stop or "Steady calculation"), "requested_stop_s": run.get("transient_requested_stop_time"),
                 "actual_stop_s": run.get("transient_time")})
    axes.append({"axis": "Calibration", "status": "uncalibrated_seeds" if seeds else "not_established",
                 "detail": ("Reference/synthetic material seeds present: " + ", ".join(sorted(set(seeds))) if seeds else
                 "User-supplied properties do not by themselves establish experimental calibration.") +
                 "; thermal boundaries/interfaces also require measured calibration, not established by configuration.", "physical_qualification": False})
    # The same estimate used by mesh guidance/optimization. This is an L_T
    # estimate from the recorded constitutive state, never resolved-3D evidence.
    from etlayout.meshing.refinement import contact_transfer_estimate
    transfer = {}
    for interface in project.interfaces:
        if interface.electrical_kind == "specific":
            for layer_id in (interface.layer_a, interface.layer_b):
                if layer_id in result.fields:
                    value, definition = contact_transfer_estimate(project, interface, layer_id, [result])
                    transfer[interface.id + "/" + layer_id] = dict(value_m=value, definition=definition)
    hemt = any(layer.electrical.kind == "hemt" for layer in project.layers)
    device_load = any(link.purpose == "device_load" or link.electrical_kind == "load" for link in project.interfaces)
    sic = any("sic" in (layer.material_id + project.materials[layer.material_id].name).lower() for layer in project.layers)
    vias = any(link.electrical_kind == "via" or link.model_kind == "physical_via" for link in project.interfaces)
    family = ("GaN HEMT" if hemt else "SiC on-state interconnect" if device_load and sic else
              "Distributed device load" if device_load else "Package / multilayer interconnect" if vias else
              "Thermal device" if project.solver.mode == "thermal" else "Contacts / transfer" if project.interfaces else "Metal conduction")
    return {"version": 1, "configuration_hash": result.configuration_hash,
        "time_s": metrics.get("time_s"), "frame_temperature_max_K": metrics.get("temperature_max"),
        "run_peak_temperature_K": run.get("transient_peak_temperature"), "run_peak_time_s": run.get("transient_peak_time"),
        "peak_scope": run.get("transient_peak_scope", "whole_run"), "axes": axes, "family": family,
        "metrics": {key: metrics[key] for key in ("total_current", "voltage_drop", "effective_resistance", "electrical_power",
            "thermal_power", "heat_out", "thermal_reference", "temperature_rise", "thermal_resistance",
            "device_current_sharing_error", "device_source_voltage_spread", "temperature_max", "temperature_min") if key in metrics},
        "run_energy": {key: run[key] for key in ("transient_input_energy", "transient_output_energy", "transient_stored_energy",
            "transient_energy_balance_error") if key in run},
        "contact_transfer": transfer, "devices": deepcopy(result.analysis.get("devices", {})), "contacts": deepcopy(result.analysis.get("contacts", {})),
        "loss_budget": deepcopy(result.analysis.get("loss_budget", {})),
        "crowding": deepcopy(result.analysis.get("current_crowding", {})),
        "heat_deposition": deepcopy(result.analysis.get("heat_deposition", {}))}


def scoped_engineering(result, layer_id, *, region="", geometry=None, window=None, view_context=None, progress=None, cancel=None):
    """Link E/K/J/heat/T on identical exact support using existing measurements.

    ``window`` is an optional SI RegionSpec (e.g. a fixed square around a probe).
    Contact generation and allocated layer heat remain separate quantities.
    """
    from etlayout.power.analysis import evaluate_measurement, selected_support
    from etlayout.power.crowding import local_crowding

    project = project_from_dict(result.configuration)
    layer = project.layer(layer_id)
    if window is not None and isinstance(window, dict):
        from etlayout.io.project import from_dict
        window = from_dict(RegionSpec, window)
    selector = SupportSelector(layers=[layer_id], regions=[region] if region else [], geometry=window)
    selected, clips = selected_support(project, result, selector)
    ids = selected.get(layer_id, np.array([], dtype=int))
    if not len(ids):
        raise SimulationError("Selected engineering support is empty")
    clip = clips.get(layer_id)
    support = union_all(polygons(result.mesh.points[result.mesh.triangles[ids]]))
    if clip is not None:
        support = support.intersection(clip)
    quantities = [("potential", "Potential"), ("electric_field", "Electric field magnitude"),
        ("sheet_current", "Sheet current"), ("joule_density", "Lateral Joule heat"),
        ("contact_density", "Allocated interface heat"), ("heat_density", "Total applied layer heat"),
        ("temperature", "Temperature"), ("temperature_rise", "Rise above named reservoir")]
    if layer.electrical.kind == "bulk" and layer.role == "conductor":
        quantities.insert(3, ("metal_current_density", "Volumetric current density"))
    elif layer.electrical.kind == "semiconductor" and layer.role == "conductor":
        quantities.insert(3, ("effective_current_density", "Volumetric semiconductor current (specified thickness)"))
    cache, rows = {}, []
    for index, (quantity, label) in enumerate(quantities):
        checkpoint(cancel, progress, index / len(quantities), "Inspecting same support: " + label)
        reductions = ["mean", "maximum"] + (["integral"] if quantity in {"joule_density", "contact_density", "heat_density"} else [])
        values = {reduction: evaluate_measurement(project, result, MeasurementSpec("engineering", quantity, selector, reduction,
                    effective_thickness=layer.thickness if quantity == "effective_current_density" else None),
                                                  _support_cache=cache) for reduction in reductions}
        rows.append(dict(quantity=quantity, label=label, measurements=values))
    if region.startswith("interface:"):
        identifier = region.split(":", 1)[1]
        contact_selector = SupportSelector(contacts=[identifier], geometry=window)
        rows.append(dict(quantity="contact_heat_density", label="Generated interface heat (before deposition)",
            measurements={"integral": evaluate_measurement(project, result,
                MeasurementSpec("engineering_interface", "contact_heat_density", contact_selector, "integral"), _support_cache=cache)}))
    crowding = None
    if "sheet_current" in result.fields.get(layer_id, {}):
        crowding = local_crowding(project, result, layer_id, support=support, geometry=geometry)
    if (crowding is not None and layer.electrical.kind == "bulk" and result.converged
            and rows[0]["measurements"]["mean"]["status"] == "available"):
        # K = thickness * J is a useful layer readout for bulk conductors. Keep
        # the stricter sheet-current stress selector untouched: this is an
        # interpretation of the existing FEM field, never an acceptance test.
        row = next(item for item in rows if item["quantity"] == "sheet_current")
        for reduction, key in (("mean", "sheet_current_average_A_per_m"),
                               ("maximum", "raw_sheet_current_max_A_per_m")):
            row["measurements"][reduction] = dict(status="available", value=crowding[key], unit="A/m",
                reporting_only=True, within_limits=False, support_area_m2=crowding["area_m2"],
                reason="Thickness-integrated bulk current readout; use volumetric current for metal stress criteria.",
                mesh_sensitive_raw_maximum=reduction == "maximum")
    checkpoint(cancel, progress, 1., "Same-support engineering report complete")
    mesh_evidence = matching_mesh_evidence(result, layer_id, support)
    return {"version": 1, "configuration_hash": result.configuration_hash, "time_s": result.metrics.get("time_s"),
            "view_context": view_context, "layer_id": layer_id, "region": region, "support_wkt": support.wkt, "support_area_m2": support.area,
            "window": window.__dict__ if window is not None else None, "quantities": rows, "crowding": crowding,
            "mesh_evidence": mesh_evidence,
            "qualification": "Exact common planar support. Raw extrema remain mesh-sensitive; no physical qualification."}


def matching_mesh_evidence(result, layer_id, support):
    """Use selected-metric evidence only on its recorded exact physical support."""
    from etlayout.geometry.prepare import resolve_region
    from etlayout.io.project import from_dict

    receipt = result.analysis.get("local_mesh_verification", {})
    layer = receipt.get("layers", {}).get(layer_id, {})
    spec = layer.get("support")
    if receipt.get("status") != "available" or not spec:
        return {"status": "not_established", "reason": "No local mesh receipt for this layer/support"}
    region = from_dict(RegionSpec, spec)
    if region.sources or region.exclude_sources or region.full_domain:
        return {"status": "not_established", "reason": "Mesh receipt lacks self-contained physical support"}
    measured = resolve_region(region, {}, None)
    # Triangle unions have roundoff seams; this tolerance only compares support
    # geometry, never electrical/thermal acceptance or a physical metric value.
    tolerance = max(support.area, measured.area) * 1e-10
    if measured.is_empty or measured.symmetric_difference(support).area > tolerance:
        return {"status": "not_established", "reason": "Mesh receipt describes different physical support"}
    return {"status": "selected_metric_evidence", "metrics": deepcopy(layer.get("metrics", {})),
            "physical_qualification": False, "reason": receipt.get("recommendation", "Review each metric separately")}
