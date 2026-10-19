# SPDX-License-Identifier: MPL-2.0
"""Reviewable local sizing and bounded flux indicators, not guaranteed error bounds.

Supports are exact prepared geometry. Estimates deliberately count overlapping
refinements independently; they are planning screens, not peak-RSS guarantees.
"""
from __future__ import annotations

import math
from copy import deepcopy
from dataclasses import asdict, dataclass, field

import numpy as np
from shapely import points, polygons, union_all
from shapely.geometry import box

from etlayout.geometry.prepare import geometry_region, polygon_parts, prepare_geometry
from etlayout.io.project import configuration_hash
from etlayout.models import (
    MeasurementSpec,
    RefinementRegion,
    RegionSpec,
    SimulationError,
    SupportSelector,
    checkpoint,
)


@dataclass
class RefinementSuggestion:
    id: str
    purpose: str
    target: str
    geometry: RegionSpec
    size: float
    reason: str
    length: float
    grading_length: float = 0.


@dataclass
class RefinementPlan:
    configuration_hash: str
    suggestions: list[RefinementSuggestion] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    max_elements: int = 250000
    max_dofs: int = 500000
    max_memory_bytes: int = 4 * 1024**3
    sharing_groups: list[str] = field(default_factory=list)
    hotspot_measurements: list[str] = field(default_factory=list)
    generated_hotspot: MeasurementSpec | None = None
    indicators: dict = field(default_factory=dict)

    def to_dict(self):
        return {"version": 1, **asdict(self)}

    @classmethod
    def from_dict(cls, data):
        if not isinstance(data, dict) or data.get("version") != 1:
            raise SimulationError("Local mesh plan requires supported version 1")
        try:
            values = {key: deepcopy(value) for key, value in data.items() if key != "version"}
            values["suggestions"] = [RefinementSuggestion(**{**value,
                "geometry": RegionSpec(**value["geometry"])}) for value in values["suggestions"]]
            if values.get("generated_hotspot") is not None:
                value = values["generated_hotspot"]
                selector = {**value["selector"]}
                if selector.get("geometry") is not None:
                    selector["geometry"] = RegionSpec(**selector["geometry"])
                values["generated_hotspot"] = MeasurementSpec(**{**value, "selector": SupportSelector(**selector)})
            return cls(**values)
        except (TypeError, KeyError) as error:
            raise SimulationError("Malformed local mesh plan; regenerate with mesh-plan") from error


def _groups(project):
    buckets = {}
    for group in project.power.devices:
        dimensions = (group.active_width, group.active_area)
        if max(dimensions) > 0:
            buckets.setdefault(dimensions, []).append(group.id)
    # Do not mix parent banks with their constituent fingers or area/width loads.
    return max(buckets.values(), key=len, default=[]) if buckets else []


