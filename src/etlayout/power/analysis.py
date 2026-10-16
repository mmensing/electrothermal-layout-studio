# SPDX-License-Identifier: MPL-2.0
"""Physical-support measurements with exact area weights and explicit validity.

P1 means, RMS and integrals use degree-two triangle quadrature. Percentiles use
the exact area CDF of each linear triangular patch. Hotspots average over a
fixed SI square on an origin-anchored half-window grid, independent of the mesh.
"""
from __future__ import annotations

from copy import deepcopy
from dataclasses import asdict, replace
from pathlib import Path

import numpy as np
from shapely import area as geometric_area
from shapely import (
    centroid,
    constrained_delaunay_triangles,
    get_coordinates,
    get_parts,
    get_x,
    get_y,
    intersection,
    polygons,
    prepare,
    union_all,
)
from shapely.geometry import Point

from etlayout.geometry.prepare import polygon_parts, resolve_region
from etlayout.io.reporting import csv_writer
from etlayout.models import Cancelled, MeasurementSpec, SimulationError, SupportSelector, checkpoint
from etlayout.power.measurement_contract import QUANTITIES, validate_limits
from etlayout.visualization.sampling import element_nodal_values
from etlayout.visualization.support import region_elements


def _known(requested, available, label):
    missing = set(requested) - set(available)
    if missing:
        raise SimulationError(f"Stale {label} selector: {', '.join(sorted(missing))}")


def _intersect_ids(left, right):
    """Exact ID intersection; exploit sorted mesh tags without assuming API order."""
    left, right = np.asarray(left).ravel(), np.asarray(right).ravel()
    if (np.any(left[1:] <= left[:-1]) or np.any(right[1:] <= right[:-1])):
        return np.intersect1d(left, right)
    if len(left) > len(right):
        left, right = right, left
    positions = np.searchsorted(right, left)
    valid = positions < len(right)
    matched = np.zeros(len(left), dtype=bool)
    matched[valid] = right[positions[valid]] == left[valid]
    return left[matched].astype(np.result_type(left.dtype, right.dtype), copy=False)


def _region_geometry(project, spec, domain):
    masks = {}
    if spec.sources or spec.exclude_sources:
        from etlayout.geometry.layout import LayoutDocument
        doc = LayoutDocument(project.layout.path)
        masks = doc.extract(project.layout.cell, project.layout.instance_path)
    return resolve_region(spec, masks, domain)


def _mesh_support_data(result, cache):
    """Report-owned geometry only; never retained across mutable run contexts."""
    key = ("mesh_support", id(result.mesh))
    if cache is not None and key in cache:
        return cache[key]
    xy = result.mesh.points[result.mesh.triangles]
    low, high = xy.min(axis=1), xy.max(axis=1)
    edges = xy[:, 1:] - xy[:, :1]
    areas = np.abs(edges[:, 0, 0]*edges[:, 1, 1] - edges[:, 0, 1]*edges[:, 1, 0])/2
    data = xy, low, high, areas
    if cache is not None:
        cache[key] = data
    return data


def _bounding_candidates(result, ids, geometry, cache):
    # This is only a candidate filter. Exact positive intersection area below
    # remains authoritative, including holes, partial cells and rotated shapes.
    _, low, high, _ = _mesh_support_data(result, cache)
    xmin, ymin, xmax, ymax = geometry.bounds
    return ids[(high[ids, 0] >= xmin) & (low[ids, 0] <= xmax) &
               (high[ids, 1] >= ymin) & (low[ids, 1] <= ymax)]


def selected_support(project, result, selector: SupportSelector, _visited=(), _cache=None):
    """Layer element IDs and exact clips, union within / intersection across categories.

    A clip is carried through every device reference. Element IDs only bound the
    work; partial triangles are integrated by _patches, never centroid filtered.
    """
    layers = {layer.id: layer for layer in project.layers}
    _known(selector.layers, layers, "layer")
    _known(selector.materials, project.materials, "material")
    _known(selector.nets, {layer.net_id for layer in project.layers if layer.net_id}, "net")
    _known(selector.contacts, {i.id for i in project.interfaces} | set(result.analysis.get("contacts", {})), "contact")
    _known(selector.ports, {p.id for p in project.power.ports}, "port")
    _known(selector.devices, {d.id for d in project.power.devices}, "device")
    if any(key in _visited for key in selector.devices):
        raise SimulationError("Cyclic device group selectors")
    cache_key = repr(asdict(selector))
    if _cache is not None and cache_key in _cache:
        return _cache[cache_key]
    selected = {name: result.mesh.elements(name) for name, layer in layers.items()
                if name in result.fields and (not selector.layers or name in selector.layers)
                and (not selector.materials or layer.material_id in selector.materials)
                and (not selector.nets or layer.net_id in selector.nets)}
    if selector.regions:
        for layer, ids in selected.items():
            parts = [region_elements(result, layer, region) for region in selector.regions]
            selected[layer] = _intersect_ids(ids, np.unique(np.concatenate(parts)))
    if selector.contacts:
        contacts = result.analysis.get("contacts", {})
        interfaces = {item.id: item for item in project.interfaces}
        for layer, ids in selected.items():
            parts = []
            for key in selector.contacts:
                record = contacts.get(key)
                interface = interfaces[record["interface_id"] if record else key]
                if layer in {interface.layer_a, interface.layer_b}:
                    parts.append(np.asarray(record["elements"], int) if record else
                                 result.mesh.regions.get("interface:" + key, np.array([], int)))
            selected[layer] = _intersect_ids(ids, np.unique(np.concatenate(parts)) if parts else np.empty(0, int))
    if selector.ports:
        for layer, ids in selected.items():
            terminals = [t for p in project.power.ports if p.id in selector.ports for landing in p.landings
                         for t in project.terminals if t.id == landing.terminal_id and t.layer_id == layer]
            parts = [result.mesh.regions.get("terminal:" + t.id, np.array([], dtype=int)) for t in terminals]
            selected[layer] = _intersect_ids(ids, np.concatenate(parts) if parts else np.empty(0, int))
    clips = {layer: None for layer in selected}
    if selector.geometry and not selector.geometry.full_domain:
        shape = _region_geometry(project, selector.geometry, None)
        clips = dict.fromkeys(selected, shape)
    if selector.devices:
        groups = [selected_support(project, result, device.selector, (*_visited, device.id), _cache)
                  for device in project.power.devices if device.id in selector.devices]
        for layer, ids in selected.items():
            pieces = []
            for group_ids, group_clips in groups:
                part_ids = _intersect_ids(ids, group_ids.get(layer, np.empty(0, int)))
                if not len(part_ids):
                    continue
                triangles = polygons(result.mesh.points[result.mesh.triangles[part_ids]])
                clip = group_clips.get(layer)
                pieces.extend(triangles if clip is None else clip.intersection(triangles))
            shape = union_all(pieces)
            clips[layer] = shape if clips[layer] is None else shape.intersection(clips[layer])
    for layer, ids in selected.items():
        clip = clips[layer]
        if clip is not None and len(ids):
            ids = _bounding_candidates(result, ids, clip, _cache)
            xy, _, _, _ = _mesh_support_data(result, _cache)
            shapes = polygons(xy[ids])
            prepare(clip)
            inside = np.asarray(clip.covers(shapes))
            partial = ~inside & np.asarray(clip.intersects(shapes))
            # Only partial cells need a constructed intersection. A boundary
            # touch has no measurable area; all positive partial areas survive.
            inside[partial] = geometric_area(clip.intersection(shapes[partial])) > 0
            selected[layer] = ids[inside]
    if _cache is not None:
        _cache[cache_key] = selected, clips
    return selected, clips


