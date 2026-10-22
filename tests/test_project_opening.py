# SPDX-License-Identifier: MPL-2.0
"""Actual archive decoding and commit guards for the default opening workflow."""
from __future__ import annotations

import threading
from collections import deque
from copy import deepcopy
from pathlib import Path

import numpy as np
import pytest
from gui_test_helpers import _click, _fill, _idle
from gui_test_helpers import guided_window as guided_window
from gui_test_helpers import solved_e1 as solved_e1
from PySide6.QtCore import QCoreApplication, QEvent, Qt, QTimer
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QMessageBox, QPushButton
from shiboken6 import isValid

from etlayout.gui import history_opening
from etlayout.gui.history_opening import HistoryOpenJob
from etlayout.gui.resources import ResourceReviewDialog
from etlayout.io.project import configuration_hash, load_project, physics_hash, save_json, save_project
from etlayout.io.resources import inspect_project

pytestmark = pytest.mark.desktop


def finished(window, qtbot):
    qtbot.waitUntil(lambda: not window.history_opener.jobs and not window.history_workflow.loader.jobs,
                    timeout=30000)
    _idle(window, qtbot)


def approve_resources(window, reviews=None):
    """Authorize through the real modal and button, without replacing Qt methods."""
    timer = QTimer(window)
    timer.setInterval(5)

    def choose():
        dialog = QApplication.activeModalWidget()
        if not isinstance(dialog, ResourceReviewDialog):
            return
        timer.stop()
        try:
            if reviews is not None:
                reviews.append(threading.get_ident())
            for row in range(dialog.table.rowCount()):
                dialog.table.item(row, 0).setCheckState(Qt.CheckState.Checked)
            button = next(item for item in dialog.findChildren(QPushButton)
                          if item.text() == "Open approved resources")
            QTest.mouseClick(button, Qt.MouseButton.LeftButton)
        except BaseException:
            dialog.reject()
            raise

    timer.timeout.connect(choose)
    timer.start()
    return timer


def documented_history(result):
    # One actual solved physical state with separately documented provenance.
    # Equal source fields do not imply identical configuration records.
    previous = deepcopy(result)
    previous.configuration["notes"] = "Earlier saved documentation of this solved physical state"
    previous.configuration_hash = configuration_hash(previous.configuration)
    return previous


@pytest.fixture
def eager_archive(tmp_path, solved_e1):
    project, result = deepcopy(solved_e1)
    target = tmp_path / "recorded.etlayout"
    save_project(project, target, result, history=[documented_history(result)], archive_format=3)
    return target, result


@pytest.mark.parametrize("version", ["json", 2, 3])
def test_default_open_keeps_eager_fields_and_all_recorded_runs(guided_window, qtbot, tmp_path, solved_e1, version):
    window = guided_window
    assert not window.history_lazy_enabled
    project, result = deepcopy(solved_e1)
    source = tmp_path / ("source.json" if version == "json" else "source.etlayout")
    if version == "json":
        layout = tmp_path / "layout.gds"
        layout.write_bytes(Path(project.layout.path).read_bytes())
        project.layout.path = str(layout)
        save_json(project, source)
    else:
        save_project(project, source, result, history=[documented_history(result)], archive_format=version)
    old = window.project
    window.open_project(source)
    assert window.project is old  # The opening request itself never replaces the document.
    finished(window, qtbot)
    assert window.path == source and window.project is not old
    assert window.history_workflow.store is None
    assert physics_hash(window.project) == physics_hash(project)
    if version == "json":
        assert window.result is None and not window.run_history
    else:
        assert window.result.converged and len(window.run_history) == 2
        assert window.result.configuration_hash == result.configuration_hash
        for layer, fields in result.fields.items():
            for name, values in fields.items():
                np.testing.assert_array_equal(window.result.fields[layer][name], values)


@pytest.mark.parametrize("cancel", [False, True])
def test_eager_read_leaves_event_loop_live_and_rejects_cancelled_or_stale_completion(
        guided_window, qtbot, eager_archive, monkeypatch, cancel):
    window = guided_window
    old = window.project
    source, result = eager_archive
    entered, release = threading.Event(), threading.Event()
    decode = history_opening.resolve_project
    worker_threads, ticks = [], []
    def held_decode(*args, **options):
        decoded = decode(*args, **options)
        worker_threads.append(threading.get_ident())
        entered.set()
        if not release.wait(10):
            raise RuntimeError("Test reader was not released")
        return decoded
    monkeypatch.setattr(history_opening, "resolve_project", held_decode)
    timer = QTimer()
    timer.setInterval(5)
    timer.timeout.connect(lambda: ticks.append(1))
    timer.start()
    try:
        window.open_project(source)
        qtbot.waitUntil(entered.is_set, timeout=30000)
        qtbot.wait(60)
        assert len(ticks) >= 2 and window.project is old
        assert worker_threads == [worker_threads[0]] and worker_threads[0] != threading.get_ident()
        jobs = list(window.history_opener.jobs)
        if cancel:
            window.cancel_catalog()
            retained = old
        else:
            retained = deepcopy(old)
            retained.name = "Newer working context"
            window.set_project(retained)
        release.set()
        finished(window, qtbot)
        assert window.project is retained and window.result is None
        assert window.path is None and not window.guided.last_error
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        assert all(not isValid(job) for job in jobs)
        assert not window.findChildren(HistoryOpenJob)
        assert result.converged  # The actual saved numerical result was decoded, then discarded.
    finally:
        release.set()
        timer.stop()
        finished(window, qtbot)


