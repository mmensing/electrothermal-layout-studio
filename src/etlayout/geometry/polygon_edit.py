# SPDX-License-Identifier: MPL-2.0
"""Bounded, instance-local polygon edits written to immutable layout copies."""
from __future__ import annotations

import math
import os
from copy import deepcopy
from dataclasses import dataclass
from pathlib import Path
from tempfile import NamedTemporaryFile

import klayout.db as kdb
from shapely.geometry import Polygon
from shapely.validation import explain_validity

from etlayout.geometry.layout import LayoutDocument
from etlayout.io.project import file_hash
from etlayout.models import SimulationError, checkpoint
from etlayout.routing.generate import _isolate
from etlayout.routing.geometry import from_region, to_region


@dataclass(frozen=True)
class EditablePolygon:
    mask: str
    instance_path: tuple[int, ...]
    shape_index: int
    geometry: Polygon
    cell_name: str
    kind: str
    source_hash: str

    @property
    def label(self):
        placement = "/".join(map(str, self.instance_path)) or "top"
        return f"{self.mask} · {self.cell_name} [{placement}] · {self.kind} {self.shape_index + 1}"


def editable_polygons(project, *, max_shapes=10000, max_placements=100000,
                      progress=None, cancel=None):
    """Return original shapes, never Boolean-merged or ROI-clipped footprints.

    Paths are polygonized explicitly when edited; text and other non-area shapes
    are retained in the layout but cannot be selected. Hierarchy/array addresses
    match LayoutDocument's stable placement indices.
    """
    digest = file_hash(project.layout.path)
    if project.layout.sha256 and project.layout.sha256 != digest:
        raise SimulationError("Layout changed on disk. Reimport or relink before editing.")
    document = LayoutDocument(project.layout.path, progress=progress, cancel=cancel)
    if file_hash(document.path) != digest:
        raise SimulationError("Layout changed while loading. Reimport or relink before editing.")
    root, initial_transform = document._selection(project.layout.cell, project.layout.instance_path)
    dbu = document.dbu_um * 1e-6
    targets = []
    placements = 0

    def visit(cell, path, transform, ancestors):
        nonlocal placements
        checkpoint(cancel)
        if cell.cell_index() in ancestors:
            raise SimulationError("Cyclic layout hierarchy cannot be edited.")
        for layer in document.layers:
            for index, shape in enumerate(cell.shapes(layer["index"]).each()):
                if not (shape.is_box() or shape.is_polygon() or shape.is_simple_polygon() or shape.is_path()):
                    continue
                if len(targets) >= max_shapes:
                    raise SimulationError(f"Layout editor supports at most {max_shapes:,} shapes per selected hierarchy. Select a smaller instance first.")
                polygon = shape.polygon.transformed(transform)
                geometry = from_region(kdb.Region(polygon).merged(), dbu)
                if geometry.geom_type != "Polygon" or not geometry.is_valid:
                    raise SimulationError(f"Invalid polygon on mask {layer['key']} in {cell.name}; repair it before vertex editing.")
                kind = "path outline" if shape.is_path() else "box" if shape.is_box() else "polygon"
                targets.append(EditablePolygon(layer["key"], tuple(path), index, geometry, cell.name, kind, digest))
        for index, (child, placed) in enumerate(document._placements(cell)):
            placements += 1
            if placements > max_placements:
                raise SimulationError("Layout editor hierarchy budget exceeded; select a smaller instance first.")
            visit(child, [*path, index], transform * placed, ancestors | {cell.cell_index()})

    visit(root, project.layout.instance_path, initial_transform, set())
    checkpoint(cancel, progress, 1., f"{len(targets):,} original layout shapes ready for editing")
    return targets, dbu


def snapped_polygon(rings, dbu):
    """Validate requested rings before/after database-unit rounding."""
    if not math.isfinite(dbu) or dbu <= 0:
        raise SimulationError("Invalid layout database unit")
    original, rounded = [], []
    for ring in rings:
        requested, points = [], []
        for x, y in ring:
            if not math.isfinite(x) or not math.isfinite(y):
                raise SimulationError("Polygon coordinates must be finite")
            points.append((round(x / dbu) * dbu, round(y / dbu) * dbu))
            requested.append((x, y))
        if len(set(points)) < 3 or len(set(points)) != len(points):
            raise SimulationError("Each polygon ring needs at least three distinct vertices; coincident vertices are not allowed.")
        original.append(requested)
        rounded.append(points)
    if not rounded:
        raise SimulationError("A polygon requires an exterior ring")
    # Snapping must not silently repair a crossing or a hole outside its hull.
    # Otherwise a geometrically invalid draft can become a different valid
    # outline before the user has a chance to correct it.
    for coordinates in (original, rounded):
        geometry = Polygon(coordinates[0], coordinates[1:])
        if geometry.is_empty or geometry.area <= 0 or not geometry.is_valid:
            raise SimulationError("Invalid polygon: " + explain_validity(geometry))
    return geometry


