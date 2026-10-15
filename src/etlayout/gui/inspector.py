# SPDX-License-Identifier: MPL-2.0
"""Inline, short-lived typed drafts reusing the established model form parser."""
from copy import deepcopy
from dataclasses import asdict

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from etlayout.gui.forms import ModelDialog, region_summary

COMMON = {
    "PhysicalLayer": {"id", "name", "material_id", "role", "thickness", "z", "electrical"},
    "Terminal": {"id", "name", "layer_id", "kind", "value"},
    "PhysicalPort": {"id", "name", "kind", "value", "landings"},
    "Interface": {"id", "layer_a", "layer_b", "model_kind", "electrical_kind", "electrical_value",
                  "thermal_kind", "thermal_value"},
    "ThermalBoundary": {"id", "layer_id", "kind", "value", "ambient"},
    "HeatSource": {"id", "layer_id", "kind", "value"},
    "RefinementRegion": {"id", "size"},
    "SolverSettings": {"initial_temperature", "max_iterations", "relative_tolerance", "relaxation"},
    "MeshSettings": {"max_size", "min_size", "contact_size", "max_elements", "max_dofs"},
}


class InlineInspector(QWidget):
    applied = Signal(object)
    cancelled = Signal()
    changed = Signal()
    pick_requested = Signal()
    numeric_requested = Signal()

    def __init__(self, model, choices, mask_keys, *, is_new=False, parent=None):
        super().__init__(parent)
        self.original = deepcopy(model)
        self.dirty = False
        self.layout_ = QVBoxLayout(self)
        self.layout_.setContentsMargins(0, 0, 0, 0)
        self.draft_label = QLabel("New object · not yet applied" if is_new else "Applied values · edit to create a draft")
        self.draft_label.setProperty("hint", True)
        self.draft_label.setWordWrap(True)
        self.layout_.addWidget(self.draft_label)
        self.form = ModelDialog(model, parent=self, choices=choices, mask_keys=mask_keys,
                                readonly=() if is_new else ("id",))
        self.form.setWindowFlags(Qt.WindowType.Widget)
        self.form.setMinimumSize(0, 0)
        self.form.setMaximumWidth(16777215)
        self.form.layout().setContentsMargins(0, 0, 0, 0)
        self.form.layout().itemAt(0).widget().hide()  # Concise contextual help lives above the form.
        self.form.findChild(QDialogButtonBox).hide()
        form_layout = self.form.scroll.widget().layout()
        form_layout.setContentsMargins(0, 4, 0, 4)
        form_layout.setRowWrapPolicy(QFormLayout.RowWrapPolicy.WrapLongRows)
        form_layout.setHorizontalSpacing(8)
        form_layout.setVerticalSpacing(8)
        common = COMMON.get(type(model).__name__)
        if common:
            advanced_body = self.form.advanced.layout().itemAt(0).widget()
            advanced_layout = advanced_body.layout()
            advanced_layout.setRowWrapPolicy(QFormLayout.RowWrapPolicy.WrapLongRows)
            for key, field in self.form.widgets.items():
                if key not in common and key != "geometry":
                    container = field.parentWidget()
                    if container.parentWidget() is self.form.scroll.widget():
                        taken = form_layout.takeRow(container)
                        advanced_layout.addRow(taken.labelItem.widget(), taken.fieldItem.widget())
            if form_layout.indexOf(self.form.advanced) < 0:
                form_layout.addRow(self.form.advanced)
        for field in self.form.widgets.values():
            field.setMinimumWidth(0)
            if isinstance(field, QLineEdit):
                field.textEdited.connect(self.mark_dirty)
            elif isinstance(field, QComboBox):
                field.currentIndexChanged.connect(self.mark_dirty)
                if field.isEditable():
                    field.editTextChanged.connect(self.mark_dirty)
            elif isinstance(field, QCheckBox):
                field.toggled.connect(self.mark_dirty)
            elif isinstance(field, QTextEdit):
                field.textChanged.connect(self.mark_dirty)
            elif isinstance(field, QPushButton):
                field.clicked.connect(self.mark_dirty)
        self.layout_.addWidget(self.form, 1)
        if hasattr(model, "geometry"):
            self.form.widgets["geometry"].parentWidget().hide()
            self.form.labels["geometry"].hide()
            self.region_label = QLabel(region_summary(model.geometry))
            self.region_label.setWordWrap(True)
            self.region_label.setAccessibleName("Draft geometric support")
            self.layout_.addWidget(self.region_label)
            row = QHBoxLayout()
            for label, signal in (("Pick in layout", self.pick_requested), ("Numeric / masks…", self.numeric_requested)):
                button = QPushButton(label)
                button.clicked.connect(signal.emit)
                row.addWidget(button)
            self.layout_.addLayout(row)
        buttons = QHBoxLayout()
        self.apply_button = QPushButton("Add object" if is_new else "Apply changes")
        self.apply_button.setProperty("primary", True)
        self.apply_button.setObjectName("inspector_apply")
        self.apply_button.clicked.connect(self.apply)
        self.cancel_button = QPushButton("Cancel")
        self.cancel_button.clicked.connect(self.cancelled.emit)
        buttons.addWidget(self.apply_button, 1)
        buttons.addWidget(self.cancel_button)
        self.layout_.addLayout(buttons)
        self.form.accepted.connect(lambda: self.applied.emit(deepcopy(self.form.model)))

    def mark_dirty(self, *_):
        self.dirty = True
        self.draft_label.setText("Unapplied draft · Apply or Cancel before running")
        self.changed.emit()

    def set_region(self, region):
        self.form.model.geometry = deepcopy(region)
        self.region_label.setText(region_summary(region))
        self.mark_dirty()

    def has_changes(self):
        return self.dirty or asdict(self.form.model) != asdict(self.original)

    def apply(self):
        self.form.setResult(QDialog.DialogCode.Rejected)
        self.form.accept()
        # The standard parser leaves invalid fields visible and focused.
        return self.form.result() == QDialog.DialogCode.Accepted
