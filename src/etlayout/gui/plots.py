# SPDX-License-Identifier: MPL-2.0
"""Geometry cuts, multiple line profiles and real sweep/comparison tables."""
from __future__ import annotations

import json
import math

import numpy as np
import pyqtgraph as pg
from PySide6.QtCore import QRectF, Qt, Signal
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QFileDialog,
    QGraphicsRectItem,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSplitter,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from etlayout.io.reporting import csv_writer
from etlayout.materials.models import evaluate_property
from etlayout.simulation.analysis import FIELD_UNITS
from etlayout.visualization.formatting import distinct_labels, series_context_range
from etlayout.visualization.sampling import DISPLAY_FIELD_LABELS as FIELD_LABELS
from etlayout.visualization.sampling import METRIC_UNITS, cross_section_intervals, metric_label, metric_unit
from etlayout.visualization.units import to_display

pg.setConfigOptions(background="w", foreground="#171717", antialias=True)


class ReadableAxis(pg.AxisItem):
    def tickStrings(self, values, scale, spacing):
        if self.logMode:
            return self.logTickStrings(values, scale, spacing)
        largest = max((abs(value) for value in values), default=0.)
        precision = min(17, max(4, math.ceil(math.log10(largest) - math.log10(spacing)) + 1)) if largest and spacing > 0 else 4
        return list(distinct_labels((value * scale for value in values), minimum_precision=precision))


def export_plot(widget, parent=None, *, title=None):
    path, selected = QFileDialog.getSaveFileName(parent, "Export plot", "plot.svg", "SVG (*.svg);;PNG (*.png)")
    if not path:
        return
    from pyqtgraph.exporters import ImageExporter, SVGExporter
    if path.lower().endswith(".svg"):
        exporter = SVGExporter(widget.plotItem)
    else:
        exporter = ImageExporter(widget.plotItem)
        exporter.parameters()["width"] = 1800
    previous_title = widget.plotItem.titleLabel.text
    try:
        if title:
            widget.setTitle(title)
        exporter.export(path)
    finally:
        if title:
            widget.setTitle(previous_title)