def snap_coordinate(value, dbu, grid_units=1):
    """Snap a finite SI coordinate to an integer multiple of the source DBU."""
    if not math.isfinite(dbu) or dbu <= 0 or isinstance(grid_units, bool) or not isinstance(grid_units, int) or grid_units < 1:
        raise SimulationError("Snap grid must be a positive integer number of layout database units")
    if not math.isfinite(value):
        raise SimulationError("Polygon coordinates must be finite")
    return round(value / (dbu * grid_units)) * (dbu * grid_units)


def insert_midpoint(rings, ring, index, dbu, grid_units=1):
    """Insert on the selected outgoing edge; reject collapsed/invalid proposals."""
    candidate = deepcopy(rings)
    points = candidate[ring]
    start, end = points[index], points[(index + 1) % len(points)]
    midpoint = tuple(snap_coordinate((a + b) / 2, dbu, grid_units) for a, b in zip(start, end, strict=True))
    points.insert(index + 1, midpoint)
    snapped_polygon(candidate, dbu)
    return candidate


def delete_vertex(rings, ring, index, dbu):
    """Delete only a vertex: never silently remove a triangular ring or hole."""
    if len(rings[ring]) <= 3:
        raise SimulationError("A ring must retain at least three vertices; deleting a hole is not a vertex operation")
    candidate = deepcopy(rings)
    del candidate[ring][index]
    snapped_polygon(candidate, dbu)
    return candidate


def translated_rings(rings, dx, dy, dbu, grid_units=1):
    """Move all rings by one snapped displacement without reshaping their edges."""
    dx, dy = (snap_coordinate(value, dbu, grid_units) for value in (dx, dy))
    candidate = [[(x + dx, y + dy) for x, y in ring] for ring in rings]
    snapped_polygon(candidate, dbu)
    return candidate


