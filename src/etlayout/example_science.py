# SPDX-License-Identifier: MPL-2.0
"""Source-linked scientific summaries for exact configured example calculations.

These records describe evidence; a converged solve never upgrades a synthetic
material model to a calibrated device or certifies a raw corner maximum.
"""
from __future__ import annotations

import json
from copy import deepcopy
from dataclasses import asdict

import numpy as np

from etlayout.example_catalog import example_info
from etlayout.io.project import configuration_hash, physics_hash
from etlayout.models import SimulationError


def _finite_range(values):
    values = np.asarray(values, dtype=float)
    if values.ndim == 2:
        values = np.linalg.norm(values, axis=1)
    values = values[np.isfinite(values)]
    return [float(values.min()), float(values.max())] if len(values) else None


def scientific_record(project, result):
    """Summarize authoritative fields/ledgers, refusing mismatched physical input.

    Missing measurements remain null, not zero. Verification status is explicit:
    this function observes one mesh/time grid and cannot establish convergence.
    """
    if result.configuration_hash != configuration_hash(project) or result.physics_hash != physics_hash(project):
        raise SimulationError("Example evidence requires the exact solved configuration; recalculate edited inputs")
    from etlayout.geometry.prepare import polygon_parts, prepare_geometry
    from etlayout.meshing.refinement import contact_transfer_estimate

    geometry = prepare_geometry(deepcopy(project))
    info = example_info(project.name)
    assumptions = {}
    for marker in ("\n\nExample configuration (SI):\n", "\n\nDevice example configuration (SI):\n"):
        if marker in project.notes:
            assumptions = json.loads(project.notes.split(marker, 1)[1])
            break
    metrics = result.metrics
    budget = deepcopy(result.analysis.get("loss_budget", {}))
    layers = []
    validity = []
    for layer in project.layers:
        material = project.materials[layer.material_id]
        fields = result.fields.get(layer.id, {})
        temperature = _finite_range(fields.get("temperature", []))
        properties = {name: asdict(getattr(material, name)) for name in ("resistivity", "thermal_conductivity")}
        outside = []
        if temperature:
            for name, prop in properties.items():
                lower, upper = prop["minimum_temperature"], prop["maximum_temperature"]
                if temperature[0] < lower or temperature[1] > upper:
                    outside.append(name)
        if outside:
            validity.append({"layer": layer.id, "outside_property_ranges": outside})
        shape = geometry.layers[layer.id]
        layers.append(dict(id=layer.id, material=layer.material_id, role=layer.role,
            bounds_m=list(shape.bounds), area_m2=shape.area, connected_components=len(list(polygon_parts(shape))),
            thickness_m=layer.thickness, z_m=layer.z, material_source=material.source,
            material_notes=material.notes, properties=properties, electrical=asdict(layer.electrical),
            temperature_range_K=temperature,
            field_ranges={key: _finite_range(fields[key]) for key in
                ("sheet_current", "current_density", "electric_field", "joule_density")
                if key in fields and (key != "current_density" or layer.electrical.kind == "bulk")},
            current_density_interpretation="K / physical thickness" if layer.electrical.kind == "bulk" else
                "Report sheet current; no bulk volumetric loading is inferred from an effective sheet/2DEG thickness"))
    contact_resolution = []
    for interface in project.interfaces:
        if not interface.enabled or interface.electrical_kind != "specific" or interface.purpose == "device_load":
            continue
        ids = result.mesh.regions.get("interface:" + interface.id, [])
        vertices = result.mesh.points[result.mesh.triangles[ids]] if len(ids) else np.empty((0, 3, 2))
        diameter = float(np.max(np.linalg.norm(vertices - np.roll(vertices, 1, axis=1), axis=2))) if len(ids) else None
        for layer_id in (interface.layer_a, interface.layer_b):
            if project.layer(layer_id).role != "conductor":
                continue
            length, estimate = contact_transfer_estimate(project, interface, layer_id, [result])
            contact_resolution.append(dict(interface=interface.id, layer=layer_id, transfer_length_m=length,
                estimate=estimate, maximum_contact_triangle_edge_m=diameter,
                four_elements_per_length_screen=bool(length and diameter and diameter <= length / 4),
                interpretation="Conservative maximum-edge screen, not directional error or mesh verification; anisotropic triangles need direction-aware review"))
    thermal = {key: deepcopy(metrics.get(key)) for key in (
        "temperature_max", "temperature_min", "temperature_average", "temperature_rise", "thermal_reference",
        "thermal_power", "heat_out", "thermal_resistance", "thermal_balance_error", "hotspot",
        "transient_peak_temperature", "transient_peak_time", "transient_peak_retained",
        "transient_completed", "transient_requested_stop_time", "transient_actual_stop_time",
        "transient_input_energy", "transient_output_energy", "transient_stored_energy",
        "transient_energy_balance_error")}
    deposited = budget.get("thermal_deposition")
    thermal["prescribed_power_W"] = (metrics["thermal_power"] - deposited
        if deposited is not None and "thermal_power" in metrics else
        metrics.get("thermal_power") if project.solver.mode == "thermal" else None)
    if result.transient is not None:
        thermal["transient_actual_stop_time"] = metrics.get("thermal_limit_compliance", {}).get("actual_stop_time", metrics.get("transient_time"))
        peak_index = metrics.get("transient_peak_snapshot_index")
        peak_frame = (result.transient.snapshots[peak_index] if isinstance(peak_index, int)
                      and 0 <= peak_index < len(result.transient.snapshots) else None)
        thermal["peak_state"] = ({key: deepcopy(peak_frame.metrics.get(key)) for key in (
            "time_s", "total_current", "voltage_drop", "electrical_power", "joule_power", "contact_power",
            "external_feed_power", "thermal_power", "heat_out", "thermal_storage_rate", "thermal_stored_energy",
            "applied_drives")} if peak_frame is not None else None)
    thermal["initial_temperature_K"] = project.solver.initial_temperature if project.transient.enabled else None
    thermal["reference_boundary"] = project.power.thermal_reference_boundary or None
    thermal["reservoirs"] = deepcopy(result.analysis.get("reservoirs", {}))
    thermal["temperature_nonuniformity_K"] = (metrics["temperature_max"] - metrics["temperature_min"]
        if "temperature_max" in metrics and "temperature_min" in metrics else None)
    model_failures = []
    if assumptions.get("model_temperature_range_K") and metrics.get("transient_peak_temperature", metrics.get("temperature_max")) is not None:
        maximum = metrics.get("transient_peak_temperature", metrics.get("temperature_max"))
        if maximum > assumptions["model_temperature_range_K"][1]:
            model_failures.append("declared_temperature_range")
    if assumptions.get("maximum_channel_field_V_m"):
        for layer in layers:
            if layer["electrical"]["kind"] == "hemt":
                interval = layer["field_ranges"].get("electric_field")
                if interval and interval[-1] > assumptions["maximum_channel_field_V_m"]:
                    model_failures.append(layer["id"] + ":low_field_transport_limit")
    return dict(schema_version=1, id=info.id, name=info.name, purpose=info.learning_goal,
        evidence_level=info.evidence_level, model_scope=info.physical_scope,
        calibration="Synthetic geometry and representative material seeds; no experimental device calibration",
        assumptions=assumptions, supported_conclusions=info.learning_goal,
        unsupported_claims="No TCAD, breakdown, switching, lifetime, full 3D heat flow or experimental calibration",
        recommended_action="Review actual diagnostics, declared material/field ranges and physically supported mesh/time studies before engineering use",
        layout_sha256=project.layout.sha256, configuration_hash=result.configuration_hash,
        physics_hash=result.physics_hash, analysis_mode=project.solver.mode,
        electrical={key: deepcopy(metrics.get(key)) for key in (
            "total_current", "voltage_drop", "effective_resistance", "electrical_power", "joule_power",
            "contact_power", "external_feed_power", "terminal_power", "current_balance_error", "power_balance_error")},
        terminals=deepcopy(result.terminals), ports=deepcopy(result.analysis.get("ports", {})),
        loss_budget=budget, thermal=thermal, layers=layers,
        geometry=dict(domain_bounds_m=list(geometry.domain.bounds), dbu_um=project.layout.dbu_um,
            diagnostics=[asdict(item) for item in geometry.diagnostics]),
        interfaces=[asdict(item) for item in project.interfaces],
        boundaries=[asdict(item) for item in project.thermal_boundaries],
        prescribed_sources=[asdict(item) for item in project.heat_sources],
        contact_resolution=contact_resolution,
        devices=deepcopy(result.analysis.get("devices", {})), measurements=deepcopy(result.analysis.get("measurements", {})),
        status=dict(numerically_converged=result.converged, engineering_accepted=metrics.get("engineering_accepted"), mesh_verified=False,
            time_verified=False if project.transient.enabled else None,
            property_range_violations=validity, declared_model_violations=model_failures, calibration_verified=False,
            measurement_failures=[key for key, value in result.analysis.get("measurements", {}).items()
                if value.get("status") != "available" or not value.get("within_limits", True)],
            thermal_limit_compliance=deepcopy(metrics.get("thermal_limit_compliance")),
            interpretation="One configured mesh/time grid; no certification of singular raw peaks"),
        mesh=dict(nodes=len(result.mesh.points), elements=len(result.mesh.triangles),
            settings=asdict(project.mesh), statistics=deepcopy(result.mesh.statistics)),
        performance=deepcopy(result.execution), diagnostics=[asdict(item) for item in result.diagnostics],
        units=dict(length="m", temperature="K", temperature_rise="K", power="W", current="A",
            voltage="V", resistance="ohm", sheet_current="A/m", current_density="A/mÃƒÆ’Ã¢â‚¬Å¡Ãƒâ€šÃ‚Â²",
            electric_field="V/m", joule_density="W/mÃƒÆ’Ã¢â‚¬Å¡Ãƒâ€šÃ‚Â²", time="s", energy="J"))