def contact_transfer_estimate(project, interface, layer_id, states=()):
    """Shared with optimizer verification; shortest recorded directional L_T."""
    from etlayout.physics.constitutive import sheet_conductance

    layer = project.layer(layer_id)
    conductances = []
    estimate = "minimum local directional sheet conductance across coarse scenarios"
    for state in states:
        values = state.fields.get(layer_id, {}).get("sheet_conductance")
        elements = state.mesh.regions.get("interface:" + interface.id, [])
        if values is not None and len(elements):
            selected = np.asarray(values)[elements]
            valid = selected[np.isfinite(selected) & (selected > 0)]
            if valid.size:
                conductances.append(float(valid.min()))
    if not conductances and layer.electrical.kind in {"bulk", "sheet", "semiconductor"}:
        conductances.extend(sheet_conductance(layer, project.materials[layer.material_id],
            np.array([project.solver.initial_temperature])).ravel())
        estimate = "constitutive sheet conductance at declared initial temperature"
    specific = interface.electrical_value
    if interface.electrical_kind == "via":
        from etlayout.materials.models import evaluate_property
        material = project.materials.get(interface.material_id)
        if material is None or interface.height <= 0:
            return None, "Via transfer estimate requires a material and positive physical height"
        temperatures = [project.solver.initial_temperature]
        from etlayout.visualization.sampling import element_nodal_values

        for state in states:
            elements = np.asarray(state.mesh.regions.get("interface:" + interface.id, []), dtype=int)
            if not len(elements) or any("temperature" not in state.fields.get(key, {})
                                        for key in (interface.layer_a, interface.layer_b)):
                continue
            # Match electrical._conductance: both component-local P1 traces,
            # averaged per interface element. Unrelated hot islands and the
            # lossy legacy shared-node view cannot set this contact's scale.
            local = [element_nodal_values(state, key, "temperature")[elements].mean(axis=1)
                     for key in (interface.layer_a, interface.layer_b)]
            values = (local[0] + local[1]) / 2
            temperatures.extend(values[np.isfinite(values)].tolist())
        # The shortest L_T over recorded temperatures is a conservative sizing
        # choice, not a prediction of a universal contact transfer length.
        specific = float(np.min(evaluate_property(material.resistivity, np.asarray(temperatures)))) * interface.height
        estimate += "; via rho(T) × physical height at minimum sampled resistivity"
    if not conductances or specific <= 0:
        if layer.electrical.kind == "hemt":
            return None, "solve the biased HEMT before estimating contact transfer length"
        return None, "Local sheet conductance unavailable for transfer-length estimate"
    return math.sqrt(specific * min(conductances)), estimate


def _physical_selector_support(project, result, geometry, selector):
    """Resolve physical sizing boundaries without inherited triangulation seams.

    Numerical triangle unions can retain roundoff-sized seams along an otherwise
    straight boundary. Feeding those seams back into the PSLG creates needle
    elements. Physical selectors use prepared geometry; element IDs only identify
    disconnected parts. Explicitly mesh-defined regions retain their cell edges.
    The measurement path validates selector identities/cycles before this call.
    """
    from etlayout.power.analysis import _region_geometry
    from etlayout.visualization.support import region_elements

    support = {layer.id: geometry.layers[layer.id] for layer in project.layers
               if layer.id in result.fields and layer.id in geometry.layers
               and (not selector.layers or layer.id in selector.layers)
               and (not selector.materials or layer.material_id in selector.materials)
               and (not selector.nets or layer.net_id in selector.nets)}
    regions = {prefix + ":" + key: shape for prefix, group in (
        ("interface", geometry.interfaces), ("terminal", geometry.terminals),
        ("thermal", geometry.thermal_boundaries), ("heat", geometry.heat_sources),
        ("refinement", geometry.refinements)) for key, shape in group.items()}

    def selected_parts(shape, ids):
        centers = points(result.mesh.points[result.mesh.triangles[ids]].mean(axis=1))
        return union_all([part for part in polygon_parts(shape)
                          if part.covers(centers).any()])

    if selector.regions:
        for layer, shape in support.items():
            pieces = []
            for key in selector.regions:
                if key.startswith("component:"):
                    pieces.append(selected_parts(shape, region_elements(result, layer, key)))
                elif key in regions:
                    pieces.append(regions[key])
                else:
                    # Adjacent-edge support tags and caller-defined mesh regions
                    # are genuinely mesh-defined. Preserve their full-cell
                    # meaning, unioning before any geometric clipping.
                    ids = region_elements(result, layer, key)
                    pieces.append(union_all(polygons(result.mesh.points[result.mesh.triangles[ids]])))
            support[layer] = shape.intersection(union_all(pieces))
    if selector.contacts:
        interfaces = {item.id: item for item in project.interfaces}
        contacts = result.analysis.get("contacts", {})
        for layer, shape in support.items():
            pieces = []
            for key in selector.contacts:
                record = contacts.get(key)
                interface = interfaces[record["interface_id"] if record else key]
                if layer not in {interface.layer_a, interface.layer_b}:
                    continue
                contact = geometry.interfaces[interface.id]
                if record is not None:
                    contact = selected_parts(contact, np.asarray(record["elements"], dtype=int))
                pieces.append(contact)
            support[layer] = shape.intersection(union_all(pieces))
    if selector.ports:
        for layer, shape in support.items():
            terminals = [terminal for port in project.power.ports if port.id in selector.ports
                         for landing in port.landings for terminal in project.terminals
                         if terminal.id == landing.terminal_id and terminal.layer_id == layer]
            support[layer] = shape.intersection(union_all([geometry.terminals[t.id] for t in terminals]))
    if selector.geometry and not selector.geometry.full_domain:
        clip = _region_geometry(project, selector.geometry, None)
        support = {layer: shape.intersection(clip) for layer, shape in support.items()}
    if selector.devices:
        groups = [_physical_selector_support(project, result, geometry, device.selector)
                  for device in project.power.devices if device.id in selector.devices]
        support = {layer: shape.intersection(union_all([group[layer] for group in groups if layer in group]))
                   for layer, shape in support.items()}
    return support


