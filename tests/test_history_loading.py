# SPDX-License-Identifier: MPL-2.0
"""Real archive leases cross queued Qt replies and owned worker cancellation."""
from __future__ import annotations

import threading

import numpy as np
import pytest
from history_test_helpers import archive
from PySide6.QtCore import QCoreApplication, QEvent
from shiboken6 import isValid

from etlayout.gui.history_loading import HistoryLoader
from etlayout.io.history_store import CacheBudgetExceeded, open_history_catalog
from etlayout.models import SimulationError


@pytest.fixture
def stored_history(tmp_path):
    store = open_history_catalog(archive(tmp_path, "actual-archive"), cache_bytes=1024)
    yield store
    store.close()
    assert not store._snapshot_path.exists()


@pytest.fixture
def loading(qtbot):
    control = HistoryLoader()
    loaded, failures = [], []
    control.loaded.connect(lambda *values: loaded.append(values))
    control.failed.connect(lambda *values: failures.append(values))
    yield control, loaded, failures
    assert control.shutdown(30000)
    qtbot.waitUntil(lambda: not control.jobs, timeout=30000)
    for _, leases in loaded:
        for lease in leases:
            lease.close()


def _disposed(qtbot, control, jobs):
    qtbot.waitUntil(lambda: not control.jobs, timeout=30000)
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    assert all(not isValid(job) for job in jobs)
    assert not control.findChildren(type(jobs[0]))


@pytest.mark.parametrize("supersede", [False, True], ids=["cancel", "replace"])
def test_queued_success_releases_stale_leases_before_display(
        qtbot, stored_history, loading, monkeypatch, supersede):
    control, loaded, failures = loading
    acquired = []
    acquire = stored_history.acquire

    def observed(*args, **options):
        lease = acquire(*args, **options)
        acquired.append(lease)
        return lease

    monkeypatch.setattr(stored_history, "acquire", observed)
    first = control.submit(stored_history, "working-epoch", [stored_history.current_run_id])
    old_job = control.jobs[0]
    # Finish real decoding without dispatching the queued GUI success yet.
    assert old_job.wait(30000)
    assert stored_history.stats.pinned_bytes > 0 and not loaded
    if supersede:
        latest = control.submit(stored_history, "working-epoch", [stored_history.current_run_id])
    else:
        control.cancel()
    jobs = tuple(control.jobs)
    _disposed(qtbot, control, jobs)
    assert acquired[0].closed
    assert not failures and all(request != first for request, _ in loaded)
    if supersede:
        assert len(loaded) == 1 and loaded[0][0] == latest
        np.testing.assert_array_equal(loaded[0][1][0].result.fields["a"]["temperature"], [300., 301., 302.])
        assert not loaded[0][1][0].closed
        loaded[0][1][0].close()
    else:
        assert not loaded
    assert stored_history.stats.pinned_bytes == 0


@pytest.mark.parametrize("supersede", [False, True], ids=["cancel", "replace"])
def test_queued_failure_from_obsolete_request_does_not_replace_latest_context(
        qtbot, stored_history, loading, supersede):
    control, loaded, failures = loading
    control.submit(stored_history, "working-epoch", ["missing-run-in-this-generation"])
    assert control.jobs[0].wait(30000)
    if supersede:
        latest = control.submit(stored_history, "working-epoch", [stored_history.current_run_id])
    else:
        control.cancel()
    _disposed(qtbot, control, tuple(control.jobs))
    assert not failures
    assert len(loaded) == int(supersede)
    if supersede:
        assert loaded[0][0] == latest
    else:
        assert stored_history.stats.pinned_bytes == 0


def test_partial_multi_run_failure_releases_already_acquired_lease(qtbot, stored_history, loading, monkeypatch):
    control, loaded, failures = loading
    acquired = []
    acquire = stored_history.acquire

    def observed(*args, **options):
        lease = acquire(*args, **options)
        acquired.append(lease)
        return lease

    monkeypatch.setattr(stored_history, "acquire", observed)
    request = control.submit(stored_history, "working-epoch", [stored_history.current_run_id, "absent-run"])
    _disposed(qtbot, control, tuple(control.jobs))
    assert not loaded and len(failures) == 1 and failures[0][0] == request
    error = failures[0][1]
    assert isinstance(error, SimulationError) and "not in this archive generation" in str(error)
    assert error.__traceback__ is None and error.__cause__ is None and error.__context__ is None
    assert len(acquired) == 1 and acquired[0].closed
    assert stored_history.stats.pinned_bytes == 0


@pytest.mark.parametrize("allow_oversize", [False, True], ids=["reject", "explicit-approval"])
def test_cache_budget_is_not_silently_enlarged(qtbot, stored_history, loading, allow_oversize):
    control, loaded, failures = loading
    stored_history.set_budget(1)
    control.submit(stored_history, "working-epoch", [stored_history.current_run_id], allow_oversize=allow_oversize)
    _disposed(qtbot, control, tuple(control.jobs))
    assert stored_history.stats.budget_bytes == 1
    if allow_oversize:
        assert len(loaded) == 1 and not failures and stored_history.stats.over_budget_bytes > 0
        lease = loaded[0][1][0]
        values = lease.result.fields["a"]["temperature"]
        np.testing.assert_array_equal(values, [300., 301., 302.])
        assert not values.flags.writeable
        lease.close()
        assert stored_history.stats.resident_bytes <= 1
    else:
        assert not loaded and len(failures) == 1
        error = failures[0][1]
        assert isinstance(error, CacheBudgetExceeded) and error.required_bytes > error.budget_bytes == 1
        assert error.pinned_bytes == 0 and error.__traceback__ is None
        assert stored_history.stats.pinned_bytes == stored_history.stats.resident_bytes == 0


def test_shutdown_has_one_deadline_and_retains_running_workers(qtbot, stored_history, loading, monkeypatch):
    control, loaded, failures = loading
    entered = [threading.Event(), threading.Event()]
    release = threading.Event()
    acquired, waits = [], []
    acquire = stored_history.acquire
    lock = threading.Lock()

    def held(*args, **options):
        lease = acquire(*args, **options)
        with lock:
            index = len(acquired)
            acquired.append(lease)
        entered[index].set()
        if not release.wait(15):
            lease.close()
            raise RuntimeError("Owned test reader was not released")
        return lease

    monkeypatch.setattr(stored_history, "acquire", held)
    jobs = []
    try:
        for index in range(2):
            control.submit(stored_history, "working-epoch", [stored_history.current_run_id])
            qtbot.waitUntil(entered[index].is_set, timeout=30000)
            job = control.jobs[-1]
            jobs.append(job)
            native_wait = job.wait

            def observed_wait(timeout, native_wait=native_wait):
                waits.append(timeout)
                return native_wait(timeout)

            monkeypatch.setattr(job, "wait", observed_wait)
        # Neither archive read is forcibly killed or deleted when the budget expires.
        assert not control.shutdown(50)
        assert len(waits) == 1 and 0 <= waits[0] <= 50
        assert all(isValid(job) and job.isRunning() for job in jobs)
    finally:
        release.set()
        _disposed(qtbot, control, jobs)
    assert all(lease.closed for lease in acquired)
    assert not loaded and not failures and stored_history.stats.pinned_bytes == 0
