# SPDX-License-Identifier: MPL-2.0
"""Physical-stack checks; effective couplings retain their explicit legacy meaning."""
from __future__ import annotations

import math
from itertools import combinations

from etlayout.models import Diagnostic, SimulationError


def validate_physical_stack(project, geometry, masks):
    """Resolve physical heights and remove displaced dielectric before meshing.

    A via spans the facing surfaces of its two endpoint sheets. The half-sheet
    thermal resistances remain the thermal solver's responsibility. Effective
    couplings are intentionally not reinterpreted as fabricated volumes.
    """
    from etlayout.geometry.prepare import resolve_region

    physical = [i for i in project.interfaces if i.enabled and i.model_kind != "effective"]
    footprints = {}
    for interface in physical:
        if interface.model_kind not in {"physical_via", "physical_contact"}:
            raise SimulationError(f"Interface {interface.id}: unknown physical/effective model kind")
        try:
            a, b = project.layer(interface.layer_a), project.layer(interface.layer_b)
        except StopIteration as exc:
            raise SimulationError(f"Interface {interface.id}: missing physical endpoint") from exc
        if a.id == b.id:
            raise SimulationError(f"Interface {interface.id}: endpoints must be different layers")
        lower, upper = sorted((a, b), key=lambda layer: layer.z)
        gap = upper.z - (lower.z + lower.thickness)
        tolerance = max(1e-15, abs(upper.z) * 1e-10, a.thickness * 1e-8, b.thickness * 1e-8)
        if gap < -tolerance:
            raise SimulationError(f"Interface {interface.id}: physical endpoint volumes overlap")
        gap = max(0., gap)
        if interface.model_kind == "physical_via" and gap <= tolerance:
            raise SimulationError(f"Interface {interface.id}: a bulk via needs positive surface separation; use physical_contact")
        if interface.model_kind == "physical_contact" and gap > tolerance:
            raise SimulationError(f"Interface {interface.id}: zero-thickness contact endpoints do not touch")
        if interface.height_mode == "stack":
            interface.height = gap
        elif interface.height_mode != "explicit":
            raise SimulationError(f"Interface {interface.id}: height mode must be explicit or stack")
        elif not math.isfinite(interface.height) or abs(interface.height - gap) > tolerance:
            raise SimulationError(f"Interface {interface.id}: explicit height {interface.height:g} m disagrees with stack surface separation {gap:g} m")
        if interface.enclosure < 0 or not math.isfinite(interface.enclosure):
            raise SimulationError(f"Interface {interface.id}: enclosure must be finite and nonnegative")
        transitions = project.power.rules.transitions
        if transitions and sorted([a.id, b.id]) not in [sorted(pair) for pair in transitions]:
            raise SimulationError(f"Interface {interface.id}: illegal layer transition {a.id} to {b.id}")
        raw = resolve_region(interface.geometry, masks, geometry.domain, clip=False)
        outside = raw.difference(geometry.domain)
        if outside.area > max(raw.area * 1e-9, 1e-30):
            if not (interface.allow_partial and project.acknowledge_clipped_boundaries):
                raise SimulationError(f"Interface {interface.id}: ROI truncates a physical connection; explicit partial-contact intent and clipped-boundary acknowledgement required")
            geometry.diagnostics.append(Diagnostic("warning", "Physical contact deliberately truncated by the acknowledged ROI", interface.id, list(raw.bounds)))
        raw = raw.intersection(geometry.domain)
        if raw.is_empty:
            raise SimulationError(f"Interface {interface.id}: no physical contact in domain")
        for layer in (a, b):
            support = geometry.layers[layer.id]
            required = raw.buffer(interface.enclosure, join_style="mitre") if interface.enclosure else raw
            if required.difference(support.buffer(tolerance)).area > max(required.area * 1e-9, 1e-30):
                if not interface.allow_partial or interface.enclosure:
                    raise SimulationError(f"Interface {interface.id}: missing landing overlap/enclosure on {layer.id}")
                geometry.diagnostics.append(Diagnostic("warning", "Intentionally partial physical landing; retained common support is reported", interface.id, list(raw.bounds)))
        if interface.allow_partial:
            raw = raw.intersection(geometry.layers[a.id]).intersection(geometry.layers[b.id])
            if raw.is_empty or raw.area <= 0:
                raise SimulationError(f"Interface {interface.id}: partial landing has no common support")
        declared = set(interface.replaces_layers)
        seen = set()
        for layer in project.layers:
            if layer.id in {a.id, b.id}:
                continue
            overlap_z = min(upper.z, layer.z + layer.thickness) - max(lower.z + lower.thickness, layer.z)
            overlap = raw.intersection(geometry.layers[layer.id])
            if overlap_z > tolerance and overlap.area > max(raw.area * 1e-9, 1e-30):
                if layer.role == "conductor":
                    raise SimulationError(f"Interface {interface.id}: via unintentionally intersects conductor {layer.id}")
                if layer.id not in declared:
                    raise SimulationError(f"Interface {interface.id}: crossed dielectric {layer.id} must be declared in replaces_layers to prevent double counting")
                seen.add(layer.id)
                remaining = geometry.layers[layer.id].difference(raw)
                if remaining.is_empty:
                    raise SimulationError(f"Interface {interface.id}: replacement removes all of {layer.id}; remove that redundant sheet explicitly")
                geometry.layers[layer.id] = remaining
        if declared - seen:
            raise SimulationError(f"Interface {interface.id}: replaced layers do not intersect its physical volume: {sorted(declared - seen)}")
        footprints[interface.id] = (raw, lower.z + lower.thickness, upper.z)
    # Literal fabricated stacks cannot retain coincident material volume.
    if physical:
        for a, b in combinations(project.layers, 2):
            z = min(a.z + a.thickness, b.z + b.thickness) - max(a.z, b.z)
            overlap = geometry.layers[a.id].intersection(geometry.layers[b.id])
            if z > min(a.thickness, b.thickness) * 1e-9 and overlap.area > 1e-30:
                raise SimulationError(f"Physical stack has unintended material-volume overlap: {a.id} and {b.id}")
    for a, b in combinations(physical, 2):
        ga, za, ta = footprints[a.id]
        gb, zb, tb = footprints[b.id]
        same_pair = {a.layer_a, a.layer_b} == {b.layer_a, b.layer_b}
        if (same_pair or min(ta, tb) > max(za, zb)) and ga.intersection(gb).area > 1e-30:
            raise SimulationError(f"Physical connections {a.id} and {b.id} double-define overlapping via/contact volume")
    return footprints


