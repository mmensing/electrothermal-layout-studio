# SPDX-License-Identifier: MPL-2.0
"""Typed configuration dialogs with explicit units and region editing.

Every user-facing physical model field is editable without Python or JSON.
Values are converted to SI as they leave the form. Nested models use separate
dialogs so less common controls do not crowd the primary engineering workflow.
"""
from __future__ import annotations

import csv
import math
from copy import deepcopy
from dataclasses import fields, is_dataclass
from typing import Any, get_args, get_origin, get_type_hints

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QTableWidget,
    QTableWidgetItem,
    QTabWidget,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from etlayout.gui.inputs import parse_number
from etlayout.models import MeasurementSpec, PropertyModel, RegionSpec, SimulationError
from etlayout.power.measurement_contract import QUANTITIES, validate_limits

ENUMS = {
    ("TemperatureLimit", "evaluation"): ["fail_after", "stop_transient"],
    ("PhysicalLayer", "role"): ["conductor", "insulator", "thermal"],
    ("Material", "category"): ["metal", "film", "semiconductor", "dielectric", "2deg", "custom"],
    ("PropertyModel", "kind"): ["constant", "linear", "polynomial", "table"],
    ("ElectricalModel", "kind"): ["bulk", "sheet", "semiconductor", "hemt"],
    ("Interface", "electrical_kind"): ["none", "ideal", "specific", "lumped", "via", "conductance", "load"],
    ("Interface", "thermal_kind"): ["none", "conductance", "resistance", "stack"],
    ("Terminal", "kind"): ["voltage", "ground", "current", "density", "floating", "probe", "insulated", "gate"],
    ("ThermalBoundary", "kind"): ["temperature", "coefficient", "resistance_area", "resistance", "insulated"],
    ("HeatSource", "kind"): ["density", "total"],
    ("SolverSettings", "mode"): ["electrical", "thermal", "electrothermal"],
    ("SolverSettings", "linear_solver"): ["direct", "cg"],
    ("SolverSettings", "floating_policy"): ["error", "reference"],
    ("SweepSpec", "mode"): ["electrical", "thermal", "electrothermal"],
    ("PhysicalLayer", "loss_role"): ["auto", "routing_metal", "other_metal", "access", "channel"],
    ("Interface", "model_kind"): ["effective", "physical_via", "physical_contact"],
    ("Interface", "height_mode"): ["explicit", "stack"],
    ("Interface", "resistance_semantics"): ["array", "instance"],
    ("Interface", "purpose"): ["connection", "device_load"],
    ("MeasurementSpec", "aggregation"): ["maximum", "minimum", "mean", "rms", "integral", "percentile", "exceedance"],
    ("MeasurementSpec", "vector_component"): ["magnitude", "x", "y", "signed"],
    ("MeasurementSpec", "weighting"): ["area"],
    ("PhysicalPort", "kind"): ["voltage", "ground", "current", "floating"],
    ("RoutingRecipe", "kind"): ["bus", "finger_feed", "strap", "via_array"],
    ("DesignVariable", "kind"): ["continuous", "integer", "choice"],
    ("OperatingScenario", "service_mode"): ["target_current", "fixed_voltage"],
    ("ObjectiveSpec", "direction"): ["min", "max"],
    ("ObjectiveSpec", "scenario_aggregation"): ["worst", "weighted_mean"],
}

UNITS = {
    "thickness": "µm", "z": "µm", "height": "µm", "mesh_size": "µm",
    "max_size": "µm", "min_size": "µm", "contact_size": "µm", "size": "µm", "grading_length": "µm",
    "density": "kg/m³", "specific_heat": "J/(kg K)", "thermal_conductivity_z": "W/(m K)",
    "reference_temperature": "K", "minimum_temperature": "K", "maximum_temperature": "K",
    "initial_temperature": "K", "ambient": "K", "alpha": "1/K", "carrier_density": "1/m³",
    "mobility": "m²/(V s)", "gate_voltage": "V", "threshold_voltage": "V",
    "threshold_temperature_coefficient": "V/K", "gate_coupling": "1/(m² V)",
    "smoothing_voltage": "V", "maximum_sheet_density": "1/m²", "background_sheet_density": "1/m²",
    "minimum_sheet_conductance": "S", "thermal_resistance_area": "m² K/W",
    "averaging_length": "µm", "effective_thickness": "µm", "active_width": "µm", "active_area": "m²",
    "enclosure": "µm", "resistance": "Ω", "thermal_fraction": "fraction", "dbu": "µm",
    "heat_to_layer_a_fraction": "fraction",
    "via_size": "µm", "via_pitch": "µm", "via_spacing": "µm", "via_enclosure": "µm", "minimum_notch": "µm",
    "max_exceedance_fraction": "fraction",
    "minimum_bend_radius": "µm", "required_current": "A", "compliance_voltage": "V",
    "service_tolerance": "fraction", "maximum_mesh_change": "fraction", "temperature_coefficient": "1/K",
}

