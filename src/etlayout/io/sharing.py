# SPDX-License-Identifier: MPL-2.0
"""Explicit figure/data sharing boundaries and confidential complete archives.

Sharing starts from selected FE support, never a cropped source-project archive.
Partial triangles are clipped geometrically and P1 data are interpolated locally.
No source layout, configuration, notes, history or machine paths enter sharing
artifacts. Source hashes describe provenance, not a reproducible experiment.
"""
from __future__ import annotations

import errno
import math
import os
import re
import shutil
import tempfile
from dataclasses import asdict, dataclass
from dataclasses import field as dataclass_field
from pathlib import Path

import numpy as np
from shapely import constrained_delaunay_triangles, get_parts
from shapely.geometry import Polygon, box

from etlayout.io.project import _json_dump, save_project
from etlayout.io.reporting import (
    LIMITATIONS,
    csv_writer,
    html_document,
    html_image,
    html_table,
    html_text,
    safe_filename,
)
from etlayout.models import PlanarMesh, SimulationResult
from etlayout.simulation.analysis import FIELD_UNITS
from etlayout.visualization.palettes import get_colormap
from etlayout.visualization.support import display_limits, field_support
from etlayout.visualization.units import display_unit

MODES = {"figure": "Share figure or report", "data": "Share selected numerical data", "archive": "Archive complete experiment"}
MODE_FORMATS = {"figure": {"png", "pdf", "svg", "html"}, "data": {"csv", "npz", "vtu"}, "archive": {"etl"}}
PAGE_INCHES = {"A4": (210 / 25.4, 297 / 25.4), "A3": (297 / 25.4, 420 / 25.4), "Letter": (8.5, 11.)}


@dataclass(frozen=True)
class FigureOptions:
    width_inches: float = 7.1
    height_inches: float = 4.8
    dpi: int = 300
    page_size: str = "A4"
    orientation: str = "landscape"
    title: str = ""
    annotations: str = ""
    colorbar: bool = True
    legend: bool = True
    smooth: bool = True

    def validate(self) -> None:
        if any(not math.isfinite(value) or not 2 <= value <= 30 for value in (self.width_inches, self.height_inches)):
            raise ValueError("Figure width and height must be between 2 and 30 inches")
        if isinstance(self.dpi, bool) or int(self.dpi) != self.dpi or not 72 <= self.dpi <= 1200:
            raise ValueError("PNG DPI must be a whole number between 72 and 1200")
        if self.width_inches * self.height_inches * self.dpi ** 2 > 64_000_000:
            raise ValueError("Figure exceeds the 64-million-pixel rendering budget; reduce dimensions or DPI")
        if self.page_size not in {*PAGE_INCHES, "Custom"} or self.orientation not in {"portrait", "landscape"}:
            raise ValueError("Choose A4, A3, Letter or Custom PDF page and portrait/landscape orientation")
        if not isinstance(self.title, str) or not isinstance(self.annotations, str):
            raise ValueError("Figure title and annotations must be text")
        if len(self.title) > 240 or len(self.annotations) > 1000:
            raise ValueError("Figure title or annotation exceeds its readable export budget")

    def size_inches(self, suffix: str) -> tuple[float, float]:
        if suffix == ".pdf":
            size = PAGE_INCHES.get(self.page_size, (self.width_inches, self.height_inches))
            return tuple(sorted(size, reverse=self.orientation == "landscape"))
        return self.width_inches, self.height_inches


