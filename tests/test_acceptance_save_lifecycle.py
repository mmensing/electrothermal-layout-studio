# SPDX-License-Identifier: MPL-2.0
"""Acceptance journeys must wait for durable queued saves before reopening/closing."""
from __future__ import annotations

import threading
from copy import deepcopy

import numpy as np
from gui_test_helpers import solved_e1 as solved_e1
from PySide6.QtCore import QCoreApplication, QEvent, QTimer
from PySide6.QtWidgets import QApplication

from etlayout.gui.main_window import MainWindow
from etlayout.io.identity import document_id
from etlayout.io.project import load_project, load_run_history
from scripts import guided_workflow_acceptance as acceptance
from scripts.desktop_acceptance_support import wait_desktop_events


def test_acceptance_waits_for_active_and_queued_real_archive_saves(qtbot, tmp_path, monkeypatch, solved_e1):
    import etlayout.gui.saving as saving

    project, result = solved_e1
    project = deepcopy(project)
    project.name = "first saved revision"
    window = MainWindow(project, result, database_path=tmp_path / "materials.sqlite3", recover=False)
    window.autosave_timer.stop()
    window.path = tmp_path / "queued-project.etlayout"
    window.show()
    entered, release = threading.Event(), threading.Event()
    original_writer = saving.save_project
    calls = []

    def delayed_writer(project, *args, **kwargs):
        calls.append(project.name)
        entered.set()
        assert release.wait(10), "The test did not release its owned save worker"
        return original_writer(project, *args, **kwargs)

    monkeypatch.setattr(saving, "save_project", delayed_writer)
    heartbeats = []
    timer = QTimer(window)
    timer.setInterval(10)
    timer.timeout.connect(lambda: heartbeats.append(True))
    try:
        acceptance.wait_for_workers(window, wait_desktop_events, timeout=30.)
        window.dirty = True
        assert window.save()  # Accepted asynchronous submission is not durable yet.
        qtbot.waitUntil(entered.is_set, timeout=5000)
        assert window.save_job and not window.path.exists()
        window.project.name = "latest queued revision"
        window.dirty = True
        assert window.save() and len(window._save_queue) == 1
        identity = document_id(window.project)
        timer.start()
        QTimer.singleShot(120, release.set)
        acceptance.wait_for_workers(window, wait_desktop_events, timeout=10.)
        timer.stop()
        assert heartbeats and release.is_set() and window.save_job is None and not window._save_queue
        assert window._save_success and not window.dirty
        reopened, saved_result = load_project(window.path)
        assert saved_result is not None and reopened.name == "latest queued revision"
        assert document_id(reopened) == identity
        assert saved_result.configuration_hash == result.configuration_hash
        layer, fields = next(iter(result.fields.items()))
        field = next(iter(fields))
        np.testing.assert_array_equal(saved_result.fields[layer][field], fields[field])
        history = load_run_history(window.path)
        assert len(history) == 1 and history[0].configuration_hash == result.configuration_hash
        np.testing.assert_array_equal(history[0].fields[layer][field], fields[field])
        assert calls == ["first saved revision", "latest queued revision"]
        assert QApplication.activeModalWidget() is None
        assert window.close() and not window.isVisible()
    finally:
        timer.stop()
        release.set()
        window.cancel_saving()
        qtbot.waitUntil(lambda: not window.save_job and not window._save_queue, timeout=10000)
        window._dirty = False
        window.close()
        window.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        QApplication.processEvents()