HELP = {
    "grading_length": "Optional smooth sizing transition outside this exact numerical refinement support, in meters. Zero preserves legacy sizing; physical geometry is unchanged.",
    "axis_sizes": "Optional [x, y] spacings in meters on rectangular partition faces. Scalar refinements still apply. Empty uses ordinary meshing; geometry and solver equations are preserved.",
    "threshold": "Exceedance sample threshold in the quantity's SI units; independent of the permitted fraction.",
    "threshold_units": "Canonical sample units (V, K, A/m², etc.). Exceedance result units are fraction.",
    "max_exceedance_fraction": "Permitted fraction in [0, 1]. Empty reports the fraction without an acceptance constraint.",
    "geometry": "Combine mask layers by union, intersection or difference; optionally add explicit polygons. All coordinates display in µm.",
    "role": "Conductor participates electrically and thermally. Insulator and thermal roles carry heat only.",
    "z": "Bottom elevation of this physical sheet, in µm. Thickness extends toward +z.",
    "kind": "Select the constitutive model or boundary condition. Units for values are given below.",
    "electrical_kind": "specific: Ω m²; lumped: Ω; conductance: S/m²; via: material resistivity × height; ideal: exact shared electrical potential.",
    "thermal_kind": "conductance: W/(m² K); resistance: m² K/W; stack: half-thickness material path plus declared interface resistance.",
    "heat_to_layer_a_fraction": "Fraction of interface/device-load loss deposited in layer A; the remainder goes to B. Legacy default is 0.5. Calibrate the partition; no internal 3D contact profile is resolved.",
    "heat_layer_id": "Optional physical-layer ID receiving all interface/device-load heat at the same x-y support. Empty uses the endpoint split. The layer must cover the complete contact. Keep the split at 0.5 when selecting a named layer.",
    "floating_policy": "error requires references for every active electrical net. reference explicitly permits a gauge for isolated passive nets.",
    "gate_regions": "Comma-separated gate-terminal IDs. Empty means the entire sheet uses the model gate voltage.",
    "coefficients": "Ascending powers of (T − reference temperature). Enter comma-separated SI coefficients, including the constant term.",
    "temperatures": "Ordered temperatures (K), comma-separated. Use Import table for CSV data.",
    "values": "Comma-separated values in the units of this property, or sweep values in SI.",
    "parameter": "Dotted parameter path. Examples: terminals.drain.value; layers.metal.thickness; layers.channel.electrical.gate_voltage; mesh.max_size.",
    "electrical_anisotropy": "Positive x,y multipliers for electrical conductivity.",
    "thermal_anisotropy": "Positive x,y multipliers for in-plane thermal conductivity.",
    "minimum_temperature": "Lower validity boundary. The solver reports excursions outside this range.",
    "maximum_temperature": "Upper validity boundary. Extrapolation can be physically invalid.",
    "max_elements": "Hard element budget to prevent accidental excessive memory usage.",
    "max_dofs": "Hard total degree-of-freedom budget across active layers.",
    "relaxation": "Nonlinear under-relaxation fraction between 0 and 1.",
    "resistance_terminals": "Two terminal IDs for the voltage difference, separated by a comma. Empty selects an unambiguous two-terminal conducting network.",
    "resistance_current_terminal": "Optional terminal ID defining the current for a Kelvin or multiport resistance measurement.",
    "maximum_sheet_density": "Maximum 2DEG sheet density in 1/m². Zero or a negative value disables the upper cap.",
    "selector": "Union within each selected category; intersection between categories. Empty overall support is unavailable, never a favorable zero.",
    "averaging_length": "Fixed physical square side. The window is clipped to selected support and normalized by supported area. Zero reports the raw mesh-sensitive maximum.",
    "effective_thickness": "Explicit sheet-current reporting conversion only. Use metal current density for metal reliability constraints.",
    "landings": "Each landing references a physical terminal footprint. Zero resistance makes only that footprint ideal. Finite feed loss may be deposited by thermal_fraction.",
    "reference_boundary": "Named thermal reservoir for temperature rise. Several reservoir temperatures are never averaged by mesh-node count.",
    "parameters": "Recipe-specific SI values. Bus: width/start/end/center/axis. Finger feed: count/pitch/finger_width/bus_width/end. Via array: rows/columns/size/pitch/pattern.",
    "scenarios": "Every candidate must deliver equivalent required service across all scenarios before Pareto ranking.",
    "refinement_factors": "Decreasing positive mesh-size factors including 1. Baseline and finalists are recalculated at the same refinement levels.",
}

LABELS = {"id": "Identifier", "material_id": "Material", "layer_id": "Physical layer",
          "z": "Bottom elevation", "kind": "Condition / model", "max_size": "Maximum element size",
          "min_size": "Minimum element size", "contact_size": "Contact element size",
          "parameter": "Parameter", "values": "Values", "resistance_terminals": "Resistance voltage terminals",
          "resistance_current_terminal": "Resistance current terminal"}