def selected_elements(project, result, selector: SupportSelector) -> dict[str, np.ndarray]:
    """Candidate element IDs; exact support remains owned by selected_support."""
    return selected_support(project, result, selector)[0]


def _support_clip(result, selected, clips, layers=None, ids=None):
    relevant = [layer for layer, support_ids in selected.items() if len(support_ids)
                and (layers is None or layer in layers)
                and (ids is None or len(_intersect_ids(ids, support_ids)))]
    # Callers already bound their work by the union of selected triangle IDs.
    # No additional clip is required for complete triangles. A single layer's
    # existing exact clip can be applied directly by _patches; re-unioning its
    # triangle polygons for every current/loss/potential is identical but costly.
    if relevant and all(clips[layer] is None for layer in relevant):
        return None
    if len(relevant) == 1:
        return clips[relevant[0]]
    pieces = []
    for layer, support_ids in selected.items():
        if layers is not None and layer not in layers:
            continue
        common = support_ids if ids is None else _intersect_ids(ids, support_ids)
        triangles = polygons(result.mesh.points[result.mesh.triangles[common]])
        clip = clips[layer]
        pieces.extend(triangles if clip is None else clip.intersection(triangles))
    return union_all(pieces)


def _patches(result, ids, local, geometry=None, *, with_patches=True, _cache=None):
    """Vectorized full-triangle integration; only cut boundary cells need solves."""
    ids = np.asarray(ids, dtype=int)
    if not len(ids):
        return [], np.empty((0, 3)), np.empty(0)
    if geometry is not None:
        ids = _bounding_candidates(result, ids, geometry, _cache)
    if not len(ids):
        return [], np.empty((0, 3)), np.empty(0)
    all_xy, _, _, all_areas = _mesh_support_data(result, _cache)
    xy = all_xy[ids]
    areas = all_areas[ids]
    if geometry is None:
        return list(polygons(xy)) if with_patches else [], np.asarray(local[ids]), areas
    shapes = polygons(xy)
    prepare(geometry)
    inside = np.asarray(geometry.covers(shapes))
    patches = list(shapes[inside]) if with_patches else []
    values = list(local[ids[inside]])
    weights = list(areas[inside])
    boundary = np.flatnonzero(~inside & np.asarray(geometry.intersects(shapes)))
    for index in boundary:
        coordinates = xy[index]
        cut = shapes[index].intersection(geometry)
        for part in polygon_parts(cut):
            pieces = [part] if len(part.exterior.coords) == 4 and not part.interiors else constrained_delaunay_triangles(part).geoms
            for triangle in pieces:
                if triangle.area <= 0:
                    continue
                vertices = np.asarray(triangle.exterior.coords)[:3]
                matrix = np.vstack(((coordinates - coordinates[0]).T, np.ones(3)))
                bary = np.linalg.solve(matrix, np.vstack(((vertices - coordinates[0]).T, np.ones(3)))).T
                if with_patches:
                    patches.append(triangle)
                values.append(bary @ local[ids[index]])
                weights.append(triangle.area)
    return patches, np.asarray(values), np.asarray(weights)


def _cdf(values, x):
    a, b, c = np.sort(values, axis=1).T
    result = (x >= c).astype(float)
    lower = (x > a) & (x < b)
    upper = (x >= b) & (x < c)
    result[lower] = (x - a[lower]) ** 2 / ((b[lower] - a[lower]) * (c[lower] - a[lower]))
    result[upper] = 1 - (c[upper] - x) ** 2 / ((c[upper] - a[upper]) * (c[upper] - b[upper]))
    return result


