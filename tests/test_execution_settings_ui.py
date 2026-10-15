# SPDX-License-Identifier: MPL-2.0
"""Execution preferences expose one bounded CPU budget without changing physics."""
from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QDialog, QTabWidget

from etlayout.gui.execution_settings import ExecutionSettingsDialog
from etlayout.simulation.execution import ExecutionSettings


def test_cpu_budget_bounds_workers_and_returns_only_accepted_settings(qtbot, monkeypatch):
    monkeypatch.setattr("etlayout.simulation.execution._cpu_count", lambda: 8)
    original = ExecutionSettings(cpu_threads=4, study_workers=4)
    dialog = ExecutionSettingsDialog(original, capabilities={"cpu_count": 8, "threadpoolctl_available": True})
    qtbot.addWidget(dialog)
    assert dialog.cpu_threads.value() == 4
    assert dialog.cpu_threads.maximum() == 8
    assert dialog.study_workers.value() == 4
    assert not dialog.findChildren(QTabWidget)
    dialog.cpu_threads.setValue(2)
    assert dialog.study_workers.value() == 2
    assert "1 assembly/native thread per worker" in dialog.cpu_note.text()
    dialog.study_workers.setValue(1)
    assert "2 assembly/native threads per worker" in dialog.cpu_note.text()
    assert original.cpu_threads == original.study_workers == 4
    dialog.accept()
    assert dialog.result() == QDialog.DialogCode.Accepted
    assert dialog.settings.to_dict() == {"cpu_threads": 2, "study_workers": 1}


def test_cancel_and_missing_control_support_are_explicit(qtbot):
    original = ExecutionSettings(cpu_threads=2, study_workers=1)
    dialog = ExecutionSettingsDialog(original, capabilities={"cpu_count": 8, "threadpoolctl_available": False})
    qtbot.addWidget(dialog)
    assert "thread-pool control is unavailable" in dialog.cpu_note.text()
    dialog.cpu_threads.setValue(8)
    dialog.reject()
    assert dialog.settings is original
    assert dialog.result() == QDialog.DialogCode.Rejected


def test_capability_change_between_open_and_accept_leaves_validation_visible(qtbot, monkeypatch):
    dialog = ExecutionSettingsDialog(ExecutionSettings(cpu_threads=4, study_workers=1),
                                     capabilities={"cpu_count": 8, "threadpoolctl_available": True})
    qtbot.addWidget(dialog)
    monkeypatch.setattr("etlayout.simulation.execution._cpu_count", lambda: 2)
    dialog.accept()
    assert dialog.result() != QDialog.DialogCode.Accepted
    assert "exceeds 2 available" in dialog.error.text()