ADVANCED = {
    "PhysicalLayer": {"color", "opacity", "mesh_size"},
    "Material": {"electrical_anisotropy", "thermal_anisotropy", "thermal_conductivity_z"},
    "SolverSettings": {"linear_solver", "linear_tolerance", "current_balance_tolerance",
                       "power_balance_tolerance", "thermal_balance_tolerance", "absolute_tolerance"},
    "ElectricalModel": {"mobility_exponent", "reference_temperature", "threshold_temperature_coefficient",
                        "maximum_sheet_density", "background_sheet_density", "minimum_sheet_conductance"},
    "MeasurementSpec": {"units", "weighting", "vector_component", "percentile", "effective_thickness"},
    "RoutingRecipe": {"version", "source_hash", "instance_path", "all_instances", "seed"},
    "OptimizationExperiment": {"seed", "improvement_margin", "maximum_mesh_change", "max_wall_seconds"},
}


def field_type(annotation):
    args = get_args(annotation)
    optional = type(None) in args
    return (next(t for t in args if t is not type(None)) if optional else annotation), optional


def region_summary(region: RegionSpec) -> str:
    if region.full_domain:
        return "Entire simulation domain"
    pieces = []
    if region.sources:
        masks = f" {region.operation} ".join(region.sources)
        if region.exclude_sources:
            masks = "(" + masks + ") minus (" + " union ".join(region.exclude_sources) + ")"
        pieces.append(masks)
    if region.polygons:
        pieces.append(f"{len(region.polygons)} explicit polygon(s)")
    if region.line:
        pieces.append("Drawn boundary line")
    return "; ".join(pieces) or "Empty — choose masks or draw geometry"


