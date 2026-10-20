# SPDX-License-Identifier: MPL-2.0
"""Queued reader failures retain diagnostics, not deleted Qt worker frames."""
from __future__ import annotations

import threading

import numpy as np
import pytest
from gui_test_helpers import guided_window as guided_window
from PySide6.QtCore import QCoreApplication, QEvent, QObject, QTimer, Slot
from shiboken6 import isValid
from test_history_opening import archive

from etlayout.gui import history_opening
from etlayout.gui.history_loading import HistoryLoader
from etlayout.gui.history_opening import HistoryOpener
from etlayout.gui.worker_errors import detached_error
from etlayout.io.history_store import CacheBudgetExceeded, open_history_catalog
from etlayout.io.resources import ResourceAccessError
from etlayout.models import SimulationError

pytestmark = pytest.mark.desktop


class Receiver(QObject):
    def __init__(self):
        super().__init__()
        self.errors = []
        self.reviews = []
        self.loaded = []
        self.threads = []

    @Slot(object, object)
    def failed(self, request, error):
        self.threads.append(threading.get_ident())
        self.errors.append(error)
        assert error.__traceback__ is None and error.__cause__ is None and error.__context__ is None

    @Slot(object, object, object)
    def review(self, request, inspection, error):
        self.threads.append(threading.get_ident())
        self.reviews.append((inspection, error))
        assert error.__traceback__ is None and error.__cause__ is None and error.__context__ is None

    @Slot(object, object)
    def read(self, request, leases):
        self.threads.append(threading.get_ident())
        self.loaded.extend(leases)


def finish(control, qtbot):
    qtbot.waitUntil(lambda: not control.jobs, timeout=30000)
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    assert control.shutdown()


@pytest.mark.parametrize("case", ["corrupt", "eager-review", "lazy-review"])
def test_actual_open_failure_and_resource_review_release_worker_frames(qtbot, tmp_path, case):
    control, receiver = HistoryOpener(), Receiver()
    control.failed.connect(receiver.failed)
    control.review_required.connect(receiver.review)
    if case == "corrupt":
        target = tmp_path / "broken.etlayout"
        target.write_bytes(b"not a ZIP project")
    else:
        target = archive(tmp_path, case, external=True, version=3 if case == "lazy-review" else 2)
    control.submit(target, "owned-document", lazy=case == "lazy-review")
    job = control.jobs[0]
    try:
        qtbot.waitUntil(lambda: bool(receiver.errors or receiver.reviews), timeout=30000)
        finish(control, qtbot)
        assert not isValid(job) and receiver.threads == [threading.get_ident()]
        if case == "corrupt":
            assert len(receiver.errors) == 1 and isinstance(receiver.errors[0], SimulationError)
            assert str(receiver.errors[0])
        else:
            assert not receiver.errors
            inspection, error = receiver.reviews[0]
            assert isinstance(error, ResourceAccessError)
            assert error.reason == "outside-root" and error.resource.key == "layout"
            assert inspection.project.name == case and "authorization" in str(error)
    finally:
        control.cancel()
        finish(control, qtbot)
        control.deleteLater()
        receiver.deleteLater()


@pytest.mark.parametrize("case", ["missing", "budget"])
def test_actual_history_error_preserves_cache_contract_and_gui_delivery(qtbot, tmp_path, case):
    target = archive(tmp_path, case)
    control, receiver = HistoryLoader(), Receiver()
    control.failed.connect(receiver.failed)
    control.loaded.connect(receiver.read)
    with open_history_catalog(target, cache_bytes=1 if case == "budget" else 1024**2) as store:
        control.submit(store, "owned-document", [store.current_run_id if case == "budget" else "missing-run"])
        job = control.jobs[0]
        try:
            qtbot.waitUntil(lambda: bool(receiver.errors), timeout=30000)
            finish(control, qtbot)
            assert not isValid(job) and receiver.threads == [threading.get_ident()]
            error = receiver.errors[0]
            if case == "budget":
                assert isinstance(error, CacheBudgetExceeded)
                assert error.budget_bytes == 1 and error.required_bytes > 1 and error.pinned_bytes == 0
                assert "explicitly authorize" in str(error)
                control.submit(store, "owned-document", [store.current_run_id], allow_oversize=True)
                qtbot.waitUntil(lambda: bool(receiver.loaded), timeout=30000)
                finish(control, qtbot)
                np.testing.assert_array_equal(receiver.loaded[0].result.fields["a"]["temperature"], [300., 301., 302.])
                receiver.loaded[0].close()
            else:
                assert isinstance(error, SimulationError) and "not in this archive generation" in str(error)
        finally:
            control.cancel()
            finish(control, qtbot)
            for lease in receiver.loaded:
                lease.close()
            control.deleteLater()
            receiver.deleteLater()


def test_detachment_preserves_type_message_args_and_explicit_attributes():
    held = []
    try:
        try:
            raise ValueError("underlying reader failure")
        except ValueError as cause:
            error = CacheBudgetExceeded(128, 64, 32)
            error.add_note("Reviewed cache-budget diagnostic")
            raise error from cause
    except CacheBudgetExceeded as error:
        held.append(error)
        assert error.__traceback__ is not None and error.__cause__ is not None and error.__context__ is not None
    error = held.pop()
    args, message, notes = error.args, str(error), list(error.__notes__)
    assert detached_error(error) is error
    assert type(error) is CacheBudgetExceeded and error.args == args and str(error) == message
    assert error.required_bytes == 128 and error.budget_bytes == 64 and error.pinned_bytes == 32
    assert error.__notes__ == notes
    assert error.__traceback__ is None and error.__cause__ is None and error.__context__ is None


def test_window_fixture_waits_for_actual_reader_before_native_deletion(guided_window, qtbot, tmp_path, monkeypatch):
    window = guided_window
    target = archive(tmp_path, "teardown-reader")
    entered, release = threading.Event(), threading.Event()
    resolve = history_opening.resolve_project
    def held(*args, **options):
        decoded = resolve(*args, **options)
        entered.set()
        if not release.wait(10):
            raise RuntimeError("Test teardown reader was not released")
        return decoded
    monkeypatch.setattr(history_opening, "resolve_project", held)
    original_delete = window.deleteLater
    def checked_delete():
        assert not window.history_opener.jobs and not window.history_workflow.loader.jobs
        original_delete()
    monkeypatch.setattr(window, "deleteLater", checked_delete)
    window.open_project(target)
    qtbot.waitUntil(entered.is_set, timeout=30000)
    assert window.history_opener.jobs and window.history_opener.jobs[0].isRunning()
    # Return while a real reader is pending. The shared fixture must cancel and
    # keep processing Qt events before it closes/deletes the native window.
    QTimer.singleShot(60, release.set)
