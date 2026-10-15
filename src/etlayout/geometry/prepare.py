# SPDX-License-Identifier: MPL-2.0
"""Resolve mask expressions and check physically meaningful simulation geometry."""
from __future__ import annotations

import math
from dataclasses import replace
from itertools import combinations

from shapely.geometry import GeometryCollection, LineString, Point, Polygon, box
from shapely.ops import unary_union
from shapely.validation import explain_validity

from etlayout.models import Diagnostic, PreparedGeometry, Project, RegionSpec, SimulationError, checkpoint

from .layout import LayoutDocument


def polygon_parts(geometry):
    if geometry.geom_type == "Polygon":
        yield geometry
    elif hasattr(geometry, "geoms"):
        for part in geometry.geoms:
            yield from polygon_parts(part)


def geometry_region(geometry):
    """Serialize exact support for preview/commit without importing GUI widgets."""
    lines = []
    def collect_lines(shape):
        if shape.geom_type == "LineString":
            lines.append(list(map(list, shape.coords)))
        elif hasattr(shape, "geoms"):
            for part in shape.geoms:
                collect_lines(part)
    collect_lines(geometry)
    if len(lines) > 1:
        raise SimulationError("Multiple disconnected edge supports need separate terminal objects")
    return RegionSpec(polygons=[{"exterior": list(map(list, part.exterior.coords)),
                                 "holes": [list(map(list, ring.coords)) for ring in part.interiors]}
                                for part in polygon_parts(geometry)], line=lines[0] if lines else [])


def _explicit(spec: RegionSpec):
    parts = []
    for item in spec.polygons:
        if isinstance(item, dict):
            exterior = item.get("exterior", item.get("shell", item.get("points", [])))
            holes = item.get("holes", [])
            if "bounds" in item:
                polygon = box(*item["bounds"])
            else:
                polygon = Polygon(exterior, holes)
        else:
            polygon = Polygon(item)
        if not polygon.is_valid:
            raise SimulationError(f"Invalid polygon: {explain_validity(polygon)}. Repair its vertices before meshing.")
        if polygon.is_empty or polygon.area <= 0:
            raise SimulationError("A region polygon has zero area")
        if not all(math.isfinite(c) for c in polygon.bounds):
            raise SimulationError("Polygon coordinates must be finite SI meter values")
        parts.append(polygon)
    return unary_union(parts) if parts else GeometryCollection()


def resolve_region(spec: RegionSpec, masks: dict, domain=None, *, clip: bool = True):
    """Combine sources, subtract exclusions, then union explicit support and line.

    Valid operations are union, intersection, difference and xor. ``domain`` is
    used for full_domain and clipping; use clip=False to build a domain that may
    extend beyond its initial extent. Pass None to inspect an uncut expression.
    Unknown masks are errors rather than silently empty simulation regions.
    """
    if spec is None:
        return GeometryCollection()
    if spec.operation not in {"union", "intersection", "difference", "xor", "symmetric_difference"}:
        raise SimulationError(f"Unknown geometry Boolean operation {spec.operation!r}")
    source_geometries = []
    for key in spec.sources:
        if key not in masks:
            raise SimulationError(f"Mask {key!r} is not present in the selected layout")
        source_geometries.append(masks[key])
    geometry = source_geometries[0] if source_geometries else GeometryCollection()
    method = "symmetric_difference" if spec.operation == "xor" else spec.operation
    for source in source_geometries[1:]:
        geometry = getattr(geometry, method)(source)
    exclusions = []
    for key in spec.exclude_sources:
        if key not in masks:
            raise SimulationError(f"Excluded mask {key!r} is not present in the selected layout")
        exclusions.append(masks[key])
    if exclusions:
        geometry = geometry.difference(unary_union(exclusions))
    geometry = geometry.union(_explicit(spec))
    if spec.full_domain:
        if domain is None:
            raise SimulationError("Full-domain geometry needs a layout, ROI, or explicit polygon extent")
        geometry = geometry.union(domain)
    if spec.line:
        if len(spec.line) < 2:
            raise SimulationError("An edge region needs at least two points")
        line = LineString(spec.line)
        if line.length <= 0 or not line.is_valid:
            raise SimulationError("An edge region must have nonzero length and finite coordinates")
        geometry = geometry.union(line)
    if domain is not None and clip:
        geometry = geometry.intersection(domain)
    return geometry


def _bounds(g):
    return list(g.bounds) if not g.is_empty else None