def suggest_refinements(project, result=None, *, geometry=None, progress=None, cancel=None,
                        max_memory_bytes=4 * 1024**3, averaging_length=None, hotspot_layer=None,
                        field_driven=False):
    from etlayout.physics.constitutive import sheet_conductance
    from etlayout.power.analysis import _context_error, evaluate_measurement, selected_support
    from etlayout.simulation.runner import _verify_layout

    _verify_layout(project)
    geometry = geometry or prepare_geometry(project, progress=progress, cancel=cancel)
    plan = RefinementPlan(configuration_hash(project), max_elements=project.mesh.max_elements,
                          max_dofs=project.mesh.max_dofs, max_memory_bytes=max_memory_bytes,
                          sharing_groups=_groups(project), hotspot_measurements=[spec.id for spec in
                          project.power.measurements if spec.averaging_length > 0 and spec.aggregation == "maximum"])
    compatible = result is not None and result.converged and not _context_error(project, result)
    if result is not None and not compatible:
        plan.warnings.append("Solved-state guidance omitted: result is unconverged or does not match this working configuration.")
    if not plan.hotspot_measurements:
        layers = ([layer for layer in project.layers if layer.role == "conductor"]
                  if project.solver.mode == "electrical" else project.layers)
        layer = next((item for item in layers if item.id == hotspot_layer), layers[0] if layers else None)
        if hotspot_layer and (layer is None or layer.id != hotspot_layer):
            raise SimulationError("Hotspot layer must name an existing participating physical layer")
        length = project.mesh.max_size * 4 if averaging_length is None else averaging_length
        if not math.isfinite(length) or length <= 0:
            raise SimulationError("Hotspot averaging length must be finite and positive in meters")
        if layer is not None:
            quantity = ("metal_current_density" if layer.electrical.kind == "bulk" else "sheet_current") if project.solver.mode == "electrical" else "temperature"
            identifier = "guided-averaged-" + layer.id
            if any(spec.id == identifier for spec in project.power.measurements):
                raise SimulationError(f"Measurement {identifier} already exists with incompatible averaging; review or rename it first")
            plan.generated_hotspot = MeasurementSpec(identifier, quantity,
                SupportSelector(layers=[layer.id]), averaging_length=length)
            plan.hotspot_measurements.append(identifier)
            plan.warnings.append(f"Reviewed study measurement: {quantity} on {layer.id}, fixed {length * 1e6:g} µm supported-area averaging. This default length is a reporting choice, not a calibrated physical length.")

    def add(identifier, purpose, target, support, length, reason):
        if support.is_empty or support.area <= 0 or not math.isfinite(length) or length <= 0:
            return
        size = min(project.mesh.max_size, length / 4)
        if size < project.mesh.min_size:
            reason += " Applying this suggestion explicitly lowers the minimum mesh size."
        plan.suggestions.append(RefinementSuggestion(identifier, purpose, target,
                                geometry_region(support), size, reason, length))

    for interface in project.interfaces:
        checkpoint(cancel)
        if not interface.enabled or interface.electrical_kind not in {"specific", "via"} or interface.purpose == "device_load":
            continue
        if interface.electrical_kind == "specific" and (not math.isfinite(interface.electrical_value) or interface.electrical_value <= 0):
            continue
        support = geometry.interfaces.get(interface.id)
        if support is None:
            continue
        estimates = {layer_id: contact_transfer_estimate(
            project, interface, layer_id, [result] if compatible else [])
            for layer_id in (interface.layer_a, interface.layer_b)}
        for layer_id in (interface.layer_a, interface.layer_b):
            layer = project.layer(layer_id)
            if layer.role != "conductor":
                continue
            length, estimate = estimates[layer_id]
            if length is None:
                plan.warnings.append(f"{interface.id}/{layer_id}: {estimate}")
                continue
            add(f"contact-{interface.id}-{layer_id}", "Contact transfer", f"{interface.id}/{layer_id}",
                support, length, f"Four elements per L_T = sqrt(rho_c G_s), using {estimate}; estimate varies with bias/temperature.")
        bounds = [estimate[0] for estimate in estimates.values()]
        if all(value is not None and value > 0 for value in bounds):
            bound = 1. / math.sqrt(sum(1. / value**2 for value in bounds))
            add("coupled-transfer-"+interface.id, "Coupled transfer bound", interface.id, support, bound,
                "Four elements per conservative two-sheet L_T = sqrt(rho_c/(Rs_a+Rs_b)). An ideal equipotential electrode can have a longer one-sheet transfer length; this bound does not claim a full 2D error estimate.")

    for port in project.power.ports:
        for landing in port.landings:
            terminal = next((item for item in project.terminals if item.id == landing.terminal_id), None)
            support = geometry.terminals.get(landing.terminal_id)
            if terminal is None or support is None or support.area <= 0 or landing.resistance <= 0:
                continue
            layer = project.layer(terminal.layer_id)
            if layer.electrical.kind == "hemt":
                plan.warnings.append(f"{port.id}/{terminal.id}: direct HEMT landing transfer-length estimate is unavailable; use an explicit contact interface.")
                continue
            conductance = sheet_conductance(layer, project.materials[layer.material_id],
                                           np.array([project.solver.initial_temperature]))
            length = math.sqrt(landing.resistance * support.area * float(conductance.min()))
            add(f"landing-{port.id}-{terminal.id}", "Finite feed landing", terminal.id, support, length,
                "Four elements per L_T from landing resistance × footprint area and initial-temperature sheet conductance. Ideal landings have no finite transfer length.")

    for layer in project.layers:
        if layer.role == "conductor" and layer.electrical.kind != "hemt":
            narrow = []
            widths = []
            for part in polygon_parts(geometry.layers[layer.id]):
                coordinates = np.asarray(part.minimum_rotated_rectangle.exterior.coords)
                lengths = np.linalg.norm(np.diff(coordinates, axis=0), axis=1)
                if lengths.min() <= 2 * project.mesh.max_size and lengths.max() >= 4 * lengths.min():
                    narrow.append(part)
                    widths.append(float(lengths.min()))
            if narrow:
                add("narrow-" + layer.id, "Narrow conductor", layer.id, union_all(narrow), min(widths),
                    "Four elements across the short extent of elongated conductor islands. This geometric heuristic does not certify notches or complex neck widths.")
        if layer.role != "conductor" or layer.electrical.kind != "hemt":
            continue
        gate_ids = layer.electrical.gate_regions or [terminal.id for terminal in project.terminals
                                                   if terminal.kind == "gate" and terminal.layer_id == layer.id]
        for gate_id in gate_ids:
            support = geometry.terminals.get(gate_id)
            if support is None:
                continue
            support = support.intersection(geometry.layers[layer.id])
            parts = list(polygon_parts(support))
            widths = []
            for part in parts:
                # The short extent estimates gate length, not conducting finger width.
                coordinates = np.asarray(part.minimum_rotated_rectangle.exterior.coords)
                widths.append(float(np.linalg.norm(np.diff(coordinates, axis=0), axis=1).min()))
            if widths:
                add(f"channel-{layer.id}-{gate_id}", "Gated channel", f"{layer.id}/{gate_id}", support,
                    min(widths), "Four elements across the short rotated gate-support extent. Exact channel boundaries remain unchanged; inspect irregular gate shapes.")

    if compatible:
        specifications = {spec.id: spec for spec in project.power.measurements}
        if plan.generated_hotspot:
            specifications[plan.generated_hotspot.id] = plan.generated_hotspot
        for identifier in plan.hotspot_measurements:
            spec = specifications[identifier]
            record = result.analysis.get("measurements", {}).get(identifier, {})
            if record.get("specification") != asdict(spec):
                record = evaluate_measurement(project, result, spec)
            center = record.get("window_center")
            if record.get("status") != "available" or center is None:
                plan.warnings.append(f"{identifier}: averaged-hotspot guidance unavailable: {record.get('reason') or 'solve a compatible configuration first'}")
                continue
            x, y = center
            half = spec.averaging_length / 2
            window = box(x-half, y-half, x+half, y+half)
            # A stored report may predate changes to nonphysical group metadata.
            # Revalidate every selector identity and cycle before resolving it.
            selected_support(project, result, spec.selector)
            selected = _physical_selector_support(project, result, geometry, spec.selector)
            support = window.intersection(union_all(list(selected.values()))).intersection(geometry.domain)
            add("hotspot-" + identifier, "Averaged hotspot", identifier, support, spec.averaging_length,
                "Four elements across this solved physical averaging window; its location is fixed throughout the study.")
    checkpoint(cancel, progress, 1., f"Prepared {len(plan.suggestions)} local refinement suggestions")
    from etlayout.meshing.physical import flux_suggestions, geometry_suggestions
    geometry_suggestions(project, geometry, plan)
    if field_driven:
        if not compatible:
            raise SimulationError("Flux-jump guidance requires a converged result of this applied configuration")
        flux_suggestions(project, result, geometry, plan)
    return plan


