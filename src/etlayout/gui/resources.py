# SPDX-License-Identifier: MPL-2.0
"""Explicit resource review; declared paths are never probed by this dialog."""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QCheckBox,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QMessageBox,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
)

from etlayout.io.project import load_run_history, resolve_project
from etlayout.io.resources import (
    ExternalResourcePolicy,
    ProjectInspection,
    ResourceAccessError,
    display_location,
    inspect_project,
)


class ResourceReviewDialog(QDialog):
    def __init__(self, inspection, error=None, parent=None):
        super().__init__(parent)
        self.inspection, self.action = inspection, "cancel"
        self.setWindowTitle("Review project resources")
        self.resize(860, 430)
        layout = QVBoxLayout(self)
        text = QLabel(
            "Project metadata is inspectable without opening declared layouts. "
            "Authorize only resources you trust. Network access needs its own opt-in. "
            "Unaccessed resources have not been checked for existence or changes."
        )
        text.setWordWrap(True)
        layout.addWidget(text)
        if error:
            message = QLabel(str(error))
            message.setWordWrap(True)
            message.setTextFormat(Qt.TextFormat.PlainText)
            layout.addWidget(message)
        self.table = QTableWidget(len(inspection.resources), 5)
        self.table.setHorizontalHeaderLabels(
            ["Authorize", "Type", "Referenced location", "Boundary", "Status"]
        )
        self.table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        for row, resource in enumerate(inspection.resources):
            check = QTableWidgetItem("")
            check.setToolTip(resource.key)
            check.setFlags(check.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            check.setCheckState(
                Qt.CheckState.Checked if resource.category == "embedded" else Qt.CheckState.Unchecked
            )
            self.table.setItem(row, 0, check)
            status = (
                "Blocked: " + error.reason
                if error and error.resource and error.resource.key == resource.key
                else resource.status
            )
            for col, value in enumerate(
                (
                    resource.kind,
                    display_location(resource.location),
                    (
                        error.resource.category
                        if error and error.resource and error.resource.key == resource.key
                        else resource.category
                    ),
                    status,
                ),
                1,
            ):
                item = QTableWidgetItem(value)
                item.setToolTip(value)
                self.table.setItem(row, col, item)
        header = self.table.horizontalHeader()
        header.setStretchLastSection(False)
        header.setSectionResizeMode(QHeaderView.ResizeMode.Interactive)
        for column, width in ((0, 78), (1, 70), (3, 108), (4, 210)):
            self.table.setColumnWidth(column, width)
        header.setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
        self.table.setWordWrap(True)
        self.table.resizeRowsToContents()
        layout.addWidget(self.table)
        self.network = QCheckBox("Separately allow network-backed resources for this opening")
        layout.addWidget(self.network)
        relink = QPushButton("Relink layout…")
        relink.clicked.connect(self.relink)
        layout.addWidget(relink)
        row = QHBoxLayout()
        for label, action in [("Open approved resources", "authorize"), ("Leave unresolved", "unresolved")]:
            button = QPushButton(label)
            button.clicked.connect(lambda checked=False, action=action: self.finish(action))
            row.addWidget(button)
        cancel = QDialogButtonBox(QDialogButtonBox.StandardButton.Cancel)
        cancel.rejected.connect(self.reject)
        row.addWidget(cancel)
        layout.addLayout(row)

    def relink(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "Select replacement layout", "", "Layouts (*.gds *.gdsii *.oas *.oasis)"
        )
        if path:
            self.inspection = self.inspection.relink(path)
            self.action = "relink"
            self.accept()

    def finish(self, action):
        self.action = action
        self.accept()

    def policy(self):
        approved = frozenset(
            resource.key
            for row, resource in enumerate(self.inspection.resources)
            if self.table.item(row, 0).checkState() == Qt.CheckState.Checked
        )
        return ExternalResourcePolicy(allow_network=self.network.isChecked(), approved_resources=approved)


def open_reviewed_project(path, parent=None, *, policy=None, lazy=False, cache_bytes=256 * 1024**2):
    """Resolve exactly the inspected container, including its historical resources."""
    policy = policy or ExternalResourcePolicy()
    try:
        inspection = inspect_project(path, allow_network_container=policy.allow_network)
    except ResourceAccessError as error:
        if error.reason != "network":
            raise
        answer = QMessageBox.question(
            parent,
            "Network project location",
            "The selected project container is network-backed. Allow network access for this opening? "
            "Outside-root resources still require their own authorization.",
        )
        if answer != QMessageBox.StandardButton.Yes:
            return None
        policy = ExternalResourcePolicy(
            allow_outside_root=policy.allow_outside_root,
            allow_network=True,
            approved_resources=policy.approved_resources,
        )
        inspection = inspect_project(path, allow_network_container=True)
    return resolve_reviewed_project(inspection, parent, policy=policy, lazy=lazy, cache_bytes=cache_bytes)


def review_layout_relink(project, location, source, parent=None):
    """Explicitly chosen replacements still require independent network approval."""
    container = Path(source or Path.cwd() / "untitled.json").with_suffix(".json")
    inspection = ProjectInspection(container, project, ()).relink(location)
    return resolve_reviewed_project(
        inspection, parent, policy=ExternalResourcePolicy(approved_resources=frozenset({"layout"}))
    )


def resolve_reviewed_project(inspection, parent=None, *, policy=None, lazy=False, cache_bytes=256 * 1024**2):
    policy = policy or ExternalResourcePolicy()
    while True:
        try:
            if (lazy and inspection.metadata.get("schema_version") == 3
                    and not inspection.relinked and not inspection.discard_stale_result):
                from etlayout.io.history_store import open_history_catalog
                store = open_history_catalog(inspection, external_policy=policy, cache_bytes=cache_bytes)
                try:
                    project = store.resolve_project()
                except BaseException:
                    store.close()
                    raise
                project._history_store = store
                project._resource_history = []
                return project, None
            project, result = resolve_project(inspection, external_policy=policy)
            history = (
                []
                if inspection.relinked
                else load_run_history(
                    inspection.source, external_policy=policy, _archive_data=inspection.archive_data
                )
            )
            project._resource_history = history
            return project, result
        except ResourceAccessError as error:
            dialog = ResourceReviewDialog(inspection, error, parent)
            if dialog.exec() != QDialog.DialogCode.Accepted:
                return None
            if dialog.action == "unresolved":
                project = inspection.unresolved_project()
                project._resource_history = []
                return project, None
            if dialog.action == "relink":
                inspection = dialog.inspection
                policy = ExternalResourcePolicy(
                    allow_network=dialog.network.isChecked(), approved_resources=frozenset({"layout"})
                )
            else:
                policy = dialog.policy()
