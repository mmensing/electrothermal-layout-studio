# SPDX-License-Identifier: MPL-2.0
"""Physical-port selection and a preview of the actual validated SPICE export."""
from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QComboBox,
    QFormLayout,
    QLabel,
    QPlainTextEdit,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)


def extraction_prerequisites(project):
    reasons = []
    if len(project.power.ports) < 2:
        reasons.append("Add at least two physical ports in Connections / Setup. Boundary terminals alone are not physical ports.")
    owned = {landing.terminal_id for port in project.power.ports for landing in port.landings}
    if any(t.id not in owned and t.kind not in {"probe", "insulated"} for t in project.terminals):
        reasons.append("Assign every driven/floating electrode to a physical port in Setup; passive probes and insulated gates may remain separate.")
    if any(layer.role == "conductor" and layer.electrical.kind == "hemt" for layer in project.layers):
        reasons.append("Voltage-dependent HEMT channels cannot be reduced to a passive resistor network. Use a separate passive interconnect project.")
    return reasons


class MultiportWorkflow(QWidget):
    selection_changed = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self._updating = False
        self._signature = None
        self._port_names = {}
        layout = QVBoxLayout(self)
        intro = QLabel("1. Choose physical ports → 2. Choose a matrix reference → 3. Extract → 4. Review and export.\n"
                       "This is a fixed-temperature passive DC resistor network. It does not model switching or transistor small-signal behavior. "
                       "Unselected ports float; existing drive settings are preserved. The reference defines matrix coordinates, not an added physical connection.")
        intro.setWordWrap(True)
        layout.addWidget(intro)
        self.readiness = QLabel()
        self.readiness.setWordWrap(True)
        layout.addWidget(self.readiness)
        self.ports = QTableWidget(0, 3)
        self.ports.setHorizontalHeaderLabels(["Include port", "Landing layers", "Feed R (Ω)"])
        self.ports.setAccessibleName("Physical ports for passive extraction")
        self.ports.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.ports.setMaximumHeight(180)
        self.ports.horizontalHeader().setStretchLastSection(True)
        layout.addWidget(self.ports)
        self.reference = QComboBox()
        self.reference.setAccessibleName("Passive matrix reference port")
        self.reference.setToolTip("Eliminates one voltage coordinate for the resistance matrix. It does not add a physical short or connect this port to SPICE node 0.")
        self.reference.setAccessibleDescription(self.reference.toolTip())
        self.ports.setToolTip("Checked physical ports are retained as independent voltage coordinates; unchecked ports are left floating. Landing layer names locate their actual electrode footprints.")
        for index, help_text in enumerate((
                "Select at least two independent, conductively connected physical ports. Drive values are not overwritten.",
                "Physical layers containing this port's localized terminal footprints. A port can have several landings.",
                "Resistance in ohms for each landing's external feed. Zero is ideal only on that landing, not on the whole layer.")):
            self.ports.horizontalHeaderItem(index).setToolTip(help_text)
        form = QFormLayout()
        form.addRow("Matrix reference", self.reference)
        layout.addLayout(form)
        self.selection_note = QLabel()
        self.selection_note.setWordWrap(True)
        layout.addWidget(self.selection_note)
        self.report_summary = QLabel()
        self.report_summary.setWordWrap(True)
        self.report_summary.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        layout.addWidget(self.report_summary)
        self.preview_status = QLabel("No network extracted. Connectivity, conservation and passivity are checked by the numerical backend.")
        self.preview_status.setWordWrap(True)
        layout.addWidget(self.preview_status)
        self.preview = QPlainTextEdit()
        self.preview.setReadOnly(True)
        self.preview.setAccessibleName("Validated SPICE network preview")
        self.preview.setPlaceholderText("Extract to see the exact port-to-node mapping and resistor values that will be exported.")
        self.preview.setMaximumHeight(190)
        layout.addWidget(self.preview)
        self.ports.itemChanged.connect(self._selection_changed)
        self.reference.currentIndexChanged.connect(self._notify_selection)

    def _notify_selection(self):
        if not self._updating:
            self.clear_report()
            self.selection_changed.emit()

    def selected_ports(self):
        return [self.ports.item(row, 0).data(Qt.ItemDataRole.UserRole)
                for row in range(self.ports.rowCount())
                if self.ports.item(row, 0).checkState() == Qt.CheckState.Checked]

    def _selection_changed(self, *_):
        if self._updating:
            return
        previous = self.reference.currentData()
        self._updating = True
        selected = self.selected_ports()
        self.reference.clear()
        for key in selected:
            self.reference.addItem(self._port_names.get(key, key), key)
        self.reference.setCurrentIndex(max(0, self.reference.findData(previous)) if previous in selected else len(selected) - 1)
        self.selection_note.setText(f"{len(selected)} selected. Choose at least two; ports must be independent and conductively connected.")
        self._updating = False
        self._notify_selection()

    def set_project(self, project):
        signature = (repr(project.power.ports), tuple((t.id, t.layer_id, t.kind) for t in project.terminals),
                     tuple((layer.id, layer.role, layer.electrical.kind) for layer in project.layers),
                     project.solver.initial_temperature)
        if signature == self._signature:
            return
        prior = set(self.selected_ports()) if self._signature is not None else None
        self._signature = signature
        self._updating = True
        self._port_names = {port.id: f"{port.name} ({port.id})" if port.name and port.name != port.id else port.id for port in project.power.ports}
        terminals = {t.id: t for t in project.terminals}
        self.ports.setRowCount(len(project.power.ports))
        for row, port in enumerate(project.power.ports):
            item = QTableWidgetItem(port.name or port.id)
            item.setData(Qt.ItemDataRole.UserRole, port.id)
            item.setToolTip(port.id)
            item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            item.setCheckState(Qt.CheckState.Checked if prior is None or port.id in prior else Qt.CheckState.Unchecked)
            self.ports.setItem(row, 0, item)
            layers = sorted({terminals[landing.terminal_id].layer_id for landing in port.landings if landing.terminal_id in terminals})
            self.ports.setItem(row, 1, QTableWidgetItem(", ".join(layers) or "Missing landing"))
            self.ports.setItem(row, 2, QTableWidgetItem(", ".join(f"{landing.resistance:g}" for landing in port.landings)))
        self.ports.setMaximumHeight(min(180, max(85,
            self.ports.horizontalHeader().height() + self.ports.verticalHeader().defaultSectionSize() * len(project.power.ports) + 4)))
        reasons = extraction_prerequisites(project)
        self.readiness.setText("\n".join(reasons) if reasons else
            f"Ready for backend checks · fixed temperature {project.solver.initial_temperature:g} K. Geometry and mesh are prepared by the standard worker.")
        self._updating = False
        self._selection_changed()

    def clear_report(self):
        self.report_summary.clear()
        self.preview.clear()
        self.preview_status.setText("Selection or setup changed. Extract again before exporting this network.")

    def show_report(self, report):
        if report is None:
            self.clear_report()
            return
        checks = report["validation"]
        fixed = report["temperature"]
        thermal = f"{fixed['temperature_K']:g} K" if fixed["kind"] == "uniform_fixed" else "recorded spatial temperature fields"
        self.report_summary.setText(
            f"{len(report['port_ids'])} ports · reference {report['reference']} · fixed {thermal}\n"
            f"KCL error {checks['kcl_absolute_error_S']:.3g} S · reciprocity {checks['reciprocity_absolute_error_S']:.3g} S\n"
            f"{report['mesh']['elements']:,} mesh elements · setup {report['configuration_hash'][:12]}\n"
            "Positive matrix currents enter the domain. Open the full port matrix for G in siemens.")
        from etlayout.models import SimulationError
        from etlayout.power.multiport import spice_netlist
        try:
            netlist = spice_netlist(report)
            self.preview.setPlainText(netlist)
            self.preview.setMaximumHeight(min(190, max(110,
                (len(netlist.splitlines()) + 2) * self.preview.fontMetrics().lineSpacing())))
        except SimulationError as error:
            self.preview.clear()
            self.preview_status.setText(f"SPICE unavailable: {error}. JSON/CSV retain the full operator.")
        else:
            self.preview_status.setText("SPICE fidelity verified per port. Node mapping and every finite resistor are shown below. "
                                       "The subcircuit keeps all physical ports; its reference is not automatically tied to SPICE ground.")
