# SPDX-License-Identifier: MPL-2.0
"""Canonical physical geometry and explicit integer-database-unit conversions."""
from __future__ import annotations

import hashlib

import klayout.db as kdb
import shapely
from shapely.geometry import GeometryCollection, Polygon
from shapely.ops import unary_union

from etlayout.geometry.prepare import geometry_region as as_spec
from etlayout.geometry.prepare import polygon_parts

__all__ = ["as_spec", "canonical_geometry_id", "to_region", "from_region"]


def canonical_geometry_id(geometry, prefix="", grid=1e-15):
    """Geometry identity excludes polygon order, ring start and collinear vertices."""
    merged = unary_union(list(polygon_parts(geometry)))
    normalized = shapely.normalize(shapely.set_precision(merged, grid).simplify(grid * .1,
                                                                                 preserve_topology=True))
    digest = hashlib.sha256(normalized.wkb).hexdigest()[:16]
    return f"{prefix}-{digest}" if prefix else digest


def to_region(geometry, dbu):
    """Round coordinates to nearest DBU (ties to even), then integer Boolean union."""
    region = kdb.Region()
    for polygon in polygon_parts(geometry):
        hull = [kdb.Point(round(x / dbu), round(y / dbu)) for x, y in polygon.exterior.coords[:-1]]
        item = kdb.Polygon(hull)
        for ring in polygon.interiors:
            item.insert_hole([kdb.Point(round(x / dbu), round(y / dbu)) for x, y in ring.coords[:-1]])
        region.insert(item)
    region.merge()
    return region


def from_region(region, dbu):
    polygons = []
    for item in region.each():
        hull = [(p.x * dbu, p.y * dbu) for p in item.each_point_hull()]
        holes = [[(p.x * dbu, p.y * dbu) for p in item.each_point_hole(i)] for i in range(item.holes())]
        if len(hull) >= 3:
            polygons.append(Polygon(hull, holes))
    return unary_union(polygons) if polygons else GeometryCollection()
