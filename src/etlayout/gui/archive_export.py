# SPDX-License-Identifier: MPL-2.0
"""Cancellable complete-archive export from an immutable handoff, without widgets."""
from __future__ import annotations

import threading

from PySide6.QtCore import QThread, Signal

from etlayout.io.saving import SaveCancelled
from etlayout.io.sharing import execute_export


class ArchiveExportJob(QThread):
    progress = Signal(float, str)
    exported = Signal(object)
    failed = Signal(str)
    cancelled = Signal(str)

    def __init__(self, snapshot, directory, request, parent=None):
        super().__init__(parent)
        self.snapshot, self.directory, self.request = snapshot, str(directory), request
        self._cancel = threading.Event()
        self.setObjectName("etlayout-archive-export")

    def cancel(self):
        self._cancel.set()

    def run(self):
        try:
            # SaveSnapshot deliberately excludes runtime attributes. The explicit
            # handoff's chosen format controls this worker's complete archive.
            self.snapshot.project._archive_format = self.snapshot.archive_format
            result = execute_export(self.snapshot.result, self.directory, self.request,
                project=self.snapshot.project, history=self.snapshot.history,
                progress=self.progress.emit, cancel=self._cancel.is_set)
        except SaveCancelled as error:
            self.cancelled.emit(str(error))
        except Exception as error:
            self.failed.emit(str(error))
        else:
            self.exported.emit(result)
        finally:
            self.snapshot.release_sources()
