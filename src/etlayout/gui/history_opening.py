# SPDX-License-Identifier: MPL-2.0
"""Owned background project opening; reviewed grants bind one archive generation."""
from __future__ import annotations

import json
import threading
import time
import zipfile
from copy import deepcopy
from dataclasses import dataclass
from pathlib import Path

from PySide6.QtCore import QObject, QThread, Signal, Slot

from etlayout.gui.worker_errors import detached_error
from etlayout.io.history_store import open_history_catalog
from etlayout.io.project import load_run_history, resolve_project
from etlayout.io.resources import (
    ExternalResourcePolicy,
    ProjectInspection,
    ResourceAccessError,
    _canonical_checked,
    classify_resource,
    inspect_project,
)
from etlayout.models import SimulationError


@dataclass(frozen=True)
class HistoryOpenRequest:
    epoch: str
    generation: int
    path: Path
    policy: ExternalResourcePolicy
    cache_bytes: int
    inspection: ProjectInspection | None = None
    lazy: bool = True


def _schema(path, policy, cancel):
    """Bound the selected root probe; its metadata never authorizes a resource."""
    from etlayout.io.project import MAX_ARCHIVE_BYTES, MAX_ARCHIVE_ENTRIES, MAX_METADATA_BYTES
    if cancel():
        raise SimulationError("Project opening was cancelled")
    source = _canonical_checked(Path(path).absolute(), network=policy.allow_network)
    limit = MAX_METADATA_BYTES if source.suffix.lower() == ".json" else MAX_ARCHIVE_BYTES
    if source.stat().st_size > limit:
        raise SimulationError("Selected project container exceeds its byte budget")
    if source.suffix.lower() == ".json":
        return 2  # The normal reader retains schema-1/2/JSON compatibility.
    try:
        from etlayout.io.archive_directory import inspect_directory
        inspect_directory(source, max_bytes=MAX_ARCHIVE_BYTES, max_entries=MAX_ARCHIVE_ENTRIES, cancel=cancel)
        with zipfile.ZipFile(source) as archive:
            members = archive.infolist()
            if len(members) > MAX_ARCHIVE_ENTRIES or len({item.filename for item in members}) != len(members):
                raise SimulationError("Project directory exceeds its entry budget or contains duplicate names")
            root = archive.getinfo("project.json")
            if root.file_size > MAX_METADATA_BYTES:
                raise SimulationError("Project metadata exceeds its byte budget")
            chunks, remaining = [], root.file_size
            with archive.open(root) as stream:
                while remaining:
                    if cancel():
                        raise SimulationError("Project opening was cancelled")
                    block = stream.read(min(1024**2, remaining))
                    if not block:
                        raise SimulationError("Truncated project metadata")
                    remaining -= len(block)
                    chunks.append(block)
            metadata = json.loads(b"".join(chunks))
        if (not isinstance(metadata, dict) or metadata.get("format") != "etlayout"
                or type(metadata.get("schema_version")) is not int or metadata["schema_version"] not in (1, 2, 3)):
            raise SimulationError("Unsupported project archive format; upgrade the application")
        return metadata["schema_version"]
    except (zipfile.BadZipFile, KeyError, OSError, ValueError, RuntimeError) as error:
        if isinstance(error, SimulationError):
            raise
        raise SimulationError("Cannot inspect the selected project metadata") from error


def catalog_inspection(store):
    """Review declarations from verified metadata without arrays or external reads.

    The runtime digest originates from the pinned reader. No persisted JSON key
    can establish a grant or substitute for this generation binding.
    """
    reader = store._reader
    metadata = deepcopy(reader.metadata)
    project = store.project_metadata
    resources = []
    embedded = metadata.get("embedded_layout")
    if embedded or project.layout.path:
        resources.append(classify_resource(embedded or project.layout.path, store.source, embedded=bool(embedded),
            expected_sha256=project.layout.sha256, key="layout"))
    for record in reader.records:
        if not record["available"]:
            continue
        descriptor = reader._descriptor(record["id"])
        from dataclasses import asdict
        if record["id"] == reader.current_run_id and descriptor["project"]["layout"] == asdict(reader.project.layout):
            continue  # One identical current declaration receives one approval.
        layout = descriptor["project"]["layout"]
        reference = descriptor["layout"]
        location = reader.object_name(reference["$ref"]) if reference else layout["path"]
        if location:
            resources.append(classify_resource(location, store.source, embedded=bool(reference),
                expected_sha256=layout["sha256"], key=record["id"] + ":layout"))
    inspection = ProjectInspection(store.source, project, tuple(resources), metadata=metadata)
    inspection._archive_sha256 = store.generation
    return inspection


