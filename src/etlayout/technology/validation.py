# SPDX-License-Identifier: MPL-2.0
from __future__ import annotations

import math

from etlayout.materials.database import validate_material
from etlayout.models import Diagnostic, Project, SimulationError


def validate_project(project: Project, raise_errors: bool = True, *, initial_state_supplied: bool = False) -> list[Diagnostic]:
    messages: list[Diagnostic] = []

    def error(message, obj=""):
        messages.append(Diagnostic("error", message, obj))

    if getattr(project, "_resource_unresolved", False):
        error("Project resources are unresolved; review resources or relink the layout before preparation or simulation", "layout")
    if not project.layers:
        error("Add at least one physical layer before simulating")
    for collection, label in [(project.layers, "layer"), (project.terminals, "terminal"),
                              (project.interfaces, "interface"), (project.thermal_boundaries, "thermal boundary"),
                              (project.heat_sources, "heat source")]:
        ids = [x.id for x in collection]
        if len(set(ids)) != len(ids) or any(not x for x in ids):
            error(f"Every {label} requires a unique nonempty ID")
    for key, material in project.materials.items():
        try:
            validate_material(material)
        except SimulationError as exc:
            error(str(exc), key)
    layer_ids = {x.id for x in project.layers}
    for layer in project.layers:
        if layer.material_id not in project.materials:
            error(f"Layer {layer.name} has no material snapshot '{layer.material_id}'", layer.id)
        if not math.isfinite(layer.thickness) or layer.thickness <= 0:
            error("Layer thickness must be finite and positive", layer.id)
        if not math.isfinite(layer.z):
            error("Layer z must be finite", layer.id)
        if layer.role not in {"conductor", "thermal", "insulator"}:
            error(f"Unknown layer role {layer.role}", layer.id)
        model = layer.electrical
        if model.kind not in {"bulk", "sheet", "semiconductor", "hemt"}:
            error(f"Unknown electrical model {model.kind}", layer.id)
        if model.kind in {"semiconductor", "hemt"} and (model.mobility <= 0 or model.reference_temperature <= 0):
            error("Mobility and its reference temperature must be positive", layer.id)
        if model.kind == "semiconductor" and model.carrier_density <= 0:
            error("Carrier density must be positive", layer.id)
        if model.kind == "hemt" and (model.smoothing_voltage <= 0 or model.gate_coupling <= 0 or
                                     not math.isfinite(model.maximum_sheet_density) or model.minimum_sheet_conductance <= 0):
            error("HEMT smoothing, gate coupling and background conductance must be positive; cap must be finite", layer.id)
    for interface in project.interfaces:
        if interface.layer_a not in layer_ids or interface.layer_b not in layer_ids:
            error("Interface refers to a missing layer", interface.id)
        elif interface.layer_a == interface.layer_b:
            error("An interface must join two different physical layers", interface.id)
        if interface.electrical_kind not in {"none", "ideal", "specific", "lumped", "via", "conductance", "load"}:
            error("Unknown electrical contact model", interface.id)
        if interface.thermal_kind not in {"none", "conductance", "resistance", "stack"}:
            error("Unknown thermal interface model", interface.id)
        if interface.electrical_kind not in {"none", "ideal", "via"} and interface.electrical_value <= 0:
            error("Contact resistance/conductance must be positive", interface.id)
        if interface.model_kind not in {"effective", "physical_via", "physical_contact"}:
            error("Interface model kind must be effective, physical_via or physical_contact", interface.id)
        if interface.height_mode not in {"explicit", "stack"}:
            error("Interface height mode must be explicit or stack", interface.id)
        if ((interface.model_kind == "physical_contact" and interface.height != 0 and interface.height_mode != "stack")
                or (interface.model_kind != "physical_contact" and interface.height <= 0 and interface.height_mode != "stack")
                or not math.isfinite(interface.height) or interface.thermal_resistance_area < 0):
            error("Bulk via height must be positive; physical contact height must be zero; thermal area resistance must be nonnegative", interface.id)
        if interface.resistance_semantics not in {"array", "instance"}:
            error("Lumped resistance semantics must be array or instance", interface.id)
        if interface.purpose not in {"connection", "device_load"}:
            error("Interface purpose must be connection or device_load", interface.id)
        if not math.isfinite(interface.temperature_coefficient) or not math.isfinite(interface.reference_temperature) or interface.reference_temperature <= 0:
            error("Interface temperature coefficient must be finite and its reference temperature positive", interface.id)
        if not math.isfinite(interface.heat_to_layer_a_fraction) or not 0 <= interface.heat_to_layer_a_fraction <= 1:
            error("Heat fraction to layer A must be finite and in [0, 1]", interface.id)
        if interface.heat_layer_id and interface.heat_layer_id not in layer_ids:
            error("Heat-deposition layer is missing; select an existing thermal sheet", interface.id)
        if interface.heat_layer_id and interface.heat_to_layer_a_fraction != .5:
            error("Named heat-deposition layer and endpoint split are alternatives; reset the split to 0.5", interface.id)
        if interface.electrical_kind == "via" and interface.material_id not in project.materials:
            error("Conductive via requires a material snapshot", interface.id)
        if interface.thermal_kind in {"conductance", "resistance"} and interface.thermal_value <= 0:
            error("Thermal interface value must be positive", interface.id)
    for terminal in project.terminals:
        if terminal.layer_id not in layer_ids:
            error("Terminal refers to a missing physical layer", terminal.id)
        elif terminal.kind != "gate" and project.layer(terminal.layer_id).role != "conductor":
            error("Electrical terminal must belong to a conducting layer", terminal.id)
        if terminal.kind not in {"voltage", "ground", "current", "density", "floating", "insulated", "gate", "probe"}:
            error("Unknown terminal condition", terminal.id)
        if not math.isfinite(terminal.value):
            error("Terminal value must be finite", terminal.id)
    for boundary in project.thermal_boundaries:
        if boundary.layer_id not in layer_ids:
            error("Thermal boundary refers to a missing layer", boundary.id)
        if boundary.kind not in {"temperature", "coefficient", "resistance_area", "resistance", "insulated"}:
            error("Unknown thermal boundary condition", boundary.id)
        if boundary.kind != "insulated" and (boundary.value <= 0 or boundary.ambient <= 0):
            error("Thermal boundary coefficient/resistance/temperature and ambient must be positive", boundary.id)
    for heat in project.heat_sources:
        if heat.layer_id not in layer_ids:
            error("Heat source refers to a missing layer", heat.id)
        if heat.kind not in {"density", "total"} or not math.isfinite(heat.value):
            error("Heat source must use total W or density W/m² with a finite value", heat.id)
    mesh = project.mesh
    if not 0 < mesh.min_size <= mesh.max_size or mesh.contact_size <= 0:
        error("Mesh sizes require 0 < minimum ≤ maximum and positive contact size")
    if mesh.max_elements < 1 or mesh.max_dofs < 1:
        error("Mesh budgets must be positive")
    solver = project.solver
    if solver.mode not in {"electrical", "thermal", "electrothermal"}:
        error("Simulation mode must be electrical, thermal or electrothermal")
    if solver.max_iterations < 1 or not 0 < solver.relaxation <= 1:
        error("Positive iteration count and relaxation in (0, 1] required")
    if solver.initial_temperature <= 0 or solver.absolute_tolerance <= 0 or solver.relative_tolerance <= 0:
        error("Temperature and solver tolerances must be positive")
    if solver.linear_solver not in {"direct", "cg"}:
        error("Linear solver must be direct or cg")
    if solver.floating_policy not in {"error", "reference"}:
        error("Floating policy must be error or reference")
    if project.transient.enabled:
        from etlayout.solvers.transient import validate_transient
        try:
            validate_transient(project, initial_state_supplied=initial_state_supplied)
        except SimulationError as exc:
            error(str(exc), "transient")
        for layer in project.layers:
            material = project.materials.get(layer.material_id)
            if material and (not math.isfinite(material.density) or material.density <= 0
                             or not math.isfinite(material.specific_heat) or material.specific_heat <= 0):
                error("Transient density (kg/m³) and specific heat (J/kg/K) must be finite and positive", layer.id)
    from etlayout.simulation.temperature import validate_temperature_limits
    messages.extend(validate_temperature_limits(project))
    from .power_validation import validate_power_settings
    messages.extend(validate_power_settings(project))
    if raise_errors and any(x.severity == "error" for x in messages):
        raise SimulationError("\n".join(f"{x.object_id + ': ' if x.object_id else ''}{x.message}"
                                        for x in messages if x.severity == "error"))
    return messages