@dataclass(frozen=True)
class ExportRequest:
    mode: str = "figure"
    layers: tuple[str, ...] = ()
    fields: tuple[str, ...] = ()
    region: str = ""
    roi: tuple[float, float, float, float] | None = None  # SI rectangle, exact geometric clip
    time_s: float | None = None
    formats: tuple[str, ...] = ("png",)
    colormap: str = "teal"
    limits: tuple[float, float] | None = None  # SI, independent of data
    logarithmic: bool = False
    figure: FigureOptions = dataclass_field(default_factory=FigureOptions)
    stem: str = "selection"
    overwrite: bool = False

    def validate(self) -> None:
        if self.mode not in MODES:
            raise ValueError("Export mode must be figure, data or archive")
        if not self.formats or len(set(self.formats)) != len(self.formats) or not set(self.formats) <= MODE_FORMATS[self.mode]:
            raise ValueError(f"Formats for {self.mode} must be selected from {sorted(MODE_FORMATS[self.mode])}")
        if self.mode != "archive" and (not self.layers or not self.fields):
            raise ValueError("Sharing requires explicitly selected layers and field quantities")
        if self.mode == "archive" and (self.roi is not None or self.region or self.time_s is not None or self.layers or self.fields):
            raise ValueError("Complete archives cannot be presented as cropped or selected exports")
        if self.time_s is not None and (isinstance(self.time_s, bool) or not isinstance(self.time_s, (int, float)) or not math.isfinite(self.time_s)):
            raise ValueError("Snapshot time must be finite seconds")
        if self.roi is not None:
            if len(self.roi) != 4 or not np.isfinite(self.roi).all() or self.roi[2] <= self.roi[0] or self.roi[3] <= self.roi[1]:
                raise ValueError("ROI needs finite xmin, ymin, xmax, ymax in meters with positive area")
        if not self.stem or Path(self.stem).name != self.stem or any(char in self.stem for char in "/\\:"):
            raise ValueError("Export filename must be a plain basename, without paths")
        self.figure.validate()
        get_colormap(self.colormap)


def _frame(result: SimulationResult, requested: float | None):
    if requested is None:
        return result
    if result.transient is not None:
        index = int(np.argmin(np.abs(result.transient.times - requested)))
        return result.transient.snapshots[index]
    time = result.metrics.get("time_s")
    if time is None or not math.isclose(float(time), requested, rel_tol=1e-12, abs_tol=0.):
        raise ValueError("This result has no requested retained snapshot")
    return result


def _public_label(value, fallback):
    # A source ID is normally a mask name. Path-like IDs receive an export-local
    # alias rather than disclosing a workstation path through labels/captions.
    return fallback if any(char in str(value) for char in "/\\") else str(value)


def _hash(value, *, required=False):
    if isinstance(value, str) and re.fullmatch(r"[0-9a-fA-F]{64}", value):
        return value
    if required or value:
        raise ValueError("Sharing requires valid recorded SHA-256 source identities")
    return ""


def _timing(result, source):
    output = {}
    if "time_s" in result.metrics:
        time = float(result.metrics["time_s"])
        if not math.isfinite(time):
            raise ValueError("Sharing snapshot time must be finite seconds")
        output["time_s"] = time
    if source.transient is not None:
        output["transient_completed"] = bool(source.transient.completed)
    visualization = result.configuration.get("visualization", {})
    if visualization.get("transient_completed") is False:
        output["transient_completed"] = False
    if visualization.get("kind") == "difference":
        output.update(kind="difference", operation="current_minus_comparison", quantity="scalar difference")
        for name in ("current_time_s", "comparison_time_s"):
            if name in visualization:
                value = visualization[name]
                if value is not None and (not isinstance(value, (int, float)) or not math.isfinite(value)):
                    raise ValueError("Sharing comparison time must be finite seconds or null")
                output[name] = value
        output["sources"] = [{key: _hash(item[key]) for key in ("configuration_hash", "physics_hash") if key in item}
                             for item in visualization.get("sources", [])]
    return output


def _clip(result, layer, quantity, roi):
    support = field_support(result, layer, quantity)
    ids = support.indices
    triangles = result.mesh.points[result.mesh.triangles[ids]]
    raw = np.asarray(result.fields[layer].get(quantity, support.data))
    local = support.element_data[ids] if support.nodal else raw[ids]
    if roi is None:
        return triangles.copy(), local.copy(), support.nodal
    clipping = box(*roi)
    geometry, values = [], []
    for coordinates, value in zip(triangles, local, strict=True):
        if (coordinates[:, 0].max() <= roi[0] or coordinates[:, 0].min() >= roi[2] or
                coordinates[:, 1].max() <= roi[1] or coordinates[:, 1].min() >= roi[3]):
            continue
        polygon = Polygon(coordinates).intersection(clipping)
        if polygon.is_empty or polygon.area == 0:
            continue
        for triangle in get_parts(constrained_delaunay_triangles(polygon)):
            xy = np.asarray(triangle.exterior.coords)[:3]
            if triangle.area == 0:
                continue
            geometry.append(xy)
            if support.nodal:
                # Affine interpolation in this original element preserves P1 support
                # and independent values at coincident component-local vertices.
                transform = np.column_stack((coordinates[1] - coordinates[0], coordinates[2] - coordinates[0]))
                weights = np.linalg.solve(transform, (xy - coordinates[0]).T).T
                values.append((1 - weights.sum(axis=1)) * value[0] + weights[:, 0] * value[1] + weights[:, 1] * value[2])
            else:
                values.append(value)
    shape = (0, 3) if support.nodal else (0,) + raw.shape[1:]
    return np.asarray(geometry).reshape(-1, 3, 2), np.asarray(values).reshape((-1, *np.shape(values[0]))) if values else np.empty(shape), support.nodal