def prepare_geometry(project: Project, progress=None, cancel=None, *,
                     document: LayoutDocument | None = None, masks_out: dict | None = None) -> PreparedGeometry:
    """Resolve physical regions without flattening placements outside an ROI.

    ``document`` reuses a parsed layout in worker processes. ``masks_out`` is an
    optional output dictionary for the domain-clipped raw-mask preview, avoiding
    a second GUI extraction. Both reading and polygon conversion accept cancel
    checkpoints; a GUI may terminate its worker during native kernel calls.
    """
    checkpoint(cancel, progress, .0, "Reading layout geometry")
    masks = {}
    import_diagnostics = []
    explicit = [_explicit(layer.geometry) for layer in project.layers]
    extent_parts = [g for g in explicit if not g.is_empty]
    if project.layout.path:
        document = document or LayoutDocument(project.layout.path, cancel=cancel)
        project.layout.dbu_um = document.dbu_um
        bounds = document.geometry_bounds(project.layout.cell, project.layout.instance_path, cancel=cancel)
        if bounds is None and not extent_parts:
            raise SimulationError("The selected layout contains no polygon or path geometry")
        if bounds is not None:
            extent_parts.append(box(*bounds))
        # Mask-defined ROIs need only their own masks before the spatial query.
        if project.roi and (project.roi.sources or project.roi.exclude_sources):
            masks = document.extract(project.layout.cell, project.layout.instance_path,
                                     mask_keys=list(dict.fromkeys([*project.roi.sources, *project.roi.exclude_sources])),
                                     cancel=cancel)
    if project.roi is not None:
        extent_parts.append(_explicit(project.roi))
    union = unary_union([g for g in extent_parts if not g.is_empty])
    if union.is_empty:
        raise SimulationError("No simulation extent: import a layout or define layer/ROI polygons")
    extent = box(*union.bounds)
    # An explicit domain may surround the mask: a thermal substrate is not
    # constrained to the footprint of metal present in the imported layout.
    domain = resolve_region(project.roi, masks, extent, clip=False) if project.roi else extent
    domain = unary_union(list(polygon_parts(domain)))
    if domain.is_empty or domain.area <= 0:
        raise SimulationError("The selected simulation domain has zero area")
    if project.layout.path:
        # Keep complete polygons intersecting the ROI so genuine cut edges can
        # be diagnosed, while skipping every distant hierarchy placement.
        masks = document.extract(project.layout.cell, project.layout.instance_path,
                                 roi=domain if project.roi else None, clip=False,
                                 progress=lambda f, m: checkpoint(cancel, progress, .05 + .2 * f, m),
                                 cancel=cancel)
        import_diagnostics = document.diagnostics
    if masks_out is not None:
        masks_out.clear()
        masks_out.update({key: shape.intersection(domain) for key, shape in masks.items()})
    geometry = PreparedGeometry(domain=domain, layers={})
    diagnostics = geometry.diagnostics
    diagnostics.extend(import_diagnostics)
    layer_ids = set()
    for layer in project.layers:
        checkpoint(cancel)
        if layer.id in layer_ids:
            raise SimulationError(f"Duplicate physical layer identifier: {layer.id}")
        layer_ids.add(layer.id)
        if not math.isfinite(layer.thickness) or layer.thickness <= 0:
            raise SimulationError(f"Layer {layer.name}: thickness must be positive (meters)")
        if not math.isfinite(layer.z):
            raise SimulationError(f"Layer {layer.name}: vertical position must be finite")
        if layer.role not in {"conductor", "insulator", "thermal"}:
            raise SimulationError(f"Layer {layer.name}: unknown physical role {layer.role}")
        raw = resolve_region(layer.geometry, masks, extent if layer.geometry.full_domain else None)
        clipped = raw.intersection(domain)
        region = unary_union(list(polygon_parts(clipped)))
        if region.is_empty or region.area <= 0:
            raise SimulationError(f"Layer {layer.name} has no area in the simulation domain")
        if not region.is_valid:
            raise SimulationError(f"Layer {layer.name}: {explain_validity(region)}")
        geometry.layers[layer.id] = region
        span = max(domain.bounds[2] - domain.bounds[0], domain.bounds[3] - domain.bounds[1])
        tolerance = max(span * 1e-10, 1e-15)
        removed_area = raw.difference(domain).area
        crosses_cut = (project.roi and removed_area > max(raw.area * 1e-12, 1e-30)
                       and raw.intersection(domain.boundary).difference(raw.boundary.buffer(tolerance)).length > tolerance)
        if layer.role == "conductor" and crosses_cut:
            diagnostics.append(Diagnostic("warning", f"ROI truncates conducting layer {layer.name}; clipped edges are electrically insulated unless explicit terminals are assigned.", layer.id, _bounds(region)))
            if not project.acknowledge_clipped_boundaries:
                raise SimulationError(f"ROI truncates a conducting path in {layer.name}. Assign cut-edge conditions and explicitly acknowledge clipped boundaries before solving.")
        elif removed_area > max(raw.area * 1e-12, 1e-30):
            diagnostics.append(Diagnostic("info", f"Simulation domain clips {removed_area / raw.area:.1%} of layer {layer.name}; inspect its retained footprint.", layer.id, _bounds(region)))
        parts = list(polygon_parts(region))
        if len(parts) > 1:
            diagnostics.append(Diagnostic("warning", f"Layer {layer.name} contains {len(parts)} disconnected polygon islands; inspect connectivity.", layer.id, _bounds(region)))
        clearance = region.minimum_clearance
        if math.isfinite(clearance) and clearance < project.mesh.min_size:
            diagnostics.append(Diagnostic("warning", f"Layer {layer.name} has a feature/vertex clearance {clearance * 1e6:.4g} µm below the minimum target mesh size; geometry is preserved but local quality should be inspected.", layer.id, _bounds(region)))
    if not geometry.layers:
        raise SimulationError("Add at least one physical layer to the process stack")
    from etlayout.technology.stack import validate_physical_stack
    physical_footprints = validate_physical_stack(project, geometry, masks)
    for first, second in combinations(project.layers, 2):
        overlap_z = min(first.z + first.thickness, second.z + second.thickness) - max(first.z, second.z)
        if overlap_z > min(first.thickness, second.thickness) * 1e-9:
            overlap = geometry.layers[first.id].intersection(geometry.layers[second.id])
            if overlap.area > 0:
                diagnostics.append(Diagnostic("warning", f"Physical volumes of {first.name} and {second.name} overlap in z and x–y. Verify stack positions and material assignment; electrical potentials remain independent.", first.id, _bounds(overlap)))
    checkpoint(cancel, progress, .35, "Resolving interfaces and boundary regions")
    seen = set()
    for interface in project.interfaces:
        if interface.id in seen:
            raise SimulationError(f"Duplicate interface identifier: {interface.id}")
        seen.add(interface.id)
        if not interface.enabled:
            continue
        if interface.layer_a not in layer_ids or interface.layer_b not in layer_ids:
            raise SimulationError(f"Interface {interface.id} refers to a missing physical layer")
        if interface.layer_a == interface.layer_b:
            raise SimulationError(f"Interface {interface.id} must join two distinct physical layers")
        raw = resolve_region(interface.geometry, masks, domain)
        # A physical via replaces the parallel dielectric path at its footprint.
        # Remove that support from a declared effective thermal coupling joining
        # the same endpoint sheets; keep the remaining dielectric area.
        replaced = False
        for physical in project.interfaces:
            if (physical.id in physical_footprints and interface.model_kind == "effective"
                    and {physical.layer_a, physical.layer_b} == {interface.layer_a, interface.layer_b}):
                cut = raw.intersection(physical_footprints[physical.id][0])
                if cut.area > 1e-30:
                    if interface.electrical_kind != "none":
                        raise SimulationError(f"Interface {interface.id} double-defines the physical electrical connection {physical.id}")
                    raw = raw.difference(cut)
                    replaced = True
                    diagnostics.append(Diagnostic("info", f"Removed physical via {physical.id} from parallel dielectric thermal support", interface.id, _bounds(cut)))
        if replaced and raw.is_empty:
            geometry.interfaces[interface.id] = raw
            continue
        overlap = geometry.layers[interface.layer_a].intersection(geometry.layers[interface.layer_b])
        region = raw.intersection(overlap)
        if region.is_empty or region.area <= 0:
            raise SimulationError(f"Interface {interface.id} has no area overlapping both participating layers")
        if raw.difference(overlap).area > max(raw.area * 1e-9, 1e-30):
            if interface.model_kind != "effective" and not interface.allow_partial:
                raise SimulationError(f"Physical interface {interface.id} extends outside its landing layers")
            diagnostics.append(Diagnostic("warning", f"Interface {interface.id} extends outside its two layers and is clipped to their common area.", interface.id, _bounds(raw)))
        if interface.electrical_kind != "none":
            if any(project.layer(key).role != "conductor" for key in (interface.layer_a, interface.layer_b)):
                raise SimulationError(f"Electrical interface {interface.id} is assigned to an insulating/thermal-only layer")
        geometry.interfaces[interface.id] = region
    groups = [(project.terminals, geometry.terminals, "Terminal"),
              (project.thermal_boundaries, geometry.thermal_boundaries, "Thermal boundary"),
              (project.heat_sources, geometry.heat_sources, "Heat source")]
    for objects, target, label in groups:
        seen = set()
        for item in objects:
            if item.id in seen:
                raise SimulationError(f"Duplicate {label.lower()} identifier: {item.id}")
            seen.add(item.id)
            if item.layer_id not in layer_ids:
                raise SimulationError(f"{label} {item.id} refers to missing layer {item.layer_id}")
            if label == "Terminal" and item.kind not in {"insulated", "gate"} and project.layer(item.layer_id).role != "conductor":
                raise SimulationError(f"Terminal {item.id} is assigned to a nonconducting layer")
            if item.geometry.line and label in {"Terminal", "Thermal boundary"}:
                boundary = geometry.layers[item.layer_id].boundary
                internal_allowed = label == "Terminal" and item.kind in {"voltage", "ground", "current", "floating", "gate", "probe"}
                span = max(domain.bounds[2] - domain.bounds[0], domain.bounds[3] - domain.bounds[1])
                tolerance = max(span * 1e-10, 1e-15)
                vertices = []
                boundary_vertices = [xy for polygon in polygon_parts(geometry.layers[item.layer_id])
                                     for ring in [polygon.exterior, *polygon.interiors]
                                     for xy in ring.coords]
                for coordinate in item.geometry.line:
                    point = Point(coordinate)
                    close_vertex = next((xy for xy in boundary_vertices
                                         if math.hypot(xy[0] - coordinate[0], xy[1] - coordinate[1]) <= tolerance), None)
                    nearest = Point(close_vertex) if close_vertex is not None else boundary.interpolate(boundary.project(point))
                    if point.distance(nearest) > tolerance and not internal_allowed:
                        raise SimulationError(f"{label} {item.id}: drawn edge must lie on the layer boundary; use a polygon for an internal contact")
                    vertices.append(nearest.coords[0] if point.distance(nearest) <= tolerance else coordinate)
                drawn = LineString(vertices)
                # Boundary coordinates originate in the layout database; entered
                # SI edge coordinates can differ by floating-point roundoff.
                # Validate every mask and retain the canonical source/exclusion/
                # explicit-union semantics after snapping the drawn edge.
                raw = resolve_region(replace(item.geometry, line=vertices), masks, domain)
                if raw.area > 0:
                    raise SimulationError(
                        f"{label} {item.id}: combined area and edge support is ambiguous. "
                        "Use separate boundary objects or port landings for area and edge selections.")
                if not internal_allowed and not boundary.buffer(tolerance).covers(drawn):
                    raise SimulationError(f"{label} {item.id}: drawn edge must follow the layer boundary; use a polygon for an internal contact")
            else:
                raw = resolve_region(item.geometry, masks, domain)
            region = raw.intersection(geometry.layers[item.layer_id])
            if region.is_empty or (region.area <= 0 and region.length <= 0):
                raise SimulationError(f"{label} {item.id} misses the assigned physical layer")
            if label == "Heat source" and region.area <= 0:
                raise SimulationError(f"Heat source {item.id} must have positive area")
            if raw.area > 0 and raw.difference(geometry.layers[item.layer_id]).area > max(raw.area * 1e-9, 1e-30):
                diagnostics.append(Diagnostic("warning", f"{label} {item.id} extends outside its layer and is clipped.", item.id, _bounds(raw)))
            if item.geometry.line and label in {"Terminal", "Thermal boundary"} and not internal_allowed:
                # Edge conditions apply only on the exterior/hole boundary of a sheet.
                boundary = geometry.layers[item.layer_id].boundary
                on_edge = region.intersection(boundary)
                if on_edge.length < region.length * (1 - 1e-7):
                    raise SimulationError(f"{label} {item.id}: drawn edge must lie on the layer boundary; use a polygon for an internal contact")
                region = on_edge
            target[item.id] = region
    for refinement in project.mesh.refinements:
        if not math.isfinite(refinement.size) or refinement.size <= 0:
            raise SimulationError(f"Refinement {refinement.id} needs a positive mesh size")
        geometry.refinements[refinement.id] = resolve_region(refinement.geometry, masks, domain)
    span = max(domain.bounds[2] - domain.bounds[0], domain.bounds[3] - domain.bounds[1])
    if span > .1 or span < 1e-8:
        diagnostics.append(Diagnostic("warning", f"Domain span is {span:g} meters; verify that entered coordinates use SI meters.", bounds=_bounds(domain)))
    checkpoint(cancel, progress, 1., "Geometry verified")
    return geometry
