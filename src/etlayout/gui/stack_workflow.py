# SPDX-License-Identifier: MPL-2.0
"""Read-only stack interpretation and explicit connection drafts.

This module owns view state only. Callers route edit signals through the shared
workspace draft/document workflow; geometric overlap never creates a connection.
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np
from PySide6.QtCore import QRect, Qt, QTimer, Signal
from PySide6.QtWidgets import (
    QComboBox,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from etlayout.geometry.prepare import geometry_region
from etlayout.models import Interface, RegionSpec


def ordered_layers(project):
    """Physical bottom-to-top order, independent of list order and visibility."""
    return sorted(project.layers, key=lambda layer: (layer.z, layer.z + layer.thickness, layer.id))


def layer_physics(layer):
    return "Electrical + thermal" if layer.role == "conductor" else "Thermal only"


def layer_support(layer):
    masks = ", ".join(layer.geometry.sources)
    parts = ([f"Masks: {masks}"] if masks else [])
    if layer.geometry.full_domain:
        parts.append("simulation domain")
    if layer.geometry.polygons:
        parts.append("explicit polygons")
    if layer.geometry.exclude_sources:
        parts.append("minus " + ", ".join(layer.geometry.exclude_sources))
    return "; ".join(parts) or "No footprint assigned"


def connection_physics(interface):
    """Human labels retaining exact constitutive model names."""
    electrical = "None" if interface.electrical_kind == "none" else interface.electrical_kind
    thermal = "None" if interface.thermal_kind == "none" else interface.thermal_kind
    if not interface.enabled:
        return "Disabled", "Disabled"
    return electrical, thermal


def connection_draft(project, layer_a, layer_b, purpose, identifier, geometry=None):
    """Create an unapplied draft with an explicit pair and purpose.

    No dimensions, contact resistivity or material properties are inferred from
    layout colors. The caller must use the normal editor and support validation.
    The shared overlap is an editable initial footprint, not an automatic link.
    """
    known = {layer.id: layer for layer in project.layers}
    if layer_a not in known or layer_b not in known or layer_a == layer_b:
        raise ValueError("Choose two different physical layers.")
    if purpose not in {"thermal", "contact", "via"}:
        raise ValueError("Choose a thermal path, finite contact or physical via.")
    a, b = known[layer_a], known[layer_b]
    if purpose != "thermal" and any(layer.role != "conductor" for layer in (a, b)):
        raise ValueError("Electrical contacts and vias need two conducting layers. Use a thermal path for dielectric sheets.")
    lower, upper = sorted((a, b), key=lambda layer: layer.z)
    gap = upper.z - (lower.z + lower.thickness)
    tolerance = max(1e-15, abs(upper.z) * 1e-10, a.thickness * 1e-8, b.thickness * 1e-8)
    if purpose != "thermal" and gap < -tolerance:
        raise ValueError("Physical endpoint volumes overlap. Review their elevations before adding a contact or via.")
    if purpose == "contact" and gap > tolerance:
        raise ValueError("Surface contact endpoints do not touch. Choose a physical via for a material-filled gap, or use an advanced effective interface deliberately.")
    if purpose == "via" and gap <= tolerance:
        raise ValueError("A physical via needs a positive surface gap. Choose a surface contact for touching sheets.")
    support = RegionSpec()
    if geometry is not None and layer_a in geometry.layers and layer_b in geometry.layers:
        overlap = geometry.layers[layer_a].intersection(geometry.layers[layer_b])
        if not overlap.is_empty and overlap.area > 0:
            support = geometry_region(overlap)
    model = Interface(identifier, layer_a, layer_b, support)
    if purpose == "thermal":
        model.thermal_kind = "stack"
        model.height = max(0., gap)
        model.height_mode = "stack"
    elif purpose == "contact":
        model.model_kind = "physical_contact"
        model.electrical_kind = "specific"
        model.height_mode = "stack"
        model.height = 0.
    else:
        model.model_kind = "physical_via"
        model.electrical_kind = "via"
        model.height_mode = "stack"
    return model


@dataclass(frozen=True)
class StackElevation:
    """A view-only monotone mapping; shared elevations remain coincident.

    Expanded mode gives each actual vertical stratum one display band. It never
    changes physical layer thicknesses, gaps, lateral support or solver inputs.
    """
    knots: tuple[float, ...]
    expanded: bool = False

    @classmethod
    def from_project(cls, project, expanded=False):
        return cls(tuple(sorted({value for layer in project.layers
                                 for value in (layer.z, layer.z + layer.thickness)})), expanded)

    def __call__(self, value):
        if not self.expanded or len(self.knots) < 2:
            return value * 1e6
        return float(np.interp(value, self.knots, np.arange(len(self.knots), dtype=float)))

    @property
    def ticks(self):
        return [(float(index), f"{value * 1e6:.5g}") for index, value in enumerate(self.knots)]


class StackOverview(QWidget):
    """One read-only physical inventory, with actions using workspace editors."""
    layer_selected = Signal(str)
    edit_layer_requested = Signal(str)
    edit_interface_requested = Signal(str)
    connection_requested = Signal(str, str, str)
    cut_requested = Signal()

    def __init__(self, parent=None, *, compact=False):
        super().__init__(parent)
        self.compact = compact
        self._row_timer = QTimer(self)
        self._row_timer.setSingleShot(True)
        self._row_timer.timeout.connect(self._resize_compact_rows)
        self.setObjectName("stack_overview")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        self.summary = QLabel("Map masks, place physical sheets, then declare the paths between them.")
        self.summary.setWordWrap(True)
        layout.addWidget(self.summary)
        self.inventory = QTabWidget()
        self.inventory.setAccessibleName("Physical stack inventory")
        layer_page = QWidget()
        layer_layout = QVBoxLayout(layer_page)
        layer_layout.setContentsMargins(0, 0, 0, 0)
        connection_page = QWidget()
        connection_layout = QVBoxLayout(connection_page)
        connection_layout.setContentsMargins(0, 0, 0, 0)
        self.layer_table = self._table(["Layer / physics", "Material", "Bottom (µm)", "Top (µm)", "Thickness (µm)", "Footprint"])
        self.layer_table.setAccessibleName("Physical layer stack bottom to top")
        self.layer_table.setAccessibleDescription("Read-only actual sheets sorted by physical elevation. Use arrow keys to select; double-click to edit through the shared inspector.")
        self.layer_table.setMaximumHeight(240)
        self.layer_table.horizontalHeader().sectionResized.connect(lambda *_: self._row_timer.start(0))
        self.layer_table.itemSelectionChanged.connect(self._layer_selected)
        self.layer_table.cellDoubleClicked.connect(lambda row, _col: self._emit_row(self.layer_table, row, self.edit_layer_requested))
        layer_layout.addWidget(self.layer_table)
        self.layer_detail = QLabel("Select a physical sheet to review its footprint and dimensions.")
        self.layer_detail.setWordWrap(True)
        self.layer_detail.setAccessibleName("Selected physical layer dimensions and footprint")
        layer_layout.addWidget(self.layer_detail)
        row = QHBoxLayout()
        self.edit_layer = QPushButton("Edit selected layer")
        self.edit_layer.clicked.connect(lambda: self._emit_row(self.layer_table, self.layer_table.currentRow(), self.edit_layer_requested))
        row.addWidget(self.edit_layer)
        inspect = QPushButton("Inspect cross-section")
        inspect.clicked.connect(self.cut_requested.emit)
        row.addWidget(inspect)
        layer_layout.addLayout(row)
        hint = QLabel("Bottom + thickness = top. Hidden sheets still participate; unmapped masks are display-only. "
                      "Overlap alone does not connect layers.")
        hint.setWordWrap(True)
        hint.setProperty("subtle", True)
        layer_layout.addWidget(hint)
        self.connection_table = self._table(["Connection", "Layer pair", "Electrical", "Thermal", "Footprint / state"])
        self.connection_table.setAccessibleName("Declared interlayer electrical and thermal paths")
        self.connection_table.setAccessibleDescription("Electrical and thermal laws are independent. Disabled interfaces carry neither. Double-click to review an existing connection.")
        self.connection_table.setMaximumHeight(165)
        self.connection_table.horizontalHeader().sectionResized.connect(lambda *_: self._row_timer.start(0))
        self.connection_table.cellDoubleClicked.connect(
            lambda row, _col: self._emit_row(self.connection_table, row, self.edit_interface_requested))
        connection_layout.addWidget(self.connection_table)
        self.edit_connection = QPushButton("Edit selected connection")
        self.edit_connection.clicked.connect(lambda: self._emit_row(
            self.connection_table, self.connection_table.currentRow(), self.edit_interface_requested))
        self.connection_table.itemSelectionChanged.connect(
            lambda: self.edit_connection.setEnabled(self.connection_table.currentRow() >= 0))
        connection_layout.addWidget(self.edit_connection)
        self.inventory.addTab(layer_page, "Physical layers")
        self.inventory.addTab(connection_page, "Connections")
        layout.addWidget(self.inventory)
        self.new_connection = QGroupBox("Add interlayer connection")
        self.new_connection.setCheckable(True)
        self.new_connection.setChecked(False)
        self.new_connection.setAccessibleName("Expand new interlayer connection")
        body = QWidget()
        body_layout = QVBoxLayout(body)
        body_layout.setContentsMargins(0, 0, 0, 0)
        body.setVisible(False)
        QVBoxLayout(self.new_connection).addWidget(body)
        self.new_connection.toggled.connect(body.setVisible)
        layout.addWidget(self.new_connection)
        form = QFormLayout()
        self.layer_a, self.layer_b, self.purpose = QComboBox(), QComboBox(), QComboBox()
        self.layer_a.setAccessibleName("Connection from physical layer")
        self.layer_b.setAccessibleName("Connection to physical layer")
        self.purpose.setAccessibleName("Interlayer connection purpose")
        self.purpose.addItem("Thermal path · no electrical current", "thermal")
        self.purpose.addItem("Finite surface contact · touching conductors", "contact")
        self.purpose.addItem("Physical via · declared material", "via")
        for control, help_text in (
            (self.layer_a, "Endpoint A for this interface. Positive reported normal current flows from A to B; choosing A does not impose current direction or vertical order."),
            (self.layer_b, "Endpoint B for this interface. The solved voltage difference determines current direction. Electrical and thermal transfer require explicit laws and footprint."),
            (self.purpose, "Choose the physical purpose, then review the draft. Thermal transfer is separate from electrical transfer; overlap or touching surfaces never creates a link automatically."),
            (self.new_connection, "Expand to choose two existing physical sheets and create an unapplied connection draft. Nothing enters the solve until Apply."),
        ):
            control.setToolTip(help_text)
            control.setAccessibleDescription(help_text)
        self.purpose.setItemData(0, "Heat-only sheet-to-sheet link. Stack includes half of each sheet thickness plus declared interface/gap terms; no automatic gap material.", Qt.ItemDataRole.ToolTipRole)
        self.purpose.setItemData(1, "Finite resistance between conducting sheets over an actual footprint. Review the area-specific resistance in Ω m²; thermal transfer is a separate choice.", Qt.ItemDataRole.ToolTipRole)
        self.purpose.setItemData(2, "A material-filled physical via between conductors. Review height, openings/replaced layers, enclosure, footprint and thermal law.", Qt.ItemDataRole.ToolTipRole)
        for title, widget in (("From layer", self.layer_a), ("To layer", self.layer_b), ("Connect with", self.purpose)):
            form.addRow(title, widget)
        body_layout.addLayout(form)
        self.pair_hint = QLabel()
        self.pair_hint.setWordWrap(True)
        self.pair_hint.setAccessibleName("Selected layer pair interpretation")
        body_layout.addWidget(self.pair_hint)
        self.add_connection = QPushButton("Review new connection…")
        self.add_connection.setToolTip("Opens an unapplied draft. Review footprint, material and values before Apply.")
        self.add_connection.clicked.connect(lambda: self.connection_requested.emit(
            self.layer_a.currentData(), self.layer_b.currentData(), self.purpose.currentData()))
        body_layout.addWidget(self.add_connection)
        for combo in (self.layer_a, self.layer_b, self.purpose):
            combo.currentIndexChanged.connect(self._pair_changed)
        self._project = None
        self.set_compact(compact)
        self.set_project(None)

    @staticmethod
    def _table(headers):
        table = QTableWidget(0, len(headers))
        table.setHorizontalHeaderLabels(headers)
        help_by_title = {
            "Layer / physics": "Physical sheet name and participation. Conductors carry current and heat; insulator/thermal roles carry heat only. Visibility never changes participation.",
            "Material": "Project material snapshot and its calibrated property laws. A familiar material name does not establish process-specific values.",
            "Bottom (µm)": "Actual lower surface elevation z, displayed in micrometres. Stored internally in metres; 1 µm = 1e-6 m.",
            "Top (µm)": "Actual upper surface elevation: bottom + thickness, in micrometres. No sheet order or gap is inferred from source-mask numbers.",
            "Thickness (µm)": "Actual physical sheet thickness, in micrometres. This affects electrical/thermal physics; it is independent of the schematic display scale.",
            "Footprint": "Source-mask expression, explicit polygons or full-domain support. Openings and exclusions remain geometric; mapping a mask creates no interlayer link.",
            "Connection": "Stable interface identifier. Double-click to edit its normal draft; the row itself creates no link.",
            "Layer pair": "Endpoint A ↔ endpoint B. Positive signed normal current is A to B; this ordering does not impose current direction or vertical placement.",
            "Electrical": "Declared electrical transfer law. None blocks electrical transfer; ideal shares potential over the footprint. Finite laws retain their configured resistance and units.",
            "Thermal": "Declared thermal transfer law, independent of the electrical law. Stack includes both half-sheet resistances and only explicitly defined gap/interface terms.",
            "Footprint / state": "Prepared interface support area in µm²; 1 µm² = 1e-12 m². Disabled or unsupported rows are explicit; overlap alone does not activate transfer.",
        }
        for column, title in enumerate(headers):
            table.horizontalHeaderItem(column).setToolTip(help_by_title[title])
        table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        table.setSelectionMode(QTableWidget.SelectionMode.SingleSelection)
        table.setWordWrap(True)
        table.setTextElideMode(Qt.TextElideMode.ElideNone)
        table.verticalHeader().hide()
        table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
        table.horizontalHeader().setStretchLastSection(True)
        return table

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._row_timer.start(0)

    def _resize_compact_rows(self):
        if not self.compact:
            return
        # Table size hints are first calculated before the inspector's final
        # column widths exist. Recalculate after layout as well as model changes;
        # fixed pixel heights would fail under font/DPI scaling.
        for table, columns, minimum_lines in ((self.layer_table, (0, 1), 3),
                                               (self.connection_table, (0, 2), 2)):
            metrics = table.fontMetrics()
            minimum = minimum_lines * metrics.lineSpacing() + 12
            for row in range(table.rowCount()):
                height = minimum
                for column in columns:
                    item = table.item(row, column)
                    if item is not None:
                        width = max(30, table.columnWidth(column) - 12)
                        bounds = metrics.boundingRect(QRect(0, 0, width, 10000),
                                                      Qt.TextFlag.TextWordWrap, item.text())
                        height = max(height, bounds.height() + 12)
                if table.rowHeight(row) != height:
                    table.setRowHeight(row, height)

    @staticmethod
    def _emit_row(table, row, signal):
        if row >= 0 and table.item(row, 0):
            signal.emit(table.item(row, 0).data(Qt.ItemDataRole.UserRole))

    def _layer_selected(self):
        row = self.layer_table.currentRow()
        self.edit_layer.setEnabled(row >= 0)
        if row >= 0:
            self._show_layer_detail(self.layer_table.item(row, 0).data(Qt.ItemDataRole.UserRole))
        self._emit_row(self.layer_table, row, self.layer_selected)

    def set_compact(self, compact=True):
        """Fit the inspector without hiding physical dimensions in tooltips."""
        self.compact = compact
        for column in (2, 3, 4, 5):
            self.layer_table.setColumnHidden(column, compact)
        for column in (1, 3, 4):
            self.connection_table.setColumnHidden(column, compact)
        self.layer_table.horizontalHeaderItem(0).setText("Layer / elevation" if compact else "Layer / physics")
        self.layer_table.horizontalHeaderItem(0).setToolTip("Physical sheet and supported physics. Bottom → top and t (thickness) are actual micrometres, not display spacing. Hidden sheets still participate.")
        self.connection_table.horizontalHeaderItem(0).setText("Connection / layers" if compact else "Connection")
        self.connection_table.horizontalHeaderItem(2).setText("Transfer laws" if compact else "Electrical")
        self.connection_table.horizontalHeaderItem(2).setToolTip("Electrical and thermal transfer are independent. None carries no corresponding transfer; disabled paths enter neither solve.")
        for table, columns in ((self.layer_table, (0, 1)), (self.connection_table, (0, 2))):
            for column in columns:
                table.horizontalHeader().setSectionResizeMode(column, QHeaderView.ResizeMode.Stretch if compact else QHeaderView.ResizeMode.ResizeToContents)
        if self._project is not None:
            current = self.layer_table.currentRow()
            selected = self.layer_table.item(current, 0).data(Qt.ItemDataRole.UserRole) if current >= 0 else ""
            self.set_project(self._project, self._geometry, selected)

    def _show_layer_detail(self, identifier):
        layer = next((item for item in self._project.layers if item.id == identifier), None) if self._project else None
        if layer is None:
            self.layer_detail.setText("Select a physical sheet to review its footprint and dimensions.")
            return
        self.layer_detail.setText(f"{layer.name}: bottom {layer.z * 1e6:.5g} → top {(layer.z + layer.thickness) * 1e6:.5g} µm; "
                                  f"thickness {layer.thickness * 1e6:.5g} µm. {layer_support(layer)}.")
        self.layer_detail.setToolTip(self.layer_detail.text() + " Stored in SI metres; view settings do not change these dimensions.")

    def select_layer(self, identifier):
        self.layer_table.blockSignals(True)
        for row in range(self.layer_table.rowCount()):
            if self.layer_table.item(row, 0).data(Qt.ItemDataRole.UserRole) == identifier:
                self.layer_table.selectRow(row)
                self.layer_table.blockSignals(False)
                self.edit_layer.setEnabled(True)
                self._show_layer_detail(identifier)
                return
        self.layer_table.clearSelection()
        self.layer_table.setCurrentCell(-1, -1)
        self.layer_table.blockSignals(False)
        self.edit_layer.setEnabled(False)
        self._show_layer_detail("")

    def set_project(self, project, geometry=None, selected_layer=""):
        self._project = project
        self._geometry = geometry
        previous_a, previous_b = self.layer_a.currentData(), self.layer_b.currentData()
        self.layer_table.blockSignals(True)
        self.connection_table.blockSignals(True)
        self.layer_table.setRowCount(0)
        self.connection_table.setRowCount(0)
        for combo in (self.layer_a, self.layer_b):
            combo.blockSignals(True)
            combo.clear()
        if project is not None:
            layers = ordered_layers(project)
            for row, layer in enumerate(layers):
                self.layer_table.insertRow(row)
                material = project.materials.get(layer.material_id)
                description = f"{layer.name}\n{layer_physics(layer)}"
                if self.compact:
                    description += f"\n{layer.z * 1e6:.5g} → {(layer.z + layer.thickness) * 1e6:.5g} µm · t {layer.thickness * 1e6:.5g} µm"
                values = (description, material.name if material else "Choose material",
                          f"{layer.z * 1e6:.5g}", f"{(layer.z + layer.thickness) * 1e6:.5g}",
                          f"{layer.thickness * 1e6:.5g}", layer_support(layer))
                for col, value in enumerate(values):
                    item = QTableWidgetItem(value)
                    item.setData(Qt.ItemDataRole.UserRole, layer.id)
                    item.setToolTip(f"{layer.id}: {layer.role}; {'visible' if layer.visible else 'hidden in layout'}; "
                                    f"{layer_physics(layer).lower()}.\n{layer_support(layer)}")
                    self.layer_table.setItem(row, col, item)
                for combo in (self.layer_a, self.layer_b):
                    combo.addItem(f"{layer.name} ({layer.id})", layer.id)
            for row, interface in enumerate(project.interfaces):
                self.connection_table.insertRow(row)
                electrical, thermal = connection_physics(interface)
                support = geometry.interfaces.get(interface.id) if geometry is not None else None
                state = ("Disabled" if not interface.enabled else f"{support.area * 1e12:.5g} µm²"
                         if support is not None and not support.is_empty else "Preparation needed"
                         if geometry is None else "No prepared support")
                name = f"{interface.id}\n{interface.layer_a} ↔ {interface.layer_b}" if self.compact else interface.id
                laws = f"Electrical: {electrical}\nThermal: {thermal}" if self.compact else electrical
                for col, value in enumerate((name, f"{interface.layer_a} ↔ {interface.layer_b}", laws, thermal, state)):
                    item = QTableWidgetItem(value)
                    item.setData(Qt.ItemDataRole.UserRole, interface.id)
                    item.setToolTip(f"{interface.id}: {interface.layer_a} ↔ {interface.layer_b}; electrical {electrical}; thermal {thermal}; support {state}. "
                                    "Declared model only; disabled paths do not enter the solve.")
                    self.connection_table.setItem(row, col, item)
            count = len(layers)
            active = sum(link.enabled and (link.electrical_kind != "none" or link.thermal_kind != "none")
                         for link in project.interfaces)
            self.summary.setText(f"{count} physical {'sheet' if count == 1 else 'sheets'} · {active} enabled interlayer paths. "
                                 "Bottom-to-top order; visibility changes the view only.")
        else:
            self.summary.setText("Add physical layers to inspect their stack and connections.")
        self.layer_table.blockSignals(False)
        self.connection_table.blockSignals(False)
        for combo, previous, fallback in ((self.layer_a, previous_a, 0), (self.layer_b, previous_b, 1)):
            index = combo.findData(previous)
            combo.setCurrentIndex(index if index >= 0 else min(fallback, combo.count() - 1))
            combo.blockSignals(False)
        self.layer_table.resizeRowsToContents()
        self.connection_table.resizeRowsToContents()
        self._resize_compact_rows()
        self._row_timer.start(0)
        self.edit_connection.setEnabled(False)
        self.select_layer(selected_layer)
        self._pair_changed()

    def _pair_changed(self):
        known = {layer.id: layer for layer in self._project.layers} if self._project else {}
        a, b = known.get(self.layer_a.currentData()), known.get(self.layer_b.currentData())
        valid = a is not None and b is not None and a.id != b.id
        electrical = self.purpose.currentData() != "thermal"
        if valid and electrical and (a.role != "conductor" or b.role != "conductor"):
            self.pair_hint.setText("Choose two conductors for current. Dielectric/thermal sheets use a thermal path.")
            valid = False
        elif valid:
            lower, upper = sorted((a, b), key=lambda layer: layer.z)
            gap = upper.z - (lower.z + lower.thickness)
            tolerance = max(1e-15, abs(upper.z) * 1e-10, a.thickness * 1e-8, b.thickness * 1e-8)
            if abs(gap) <= tolerance:
                gap = 0.
            purpose = self.purpose.currentData()
            if electrical and gap < 0:
                self.pair_hint.setText("Physical endpoint volumes overlap. Review elevations before adding a contact or via.")
                self.add_connection.setEnabled(False)
                return
            if purpose == "contact" and gap > 0:
                self.pair_hint.setText("These surfaces do not touch. Use a physical via for a material-filled gap, or deliberately define an effective interface in the complete model editor.")
                self.add_connection.setEnabled(False)
                return
            if purpose == "via" and gap == 0:
                self.pair_hint.setText("These surfaces touch. Use a finite surface contact; a physical via needs a positive surface gap.")
                self.add_connection.setEnabled(False)
                return
            placement = (f"Surface gap {gap * 1e6:.5g} µm. Declare its material or effective resistance. " if gap > 0
                         else "Surfaces touch; transfer still requires a declared interface. " if gap == 0
                         else "Elevations overlap. Review actual material/openings before defining a physical via. ")
            links = [link.id for link in self._project.interfaces
                     if {link.layer_a, link.layer_b} == {a.id, b.id} and link.enabled]
            self.pair_hint.setText(placement + ("Existing enabled paths: " + ", ".join(links) + ". " if links else "No enabled interface for this pair. ")
                                   + "A new draft does not connect anything until Apply.")
        else:
            self.pair_hint.setText("Choose two different physical layers.")
        self.add_connection.setEnabled(valid)
