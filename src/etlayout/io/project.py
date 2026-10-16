# SPDX-License-Identifier: MPL-2.0
"""Portable .etl ZIP archive: JSON metadata, embedded layout, non-pickled NPZ arrays."""
from __future__ import annotations

import hashlib
import io
import json
import math
import os
import tempfile
import types
import zipfile
from copy import copy, deepcopy
from dataclasses import asdict, fields, is_dataclass, replace
from functools import cache
from pathlib import Path, PurePosixPath
from typing import Any, Union, get_args, get_origin, get_type_hints

import numpy as np

from etlayout import __version__
from etlayout.io.identity import document_id
from etlayout.io.resources import (
    ExternalResourcePolicy,
    ProjectInspection,
    inspect_project,
    require_resources,
    resolve_layout,
)
from etlayout.models import (
    Diagnostic,
    PlanarMesh,
    PowerSettings,
    Project,
    SimulationError,
    SimulationResult,
)

MAX_ARCHIVE_BYTES = 2_000_000_000
MAX_METADATA_BYTES = 20_000_000
MAX_ARCHIVE_ENTRIES = 100_000
MAX_ARCHIVE_DEPTH = 3


def _decode(annotation, value):
    if annotation is Any:
        return value
    origin, args = get_origin(annotation), get_args(annotation)
    if origin in (Union, types.UnionType):
        if value is None and type(None) in args:
            return None
        for option in args:
            if option is not type(None):
                try:
                    return _decode(option, value)
                except SimulationError:
                    pass
        raise SimulationError(f"Value does not match {annotation}")
    if value is None:
        raise SimulationError(f"Null is not allowed for {annotation}")
    if is_dataclass(annotation):
        return from_dict(annotation, value)
    if origin is list:
        if not isinstance(value, list):
            raise SimulationError("Expected a JSON array")
        return [_decode(args[0], item) for item in value]
    if origin is dict:
        if not isinstance(value, dict):
            raise SimulationError("Expected a JSON object")
        return {_decode(args[0], k): _decode(args[1], v) for k, v in value.items()}
    if annotation is bool:
        if type(value) is not bool:
            raise SimulationError("Expected true or false, not a string or number")
    elif annotation is int:
        if type(value) is not int:
            raise SimulationError("Expected an integer, not a Boolean or fractional value")
    elif annotation is float:
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise SimulationError("Expected a finite number")
        try:
            numeric_value = float(value)
        except (OverflowError, ValueError) as exc:
            raise SimulationError("Expected a finite number") from exc
        if not np.isfinite(numeric_value):
            raise SimulationError("Expected a finite number")
    elif annotation is str:
        if not isinstance(value, str):
            raise SimulationError("Expected a string")
    return value


@cache
def _model_schema(cls):
    """Reuse class metadata, never decoded project values or validation results."""
    return get_type_hints(cls), frozenset(f.name for f in fields(cls))


def from_dict(cls, data: dict):
    if not isinstance(data, dict):
        raise SimulationError(f"Expected an object for {cls.__name__}")
    hints, allowed = _model_schema(cls)
    unknown = set(data) - allowed
    if unknown:
        raise SimulationError(f"Unknown {cls.__name__} fields: {', '.join(sorted(unknown))}")
    try:
        decoded = {}
        for key, value in data.items():
            try:
                decoded[key] = _decode(hints[key], value)
            except SimulationError as exc:
                raise SimulationError(f"Invalid {cls.__name__}.{key}: {exc}") from exc
        return cls(**decoded)
    except (TypeError, ValueError) as exc:
        raise SimulationError(f"Invalid {cls.__name__}: {exc}") from exc


def project_from_dict(data: dict) -> Project:
    if not isinstance(data, dict):
        raise SimulationError("Expected a project JSON object")
    if type(data.get("schema_version", 1)) is not int or data.get("schema_version", 1) not in (1, 2):
        raise SimulationError(f"Unsupported project schema {data.get('schema_version')}; this app supports 1 and 2")
    migrated = deepcopy(data)
    # Version 1 lumped interface resistance meant the complete contact array.
    # All new contracts default to that meaning; old links remain effective.
    migrated["schema_version"] = 2
    return from_dict(Project, migrated)


