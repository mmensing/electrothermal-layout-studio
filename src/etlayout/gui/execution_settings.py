# SPDX-License-Identifier: MPL-2.0
"""Machine-local CPU budgets for solves and parallel parameter studies."""
from __future__ import annotations

from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QGroupBox,
    QLabel,
    QSpinBox,
    QVBoxLayout,
)

from etlayout.models import SimulationError
from etlayout.simulation.execution import ExecutionSettings, detect_capabilities, validate_execution


class ExecutionSettingsDialog(QDialog):
    """Cancel leaves caller settings unchanged; only accepted settings are returned."""

    def __init__(self, settings=None, parent=None, *, capabilities=None):
        super().__init__(parent)
        self.settings = settings or ExecutionSettings()
        self.capabilities = detect_capabilities() if capabilities is None else capabilities
        self.setWindowTitle("Execution settings")
        self.resize(550, 370)
        layout = QVBoxLayout(self)
        intro = QLabel("Choose how calculations use this computer. These settings apply to future runs and are saved on this computer.")
        intro.setWordWrap(True)
        layout.addWidget(intro)
        cpu_group = QGroupBox("CPU and studies")
        form = QFormLayout(cpu_group)
        self.cpu_threads = QSpinBox()
        self.cpu_threads.setRange(1, max(1, self.capabilities.get("cpu_count") or 1))
        self.cpu_threads.setValue(self.settings.cpu_threads)
        self.cpu_threads.setAccessibleName("Total CPU thread budget")
        self.cpu_threads.setToolTip("Maximum assembly workers and native numerical threads across the solve or study. Simultaneous study workers share this budget.")
        form.addRow("Total CPU thread budget", self.cpu_threads)
        self.study_workers = QSpinBox()
        self.study_workers.setRange(1, self.cpu_threads.value())
        self.study_workers.setValue(self.settings.study_workers)
        self.study_workers.setAccessibleName("Parallel study worker limit")
        self.study_workers.setToolTip("Maximum simultaneous study points. Fewer points use fewer workers. Each worker needs its own solver memory.")
        form.addRow("Parallel study worker limit", self.study_workers)
        self.cpu_note = QLabel()
        self.cpu_note.setWordWrap(True)
        self.cpu_note.setAccessibleName("CPU budget allocation")
        form.addRow(self.cpu_note)
        layout.addWidget(cpu_group)
        note = QLabel("Larger electrical and thermal assemblies use worker threads within this budget. Small assemblies, geometry preparation and SuperLU sparse factorization remain serial. CPU activity varies by phase; more threads do not guarantee faster runs. Parallel studies also require more memory.")
        note.setWordWrap(True)
        layout.addWidget(note)
        self.error = QLabel()
        self.error.setWordWrap(True)
        self.error.setAccessibleName("Execution settings validation")
        self.error.setProperty("error", True)
        layout.addWidget(self.error)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
        self.cpu_threads.valueChanged.connect(self.update_status)
        self.study_workers.valueChanged.connect(self.update_status)
        self.update_status()

    def update_status(self, *_):
        budget = self.cpu_threads.value()
        self.study_workers.setMaximum(budget)
        workers = self.study_workers.value()
        per_worker = budget // workers
        available = self.capabilities.get("cpu_count") or 1
        self.cpu_note.setText(
            f"Available logical CPUs: {available}. Single run: up to {budget} assembly workers or native threads. "
            f"At {workers} simultaneous study {'point' if workers == 1 else 'points'}: "
            f"up to {per_worker} assembly/native {'thread' if per_worker == 1 else 'threads'} per worker. "
            "Fewer active workers receive a larger share of the budget."
            + (" Native thread-pool control is unavailable; install the project dependencies before running."
               if not self.capabilities.get("threadpoolctl_available", False) else ""))

    def accept(self):
        try:
            settings = ExecutionSettings(cpu_threads=self.cpu_threads.value(), study_workers=self.study_workers.value())
            self.settings = validate_execution(settings)
            super().accept()
        except (SimulationError, ValueError) as error:
            self.error.setText(str(error))
