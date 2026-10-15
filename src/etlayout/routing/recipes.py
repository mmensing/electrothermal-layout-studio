# SPDX-License-Identifier: MPL-2.0
"""Declarative bounded bus, finger-feed, strap and via-array polygon recipes."""
from __future__ import annotations

import math

from shapely.geometry import Polygon, box
from shapely.ops import unary_union

from etlayout.models import SimulationError


def _number(parameters, key, default, *, minimum=None, integer=False):
    value = parameters.get(key, default)
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise SimulationError(f"Recipe parameter {key} must be a finite number")
    if integer and value != int(value):
        raise SimulationError(f"Recipe parameter {key} must be an integer")
    if minimum is not None and value < minimum:
        raise SimulationError(f"Recipe parameter {key} must be at least {minimum:g}")
    return int(value) if integer else float(value)


def recipe_geometry(recipe, edit, rules):
    """All lengths are SI; edit bounds are hard limits checked after snapping."""
    if recipe.version != 1:
        raise SimulationError(f"Unsupported recipe version {recipe.version}")
    if edit.is_empty or edit.area <= 0:
        raise SimulationError("Routing recipe requires a nonempty editable polygon")
    x0, y0, x1, y1 = edit.bounds
    p = recipe.parameters
    bounds = p.get("bounds", {})
    for key, interval in bounds.items():
        if key not in p or len(interval) != 2 or not interval[0] <= p[key] <= interval[1]:
            raise SimulationError(f"Recipe parameter {key} is outside its declared bounds")
    if recipe.kind in {"bus", "strap"}:
        allowed = {"bounds", "axis", "width", "start_width", "end_width", "widths", "stations", "center", "start", "end", "anchors"}
        axis = p.get("axis", "x")
        if axis not in {"x", "y"}:
            raise SimulationError("Bus axis must be x or y")
        lo, hi = (x0, x1) if axis == "x" else (y0, y1)
        crosslo, crosshi = (y0, y1) if axis == "x" else (x0, x1)
        start = _number(p, "start", lo)
        end = _number(p, "end", hi)
        center = _number(p, "center", (crosslo + crosshi) / 2)
        width = _number(p, "width", crosshi - crosslo, minimum=rules.dbu)
        widths = p.get("widths", [p.get("start_width", width), p.get("end_width", width)])
        stations = p.get("stations", [start + i * (end - start) / (len(widths) - 1) for i in range(len(widths))] if len(widths) > 1 else [])
        if len(widths) < 2 or len(stations) != len(widths) or end <= start:
            raise SimulationError("Bus requires increasing stations and at least two widths")
        if any(not math.isfinite(v) or v <= 0 for v in widths) or any(not math.isfinite(v) for v in stations):
            raise SimulationError("Bus stations/widths must be finite and widths positive")
        if any(a >= b for a, b in zip(stations[:-1], stations[1:])):
            raise SimulationError("Bus stations must increase strictly")
        points = [(s, center - w / 2) for s, w in zip(stations, widths)] + [(s, center + w / 2) for s, w in reversed(list(zip(stations, widths)))]
        if axis == "y":
            points = [(y, x) for x, y in points]
        result = Polygon(points)
    elif recipe.kind == "finger_feed":
        allowed = {"bounds", "axis", "count", "pitch", "finger_width", "bus_width", "end", "anchors"}
        axis = p.get("axis", "x")
        if axis not in {"x", "y"}:
            raise SimulationError("Finger-feed array axis must be x or y")
        if axis == "y":
            x0, y0, x1, y1 = y0, x0, y1, x1
        count = _number(p, "count", 4, minimum=1, integer=True)
        if count > 10000:
            raise SimulationError("Finger count exceeds recipe budget of 10000")
        pitch = _number(p, "pitch", (x1 - x0) / count, minimum=rules.dbu)
        finger = _number(p, "finger_width", pitch / 2, minimum=rules.dbu)
        bus = _number(p, "bus_width", min((y1 - y0) / 4, pitch), minimum=rules.dbu)
        ending = p.get("end", "single")
        if ending not in {"single", "dual"}:
            raise SimulationError("Finger feed end must be single or dual")
        centers = [(x0 + x1) / 2 + (i - (count - 1) / 2) * pitch for i in range(count)]
        shapes = [box(x0, y0, x1, y0 + bus)]
        if ending == "dual":
            shapes.append(box(x0, y1 - bus, x1, y1))
        shapes += [box(x - finger / 2, y0, x + finger / 2, y1) for x in centers]
        result = unary_union(shapes)
        if axis == "y":
            from shapely.affinity import affine_transform
            result = affine_transform(result, [0, 1, 1, 0, 0, 0])
    elif recipe.kind == "via_array":
        allowed = {"bounds", "columns", "rows", "count", "pitch", "pitch_x", "pitch_y", "size", "pattern", "grade", "center_x", "center_y", "anchors"}
        size = _number(p, "size", rules.via_size, minimum=rules.dbu)
        pitch = _number(p, "pitch", rules.via_pitch, minimum=size)
        px, py = _number(p, "pitch_x", pitch, minimum=size), _number(p, "pitch_y", pitch, minimum=size)
        columns = _number(p, "columns", p.get("count", 1), minimum=1, integer=True)
        rows = _number(p, "rows", 1, minimum=1, integer=True)
        if rows * columns > 10000:
            raise SimulationError("Via count exceeds recipe budget of 10000")
        pattern = p.get("pattern", "uniform")
        if pattern not in {"uniform", "graded", "staggered"}:
            raise SimulationError("Via pattern must be uniform, graded or staggered")
        grade = _number(p, "grade", 0., minimum=0.)
        if grade > 4:
            raise SimulationError("Via grading must be bounded in [0, 4]")
        positions = [0.]
        for i in range(1, columns):
            positions.append(positions[-1] + px * (1 + grade * (i - 1) / max(columns - 2, 1) if pattern == "graded" else 1))
        cx, cy = _number(p, "center_x", (x0 + x1) / 2), _number(p, "center_y", (y0 + y1) / 2)
        shapes = []
        for j in range(rows):
            for pos in positions:
                x = cx + pos - positions[-1] / 2 + (px / 2 if pattern == "staggered" and j % 2 else 0.)
                y = cy + (j - (rows - 1) / 2) * py
                shapes.append(box(x - size / 2, y - size / 2, x + size / 2, y + size / 2))
        result = unary_union(shapes)
    else:
        raise SimulationError(f"Unsupported routing recipe {recipe.kind!r}")
    unknown = set(p) - allowed
    if unknown:
        raise SimulationError(f"Unsupported parameters for {recipe.kind}: {sorted(unknown)}")
    if not result.is_valid or result.area <= 0:
        raise SimulationError("Generated geometry is empty or invalid")
    return result
