# SPDX-License-Identifier: MPL-2.0
"""Opt-in archive-local content addressing, exact typed metadata, and guarded reads."""
from __future__ import annotations

import hashlib
import io
import json
import math
import os
import re
import struct
import tempfile
import uuid
import zipfile
from copy import deepcopy
from dataclasses import dataclass, fields
from pathlib import Path, PurePosixPath

import numpy as np

from etlayout import __version__
from etlayout.io.identity import restore_document_id
from etlayout.io.resources import ExternalResourcePolicy, ProjectInspection, classify_resource, resolve_layout
from etlayout.models import Diagnostic, PlanarMesh, SimulationError, SimulationResult, TransientHistory

_CATALOG = object()

_DIGEST = re.compile(r"[0-9a-f]{64}\Z")
_GROUPS = ("fields", "element_fields", "interface_fields")
_MESH_GROUPS = ("regions", "terminal_nodes", "boundary_nodes", "boundary_edges")
_META_FIELDS = tuple(item.name for item in fields(SimulationResult)
                     if item.name not in {"mesh", "transient", *_GROUPS})


def _limits():
    from etlayout.io import project
    return project.MAX_ARCHIVE_BYTES, project.MAX_METADATA_BYTES, project.MAX_ARCHIVE_ENTRIES


def _json(data):
    try:
        return json.dumps(data, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")
    except (TypeError, ValueError, OverflowError, RecursionError) as error:
        raise SimulationError("Invalid content-addressed metadata") from error


def _parse(raw):
    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                raise SimulationError("Duplicate metadata object key")
            result[key] = value
        return result
    def nonfinite(_):
        raise SimulationError("Nonfinite plain JSON metadata")
    try:
        return json.loads(raw, object_pairs_hook=pairs, parse_constant=nonfinite)
    except (ValueError, TypeError, RecursionError, UnicodeError) as error:
        raise SimulationError("Invalid content-addressed JSON metadata") from error


def _ref(digest):
    return {"$ref": digest}


def _references(value, depth=0):
    if depth > 64:
        raise SimulationError("Object metadata exceeds its nesting budget")
    if isinstance(value, dict):
        if set(value) == {"$ref"}:
            digest = value["$ref"]
            if not isinstance(digest, str) or not _DIGEST.fullmatch(digest):
                raise SimulationError("Invalid archive-local object address")
            yield digest
        else:
            for item in value.values():
                yield from _references(item, depth + 1)
    elif isinstance(value, list):
        for item in value:
            yield from _references(item, depth + 1)


def _array_header(array):
    if array.dtype.hasobject or array.dtype.fields is not None or array.dtype.itemsize == 0:
        raise SimulationError("Object and structured arrays are unsupported by the non-pickled array codec")
    header = io.BytesIO()
    np.lib.format.write_array_header_2_0(header, {"descr": array.dtype.str, "fortran_order": False, "shape": array.shape})
    return header.getvalue()


def _array_chunks(array):
    yield _array_header(array)
    if array.size:
        iterator = np.nditer(array, flags=["external_loop", "buffered", "zerosize_ok"], op_flags=["readonly"],
                             order="C", buffersize=max(1, 1024 * 1024 // max(array.dtype.itemsize, 1)))
        for block in iterator:
            yield block.tobytes(order="C")


def _typed(value, collector, depth=0):
    if depth > 64:
        raise SimulationError("Typed metadata exceeds its nesting budget")
    if isinstance(value, np.generic):
        return {"type": "numpy_scalar", "array": collector.array(np.asarray(value))}
    if isinstance(value, np.ndarray):
        return {"type": "ndarray", "array": collector.array(value)}
    if value is None or type(value) in (bool, int, str):
        return value
    if type(value) is float:
        return {"type": "float64", "bits": struct.pack(">d", value).hex()}
    if isinstance(value, Diagnostic):
        return {"type": "diagnostic", "items": {item.name: _typed(getattr(value, item.name), collector, depth + 1)
                                                   for item in fields(value)}}
    if isinstance(value, dict):
        return {"type": "mapping", "items": [[_typed(key, collector, depth + 1), _typed(item, collector, depth + 1)]
                                               for key, item in value.items()]}
    if isinstance(value, (list, tuple)):
        return {"type": "tuple" if isinstance(value, tuple) else "list",
                "items": [_typed(item, collector, depth + 1) for item in value]}
    raise SimulationError(f"Unsupported typed metadata value: {type(value).__name__}")


def _untyped(value, reader, loader, cancel, depth=0):
    if depth > 64:
        raise SimulationError("Typed metadata exceeds its nesting budget")
    if value is None or type(value) in (bool, int, str):
        return value
    if not isinstance(value, dict) or not isinstance(value.get("type"), str):
        raise SimulationError("Invalid typed metadata value")
    kind = value["type"]
    if kind == "shared_metadata" and set(value) == {"type", "object"}:
        return reader._metadata(value["object"], loader, cancel)
    if kind == "float64" and set(value) == {"type", "bits"}:
        if not isinstance(value["bits"], str) or not re.fullmatch(r"[0-9a-f]{16}", value["bits"]):
            raise SimulationError("Invalid exact floating-point metadata")
        return struct.unpack(">d", bytes.fromhex(value["bits"]))[0]
    if kind in {"ndarray", "numpy_scalar"} and set(value) == {"type", "array"}:
        if loader is _CATALOG:
            reference = value["array"]
            refs = list(_references(reference))
            if len(refs) != 1 or set(reference) != {"$ref"}:
                raise SimulationError("Invalid typed array reference")
            info = reader.array_info(refs[0])
            if kind == "numpy_scalar" and info["shape"] != []:
                raise SimulationError("Invalid NumPy scalar metadata shape")
            return ("unloaded-numpy-value", refs[0])
        array = reader._array(value["array"], loader, cancel)
        if kind == "numpy_scalar":
            if array.shape != ():
                raise SimulationError("Invalid NumPy scalar metadata shape")
            return array[()]
        return array
    if kind in {"list", "tuple", "mapping", "diagnostic"} and set(value) == {"type", "items"}:
        items = value["items"]
        if kind == "diagnostic":
            if not isinstance(items, dict) or set(items) != {item.name for item in fields(Diagnostic)}:
                raise SimulationError("Invalid diagnostic metadata")
            return Diagnostic(**{key: _untyped(item, reader, loader, cancel, depth + 1) for key, item in items.items()})
        if not isinstance(items, list):
            raise SimulationError("Invalid typed collection metadata")
        if kind == "mapping":
            output = {}
            for pair in items:
                if not isinstance(pair, list) or len(pair) != 2:
                    raise SimulationError("Invalid typed mapping pair")
                key, item = (_untyped(part, reader, loader, cancel, depth + 1) for part in pair)
                try:
                    if key in output:
                        raise SimulationError("Duplicate typed mapping key")
                    output[key] = item
                except TypeError as error:
                    raise SimulationError("Invalid typed mapping key") from error
            return output
        output = [_untyped(item, reader, loader, cancel, depth + 1) for item in items]
        return tuple(output) if kind == "tuple" else output
    raise SimulationError("Unsupported typed metadata codec; upgrade the application")


def _checkpoint(cancel=None):
    if cancel is not None and cancel():
        from etlayout.io.saving import SaveCancelled
        raise SaveCancelled("Archive operation cancelled; the previous project was preserved")


class _Collector:
    def __init__(self, cancel=None):
        self.index, self.payloads, self.sources = {}, {}, {}
        self.cancel = cancel

    def _add(self, digest, record, payload):
        record = {"compression": "deflate", **record}
        if digest in self.index and self.index[digest] != record:
            raise SimulationError("Conflicting content-addressed object kinds")
        self.index[digest], self.payloads[digest] = record, payload
        return _ref(digest)

    def array(self, array):
        digest, size = hashlib.sha256(), 0
        for chunk in _array_chunks(array):
            _checkpoint(self.cancel)
            digest.update(chunk)
            size += len(chunk)
        return self._add(digest.hexdigest(), {"kind": "array", "suffix": ".npy", "bytes": size,
            "nbytes": array.nbytes, "dtype": array.dtype.str, "shape": list(array.shape), "references": []}, array)

    def json(self, value):
        raw = _json(value)
        if len(raw) > _limits()[1]:
            raise SimulationError("Object metadata exceeds its byte budget")
        return self._add(hashlib.sha256(raw).hexdigest(), {"kind": "json", "suffix": ".json", "bytes": len(raw),
            "references": sorted(set(_references(value)))}, raw)

    def metadata(self, value):
        return self.json({"typed_codec": 1, "value": _typed(value, self)})

    def layout(self, path, expected):
        from etlayout.io.project import file_hash
        path = Path(path)
        suffix = path.suffix.lower()
        if suffix not in {".gds", ".gdsii", ".oas", ".oasis"}:
            raise SimulationError("Unsupported embedded source layout format")
        if path.stat().st_size > _limits()[0]:
            raise SimulationError("Source layout exceeds the archive byte budget")
        digest = file_hash(path)
        self.sources[path] = digest
        if expected and digest != expected.lower():
            raise SimulationError("Layout source changed; relink before saving. The previous project was preserved.")
        return self._add(digest, {"kind": "layout", "suffix": suffix, "bytes": path.stat().st_size, "references": []}, path)

    def name(self, digest):
        return "objects/sha256/" + digest + self.index[digest]["suffix"]

    def write(self, archive, progress=None):
        for number, (digest, record) in enumerate(self.index.items()):
            _checkpoint(self.cancel)
            calculated, payload = hashlib.sha256(), self.payloads[digest]
            with archive.open(self.name(digest), "w", force_zip64=True) as destination:
                if isinstance(payload, tuple):
                    reader, source_digest = payload
                    for chunk in reader.iter_object_chunks(source_digest, cancel=self.cancel):
                        calculated.update(chunk)
                        destination.write(chunk)
                elif record["kind"] == "array":
                    for chunk in _array_chunks(payload):
                        _checkpoint(self.cancel)
                        calculated.update(chunk)
                        destination.write(chunk)
                elif record["kind"] == "layout":
                    with payload.open("rb") as source:
                        while chunk := source.read(1024 * 1024):
                            _checkpoint(self.cancel)
                            calculated.update(chunk)
                            destination.write(chunk)
                else:
                    calculated.update(payload)
                    destination.write(payload)
            if calculated.hexdigest() != digest:
                raise SimulationError("Source content changed while saving; the previous project was preserved")
            if progress is not None:
                progress(.25 + .5 * (number + 1) / max(len(self.index), 1), "Writing verified shared objects")


def _groups(value, collector):
    return {group: {owner: {name: collector.array(array) for name, array in values.items()}
                    for owner, values in getattr(value, group).items()} for group in _GROUPS}


def _result_metadata(value, collector):
    # Configuration/execution/analysis snapshots recur across transient frames
    # while metrics and diagnostics vary. Address those metadata values once;
    # sharing never turns scientific hashes into experiment/record identities.
    shared = {"configuration", "execution", "analysis"}
    encoded = {"type": "mapping", "items": [[name,
        {"type": "shared_metadata", "object": collector.metadata(getattr(value, name))}
        if name in shared else _typed(getattr(value, name), collector)] for name in _META_FIELDS]}
    return collector.json({"typed_codec": 1, "value": encoded})


def _summary(value):
    from etlayout.io.project import _json_value
    def scalars(items):
        return {key: _json_value(item) for key, item in items.items()
                if isinstance(key, str) and (item is None or type(item) in (str, bool, int, float))}
    return {"name": value.configuration.get("name", ""), "solver_version": value.solver_version,
        "configuration": deepcopy(value.configuration), "configuration_hash": value.configuration_hash,
        "physics_hash": value.physics_hash, "mode": value.configuration.get("solver", {}).get("mode", ""),
        "elapsed_seconds": value.elapsed_seconds, "converged": value.converged,
        "diagnostics": [{item.name: _json_value(getattr(diagnostic, item.name)) for item in fields(diagnostic)}
                        for diagnostic in value.diagnostics], "field_version": value.field_version,
        "field_keys": {group: {owner: sorted(data) for owner, data in getattr(value, group).items()} for group in _GROUPS},
        "metrics": scalars(value.metrics), "statistics": scalars(value.mesh.statistics), "execution": scalars(value.execution),
        "times_s": [] if value.transient is None else value.transient.times.tolist(),
        "completed": value.converged if value.transient is None else value.transient.completed,
        "transient_metadata": None if value.transient is None else {"times_s": value.transient.times.tolist(),
            "rows_count": len(value.transient.rows), "completed": value.transient.completed}}


def _describe(setup, value, collector, target, embed_layout):
    from etlayout.io.project import (
        _validate_local_fields,
        _validate_result_arrays,
        configuration_hash,
        file_hash,
        physics_hash,
        project_from_dict,
    )
    from etlayout.io.transient import validate_history
    project_from_dict(setup.to_dict())
    saved, layout = deepcopy(setup), None
    if setup.layout.path:
        path = Path(setup.layout.path)
        if not path.is_file():
            if embed_layout and value is not None:
                raise SimulationError("Cannot embed the result's layout source; relink before portable saving")
        elif embed_layout:
            layout = collector.layout(path, setup.layout.sha256)
            saved.layout.sha256 = layout["$ref"]
            saved.layout.path = collector.name(layout["$ref"])
        else:
            digest = file_hash(path)
            if setup.layout.sha256 and digest != setup.layout.sha256.lower():
                raise SimulationError("Layout source changed; relink before saving")
            collector.sources[path] = digest
            saved.layout.sha256 = digest
            saved.layout.path = os.path.relpath(path.resolve(), target.parent.resolve())
    if value is None:
        return saved, layout, None
    if value.configuration:
        project_from_dict(value.configuration)
        if value.configuration_hash and configuration_hash(value.configuration) != value.configuration_hash:
            raise SimulationError("Result configuration content does not match its recorded hash")
        recorded = value.physics_hash or physics_hash(value.configuration)
        if recorded != physics_hash(value.configuration) or recorded != physics_hash(saved):
            raise SimulationError("Results belong to a different physical setup; save without stale results")
    _validate_result_arrays(value.mesh, value.fields, value.converged)
    _validate_local_fields(value)
    validate_history(value, saved)
    mesh = {"points": collector.array(value.mesh.points), "triangles": collector.array(value.mesh.triangles),
        "statistics": collector.metadata(value.mesh.statistics),
        **{name: {key: collector.array(array) for key, array in getattr(value.mesh, name).items()} for name in _MESH_GROUPS}}
    transient = None
    if value.transient is not None:
        transient = {"times": collector.array(value.transient.times), "rows": collector.metadata(value.transient.rows),
            "completed": value.transient.completed,
            "frames": [{"metadata": _result_metadata(frame, collector), **_groups(frame, collector)}
                       for frame in value.transient.snapshots]}
    descriptor = {"run_codec": 1, "project": saved.to_dict(), "layout": layout, "mesh": mesh,
                  "metadata": _result_metadata(value, collector), **_groups(value, collector), "transient": transient}
    return saved, layout, collector.json(descriptor)


class ArchiveV3Reader:
    """Metadata-first object graph; explicit array reads have independent ZIP streams.

    Generation pinning belongs to the caller. ResultStore supplies an immutable
    private disk copy; eager APIs supply ProjectInspection's selected bytes.
    """
    def __init__(self, path_or_bytes, *, source_path=None, policy=None):
        self._data = path_or_bytes if isinstance(path_or_bytes, bytes) else None
        self.path = None if self._data is not None else Path(path_or_bytes)
        self.source = Path(source_path) if source_path is not None else (self.path or Path("project.etlayout"))
        self.policy = policy or ExternalResourcePolicy()
        if self.path is not None:
            from etlayout.io.resources import _canonical_checked
            self.path = _canonical_checked(self.path.absolute(), network=self.policy.allow_network)
        self.closed = False
        self._json_objects, self._index, self._records, self._errors = {}, {}, {}, {}
        self._object_errors = {}
        self._checked_descriptors = {}
        self._catalog_metadata = {}
        try:
            self._inspect()
        except (zipfile.BadZipFile, KeyError, ValueError, TypeError, OverflowError, OSError, EOFError, RecursionError) as error:
            if isinstance(error, SimulationError):
                raise
            raise SimulationError(f"Invalid content-addressed archive: {error}") from error

    def _zip(self):
        if self.closed:
            raise SimulationError("Archive reader is closed")
        return zipfile.ZipFile(io.BytesIO(self._data) if self._data is not None else self.path)

    def _read(self, name, cancel=None):
        return b"".join(self._member_chunks(name, cancel))

    def _member_chunks(self, name, cancel=None):
        with self._zip() as archive, archive.open(name) as stream:
            remaining = archive.getinfo(name).file_size
            while remaining:
                _checkpoint(cancel)
                chunk = stream.read(min(1024 * 1024, remaining))
                if not chunk:
                    raise SimulationError("Truncated archive object")
                remaining -= len(chunk)
                yield chunk
            if stream.read(1):
                raise SimulationError("Archive object length mismatch")

    def iter_object_chunks(self, digest, *, cancel=None):
        record = self._index.get(digest)
        if record is None:
            raise SimulationError("Missing archive-local object; the selected run is unavailable")
        calculated, size = hashlib.sha256(), 0
        for chunk in self._member_chunks(self.object_name(digest), cancel):
            calculated.update(chunk)
            size += len(chunk)
            yield chunk
        if size != record["bytes"] or calculated.hexdigest() != digest:
            raise SimulationError("Archive object hash/length mismatch; the selected run is unavailable")

    def _object_bytes(self, digest, cancel=None):
        try:
            return b"".join(self.iter_object_chunks(digest, cancel=cancel))
        except (zipfile.BadZipFile, KeyError, OSError, RuntimeError) as error:
            if isinstance(error, SimulationError):
                raise
            raise SimulationError("Cannot read/verify the archive object; the selected run is unavailable") from error

    def object_name(self, digest):
        return "objects/sha256/" + digest + self._index[digest]["suffix"]

    def _inspect(self):
        byte_limit, metadata_limit, entry_limit = _limits()
        with self._zip() as archive:
            members = archive.infolist()
            names = [item.filename for item in members]
            if len(members) > entry_limit or len(set(names)) != len(names):
                raise SimulationError("Archive duplicate member names or entry budget exceeded")
            total = sum(item.file_size for item in members)
            if total > byte_limit:
                raise SimulationError("Unique archive objects exceed the decompressed byte budget")
            for item in members:
                name = item.filename
                if ("\\" in name or ":" in name or ".." in PurePosixPath(name).parts or PurePosixPath(name).is_absolute()
                        or item.is_dir() or item.flag_bits & 1 or item.compress_type not in (zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED)
                        or (item.external_attr >> 16) & 0o170000 == 0o120000):
                    raise SimulationError("Unsafe archive member name or symbolic link")
                if name.endswith(".json") and item.file_size > metadata_limit:
                    raise SimulationError("Archive metadata exceeds its byte budget")
            lengths = {item.filename: item.file_size for item in members}
            compression = {item.filename: item.compress_type for item in members}
        self.metadata = _parse(self._read("project.json"))
        root = self.metadata
        if (not isinstance(root, dict) or root.get("format") != "etlayout" or type(root.get("schema_version")) is not int
                or root["schema_version"] != 3 or root.get("object_codec") != 1):
            raise SimulationError("Unsupported archive format/object codec; upgrade the application")
        from etlayout.io.project import project_from_dict
        self.project = project_from_dict(root["project"])
        self.project._archive_format = 3
        restore_document_id(self.project, root.get("document_id"), self.source)
        index = root.get("objects")
        if not isinstance(index, dict) or len(index) + 2 > entry_limit:
            raise SimulationError("Invalid object index or entry budget")
        for digest, record in index.items():
            if not isinstance(digest, str) or not _DIGEST.fullmatch(digest) or not isinstance(record, dict):
                raise SimulationError("Invalid archive-local object address")
            kind, suffix = record.get("kind"), record.get("suffix")
            if (kind, suffix) not in {("array", ".npy"), ("json", ".json"), ("layout", ".gds"),
                                     ("layout", ".gdsii"), ("layout", ".oas"), ("layout", ".oasis")}:
                raise SimulationError("Unsupported object kind/codec; upgrade the application")
            if record.get("compression") != "deflate":
                raise SimulationError("Unsupported object compression codec")
            size = record.get("bytes")
            if type(size) is not int or size < 0 or size > byte_limit:
                raise SimulationError("Invalid object length or byte budget")
            refs = record.get("references")
            if (not isinstance(refs, list) or len(refs) > entry_limit
                    or any(not isinstance(ref, str) or not _DIGEST.fullmatch(ref) for ref in refs)
                    or len(set(refs)) != len(refs)):
                raise SimulationError("Invalid object dependency references")
            if kind != "json" and refs:
                raise SimulationError("Only JSON objects can reference archive objects")
            if kind == "array":
                shape, dtype = record.get("shape"), record.get("dtype")
                if (not isinstance(shape, list) or len(shape) > 32 or any(type(part) is not int or part < 0 for part in shape)
                        or not isinstance(dtype, str)):
                    raise SimulationError("Invalid declared NumPy shape/dtype")
                dtype = np.dtype(dtype)
                if dtype.hasobject or dtype.fields is not None or dtype.itemsize == 0 or dtype.str != record["dtype"]:
                    raise SimulationError("Unsafe or noncanonical NumPy dtype")
                nbytes = math.prod(shape) * dtype.itemsize
                if type(record.get("nbytes")) is not int or record["nbytes"] != nbytes or nbytes > byte_limit or size < nbytes + 12:
                    raise SimulationError("Declared NumPy payload exceeds its shape/byte budget")
            self._index[digest] = record
        expected = {"project.json", "runs.json", *(self.object_name(digest) for digest in index)}
        if set(names) - expected:
            raise SimulationError("Unindexed archive member")
        for digest, record in index.items():
            try:
                if self.object_name(digest) in lengths and (lengths[self.object_name(digest)] != record["bytes"]
                        or compression[self.object_name(digest)] != zipfile.ZIP_DEFLATED):
                    raise SimulationError("Indexed object length/compression disagrees with its ZIP member")
                if record["kind"] == "json" and self.object_name(digest) in lengths:
                    data = _parse(self._object_bytes(digest))
                    if sorted(set(_references(data))) != sorted(record["references"]):
                        raise SimulationError("Object graph references disagree with hashed metadata")
                    self._json_objects[digest] = data
            except SimulationError as error:
                self._object_errors[digest] = str(error)
        self.runs_metadata = _parse(self._read("runs.json"))
        runs = self.runs_metadata
        if (not isinstance(runs, dict) or runs.get("record_codec") != 1 or not isinstance(runs.get("records"), list)
                or len(runs["records"]) > entry_limit):
            raise SimulationError("Unsupported or invalid run catalog")
        self.current_run_id = runs.get("current_run")
        if self.current_run_id is not None and not isinstance(self.current_run_id, str):
            raise SimulationError("Invalid current run identity")
        roots = []
        for record in runs["records"]:
            if not isinstance(record, dict) or set(record) != {"id", "descriptor", "summary"}:
                raise SimulationError("Invalid run record")
            identity = record["id"]
            try:
                if str(uuid.UUID(identity)) != identity:
                    raise ValueError()
            except (ValueError, AttributeError, TypeError) as error:
                raise SimulationError("Invalid storage record identity") from error
            if identity in self._records or not isinstance(record["summary"], dict):
                raise SimulationError("Duplicate run identity or invalid summary")
            references = list(_references(record["descriptor"]))
            if len(references) != 1 or set(record["descriptor"]) != {"$ref"}:
                raise SimulationError("Invalid run descriptor reference")
            self._records[identity] = record
            roots.extend(references)
        if self.current_run_id is not None and self.current_run_id not in self._records:
            raise SimulationError("Current run reference is missing")
        embedded = root.get("embedded_layout")
        if embedded is not None:
            if not isinstance(embedded, str) or embedded not in expected or not embedded.startswith("objects/sha256/"):
                raise SimulationError("Invalid embedded source declaration")
            digest = embedded.rsplit("/", 1)[-1].split(".", 1)[0]
            if index.get(digest, {}).get("kind") != "layout" or self.project.layout.sha256 != digest:
                raise SimulationError("Embedded layout/raw source identity mismatch")
            roots.append(digest)
        active, seen = set(), set()
        def visit(digest, depth=0):
            if depth > 16:
                raise SimulationError("Object graph exceeds its nesting budget")
            if digest in active:
                raise SimulationError("Cyclic archive object graph")
            if digest in seen or digest not in index:
                return
            active.add(digest)
            for ref in index[digest]["references"]:
                visit(ref, depth + 1)
            active.remove(digest)
            seen.add(digest)
        for digest in roots:
            visit(digest)
        if set(index) - seen:
            raise SimulationError("Unreachable objects in archive manifest")
        logical_bytes = lengths["project.json"] + lengths["runs.json"]
        logical_entries = 2
        def cost(digest, depth=0):
            nonlocal logical_bytes, logical_entries
            if depth > 16:
                raise SimulationError("Object graph exceeds its nesting budget")
            if digest not in index or self.object_name(digest) not in lengths:
                raise SimulationError("Missing archive-local object")
            if digest in self._object_errors:
                raise SimulationError(self._object_errors[digest])
            record = index[digest]
            logical_bytes += record["bytes"]
            logical_entries += 1
            if logical_bytes > byte_limit or logical_entries > entry_limit:
                raise SimulationError("Logical materialization exceeds its byte/entry budget")
            if record["kind"] == "json":
                for ref in _references(self._json_objects[digest]):
                    cost(ref, depth + 1)
        for identity, record in self._records.items():
            try:
                cost(record["descriptor"]["$ref"])
                self._descriptor(identity)
            except SimulationError as error:
                if "budget" in str(error):
                    raise
                self._errors[identity] = str(error)
        if embedded:
            cost(embedded.rsplit("/", 1)[-1].split(".", 1)[0])
        self.size = {"uncompressed_bytes": total, "logical_bytes": logical_bytes,
            "metadata_bytes": sum(size for name, size in lengths.items() if name.endswith(".json")),
            "entries": len(members), "logical_entries": logical_entries}
        output = []
        for identity, record in self._records.items():
            details = deepcopy(record)
            details["available"], details["error"] = identity not in self._errors, self._errors.get(identity, "")
            details["estimated_bytes"] = sum(self.array_info(digest)["nbytes"] for digest in self.required_arrays(identity)) if details["available"] else 0
            output.append(details)
        self.records = tuple(output)

    def _check_ref(self, reference, kind):
        if not isinstance(reference, dict) or set(reference) != {"$ref"}:
            raise SimulationError("Invalid object reference structure")
        digest = reference["$ref"]
        if not isinstance(digest, str) or self._index.get(digest, {}).get("kind") != kind:
            raise SimulationError("Missing object or wrong object kind in run support")
        if digest in self._object_errors:
            raise SimulationError(self._object_errors[digest])

    def _check_fields(self, descriptor):
        self._check_ref(descriptor["metadata"], "json")
        for group in _GROUPS:
            data = descriptor[group]
            if not isinstance(data, dict):
                raise SimulationError("Invalid field support mapping")
            for owner, values in data.items():
                if not isinstance(owner, str) or not isinstance(values, dict):
                    raise SimulationError("Invalid field support owner")
                for name, reference in values.items():
                    if not isinstance(name, str):
                        raise SimulationError("Invalid field quantity name")
                    self._check_ref(reference, "array")
        metadata = self._metadata(descriptor["metadata"], _CATALOG, None)
        if not isinstance(metadata, dict) or set(metadata) != set(_META_FIELDS):
            raise SimulationError("Incomplete result metadata")
        if (type(metadata["converged"]) is not bool or type(metadata["field_version"]) is not int
                or metadata["field_version"] not in (1, 2) or not isinstance(metadata["diagnostics"], list)
                or any(not isinstance(item, Diagnostic) for item in metadata["diagnostics"])):
            raise SimulationError("Invalid stored result status or diagnostics")
        from etlayout.io.project import configuration_hash, physics_hash, project_from_dict
        if metadata["configuration"]:
            project_from_dict(metadata["configuration"])
            if metadata["configuration_hash"] and configuration_hash(metadata["configuration"]) != metadata["configuration_hash"]:
                raise SimulationError("Stored configuration content does not match its hash")
            if metadata["physics_hash"] and physics_hash(metadata["configuration"], resolve_layout=False) != metadata["physics_hash"]:
                raise SimulationError("Stored physics hash does not match its configuration")
        self._validate_metadata(metadata)
        return metadata

    @staticmethod
    def _validate_metadata(metadata):
        for name in ("metrics", "terminals", "configuration", "execution", "analysis"):
            if not isinstance(metadata.get(name), dict):
                raise SimulationError(f"Invalid result {name}: an object is required")
        if not isinstance(metadata.get("iterations"), list) or any(not isinstance(row, dict) for row in metadata["iterations"]):
            raise SimulationError("Invalid result iterations")
        elapsed = metadata.get("elapsed_seconds")
        if type(elapsed) not in (int, float) or not math.isfinite(elapsed) or elapsed < 0:
            raise SimulationError("Invalid result elapsed time")
        if not isinstance(metadata.get("solver_version"), str):
            raise SimulationError("Invalid result software version")
        for name in ("physics_hash", "configuration_hash"):
            value = metadata.get(name)
            if not isinstance(value, str) or value and not _DIGEST.fullmatch(value):
                raise SimulationError(f"Invalid result {name}")
        from etlayout.io.project import from_dict
        for diagnostic in metadata["diagnostics"]:
            from_dict(Diagnostic, {item.name: getattr(diagnostic, item.name) for item in fields(Diagnostic)})

    def _descriptor(self, identity):
        record = self._records.get(identity)
        if record is None:
            raise SimulationError("Unknown storage run identity")
        digest = record["descriptor"]["$ref"]
        if digest in self._checked_descriptors:
            value, details = self._checked_descriptors[digest]
            self._check_summary(record["summary"], details, value)
            return value
        value = self._json_objects.get(digest)
        if not isinstance(value, dict) or value.get("run_codec") != 1:
            raise SimulationError("Missing or unsupported run descriptor")
        if set(value) != {"run_codec", "project", "layout", "mesh", "metadata", *_GROUPS, "transient"} or not isinstance(value["mesh"], dict):
            raise SimulationError("Invalid run descriptor structure")
        from etlayout.io.project import project_from_dict
        project_from_dict(value["project"])
        if set(value["mesh"]) != {"points", "triangles", "statistics", *_MESH_GROUPS}:
            raise SimulationError("Incomplete stored mesh descriptor")
        for name in ("points", "triangles"):
            self._check_ref(value["mesh"][name], "array")
        self._check_ref(value["mesh"]["statistics"], "json")
        for group in _MESH_GROUPS:
            if not isinstance(value["mesh"][group], dict):
                raise SimulationError("Invalid mesh support mapping")
            for name, reference in value["mesh"][group].items():
                if not isinstance(name, str):
                    raise SimulationError("Invalid mesh support name")
                self._check_ref(reference, "array")
        if not isinstance(self._metadata(value["mesh"]["statistics"], _CATALOG, None), dict):
            raise SimulationError("Invalid stored mesh statistics")
        if value["layout"] is not None:
            self._check_ref(value["layout"], "layout")
        details = self._check_fields(value)
        transient = value["transient"]
        if transient is not None:
            if (not isinstance(transient, dict) or set(transient) != {"times", "rows", "completed", "frames"}
                    or type(transient["completed"]) is not bool or not isinstance(transient["frames"], list)
                    or not 0 < len(transient["frames"]) <= 10000
                    or any(not isinstance(frame, dict) or set(frame) != {"metadata", *_GROUPS} for frame in transient["frames"])):
                raise SimulationError("Invalid stored transient descriptor")
            self._check_ref(transient["times"], "array")
            self._check_ref(transient["rows"], "json")
            time_info = self.array_info(transient["times"]["$ref"])
            if time_info["shape"] != [len(transient["frames"])] or np.dtype(time_info["dtype"]).kind not in "fiu":
                raise SimulationError("Stored transient time support disagrees with its frame count")
            for frame in transient["frames"]:
                self._check_fields(frame)
        self._check_summary(record["summary"], details, value)
        self._checked_descriptors[digest] = (value, details)
        return value

    def _check_summary(self, summary, details, descriptor):
        from types import SimpleNamespace
        transient = descriptor["transient"]
        history = None
        if transient is not None:
            frame_metadata = [self._metadata(frame["metadata"], _CATALOG, None) for frame in transient["frames"]]
            times = summary.get("times_s")
            if (not isinstance(times, list) or len(times) != len(frame_metadata)
                    or any(type(time) not in (int, float) or not math.isfinite(time) for time in times)):
                raise SimulationError("Invalid stored transient catalog time metadata")
            rows_object = transient["rows"]["$ref"]
            if self._index[rows_object]["references"]:
                raise SimulationError("Transient step rows must contain plain finite JSON values")
            rows = self._metadata(transient["rows"], _CATALOG, None)
            history = TransientHistory(times=np.asarray(times), rows=rows, completed=transient["completed"])
            from etlayout.io.project import project_from_dict
            from etlayout.io.transient import _validate_times
            _, tolerance = _validate_times(history, SimpleNamespace(metrics=details["metrics"], converged=details["converged"]),
                                           project_from_dict(descriptor["project"]), len(times))
            for time, frame in zip(times, frame_metadata, strict=True):
                recorded = frame["metrics"].get("time_s")
                if type(recorded) not in (int, float) or not math.isfinite(recorded) or abs(recorded - time) > tolerance:
                    raise SimulationError("Stored transient frame time disagrees with its index")
        value = SimpleNamespace(**details, mesh=SimpleNamespace(statistics=self._metadata(descriptor["mesh"]["statistics"], _CATALOG, None)),
                                transient=history, **{name: descriptor[name] for name in _GROUPS})
        if summary != _summary(value):
            raise SimulationError("Run catalog summary disagrees with its typed metadata")

    def required_arrays(self, run_id, index=None):
        if run_id in self._errors:
            raise SimulationError(self._errors[run_id])
        descriptor = self._descriptor(run_id)
        roots = [descriptor]
        if index is not None:
            transient = descriptor["transient"]
            if transient is None or type(index) is not int or not 0 <= index < len(transient["frames"]):
                raise SimulationError("Transient frame index is unavailable")
            roots = [descriptor["mesh"], descriptor["metadata"], transient["times"], transient["rows"], transient["frames"][index]]
        seen, arrays = set(), set()
        def visit(digest):
            if digest in seen:
                return
            seen.add(digest)
            record = self._index[digest]
            if record["kind"] == "array":
                arrays.add(digest)
            for reference in record["references"]:
                visit(reference)
        for root in roots:
            for digest in _references(root):
                visit(digest)
        return tuple(sorted(arrays))

    def array_info(self, digest):
        record = self._index.get(digest)
        if record is None or record["kind"] != "array":
            raise SimulationError("Expected a numeric array object")
        return {key: deepcopy(record[key]) for key in ("nbytes", "dtype", "shape")}

    def read_array(self, digest, *, cancel=None):
        info = self.array_info(digest)
        raw = self._object_bytes(digest, cancel)
        stream = io.BytesIO(raw)
        try:
            if np.lib.format.read_magic(stream) != (2, 0):
                raise SimulationError("Unsupported canonical NPY object version")
            shape, fortran, dtype = np.lib.format.read_array_header_2_0(stream, max_header_size=10000)
            if (fortran or list(shape) != info["shape"] or dtype.str != info["dtype"] or dtype.hasobject
                    or dtype.fields is not None or len(raw) - stream.tell() != info["nbytes"]):
                raise SimulationError("Stored NPY header disagrees with the safe object manifest")
            # Cache accounting counts the owned NumPy payload exactly. Keeping
            # the NPY header in the bytes base would retain uncounted storage.
            payload = raw[stream.tell():]
            return np.frombuffer(payload, dtype=dtype, count=math.prod(shape)).reshape(shape)
        except (ValueError, EOFError, TypeError, OverflowError) as error:
            raise SimulationError("Invalid stored NPY object header/payload") from error

    def _array(self, reference, loader, cancel):
        refs = list(_references(reference))
        if not isinstance(reference, dict) or set(reference) != {"$ref"} or len(refs) != 1:
            raise SimulationError("Invalid array object reference")
        digest = refs[0]
        self.array_info(digest)
        def load():
            return self.read_array(digest, cancel=cancel)
        return load().copy() if loader is None else loader(digest, load)

    def _metadata(self, reference, loader, cancel):
        refs = list(_references(reference))
        if len(refs) != 1:
            raise SimulationError("Invalid typed metadata reference")
        digest = refs[0]
        if loader is _CATALOG and digest in self._catalog_metadata:
            return self._catalog_metadata[digest]
        value = self._json_objects.get(digest)
        if not isinstance(value, dict) or set(value) != {"typed_codec", "value"} or value["typed_codec"] != 1:
            raise SimulationError("Unsupported typed metadata object codec")
        decoded = _untyped(value["value"], self, loader, cancel)
        if loader is _CATALOG:
            self._catalog_metadata[digest] = decoded
        return decoded

    def resolve_project(self, *, resolve_sources=True):
        return self._setup(self.project.to_dict(), self.metadata.get("embedded_layout"), resolve_sources=resolve_sources)

    def _setup(self, data, embedded, *, resolve_sources=True, key="layout"):
        from etlayout.io.project import file_hash, project_from_dict
        project = project_from_dict(data)
        project._archive_format = 3
        restore_document_id(project, self.metadata.get("document_id"), self.source)
        if not resolve_sources:
            return project
        if embedded:
            digest = embedded.rsplit("/", 1)[-1].split(".", 1)[0]
            record = self._index.get(digest)
            if record is None or record["kind"] != "layout" or project.layout.sha256 != digest:
                raise SimulationError("Embedded layout/source hash mismatch")
            raw = self._object_bytes(digest)
            cache = Path(tempfile.gettempdir()) / "etlayout-layouts"
            cache.mkdir(parents=True, exist_ok=True)
            cached = cache / (digest + record["suffix"])
            if not cached.exists() or file_hash(cached) != digest:
                fd, temporary = tempfile.mkstemp(dir=cache, prefix=digest + ".", suffix=".tmp")
                try:
                    with os.fdopen(fd, "wb") as stream:
                        stream.write(raw)
                    os.replace(temporary, cached)
                finally:
                    Path(temporary).unlink(missing_ok=True)
            project.layout.path = str(cached)
        elif project.layout.path:
            inspection = ProjectInspection(self.source, project, (classify_resource(project.layout.path, self.source,
                key=key, expected_sha256=project.layout.sha256),))
            location, digest = resolve_layout(inspection, self.policy)
            project.layout.path, project.layout.sha256 = str(location), digest
        return project

    def _result(self, descriptor, mesh, loader, cancel):
        metadata = self._metadata(descriptor["metadata"], loader, cancel)
        if not isinstance(metadata, dict) or set(metadata) != set(_META_FIELDS):
            raise SimulationError("Incomplete result metadata")
        if type(metadata["converged"]) is not bool or type(metadata["field_version"]) is not int:
            raise SimulationError("Invalid typed result status")
        groups = {}
        for group in _GROUPS:
            data = descriptor[group]
            if not isinstance(data, dict) or any(not isinstance(values, dict) for values in data.values()):
                raise SimulationError("Invalid field binding metadata")
            groups[group] = {owner: {name: self._array(reference, loader, cancel) for name, reference in values.items()}
                             for owner, values in data.items()}
        self._validate_metadata(metadata)
        result = SimulationResult(mesh=mesh, **groups, **metadata)
        from etlayout.io.project import _validate_local_fields, _validate_result_arrays
        _validate_result_arrays(mesh, result.fields, result.converged)
        _validate_local_fields(result)
        return result

    def _mesh(self, descriptor, loader, cancel):
        groups = {name: {key: self._array(reference, loader, cancel) for key, reference in descriptor[name].items()}
                  for name in _MESH_GROUPS}
        return PlanarMesh(self._array(descriptor["points"], loader, cancel), self._array(descriptor["triangles"], loader, cancel),
                          statistics=self._metadata(descriptor["statistics"], loader, cancel), **groups)

    def materialize(self, run_id, *, array_loader=None, cancel=None, resolve_sources=True):
        _checkpoint(cancel)
        self.required_arrays(run_id)
        descriptor = self._descriptor(run_id)
        embedded = self.object_name(descriptor["layout"]["$ref"]) if descriptor["layout"] else None
        project = self._setup(descriptor["project"], embedded, resolve_sources=resolve_sources, key=run_id + ":layout")
        mesh = self._mesh(descriptor["mesh"], array_loader, cancel)
        result = self._result(descriptor, mesh, array_loader, cancel)
        from etlayout.io.project import (
            configuration_hash,
            diagnose_legacy_fields,
            physics_hash,
            project_from_dict,
        )
        if result.configuration:
            project_from_dict(result.configuration)
            if result.configuration_hash and configuration_hash(result.configuration) != result.configuration_hash:
                raise SimulationError("Stored configuration content does not match its recorded hash")
            physical = physics_hash(result.configuration, resolve_layout=False)
            if result.physics_hash and physical != result.physics_hash:
                raise SimulationError("Stored physics hash does not match its original configuration")
            if physical != physics_hash(project, resolve_layout=False):
                raise SimulationError("Stored result configuration differs from its run setup")
        if not result.physics_hash:
            result.physics_hash = physics_hash(result.configuration or project, resolve_layout=False)
        if resolve_sources and result.configuration.get("layout", {}).get("sha256"):
            result.configuration["layout"]["path"] = project.layout.path
        transient = descriptor["transient"]
        if transient is not None:
            result.transient = TransientHistory(self._array(transient["times"], array_loader, cancel),
                [self._result(frame, mesh, array_loader, cancel) for frame in transient["frames"]],
                self._metadata(transient["rows"], array_loader, cancel), transient["completed"])
            from etlayout.io.transient import validate_history
            validate_history(result, project)
            if not np.array_equal(result.transient.times, self._records[run_id]["summary"]["times_s"]):
                raise SimulationError("Stored transient times disagree with the catalog")
        elif any(key in result.metrics for key in ("transient_completed", "transient_steps", "transient_snapshot_count", "transient_time")):
            raise SimulationError("Stored transient run is missing its complete history")
        diagnose_legacy_fields(result)
        result._storage_run_id = run_id
        result._storage_descriptor = self._records[run_id]["descriptor"]["$ref"]
        return result

    def transient_metadata(self, run_id):
        """Explicit row metadata access; no mesh/frame array is loaded."""
        if run_id in self._errors:
            raise SimulationError(self._errors[run_id])
        descriptor = self._descriptor(run_id)
        transient = descriptor["transient"]
        if transient is None:
            return {}
        # Accepted step rows are required finite JSON by validate_history. Any
        # array-valued row is invalid, so the metadata-only sentinel is rejected.
        rows = self._metadata(transient["rows"], _CATALOG, None)
        try:
            _json(rows)
        except SimulationError as error:
            raise SimulationError("Transient rows must contain finite plain values") from error
        return {"times_s": deepcopy(self._records[run_id]["summary"].get("times_s", [])),
                "rows": deepcopy(rows), "completed": transient["completed"]}

    def frame(self, run_id, index, *, array_loader=None, cancel=None):
        self.required_arrays(run_id, index)
        descriptor = self._descriptor(run_id)
        embedded = self.object_name(descriptor["layout"]["$ref"]) if descriptor["layout"] else None
        project = self._setup(descriptor["project"], embedded, key=run_id + ":layout")
        transient = descriptor["transient"]
        times = self._array(transient["times"], array_loader, cancel)
        rows = self._metadata(transient["rows"], array_loader, cancel)
        metadata = self._metadata(descriptor["metadata"], array_loader, cancel)
        from types import SimpleNamespace

        from etlayout.io.transient import _validate_times
        _, tolerance = _validate_times(TransientHistory(times=times, rows=rows, completed=transient["completed"]),
                        SimpleNamespace(metrics=metadata["metrics"], converged=metadata["converged"]),
                        project, len(transient["frames"]))
        mesh = self._mesh(descriptor["mesh"], array_loader, cancel)
        result = self._result(transient["frames"][index], mesh, array_loader, cancel)
        if not np.array_equal(times, self._records[run_id]["summary"]["times_s"]):
            raise SimulationError("Stored transient times disagree with the catalog")
        recorded = result.metrics.get("time_s")
        if type(recorded) not in (int, float) or not math.isfinite(recorded) or abs(recorded - times[index]) > tolerance:
            raise SimulationError("Transient frame time disagrees with its stored index")
        result._stored_frame_time = times[index]
        return result

    def verify_all(self, *, cancel=None, materialize=True):
        for digest, record in self._index.items():
            _checkpoint(cancel)
            if record["kind"] == "array":
                self.read_array(digest, cancel=cancel)
            else:
                self._object_bytes(digest, cancel)
        if materialize:
            for record in self.records:
                self.materialize(record["id"], cancel=cancel, resolve_sources=False)

    def close(self):
        self.closed, self._data = True, None
        self._json_objects.clear()
        self._catalog_metadata.clear()
        self._checked_descriptors.clear()

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.close()


@dataclass(frozen=True)
class StoredRunReference:
    """Explicit save-worker transport; its owner pins the source generation."""
    run_id: str
    generation: str
    reader: object
    _token: object

    def require_open(self):
        if self._token.closed:
            raise SimulationError("The stored run's source-generation lease is closed")

    def __deepcopy__(self, memo):
        return self


def _stored_reference(value):
    return isinstance(value, StoredRunReference)


def _import_run(collector, reference, target, *, embed_layout=True):
    reference.require_open()
    reader, run_id = reference.reader, reference.run_id
    reader.required_arrays(run_id)
    root = reader._records[run_id]["descriptor"]
    seen = set()
    def transfer(digest):
        if digest in seen:
            return
        seen.add(digest)
        record = deepcopy(reader._index[digest])
        for child in record["references"]:
            transfer(child)
        collector._add(digest, record, (reader, digest))
    descriptor = deepcopy(reader._descriptor(run_id))
    # Relative external locations belong to the source generation's root. Resolve
    # them through its explicit policy before rebasing or portable embedding.
    if descriptor["layout"] is None and descriptor["project"]["layout"]["path"]:
        setup = reader._setup(descriptor["project"], None, key=run_id + ":layout")
        for digest in _references(descriptor):
            transfer(digest)
        if embed_layout:
            layout = collector.layout(setup.layout.path, setup.layout.sha256)
            descriptor["layout"] = layout
            descriptor["project"]["layout"]["path"] = collector.name(layout["$ref"])
        else:
            descriptor["project"]["layout"]["path"] = os.path.relpath(Path(setup.layout.path).resolve(), target.parent.resolve())
            collector.sources[Path(setup.layout.path)] = setup.layout.sha256
        descriptor["project"]["layout"]["sha256"] = setup.layout.sha256
        root = collector.json(descriptor)
    else:
        transfer(root["$ref"])
    return deepcopy(root), deepcopy(reader._records[run_id]["summary"])


def save_archive(project, path, result=None, *, embed_layout=True, history=None, progress=None, cancel=None, commit=None):
    """Write schema 3 only through the explicit archive_format=3 option."""
    from etlayout.io.project import physics_hash, project_from_dict
    from etlayout.io.saving import SaveSnapshot
    snapshot = SaveSnapshot.capture(project, result, history)
    project, result, history = snapshot.project, snapshot.result, snapshot.history
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    collector = _Collector(cancel)
    current_stored = result is not None and _stored_reference(result)
    saved, embedded, current = _describe(project, None if current_stored else result, collector, target, embed_layout)
    current_summary = None
    if current_stored:
        current, current_summary = _import_run(collector, result, target, embed_layout=embed_layout)
        recorded = result.reader._descriptor(result.run_id)["project"]
        if physics_hash(project_from_dict(recorded), resolve_layout=False) != physics_hash(saved, resolve_layout=False):
            raise SimulationError("Stored run belongs to a different physical setup; save without stale results")
    records, current_id, used_ids = [], None, set()
    for index, value in enumerate((result, *history)):
        _checkpoint(cancel)
        if value is None:
            continue
        if index == 0:
            reference = current
            summary = current_summary if current_stored else _summary(value)
        elif _stored_reference(value):
            reference, summary = _import_run(collector, value, target)
        else:
            if not isinstance(value, SimulationResult) or not value.configuration:
                raise SimulationError("Saved history requires recorded run configurations")
            _, _, reference = _describe(project_from_dict(value.configuration), value, collector, target, True)
            summary = _summary(value)
        identity = value.run_id if _stored_reference(value) else getattr(value, "_storage_run_id", None)
        if identity is not None:
            try:
                identity = str(uuid.UUID(identity))
            except (ValueError, TypeError, AttributeError) as error:
                raise SimulationError("Invalid runtime storage-record identity") from error
        if identity is None or identity in used_ids:
            identity = str(uuid.uuid4())
            while identity in used_ids:
                identity = str(uuid.uuid4())
        used_ids.add(identity)
        records.append({"id": identity, "descriptor": reference, "summary": summary})
        if index == 0:
            current_id = identity
    root = {"format": "etlayout", "schema_version": 3, "object_codec": 1, "application_version": __version__,
        "document_id": snapshot.document_id, "project": saved.to_dict(),
        "embedded_layout": collector.name(embedded["$ref"]) if embedded else None, "objects": collector.index}
    runs = {"record_codec": 1, "current_run": current_id, "records": records}
    raw_root, raw_runs = _json(root), _json(runs)
    byte_limit, metadata_limit, entry_limit = _limits()
    if (max(len(raw_root), len(raw_runs)) > metadata_limit or len(collector.index) + 2 > entry_limit
            or len(raw_root) + len(raw_runs) + sum(item["bytes"] for item in collector.index.values()) > byte_limit):
        raise SimulationError("Content-addressed archive exceeds its metadata/unique byte/entry budget")
    fd, temporary = tempfile.mkstemp(prefix=target.name + ".", suffix=".tmp", dir=target.parent)
    os.close(fd)
    try:
        with zipfile.ZipFile(temporary, "w", zipfile.ZIP_DEFLATED, allowZip64=True) as archive:
            archive.writestr("project.json", raw_root)
            archive.writestr("runs.json", raw_runs)
            collector.write(archive, progress)
        _checkpoint(cancel)
        if progress is not None:
            progress(.8, "Verifying object graph and exact stored fields")
        with ArchiveV3Reader(temporary, source_path=target) as reader:
            # Verify physical support one run at a time. Content hashes establish
            # integrity, never scientific validity of an untrusted source archive.
            reader.verify_all(cancel=cancel)
        with Path(temporary).open("r+b") as durable:
            durable.flush()
            os.fsync(durable.fileno())
        _checkpoint(cancel)
        from etlayout.io.project import file_hash
        for source, expected in collector.sources.items():
            _checkpoint(cancel)
            if file_hash(source) != expected:
                raise SimulationError("Source layout changed during saving; the previous project was preserved")
        if commit is None:
            os.replace(temporary, target)
        else:
            commit(temporary, target)
        if progress is not None:
            progress(1., "Project archive committed")
    finally:
        Path(temporary).unlink(missing_ok=True)


class _LegacyHistory:
    def __init__(self, values):
        self.values = tuple(values)

    def __len__(self):
        return len(self.values)

    def __iter__(self):
        for value in self.values:
            if _stored_reference(value):
                value.require_open()
                yield value.reader.materialize(value.run_id)
            else:
                yield value


def materialized_legacy_inputs(result, history):
    """Legacy writer streams retained runs one at a time, on its save worker."""
    if result is not None and _stored_reference(result):
        result.require_open()
        result = result.reader.materialize(result.run_id)
    return result, _LegacyHistory(history) if history else history


def _fingerprint(value):
    """Exact logical-byte/typed fingerprint, normalizing only hashed layout locations."""
    from dataclasses import is_dataclass
    collector = _Collector()
    def normalize(item):
        if is_dataclass(item):
            return {part.name: normalize(getattr(item, part.name)) for part in fields(item)}
        if isinstance(item, dict):
            # Mapping order is not scientific content; field bindings are
            # canonical JSON maps. Sequence order and tuple/list types remain exact.
            ordered = sorted(item.items(), key=lambda pair: _json(_typed(pair[0], collector)))
            result = {key: normalize(child) for key, child in ordered}
            if isinstance(result.get("layout"), dict) and result["layout"].get("sha256"):
                result["layout"]["path"] = ""
            return result
        if isinstance(item, list):
            return [normalize(child) for child in item]
        if isinstance(item, tuple):
            return tuple(normalize(child) for child in item)
        return item
    return hashlib.sha256(_json(_typed(normalize(value), collector))).hexdigest()


def migrate_project(source, destination, *, archive_format=3, external_policy=None, progress=None, cancel=None):
    """Verified source-preserving conversion into a new destination, never in place.

    An exclusive atomic hard-link install is immediately followed by removal of
    the owned temporary name. No persistent shared store or hard-link graph is
    created. Unsupported destination filesystems fail without publishing content.
    """
    from etlayout.io.identity import document_id
    from etlayout.io.project import file_hash, load_project, load_run_history, save_project
    from etlayout.io.resources import inspect_project
    source, destination = Path(source), Path(destination)
    if destination.exists() or destination.is_symlink() or source.resolve() == destination.resolve():
        raise SimulationError("Migration requires a new destination; the source and existing files are preserved")
    if type(archive_format) is not int or archive_format not in (2, 3):
        raise SimulationError("Choose archive format 2 (legacy) or 3 (content-addressed)")
    if destination.suffix.lower() not in {".etl", ".etlayout"}:
        raise SimulationError("Migration destination must be an .etl or .etlayout archive")
    _checkpoint(cancel)
    policy = external_policy or ExternalResourcePolicy()
    inspection = inspect_project(source, allow_network_container=policy.allow_network)
    # All eager operations bind to the exact inspected bytes, not a reopened
    # mutable source path. JSON metadata follows the same explicitly chosen file.
    from etlayout.io.project import resolve_project
    project, result = resolve_project(inspection, external_policy=policy)
    history = load_run_history(source, external_policy=policy, _archive_data=inspection.archive_data)
    source_digest = (hashlib.sha256(inspection.archive_data).hexdigest() if inspection.archive_data is not None
                     else inspection._source_sha256)
    if inspection.metadata.get("schema_version") != 3:
        for index, value in enumerate((result, *history)):
            if value is not None:
                value._storage_run_id = str(uuid.uuid5(uuid.UUID(document_id(project)), f"legacy-storage-v1:{index}"))
    expected = _fingerprint((project, result, history))
    destination.parent.mkdir(parents=True, exist_ok=True)
    fd, candidate = tempfile.mkstemp(prefix=destination.stem + ".migration-", suffix=destination.suffix, dir=destination.parent)
    os.close(fd)
    try:
        save_project(project, candidate, result, history=history, archive_format=archive_format, progress=progress, cancel=cancel)
        _checkpoint(cancel)
        reopened, saved = load_project(candidate, external_policy=policy)
        saved_history = load_run_history(candidate, external_policy=policy)
        if document_id(project) != document_id(reopened) or _fingerprint((reopened, saved, saved_history)) != expected:
            if archive_format == 2:
                raise SimulationError("Legacy schema 2 cannot preserve these exact typed values; use schema 3. The source was preserved")
            raise SimulationError("Migration verification changed typed values or provenance; the source was preserved")
        if file_hash(source) != source_digest:
            raise SimulationError("Source archive changed during migration; the source and destination were preserved")
        _checkpoint(cancel)
        os.link(candidate, destination)  # exclusive destination creation; no overwrite race
        return {"source_schema": inspection.metadata.get("schema_version", 2), "archive_format": archive_format,
                "records": len(history) + int(result is not None), "document_id": document_id(reopened),
                "sha256": file_hash(destination), "verified": True}
    except FileExistsError as error:
        raise SimulationError("Migration destination appeared during verification; it was preserved") from error
    finally:
        Path(candidate).unlink(missing_ok=True)


def verify_legacy_snapshot(path, project, result, history, *, cancel=None):
    """Reject a lossy explicit legacy copy before the writer's atomic commit."""
    from etlayout.io.project import load_project, resolve_project
    from etlayout.io.resources import inspect_project
    inspection = inspect_project(path)
    # Verifying values must not introduce resource grants. The already hashed
    # layout identity suffices; source bytes were CRC/hash checked by the writer.
    metadata_project = deepcopy(inspection.project)
    if metadata_project.layout.path and not metadata_project.layout.sha256:
        raise SimulationError("Exact legacy verification needs a recorded source hash")
    metadata_project.layout.path = ""
    reopened, actual = resolve_project(inspection.for_project(metadata_project))
    if _fingerprint((project, result)) != _fingerprint((reopened, actual)):
        raise SimulationError("Legacy schema 2 cannot preserve these exact typed values; use schema 3. The previous project was preserved")
    with zipfile.ZipFile(path) as archive:
        names = sorted(name for name in archive.namelist() if name.startswith("runs/run_") and name.endswith(".etl"))
        if len(names) != len(history or ()):
            raise SimulationError("Legacy copy changed the retained run count")
        for name, expected in zip(names, history or (), strict=True):
            _checkpoint(cancel)
            _, actual = load_project(path, _archive_data=archive.read(name))
            if _fingerprint(expected) != _fingerprint(actual):
                raise SimulationError("Legacy schema 2 cannot preserve retained run typed values; use schema 3. The previous project was preserved")