def file_hash(path: str | Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        while chunk := stream.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def configuration_hash(project: Project | dict) -> str:
    data = deepcopy(project.to_dict() if isinstance(project, Project) else project)
    # File content, not its machine-specific location, identifies the layout.
    if data.get("layout", {}).get("sha256"):
        data["layout"]["path"] = ""
    return hashlib.sha256(json.dumps(data, sort_keys=True, separators=(",", ":"),
                                     allow_nan=False).encode()).hexdigest()


def physics_hash(project: Project | dict, *, resolve_layout: bool = True) -> str:
    """Identify a calculation independently of names, annotations and display state.

    Legacy dictionaries acquire current default settings before comparison. A
    layout without an identity is hashed from its bytes; an existing identity
    remains stable when the original path moves or its old file is replaced.
    """
    snapshot = project if isinstance(project, Project) else project_from_dict(project)
    data = snapshot.to_dict()
    data["schema_version"] = 1  # preserve the identity of supported old archives
    def omit_empty_exclusions(value):
        # Empty source exclusions preserve every pre-0.3.1 region expression.
        # Nonempty exclusions remain part of physical identity at every depth.
        if isinstance(value, dict):
            if value.get("exclude_sources") == []:
                value.pop("exclude_sources")
            for child in value.values():
                omit_empty_exclusions(child)
        elif isinstance(value, list):
            for child in value:
                omit_empty_exclusions(child)
    omit_empty_exclusions(data)
    for refinement in data["mesh"]["refinements"]:
        if not refinement.get("axis_sizes"):
            refinement.pop("axis_sizes", None)
    if data.get("power") == asdict(PowerSettings()):
        data.pop("power", None)
    for layer in data["layers"]:
        for key, default in {"net_id": "", "loss_role": "auto"}.items():
            if layer.get(key) == default:
                layer.pop(key, None)
    defaults = {"model_kind": "effective", "height_mode": "explicit", "enclosure": 0.,
                "allow_partial": False, "replaces_layers": [], "resistance_semantics": "array",
                "purpose": "connection", "reference_temperature": 300., "temperature_coefficient": 0.}
    for interface in data["interfaces"]:
        if interface.get("height_mode") == "stack" and interface.get("model_kind") != "effective":
            endpoints = [layer for layer in data["layers"] if layer["id"] in {interface["layer_a"], interface["layer_b"]}]
            if len(endpoints) == 2:
                lower, upper = sorted(endpoints, key=lambda layer: layer["z"])
                interface["height"] = upper["z"] - lower["z"] - lower["thickness"]
        for key, default in defaults.items():
            if interface.get(key) == default:
                interface.pop(key, None)
    # A disabled time-domain setup does not change a steady calculation. This
    # also preserves the physics identity of archives written before transients.
    if not data.get("transient", {}).get("enabled", False):
        data.pop("transient", None)
    else:
        # These settings were added after the first transient archive format.
        # Their defaults preserve its physical problem and must not change the
        # identity of real, already-saved enabled-transient results. Nondefault
        # startup/periodic choices and source-state digests remain part of it.
        for key, default in {"initialization": "uniform", "initial_state_digest": "",
                             "retain_peak": True, "periodic_tolerance": 0.,
                             "periodic_min_cycles": 3, "periodic_required_cycles": 2,
                             "capacity_scheme": "consistent"}.items():
            if data["transient"].get(key) == default:
                data["transient"].pop(key, None)
    for key in ("name", "notes", "display_roi", "sweeps", "thresholds"):
        data.pop(key, None)
    layout = data["layout"]
    if (resolve_layout and not getattr(snapshot, "_resource_unresolved", False)
            and not layout["sha256"] and layout["path"] and Path(layout["path"]).is_file()):
        layout["sha256"] = file_hash(layout["path"])
    if layout["sha256"]:
        layout["path"] = ""
    for layer in data["layers"]:
        for key in ("name", "visible", "color", "opacity"):
            layer.pop(key, None)
    for material in data["materials"].values():
        for key in ("name", "notes", "source", "version", "reference"):
            material.pop(key, None)
    for terminal in data["terminals"]:
        terminal.pop("name", None)

    def canonical(value):
        if isinstance(value, dict):
            return {k: canonical(v) for k, v in value.items()}
        if isinstance(value, list):
            return [canonical(v) for v in value]
        if type(value) in (int, float):
            return float(value)  # 300 and 300.0 describe the same physical setting
        return value

    return hashlib.sha256(json.dumps(canonical(data), sort_keys=True, separators=(",", ":"),
                                     allow_nan=False).encode()).hexdigest()


def refresh_threshold_diagnostics(result: SimulationResult, project: Project) -> None:
    """Refresh editable engineering annotations without recomputing physical fields."""
    prefix = "Engineering threshold exceeded:"
    result.diagnostics = [d for d in result.diagnostics if not d.message.startswith(prefix)]
    for metric, threshold in project.thresholds.items():
        run_metrics = result.metrics.get("whole_run_metrics", {})
        value = run_metrics.get(metric, result.metrics.get(metric)) if metric.startswith("transient_") else result.metrics.get(metric)
        if isinstance(value, (int, float)) and value > threshold:
            result.diagnostics.append(Diagnostic("warning", f"{prefix} {metric}={value:.6g}, limit={threshold:.6g}"))


def annotated_result(result: SimulationResult, project: Project, *, run: SimulationResult | None = None) -> SimulationResult:
    """Annotate a selected frame without losing run-wide checks or mutating it.

    Ordinary metrics refer to the selected time (or steady solution); explicitly
    named ``transient_*`` metrics describe the complete accepted run.
    """
    run = run or result
    # Keep frame cumulative energies intact: a displayed-frame archive can be
    # used as an initial/restart state. Whole-run annotations are separate.
    metrics = dict(result.metrics)
    if run is not result:
        metrics["whole_run_metrics"] = {key: value for key, value in run.metrics.items() if key.startswith("transient_")}
    diagnostics = list(run.diagnostics)
    diagnostics.extend(item for item in result.diagnostics if item not in diagnostics)
    annotated = replace(result, metrics=metrics, diagnostics=diagnostics)
    refresh_threshold_diagnostics(annotated, project)
    return annotated


def _json_value(value):
    if isinstance(value, np.ndarray):
        return [_json_value(x) for x in value.tolist()]
    if isinstance(value, np.generic):
        return _json_value(value.item())
    if isinstance(value, dict):
        return {str(k): _json_value(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_value(v) for v in value]
    if isinstance(value, float) and not np.isfinite(value):
        return None
    return value


def _json_dump(data) -> str:
    return json.dumps(_json_value(data), indent=2, allow_nan=False)


def _result_payload(result, project):
    arrays = {"points": result.mesh.points, "triangles": result.mesh.triangles}
    keys = {"regions": {}, "terminal_nodes": {}, "boundary_nodes": {}, "boundary_edges": {}, "fields": {}}
    for group in ("regions", "terminal_nodes", "boundary_nodes", "boundary_edges"):
        for index, (name, value) in enumerate(getattr(result.mesh, group).items()):
            key = f"{group}_{index}"
            arrays[key] = value
            keys[group][name] = key
    for group in ("fields", "element_fields", "interface_fields"):
        keys[group] = {}
        for i, (layer, field_data) in enumerate(getattr(result, group).items()):
            keys[group][layer] = {}
            for j, (name, value) in enumerate(field_data.items()):
                prefix = "field" if group == "fields" else group
                key = f"{prefix}_{i}_{j}"
                arrays[key] = value
                keys[group][layer][name] = key
    metadata = {"arrays": keys, "statistics": result.mesh.statistics,
                "metrics": result.metrics, "terminals": result.terminals,
                "diagnostics": [asdict(d) for d in result.diagnostics],
                "iterations": result.iterations, "converged": result.converged,
                "configuration": result.configuration, "configuration_hash": result.configuration_hash,
                "execution": getattr(result, "execution", {}),
                "analysis": result.analysis, "field_version": result.field_version,
                "elapsed_seconds": result.elapsed_seconds, "solver_version": result.solver_version,
                "physics_hash": result.physics_hash or physics_hash(result.configuration or project)}
    return arrays, metadata


def estimate_project_archive(project: Project, result: SimulationResult | None = None,
                             history: list[SimulationResult] | None = None, *, embed_layout=True) -> dict:
    """Estimate the aggregate expanded archive, including every immutable run.

    This deliberately counts uncompressed NPY data, not ZIP compression savings.
    It is a preflight estimate; save also inspects the completed temporary archive
    before replacing a destination. No large array copies/compression are needed.
    """
    require_resources(project)
    from etlayout.io.transient import _array_storage_size, _encoded_metadata
    used = metadata_bytes = entries = 0
    largest_metadata = 0

    def metadata(data):
        nonlocal used, metadata_bytes, entries, largest_metadata
        size = len(data if isinstance(data, bytes) else _json_dump(data).encode("utf-8"))
        used += size
        metadata_bytes += size
        largest_metadata = max(largest_metadata, size)
        entries += 1

    def arrays(values):
        nonlocal used, entries
        entries += 1 + len(values)  # NPZ container and NPY members
        used += sum(_array_storage_size(array) for array in values)

    def run(setup, value, embedded_flag):
        nonlocal used, entries
        saved = deepcopy(setup)
        embedded = None
        if setup.layout.path and Path(setup.layout.path).is_file():
            if embedded_flag:
                embedded = "layout/source" + Path(setup.layout.path).suffix.lower()
                saved.layout.path = embedded
                used += Path(setup.layout.path).stat().st_size
                entries += 1
            # Hash is always present after serialization, even for new layouts.
            saved.layout.sha256 = saved.layout.sha256 or "0" * 64
        metadata({"format": "etlayout", "schema_version": 2, "application_version": __version__,
                  "project": saved.to_dict(), "embedded_layout": embedded,
            "document_id": document_id(project)})
        if value is not None:
            payload, details = _result_payload(value, saved)
            arrays(list(payload.values()))
            metadata(details)
            if value.transient is not None:
                metadata(_encoded_metadata(value.transient))
                for frame in value.transient.snapshots:
                    arrays([array for group in (frame.fields, frame.element_fields, frame.interface_fields)
                            for fields in group.values() for array in fields.values()])

    run(project, result, embed_layout)
    for value in history or []:
        if not isinstance(value, SimulationResult) or not value.configuration:
            raise SimulationError("Saved run history requires results with their recorded configurations")
        entries += 1
        run(project_from_dict(value.configuration), value, True)
    issues = []
    if used > MAX_ARCHIVE_BYTES:
        issues.append("Aggregate expanded project and run history exceed the archive byte budget")
    if largest_metadata > MAX_METADATA_BYTES:
        issues.append("An archive metadata document exceeds its byte budget")
    if entries > MAX_ARCHIVE_ENTRIES:
        issues.append("Project and run history exceed the archive entry budget")
    return {"estimated_uncompressed_bytes": used, "metadata_bytes": metadata_bytes,
            "largest_metadata_bytes": largest_metadata, "entries": entries,
            "limits": {"uncompressed_bytes": MAX_ARCHIVE_BYTES, "metadata_bytes": MAX_METADATA_BYTES,
                       "entries": MAX_ARCHIVE_ENTRIES}, "fits_budget": not issues, "issues": issues}


def inspect_archive_size(path, *, _archive_data=None) -> dict:
    """Check recursive ZIP/NPZ budgets before array allocation or replacement.

    Nested run archives share the outer budget. NPY header shapes are checked
    against their actual member sizes, preventing tiny payloads claiming huge arrays.
    """
    used = metadata_bytes = entries = 0

    def inspect(source, depth=0, npz=False):
        nonlocal used, metadata_bytes, entries
        if depth > MAX_ARCHIVE_DEPTH:
            raise SimulationError("Project archive exceeds its nesting budget")
        with zipfile.ZipFile(source) as archive:
            members = archive.infolist()
            for member in members:
                name = member.filename
                if ("\\" in name or PurePosixPath(name).is_absolute()
                        or ".." in PurePosixPath(name).parts or ":" in name):
                    raise SimulationError("Project archive contains an unsafe member name")
                if (member.external_attr >> 16) & 0o170000 == 0o120000:
                    raise SimulationError("Project archive contains a symbolic link")
            if len({item.filename for item in members}) != len(members):
                raise SimulationError("Project archive contains duplicate member names")
            entries += len(members)
            if entries > MAX_ARCHIVE_ENTRIES:
                raise SimulationError("Project archive exceeds its entry budget")
            if sum(item.file_size for item in members) > MAX_ARCHIVE_BYTES:
                raise SimulationError("Project archive exceeds its decompressed safety budget")
            for item in members:
                if item.is_dir():
                    continue
                if not npz and (item.filename.endswith(".npz") or
                                item.filename.startswith("runs/") and item.filename.endswith(".etl")):
                    inspect(io.BytesIO(archive.read(item)), depth + 1, item.filename.endswith(".npz"))
                    continue
                used += item.file_size
                if used > MAX_ARCHIVE_BYTES:
                    raise SimulationError("Aggregate expanded project and run history exceed the archive byte budget")
                if item.filename.endswith(".json"):
                    metadata_bytes += item.file_size
                    if item.file_size > MAX_METADATA_BYTES:
                        raise SimulationError("Project metadata exceeds its byte budget")
                if npz:
                    with archive.open(item) as stream:
                        version = np.lib.format.read_magic(stream)
                        if version == (1, 0):
                            shape, _, dtype = np.lib.format.read_array_header_1_0(stream)
                        elif version == (2, 0):
                            shape, _, dtype = np.lib.format.read_array_header_2_0(stream)
                        else:
                            raise SimulationError("Unsupported stored NumPy array format")
                        if dtype.hasobject or math.prod(shape) * dtype.itemsize != item.file_size - stream.tell():
                            raise SimulationError("Stored NumPy array shape does not match its safe numeric payload")

    try:
        inspect(io.BytesIO(_archive_data) if _archive_data is not None else path)
    except (zipfile.BadZipFile, ValueError, EOFError, OSError) as error:
        raise SimulationError(f"Cannot inspect project archive: {error}") from error
    return {"uncompressed_bytes": used, "metadata_bytes": metadata_bytes, "entries": entries}


def save_project(project: Project, path: str | Path, result: SimulationResult | None = None,
                 embed_layout: bool = True, history: list[SimulationResult] | None = None,
                 *, progress=None, cancel=None, commit=None) -> None:
    target = Path(path)
    if target.suffix.lower() == ".json":
        raise SimulationError("JSON is a metadata format. Use save_json for JSON metadata or choose .etlayout for a project archive.")

    def checkpoint(fraction, message):
        if cancel is not None and cancel():
            from etlayout.io.saving import SaveCancelled
            raise SaveCancelled("Save cancelled; the previous project was preserved")
        if progress is not None:
            progress(fraction, message)

    checkpoint(0., "Checking save snapshot")
    # Incomplete physical setups are useful drafts, but every saved setup must
    # satisfy the typed schema and reopen without losing null/nonfinite values.
    project_from_dict(project.to_dict())
    estimate = estimate_project_archive(project, result, history, embed_layout=embed_layout)
    if not estimate["fits_budget"]:
        raise SimulationError("; ".join(estimate["issues"]) + ". Reduce retained snapshots or saved run history.")
    checkpoint(.08, "Preparing archive and source layout")
    target.parent.mkdir(parents=True, exist_ok=True)
    saved = deepcopy(project)
    embedded = None
    if project.layout.path and Path(project.layout.path).is_file():
        saved.layout.sha256 = file_hash(project.layout.path)
        if project.layout.sha256 and saved.layout.sha256 != project.layout.sha256.lower():
            raise SimulationError("Layout source changed and describes a different physical setup; it no longer matches the save snapshot. Relink before saving; the previous project was preserved.")
        if embed_layout:
            embedded = "layout/source" + Path(project.layout.path).suffix.lower()
            saved.layout.path = embedded
        else:
            saved.layout.path = os.path.relpath(Path(project.layout.path).resolve(), target.parent.resolve())
    if result:
        result = copy(result)
        if project.layout.path and not Path(project.layout.path).is_file() and embed_layout:
            raise SimulationError("Cannot embed the result's layout source. Relink the source before exporting a portable archive.")
        if result.configuration:
            project_from_dict(result.configuration)
            if result.configuration_hash and configuration_hash(result.configuration) != result.configuration_hash:
                raise SimulationError("Result configuration content does not match its recorded hash")
            recorded = result.physics_hash or physics_hash(result.configuration)
            if recorded != physics_hash(result.configuration):
                raise SimulationError("Result physics hash does not match its recorded configuration")
            if recorded != physics_hash(saved):
                raise SimulationError("Results belong to a different physical setup. Save the edited project without stale results.")
        _validate_result_arrays(result.mesh, result.fields, result.converged)
        _validate_local_fields(result)
        if result.transient is not None:
            from etlayout.io.transient import validate_history
            validate_history(result, saved)
        refresh_threshold_diagnostics(result, saved)
    meta = {"format": "etlayout", "schema_version": 2, "application_version": __version__,
            "project": saved.to_dict(), "embedded_layout": embedded,
            "document_id": document_id(project)}
    fd, temporary = tempfile.mkstemp(prefix=target.name + ".", suffix=".tmp", dir=target.parent)
    os.close(fd)
    try:
        with zipfile.ZipFile(temporary, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            archive.writestr("project.json", _json_dump(meta))
            if embedded:
                archive.write(project.layout.path, embedded)
            if result:
                checkpoint(.2, "Serializing numerical fields")
                array_data, metadata = _result_payload(result, saved)
                buffer = io.BytesIO()
                np.savez_compressed(buffer, **array_data)
                archive.writestr("results/arrays.npz", buffer.getvalue())
                archive.writestr("results/metadata.json", _json_dump(metadata))
                if result.transient is not None:
                    from etlayout.io.transient import write_history
                    write_history(archive, result)
            if history:
                # Each immutable run carries its own setup and layout, including
                # runs from another design. Existing readers ignore this folder.
                with tempfile.TemporaryDirectory(prefix="etlayout-runs-", dir=target.parent) as folder:
                    for index, run in enumerate(history):
                        checkpoint(.4 + .4 * index / len(history), f"Saving history {index + 1}/{len(history)}")
                        if not isinstance(run, SimulationResult) or not run.configuration:
                            raise SimulationError("Saved run history requires results with their recorded configurations")
                        nested = Path(folder) / f"run_{index:04d}.etl"
                        save_project(project_from_dict(run.configuration), nested, run, embed_layout=True, cancel=cancel)
                        archive.write(nested, f"runs/run_{index:04d}.etl")
        checkpoint(.85, "Verifying complete archive")
        inspect_archive_size(temporary)
        with zipfile.ZipFile(temporary) as verified:
            if verified.testzip() is not None:
                raise SimulationError("Finished project archive failed its CRC integrity check")
            if embedded:
                digest = hashlib.sha256()
                with verified.open(embedded) as layout_stream:
                    while chunk := layout_stream.read(1024 * 1024):
                        checkpoint(.9, "Verifying embedded source identity")
                        digest.update(chunk)
                if digest.hexdigest() != saved.layout.sha256:
                    raise SimulationError("Source layout changed during saving; the previous project was preserved")
        with Path(temporary).open("r+b") as durable:
            durable.flush()
            os.fsync(durable.fileno())
        checkpoint(.98, "Committing verified project")
        if commit is None:
            os.replace(temporary, target)
        else:
            commit(temporary, target)
        if progress is not None:
            progress(1., "Project archive committed")
    finally:
        if Path(temporary).exists():
            Path(temporary).unlink()


def _validate_result_arrays(mesh: PlanarMesh, field_data: dict, converged: bool) -> None:
    """Validate the portable N-node/M-element contract before plotting or solving."""
    points, triangles = mesh.points, mesh.triangles
    if (points.ndim != 2 or points.shape[1] != 2 or len(points) < 3
            or points.dtype.kind not in "fiu" or not np.all(np.isfinite(points))):
        raise SimulationError("Invalid stored mesh points: need finite real coordinates with shape (N, 2)")
    if (triangles.ndim != 2 or triangles.shape[1] != 3 or len(triangles) == 0
            or triangles.dtype.kind not in "iu"):
        raise SimulationError("Invalid stored mesh triangles: need integer connectivity with shape (M, 3)")
    if np.any(triangles < 0) or np.any(triangles >= len(points)):
        raise SimulationError("Invalid stored mesh connectivity: node index out of range")
    if np.any(~np.isfinite(mesh.areas)) or np.any(mesh.areas <= 0):
        raise SimulationError("Invalid stored mesh: degenerate triangles")

    def indices(values, limit, label, dimension=1):
        if values.ndim != dimension or values.dtype.kind not in "iu":
            raise SimulationError(f"Invalid stored {label}: integer indices required")
        if np.any(values < 0) or np.any(values >= limit):
            raise SimulationError(f"Invalid stored {label}: index out of range")

    for name, values in mesh.regions.items():
        indices(values, len(triangles), f"region {name}")
        if len(np.unique(values)) != len(values):
            raise SimulationError(f"Invalid stored region {name}: duplicate triangle indices")
    for group_name in ("terminal_nodes", "boundary_nodes"):
        for name, values in getattr(mesh, group_name).items():
            indices(values, len(points), f"{group_name} {name}")
    if mesh.boundary_edges:
        all_edges = np.unique(np.sort(np.concatenate([triangles[:, [0, 1]], triangles[:, [1, 2]],
                                                      triangles[:, [2, 0]]]), axis=1), axis=0)
        # Structured rows permit vectorized pair membership without a large
        # Python set of tuples for meshes with hundreds of thousands of cells.
        edge_dtype = np.dtype([("a", np.int64), ("b", np.int64)])
        edge_rows = np.ascontiguousarray(all_edges, dtype=np.int64).view(edge_dtype).ravel()
        for name, values in mesh.boundary_edges.items():
            indices(values, len(points), f"boundary_edges {name}", dimension=2)
            if values.shape[1] != 2 or np.any(values[:, 0] == values[:, 1]):
                raise SimulationError(f"Invalid stored boundary_edges {name}: need distinct node pairs")
            selected = np.ascontiguousarray(np.sort(values, axis=1), dtype=np.int64).view(edge_dtype).ravel()
            if not np.all(np.isin(selected, edge_rows)):
                raise SimulationError(f"Invalid stored boundary_edges {name}: pair is not a mesh edge")
    nodal = {"potential", "temperature", "temperature_rise"}
    vectors = {"electric_field", "sheet_current", "current_density", "thermal_gradient", "sheet_conductance"}
    scalar_elements = {"joule_density", "heat_density", "contact_density"}
    for layer_id, fields_by_name in field_data.items():
        if f"layer:{layer_id}" not in mesh.regions:
            raise SimulationError(f"Stored field layer {layer_id} is absent from mesh regions")
        elements = mesh.elements(layer_id)
        nodes = np.unique(triangles[elements])
        for name, values in fields_by_name.items():
            if values.dtype.kind not in "fiu" or np.any(np.isinf(values)):
                raise SimulationError(f"Stored field {layer_id}/{name} needs real values without infinities")
            if name in nodal:
                valid_shape = values.shape == (len(points),)
                active = nodes
            elif name in vectors:
                valid_shape = values.shape == (len(triangles), 2)
                active = elements
            else:
                valid_shape = (values.shape == (len(triangles),) or
                               (name not in scalar_elements and values.shape == (len(triangles), 2)))
                active = elements
            if not valid_shape:
                raise SimulationError(f"Stored field {layer_id}/{name} violates the N-node/M-element shape contract")
            if converged and not np.all(np.isfinite(values[active])):
                raise SimulationError(f"Converged field {layer_id}/{name} has nonfinite values on active mesh entities")


def _validate_local_fields(result: SimulationResult) -> None:
    """Validate authoritative P1 values without merging coordinate-coincident DOFs."""
    if result.field_version not in (1, 2):
        raise SimulationError("Unsupported result field representation")
    for category in ("element_fields", "interface_fields"):
        for owner, fields_by_name in getattr(result, category).items():
            prefix = "layer:" if category == "element_fields" else "interface:"
            if prefix + owner not in result.mesh.regions:
                raise SimulationError(f"Stored {category} owner {owner} is absent from mesh regions")
            active = result.mesh.regions[prefix + owner]
            for name, values in fields_by_name.items():
                shapes = {(len(result.mesh.triangles), 3)}
                if category == "interface_fields":
                    shapes.add((len(result.mesh.triangles),))
                if values.shape not in shapes or values.dtype.kind not in "fiu" or np.isinf(values).any():
                    raise SimulationError(f"Stored {category} {owner}/{name} violates the local element shape contract")
                if result.converged and not np.isfinite(values[active]).all():
                    raise SimulationError(f"Converged local field {owner}/{name} contains nonfinite active values")


def diagnose_legacy_fields(result: SimulationResult) -> None:
    """A legacy point-touch average cannot be inverted; require a fresh solve."""
    from etlayout.visualization.support import _components
    for layer, fields_by_name in result.fields.items():
        missing = {"potential", "temperature"}.intersection(fields_by_name) - set(result.element_fields.get(layer, {}))
        if not missing:
            continue
        owners = {}
        ambiguous = False
        for component, elements in enumerate(_components(result.mesh, layer)):
            for node in np.unique(result.mesh.triangles[elements]):
                if node in owners and owners[node] != component:
                    ambiguous = True
                owners[node] = component
        if ambiguous:
            message = f"Legacy field ambiguity in layer {layer}: point-touch component values were averaged; recompute before sampling, comparison, or optimization."
            if not any(d.message == message for d in result.diagnostics):
                result.diagnostics.append(Diagnostic("warning", message))
            result.analysis.setdefault("legacy_ambiguous_layers", [])
            if layer not in result.analysis["legacy_ambiguous_layers"]:
                result.analysis["legacy_ambiguous_layers"].append(layer)


def load_project(path: str | Path, *, external_policy: ExternalResourcePolicy | None = None,
                 _archive_data: bytes | None = None, _resource_prefix: str = "") -> tuple[Project, SimulationResult | None]:
    """Inspect first, then resolve only policy-approved resources (local-root by default)."""
    policy = external_policy or ExternalResourcePolicy()
    inspection = inspect_project(path, _archive_data=_archive_data, _prefix=_resource_prefix,
                                 allow_network_container=policy.allow_network)
    return resolve_project(inspection, external_policy=external_policy)


def resolve_project(inspection: ProjectInspection, *, external_policy: ExternalResourcePolicy | None = None
                    ) -> tuple[Project, SimulationResult | None]:
    """Second stage: resolve the exact metadata/container previously inspected."""
    policy = external_policy or ExternalResourcePolicy()
    source, _archive_data = inspection.source, inspection.archive_data
    project = deepcopy(inspection.project)
    if _archive_data is None:
        if project.layout.path:
            location, digest = resolve_layout(inspection, policy)
            project.layout.path, project.layout.sha256 = str(location), digest
        return project, None
    try:
        inspect_archive_size(source, _archive_data=_archive_data)
        with zipfile.ZipFile(io.BytesIO(_archive_data) if _archive_data is not None else source) as archive:
            metadata = inspection.metadata
            if (not isinstance(metadata, dict) or metadata.get("format") != "etlayout"
                    or type(metadata.get("schema_version")) is not int or metadata["schema_version"] not in (1, 2)):
                raise SimulationError("Unsupported project format/version")
            if metadata.get("embedded_layout"):
                raw = archive.read(metadata["embedded_layout"])
                digest = hashlib.sha256(raw).hexdigest()
                if project.layout.sha256 and digest != project.layout.sha256:
                    raise SimulationError("Embedded layout hash does not match project metadata")
                suffix = Path(metadata["embedded_layout"]).suffix.lower()
                if suffix not in {".gds", ".gdsii", ".oas", ".oasis"}:
                    raise SimulationError("Unexpected embedded layout format")
                cache = Path(tempfile.gettempdir()) / "etlayout-layouts"
                cache.mkdir(parents=True, exist_ok=True)
                cached = cache / (digest + suffix)
                if not cached.exists() or file_hash(cached) != digest:
                    cached.write_bytes(raw)
                project.layout.path = str(cached)
                project.layout.sha256 = digest
            elif project.layout.path:
                candidate, digest = resolve_layout(inspection, policy)
                project.layout.path, project.layout.sha256 = str(candidate), digest
            if inspection.relinked or "results/metadata.json" not in archive.namelist():
                return project, None
            rm = json.loads(archive.read("results/metadata.json"))
            if not isinstance(rm, dict) or not isinstance(rm.get("arrays"), dict):
                raise SimulationError("Invalid stored result metadata objects")
            converged = _decode(bool, rm["converged"])
            recorded_configuration = rm.get("configuration") or json.loads(archive.read("project.json"))["project"]
            if rm.get("configuration_hash") and rm.get("configuration"):
                project_from_dict(rm["configuration"])
                if configuration_hash(rm["configuration"]) != rm["configuration_hash"]:
                    raise SimulationError("Saved result configuration content does not match its hash")
            if physics_hash(recorded_configuration, resolve_layout=False) != physics_hash(project):
                if inspection.discard_stale_result:
                    return project, None
                raise SimulationError("Saved result configuration does not match project physical setup")
            with np.load(io.BytesIO(archive.read("results/arrays.npz")), allow_pickle=False) as arrays:
                def group(name):
                    names = _decode(dict[str, str], rm["arrays"].get(name, {}))
                    return {k: np.array(arrays[v]) for k, v in names.items()}
                mesh = PlanarMesh(np.array(arrays["points"]), np.array(arrays["triangles"]),
                                  group("regions"), group("terminal_nodes"), group("boundary_nodes"),
                                  rm.get("statistics", {}), group("boundary_edges"))
                field_keys = _decode(dict[str, dict[str, str]], rm["arrays"]["fields"])
                field_data = {layer: {name: np.array(arrays[key]) for name, key in values.items()}
                              for layer, values in field_keys.items()}
                local_data = {}
                for category in ("element_fields", "interface_fields"):
                    names = _decode(dict[str, dict[str, str]], rm["arrays"].get(category, {}))
                    local_data[category] = {layer: {name: np.array(arrays[key]) for name, key in values.items()}
                                            for layer, values in names.items()}
            _validate_result_arrays(mesh, field_data, converged)
            result = SimulationResult(mesh, fields=field_data, metrics=rm["metrics"], terminals=rm["terminals"],
                                      diagnostics=[Diagnostic(**d) for d in rm["diagnostics"]],
                                      iterations=rm["iterations"], converged=converged,
                                      configuration=rm["configuration"], configuration_hash=rm["configuration_hash"],
                                      elapsed_seconds=rm["elapsed_seconds"], solver_version=rm["solver_version"],
                                      execution=rm.get("execution", {}),
                                      **local_data, analysis=rm.get("analysis", {}),
                                      field_version=_decode(int, rm.get("field_version", 1)),
                                      physics_hash=rm.get("physics_hash", "") or physics_hash(recorded_configuration, resolve_layout=False))
            _validate_local_fields(result)
            diagnose_legacy_fields(result)
            if result.physics_hash != physics_hash(project):
                raise SimulationError("Saved result physics hash does not match project setup")
            if result.configuration.get("layout", {}).get("sha256"):
                result.configuration["layout"]["path"] = project.layout.path
            if "transient/metadata.json" in archive.namelist():
                from etlayout.io.transient import read_history
                result.transient = read_history(archive, result, project)
            elif any(key in result.metrics for key in ("transient_completed", "transient_steps", "transient_snapshot_count", "transient_time")):
                raise SimulationError("Stored transient run is missing its time-history payload")
            refresh_threshold_diagnostics(result, project)
            return project, result
    except (zipfile.BadZipFile, KeyError, ValueError, TypeError, IndexError, OverflowError, OSError) as exc:
        if isinstance(exc, SimulationError):
            raise
        raise SimulationError(f"Cannot load project {source.name}: {exc}") from exc


def save_json(project: Project, path: str | Path) -> None:
    project_from_dict(project.to_dict())
    Path(path).write_text(_json_dump({"project": project.to_dict(), "document_id": document_id(project)}), encoding="utf-8")


def load_run_history(path: str | Path, *, external_policy: ExternalResourcePolicy | None = None,
                     _archive_data: bytes | None = None) -> list[SimulationResult]:
    """Load immutable stored runs without changing load_project's two-value API."""
    source = Path(path)
    if source.suffix.lower() == ".json":
        return []
    history = []
    try:
        if _archive_data is None:
            inspected = inspect_project(source, allow_network_container=(external_policy or ExternalResourcePolicy()).allow_network)
            source, _archive_data = inspected.source, inspected.archive_data
        inspect_archive_size(source, _archive_data=_archive_data)
        with zipfile.ZipFile(io.BytesIO(_archive_data) if _archive_data is not None else source) as archive:
            for name in sorted(n for n in archive.namelist() if n.startswith("runs/run_") and n.endswith(".etl")):
                _, result = load_project(source, external_policy=external_policy, _archive_data=archive.read(name),
                                         _resource_prefix=name+":")
                if result is None:
                    raise SimulationError(f"Saved run {name} has no numerical result")
                history.append(result)
        return history
    except (zipfile.BadZipFile, KeyError, OSError) as exc:
        raise SimulationError(f"Cannot load run history: {exc}") from exc
