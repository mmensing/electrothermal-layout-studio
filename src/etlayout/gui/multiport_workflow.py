# SPDX-License-Identifier: MPL-2.0
"""Physical-port selection and a preview of the actual validated SPICE export."""
from PySide6.QtCore import QRectF, Qt, Signal
from PySide6.QtWidgets import (
    QComboBox,
    QDoubleSpinBox,
    QFileDialog,
    QFormLayout,
    QLabel,
    QLineEdit,
    QPlainTextEdit,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QTabWidget,
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
    mesh_requested = Signal()
    bundle_requested = Signal()
    validation_requested = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self._updating = False
        self._signature = None
        self._port_names = {}
        self._project = None
        self._result = None
        self._geometry = None
        self._report = None
        layout = QVBoxLayout(self)
        intro = QLabel("1. Choose physical ports → 2. Choose a matrix reference → 3. Extract → 4. Review and export.\n"
                       "This is a fixed-temperature passive DC resistor network. It does not model switching or transistor small-signal behavior. "
                       "Unselected ports float; existing drive settings are preserved. The reference defines matrix coordinates, not an added physical connection.")
        intro.setWordWrap(True)
        layout.addWidget(intro)
        self.readiness = QLabel()
        self.readiness.setWordWrap(True)
        layout.addWidget(self.readiness)
        thermal = QFormLayout()
        self.temperature_mode = QComboBox()
        for title, key in (("Project initial temperature", "initial"), ("Explicit fixed temperature", "uniform"),
                           ("Selected run — retained final temperature", "retained")):
            self.temperature_mode.addItem(title, key)
        self.temperature_mode.setAccessibleName("Extraction temperature source")
        self.temperature_mode.setToolTip("Freeze material conductance at the initial, explicit uniform, or compatible retained final thermal state. This does not add thermal feedback to SPICE.")
        self.temperature_value = QLineEdit("300")
        self.temperature_value.setAccessibleName("Fixed extraction temperature in kelvin")
        self.temperature_value.setToolTip("Absolute temperature in K, above zero. Used only for Explicit fixed temperature; the project initial temperature and saved run stay unchanged.")
        self.temperature_value.setEnabled(False)
        thermal.addRow("Temperature source", self.temperature_mode)
        thermal.addRow("Fixed temperature (K)", self.temperature_value)
        layout.addLayout(thermal)
        self.temperature_note = QLabel()
        self.temperature_note.setWordWrap(True)
        layout.addWidget(self.temperature_note)
        self.pages = QTabWidget()
        self.pages.setAccessibleName("Passive extraction tools")
        layout.addWidget(self.pages)
        network = QWidget()
        layout = QVBoxLayout(network)
        self.pages.addTab(network, "Network")
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
        self.temperature_mode.currentIndexChanged.connect(self._temperature_changed)
        self.temperature_value.textChanged.connect(self._notify_selection)
        locate = QPushButton("Locate selected port")
        locate.setToolTip("Highlight the selected row's physical landings on the verified layout, without editing any geometry or selection purpose.")
        locate.clicked.connect(self.locate_selected_port)
        layout.addWidget(locate)
        self._build_mapping()
        self._build_mesh()
        self._build_simulator()

    def _temperature_changed(self):
        mode = self.temperature_mode.currentData()
        self.temperature_value.setEnabled(mode == "uniform")
        self._refresh_temperature_note()
        self._notify_selection()

    def _refresh_temperature_note(self):
        from etlayout.power.multiport_context import retained_temperature_reason
        mode = self.temperature_mode.currentData()
        if mode == "retained" and self._project is not None:
            reason = retained_temperature_reason(self._project, self._result)
            self.temperature_note.setText(reason or "Use the selected run's exact mesh and final component-local temperatures. No remeshing or smoothing.")
        elif mode == "uniform":
            self.temperature_note.setText("Job-local fixed temperature; the working setup and saved run are preserved.")
        elif self._project is not None:
            self.temperature_note.setText(f"Fixed at the project's initial {self._project.solver.initial_temperature:g} K.")

    def set_result(self, result):
        changed = result is not self._result
        self._result = result
        self._refresh_temperature_note()
        if changed and self.temperature_mode.currentData() == "retained":
            self._notify_selection()

    def extraction_options(self):
        from etlayout.gui.inputs import parse_number
        mode = self.temperature_mode.currentData()
        options = {"port_ids": self.selected_ports(), "reference": self.reference.currentData(),
                   "temperature_mode": mode}
        if mode == "uniform":
            options["temperature_K"] = parse_number(self.temperature_value.text(), "K")
        elif mode == "retained":
            options["result"] = self._result
        return options

    def _build_mapping(self):
        from etlayout.gui.viewer import LayoutView
        page = QWidget()
        layout = QVBoxLayout(page)
        note = QLabel("Select a physical port or reduced link to locate its landings. A reduced resistor represents a distributed conductive path; it is not one particular wire or via.")
        note.setWordWrap(True)
        layout.addWidget(note)
        self.mapping_note = QLabel("Extract a network to load verified landing geometry.")
        self.mapping_note.setWordWrap(True)
        layout.addWidget(self.mapping_note)
        self.mapping = LayoutView()
        self.mapping.setMinimumHeight(180)
        self.mapping.setMaximumHeight(280)
        self.mapping.setAccessibleName("Extracted network physical landing map")
        layout.addWidget(self.mapping)
        self.links = QTableWidget(0, 3)
        self.links.setHorizontalHeaderLabels(["First port", "Second port", "Equivalent R (Ω)"])
        self.links.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.links.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.links.setMaximumHeight(170)
        self.links.setToolTip("Reduced pairwise resistors from the exact validated SPICE network. Select a row to locate both endpoint landing groups.")
        self.links.itemSelectionChanged.connect(self._locate_link)
        layout.addWidget(self.links)
        self.pages.addTab(page, "Layout")

    def set_geometry(self, geometry, layers):
        self._geometry = geometry
        self.mapping.set_geometries(geometry.layers, layers, geometry.terminals)
        self.mapping.fit_all()
        self.mapping_note.setText("Verified extraction geometry. Select a port row or equivalent link.")

    def locate_selected_port(self):
        row = self.ports.currentRow()
        if row < 0:
            self.mapping_note.setText("Select a physical-port row first.")
            return
        self.locate_ports([self.ports.item(row, 0).data(Qt.ItemDataRole.UserRole)])

    def _locate_link(self):
        row = self.links.currentRow()
        if row >= 0 and self.links.item(row, 0) is not None:
            self.locate_ports([self.links.item(row, col).data(Qt.ItemDataRole.UserRole) for col in (0, 1)])

    def locate_ports(self, identifiers):
        from shapely.ops import unary_union
        if self._geometry is None or self._report is None:
            self.mapping_note.setText("Extract a current network before locating its landings.")
            self.pages.setCurrentIndex(1)
            return
        shapes = [self._geometry.terminals[landing.terminal_id]
                  for port in self._project.power.ports if port.id in identifiers
                  for landing in port.landings if landing.terminal_id in self._geometry.terminals]
        if not shapes:
            self.mapping_note.setText("No verified landing geometry for these ports.")
            return
        for item in self.mapping.annotations:
            self.mapping.scene().removeItem(item)
        self.mapping.annotations.clear()
        shape = unary_union(shapes)
        self.mapping.show_region(shape, " / ".join(identifiers), "#006bff")
        x0, y0, x1, y1 = shape.bounds
        rect = QRectF(x0 * 1e6, -y1 * 1e6, max((x1-x0)*1e6, 1.), max((y1-y0)*1e6, 1.))
        margin = max(rect.width(), rect.height()) * .15
        self.mapping.fitInView(rect.adjusted(-margin, -margin, margin, margin), Qt.AspectRatioMode.KeepAspectRatio)
        self.mapping_note.setText("Highlighted physical landings: " + ", ".join(identifiers))
        self.pages.setCurrentIndex(1)

    def _build_mesh(self):
        page = QWidget()
        layout = QVBoxLayout(page)
        note = QLabel("Compare every conductance and driving/transfer resistance entry, including weak connections, on three to five successively finer meshes. Two successful distinct refinements are required. Retained spatial fields need a separately validated projection and cannot be used for this study.")
        note.setWordWrap(True)
        layout.addWidget(note)
        form = QFormLayout()
        self.mesh_sizes = QLineEdit()
        self.mesh_sizes.setPlaceholderText("Leave empty for 2h, h, h/2")
        self.mesh_sizes.setToolTip("Comma-separated global mesh sizes in µm, coarse to fine; existing local/contact sizes scale proportionally. Three to five levels, within the project's element and DOF budgets.")
        self.mesh_tolerance = QDoubleSpinBox()
        self.mesh_tolerance.setRange(.0001, 99.9999)
        self.mesh_tolerance.setDecimals(4)
        self.mesh_tolerance.setValue(2.)
        self.mesh_tolerance.setSuffix(" %")
        self.mesh_tolerance.setToolTip("Maximum relative change of each individual port-operator and resistance entry on each of the final two refinements. Zero/sign changes fail; a strong port cannot hide a weak one.")
        form.addRow("Mesh sizes (µm)", self.mesh_sizes)
        form.addRow("Per-entry change limit", self.mesh_tolerance)
        layout.addLayout(form)
        action = QPushButton("Run extraction mesh study")
        action.clicked.connect(self.mesh_requested)
        layout.addWidget(action)
        self.mesh_evidence = QPlainTextEdit()
        self.mesh_evidence.setReadOnly(True)
        self.mesh_evidence.setAccessibleName("Extraction-specific mesh evidence")
        self.mesh_evidence.setMaximumHeight(280)
        layout.addWidget(self.mesh_evidence)
        details = QPushButton("Show detailed matrix evidence")
        details.setCheckable(True)
        self.mesh_details = QPlainTextEdit()
        self.mesh_details.setReadOnly(True)
        self.mesh_details.setMaximumHeight(200)
        self.mesh_details.hide()
        details.toggled.connect(self.mesh_details.setVisible)
        layout.addWidget(details)
        layout.addWidget(self.mesh_details)
        self.pages.addTab(page, "Mesh evidence")

    def mesh_options(self):
        from etlayout.gui.inputs import parse_number
        sizes = self.mesh_sizes.text().strip()
        return {"mesh_sizes": [parse_number(value, "µm") for value in sizes.split(",")] if sizes else None,
                "relative_tolerance": self.mesh_tolerance.value() / 100.}

    def show_mesh_report(self, report):
        from etlayout.io.project import _json_dump
        lines = ["Extraction mesh evidence: " + report["status"], report.get("reason", ""),
                 f"Per-entry change limit: {report['relative_tolerance'] * 100:g}%",
                 "Comparison reference: " + str(report.get("comparison_reference", "not established"))]
        for level in report.get("levels", []):
            lines.append(f"Level {level['index'] + 1}: h={level['mesh_max_size_m'] * 1e6:g} µm · "
                         f"{level['mesh']['elements']:,} elements · {level['combined_dofs']:,} DOFs · {level['elapsed_seconds']:.2f} s")
        for pair in report.get("comparisons", []):
            g, r = pair["conductance"], pair["resistance"]
            lines.append(f"Levels {pair['coarse_level'] + 1} → {pair['fine_level'] + 1}: "
                         f"max G change {g['maximum_relative_change'] * 100:.4g}% · "
                         f"max R change {r['maximum_relative_change'] * 100:.4g}% · "
                         + ("passed" if pair["within_tolerance"] else "inspect detailed evidence"))
        if report.get("failure"):
            lines.append("Failed level: " + report["failure"]["message"])
        self.mesh_evidence.setPlainText("\n".join(lines))
        self.mesh_details.setPlainText(_json_dump(report))
        self.pages.setCurrentIndex(2)

    def _build_simulator(self):
        page = QWidget()
        layout = QVBoxLayout(page)
        note = QLabel("Export a self-contained validation bundle, or explicitly choose an independently installed ngspice executable to run all generated port basis tests. Imported netlists are never executed. Simulator agreement and mesh confidence are separate evidence.")
        note.setWordWrap(True)
        layout.addWidget(note)
        form = QFormLayout()
        self.simulator = QLineEdit()
        self.simulator.setPlaceholderText("Full path to console ngspice executable")
        self.simulator.setToolTip("Console/batch executable you explicitly trust (Windows: ngspice_con.exe, not the GUI launcher). It runs only freshly generated resistor decks, with no shell or simulator initialization files. Nothing is bundled or downloaded.")
        form.addRow("ngspice executable", self.simulator)
        self.simulator_timeout = QDoubleSpinBox()
        self.simulator_timeout.setRange(1., 300.)
        self.simulator_timeout.setValue(30.)
        self.simulator_timeout.setSuffix(" s")
        self.simulator_timeout.setToolTip("Total time budget for all port-basis runs. Output is bounded; cancellation stops the owned simulator process.")
        form.addRow("Total execution budget", self.simulator_timeout)
        layout.addLayout(form)
        browse = QPushButton("Choose ngspice executable…")
        browse.clicked.connect(self._choose_simulator)
        layout.addWidget(browse)
        bundle = QPushButton("Export validation bundle…")
        bundle.clicked.connect(self.bundle_requested)
        layout.addWidget(bundle)
        validate = QPushButton("Validate with selected ngspice…")
        validate.clicked.connect(self.validation_requested)
        layout.addWidget(validate)
        self.simulator_evidence = QPlainTextEdit()
        self.simulator_evidence.setReadOnly(True)
        self.simulator_evidence.setAccessibleName("External circuit validation evidence")
        self.simulator_evidence.setMaximumHeight(280)
        layout.addWidget(self.simulator_evidence)
        details = QPushButton("Show per-port validation details")
        details.setCheckable(True)
        self.simulator_details = QPlainTextEdit()
        self.simulator_details.setReadOnly(True)
        self.simulator_details.setMaximumHeight(200)
        self.simulator_details.hide()
        details.toggled.connect(self.simulator_details.setVisible)
        layout.addWidget(details)
        layout.addWidget(self.simulator_details)
        self.pages.addTab(page, "Simulator")

    def _choose_simulator(self):
        path, _ = QFileDialog.getOpenFileName(self, "Choose trusted ngspice executable", "", "Executable (*.exe);;All files (*)")
        if path:
            self.simulator.setText(path)

    def show_simulator_report(self, report):
        from etlayout.io.project import _json_dump
        lines = ["Circuit validation: " + report["status"]]
        if report["status"] == "prepared":
            lines.append(f"{len(report.get('cases', []))} self-contained port-basis decks prepared. No simulator was run.")
        else:
            cases = report.get("cases", [])
            lines.append(f"{sum(case.get('status') == 'passed' for case in cases)}/{len(cases)} executed port-basis checks passed.")
            errors = [item["relative_error"] for case in cases for item in case.get("currents", [])
                      if item.get("relative_error") is not None]
            if errors:
                lines.append(f"Maximum measured relative current error: {max(errors):.3g}")
            for case in cases:
                if case.get("diagnostic"):
                    lines.append(case["diagnostic"])
        lines.append("Simulator fidelity does not establish mesh convergence or model calibration.")
        self.simulator_evidence.setPlainText("\n".join(lines))
        self.simulator_details.setPlainText(_json_dump(report))
        self.pages.setCurrentIndex(3)

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
        self._project = project
        self._refresh_temperature_note()
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
            f"Ready for backend checks · project initial temperature {project.solver.initial_temperature:g} K. Choose the extraction temperature below; geometry and mesh use the standard worker.")
        self._updating = False
        self._selection_changed()

    def clear_report(self):
        self._report = None
        self._geometry = None
        self.mapping.set_geometries({})
        self.mapping_note.setText("Extract a current network to load verified landing geometry.")
        self.report_summary.clear()
        self.links.setRowCount(0)
        self.mesh_evidence.clear()
        self.mesh_details.clear()
        self.simulator_evidence.clear()
        self.simulator_details.clear()
        self.preview.clear()
        self.preview_status.setText("Selection or setup changed. Extract again before exporting this network.")

    def show_report(self, report):
        if report is None:
            self.clear_report()
            return
        self._report = report
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
            self.links.setRowCount(0)
            for line in netlist.splitlines():
                if line.startswith("R"):
                    _, a, b, resistance = line.split()
                    keys = [report["port_ids"][int(node[1:])-1] for node in (a, b)]
                    row = self.links.rowCount()
                    self.links.insertRow(row)
                    for column, key in enumerate(keys):
                        item = QTableWidgetItem(self._port_names.get(key, key))
                        item.setData(Qt.ItemDataRole.UserRole, key)
                        self.links.setItem(row, column, item)
                    self.links.setItem(row, 2, QTableWidgetItem(f"{float(resistance):.7g}"))
            self.preview.setMaximumHeight(min(190, max(110,
                (len(netlist.splitlines()) + 2) * self.preview.fontMetrics().lineSpacing())))
        except SimulationError as error:
            self.preview.clear()
            self.preview_status.setText(f"SPICE unavailable: {error}. JSON/CSV retain the full operator.")
        else:
            self.preview_status.setText("SPICE fidelity verified per port. Node mapping and every finite resistor are shown below. "
                                       "The subcircuit keeps all physical ports; its reference is not automatically tied to SPICE ground.")
