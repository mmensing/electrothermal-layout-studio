# SPDX-License-Identifier: MPL-2.0
"""Purpose-grouped physical analysis and constrained-design desktop workflows."""
from __future__ import annotations

import json
from copy import deepcopy
from dataclasses import asdict, fields
from pathlib import Path
from uuid import uuid4

import numpy as np
from PySide6.QtCore import QRectF, Qt, Signal
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QFormLayout,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QProgressBar,
    QPushButton,
    QScrollArea,
    QSplitter,
    QTableWidget,
    QTableWidgetItem,
    QTabWidget,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)
from shapely import from_wkt

from etlayout.gui.field_scale import FieldScaleControl
from etlayout.gui.forms import ModelDialog
from etlayout.gui.inputs import parse_number
from etlayout.gui.jobs import ComputeJob
from etlayout.gui.power_forms import ModelCollectionDialog
from etlayout.gui.power_pareto import ParetoPanel
from etlayout.gui.viewer import LayoutView
from etlayout.io.project import _json_dump, configuration_hash, physics_hash, project_from_dict
from etlayout.models import (
    DeviceGroup,
    MeasurementSpec,
    OperatingScenario,
    OptimizationExperiment,
    PhysicalPort,
    RoutingRecipe,
    SimulationError,
    Terminal,
)
from etlayout.visualization.units import display_unit, to_display


def _text(value):
    if value is None:
        return "Unavailable"
    if isinstance(value, float):
        return f"{value:.7g}" if np.isfinite(value) else "Invalid"
    if isinstance(value, (list, dict)):
        return json.dumps(value, ensure_ascii=False, separators=(", ", ": "))
    return str(value)


class RecordTable(QTableWidget):
    """Ordered quantitative rows with original records retained for selection."""
    def __init__(self, columns, parent=None, *, units=None):
        super().__init__(0, len(columns), parent)
        self.columns, self.records = columns, []
        self.units = units or {}
        self.setHorizontalHeaderLabels([label.replace(f"({self.units.get(key)})", f"({display_unit(self.units.get(key))})")
                                       if key in self.units else label for key, label in columns])
        self.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.setSelectionMode(QTableWidget.SelectionMode.SingleSelection)
        self.horizontalHeader().setStretchLastSection(True)
        self.verticalHeader().hide()

    def set_records(self, rows):
        if isinstance(rows, dict):
            rows = [{"id": key, **value} for key, value in rows.items()]
        self.records = list(rows)
        self.setRowCount(len(self.records))
        for index, record in enumerate(self.records):
            for column, (key, _) in enumerate(self.columns):
                value = record.get(key)
                unit = self.units.get(key)
                if unit == "record":
                    value = to_display(value, record.get("unit", ""))
                elif unit:
                    value = to_display(value, unit)
                elif key == "unit" and self.units.get("value") == "record":
                    value = display_unit(value)
                item = QTableWidgetItem(_text(value))
                item.setToolTip(_json_dump(record))
                self.setItem(index, column, item)
        self.resizeColumnsToContents()

    def selected_record(self):
        index = self.currentRow()
        return self.records[index] if 0 <= index < len(self.records) else None


class ScenarioDialog(ModelDialog):
    """Unit-aware operating overrides using the existing scenario parameter paths."""

    def __init__(self, scenario, project, parent=None, *, choices=None):
        self.project = deepcopy(project)
        self.parameters = {}

        def parameter(path, label, unit):
            self.parameters[path] = (label, unit)

        for terminal in project.terminals:
            if terminal.kind in {"voltage", "current", "density", "gate"}:
                unit = {"voltage": "V", "current": "A", "gate": "V", "density": "A/m²"}[terminal.kind]
                parameter(f"terminals.{terminal.id}.value", f"{terminal.name or terminal.id} · {terminal.kind}", unit)
        for port in project.power.ports:
            if port.kind in {"voltage", "current"}:
                parameter(f"power.ports.{port.id}.value", f"{port.name or port.id} · port {port.kind}", "V" if port.kind == "voltage" else "A")
        for layer in project.layers:
            if layer.electrical.kind == "hemt":
                for field in ("gate_voltage", "threshold_voltage"):
                    parameter(f"layers.{layer.id}.electrical.{field}", f"{layer.name} · {field.replace('_', ' ')}", "V")
        for sink in project.thermal_boundaries:
            if sink.kind != "insulated":
                parameter(f"thermal_boundaries.{sink.id}.value", f"{sink.id} · {sink.kind.replace('_', ' ')}",
                          {"temperature": "K", "coefficient": "W/(m² K)", "resistance_area": "m² K/W", "resistance": "K/W"}[sink.kind])
                if sink.kind != "temperature":
                    parameter(f"thermal_boundaries.{sink.id}.ambient", f"{sink.id} · ambient temperature", "K")
        parameter("solver.initial_temperature", "Initial temperature", "K")
        choices = dict(choices or {})
        ports = list(dict.fromkeys([item.id for item in project.power.ports] + [item.id for item in project.terminals]))
        choices.update(service_port=ports, reference_port=ports)
        super().__init__(scenario, "Operating scenario · equal-service conditions", parent, choices=choices)
        self.labels["settings"].setText("Bias and thermal overrides")
        original = self.widgets["settings"]
        original.hide()
        layout = original.parentWidget().layout()
        self.setting_table = QTableWidget(0, 3)
        self.setting_table.setHorizontalHeaderLabels(["Parameter", "Scenario value", "Unit"])
        self.setting_table.setAccessibleName("Scenario bias and thermal condition overrides")
        self.setting_table.horizontalHeader().setStretchLastSection(True)
        self.setting_table.setMinimumHeight(170)
        self.setting_table.setMaximumHeight(260)
        layout.insertWidget(0, self.setting_table)
        row = QHBoxLayout()
        self.add_setting_button, self.remove_setting_button = QPushButton("Add condition"), QPushButton("Remove selected condition")
        self.add_setting_button.clicked.connect(lambda: self.add_setting())
        self.remove_setting_button.clicked.connect(self.remove_setting)
        row.addWidget(self.add_setting_button)
        row.addWidget(self.remove_setting_button)
        layout.insertLayout(1, row)
        note = QLabel("Each value replaces a baseline condition for this scenario. Target current controls the service source; the reference stays at 0 V.")
        note.setWordWrap(True)
        layout.insertWidget(2, note)
        self.advanced_settings = QGroupBox("Advanced scalar settings (SI)")
        self.advanced_settings.setCheckable(True)
        self.advanced_settings.setChecked(False)
        self.advanced_values = QTextEdit(json.dumps({key: value for key, value in scenario.settings.items() if key not in self.parameters}, indent=2))
        self.advanced_values.setAccessibleName("Advanced scenario scalar settings in SI")
        self.advanced_values.setMaximumHeight(130)
        QVBoxLayout(self.advanced_settings).addWidget(self.advanced_values)
        self.advanced_values.hide()
        self.advanced_settings.toggled.connect(self.advanced_values.setVisible)
        layout.insertWidget(3, self.advanced_settings)
        for path, value in scenario.settings.items():
            if path in self.parameters:
                self.add_setting(path, value)
        self.resize(760, 720)

    def add_setting(self, path=None, value=None):
        from etlayout.simulation.sweep import get_parameter
        row = self.setting_table.rowCount()
        self.setting_table.insertRow(row)
        selector, entry, unit = QComboBox(), QLineEdit(), QTableWidgetItem()
        for key, (label, units) in self.parameters.items():
            selector.addItem(f"{label} ({units})", key)
            selector.setItemData(selector.count() - 1, key, Qt.ItemDataRole.ToolTipRole)
        used = {self.setting_table.cellWidget(index, 0).currentData() for index in range(row)}
        path = path or next((key for key in self.parameters if key not in used), next(iter(self.parameters)))
        selector.setCurrentIndex(selector.findData(path))
        entry.setAccessibleName("Scenario condition value")
        selector.setAccessibleName("Scenario condition parameter")
        unit.setFlags(unit.flags() & ~Qt.ItemFlag.ItemIsEditable)
        self.setting_table.setCellWidget(row, 0, selector)
        self.setting_table.setCellWidget(row, 1, entry)
        self.setting_table.setItem(row, 2, unit)

        def select():
            key = selector.currentData()
            unit.setText(self.parameters[key][1])
            entry.setText(f"{get_parameter(self.project, key):.12g}")
            entry.setToolTip("Use " + self.parameters[key][1] + " or an explicit compatible engineering unit.")

        selector.currentIndexChanged.connect(select)
        select()
        if value is not None:
            entry.setText(f"{value:.12g}" if isinstance(value, (int, float)) and not isinstance(value, bool) else str(value))
        self.setting_table.setCurrentCell(row, 1)
        self.setting_table.setColumnWidth(0, 275)
        self.setting_table.setColumnWidth(1, 130)

    def remove_setting(self):
        row = self.setting_table.currentRow()
        if row >= 0:
            self.setting_table.removeRow(row)

    def accept(self):
        super().accept()
        if self.result() != QDialog.DialogCode.Accepted and not self.errors["settings"].isHidden():
            self.scroll.ensureWidgetVisible(self.setting_table)
            self.setting_table.setFocus()

    def validate_candidate(self, scenario):
        from etlayout.simulation.sweep import set_parameter
        errors = super().validate_candidate(scenario)
        if scenario.id in getattr(self, "reserved_ids", set()):
            errors["id"] = "Scenario identifiers must be unique."
        ports = {item.id for item in self.project.power.ports} | {item.id for item in self.project.terminals}
        if scenario.service_port not in ports or scenario.reference_port not in ports or scenario.service_port == scenario.reference_port:
            errors["service_port"] = "Choose distinct existing source and reference ports or terminals."
        for name in ("required_current", "compliance_voltage", "weight"):
            if getattr(scenario, name) <= 0:
                errors[name] = "Enter a positive value."
        if not 0 <= scenario.service_tolerance < 1:
            errors["service_tolerance"] = "Use a relative tolerance from zero up to (excluding) one."
        try:
            def unique(pairs):
                values = {}
                for key, value in pairs:
                    if key in values:
                        raise ValueError("Duplicate advanced parameter: " + key)
                    values[key] = value
                return values
            values = json.loads(self.advanced_values.toPlainText(), object_pairs_hook=unique)
            if not isinstance(values, dict):
                raise ValueError("Advanced settings must be an object of scalar parameter values.")
            for row in range(self.setting_table.rowCount()):
                path = self.setting_table.cellWidget(row, 0).currentData()
                if path in values:
                    raise ValueError("Duplicate parameter; remove the duplicate row or advanced entry: " + path)
                values[path] = parse_number(self.setting_table.cellWidget(row, 1).text(), self.parameters[path][1])
            candidate = deepcopy(self.project)
            for path, value in values.items():
                if not path.startswith(("terminals.", "power.ports.", "solver.initial_temperature", "thermal_boundaries.", "layers.")):
                    raise ValueError("This setting is not supported by operating scenarios: " + path)
                if path.startswith("layers.") and not path.endswith((".gate_voltage", ".threshold_voltage")):
                    raise ValueError("Scenario layer overrides support gate/threshold bias only: " + path)
                for identifier, forced in ((scenario.reference_port, True), (scenario.service_port, scenario.service_mode == "target_current")):
                    prefix = "power.ports" if identifier in {port.id for port in self.project.power.ports} else "terminals"
                    if forced and path in {f"{prefix}.{identifier}.value", f"{prefix}.{identifier}.kind"}:
                        raise ValueError("Equal-service conditions control this source/reference; remove its override: " + path)
                set_parameter(candidate, path, value)
                if (path == "solver.initial_temperature" or path.startswith("thermal_boundaries.")) and path.endswith((".value", ".ambient", "initial_temperature")) and value <= 0:
                    raise ValueError("Thermal temperatures and boundary coefficients/resistances must be positive: " + path)
            scenario.settings = values
        except (TypeError, ValueError, SimulationError) as error:
            errors["settings"] = str(error)
        return errors