def local_study_metrics(project, result, plan):
    """Track fixed physical averaging lengths and like-sized device groups."""
    values = {}
    for identifier in plan.hotspot_measurements:
        record = result.analysis.get("measurements", {}).get(identifier, {})
        spec = next((item for item in project.power.measurements if item.id == identifier), None)
        if spec is None or record.get("status") != "available":
            raise SimulationError(f"Local mesh study needs available averaged measurement {identifier}: {record.get('reason', 'missing specification')}")
        values[f"regularized_{spec.quantity}:{identifier}"] = record["value"]
    if len(plan.sharing_groups) > 1:
        loading, potentials = [], []
        for identifier in plan.sharing_groups:
            record = result.analysis.get("devices", {}).get(identifier, {})
            group = next((item for item in project.power.devices if item.id == identifier), None)
            if group is None or record.get("status") != "available":
                raise SimulationError(f"Current-sharing study needs available device group {identifier}: {record.get('reason', 'missing group')}")
            normalization = group.active_width or group.active_area
            loading.append(record["current"] / normalization)
            if record.get("source_voltage") is not None:
                potentials.append(record["source_voltage"])
        mean = float(np.mean(np.abs(loading)))
        if mean > 0:
            values["device_current_sharing_error"] = float(np.ptp(loading) / mean)
        if len(potentials) == len(loading):
            values["device_source_voltage_spread"] = float(np.ptp(potentials))
    return values