def edit_polygon(project, target, geometry, destination, *, progress=None, cancel=None):
    """Change exactly one placed original shape and verify the exported copy.

    Ancestors of the selected array placement are cloned. Shape and instance
    properties, unrelated geometry, units and project selection are preserved.
    Imported sources and previous undo revisions are never written.
    """
    destination = Path(destination).resolve()
    source = Path(project.layout.path).resolve()
    if destination == source or destination.exists() or destination.suffix.lower() not in {".oas", ".oasis", ".gds", ".gdsii"}:
        raise SimulationError("Layout edits require a new GDS/OASIS file, never the imported source or an existing revision.")
    digest = file_hash(source)
    if digest != target.source_hash or (project.layout.sha256 and digest != project.layout.sha256):
        raise SimulationError("The layout revision changed; reload the selected polygon before applying.")
    if tuple(target.instance_path[:len(project.layout.instance_path)]) != tuple(project.layout.instance_path):
        raise SimulationError("Polygon is outside the selected hierarchy")
    document = LayoutDocument(source, progress=progress, cancel=cancel)
    if file_hash(source) != digest:
        raise SimulationError("The layout changed while loading; reload the selected polygon before applying.")
    layout = document._layout
    root = document._cell(project.layout.cell)
    selected, transform = document._selection(root.name, list(target.instance_path))
    number, datatype = map(int, target.mask.split("/"))
    layer = layout.find_layer(number, datatype)
    shapes = list(selected.shapes(layer).each()) if layer is not None else []
    if not 0 <= target.shape_index < len(shapes):
        raise SimulationError("Selected polygon no longer exists")
    shape = shapes[target.shape_index]
    if not (shape.is_box() or shape.is_polygon() or shape.is_simple_polygon() or shape.is_path()):
        raise SimulationError("Selected object has no editable polygon outline")
    dbu = document.dbu_um * 1e-6
    old = from_region(kdb.Region(shape.polygon.transformed(transform)).merged(), dbu)
    if not old.equals(target.geometry):
        raise SimulationError("Selected polygon changed; reload before applying.")
    if not isinstance(geometry, Polygon):
        raise SimulationError("Vertex editing requires exactly one polygon with its original holes.")
    rings = [list(geometry.exterior.coords)[:-1], *[list(r.coords)[:-1] for r in geometry.interiors]]
    effective = snapped_polygon(rings, dbu)
    if effective.equals(old):
        return deepcopy(project)
    # Root definitions referenced elsewhere remain unchanged, including other
    # top cells. Keep the working cell's name for project/group compatibility.
    original_name = root.name
    cell_properties = [root.properties()]
    for depth in range(1, len(target.instance_path) + 1):
        ancestor, _ = document._selection(original_name, list(target.instance_path[:depth]))
        cell_properties.append(ancestor.properties())
    edited_root = root.dup()
    edited_root.set_properties(cell_properties[0])
    root.name = layout.unique_cell_name(original_name + "_imported")
    edited_root.name = original_name
    selected, transform = _isolate(document, edited_root, list(target.instance_path))
    # KLayout Cell.dup copies contents, but not the cell's own properties.
    for depth, properties in enumerate(cell_properties[1:], 1):
        ancestor, _ = document._selection(original_name, list(target.instance_path[:depth]))
        ancestor.set_properties(properties)
    shape = list(selected.shapes(layer).each())[target.shape_index]
    properties = shape.properties()
    local = to_region(effective, dbu).transformed(transform.inverted())
    expected = from_region(local.transformed(transform), dbu)
    if not expected.equals(effective):
        raise SimulationError("This instance transform cannot represent the requested vertices exactly at the layout database unit; edit its untransformed cell instead.")
    shape.delete()
    for polygon in local.each():
        replacement = selected.shapes(layer).insert(polygon)
        if properties:
            replacement.set_properties(properties)
    if root.parent_cells() == 0:
        # Undo owns the previous immutable file; do not accumulate unused root
        # revisions inside every new file. Referenced imported definitions stay.
        layout.prune_cell(root.cell_index(), -1)
    checkpoint(cancel, progress, .7, "Writing project-owned edited layout")
    destination.parent.mkdir(parents=True, exist_ok=True)
    # Verify a temporary file before publishing. Windows rename and exclusive
    # links on other platforms prevent a destination created during export from
    # being overwritten. Windows rename avoids requiring hard-link privileges.
    # KLayout writes canonical extensions but reads all four supported aliases.
    suffix = ".oas" if destination.suffix.lower() in {".oas", ".oasis"} else ".gds"
    with NamedTemporaryFile(prefix=".layout-edit-", suffix=suffix,
                            dir=destination.parent, delete=False) as stream:
        temporary = Path(stream.name)
    try:
        options = kdb.SaveLayoutOptions()
        options.format = "OASIS" if suffix == ".oas" else "GDS2"
        # Recompression groups instances and can reorder their placement
        # addresses, which are used by project/device-group selections.
        options.oasis_compression_level = 0
        options.gds2_write_cell_properties = True
        options.gds2_write_file_properties = True
        layout.write(str(temporary), options)
        verified = LayoutDocument(temporary)
        check_cell, check_transform = verified._selection(original_name, list(target.instance_path))
        written = check_cell.shapes(verified._layout.find_layer(number, datatype))
        found = any(from_region(kdb.Region(shape.polygon.transformed(check_transform)).merged(), dbu).equals(effective)
                    for shape in written.each()
                    if shape.is_polygon() or shape.is_box() or shape.is_simple_polygon() or shape.is_path())
        if not found:
            raise SimulationError("Exported polygon differs from the preview; the working project was not changed.")
        if file_hash(source) != digest:
            raise SimulationError("The imported layout changed during export; reload before applying.")
        checkpoint(cancel)
        try:
            if os.name == "nt":
                os.rename(temporary, destination)
            else:
                os.link(temporary, destination)
        except OSError as error:
            raise SimulationError("Cannot publish a new immutable layout revision. Choose a writable local directory and a new filename; existing files are never replaced.") from error
    finally:
        temporary.unlink(missing_ok=True)
    candidate = deepcopy(project)
    candidate.layout.path = str(destination)
    candidate.layout.sha256 = file_hash(destination)
    # Recipes derived from an older source require deliberate revalidation.
    # Keep their original hash so generation rejects stale geometry explicitly.
    checkpoint(cancel, progress, 1., "Edited layout reimport verified")
    return candidate
