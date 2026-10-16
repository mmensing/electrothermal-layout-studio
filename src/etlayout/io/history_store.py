# SPDX-License-Identifier: MPL-2.0
"""Explicit metadata-first archive access and a bounded immutable array cache.

The legacy readers remain eager. This opt-in API pins a bounded private disk copy
of one selected schema-3 archive; it never reopens a mutable document pathname.
A catalog proves stored metadata, not the integrity of unread numerical fields.
"""
from __future__ import annotations

import hashlib
import os
import tempfile
import threading
import weakref
from collections import OrderedDict
from collections.abc import Mapping
from copy import deepcopy
from dataclasses import dataclass, replace
from pathlib import Path
from types import MappingProxyType

import numpy as np

from etlayout.io.archive_v3 import StoredRunReference
from etlayout.io.resources import ExternalResourcePolicy, ProjectInspection, _canonical_checked
from etlayout.io.saving import _immutable_array
from etlayout.models import SimulationError, SimulationResult

DEFAULT_CACHE_BYTES = 256 * 1024**2
MAX_CACHE_BYTES = 8 * 1024**3


class HistoryReadCancelled(SimulationError):
    """The previously displayed result remains usable."""


class CacheBudgetExceeded(SimulationError):
    """No numerical payload is read until the caller authorizes oversize use."""

    def __init__(self, required_bytes, budget_bytes, pinned_bytes):
        self.required_bytes = required_bytes
        self.budget_bytes = budget_bytes
        self.pinned_bytes = pinned_bytes
        super().__init__(
            f"This selection requires {required_bytes / 1024**2:.2f} MiB of unique arrays "
            f"with {pinned_bytes / 1024**2:.2f} MiB already pinned; the history cache budget "
            f"is {budget_bytes / 1024**2:.2f} MiB. Release another view, increase the local "
            "cache budget, or explicitly authorize temporary over-budget use."
        )


def _freeze(value):
    if isinstance(value, dict):
        return MappingProxyType({key: _freeze(child) for key, child in value.items()})
    if isinstance(value, (tuple, list)):
        return tuple(_freeze(child) for child in value)
    return value


def _thaw(value):
    if isinstance(value, Mapping):
        return {key: _thaw(child) for key, child in value.items()}
    if isinstance(value, tuple):
        return [_thaw(child) for child in value]
    return deepcopy(value)


@dataclass(frozen=True)
class RunRecord:
    """Small immutable metadata; attribute access never loads numerical arrays."""

    run_id: str
    name: str
    mode: str
    configuration: Mapping
    configuration_hash: str
    physics_hash: str
    converged: bool
    elapsed_seconds: float
    solver_version: str
    metrics: Mapping
    diagnostics: tuple
    summary: Mapping
    times: tuple[float, ...]
    transient_metadata: Mapping
    estimated_bytes: int
    available: bool
    error: str
    current: bool
    generation: str

    @property
    def id(self):
        return self.run_id

    def __deepcopy__(self, memo):
        return self

    def configuration_copy(self):
        """Return ordinary model-compatible JSON containers, isolated from the catalog."""
        return _thaw(self.configuration)


@dataclass(frozen=True)
class CacheStats:
    resident_bytes: int
    pinned_bytes: int
    budget_bytes: int
    over_budget_bytes: int
    hits: int
    misses: int
    active_reads: int
    estimated_next_bytes: int
    private_copy_bytes: int
    entries: int
    generation: str
    closed: bool


class _GenerationToken:
    def __init__(self, store):
        self._release = weakref.finalize(self, store._release_generation)

    @property
    def closed(self):
        return not self._release.alive

    def close(self):
        self._release()


class StoredHistoryLease:
    """Pin source objects for a complete archive save after a partial browse."""

    def __init__(self, store, run_ids):
        self._token = _GenerationToken(store)
        self.references = tuple(StoredRunReference(run_id, store.generation, store._reader, self._token)
                                for run_id in run_ids)

    @property
    def closed(self):
        return self._token.closed

    def close(self):
        self._token.close()

    release = close

    def __enter__(self):
        if self.closed:
            raise SimulationError("The stored-history lease is closed")
        return self.references

    def __exit__(self, *args):
        self.close()