def apply_refinement_plan(project, plan, selected=None, *, factor=1.):
    """Return an independent candidate. This operation is an explicit user commit."""
    if isinstance(plan, dict):
        plan = RefinementPlan.from_dict(plan)
    if configuration_hash(project) != plan.configuration_hash:
        raise SimulationError("Local mesh plan belongs to a different configuration; regenerate suggestions before applying.")
    if not math.isfinite(factor) or not 0 < factor <= 1:
        raise SimulationError("Local refinement factor must be finite, positive and no greater than 1")
    for value, label in ((plan.max_elements, "element"), (plan.max_dofs, "DOF"), (plan.max_memory_bytes, "memory")):
        if type(value) is not int or value <= 0:
            raise SimulationError(f"Local refinement {label} budget must be a positive integer")
    identifiers = [item.id for item in plan.suggestions]
    if len(set(identifiers)) != len(identifiers):
        raise SimulationError("Local mesh suggestion IDs must be unique")
    selected = set(identifiers if selected is None else selected)
    if not selected or selected - set(identifiers):
        raise SimulationError("Select at least one existing local mesh suggestion")
    candidate = deepcopy(project)
    candidate.mesh.max_elements, candidate.mesh.max_dofs = plan.max_elements, plan.max_dofs
    existing = {item.id: index for index, item in enumerate(candidate.mesh.refinements)}
    for suggestion in plan.suggestions:
        if suggestion.id not in selected:
            continue
        size = suggestion.size * factor
        if not math.isfinite(size) or size <= 0 or suggestion.geometry.line:
            raise SimulationError(f"Suggestion {suggestion.id} requires positive finite area sizing")
        identifier = "guided-" + suggestion.id
        if not math.isfinite(suggestion.grading_length) or suggestion.grading_length < 0:
            raise SimulationError("Refinement grading length must be finite and nonnegative")
        refinement = RefinementRegion(identifier, deepcopy(suggestion.geometry), size,
                                      grading_length=suggestion.grading_length)
        if identifier in existing:
            candidate.mesh.refinements[existing[identifier]] = refinement
        else:
            existing[identifier] = len(candidate.mesh.refinements)
            candidate.mesh.refinements.append(refinement)
        candidate.mesh.min_size = min(candidate.mesh.min_size, size)
    if plan.generated_hotspot:
        if any(spec.id == plan.generated_hotspot.id for spec in candidate.power.measurements):
            raise SimulationError("Generated averaged measurement conflicts with an existing ID; regenerate the plan")
        candidate.power.measurements.append(deepcopy(plan.generated_hotspot))
    return candidate