def _datasets(source, request):
    result = _frame(source, request.time_s)
    datasets = []
    for layer in dict.fromkeys(request.layers):
        if layer not in result.fields:
            raise ValueError("Selected export layer is not available in the result")
        for quantity in dict.fromkeys(request.fields):
            if quantity not in FIELD_UNITS:
                raise ValueError("Sharing requires a supported physical quantity with declared units")
            stored = "temperature" if quantity == "temperature_rise" else quantity
            if stored not in result.fields[layer]:
                raise ValueError("Selected quantity is not available on the selected layer")
            support = field_support(result, layer, quantity, region=request.region)
            # Region selection is enforced before geometric clipping. A narrow copy
            # references the immutable source mesh but only the approved element IDs.
            narrowed = SimulationResult(result.mesh, fields=result.fields, element_fields=result.element_fields,
                configuration=result.configuration, metrics=result.metrics)
            selected_mesh = PlanarMesh(result.mesh.points, result.mesh.triangles, {"layer:" + layer: support.indices})
            narrowed.mesh = selected_mesh
            triangles, values, nodal = _clip(narrowed, layer, quantity, request.roi)
            if not len(triangles):
                raise ValueError(f"No finite selected support for {layer} / {quantity}")
            if request.logarithmic:
                scalars = values if nodal or values.ndim == 1 else np.linalg.norm(values.reshape(len(values), -1), axis=1)
                eligible = np.all(scalars > 0, axis=1) if nodal else scalars > 0
                # Logarithms affect figure eligibility only, never the data dataset.
                if request.mode == "figure" and not eligible.any():
                    raise ValueError("No positive selected support for logarithmic figure")
            layer_config = next((entry for entry in result.configuration.get("layers", []) if entry.get("id") == layer), {})
            datasets.append(dict(layer=layer, public_layer=_public_label(layer, f"Layer {len(datasets) + 1}"),
                                 field=quantity, triangles=triangles, values=values, nodal=nodal,
                                 z=float(layer_config.get("z", 0.))))
            if quantity == "temperature_rise":
                reference = float(result.metrics.get("thermal_reference", result.configuration.get("solver", {}).get("initial_temperature", 300.)))
                datasets[-1]["reference_temperature_K"] = reference
                datasets[-1]["resolution_scale"] = max(abs(reference), float(np.max(np.abs(values + reference))))
    return result, datasets