def _aggregate(values, weights, spec):
    total = weights.sum()
    means = values.mean(axis=1)
    if spec.aggregation == "integral":
        return float(weights @ means)
    if spec.aggregation == "mean":
        return float(weights @ means / total)
    if spec.aggregation == "rms":
        moment = ((values ** 2).sum(axis=1) + values[:, 0]*values[:, 1]
                  + values[:, 0]*values[:, 2] + values[:, 1]*values[:, 2]) / 6
        return float(np.sqrt(weights @ moment / total))
    if spec.aggregation in {"minimum", "maximum"}:
        return float(values.min() if spec.aggregation == "minimum" else values.max())
    if spec.aggregation == "exceedance":
        # Use the same positive-area summation as the denominator. A BLAS dot
        # uses a different reduction order and can turn exact 100% into >1.
        return float(np.sum(weights * (1 - _cdf(values, spec.threshold))) / total)
    if spec.aggregation == "percentile":
        if not 0 <= spec.percentile <= 100:
            raise SimulationError("Percentile must lie in [0, 100]")
        lo, hi = values.min(), values.max()
        if spec.percentile == 0:
            return float(lo)
        for _ in range(64):
            middle = (lo + hi) / 2
            if weights @ _cdf(values, middle) / total < spec.percentile / 100:
                lo = middle
            else:
                hi = middle
        return float(hi)
    raise SimulationError(f"Unknown measurement aggregation {spec.aggregation!r}")


def _moment(values, order):
    """Exact mean of a linear triangle field raised to an integer power."""
    total = np.zeros(len(values))
    for first in range(order + 1):
        for second in range(order - first + 1):
            third = order - first - second
            total += values[:, 0] ** first * values[:, 1] ** second * values[:, 2] ** third
    return total * (2. / ((order + 1) * (order + 2)))


def _aggregate_squared(values, weights, spec):
    if spec.aggregation == "integral":
        return float(weights @ _moment(values, 2))
    if spec.aggregation == "mean":
        return float(weights @ _moment(values, 2) / weights.sum())
    if spec.aggregation == "rms":
        return float(np.sqrt(weights @ _moment(values, 4) / weights.sum()))
    if spec.aggregation == "exceedance":
        if spec.threshold < 0:
            return 1.
        return _aggregate(values, weights, replace(spec, threshold=np.sqrt(spec.threshold)))
    # Values are nonnegative after exact sign splitting, so quantiles and
    # extrema commute with squaring. No nodal heat interpolation is used.
    return _aggregate(values, weights, spec) ** 2


def _contact_heat_root(result, owner):
    fields = result.interface_fields.get(owner, {})
    drop = fields.get("voltage_drop")
    normal = fields.get("j_normal")
    if drop is None:
        raise SimulationError("Contact voltage drop is unavailable")
    if normal is None:
        heat = fields.get("heat_density")
        if heat is None or np.any(heat[np.isfinite(heat)] != 0):
            raise SimulationError("Finite contact heat needs normal current and voltage drop")
        return np.sqrt(heat)
    denominator = np.abs(drop).sum(axis=1)
    g = np.divide(np.abs(normal).sum(axis=1), denominator, out=np.zeros(len(drop)), where=denominator > 0)
    return np.sqrt(g)[:, None] * drop


def _hotspot(patches, values, spec, squared=False, cancel=None):
    domain = union_all(patches)
    length = spec.averaging_length
    xmin, ymin, xmax, ymax = domain.bounds
    with np.errstate(over="ignore", invalid="ignore", divide="ignore"):
        bounds = np.array([np.floor(xmin/(length/2)), np.ceil(xmax/(length/2)),
                           np.floor(ymin/(length/2)), np.ceil(ymax/(length/2))])
    if (not np.isfinite(bounds).all() or bounds[1]-bounds[0]+1 > 100000
            or bounds[3]-bounds[2]+1 > 100000
            or bounds[3]-bounds[2]+1 > 100000 / (bounds[1]-bounds[0]+1)):
        raise SimulationError("Hotspot window grid exceeds 100000 centers; choose a larger physical averaging length")
    xs = np.arange(bounds[0], bounds[1]+1) * (length/2)
    ys = np.arange(bounds[2], bounds[3]+1) * (length/2)
    from shapely import STRtree
    centers = [point for x in xs for y in ys
               if domain.covers(point := Point(float(x), float(y)))]
    center_tree = STRtree(centers)
    components = domain.geoms if domain.geom_type == "MultiPolygon" else (domain,)
    for component in components:
        if not len(center_tree.query(component, predicate="covers")):
            # Every disconnected support participates, including islands too
            # small to contain any center of the fixed physical sampling grid.
            centers.append(component.representative_point())
    patches = np.asarray(patches, dtype=object)
    tree = STRtree(patches)
    # Every patch carries an affine P1 field. Compute its gradient once instead
    # of solving the same 3x3 interpolation system for every overlapping window.
    coordinates = get_coordinates(patches).reshape(-1, 4, 2)[:, :3]
    origins = coordinates[:, 0]
    gradients = np.linalg.solve(coordinates[:, 1:] - origins[:, None],
                                (values[:, 1:] - values[:, :1])[..., None])[..., 0]
    best = -np.inf
    location = None
    # Bounded batches retain exact intersections and supported-area normalization.
    # Neither the physical window grid nor the number of sampled centers changes.
    for start in range(0, len(centers), 128):
        checkpoint(cancel)
        chosen = centers[start:start+128]
        xy = np.asarray([[center.x, center.y] for center in chosen])
        from shapely import box as boxes
        windows = boxes(xy[:, 0]-length/2, xy[:, 1]-length/2,
                        xy[:, 0]+length/2, xy[:, 1]+length/2)
        window_ids, patch_ids = tree.query(windows, predicate="intersects")
        cuts = intersection(patches[patch_ids], windows[window_ids])
        areas = geometric_area(cuts)
        positive = areas > 0
        cuts, areas, window_ids, patch_ids = (item[positive] for item in (cuts, areas, window_ids, patch_ids))
        if squared:
            triangles, parents = get_parts(constrained_delaunay_triangles(cuts), return_index=True)
            vertices = get_coordinates(triangles).reshape(-1, 4, 2)[:, :3]
            owners = patch_ids[parents]
            samples = values[owners, :1] + np.einsum("nvi,ni->nv", vertices-origins[owners, None], gradients[owners])
            integrals = np.bincount(window_ids[parents], geometric_area(triangles)*_moment(samples, 2),
                                    minlength=len(chosen))
        else:
            middle = centroid(cuts)
            points = np.column_stack((get_x(middle), get_y(middle)))
            samples = values[patch_ids, 0] + np.einsum("ni,ni->n", points-origins[patch_ids], gradients[patch_ids])
            integrals = np.bincount(window_ids, areas*samples, minlength=len(chosen))
        support = np.bincount(window_ids, areas, minlength=len(chosen))
        averages = np.divide(integrals, support, out=np.full(len(chosen), -np.inf), where=support > 0)
        index = int(np.argmax(averages))
        if averages[index] > best:
            best, location = float(averages[index]), xy[index].tolist()
    return float(best), location


