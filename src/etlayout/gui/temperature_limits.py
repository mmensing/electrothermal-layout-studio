# SPDX-License-Identifier: MPL-2.0
"""Compact project maximum-temperature controls; Kelvin is the saved contract."""
from copy import deepcopy

from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QVBoxLayout,
)

from etlayout.gui.inputs import parse_number
from etlayout.models import TemperatureLimit
from etlayout.simulation.temperature import validate_temperature_limits


class TemperatureLimitsDialog(QDialog):
    def __init__(self, project, parent=None, *, mask_keys=()):
        super().__init__(parent)
        self.project = deepcopy(project)
        self.setWindowTitle("Maximum operating temperature")
        self.resize(600, 340)
        outer = QVBoxLayout(self)
        note = QLabel("A temperature at or above its limit fails engineering acceptance. "
            "Converged fields remain available. The example 150 °C is a user setting, not a certified device rating.")
        note.setWordWrap(True)
        outer.addWidget(note)
        self.global_limit = next((limit for limit in self.project.temperature_limits
                                  if not limit.layer_ids and limit.geometry is None), None)
        self.scoped = [limit for limit in self.project.temperature_limits if limit is not self.global_limit]
        initial = self.global_limit or TemperatureLimit()
        form = QFormLayout()
        self.enabled = QCheckBox("Enforce over all active thermal sheets")
        self.enabled.setChecked(self.global_limit is not None and initial.enabled)
        form.addRow("Global limit", self.enabled)
        row = QHBoxLayout()
        self.maximum = QLineEdit(f"{initial.maximum_temperature - 273.15:.12g}")
        self.maximum.setAccessibleName("Maximum absolute temperature")
        self.units = QComboBox()
        self.units.addItems(["°C", "K"])
        self.units.setAccessibleName("Maximum temperature units")
        row.addWidget(self.maximum, 1)
        row.addWidget(self.units)
        form.addRow("Maximum temperature", row)
        self.policy = QComboBox()
        self.policy.addItem("Fail after calculation; retain complete run", "fail_after")
        self.policy.addItem("Stop at first violating accepted transient state", "stop_transient")
        self.policy.setCurrentIndex(self.policy.findData(initial.evaluation))
        form.addRow("When violated", self.policy)
        outer.addLayout(form)
        self.units.currentTextChanged.connect(self.change_units)
        self.scope_summary = QLabel()
        self.scope_summary.setWordWrap(True)
        outer.addWidget(self.scope_summary)
        edit = QPushButton("Layer / selected-region limits…")
        def edit_scoped():
            from etlayout.gui.power_forms import ModelCollectionDialog
            dialog = ModelCollectionDialog(TemperatureLimit, self.scoped, self, mask_keys=mask_keys)
            if dialog.exec():
                self.scoped = dialog.entries
                self.update_summary()
        edit.clicked.connect(edit_scoped)
        outer.addWidget(edit)
        self.update_summary()
        help_text = QLabel("Scoped limits use SI kelvin and exact selected area support. "
            "Existing engineering warning thresholds remain warnings; measurement constraints retain their named aggregation. "
            "Hard limits use the unsmoothed physical P1 maximum and require independent mesh/time verification.")
        help_text.setWordWrap(True)
        outer.addWidget(help_text)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        outer.addWidget(buttons)
        self.limits = deepcopy(project.temperature_limits)

    def update_summary(self):
        self.scope_summary.setText(f"{len(self.scoped)} additional layer / region limits configured.")

    def change_units(self, unit):
        try:
            value = parse_number(self.maximum.text())
            self.maximum.setText(f"{value + (273.15 if unit == 'K' else -273.15):.12g}")
        except ValueError:
            pass

    def accept(self):
        try:
            limits = deepcopy(self.scoped)
            if self.enabled.isChecked() or self.global_limit is not None:
                value = parse_number(self.maximum.text())
                if self.units.currentText() == "°C":
                    value += 273.15
                limit = deepcopy(self.global_limit) if self.global_limit else TemperatureLimit()
                limit.enabled = self.enabled.isChecked()
                limit.maximum_temperature = value
                limit.evaluation = self.policy.currentData()
                limits.insert(0, limit)
            self.project.temperature_limits = limits
            errors = validate_temperature_limits(self.project)
            if errors:
                raise ValueError("\n".join(item.message for item in errors))
            self.limits = limits
        except ValueError as exc:
            QMessageBox.warning(self, "Invalid temperature limit", str(exc))
            return
        super().accept()
