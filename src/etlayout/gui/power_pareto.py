# SPDX-License-Identifier: MPL-2.0
"""Selectable objective projections from recorded, physically evaluated candidates."""
from __future__ import annotations

import math

import numpy as np
import pyqtgraph as pg
from PySide6.QtCore import Signal
from PySide6.QtGui import QPainter
from PySide6.QtSvg import QSvgGenerator
from PySide6.QtWidgets import (
    QComboBox,
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from etlayout.visualization.units import display_unit, to_display


class ParetoPanel(QWidget):
    candidate_selected = Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.history = {}
        self.plotted_records = []
        self.front_ids = set()
        layout = QVBoxLayout(self)
        controls = QHBoxLayout()
        self.resolution = QComboBox()
        self.resolution.addItems(["coarse", "refined"])
        self.resolution.setAccessibleName("Common mesh basis for objective comparisons")
        self.resolution.currentIndexChanged.connect(self.refresh)
        controls.addWidget(QLabel("Mesh basis"))
        controls.addWidget(self.resolution)
        self.x_objective, self.y_objective = QComboBox(), QComboBox()
        for title, selector in (("Horizontal objective", self.x_objective), ("Vertical objective", self.y_objective)):
            selector.setAccessibleName(title)
            controls.addWidget(QLabel(title))
            controls.addWidget(selector, 1)
            selector.currentIndexChanged.connect(self.refresh)
        export = QPushButton("Export plot…")
        export.clicked.connect(self.export_plot)
        controls.addWidget(export)
        layout.addLayout(controls)
        self.plot = pg.PlotWidget(background="w")
        self.plot.setMinimumSize(250, 200)
        self.plot.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Ignored)
        self.plot.setAccessibleName("Feasible candidate objective trade-offs")
        self.plot.showGrid(x=False, y=True, alpha=.12)
        for name in ("bottom", "left"):
            self.plot.getAxis(name).setPen("#404040")
            self.plot.getAxis(name).setTextPen("#171717")
        layout.addWidget(self.plot, 1)
        self.note = QLabel("Run or open an experiment to inspect objective trade-offs.")
        self.note.setWordWrap(True)
        layout.addWidget(self.note)

    def set_history(self, history):
        new_history = self.history is not history
        self.history = history
        self.resolution.blockSignals(True)
        if new_history:
            self.resolution.setCurrentText(history.get("pareto_resolution", "coarse"))
        self.resolution.blockSignals(False)
        configured = history.get("identity", {}).get("experiment", {}).get("objectives", [])
        keys = list(dict.fromkeys([item["measurement_id"] for item in configured] +
            [key for row in history.get("rows", []) for key in row.get("objectives", {})]))
        for index, selector in enumerate((self.x_objective, self.y_objective)):
            previous = selector.currentText()
            selector.blockSignals(True)
            selector.clear()
            selector.addItems(keys)
            selector.setCurrentIndex(keys.index(previous) if previous in keys else min(index, len(keys)-1))
            selector.blockSignals(False)
        self.y_objective.setEnabled(len(keys) > 1)
        self.refresh()

    def objective_unit(self, key):
        units = {scenario.get("measurements", {}).get(key, {}).get("unit")
                 for row in self.history.get("rows", []) for scenario in row.get("scenarios", [])}
        units.discard(None)
        return next(iter(units)) if len(units) == 1 else "unit unavailable"

    def metadata(self, key):
        objectives = self.history.get("identity", {}).get("experiment", {}).get("objectives", [])
        definition = next((item for item in objectives if item["measurement_id"] == key), {})
        unit = display_unit(self.objective_unit(key))
        direction = definition.get("direction")
        preference = "lower is better" if direction == "min" else "higher is better" if direction == "max" else "direction unspecified"
        aggregation = definition.get("scenario_aggregation", "aggregation unspecified")
        return f"{key} ({unit}) · {preference} · {aggregation}", direction

    def refresh(self, *_):
        self.plot.clear()
        self.plotted_records = []
        self.front_ids = set()
        xkey, ykey = self.x_objective.currentText(), self.y_objective.currentText()
        one_dimensional = self.x_objective.count() < 2 or xkey == ykey
        self.plot.getAxis("left").setTicks(None)
        if not xkey:
            self.note.setText("No recorded objective values are available. Failed candidates remain in the Candidates table.")
            return
        x_label, x_direction = self.metadata(xkey)
        y_label, y_direction = self.metadata(ykey)
        def short_label(label, direction):
            return label.split(" · ")[0] + (" ↓" if direction == "min" else " ↑" if direction == "max" else "")
        self.plot.setLabel("bottom", short_label(x_label, x_direction), color="#171717")
        self.plot.setLabel("left", "Candidate" if one_dimensional else short_label(y_label, y_direction), color="#171717")
        from etlayout.optimization import ranking_rows
        basis = self.resolution.currentText()
        rows = [row for row in ranking_rows(self.history, basis)
                if all(isinstance(row.get("objectives", {}).get(key), (int, float))
                        and math.isfinite(row["objectives"][key]) for key in {xkey, ykey})]
        self.plotted_records = rows
        if not rows:
            self.note.setText("No feasible finite candidate values for this selection. Inspect failure diagnostics in Candidates.")
            return
        values = np.array([[row["objectives"][key] for key in ([xkey] if one_dimensional else [xkey, ykey])] for row in rows])
        directions = [x_direction] if one_dimensional else [x_direction, y_direction]
        if all(direction in {"min", "max"} for direction in directions):
            preferred = values * np.array([1 if direction == "min" else -1 for direction in directions])
            self.front_ids = {row["id"] for i, row in enumerate(rows)
                              if not any(np.all(other <= preferred[i]) and np.any(other < preferred[i]) for other in preferred)}
        spots, rings = [], []
        for index, row in enumerate(rows):
            x = to_display(row["objectives"][xkey], self.objective_unit(xkey))
            y = index if one_dimensional else to_display(row["objectives"][ykey], self.objective_unit(ykey))
            verified = row["status"] == "verified_feasible"
            symbol = "s" if verified else "t" if row["status"] == "mesh_uncertain" else "o"
            color = "#1F6F5F" if verified else "#6F6F6F"
            spots.append({"pos": (x, y), "data": row["id"], "symbol": symbol, "brush": color, "pen": color, "size": 10})
            if row["id"] in self.front_ids:
                rings.append({"pos": (x, y), "symbol": "o", "brush": None, "pen": pg.mkPen("#171717", width=1), "size": 17})
            changes = row.get("verification", {}).get("absolute_changes", {}) if basis == "refined" else {}
            dx = changes.get(xkey)
            dy = None if one_dimensional else changes.get(ykey)
            if dx is not None or dy is not None:
                errors = {"x": np.array([x]), "y": np.array([y]), "pen": pg.mkPen(color, width=1)}
                if isinstance(dx, (int, float)) and math.isfinite(dx):
                    errors["width"] = np.array([2 * abs(to_display(dx, self.objective_unit(xkey)))])
                if isinstance(dy, (int, float)) and math.isfinite(dy):
                    errors["height"] = np.array([2 * abs(to_display(dy, self.objective_unit(ykey)))])
                self.plot.addItem(pg.ErrorBarItem(**errors))
        if rings:
            self.plot.addItem(pg.ScatterPlotItem(spots=rings, useCache=False))
        self.points = pg.ScatterPlotItem(spots=spots, hoverable=True, useCache=False,
            tip=lambda x, y, data: f"{data}\n{xkey}: {x:.7g}" + ("" if one_dimensional else f"\n{ykey}: {y:.7g}"))
        self.points.sigClicked.connect(lambda _item, points, _event: self.candidate_selected.emit(points[0].data()) if points else None)
        self.plot.addItem(self.points)
        if one_dimensional:
            self.plot.getAxis("left").setTicks([[(index, "Baseline" if row.get("baseline") else f"Candidate {index+1}")
                                                  for index, row in enumerate(rows)]])
        self.plot.enableAutoRange()
        aggregation = "; ".join(f"{key}: {self.metadata(key)[0].rsplit(' · ', 1)[-1]}" for key in dict.fromkeys([xkey, ykey]))
        self.note.setText(f"{len(rows)} feasible candidates at {basis} resolution · square: verified finalist; circle: provisional/baseline; triangle: mesh uncertain. "
            "Outer ring: nondominated in the displayed objective selection. Bars show recorded last mesh changes, not statistical intervals; absent bars mean unavailable. "
            f"Arrows show preferred direction. Scenario aggregation — {aggregation}. Click a point for exact evidence. " +
            ("One objective: candidate comparison, without a second trade-off axis." if one_dimensional else ""))

    def export_plot(self):
        path, _ = QFileDialog.getSaveFileName(self, "Export objective plot", "pareto.svg", "SVG (*.svg);;PNG (*.png)")
        if path:
            try:
                self.export_to(path)
            except (OSError, ValueError) as error:
                self.note.setText(str(error))

    def export_to(self, path):
        """Use Qt's vector renderer; avoid pyqtgraph's fragile SVG path rewriting."""
        if str(path).lower().endswith(".svg"):
            generator = QSvgGenerator()
            generator.setFileName(str(path))
            generator.setSize(self.plot.size())
            generator.setViewBox(self.plot.rect())
            generator.setTitle("Feasible candidate objective trade-offs")
            generator.setDescription(self.note.text())
            painter = QPainter(generator)
            if not painter.isActive():
                raise OSError("Cannot open plot export destination")
            try:
                self.plot.render(painter)
            finally:
                painter.end()
        else:
            from pyqtgraph.exporters import ImageExporter
            exporter = ImageExporter(self.plot.plotItem)
            exporter.parameters()["width"] = max(1800, 2 * self.plot.width())
            exporter.export(str(path))

    def closeEvent(self, event):
        self.plot.close()
        super().closeEvent(event)