def _reference(project, result, spec):
    # Reporting references may change without recomputing compatible physical
    # fields. Never reuse a former explicit override after it has been cleared.
    reservoirs = result.analysis.get("reservoirs", {})
    named = spec.reference_boundary
    value = spec.reference_temperature
    if value is None and not named:
        named = project.power.thermal_reference_boundary
        value = project.power.thermal_reference_temperature if not named else None
    if value is not None:
        if not np.isfinite(value) or value <= 0:
            raise SimulationError("Reference temperature must be finite and positive")
        return float(value)
    if named:
        reservoir = reservoirs.get(named)
        if not reservoir or reservoir.get("temperature") is None:
            raise SimulationError("Requested thermal reference reservoir is unavailable")
        value = reservoir["temperature"]
    elif "reservoirs" in result.analysis:
        temperatures = {item.get("temperature") for item in reservoirs.values()}
        value = next(iter(temperatures)) if len(temperatures) == 1 else None
    else:
        # Older archives may retain only the solver's scalar reference. It is
        # safe only when it was not a now-removed project reporting override.
        source_power = result.configuration.get("power", {})
        if not source_power.get("thermal_reference_boundary") and source_power.get("thermal_reference_temperature") is None:
            value = result.metrics.get("thermal_reference")
    if value is None:
        raise SimulationError("Temperature rise needs an explicit temperature or named reservoir")
    if not np.isfinite(value) or value <= 0:
        raise SimulationError("Reference temperature must be finite and positive")
    return float(value)


def _validate_result_context(project, result):
    from etlayout.io.project import physics_hash

    if not result.configuration:
        raise SimulationError("Result lacks source physical configuration")
    def physical(data):
        data = deepcopy(data)
        power = data.get("power", {})
        for key in ("measurements", "devices", "experiments", "recipes"):
            power[key] = []
        # Rules/measurement references do not change solved physical fields.
        from etlayout.models import RoutingRules
        power["rules"] = asdict(RoutingRules())
        power["thermal_reference_boundary"] = ""
        power["thermal_reference_temperature"] = None
        if power:
            data["power"] = power
        return physics_hash(data)
    if physical(project.to_dict()) != physical(result.configuration):
        raise SimulationError("Stale result: physical configuration has changed; recompute")


def _validate_temperatures(project, result, selected):
    for key, ids in selected.items():
        if not len(ids):
            continue
        temperature = element_nodal_values(result, key, "temperature")[ids]
        if not np.isfinite(temperature).all() or np.any(temperature <= 0):
            raise SimulationError(f"Invalid absolute temperatures on {key}")
        layer = project.layer(key)
        material = project.materials[layer.material_id]
        properties = []
        if "potential" in result.fields.get(key, {}):
            if layer.electrical.kind == "bulk":
                properties.append(material.resistivity)
            elif layer.electrical.kind == "sheet":
                properties.append(layer.electrical.sheet_resistance)
        if "thermal_power" in result.metrics:
            properties.append(material.thermal_conductivity)
        for model in properties:
            low, high = model.minimum_temperature, model.maximum_temperature
            if model.kind == "table":
                low, high = max(low, model.temperatures[0]), min(high, model.temperatures[-1])
            if np.any((temperature < low) | (temperature > high)):
                raise SimulationError(f"Material model on {key} evaluated outside its calibrated temperature range [{low:g}, {high:g}] K")

    for interface in project.interfaces:
        if not interface.enabled or not interface.material_id:
            continue
        ids = result.mesh.regions.get("interface:" + interface.id, np.array([], int))
        active = np.union1d(selected.get(interface.layer_a, []), selected.get(interface.layer_b, []))
        ids = _intersect_ids(ids, active).astype(int)
        if not len(ids):
            continue
        material = project.materials.get(interface.material_id)
        if material is None:
            raise SimulationError("Interface material is missing")
        properties = []
        if interface.electrical_kind == "via":
            properties.append(material.resistivity)
        if "thermal_power" in result.metrics and interface.thermal_kind == "stack":
            properties.append(material.thermal_conductivity)
        if not properties:
            continue
        temperature = (element_nodal_values(result, interface.layer_a, "temperature")[ids]
                       + element_nodal_values(result, interface.layer_b, "temperature")[ids]) / 2
        for model in properties:
            low, high = model.minimum_temperature, model.maximum_temperature
            if model.kind == "table":
                low, high = max(low, model.temperatures[0]), min(high, model.temperatures[-1])
            if np.any((temperature < low) | (temperature > high)):
                raise SimulationError(f"Interface {interface.id} material evaluated outside its calibrated temperature range")


