# SPDX-License-Identifier: MPL-2.0
"""Desktop journeys must not solve old geometry during an asynchronous Open."""
from __future__ import annotations

import threading
from pathlib import Path

import pytest
from gui_test_helpers import guided_window as guided_window
from PySide6.QtCore import QTimer
from shiboken6 import isValid

from etlayout.gui import history_opening
from etlayout.gui.main_window import MainWindow
from etlayout.io.project import load_project
from scripts.desktop_acceptance_support import (
    assert_desktop_project,
    close_desktop_window,
    desktop_worker_activity,
    wait_desktop_workers,
)

ROOT = Path(__file__).resolve().parents[1]


def test_actual_e15_open_cannot_report_old_e1_geometry_as_ready(guided_window, qtbot, monkeypatch):
    window = guided_window
    old_project, old_geometry = window.project, window.geometry
    target = ROOT / "examples/E15_thermal_crosstalk.etl"
    expected, _ = load_project(target)
    entered, release = threading.Event(), threading.Event()
    decode = history_opening.resolve_project

    def held_decode(*args, **kwargs):
        decoded = decode(*args, **kwargs)
        entered.set()
        assert release.wait(10), "The test did not release its actual E15 reader"
        return decoded

    monkeypatch.setattr(history_opening, "resolve_project", held_decode)
    try:
        window.open_project(target)
        qtbot.waitUntil(entered.is_set, timeout=30000)
        with pytest.raises(TimeoutError, match="project reader"):
            wait_desktop_workers(window, timeout=.15)
        assert entered.is_set()
        assert window.project is old_project and window.geometry is old_geometry
        assert window.job is None and "project reader" in desktop_worker_activity(window)
        with pytest.raises(AssertionError):
            assert_desktop_project(window, target, expected)
        release.set()
        wait_desktop_workers(window, timeout=30.)
        assert_desktop_project(window, target, expected)
        assert window.project is not old_project and window.geometry is not old_geometry
        assert window.job is None and not desktop_worker_activity(window)
    finally:
        release.set()
        window.cancel_catalog()
        wait_desktop_workers(window, timeout=30.)


def test_acceptance_cleanup_joins_cancelled_reader_before_window_teardown(qtbot, tmp_path, monkeypatch):
    project, _ = load_project(ROOT / "examples/E1_straight.etl")
    window = MainWindow(project, database_path=tmp_path / "materials.sqlite3", recover=False)
    window.autosave_timer.stop()
    window.show()
    wait_desktop_workers(window, timeout=30.)
    entered, release = threading.Event(), threading.Event()
    decode = history_opening.resolve_project

    def held_decode(*args, **kwargs):
        decoded = decode(*args, **kwargs)
        entered.set()
        assert release.wait(10), "The test did not release its cancelled reader"
        return decoded

    monkeypatch.setattr(history_opening, "resolve_project", held_decode)
    window.open_project(ROOT / "examples/E15_thermal_crosstalk.etl")
    try:
        qtbot.waitUntil(entered.is_set, timeout=30000)
        with pytest.raises(TimeoutError):
            wait_desktop_workers(window, timeout=.15)
        assert entered.is_set()
        QTimer.singleShot(120, release.set)
        readers = list(window.history_opener.jobs)
        close_desktop_window(window, timeout=10.)
        assert release.is_set() and not isValid(window)
        assert all(not isValid(reader) for reader in readers)
    finally:
        release.set()
        if isValid(window):
            close_desktop_window(window)


def test_idle_wrong_requested_project_is_rejected(guided_window):
    window = guided_window
    expected, _ = load_project(ROOT / "examples/E15_thermal_crosstalk.etl")
    wait_desktop_workers(window, timeout=30.)
    with pytest.raises(AssertionError):
        assert_desktop_project(window, ROOT / "examples/E15_thermal_crosstalk.etl", expected)


@pytest.mark.parametrize("expected", [False, True])
def test_acceptance_modal_driver_is_explicit_and_bounded(guided_window, expected):
    from PySide6.QtWidgets import QMessageBox

    window = guided_window
    dialog = QMessageBox(QMessageBox.Icon.Information, "Acceptance modal", "Actual Qt modal", parent=window)
    QTimer.singleShot(10, dialog.exec)
    if expected:
        QTimer.singleShot(100, dialog.accept)
        wait_desktop_workers(window, timeout=2., allow_modal=lambda modal: modal is dialog)
    else:
        with pytest.raises(RuntimeError, match="Unexpected desktop dialog: Actual Qt modal"):
            wait_desktop_workers(window, timeout=2.)
    assert not dialog.isVisible()
    dialog.deleteLater()
