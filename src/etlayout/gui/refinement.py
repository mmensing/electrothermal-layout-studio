# SPDX-License-Identifier: MPL-2.0
"""One compact review step for local mesh suggestions and bounded studies."""
from __future__ import annotations

from copy import deepcopy
from math import isfinite

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QPen
from PySide6.QtWidgets import (
    QDialog,
    QFormLayout,
    QGraphicsPathItem,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QSpinBox,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
)

from etlayout.geometry.prepare import resolve_region
from etlayout.meshing.refinement import apply_refinement_plan, screen_resources
from etlayout.models import SimulationError


class RefinementPreview:
    """One dialog-owned temporary outline; ordinary annotations remain untouched."""
    def __init__(self, view):
        self.view, self.item = view, None

    def show(self, support):
        from etlayout.gui.viewer import geometry_path
        self.clear()
        if support.is_empty:
            return
        self.item = QGraphicsPathItem(geometry_path(support))
        pen = QPen(QColor("#006bff"), 2., Qt.PenStyle.DashLine)
        pen.setCosmetic(True)
        self.item.setPen(pen)
        self.item.setZValue(1003)
        self.item.setToolTip("Temporary local refinement preview")
        self.item.setData(0, "local-refinement-preview")
        self.item.setAcceptedMouseButtons(Qt.MouseButton.NoButton)
        self.view.scene().addItem(self.item)

    def clear(self):
        if self.item is not None:
            try:
                scene = self.item.scene()
                if scene is not None:
                    scene.removeItem(self.item)
            except RuntimeError:
                # Applying a document edit can refresh/delete the scene before
                # the dialog's finished signal; that already removed our item.
                pass
            self.item = None


