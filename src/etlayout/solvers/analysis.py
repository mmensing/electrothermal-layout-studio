# SPDX-License-Identifier: MPL-2.0
"""Dimensional field integrals for postprocessing."""
from __future__ import annotations

from itertools import pairwise

import numpy as np

from etlayout.models import SimulationError, SimulationResult


def branch_current(result: SimulationResult, layer_id: str,
                   start: list[float], end: list[float]) -> float:
    """Integrate sheet-current normal to an arbitrary straight cut, in amperes.

    Positive is toward the left normal of start→end; coordinates use meters.
    Fields are constant on each P1 triangle, so intersections are integrated
    exactly. If a cut coincides with an element edge, the two one-sided field
    estimates are averaged; the edge is counted once. A mesh refinement study
    is needed near contacts, corners and material interfaces. This is lateral
    sheet current; vertical current is in metrics['contacts'][id].
    """
    from shapely.geometry import LineString, Polygon
    from shapely.strtree import STRtree

    if layer_id not in result.fields or "sheet_current" not in result.fields[layer_id]:
        raise SimulationError(f"Layer {layer_id} has no electrical sheet-current field")
    a, b = np.asarray(start, dtype=float), np.asarray(end, dtype=float)
    if a.shape != (2,) or b.shape != (2,) or not np.all(np.isfinite([a, b])):
        raise SimulationError("Current cut needs two finite x–y endpoints in meters")
    direction = b - a
    length = float(np.linalg.norm(direction))
    if length == 0:
        raise SimulationError("Current cut endpoints must differ")
    tangent = direction / length
    normal = np.array([-tangent[1], tangent[0]])
    cut = LineString([a, b])
    mesh = result.mesh
    elements = mesh.elements(layer_id)
    polygons = [Polygon(mesh.points[mesh.triangles[element]]) for element in elements]
    tree = STRtree(polygons)
    intervals = []
    for index in tree.query(cut, predicate="intersects"):
        intersection = polygons[index].intersection(cut)
        if intersection.geom_type != "LineString" or intersection.length == 0:
            continue
        coords = np.asarray(intersection.coords)
        positions = (coords - a) @ tangent
        intervals.append((float(positions.min()), float(positions.max()), int(elements[index])))
    if not intervals:
        return 0.
    tolerance = length * 1e-12
    positions = sorted({position for low, high, _ in intervals for position in (low, high)})
    breaks = [positions[0]]
    for position in positions[1:]:
        if position - breaks[-1] > tolerance:
            breaks.append(position)
    current = result.fields[layer_id]["sheet_current"]
    integral = 0.
    for low, high in pairwise(breaks):
        midpoint = (low + high) / 2.
        active = [element for begin, stop, element in intervals if begin - tolerance <= midpoint <= stop + tolerance]
        if active:
            vectors = current[active]
            if not np.all(np.isfinite(vectors)):
                raise SimulationError("Current cut intersects a nonfinite electrical field")
            integral += float(np.mean(vectors, axis=0) @ normal) * (high - low)
    return integral