def estimate_resources(project, *, geometry=None):
    """Conservative overlap-counting planning estimate; actual Gmsh limits apply too."""
    geometry = geometry or prepare_geometry(project)
    base = 2.31 * geometry.domain.area / project.mesh.max_size**2
    extra = 0.
    controls = [(support, project.mesh.contact_size) for support in geometry.interfaces.values()]
    controls += [(support, project.mesh.contact_size) for support in geometry.terminals.values()]
    controls += [(geometry.refinements[item.id], min(item.axis_sizes or [item.size]))
                 for item in project.mesh.refinements]
    controls += [(geometry.layers[item.id], item.mesh_size) for item in project.layers if item.mesh_size]
    # A grading halo is conservatively screened at the finest requested size.
    # Its actual smooth transition is coarser; overlapping halos overcount.
    controls += [(geometry.refinements[item.id].buffer(item.grading_length).intersection(geometry.domain), item.size)
                 for item in project.mesh.refinements if item.grading_length > 0]
    for support, size in controls:
        extra += 2.31 * support.area * max(0., 1 / size**2 - 1 / project.mesh.max_size**2)
    elements = math.ceil(base + extra)
    weighted_layers = sum((2 if layer.role == "conductor" else 1) * geometry.layers[layer.id].area
                          for layer in project.layers) / geometry.domain.area
    dofs = math.ceil((elements / 2 + 4 * math.sqrt(elements)) * weighted_layers)
    return {"elements": elements, "dofs": dofs, "memory_bytes": dofs * 2048 + elements * 512,
            "planning_estimate_only": True,
            "definition": "Additional numerical memory only, approximately 2 KiB/DOF plus 512 B/element; excludes Python/Qt/import baselines. Overlapping supports count independently; sparse fill, allocator and reporting costs vary."}


def screen_resources(project, plan, *, geometry=None):
    estimate = estimate_resources(project, geometry=geometry)
    for metric, maximum in (("elements", plan.max_elements), ("dofs", plan.max_dofs), ("memory_bytes", plan.max_memory_bytes)):
        if estimate[metric] > maximum:
            raise SimulationError(f"Local refinement planning estimate {metric}={estimate[metric]:,} exceeds budget {maximum:,}; deselect expensive supports, use fewer levels or explicitly increase the budget. Estimates are not peak-memory guarantees.")
    return estimate