def _plan(result, request, project=None, history=None):
    request.validate()
    stem = safe_filename(request.stem)
    if request.mode == "archive":
        if project is None:
            raise ValueError("Complete experiment export requires its physical project")
        return {"schema_version": 1, "mode": "archive", "scope": MODES["archive"], "confidential": True,
                "disclosure": "Includes original layout, complete physical configuration, material snapshots, fields, meshes, solver metadata, annotations and selected run history. A cropped view does not crop this archive.",
                "files": [stem + ".etl", stem + ".manifest.json"], "history_runs": len(history or [])}, []
    selected, datasets = _datasets(result, request)
    items, files = [], []
    for index, data in enumerate(datasets):
        prefix = stem + (f"-{index + 1:03d}" if len(datasets) > 1 else "")
        names = {format: prefix + "." + format for format in request.formats}
        files.extend(names.values())
        unit = FIELD_UNITS.get(data["field"], "")
        point_count = len(data["triangles"]) * 3
        record = {"layer": data["public_layer"], "field": data["field"], "unit": unit,
                  "display_unit": display_unit(unit), "location": "element_local_node" if data["nodal"] else "element",
                  "triangle_count": len(data["triangles"]), "point_count": point_count,
                  "bounds_m": [*data["triangles"].min(axis=(0, 1)), *data["triangles"].max(axis=(0, 1))],
                  "z_m": data["z"], "files": names,
                  "component_shape": [] if data["nodal"] else list(data["values"].shape[1:]),
                  "component_order": "row-major; vector axes x,y and tensor indices in matrix order",
                  "area_m2": float(np.abs((data["triangles"][:, 1, 0] - data["triangles"][:, 0, 0]) *
                      (data["triangles"][:, 2, 1] - data["triangles"][:, 0, 1]) -
                      (data["triangles"][:, 1, 1] - data["triangles"][:, 0, 1]) *
                      (data["triangles"][:, 2, 0] - data["triangles"][:, 0, 0])).sum() / 2)}
        if "reference_temperature_K" in data:
            record["reference_temperature_K"] = data["reference_temperature_K"]
        if request.mode == "figure":
            figure_result = _figure_result(selected, data, _timing(selected, result))
            plotted = field_support(figure_result, data["public_layer"], data["field"], logarithmic=request.logarithmic)
            record["effective_limits_SI"] = display_limits(plotted.limits, limits=request.limits,
                logarithmic=request.logarithmic, colormap=request.colormap, provenance=_timing(selected, result),
                resolution_scale=data.get("resolution_scale", 0.))
        items.append(record)
    files.append(stem + ".manifest.json")
    from etlayout.provenance import shared_result_identity
    manifest = {"schema_version": 1, "implementation": shared_result_identity(selected), "mode": request.mode, "scope": MODES[request.mode], "contains_complete_experiment": False, "may_reveal_selected_design": True,
                "configuration_hash": _hash(selected.configuration_hash, required=True), "physics_hash": _hash(selected.physics_hash),
                "converged": bool(selected.converged), "timing": _timing(selected, result),
                "requested_time_s": request.time_s, "region": _public_label(request.region, "Selected region"), "roi_m": request.roi,
                "coordinate_unit": "m", "value_convention": "Unclipped SI quantities; element-local P1 interpolation inside exact selected support",
                "excluded": ["source layout", "complete physical configuration", "materials/process database", "unselected fields and geometry", "notes", "run history", "absolute local paths", "machine metadata"],
                "disclosure": "Selected field geometry can reveal design information inside its support. This is a sharing artifact, not a reproducible experiment archive.",
                "limitations": LIMITATIONS, "mesh_verification": "Not established by export; see the source verification record",
                "datasets": items, "files": files}
    if request.mode == "figure":
        manifest["display"] = {"colormap": request.colormap, "limits_SI": request.limits,
            "logarithmic": request.logarithmic, "manual_clipping": request.limits is not None,
            "figure": asdict(request.figure)}
    return manifest, datasets


def preview_export(result, request: ExportRequest, *, project=None, history=None):
    """Return the exact enforced scope/files without creating output artifacts."""
    return _plan(result, request, project, history)[0]


def _figure_result(source, data, timing):
    count = len(data["triangles"])
    layer = data["public_layer"]
    mesh = PlanarMesh(data["triangles"].reshape(-1, 2), np.arange(count * 3).reshape(-1, 3),
                      {"layer:" + layer: np.arange(count)})
    quantity = "temperature" if data["field"] == "temperature_rise" else data["field"]
    values = data["values"]
    fields = {layer: {quantity: values.ravel() if data["nodal"] else values}}
    local = {layer: {quantity: values}} if data["nodal"] else {}
    metrics = {key: value for key, value in timing.items() if key == "time_s"}
    if data["field"] == "temperature_rise":
        metrics["thermal_reference"] = 0.
    return SimulationResult(mesh, fields=fields, element_fields=local, metrics=metrics, converged=source.converged,
        configuration={"visualization": timing}, configuration_hash=source.configuration_hash, physics_hash=source.physics_hash)


