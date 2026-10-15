# SPDX-License-Identifier: MPL-2.0
"""Shared, finite support for maps, scales, probes and exported result fields.

Connected regions use shared triangle edges: two islands touching at one vertex
remain separate. Result mesh arrays are treated as immutable after a solve.
"""
from __future__ import annotations

import math
import weakref
from collections import OrderedDict
from dataclasses import dataclass

import numpy as np
from scipy.sparse import coo_matrix
from scipy.sparse.csgraph import connected_components

from etlayout.visualization.sampling import element_nodal_values, scalar_field

NODAL_FIELDS = frozenset({"potential", "temperature", "temperature_rise"})
_COMPONENT_CACHE: OrderedDict = OrderedDict()


@dataclass(frozen=True)
class FieldSupport:
    data: np.ndarray
    indices: np.ndarray
    nodes: np.ndarray
    values: np.ndarray
    nodal: bool
    limits: tuple[float, float] | None
    bounds: tuple[float, float, float, float] | None
    element_data: np.ndarray | None = None
    resolution_scale: float = 0.


def display_limits(support_limits, *, limits=None, logarithmic=False, colormap="teal", provenance=None,
                   resolution_scale=0.):
    """Validate color limits and center automatic diverging differences on zero.

    Roundoff-sized automatic spans receive the same relative padding used for
    constant-field exports. The precision test has no absolute floor: meaningful
    tiny-valued fields retain their range. Raw support and statistics never change.
    An identically zero difference uses epsilon around zero for a neutral scale.
    """
    if support_limits is None:
        return None
    low, high = map(float, support_limits if limits is None else limits)
    if limits is not None and low == high:
        raise ValueError("Manual color limits require a maximum greater than the minimum")
    if not math.isfinite(low) or not math.isfinite(high) or high < low or (logarithmic and low <= 0):
        raise ValueError("Use finite increasing color limits, positive for logarithmic scale")
    if (limits is None and not logarithmic and colormap == "coolwarm" and
            (provenance or {}).get("kind") == "difference"):
        extent = max(abs(low), abs(high)) or np.finfo(float).eps
        low, high = -extent, extent
    elif limits is None and not logarithmic and resolution_scale and high - low <= 8 * np.finfo(float).eps * resolution_scale:
        # Subtracting a reservoir from absolute temperature can amplify ULP-only
        # noise. Use the precision of the operands, preserving every raw value.
        center = low / 2 + high / 2
        padding = 64 * np.finfo(float).eps * resolution_scale
        low, high = center - padding, center + padding
    elif limits is None and high > low:
        scale = max(abs(low), abs(high))
        if high - low <= 8 * np.finfo(float).eps * scale:
            center = low / 2 + high / 2
            maximum = np.finfo(float).max
            if logarithmic:
                low, high = center / 1.001, min(maximum, center * 1.001)
            else:
                padding = scale * 1e-6
                low, high = max(-maximum, center - padding), min(maximum, center + padding)
    return low, high


def _components(mesh, layer: str) -> tuple[np.ndarray, ...]:
    indices = np.unique(np.asarray(mesh.elements(layer), dtype=np.int64))
    key = (id(mesh), layer, hash(indices.tobytes()))
    cached = _COMPONENT_CACHE.get(key)
    if cached is not None and cached[0]() is mesh:
        _COMPONENT_CACHE.move_to_end(key)
        return cached[1]
    triangles = mesh.triangles[indices]
    if not len(triangles):
        return ()
    edges = np.sort(triangles[:, [[0, 1], [1, 2], [2, 0]]].reshape(-1, 2), axis=1)
    owners = np.repeat(np.arange(len(indices)), 3)
    order = np.lexsort((edges[:, 1], edges[:, 0]))
    same = np.all(edges[order[1:]] == edges[order[:-1]], axis=1)
    first, second = owners[order[:-1][same]], owners[order[1:][same]]
    graph = coo_matrix((np.ones(len(first)), (first, second)), shape=(len(indices), len(indices)))
    _, labels = connected_components(graph, directed=False)
    order = np.argsort(labels, kind="stable")
    boundaries = np.flatnonzero(np.diff(labels[order])) + 1
    components = tuple(sorted(np.split(indices[order], boundaries), key=lambda ids: ids[0]))
    _COMPONENT_CACHE[key] = (weakref.ref(mesh), components)
    _COMPONENT_CACHE.move_to_end(key)
    while len(_COMPONENT_CACHE) > 16:
        _COMPONENT_CACHE.popitem(last=False)
    return components


