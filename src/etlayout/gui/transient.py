# SPDX-License-Identifier: MPL-2.0
"""On-demand pulse setup and time-history inspection for the native workflow."""
from __future__ import annotations

from copy import deepcopy
from pathlib import Path

import numpy as np
import pyqtgraph as pg
from PySide6.QtCore import QEvent, Qt, QTimer, Signal
from PySide6.QtGui import QPalette
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QFormLayout,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QListWidget,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QSlider,
    QSplitter,
    QStackedWidget,
    QTableWidget,
    QTableWidgetItem,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from etlayout.gui.inputs import parse_number
from etlayout.gui.workspace import engineering
from etlayout.io.reporting import csv_writer
from etlayout.models import SimulationError, TransientDrive, Waveform
from etlayout.solvers.transient import validate_transient, validate_waveform, waveform_value
from etlayout.visualization.history import local_history


class HistoryPlot(pg.PlotWidget):
    """Keep chart labels and curves legible under the application's Qt palette."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.sync_palette()

    def sync_palette(self):
        if getattr(self, "plotItem", None) is None:
            return
        foreground = self.palette().color(QPalette.ColorRole.Text)
        self.setBackground(self.palette().color(QPalette.ColorRole.Base))
        for name in ("left", "bottom"):
            axis = self.getAxis(name)
            axis.setPen(foreground)
            axis.setTextPen(foreground)
            axis.setLabel(axis.labelText, units=axis.labelUnits, color=foreground.name())
        self.plotItem.titleLabel.setText(self.plotItem.titleLabel.text, color=foreground.name())
        for curve in self.listDataItems():
            curve.setPen(pg.mkPen(self.palette().color(QPalette.ColorRole.Link), width=2))

    def changeEvent(self, event):
        super().changeEvent(event)
        if event.type() == QEvent.Type.PaletteChange:
            self.sync_palette()


def drive_target_labels(project):
    labels = {}
    for terminal in project.terminals:
        labels[f"terminals.{terminal.id}.value"] = f"Terminal · {getattr(terminal, 'name', '') or terminal.id} · {terminal.kind}"
    for source in project.heat_sources:
        labels[f"heat_sources.{source.id}.value"] = f"Heat source · {getattr(source, 'name', '') or source.id}"
    for port in project.power.ports:
        labels[f"power.ports.{port.id}.value"] = f"Power port · {port.name or port.id} · {port.kind}"
    return labels


def drive_targets(project):
    targets = []
    units = {"voltage": "V", "current": "A", "density": "A/m²", "gate": "V"}
    owned = {landing.terminal_id for port in project.power.ports for landing in port.landings}
    for terminal in project.terminals:
        if terminal.kind in units and terminal.id not in owned:
            unit = units[terminal.kind]
            targets.append((f"terminals.{terminal.id}.value", unit))
    for port in project.power.ports:
        if port.kind in {"voltage", "current"}:
            targets.append((f"power.ports.{port.id}.value", units[port.kind]))
    for source in project.heat_sources:
        targets.append((f"heat_sources.{source.id}.value", "W" if source.kind == "total" else "W/m²"))
    return targets


class TransientDialog(QDialog):
    """Edits a detached configuration; Cancel never mutates the project."""
    def __init__(self, project, parent=None, *, mesh=None, start_time=0.):
        super().__init__(parent)
        self.project = deepcopy(project)
        self.mesh = mesh
        self.start_time = float(start_time)
        self.drives = self.project.transient.drives
        self._loaded_drive_index = -1
        self._loading = False
        self._wave_dirty = False
        self.setWindowTitle("Transient and pulsed operation")
        self.resize(780, 690)
        layout = QVBoxLayout(self)
        info = QLabel("Transient heat flow includes thermal mass. Electrical fields are quasi-static. Engineering units are accepted, for example 25 µs and 20 mV.")
        info.setWordWrap(True)
        layout.addWidget(info)
        tabs = QTabWidget()
        layout.addWidget(tabs, 1)
        timing = QWidget()
        form = QFormLayout(timing)
        self.enabled = QCheckBox("Enable transient operation")
        self.enabled.setChecked(project.transient.enabled)
        form.addRow(self.enabled)
        self.time_inputs = {}
        for name, label in (("stop_time", "Stop time (s)"), ("time_step", "Maximum step (s)"),
                            ("output_interval", "Saved field interval (s)")):
            edit = QLineEdit(engineering(getattr(project.transient, name), "s"))
            edit.setAccessibleName(label)
            self.time_inputs[name] = edit
            form.addRow(label, edit)
        self.initial_temperature = QLineEdit(str(project.solver.initial_temperature))
        self.initial_temperature.setAccessibleName("Initial temperature (K)")
        form.addRow("Initial temperature (K)", self.initial_temperature)
        self.initialization = QComboBox()
        self.initialization.addItem("Uniform initial temperature", "uniform")
        self.initialization.addItem("Steady solution at the initial drive", "steady")
        initial_mode = getattr(project.transient, "initialization", "uniform")
        if initial_mode in {"saved", "resume"}:
            self.initialization.addItem("Saved thermal state" if initial_mode == "saved" else "Resume saved state", initial_mode)
        self.initialization.setCurrentIndex(max(0, self.initialization.findData(initial_mode)))
        self.initialization.setAccessibleName("Transient initialization")
        self.initialization.setToolTip("Choose a saved state or resume a previous run through the Solve workflow's saved-state actions.")
        form.addRow("Initialize from", self.initialization)
        note = QLabel("Pulse edges, waveform knots and saved times split integration steps. Refine the maximum step to check accuracy; keep the saved interval fixed during that comparison. Use the Solve mode selector for thermal or electrothermal coupling.")
        note.setWordWrap(True)
        form.addRow(note)
        self.estimates = QLabel()
        self.estimates.setWordWrap(True)
        form.addRow(self.estimates)
        tabs.addTab(timing, "Timing")
        drive_page = QWidget()
        drive_layout = QVBoxLayout(drive_page)
        split = QSplitter()
        self.drive_list = QListWidget()
        self.drive_list.setAccessibleName("Saved waveform drives")
        self.drive_list.currentRowChanged.connect(self.load_drive)
        split.addWidget(self.drive_list)
        editor = QWidget()
        editor_form = QFormLayout(editor)
        self.target = QComboBox()
        self.target.setAccessibleName("Waveform target")
        self.target_units = dict(drive_targets(project))
        self.target_labels = drive_target_labels(project)
        for path, unit in self.target_units.items():
            self.target.addItem(f"{self.target_labels[path]} ({unit})", path)
            self.target.setItemData(self.target.count() - 1, path, Qt.ItemDataRole.ToolTipRole)
        editor_form.addRow("Target", self.target)
        self.kind = QComboBox()
        self.kind.setAccessibleName("Waveform shape")
        for title, key in (("Constant", "constant"), ("Pulse", "pulse"), ("Piecewise linear", "piecewise_linear")):
            self.kind.addItem(title, key)
        editor_form.addRow("Waveform", self.kind)
        self.wave_pages = QStackedWidget()
        self.wave_inputs = {}
        specs = [[("value", "Value (target unit)")],
                 [("low", "Low (target unit)"), ("high", "High (target unit)"), ("delay", "Delay (s)"),
                  ("width", "High plateau width (s)"), ("period", "Period (s; 0 = single pulse)"),
                  ("rise_time", "Rise time (s)"), ("fall_time", "Fall time (s)")]]
        for specs_for_kind in specs:
            page = QWidget()
            page_form = QFormLayout(page)
            for name, label in specs_for_kind:
                edit = QLineEdit()
                edit.setAccessibleName(label)
                self.wave_inputs[name] = edit
                page_form.addRow(label, edit)
            self.wave_pages.addWidget(page)
        piecewise = QWidget()
        piecewise_layout = QVBoxLayout(piecewise)
        self.piecewise_table = QTableWidget(0, 2)
        self.piecewise_table.setHorizontalHeaderLabels(["Time (s)", "Value (target unit)"])
        self.piecewise_table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self.piecewise_table.setAccessibleName("Piecewise waveform time and value table")
        self.piecewise_table.setMinimumHeight(130)
        self.piecewise_table.setMaximumHeight(145)
        piecewise_layout.addWidget(self.piecewise_table)
        knot_actions = QHBoxLayout()
        add_knot = QPushButton("Add point")
        add_knot.clicked.connect(self.add_knot)
        remove_knot = QPushButton("Remove point")
        remove_knot.clicked.connect(self.remove_knot)
        knot_actions.addWidget(add_knot)
        knot_actions.addWidget(remove_knot)
        piecewise_layout.addLayout(knot_actions)
        self.wave_pages.addWidget(piecewise)
        self.kind.currentIndexChanged.connect(self.select_wave_page)
        editor_form.addRow(self.wave_pages)
        apply_drive = QPushButton("Apply waveform")
        apply_drive.clicked.connect(self.apply_drive)
        self.editor_scroll = QScrollArea()
        self.editor_scroll.setWidgetResizable(True)
        self.editor_scroll.setMinimumSize(0, 170)
        self.editor_scroll.setWidget(editor)
        split.addWidget(self.editor_scroll)
        split.setStretchFactor(1, 2)
        drive_layout.addWidget(split)
        row = QHBoxLayout()
        add = QPushButton("Add drive")
        add.clicked.connect(self.add_drive)
        add.setEnabled(self.target.count() > 0)
        remove = QPushButton("Remove drive")
        remove.clicked.connect(self.remove_drive)
        row.addWidget(add)
        row.addWidget(remove)
        row.addStretch()
        row.addWidget(apply_drive)
        drive_layout.addLayout(row)
        self.preview = HistoryPlot()
        self.preview.setMinimumHeight(145)
        self.preview.setMaximumHeight(180)
        self.preview.setLabel("bottom", "Time", units="s")
        self.preview.setLabel("left", "Drive")
        drive_layout.addWidget(self.preview)
        tabs.addTab(drive_page, "Waveforms")
        advanced = QWidget()
        advanced_form = QFormLayout(advanced)
        for name, label in (("max_steps", "Maximum steps"), ("max_snapshots", "Maximum saved fields"),
                            ("max_history_bytes", "History budget (MiB)")):
            value = getattr(project.transient, name)
            edit = QLineEdit(str(value / 1024**2) if name == "max_history_bytes" else str(value))
            edit.setAccessibleName(label)
            self.time_inputs[name] = edit
            advanced_form.addRow(label, edit)
        self.retain_peak = QCheckBox("Retain the whole-run peak temperature field")
        self.retain_peak.setChecked(getattr(project.transient, "retain_peak", True))
        advanced_form.addRow(self.retain_peak)
        self.capacity_scheme = QComboBox()
        self.capacity_scheme.addItem("Consistent finite-element capacity", "consistent")
        self.capacity_scheme.addItem("Lumped nodal capacity", "lumped")
        self.capacity_scheme.setCurrentIndex(max(0, self.capacity_scheme.findData(getattr(project.transient, "capacity_scheme", "consistent"))))
        self.capacity_scheme.setAccessibleName("Thermal capacity discretization")
        self.capacity_scheme.setToolTip("Consistent capacity is the default. Lumped capacity changes the spatial discretization; check field-bound diagnostics and time/mesh convergence for either scheme.")
        advanced_form.addRow("Thermal capacity", self.capacity_scheme)
        self.periodic_tolerance = QLineEdit(str(getattr(project.transient, "periodic_tolerance", 0.)))
        self.periodic_tolerance.setAccessibleName("Periodic temperature tolerance (K); zero disables")
        advanced_form.addRow("Periodic tolerance (K; 0 = off)", self.periodic_tolerance)
        self.periodic_min_cycles = QLineEdit(str(getattr(project.transient, "periodic_min_cycles", 3)))
        self.periodic_min_cycles.setAccessibleName("Minimum cycles before periodic stop")
        advanced_form.addRow("Minimum cycles", self.periodic_min_cycles)
        self.periodic_required_cycles = QLineEdit(str(getattr(project.transient, "periodic_required_cycles", 2)))
        self.periodic_required_cycles.setAccessibleName("Consecutive matching cycles required")
        advanced_form.addRow("Matching cycles required", self.periodic_required_cycles)
        periodic_note = QLabel("Periodic stopping compares the spatial temperature state at equal cycle phase. Use constant or pulsed drives with a shared period; the stop time remains the upper limit.")
        periodic_note.setWordWrap(True)
        advanced_form.addRow(periodic_note)
        tabs.addTab(advanced, "Advanced")
        self.error = QLabel()
        self.error.setWordWrap(True)
        self.error.setStyleSheet("color:#B5473C")
        layout.addWidget(self.error)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
        self.refresh_list()
        if self.drives:
            self.drive_list.setCurrentRow(0)
        else:
            self.populate_waveform(Waveform(kind="pulse"))
        for edit in self.wave_inputs.values():
            edit.textChanged.connect(self.mark_wave_dirty)
            edit.editingFinished.connect(self.preview_editor)
        self.kind.currentIndexChanged.connect(self.mark_wave_dirty)
        self.target.currentIndexChanged.connect(self.mark_wave_dirty)
        self.target.currentIndexChanged.connect(self.update_target_unit)
        self.piecewise_table.cellChanged.connect(self.mark_wave_dirty)
        self.piecewise_table.cellChanged.connect(self.preview_editor)
        for edit in self.time_inputs.values():
            edit.editingFinished.connect(self.update_estimates)
        self.retain_peak.toggled.connect(self.update_estimates)
        self.initialization.currentIndexChanged.connect(self.update_estimates)
        self.time_inputs["stop_time"].editingFinished.connect(self.preview_editor)
        self.update_target_unit()
        self.select_wave_page(self.kind.currentIndex())
        self.update_estimates()

    def select_wave_page(self, index):
        self.wave_pages.setCurrentIndex(index)
        # QStackedWidget otherwise reserves the pulse form's height even for
        # the small constant/table editors, placing their controls offscreen.
        self.wave_pages.setFixedHeight(self.wave_pages.currentWidget().sizeHint().height())

    def mark_wave_dirty(self, *_):
        if not self._loading:
            self._wave_dirty = True

    def preview_editor(self, *_):
        try:
            self.plot_preview(self.read_waveform())
        except (ValueError, TypeError, SimulationError):
            self.preview.clear()
            self.preview.setTitle("Preview unavailable until waveform values are valid")

    def refresh_list(self, selected=-1):
        self.drive_list.blockSignals(True)
        self.drive_list.clear()
        self.drive_list.addItems([f"{self.target_labels.get(drive.target, drive.target)} · {drive.waveform.kind}" for drive in self.drives])
        self.drive_list.setCurrentRow(selected)
        self.drive_list.blockSignals(False)

    def populate_waveform(self, wave):
        was_loading, self._loading = self._loading, True
        self.kind.setCurrentIndex(self.kind.findData(wave.kind))
        for name, edit in self.wave_inputs.items():
            value = getattr(wave, name)
            edit.setText(engineering(value, "s") if name in {"delay", "width", "period", "rise_time", "fall_time"} else str(value))
        self.piecewise_table.blockSignals(True)
        self.piecewise_table.setRowCount(len(wave.times))
        for row, (time, value) in enumerate(zip(wave.times, wave.values)):
            self.piecewise_table.setItem(row, 0, QTableWidgetItem(engineering(time, "s")))
            self.piecewise_table.setItem(row, 1, QTableWidgetItem(str(value)))
        self.piecewise_table.blockSignals(False)
        self._loading = was_loading

    def update_target_unit(self, *_):
        unit = self.target_units.get(self.target.currentData(), "")
        self.piecewise_table.setHorizontalHeaderLabels(["Time (s)", f"Value ({unit})"])
        for name in ("value", "low", "high"):
            self.wave_inputs[name].setToolTip(f"Enter {unit} or an engineering prefix, for example 20 m{unit}.")

    def add_knot(self):
        row = self.piecewise_table.rowCount()
        self.piecewise_table.insertRow(row)
        self.piecewise_table.setItem(row, 0, QTableWidgetItem("0 s"))
        self.piecewise_table.setItem(row, 1, QTableWidgetItem("0"))
        self.piecewise_table.setCurrentCell(row, 0)
        self.piecewise_table.editItem(self.piecewise_table.item(row, 0))

    def remove_knot(self):
        row = self.piecewise_table.currentRow()
        if row >= 0:
            self.piecewise_table.removeRow(row)
            self.mark_wave_dirty()
            self.preview_editor()

    def load_drive(self, index):
        if self._loading or index == self._loaded_drive_index:
            return
        if self._wave_dirty and not self.apply_drive():
            self.drive_list.blockSignals(True)
            self.drive_list.setCurrentRow(self._loaded_drive_index)
            self.drive_list.blockSignals(False)
            return
        if 0 <= index < len(self.drives):
            drive = self.drives[index]
            self._loading = True
            self.target.setCurrentIndex(self.target.findData(drive.target))
            self.populate_waveform(drive.waveform)
            self._loading = False
            self._loaded_drive_index = index
            self._wave_dirty = False
            self.refresh_list(index)
            self.plot_preview(drive.waveform)

    def read_waveform(self):
        kind = self.kind.currentData()
        names = {"constant": ["value"], "pulse": ["low", "high", "delay", "width", "period", "rise_time", "fall_time"],
                 "piecewise_linear": []}[kind]
        values = {}
        for name in names:
            unit = "s" if name in {"delay", "width", "period", "rise_time", "fall_time"} else self.target_units.get(self.target.currentData(), "")
            values[name] = parse_number(self.wave_inputs[name].text(), unit)
        if kind == "piecewise_linear":
            values = {"times": [], "values": []}
            for row in range(self.piecewise_table.rowCount()):
                for column, (key, unit) in enumerate((("times", "s"), ("values", self.target_units.get(self.target.currentData(), "")))):
                    item = self.piecewise_table.item(row, column)
                    try:
                        values[key].append(parse_number(item.text() if item else "", unit))
                    except ValueError as error:
                        raise ValueError(f"Point {row + 1}, {'time' if column == 0 else 'value'}: {error}") from error
        wave = Waveform(kind=kind, **values)
        validate_waveform(wave)
        return wave

    def apply_drive(self):
        try:
            target = self.target.currentData()
            if not target:
                raise SimulationError("Add a current/voltage terminal or heat source in Setup before defining a waveform.")
            drive = TransientDrive(target, self.read_waveform())
            index = self._loaded_drive_index
            if any(item.target == target and i != index for i, item in enumerate(self.drives)):
                raise SimulationError("This target already has a waveform. Select it from the list to edit it.")
            if index < 0:
                self.drives.append(drive)
                index = len(self.drives) - 1
            else:
                self.drives[index] = drive
            self._loaded_drive_index = index
            self._wave_dirty = False
            self.refresh_list(index)
            self.plot_preview(drive.waveform)
            self.error.clear()
            self.update_estimates()
            return True
        except (ValueError, TypeError, SimulationError) as exc:
            self.error.setText(str(exc))
            return False

    def add_drive(self):
        if self._wave_dirty and not self.apply_drive():
            return
        used = {drive.target for drive in self.drives}
        target = next((index for index in range(self.target.count()) if self.target.itemData(index) not in used), -1)
        if target < 0:
            self.error.setText("Every eligible target already has a drive. Select one to edit it.")
            return
        self._loaded_drive_index = -1
        self._loading = True
        self.drive_list.blockSignals(True)
        self.drive_list.setCurrentRow(-1)
        self.drive_list.blockSignals(False)
        self.target.setCurrentIndex(target)
        self.populate_waveform(Waveform(kind="pulse"))
        self._loading = False
        self.apply_drive()

    def remove_drive(self):
        index = self.drive_list.currentRow()
        if index >= 0:
            self.drives.pop(index)
            self._loaded_drive_index = -1
            self._wave_dirty = False
            self.refresh_list(min(index, len(self.drives) - 1))
            self.preview.clear()
            if self.drives:
                self.load_drive(self.drive_list.currentRow())
            self.update_estimates()

    def plot_preview(self, wave):
        try:
            stop = parse_number(self.time_inputs["stop_time"].text(), "s")
            if not np.isfinite(stop) or stop <= 0:
                return
            caption = "Waveform preview"
            knots = []
            if wave.kind == "pulse":
                ratio = max(0., (stop - wave.delay) / wave.period) if wave.period else 1
                if ratio > 200:
                    cycles = 5
                    stop = min(stop, wave.delay + cycles * wave.period)
                    caption += " · first 5 periods"
                else:
                    cycles = int(np.ceil(ratio))
                for cycle in range(cycles):
                    start = wave.delay + cycle * wave.period
                    knots.extend([start, start + wave.rise_time, start + wave.rise_time + wave.width,
                                  start + wave.rise_time + wave.width + wave.fall_time])
            elif wave.kind == "piecewise_linear":
                knots = wave.times
                if len(knots) > 2000:
                    knots = np.asarray(knots)[np.linspace(0, len(knots) - 1, 2000).astype(int)]
                    caption += " · sampled knots"
            samples = sorted(set([*np.linspace(0, stop, 401), *(t for t in knots if 0 <= t <= stop)]))
            # Draw both sides at the same x coordinate. Ideal edges remain
            # visible even when a pulse is narrower than regular preview samples.
            times = np.repeat(samples, 2)
            values = [value for t in samples for value in
                      (waveform_value(wave, t, left_limit=True), waveform_value(wave, t))]
            self.preview.clear()
            self.preview.plot(times, values, pen=pg.mkPen("#1F6F5F", width=2))
            self.preview.setLabel("left", "Drive", units=self.target_units.get(self.target.currentData(), ""))
            self.preview.setTitle(caption)
            self.preview.sync_palette()
        except ValueError:
            pass

    def accept(self):
        try:
            if self._wave_dirty and not self.apply_drive():
                return
            settings = self.project.transient
            settings.enabled = self.enabled.isChecked()
            for name, edit in self.time_inputs.items():
                if name == "max_history_bytes":
                    value = parse_number(edit.text(), "MiB") * 1024**2
                    if not value.is_integer():
                        raise ValueError("History budget must represent a whole number of bytes")
                    value = int(value)
                else:
                    value = parse_number(edit.text(), "" if name.startswith("max_") else "s", integer=name.startswith("max_"))
                setattr(settings, name, value)
            settings.initialization = self.initialization.currentData()
            if settings.initialization in {"uniform", "steady"}:
                settings.initial_state_digest = ""
            settings.retain_peak = self.retain_peak.isChecked()
            settings.capacity_scheme = self.capacity_scheme.currentData()
            settings.periodic_tolerance = parse_number(self.periodic_tolerance.text(), "K")
            settings.periodic_min_cycles = parse_number(self.periodic_min_cycles.text(), integer=True)
            settings.periodic_required_cycles = parse_number(self.periodic_required_cycles.text(), integer=True)
            self.project.solver.initial_temperature = parse_number(self.initial_temperature.text(), "K")
            if not np.isfinite(self.project.solver.initial_temperature) or self.project.solver.initial_temperature <= 0:
                raise SimulationError("Initial temperature must be finite and positive")
            if settings.enabled:
                if self.project.solver.mode == "electrical":
                    self.project.solver.mode = "electrothermal"
                validate_transient(self.project)
            super().accept()
        except (ValueError, TypeError, SimulationError) as exc:
            self.error.setText(str(exc))

    def update_estimates(self, *_):
        try:
            from etlayout.solvers.transient import estimate_transient_resources
            estimate_project = deepcopy(self.project)
            for key in ("stop_time", "time_step", "output_interval"):
                setattr(estimate_project.transient, key, parse_number(self.time_inputs[key].text(), "s"))
            for key in ("max_steps", "max_snapshots"):
                setattr(estimate_project.transient, key, parse_number(self.time_inputs[key].text(), integer=True))
            estimate_project.transient.max_history_bytes = int(parse_number(self.time_inputs["max_history_bytes"].text(), "MiB") * 1024**2)
            estimate_project.transient.retain_peak = self.retain_peak.isChecked()
            estimate_project.transient.initialization = self.initialization.currentData()
            start = self.start_time if estimate_project.transient.initialization == "resume" else 0.
            info = estimate_transient_resources(estimate_project, self.mesh, start_time=start)
            memory = info.get("estimated_history_bytes")
            memory_note = f"Estimated field history: {memory / 1024**2:.1f} MiB." if memory is not None else "Memory estimate is available after meshing."
            self.estimates.setText(f"Preflight estimate: {info['steps']:,} steps · {info['output_snapshots']:,} scheduled fields "
                                   f"+ {info['peak_reserve_snapshots']:,} peak reserve. {memory_note}" +
                                   (" Exceeds the configured field budget." if not info['fits_snapshot_budget'] else "") +
                                   (" Exceeds the configured memory budget." if info['fits_history_budget'] is False else ""))
        except (ImportError, ValueError, TypeError, SimulationError):
            self.estimates.setText("Enter valid timing values to estimate integration and storage.")

    def done(self, result):
        # pyqtgraph owns menus outside the Qt parent tree; explicit cleanup
        # prevents abandoned plot objects from receiving later theme changes.
        if self.preview.plotItem is not None:
            self.preview.close()
        super().done(result)


class TimeNavigation(QWidget):
    snapshot_changed = Signal(int)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.times = np.array([])
        self.peak_time = None
        self._pending_index = None
        self._drag_timer = QTimer(self)
        self._drag_timer.setInterval(34)
        self._drag_timer.setSingleShot(True)
        self._drag_timer.timeout.connect(self.flush_selection)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(2)
        row = QHBoxLayout()
        self._navigation_row = row
        layout.addLayout(row)
        self.run_metrics = {}
        self.displayed_metrics = {}
        self.run_completed = None
        self._frame_metrics = []
        previous = QPushButton("‹")
        previous.setAccessibleName("Previous saved time")
        previous.setMinimumWidth(32)
        previous.setMaximumWidth(36)
        previous.clicked.connect(lambda: self.slider.setValue(self.slider.value() - 1))
        row.addWidget(previous)
        self.slider = QSlider(Qt.Orientation.Horizontal)
        self.slider.setAccessibleName("Saved transient time")
        self.slider.valueChanged.connect(self.changed)
        self.slider.sliderReleased.connect(self.flush_selection)
        row.addWidget(self.slider, 1)
        following = QPushButton("›")
        following.setAccessibleName("Next saved time")
        following.setMinimumWidth(32)
        following.setMaximumWidth(36)
        following.clicked.connect(lambda: self.slider.setValue(self.slider.value() + 1))
        row.addWidget(following)
        self.time_input = QLineEdit()
        self.time_input.setAccessibleName("Jump to time; nearest retained field")
        self.time_input.setToolTip("Enter a time such as 25 µs; Enter selects the nearest retained field.")
        self.time_input.setMaximumWidth(95)
        self.time_input.returnPressed.connect(self.jump_to_input)
        row.addWidget(self.time_input)
        self.peak_button = QPushButton("Peak")
        self.peak_button.setAccessibleName("Jump to whole-run peak temperature time")
        self.peak_button.setToolTip("Select the saved field nearest the whole-run peak temperature.")
        self.peak_button.clicked.connect(self.jump_to_peak)
        self.last_button = QPushButton("Last frame")
        self.last_button.setAccessibleName("Return to last accepted saved frame")
        self.last_button.clicked.connect(self.jump_to_last)
        self.label = QLabel()
        self.label.setMinimumWidth(70)
        row.addWidget(self.label)
        self.temperature_summary = QLabel()
        self.temperature_summary.setWordWrap(True)
        self.temperature_summary.setAccessibleName("Displayed frame and accepted-run or segment peak temperatures")
        self.temperature_summary.setMinimumWidth(0)
        self.temperature_summary.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        actions = QHBoxLayout()
        actions.addWidget(self.peak_button)
        actions.addWidget(self.last_button)
        actions.addWidget(self.temperature_summary, 1)
        layout.addLayout(actions)
        self._actions_row = actions
        self._navigation_controls = (previous, following, self.time_input, self.label,
                                     self.peak_button, self.last_button)
        self._actions_in_navigation = False
        self.hide()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._place_actions()

    def _place_actions(self):
        # Keep the summary on its own short line when the saved-time controls
        # fit. Narrow inspectors retain the two grouped action/summary rows.
        row = self._navigation_row
        spacing = max(0, row.spacing())
        needed = sum(min(widget.sizeHint().width(), widget.maximumWidth())
                     for widget in self._navigation_controls)
        needed += self.slider.minimumSizeHint().width() + spacing * len(self._navigation_controls)
        inline = self.width() >= needed
        if inline == self._actions_in_navigation:
            return
        self._actions_in_navigation = inline
        for index, button in enumerate((self.peak_button, self.last_button)):
            if inline:
                self._actions_row.removeWidget(button)
                row.insertWidget(row.indexOf(self.label), button)
            else:
                row.removeWidget(button)
                self._actions_row.insertWidget(index, button)
        self.updateGeometry()

    def set_result(self, result):
        history = result.transient if result else None
        self.run_completed = history.completed if history else None
        self._frame_metrics = [snapshot.metrics for snapshot in history.snapshots] if history else []
        self._set_times(history.times if history else (), result.metrics if history else {},
                        present=history is not None)
        self.set_frame_metrics(history.snapshots[-1].metrics if history and history.snapshots else result.metrics if result else {})

    def set_record(self, record, index=None):
        """Navigate exact stored indices without manufacturing a numerical result."""
        self._frame_metrics = []
        self.run_completed = getattr(record, "transient_metadata", {}).get("completed") if record else None
        self._set_times(record.times if record else (), record.metrics if record else {},
                        present=bool(record and record.times), index=index, stored=True)

    def _set_times(self, times, metrics, *, present, index=None, stored=False):
        self._drag_timer.stop()
        self._pending_index = None
        self.slider.blockSignals(True)
        self.times = np.asarray(times)
        self.run_metrics = dict(metrics)
        self.peak_time = metrics.get("transient_peak_time") if present else None
        self.peak_button.setEnabled(self.peak_time is not None)
        peak_scope = "Segment peak" if present and metrics.get("transient_peak_scope") == "current_segment" else "Whole-run peak"
        self.peak_button.setText("Segment peak frame" if peak_scope == "Segment peak" else "Peak frame")
        self.peak_button.setAccessibleName(f"Jump to {peak_scope.lower()} temperature time")
        if self.peak_time is not None:
            retained = bool(np.any(np.isclose(self.times, self.peak_time, rtol=1e-10, atol=0)))
            if not retained:
                self.peak_button.setText("Nearest to peak")
            self.peak_button.setToolTip(f"{peak_scope} at {engineering(self.peak_time, 's')}. " +
                                       ("Its field is retained." if retained else "Peak field was not retained; selects the nearest retained time.") +
                                       (" Stored metadata; a selected field is validated when loaded." if stored else ""))
        self.last_button.setEnabled(bool(len(self.times)))
        self.slider.setRange(0, max(0, len(self.times) - 1))
        selected = len(self.times) - 1 if index is None else index
        self.slider.setValue(max(0, min(selected, len(self.times) - 1)))
        self.slider.blockSignals(False)
        self.setVisible(present)
        self.update_label()
        self.set_frame_metrics(self.displayed_metrics)

    def update_label(self):
        if len(self.times):
            self.time_input.setText(engineering(self.times[self.slider.value()], "s"))
            self.label.setText(f"Saved {self.slider.value() + 1}/{len(self.times)}")
            self.label.setToolTip(f"Retained time: {self.times[self.slider.value()]:.12g} s")

    def set_frame_metrics(self, metrics):
        self.displayed_metrics = dict(metrics)
        now = metrics.get("time_s")
        maximum = metrics.get("temperature_max")
        text = ("Displayed " + engineering(now, "s") + ": " if now is not None else "Displayed frame: ")
        text += "Tmax " + engineering(maximum, "K") if maximum is not None else "temperature unavailable"
        peak, time = self.run_metrics.get("transient_peak_temperature"), self.run_metrics.get("transient_peak_time")
        if peak is not None and time is not None:
            scope = "Segment peak" if self.run_metrics.get("transient_peak_scope") == "current_segment" else "Accepted-run peak" if self.run_completed is False else "Run peak"
            text += f" · {scope} " + engineering(peak, "K") + " at " + engineering(time, "s")
            if not np.any(np.isclose(self.times, time, rtol=1e-10, atol=0)):
                text += " · peak spatial field not retained"
        self.temperature_summary.setText(text)
        self._place_actions()

    def jump_to_last(self):
        if len(self.times):
            self.slider.setValue(len(self.times) - 1)
            self.flush_selection()

    def jump_to_input(self):
        try:
            self.select_time(parse_number(self.time_input.text(), "s"))
            self.time_input.setStyleSheet("")
        except ValueError as error:
            self.time_input.setToolTip(str(error))
            self.time_input.setStyleSheet("border:1px solid #B5473C")

    def select_time(self, time_s):
        if len(self.times):
            index = int(np.argmin(np.abs(self.times - float(time_s))))
            self.slider.setValue(index)
            self.flush_selection()
            self.update_label()
            self.label.setToolTip(f"Requested {engineering(time_s, 's')}; nearest retained field {engineering(self.times[index], 's')}")

    def jump_to_peak(self):
        if self.peak_time is not None:
            self.select_time(self.peak_time)

    def changed(self, index):
        self.update_label()
        if self.slider.isSliderDown():
            self._pending_index = index
            if not self._drag_timer.isActive():
                self._drag_timer.start()
        else:
            self._drag_timer.stop()
            self._pending_index = None
            self._emit_selection(index)

    def flush_selection(self):
        self._drag_timer.stop()
        if self._pending_index is not None:
            index, self._pending_index = self._pending_index, None
            self._emit_selection(index)


    def _emit_selection(self, index):
        # A dragged/requested time is not yet the displayed frame. Advance the
        # summary together with the coalesced field-selection event only.
        if 0 <= index < len(self._frame_metrics):
            self.set_frame_metrics(self._frame_metrics[index])
        self.snapshot_changed.emit(index)


class TimeHistoryPanel(QWidget):
    time_selected = Signal(float)
    scan_requested = Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.result = None
        self.record = None
        self.record_metadata = {}
        self.selected_time = 0.
        self.layer = ""
        self.region = ""
        self.probe = None
        self.trace_times = np.array([])
        self.trace_values = np.array([])
        layout = QVBoxLayout(self)
        self.description = QLabel("Run transient operation to inspect the time history.")
        self.description.setWordWrap(True)
        layout.addWidget(self.description)
        row = QFormLayout()
        self.scope_selector = QComboBox()
        for label, key in (("Whole device", "global"), ("Selected layer / region", "selection"), ("Map probe", "probe")):
            self.scope_selector.addItem(label, key)
        self.scope_selector.setAccessibleName("Time history spatial scope")
        self.scope_selector.currentIndexChanged.connect(self.scope_changed)
        row.addRow("Scope", self.scope_selector)
        self.metric_selector = QComboBox()
        for label, key, unit in (("Peak temperature", "temperature_max", "K"),
                                  ("Mean temperature", "temperature_average", "K"),
                                  ("Heat input", "thermal_power", "W"), ("Heat out", "heat_out", "W"),
                                  ("Stored energy", "thermal_stored_energy", "J"),
                                  ("Input energy", "input_energy", "J"),
                                  ("Energy balance residual", "energy_balance_error", "J")):
            self.metric_selector.addItem(label, (key, unit))
        self.metric_selector.currentIndexChanged.connect(self.refresh)
        self.metric_selector.setAccessibleName("Time history quantity")
        row.addRow("Quantity", self.metric_selector)
        export = QPushButton("Export time history…")
        export.clicked.connect(self.export_history)
        row.addRow(export)
        self.load_history = QPushButton("Load local history…")
        self.load_history.setAccessibleName("Load complete saved fields for this local time history")
        self.load_history.setToolTip("Explicitly load and validate this run's saved fields in a cancellable worker. "
                                    "The local cache budget applies.")
        self.load_history.clicked.connect(lambda: self.scan_requested.emit("trace"))
        self.load_history.hide()
        row.addRow(self.load_history)
        layout.addLayout(row)
        self.plot = HistoryPlot()
        self.plot.setLabel("bottom", "Time", units="s")
        self.plot.showGrid(x=False, y=True, alpha=.12)
        self.plot.scene().sigMouseClicked.connect(self.plot_clicked)
        layout.addWidget(self.plot)
        self.cursor = None

    def set_result(self, result):
        self.record = None
        self.record_metadata = {}
        self.probe = None
        self.layer, self.region = "", ""
        self.result = result if result and result.transient else None
        self.selected_time = float(self.result.transient.times[-1]) if self.result else 0.
        self.refresh()

    def set_record(self, record, metadata=None):
        """Show stored scalar history; local fields require an explicit scan."""
        self.record = record if record and record.times else None
        self.record_metadata = metadata if metadata is not None else (record.transient_metadata if record else {})
        self.result = None
        self.probe = None
        self.layer, self.region = "", ""
        self.selected_time = float(self.record.times[-1]) if self.record else 0.
        self.refresh()

    def context(self):
        return {"layer": self.layer, "region": self.region, "probe": self.probe,
                "scope": self.scope_selector.currentData(), "quantity": self.metric_selector.currentData(),
                "selected_time": self.selected_time}

    def restore_context(self, context):
        """Restore view choices after an explicitly requested full worker load."""
        self.layer = context.get("layer", "")
        self.region = context.get("region", "")
        self.probe = context.get("probe")
        self.selected_time = context.get("selected_time", self.selected_time)
        self.scope_selector.blockSignals(True)
        self.scope_selector.setCurrentIndex(max(0, self.scope_selector.findData(context.get("scope", "global"))))
        self.scope_selector.blockSignals(False)
        self.metric_selector.blockSignals(True)
        self.metric_selector.setCurrentIndex(max(0, self.metric_selector.findData(context.get("quantity"))))
        self.metric_selector.blockSignals(False)
        self.scope_changed()

    def set_context(self, layer, region="", probe=None):
        layer, region = layer or "", region or ""
        point = tuple(probe) if probe is not None else self.probe if (layer, region) == (self.layer, self.region) else None
        changed = (layer, region, point) != (self.layer, self.region, self.probe)
        self.layer, self.region, self.probe = layer, region, point
        if changed and self.scope_selector.currentData() != "global":
            self.refresh()

    def scope_changed(self, *_):
        local = self.scope_selector.currentData() != "global"
        self.metric_selector.blockSignals(True)
        for index in range(self.metric_selector.count()):
            self.metric_selector.model().item(index).setEnabled(not local or index < 2)
        if local and self.metric_selector.currentIndex() > 1:
            self.metric_selector.setCurrentIndex(0)
        self.metric_selector.blockSignals(False)
        self.refresh()

    def plot_clicked(self, event):
        if (self.result is None and self.record is None) or event.button() != Qt.MouseButton.LeftButton:
            return
        if self.plot.plotItem.vb.sceneBoundingRect().contains(event.scenePos()):
            point = self.plot.plotItem.vb.mapSceneToView(event.scenePos())
            self.time_selected.emit(float(point.x()))

    def refresh(self):
        self.plot.clear()
        self.cursor = None
        self.trace_times = np.array([])
        self.trace_values = np.array([])
        self.load_history.hide()
        if self.result is None and self.record is None:
            self.description.setText("Run transient operation to inspect the time history.")
            return
        history = self.result.transient if self.result is not None else None
        metadata = self.record_metadata if self.record is not None else {}
        rows = history.rows if history else metadata.get("rows", ())
        key, unit = self.metric_selector.currentData()
        self.plot.setLabel("left", self.metric_selector.currentText(), units=unit)
        scope = self.scope_selector.currentData()
        if scope == "global":
            if not rows:
                self.description.setText("Stored step summaries are unavailable. Load the complete history to inspect this trace.")
                self.load_history.setText("Load complete time history…")
                self.load_history.show()
                return
            times = [row["time_s"] for row in rows]
            values = [row.get(key, np.nan) for row in rows]
            context = "All accepted steps"
        else:
            if not self.layer:
                self.description.setText("Select a result layer on the map to inspect its saved-field history.")
                return
            if key not in {"temperature_max", "temperature_average"}:
                self.description.setText("Local histories provide peak and area-mean temperature; choose a temperature quantity. Energy and power balances are whole-device quantities.")
                return
            if scope == "probe" and self.probe is None:
                self.description.setText("Click a probe on the map, then inspect its temperature history.")
                return
            if self.record is not None:
                self.description.setText("Local history fields are not loaded. Load this run's saved fields to calculate the selected trace.")
                self.load_history.setText("Load local history…")
                self.load_history.show()
                return
            times, values = local_history(self.result, self.layer, region=self.region,
                                         reduction="maximum" if key == "temperature_max" else "mean",
                                         probe=self.probe if scope == "probe" else None)
            context = f"{self.layer} · {self.region or 'all regions'} · saved fields"
            if scope == "probe":
                context += f" · probe ({engineering(self.probe[0], 'm')}, {engineering(self.probe[1], 'm')})"
                self.plot.setLabel("left", "Probe temperature", units="K")
        self.trace_times, self.trace_values = np.asarray(times), np.asarray(values)
        if unit == "W":
            times, values = np.repeat(times, 2)[:-1], np.repeat(values, 2)[1:]
        self.plot.plot(times, values, pen=pg.mkPen("#1F6F5F", width=2), connect="finite")
        self.cursor = pg.InfiniteLine(pos=self.selected_time, angle=90, movable=False,
                                      pen=pg.mkPen("#6F6F6F", style=Qt.PenStyle.DashLine))
        self.plot.addItem(self.cursor)
        self.plot.sync_palette()
        if history:
            status = "Completed" if history.completed else "Incomplete"
            count = len(history.snapshots)
        else:
            status = "Stored completed" if metadata.get("completed", False) else "Stored incomplete"
            count = len(self.record.times)
            context += " · numerical fields not loaded"
        self.description.setText(f"{status} · {count} saved fields\n{context}")
        self.description.setToolTip(f"{len(rows) - 1} accepted integration steps. Click the trace to select the nearest retained field. "
                                    "Whole-device histories include every accepted step; local histories sample retained fields only. " +
                                    ("Stored summaries do not validate unloaded numerical fields." if self.record is not None else ""))
        self.plot.setToolTip("Click the trace to select the nearest saved field on the map.")

    def select_time(self, time_s):
        self.selected_time = float(time_s)
        if self.cursor is not None:
            self.cursor.setValue(time_s)

    def export_history(self):
        if self.record is not None:
            self.scan_requested.emit("export")
            return
        if self.result is None:
            return
        if self.scope_selector.currentData() != "global":
            if not len(self.trace_times):
                return
            path, _ = QFileDialog.getSaveFileName(self, "Export selected temperature history", "selected-temperature-history.csv", "CSV (*.csv)")
            if path:
                try:
                    self.export_local_history_to(path)
                except (OSError, ValueError) as error:
                    QMessageBox.warning(self, "Export failed", str(error))
            return
        path = QFileDialog.getExistingDirectory(self, "Export time history")
        if path:
            from etlayout.io.transient_export import export_transient_history
            try:
                export_transient_history(self.result, path)
            except (OSError, ValueError) as error:
                QMessageBox.warning(self, "Export failed", str(error))

    def export_local_history_to(self, path):
        """Export exactly the visible local trace with its spatial provenance."""
        if not len(self.trace_times) or self.scope_selector.currentData() == "global":
            raise ValueError("Select an available local temperature history before exporting")
        probe = self.probe if self.scope_selector.currentData() == "probe" else None
        with Path(path).open("w", encoding="utf-8", newline="") as handle:
            writer = csv_writer(handle)
            writer.writerow(["time_s", "temperature_K", "layer", "region", "reduction", "probe_x_m", "probe_y_m", "configuration_hash"])
            for time, value in zip(self.trace_times, self.trace_values):
                writer.writerow([time, value, self.layer, self.region,
                                 "probe" if probe else self.metric_selector.currentData()[0],
                                 probe[0] if probe else "", probe[1] if probe else "", self.result.configuration_hash])
