# SPDX-License-Identifier: MPL-2.0
"""Qt save worker: immutable inputs, no widgets or live document access."""
from __future__ import annotations

import threading
from dataclasses import dataclass, field
from pathlib import Path

from PySide6.QtCore import QThread, Signal

from etlayout.io.project import save_project
from etlayout.io.saving import SaveCancelled, SaveOrder, SaveSnapshot


@dataclass(frozen=True)
class SaveRequest:
    snapshot: SaveSnapshot
    destination: Path
    epoch: str
    sequence: int
    recovery_store: object = None
    original_path: str = ""
    ticket: tuple = ()
    cleanup_store: object = None
    expected_destination: tuple | None = field(init=False)

    def __post_init__(self):
        # Bind ownership when the request is submitted, before it can wait in a
        # queue. A newer external archive must never become this snapshot's
        # baseline merely because the worker starts later.
        expected = None if self.recovery_store is not None else SaveOrder.signature(self.destination)
        object.__setattr__(self, "expected_destination", expected)


class SaveJob(QThread):
    progress = Signal(float, str)
    saved = Signal(object, object)
    failed = Signal(object, str)
    cancelled = Signal(object, str)

    def __init__(self, request, parent=None):
        super().__init__(parent)
        self.request = request
        self.cleanup_errors = []
        self._cancel = threading.Event()
        self.setObjectName("etlayout-save")

    def cancel(self):
        self._cancel.set()

    def run(self):
        request = self.request
        snapshot = request.snapshot
        try:
            options = {"history": list(snapshot.history), "progress": self.progress.emit,
                       "cancel": self._cancel.is_set}
            if request.recovery_store is not None:
                def writer(project, path, result, history=None):
                    save_project(project, path, result, **{**options, "history": history})
                entry = request.recovery_store.snapshot(snapshot.project, snapshot.result, snapshot.history,
                    revision=snapshot.revision, original_path=request.original_path, writer=writer)
                self.saved.emit(request, entry)
            else:
                options["commit"] = lambda temporary, target: SaveOrder.commit(
                    request.ticket, request.expected_destination, temporary, target)
                save_project(snapshot.project, request.destination, snapshot.result, **options)
                if request.cleanup_store is not None:
                    try:
                        self.cleanup_errors = request.cleanup_store.cleanup(snapshot.document_id, snapshot.revision)
                    except Exception as error:
                        self.cleanup_errors = [str(error)]
                self.saved.emit(request, None)
        except SaveCancelled as error:
            self.cancelled.emit(request, str(error))
        except Exception as error:
            self.failed.emit(request, str(error))
