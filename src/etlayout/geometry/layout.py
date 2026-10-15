# SPDX-License-Identifier: MPL-2.0
"""KLayout-backed GDSII/OASIS hierarchy access without eager flattening."""
from __future__ import annotations

import math
import warnings
from collections import OrderedDict
from pathlib import Path
from typing import Iterator

import klayout.db as kdb
from shapely.geometry import GeometryCollection, Polygon
from shapely.ops import unary_union
from shapely.validation import explain_validity

from etlayout.models import Diagnostic, SimulationError, checkpoint


class LayoutDocument:
    """Read a layout once and flatten only the selected hierarchy/ROI on demand.

    ``instance_path`` is a sequence of placement indices: each level enumerates
    each instance and, inside it, each array placement in KLayout order. The
    coordinates of a selected nested instance remain in its root cell frame.
    Text never contributes conducting area. KLayout polygonizes path widths,
    end caps and extension lengths before geometry is transferred to Shapely.
    """

    def __init__(self, path: str | Path, *, progress=None, cancel=None):
        checkpoint(cancel, progress, 0., "Reading layout file")
        self.path = str(Path(path).resolve())
        if Path(path).suffix.lower() not in {".gds", ".gdsii", ".oas", ".oasis"}:
            raise SimulationError("Choose a GDSII (.gds/.gdsii) or OASIS (.oas/.oasis) layout")
        self._layout = kdb.Layout()
        try:
            self._layout.read(self.path)
        except Exception as exc:
            raise SimulationError(f"Cannot read layout {path}: {exc}") from exc
        self.dbu_um = float(self._layout.dbu)
        if not math.isfinite(self.dbu_um) or self.dbu_um <= 0:
            raise SimulationError("The layout has an invalid database unit")
        self.cells = sorted(c.name for c in self._layout.each_cell())
        self.top_cells = sorted(c.name for c in self._layout.top_cells())
        self.layers = []
        for index in self._layout.layer_indexes():
            info = self._layout.get_info(index)
            self.layers.append(dict(key=f"{info.layer}/{info.datatype}", layer=info.layer,
                                    datatype=info.datatype, name=info.name or "", index=index))
        self.layers.sort(key=lambda x: (x["layer"], x["datatype"]))
        self.bounds = self.cell_bounds() if self.top_cells else [0., 0., 0., 0.]
        self.diagnostics = []
        self._extraction_cache = OrderedDict()
        self._shape_bounds_cache = {}
        checkpoint(cancel, progress, 1., "Layout metadata ready")

    def _cell(self, name: str = ""):
        if not name:
            if not self.top_cells:
                raise SimulationError("The layout contains no top cells")
            name = self.top_cells[0]
        result = self._layout.cell(name)
        if result is None:
            raise SimulationError(f"Layout cell {name!r} does not exist")
        return result

    def _placements(self, cell) -> Iterator[tuple]:
        for inst in cell.each_inst():
            for transform in inst.cell_inst.each_cplx_trans():
                yield self._layout.cell(inst.cell_index), transform

    def _selection(self, cell: str, instance_path: list[int] | None):
        selected, transform = self._cell(cell), kdb.ICplxTrans()
        for index in instance_path or []:
            placement = next(self._placement_slice(selected, index, 1), None) if index >= 0 else None
            if placement is None:
                raise SimulationError(f"Invalid instance path {instance_path}: placement {index} missing")
            selected, child_transform = placement
            transform = transform * child_transform
        return selected, transform

    def _placement_slice(self, cell, offset, count):
        """Skip complete arrays and address their elements without expansion."""
        for instance in cell.each_inst():
            array = instance.cell_inst
            size = array.size()
            if offset >= size:
                offset -= size
                continue
            stop = min(size, offset + count)
            for index in range(offset, stop):
                na = max(1, array.na)
                a, b = index % na, index // na
                dx = a * array.a.x + b * array.b.x
                dy = a * array.a.y + b * array.b.y
                transform = kdb.ICplxTrans(kdb.Trans(dx, dy)) * array.cplx_trans
                yield self._layout.cell(instance.cell_index), transform
            count -= stop - offset
            if count <= 0:
                return
            offset = 0

    def instances_page(self, cell: str = "", instance_path: list[int] | None = None,
                       offset: int = 0, limit: int = 200) -> dict:
        """Return an exact page of immediate children in root-cell coordinates.

        ``total`` counts placements, including array elements. Every returned
        path can be passed directly to this method or :meth:`extract`.
        """
        if offset < 0 or limit < 1:
            raise SimulationError("Hierarchy page requires a nonnegative offset and positive limit")
        parent, transform = self._selection(cell, instance_path)
        total = sum(instance.cell_inst.size() for instance in parent.each_inst())
        items = []
        for index, (child, child_transform) in enumerate(self._placement_slice(parent, offset, limit), offset):
            path = [*(instance_path or []), index]
            placed = transform * child_transform
            items.append(dict(path=path, cell=child.name, bounds=self._bounds(child, placed),
                              label=f"{child.name} [{'/'.join(map(str, path))}]", transform=str(placed)))
        return dict(items=items, total=total, offset=offset, limit=limit,
                    has_more=offset + len(items) < total)

    def geometry_bounds(self, cell: str = "", instance_path: list[int] | None = None,
                        *, cancel=None) -> list[float] | None:
        """Hierarchical polygon/path extent, excluding text, without flattening."""
        key = (cell, tuple(instance_path or []))
        if key not in self._shape_bounds_cache:
            selected, transform = self._selection(cell, instance_path)
            store = kdb.DeepShapeStore()
            bounds = kdb.Box()
            for layer in self.layers:
                checkpoint(cancel)
                region = kdb.Region(selected.begin_shapes_rec(layer["index"]), store)
                if transform != kdb.ICplxTrans():
                    region = region.transformed(transform)
                bounds += region.bbox()
            scale = self.dbu_um * 1e-6
            self._shape_bounds_cache[key] = None if bounds.empty() else [
                bounds.left * scale, bounds.bottom * scale, bounds.right * scale, bounds.top * scale]
        return self._shape_bounds_cache[key]

    def _bounds(self, cell, transform) -> list[float]:
        bbox = cell.bbox().transformed(transform)
        scale = self.dbu_um * 1e-6
        return [bbox.left * scale, bbox.bottom * scale, bbox.right * scale, bbox.top * scale]

    def cell_bounds(self, cell: str = "", instance_path: list[int] | None = None) -> list[float]:
        selected, transform = self._selection(cell, instance_path)
        return self._bounds(selected, transform)

    def instances(self, cell: str = "", recursive: bool = False,
                  max_instances: int = 10000) -> list[dict]:
        """Legacy bounded enumeration; use instances_page for complete browsing."""
        if max_instances < 1:
            raise SimulationError("Instance enumeration limit must be positive")
        root = self._cell(cell)
        result = []
        truncated = False

        def visit(parent, transform, path):
            nonlocal truncated
            for index, (child, child_transform) in enumerate(self._placements(parent)):
                if len(result) >= max_instances:
                    truncated = True
                    return
                placed = transform * child_transform
                child_path = path + [index]
                result.append(dict(path=child_path, cell=child.name, bounds=self._bounds(child, placed),
                                   label=f"{child.name} [{'/'.join(map(str, child_path))}]",
                                   transform=str(placed)))
                if recursive:
                    visit(child, placed, child_path)

        visit(root, kdb.ICplxTrans(), [])
        if truncated:
            warnings.warn(f"Hierarchy enumeration truncated at {max_instances:,} placements; use instances_page for complete access.",
                          RuntimeWarning, stacklevel=2)
        return result

    def extract(self, cell: str = "", instance_path: list[int] | None = None,
                roi=None, *, clip: bool = True, mask_keys=None, progress=None, cancel=None) -> dict:
        """Return merged polygons by layer/datatype; output coordinates are SI.

        A bbox search prunes recursive shape traversal when an ROI is supplied.
        The final exact polygon intersection handles nonrectangular ROIs.
        ``clip=False`` keeps complete candidate polygons for boundary-cut checks.
        ``mask_keys`` limits extraction to selected masks. An eight-query LRU
        caches immutable geometry; returned dictionaries can be edited safely.
        """
        checkpoint(cancel, progress, 0., "Extracting selected layout geometry")
        selected_layers = [layer for layer in self.layers if mask_keys is None or layer["key"] in mask_keys]
        key = (cell, tuple(instance_path or []), roi.wkb if roi is not None else None,
               clip, tuple(layer["key"] for layer in selected_layers))
        if key in self._extraction_cache:
            result, diagnostics = self._extraction_cache.pop(key)
            self._extraction_cache[key] = (result, diagnostics)
            self.diagnostics = list(diagnostics)
            checkpoint(cancel, progress, 1., "Reusing selected layout geometry")
            return dict(result)
        selected, transform = self._selection(cell, instance_path)
        scale = self.dbu_um * 1e-6
        result = {}
        self.diagnostics = []

        def to_polygon(polygon):
            exterior = [(p.x * scale, p.y * scale) for p in polygon.each_point_hull()]
            holes = [[(p.x * scale, p.y * scale) for p in polygon.each_point_hole(i)]
                     for i in range(polygon.holes())]
            return Polygon(exterior, holes) if len(exterior) >= 3 else GeometryCollection()

        for layer_number, layer in enumerate(selected_layers):
            checkpoint(cancel, progress, layer_number / max(len(selected_layers), 1),
                       f"Extracting mask {layer['key']}")
            if roi is not None and roi.is_empty:
                result[layer["key"]] = GeometryCollection()
                continue
            if roi is not None:
                x0, y0, x1, y1 = roi.bounds
                search = kdb.Box(math.floor(x0 / scale), math.floor(y0 / scale),
                                 math.ceil(x1 / scale), math.ceil(y1 / scale))
                search = search.transformed(transform.inverted())
                iterator = selected.begin_shapes_rec_overlapping(layer["index"], search)
            else:
                iterator = selected.begin_shapes_rec(layer["index"])
            region = kdb.Region(iterator)
            if transform != kdb.ICplxTrans():
                region.transform(transform)
            for index, original in enumerate(region.each()):
                if index % 128 == 0:
                    checkpoint(cancel)
                candidate = to_polygon(original)
                if candidate.is_empty or candidate.area <= 0:
                    self.diagnostics.append(Diagnostic("warning", "Zero-area layout polygon was discarded; check the source mask.", layer["key"], list(candidate.bounds) if not candidate.is_empty else None))
                elif not candidate.is_valid:
                    # GDS stores polygon holes as cutlines, which are nonsimple
                    # rings in OGC geometry but legal layout geometry. KLayout
                    # normalizes these exactly in integer database coordinates.
                    self.diagnostics.append(Diagnostic("warning", f"KLayout normalized a nonsimple polygon/cutline on mask {layer['key']}: {explain_validity(candidate)}. Inspect the resulting geometry; cutlines encoding holes are expected in GDS.", layer["key"], list(candidate.bounds)))
            region.merge()
            polygons = []
            for index, polygon in enumerate(region.each()):
                if index % 128 == 0:
                    checkpoint(cancel)
                candidate = to_polygon(polygon)
                if not candidate.is_empty:
                    if not candidate.is_valid:
                        raise SimulationError(f"Mask {layer['key']} contains invalid geometry after KLayout normalization: {explain_validity(candidate)}")
                    polygons.append(candidate)
            geometry = unary_union(polygons) if polygons else GeometryCollection()
            if roi is not None and clip:
                geometry = geometry.intersection(roi)
            result[layer["key"]] = geometry
        self._extraction_cache[key] = (dict(result), list(self.diagnostics))
        while len(self._extraction_cache) > 8:
            self._extraction_cache.popitem(last=False)
        checkpoint(cancel, progress, 1., "Selected layout geometry ready")
        return result