def validate_thermal_stack(project, geometry):
    """Reject duplicate explicit/effective dielectric volumes, diagnose bypasses.

    Effective legacy links keep their stated conductance/resistance. A direct
    link over an explicit film can be a deliberate parallel approximation, so
    report it rather than silently deleting a path or changing a coefficient.
    A stack-material gap occupying an explicit film is unambiguously duplicated
    and requires an explicit correction by the author.
    """
    for interface in project.interfaces:
        if not interface.enabled or interface.id not in geometry.interfaces:
            continue
        support = geometry.interfaces[interface.id]
        if support.is_empty:
            continue
        if interface.heat_layer_id:
            if interface.heat_layer_id not in geometry.layers:
                raise SimulationError(f"Interface {interface.id}: missing heat-deposition layer {interface.heat_layer_id}")
            missing = support.difference(geometry.layers[interface.heat_layer_id])
            if missing.area > max(support.area * 1e-9, 1e-30):
                raise SimulationError(f"Interface {interface.id}: heat-deposition layer must cover the complete interface support")
        if interface.thermal_kind == "none" or interface.model_kind != "effective":
            continue
        lower, upper = sorted((project.layer(interface.layer_a), project.layer(interface.layer_b)),
                              key=lambda layer: layer.z)
        low, high = lower.z + lower.thickness, upper.z
        if high <= low:
            continue
        for layer in project.layers:
            if layer.id in {lower.id, upper.id}:
                continue
            z_overlap = min(high, layer.z + layer.thickness) - max(low, layer.z)
            overlap = support.intersection(geometry.layers[layer.id])
            if z_overlap <= min(layer.thickness, high - low) * 1e-9 or overlap.area <= max(support.area * 1e-9, 1e-30):
                continue
            if interface.thermal_kind == "stack" and interface.material_id and interface.height > 0:
                raise SimulationError(f"Interface {interface.id}: effective gap duplicates explicit thermal layer {layer.id}; "
                                      "replace the bridge with adjacent sheet-to-sheet stack links and remove its gap material/height")
            geometry.diagnostics.append(Diagnostic("warning",
                f"Thermal link {interface.id} bypasses explicit layer {layer.id}. Check for a duplicated parallel heat path; "
                "keep this effective bridge only if it is deliberately calibrated. Adjacent stack links include half-sheet resistances.",
                interface.id, list(overlap.bounds)))
