# SPDX-License-Identifier: MPL-2.0
"""Immutable save handoff and atomic publication order, independent of Qt."""
from __future__ import annotations

import os
import threading
import uuid
from copy import deepcopy
from dataclasses import dataclass, fields, is_dataclass
from pathlib import Path

import numpy as np

from etlayout.io.identity import document_id
from etlayout.io.resources import require_resources
from etlayout.models import Project, SimulationError, SimulationResult


class SaveCancelled(SimulationError):
    """The previous durable archive was preserved."""


class SaveSuperseded(SaveCancelled):
    """A newer submitted save owns this destination."""


def _immutable_array(array):
    if array.dtype.hasobject:
        raise SimulationError("Object arrays cannot be handed to the save worker")
    base = array
    while isinstance(base, np.ndarray) and base.base is not None:
        base = base.base
    if isinstance(base, bytes) and not array.flags.writeable:
        return array.view()
    # A flag on a view is insufficient: another writable alias could mutate it.
    # Bytes-backed storage cannot be made writable even with setflags().
    return np.frombuffer(array.tobytes(order="C"), dtype=array.dtype).reshape(array.shape)


def _arrays(value, seen=None):
    seen = set() if seen is None else seen
    if id(value) in seen:
        return
    seen.add(id(value))
    if isinstance(value, np.ndarray):
        yield value
    elif is_dataclass(value):
        for item in fields(value):
            yield from _arrays(getattr(value, item.name), seen)
    elif isinstance(value, dict):
        for child in value.values():
            yield from _arrays(child, seen)
    elif isinstance(value, (tuple, list)):
        for child in value:
            yield from _arrays(child, seen)


def freeze_result_arrays(result):
    """Own immutable buffers once when a GUI receives a completed result.

    Object/dictionary identities remain unchanged; replacement field assignment
    remains possible. A later replacement by a mutable array is copied at save.
    Non-GUI solver/Python callers keep their existing mutable-result contract.
    """
    seen = set()
    buffers = {}
    def freeze(value):
        if isinstance(value, np.ndarray):
            if id(value) not in buffers:
                buffers[id(value)] = _immutable_array(value)
            return buffers[id(value)]
        if id(value) in seen:
            return value
        seen.add(id(value))
        if is_dataclass(value):
            for item in fields(value):
                setattr(value, item.name, freeze(getattr(value, item.name)))
        elif isinstance(value, dict):
            for key, child in list(value.items()):
                value[key] = freeze(child)
        elif isinstance(value, list):
            for index, child in enumerate(value):
                value[index] = freeze(child)
        return value
    freeze(result)


@dataclass(frozen=True)
class SaveSnapshot:
    project: Project
    result: SimulationResult | None
    history: tuple[SimulationResult, ...]
    document_id: str
    revision: int
    archive_format: int = 2
    source_lease: object = None
    verify_exact: bool = False

    def release_sources(self):
        if self.source_lease is not None:
            self.source_lease.close()

    @classmethod
    def capture(cls, project, result=None, history=None, *, revision=0, archive_format=2, source_lease=None):
        if type(archive_format) is not int or archive_format not in (2, 3):
            raise SimulationError("Archive format must be 2 or 3")
        history = tuple(history or ())
        if archive_format == 3:
            for run in (result, *history):
                if isinstance(run, SimulationResult) and not getattr(run, "_storage_run_id", None):
                    run._storage_run_id = str(uuid.uuid4())
        require_resources(project)  # Pure state check; never authorize or open a resource.
        identity = document_id(project)
        # Persisted model fields only: runtime inspection/history caches must not
        # copy removed runs, retain private resource declarations or reach workers.
        persisted = Project(**{item.name: getattr(project, item.name) for item in fields(project)})
        persisted._document_id = identity
        # Isolate every mutable metadata container. Share only buffers backed by
        # immutable bytes, not merely arrays whose own write flag is clear.
        graph = (persisted, result, history)
        memo = {id(array): _immutable_array(array) for array in _arrays(graph)}
        saved_project, saved_result, saved_history = deepcopy(graph, memo)
        return cls(saved_project, saved_result, saved_history, identity, revision, archive_format, source_lease,
                   archive_format == 2 and getattr(project, "_archive_format", 2) == 3)


class SaveOrder:
    """One process-wide destination authority; older workers cannot publish."""
    _lock = threading.Lock()
    _tickets = {}
    _counter = 0

    @classmethod
    def reserve(cls, path):
        key = os.path.normcase(os.path.abspath(path))
        with cls._lock:
            cls._counter += 1
            cls._tickets[key] = cls._counter
            return key, cls._counter

    @staticmethod
    def signature(path):
        try:
            value = Path(path).stat()
            return value.st_ino, value.st_size, value.st_mtime_ns
        except FileNotFoundError:
            return None

    @classmethod
    def commit(cls, ticket, expected, temporary, target):
        # A second process can publish while this worker compresses. Check the
        # destination again under an OS lock before replacing its durable file.
        from etlayout.io.recovery import file_lock
        with file_lock(Path(target).with_name(Path(target).name + ".save.lock")):
            with cls._lock:
                if cls._tickets.get(ticket[0]) != ticket[1]:
                    raise SaveSuperseded("A newer save was requested; the older snapshot was not published")
                if cls.signature(target) != expected:
                    raise SaveSuperseded("The destination changed during saving; reopen or choose Save As to avoid overwriting it")
                os.replace(temporary, target)
