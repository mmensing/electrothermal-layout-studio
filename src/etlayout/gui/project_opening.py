# SPDX-License-Identifier: MPL-2.0
"""Project opening owns asynchronous reads, consent and the final document transition.

Only this controller retains pending read results. It never mutates the working
setup until the shared draft/save/history guards accept the current generation.
"""
from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QObject, QTimer
from PySide6.QtWidgets import QDialog, QFileDialog, QMessageBox

from etlayout.gui.history_opening import HistoryOpener
from etlayout.io.project import physics_hash


class ProjectOpeningController(QObject):
    def __init__(self, window):
        super().__init__(window)
        self.window = window
        self.pending = None
        self.review_restore = None
        self.revision = 0
        self.opener = HistoryOpener(self)
        self.opener.loaded.connect(self.opened)
        self.opener.review_required.connect(self.review)
        self.opener.failed.connect(self.failed)
        self.opener.busy_changed.connect(self.busy)

    def review_resources(self):
        window = self.window
        if window.model_locked():
            window.log("Return to the working setup and finish the active job before reviewing its resources.")
            return
        inspection = getattr(window.project, "_resource_inspection", None)
        if inspection is None:
            window.relink_layout()
            return
        if hasattr(window, "guided") and not window.guided.resolve_draft():
            return
        self.drop_pending()
        self.revision = window.document_revision
        self.review_restore = window.project
        # Byte-backed legacy inspections retain their exact reviewed archive.
        # Catalog inspections retain their verified generation digest instead.
        held = inspection if (inspection.archive_data is None
            and inspection.metadata.get("schema_version") == 3
            and getattr(inspection, "_archive_sha256", None)) else inspection.for_project(window.project)
        window.history_opener.submit(held.source, window.document_epoch,
            cache_bytes=window.history_cache_mib * 1024**2, inspection=held,
            lazy=window.history_lazy_enabled)

    def open(self, path=None, *, external_policy=None):
        window = self.window
        if not window.confirm_discard(lambda: self.open(path, external_policy=external_policy)):
            return
        if not isinstance(path, (str, Path)):
            path, _ = QFileDialog.getOpenFileName(window, "Open project", "", "Electrothermal projects (*.etl *.etlayout *.etproj *.zip);;All files (*)")
        if path:
            self.drop_pending()
            self.revision = window.document_revision
            window.history_opener.submit(path, window.document_epoch, policy=external_policy,
                cache_bytes=window.history_cache_mib * 1024**2, lazy=window.history_lazy_enabled)
            window.statusBar().showMessage("Opening project in a worker; the current project remains available…")

    def cancel(self):
        window = self.window
        window.history_opener.cancel()
        self.drop_pending()
        if window._save_continuation == self.apply:
            window._save_continuation = None

    def busy(self, busy):
        window = self.window
        window.history_panel.set_loading(busy or bool(window.history_workflow.loader.jobs))

    def drop_pending(self):
        self.review_restore = None
        pending, self.pending = self.pending, None
        if pending and pending[3] is not None:
            pending[3].close()

    def opened(self, request, project, store):
        self.accept(request, project, project.__dict__.pop("_opening_result", None), store)

    def accept(self, request, project, result, store=None):
        window = self.window
        current = window.history_opener.request
        if request.epoch != window.document_epoch or current is None or request.generation != current.generation:
            if store:
                store.close()
            return
        power = getattr(window.guided, "power", None)
        if window.job or (power and power.job) or not window.guided.resolve_draft():
            if store:
                store.close()
            return
        self.pending = (request, project, result, store)
        self.apply()

    def apply(self):
        window = self.window
        if self.pending and (window.save_job or window._save_queue
                or any(job.isRunning() for job in window.history_workflow.loader.jobs)):
            window.history_workflow.loader.cancel()
            QTimer.singleShot(25, self.apply)
            return
        if self.pending is None:
            return
        # A read cancelled above can finish after another draft or edit was made.
        # Recheck the transition at its commit point, not just when metadata arrived.
        power = getattr(window.guided, "power", None)
        if window.job or (power and power.job) or not window.guided.resolve_draft():
            self.drop_pending()
            return
        continuation = self.apply
        if self.review_restore is not window.project and window.document_revision != self.revision:
            if not window.confirm_discard(continuation):
                if window._save_continuation != continuation:
                    self.drop_pending()
                return
            self.revision = window.document_revision
        pending, self.pending = self.pending, None
        request, project, result, store = pending
        current = window.history_opener.request
        if request.epoch != window.document_epoch or current is None or request.generation != current.generation:
            if store:
                store.close()
            return
        if self.review_restore is window.project:
            self.review_restore = None
            if store is None and getattr(project, "_resource_unresolved", False):
                return
            working = window.project
            inspection = getattr(working, "_resource_inspection", None)
            declared = inspection.project.layout if inspection else None
            if declared and (working.layout.path != declared.path or working.layout.sha256 != declared.sha256):
                if store:
                    store.close()
                window.history_workflow.issue(
                    "The layout source declaration changed during resource review. "
                    "Use File → Relink layout to review the new source; the working setup was retained.", project_opening=True)
                return
            if not window.history_workflow.close():
                if store:
                    store.close()
                return
            # Resource approval updates the existing document rather than replacing
            # it: cell/instance choices, undo, studies and view context stay owned.
            view, workspace = window.capture_view_state(), window.guided.capture_context()
            working.layout.path, working.layout.sha256 = project.layout.path, project.layout.sha256
            working._resource_unresolved = False
            working.__dict__.pop("_resource_inspection", None)
            if store:
                working._archive_format = store.archive_format
                window.run_history = list(store.records)
                window.history_workflow.attach(store)
            else:
                if result and result.physics_hash != physics_hash(working, resolve_layout=False):
                    result = None
                window.result = result
                window.mesh = result.mesh if result else None
                window.run_history = list(getattr(project, "_resource_history", []))
                if result:
                    window.remember_result(result)
                window.history_workflow.attach(None)
            window.refresh_project(preserve_view=True)
            window.guided.restore_context(workspace)
            window.restore_view_state(view)
            window.set_running(False)
            if store:
                window.history_workflow.open_current()
            elif result:
                window.render_result(result)
            window.update_title()
            window.log("Resolved resources for the current edited project")
            return
        if window.set_project(project, result, request.path, getattr(project, "_resource_history", [])) is False:
            if store:
                store.close()
            return
        window.log("Opened metadata-first archive; recorded fields load on demand" if store else "Opened project")

    def review(self, request, inspection, error):
        window = self.window
        if request.epoch != window.document_epoch or request != window.history_opener.request:
            return
        from etlayout.gui.resources import ResourceReviewDialog
        from etlayout.io.resources import ExternalResourcePolicy
        if inspection is None:
            if getattr(error, "reason", "") != "network":
                self.failed(request, error)
                return
            if QMessageBox.question(window, "Network project location",
                    "Allow network access for this selected project container? Referenced outside-root resources still need their own approval.",
                    QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                    QMessageBox.StandardButton.No) != QMessageBox.StandardButton.Yes:
                return
            policy = ExternalResourcePolicy(allow_outside_root=request.policy.allow_outside_root,
                allow_network=True, approved_resources=request.policy.approved_resources)
            window.history_opener.submit(request.path, request.epoch, policy=policy,
                cache_bytes=request.cache_bytes, lazy=request.lazy)
            return
        dialog = ResourceReviewDialog(inspection, error, window)
        try:
            if dialog.exec() != QDialog.DialogCode.Accepted:
                return
            if request.epoch != window.document_epoch or request != window.history_opener.request:
                return
            if dialog.action == "unresolved":
                self.accept(request, inspection.unresolved_project(), None)
            elif dialog.action == "relink":
                policy = ExternalResourcePolicy(allow_network=dialog.network.isChecked(),
                                                approved_resources=frozenset({"layout"}))
                window.history_opener.submit(request.path, request.epoch, policy=policy,
                    cache_bytes=request.cache_bytes, inspection=dialog.inspection, lazy=request.lazy)
            else:
                window.history_opener.submit(request.path, request.epoch, policy=dialog.policy(),
                                           cache_bytes=request.cache_bytes, inspection=inspection, lazy=request.lazy)
        except Exception as failure:
            self.failed(request, failure)
        finally:
            dialog.deleteLater()

    def failed(self, request, error):
        window = self.window
        if request.epoch == window.document_epoch and request == window.history_opener.request:
            self.review_restore = None
            window.history_workflow.issue("Opening the selected project failed: " + str(error), project_opening=True)