def _absolute_patches(patches, values, weights):
    """Split linear sign changes before taking magnitude; preserve exact area."""
    if not np.any((values.min(axis=1) < 0) & (values.max(axis=1) > 0)):
        return patches, np.abs(values), weights
    output, samples, areas = [], [], []
    for polygon, nodal, area in zip(patches, values, weights):
        if nodal.min() >= 0 or nodal.max() <= 0:
            output.append(polygon)
            samples.append(np.abs(nodal))
            areas.append(area)
            continue
        xy = np.asarray(polygon.exterior.coords)[:3]
        for sign in (-1., 1.):
            original = list(zip(xy, sign * nodal))
            clipped = []
            for index, (point, value) in enumerate(original):
                previous, old = original[index - 1]
                if (value >= 0) != (old >= 0):
                    clipped.append((previous + old / (old - value) * (point - previous), 0.))
                if value >= 0:
                    clipped.append((point, value))
            for index in range(1, len(clipped) - 1):
                triangle = [clipped[0], clipped[index], clipped[index + 1]]
                piece = polygons(np.array([point for point, _ in triangle]))
                if piece.area > 0:
                    output.append(piece)
                    samples.append([value for _, value in triangle])
                    areas.append(piece.area)
    return output, np.asarray(samples), np.asarray(areas)


def _context_error(project, result):
    """Check mutable input identity anew for each synchronous report/call."""
    try:
        _validate_result_context(project, result)
    except (SimulationError, ValueError, KeyError) as error:
        return str(error)
    return ""


def evaluate_measurement(project, result, spec: MeasurementSpec, *, _support_cache=None):
    return _evaluate_measurement(project, result, spec, _support_cache=_support_cache,
                                 context_error=_context_error(project, result))


