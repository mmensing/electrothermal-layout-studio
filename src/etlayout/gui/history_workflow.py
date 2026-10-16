# SPDX-License-Identifier: MPL-2.0
"""History transitions own explicit read leases; the working document stays intact."""
from __future__ import annotations

from PySide6.QtWidgets import QMessageBox

from etlayout.gui.history_loading import HistoryLoader
from etlayout.io.history_store import CacheBudgetExceeded, RunRecord
from etlayout.io.project import physics_hash


def is_record(value):
    return isinstance(value, RunRecord)


class HistoryWorkflow:
    def __init__(self, window):
        self.window = window
        self.store = None
        self.loader = HistoryLoader(window)
        self.loader.loaded.connect(self.loaded)
        self.loader.failed.connect(self.failed)
        self.loader.busy_changed.connect(self.busy_changed)
        self.record = None
        self.working_lease = None
        self.inspection_lease = None
        self.view_states = {}
        self.pending_comparison = None

    def attach(self, store):
        self.store = store
        self.record = None
        self.view_states.clear()
        self.refresh()

    def refresh(self):
        window = self.window
        if hasattr(window, "history_panel"):
            if self.store:
                window.run_history = [self.store.record(item.run_id) if is_record(item) else item
                                      for item in window.run_history]
            window.history_panel.set_results(window.run_history)
            if self.store:
                window.history_panel.set_cache_stats(self.store.stats)

    def busy_changed(self, busy):
        if hasattr(self.window, "history_panel"):
            self.window.history_panel.set_loading(busy or bool(self.window.history_opener.jobs))
            self.refresh()

    def guard(self):
        window = self.window
        power = getattr(window.guided, "power", None)
        if window.job or (power and power.job):
            window.log("Wait for the active computation before inspecting history.")
            return False
        return window.guided.resolve_draft()

    def request(self, record, purpose="inspect", *, index=None, allow_oversize=False):
        if not self.store or not self.guard():
            return False
        if not record.available:
            self.issue(record.error or "This run is unavailable; reopen a trusted intact archive.")
            return False
        if index is None and record.times and purpose in {"inspect", "working"}:
            index = len(record.times) - 1
        self.window.cancel_catalog()
        self.loader.submit(self.store, self.window.document_epoch, (record.run_id,), purpose=purpose,
                           frame_index=index, allow_oversize=allow_oversize)
        self.window.statusBar().showMessage("Loading recorded fields; the previous view remains available…")
        return True

    def open_current(self):
        if self.store and self.store.current_run_id:
            self.request(self.store.record(self.store.current_run_id), "working")

    def inspect(self, record):
        return self.request(record)

    def frame(self, index):
        if self.record and self.record.times:
            return self.request(self.record, "frame", index=index)
        return False

    def full(self, purpose="trace"):
        purpose = "export-history" if purpose == "export" else purpose
        if self.record:
            return self.request(self.record, purpose)
        return False

    def compare(self, records):
        if not self.store or not self.guard():
            return False
        self.pending_comparison = list(records)
        self.loader.submit(self.store, self.window.document_epoch,
                           [item.run_id for item in records if is_record(item)], purpose="compare")
        self.window.statusBar().showMessage("Loading selected runs for comparison…")
        return True

    def loaded(self, request, leases):
        window = self.window
        if request.epoch != window.document_epoch or not self.store:
            for lease in leases:
                lease.close()
            return
        if request.purpose == "compare":
            try:
                if not self.guard():
                    return
                loaded = iter(lease.result for lease in leases)
                results = [next(loaded) if is_record(item) else item for item in self.pending_comparison or []]
                window.compare_history(results)
            finally:
                self.pending_comparison = None
                for lease in leases:
                    lease.close()
                self.refresh()
            return
        lease = leases[0]
        record = self.store.record(request.run_ids[0])
        result = lease.result
        if not self.guard():
            lease.close()
            return
        previous = self.capture_context()
        release_after = []
        try:
            if request.purpose == "inspect":
                previous_record = self.record
                if previous_record:
                    self.view_states[previous_record.run_id] = window.capture_view_state()
                accepted = window.inspect_result(result)
                if accepted is False or window.result is not result:
                    lease.close()
                    return
                old, self.inspection_lease = self.inspection_lease, lease
                self.record = record
                window.project._archive_format = self.store.archive_format
                if old:
                    release_after.append(old)
                self.configure_time(record, request.frame_index)
                if record.run_id in self.view_states:
                    window.restore_view_state(self.view_states[record.run_id])
            elif request.purpose == "working":
                if physics_hash(result.configuration, resolve_layout=False) != physics_hash(window.project):
                    lease.close()
                    return
                old, self.working_lease = self.working_lease, lease
                window.result = result
                self.record = record
                window.render_result(result)
                self.configure_time(record, request.frame_index)
                if old:
                    release_after.append(old)
            else:
                if not self.record or self.record.run_id != record.run_id:
                    lease.close()
                    return
                state = window.capture_view_state()
                context = window.time_history.context()
                window.result = result
                window.render_result(result)
                if window.inspection_context:
                    old, self.inspection_lease = self.inspection_lease, lease
                else:
                    old, self.working_lease = self.working_lease, lease
                if old:
                    release_after.append(old)
                if request.frame_index is not None:
                    self.configure_time(record, request.frame_index)
                    window.restore_viewport(state)
                else:
                    if result.transient:
                        window.time_navigation.slider.setValue(state["snapshot_index"])
                    window.time_history.restore_context(context)
                    window.restore_viewport(state)
                    if request.purpose == "range":
                        window.history_range.setChecked(True)
                        window.update_overlay()
                    elif request.purpose == "export-history":
                        window.time_history.export_history()
                    elif request.purpose == "share":
                        window.share_export()
                    elif request.purpose == "bundle":
                        window.export_results()
            self.refresh()
            window.statusBar().showMessage("Exact stored frame loaded and validated" if request.frame_index is not None
                                           else "Recorded run loaded and validated", 8000)
        except Exception as error:
            try:
                self.restore_context(previous)
            except Exception as restoration:
                self.issue(f"{error}; repainting the previous context also failed: {restoration}")
            else:
                self.issue(str(error))
            finally:
                lease.close()
        else:
            for old in release_after:
                old.close()

    def capture_context(self):
        window = self.window
        names = ("project", "result", "path", "_dirty", "undo_history", "redo_history", "inspection_context",
                 "mesh", "transient_frame")
        return ({name: getattr(window, name) for name in names}, window.capture_view_state(),
                window.guided.capture_context(), self.record, self.working_lease, self.inspection_lease,
                window.inspection_label.text(), window.time_history.context())

    def restore_context(self, context):
        values, view, workspace, self.record, self.working_lease, self.inspection_lease, label, trace = context
        window = self.window
        window.cancel_geometry()
        for name, value in values.items():
            setattr(window, name, value)
        window.inspection_label.setText(label)
        window.inspection_bar.setVisible(window.inspection_context is not None)
        window.refresh_project(preserve_view=True)
        if window.result:
            window.render_result(window.result)
            if self.record and self.record.times and not window.result.transient:
                time = window.result.metrics.get("time_s", self.record.times[-1])
                index = min(range(len(self.record.times)), key=lambda item: abs(self.record.times[item] - time))
                self.configure_time(self.record, index)
        window.time_history.restore_context(trace)
        window.guided.restore_context(workspace)
        window.restore_view_state(view)
        window.set_running(False)

    def configure_time(self, record, index=None):
        window = self.window
        if record.times:
            window.render_result_metrics(window.view_source_result() or window.result)
            window.time_navigation.set_record(record, index=index)
            window.time_history.set_record(record, metadata=self.store.transient_metadata(record.run_id))
            if window.result and not window.result.transient and window.history_range.isChecked():
                window.history_range.blockSignals(True)
                window.history_range.setChecked(False)
                window.history_range.blockSignals(False)
            if index is not None:
                window.time_history.select_time(record.times[index])
            window.result_status.setText(window.result_status.text() +
                "\nExact retained frame loaded and validated; other saved frames remain on demand.")

    def failed(self, request, error):
        if request.epoch != self.window.document_epoch:
            return
        if isinstance(error, CacheBudgetExceeded):
            answer = QMessageBox.question(self.window, "History cache budget",
                str(error) + "\nTemporarily allow this selection to exceed the cache budget?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No)
            if answer == QMessageBox.StandardButton.Yes and self.store:
                self.loader.submit(self.store, request.epoch, request.run_ids, purpose=request.purpose,
                                   frame_index=request.frame_index, allow_oversize=True)
                return
        self.issue(str(error))
        if self.record and self.record.times and self.window.result:
            time = self.window.result.metrics.get("time_s")
            if time is not None:
                index = min(range(len(self.record.times)), key=lambda item: abs(self.record.times[item] - time))
                self.window.time_navigation.set_record(self.record, index=index)
        self.refresh()

    def issue(self, message):
        window = self.window
        window.log("Recorded result unavailable: " + message)
        window.guided.last_error = "Recorded result could not be loaded"
        window.guided.last_error_detail = message + " The previous view was retained. Reopen an intact archive or remove this unavailable run explicitly."
        window.guided.last_error_stage = "results"
        window.guided.refresh_issues()
        window.guided.open_drawer(0)

    def cancel(self):
        self.loader.cancel()
        self.window.statusBar().showMessage("History read cancelled; the previous view is retained.")

    def returned(self):
        self.loader.cancel()
        if self.inspection_lease:
            self.inspection_lease.close()
            self.inspection_lease = None
        self.record = None
        if self.working_lease and self.store and self.store.current_run_id:
            self.record = self.store.record(self.store.current_run_id)
            if self.record.times:
                time = self.working_lease.result.metrics.get("time_s", self.record.times[-1])
                index = min(range(len(self.record.times)), key=lambda item: abs(self.record.times[item] - time))
                self.configure_time(self.record, index)
        self.refresh()

    def new_result(self):
        self.loader.cancel()
        self.record = None
        for name in ("working_lease", "inspection_lease"):
            lease = getattr(self, name)
            if lease:
                lease.close()
                setattr(self, name, None)

    def save_inputs(self, project, result, history):
        if not self.store:
            return result, list(history), None
        current_id = None
        retained_current = any(is_record(item) and item.run_id == self.store.current_run_id for item in history)
        if self.store.current_run_id and (result is None and retained_current
                                         or self.working_lease and result is self.working_lease.result):
            candidate = self.store.record(self.store.current_run_id)
            recorded_physics = candidate.physics_hash or physics_hash(candidate.configuration_copy(), resolve_layout=False)
            if recorded_physics == physics_hash(project):
                current_id = candidate.run_id
        ids = [item.run_id for item in history if is_record(item)]
        if current_id and current_id not in ids:
            ids.append(current_id)
        owner = self.store.capture_history(ids)
        references = {ref.run_id: ref for ref in owner.references}
        saved_result = references[current_id] if current_id else result
        saved_history = [references[item.run_id] if is_record(item) else item for item in history
                         if not (is_record(item) and item.current and item.run_id == current_id)]
        return saved_result, saved_history, owner

    def export_inputs(self, result, history):
        if not self.store:
            return result, list(history), None
        ids = [item.run_id for item in history if is_record(item)]
        if self.record and self.record.run_id not in ids:
            ids.append(self.record.run_id)
        owner = self.store.capture_history(ids)
        references = {ref.run_id: ref for ref in owner.references}
        archived = references[self.record.run_id] if self.record else result
        retained = [references[item.run_id] if is_record(item) else item for item in history
                    if not (is_record(item) and self.record and item.run_id == self.record.run_id)]
        return archived, retained, owner

    def close(self):
        if not self.loader.shutdown():
            return False
        self.new_result()
        if self.store:
            self.store.close()
        self.store = None
        self.view_states.clear()
        return True