@pytest.mark.parametrize("drawer_visible", [False, True])
def test_eager_decode_error_is_visible_and_retains_working_context(guided_window, qtbot, tmp_path, drawer_visible):
    window = guided_window
    window.guided.panels.set_visible("drawer", drawer_visible)
    QApplication.processEvents()
    source = tmp_path / "broken.etlayout"
    source.write_bytes(b"not a project archive")
    project, view = window.project, window.capture_view_state()
    window.open_project(source)
    finished(window, qtbot)
    assert window.project is project and window.result is None and window.capture_view_state() == view
    assert window.guided.last_error == "Project could not be opened"
    assert window.guided.last_error_stage == "layout"
    assert "Opening the selected project failed" in window.guided.last_error_detail
    assert "working project and selected result were retained" in window.guided.last_error_detail
    assert "remove this unavailable run" not in window.guided.last_error_detail
    assert window.guided.issue_table.isVisible()
    displayed = window.guided.issue_table.item(0, 1).text()
    assert "Project could not be opened" in displayed
    assert "computation failed" not in displayed.lower()
    assert "result could not be loaded" not in displayed.lower()


@pytest.mark.parametrize("answer", ["Apply", "Discard", "Stay"])
def test_real_draft_decision_precedes_default_open(guided_window, qtbot, eager_archive, tmp_path, answer):
    window = guided_window
    _click(window, window.guided.stage_buttons["layers"])
    _fill(window, "thickness", "3.5")
    old, editor, geometry = window.project, window.guided.editor, window.geometry
    before = old.to_dict()
    window.path = tmp_path / "working.etlayout"
    remaining = deque([answer, "Save"] if answer == "Apply" else [answer])
    answered = []
    def choose():
        modal = QApplication.activeModalWidget()
        assert isinstance(modal, QMessageBox)
        selected = remaining.popleft()
        answered.append((modal.windowTitle(), selected))
        button = next(item for item in modal.buttons() if item.text().replace("&", "") == selected)
        if remaining:
            QTimer.singleShot(0, choose)
        QTest.mouseClick(button, Qt.MouseButton.LeftButton)
    QTimer.singleShot(0, choose)
    window.open_project(eager_archive[0])
    finished(window, qtbot)
    if answer == "Apply":
        qtbot.waitUntil(lambda: window.project is not old, timeout=30000)
        finished(window, qtbot)
        saved, _ = load_project(tmp_path / "working.etlayout")
        assert saved.layer("metal").thickness == pytest.approx(3.5e-6)
        assert ("Unsaved project", "Save") in answered
    elif answer == "Discard":
        assert window.project is not old and old.to_dict() == before
    else:
        assert window.project is old and window.guided.editor is editor
        assert window.geometry is geometry and editor.has_changes()
        assert old.to_dict() == before and not window.history_opener.jobs
    assert ("Unapplied draft", answer) in answered and not remaining


@pytest.mark.parametrize("version,lazy", [(2, False), (3, False), (3, True)])
def test_external_approval_occurs_on_gui_and_resolution_remains_in_worker(
        guided_window, qtbot, tmp_path, solved_e1, monkeypatch, version, lazy):
    window = guided_window
    window.history_lazy_enabled = lazy
    project, result = deepcopy(solved_e1)
    source = tmp_path / "outside-root-container" / "project.etlayout"
    save_project(project, source, result, history=[documented_history(result)], embed_layout=False, archive_format=version)
    main_thread = threading.get_ident()
    reviews, reads = [], []
    original = history_opening.resolve_project
    def observed(*args, **options):
        reads.append(threading.get_ident())
        return original(*args, **options)
    monkeypatch.setattr(history_opening, "resolve_project", observed)
    approval = approve_resources(window, reviews)
    try:
        window.open_project(source)
        finished(window, qtbot)
    finally:
        approval.stop()
        approval.deleteLater()
    assert reviews and all(item == main_thread for item in reviews)
    assert window.path == source and window.result.converged
    assert window.result.configuration_hash == result.configuration_hash
    if lazy:
        assert window.history_workflow.store is not None and not reads
    else:
        assert reads and all(item != main_thread for item in reads)
        assert window.history_workflow.store is None and len(window.run_history) == 2