class RegionDialog(QDialog):
    def __init__(self, region: RegionSpec, mask_keys=(), parent=None):
        super().__init__(parent)
        self.setWindowTitle("Region geometry · coordinates in µm")
        self.resize(670, 620)
        self.region = deepcopy(region)
        layout = QVBoxLayout(self)
        self.full = QCheckBox("Cover the entire simulation domain")
        self.full.setChecked(region.full_domain)
        layout.addWidget(self.full)
        row = QHBoxLayout()
        self.mask_tabs = QTabWidget()
        self.sources = QListWidget()
        self.sources.setAccessibleName("Combined source masks")
        for key in [*region.sources, *sorted(set(mask_keys) - set(region.sources))]:
            item = QListWidgetItem(key)
            item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            item.setCheckState(Qt.CheckState.Checked if key in region.sources else Qt.CheckState.Unchecked)
            self.sources.addItem(item)
        self.mask_tabs.addTab(self.sources, "Source masks")
        self.exclusions = QListWidget()
        self.exclusions.setAccessibleName("Excluded masks")
        for key in [*region.exclude_sources, *sorted(set(mask_keys) - set(region.exclude_sources))]:
            item = QListWidgetItem(key)
            item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            item.setCheckState(Qt.CheckState.Checked if key in region.exclude_sources else Qt.CheckState.Unchecked)
            self.exclusions.addItem(item)
        self.mask_tabs.addTab(self.exclusions, "Excluded masks")
        self.mask_tabs.setToolTip("Excluded masks are subtracted from the combined source masks, before explicit polygons are added.")
        row.addWidget(self.mask_tabs)
        self.operation = QComboBox()
        self.operation.addItems(["union", "intersection", "difference", "xor"])
        self.operation.setCurrentText(region.operation)
        row.addWidget(self.operation)
        layout.addWidget(QLabel("Source masks (difference follows the listed order)"))
        layout.addLayout(row)
        order = QHBoxLayout()
        for label, direction in [("Move mask up", -1), ("Move mask down", 1)]:
            button = QPushButton(label)
            button.clicked.connect(lambda checked=False, step=direction: self.move_source(step))
            self.mask_tabs.currentChanged.connect(lambda index, control=button: control.setEnabled(index == 0))
            order.addWidget(button)
        layout.addLayout(order)
        layout.addWidget(QLabel("Explicit polygons / line · each row is an x, y coordinate pair (µm)"))
        self.shape = QComboBox()
        self.shape.addItem("Boundary line")
        for i in range(len(region.polygons)):
            self.shape.addItem(f"Polygon {i + 1}")
        self.points = QTableWidget(0, 2)
        self.points.setHorizontalHeaderLabels(["x (µm)", "y (µm)"])
        self.points.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self.current_shape = 0
        self.shape.currentIndexChanged.connect(self.select_shape)
        layout.addWidget(self.shape)
        layout.addWidget(self.points)
        controls = QHBoxLayout()
        for label, callback in [("Add point", self.add_point), ("Remove point", self.remove_point),
                                ("Add polygon", self.add_polygon), ("Delete shape", self.delete_shape),
                                ("Rectangle…", self.rectangle)]:
            button = QPushButton(label)
            button.clicked.connect(callback)
            controls.addWidget(button)
        layout.addLayout(controls)
        note = QLabel("Holes in existing polygons are retained. Use the viewport tools to draw regions graphically.")
        note.setWordWrap(True)
        layout.addWidget(note)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
        self.load_shape(0)

    def get_points(self):
        return [[parse_number(self.points.item(r, c).text(), "µm") for c in range(2)]
                for r in range(self.points.rowCount())]

    def move_source(self, step):
        current = self.sources.currentRow()
        destination = current + step
        if current >= 0 and 0 <= destination < self.sources.count():
            item = self.sources.takeItem(current)
            self.sources.insertItem(destination, item)
            self.sources.setCurrentRow(destination)

    def save_shape(self):
        points = self.get_points()
        if self.current_shape == 0:
            self.region.line = points
        else:
            self.region.polygons[self.current_shape - 1]["exterior"] = points

    def load_shape(self, index):
        points = self.region.line if index == 0 else self.region.polygons[index - 1].get("exterior", [])
        self.points.setRowCount(len(points))
        for row, point in enumerate(points):
            for col, value in enumerate(point):
                self.points.setItem(row, col, QTableWidgetItem(f"{value * 1e6:.10g}"))

    def select_shape(self, index):
        try:
            self.save_shape()
            self.current_shape = index
            self.load_shape(index)
        except ValueError:
            QMessageBox.warning(self, "Invalid coordinate", "Coordinates must be numbers in µm.")

    def add_point(self):
        row = self.points.rowCount()
        self.points.insertRow(row)
        for col in range(2):
            self.points.setItem(row, col, QTableWidgetItem("0"))

    def remove_point(self):
        self.points.removeRow(self.points.currentRow())

    def add_polygon(self):
        try:
            self.save_shape()
        except ValueError:
            return
        self.region.polygons.append({"exterior": [], "holes": []})
        self.shape.addItem(f"Polygon {len(self.region.polygons)}")
        self.shape.setCurrentIndex(self.shape.count() - 1)

    def delete_shape(self):
        if self.current_shape == 0:
            self.region.line = []
            self.load_shape(0)
        else:
            del self.region.polygons[self.current_shape - 1]
            self.shape.blockSignals(True)
            self.shape.removeItem(self.current_shape)
            self.shape.setCurrentIndex(0)
            self.shape.blockSignals(False)
            self.current_shape = 0
            self.load_shape(0)

    def rectangle(self):
        dialog = BoundsDialog(self)
        if dialog.exec():
            self.add_polygon()
            self.region.polygons[-1] = dialog.region().polygons[0]
            self.load_shape(len(self.region.polygons))

    def accept(self):
        try:
            self.save_shape()
            self.region.polygons = [p for p in self.region.polygons if p.get("exterior")]
            if any(len(p["exterior"]) < 3 for p in self.region.polygons):
                raise ValueError("Polygons need at least three points.")
            if self.region.line and len(self.region.line) < 2:
                raise ValueError("A boundary line needs at least two points.")
            self.region.full_domain = self.full.isChecked()
            self.region.operation = self.operation.currentText()
            self.region.sources = [self.sources.item(i).text() for i in range(self.sources.count())
                                   if self.sources.item(i).checkState() == Qt.CheckState.Checked]
            self.region.exclude_sources = [self.exclusions.item(i).text() for i in range(self.exclusions.count())
                                           if self.exclusions.item(i).checkState() == Qt.CheckState.Checked]
        except (ValueError, AttributeError) as error:
            QMessageBox.warning(self, "Invalid geometry", str(error))
            return
        super().accept()


class BoundsDialog(QDialog):
    def __init__(self, parent=None, bounds=(0., 0., 100e-6, 100e-6)):
        super().__init__(parent)
        self.setWindowTitle("Rectangle bounds")
        layout = QFormLayout(self)
        self.entries = []
        for label, value in zip(["Minimum x (µm)", "Minimum y (µm)", "Maximum x (µm)", "Maximum y (µm)"], bounds):
            entry = QLineEdit(f"{value * 1e6:.10g}")
            self.entries.append(entry)
            layout.addRow(label, entry)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addRow(buttons)

    def region(self):
        x0, y0, x1, y1 = [parse_number(w.text(), "µm") for w in self.entries]
        if x0 >= x1 or y0 >= y1:
            raise ValueError("Minimum coordinates must be below maximum coordinates.")
        return RegionSpec(polygons=[{"exterior": [[x0, y0], [x1, y0], [x1, y1], [x0, y1]], "holes": []}])

    def accept(self):
        try:
            self.region()
        except ValueError as error:
            QMessageBox.warning(self, "Invalid bounds", str(error))
            return
        super().accept()