class HistoryOpenJob(QThread):
    loaded = Signal(object, object, object)
    review_required = Signal(object, object, object)
    failed = Signal(object, object)

    def __init__(self, request, parent=None):
        super().__init__(parent)
        self.request = request
        self._cancel = threading.Event()
        self.setObjectName("etlayout-history-open")

    def cancel(self):
        self._cancel.set()

    def _checkpoint(self):
        if self._cancel.is_set():
            raise SimulationError("Project opening was cancelled; the working project was retained")

    def run(self):
        request, store = self.request, None
        inspection = request.inspection
        try:
            self._checkpoint()
            # Keep legacy/JSON and default eager deserialization off the GUI too.
            # Review retries use the held bytes; grants never transfer to a newly
            # replaced container at the same filename.
            lazy = request.lazy and not (inspection and (
                inspection.relinked or inspection.discard_stale_result
            ))
            if inspection is None:
                if not lazy or _schema(request.path, request.policy, self._cancel.is_set) != 3:
                    inspection = inspect_project(request.path, allow_network_container=request.policy.allow_network)
            if (not lazy and inspection is not None and inspection.archive_data is None
                    and inspection.metadata.get("schema_version") == 3
                    and getattr(inspection, "_archive_sha256", None)):
                # A metadata-only review may outlive a changed lazy preference.
                # Verify its recorded generation before creating an eager reader;
                # only the owned bounded snapshot supplies bytes for this retry.
                store = open_history_catalog(inspection, external_policy=request.policy,
                    cache_bytes=request.cache_bytes, cancel=self._cancel.is_set)
                inspection = inspect_project(store.source, _archive_data=store._snapshot_path.read_bytes())
            if not lazy or (inspection and inspection.metadata.get("schema_version") != 3):
                self._checkpoint()
                project, result = resolve_project(inspection, external_policy=request.policy)
                self._checkpoint()
                history = [] if inspection.relinked else load_run_history(
                    inspection.source, external_policy=request.policy, _archive_data=inspection.archive_data)
                self._checkpoint()
                project._resource_history = history
                project._opening_result = result
                self.loaded.emit(request, project, None)
                return
            store = open_history_catalog(inspection or request.path, external_policy=request.policy,
                                         cache_bytes=request.cache_bytes, cancel=self._cancel.is_set)
            inspection = catalog_inspection(store)
            if self._cancel.is_set():
                return
            project = store.resolve_project()
            if self._cancel.is_set():
                return
            project._history_store = store
            project._resource_history = []
            self.loaded.emit(request, project, store)
            store = None  # The queued receiver owns it, including stale replies.
        except ResourceAccessError as error:
            if not self._cancel.is_set():
                self.review_required.emit(request, inspection, detached_error(error))
        except Exception as error:
            if not self._cancel.is_set():
                self.failed.emit(request, detached_error(error))
        finally:
            if store is not None:
                store.close()


class HistoryOpener(QObject):
    """Only the current request can replace a document or ask for resource grants.

    Eager decoding is cancellable between bounded reader phases; an in-progress
    native array decode finishes off-thread before cancellation disposes it.
    """
    loaded = Signal(object, object, object)
    review_required = Signal(object, object, object)
    failed = Signal(object, object)
    busy_changed = Signal(bool)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.generation = 0
        self.request = None
        self.jobs = []

    def submit(self, path, epoch, *, policy=None, cache_bytes=256 * 1024**2, inspection=None, lazy=True):
        self.cancel()
        request = HistoryOpenRequest(epoch, self.generation, Path(path), policy or ExternalResourcePolicy(),
                                     cache_bytes, inspection, lazy)
        self.request = request
        job = HistoryOpenJob(request, self)
        self.jobs.append(job)
        job.loaded.connect(self._loaded)
        job.review_required.connect(self._review)
        job.failed.connect(self._failed)
        job.finished.connect(self._finished)
        self.busy_changed.emit(True)
        job.start()
        return request

    def cancel(self):
        self.generation += 1
        self.request = None
        for job in self.jobs:
            job.cancel()

    @Slot(object, object, object)
    def _loaded(self, request, project, store):
        if request != self.request:
            if store is not None:
                store.close()
            return
        self.loaded.emit(request, project, store)

    @Slot(object, object, object)
    def _review(self, request, inspection, error):
        if request == self.request:
            self.review_required.emit(request, inspection, error)

    @Slot(object, object)
    def _failed(self, request, error):
        if request == self.request:
            self.failed.emit(request, error)

    @Slot()
    def _finished(self):
        job = self.sender()
        if job in self.jobs:
            self.jobs.remove(job)
        job.deleteLater()
        self.busy_changed.emit(bool(self.jobs))

    def shutdown(self, timeout_ms=0):
        self.cancel()
        if timeout_ms <= 0:
            return all(not job.isRunning() for job in self.jobs)
        deadline = time.monotonic() + timeout_ms / 1000
        for job in tuple(self.jobs):
            if not job.wait(max(0, int((deadline - time.monotonic()) * 1000))):
                return False
        return True