class ResultLease:
    """Own a validated result and pin its immutable arrays until explicitly closed."""

    def __init__(self, store, result, keys):
        self.result = result
        self._release = weakref.finalize(self, store._release_keys, keys)

    @property
    def closed(self):
        return not self._release.alive

    def close(self):
        self._release()

    release = close

    def __enter__(self):
        if self.closed:
            raise SimulationError("The historical result lease is closed")
        return self.result

    def __exit__(self, *args):
        self.close()


def _cache_budget(value):
    if type(value) is not int or not 1 <= value <= MAX_CACHE_BYTES:
        raise SimulationError(f"History cache bytes must be an integer from 1 to {MAX_CACHE_BYTES}")
    return value


def _signature(info):
    return info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns


class ResultStore:
    """Closable, archive-generation-local catalog with explicit materialization.

    A private read copy costs up to the selected archive's bounded on-disk size.
    Its filename is never used as the base for external resource permissions.
    Leases keep immutable arrays alive even after the archive is closed; close a
    lease when its view/save/comparison is finished. Cache statistics count owned
    NumPy storage, not process RSS or Python metadata.
    """

    def __init__(self, path, *, external_policy=None, cache_bytes=DEFAULT_CACHE_BYTES,
                 cancel=None, copy_directory=None):
        from etlayout.io.archive_v3 import ArchiveV3Reader
        from etlayout.io.project import MAX_ARCHIVE_BYTES

        self._budget = _cache_budget(cache_bytes)
        self._lock = threading.RLock()
        self._read_lock = threading.Lock()
        self._cancel = threading.Event()
        self._closed = False
        self._cache = OrderedDict()
        self._pins = {}
        self._errors = {}
        self._materialized = {}
        self._hits = self._misses = self._active_reads = self._estimated_next = 0
        self._reader = None
        self._generation_pins = 0
        self._temporary = None
        self._private_copy_bytes = 0
        self.policy = external_policy or ExternalResourcePolicy()
        inspection = path if isinstance(path, ProjectInspection) else None
        inspected_data = None if inspection is None else inspection.archive_data
        expected_generation = None if inspection is None else getattr(inspection, "_archive_sha256", None)
        if inspection is not None:
            if inspection.relinked or inspection.discard_stale_result:
                raise SimulationError("The reviewed setup was relinked or edited; resolve it through the normal "
                                      "project reader before opening a new history catalog")
            if inspected_data is None and not isinstance(expected_generation, str):
                raise SimulationError("History catalog requires the exact inspected archive bytes or a "
                                      "reader-established generation; review the selected archive again")
            if inspected_data is not None and not isinstance(inspected_data, bytes):
                raise SimulationError("Invalid inspected archive byte snapshot")
            self.source = Path(os.path.abspath(inspection.source))
        else:
            self.source = _canonical_checked(Path(os.path.abspath(path)), network=self.policy.allow_network)
        self._temporary = tempfile.TemporaryDirectory(prefix="etlayout-history-", dir=copy_directory)
        self._snapshot_path = Path(self._temporary.name) / "selected.etlayout"
        try:
            digest = hashlib.sha256()
            with self._snapshot_path.open("xb") as pinned:
                def copy_chunk(chunk):
                    self._checkpoint(cancel)
                    self._private_copy_bytes += len(chunk)
                    if self._private_copy_bytes > MAX_ARCHIVE_BYTES:
                        raise SimulationError("Selected history archive exceeds its bounded private-copy budget")
                    digest.update(chunk)
                    pinned.write(chunk)
                if inspected_data is not None:
                    if len(inspected_data) > MAX_ARCHIVE_BYTES:
                        raise SimulationError("Inspected history archive exceeds its private-copy budget")
                    view = memoryview(inspected_data)
                    for offset in range(0, len(view), 1024**2):
                        copy_chunk(view[offset:offset + 1024**2])
                else:
                    selected_path = _canonical_checked(self.source, network=self.policy.allow_network)
                    with selected_path.open("rb") as selected:
                        before = os.fstat(selected.fileno())
                        if before.st_size > MAX_ARCHIVE_BYTES:
                            raise SimulationError("Selected history archive exceeds its bounded private-copy budget")
                        while True:
                            self._checkpoint(cancel)
                            chunk = selected.read(min(1024**2, MAX_ARCHIVE_BYTES - self._private_copy_bytes + 1))
                            if not chunk:
                                break
                            copy_chunk(chunk)
                        if _signature(before) != _signature(os.fstat(selected.fileno())):
                            raise SimulationError("The selected archive changed during inspection; reopen the durable file")
            self.generation = digest.hexdigest()
            if expected_generation is not None and self.generation != expected_generation:
                raise SimulationError("The selected archive generation differs from the reviewed bytes; review it again")
            self._checkpoint(cancel)
            self._reader = ArchiveV3Reader(self._snapshot_path, source_path=self.source, policy=self.policy)
            self.current_run_id = self._reader.current_run_id
            records = []
            for descriptor in self._reader.records:
                summary = descriptor.get("summary", {})
                if not descriptor.get("available", True):
                    # A bad run is isolated, including its untrusted summary's
                    # types. Do not let an invalid elapsed/configuration/time
                    # value break a healthy catalog row or present fake metrics.
                    summary = {"name": "Unavailable run · " + descriptor["id"][:8],
                               "mode": "unavailable", "configuration": {}, "metrics": {}}
                configuration = summary.get("configuration", descriptor.get("configuration", {}))
                run_id = descriptor["id"]
                records.append(RunRecord(
                    run_id, summary.get("name", configuration.get("name", run_id)),
                    summary.get("mode", configuration.get("solver", {}).get("mode", "")),
                    _freeze(deepcopy(configuration)), summary.get("configuration_hash", ""),
                    summary.get("physics_hash", ""), summary.get("converged", False),
                    summary.get("elapsed_seconds", 0.), summary.get("solver_version", ""),
                    _freeze(deepcopy(summary.get("metrics", {}))),
                    _freeze(deepcopy(summary.get("diagnostics", []))), _freeze(deepcopy(summary)),
                    tuple(summary.get("times", summary.get("times_s", ()))),
                    _freeze(deepcopy(summary.get("transient_metadata") or {})),
                    descriptor.get("estimated_bytes", 0),
                    descriptor.get("available", True), descriptor.get("error", ""),
                    run_id == self.current_run_id, self.generation,
                ))
            self._records = tuple(records)
            self._by_id = {item.run_id: item for item in records}
            self._checkpoint(cancel)
        except BaseException as error:
            if self._reader is not None:
                self._reader.close()
            self._temporary.cleanup()
            self._temporary = None
            self._closed = True
            if isinstance(error, OSError):
                raise SimulationError("Cannot create the bounded private history copy; check disk space and access") from error
            raise

    @property
    def document_id(self):
        return self._reader.metadata.get("document_id", "")

    @property
    def archive_format(self):
        return 3

    @property
    def project_metadata(self):
        """An isolated setup, without resolving external paths or any field arrays."""
        return deepcopy(self._reader.project)

    @property
    def records(self):
        with self._lock:
            return tuple(replace(item, available=False, error=self._errors[item.run_id])
                         if item.run_id in self._errors else item for item in self._records)

    @property
    def history(self):
        return tuple(item for item in self.records if not item.current)

    def record(self, run_id):
        return next((item for item in self.records if item.run_id == run_id), None)

    def resolve_project(self):
        """Resolve the stored working setup explicitly under this opening's policy."""
        with self._read_lock:
            self._checkpoint()
            return self._reader.resolve_project()

    def transient_metadata(self, run_id):
        """Read already-validated scalar step summaries without loading fields."""
        with self._read_lock:
            self._checkpoint()
            return _freeze(self._reader.transient_metadata(run_id) or {})

    @property
    def stats(self):
        with self._lock:
            resident = sum(value.nbytes for value in self._cache.values())
            pinned = sum(value.nbytes for key, value in self._cache.items() if self._pins.get(key, 0))
            return CacheStats(resident, pinned, self._budget, max(0, resident - self._budget),
                              self._hits, self._misses, self._active_reads, self._estimated_next,
                              self._private_copy_bytes, len(self._cache), self.generation, self._closed)

    def _checkpoint(self, cancel=None):
        if self._cancel.is_set() or self._closed or (cancel is not None and cancel()):
            raise HistoryReadCancelled("Historical result loading was cancelled; the previous view is unchanged")

    def _evict(self, *, clear=False):
        resident = sum(array.nbytes for array in self._cache.values())
        for key in tuple(self._cache):
            if not clear and resident <= self._budget:
                break
            if not self._pins.get(key, 0):
                resident -= self._cache.pop(key).nbytes

    def clear(self):
        """Release unpinned entries; active views are not invalidated."""
        with self._lock:
            self._evict(clear=True)

    def set_budget(self, cache_bytes):
        with self._lock:
            self._budget = _cache_budget(cache_bytes)
            self._evict()

    def _release_keys(self, keys):
        with self._lock:
            for key in keys:
                count = self._pins.get(key, 0) - 1
                if count > 0:
                    self._pins[key] = count
                else:
                    self._pins.pop(key, None)
            self._evict(clear=self._closed)

    def _prepare(self, run_id, index, allow_oversize):
        record = self._by_id.get(run_id)
        if record is None:
            raise SimulationError("The requested historical run is not in this archive generation")
        if not record.available or run_id in self._errors:
            raise SimulationError(self._errors.get(run_id, record.error) or "This stored run is unavailable")
        keys = tuple(dict.fromkeys(self._reader.required_arrays(run_id, index)))
        sizes = {key: self._reader.array_info(key)["nbytes"] for key in keys}
        with self._lock:
            requested = set(keys)
            pinned_other = sum(array.nbytes for key, array in self._cache.items()
                               if self._pins.get(key, 0) and key not in requested)
            required = sum(sizes.values())
            if required + pinned_other > self._budget and not allow_oversize:
                raise CacheBudgetExceeded(required, self._budget, pinned_other)
            # Reserve the full request before its first allocation. A concurrent
            # clear/budget change cannot remove arrays required by this read.
            for key in keys:
                self._pins[key] = self._pins.get(key, 0) + 1
            projected = sum(array.nbytes for array in self._cache.values())
            projected += sum(size for key, size in sizes.items() if key not in self._cache)
            for key in tuple(self._cache):
                if projected <= self._budget:
                    break
                if not self._pins.get(key, 0):
                    projected -= self._cache.pop(key).nbytes
            self._estimated_next = sum(size for key, size in sizes.items() if key not in self._cache)
        return keys, sizes

    def _request(self, run_id, index, *, allow_oversize=False, cancel=None):
        self._checkpoint(cancel)
        while not self._read_lock.acquire(timeout=.05):
            self._checkpoint(cancel)
        keys = ()
        active = False
        try:
            self._checkpoint(cancel)
            keys, sizes = self._prepare(run_id, index, allow_oversize)
            with self._lock:
                self._active_reads += 1
                active = True

            def load_array(key, loader):
                self._checkpoint(cancel)
                if key not in sizes:
                    raise SimulationError("Stored run attempted an undeclared array allocation")
                with self._lock:
                    if key in self._cache:
                        self._hits += 1
                        self._cache.move_to_end(key)
                        return self._cache[key].view()
                    self._misses += 1
                array = loader()
                self._checkpoint(cancel)
                if not isinstance(array, np.ndarray) or array.nbytes != sizes[key]:
                    raise SimulationError("Stored array allocation does not match its declared byte budget")
                immutable = _immutable_array(array)
                with self._lock:
                    self._cache[key] = immutable
                    self._estimated_next -= sizes[key]
                # Independent descriptors prevent one consumer's shape assignment
                # from changing another binding over the same immutable storage.
                return immutable.view()

            method = self._reader.materialize if index is None else self._reader.frame
            args = (run_id,) if index is None else (run_id, index)
            result = method(*args, array_loader=load_array,
                            cancel=lambda: self._cancel.is_set() or (cancel is not None and cancel()))
            self._checkpoint(cancel)
            if not isinstance(result, SimulationResult):
                raise SimulationError("Archive reader did not return a complete validated SimulationResult")
            record = self._by_id[run_id]
            if index is not None:
                actual_time = getattr(result, "_stored_frame_time", result.metrics.get("time_s"))
                if result.transient is not None or actual_time != record.times[index]:
                    raise SimulationError("Loaded frame time does not match the catalog's exact stored index")
            elif bool(record.times) != (result.transient is not None) or (
                    result.transient is not None and not np.array_equal(result.transient.times, record.times)):
                raise SimulationError("Loaded transient times disagree with the stored catalog")
            return result, keys
        except BaseException as exc:
            if keys:
                self._release_keys(keys)
            if keys and isinstance(exc, SimulationError) and not isinstance(exc, (CacheBudgetExceeded, HistoryReadCancelled)):
                if self._cancel.is_set() or (cancel is not None and cancel()):
                    raise HistoryReadCancelled("Historical result loading was cancelled") from exc
                with self._lock:
                    self._errors[run_id] = str(exc)
            raise
        finally:
            with self._lock:
                if active:
                    self._active_reads -= 1
                self._estimated_next = 0
            self._read_lock.release()

    def acquire(self, run_id, *, allow_oversize=False, cancel=None):
        result, keys = self._request(run_id, None, allow_oversize=allow_oversize, cancel=cancel)
        return ResultLease(self, result, keys)

    def _frame_index(self, run_id, index):
        record = self._by_id.get(run_id)
        if type(index) is not int or index < 0 or record is None or index >= len(record.times):
            raise SimulationError("Choose an exact stored frame index within this run's available times")

    def acquire_frame(self, run_id, index, *, allow_oversize=False, cancel=None):
        self._frame_index(run_id, index)
        result, keys = self._request(run_id, index, allow_oversize=allow_oversize, cancel=cancel)
        return ResultLease(self, result, keys)

    def _autolease(self, result, keys):
        identity = id(result)
        def release():
            self._release_keys(keys)
            with self._lock:
                self._materialized.pop(identity, None)
        finalizer = weakref.finalize(result, release)
        with self._lock:
            self._materialized[identity] = finalizer
        return result

    def materialize(self, run_id, *, allow_oversize=False, cancel=None):
        """Return a real result; release_result or GC releases its automatic pin."""
        result, keys = self._request(run_id, None, allow_oversize=allow_oversize, cancel=cancel)
        return self._autolease(result, keys)

    def frame(self, run_id, index, *, allow_oversize=False, cancel=None):
        self._frame_index(run_id, index)
        result, keys = self._request(run_id, index, allow_oversize=allow_oversize, cancel=cancel)
        return self._autolease(result, keys)

    def release_result(self, result):
        with self._lock:
            finalizer = self._materialized.get(id(result))
        if finalizer is not None:
            finalizer()

    def capture_history(self, run_ids=None):
        """Capture metadata references with a source lifetime independent of the view."""
        with self._lock:
            self._checkpoint()
            run_ids = tuple(item.run_id for item in self.history) if run_ids is None else tuple(run_ids)
            for run_id in run_ids:
                record = self._by_id.get(run_id)
                if record is None or not record.available or run_id in self._errors:
                    raise SimulationError("Cannot save an unavailable stored run; inspect or remove it explicitly")
            self._generation_pins += 1
            return StoredHistoryLease(self, run_ids)

    def pin_generation(self):
        return self.capture_history(item.run_id for item in self.records)

    def _dispose_reader(self):
        if self._temporary is None:
            return
        try:
            self._reader.close()
        finally:
            self._temporary.cleanup()
            self._temporary = None

    def _release_generation(self):
        with self._lock:
            self._generation_pins -= 1
            dispose = self._closed and self._generation_pins == 0
        if dispose:
            with self._read_lock:
                self._dispose_reader()

    def close(self):
        """Cancel view reads; saves retain their independently leased source copy."""
        with self._lock:
            if self._closed:
                return
            self._cancel.set()
        with self._read_lock:
            with self._lock:
                self._closed = True
                dispose = self._generation_pins == 0
            try:
                if dispose:
                    self._dispose_reader()
            finally:
                with self._lock:
                    self._evict(clear=True)

    def __enter__(self):
        self._checkpoint()
        return self

    def __exit__(self, *args):
        self.close()


def open_history_catalog(path, *, external_policy=None, cache_bytes=DEFAULT_CACHE_BYTES,
                         cancel=None, copy_directory=None):
    """Open an opt-in schema-3 catalog; legacy APIs retain their eager semantics."""
    return ResultStore(path, external_policy=external_policy, cache_bytes=cache_bytes,
                       cancel=cancel, copy_directory=copy_directory)