class ModelDialog(QDialog):
    def __init__(self, model, title=None, parent=None, choices=None, mask_keys=(), property_unit="", readonly=(),
                 parameter_units=None):
        super().__init__(parent)
        self.model = deepcopy(model)
        self.choices = choices or {}
        self.mask_keys = mask_keys
        self.widgets = {}
        self.labels = {}
        self.errors = {}
        self.types = get_type_hints(type(model))
        self.parameter_units = parameter_units or {}
        self.property_unit = property_unit
        self.setWindowTitle(title or type(model).__name__)
        self.resize(660, 320)
        outer = QVBoxLayout(self)
        intro = QLabel(self.description(type(model).__name__, property_unit))
        intro.setWordWrap(True)
        outer.addWidget(intro)
        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        body = QWidget()
        layout = QFormLayout(body)
        layout.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.AllNonFixedFieldsGrow)
        self.scroll.setWidget(body)
        outer.addWidget(self.scroll, 1)
        advanced = ADVANCED.get(type(model).__name__, set())
        advanced_body = QWidget()
        advanced_layout = QFormLayout(advanced_body)
        advanced_layout.setContentsMargins(0, 0, 0, 0)
        advanced_layout.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.AllNonFixedFieldsGrow)
        self.advanced = QGroupBox("Advanced options")
        self.advanced.setCheckable(True)
        self.advanced.setChecked(False)
        QVBoxLayout(self.advanced).addWidget(advanced_body)
        advanced_body.setVisible(False)
        self.advanced.toggled.connect(advanced_body.setVisible)
        basic_count = 0
        for descriptor in fields(model):
            name = descriptor.name
            value = getattr(self.model, name)
            if name == "reference":
                continue
            title = LABELS.get(name, name.replace("_", " ").capitalize())
            unit = UNITS.get(name, "")
            if name == "value" and property_unit:
                unit = property_unit
            if unit:
                title += f" ({unit})"
            enum = self.choices.get(name) or ENUMS.get((type(model).__name__, name))
            annotation, optional = field_type(self.types.get(name, type(value)))
            if isinstance(value, RegionSpec):
                widget = QPushButton(region_summary(value))
                widget.clicked.connect(lambda checked=False, key=name: self.edit_nested(key))
            elif is_dataclass(value) or (value is None and is_dataclass(annotation)):
                widget = QPushButton("Edit " + title.lower() + "…")
                widget.clicked.connect(lambda checked=False, key=name: self.edit_nested(key))
            elif enum:
                widget = QComboBox()
                for option in enum:
                    widget.addItem(str(enum[option]) if isinstance(enum, dict) else str(option), option)
                if optional and value is None:
                    widget.addItem("Automatic / none", None)
                elif widget.findData(value) < 0:
                    widget.addItem(str(value), value)
                widget.setEditable(name in {"id", "material_id", "layer_id", "layer_a", "layer_b", "heat_layer_id"})
                widget.setCurrentIndex(widget.findData(value))
            elif isinstance(value, bool):
                widget = QCheckBox()
                widget.setChecked(value)
            elif name in {"notes", "source"}:
                widget = QTextEdit(str(value))
                widget.setMaximumHeight(95)
            elif get_origin(annotation) is list and is_dataclass(get_args(annotation)[0]):
                widget = QPushButton(f"{len(value)} entries · edit…")
                widget.clicked.connect(lambda checked=False, key=name: self.edit_collection(key))
            elif get_origin(annotation) is dict or (get_origin(annotation) is list
                    and get_args(annotation)[0] not in {str, float, int}
                    and type(model).__name__ != "SweepSpec"):
                widget = QPushButton("Edit structured values (SI)…")
                widget.clicked.connect(lambda checked=False, key=name: self.edit_structured(key))
            elif isinstance(value, list):
                scale = 1e6 if (type(model).__name__ == "SweepSpec" and name == "values"
                               and self.parameter_units.get(model.parameter) == "µm") else 1.
                widget = QLineEdit(", ".join(str(v * scale) if isinstance(v, (float, int)) else str(v) for v in value))
            elif annotation in {float, int}:
                scale = 1e6 if unit == "µm" else 1.
                widget = QLineEdit("" if value is None else f"{value * scale:.12g}")
                widget.setPlaceholderText("Automatic (optional)" if optional else f"Required · {unit or 'number'}")
            else:
                widget = QLineEdit("" if value is None else str(value))
            widget.setToolTip(HELP.get(name, f"{title}. SI units unless another unit is shown."))
            if name in readonly:
                widget.setEnabled(False)
            self.widgets[name] = widget
            widget.setObjectName("field_" + name)
            widget.setAccessibleName(title)
            widget.setAccessibleDescription(widget.toolTip())
            label = QLabel(title)
            label.setBuddy(widget)
            self.labels[name] = label
            target_layout = advanced_layout if name in advanced else layout
            field_container = QWidget()
            field_layout = QVBoxLayout(field_container)
            field_layout.setContentsMargins(0, 0, 0, 0)
            field_layout.setSpacing(2)
            field_layout.addWidget(widget)
            error_label = QLabel()
            error_label.setProperty("error", True)
            error_label.setWordWrap(True)
            error_label.hide()
            field_layout.addWidget(error_label)
            self.errors[name] = error_label
            target_layout.addRow(label, field_container)
            if name not in advanced:
                basic_count += 1
        if advanced:
            layout.addRow(self.advanced)
        if isinstance(model, PropertyModel):
            button = QPushButton("Import temperature table from CSV…")
            button.clicked.connect(self.import_table)
            layout.addRow(button)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        outer.addWidget(buttons)
        self.resize(660, min(680, max(300, 160 + basic_count * 47)))
        for name in ("kind", "electrical_kind", "thermal_kind", "parameter", "quantity", "aggregation"):
            if isinstance(self.widgets.get(name), QComboBox):
                self.widgets[name].currentIndexChanged.connect(self.update_units)
        self.update_units()

    def choice(self, name):
        widget = self.widgets[name]
        index = widget.currentIndex()
        if widget.isEditable() and widget.currentText() != widget.itemText(index):
            return widget.currentText()
        return widget.currentData()

    def unit_for(self, name):
        model = type(self.model).__name__
        if model == "MeasurementSpec" and name == "threshold":
            widget = self.widgets.get("quantity")
            quantity = self.choice("quantity") if isinstance(widget, QComboBox) else widget.text() if widget else self.model.quantity
            return QUANTITIES.get(quantity, (None, ""))[1]
        kind = self.choice("kind") if "kind" in self.widgets else ""
        if name == "values" and model == "SweepSpec":
            return self.parameter_units.get(self.choice("parameter"), "")
        if name == "value":
            mapping = {"Terminal": {"voltage": "V", "gate": "V", "current": "A", "density": "A/m²"},
                       "PhysicalPort": {"voltage": "V", "ground": "V", "current": "A"},
                       "HeatSource": {"density": "W/m²", "total": "W"},
                       "ThermalBoundary": {"temperature": "K", "coefficient": "W/(m² K)",
                                           "resistance_area": "m² K/W", "resistance": "K/W"}}
            return mapping.get(model, {}).get(kind, self.property_unit)
        if name == "electrical_value":
            return {"specific": "Ω m²", "lumped": "Ω", "conductance": "S/m²", "load": "Ω m²"}.get(self.choice("electrical_kind"), "")
        if name == "thermal_value":
            return {"conductance": "W/(m² K)", "resistance": "m² K/W"}.get(self.choice("thermal_kind"), "")
        return UNITS.get(name, "")

    def update_units(self, *_):
        for name, label in self.labels.items():
            unit = self.unit_for(name)
            title = LABELS.get(name, name.replace("_", " ").capitalize())
            label.setText(title + (f" ({unit})" if unit else ""))
            self.widgets[name].setAccessibleName(label.text())
        if type(self.model).__name__ in {"Terminal", "PhysicalPort"}:
            self.widgets["value"].setEnabled(self.choice("kind") not in {"ground", "floating", "probe", "insulated"})

    @staticmethod
    def description(model, unit):
        return {
            "Terminal": "Voltage / gate: V · current: total A (equipotential). Probe observes without changing the solution; floating is an equipotential electrode that can redistribute current. Density uses A/m²: area supports integrate over their area; edge supports integrate over edge length × layer thickness. Geometry must overlap the assigned physical layer.",
            "PhysicalPort": "Voltage: V · current: total A entering all of this port's landings. Each landing keeps its own finite feed resistance and local heat fraction. Ground is a 0 V reference; floating ports impose zero net current.",
            "Interface": "Electrical values: specific Ω m² · lumped Ω · conductance S/m². Thermal values: conductance W/(m² K) · resistance m² K/W. Declare the actual overlap / via footprint.",
            "ThermalBoundary": "Temperature: K · coefficient: W/(m² K) · resistance area: m² K/W · resistance: total K/W. Ambient is the sink temperature.",
            "HeatSource": "Density is W/m²; total is W integrated over the selected area. Electrical Joule losses are added automatically in coupled mode.",
            "PropertyModel": f"Property values in {unit or 'SI units'}. Linear law: value × [1 + alpha × (T − Tref)]. Polynomial coefficients use ascending powers of T − Tref. Table is linearly interpolated.",
            "ElectricalModel": "Bulk uses material resistivity and layer thickness. Sheet uses Ω/square. Semiconductor uses carrier density and mobility. HEMT uses local gate-to-channel voltage with smooth charge control.",
            "Material": "Reference values are representative and require process calibration. This editor creates a project snapshot; custom materials may also be saved to the persistent user database.",
            "PhysicalLayer": "Assign masks or explicit geometry, material, physical thickness and elevation. Independent sheets only connect electrically through declared interfaces.",
            "SolverSettings": "Convergence tolerances and explicit floating-net policy. Coupled mode updates conductivity and Joule heating self-consistently.",
            "SweepSpec": "Choose a parameter and enter values separated by commas. Use the displayed unit or explicit engineering units (for example 20 mV). Each point retains its applied setting and result.",
        }.get(model, "Enter values in the displayed units. Scientific notation and engineering prefixes are accepted (for example 2.5, 1e-6 or 200 nm in a length field).")

    def edit_nested(self, key):
        value = getattr(self.model, key)
        if value is None:
            annotation, _ = field_type(self.types[key])
            value = annotation()
        if isinstance(value, RegionSpec):
            dialog = RegionDialog(value, self.mask_keys, self)
        else:
            unit = {"resistivity": "Ω m", "thermal_conductivity": "W/(m K)", "sheet_resistance": "Ω/square"}.get(key, "")
            dialog = ModelDialog(value, key.replace("_", " ").capitalize(), self,
                                 self.choices, self.mask_keys, property_unit=unit)
        if dialog.exec():
            setattr(self.model, key, dialog.region if isinstance(value, RegionSpec) else dialog.model)
            if isinstance(value, RegionSpec):
                self.widgets[key].setText(region_summary(dialog.region))
            self.update_units()

    def edit_collection(self, key):
        from etlayout.gui.power_forms import ModelCollectionDialog
        annotation, _ = field_type(self.types[key])
        dialog = ModelCollectionDialog(get_args(annotation)[0], getattr(self.model, key),
            self, choices=self.choices, mask_keys=self.mask_keys)
        if dialog.exec():
            setattr(self.model, key, dialog.entries)
            self.widgets[key].setText(f"{len(dialog.entries)} entries · edit…")

    def edit_structured(self, key):
        from etlayout.gui.power_forms import StructuredValueDialog
        dialog = StructuredValueDialog(getattr(self.model, key), self.types[key], key, self)
        if dialog.exec():
            setattr(self.model, key, dialog.value)

    def import_table(self):
        path, _ = QFileDialog.getOpenFileName(self, "Temperature, value CSV", "", "CSV (*.csv)")
        if not path:
            return
        try:
            temperatures, values = [], []
            with open(path, newline="", encoding="utf-8-sig") as stream:
                for row in csv.reader(stream):
                    if not row or row[0].lstrip().startswith("#"):
                        continue
                    try:
                        temperature, value = float(row[0]), float(row[1])
                    except ValueError:
                        if not temperatures:
                            continue
                        raise
                    temperatures.append(temperature)
                    values.append(value)
            if (len(temperatures) < 2 or any(a >= b for a, b in zip(temperatures, temperatures[1:]))
                    or not all(math.isfinite(v) for v in temperatures + values)):
                raise ValueError("At least two strictly increasing temperatures are required.")
            self.widgets["temperatures"].setText(", ".join(map(str, temperatures)))
            self.widgets["values"].setText(", ".join(map(str, values)))
            self.widgets["kind"].setCurrentText("table")
            self.widgets["minimum_temperature"].setText(str(temperatures[0]))
            self.widgets["maximum_temperature"].setText(str(temperatures[-1]))
        except Exception as error:
            QMessageBox.warning(self, "Invalid table", str(error))

    def parse_candidate(self):
        """Validate the complete draft without committing or emitting accepted."""
        candidate = deepcopy(self.model)
        errors = {}
        for name, widget in self.widgets.items():
            self.errors[name].hide()
            widget.setProperty("invalid", False)
            try:
                if isinstance(widget, QPushButton) or not widget.isEnabled():
                    continue
                if isinstance(widget, QCheckBox):
                    value = widget.isChecked()
                elif isinstance(widget, QComboBox):
                    value = self.choice(name)
                elif isinstance(widget, QTextEdit):
                    value = widget.toPlainText()
                else:
                    raw = widget.text().strip()
                    annotation, optional = field_type(self.types.get(name, str))
                    if optional and not raw:
                        value = None
                    elif get_origin(annotation) is list:
                        chunks = [v.strip() for v in raw.split(",") if v.strip()]
                        element_type = get_args(annotation)[0]
                        if element_type is str:
                            value = chunks
                        elif element_type in {float, int, Any}:
                            value = [parse_number(v, self.unit_for(name), integer=element_type is int) for v in chunks]
                        else:
                            raise ValueError("Edit these entries in their dedicated panel.")
                    elif annotation in {int, float}:
                        if not raw:
                            raise ValueError("This value is required.")
                        value = parse_number(raw, self.unit_for(name), integer=annotation is int)
                    else:
                        value = raw
                setattr(candidate, name, value)
            except (ValueError, TypeError) as error:
                errors[name] = str(error)
        if not errors:
            errors.update(self.validate_candidate(candidate))
        for name, widget in self.widgets.items():
            widget.setProperty("invalid", name in errors)
            widget.style().unpolish(widget)
            widget.style().polish(widget)
        if errors:
            for name, message in errors.items():
                self.errors[name].setText(message)
                self.errors[name].show()
            first = next(iter(errors))
            if first in ADVANCED.get(type(self.model).__name__, set()):
                self.advanced.setChecked(True)
            self.scroll.ensureWidgetVisible(self.widgets[first])
            self.widgets[first].setFocus(Qt.FocusReason.OtherFocusReason)
            return None
        return candidate

    def accept(self):
        candidate = self.parse_candidate()
        if candidate is not None:
            self.model = candidate
            super().accept()

    @staticmethod
    def validate_candidate(model):
        errors = {}
        if isinstance(model, MeasurementSpec):
            try:
                validate_limits(model)
            except SimulationError as error:
                message = str(error)
                field = ("limit" if "Legacy/ambiguous" in message else
                         "max_exceedance_fraction" if "max_exceedance_fraction must" in message else
                         "threshold_units" if "requires" in message and "received" in message else
                         "units" if "result units" in message else "threshold")
                errors[field] = message
        positive = {"thickness", "height", "mesh_size", "min_size", "max_size", "contact_size", "size",
                    "max_elements", "max_dofs", "max_iterations", "initial_temperature", "ambient",
                    "reference_temperature", "minimum_temperature", "maximum_temperature", "relative_tolerance",
                    "absolute_tolerance", "linear_tolerance", "current_balance_tolerance", "power_balance_tolerance",
                    "thermal_balance_tolerance", "density", "specific_heat", "thermal_conductivity_z", "mobility",
                    "carrier_density", "smoothing_voltage", "gate_coupling",
                    "minimum_sheet_conductance"}
        for descriptor in fields(model):
            name, value = descriptor.name, getattr(model, descriptor.name)
            if name in positive and value is not None and value <= 0:
                if name == "height" and type(model).__name__ == "Interface" and model.model_kind == "physical_contact" and value == 0:
                    continue
                errors[name] = "Enter a value greater than zero."
        if hasattr(model, "id") and not model.id.strip():
            errors["id"] = "An identifier is required."
        if hasattr(model, "relaxation") and not 0 < model.relaxation <= 1:
            errors["relaxation"] = "Use a fraction greater than zero and at most 1."
        if hasattr(model, "opacity") and not 0 <= model.opacity <= 1:
            errors["opacity"] = "Use a fraction between 0 and 1."
        if hasattr(model, "min_size") and model.min_size > model.max_size:
            errors["min_size"] = "Minimum size must not exceed maximum size."
        for name in ("thermal_resistance_area", "background_sheet_density", "grading_length"):
            if hasattr(model, name) and getattr(model, name) < 0:
                errors[name] = "Enter zero or a positive value."
        if isinstance(model, PropertyModel):
            if model.minimum_temperature >= model.maximum_temperature:
                errors["maximum_temperature"] = "Maximum temperature must exceed minimum temperature."
            if model.kind == "table":
                if len(model.temperatures) != len(model.values) or len(model.values) < 2:
                    errors["values"] = "Temperature and value tables need equal lengths of at least two."
                if any(a >= b for a, b in zip(model.temperatures, model.temperatures[1:])):
                    errors["temperatures"] = "Temperatures must be strictly increasing."
            elif model.kind == "polynomial" and not model.coefficients:
                errors["coefficients"] = "Enter at least the constant coefficient."
        for name in ("electrical_anisotropy", "thermal_anisotropy"):
            if hasattr(model, name):
                values = getattr(model, name)
                if len(values) != 2 or any(v <= 0 for v in values):
                    errors[name] = "Enter two positive multipliers: x, y."
        model_name = type(model).__name__
        if model_name == "ThermalBoundary" and model.kind != "insulated" and model.value <= 0:
            errors["value"] = "Temperature, coefficient or resistance must be positive."
        if model_name == "Interface":
            if model.electrical_kind in {"specific", "lumped", "conductance"} and model.electrical_value <= 0:
                errors["electrical_value"] = "Resistance or conductance must be positive."
            if not 0 <= model.heat_to_layer_a_fraction <= 1:
                errors["heat_to_layer_a_fraction"] = "Use a fraction between 0 and 1."
            if model.heat_layer_id and model.heat_to_layer_a_fraction != .5:
                errors["heat_to_layer_a_fraction"] = "Reset to 0.5 when using a named heat-deposition layer."
            if model.thermal_kind in {"conductance", "resistance"} and model.thermal_value <= 0:
                errors["thermal_value"] = "Thermal resistance or conductance must be positive."
        if model_name == "SweepSpec" and not model.values:
            errors["values"] = "Enter at least one sweep value."
        if hasattr(model, "resistance_terminals") and len(model.resistance_terminals) not in {0, 2}:
            errors["resistance_terminals"] = "Enter two terminal IDs, or leave empty for automatic selection."
        return errors