class PowerWorkbench(QDialog):
    """Shared-document studies; standalone callers retain an explicit modal draft.

    The embedding adapter supplies ``project`` and ``transact(label, mutator)``.
    No Project is cached in embedded mode, including when undo replaces it.
    Worker inputs and generated candidates remain independent immutable snapshots.
    """
    busy_changed = Signal(bool)
    result_ready = Signal(object)
    document_changed = Signal()

    def __init__(self, project, result=None, parent=None, *, execution=None, mask_keys=(), document=None):
        super().__init__(parent)
        self.document = document
        self._standalone_project = deepcopy(project) if document is None else None
        self._reference_snapshot = None
        self._reference_widget_snapshot = None
        self._job_configuration = None
        self._job_project = None
        self._candidate_source = None
        self._running = False
        if document is not None:
            self.setWindowFlags(Qt.WindowType.Widget)
        self.setWindowTitle("Power interconnects & routing experiments")
        self.resize(1120, 760)
        self.result = result if result is not None and physics_hash(result.configuration) == physics_hash(self.project) else None
        self.execution = execution
        self.mask_keys = mask_keys
        self.job = None
        self.operation = ""
        self.analysis = deepcopy(self.result.analysis) if self.result else {}
        self.multiport_report = None
        self.history = None
        self.candidate = None
        self.changes = []
        self.actions = []
        outer = QVBoxLayout(self)
        note = QLabel("Physical supports and SI quantities remain explicit. Generated designs are checked before solving; "
                      "only equal-service, mesh-verified finalists can be exported as accepted designs.")
        note.setWordWrap(True)
        outer.addWidget(note)
        self.tabs = QTabWidget()
        self.tabs.setAccessibleName("Power analysis workflow stages")
        outer.addWidget(self.tabs, 1)
        self.build_setup()
        self.build_analysis()
        self.build_routing()
        self.build_experiments()
        self.build_multiport()
        status_row = QHBoxLayout()
        self.status = QLabel("Configure physical intent, then solve or run a saved experiment.")
        self.status.setWordWrap(True)
        status_row.addWidget(self.status, 1)
        self.progress = QProgressBar()
        self.progress.setRange(0, 100)
        self.progress.setMaximumWidth(180)
        self.progress.hide()
        status_row.addWidget(self.progress)
        self.cancel = QPushButton("Cancel computation")
        self.cancel.setEnabled(False)
        self.cancel.clicked.connect(self.cancel_job)
        status_row.addWidget(self.cancel)
        outer.addLayout(status_row)
        self.buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel)
        self.buttons.button(QDialogButtonBox.StandardButton.Save).setText("Apply settings && close")
        self.buttons.accepted.connect(self.accept)
        self.buttons.rejected.connect(self.reject)
        outer.addWidget(self.buttons)
        self.buttons.setVisible(document is None)
        self.refresh_setup()
        self.reload_reference()
        self.show_analysis(self.analysis)

    @property
    def project(self):
        return self.document.project if self.document is not None else self._standalone_project

    @project.setter
    def project(self, project):
        if self.document is None:
            self._standalone_project = project
            return
        replacement = project_from_dict(project.to_dict())

        def replace(target):
            for field in fields(replacement):
                setattr(target, field.name, deepcopy(getattr(replacement, field.name)))

        self._transact("Use generated routing candidate", replace)

    def _transact(self, label, callback):
        if self.job:
            raise ValueError("Wait for or cancel the current computation before editing its setup.")
        if self.document is None:
            callback(self.project)
        else:
            self.document.transact(label, callback)
            self.document_changed.emit()

    def refresh_document(self):
        """Refresh after a shared-document edit or undo without discarding a draft."""
        self.refresh_setup()
        self.multiport_workflow.set_project(self.project)
        if self.multiport_report is not None and self.multiport_report["physics_hash"] != physics_hash(self.project):
            self.clear_multiport()
        if not self.has_pending_reference():
            self.reload_reference()
        if self.result is not None and physics_hash(self.result.configuration) != physics_hash(self.project):
            self.status.setText("Inspecting a previous result; the working setup has changed. Solve the working setup to update it.")

    def bind_result(self, result):
        """Inspect the explicitly selected immutable run, including historical runs."""
        self.result = result
        self.show_analysis(result.analysis if result is not None else {})
        self.analysis_map.set_geometries({})
        self.analysis_map.show_field(None, "", "")
        if self.multiport_report is None or self.multiport_report["physics_hash"] != physics_hash(self.project):
            self.clear_multiport()
        if result is None:
            self.status.setText("No run selected. Configure or evaluate the working setup.")
        elif physics_hash(result.configuration) != physics_hash(self.project):
            self.status.setText("Inspecting a historical run. These values belong to its saved setup.")
        else:
            self.status.setText("Inspecting the selected run; its physical setup matches the working setup.")

    def button(self, layout, title, callback):
        button = QPushButton(title.replace("&", "&&"))
        button.clicked.connect(callback)
        if isinstance(layout, QGridLayout):
            index = layout.count()
            layout.addWidget(button, index // 2, index % 2)
        else:
            layout.addWidget(button)
        self.actions.append(button)
        return button

    def action_row(self):
        return QGridLayout() if self.document is not None else QHBoxLayout()

    def add_page(self, page, title):
        if self.document is not None:
            scroll = QScrollArea()
            scroll.setWidgetResizable(True)
            scroll.setFrameShape(QScrollArea.Shape.NoFrame)
            scroll.setWidget(page)
            scroll.setAccessibleName(title + " study controls")
            self.tabs.addTab(scroll, title)
        else:
            self.tabs.addTab(page, title)

    @staticmethod
    def limitation(title, explanation):
        group = QGroupBox(title)
        group.setCheckable(True)
        group.setChecked(False)
        details = QLabel(explanation + " See docs/scientific-acceptance.md.")
        details.setWordWrap(True)
        details.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        QVBoxLayout(group).addWidget(details)
        details.hide()
        group.toggled.connect(details.setVisible)
        return group

    def build_setup(self):
        page = QWidget()
        layout = QVBoxLayout(page)
        self.setup_kind = QComboBox()
        self.setup_kind.setAccessibleName("Physical intent editor")
        self.record_types = {"measurements": MeasurementSpec, "ports": PhysicalPort,
            "devices": DeviceGroup, "recipes": RoutingRecipe, "experiments": OptimizationExperiment,
            "terminals": Terminal}
        for key, label in (("measurements", "Scoped measurements & limits"), ("ports", "Physical ports & landings"),
                           ("terminals", "Landing footprints, electrodes & passive probes"),
                           ("devices", "Device / finger groups"), ("recipes", "Permitted routing recipes"),
                           ("experiments", "Scenarios, service constraints & search bounds")):
            self.setup_kind.addItem(label, key)
        self.setup_kind.currentIndexChanged.connect(self.refresh_setup)
        layout.addWidget(self.setup_kind)
        self.setup_help = QLabel()
        self.setup_help.setWordWrap(True)
        layout.addWidget(self.setup_help)
        self.measurement_limits = self.limitation(
            "Measurement support and acceptance",
            "Categories intersect; selections within a category union, including exact nested device clips. "
            "Exceedance uses a dimensional threshold and a separate permitted fraction. Legacy limits require explicit migration.")
        layout.addWidget(self.measurement_limits)
        self.setup_table = RecordTable([("id", "Identifier"), ("summary", "Physical definition")])
        layout.addWidget(self.setup_table, 1)
        row = self.action_row()
        self.button(row, "Edit collection…", self.edit_records)
        self.button(row, "Technology rules…", self.edit_rules)
        self.button(row, "Validate current settings", self.validate_settings)
        layout.addLayout(row)
        reference = QFormLayout()
        self.reference = QComboBox()
        self.reference.addItem("No explicit reference / unambiguous single reservoir", "")
        for boundary in self.project.thermal_boundaries:
            self.reference.addItem(boundary.id, boundary.id)
        if self.project.power.thermal_reference_boundary and self.reference.findData(self.project.power.thermal_reference_boundary) < 0:
            self.reference.addItem(self.project.power.thermal_reference_boundary, self.project.power.thermal_reference_boundary)
        self.reference.setCurrentIndex(self.reference.findData(self.project.power.thermal_reference_boundary))
        self.reference_temperature = QLineEdit("" if self.project.power.thermal_reference_temperature is None
            else str(self.project.power.thermal_reference_temperature))
        self.reference_temperature.setPlaceholderText("Optional explicit Kelvin reference")
        self.reference.setToolTip("Named reservoir used to report temperature rise. This reporting choice does not create a heat sink or change its boundary temperature.")
        self.reference_temperature.setToolTip("Optional absolute reporting reference in kelvin: 300 K = 26.85 °C. This changes reported rise, not physical cooling or the solved temperature. Leave empty for the named or unambiguous reservoir.")
        self.reference.currentIndexChanged.connect(self.reference_edited)
        self.reference_temperature.textEdited.connect(self.reference_edited)
        reference.addRow("Named thermal reference", self.reference)
        reference.addRow("Explicit reference temperature (K)", self.reference_temperature)
        layout.addLayout(reference)
        self.reference_actions = QWidget()
        reference_buttons = QHBoxLayout(self.reference_actions)
        reference_buttons.setContentsMargins(0, 0, 0, 0)
        self.reference_apply = self.button(reference_buttons, "Apply reporting reference", self.apply_reference)
        self.reference_discard = self.button(reference_buttons, "Discard reference edits", self.reload_reference)
        layout.addWidget(self.reference_actions)
        self.reference_actions.setVisible(self.document is not None)
        self.add_page(page, "Setup")

    def choices(self):
        from etlayout.power.analysis import QUANTITIES
        return {"layer_id": [item.id for item in self.project.layers],
            "material_id": sorted(self.project.materials), "quantity": sorted(QUANTITIES),
            "terminal_id": [item.id for item in self.project.terminals],
            "source_port": ["", *[item.id for item in self.project.power.ports]],
            "drain_port": ["", *[item.id for item in self.project.power.ports]],
            "service_port": [item.id for item in self.project.power.ports],
            "reference_port": [item.id for item in self.project.power.ports],
            "measurement_id": [item.id for item in self.project.power.measurements],
            "recipe_id": [item.id for item in self.project.power.recipes]}

    def refresh_setup(self, *_):
        if not hasattr(self, "setup_table"):
            return
        key = self.setup_kind.currentData()
        owner = self.project if key == "terminals" else self.project.power
        self.setup_table.set_records([{"id": item.id, "summary": self.record_summary(item)}
                                      for item in getattr(owner, key)])
        helps = {"measurements": "Select layers, materials, nets, regions, devices, contacts or ports. Metal A/m² and semiconductor A/m are distinct. Raw maxima are mesh sensitive; fix hotspot size before optimizing.",
            "ports": "Landings reference terminal footprints. A passive probe observes without constraints; a floating electrode redistributes current. Feed resistance is in Ω and thermal fraction controls heat deposition.",
            "terminals": "Use the existing geometry editor for each footprint. Choose probe for passive sensing; floating is an equipotential electrode with zero net current.",
            "devices": "Keep group identities and intended active width/area fixed across routing candidates. Group summaries overlap by definition and are not added to the disjoint loss ledger.",
            "recipes": "Define a permitted edit region and mask. Recipe-specific parameters use SI. The source layout is preserved; generation writes a new candidate file.",
            "experiments": "Variables modify allowed recipe parameters. Every scenario must meet delivered-current service and compliance. Objectives refer to persisted measurements; limits and averaging windows stay fixed."}
        self.setup_help.setText(helps[key])
        self.measurement_limits.setVisible(key == "measurements")
        if hasattr(self, "experiment"):
            selected = self.experiment.currentText()
            self.experiment.clear()
            self.experiment.addItems([entry.id for entry in self.project.power.experiments])
            self.experiment.setCurrentText(selected)
        self.refresh_recipe_values()
        self.refresh_study_summary()

    @staticmethod
    def record_summary(item):
        if isinstance(item, MeasurementSpec):
            support = ", ".join(f"{key}: {_text(value)}" for key, value in asdict(item.selector).items() if value)
            return f"{item.aggregation} {item.quantity} · {support or 'whole physical support'}"
        if isinstance(item, PhysicalPort):
            return f"{item.kind}: {item.value:g} · landings: {', '.join(landing.terminal_id for landing in item.landings) or 'none'}"
        if isinstance(item, RoutingRecipe):
            return f"{item.kind} · {item.layer_id or item.interface_id} · {_text(item.parameters)}"
        if isinstance(item, OptimizationExperiment):
            return f"{len(item.variables)} variables · {len(item.scenarios)} scenarios · {item.max_evaluations} evaluations"
        if isinstance(item, Terminal):
            return f"{item.kind} · {item.layer_id} · {item.value:g}"
        return _text(asdict(item))

    def edit_records(self):
        key = self.setup_kind.currentData()
        owner = self.project if key == "terminals" else self.project.power
        dialog = ModelCollectionDialog(self.record_types[key], getattr(owner, key), self,
            choices=self.choices(), mask_keys=self.mask_keys)
        if dialog.exec():
            entries = deepcopy(dialog.entries)
            self._transact("Edit " + key, lambda project: setattr(
                project if key == "terminals" else project.power, key, entries))
            self.invalidate_result()
            self.refresh_setup()

    def edit_rules(self):
        dialog = ModelDialog(self.project.power.rules, "Routing technology rules", self,
            choices=self.choices(), mask_keys=self.mask_keys)
        if dialog.exec():
            self._transact("Edit routing technology rules", lambda project:
                setattr(project.power, "rules", deepcopy(dialog.model)))
            self.invalidate_result()

    def reference_edited(self, *_):
        if self.document is None:
            self.invalidate_result()
        elif hasattr(self, "status"):
            self.status.setText("Reporting-reference draft: Apply or Discard before running or leaving this task.")

    def reference_values(self):
        return (self.project.power.thermal_reference_boundary, self.project.power.thermal_reference_temperature)

    def has_pending_reference(self):
        if self._reference_widget_snapshot is None:
            return False
        return (self.reference.currentData(), self.reference_temperature.text()) != self._reference_widget_snapshot

    def reload_reference(self):
        reference, temperature = self.reference_values()
        self.reference.blockSignals(True)
        self.reference.clear()
        self.reference.addItem("No explicit reference / unambiguous single reservoir", "")
        for boundary in self.project.thermal_boundaries:
            self.reference.addItem(boundary.id, boundary.id)
        if reference and self.reference.findData(reference) < 0:
            self.reference.addItem(reference, reference)
        self.reference.setCurrentIndex(self.reference.findData(reference))
        self.reference.blockSignals(False)
        self.reference_temperature.setText("" if temperature is None else str(temperature))
        self._reference_snapshot = (reference, temperature)
        self._reference_widget_snapshot = (self.reference.currentData(), self.reference_temperature.text())

    def apply_reference(self):
        try:
            self.sync_reference()
        except (ValueError, SimulationError) as error:
            self.status.setText(str(error))
            return False
        self.status.setText("Reporting reference applied to the working setup. It does not create a thermal sink.")
        return True

    def resolve_pending(self, decision=None):
        """Resolve the small reference draft explicitly; never silently run it."""
        from etlayout.gui.drafts import ask_pending, resolve_pending
        return resolve_pending(changed=self.has_pending_reference(),
            choose=lambda: decision if decision is not None else ask_pending(self, "Unapplied reporting reference",
                "Apply the reporting-reference edits before leaving, discard them, or stay here?"),
            apply=self.apply_reference, discard=self.reload_reference)

    def sync_reference(self):
        if self.document is not None and self.has_pending_reference() and self.reference_values() != self._reference_snapshot:
            raise ValueError("The reporting reference changed in the shared setup. Discard this draft and edit the current value.")
        temperature = self.reference_temperature.text().strip()
        value = parse_number(temperature, "K") if temperature else None
        if value is not None and value <= 0:
            raise ValueError("Reference temperature must be above zero Kelvin")
        reference = self.reference.currentData()
        if (reference, value) != self.reference_values():
            def apply(project):
                project.power.thermal_reference_boundary = reference
                project.power.thermal_reference_temperature = value
            self._transact("Edit reporting reference", apply)
            self.invalidate_result()
        self.reload_reference()

    def validate_settings(self):
        try:
            if self.document is not None:
                if not self.resolve_pending():
                    return
            else:
                self.sync_reference()
            from etlayout.technology.validation import validate_project
            validate_project(self.project)
            self.status.setText("Typed settings and physical parameter contracts are valid. Generate/solve checks geometric support and connectivity.")
        except (ValueError, SimulationError) as error:
            self.status.setText(str(error))

    def invalidate_result(self, *_):
        if hasattr(self, "multiport_workflow"):
            self.clear_multiport()
        if self.document is not None:
            self.multiport_report = None
            if hasattr(self, "matrix"):
                self.matrix.set_records([])
            self.refresh_document()
            return
        self.result = None
        self.analysis = {}
        self.multiport_report = None
        if hasattr(self, "status"):
            self.status.setText("Settings changed. Solve again before using numerical results.")
        if hasattr(self, "analysis_tables"):
            self.show_analysis({})
            self.analysis_map.show_field(None, "", "")
            self.matrix.set_records([])

    def build_analysis(self):
        page = QWidget()
        layout = QVBoxLayout(page)
        row = self.action_row()
        self.button(row, "Solve & analyze", lambda: self.start("power", {}))
        self.button(row, "Reanalyze retained fields", lambda: self.start("reanalyze", {"result": self.result}))
        self.button(row, "Export analysis…", self.export_analysis)
        self.button(row, "Passive network / SPICE…", self.open_multiport)
        layout.addLayout(row)
        split = QSplitter(Qt.Orientation.Vertical)
        map_panel = QWidget()
        map_layout = QHBoxLayout(map_panel)
        map_layout.setContentsMargins(0, 0, 0, 0)
        self.analysis_map = LayoutView()
        map_layout.addWidget(self.analysis_map, 1)
        scale_panel = QWidget()
        scale_panel.setMaximumWidth(300)
        scale_layout = QVBoxLayout(scale_panel)
        self.contact_legend = QLabel("Select a contact for normal-current density (mA/µm²).")
        self.contact_legend.setWordWrap(True)
        scale_layout.addWidget(self.contact_legend)
        self.contact_colormap = QComboBox()
        self.contact_colormap.setAccessibleName("Contact normal-current density colormap")
        from etlayout.visualization.palettes import COLORMAPS
        self.contact_colormap.addItems(list(COLORMAPS))
        self.contact_colormap.setCurrentText("coolwarm")
        scale_layout.addWidget(self.contact_colormap)
        self.contact_scale = FieldScaleControl()
        scale_layout.addWidget(self.contact_scale)
        scale_layout.addStretch(1)
        map_layout.addWidget(scale_panel)
        self.analysis_map.legend_changed.connect(self.contact_legend.setText)
        self.analysis_map.legend_details.connect(self.contact_scale.set_details)
        self.contact_scale.range_changed.connect(self.highlight_contact)
        self.contact_colormap.currentIndexChanged.connect(self.highlight_contact)
        split.addWidget(map_panel)
        self.analysis_views = QTabWidget()
        self.analysis_tables = {}
        definitions = {
            "measurements": [("id", "Measurement"), ("status", "Status"), ("value", "Value"), ("unit", "Unit"), ("within_limits", "Within limits"), ("reason", "Reason")],
            "contacts": [("id", "Contact / via"), ("status", "Status"), ("current_a_to_b", "Current A → B (A)"), ("area", "Area (m²)"), ("j_normal_mean", "Mean normal J (A/m²)"), ("j_normal_peak", "Peak normal J (A/m²)"), ("power", "Loss (W)"), ("absolute_loading_fraction", "Absolute loading (fraction)")],
            "ports": [("id", "Port"), ("voltage", "Voltage (V)"), ("current", "Current (A)"), ("feed_power", "Feed loss (W)"), ("external_node", "External node")],
            "devices": [("id", "Device / finger"), ("current", "Current (A)"), ("current_per_width", "Current / width (A/m)"), ("temperature_max", "Peak temperature (K)"), ("layer_loss", "Scoped loss (W)"), ("effective_gate_bias", "Effective gate bias (V)")],
            "reservoirs": [("id", "Reservoir"), ("temperature", "Temperature (K)"), ("heat_out", "Heat leaving domain (W)"), ("status", "Status")],
            "loss_budget": [("id", "Contributor"), ("category", "Disjoint category"), ("power", "Loss (W)"), ("thermal_deposition", "Domain heat (W)"), ("excluded_power", "External heat (W)")],
        }
        for key, columns in definitions.items():
            units = {"measurements": {"value": "record"},
                     "contacts": {"area": "m²", "j_normal_mean": "A/m²", "j_normal_peak": "A/m²",
                                  "absolute_loading_fraction": "fraction"},
                     "devices": {"current_per_width": "A/m"}}.get(key, {})
            table = RecordTable(columns, units=units)
            table.setAccessibleName(key.replace("_", " ") + " results")
            self.analysis_tables[key] = table
            self.analysis_views.addTab(table, key.replace("_", " ").capitalize())
        self.analysis_tables["contacts"].itemSelectionChanged.connect(self.highlight_contact)
        self.matrix = RecordTable([("port", "Port")])
        self.analysis_views.addTab(self.matrix, "Multiport matrix")
        split.addWidget(self.analysis_views)
        split.setSizes([230, 230])
        layout.addWidget(split, 1)
        self.analysis_notes = QLabel("Currents are positive into ports; interface current is positive from layer A to B. Select a contact to inspect its map.")
        self.analysis_notes.setWordWrap(True)
        layout.addWidget(self.analysis_notes)
        self.add_page(page, "Analysis")

    def show_analysis(self, report):
        self.analysis = report
        for key, table in self.analysis_tables.items():
            data = report.get(key, {})
            table.set_records(data.get("entries", []) if key == "loss_budget" else data)

    def highlight_contact(self):
        record = self.analysis_tables["contacts"].selected_record()
        if not record:
            return
        self.analysis_map.show_field(None, "", "")
        if self.result is not None:
            interface = record.get("interface_id")
            values = self.result.interface_fields.get(interface, {}).get("j_normal")
            if values is not None:
                from copy import copy
                mapped = copy(self.result)
                layer = record["layer_a"]
                mapped.fields = {layer: {"contact_normal_density": np.mean(values, axis=1)}}
                mapped.element_fields = {}
                try:
                    limits = self.contact_scale.validated_limits()
                except ValueError as error:
                    self.status.setText(str(error))
                    return
                self.analysis_map.show_field(mapped, layer, "contact_normal_density", colormap=self.contact_colormap.currentText(),
                    limits=limits, region="interface:" + interface)
                source = project_from_dict(self.result.configuration)
                self.analysis_map.set_layer_focus(layer, {t.id: t.layer_id for t in source.terminals})
                self.analysis_map.fit_field()
        if record.get("geometry_wkt"):
            self.analysis_map.show_region(from_wkt(record["geometry_wkt"]), record.get("id", "Contact"))
        self.status.setText("Selected contact: signed positive current flows from layer A to layer B. Map shows element mean normal density; raw peaks are mesh sensitive.")

    def build_multiport(self):
        from etlayout.gui.multiport_workflow import MultiportWorkflow
        page = QWidget()
        layout = QVBoxLayout(page)
        self.multiport_workflow = MultiportWorkflow()
        layout.addWidget(self.multiport_workflow)
        row = self.action_row()
        self.button(row, "Edit physical ports & landings", self.edit_multiport_ports)
        host = getattr(self.document, "window", None)
        guided = getattr(host, "guided", None)
        if guided is not None:
            self.button(row, "Edit interlayer connections", guided.open_stack_connections)
        self.button(row, "Extract passive network", self.extract_multiport)
        self.network_export = self.button(row, "Export reviewed network…", self.export_multiport)
        self.network_export.setEnabled(False)
        self.button(row, "View full port matrix", self.open_multiport_matrix)
        layout.addLayout(row)
        layout.addStretch()
        self.multiport_workflow.set_project(self.project)
        self.multiport_workflow.selection_changed.connect(self.clear_multiport)
        self.add_page(page, "Passive network")

    def edit_multiport_ports(self):
        self.setup_kind.setCurrentIndex(self.setup_kind.findData("ports"))
        self.tabs.setCurrentIndex(0)

    def open_multiport_matrix(self):
        if self.multiport_report is None:
            self.status.setText("Extract a passive network before inspecting its port matrix.")
            return
        self.analysis_views.setCurrentWidget(self.matrix)
        self.tabs.setCurrentIndex(1)

    def open_multiport(self):
        self.multiport_workflow.set_project(self.project)
        self.tabs.setCurrentIndex(4)

    def clear_multiport(self):
        self.multiport_report = None
        self.network_export.setEnabled(False)
        self.matrix.set_records([])
        self.multiport_workflow.clear_report()

    def extract_multiport(self):
        from etlayout.gui.multiport_workflow import extraction_prerequisites
        self.multiport_workflow.set_project(self.project)
        reasons = extraction_prerequisites(self.project)
        ids = self.multiport_workflow.selected_ports()
        if reasons or len(ids) < 2:
            self.status.setText(" ".join(reasons) if reasons else "Select at least two physical ports for extraction.")
            self.open_multiport()
            return
        self.start("multiport", {"port_ids": ids, "reference": self.multiport_workflow.reference.currentData()})

    def export_analysis(self):
        if self.result is None:
            self.status.setText("Solve the current settings before exporting analysis.")
            return
        directory = QFileDialog.getExistingDirectory(self, "Export complete analysis")
        if directory:
            self.start("power", {"output": directory, "plots": False})

    def export_multiport(self):
        if self.multiport_report is None:
            self.status.setText("Extract a passive multiport before exporting.")
            return
        if self.multiport_report["physics_hash"] != physics_hash(self.project):
            self.clear_multiport()
            self.status.setText("The working setup changed. Extract its passive network again before export.")
            return
        path, selected = QFileDialog.getSaveFileName(self, "Export passive multiport", "multiport.json",
            "JSON (*.json);;CSV (*.csv);;SPICE (*.cir)")
        if path:
            try:
                from etlayout.power.multiport import export_multiport
                format_name = "spice" if "SPICE" in selected else "csv" if "CSV" in selected else "json"
                export_multiport(self.multiport_report, path, format=format_name)
                self.status.setText("Exported " + path)
            except (ValueError, OSError, SimulationError) as error:
                self.status.setText(str(error))

    def build_routing(self):
        page = QWidget()
        layout = QVBoxLayout(page)
        form = QFormLayout()
        self.routing_output = QLineEdit(str(Path.cwd() / f"candidate-{uuid4().hex[:8]}.oas"))
        self.routing_parameters = QLineEdit("{}")
        self.routing_parameters.setPlaceholderText('SI overrides, e.g. {"bus.width": 5e-6}')
        form.addRow("New candidate GDS / OASIS path", self.routing_output)
        if self.document is None:
            form.addRow("Recipe parameter overrides (JSON)", self.routing_parameters)
        layout.addLayout(form)
        self.recipe_values = QTableWidget(0, 3)
        self.recipe_values.setHorizontalHeaderLabels(["Allowed recipe / parameter", "Candidate value", "Unit"])
        self.recipe_values.setAccessibleName("Typed routing candidate parameters")
        self.recipe_values.setMaximumHeight(200)
        self.recipe_values.horizontalHeader().setStretchLastSection(True)
        self._routing_inputs = {}
        self._routing_recipe_signature = None
        self.recipe_values.setVisible(self.document is not None)
        layout.addWidget(self.recipe_values)
        if self.document is not None:
            advanced = QPushButton("Use advanced parameter overrides (JSON; takes precedence)")
            self.routing_advanced = advanced
            advanced.setCheckable(True)
            advanced.toggled.connect(self.routing_parameters.setVisible)
            self.routing_parameters.hide()
            layout.addWidget(advanced)
            layout.addWidget(self.routing_parameters)
        self.refresh_recipe_values()
        row = self.action_row()
        self.button(row, "Choose output…", self.choose_routing_path)
        self.button(row, "Check technology rules", lambda: self.start("routing_validate", {}))
        self.button(row, "Generate & preview candidate", self.generate_candidate)
        self.button(row, "Use valid candidate in this project", self.use_candidate)
        layout.addLayout(row)
        self.routing_map = LayoutView()
        layout.addWidget(self.routing_map, 1)
        self.routing_table = RecordTable([("rule_id", "Rule / change"), ("severity", "Status"),
            ("object_id", "Object"), ("message", "Diagnostic / difference")])
        self.routing_table.itemSelectionChanged.connect(self.highlight_routing)
        layout.addWidget(self.routing_table, 1)
        note = QLabel("Generation writes a separate candidate and verifies its actual GDS/OASIS round trip. "
                      "Invalid candidates remain diagnostic records and cannot replace this project.")
        note.setWordWrap(True)
        layout.addWidget(note)
        self.add_page(page, "Routing")

    def refresh_recipe_values(self):
        if not hasattr(self, "recipe_values"):
            return
        signature = _json_dump([asdict(recipe) for recipe in self.project.power.recipes])
        if signature == self._routing_recipe_signature:
            return
        self._routing_recipe_signature = signature
        common = {
            "bus": ("width", "start_width", "end_width", "start", "end", "center", "axis"),
            "strap": ("width", "start_width", "end_width", "start", "end", "center", "axis"),
            "finger_feed": ("count", "pitch", "finger_width", "bus_width", "axis", "end"),
            "via_array": ("rows", "columns", "size", "pitch", "pitch_x", "pitch_y", "center_x", "center_y", "grade", "pattern"),
        }
        choices = {"axis": ("x", "y"), "pattern": ("uniform", "graded", "staggered"), "end": ("single", "dual")}
        rows = [(recipe, name) for recipe in self.project.power.recipes for name in common.get(recipe.kind, ())]
        self.recipe_values.setRowCount(len(rows))
        self._routing_inputs = {}
        for row, (recipe, name) in enumerate(rows):
            key = f"{recipe.id}.{name}"
            value = recipe.parameters.get(name)
            enum = choices.get(name) if name != "end" or recipe.kind == "finger_feed" else None
            unit = "" if enum or name in {"rows", "columns", "count", "grade"} else "µm"
            if enum:
                editor = QComboBox()
                editor.addItem("Use recipe default", None)
                for option in enum:
                    editor.addItem(option, option)
                editor.setCurrentIndex(max(0, editor.findData(value)))
            else:
                editor = QLineEdit("" if value is None else f"{value * (1e6 if unit else 1):.10g}")
                editor.setPlaceholderText("Use recipe default")
            editor.setAccessibleName(key + (" in " + unit if unit else ""))
            for column, text in ((0, key), (2, unit or ("integer" if name in {"rows", "columns", "count"} else "choice" if enum else "dimensionless"))):
                item = QTableWidgetItem(text)
                item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsEditable)
                self.recipe_values.setItem(row, column, item)
            self.recipe_values.setCellWidget(row, 1, editor)
            self._routing_inputs[key] = (editor, unit, name in {"rows", "columns", "count"})
        self.recipe_values.resizeColumnsToContents()

    def candidate_parameters(self):
        from etlayout.cli.power import read_object
        values = {}
        if self.document is not None:
            for key, (editor, unit, integral) in self._routing_inputs.items():
                if isinstance(editor, QComboBox):
                    value = editor.currentData()
                else:
                    raw = editor.text().strip()
                    value = parse_number(raw, unit) if raw else None
                    if value is not None and integral:
                        if value != int(value):
                            raise ValueError(key + " must be an integer")
                        value = int(value)
                if value is not None:
                    values[key] = value
        if self.document is None or self.routing_advanced.isChecked():
            values.update(read_object(self.routing_parameters.text()))
        return values

    def choose_routing_path(self):
        path, _ = QFileDialog.getSaveFileName(self, "New candidate layout", self.routing_output.text(), "OASIS (*.oas);;GDSII (*.gds)")
        if path:
            self.routing_output.setText(path)

    def generate_candidate(self):
        try:
            parameters = self.candidate_parameters()
            self.start("routing_generate", {"parameters": parameters, "output": self.routing_output.text()})
        except (ValueError, OSError, SimulationError) as error:
            self.status.setText(str(error))

    def highlight_routing(self):
        record = self.routing_table.selected_record()
        if not record:
            return
        if record.get("geometry_wkt"):
            self.routing_map.show_region(from_wkt(record["geometry_wkt"]), record.get("object_id", "Change"))
        elif record.get("bounds"):
            from shapely.geometry import box
            self.routing_map.show_region(box(*record["bounds"]), record.get("rule_id", "Violation"))

    def use_candidate(self):
        if not self.candidate or not self.candidate["report"].get("valid"):
            self.status.setText("Generate a physically valid candidate before using it.")
            return
        if self.document is not None and not self.resolve_pending():
            return
        if self.document is not None and self._candidate_source != configuration_hash(self.project):
            self.status.setText("The working setup changed after this candidate was generated. Regenerate it before applying.")
            return
        self.project = deepcopy(self.candidate["project"])
        self.invalidate_result()
        self.refresh_setup()
        self.reload_reference()
        self.status.setText("Valid candidate applied to the shared working setup; Undo restores the previous setup."
            if self.document is not None else "Valid candidate is now the project draft. Apply settings & close to keep it.")

    def build_experiments(self):
        page = QWidget()
        layout = QVBoxLayout(page)
        intro = QLabel("Baseline → Allowed changes → Operating scenarios → Goals / limits → Preview / check → Evaluate → Compare / verify")
        intro.setWordWrap(True)
        intro.setAccessibleName("Routing study workflow")
        layout.addWidget(intro)
        self.study_summary = QLabel()
        self.study_summary.setWordWrap(True)
        layout.addWidget(self.study_summary)
        form = QFormLayout()
        self.experiment = QComboBox()
        self.experiment.setAccessibleName("Saved routing experiment")
        self.experiment.currentIndexChanged.connect(self.refresh_study_summary)
        self.experiment_output = QLineEdit(str(Path.cwd() / "power-experiment"))
        self.resume = QCheckBox("Resume compatible completed checkpoints")
        self.resume.setChecked(True)
        form.addRow("Experiment", self.experiment)
        form.addRow("Experiment folder", self.experiment_output)
        form.addRow(self.resume)
        layout.addLayout(form)
        self.scenario_table = RecordTable([("id", "Operating scenario"), ("service_mode", "Service condition"),
            ("service_port", "Source port"), ("reference_port", "Return port"),
            ("required_current", "Required current (A)"), ("compliance_voltage", "Compliance (V)"),
            ("service_tolerance", "Relative service tolerance")])
        self.scenario_table.setAccessibleName("Operating scenarios and equal-service conditions")
        self.scenario_table.setMaximumHeight(160)
        self.scenario_table.itemDoubleClicked.connect(lambda *_: self.edit_scenario())
        layout.addWidget(self.scenario_table)
        scenario_row = self.action_row()
        self.button(scenario_row, "Add operating scenario…", lambda: self.edit_scenario(new=True))
        self.button(scenario_row, "Edit selected scenario…", self.edit_scenario)
        self.button(scenario_row, "Remove selected scenario", self.remove_scenario)
        self.button(scenario_row, "Edit experiment goals and limits…", self.edit_experiment)
        layout.addLayout(scenario_row)
        row = self.action_row()
        self.button(row, "Choose folder…", self.choose_experiment_folder)
        self.button(row, "Start / resume experiment", self.run_experiment)
        self.button(row, "Open saved history…", self.open_history)
        self.button(row, "Export selected verified finalist…", self.export_finalist)
        layout.addLayout(row)
        self.history_identity = QLabel("No experiment history loaded.")
        self.history_identity.setWordWrap(True)
        layout.addWidget(self.history_identity)
        self.candidates = RecordTable([("id", "Candidate"), ("status", "Status"),
            ("objectives", "Objectives"), ("reason", "Failure / uncertainty"), ("parameters", "Routing parameters")])
        self.candidates.itemSelectionChanged.connect(self.show_candidate)
        self.history_views = QTabWidget()
        self.history_views.addTab(self.candidates, "Candidates")
        self.pareto_plot = ParetoPanel()
        self.pareto_plot.candidate_selected.connect(self.select_plot_candidate)
        self.history_views.addTab(self.pareto_plot, "Pareto plot")
        layout.addWidget(self.history_views, 1)
        self.pareto_only = QCheckBox("Show feasible Pareto candidates only")
        self.pareto_only.toggled.connect(self.show_history)
        layout.addWidget(self.pareto_only)
        self.candidate_details = QTextEdit()
        self.candidate_details.setReadOnly(True)
        self.candidate_details.setAccessibleName("Scenario service and mesh verification evidence")
        self.candidate_details.setMaximumHeight(130)
        layout.addWidget(self.candidate_details)
        self.add_page(page, "Experiments")

    def refresh_study_summary(self):
        if not hasattr(self, "study_summary"):
            return
        experiment = next((entry for entry in self.project.power.experiments if entry.id == self.experiment.currentText()), None)
        if experiment is None:
            self.study_summary.setText("A configured baseline is enough to draft a study; a completed solve is not required. Define recipes, scenarios and goals in Setup.")
        else:
            self.study_summary.setText(f"Working baseline: {self.project.name} · {len(self.project.power.recipes)} permitted recipes · "
                f"{len(experiment.scenarios)} operating scenarios · {len(experiment.objectives)} objectives · "
                f"{len(experiment.constraints)} constraints. Evaluation enforces physical checks and equal service; accepted exports require mesh verification.")
        if hasattr(self, "scenario_table"):
            self.scenario_table.set_records([asdict(scenario) for scenario in experiment.scenarios] if experiment else [])

    def edit_scenario(self, _checked=False, *, new=False):
        experiment = next((entry for entry in self.project.power.experiments if entry.id == self.experiment.currentText()), None)
        record = self.scenario_table.selected_record()
        if experiment is None or (not new and record is None):
            self.status.setText("Select an experiment and operating scenario to edit.")
            return
        if new:
            ids = {entry.id for entry in experiment.scenarios}
            number = next(index for index in range(1, len(ids) + 2) if f"scenario_{index}" not in ids)
            scenario = OperatingScenario(f"scenario_{number}")
        else:
            scenario = next(entry for entry in experiment.scenarios if entry.id == record["id"])
        dialog = ScenarioDialog(scenario, self.project, self, choices=self.choices())
        dialog.reserved_ids = {entry.id for entry in experiment.scenarios if new or entry.id != scenario.id}
        if dialog.exec():
            def replace(project):
                target = next(entry for entry in project.power.experiments if entry.id == experiment.id)
                if any(entry.id == dialog.model.id and (new or entry.id != scenario.id) for entry in target.scenarios):
                    raise ValueError("Scenario identifiers must be unique.")
                if new:
                    target.scenarios.append(deepcopy(dialog.model))
                else:
                    index = next(index for index, entry in enumerate(target.scenarios) if entry.id == scenario.id)
                    target.scenarios[index] = deepcopy(dialog.model)
            self._transact("Edit operating scenario " + scenario.id, replace)
            self.refresh_study_summary()

    def remove_scenario(self):
        record = self.scenario_table.selected_record()
        if record is None:
            return
        identifier, experiment_id = record["id"], self.experiment.currentText()
        def remove(project):
            experiment = next(entry for entry in project.power.experiments if entry.id == experiment_id)
            experiment.scenarios[:] = [entry for entry in experiment.scenarios if entry.id != identifier]
        self._transact("Remove operating scenario " + identifier, remove)
        self.refresh_study_summary()

    def edit_experiment(self):
        experiment = next((entry for entry in self.project.power.experiments if entry.id == self.experiment.currentText()), None)
        if experiment is None:
            self.setup_kind.setCurrentIndex(self.setup_kind.findData("experiments"))
            self.tabs.setCurrentIndex(0)
            self.edit_records()
            return
        dialog = ModelDialog(experiment, "Experiment variables, goals and limits", self, choices=self.choices(), mask_keys=self.mask_keys)
        if dialog.exec():
            def replace(project):
                index = next(index for index, entry in enumerate(project.power.experiments) if entry.id == experiment.id)
                project.power.experiments[index] = deepcopy(dialog.model)
            self._transact("Edit routing experiment " + experiment.id, replace)
            self.refresh_setup()

    def choose_experiment_folder(self):
        directory = QFileDialog.getExistingDirectory(self, "Experiment folder", self.experiment_output.text())
        if directory:
            self.experiment_output.setText(directory)

    def run_experiment(self):
        if not self.experiment.currentText():
            self.status.setText("Create an experiment in Setup first.")
            return
        self.start("optimize", {"experiment_id": self.experiment.currentText(),
            "output": self.experiment_output.text(), "resume": self.resume.isChecked()})

    def open_history(self):
        path, _ = QFileDialog.getOpenFileName(self, "Open experiment history", self.experiment_output.text(), "JSON (*.json)")
        if path:
            try:
                self.load_history(path)
                self.experiment_output.setText(str(Path(path).parent))
                self.status.setText("Loaded recorded history. Resume verifies project and experiment compatibility before computing.")
            except (ValueError, OSError) as error:
                self.status.setText(str(error))

    def load_history(self, path):
        history = json.loads(Path(path).read_text(encoding="utf-8"))
        if (not isinstance(history, dict) or not isinstance(history.get("rows"), list)
                or not isinstance(history.get("experiment_id"), str)
                or any(not isinstance(row, dict) or not isinstance(row.get("id"), str) for row in history["rows"])):
            raise ValueError("Expected experiment history with experiment_id and candidate rows")
        self.history = history
        index = self.experiment.findText(history["experiment_id"])
        if index >= 0:
            self.experiment.setCurrentIndex(index)
        self.show_history()

    def show_history(self, *_):
        if self.history is None:
            return
        self.history_identity.setText(f"Recorded experiment: {self.history.get('experiment_id', 'unknown')} · "
            f"{self.history.get('status', 'unknown')} · {len(self.history.get('verified', []))} verified finalists · "
            f"{self.history.get('termination_reason', '')}")
        records = self.history.get("rows", [])
        if isinstance(records, dict):
            records = [{"id": key, **record} for key, record in records.items()]
        pareto = set(self.history.get("pareto", self.history.get("pareto_ids", [])))
        if self.pareto_only.isChecked():
            records = [record for record in records if record.get("id") in pareto or record.get("pareto") is True]
        self.candidates.set_records(records)
        self.pareto_plot.set_history(self.history)
        self.candidate_details.setPlainText(_json_dump({key: value for key, value in self.history.items()
                                                       if key not in {"rows"}}))

    @staticmethod
    def fit_geometry(view, geometry):
        x0, y0, x1, y1 = geometry.domain.bounds
        rect = QRectF(x0 * 1e6, -y1 * 1e6, (x1-x0) * 1e6, (y1-y0) * 1e6)
        margin = max(rect.width(), rect.height()) * .08
        view.fitInView(rect.adjusted(-margin, -margin, margin, margin), Qt.AspectRatioMode.KeepAspectRatio)

    def show_candidate(self):
        record = self.candidates.selected_record()
        if record:
            self.candidate_details.setPlainText(_json_dump(record))

    def select_plot_candidate(self, candidate_id):
        if not any(row["id"] == candidate_id for row in self.candidates.records):
            self.pareto_only.setChecked(False)
        for index, row in enumerate(self.candidates.records):
            if row["id"] == candidate_id:
                self.candidates.selectRow(index)
                self.show_candidate()
                break

    def export_finalist(self):
        record = self.candidates.selected_record()
        if not record:
            self.status.setText("Select a candidate with completed mesh verification.")
            return
        directory = QFileDialog.getExistingDirectory(self, "Export verified finalist")
        if directory:
            self.start("export_finalist", {"history": self.history, "candidate_id": record["id"],
                "output": self.experiment_output.text(), "export_directory": directory})

    def start(self, operation, options):
        if self.job:
            return
        window = getattr(self.document, "window", None)
        if window is not None and window.model_locked():
            self.status.setText("Finish the active computation and return to the working setup before starting a study.")
            return
        try:
            if self.document is not None:
                if not self.resolve_pending():
                    return
            else:
                self.sync_reference()
            project = project_from_dict(self.project.to_dict())
            self._job_project = project
            self._job_configuration = configuration_hash(project)
            self.operation = operation
            job = ComputeJob(project, "power:" + operation, options, self, execution=self.execution)
            self.job = job
            job.progress.connect(self.on_progress)
            job.finished.connect(self.completed)
            job.failed.connect(self.failed)
            job.cancelled.connect(self.cancelled)
            self.set_running(True)
            self.status.setText("Starting " + operation.replace("_", " ") + " in an independent process…")
            job.start()
        except (ValueError, OSError, SimulationError) as error:
            if self.job:
                self.job.cleanup()
            self.job = None
            self.set_running(False)
            self.status.setText(str(error))

    def set_running(self, running):
        self.tabs.widget(0).setEnabled(not running)
        self.multiport_workflow.setEnabled(not running)
        self.buttons.setEnabled(not running)
        for button in self.actions:
            button.setEnabled(not running)
        self.network_export.setEnabled(not running and self.multiport_report is not None)
        self.cancel.setEnabled(running)
        self.progress.setVisible(running)
        if running:
            self.progress.setValue(0)
        if running != self._running:
            self._running = running
            self.busy_changed.emit(running)

    def on_progress(self, fraction, message):
        self.progress.setValue(max(0, min(100, int(100 * fraction))))
        self.status.setText(message)

    def completed(self, output):
        self.job = None
        self.set_running(False)
        report = output["report"]
        if self.operation in {"power", "reanalyze"}:
            self.result = output["result"]
            self.show_analysis(report)
            geometry = output.get("geometry")
            if geometry:
                self.analysis_map.set_geometries(geometry.layers, self._job_project.layers, geometry.terminals)
                self.fit_geometry(self.analysis_map, geometry)
            self.tabs.setCurrentIndex(1)
            if self.operation == "power":
                self.result_ready.emit(self.result)
        elif self.operation == "multiport":
            self.multiport_report = report
            self.multiport_workflow.show_report(report)
            self.network_export.setEnabled(True)
            ports = report["port_ids"]
            self.matrix.columns = [("port", "Port"), *[(key, key + " (S)") for key in ports]]
            self.matrix.setColumnCount(len(ports) + 1)
            self.matrix.setHorizontalHeaderLabels([title for _, title in self.matrix.columns])
            self.matrix.set_records([{"port": key, **dict(zip(ports, row))}
                for key, row in zip(ports, report["conductance_matrix"])])
            checks = report["validation"]
            self.analysis_notes.setText(f"Passive fixed-temperature DC · reference: {report['reference']} · "
                f"KCL error {checks['kcl_absolute_error_S']:.3g} S · reciprocity error "
                f"{checks['reciprocity_absolute_error_S']:.3g} S · {report['temperature']}")
            self.analysis_views.setCurrentWidget(self.matrix)
            self.tabs.setCurrentIndex(4)
        elif self.operation.startswith("routing_"):
            rows = list(report.get("violations", []))
            for layer, change in report.get("geometry_changes", {}).items():
                for kind in ("added", "removed"):
                    if change[kind + "_area_m2"]:
                        rows.append({"rule_id": kind, "severity": "info", "object_id": layer,
                            "message": f"{change[kind + '_area_m2']:.8g} m² {kind}",
                            "geometry_wkt": change[kind + "_wkt"]})
            self.routing_table.set_records(rows)
            if output.get("geometry"):
                geometry = output["geometry"]
                self.routing_map.set_geometries(geometry.layers, output["project"].layers, geometry.terminals)
                self.fit_geometry(self.routing_map, geometry)
            if self.operation != "routing_validate":
                self.candidate = output
                self._candidate_source = self._job_configuration
            self.tabs.setCurrentIndex(2)
        elif self.operation == "optimize":
            self.history = report
            self.show_history()
            self.tabs.setCurrentIndex(3)
        self.status.setText("Completed " + self.operation.replace("_", " ") + ("; candidate is invalid—inspect diagnostics." if report.get("valid") is False else "."))
        if self.document is not None and self._job_configuration != configuration_hash(self.project):
            self.status.setText(self.status.text() + " This output belongs to an earlier setup; the working setup has changed.")

    def failed(self, message, detail):
        self.job = None
        self.set_running(False)
        self.status.setText(message)
        self.status.setToolTip(detail)

    def cancel_job(self):
        if self.job:
            self.job.cancel()

    def cancelled(self):
        self.job = None
        self.set_running(False)
        self.status.setText("Cancelled. Completed experiment checkpoints remain in the output folder; resume to continue.")

    def accept(self):
        if self.job:
            return
        try:
            self.sync_reference()
            self.project = project_from_dict(self.project.to_dict())
        except (ValueError, SimulationError) as error:
            self.status.setText(str(error))
            return
        if self.document is not None:
            return
        super().accept()

    def reject(self):
        if self.job:
            self.cancel_job()
        super().reject()

    def closeEvent(self, event):
        if self.job:
            self.cancel_job()
        self.pareto_plot.close()
        super().closeEvent(event)
