# SPDX-License-Identifier: MPL-2.0
"""Small nested editors for physical supports and experiment records."""
from __future__ import annotations

import json
from copy import deepcopy
from dataclasses import MISSING, asdict, fields
from typing import get_type_hints

from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QPushButton,
    QTextEdit,
    QVBoxLayout,
)

from etlayout.io.project import _decode
from etlayout.models import SimulationError


def new_record(model_type):
    """Create an editable draft; field validation prevents saving empty identities."""
    hints = get_type_hints(model_type)
    values = {}
    for descriptor in fields(model_type):
        if descriptor.default is MISSING and descriptor.default_factory is MISSING:
            annotation = hints[descriptor.name]
            values[descriptor.name] = "" if annotation is str else 0. if annotation is float else 0
    return model_type(**values)


class StructuredValueDialog(QDialog):
    def __init__(self, value, annotation, name, parent=None):
        super().__init__(parent)
        self.setWindowTitle(name.replace("_", " ").capitalize() + " · SI values")
        self.resize(570, 370)
        self.value, self.annotation = deepcopy(value), annotation
        layout = QVBoxLayout(self)
        note = QLabel("Enter the selected mapping or list as JSON. Physical values use SI units. "
                      "Unknown types and nonfinite values are rejected before applying.")
        note.setWordWrap(True)
        layout.addWidget(note)
        self.editor = QTextEdit(json.dumps(value, indent=2, default=lambda item: asdict(item)))
        self.editor.setAccessibleName(name.replace("_", " ") + " JSON values")
        layout.addWidget(self.editor)
        self.error = QLabel()
        self.error.setWordWrap(True)
        layout.addWidget(self.error)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def accept(self):
        try:
            value = json.loads(self.editor.toPlainText(), parse_constant=lambda value:
                (_ for _ in ()).throw(ValueError(f"Nonfinite JSON value {value}")))
            json.dumps(value, allow_nan=False)
            self.value = _decode(self.annotation, value)
        except (ValueError, SimulationError) as error:
            self.error.setText(str(error))
            return
        super().accept()


class ModelCollectionDialog(QDialog):
    """Edit one typed record at a time, including nested ports/scenarios/objectives."""
    def __init__(self, model_type, entries, parent=None, *, choices=None, mask_keys=()):
        super().__init__(parent)
        self.setWindowTitle(model_type.__name__ + " entries")
        self.resize(590, 370)
        self.model_type, self.entries = model_type, deepcopy(entries)
        self.choices, self.mask_keys = choices or {}, mask_keys
        layout = QVBoxLayout(self)
        self.list = QListWidget()
        self.list.setAccessibleName(model_type.__name__ + " records")
        self.list.itemDoubleClicked.connect(lambda *_: self.edit())
        layout.addWidget(self.list)
        row = QHBoxLayout()
        for label, callback in (("Add", self.add), ("Edit", self.edit), ("Remove", self.remove)):
            button = QPushButton(label)
            button.clicked.connect(callback)
            row.addWidget(button)
        layout.addLayout(row)
        self.error = QLabel()
        layout.addWidget(self.error)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
        self.refresh()

    def refresh(self):
        selected = self.list.currentRow()
        self.list.clear()
        for index, entry in enumerate(self.entries):
            identity = next((getattr(entry, key) for key in ("id", "terminal_id", "measurement_id")
                             if getattr(entry, key, "")), str(index + 1))
            self.list.addItem(str(identity))
        if self.entries:
            self.list.setCurrentRow(max(0, min(selected, len(self.entries)-1)))

    def add(self):
        from etlayout.gui.forms import ModelDialog
        dialog = ModelDialog(new_record(self.model_type), parent=self, choices=self.choices, mask_keys=self.mask_keys)
        if dialog.exec():
            self.entries.append(dialog.model)
            self.refresh()

    def edit(self):
        from etlayout.gui.forms import ModelDialog
        index = self.list.currentRow()
        if index < 0:
            return
        dialog = ModelDialog(self.entries[index], parent=self, choices=self.choices, mask_keys=self.mask_keys)
        if dialog.exec():
            self.entries[index] = dialog.model
            self.refresh()

    def remove(self):
        index = self.list.currentRow()
        if index >= 0:
            del self.entries[index]
            self.refresh()

    def accept(self):
        identities = [getattr(entry, "id", None) for entry in self.entries]
        identities = [key for key in identities if key is not None]
        if len(identities) != len(set(identities)):
            self.error.setText("Identifiers must be unique in this collection.")
            return
        # Verify all nested records through the same decoder used by project persistence.
        self.entries = [_decode(self.model_type, asdict(entry)) for entry in self.entries]
        super().accept()
