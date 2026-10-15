# SPDX-License-Identifier: MPL-2.0
"""Read-only support consequences of a draft original-shape edit.

This is geometry inspection, not a solver or design-rule pass. Explicit
coordinates, active dimensions and recipe source hashes are never rewritten.
"""
from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass, replace

from shapely.geometry import GeometryCollection, box
from shapely.ops import unary_union

from etlayout.geometry.prepare import resolve_region
from etlayout.models import PreparedGeometry, SimulationError
from etlayout.technology.stack import validate_physical_stack


@dataclass(frozen=True)
class EditImpact:
    category: str
    object_id: str
    message: str
    bounds: tuple[float, ...] | None = None


def _union(shapes):
    return unary_union(list(shapes))


class EditImpactPreview:
    """Cache imported geometry once, then compare requested effective supports."""

    def __init__(self, project, targets, target):
        self.project = deepcopy(project)
        self.target = target
        self.masks = {mask: _union(item.geometry for item in targets if item.mask == mask)
                      for mask in {item.mask for item in targets}}
        self.remainder = _union(item.geometry for item in targets if item.mask == target.mask and item is not target)
        self.before = self._snapshot(self.masks)

    def _snapshot(self, masks):
        # Stack resolution can update derived physical heights. Work on a clone
        # so even internal preview snapshots retain the applied definition.
        p = deepcopy(self.project)
        extents = [shape for shape in masks.values() if not shape.is_empty]
        # Explicit layers may extend beyond the imported geometry.
        extents.extend(resolve_region(replace(layer.geometry, sources=[], exclude_sources=[], full_domain=False), {}, None)
                       for layer in p.layers)
        if p.roi and not p.roi.full_domain:
            extents.append(resolve_region(p.roi, masks, None, clip=False))
        extent = box(*_union(extents).bounds)
        domain = resolve_region(p.roi, masks, extent, clip=False) if p.roi else extent
        layers = {layer.id: resolve_region(layer.geometry, masks, extent).intersection(domain) for layer in p.layers}
        geometry = PreparedGeometry(domain, layers)
        physical = validate_physical_stack(p, geometry, masks)
        objects, regions = {}, {}
        warnings = []
        for category, prefix, values in (("Contact", "terminal", p.terminals),
                                         ("Thermal boundary", "thermal", p.thermal_boundaries),
                                         ("Heat source", "heat", p.heat_sources)):
            for item in values:
                shape = resolve_region(item.geometry, masks, domain).intersection(layers[item.layer_id])
                if item.geometry.line and (category == "Thermal boundary" or category == "Contact" and item.kind not in
                                           {"voltage", "ground", "current", "floating", "gate", "probe"}):
                    shape = shape.intersection(layers[item.layer_id].boundary)
                if item.geometry.line:
                    warnings.append(EditImpact(category, item.id, "Revalidation required (F6): edge support snapping and boundary acceptance are not certified by this geometric preview."))
                objects[category, item.id] = {item.layer_id: shape}
                regions[prefix + ":" + item.id] = {item.layer_id: shape}
        for item in p.interfaces:
            if item.enabled:
                raw = resolve_region(item.geometry, masks, domain)
                for connection in p.interfaces:
                    if (connection.id in physical and item.model_kind == "effective" and
                            {connection.layer_a, connection.layer_b} == {item.layer_a, item.layer_b}):
                        cut = raw.intersection(physical[connection.id][0])
                        if cut.area > 1e-30:
                            if item.electrical_kind != "none":
                                raise SimulationError(f"Interface {item.id} double-defines physical connection {connection.id}")
                            raw = raw.difference(cut)
                shape = raw.intersection(layers[item.layer_a]).intersection(layers[item.layer_b])
                support = dict.fromkeys((item.layer_a, item.layer_b), shape)
                objects["Interface", item.id] = support
                regions["interface:" + item.id] = support
        for item in p.mesh.refinements:
            shape = resolve_region(item.geometry, masks, domain)
            regions["refinement:" + item.id] = {key: shape.intersection(support) for key, support in layers.items()}
        devices = {item.id: item for item in p.power.devices}
        ports = {item.id: item for item in p.power.ports}

        def select(selector, visited=()):
            selected = {layer.id: layers[layer.id] for layer in p.layers
                        if (not selector.layers or layer.id in selector.layers)
                        and (not selector.materials or layer.material_id in selector.materials)
                        and (not selector.nets or layer.net_id in selector.nets)}
            categories = []
            if selector.regions:
                if any(key not in regions for key in selector.regions):
                    raise SimulationError("Result-component or stale region selector needs a regenerated mesh")
                categories.append([regions[key] for key in selector.regions])
            if selector.contacts:
                if any(("Interface", key) not in objects for key in selector.contacts):
                    raise SimulationError("Per-instance contact selector needs a regenerated mesh")
                categories.append([objects["Interface", key] for key in selector.contacts])
            if selector.ports:
                categories.append([_port_support(ports[key]) for key in selector.ports])
            if selector.devices:
                if any(key in visited for key in selector.devices):
                    raise SimulationError("Cyclic device selector needs correction")
                categories.append([select(devices[key].selector, (*visited, key)) for key in selector.devices])
            for group in categories:
                selected = {key: shape.intersection(_union(part.get(key, GeometryCollection()) for part in group))
                            for key, shape in selected.items()}
            if selector.geometry:
                shape = resolve_region(selector.geometry, masks, domain)
                selected = {key: support.intersection(shape) for key, support in selected.items()}
            return selected

        def _port_support(port):
            values = [objects["Contact", landing.terminal_id] for landing in port.landings]
            return {key: _union(item.get(key, GeometryCollection()) for item in values) for key in layers}

        for category, values in (("Device group", p.power.devices), ("Measurement", p.power.measurements)):
            for item in values:
                try:
                    objects[category, item.id] = select(item.selector, (item.id,) if category == "Device group" else ())
                except (SimulationError, KeyError) as error:
                    warnings.append(EditImpact(category, item.id, "Support cannot be assessed: " + str(error)))
        for port in p.power.ports:
            objects["Power port", port.id] = _port_support(port)
        return domain, layers, objects, warnings

    def compare(self, polygon):
        masks = dict(self.masks)
        masks[self.target.mask] = self.remainder.union(polygon)
        domain, layers, objects, warnings = self._snapshot(masks)
        old_domain, old_layers, old_objects, old_warnings = self.before
        impacts = list(dict.fromkeys([*old_warnings, *warnings]))
        if not old_domain.equals(domain):
            impacts.append(EditImpact("Simulation domain", "domain", "Effective domain changes; the domain definition retains its coordinates.", tuple(old_domain.union(domain).bounds)))
        for key, after in layers.items():
            if not old_layers[key].equals(after):
                impacts.append(EditImpact("Physical layer", key, "Physical support changes; regenerate the mesh.", tuple(old_layers[key].union(after).bounds)))
        for (category, key), after in objects.items():
            before = old_objects.get((category, key))
            if before is None or all(before.get(layer, GeometryCollection()).equals(shape) for layer, shape in after.items()):
                continue
            old_area, new_area = sum(s.area for s in before.values()), sum(s.area for s in after.values())
            if category == "Interface":
                old_area, new_area = _union(before.values()).area, _union(after.values()).area
            lost = all(shape.is_empty for shape in after.values())
            message = "Support is lost; repair its geometry before solving." if lost else "Effective support changes; inspect connectivity and averaging support."
            if old_area or new_area:
                message += f" Area {old_area * 1e12:.6g} → {new_area * 1e12:.6g} µm²."
            if category == "Device group":
                message += " Declared active width and area stay unchanged."
            unassessed = next((entry for entry in impacts if (entry.category, entry.object_id) == (category, key)), None)
            if unassessed:
                impacts.remove(unassessed)
                message = unassessed.message + " " + message
            shape = _union([*before.values(), *after.values()])
            impacts.append(EditImpact(category, key, message, tuple(shape.bounds) if not shape.is_empty else None))
        changed = self.target.geometry.symmetric_difference(polygon)
        rules = self.project.power.rules
        for category, values in (("Routing keepout", rules.keepouts), ("Fixed routing region", rules.fixed_regions)):
            for index, region in enumerate(values):
                shape = resolve_region(region, masks, domain)
                old = resolve_region(region, self.masks, old_domain)
                if not shape.equals(old) or old.union(shape).intersects(changed):
                    impacts.append(EditImpact(category, str(index + 1), "Protected geometry is affected; rerun routing checks.", tuple(old.union(shape).bounds)))
        if self.target.mask in rules.protected_masks and not self.masks[self.target.mask].equals(masks[self.target.mask]):
            impacts.append(EditImpact("Protected mask", self.target.mask, "This edit changes a protected routing mask; rerun routing checks.", tuple(changed.bounds)))
        for recipe in self.project.power.recipes:
            region = resolve_region(recipe.edit_region, masks, domain)
            previous_region = resolve_region(recipe.edit_region, self.masks, old_domain)
            if recipe.source_hash:
                impacts.append(EditImpact("Routing recipe", recipe.id, "Source hash becomes stale after Apply. Revalidate the recipe before generation."))
            elif (not region.equals(previous_region) or region.intersects(changed) or recipe.mask == self.target.mask or recipe.layer_id in layers and
                  not old_layers[recipe.layer_id].equals(layers[recipe.layer_id])):
                impacts.append(EditImpact("Routing recipe", recipe.id, "Route geometry is affected; review the recipe before generation."))
        return impacts
