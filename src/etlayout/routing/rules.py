# SPDX-License-Identifier: MPL-2.0
"""Executable integer-DBU routing checks, independent of Qt and any DRC DSL."""
from __future__ import annotations

import math
from itertools import combinations

from shapely.geometry import GeometryCollection

from etlayout.geometry.prepare import polygon_parts, prepare_geometry, resolve_region
from etlayout.models import SimulationError

from .geometry import to_region


def violation(rule_id, message, *, layer_id="", net_id="", object_id="", geometry=None):
    return dict(rule_id=rule_id, severity="error", layer_id=layer_id, net_id=net_id,
                object_id=object_id, bounds=list(geometry.bounds) if geometry is not None and not geometry.is_empty else None,
                message=message)


def validate_rules(project, geometry=None):
    """Return hard violations. Positive unsupported bend-radius rules fail explicitly.

    Width/space/notch checks use KLayout's Region methods in integer DBUs.
    Net-dependent spacing applies between declared net-bearing physical sheets
    occupying the same vertical level. Cross-level spacing is a stack question.
    """
    rules = project.power.rules
    issues = []
    if not math.isfinite(rules.dbu) or rules.dbu <= 0:
        return [violation("dbu", "Rule database unit must be finite and positive")]
    if rules.minimum_bend_radius:
        issues.append(violation("unsupported_bend_radius", "Circular bend-radius checking is unsupported by polygon recipes; set this rule to zero or reject the candidate"))
    for name, values in (("minimum_width", rules.minimum_width), ("minimum_spacing", rules.minimum_spacing),
                         ("net_spacing", rules.net_spacing)):
        for key, value in values.items():
            if not math.isfinite(value) or value < 0:
                issues.append(violation(name, f"{key}: rule must be finite and nonnegative"))
    for name in ("via_size", "via_pitch", "via_spacing", "via_enclosure", "minimum_notch"):
        value = getattr(rules, name)
        if not math.isfinite(value) or value < 0:
            issues.append(violation(name, f"{name} must be finite and nonnegative"))
    if issues:
        return issues
    if geometry is None:
        try:
            geometry = prepare_geometry(project)
        except SimulationError as exc:
            return [violation("physical_geometry", str(exc))]
    for layer in project.layers:
        shape = geometry.layers[layer.id]
        region = to_region(shape, rules.dbu)
        if region.is_empty():
            issues.append(violation("dbu_vanished", "Layer vanishes after database-unit snapping", layer_id=layer.id, geometry=shape))
            continue
        checks = [("minimum_width", rules.minimum_width.get(layer.id, 0.), region.width_check),
                  ("minimum_spacing", rules.minimum_spacing.get(layer.id, 0.), region.space_check),
                  ("minimum_notch", rules.minimum_notch, region.notch_check)]
        for key, threshold, checker in checks:
            if threshold <= 0:
                continue
            edges = checker(math.ceil(threshold / rules.dbu - 1e-9))
            for pair in edges.each():
                marker = pair.bbox()
                item = violation(key, f"{key.replace('_', ' ')} below {threshold:g} m", layer_id=layer.id, net_id=layer.net_id)
                item["bounds"] = [marker.left * rules.dbu, marker.bottom * rules.dbu,
                                  marker.right * rules.dbu, marker.top * rules.dbu]
                issues.append(item)
                if len(issues) >= 1000:
                    return issues + [violation("violation_limit", "At least 1000 rule violations; candidate rejected")]
    for a, b in combinations(project.layers, 2):
        if not a.net_id or not b.net_id or a.net_id == b.net_id or abs(a.z - b.z) > rules.dbu:
            continue
        threshold = rules.net_spacing.get(":".join(sorted([a.net_id, b.net_id])), 0.)
        ga, gb = geometry.layers[a.id], geometry.layers[b.id]
        if ga.intersection(gb).area > 1e-30 or (threshold and ga.distance(gb) < threshold - rules.dbu * .1):
            issues.append(violation("net_spacing", f"Different nets {a.net_id}/{b.net_id} overlap or violate {threshold:g} m separation", layer_id=a.id,
                                    net_id=f"{a.net_id}:{b.net_id}", geometry=ga.intersection(gb.buffer(threshold))))
    for recipe in project.power.recipes:
        if rules.allowed_layers and recipe.layer_id not in rules.allowed_layers:
            issues.append(violation("allowed_layers", "Recipe layer is not allowed for routing", layer_id=recipe.layer_id, object_id=recipe.id))
        if recipe.mask in rules.protected_masks:
            issues.append(violation("protected_mask", "Recipe attempts to edit a protected mask", layer_id=recipe.layer_id, object_id=recipe.id))
    transitions = {}
    for interface in project.interfaces:
        if not interface.enabled or interface.model_kind != "physical_via":
            continue
        shape = geometry.interfaces.get(interface.id, GeometryCollection())
        parts = list(polygon_parts(shape))
        if not parts or shape.geom_type not in {"Polygon", "MultiPolygon"}:
            issues.append(violation("unsupported_via_shape", "Physical vias require nonempty polygonal support", object_id=interface.id, geometry=shape))
            continue
        transition = tuple(sorted((interface.layer_a, interface.layer_b)))
        for part in parts:
            transitions.setdefault(transition, []).append((interface.id, part))
            region = to_region(part, rules.dbu)
            if region.is_empty():
                issues.append(violation("dbu_vanished", "Via vanishes after database-unit snapping", object_id=interface.id, geometry=part))
            elif rules.via_size and not region.width_check(math.ceil(rules.via_size / rules.dbu - 1e-9)).is_empty():
                issues.append(violation("via_size", "Via geometric width is below the minimum size", object_id=interface.id, geometry=part))
            for key in (interface.layer_a, interface.layer_b):
                required = part.buffer(rules.via_enclosure, join_style="mitre")
                if required.difference(geometry.layers[key].buffer(rules.dbu * .01)).area > 1e-30:
                    issues.append(violation("via_enclosure", "Via lacks the rule-deck landing enclosure", layer_id=key, object_id=interface.id, geometry=part))
    # Fabrication rules operate on the transition, not an arbitrary interface
    # collection. Splitting one array across objects must not evade checks.
    for transition, vias in transitions.items():
        for (owner_a, a), (owner_b, b) in combinations(vias, 2):
            owner = ":".join(sorted({owner_a, owner_b}))
            if a.centroid.distance(b.centroid) < rules.via_pitch - rules.dbu * .1:
                issues.append(violation("via_pitch", f"Via centers on transition {transition} are below the minimum pitch", object_id=owner, geometry=a.union(b)))
            if a.intersection(b).area > 0 or a.distance(b) < rules.via_spacing - rules.dbu * .1:
                issues.append(violation("via_spacing", f"Vias on transition {transition} overlap or violate the edge spacing", object_id=owner, geometry=a.union(b)))
    # Keepouts apply to newly generated material in generation, not unrelated
    # legacy geometry. Explicit fixed regions are similarly compared to source.
    return issues


def resolved_protected(project, masks, domain):
    return [("fixed_region", resolve_region(spec, masks, domain, clip=False)) for spec in project.power.rules.fixed_regions] + [
        ("keepout", resolve_region(spec, masks, domain, clip=False)) for spec in project.power.rules.keepouts]