def test_resource_review_retains_edits_made_during_eager_read(guided_window, qtbot, tmp_path, solved_e1, monkeypatch):
    window = guided_window
    project, result = deepcopy(solved_e1)
    source = tmp_path / "resource-root" / "project.etlayout"
    save_project(project, source, result, history=[documented_history(result)], embed_layout=False, archive_format=2)
    inspection = inspect_project(source)
    working = inspection.unresolved_project()
    window.set_project(working, None, source)
    entered, release = threading.Event(), threading.Event()
    original = history_opening.resolve_project
    def held(*args, **options):
        decoded = original(*args, **options)
        entered.set()
        if not release.wait(10):
            raise RuntimeError("Test reader was not released")
        return decoded
    monkeypatch.setattr(history_opening, "resolve_project", held)
    approval = approve_resources(window)
    try:
        window.review_project_resources()
        qtbot.waitUntil(entered.is_set, timeout=30000)
        window.before_edit()
        working.notes = "Preserve an edit during archive decoding"
        working.layers[0].thickness *= 1.2
        window.changed(geometry=False)
        release.set()
        finished(window, qtbot)
        assert window.project is working and window.dirty
        assert working.notes and working.layers[0].thickness != project.layers[0].thickness
        assert not getattr(working, "_resource_unresolved", False)
        assert window.result is None  # A field for the pre-edit physics cannot become current.
    finally:
        release.set()
        try:
            finished(window, qtbot)
        finally:
            approval.stop()
            approval.deleteLater()


@pytest.mark.parametrize("transition", ["inspect", "return"])
def test_history_navigation_cancels_pending_eager_open_without_late_context_replacement(
        guided_window, qtbot, eager_archive, solved_e1, monkeypatch, transition):
    window = guided_window
    working, result = deepcopy(solved_e1)
    window.set_project(working, result)
    finished(window, qtbot)
    historical = documented_history(result)
    if transition == "return":
        window.inspect_result(historical)
        finished(window, qtbot)
        assert window.inspection_context["project"] is working
    entered, release = threading.Event(), threading.Event()
    decode = history_opening.resolve_project
    def held_decode(*args, **options):
        decoded = decode(*args, **options)
        entered.set()
        if not release.wait(10):
            raise RuntimeError("Test reader was not released")
        return decoded
    monkeypatch.setattr(history_opening, "resolve_project", held_decode)
    try:
        window.open_project(eager_archive[0])
        qtbot.waitUntil(entered.is_set, timeout=30000)
        if transition == "inspect":
            window.inspect_result(historical)
            assert window.inspection_context["project"] is working
            retained = window.project, historical, window.inspection_context
        else:
            window.return_to_project()
            retained = working, result, None
        # Finish the intentional history transition's own geometry/camera work
        # before observing whether the obsolete archive reply changes that view.
        _idle(window, qtbot)
        view = window.capture_view_state()
        release.set()
        finished(window, qtbot)
        assert window.project is retained[0] and window.result is retained[1]
        assert window.inspection_context is retained[2] and window.capture_view_state() == view
    finally:
        release.set()
        finished(window, qtbot)


def test_close_during_eager_read_retains_window_until_owned_thread_stops(
        guided_window, qtbot, eager_archive, monkeypatch):
    window = guided_window
    old = window.project
    entered, release = threading.Event(), threading.Event()
    decode = history_opening.resolve_project
    def held_decode(*args, **options):
        decoded = decode(*args, **options)
        entered.set()
        if not release.wait(10):
            raise RuntimeError("Test reader was not released")
        return decoded
    monkeypatch.setattr(history_opening, "resolve_project", held_decode)
    try:
        window.open_project(eager_archive[0])
        qtbot.waitUntil(entered.is_set, timeout=30000)
        jobs = list(window.history_opener.jobs)
        assert not window.close()
        assert window.isVisible() and all(isValid(job) and job.isRunning() for job in jobs)
        assert "still stopping" in window.statusBar().currentMessage()
        release.set()
        finished(window, qtbot)
        assert window.project is old and window.result is None and window.isVisible()
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        assert all(not isValid(job) for job in jobs)
    finally:
        release.set()
        finished(window, qtbot)


@pytest.mark.parametrize("lazy", [False, True], ids=["eager", "on-demand"])
def test_declined_external_resource_review_keeps_document_camera_and_history(
        guided_window, qtbot, tmp_path, solved_e1, lazy):
    window = guided_window
    window.history_lazy_enabled = lazy
    project, result = deepcopy(solved_e1)
    source = tmp_path / "outside-root" / "requires-consent.etlayout"
    save_project(project, source, result, embed_layout=False, archive_format=3)
    before = (window.project, window.result, window.mesh, window.path, window.document_epoch,
              list(window.run_history), window.capture_view_state())
    rejected = []
    timer = QTimer(window)
    timer.setInterval(5)

    def reject_review():
        dialog = QApplication.activeModalWidget()
        if isinstance(dialog, ResourceReviewDialog):
            rejected.append(dialog.inspection)
            timer.stop()
            dialog.reject()

    timer.timeout.connect(reject_review)
    timer.start()
    try:
        window.open_project(source)
        finished(window, qtbot)
        assert len(rejected) == 1
        assert (window.project, window.result, window.mesh, window.path, window.document_epoch,
                list(window.run_history), window.capture_view_state()) == before
        assert not window.inspection_context and not window.guided.last_error
        assert window.project_opening.pending is None
    finally:
        timer.stop()
        timer.deleteLater()
