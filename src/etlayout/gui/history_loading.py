# SPDX-License-Identifier: MPL-2.0
"""Explicit cancellable archive reads; widgets never receive stale results."""
from __future__ import annotations

import threading
from dataclasses import dataclass

from PySide6.QtCore import QObject, QThread, Signal
from PySide6.QtWidgets import (
    QCheckBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QLabel,
    QSpinBox,
    QVBoxLayout,
)


@dataclass(frozen=True)
class HistoryRequest:
    epoch: str
    generation: int
    run_ids: tuple[str, ...]
    purpose: str = "inspect"
    frame_index: int | None = None
    allow_oversize: bool = False


class HistoryReadJob(QThread):
    loaded = Signal(object, object)
    failed = Signal(object, object)

    def __init__(self, store, request, parent=None):
        super().__init__(parent)
        self.store, self.request = store, request
        self._cancel = threading.Event()
        self.setObjectName("etlayout-history-read")

    def cancel(self):
        self._cancel.set()

    def run(self):
        leases = []
        try:
            for run_id in self.request.run_ids:
                options = dict(cancel=self._cancel.is_set, allow_oversize=self.request.allow_oversize)
                lease = (self.store.acquire_frame(run_id, self.request.frame_index, **options)
                         if self.request.frame_index is not None else self.store.acquire(run_id, **options))
                leases.append(lease)
                if self._cancel.is_set():
                    return
            self.loaded.emit(self.request, tuple(leases))
            leases = []  # Ownership passes to the queued receiver, including stale replies.
        except Exception as error:
            self.failed.emit(self.request, error)
        finally:
            for lease in leases:
                lease.close()


class HistoryLoader(QObject):
    """Document epoch + monotonic request generation form the display authority."""
    loaded = Signal(object, object)
    failed = Signal(object, object)
    busy_changed = Signal(bool)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.generation = 0
        self.request = None
        self.jobs = []

    def submit(self, store, epoch, run_ids, *, purpose="inspect", frame_index=None, allow_oversize=False):
        self.cancel()
        self.request = request = HistoryRequest(epoch, self.generation, tuple(run_ids), purpose,
                                                frame_index, allow_oversize)
        job = HistoryReadJob(store, request, self)
        self.jobs.append(job)
        job.loaded.connect(self._loaded)
        job.failed.connect(self._failed)
        job.finished.connect(lambda: self._finished(job))
        self.busy_changed.emit(True)
        job.start()
        return request

    def cancel(self):
        self.generation += 1
        self.request = None
        for job in self.jobs:
            job.cancel()

    def _loaded(self, request, leases):
        if request != self.request:
            for lease in leases:
                lease.close()
            return
        self.loaded.emit(request, leases)

    def _failed(self, request, error):
        if request == self.request:
            self.failed.emit(request, error)

    def _finished(self, job):
        if job in self.jobs:
            self.jobs.remove(job)
        job.deleteLater()
        self.busy_changed.emit(bool(self.jobs))

    def shutdown(self, timeout_ms=0):
        self.cancel()
        if timeout_ms <= 0:
            return all(not job.isRunning() for job in self.jobs)
        return all([job.wait(timeout_ms) for job in tuple(self.jobs)])


class HistorySettingsDialog(QDialog):
    def __init__(self, enabled, cache_mib, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Archive and history settings")
        self.resize(520, 280)
        layout = QVBoxLayout(self)
        note = QLabel("On-demand history is opt-in for content-addressed (schema 3) projects. "
                      "Older projects retain their eager opening behavior. Changing this setting applies to the next opening. "
                      "The reader keeps a bounded private disk copy of the opened archive until its readers and save leases close.")
        note.setWordWrap(True)
        layout.addWidget(note)
        self.enabled = QCheckBox("Open schema-3 history on demand")
        self.enabled.setChecked(enabled)
        layout.addWidget(self.enabled)
        form = QFormLayout()
        self.cache_mib = QSpinBox()
        self.cache_mib.setRange(16, 8192)
        self.cache_mib.setValue(cache_mib)
        self.cache_mib.setSuffix(" MiB")
        self.cache_mib.setAccessibleName("History array cache budget")
        form.addRow("Array cache budget", self.cache_mib)
        layout.addLayout(form)
        detail = QLabel("The budget counts unique resident array buffers, not process memory. Active views and comparisons stay pinned. "
                        "A larger selection requires your explicit temporary over-budget approval. Default: 256 MiB.")
        detail.setWordWrap(True)
        layout.addWidget(detail)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