class RefinementReviewDialog(QDialog):
    def __init__(self, project, plan, parent=None, *, geometry, apply, run, preview):
        super().__init__(parent)
        self.setWindowTitle("Review local mesh suggestions")
        self.setObjectName("local_refinement_review")
        self.resize(760, 590)
        self.project, self.plan, self.geometry = project, plan, geometry
        self.apply_callback, self.run_callback, self.preview_callback = apply, run, preview
        layout = QVBoxLayout(self)
        note = QLabel("Review exact supports before applying. These are sizing suggestions, not an error estimator. "
                      "A study changes selected local sizes only; active geometry, material properties and global mesh size stay fixed.")
        note.setWordWrap(True)
        layout.addWidget(note)
        self.table = QTableWidget(len(plan.suggestions), 3)
        self.table.setObjectName("local_refinement_suggestions")
        self.table.setHorizontalHeaderLabels(["Purpose / target", "Size (µm)", "Reason"])
        self.table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.table.setColumnWidth(0, 215)
        self.table.setColumnWidth(1, 80)
        self.table.horizontalHeader().setStretchLastSection(True)
        for row, suggestion in enumerate(plan.suggestions):
            item = QTableWidgetItem(suggestion.purpose + " · " + suggestion.target)
            item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            item.setCheckState(Qt.CheckState.Checked)
            item.setToolTip(suggestion.reason)
            self.table.setItem(row, 0, item)
            self.table.setItem(row, 1, QTableWidgetItem(f"{suggestion.size * 1e6:.5g}"))
            reason = QTableWidgetItem(suggestion.reason)
            reason.setToolTip(suggestion.reason)
            self.table.setItem(row, 2, reason)
        layout.addWidget(self.table, 1)
        self.details = QLabel()
        self.details.setWordWrap(True)
        layout.addWidget(self.details)
        self.table.currentCellChanged.connect(self.show_details)
        if plan.suggestions:
            self.table.setCurrentCell(0, 0)
        preview_button = QPushButton("Preview selected support")
        preview_button.clicked.connect(self.preview)
        layout.addWidget(preview_button)
        form = QFormLayout()
        self.elements = self.spin(plan.max_elements)
        self.elements.setObjectName("local_refinement_element_budget")
        self.dofs = self.spin(plan.max_dofs)
        self.memory = self.spin(plan.max_memory_bytes // 1024**2, maximum=262144)
        self.factors = QLineEdit("1, 0.75, 0.5")
        self.factors.setObjectName("local_refinement_factors")
        for label, widget in (("Element budget", self.elements), ("DOF budget", self.dofs),
                              ("Additional memory screen (MiB)", self.memory), ("Local study factors (2–4 levels)", self.factors)):
            form.addRow(label, widget)
        layout.addLayout(form)
        self.status = QLabel()
        self.status.setObjectName("local_refinement_resource_status")
        self.status.setWordWrap(True)
        layout.addWidget(self.status)
        warnings = QLabel("\n".join(plan.warnings) if plan.warnings else
                          "Planning estimates can overcount overlapping supports and cannot guarantee peak memory. Actual mesher element/DOF limits remain enforced.")
        warnings.setWordWrap(True)
        layout.addWidget(warnings)
        row = QHBoxLayout()
        self.apply_button = QPushButton("Apply selected sizing")
        self.apply_button.setObjectName("local_refinement_apply")
        self.run_button = QPushButton("Run bounded study")
        self.run_button.setObjectName("local_refinement_run")
        cancel = QPushButton("Cancel")
        row.addWidget(cancel)
        row.addStretch()
        row.addWidget(self.apply_button)
        row.addWidget(self.run_button)
        layout.addLayout(row)
        cancel.clicked.connect(self.reject)
        self.apply_button.clicked.connect(lambda: self.submit(False))
        self.run_button.clicked.connect(lambda: self.submit(True))
        self.table.itemChanged.connect(self.refresh_estimates)
        for widget in (self.elements, self.dofs, self.memory):
            widget.valueChanged.connect(self.refresh_estimates)
        self.factors.textChanged.connect(self.refresh_estimates)
        self.refresh_estimates()

    @staticmethod
    def spin(value, maximum=2_000_000_000):
        widget = QSpinBox()
        widget.setRange(1, maximum)
        widget.setValue(value)
        widget.setGroupSeparatorShown(True)
        return widget

    def selected(self):
        return [suggestion.id for row, suggestion in enumerate(self.plan.suggestions)
                if self.table.item(row, 0).checkState() == Qt.CheckState.Checked]

    def selection_options(self):
        plan = deepcopy(self.plan)
        plan.max_elements, plan.max_dofs = self.elements.value(), self.dofs.value()
        plan.max_memory_bytes = self.memory.value() * 1024**2
        return plan, self.selected()

    def study_factors(self):
        try:
            factors = [float(value.strip()) for value in self.factors.text().split(",")]
        except ValueError as error:
            raise SimulationError("Study factors must be numeric, for example 1,0.75,0.5") from error
        if (not 2 <= len(factors) <= 4 or factors[0] != 1 or
                any(not isfinite(value) or not 0 < value <= 1 for value in factors) or
                any(b >= a for a, b in zip(factors, factors[1:]))):
            raise SimulationError("Use 2–4 finite positive decreasing local factors starting at 1")
        return factors

    def estimate(self, plan, selected, factor):
        candidate = apply_refinement_plan(self.project, plan, selected, factor=factor)
        geometry = deepcopy(self.geometry)
        geometry.refinements = {item.id: (self.geometry.refinements[item.id] if item.id in
                               self.geometry.refinements else resolve_region(item.geometry, {}, geometry.domain))
                               for item in candidate.mesh.refinements}
        return screen_resources(candidate, plan, geometry=geometry)

    def refresh_estimates(self, *_args):
        apply_ok, study_ok = False, False
        try:
            plan, selected = self.selection_options()
            coarse = self.estimate(plan, selected, 1.)
            apply_ok = True
            factors = self.study_factors()
            fine = self.estimate(plan, selected, factors[-1])
            study_ok = True
            self.status.setText(f"Planning estimate: {coarse['elements']:,} → {fine['elements']:,} elements; "
                                f"{fine['dofs']:,} DOFs and {fine['memory_bytes'] / 1024**2:.0f} MiB additional numerical memory at the finest level. "
                                "Successive changes track resistance, delivered current, like-sized group sharing and configured physical hotspot averages.")
        except (SimulationError, ValueError) as error:
            self.status.setText(str(error))
        self.apply_button.setEnabled(apply_ok)
        self.run_button.setEnabled(study_ok)

    def submit(self, study):
        try:
            plan, selected = self.selection_options()
            factors = self.study_factors() if study else [1.]
            for factor in factors if study else [1.]:
                self.estimate(plan, selected, factor)
            if study:
                self.run_callback(plan, selected, factors)
            else:
                self.apply_callback(plan, selected)
            self.accept()
        except (SimulationError, ValueError) as error:
            self.status.setText(str(error))

    def preview(self):
        row = self.table.currentRow()
        if row < 0:
            row = 0
        if row < len(self.plan.suggestions):
            support = resolve_region(self.plan.suggestions[row].geometry, {}, self.geometry.domain)
            self.preview_callback(support)

    def show_details(self, row, *_args):
        self.details.setText(self.plan.suggestions[row].reason if 0 <= row < len(self.plan.suggestions) else "")
