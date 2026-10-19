# SPDX-License-Identifier: MPL-2.0
"""Purpose-grouped engineering evidence and linked physical-support readouts."""
from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QCheckBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QScrollArea,
    QTableWidget,
    QTableWidgetItem,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from etlayout.gui.inputs import parse_number
from etlayout.gui.workspace import engineering
from etlayout.visualization.sampling import metric_label, metric_unit


class WrappedTable(QTableWidget):
    def resizeEvent(self, event):
        super().resizeEvent(event)
        self.resizeRowsToContents()


def _table(labels):
    table = WrappedTable(0, len(labels))
    table.setHorizontalHeaderLabels(labels)
    table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
    table.horizontalHeader().setStretchLastSection(True)
    table.verticalHeader().hide()
    table.setWordWrap(True)
    return table


def _rows(table, rows):
    table.setRowCount(len(rows))
    for index, row in enumerate(rows):
        for column, value in enumerate(row):
            item = QTableWidgetItem(str(value))
            item.setToolTip(str(value))
            table.setItem(index, column, item)
    table.resizeColumnsToContents()
    table.resizeRowsToContents()


class EngineeringPanel(QWidget):
    inspect_requested = Signal(object)
    quantity_requested = Signal(str)
    locate_requested = Signal(object)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.report = None
        self.support_report = None
        layout = QVBoxLayout(self)
        self.heading = QLabel("Select a recorded result to inspect engineering evidence.")
        self.heading.setWordWrap(True)
        self.heading.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        layout.addWidget(self.heading)
        self.tabs = QTabWidget()
        self.evidence = _table(["Evidence axis", "State", "Meaning / scope"])
        self.evidence.setAccessibleName("Independent engineering evidence axes")
        evidence_page = QWidget()
        evidence_layout = QVBoxLayout(evidence_page)
        evidence_layout.setContentsMargins(0, 0, 0, 0)
        evidence_layout.addWidget(self.evidence, 1)
        self.mesh_details = QPushButton("Local mesh evidence…")
        self.mesh_details.setAccessibleName("Inspect selected-metric local mesh refinement evidence")
        self.mesh_details.clicked.connect(self.show_mesh_evidence)
        self.mesh_details.hide()
        evidence_layout.addWidget(self.mesh_details)
        self.tabs.addTab(evidence_page, "Evidence")
        self.dashboard = _table(["Quantity", "Recorded value", "Definition / support"])
        self.dashboard.setAccessibleName("Device and disjoint loss dashboard")
        self.tabs.addTab(self.dashboard, "Device && losses")
        support_page = QWidget()
        support_layout = QVBoxLayout(support_page)
        self.support_description = QLabel("Inspect the current layer/region, or a fixed physical square around the last map probe. All quantities use identical support.")
        self.support_description.setWordWrap(True)
        support_layout.addWidget(self.support_description)
        controls = QHBoxLayout()
        self.use_probe = QCheckBox("Square around last probe")
        self.use_probe.setAccessibleName("Use fixed physical support around the last map probe")
        controls.addWidget(self.use_probe)
        self.window_side = QLineEdit("20 µm")
        self.window_side.setAccessibleName("Physical averaging square side (µm)")
        self.window_side.setMaximumWidth(110)
        controls.addWidget(self.window_side)
        self.inspect_button = QPushButton("Inspect same support")
        self.inspect_button.setEnabled(False)
        self.inspect_button.clicked.connect(self.request_support)
        controls.addWidget(self.inspect_button)
        support_layout.addLayout(controls)
        self.support_error = QLabel()
        self.support_error.setWordWrap(True)
        support_layout.addWidget(self.support_error)
        self.quantities = _table(["Quantity", "Area mean", "Raw maximum", "Integral", "Availability"])
        self.quantities.setAccessibleName("Linked quantities on identical physical support")
        self.quantities.cellDoubleClicked.connect(self.select_quantity)
        self.quantities.setMinimumHeight(170)
        support_layout.addWidget(self.quantities, 1)
        self.crowding = QLabel("Raw FEM peaks need physical-radius and mesh verification.")
        self.crowding.setWordWrap(True)
        self.crowding.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        support_layout.addWidget(self.crowding)
        self.locate_button = QPushButton("Locate selected-support peak")
        self.locate_button.clicked.connect(lambda: self.locate_requested.emit(self.support_report))
        self.locate_button.setEnabled(False)
        support_layout.addWidget(self.locate_button)
        support_scroll = QScrollArea()
        support_scroll.setWidgetResizable(True)
        support_scroll.setWidget(support_page)
        self.tabs.addTab(support_scroll, "Same support")
        for label in (self.heading, self.support_description, self.support_error, self.crowding):
            label.setTextFormat(Qt.TextFormat.PlainText)
        layout.addWidget(self.tabs, 1)

    def clear(self, message="Select a recorded result to inspect engineering evidence."):
        self.report = self.support_report = None
        self.heading.setText(message)
        self.heading.setProperty("error", False)
        self.heading.style().unpolish(self.heading)
        self.heading.style().polish(self.heading)
        for table in (self.evidence, self.dashboard, self.quantities):
            table.setRowCount(0)
        self.support_description.setText("Choose a solved physical layer/region and inspect its support.")
        self.support_error.clear()
        self.crowding.clear()
        self.inspect_button.setEnabled(False)
        self.locate_button.setEnabled(False)
        self.mesh_details.hide()

    def show_mesh_evidence(self):
        from etlayout.gui.refinement import MeshVerificationDialog
        receipt = next((row["evidence"] for row in self.report["axes"] if row["axis"] == "Mesh confidence"), {}) if self.report else {}
        if receipt:
            dialog = MeshVerificationDialog(receipt, self)
            try:
                dialog.exec()
            finally:
                dialog.deleteLater()

    def request_support(self):
        try:
            side = parse_number(self.window_side.text(), "µm") if self.use_probe.isChecked() else None
            if side is not None and side <= 0:
                raise ValueError("Use a positive physical square side.")
        except ValueError as error:
            self.support_error.setText(str(error))
            return
        self.support_error.clear()
        self.inspect_requested.emit(side)

    def set_report(self, report):
        self.report = report
        frame = report.get("time_s")
        heading = report["family"] + " · recorded physical state"
        if frame is not None:
            heading += " at " + engineering(frame, "s")
        heading += " · run " + report["configuration_hash"][:12]
        concerns = [row["axis"] + ": " + row["status"].replace("_", " ") for row in report["axes"]
                    if row["status"] in {"failed", "outside_declared_range", "stopped_early"}]
        self.heading.setText(("Attention — " + "; ".join(concerns) + "\n" if concerns else "") + heading)
        self.heading.setProperty("error", bool(concerns))
        self.heading.style().unpolish(self.heading)
        self.heading.style().polish(self.heading)
        _rows(self.evidence, [(row["axis"], row["status"].replace("_", " "), row["detail"])
                              for row in report["axes"]])
        self.mesh_details.setVisible(any(row["axis"] == "Mesh confidence" and row.get("evidence") for row in report["axes"]))
        rows = [(metric_label(key, display=False), engineering(value, metric_unit(key)), "Recorded solver metric")
                for key, value in report["metrics"].items()]
        labels = {"routing_metal": "Routing metal loss", "other_metal": "Other metal loss", "access": "Access loss",
                  "channel": "Channel loss", "contact": "Ohmic contact / via loss", "device_load": "Intrinsic device-load loss",
                  "external_feed": "External feed loss"}
        budget = report.get("loss_budget", {})
        rows += [(labels.get(key, key), engineering(value, "W"), "Disjoint loss ledger; groups are views, never added again")
                 for key, value in budget.get("categories", {}).items()]
        for key in ("thermal_deposition", "excluded_power"):
            if key in budget:
                rows.append((key.replace("_", " ").capitalize(), engineering(budget[key], "W"), "Authoritative loss-deposition ledger"))
        for name, record in report.get("devices", {}).items():
            for key, unit in (("current", "A"), ("current_per_width", "A/m"), ("current_per_area", "A/m²"),
                              ("source_voltage", "V"), ("effective_gate_bias", "V"), ("temperature_max", "K"),
                              ("layer_loss", "W"), ("contact_loss", "W")):
                if record.get(key) is not None:
                    rows.append((name + " · " + key.replace("_", " "), engineering(record[key], unit), record.get("definition", "Device-group view")))
        for name, record in report.get("contacts", {}).items():
            for key, unit in (("current_a_to_b", "A"), ("absolute_loading_fraction", "fraction"),
                              ("power", "W"), ("j_normal_mean", "A/m²"), ("j_normal_peak", "A/m²")):
                value = record.get(key)
                rows.append((name + " · " + key.replace("_", " "), engineering(value, unit) if value is not None else "Unavailable",
                             record.get("peak_definition", "Recorded contact island") if "peak" in key else "Recorded contact island; signed layer A → B"))
        for name, record in report.get("contact_transfer", {}).items():
            rows.append((name + " · transfer length", engineering(record["value_m"], "m") if record["value_m"] is not None else "Unavailable",
                         record["definition"] + "; estimate, not 3D validation"))
        for name, record in report.get("crowding", {}).items():
            for key, unit in (("sheet_current_average_A_per_m", "A/m"), ("sheet_current_p95_A_per_m", "A/m"),
                              ("raw_sheet_current_max_A_per_m", "A/m"), ("crowding_factor", "fraction")):
                value = record.get(key)
                if value is not None:
                    rows.append((name + " · " + key.replace("_", " "), engineering(value, unit), record["qualification"]))
        for key, value in report.get("run_energy", {}).items():
            rows.append((metric_label(key), engineering(value, metric_unit(key)), "Recorded whole-run energy scope"))
        _rows(self.dashboard, rows)
        self.inspect_button.setEnabled(True)
        if self.support_report and self.support_report.get("view_context") != report.get("view_context"):
            self.support_description.setText("Previous support report belongs to another recorded state. Inspect again for this frame; fields were not reused.")
            self.locate_button.setEnabled(False)

    def evidence_text(self):
        if not self.report:
            return "No engineering evidence"
        chosen = {row["axis"]: row["status"].replace("_", " ") for row in self.report["axes"]}
        return (f"Numerical: {chosen['Numerical solve']} · Conservation: {chosen['Conservation']}\n"
                f"Thermal limits: {chosen['Temperature limits']} · Mesh: {chosen['Mesh confidence']}\n"
                f"Model range: {chosen['Material/model range']} · Calibration: {chosen['Calibration']}\n"
                f"Duration: {chosen['Requested duration']} · Time refinement: {chosen['Time confidence']}")

    def set_support_report(self, report):
        self.support_report = report
        self.support_error.clear()
        when = " at " + engineering(report["time_s"], "s") if report.get("time_s") is not None else ""
        self.support_description.setText(f"Recorded {report['layer_id']} / {report['region'] or 'all regions'}{when} · "
            f"supported area {report['support_area_m2'] * 1e12:.6g} µm². Double-click a quantity to select its map on the same layer/region.")
        rows = []
        for record in report["quantities"]:
            cells, reasons = [], []
            for reduction in ("mean", "maximum", "integral"):
                measured = record["measurements"].get(reduction)
                cells.append(engineering(measured["value"], measured.get("unit", ""))
                             if measured and measured["status"] == "available" else "—")
                if measured and measured["status"] != "available":
                    reasons.append(measured.get("reason", "Unavailable"))
            rows.append((record["label"], *cells, "; ".join(dict.fromkeys(reasons)) or "Available; extrema remain unqualified"))
        _rows(self.quantities, rows)
        crowding = report.get("crowding")
        if crowding:
            shapes = ", ".join(sorted({row["kind"].replace("_", " ") for row in crowding.get("nearby_geometry", [])})) or "No boundary classification available"
            edges = ", ".join(row["kind"].replace("_", " ") + " " + row["id"] for row in crowding.get("nearby_boundaries", []))
            radii = [row["inferred_radius_m"] for row in crowding.get("nearby_geometry", []) if row.get("inferred_radius_m")]
            self.crowding.setText(crowding["qualification"] + ". " + shapes + ("; " + edges if edges else "")
                + (". Faceted radius estimate: " + ", ".join(engineering(value, "m") for value in radii) + "; not a calibrated fabricated radius" if radii else "")
                + ". Raw peak: " + crowding["raw_peak_status"].replace("_", " "))
        else:
            self.crowding.setText(report["qualification"] + " No electrical current field exists on this layer.")
        verification = report.get("mesh_evidence", {})
        if verification.get("status") == "selected_metric_evidence":
            self.crowding.setText(self.crowding.text() + ". Same-support mesh evidence: " +
                "; ".join(key + "=" + row["status"].replace("_", " ") for key, row in verification.get("metrics", {}).items()) +
                ". Physical qualification remains unestablished.")
        else:
            self.crowding.setText(self.crowding.text() + ". " + verification.get("reason", "Mesh verification not established."))
        self.locate_button.setEnabled(bool(crowding))
        self.tabs.setCurrentIndex(2)

    def select_quantity(self, row, column):
        if self.support_report and 0 <= row < len(self.support_report["quantities"]):
            key = self.support_report["quantities"][row]["quantity"]
            self.quantity_requested.emit("current_density" if key in {"metal_current_density", "effective_current_density"} else key)
