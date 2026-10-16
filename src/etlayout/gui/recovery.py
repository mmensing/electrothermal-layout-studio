# SPDX-License-Identifier: MPL-2.0
"""Independent, explicit recovery choices for interrupted application sessions."""
from __future__ import annotations

from datetime import datetime

from PySide6.QtWidgets import (
    QAbstractItemView,
    QDialog,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
)


class RecoveryDialog(QDialog):
    def __init__(self, store, recover, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Recover interrupted projects")
        self.resize(900, 420)
        self.store, self.recover = store, recover
        layout = QVBoxLayout(self)
        note = QLabel("Each row belongs to a separate interrupted session. Active applications are excluded. "
                      "Ignore keeps snapshots for later; discard permanently removes only selected snapshots.")
        note.setWordWrap(True)
        layout.addWidget(note)
        self.table = QTableWidget(0, 5)
        self.table.setHorizontalHeaderLabels(["Project", "Last snapshot", "Revision", "Original project", "Status"])
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        layout.addWidget(self.table)
        buttons = QHBoxLayout()
        self.actions = {}
        for title, callback in [("Recover selected", self.restore), ("Open original", self.open_original),
                                ("Discard selected…", self.discard), ("Ignore for now", self.reject)]:
            button = QPushButton(title)
            button.clicked.connect(callback)
            self.actions[title] = button
            buttons.addWidget(button)
        layout.addLayout(buttons)
        self.table.itemSelectionChanged.connect(self.update_actions)
        self.refresh()
        if store.issues or store.last_error:
            warning = QLabel(store.last_error or f"{len(store.issues)} unavailable or invalid recovery entries were isolated. Other snapshots remain available.")
            warning.setWordWrap(True)
            layout.addWidget(warning)

    def refresh(self):
        self.entries = self.store.entries()
        self.table.setRowCount(len(self.entries))
        for row, entry in enumerate(self.entries):
            values = [entry.name, datetime.fromtimestamp(entry.saved_at).isoformat(timespec="seconds"),
                      str(entry.revision), entry.original_path or "Unsaved project", entry.status]
            for column, value in enumerate(values):
                self.table.setItem(row, column, QTableWidgetItem(value))
        self.table.resizeColumnsToContents()
        self.update_actions()

    def update_actions(self):
        entries = self.selected()
        self.actions["Recover selected"].setEnabled(bool(entries))
        self.actions["Discard selected…"].setEnabled(bool(entries))
        self.actions["Open original"].setEnabled(bool(entries) and all(e.original_path for e in entries))
        self.actions["Open original"].setToolTip("Select snapshots with an original project location")

    def selected(self):
        return [self.entries[row] for row in sorted({i.row() for i in self.table.selectedIndexes()})]

    def restore(self):
        restored = [self.recover(entry) for entry in self.selected()]
        # Recovery keeps the source snapshot until an explicit durable save or
        # discard; opening it must never destroy the only crash-recovery record.
        if any(restored):
            self.accept()

    def open_original(self):
        restored = [self.recover(entry, True) for entry in self.selected() if entry.original_path]
        if any(restored):
            self.accept()

    def discard(self):
        entries = self.selected()
        if not entries:
            return
        if QMessageBox.question(self, "Discard recovery snapshots", f"Permanently discard {len(entries)} selected snapshots?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No) != QMessageBox.StandardButton.Yes:
            return
        errors = []
        for entry in entries:
            errors.extend(self.store.discard(entry))
        if errors:
            QMessageBox.warning(self, "Recovery cleanup failed", "\n".join(errors))
        self.refresh()