def _write_data(data, names, directory):
    points = np.column_stack((data["triangles"].reshape(-1, 2), np.full(len(data["triangles"]) * 3, data["z"])))
    triangles = np.arange(len(points)).reshape(-1, 3)
    values = data["values"].ravel() if data["nodal"] else data["values"]
    if "npz" in names:
        np.savez_compressed(directory / names["npz"], points_m=points, triangles=triangles, values=values)
    if "vtu" in names:
        import meshio
        meshio.write_points_cells(directory / names["vtu"], points, [("triangle", triangles)],
            point_data={data["field"]: values} if data["nodal"] else {},
            cell_data={} if data["nodal"] else {data["field"]: [values.reshape(len(values), -1) if values.ndim > 2 else values]})
    if "csv" in names:
        with (directory / names["csv"]).open("w", newline="", encoding="utf-8-sig") as stream:
            writer = csv_writer(stream)
            components = values.reshape(len(values), -1)
            headings = [data["field"]] if components.shape[1] == 1 else [f"{data['field']}_{index}" for index in range(components.shape[1])]
            geometry_headers = [] if data["nodal"] else ["area_m2", "x0_m", "y0_m", "x1_m", "y1_m", "x2_m", "y2_m"]
            writer.writerow(["element", "local_vertex", "x_m", "y_m", "z_m", *geometry_headers,
                             *[f"{name} [{FIELD_UNITS.get(data['field'], '')}]" for name in headings]])
            if data["nodal"]:
                for index, (point, value) in enumerate(zip(points, components, strict=True)):
                    writer.writerow([index // 3, index % 3, *point, *value])
            else:
                for index, (point, value) in enumerate(zip(points.reshape(-1, 3, 3).mean(axis=1), components, strict=True)):
                    triangle = data["triangles"][index]
                    first, second = triangle[1] - triangle[0], triangle[2] - triangle[0]
                    area = abs(first[0] * second[1] - first[1] * second[0]) / 2
                    writer.writerow([index, "", *point, area, *triangle.ravel(), *value])


def _share_html(manifest, pngs, options):
    title = options.title or "Selected electrothermal field results"
    status = "Converged source" if manifest["converged"] else "NOT CONVERGED source — diagnostic output only"
    if manifest["timing"].get("transient_completed") is False:
        status += " · snapshot from INCOMPLETE transient run"
    body = f"<h1>{html_text(title)}</h1><p>{html_text(status)}</p><p>{html_text(manifest['disclosure'])}</p>"
    body += "".join(html_image(png, caption) for png, caption in pngs)
    body += "<h2>Selected quantities</h2>" + html_table(
        [[item["layer"], item["field"], item["unit"], item["location"], item["triangle_count"]] for item in manifest["datasets"]],
        ["Layer", "Quantity", "SI unit", "Support", "Triangles"])
    body += "<h2>Annotations explicitly included by exporter</h2><p>" + html_text(options.annotations).replace("\n", "<br>") + "</p>"
    body += "<h2>Model limitations</h2><p>" + html_text(LIMITATIONS) + "</p><details><summary>Sharing manifest</summary><pre>" + html_text(_json_dump(manifest)) + "</pre></details>"
    return html_document(title, body)


class ExportRecoveryError(OSError):
    """Publication rollback failed; preserve sibling staging for manual recovery."""


def _publish_artifacts(root, staging, names, overwrite):
    backups, installed = {}, []
    backup_root = staging / "previous"
    backup_root.mkdir()
    if overwrite:
        for name in names:
            target = root / name
            if target.is_symlink():
                raise ValueError("Export destination became a file symlink")
            if target.exists():
                backup = backup_root / name
                try:
                    os.link(target, backup, follow_symlinks=False)
                except (OSError, NotImplementedError):
                    shutil.copy2(target, backup, follow_symlinks=False)
                backups[target] = backup
    manifest = root / names[-1]
    withdrawn_manifest = False
    try:
        # Never leave an old manifest describing a partly replaced new dataset.
        if manifest in backups:
            manifest.unlink()
            withdrawn_manifest = True
        for name in names:
            target = root / name
            if target.is_symlink():
                raise ValueError("Export destination became a file symlink")
            if overwrite:
                os.replace(staging / name, target)
                installed.append(target)
            else:
                try:
                    os.link(staging / name, target)
                    installed.append(target)
                except OSError as error:
                    if error.errno not in {errno.ENOSYS, errno.EOPNOTSUPP, errno.EPERM} and getattr(error, "winerror", None) not in {1, 50}:
                        raise
                    with target.open("xb") as output:
                        installed.append(target)
                        with (staging / name).open("rb") as source:
                            shutil.copyfileobj(source, output)
    except (OSError, ValueError) as error:
        failed = []
        for target in reversed(installed):
            try:
                if target in backups:
                    os.replace(backups[target], target)
                else:
                    target.unlink(missing_ok=True)
            except OSError:
                failed.append(target.name)
        if withdrawn_manifest and manifest not in installed:
            try:
                os.replace(backups[manifest], manifest)
            except OSError:
                failed.append(manifest.name)
        if failed:
            raise ExportRecoveryError("Export rollback could not restore every artifact; recovery files remain in " + staging.name) from error
        raise


def execute_export(result, directory: str | Path, request: ExportRequest, *, project=None, history=None,
                   progress=None, cancel=None):
    """Write only planned files, with exclusive creation and rollback on IO errors.

    Whole-directory replacement is not atomic. The interpretation manifest is
    withdrawn while replacing an old bundle and published last; ordinary failures
    restore previously valid output. Recovery files survive rollback failure.
    """
    def checkpoint(fraction, message):
        if cancel is not None and cancel():
            from etlayout.io.saving import SaveCancelled
            raise SaveCancelled("Archive export cancelled; previously published artifacts were preserved")
        if progress is not None:
            progress(fraction, message)
        if cancel is not None and cancel():
            from etlayout.io.saving import SaveCancelled
            raise SaveCancelled("Archive export cancelled; previously published artifacts were preserved")

    checkpoint(0., "Preparing reviewed export")
    manifest, datasets = _plan(result, request, project, history)
    root = Path(directory)
    root.mkdir(parents=True, exist_ok=True)
    root = root.resolve()
    paths = [root / name for name in manifest["files"]]
    existing_names = {path.name.casefold(): path.name for path in root.iterdir()}
    if any(name.casefold() in existing_names and existing_names[name.casefold()] != name for name in manifest["files"]):
        raise FileExistsError("Export artifact collides with an existing filename under Windows case-insensitive rules; choose another basename")
    if any(path.parent != root or path.is_symlink() for path in paths):
        raise ValueError("Export output must remain inside the requested directory without file symlinks")
    if any(path.exists() for path in paths) and not request.overwrite:
        raise FileExistsError("Export artifacts already exist; choose another filename or explicitly allow replacement")
    if any(path.exists() and not path.is_file() for path in paths):
        raise ValueError("An export artifact collides with a directory")
    staging = Path(tempfile.mkdtemp(prefix=".etlayout-export-", dir=root))
    preserve_staging = False
    try:
        if request.mode == "archive":
            save_project(project, staging / manifest["files"][0], result, history=history,
                         archive_format=getattr(project, "_archive_format", 2), cancel=cancel,
                         progress=lambda fraction, message: checkpoint(.05 + .85 * fraction, message))
        elif request.mode == "data":
            for data, entry in zip(datasets, manifest["datasets"], strict=True):
                _write_data(data, entry["files"], staging)
        else:
            from etlayout.io.export import export_field_plot
            selected = _frame(result, request.time_s)
            for data, entry in zip(datasets, manifest["datasets"], strict=True):
                figure_result = _figure_result(selected, data, {**manifest["timing"], "smooth": request.figure.smooth})
                pngs = []
                options = dict(colormap=request.colormap, limits=request.limits,
                    provenance={**manifest["timing"], "smooth": request.figure.smooth}, figure_options=request.figure,
                    resolution_scale=data.get("resolution_scale", 0.))
                for format, name in entry["files"].items():
                    if format != "html":
                        export_field_plot(figure_result, data["public_layer"], data["field"], staging / name, request.logarithmic, **options)
                if "html" in entry["files"]:
                    name = entry["files"].get("png", "embedded.png")
                    if "png" not in entry["files"]:
                        export_field_plot(figure_result, data["public_layer"], data["field"], staging / name, request.logarithmic, **options)
                    pngs.append(((staging / name).read_bytes(), entry["layer"] + " · " + data["field"] + " (" + entry["display_unit"] + ")"))
                    (staging / entry["files"]["html"]).write_text(_share_html(manifest, pngs, request.figure), encoding="utf-8")
        (staging / manifest["files"][-1]).write_text(_json_dump(manifest), encoding="utf-8")
        for path in paths:
            if path.is_symlink() or path.exists() and not request.overwrite:
                raise FileExistsError("Export destination changed during rendering; no replacement authorized")
        checkpoint(.95, "Publishing reviewed export artifacts")
        _publish_artifacts(root, staging, manifest["files"], request.overwrite)
        # Publication is the transaction's commit point. A later cancellation
        # cannot truthfully turn an already published bundle into a cancellation.
        if progress is not None:
            progress(1., "Reviewed export complete")
    except ExportRecoveryError:
        preserve_staging = True
        raise
    finally:
        if not preserve_staging:
            shutil.rmtree(staging)
    return manifest