def region_elements(result, layer: str, region: str = "") -> np.ndarray:
    """Global triangle IDs in the intersection of a layer and selected region."""
    mesh = result.mesh
    base = np.unique(np.asarray(mesh.elements(layer), dtype=np.int64))
    if not region:
        return base
    if region.startswith("component:"):
        components = _component_regions(result, layer)
        if region in components:
            return components[region]
        raise ValueError(f"Unknown connected region {region!r} on layer {layer!r}")
    if region in mesh.regions and not region.startswith("layer:"):
        return np.intersect1d(base, mesh.regions[region])
    raise ValueError(f"Unknown result region {region!r}")


def _component_regions(result, layer: str) -> dict[str, np.ndarray]:
    """Keep source component identities when difference maps trim their mesh."""
    configuration = getattr(result, "configuration", {})
    source = configuration.get("visualization", {}).get("source_components", {}).get(layer)
    if source is not None:
        base = result.mesh.elements(layer)
        return {key: np.intersect1d(base, np.asarray(indices, dtype=np.int64))
                for key, indices in source.items() if key.startswith("component:")}
    components = _components(result.mesh, layer)
    return {f"component:{i}": ids for i, ids in enumerate(components, 1)} if len(components) > 1 else {}


def _region_label(result, key: str) -> str:
    kind, _, identity = key.partition(":")
    collection, label = {
        "interface": ("interfaces", "Interface"), "terminal": ("terminals", "Terminal"),
        "thermal": ("thermal_boundaries", "Thermal boundary"), "heat": ("heat_sources", "Heat source"),
        "refinement": ("refinements", "Refinement"),
    }.get(kind, ("", kind.replace("_", " ").capitalize()))
    configuration = result.configuration
    entries = configuration.get("mesh", {}).get(collection, []) if kind == "refinement" else configuration.get(collection, [])
    name = next((entry.get("name") or identity for entry in entries if entry.get("id") == identity), identity)
    return f"{label} · {name}" if identity else key


def region_options(result, layer: str) -> list[tuple[str, str]]:
    """Named mesh selections and disconnected pieces of a physical layer."""
    options = [("", "All solved regions")]
    components = _component_regions(result, layer)
    options.extend((key, f"Connected region {key.split(':', 1)[1]} ({len(ids):,} elements)")
                   for key, ids in components.items() if len(ids))
    for key in sorted(result.mesh.regions):
        if not key.startswith("layer:") and len(region_elements(result, layer, key)):
            options.append((key, _region_label(result, key)))
    return options


def field_support(result, layer: str, field: str, *, region: str = "", logarithmic: bool = False) -> FieldSupport:
    """Return exactly the triangles that define the displayed scalar field.

Nodal fields require three finite vertices. Logarithmic maps additionally require
all three vertices to be positive, so interpolation never enters an invalid
logarithmic domain. Limits use the rendered vertices, not triangle averages.
"""
    data = scalar_field(result, layer, field)
    indices = region_elements(result, layer, region)
    nodal = field in NODAL_FIELDS
    element_data = element_nodal_values(result, layer, field) if nodal else None
    if nodal:
        samples = element_data[indices]
        valid = np.isfinite(samples).all(axis=1)
        if logarithmic:
            valid &= (samples > 0).all(axis=1)
        indices = indices[valid]
        # Dividing before summing avoids overflow for otherwise finite values.
        values = (samples[valid] / 3.).sum(axis=1)
    else:
        values = data[indices]
        valid = np.isfinite(values)
        if logarithmic:
            valid &= values > 0
        indices, values = indices[valid], values[valid]
    nodes = np.unique(result.mesh.triangles[indices])
    if not len(indices):
        return FieldSupport(data, indices, nodes, values, nodal, None, None, element_data)
    extent = element_data[indices] if nodal else values
    limits = (float(extent.min()), float(extent.max()))
    points = result.mesh.points[nodes]
    bounds = (*map(float, points.min(axis=0)), *map(float, points.max(axis=0)))
    resolution_scale = 0.
    if field == "temperature_rise":
        reference = result.metrics.get("thermal_reference", result.configuration.get("solver", {}).get("initial_temperature", 300.))
        # Reconstruct the magnitude of the subtraction operands from the
        # supported absolute values, not unrelated/unavailable global vertices.
        resolution_scale = max(abs(float(reference)), float(np.max(np.abs(extent + reference))))
    return FieldSupport(data, indices, nodes, values, nodal, limits, bounds, element_data, resolution_scale)