def _evaluate_measurement(project, result, spec, *, context_error, _support_cache=None, _cancel=None):
    record = {"id": spec.id, "quantity": spec.quantity, "status": "unavailable", "value": None,
              "specification": asdict(spec), "configuration_hash": result.configuration_hash,
              "mesh_triangles": len(result.mesh.triangles), "reason": "", "within_limits": False}
    try:
        if not result.converged:
            raise SimulationError("Nonconverged result")
        if context_error:
            raise SimulationError(context_error)
        if spec.quantity not in QUANTITIES:
            raise SimulationError(f"Unknown scoped quantity {spec.quantity!r}")
        if spec.weighting != "area" or not np.isfinite(spec.averaging_length) or spec.averaging_length < 0:
            raise SimulationError("Use area weighting and a nonnegative finite averaging length")
        validate_limits(spec)
        name, unit = QUANTITIES[spec.quantity]
        squared = spec.quantity == "contact_heat_density"
        direct = spec.quantity.startswith("port_") or spec.quantity in {"terminal_current", "loss_power"}
        if direct:
            _validate_temperatures(project, result, selected_elements(project, result, SupportSelector()))
            if spec.averaging_length:
                raise SimulationError("Physical averaging windows apply to spatial quantities only")
            if spec.quantity == "loss_power":
                value = result.analysis.get("loss_budget", {}).get("total_power")
                if any(asdict(spec.selector).values()):
                    raise SimulationError("Loss total uses the disjoint ledger; use scoped joule/contact integrals for spatial losses")
            else:
                records = result.terminals if spec.quantity == "terminal_current" else result.analysis.get("ports", {})
                if any(getattr(spec.selector, key) for key in ("layers", "materials", "nets", "devices", "contacts", "geometry")):
                    raise SimulationError("Branch measurements accept only explicit port/terminal IDs")
                if spec.selector.ports and spec.selector.regions:
                    raise SimulationError("Branch measurements cannot mix port and terminal-region selectors")
                keys = list(dict.fromkeys(spec.selector.ports or spec.selector.regions))
                if not keys:
                    raise SimulationError("A branch measurement requires explicit port/terminal IDs")
                _known(keys, records, "branch")
                samples = np.array([records[key][name] for key in keys], dtype=float)
                if not np.isfinite(samples).all():
                    raise SimulationError("Nonfinite branch quantity")
                value = _aggregate(np.repeat(samples[:, None], 3, axis=1), np.ones(len(samples)), spec)
        else:
            selected, clips = selected_support(project, result, spec.selector, _cache=_support_cache)
            _validate_temperatures(project, result, selected)
            patches, values, weights = [], [], []
            if spec.quantity.startswith("contact_") and spec.quantity != "contact_density":
                keys = spec.selector.contacts
                if not keys:
                    raise SimulationError("Contact measurements require explicit contacts")
                entries = []
                by_owner = {}
                for key in keys:
                    row = result.analysis.get("contacts", {}).get(key)
                    owner = row["interface_id"] if row else key
                    ids = np.asarray(row["elements"], int) if row else result.mesh.regions.get("interface:"+key, np.array([], int))
                    by_owner.setdefault(owner, []).append(ids)
                for owner, pieces in by_owner.items():
                    ids = np.unique(np.concatenate(pieces))
                    local = _contact_heat_root(result, owner) if squared else result.interface_fields.get(owner, {}).get(name)
                    if local is None:
                        raise SimulationError("Contact density is unavailable (ideal or nonunique reaction)")
                    if local.ndim == 1:
                        local = np.repeat(local[:, None], 3, axis=1)
                    support = np.unique(np.concatenate(list(selected.values()))) if selected else np.array([], int)
                    entries.append((owner, _intersect_ids(ids, support), local))
            else:
                entries = []
                for layer_id, ids in selected.items():
                    if not len(ids):
                        continue
                    layer = project.layer(layer_id)
                    if spec.quantity == "metal_current_density" and (layer.electrical.kind != "bulk" or project.materials[layer.material_id].category != "metal"):
                        raise SimulationError("Metal volumetric current density requires bulk metal with physical thickness; select sheet_current for sheets/semiconductors")
                    if spec.quantity == "sheet_current" and layer.electrical.kind == "bulk":
                        raise SimulationError("sheet_current stress is for sheet/semiconductor models; select metal_current_density for bulk metal")
                    if name in {"potential", "temperature"}:
                        local = element_nodal_values(result, layer_id, name)
                    else:
                        raw = result.fields[layer_id].get(name)
                        if raw is None:
                            raise SimulationError(f"Field {name} is unavailable on {layer_id}")
                        if raw.ndim == 2:
                            if spec.vector_component not in {"magnitude", "x", "y"}:
                                raise SimulationError("Unknown vector component")
                            raw = np.linalg.norm(raw, axis=1) if spec.vector_component == "magnitude" else raw[:, 0 if spec.vector_component == "x" else 1]
                        local = np.repeat(raw[:, None], 3, axis=1)
                    if spec.quantity == "effective_current_density":
                        if spec.effective_thickness is None or not np.isfinite(spec.effective_thickness) or spec.effective_thickness <= 0:
                            raise SimulationError("Reporting-only sheet conversion requires an explicit positive effective_thickness")
                        local = local / spec.effective_thickness
                        record["reporting_only"] = True
                    if spec.quantity == "temperature_rise":
                        reference = _reference(project, result, spec)
                        local = local - reference
                        record["reference_temperature"] = reference
                    entries.append((layer_id, ids, local))
            for _owner, ids, local in entries:
                if not len(ids):
                    continue
                if not np.isfinite(local[ids]).all():
                    raise SimulationError("Nonfinite or unrecoverable legacy values on selected support")
                clip = clips.get(_owner)
                if spec.quantity.startswith("contact_") and spec.quantity != "contact_density":
                    # Contact owners span two sheets. Union only their selected
                    # support; category intersections were already applied above.
                    clip = _support_clip(result, selected, clips, ids=ids)
                magnitude = squared or (spec.quantity == "contact_normal_density" and spec.vector_component == "magnitude")
                needs_patches = bool(spec.averaging_length or (magnitude and
                    np.any((local[ids].min(axis=1) < 0) & (local[ids].max(axis=1) > 0))))
                p, v, w = _patches(result, ids, local, clip, with_patches=needs_patches, _cache=_support_cache)
                if squared:
                    p, v, w = _absolute_patches(p, v, w)
                elif spec.quantity == "contact_normal_density":
                    if spec.vector_component == "magnitude":
                        p, v, w = _absolute_patches(p, v, w)
                    elif spec.vector_component != "signed":
                        raise SimulationError("Normal density component must be magnitude or signed (layer_a to layer_b)")
                patches.extend(p)
                values.extend(v)
                weights.extend(w)
            if not weights:
                raise SimulationError("Empty measurement support")
            values, weights = np.asarray(values), np.asarray(weights)
            if spec.averaging_length:
                if spec.aggregation != "maximum":
                    raise SimulationError("Physical hotspot averaging requires maximum aggregation")
                value, center = _hotspot(patches, values, spec, squared=squared, cancel=_cancel)
                record.update(window_center=center, window_definition="square clipped to support, supported-area normalization; origin-anchored half-length grid with centers on support (representative-point fallback for subgrid support)")
            else:
                value = (_aggregate_squared if squared else _aggregate)(values, weights, spec)
            record["support_area_m2"] = float(weights.sum())
            if spec.aggregation == "integral":
                unit = {"W/m²": "W", "A/m²": "A"}.get(unit, unit + " m²")
        if spec.aggregation == "exceedance":
            unit = "fraction"
        if spec.units and spec.units != unit:
            raise SimulationError(f"Quantity/aggregation requires {unit}, received {spec.units}")
        if value is None or not np.isfinite(value):
            raise SimulationError("Unavailable or nonfinite measurement")
        record["mesh_sensitive_raw_maximum"] = spec.aggregation == "maximum" and not spec.averaging_length and not direct
        within_limits = (spec.max_exceedance_fraction is None or value <= spec.max_exceedance_fraction
                         if spec.aggregation == "exceedance" else
                         (spec.lower_limit is None or value >= spec.lower_limit) and (spec.limit is None or value <= spec.limit))
        record.update(status="available", value=float(value), unit=unit, within_limits=within_limits)
    except Cancelled:
        raise
    except (SimulationError, ValueError, KeyError) as error:
        record["reason"] = str(error)
    return record


def evaluate_measurements(project, result, *, _support_cache=None):
    return _evaluate_measurements(project, result, _support_cache=_support_cache,
                                  context_error=_context_error(project, result))


def _evaluate_measurements(project, result, *, context_error, _support_cache=None, progress=None, cancel=None):
    ids = [s.id for s in project.power.measurements]
    if len(ids) != len(set(ids)) or any(not key for key in ids):
        raise SimulationError("Scoped measurements require unique nonempty IDs")
    records = {}
    for index, spec in enumerate(project.power.measurements):
        checkpoint(cancel, progress, index/max(1, len(ids)), f"Reporting measurement {index+1}/{len(ids)}: {spec.id}")
        records[spec.id] = _evaluate_measurement(project, result, spec, _support_cache=_support_cache,
                                                context_error=context_error, _cancel=cancel)
    return records