class ProfilePanel(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        layout = QVBoxLayout(self)
        self.description = QLabel("Draw a profile line across a result map. NaN gaps represent holes or inactive regions.")
        self.description.setWordWrap(True)
        layout.addWidget(self.description)
        row = QHBoxLayout()
        self.selector = QComboBox()
        self.selector.currentIndexChanged.connect(self.refresh)
        row.addWidget(self.selector, 1)
        for title, callback in [("Export CSV", self.export_csv), ("Export plot", self.export_profile_plot),
                                ("Remove", self.remove)]:
            button = QPushButton(title)
            button.clicked.connect(callback)
            row.addWidget(button)
        layout.addLayout(row)
        self.plot = pg.PlotWidget()
        self.plot.setLabel("bottom", "Distance along cut", units="µm")
        self.plot.showGrid(x=False, y=True, alpha=.12)
        layout.addWidget(self.plot)
        self.profiles = []
        self.quantity_label = ""

    def add_profile(self, name, layer, field, distance, values, coordinates, *, provenance=None):
        self.profiles.append(dict(name=name, layer=layer, field=field, distance=distance,
                                  values=values, coordinates=coordinates, provenance=provenance or {}))
        self.selector.addItem(name)
        self.selector.setCurrentIndex(len(self.profiles) - 1)
        self.refresh()

    def refresh(self):
        self.plot.clear()
        index = self.selector.currentIndex()
        if not 0 <= index < len(self.profiles):
            return
        focal = self.profiles[index]
        label, unit = FIELD_LABELS.get(focal["field"], (focal["field"], "SI"))
        kind = focal.get("provenance", {}).get("kind", "original")
        if kind == "difference":
            label = "Difference · " + label
        self.quantity_label = label
        self.plot.setLabel("left", "", units=unit)
        self.plot.getAxis("left").enableAutoSIPrefix(unit == FIELD_UNITS.get(focal["field"]))
        compatible = [p for p in self.profiles if p["field"] == focal["field"]
                      and p.get("provenance", {}).get("kind", "original") == kind]
        for entry in compatible:
            self.plot.plot(entry["distance"] * 1e6, to_display(entry["values"], FIELD_UNITS.get(entry["field"], "")),
                           pen=pg.mkPen("#1F6F5F" if entry is focal else "#A2A8A5", width=2 if entry is focal else 1),
                           connect="finite", name=entry["name"])
        self.description.setText(f"{focal['name']} · {focal['layer']} · {label} ({unit}). Other {label.lower()} profiles shown in gray.")

    def export_profile_plot(self):
        export_plot(self.plot, self, title=self.quantity_label)

    def remove(self):
        index = self.selector.currentIndex()
        if index >= 0:
            self.profiles.pop(index)
            self.selector.removeItem(index)
            self.refresh()

    def export_csv(self):
        if not self.profiles:
            return
        path, _ = QFileDialog.getSaveFileName(self, "Export all profiles", "profiles.csv", "CSV (*.csv)")
        if path:
            with open(path, "w", newline="", encoding="utf-8") as stream:
                writer = csv_writer(stream)
                writer.writerow(["profile", "layer", "field", "unit", "distance_m", "x_m", "y_m", "value", "provenance_json"])
                for profile in self.profiles:
                    unit = FIELD_UNITS.get(profile["field"], "SI")
                    for distance, value, coordinate in zip(profile["distance"], profile["values"], profile["coordinates"]):
                        writer.writerow([profile["name"], profile["layer"], profile["field"], unit,
                                         distance, *coordinate, value if np.isfinite(value) else "",
                                         json.dumps(profile.get("provenance", {}), ensure_ascii=False)])


class CrossSectionPanel(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        layout = QVBoxLayout(self)
        row = QHBoxLayout()
        self.description = QLabel("Draw a cross-section line through the layout to inspect actual lateral occupancy.")
        self.description.setWordWrap(True)
        row.addWidget(self.description, 1)
        button = QPushButton("Export plot")
        button.clicked.connect(lambda: export_plot(self.plot, self))
        row.addWidget(button)
        layout.addLayout(row)
        self.plot = pg.PlotWidget()
        self.plot.setLabel("bottom", "Distance along cut", units="µm")
        self.plot.setLabel("left", "Elevation z", units="µm")
        self.plot.getAxis("bottom").enableAutoSIPrefix(False)
        self.plot.getAxis("left").enableAutoSIPrefix(False)
        self.plot.showGrid(x=False, y=True, alpha=.12)
        layout.addWidget(self.plot)
        self.intervals = {}
        self.stack_summary = QLabel("Conductors carry current and heat; insulators and thermal sheets carry heat only. Dashed links are effective transfer paths, not solid material.")
        self.stack_summary.setWordWrap(True)
        self.stack_summary.setAccessibleName("Thermal stack interpretation")
        layout.addWidget(self.stack_summary)
        self.layer_table = QTableWidget(0, 6)
        self.layer_table.setHorizontalHeaderLabels(["Layer", "Role", "Material", "Thickness (µm)", "Bottom z (µm)", "Nominal k x/y/z (W/m K)"])
        self.layer_table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.layer_table.setMaximumHeight(155)
        self.layer_table.setAccessibleName("Physical thermal stack layers")
        self.layer_table.horizontalHeader().setStretchLastSection(True)
        layout.addWidget(self.layer_table)

    def show_cut(self, project, geometry, start, end):
        self.plot.clear()
        self.intervals = {}
        self.layer_table.setRowCount(0)
        span_z = max(layer.z + layer.thickness for layer in project.layers) - min(layer.z for layer in project.layers)
        length = np.linalg.norm(np.asarray(end) - np.asarray(start))
        for layer in project.layers:
            if not layer.visible or layer.id not in geometry.layers:
                continue
            intervals = cross_section_intervals(geometry.layers[layer.id], start, end)
            self.intervals[layer.id] = intervals
            material = project.materials[layer.material_id]
            role = {"conductor": "conductor", "thermal": "thermal-only", "insulator": "insulator (thermal-only)"}[layer.role]
            color = QColor(layer.color)
            color.setAlphaF(min(layer.opacity, .25) if layer.role == "insulator" else layer.opacity)
            for a, b in intervals:
                rect = QGraphicsRectItem(QRectF(a * 1e6, layer.z * 1e6, (b - a) * 1e6, layer.thickness * 1e6))
                rect.setBrush(color)
                rect.setPen(pg.mkPen(layer.color, width=1))
                kz = material.thermal_conductivity_z
                rect.setToolTip(f"{layer.name} · {layer.material_id} · {role}\n"
                                f"z={layer.z * 1e6:.5g} µm, t={layer.thickness * 1e6:.5g} µm\n"
                                f"Lateral k multipliers x,y: {material.thermal_anisotropy}; "
                                f"k_z: {kz if kz is not None else 'same material law as lateral k'} W/(m K)\n"
                                f"{material.notes}\nSource: {material.source}")
                self.plot.addItem(rect)
            if intervals:
                row = self.layer_table.rowCount()
                self.layer_table.insertRow(row)
                base_k = float(evaluate_property(material.thermal_conductivity, material.thermal_conductivity.reference_temperature))
                k_label = (f"{base_k * material.thermal_anisotropy[0]:.4g} / "
                           f"{base_k * material.thermal_anisotropy[1]:.4g} / "
                           f"{material.thermal_conductivity_z if material.thermal_conductivity_z is not None else base_k:.4g}")
                for column, text in enumerate((layer.name, role, material.name, f"{layer.thickness * 1e6:.5g}",
                                               f"{layer.z * 1e6:.5g}", k_label)):
                    item = QTableWidgetItem(text)
                    item.setToolTip("Nominal property value at its declared reference temperature; process calibration is required. " + material.notes)
                    self.layer_table.setItem(row, column, item)
            if intervals and layer.thickness > span_z * .04:
                widest = max(intervals, key=lambda ab: ab[1] - ab[0])
                label = pg.TextItem(f"{layer.name} · {layer.thickness * 1e6:.3g} µm", color="#171717", anchor=(.5, .5))
                label.setPos(sum(widest) * .5e6, (layer.z + layer.thickness / 2) * 1e6)
                self.plot.addItem(label)
        for interface in project.interfaces:
            if not interface.enabled or interface.id not in geometry.interfaces:
                continue
            a, b = project.layer(interface.layer_a), project.layer(interface.layer_b)
            lower, upper = (a, b) if a.z <= b.z else (b, a)
            low, high = lower.z + lower.thickness, upper.z
            for left, right in cross_section_intervals(geometry.interfaces[interface.id], start, end):
                if interface.electrical_kind == "none":
                    # A thermal resistance is a declared transfer path, not invented solid material.
                    center = (left + right) * .5e6
                    link = pg.PlotDataItem([center, center], [low * 1e6, high * 1e6],
                                          pen=pg.mkPen("#7E8982", width=1, style=Qt.PenStyle.DashLine))
                    gap = (f"gap {interface.height * 1e6:.5g} µm / {interface.material_id}" if interface.material_id else "no implicit gap material")
                    path = "includes both half-sheet resistances" if interface.thermal_kind == "stack" else "declared effective sheet-to-sheet link"
                    link.setToolTip(f"{interface.id}: thermal {interface.thermal_kind}; {path}; {gap}\n"
                                    f"Extra interface area resistance: {interface.thermal_resistance_area:.5g} m² K/W")
                    self.plot.addItem(link)
                elif high > low:
                    rect = QGraphicsRectItem(QRectF(left * 1e6, low * 1e6, (right - left) * 1e6, (high - low) * 1e6))
                    rect.setBrush(QColor(153, 108, 68, 90))
                    rect.setPen(pg.mkPen("#986B43", width=1, style=Qt.PenStyle.DashLine))
                    rect.setToolTip(f"{interface.id}\nElectrical: {interface.electrical_kind}\nThermal: {interface.thermal_kind}")
                    self.plot.addItem(rect)
                else:
                    self.plot.plot([left * 1e6, right * 1e6], [high * 1e6, high * 1e6],
                                   pen=pg.mkPen("#986B43", width=2))
        self.layer_table.resizeColumnsToContents()
        boundaries = []
        for boundary in project.thermal_boundaries:
            support = geometry.thermal_boundaries.get(boundary.id)
            if support is not None and cross_section_intervals(support, start, end):
                temperature = boundary.value if boundary.kind == "temperature" else boundary.ambient
                boundaries.append(f"{boundary.id} on {boundary.layer_id}: {boundary.kind}, reservoir {temperature:.5g} K")
        deposition = []
        for interface in project.interfaces:
            if not interface.enabled or interface.electrical_kind in {"none", "ideal"}:
                continue
            model = (f"all to {interface.heat_layer_id}" if interface.heat_layer_id else
                     f"{interface.heat_to_layer_a_fraction:.0%} to {interface.layer_a}, "
                     f"{1-interface.heat_to_layer_a_fraction:.0%} to {interface.layer_b}")
            deposition.append(f"{interface.id}: {model}")
        self.stack_summary.setText("Thermal-only sheets carry no DC current. Dashed links are effective transfer paths; "
            "gaps without declared material have no automatic heat path. "
            + ("Boundaries on cut: " + "; ".join(boundaries) + ". " if boundaries else "No reservoir boundary on this cut. ")
            + ("Interface/device heat: " + "; ".join(deposition) + ". " if deposition else "")
            + "Thickness-averaged 2.5D model; independent lateral/z scales; no resolved 3D internal heat profile.")
        self.plot.setXRange(0, length * 1e6, padding=.03)
        self.plot.enableAutoRange(axis="y", enable=True)
        self.description.setText(f"Cut ({start[0] * 1e6:.4g}, {start[1] * 1e6:.4g}) → ({end[0] * 1e6:.4g}, {end[1] * 1e6:.4g}) µm · separate x/z scales")


class StudyPanel(QWidget):
    result_selected = Signal(object)
    compare_requested = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        layout = QVBoxLayout(self)
        row = QHBoxLayout()
        self.metric_combo = QComboBox()
        self.metric_combo.setAccessibleName("Study metric")
        self.metric_combo.currentTextChanged.connect(self.refresh)
        row.addWidget(QLabel("Metric"))
        row.addWidget(self.metric_combo, 1)
        self.all_metrics = QCheckBox("All metrics")
        self.all_metrics.setToolTip("Include solver details and execution measurements in the metric selector.")
        self.all_metrics.toggled.connect(self.populate_metrics)
        row.addWidget(self.all_metrics)
        button = QPushButton("Export table")
        button.clicked.connect(self.export_csv)
        row.addWidget(button)
        button = QPushButton("Export plot")
        button.clicked.connect(lambda: export_plot(self.plot, self))
        row.addWidget(button)
        compare = QPushButton("Compare field maps")
        compare.clicked.connect(self.compare_requested.emit)
        row.addWidget(compare)
        layout.addLayout(row)
        self.mesh_confidence = QPushButton("Local mesh confidence…")
        self.mesh_confidence.setObjectName("study_mesh_confidence")
        self.mesh_confidence.setVisible(False)
        self.mesh_confidence.clicked.connect(self.show_mesh_confidence)
        layout.addWidget(self.mesh_confidence)
        self.summary = QLabel()
        self.summary.setWordWrap(True)
        self.summary.setAccessibleName("Study completion summary")
        layout.addWidget(self.summary)
        splitter = QSplitter(Qt.Orientation.Horizontal)
        self.table = QTableWidget()
        self.table.setAccessibleName("Study results and completion status")
        self.table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.table.itemActivated.connect(lambda item: self.select_result(item.row(), item.column()))
        self.table.setShowGrid(False)
        splitter.addWidget(self.table)
        self.plot = pg.PlotWidget(axisItems={"left": ReadableAxis(orientation="left")})
        self.plot_legend = self.plot.addLegend()
        self.plot.showGrid(x=False, y=True, alpha=.12)
        splitter.addWidget(self.plot)
        splitter.setSizes([530, 650])
        layout.addWidget(splitter)
        self.rows = []
        self.parameter = "Design"

    def set_rows(self, rows, parameter="Design"):
        self.rows = rows
        self.mesh_receipt = next((row.result.analysis["local_mesh_verification"] for row in reversed(rows)
                                  if row.result and "local_mesh_verification" in row.result.analysis), None)
        self.mesh_confidence.setVisible(self.mesh_receipt is not None)
        self.parameter = parameter
        self.populate_metrics(default=True)

    def populate_metrics(self, *_args, default=False):
        previous = None if default else self.metric_combo.currentData()
        metrics = sorted({key for row in self.rows if row.result for key, value in row.result.metrics.items()
                          if isinstance(value, (float, int)) and not isinstance(value, bool)})
        if not self.all_metrics.isChecked():
            def physical(key):
                base = key.removesuffix("_absolute_change").removesuffix("_relative_change")
                return base in METRIC_UNITS or base.startswith(("terminal_current:", "regularized_"))
            curated = [metric for metric in metrics if physical(metric)]
            metrics = curated or metrics
        self.metric_combo.blockSignals(True)
        self.metric_combo.clear()
        for metric in metrics:
            self.metric_combo.addItem(metric_label(metric), metric)
        preferred = ["effective_resistance", "temperature_max", "total_current"] if self.parameter == "Design" else ["temperature_max", "total_current", "effective_resistance"]
        if self.parameter == "Maximum time step (s)" or any(row.result and
                "transient_peak_temperature" in row.result.metrics for row in self.rows):
            preferred = ["transient_peak_temperature", "transient_energy_balance_error", "transient_peak_time"]
        if previous in metrics:
            preferred.insert(0, previous)
        for metric in preferred:
            if metric in metrics:
                self.metric_combo.setCurrentIndex(self.metric_combo.findData(metric))
                break
        self.metric_combo.blockSignals(False)
        self.refresh()

    def show_mesh_confidence(self):
        from etlayout.gui.refinement import MeshVerificationDialog
        if self.mesh_receipt is not None:
            dialog = MeshVerificationDialog(self.mesh_receipt, self)
            try:
                dialog.exec()
            finally:
                dialog.deleteLater()

    def rows_as_table(self):
        metric = self.metric_combo.currentData() or ""
        output = []
        for row in self.rows:
            label = getattr(row, "value", getattr(row, "name", ""))
            result = row.result
            value = result.metrics.get(metric) if result else None
            value = to_display(value, metric_unit(metric))
            applied = getattr(row, "applied_value", None)
            status = "Accepted" if result and result.converged and not row.error else "Not converged" if result else "Failed"
            output.append([status, str(label), "—" if value is None else f"{value:.9g}",
                           "—" if applied is None else str(applied), str(row.error or "")])
        return output

    def refresh(self):
        metric = self.metric_combo.currentData() or ""
        data = self.rows_as_table()
        self.table.setColumnCount(5)
        self.table.setHorizontalHeaderLabels(["Status", "Design" if self.parameter == "Design" else "Requested (SI)", metric_label(metric), "Applied (SI)", "Error"])
        self.table.horizontalHeaderItem(1).setToolTip(self.parameter + " / requested value in SI units")
        self.table.horizontalHeaderItem(2).setToolTip(metric_label(metric))
        self.table.setColumnHidden(3, not any(getattr(row, "applied_value", None) is not None for row in self.rows))
        self.table.setRowCount(len(data))
        for row, values in enumerate(data):
            for col, value in enumerate(values):
                self.table.setItem(row, col, QTableWidgetItem(value))
                self.table.item(row, col).setToolTip(str(value) + ("\n" + values[-1] if values[-1] else ""))
            if values[0] != "Accepted":
                self.table.item(row, 0).setForeground(QColor("#B5473C"))
        for column, width in enumerate((112, 140, 238, 130, 280)):
            self.table.setColumnWidth(column, width)
        accepted = sum(bool(row.result and row.result.converged and not row.error) for row in self.rows)
        failed = len(self.rows) - accepted
        self.summary.setText(f"{accepted}/{len(self.rows)} accepted · {failed} failed or incomplete. " +
                             ("Available failed metrics are crosses and never join the accepted trend. " if failed else "") +
                             "Select a row and press Enter to inspect its fields.")
        self.plot.clear()
        self.plot_legend.clear()
        self.plot.setTitle(f"{accepted} accepted · {failed} failed or incomplete" if failed else "")
        self.plot.setLabel("left", metric_label(metric))
        self.plot.setLabel("bottom", self.parameter)
        xs, ys, labels, failed_xs, failed_ys = [], [], [], [], []
        numeric = all(isinstance(getattr(row, "value", None), (float, int)) for row in self.rows)
        for i, row in enumerate(self.rows):
            applied = getattr(row, "applied_value", None)
            x = float(row.value if applied is None else applied) if numeric else i
            value = row.result.metrics.get(metric) if row.result else None
            value = to_display(value, metric_unit(metric))
            finite = isinstance(value, (float, int)) and np.isfinite(value)
            valid = finite and row.result.converged and not row.error
            xs.append(x)
            ys.append(value if valid else np.nan)
            if finite and not valid:
                failed_xs.append(x)
                failed_ys.append(value)
            labels.append((i, str(getattr(row, "name", i))))
        if numeric:
            ordered = sorted(zip(xs, ys))
            if ordered:
                xs, ys = zip(*ordered)
            self.plot.getAxis("bottom").setTicks(None)
        else:
            self.plot.getAxis("bottom").setTicks([labels])
        if xs:
            self.plot.plot(xs, ys, pen=pg.mkPen("#1F6F5F", width=2) if numeric else None,
                           symbol="o", symbolBrush="#1F6F5F", symbolPen=None, symbolSize=7,
                           connect="finite", name="Accepted")
        if failed_xs:
            self.plot.plot(failed_xs, failed_ys, pen=None, symbol="x", symbolPen=pg.mkPen("#B5473C", width=2),
                           symbolBrush=None, symbolSize=11, name="Not converged")
        self.plot.enableAutoRange(axis="y", enable=True)
        contextual = series_context_range([*ys, *failed_ys]) if not self.plot.getAxis("left").logMode else None
        if contextual is not None:
            self.plot.setYRange(*contextual, padding=0.)
            self.summary.setText(self.summary.text() + " Nearly constant: chart uses a contextual range; numerical data are unchanged.")
            self.plot.setToolTip("Zoom to inspect small changes. The automatic axis span is display context, not a scientific accuracy bound.")
        else:
            self.plot.setToolTip("")

    def select_result(self, row, col):
        if 0 <= row < len(self.rows) and self.rows[row].result:
            self.result_selected.emit(self.rows[row].result)

    def export_csv(self):
        path, _ = QFileDialog.getSaveFileName(self, "Export study", "study.csv", "CSV (*.csv)")
        if path:
            with open(path, "w", newline="", encoding="utf-8") as stream:
                writer = csv_writer(stream)
                metrics = sorted({key for row in self.rows if row.result for key, value in row.result.metrics.items()
                                  if isinstance(value, (float, int))})
                writer.writerow([self.parameter, "applied_value", *metrics, "error"])
                for row in self.rows:
                    writer.writerow([getattr(row, "value", getattr(row, "name", "")), getattr(row, "applied_value", ""),
                                     *[row.result.metrics.get(key, "") if row.result else "" for key in metrics], row.error or ""])