def _group_contact(project, result, selected, clips, interface, field, layer=None, *, _cache=None):
    """Integrate one contact over the group's physical intersection once."""
    support = np.unique(np.concatenate([ids for key, ids in selected.items()
        if key in {interface.layer_a, interface.layer_b}])) if any(
        len(ids) for key, ids in selected.items() if key in {interface.layer_a, interface.layer_b}) else np.array([], int)
    ids = _intersect_ids(result.mesh.regions.get("interface:" + interface.id, []), support)
    if not len(ids):
        return None, 0.
    clip = _support_clip(result, selected, clips, {interface.layer_a, interface.layer_b}, ids)
    squared = field == "power_density" and not layer
    if layer:
        local = element_nodal_values(result, layer, field)
    elif squared:
        local = _contact_heat_root(result, interface.id)
    else:
        local = result.interface_fields.get(interface.id, {}).get(field)
        if local is None:
            if field == "j_normal" and interface.electrical_kind == "ideal":
                from shapely import from_wkt
                candidates = [record for record in result.analysis.get("contacts", {}).values()
                    if record["interface_id"] == interface.id and record.get("current_a_to_b") is not None
                    and np.all(np.isin(record["elements"], ids))
                    and (clip is None or clip.covers(from_wkt(record["geometry_wkt"])))]
                if candidates:
                    return sum(record["current_a_to_b"] for record in candidates), sum(record["area"] for record in candidates)
            return None, 0.
        if local.ndim == 1:
            local = np.repeat(local[:, None], 3, axis=1)
    if not np.isfinite(local[ids]).all():
        return None, 0.
    _, values, weights = _patches(result, ids, local, clip, with_patches=False, _cache=_cache)
    if not len(weights):
        return None, 0.
    return float(weights @ (_moment(values, 2) if squared else values.mean(axis=1))), float(weights.sum())


def _group_port_layers(project, port_id):
    layers = {terminal.layer_id for port in project.power.ports if port.id == port_id
              for landing in port.landings for terminal in project.terminals if terminal.id == landing.terminal_id}
    # A port may land on redistribution rather than directly on device metal.
    # Follow only declared same-net passive vias, never device/contact loads.
    changed = True
    while changed:
        changed = False
        for interface in project.interfaces:
            if not interface.enabled or interface.electrical_kind != "via":
                continue
            a, b = project.layer(interface.layer_a), project.layer(interface.layer_b)
            if a.net_id and a.net_id == b.net_id and {a.id, b.id} & layers and not {a.id, b.id} <= layers:
                layers.update((a.id, b.id))
                changed = True
    return layers


def _group_landing(project, result, selected, clips, port_id):
    """Use a solved landing branch only when the entire footprint is selected."""
    from shapely import points
    records = result.analysis.get("ports", {}).get(port_id, {}).get("landings", [])
    chosen = []
    for record in records:
        terminal = next(t for t in project.terminals if t.id == record["terminal_id"])
        ids = selected.get(terminal.layer_id, [])
        if not len(ids):
            continue
        nodes = result.mesh.terminal_nodes.get(terminal.id)
        if nodes is None:
            nodes = np.unique(result.mesh.triangles[result.mesh.regions.get("terminal:" + terminal.id, np.array([], int))])
        if not len(nodes):
            continue
        supported = np.unique(result.mesh.triangles[ids])
        if not np.all(np.isin(nodes, supported)):
            continue
        clip = clips[terminal.layer_id]
        if clip is not None:
            if not np.all(clip.covers(points(result.mesh.points[nodes]))):
                continue
        chosen.append(record)
    if not chosen:
        return None, None
    weights = np.array([abs(row["current"]) for row in chosen])
    if weights.sum() == 0:
        weights[:] = 1.
    return sum(row["current"] for row in chosen), float(np.average([row["voltage"] for row in chosen], weights=weights))


def analyze_power(project, result, progress=None, cancel=None):
    # One report owns this memo; it cannot survive project/result changes or
    # historical transitions. Only exact supports are reused, never field data.
    support_cache = {}
    context_error = _context_error(project, result)
    result.analysis["measurements"] = _evaluate_measurements(project, result, _support_cache=support_cache,
        context_error=context_error, progress=lambda f, m: checkpoint(cancel, progress, .5*f, m), cancel=cancel)
    groups = {}
    for index, group in enumerate(project.power.devices):
        checkpoint(cancel, progress, .5 + .5*index/max(1, len(project.power.devices)),
                   f"Reporting device {index+1}/{len(project.power.devices)}: {group.id}")
        record = {"status": "unavailable", "reason": "", "current": None,
                  "active_width": group.active_width, "active_area": group.active_area,
                  "current_per_width": None, "current_per_area": None,
                  "source_voltage": None, "drain_voltage": None, "effective_gate_bias": None,
                  "temperature_max": None, "layer_loss": None, "contact_loss": None,
                  "definition": "Scoped group view; never added to the disjoint loss ledger"}
        groups[group.id] = record
        try:
            if not np.isfinite([group.active_width, group.active_area]).all() or min(group.active_width, group.active_area) < 0:
                raise SimulationError("Intended active dimensions must be finite and nonnegative")
            if context_error:
                raise SimulationError(context_error)
            selected, clips = selected_support(project, result, group.selector, _cache=support_cache)
            if not any(len(ids) for ids in selected.values()):
                raise SimulationError("Empty device support")
            temperature = _evaluate_measurement(project, result, MeasurementSpec(group.id, "temperature", group.selector), _support_cache=support_cache, context_error=context_error)
            loss = _evaluate_measurement(project, result, MeasurementSpec(group.id, "joule_density", group.selector, "integral"), _support_cache=support_cache, context_error=context_error)
            if temperature["status"] != "available" or loss["status"] != "available":
                raise SimulationError(temperature["reason"] or loss["reason"])
            record.update(temperature_max=temperature["value"], layer_loss=loss["value"])
            drain_layers = _group_port_layers(project, group.drain_port)
            source_layers = _group_port_layers(project, group.source_port)
            if group.drain_port and not drain_layers or group.source_port and not source_layers:
                raise SimulationError("Device source/drain port is missing")
            selected_layers = {key for key, ids in selected.items() if len(ids)}
            loads = [interface for interface in project.interfaces if interface.enabled
                and (interface.purpose == "device_load" or interface.electrical_kind == "load")
                and {interface.layer_a, interface.layer_b} & selected_layers]
            branch = []
            drain_samples = []
            source_samples = []
            contact_loss = 0.
            for interface in project.interfaces:
                if not interface.enabled or interface.electrical_kind == "none":
                    continue
                endpoints = {interface.layer_a, interface.layer_b}
                if not endpoints & selected_layers:
                    continue
                power, _ = _group_contact(project, result, selected, clips, interface, "power_density", _cache=support_cache)
                if power is not None:
                    contact_loss += power
                is_load = interface in loads
                drain_contact = bool(endpoints & drain_layers) and bool((endpoints - drain_layers) & selected_layers)
                source_contact = bool(endpoints & source_layers) and bool((endpoints - source_layers) & selected_layers)
                if not (is_load or drain_contact or source_contact):
                    continue
                current, area = _group_contact(project, result, selected, clips, interface, "j_normal", _cache=support_cache)
                if area == 0:
                    continue
                if is_load:
                    if interface.layer_a in drain_layers or interface.layer_b in source_layers:
                        drain_layer, source_layer, sign = interface.layer_a, interface.layer_b, 1.
                    elif interface.layer_b in drain_layers or interface.layer_a in source_layers:
                        drain_layer, source_layer, sign = interface.layer_b, interface.layer_a, -1.
                    elif len(endpoints & selected_layers) == 1:
                        drain_layer = next(iter(endpoints & selected_layers))
                        source_layer = next(iter(endpoints - selected_layers))
                        sign = 1. if drain_layer == interface.layer_a else -1.
                    else:
                        raise SimulationError("Load direction needs explicit source/drain ports or one selected active layer")
                    branch.append(sign * current)
                    for layer, samples in ((drain_layer, drain_samples), (source_layer, source_samples)):
                        value, measure = _group_contact(project, result, selected, clips, interface, "potential", layer, _cache=support_cache)
                        if value is not None:
                            samples.append((value, measure))
                else:
                    local_layers = endpoints & selected_layers
                    if len(local_layers) != 1:
                        continue
                    local_layer = next(iter(local_layers))
                    value, measure = _group_contact(project, result, selected, clips, interface, "potential", local_layer, _cache=support_cache)
                    if drain_contact and not loads:
                        branch.append(current if local_layer == interface.layer_b else -current)
                        if value is not None:
                            drain_samples.append((value, measure))
                    if source_contact and value is not None:
                        source_samples.append((value, measure))
            current, drain = _group_landing(project, result, selected, clips, group.drain_port)
            _, source = _group_landing(project, result, selected, clips, group.source_port)
            if branch:
                current = sum(branch)
            if drain_samples:
                drain = sum(value for value, _ in drain_samples) / sum(area for _, area in drain_samples)
            if source_samples:
                source = sum(value for value, _ in source_samples) / sum(area for _, area in source_samples)
            record.update(current=current, source_voltage=source, drain_voltage=drain, contact_loss=contact_loss,
                          local_voltage_definition="Area mean of device-side contact potential; complete landing branches use absolute-current-weighted footprint means")
            if current is None:
                raise SimulationError("Group current needs a selected drain/load contact or a complete dedicated landing; a shared port total is not a finger current")
            channel_selector = group.selector
            if group.gate_terminal:
                gate = result.terminals.get(group.gate_terminal, {}).get("voltage")
                channel_selector = replace(group.selector, regions=["terminal:" + group.gate_terminal])
            else:
                models = [project.layer(key).electrical for key in selected_layers if project.layer(key).electrical.kind == "hemt"]
                gate = models[0].gate_voltage if models and len({model.gate_voltage for model in models}) == 1 else None
            local = _evaluate_measurement(project, result, MeasurementSpec(group.id, "potential", channel_selector, "mean"), _support_cache=support_cache, context_error=context_error)
            if gate is not None and local["status"] == "available":
                record["effective_gate_bias"] = gate - local["value"]
                record["gate_bias_definition"] = "Gate voltage minus selected channel-area mean potential"
            record.update(status="available", current_per_width=current / group.active_width if group.active_width else None,
                          current_per_area=current / group.active_area if group.active_area else None)
        except (SimulationError, ValueError, KeyError) as error:
            record["reason"] = str(error)
    result.analysis["devices"] = groups
    checkpoint(cancel, progress, 1., "Power report complete")
    return result.analysis


def export_power_report(result, path):
    from etlayout.io.project import _json_dump
    root = Path(path)
    root.mkdir(parents=True, exist_ok=True)
    (root / "power-analysis.json").write_text(_json_dump(result.analysis), encoding="utf-8")
    for category in ("measurements", "contacts", "ports", "devices", "reservoirs"):
        rows = result.analysis.get(category, {})
        keys = sorted({key for row in rows.values() for key in row})
        with (root / (category + ".csv")).open("w", encoding="utf-8-sig", newline="") as stream:
            writer = csv_writer(stream)
            writer.writerow(["record_id", *keys])
            for key, row in rows.items():
                writer.writerow([key, *[row.get(k) for k in keys]])
    return root / "power-analysis.json"
