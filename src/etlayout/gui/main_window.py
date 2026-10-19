# SPDX-License-Identifier: MPL-2.0
"""Electrothermal Layout Studio's dockable native engineering workspace."""
from __future__ import annotations

import json
import logging
import uuid
from copy import deepcopy
from dataclasses import asdict
from pathlib import Path
from types import SimpleNamespace

import numpy as np
from PySide6.QtCore import QPointF, QSettings, QStandardPaths, Qt, QTimer
from PySide6.QtGui import QAction, QColor, QDesktopServices, QKeySequence, QTransform
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QDockWidget,
    QFileDialog,
    QFormLayout,
    QGridLayout,
    QHBoxLayout,
    QHeaderView,
    QInputDialog,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QSplitter,
    QStyle,
    QTableWidget,
    QTableWidgetItem,
    QTabWidget,
    QTextEdit,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
    QWizard,
    QWizardPage,
)
from shapely.geometry import LineString, Point
from shapely.ops import unary_union

from etlayout import __version__
from etlayout.geometry.prepare import resolve_region
from etlayout.gui.field_scale import FieldScaleControl
from etlayout.gui.forms import ModelDialog, region_summary
from etlayout.gui.history_opening import HistoryOpener
from etlayout.gui.history_workflow import HistoryWorkflow, is_record
from etlayout.gui.inputs import parse_number
from etlayout.gui.jobs import ComputeJob
from etlayout.gui.plots import CrossSectionPanel, ProfilePanel, StudyPanel
from etlayout.gui.saving import SaveJob, SaveRequest
from etlayout.gui.transient import TimeHistoryPanel, TimeNavigation, TransientDialog
from etlayout.gui.viewer import VECTOR_FIELDS, LayoutView, geometry_region
from etlayout.gui.workspace import (
    ConfigurationNavigation,
    RunHistoryPanel,
    configuration_changes,
    engineering,
    run_caption,
)
from etlayout.io.identity import document_id
from etlayout.io.project import (
    annotated_result,
    physics_hash,
    project_from_dict,
)
from etlayout.io.recovery import RecoveryStore
from etlayout.io.saving import SaveOrder, SaveSnapshot, freeze_result_arrays
from etlayout.materials.database import MaterialDatabase
from etlayout.models import (
    HeatSource,
    Interface,
    LayoutSource,
    Material,
    PhysicalLayer,
    Project,
    RefinementRegion,
    RegionSpec,
    SimulationError,
    SweepSpec,
    Terminal,
    ThermalBoundary,
)
from etlayout.simulation.analysis import FIELD_UNITS
from etlayout.simulation.execution import ExecutionSettings
from etlayout.visualization.palettes import COLORMAPS
from etlayout.visualization.sampling import (
    DISPLAY_FIELD_LABELS as FIELD_LABELS,
)
from etlayout.visualization.sampling import (
    METRIC_UNITS,
    FieldSampler,
    metric_label,
    metric_unit,
)
from etlayout.visualization.support import field_support, region_options
from etlayout.visualization.units import to_display

LOG = logging.getLogger(__name__)

LIGHT_STYLE = """
QMainWindow, QDialog, QWizard { background: #f5f6f4; color: #17221e; }
QWidget { font-family: 'Segoe UI', Arial; font-size: 10pt; color: #17221e; }
QMenuBar, QMenu, QToolBar { background: #fbfcfa; color: #17221e; }
QToolBar { spacing: 6px; padding: 5px; border-bottom: 1px solid #d9dfda; }
QDockWidget::title { background: #e9eee9; padding: 7px; font-weight: 600; }
QTabWidget::pane { border: 1px solid #d9dfda; }
QTabBar::tab { padding: 7px 10px; background: #eef1ed; }
QTabBar::tab:selected { background: white; border-top: 2px solid #1f6f5f; }
QTableWidget, QTreeWidget, QListWidget, QTextEdit { background: white; color: #17221e; border: 1px solid #d9dfda; selection-background-color: #dcebe4; selection-color: #10251b; }
QHeaderView::section { background: #f4f6f2; border: 0; border-bottom: 1px solid #d9dfda; padding: 6px; font-weight: 600; }
QPushButton { padding: 6px 10px; border: 1px solid #bac9bf; border-radius: 3px; background: #f9fbf8; }
QPushButton:hover { background: #e2eee7; }
QPushButton:disabled { color: #939c95; }
QLineEdit, QComboBox { padding: 5px; border: 1px solid #bfcac1; background: white; color: #17221e; }
QProgressBar { border: 1px solid #c6d1c8; border-radius: 3px; text-align: center; background: white; }
QProgressBar::chunk { background: #71ab98; }
QStatusBar { background: #edf1eb; }
QCheckBox::indicator { width: 13px; height: 13px; border: 1px solid #72867a; border-radius: 2px; background: white; }
QCheckBox::indicator:checked { background: #1f6f5f; border: 2px solid #b4d2c2; }
QToolButton { color: #17221e; padding: 4px; }
"""

DARK_STYLE = LIGHT_STYLE + """
QMainWindow, QDialog, QWizard, QWidget { background: #252e2a; color: #edf3ee; }
QMenuBar, QMenu, QToolBar { background: #202822; color: #edf3ee; }
QDockWidget::title { background: #344139; }
QTabBar::tab { background: #344139; }
QTabBar::tab:selected { background: #202822; }
QTableWidget, QTreeWidget, QListWidget, QTextEdit, QLineEdit, QComboBox { background: #1e2822; color: #edf3ee; selection-background-color: #386451; selection-color: white; }
QHeaderView::section, QPushButton { background: #344139; color: #edf3ee; }
QToolButton { color: #edf3ee; }
QPushButton:hover { background: #426450; }
QStatusBar { background: #202822; }
"""


def short_number(value):
    if isinstance(value, (int, float)):
        return f"{value:.7g}"
    if isinstance(value, dict):
        return "; ".join(f"{key}: {short_number(item)}" for key, item in value.items())
    if isinstance(value, (list, tuple)):
        return ", ".join(short_number(item) for item in value)
    return str(value)


class ObjectPanel(QWidget):
    """Searchable typed objects; double-click opens the complete property form."""

    def __init__(self, title, columns, add, edit, remove, parent=None, extra=()):
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(7, 7, 7, 7)
        self.search = QLineEdit()
        self.search.setPlaceholderText(f"Filter {title.lower()}…")
        self.search.textChanged.connect(self.filter)
        layout.addWidget(self.search)
        self.table = QTableWidget(0, len(columns))
        self.table.setHorizontalHeaderLabels(columns)
        self.table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QTableWidget.SelectionMode.SingleSelection)
        self.table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.table.setShowGrid(False)
        self.table.verticalHeader().setVisible(False)
        self.table.horizontalHeader().setStretchLastSection(True)
        self.table.cellDoubleClicked.connect(lambda *_: edit())
        layout.addWidget(self.table)
        buttons = QHBoxLayout()
        for label, callback in [("Add", add), ("Edit", edit), ("Remove", remove), *extra]:
            button = QPushButton(label)
            button.clicked.connect(callback)
            buttons.addWidget(button)
        layout.addLayout(buttons)
        self.ids = []

    def selected_id(self):
        row = self.table.currentRow()
        return self.ids[row] if 0 <= row < len(self.ids) else None

    def set_rows(self, rows):
        selected = self.selected_id()
        self.ids = [row[0] for row in rows]
        self.table.setRowCount(len(rows))
        for row, values in enumerate(rows):
            for col, value in enumerate(values):
                item = QTableWidgetItem(str(value))
                item.setToolTip(str(value))
                self.table.setItem(row, col, item)
        self.table.resizeColumnsToContents()
        if selected in self.ids:
            self.table.selectRow(self.ids.index(selected))
        elif rows:
            self.table.selectRow(0)
        self.filter(self.search.text())

    def filter(self, text):
        text = text.lower()
        for row in range(self.table.rowCount()):
            content = " ".join(self.table.item(row, col).text() for col in range(self.table.columnCount())
                               if self.table.item(row, col))
            self.table.setRowHidden(row, text not in content.lower())


class NewProjectWizard(QWizard):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Create an electrothermal project")
        self.resize(660, 390)
        page = QWizardPage()
        page.setTitle("Name your project")
        page.setSubTitle("Geometry, materials and simulation results are stored together in a portable .etlayout project.")
        form = QFormLayout(page)
        self.name = QLineEdit("New project")
        page.registerField("project_name*", self.name)
        form.addRow("Project name", self.name)
        self.mode = QComboBox()
        self.mode.addItem("DC electrical", "electrical")
        self.mode.addItem("Steady-state thermal", "thermal")
        self.mode.addItem("Coupled DC electrothermal", "electrothermal")
        form.addRow("Initial analysis", self.mode)
        self.import_after = QCheckBox("Import a GDSII / OASIS layout after creation")
        self.import_after.setChecked(True)
        form.addRow(self.import_after)
        note = QLabel("Start with layout masks, then define the physical stack, contacts and terminals. All three analysis modes remain available at any time.")
        note.setWordWrap(True)
        form.addRow(note)
        self.addPage(page)


class ImportWizard(QWizard):
    def __init__(self, document, project, parent=None):
        super().__init__(parent)
        self.document = document
        self.setWindowTitle("Import layout")
        self.resize(760, 600)
        page = QWizardPage()
        page.setTitle(Path(document.path).name)
        page.setSubTitle(f"Database unit: {document.dbu_um:.8g} µm. Paths and instance transforms are resolved by KLayout.")
        form = QFormLayout(page)
        self.cell = QComboBox()
        self.cell.addItems(document.cells)
        if document.top_cells:
            self.cell.setCurrentText(document.top_cells[0])
        form.addRow("Top / selected cell", self.cell)
        self.mapping = QTableWidget(len(document.layers), 5)
        self.mapping.setHorizontalHeaderLabels(["Use", "Mask", "Physical name", "Role", "Material"])
        self.mapping.horizontalHeader().setStretchLastSection(True)
        for row, layer in enumerate(document.layers):
            enabled = QCheckBox()
            enabled.setChecked(False)
            enabled.setToolTip("Explicitly map this mask to physics. Unchecked masks remain display-only and are retained in the source.")
            self.mapping.setCellWidget(row, 0, enabled)
            self.mapping.setItem(row, 1, QTableWidgetItem(layer["key"]))
            self.mapping.item(row, 1).setFlags(Qt.ItemFlag.ItemIsEnabled)
            self.mapping.setItem(row, 2, QTableWidgetItem(layer["name"] or "Layer " + layer["key"]))
            role = QComboBox()
            role.addItems(["conductor", "thermal", "insulator"])
            self.mapping.setCellWidget(row, 3, role)
            material = QComboBox()
            material.addItems(sorted(project.materials))
            material.setCurrentText("copper")
            self.mapping.setCellWidget(row, 4, material)
        form.addRow(self.mapping)
        self.thickness = QLineEdit("1")
        form.addRow("Initial sheet thickness (µm)", self.thickness)
        text = QLabel("Unchecked masks remain display-only. Mapping is an explicit choice; review each selected mask: vias should generally define interfaces, not conducting sheets. The stack editor supports multiple masks, Boolean geometry and full-domain layers.")
        text.setWordWrap(True)
        form.addRow(text)
        self.addPage(page)

    def accept(self):
        try:
            if float(self.thickness.text()) <= 0:
                raise ValueError
        except ValueError:
            QMessageBox.warning(self, "Invalid thickness", "Enter a positive thickness in µm.")
            return
        super().accept()

    def layers(self):
        thickness = float(self.thickness.text()) * 1e-6
        layers = []
        for row, data in enumerate(self.document.layers):
            if not self.mapping.cellWidget(row, 0).isChecked():
                continue
            role = self.mapping.cellWidget(row, 3).currentText()
            layers.append(PhysicalLayer(
                id="layer_" + data["key"].replace("/", "_"), name=self.mapping.item(row, 2).text(),
                material_id=self.mapping.cellWidget(row, 4).currentText(), thickness=thickness, z=len(layers) * thickness,
                role=role, geometry=RegionSpec(sources=[data["key"]]), opacity=.25 if role == "insulator" else .75,
                color=["#397F70", "#778C9B", "#AE9363", "#8D86A1"][row % 4]))
        return layers


class MainWindow(QMainWindow):
    def __init__(self, project=None, result=None, database_path=None, recover=True):
        super().__init__()
        self.setObjectName("ElectrothermalStudio")
        self.resize(1560, 980)
        self.settings = QSettings("etlayout", "Electrothermal Layout Studio")
        try:
            self.execution_settings = ExecutionSettings.from_dict(json.loads(self.settings.value("execution", "{}")))
        except (SimulationError, TypeError, ValueError):
            self.execution_settings = ExecutionSettings()
        self.history_lazy_enabled = self.settings.value("history/lazy", False, type=bool)
        try:
            self.history_cache_mib = max(16, min(8192, int(self.settings.value("history/cache_mib", 256))))
        except (TypeError, ValueError):
            self.history_cache_mib = 256
        self.database = MaterialDatabase(database_path)
        self.project = project or Project()
        if not self.project.materials:
            self.project.materials = {m.id: m for m in self.database.list_materials()}
        self.result = result
        if result is not None:
            freeze_result_arrays(result)
        self.transient_frame = None
        self.mesh = result.mesh if result else None
        self.geometry = None
        self.document = None
        self.masks = {}
        self.path = None
        self.document_revision = 0
        self.document_epoch = str(uuid.uuid4())
        self.save_job = None
        self._save_queue = []
        self._save_sequence = 0
        self._latest_manual_save = 0
        self._save_continuation = None
        self._save_success = False
        self._close_after_save = False
        self.dirty = False
        document_id(self.project)
        self.undo_history = []
        self.redo_history = []
        self.job = None
        self.job_context = None
        self.prepare_job = None
        self._pending_compute = None
        self.hierarchy_jobs = []
        self.hierarchy_queue = []
        self.geometry_generation = 0
        self.run_history = [result] if result else []
        self.history_workflow = HistoryWorkflow(self)
        self.history_opener = HistoryOpener(self)
        self.history_opener.loaded.connect(self._catalog_opened)
        self.history_opener.review_required.connect(self._catalog_review)
        self.history_opener.legacy.connect(self._catalog_legacy)
        self.history_opener.failed.connect(self._catalog_failed)
        self.history_opener.busy_changed.connect(self._catalog_busy)
        self._pending_catalog = None
        self._catalog_review_restore = None
        self._catalog_revision = 0
        self.inspection_context = None
        self._before_physics = None
        self._autosave_error = ""
        self.operation = ""
        self.sampler = None
        self.net_report = None
        self.last_cut = None
        self.studies = []
        self.display_sampler = None
        self.display_result = None
        self._original_key_metrics = ""
        self.difference_source = None
        self.difference_name = ""
        self._difference_cache = {}
        self._region_choices = {}
        self._region_layer = None
        self._run_view_states = {}
        self._pending_view_state = None
        self._refreshing = False
        self._show_masks = False
        self._preferred_field = None
        recovery_root = Path(QStandardPaths.writableLocation(QStandardPaths.StandardLocation.AppLocalDataLocation))
        self.recovery_path = recovery_root / "recovery" / "pending.etlayout"
        self.recovery_store = None
        self._recovery_startup_error = ""
        try:
            self.recovery_store = RecoveryStore(self.recovery_path.parent)
        except OSError as error:
            self._recovery_startup_error = str(error)
        self._recovery_current_path = self.recovery_path
        self._recovered_windows = []
        if recover and self.recovery_store is not None:
            try:
                self.recovery_store.import_legacy(recovery_root / "recovery.etlayout")
            except (OSError, SimulationError) as error:
                self._recovery_startup_error = str(error)
        self.build_workspace()
        self.build_actions()
        dark = self.settings.value("dark", False, type=bool)
        self.apply_theme(dark)
        self.dark_action.setChecked(dark)
        self.refresh_project()
        self.apply_example_view_defaults()
        if result:
            self.show_result(result)
        self.autosave_timer = QTimer(self)
        self.autosave_timer.setInterval(60000)
        self.autosave_timer.timeout.connect(self.autosave)
        self.autosave_timer.start()
        try:
            if recover and self.recovery_store is not None and self.recovery_store.entries():
                QTimer.singleShot(150, self.offer_recovery)
        except (OSError, SimulationError) as error:
            self._recovery_startup_error = str(error)
        if self._recovery_startup_error:
            self.recovery_status.setText("Recovery unavailable — save your project")
            self.recovery_status.setToolTip(self._recovery_startup_error)
            self.log("Automatic recovery is unavailable. Save explicitly to a writable project location.")
        self.update_title()

    @property
    def dirty(self):
        return self._dirty

    @dirty.setter
    def dirty(self, value):
        self._dirty = bool(value)
        if value:
            self.document_revision = getattr(self, "document_revision", 0) + 1

    def _recovery_owner(self):
        # Preserve the existing test/embedding hook for a custom local directory;
        # filenames are always generated by the ownership store, never shared.
        if self.recovery_store is None or self.recovery_path != self._recovery_current_path:
            if self.recovery_path.is_dir():
                raise OSError("Recovery destination is a directory; choose a file location")
            root = (self.recovery_path.parent / "recovery" if self.recovery_path != self._recovery_current_path
                    else self.recovery_path.parent)
            if self.recovery_store is not None:
                self.recovery_store.close()
            self.recovery_store = RecoveryStore(root)
            self._recovery_current_path = self.recovery_path
        return self.recovery_store

    def _cleanup_recovery(self, project=None, revision=None):
        if self.recovery_store is None:
            return
        try:
            errors = self.recovery_store.cleanup(document_id(project or self.project),
                                                 self.document_revision if revision is None else revision)
            if errors:
                self.log("Saved project, but recovery cleanup failed: " + "; ".join(errors))
        except Exception as error:
            self.log(f"Saved project, but recovery cleanup failed: {error}")

    def build_workspace(self):
        central = QWidget()
        layout = QVBoxLayout(central)
        layout.setSpacing(4)
        layout.setContentsMargins(0, 0, 0, 0)
        self.welcome = QWidget()
        welcome_layout = QVBoxLayout(self.welcome)
        heading = QLabel("Electrothermal Layout Studio")
        heading.setStyleSheet("font-size: 22px; font-weight: 600;")
        welcome_layout.addWidget(heading)
        hint = QLabel("Start with a layout, assign its physical stack, then define current paths and thermal boundaries.")
        hint.setWordWrap(True)
        welcome_layout.addWidget(hint)
        welcome_actions = QGridLayout()
        for index, (title, callback) in enumerate([("New project", self.new_project), ("Import layout", self.import_layout),
                                ("Open project", self.open_project), ("Try straight conductor", lambda: self.load_example("E1")),
                                ("Try GaN HEMT", lambda: self.load_example("E7"))]):
            button = QPushButton(title)
            button.clicked.connect(callback)
            welcome_actions.addWidget(button, index // 3, index % 3)
        welcome_layout.addLayout(welcome_actions)
        layout.addWidget(self.welcome)
        self.workflow_hint = QLabel()
        self.workflow_hint.setWordWrap(True)
        layout.addWidget(self.workflow_hint)
        inspection_bar = QHBoxLayout()
        self.inspection_label = QLabel()
        self.inspection_label.setWordWrap(True)
        inspection_bar.addWidget(self.inspection_label, 1)
        self.return_button = QPushButton("Return to current project")
        self.return_button.clicked.connect(self.return_to_project)
        inspection_bar.addWidget(self.return_button)
        self.inspection_bar = QWidget()
        self.inspection_bar.setLayout(inspection_bar)
        self.inspection_bar.hide()
        layout.addWidget(self.inspection_bar)
        self.view = LayoutView()
        self.view.setMinimumSize(360, 100)
        self.view.position_changed.connect(self.update_coordinates)
        self.view.region_drawn.connect(self.on_region_drawn)
        self.view.line_drawn.connect(self.on_line_drawn)
        self.view.point_probed.connect(self.probe)
        self.view.context_action.connect(self.selection_action)
        self.result_controls = QWidget()
        field_layout = QGridLayout(self.result_controls)
        field_layout.setContentsMargins(8, 2, 8, 2)
        field_layout.setVerticalSpacing(3)
        field_layout.setColumnStretch(1, 1)
        self.field_layer = QComboBox()
        self.field_layer.setMinimumContentsLength(18)
        self.field_layer.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
        self.field_layer.setAccessibleName("Result layer")
        self.field_layer.setToolTip("Choose a physical result layer, then all its solved regions or a specific region.")
        self.field_layer.currentIndexChanged.connect(self.populate_fields)
        layer_label = QLabel("Result &layer")
        layer_label.setBuddy(self.field_layer)
        field_layout.addWidget(layer_label, 0, 0)
        field_layout.addWidget(self.field_layer, 0, 1)
        self.fit_layer_button = QPushButton("Fit layer")
        self.fit_layer_button.setToolTip("Fit the mapped elements of the selected layer without changing the simulation region.")
        self.fit_layer_button.clicked.connect(self.fit_result_layer)
        self.field_combo = QComboBox()
        self.field_combo.setMinimumContentsLength(18)
        self.field_combo.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
        self.field_combo.setAccessibleName("Result field")
        self.field_combo.currentIndexChanged.connect(self.select_result_field)
        field_label = QLabel("Result &field")
        field_label.setBuddy(self.field_combo)
        field_layout.addWidget(field_label, 1, 0)
        field_layout.addWidget(self.field_combo, 1, 1)
        self.isolate_layer = QCheckBox("Isolate layer")
        self.isolate_layer.setChecked(True)
        self.isolate_layer.setToolTip("Hide other layers and their terminals while viewing a field. Geometry visibility settings and the physical model are preserved.")
        self.isolate_layer.toggled.connect(self.update_overlay)
        self.field_region = QComboBox()
        self.field_region.setMinimumContentsLength(16)
        self.field_region.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
        self.field_region.setAccessibleName("Result region")
        self.field_region.setToolTip("Restrict the map, probes, profiles, statistics and displayed-field exports to this solved region.")
        self.field_region.currentIndexChanged.connect(self.select_result_region)
        region_label = QLabel("Result &region")
        region_label.setBuddy(self.field_region)
        field_layout.addWidget(region_label, 2, 0)
        field_layout.addWidget(self.field_region, 2, 1)
        self.compare_layers_button = QPushButton("Compare layers…")
        self.compare_layers_button.clicked.connect(self.compare_layers_dialog)
        selection_actions = QHBoxLayout()
        for button in (self.fit_layer_button, self.isolate_layer, self.compare_layers_button):
            selection_actions.addWidget(button)
        field_layout.addLayout(selection_actions, 3, 0, 1, 2)
        self.view_mode = QComboBox()
        self.view_mode.addItem("Original result", "original")
        self.view_mode.addItem("Current − comparison", "difference")
        self.view_mode.setAccessibleName("Original or difference result")
        self.view_mode.currentIndexChanged.connect(self.select_view_mode)
        self.view_mode_label = QLabel("View &mode")
        self.view_mode_label.setBuddy(self.view_mode)
        field_layout.addWidget(self.view_mode_label, 4, 0)
        field_layout.addWidget(self.view_mode, 4, 1)
        self.view_mode.hide()
        self.view_mode_label.hide()
        self.field_hint = QLabel()
        self.field_hint.setWordWrap(True)
        self.field_hint.setAccessibleName("Result layer availability and display scope")
        # These bound controls are also reparented into the contextual result
        # form. Long labels/fonts must not force that form outside its viewport.
        for control in (self.field_layer, self.field_combo, self.field_region, self.view_mode):
            control.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Fixed)
        self.field_hint.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        field_layout.addWidget(self.field_hint, 5, 0, 1, 2)
        layout.addWidget(self.result_controls)
        self.time_navigation = TimeNavigation()
        self.time_navigation.snapshot_changed.connect(self.select_snapshot)
        layout.addWidget(self.time_navigation)
        layout.addWidget(self.view)
        legend_row = QHBoxLayout()
        self.legend_scale = FieldScaleControl()
        self.legend_scale.range_changed.connect(self.update_overlay)
        self.view.legend_details.connect(self.legend_scale.set_details)
        # Keep the quantitative key beside the map; editing its range belongs
        # with the other appearance controls, not permanently below the canvas.
        self.legend_scale.layout().removeWidget(self.legend_scale.color_bar)
        legend_row.addWidget(self.legend_scale.color_bar, 1)
        self.view.legend_details.connect(lambda details: self.legend_scale.color_bar.setVisible(bool(details)))
        self.legend = QLabel("Layout geometry · coordinates in µm")
        self.legend.setWordWrap(True)
        self.view.legend_changed.connect(self.legend.setText)
        self.display_tabs = QTabWidget()
        self.display_tabs.setAccessibleName("Display settings categories")
        self.display_tabs.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        self.display_tabs.setUsesScrollButtons(True)
        self.display_tabs.setElideMode(Qt.TextElideMode.ElideRight)
        appearance = QWidget()
        self.appearance_layout = QVBoxLayout(appearance)
        self.appearance_layout.setContentsMargins(10, 10, 10, 10)
        palette_row = QHBoxLayout()
        palette_label = QLabel("&Color")
        self.colormap_combo = QComboBox()
        self.colormap_combo.setAccessibleName("Field color map")
        self.colormap_combo.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Fixed)
        for key, label in COLORMAPS.items():
            self.colormap_combo.addItem("Coolwarm" if key == "coolwarm" else label, key)
        self.colormap_combo.setToolTip("Choose the field colormap. Coolwarm is diverging; automatic difference maps center it at zero.")
        self.colormap_combo.currentIndexChanged.connect(self.update_overlay)
        palette_label.setBuddy(self.colormap_combo)
        palette_row.addWidget(palette_label)
        palette_row.addWidget(self.colormap_combo, 1)
        self.smooth_field = QCheckBox("Smooth")
        self.smooth_field.setAccessibleName("Interpolate nodal field colors")
        self.smooth_field.setToolTip("Interpolate nodal potential and temperature. Element quantities remain flat.")
        self.smooth_field.toggled.connect(self.update_overlay)
        palette_row.addWidget(self.smooth_field)
        self.appearance_layout.addLayout(palette_row)
        self.appearance_layout.addWidget(self.legend_scale)
        self.history_range = QCheckBox("Use range across saved times")
        self.history_range.setAccessibleName("Automatic color range across saved transient times")
        self.history_range.setToolTip("Keep one automatic color scale for this layer, field and region through the saved history. Manual limits take precedence.")
        self.history_range.toggled.connect(self.history_range_changed)
        self.appearance_layout.addWidget(self.history_range)
        self.appearance_layout.addStretch()
        self.display_tabs.addTab(appearance, "Appearance")
        comparison = QWidget()
        comparison_layout = QVBoxLayout(comparison)
        comparison_hint = QLabel("Compare layers in this run or load a saved run to inspect changes on the same physical coordinates.")
        comparison_hint.setWordWrap(True)
        comparison_hint.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        comparison_layout.addWidget(comparison_hint)
        for label, callback in [("Compare layers…", self.compare_layers_dialog),
                                ("Compare saved maps…", self.compare_maps_dialog),
                                ("Load field difference…", self.field_difference_dialog)]:
            button = QPushButton(label)
            button.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Fixed)
            button.setToolTip(label)
            button.clicked.connect(callback)
            comparison_layout.addWidget(button)
        self.difference_time = TimeNavigation()
        self.difference_time.setAccessibleName("Comparison result time")
        self.difference_time.snapshot_changed.connect(self.update_overlay)
        self.difference_link_time = QCheckBox("Match current map time")
        self.difference_link_time.setToolTip("Use the nearest retained comparison time. The comparison time entry and export metadata show the actual matched time.")
        self.difference_link_time.setChecked(True)
        self.difference_link_time.toggled.connect(self.update_overlay)
        self.difference_link_time.hide()
        comparison_layout.addWidget(self.difference_link_time)
        comparison_layout.addWidget(self.difference_time)
        comparison_layout.addStretch()
        self.display_tabs.addTab(comparison, "Compare")
        self.legend.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        legend_row.addWidget(self.legend, 1)
        layout.addLayout(legend_row)
        self.selection_statistics = QLabel()
        self.selection_statistics.setWordWrap(True)
        self.selection_statistics.setAccessibleName("Selected field statistics")
        layout.addWidget(self.selection_statistics)
        self.setCentralWidget(central)

        design_tabs = QTabWidget()
        self.hierarchy = QTreeWidget()
        self.hierarchy.setHeaderLabels(["Cell / instance", "Extent (µm)"])
        self.hierarchy.itemDoubleClicked.connect(self.select_hierarchy)
        self.hierarchy.itemExpanded.connect(self.expand_instance)
        hierarchy_widget = QWidget()
        hierarchy_layout = QVBoxLayout(hierarchy_widget)
        hierarchy_hint = QLabel("Browse or frame a cell / instance. Use as simulation scope explicitly in Layout & region.")
        hierarchy_hint.setWordWrap(True)
        hierarchy_layout.addWidget(hierarchy_hint)
        hierarchy_layout.addWidget(self.hierarchy)
        design_tabs.addTab(hierarchy_widget, "Hierarchy")
        layer_widget = QWidget()
        layer_layout = QVBoxLayout(layer_widget)
        self.layer_filter = QLineEdit()
        self.layer_filter.setPlaceholderText("Filter masks / physical layers…")
        self.layer_filter.textChanged.connect(self.filter_layers)
        layer_layout.addWidget(self.layer_filter)
        self.layer_tree = QTreeWidget()
        self.layer_tree.setHeaderLabels(["Layer", "Material / mask", "Role"])
        self.layer_tree.itemChanged.connect(self.toggle_layer)
        self.layer_tree.itemClicked.connect(self.select_layer_from_tree)
        self.layer_tree.itemDoubleClicked.connect(self.edit_layer_from_tree)
        layer_hint = QLabel("Click a physical layer to inspect its results. Checkboxes show background geometry; double-click edits the layer.")
        layer_hint.setWordWrap(True)
        layer_layout.addWidget(layer_hint)
        layer_layout.addWidget(self.layer_tree)
        self.mask_toggle = QCheckBox("Display source masks")
        self.mask_toggle.toggled.connect(self.toggle_masks)
        layer_layout.addWidget(self.mask_toggle)
        design_tabs.addTab(layer_widget, "Layers")
        self.net_tree = QTreeWidget()
        self.net_tree.setHeaderLabels(["Net / component", "Terminals"])
        self.net_tree.itemDoubleClicked.connect(self.locate_net)
        design_tabs.addTab(self.net_tree, "Nets")
        self.design_dock = self.add_dock("Design browser", design_tabs, Qt.DockWidgetArea.LeftDockWidgetArea)

        self.config_tabs = ConfigurationNavigation()
        self.panels = {}
        definitions = [
            ("layers", "Stack", ["ID", "Name", "Material", "z (µm)", "t (µm)", "Role"]),
            ("terminals", "Terminals", ["ID", "Layer", "Condition", "Value (SI)", "Geometry"]),
            ("interfaces", "Interfaces", ["ID", "Layer A", "Layer B", "Electrical", "Thermal"]),
            ("thermal_boundaries", "Thermal BC", ["ID", "Layer", "Condition", "Value (SI)", "Ambient (K)"]),
            ("heat_sources", "Heat sources", ["ID", "Layer", "Kind", "Value (SI)"]),
            ("refinements", "Refinement", ["ID", "Size (µm)", "Geometry"]),
        ]
        for key, title, columns in definitions:
            panel = ObjectPanel(title, columns,
                                lambda checked=False, group=key: self.add_object(group),
                                lambda group=key: self.edit_object(group),
                                lambda checked=False, group=key: self.remove_object(group), self,
                                extra=[("↑", lambda checked=False, group=key: self.reorder_object(group, -1)),
                                       ("↓", lambda checked=False, group=key: self.reorder_object(group, 1))] if key == "layers" else ())
            self.panels[key] = panel
            self.config_tabs.addTab(panel, title)
        self.material_panel = ObjectPanel("Materials", ["ID", "Name", "Type", "Scope"], self.add_material,
                                         self.edit_material, self.remove_material, self,
                                         extra=[("Copy", self.copy_material), ("Library…", self.material_library)])
        self.config_tabs.addTab(self.material_panel, "Materials")
        settings_widget = QWidget()
        settings_layout = QVBoxLayout(settings_widget)
        for label, callback in [("Mesh settings…", lambda: self.edit_settings("mesh")),
                                ("Solver settings…", lambda: self.edit_settings("solver")),
                                ("Maximum operating temperature…", self.edit_temperature_limits),
                                ("Engineering warning thresholds…", self.edit_thresholds),
                                ("Save simulation preset…", self.save_preset),
                                ("Load simulation preset…", self.load_preset),
                                ("Project notes…", self.edit_notes),
                                ("Advanced project JSON…", self.edit_advanced)]:
            button = QPushButton(label)
            button.clicked.connect(callback)
            settings_layout.addWidget(button)
        self.settings_summary = QLabel()
        self.settings_summary.setWordWrap(True)
        settings_layout.addWidget(self.settings_summary)
        settings_layout.addStretch()
        settings_scroll = QScrollArea()
        settings_scroll.setWidgetResizable(True)
        settings_scroll.setWidget(settings_widget)
        self.config_tabs.addTab(settings_scroll, "Settings")
        self.config_dock = self.add_dock("Technology & conditions", self.config_tabs, Qt.DockWidgetArea.RightDockWidgetArea)
        self.display_dock = self.add_dock("Display settings", self.display_tabs, Qt.DockWidgetArea.RightDockWidgetArea)
        self.tabifyDockWidget(self.config_dock, self.display_dock)

        self.analysis_tabs = QTabWidget()
        self.analysis_tabs.setDocumentMode(True)
        results_widget = self.results_page = QWidget()
        results_layout = QVBoxLayout(results_widget)
        self.result_status = QLabel("No simulation result. Configure the stack, place terminals and generate a mesh.")
        self.result_status.setWordWrap(True)
        results_layout.addWidget(self.result_status)
        self.key_metrics = QLabel()
        self.key_metrics.setWordWrap(True)
        self.key_metrics.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        results_layout.addWidget(self.key_metrics)
        results_splitter = QSplitter(Qt.Orientation.Horizontal)
        self.metrics_table = QTableWidget(0, 2)
        self.metrics_table.setHorizontalHeaderLabels(["Metric", "Value with unit"])
        self.terminals_table = QTableWidget()
        for table in (self.metrics_table, self.terminals_table):
            table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
            table.setShowGrid(False)
            table.horizontalHeader().setStretchLastSection(True)
            results_splitter.addWidget(table)
        results_layout.addWidget(results_splitter)
        self.analysis_tabs.addTab(results_widget, "Results")
        self.profiles = ProfilePanel()
        self.analysis_tabs.addTab(self.profiles, "Line profiles")
        self.cross_section = CrossSectionPanel()
        self.analysis_tabs.addTab(self.cross_section, "Cross-section")
        self.study_panel = StudyPanel()
        self.study_panel.result_selected.connect(self.inspect_result)
        self.study_panel.compare_requested.connect(self.compare_maps_dialog)
        self.analysis_tabs.addTab(self.study_panel, "Studies")
        self.history_panel = RunHistoryPanel()
        self.history_panel.cancel_requested.connect(self.history_workflow.cancel)
        self.history_panel.cancel_requested.connect(self.cancel_catalog)
        self.history_panel.inspect_requested.connect(self.inspect_result)
        self.history_panel.compare_requested.connect(self.compare_history)
        self.history_panel.remove_requested.connect(self.remove_history)
        self.analysis_tabs.addTab(self.history_panel, "Run history")
        self.time_history = TimeHistoryPanel()
        self.time_history.scan_requested.connect(self.history_workflow.full)
        self.time_history.time_selected.connect(self.time_navigation.select_time)
        self.display_tabs.addTab(self.time_history, "Time history")
        self.diagnostics = QTreeWidget()
        self.diagnostics.setHeaderLabels(["Level", "Object", "Diagnostic · double-click to locate"])
        self.diagnostics.itemDoubleClicked.connect(self.locate_diagnostic)
        self.analysis_tabs.addTab(self.diagnostics, "Validation")
        self.console = QTextEdit()
        self.console.setReadOnly(True)
        self.analysis_tabs.addTab(self.console, "Console")
        self.analysis_dock = self.add_dock("Analysis", self.analysis_tabs, Qt.DockWidgetArea.BottomDockWidgetArea)
        self.resizeDocks([self.design_dock, self.config_dock], [300, 440], Qt.Orientation.Horizontal)
        self.resizeDocks([self.analysis_dock], [280], Qt.Orientation.Vertical)
        self.coordinates = QLabel("x — µm · y — µm")
        self.statusBar().addPermanentWidget(self.coordinates)
        self.progress = QProgressBar()
        self.progress.setFixedWidth(230)
        self.progress.setRange(0, 100)
        self.progress.hide()
        self.statusBar().addPermanentWidget(self.progress)
        self.recovery_status = QLabel()
        self.recovery_status.setWordWrap(True)
        self.statusBar().addPermanentWidget(self.recovery_status)
        self.save_progress = QProgressBar()
        self.save_progress.setMaximumWidth(160)
        self.save_progress.hide()
        self.statusBar().addPermanentWidget(self.save_progress)
        self.cancel_save_button = QPushButton("Cancel save")
        self.cancel_save_button.clicked.connect(self.cancel_saving)
        self.cancel_save_button.hide()
        self.statusBar().addPermanentWidget(self.cancel_save_button)

    def add_dock(self, title, widget, area):
        dock = QDockWidget(title, self)
        dock.setObjectName(title.replace(" ", "_"))
        dock.setWidget(widget)
        self.addDockWidget(area, dock)
        return dock

    def action(self, title, callback, shortcut=None, checkable=False):
        action = QAction(title, self)
        action.setCheckable(checkable)
        action.triggered.connect(callback)
        if shortcut:
            action.setShortcut(shortcut)
        icon = None
        for prefix, candidate in [("Open", QStyle.StandardPixmap.SP_DialogOpenButton),
                                  ("Import", QStyle.StandardPixmap.SP_DialogOpenButton),
                                  ("Save", QStyle.StandardPixmap.SP_DialogSaveButton),
                                  ("Run", QStyle.StandardPixmap.SP_MediaPlay),
                                  ("Cancel", QStyle.StandardPixmap.SP_MediaStop),
                                  ("Validate", QStyle.StandardPixmap.SP_DialogApplyButton)]:
            if title.startswith(prefix):
                icon = candidate
                break
        if icon is not None:
            action.setIcon(self.style().standardIcon(icon))
        return action

    def build_actions(self):
        files = self.menuBar().addMenu("&File")
        for title, callback, shortcut in [
            ("New project…", self.new_project, QKeySequence.StandardKey.New),
            ("Open project…", self.open_project, QKeySequence.StandardKey.Open),
            ("Import layout…", self.import_layout, "Ctrl+I"),
            ("Save project", self.save, QKeySequence.StandardKey.Save),
            ("Save project as…", self.save_as, QKeySequence.StandardKey.SaveAs),
            ("Save content-addressed copy…", lambda: self.save_as(archive_format=3), None),
            ("Save legacy-compatible copy…", lambda: self.save_as(archive_format=2), None),
            ("Duplicate project…", self.duplicate_project, None),
            ("Relink layout…", self.relink_layout, None),
            ("Export / share results…", lambda: self.share_export(), None),
            ("Export complete numerical bundle (confidential)…", self.export_results, None),
            ("Export displayed field / region…", self.export_displayed_field, None),
            ("Export map…", self.export_map, None),
            ("Export report…", self.export_report, None),
        ]:
            files.addAction(self.action(title, callback, shortcut))
        files.addSeparator()
        examples = files.addMenu("Ready-to-run examples")
        from etlayout.example_catalog import example_info
        from etlayout.examples import EXAMPLE_NAMES
        categories = {}
        for name in EXAMPLE_NAMES:
            info = example_info(name)
            if info.category not in categories:
                categories[info.category] = examples.addMenu(info.category)
            label = f"{info.id} · {info.title}"
            action = self.action(label, lambda checked=False, example=name: self.load_example(example))
            detail = "\n" + info.physical_scope
            if info.family:
                from etlayout.power_examples import reference_summary
                detail = "\n" + reference_summary(name) + detail
            action.setToolTip(info.learning_goal + detail + "\nEvidence: " + info.evidence_level
                + "; material seeds are uncalibrated; a converged solve is not mesh verification.")
            categories[info.category].addAction(action)
        files.addAction(self.action("Choose example operating scenario…", self.choose_example_scenario))
        files.addSeparator()
        files.addAction(self.action("Review project resources…", self.review_project_resources))
        files.addAction(self.action("Exit", self.close, "Alt+F4"))
        # Only shared actions are created here. GuidedWorkspace owns the menus;
        # there is no second set of hidden Geometry/Simulation/View actions.
        self.undo_action = self.action("Undo", self.undo, QKeySequence.StandardKey.Undo)
        self.redo_action = self.action("Redo", self.redo, QKeySequence.StandardKey.Redo)
        self.validate_action = self.action("Validate geometry & connectivity", self.validate_setup, "F6")
        self.mesh_action = self.action("Generate mesh", lambda: self.start_job("mesh"), "F7")
        self.run_action = self.action("Run analysis", lambda: self.start_job("solve"), "F5")
        self.cancel_action = self.action("Cancel computation", self.cancel_job, "Shift+F5")
        self.cancel_action.setEnabled(False)
        self.power_action = self.action("Routing & optimization", self.open_power_workbench)
        self.dark_action = self.action("Dark theme", self.apply_theme, checkable=True)
        self.execution_action = self.action("Execution settings…", self.edit_execution_settings)
        self.history_settings_action = self.action("Archive and history settings…", self.edit_history_settings)
        self.acknowledge_action = self.action("Acknowledge reviewed clipped boundaries", self.acknowledge_boundaries, checkable=True)
        self.workspace_actions = [self.action(title, callback) for title, callback in (
            ("Save workspace layout…", self.save_workspace_layout),
            ("Restore workspace layout…", self.restore_workspace_layout),
            ("Reset workspace layout", self.reset_workspace_layout))]
        help_menu = self.menuBar().addMenu("&Help")
        help_menu.addAction(self.action("Workflow & model limits", self.help_dialog))
        help_menu.addAction(self.action("Open user guide", self.open_user_guide))
        help_menu.addAction(self.action("About Electrothermal Layout Studio", lambda: QMessageBox.about(
            self, "Electrothermal Layout Studio", f"Electrothermal Layout Studio {__version__}\nNative 2.5D finite-element electrical and thermal analysis.\nCopyright 2026 mmensing; third-party copyrights retained.\nIncludes LGPL-covered libraries with their original grants and exceptions:\nGEOS C API: Copyright (C) 2010 2011 Sandro Santilli; (C) 2005 Refractions Research Inc.\nANN: Copyright (C) 1997–2010 University of Maryland and Sunil Arya and David Mount.\nPthreads-win32: Copyright (C) 1998 John E. Bossom; (C) 1999,2006 Pthreads-win32 contributors.\nGmm: Copyright (C) 2002–2008 Yves Renard.\nMathEx is based in part on SSCILIB: Copyright (C) 2000–2003 Sadao Massago.\nComplete LGPL texts and native notices: licenses/license-texts/; source/relink instructions accompany releases.\nOriginal application source: MPL-2.0.\nWindows combined distribution: GPL-3.0-only (MPL section 3.3).\nSource/build information and third-party notices accompany releases.\nNo warranty; copying, modification and redistribution are permitted under the supplied licenses.\nSee Help → Open user guide; full GPL terms: licenses/license-texts/GPL-3.0.txt.\nRepresentative material data require process calibration.")))

        self.build_workflow_ribbon()

    def open_power_workbench(self):
        self.guided.open_studies()

    def edit_execution_settings(self):
        from etlayout.gui.execution_settings import ExecutionSettingsDialog
        dialog = ExecutionSettingsDialog(self.execution_settings, self)
        if dialog.exec():
            self.execution_settings = dialog.settings
            self.settings.setValue("execution", json.dumps(asdict(self.execution_settings)))
            self.statusBar().showMessage("Execution settings saved for subsequent jobs on this computer.", 6000)

    def edit_history_settings(self):
        from etlayout.gui.history_loading import HistorySettingsDialog
        dialog = HistorySettingsDialog(self.history_lazy_enabled, self.history_cache_mib, self)
        if dialog.exec():
            self.history_lazy_enabled = dialog.enabled.isChecked()
            self.history_cache_mib = dialog.cache_mib.value()
            self.settings.setValue("history/lazy", self.history_lazy_enabled)
            self.settings.setValue("history/cache_mib", self.history_cache_mib)
            if self.history_workflow.store:
                self.history_workflow.store.set_budget(self.history_cache_mib * 1024**2)
                self.history_workflow.refresh()
            self.statusBar().showMessage("History settings saved on this computer; opening mode applies to the next project.", 6000)

    def build_workflow_ribbon(self):
        from etlayout.gui.guided import GuidedWorkspace
        self.guided = GuidedWorkspace(self)
        self.guided.start()

    def show_display_settings(self):
        if not self.guided.navigate("results"):
            return
        self.display_tabs.setCurrentIndex(0)
        self.colormap_combo.setFocus()

    def show_configuration_page(self, key):
        stage = {"layers": "layers", "terminals": "connections", "interfaces": "connections",
                 "thermal_boundaries": "conditions", "heat_sources": "conditions", "refinements": "mesh"}[key]
        self.guided.navigate(stage)

    def show_analysis_page(self, widget):
        if widget is self.diagnostics:
            self.guided.open_drawer(0)
        elif widget is self.console:
            self.guided.open_drawer(2)
        elif widget is self.history_panel:
            self.guided.open_drawer(3)
        else:
            self.guided.show_page(widget, "Time history" if widget is self.time_history else "Study results" if widget is self.study_panel else "Analysis")

    def update_title(self):
        self.setWindowTitle(f"{'Read-only run · ' if self.inspection_context else '● ' if self.dirty else ''}{self.project.name} — Electrothermal Layout Studio")
        self.undo_action.setEnabled(bool(self.undo_history) and self.job is None and self.inspection_context is None)
        self.redo_action.setEnabled(bool(self.redo_history) and self.job is None and self.inspection_context is None)
        if hasattr(self, "guided"):
            self.guided.refresh()

    def log(self, message):
        self.console.append(str(message))
        LOG.info(message)
        self.statusBar().showMessage(str(message).splitlines()[0], 10000)

    def error(self, title, error):
        self.log(f"{title}: {error}")
        QMessageBox.warning(self, title, str(error))

    def model_locked(self):
        power = getattr(getattr(self, "guided", None), "power", None)
        editor = getattr(getattr(self, "guided", None), "layout_editor", None)
        return bool(self.job or self.inspection_context or (power and power.job) or (editor and editor.job))

    def before_edit(self):
        editor = getattr(getattr(self, "guided", None), "layout_editor", None)
        if editor and editor.dirty:
            raise ValueError("Apply or discard pending layout vertices before changing the project.")
        if self.model_locked():
            raise ValueError("Return to the working setup and finish the active job before editing.")
        from etlayout.simulation.runner import _geometry_key, _mesh_key
        self._before_geometry = _geometry_key(self.project)
        self._before_mesh = _mesh_key(self.project)
        self._before_physics = physics_hash(self.project)
        self.undo_history.append(deepcopy(self.project))
        self.undo_history = self.undo_history[-50:]
        self.redo_history.clear()

    def changed(self, geometry=True):
        from etlayout.simulation.runner import _geometry_key, _mesh_key
        old_mesh = self.mesh
        geometry_changed = getattr(self, "_before_geometry", "") != _geometry_key(self.project)
        mesh_changed = getattr(self, "_before_mesh", "") != _mesh_key(self.project)
        self.dirty = True
        physical_change = self._before_physics != physics_hash(self.project)
        self._before_physics = None
        if physical_change:
            self.history_workflow.new_result()
            self.clear_analysis("Project changed. Run analysis to compute fields for this configuration.", clear_studies=True)
        if not mesh_changed:
            self.mesh = old_mesh
        self.refresh_project(preserve_view=True, refresh_geometry=geometry and physical_change and geometry_changed)
        if not physical_change:
            self.refresh_view(True)
            self.update_overlay()
            if self.result:
                self.render_result_metrics(self.view_source_result())
        self.update_title()

    def undo(self):
        if hasattr(self, "guided") and not self.guided.layout_editor.finish():
            return
        if self.model_locked():
            return
        if not self.undo_history or self.job:
            return
        self.redo_history.append(deepcopy(self.project))
        from etlayout.simulation.runner import _geometry_key, _mesh_key
        self._before_geometry, self._before_mesh = _geometry_key(self.project), _mesh_key(self.project)
        self._before_physics = physics_hash(self.project)
        self.project = self.undo_history.pop()
        self.changed()

    def redo(self):
        if hasattr(self, "guided") and not self.guided.layout_editor.finish():
            return
        if self.model_locked():
            return
        if not self.redo_history or self.job:
            return
        self.undo_history.append(deepcopy(self.project))
        from etlayout.simulation.runner import _geometry_key, _mesh_key
        self._before_geometry, self._before_mesh = _geometry_key(self.project), _mesh_key(self.project)
        self._before_physics = physics_hash(self.project)
        self.project = self.redo_history.pop()
        self.changed()

    def choices(self):
        layer_ids = [layer.id for layer in self.project.layers]
        return {"material_id": sorted(self.project.materials), "layer_id": layer_ids,
                "layer_a": layer_ids, "layer_b": layer_ids, "heat_layer_id": {"": "Endpoint partition", **{key: key for key in layer_ids}}}

    def mask_keys(self):
        return [layer["key"] for layer in self.document.layers] if self.document else list(self.masks)

    def refresh_project(self, preserve_view=False, refresh_geometry=True):
        self._refreshing = True
        try:
            self.thermal_joule.setChecked(self.project.solver.thermal_include_joule)
            self.update_heating_control()
            self.acknowledge_action.setChecked(self.project.acknowledge_clipped_boundaries)
            if refresh_geometry:
                self.schedule_geometry(preserve_view)
            self.refresh_tables()
            self.refresh_layer_tree()
            old_layer = self.field_layer.currentData()
            self.field_layer.blockSignals(True)
            self.field_layer.clear()
            for layer in self.project.layers:
                self.field_layer.addItem(f"{layer.name} [{layer.id}]", layer.id)
                self.field_layer.setItemData(self.field_layer.count() - 1,
                    f"{layer.name} [{layer.id}] · {layer.role}\nz = {layer.z * 1e6:g} µm · thickness = {layer.thickness * 1e6:g} µm",
                    Qt.ItemDataRole.ToolTipRole)
            if old_layer:
                index = self.field_layer.findData(old_layer)
                if index >= 0:
                    self.field_layer.setCurrentIndex(index)
            self.field_layer.blockSignals(False)
            self.populate_fields()
            self.settings_summary.setText(
                f"Mesh: {self.project.mesh.min_size * 1e6:g}–{self.project.mesh.max_size * 1e6:g} µm\n"
                f"Contacts: {self.project.mesh.contact_size * 1e6:g} µm\n"
                f"Budget: {self.project.mesh.max_elements:,} elements / {self.project.mesh.max_dofs:,} DOFs\n\n"
                f"Analysis: {self.project.solver.mode}\nInitial temperature: {self.project.solver.initial_temperature:g} K\n"
                f"Linear solver: {self.project.solver.linear_solver}\nFloating nets: {self.project.solver.floating_policy}")
        finally:
            self._refreshing = False
        self.update_overlay()
        self.update_workflow()
        self.update_title()
        if hasattr(self, "guided"):
            self.guided.refresh(rebuild=True)

    def refresh_view(self, preserve=False):
        if hasattr(self, "guided") and self.guided.layout_editor.active:
            return
        if self._show_masks or self.geometry is None:
            self.view.set_geometries(self.masks, preserve_view=preserve)
        else:
            self.view.set_geometries(self.geometry.layers, self.project.layers,
                                     self.geometry.terminals, preserve_view=preserve)
            if self.project.roi:
                self.view.show_region(self.geometry.domain, "Simulation ROI")
        if self.project.display_roi:
            try:
                display = resolve_region(self.project.display_roi, self.masks,
                                         self.geometry.domain if self.geometry else None)
                self.view.show_region(display, "Displayed ROI", "#777C8F")
                x0, y0, x1, y1 = display.bounds
                from PySide6.QtCore import QRectF
                self.view.fitInView(QRectF(x0 * 1e6, -y1 * 1e6, (x1 - x0) * 1e6, (y1 - y0) * 1e6), Qt.AspectRatioMode.KeepAspectRatio)
            except Exception as error:
                self.log(f"Display ROI: {error}")

    def clear_analysis(self, message="No result for this project.", clear_studies=False):
        self.result = None
        self.transient_frame = None
        self.time_navigation.set_result(None)
        self.time_history.set_result(None)
        self.mesh = None
        self.view.show_mesh(None, False)
        self.sampler = None
        self.display_result = None
        self.display_sampler = None
        self.difference_source = None
        self.difference_name = ""
        self.difference_time.set_result(None)
        self.difference_link_time.hide()
        self._difference_cache.clear()
        self._pending_view_state = None
        self.view_mode.blockSignals(True)
        self.view_mode.setCurrentIndex(0)
        self.view_mode.blockSignals(False)
        self.view_mode.hide()
        self.view_mode_label.hide()
        self.selection_statistics.clear()
        self.net_report = None
        self.net_tree.clear()
        self.diagnostics.clear()
        self.analysis_tabs.setTabText(self.analysis_tabs.indexOf(self.diagnostics), "Validation")
        self.metrics_table.setRowCount(0)
        self.terminals_table.setRowCount(0)
        self.key_metrics.clear()
        self._original_key_metrics = ""
        self.result_status.setText(message)
        self.cross_section.plot.clear()
        self.cross_section.intervals = {}
        self.last_cut = None
        self.view.show_field(None, "", "", False, False)
        self.view.set_layer_focus("")
        self.legend_scale.hide()
        self.fit_layer_button.setEnabled(False)
        self.field_hint.setText(message)
        if clear_studies:
            self.studies = []
            self.study_panel.set_rows([])

    def cancel_geometry(self):
        self._pending_compute = None
        self.geometry_generation += 1
        self.hierarchy_queue.clear()
        job, self.prepare_job = self.prepare_job, None
        if job:
            job.cancel()
        for job in self.hierarchy_jobs:
            job.cancel()
        self.hierarchy_jobs.clear()

    def schedule_geometry(self, preserve=False):
        """Parsing, clipping, Boolean geometry and preparation never run on Qt."""
        self.cancel_geometry()
        self.geometry = None
        self.document = None
        self.masks = {}
        self.hierarchy.clear()
        self.view.set_geometries({}, preserve_view=preserve)
        if getattr(self.project, "_resource_unresolved", False):
            self.workflow_hint.setText("Project resources are unresolved. Review project resources before geometry or simulation.")
            return
        if not self.project.layers and not self.project.layout.path:
            return
        generation = self.geometry_generation
        job = ComputeJob(deepcopy(self.project), "prepare", parent=self, execution=self.execution_settings)
        self.prepare_job = job
        job.finished.connect(lambda output: self.geometry_ready(job, generation, output, preserve))
        job.failed.connect(lambda message, detail: self.geometry_failed(job, generation, message, detail))
        job.progress.connect(lambda fraction, message: self.statusBar().showMessage(message) if job is self.prepare_job else None)
        self.workflow_hint.setText("Preparing layout in a background process. You can cancel or continue editing; superseded work is discarded.")
        self.cancel_action.setEnabled(True)
        try:
            job.start()
        except Exception as error:
            job.cleanup()
            self.geometry_failed(job, generation, str(error))

    def geometry_ready(self, job, generation, output, preserve):
        if job is not self.prepare_job or generation != self.geometry_generation:
            return
        metadata = output["document"]
        if (metadata and self.project.layout.sha256
                and metadata["source_sha256"] != self.project.layout.sha256):
            self.geometry_failed(job, generation, "Layout content changed. Reimport or relink before running.")
            return
        self.prepare_job = None
        self.geometry = output["geometry"]
        if hasattr(self, "guided") and self.guided.last_error_stage == "layout":
            self.guided.last_error = ""
        self.masks = output["masks"]
        self.document = SimpleNamespace(**output["document"]) if output["document"] else None
        if self.document and not self.project.layout.sha256:
            self.project.layout.sha256 = self.document.source_sha256
        self.refresh_view(preserve)
        self.refresh_hierarchy()
        self.refresh_layer_tree()
        self.update_overlay()
        if self._pending_view_state is not None:
            self.restore_viewport(self._pending_view_state)
            self._pending_view_state = None
        self.cancel_action.setEnabled(self.job is not None)
        self.update_workflow()
        if hasattr(self, "guided"):
            self.guided.refresh(rebuild=True)
            if self.guided.pending_region is not None and self.guided.selection.requested is not None:
                self.guided.preview_region("refresh", geometry_region(self.guided.selection.requested),
                                           purpose=self.guided.pending_purpose)
        pending, self._pending_compute = self._pending_compute, None
        if pending is not None:
            self.start_job(*pending)
        if hasattr(self, "guided"):
            self.guided.layout_editor.geometry_ready()

    def geometry_failed(self, job, generation, message, detail=""):
        if job is not self.prepare_job or generation != self.geometry_generation:
            return
        self.prepare_job = None
        self._pending_compute = None
        self.cancel_action.setEnabled(self.job is not None)
        self.workflow_hint.setText("Geometry needs attention: " + message)
        self.log("Geometry preparation failed: " + message)
        if detail:
            self.log(detail)
        if hasattr(self, "guided"):
            self.guided.job_failed("Geometry preparation failed: " + message, stage="layout", detail=detail)

    def update_workflow(self):
        if hasattr(self, "guided"):
            self.welcome.hide()
            self.workflow_hint.hide()
            self.guided.refresh()
            return
        self.welcome.setVisible(not self.project.layers and not self.inspection_context)
        self.workflow_hint.setVisible(self.result is None or self.prepare_job is not None)
        if self.prepare_job:
            return
        project = self.project
        layout = bool(project.layout.path or any(layer.geometry.polygons for layer in project.layers))
        stack = bool(project.layers)
        electrical = any(t.kind in {"voltage", "ground", "current", "density"} for t in project.terminals)
        thermal = any(boundary.kind == "temperature" or
                      boundary.kind in {"coefficient", "resistance_area", "resistance"} and boundary.value > 0
                      for boundary in project.thermal_boundaries)
        checks = [("Layout", layout), ("Physical stack", stack)]
        if project.solver.mode != "thermal" or project.solver.thermal_include_joule:
            checks.append(("Electrical terminals", electrical))
        if project.solver.mode != "electrical":
            checks.append(("Thermal boundaries" if thermal else "Adiabatic transient" if project.transient.enabled
                           else "Thermal sink", thermal or project.transient.enabled))
        self.workflow_hint.setText("  ·  ".join(f"{'✓' if ready else '○'} {name}" for name, ready in checks)
                                   + "  ·  Validate before solving; review all reported warnings.")

    def refresh_hierarchy(self):
        self.hierarchy.clear()
        if not self.document:
            return
        for cell in self.document.cells:
            bounds = self.document.cell_bounds[cell]
            item = QTreeWidgetItem([cell, f"{(bounds[2] - bounds[0]) * 1e6:g} × {(bounds[3] - bounds[1]) * 1e6:g}"])
            item.setData(0, Qt.ItemDataRole.UserRole, (cell, []))
            item.setData(1, Qt.ItemDataRole.UserRole, False)
            item.setData(0, Qt.ItemDataRole.UserRole + 1, bounds)
            item.setChildIndicatorPolicy(QTreeWidgetItem.ChildIndicatorPolicy.ShowIndicator)
            self.hierarchy.addTopLevelItem(item)
        self.hierarchy.resizeColumnToContents(0)

    def expand_instance(self, item):
        payload = item.data(0, Qt.ItemDataRole.UserRole)
        if not payload or item.data(1, Qt.ItemDataRole.UserRole):
            return
        item.setData(1, Qt.ItemDataRole.UserRole, True)
        self.load_hierarchy_page(item, 0)

    def load_hierarchy_page(self, item, offset):
        if self.job or self.prepare_job or self.hierarchy_jobs:
            self.hierarchy_queue.append((item, offset, self.geometry_generation))
            return
        cell, prefix = item.data(0, Qt.ItemDataRole.UserRole)
        generation = self.geometry_generation
        argument = {"cell": cell, "instance_path": prefix, "offset": offset, "limit": 100}
        job = ComputeJob(deepcopy(self.project), "hierarchy", argument, self, execution=self.execution_settings)
        self.hierarchy_jobs.append(job)
        loading = QTreeWidgetItem(["Loading instances…", ""])
        item.addChild(loading)
        def ready(page):
            if job in self.hierarchy_jobs:
                self.hierarchy_jobs.remove(job)
            if generation != self.geometry_generation:
                return
            item.removeChild(loading)
            for instance in page["items"]:
                child = QTreeWidgetItem([instance["label"], instance["cell"]])
                child.setData(0, Qt.ItemDataRole.UserRole, (cell, instance["path"]))
                child.setData(1, Qt.ItemDataRole.UserRole, False)
                child.setData(0, Qt.ItemDataRole.UserRole + 1, instance["bounds"])
                child.setChildIndicatorPolicy(QTreeWidgetItem.ChildIndicatorPolicy.ShowIndicator)
                item.addChild(child)
            if page["has_more"]:
                more = QTreeWidgetItem([f"Load next 100… ({offset + len(page['items']):,} / {page['total']:,})", "Double-click"])
                more.setData(0, Qt.ItemDataRole.UserRole, ("__page__", offset + len(page["items"])))
                item.addChild(more)
            if not page["total"]:
                item.setChildIndicatorPolicy(QTreeWidgetItem.ChildIndicatorPolicy.DontShowIndicator)
            self.drain_hierarchy_queue()
        def failed(message, detail):
            if job in self.hierarchy_jobs:
                self.hierarchy_jobs.remove(job)
            if generation == self.geometry_generation:
                item.removeChild(loading)
                item.setData(1, Qt.ItemDataRole.UserRole, False)
                self.log("Cannot load hierarchy page: " + message)
            self.drain_hierarchy_queue()
        job.finished.connect(ready)
        job.failed.connect(failed)
        try:
            job.start()
        except Exception as error:
            job.cleanup()
            failed(str(error), "")

    def drain_hierarchy_queue(self):
        while self.hierarchy_queue and not self.job and not self.prepare_job and not self.hierarchy_jobs:
            item, offset, generation = self.hierarchy_queue.pop(0)
            if generation == self.geometry_generation:
                self.load_hierarchy_page(item, offset)

    def select_hierarchy(self, item, column):
        """Browse/frame only. Scope changes require the explicit inspector action."""
        data = item.data(0, Qt.ItemDataRole.UserRole)
        if data and data[0] == "__page__":
            parent = item.parent()
            parent.removeChild(item)
            self.load_hierarchy_page(parent, data[1])
            return
        if data:
            bounds = item.data(0, Qt.ItemDataRole.UserRole + 1)
            if not isinstance(bounds, (tuple, list)) or len(bounds) != 4:
                bounds = None
            if bounds is None and self.document:
                bounds = self.document.cell_bounds.get(data[0])
            if bounds:
                self.guided.frame_bounds(bounds)
            self.log(f"Browsing {data[0]} / {data[1] or '(top)'}. Use as simulation scope explicitly to change the physical model.")

    def refresh_layer_tree(self):
        self.layer_tree.blockSignals(True)
        self.layer_tree.clear()
        if self._show_masks:
            for key in self.mask_keys():
                item = QTreeWidgetItem([key, "Source mask", "layout"])
                item.setData(0, Qt.ItemDataRole.UserRole, key)
                item.setCheckState(0, Qt.CheckState.Checked)
                self.layer_tree.addTopLevelItem(item)
        else:
            for layer in self.project.layers:
                item = QTreeWidgetItem([layer.name, layer.material_id, layer.role])
                item.setData(0, Qt.ItemDataRole.UserRole, layer.id)
                item.setCheckState(0, Qt.CheckState.Checked if layer.visible else Qt.CheckState.Unchecked)
                item.setData(0, Qt.ItemDataRole.DecorationRole, QColor(layer.color))
                item.setToolTip(0, f"{layer.id}\n{region_summary(layer.geometry)}\nOpacity {layer.opacity:g}")
                self.layer_tree.addTopLevelItem(item)
        self.layer_tree.blockSignals(False)
        self.layer_tree.resizeColumnToContents(0)
        self.filter_layers(self.layer_filter.text())

    def filter_layers(self, text):
        for i in range(self.layer_tree.topLevelItemCount()):
            item = self.layer_tree.topLevelItem(i)
            item.setHidden(text.lower() not in " ".join(item.text(c) for c in range(3)).lower())

    def toggle_layer(self, item, column):
        if self.model_locked():
            return
        key = item.data(0, Qt.ItemDataRole.UserRole)
        visible = item.checkState(0) == Qt.CheckState.Checked
        if not self._show_masks:
            self.before_edit()
            self.project.layer(key).visible = visible
            self.dirty = True
            self.update_title()
        self.view.set_layer_visible(key, visible)
        if self.last_cut and self.geometry:
            self.cross_section.show_cut(self.project, self.geometry, *self.last_cut)

    def edit_layer_from_tree(self, item, column):
        if self._show_masks:
            return
        key = item.data(0, Qt.ItemDataRole.UserRole)
        self.edit_object("layers", key)

    def select_layer_from_tree(self, item, column=0):
        if self._show_masks:
            return
        index = self.field_layer.findData(item.data(0, Qt.ItemDataRole.UserRole))
        if index >= 0:
            self.field_layer.setCurrentIndex(index)

    def toggle_masks(self, checked):
        self._show_masks = checked
        self.refresh_layer_tree()
        self.refresh_view(True)
        self.update_overlay()

    def refresh_tables(self):
        self.panels["layers"].set_rows([[x.id, x.name, x.material_id, f"{x.z * 1e6:.5g}", f"{x.thickness * 1e6:.5g}", x.role] for x in self.project.layers])
        self.panels["terminals"].set_rows([[x.id, x.layer_id, x.kind,
                                          engineering(x.value, {"voltage": "V", "gate": "V", "current": "A", "density": "A/m²"}.get(x.kind, "")),
                                          region_summary(x.geometry)] for x in self.project.terminals])
        self.panels["interfaces"].set_rows([[x.id, x.layer_a, x.layer_b, x.electrical_kind, x.thermal_kind] for x in self.project.interfaces])
        self.panels["thermal_boundaries"].set_rows([[x.id, x.layer_id, x.kind, short_number(x.value), short_number(x.ambient)] for x in self.project.thermal_boundaries])
        self.panels["heat_sources"].set_rows([[x.id, x.layer_id, x.kind, short_number(x.value)] for x in self.project.heat_sources])
        self.panels["refinements"].set_rows([[x.id, f"{x.size * 1e6:.5g}", region_summary(x.geometry)] for x in self.project.mesh.refinements])
        self.material_panel.set_rows([[x.id, x.name, x.category, "Reference snapshot" if x.reference else "Project / custom"] for x in self.project.materials.values()])

    def objects(self, key):
        return self.project.mesh.refinements if key == "refinements" else getattr(self.project, key)

    def unique_id(self, prefix, objects):
        ids = {x.id for x in objects}
        index = 1
        while f"{prefix}{index}" in ids:
            index += 1
        return f"{prefix}{index}"

    def add_object(self, key, region=None):
        if self.model_locked():
            return
        if hasattr(self, "guided") and not self.guided.resolve_draft():
            return
        objects = self.objects(key)
        layer = self.field_layer.currentData() or (self.project.layers[0].id if self.project.layers else "")
        if key != "layers" and not layer:
            self.error("Physical layer required", "Add a physical layer in the stack first.")
            return
        identifier = self.unique_id({"layers": "layer", "terminals": "terminal", "interfaces": "interface", "thermal_boundaries": "sink", "heat_sources": "heat", "refinements": "refine"}[key], objects)
        default_region = region or RegionSpec(full_domain=key in {"thermal_boundaries", "heat_sources"})
        if key == "layers":
            model = PhysicalLayer(identifier, "New layer", "copper", geometry=default_region,
                                  z=max((x.z + x.thickness for x in self.project.layers), default=0.))
        elif key == "terminals":
            model = Terminal(identifier, layer, default_region, name=identifier)
        elif key == "interfaces":
            other = next((x.id for x in self.project.layers if x.id != layer), layer)
            model = Interface(identifier, layer, other, default_region)
        elif key == "thermal_boundaries":
            model = ThermalBoundary(identifier, layer, default_region)
        elif key == "heat_sources":
            model = HeatSource(identifier, layer, default_region)
        else:
            model = RefinementRegion(identifier, default_region, self.project.mesh.contact_size)
        dialog = ModelDialog(model, "Add " + key.replace("_", " ").rstrip("s"), self, self.choices(), self.mask_keys())
        if dialog.exec():
            if any(x.id == dialog.model.id for x in objects):
                self.error("Duplicate identifier", dialog.model.id)
                return
            self.before_edit()
            objects.append(dialog.model)
            self.changed()
            self.config_tabs.setCurrentWidget(self.panels[key])

    def edit_object(self, key, identifier=None):
        if self.model_locked():
            return
        if hasattr(self, "guided") and not self.guided.resolve_draft():
            return
        if self.job:
            return
        identifier = identifier or self.panels[key].selected_id()
        objects = self.objects(key)
        model = next((x for x in objects if x.id == identifier), None)
        if model is None:
            return
        dialog = ModelDialog(model, "Edit " + model.id, self, self.choices(), self.mask_keys(), readonly=("id",))
        if dialog.exec():
            self.before_edit()
            objects[objects.index(model)] = dialog.model
            self.changed()

    def remove_object(self, key):
        if self.model_locked():
            return
        if hasattr(self, "guided") and not self.guided.resolve_draft():
            return
        identifier = self.panels[key].selected_id()
        if not identifier or self.job:
            return
        objects = self.objects(key)
        if QMessageBox.question(self, "Remove object", f"Remove {identifier}?" +
                                (" Related terminals, interfaces and boundaries will also be removed." if key == "layers" else "")) != QMessageBox.StandardButton.Yes:
            return
        self.before_edit()
        objects[:] = [x for x in objects if x.id != identifier]
        if key == "layers":
            self.project.terminals = [x for x in self.project.terminals if x.layer_id != identifier]
            self.project.interfaces = [x for x in self.project.interfaces
                                       if identifier not in (x.layer_a, x.layer_b, x.heat_layer_id, *x.replaces_layers)]
            self.project.thermal_boundaries = [x for x in self.project.thermal_boundaries if x.layer_id != identifier]
            self.project.heat_sources = [x for x in self.project.heat_sources if x.layer_id != identifier]
        self.changed()

    def reorder_object(self, key, direction):
        if self.model_locked():
            return
        if hasattr(self, "guided") and not self.guided.resolve_draft():
            return
        identifier = self.panels[key].selected_id()
        objects = self.objects(key)
        index = next((i for i, value in enumerate(objects) if value.id == identifier), -1)
        if 0 <= index + direction < len(objects) and index >= 0:
            self.before_edit()
            objects[index], objects[index + direction] = objects[index + direction], objects[index]
            self.changed()

    def edit_settings(self, key):
        if self.model_locked():
            return
        if hasattr(self, "guided") and not self.guided.resolve_draft():
            return
        dialog = ModelDialog(getattr(self.project, key), key.capitalize() + " settings", self, self.choices(), self.mask_keys())
        if dialog.exec():
            self.before_edit()
            setattr(self.project, key, dialog.model)
            self.changed()

    def edit_transient(self):
        if self.model_locked():
            return
        start_time = 0.
        if self.project.transient.initialization == "resume":
            try:
                source = self.initial_state_argument()
                start_time = float(source["initial_result"].metrics["time_s"]) if source else 0.
            except ValueError:
                pass  # Settings remain editable when a source still needs binding.
        dialog = TransientDialog(self.project, self, mesh=self.mesh, start_time=start_time)
        if dialog.exec():
            self.before_edit()
            self.project.transient = dialog.project.transient
            self.project.solver.initial_temperature = dialog.project.solver.initial_temperature
            self.project.solver.mode = dialog.project.solver.mode
            self.changed(geometry=False)

    def toggle_thermal_joule(self, checked):
        if self.model_locked():
            return
        if self._refreshing:
            return
        self.before_edit()
        self.project.solver.thermal_include_joule = checked
        self.changed()

    def update_heating_control(self):
        self.thermal_joule.setEnabled(self.project.solver.mode == "thermal" and not self.job and not self.inspection_context)
        self.thermal_joule.setToolTip(
            "Thermal mode: recompute electrical heating at the initial reference temperature for each drive step. "
            "Use electrothermal for temperature feedback into electrical properties."
            if self.project.transient.enabled else
            "Thermal mode: compute DC Joule and contact heating at the reference temperature. "
            "Electrothermal mode always updates heating with temperature feedback.")

    def change_tool(self):
        self.view.set_mode(self.tool_combo.currentData())
        self.statusBar().showMessage(self.view.tool_hint(self.tool_combo.currentData()))

    def set_tool(self, mode):
        self.tool_combo.setCurrentIndex(self.tool_combo.findData(mode))

    def on_region_drawn(self, mode, region):
        self.guided.preview_region(mode, region)

    def on_line_drawn(self, mode, line):
        if mode == "terminal_edge":
            self.guided.preview_region(mode, RegionSpec(line=line))
        elif mode == "measure":
            delta = np.asarray(line[1]) - line[0]
            self.log(f"Measured distance {np.linalg.norm(delta) * 1e6:.7g} µm; Δx {delta[0] * 1e6:.7g} µm, Δy {delta[1] * 1e6:.7g} µm")
            self.view.show_region(LineString(line), "Measurement", "#7A572B")
            self.show_analysis_page(self.console)
        elif mode == "cross_section":
            if self.geometry:
                self.last_cut = line
                self.cross_section.show_cut(self.project, self.geometry, *line)
                self.show_analysis_page(self.cross_section)
                self.view.show_region(LineString(line), "Physical cross-section")
            else:
                self.error("No physical geometry", "Define valid physical layers first.")
        elif mode == "profile":
            if (self.field_combo.currentData() not in self.fields_for_layer(self.field_layer.currentData())
                    or self.view.overlay_item is None):
                self.error("No field on this layer", "Choose a result layer and field with available values before drawing a profile.")
            else:
                sampler = self.get_sampler()
                layer, field = self.field_layer.currentData(), self.field_combo.currentData()
                distance, values, coordinates = sampler.profile(layer, field, *line,
                    region=self.field_region.currentData() or "", logarithmic=self.log_scale.isChecked())
                name = f"Profile {len(self.profiles.profiles) + 1} · {self.result.configuration_hash[:8]}"
                if self.view_source_result().metrics.get("time_s") is not None:
                    name += f" · t = {self.view_source_result().metrics['time_s']:.6g} s"
                if self.display_result:
                    name += " · Current − comparison"
                name += " · " + self.field_region.currentText()
                self.profiles.add_profile(name, layer, field, distance, values, coordinates,
                                          provenance=self.displayed_provenance())
                self.show_analysis_page(self.profiles)
                self.view.show_region(LineString(line), "Field profile", "#1F6F5F")
        elif mode == "branch_current":
            if not self.result:
                self.error("No electrical result", "Run an electrical or coupled analysis first.")
            else:
                try:
                    from etlayout.solvers import branch_current
                    layer = self.field_layer.currentData()
                    source = self.view_source_result()
                    current = branch_current(source, layer, *line)
                    self.view.show_region(LineString(line), "Integrated branch-current cut", "#1F6F5F")
                    time_note = f" at t = {source.metrics['time_s']:.9g} s" if "time_s" in source.metrics else ""
                    self.log(f"Original full-layer branch current on {layer}{time_note}: {current:.9g} A across the drawn cut "
                             "(positive toward its left normal; display region and difference mode do not change this physical integral).")
                    self.show_analysis_page(self.console)
                except Exception as error:
                    self.error("Branch-current integration failed", error)
        self.set_tool("select")

    def selection_action(self, action):
        if action == "fit":
            self.view.fit_selection()
            return
        selected = self.view.selected_geometries()
        if not selected:
            self.statusBar().showMessage("Select one or more polygons first.", 6000)
            return
        geometry = unary_union([shape for key, shape in selected])
        if action == "connected":
            keys = {key for key, shape in selected}
            all_shapes = [item.data(1) for key in keys for item in self.view.layer_items.get(key, [])]
            merged = unary_union(all_shapes)
            components = list(merged.geoms) if hasattr(merged, "geoms") else [merged]
            matching = [component for component in components if component.intersection(geometry).area > 0
                        or component.intersection(geometry).length > 0]
            for key in keys:
                for item in self.view.layer_items.get(key, []):
                    item.setSelected(any(component.intersection(item.data(1)).area > 0 for component in matching))
            return
        region = geometry_region(geometry)
        if action == "roi":
            self.guided.pending_purpose = "domain"
            self.guided.preview_region("roi_polygon", region)
        else:
            if action == "layer":
                if not self.guided.navigate("layers"):
                    return
                self.guided.add_layer()
                self.guided.editor.set_region(region)
            else:
                if not self.guided.navigate("connections"):
                    return
                self.guided.new_connection("terminal" if action == "terminal" else "interface", region)



    def clear_roi(self):
        if self.model_locked():
            return
        if not self.guided.resolve_draft():
            return
        self.before_edit()
        self.project.roi = None
        self.project.acknowledge_clipped_boundaries = False
        self.changed()


    def clear_display_roi(self):
        if self.model_locked():
            return
        if not self.guided.resolve_draft():
            return
        self.before_edit()
        self.project.display_roi = None
        self.changed()
        self.view.fit_all()

    def acknowledge_boundaries(self, checked):
        if self.model_locked():
            return
        if self._refreshing:
            return
        if checked and QMessageBox.question(self, "Clipped conducting boundary", "Confirm that you have reviewed the cut conducting paths. Add terminals at artificial cuts when current crosses them. Unassigned cuts impose the model's zero normal current boundary.") != QMessageBox.StandardButton.Yes:
            self.acknowledge_action.setChecked(False)
            return
        self.before_edit()
        self.project.acknowledge_clipped_boundaries = checked
        self.changed()

    def update_coordinates(self, x, y):
        self.coordinates.setText(f"x {x * 1e6:.5f} µm · y {y * 1e6:.5f} µm")

    def run(self):
        if self.model_locked():
            return
        try:
            self.start_job("solve", self.initial_state_argument())
        except ValueError as error:
            self.error("Initial state needed", error)

    def initial_state_argument(self, project=None):
        project = project or self.project
        settings = project.transient
        if not settings.enabled or settings.initialization not in {"saved", "resume"}:
            return None
        if not settings.initial_state_digest:
            raise ValueError("The saved initial state has not been bound to a completed run. Choose its archive again using Solve → Studies / start.")
        candidates = ([self.result] if self.result else []) + [run for run in reversed(self.run_history) if not is_record(run)]
        from etlayout.models import SimulationError
        from etlayout.simulation.sweep import resolve_study_initial_state
        for run in candidates:
            for explicit in (False, True):
                try:
                    options = resolve_study_initial_state(project, None if explicit else run,
                        initial_result=run if explicit else None, resume=settings.initialization == "resume")
                    if project is self.project:
                        self.guided.initial_state_evidence = self.guided.initial_state_key()
                    return options
                except SimulationError:
                    continue
        raise ValueError("This setup requires its saved initial thermal field. Open its result archive or use Solve → Studies / start → Start from saved state / Resume saved transient.")

    def validate_setup(self):
        if self.model_locked():
            return
        try:
            source = self.initial_state_argument()
            start = float(source["initial_result"].metrics["time_s"]) if source and source["resume"] else 0.
            self.start_job("validate", {"start_time": start, "initial_state_supplied": source is not None})
        except ValueError as error:
            self.error("Initial state needed", error)

    def run_saved_state(self, resume=False):
        if self.model_locked():
            return
        path, _ = QFileDialog.getOpenFileName(self, "Choose saved thermal state", "", "Projects (*.etl *.etlayout *.etproj *.zip)")
        if not path:
            return
        try:
            _, initial = self.load_project_data(path)
            if initial is None or not any("temperature" in fields for fields in initial.fields.values()):
                raise ValueError("Choose an archive containing a computed thermal state.")
            restart_keys = ("time_s", "transient_input_energy", "transient_output_energy", "transient_energy_reference_offset")
            if resume and not all(key in initial.metrics for key in restart_keys):
                raise ValueError("Resume requires a transient result or exported snapshot with restart metadata. Use Start from saved state for a steady result.")
            start = float(initial.metrics["time_s"]) if resume else 0.
            stop = max(self.project.transient.stop_time, start + self.project.transient.stop_time)
            text, okay = QInputDialog.getText(self, "Resume transient" if resume else "Start from thermal state",
                f"Start: {engineering(start, 's')}. Waveforms use absolute time.\n"
                "The saved thermal field must match this stack and mesh.\nAbsolute stop time (s; engineering units accepted)",
                text=engineering(stop, "s"))
            if not okay:
                return
            stop = parse_number(text, "s")
            if stop <= start:
                raise ValueError("Stop time must be after the saved start time.")
            self.before_edit()
            self.project.transient.enabled = True
            self.project.transient.initialization = "resume" if resume else "saved"
            self.project.transient.initial_state_digest = ""
            self.project.transient.stop_time = stop
            if self.project.solver.mode == "electrical":
                self.project.solver.mode = "electrothermal"
            self.changed(geometry=False)
            self.start_job("solve", {"initial_result": initial, "resume": resume})
        except Exception as error:
            self.error("Cannot use saved state", error)

    def run_time_convergence_dialog(self):
        if self.model_locked():
            return
        if not self.project.transient.enabled:
            self.error("Select Transient", "Choose Transient in Solve before running a time-step study.")
            return
        dialog = QDialog(self)
        dialog.setWindowTitle("Time-step convergence")
        layout = QVBoxLayout(dialog)
        note = QLabel("Compare peak temperature, peak time and energy balance as the maximum step is refined. "
                      f"Saved field interval stays fixed at {engineering(self.project.transient.output_interval, 's')}. "
                      "Waveform edges and saved times also split integration steps.")
        note.setWordWrap(True)
        layout.addWidget(note)
        form = QFormLayout()
        steps = QLineEdit(", ".join(engineering(self.project.transient.time_step / factor, "s") for factor in (1, 2, 4)))
        steps.setAccessibleName("Maximum time steps, separated by commas")
        form.addRow("Maximum steps", steps)
        layout.addLayout(form)
        error = QLabel()
        error.setWordWrap(True)
        layout.addWidget(error)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.rejected.connect(dialog.reject)
        def accept():
            try:
                values = [parse_number(value, "s") for value in steps.text().split(",")]
                if len(values) < 2 or any(value <= 0 for value in values) or any(b >= a for a, b in zip(values, values[1:])):
                    raise ValueError("Enter at least two positive time steps in decreasing order.")
                initial = self.initial_state_argument() or {}
                dialog.accept()
                self.start_job("time_convergence", {"steps": values, **initial})
            except ValueError as exc:
                error.setText(str(exc))
        buttons.accepted.connect(accept)
        layout.addWidget(buttons)
        dialog.exec()

    def start_job(self, operation, argument=None):
        if self.model_locked():
            return
        if not self.project.layers and operation != "import":
            self.error("No physical stack", "Import a layout and assign at least one physical layer.")
            return
        if getattr(self.project, "_resource_unresolved", False) and operation != "import":
            self.error("Unresolved resources", "Review project resources or relink the layout before computation.")
            return
        self.cancel_catalog()
        self.history_workflow.loader.cancel()
        if self.prepare_job:
            # Finish binding source identity before taking the run snapshot.
            # Keep one CPU budget rather than overlapping geometry and solve.
            self._pending_compute = (operation, deepcopy(argument))
            self.statusBar().showMessage(f"{operation.capitalize()} queued until layout preparation finishes.")
            return
        if self.hierarchy_jobs or self.hierarchy_queue:
            self.cancel_geometry()
            self.refresh_hierarchy()
        self.operation = operation
        snapshot = deepcopy(self.project)
        self.job_context = {"project": snapshot, "physics_hash": physics_hash(snapshot), "operation": operation,
                            "supplied_initial_state": isinstance(argument, dict) and argument.get("initial_result") is not None}
        job = ComputeJob(snapshot, operation, deepcopy(argument), self, execution=self.execution_settings)
        self.job = job
        job.progress.connect(lambda fraction, message: self.job_progress(fraction, message) if job is self.job else None)
        job.finished.connect(lambda output: self.job_finished(output) if job is self.job else None)
        job.failed.connect(lambda message, detail: self.job_failed(message, detail) if job is self.job else None)
        job.cancelled.connect(lambda: self.job_cancelled() if job is self.job else None)
        self.progress.show()
        self.progress.setValue(0)
        self.set_running(True)
        if hasattr(self, "guided"):
            self.guided.job_started()
        self.log(f"Starting {operation} in an independent process · {self.execution_settings.cpu_threads} CPU budget · "
                 f"up to {self.execution_settings.study_workers} parallel study workers…")
        try:
            job.start()
        except Exception as error:
            job.cleanup()
            self.job_failed(str(error), "")

    def set_running(self, running):
        locked = running or self.model_locked()
        unresolved = getattr(self.project, "_resource_unresolved", False)
        for action in (self.run_action, self.mesh_action, self.validate_action):
            action.setEnabled(not locked and not unresolved)
        self.power_action.setEnabled(not locked and not unresolved)
        self.cancel_action.setEnabled(running or self.prepare_job is not None)
        self.config_dock.setEnabled(not locked)
        # The guided Complete model page reparents these controls out of the dock.
        self.config_tabs.setEnabled(not locked)
        self.design_dock.setEnabled(not locked)
        self.update_heating_control()
        self.tool_combo.setEnabled(not locked)
        self.welcome.setEnabled(not locked)
        self.study_panel.table.setEnabled(not running)
        self.history_panel.setEnabled(not running)
        self.return_button.setEnabled(not running)
        if locked:
            self.view.set_mode("pan")
        else:
            self.change_tool()
        self.update_title()

    def job_progress(self, fraction, message):
        self.progress.setValue(max(0, min(100, int(fraction * 100))))
        self.statusBar().showMessage(message)
        self.console.append(message)
        if hasattr(self, "guided"):
            self.guided.job_progress(message)

    def finish_job(self):
        self.job = None
        self.job_context = None
        self.progress.hide()
        self.set_running(False)

    def job_finished(self, output):
        operation = self.operation
        context = self.job_context
        self.finish_job()
        if context and physics_hash(self.project) != context["physics_hash"]:
            if operation == "solve":
                self.remember_result(output)
                self.dirty = True
                self.update_title()
            self.log("The project changed during computation. The completed run was retained in history; it was not attached to the current setup.")
            return
        if operation == "import":
            self.finish_import(SimpleNamespace(**output))
        elif operation == "mesh":
            self.geometry, self.mesh = output
            self.refresh_view(True)
            self.mesh_toggle.setChecked(True)
            self.view.show_mesh(self.mesh)
            self.log("Mesh complete: " + short_number(self.mesh.statistics))
            self.show_diagnostics(self.geometry.diagnostics)
        elif operation == "validate":
            self.geometry, self.net_report = output
            diagnostics = [*self.geometry.diagnostics, *self.net_report.diagnostics]
            self.show_diagnostics(diagnostics)
            counts = {level: sum(d.severity == level for d in diagnostics) for level in ("error", "warning", "info")}
            self.log(f"Validation: {counts['error']} errors · {counts['warning']} warnings · {len(self.net_report.nets)} electrical nets. Select a diagnostic or net to inspect it.")
            self.highlight_nets()
            self.show_analysis_page(self.diagnostics)
        elif operation == "refinement-guidance":
            QTimer.singleShot(0, lambda: self.guided.review_refinements(output))
        elif operation in {"sweep", "compare", "convergence", "time_convergence", "local-refine"}:
            self.studies = output
            parameter = (self.project.sweeps[-1].parameter if operation == "sweep" else "Mesh size (m)"
                         if operation == "convergence" else "Local refinement factor" if operation == "local-refine"
                         else "Maximum time step (s)" if operation == "time_convergence" else "Design")
            self.study_panel.set_rows(output, parameter)
            self.show_analysis_page(self.study_panel)
            successes = sum(row.result is not None and row.result.converged for row in output)
            for row in output:
                if row.result:
                    self.remember_result(row.result)
            self.dirty = True
            self.update_title()
            self.log(f"{operation.capitalize()} finished: {successes}/{len(output)} points converged. Double-click a row to inspect its actual field map.")
            if operation == "convergence":
                for row in output:
                    if row.result:
                        for index, profile in enumerate(row.result.metrics.get("mesh_profiles", [])):
                            self.profiles.add_profile(
                                f"Mesh {row.value * 1e6:.6g} µm · cut {index + 1}", profile["layer_id"], profile["field"],
                                np.asarray(profile["distance_m"]), np.asarray(profile["value"]),
                                np.column_stack((profile["x_m"], profile["y_m"])))
        else:
            if context and context.get("supplied_initial_state"):
                self.project.transient = project_from_dict(output.configuration).transient
                self.refresh_project(preserve_view=True, refresh_geometry=False)
            self.show_result(output)
            self.dirty = True
            self.update_title()
            self.log(f"Simulation {'converged' if output.converged else 'did not converge'} in {output.elapsed_seconds:.3f} s."
                     + (" Engineering acceptance failed: maximum-temperature compliance."
                        if output.metrics.get("thermal_limit_compliance", {}).get("status") in {"failed", "unavailable"} else ""))

        if hasattr(self, "guided"):
            self.guided.job_completed(operation, context)

    def job_failed(self, message, detail):
        self.finish_job()
        self.log(detail or message)
        self.show_analysis_page(self.console)
        if hasattr(self, "guided"):
            self.guided.job_failed(message, detail=detail)
        else:
            self.error("Computation failed", message)

    def job_cancelled(self):
        self.finish_job()
        self.log("Computation cancelled. The independent worker was terminated.")
        if hasattr(self, "guided"):
            self.guided.execution_state = "Cancelled · no new result"
            self.guided.refresh()

    def cancel_job(self):
        if self.job:
            self.job.cancel()
        elif hasattr(self, "guided") and self.guided.power and self.guided.power.job:
            self.guided.power.cancel_job()
        elif self.prepare_job or self.hierarchy_jobs:
            self.cancel_geometry()
            self.cancel_action.setEnabled(False)
            self.workflow_hint.setText("Geometry preparation cancelled. Edit the project or run validation to prepare it again.")
            self.log("Geometry preparation cancelled.")

    def show_diagnostics(self, diagnostics):
        self.diagnostics.clear()
        for diagnostic in diagnostics:
            item = QTreeWidgetItem([diagnostic.severity, diagnostic.object_id, diagnostic.message])
            item.setData(0, Qt.ItemDataRole.UserRole, diagnostic.bounds)
            if diagnostic.severity == "error":
                item.setForeground(0, QColor("#B5473C"))
            elif diagnostic.severity == "warning":
                item.setForeground(0, QColor("#9A681A"))
            self.diagnostics.addTopLevelItem(item)
        self.diagnostics.resizeColumnToContents(0)
        self.diagnostics.resizeColumnToContents(1)
        errors = sum(d.severity == "error" for d in diagnostics)
        warnings = sum(d.severity == "warning" for d in diagnostics)
        index = self.analysis_tabs.indexOf(self.diagnostics)
        self.analysis_tabs.setTabText(index, f"Validation ({errors} / {warnings})")
        self.analysis_tabs.setTabToolTip(index, f"{errors} errors · {warnings} warnings. Double-click an entry to locate it.")

    def locate_diagnostic(self, item, col):
        bounds = item.data(0, Qt.ItemDataRole.UserRole)
        if bounds:
            from PySide6.QtCore import QRectF
            x0, y0, x1, y1 = bounds
            self.view.fitInView(QRectF(x0 * 1e6, -y1 * 1e6, max((x1 - x0) * 1e6, 1), max((y1 - y0) * 1e6, 1)), Qt.AspectRatioMode.KeepAspectRatio)

    def highlight_nets(self):
        self.net_tree.clear()
        if not self.net_report:
            return
        for net, members in self.net_report.nets.items():
            terminals = [key for key, nets in self.net_report.terminal_nets.items() if net in nets]
            item = QTreeWidgetItem([net, ", ".join(terminals)])
            item.setData(0, Qt.ItemDataRole.UserRole, members)
            self.net_tree.addTopLevelItem(item)
            for member in members:
                component = self.net_report.components[member]
                child = QTreeWidgetItem([member, f"{component.geometry.area * 1e12:.5g} µm²"])
                child.setData(0, Qt.ItemDataRole.UserRole, [member])
                item.addChild(child)
        self.net_tree.expandAll()
        for key, items in self.view.layer_items.items():
            for item in items:
                nets = {component.net for component in self.net_report.components.values()
                        if component.layer_id == key and component.geometry.intersects(item.data(1))}
                item.setToolTip(item.toolTip() + "\nNets: " + ", ".join(sorted(nets)))

    def locate_net(self, item, column):
        if not self.net_report:
            return
        members = item.data(0, Qt.ItemDataRole.UserRole) or []
        geometry = unary_union([self.net_report.components[key].geometry for key in members])
        self.view.show_region(geometry, "Net " + item.text(0), "#B47E35")
        for items in self.view.layer_items.values():
            for shape in items:
                shape.setSelected(any(self.net_report.components[key].layer_id == shape.data(0)
                                      and self.net_report.components[key].geometry.intersects(shape.data(1)) for key in members))
        self.view.fit_selection()

    def trace_terminals(self):
        if not self.net_report:
            self.error("Validate connectivity first", "Run Validate (F6) to extract nets before tracing a path.")
            return
        ids = [terminal.id for terminal in self.project.terminals]
        first, okay = QInputDialog.getItem(self, "Trace terminal path", "From terminal", ids, editable=False)
        if not okay:
            return
        second, okay = QInputDialog.getItem(self, "Trace terminal path", "To terminal", ids, editable=False)
        if okay:
            path = self.net_report.trace(first, second)
            if not path:
                self.log(f"No electrical path from {first} to {second}.")
                return
            geometry = unary_union([self.net_report.components[key].geometry for key in path])
            self.view.show_region(geometry, f"Path {first} → {second}", "#B47E35")
            self.log(f"Electrical path {first} → {second}: " + " → ".join(path))

    def show_result(self, result):
        if hasattr(self, "guided") and not self.guided.resolve_draft():
            return False
        if physics_hash(result.configuration) != physics_hash(self.project):
            self.remember_result(result)
            self.inspect_result(result)
            return
        if self.inspection_context:
            self.return_to_project()
        self.history_workflow.new_result()
        self.result = result
        self.remember_result(result)
        self.render_result(result)
        self.workflow_tabs.setCurrentIndex(3)

    def remember_result(self, result):
        freeze_result_arrays(result)
        if not any(existing is result or (existing.configuration_hash == result.configuration_hash
                                          and existing.elapsed_seconds == result.elapsed_seconds)
                   for existing in self.run_history):
            self.run_history.append(result)
        self.history_panel.set_results(self.run_history)

    def inspect_result(self, result):
        if is_record(result):
            return self.history_workflow.inspect(result)
        power = getattr(getattr(self, "guided", None), "power", None)
        if self.job or (power and power.job):
            self.log("Wait for the active computation to finish before changing the inspected run.")
            return
        if hasattr(self, "guided") and not self.guided.resolve_draft():
            return False
        self.guided.cancel_selection()
        try:
            project = project_from_dict(result.configuration)
        except Exception as error:
            self.error("Cannot inspect run configuration", error)
            return
        if self.inspection_context is None:
            self.inspection_context = {"project": self.project, "result": self.result, "path": self.path,
                                       "dirty": self.dirty, "undo": self.undo_history, "redo": self.redo_history,
                                       "view_state": self.capture_view_state(), "workspace": self.guided.capture_context()}
        elif self.result:
            self._run_view_states[id(self.result)] = self.capture_view_state()
        self.clear_analysis("Preparing the recorded run for inspection.")
        self.project, self.result, self.path, self.dirty = project, result, None, False
        self.undo_history, self.redo_history = [], []
        self.inspection_label.setText("READ-ONLY RUN · " + run_caption(result) + "\nFields, stack, terminals and exports use this run's exact configuration.")
        self.inspection_bar.show()
        self.refresh_project()
        self.render_result(result)
        self.workflow_tabs.setCurrentIndex(3)
        if id(result) in self._run_view_states:
            self.restore_view_state(self._run_view_states[id(result)])
        self.set_running(False)

    def return_to_project(self):
        if not self.inspection_context or self.job:
            return
        if hasattr(self, "guided") and not self.guided.resolve_draft():
            return False
        if self.result:
            self._run_view_states[id(self.result)] = self.capture_view_state()
        self.history_workflow.loader.cancel()
        context, self.inspection_context = self.inspection_context, None
        self.clear_analysis()
        self.project, self.result, self.path = context["project"], context["result"], context["path"]
        self._dirty = context["dirty"]
        self.undo_history, self.redo_history = context["undo"], context["redo"]
        self.inspection_bar.hide()
        self.refresh_project()
        if self.result:
            self.render_result(self.result)
        if context.get("workspace"):
            self.guided.restore_context(context["workspace"])
        self.restore_view_state(context["view_state"])
        self.history_workflow.returned()
        self.set_running(False)

    def capture_view_state(self):
        transform = self.view.transform()
        center = self.view.mapToScene(self.view.viewport().rect().center())
        return {"layer": self.field_layer.currentData(), "field": self.field_combo.currentData(),
                "snapshot_index": self.time_navigation.slider.value(),
                "regions": dict(self._region_choices), "colormap": self.colormap_combo.currentData(),
                "range": self.legend_scale.capture_state(), "log": self.log_scale.isChecked(),
                "history_range": self.history_range.isChecked(),
                "vectors": self.vector_toggle.isChecked(), "mesh": self.mesh_toggle.isChecked(),
                "smooth": self.smooth_field.isChecked(), "isolate": self.isolate_layer.isChecked(),
                "source_masks": self._show_masks, "mode": self.view_mode.currentData(),
                "comparison": self.difference_source, "comparison_name": self.difference_name,
                "comparison_snapshot_index": self.difference_time.slider.value(),
                "comparison_link_time": self.difference_link_time.isChecked(),
                "transform": (transform.m11(), transform.m12(), transform.m13(), transform.m21(),
                              transform.m22(), transform.m23(), transform.m31(), transform.m32(), transform.m33()),
                "center": (center.x(), center.y())}

    def restore_viewport(self, state):
        self.view.setTransform(QTransform(*state["transform"]))
        self.view.centerOn(QPointF(*state["center"]))

    def restore_view_state(self, state):
        self._refreshing = True
        try:
            self.history_range.setChecked(state.get("history_range", False))
            self._region_choices = dict(state["regions"])
            self._region_layer = None
            self.difference_source = state["comparison"]
            self.difference_name = state["comparison_name"]
            self.difference_time.set_result(self.difference_source)
            self.difference_link_time.setChecked(state.get("comparison_link_time", True))
            self.difference_link_time.setVisible(bool(self.difference_source and self.difference_source.transient))
            self.difference_time.slider.setValue(state.get("comparison_snapshot_index", self.difference_time.slider.maximum()))
            self._preferred_field = state["field"]
            for combo, key in [(self.field_layer, "layer"), (self.colormap_combo, "colormap"),
                               (self.view_mode, "mode")]:
                combo.blockSignals(True)
                combo.setCurrentIndex(max(0, combo.findData(state[key])))
                combo.blockSignals(False)
            for control, key in [(self.log_scale, "log"), (self.vector_toggle, "vectors"),
                                 (self.mesh_toggle, "mesh"), (self.smooth_field, "smooth"),
                                 (self.isolate_layer, "isolate"), (self.mask_toggle, "source_masks")]:
                control.blockSignals(True)
                control.setChecked(state[key])
                control.blockSignals(False)
            self._show_masks = state["source_masks"]
            self.populate_fields()
            self.refresh_layer_tree()
            if self.geometry is not None:
                self.refresh_view(True)
        finally:
            self._refreshing = False
        if self.result and self.result.transient:
            self.time_navigation.slider.setValue(state.get("snapshot_index", len(self.result.transient.times) - 1))
        self.legend_scale.reset()
        self.update_overlay()
        self.legend_scale.restore_state(state["range"], emit=False)
        self.update_overlay()
        self.restore_viewport(state)
        if self.prepare_job is not None:
            self._pending_view_state = state

    def compare_history(self, results):
        if len(results) < 2:
            self.error("Select two runs", "Select two or more runs in Run history using Ctrl-click, then choose Compare selected.")
            return
        if any(is_record(result) for result in results):
            return self.history_workflow.compare(results)
        from etlayout.gui.comparison import MapComparisonDialog
        dialog = MapComparisonDialog([(run_caption(result), result) for result in results], self)
        try:
            dialog.exec()
        finally:
            for view in dialog.views:
                view.show_field(None, "", "")
            for timeline in dialog.time_navigations:
                timeline.set_result(None)
            dialog.results = []
            dialog.deleteLater()

    def remove_history(self, results):
        if self.job:
            return
        if QMessageBox.question(self, "Remove recorded runs", f"Remove {len(results)} selected run(s) from this project's history? Current result fields remain available.") != QMessageBox.StandardButton.Yes:
            return
        self.history_workflow.loader.cancel()
        self.run_history = [result for result in self.run_history if not any(result is selected for selected in results)]
        self.history_panel.set_results(self.run_history)
        if self.inspection_context:
            self.inspection_context["dirty"] = True
        else:
            self.dirty = True
            self.update_title()

    def compare_run_configuration(self):
        if self.inspection_context:
            baseline = self.inspection_context["project"].to_dict()
            candidate = self.result.configuration
        else:
            selected = self.history_panel.selected()
            if not selected:
                self.show_analysis_page(self.history_panel)
                self.log("Select a recorded run, then choose Compare run configuration.")
                return
            baseline, candidate = self.project.to_dict(), (selected[0].configuration_copy() if is_record(selected[0]) else selected[0].configuration)
        rows = configuration_changes(baseline, candidate)
        dialog = QDialog(self)
        dialog.setWindowTitle("Current project → recorded run · configuration changes")
        dialog.resize(980, 580)
        layout = QVBoxLayout(dialog)
        layout.addWidget(QLabel(f"{len(rows)} changed configuration values. All numeric model values use SI units."))
        table = QTableWidget(len(rows), 3)
        table.setHorizontalHeaderLabels(["Setting", "Current project", "Recorded run"])
        table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        for row, values in enumerate(rows):
            for column, value in enumerate(values):
                item = QTableWidgetItem(value)
                item.setToolTip(value)
                table.setItem(row, column, item)
        layout.addWidget(table)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        buttons.rejected.connect(dialog.reject)
        layout.addWidget(buttons)
        dialog.exec()

    def render_result(self, result):
        self.transient_frame = result.transient.snapshots[-1] if result.transient else None
        self.time_navigation.set_result(result)
        self.time_history.set_result(result)
        self.render_result_metrics(self.view_source_result() or result)
        self.populate_fields()
        self.update_overlay()
        self.analysis_tabs.setCurrentIndex(0)

    def render_result_metrics(self, result):
        self.mesh = result.mesh
        self.sampler = None
        run = self.result if self.result and self.result.transient else result
        status = ("Transient run completed" if run.converged else "TRANSIENT RUN INCOMPLETE") if run.transient else (
            "Converged" if result.converged else "NOT CONVERGED")
        compliance = run.metrics.get("thermal_limit_compliance", {})
        thermal_text = ""
        if compliance.get("status") in {"failed", "unavailable"}:
            status = "FAILED — Maximum temperature exceeded" if compliance["status"] == "failed" else "FAILED — Temperature compliance unavailable"
            failed_limits = [row for row in compliance.get("limits", []) if row["status"] == "failed"]
            violation = max(failed_limits, key=lambda row: row["peak"]["temperature"]) if failed_limits else None
            if violation:
                peak = violation["peak"]
                thermal_text = (f"Limit {violation['maximum_temperature'] - 273.15:.6g} °C; "
                    f"actual {peak['temperature'] - 273.15:.6g} °C on {peak['layer_id']} "
                    f"at ({peak['location'][0] * 1e6:.6g}, {peak['location'][1] * 1e6:.6g}) µm. "
                    + (f"Peak sampled at t={peak['time_s']:.9g} s. " if peak['time_s'] is not None else "")
                    + (f"First violation observed at t={compliance['first_violation']['peak']['time_s']:.9g} s. "
                       if compliance.get("first_violation", {}).get("peak", {}).get("time_s") is not None else "")
                    + (f"Rise {peak['temperature_rise']:.6g} K relative to {peak.get('reference', {}).get('method', 'selected reference')}. " if peak.get("temperature_rise") is not None else "")
                    + f"Numerical convergence: {'yes' if run.converged else 'no'}. "
                    + ("Stopped early; accepted history retained. " if compliance.get("stopped_early") else ""))
        self.result_status.setText(
            f"{status} · {len(result.mesh.triangles):,} triangles · "
            f"{run.elapsed_seconds:.3f} s · configuration {result.configuration_hash[:12]}\n"
            + (f"Selected transient snapshot: t = {result.metrics['time_s']:.9g} s. " if "time_s" in result.metrics else "")
            + thermal_text
            + "Select a field above; probe or draw a line profile. Peak current at sharp corners is mesh-sensitive.")
        metrics = {**result.metrics, **{key: value for key, value in run.metrics.items() if key.startswith("transient_")}}
        scalar_metrics = [(key, value) for key, value in metrics.items() if isinstance(value, (int, float, str))]
        self.metrics_table.setRowCount(len(scalar_metrics))
        for row, (key, value) in enumerate(scalar_metrics):
            self.metrics_table.setItem(row, 0, QTableWidgetItem(metric_label(key)))
            self.metrics_table.setItem(row, 1, QTableWidgetItem(engineering(value, metric_unit(key))))
        pair = result.metrics.get("resistance_terminal_pair", [])
        resistance_name = "Resistance" + (f" ({' → '.join(pair)})" if pair else "")
        headlines = []
        for key, title, unit in [("effective_resistance", resistance_name, "Ω"), ("total_current", "Total current", "A"),
                                 ("electrical_power", "Electrical power", "W"),
                                 ("temperature_max", "Selected-time peak temperature" if run.transient or self.history_workflow.record and self.history_workflow.record.times else "Overall peak temperature", "K")]:
            if key in result.metrics:
                headlines.append(f"{title}: {engineering(result.metrics[key], unit)}")
        current_ref = result.metrics.get("resistance_current_terminal", "")
        if run.transient:
            scope = "Resumed-segment peak" if run.metrics.get("transient_peak_scope") == "current_segment" else "Whole-run peak"
            headlines.append(f"{scope}: {run.metrics['transient_peak_temperature']:.6g} K at {engineering(run.metrics['transient_peak_time'], 's')}")
            if run.metrics.get("transient_energy_scope") == "cumulative_from_origin":
                headlines.append("Energy totals include the source run; fields cover the resumed segment.")
        self.key_metrics.setText("  ·  ".join(headlines) + "\nTerminal current is positive into the device."
                                 + (f" Resistance current reference: {current_ref}." if current_ref else ""))
        self._original_key_metrics = self.key_metrics.text()
        self.metrics_table.resizeColumnToContents(0)
        keys = sorted({key for values in result.terminals.values() for key in values})
        self.terminals_table.setColumnCount(len(keys) + 1)
        terminal_labels = {"voltage": "Potential (V)", "current": "Current into device (A)", "power": "Power (W)"}
        self.terminals_table.setHorizontalHeaderLabels(["Terminal", *[terminal_labels.get(key, key.replace("_", " ")) for key in keys]])
        self.terminals_table.setRowCount(len(result.terminals))
        for row, (name, values) in enumerate(result.terminals.items()):
            self.terminals_table.setItem(row, 0, QTableWidgetItem(name))
            for col, key in enumerate(keys, start=1):
                self.terminals_table.setItem(row, col, QTableWidgetItem(engineering(values.get(key, "—"), {"current": "A", "voltage": "V", "power": "W"}.get(key, ""))))
        self.terminals_table.resizeColumnsToContents()
        annotations = annotated_result(result, self.project, run=run)
        self.show_diagnostics(annotations.diagnostics)

    def view_source_result(self):
        if self.result and self.result.transient:
            return self.transient_frame or self.result.transient.snapshots[-1]
        return self.result

    def history_range_changed(self, checked):
        record = self.history_workflow.record
        if (checked and record and record.times and self.result and not self.result.transient
                and not self.legend_scale.lock.isChecked()):
            self.history_range.blockSignals(True)
            self.history_range.setChecked(False)
            self.history_range.blockSignals(False)
            self.history_workflow.full("range")
            return
        self.update_overlay()

    def select_snapshot(self, index):
        if self.history_workflow.record and self.history_workflow.record.times and not (self.result and self.result.transient):
            return self.history_workflow.frame(index)
        if not self.result or not self.result.transient or not 0 <= index < len(self.result.transient.snapshots):
            return
        self.transient_frame = self.result.transient.snapshots[index]
        self.sampler = self.display_sampler = None
        self._difference_cache.clear()
        self.render_result_metrics(self.transient_frame)
        self.time_history.select_time(self.result.transient.times[index])
        self.update_overlay()

    def populate_fields(self):
        if not hasattr(self, "field_combo"):
            return
        old = self._preferred_field
        self.field_combo.blockSignals(True)
        self.field_combo.clear()
        self.field_combo.addItem("Layout geometry", "")
        layer = self.field_layer.currentData()
        if self.result:
            available = self.fields_for_layer(layer)
            all_fields = list(dict.fromkeys(field for item in self.project.layers
                                           for field in self.fields_for_layer(item.id)))
            for field in all_fields:
                name, unit = FIELD_LABELS.get(field, (field, "SI"))
                self.field_combo.addItem(f"{name} ({unit})", field)
                item = self.field_combo.model().item(self.field_combo.count() - 1)
                item.setEnabled(field in available)
                if field not in available:
                    item.setToolTip(f"Not available on {layer}; select another result layer.")
            index = self.field_combo.findData(old)
            if old is None or index < 0:
                preferred = "temperature" if self.project.solver.mode == "thermal" else "potential"
                field = preferred if preferred in available else next(iter(available), "")
                index = self.field_combo.findData(field)
            self.field_combo.setCurrentIndex(max(0, index))
            self._preferred_field = self.field_combo.currentData()
        self.field_combo.blockSignals(False)
        self.field_combo.setEnabled(bool(self.result))
        self.field_layer.setEnabled(bool(self.project.layers))
        self.populate_regions()
        self.update_overlay()

    def populate_regions(self):
        layer = self.field_layer.currentData()
        self._region_layer = layer
        selected = self._region_choices.get(layer, "")
        self.field_region.blockSignals(True)
        self.field_region.clear()
        choices = region_options(self.result, layer) if self.result and layer else [("", "All solved regions")]
        for key, label in choices:
            self.field_region.addItem(label, key)
        self.field_region.setCurrentIndex(max(0, self.field_region.findData(selected)))
        self.field_region.blockSignals(False)
        self.field_region.setEnabled(bool(self.result))
        self._region_choices[layer] = self.field_region.currentData() or ""

    def select_result_region(self, *_):
        self._region_choices[self.field_layer.currentData()] = self.field_region.currentData() or ""
        self.update_overlay()

    def select_view_mode(self, *_):
        self.legend_scale.reset()
        if self.view_mode.currentData() == "difference":
            self.log_scale.setChecked(False)
            self.colormap_combo.setCurrentIndex(self.colormap_combo.findData("coolwarm"))
        self.update_overlay()

    def active_view_result(self):
        source = self.view_source_result()
        if not self.result or self.view_mode.currentData() != "difference":
            return source
        if self.difference_source is None:
            raise ValueError("Load a comparison result before choosing difference mode.")
        layer, field = self.field_layer.currentData(), self.field_combo.currentData()
        comparison = self.difference_source
        if comparison.transient:
            times = comparison.transient.times
            if self.difference_link_time.isChecked() and "time_s" in source.metrics:
                index = int(np.argmin(abs(times - source.metrics["time_s"])))
                self.difference_time.slider.blockSignals(True)
                self.difference_time.slider.setValue(index)
                self.difference_time.slider.blockSignals(False)
                self.difference_time.update_label()
            else:
                index = self.difference_time.slider.value()
            comparison = comparison.transient.snapshots[index]
        self.difference_time.setEnabled(not self.difference_link_time.isChecked() or "time_s" not in source.metrics)
        key = (id(source), id(comparison), layer, field)
        if key not in self._difference_cache:
            from etlayout.visualization.difference import make_field_difference
            result = make_field_difference(source, comparison, layer, field, self.difference_name)
            if "time_s" in source.metrics or "time_s" in comparison.metrics:
                result.configuration["visualization"].update(current_time_s=source.metrics.get("time_s"),
                    comparison_time_s=comparison.metrics.get("time_s"))
            # Derived comparisons can hold large arrays. Retain at most four selections.
            if len(self._difference_cache) >= 4:
                self._difference_cache.pop(next(iter(self._difference_cache)))
            self._difference_cache[key] = result
        return self._difference_cache[key]

    def displayed_provenance(self):
        result = self.display_result or self.view_source_result()
        provenance = dict(result.configuration.get("visualization", {})) if result else {}
        return {**provenance, "configuration_hash": result.configuration_hash if result else "",
                "converged": bool(result and result.converged),
                "time_s": result.metrics.get("time_s") if result else None,
                "applied_drives": result.metrics.get("applied_drives") if result else None,
                "transient_completed": self.result.transient.completed if self.result and self.result.transient else None,
                "whole_run_metrics": {key: value for key, value in self.result.metrics.items() if key.startswith("transient_")} if self.result else {},
                "layer": self.field_layer.currentData(), "field": self.field_combo.currentData(),
                "region": self.field_region.currentData() or "", "region_label": self.field_region.currentText(),
                "colormap": self.colormap_combo.currentData(), "logarithmic": self.log_scale.isChecked(),
                "smooth": self.view.field_smooth, "manual_limits": self.view.field_manual_limits,
                "range_across_saved_times": self.history_range.isChecked()}

    def fields_for_layer(self, layer):
        fields = list(self.result.fields.get(layer, {})) if self.result else []
        if "temperature" in fields:
            fields.append("temperature_rise")
        return fields

    def select_result_field(self, *_):
        self._preferred_field = self.field_combo.currentData()
        self.legend_scale.reset()
        self.update_overlay()

    def fit_result_layer(self):
        if not self.view.fit_field():
            self.log("Choose a field with values on this layer before fitting it.")

    def update_overlay(self, *_):
        if hasattr(self, "guided") and self.guided.layout_editor.active:
            return
        if self._refreshing:
            return
        layer, field = self.field_layer.currentData(), self.field_combo.currentData()
        self.result_controls.setVisible(bool(self.project.layers))
        self.field_combo.setToolTip(self.field_combo.currentText())
        self.view_mode.setVisible(self.difference_source is not None)
        self.view_mode_label.setVisible(self.difference_source is not None)
        self.compare_layers_button.setEnabled(bool(self.result))
        self.key_metrics.setText(("Original run metrics (not differences): " if self.view_mode.currentData() == "difference" else "")
                                 + self._original_key_metrics)
        self.selection_statistics.clear()
        self.history_range.setVisible(bool(self.result and (self.result.transient or self.history_workflow.record and self.history_workflow.record.times)))
        self.history_range.setEnabled(self.view_mode.currentData() == "original")
        try:
            if field and self.legend_scale.details and self.legend_scale.details["field"] != field:
                self.legend_scale.reset()
            available = bool(field and field in self.fields_for_layer(layer))
            if not available and self.legend_scale.lock.isChecked():
                self.legend_scale.reset()
            active = self.active_view_result() if available else None
            derived = active if self.view_mode.currentData() == "difference" else None
            if derived is not self.display_result:
                self.display_sampler = None
            self.display_result = derived
            limits = self.legend_scale.validated_limits(self.log_scale.isChecked()) if available else None
            region = self.field_region.currentData() or ""
            if (limits is None and available and self.history_range.isChecked() and self.result.transient
                    and self.view_mode.currentData() == "original"):
                from etlayout.visualization.history import history_field_limits
                from etlayout.visualization.support import display_limits
                limits = history_field_limits(self.result, layer, field, region=region,
                                              logarithmic=self.log_scale.isChecked())
                limits = display_limits(limits, logarithmic=self.log_scale.isChecked(),
                                        colormap=self.colormap_combo.currentData(),
                                        resolution_scale=field_support(active, layer, field, region=region,
                                                                       logarithmic=self.log_scale.isChecked()).resolution_scale)
                if limits is not None and limits[0] == limits[1]:
                    limits = None
            self.view.show_field(active, layer, field,
                                 self.log_scale.isChecked(), self.vector_toggle.isChecked(),
                                 limits=limits, region=region, colormap=self.colormap_combo.currentData(),
                                 smooth=self.smooth_field.isChecked(), manual_limits=self.legend_scale.lock.isChecked())
            self.legend.setToolTip(self.view.legend)
            isolated = available and self.isolate_layer.isChecked() and not self._show_masks
            self.view.set_layer_focus(layer if isolated else "",
                                      {terminal.id: terminal.layer_id for terminal in self.project.terminals})
            self.isolate_layer.setEnabled(available and not self._show_masks)
            self.log_scale.setEnabled(available)
            raw = active.fields.get(layer, {}).get(field) if available else None
            self.vector_toggle.setEnabled(field in VECTOR_FIELDS and raw is not None and raw.ndim == 2)
            self.vector_toggle.setToolTip("Arrows indicate direction; their length is normalized, not proportional to magnitude.")
            self.smooth_field.setEnabled(available and field in {"potential", "temperature", "temperature_rise"})
            self.legend_scale.setVisible(available and self.view.overlay_item is not None)
            self.fit_layer_button.setEnabled(self.view.overlay_item is not None)
            count = sum(field in self.fields_for_layer(item.id) for item in self.project.layers) if field else 0
            if not self.result:
                message = "Run an analysis to inspect fields on each physical layer."
            elif not field:
                message = "Layout geometry only. Choose a result field to inspect computed values."
            elif not available:
                name = FIELD_LABELS.get(field, (field, ""))[0]
                message = f"{name} is unavailable on {self.field_layer.currentText()}. Choose another layer or field."
                self.legend.setText(message)
            elif self.view.overlay_item is None:
                message = self.view.legend
            else:
                scope = ("Source masks show geometry only" if self._show_masks else
                         "Other layers hidden" if isolated else "Other colors show geometry only")
                region_label = self.field_region.currentText() if region else "All solved regions on this layer"
                message = f"{region_label} · {count}/{len(self.project.layers)} layers support this field · {scope}."
                support = field_support(active, layer, field, region=region, logarithmic=self.log_scale.isChecked())
                areas = active.mesh.areas[support.indices]
                mean = np.average(support.values, weights=areas)
                low, high = support.limits
                unit = FIELD_LABELS.get(field, (field, "SI"))[1]
                low, mean, high = (to_display(value, FIELD_UNITS.get(field, "")) for value in (low, mean, high))
                self.selection_statistics.setText(f"Selected field: {len(support.indices):,} elements · "
                    f"area {areas.sum() * 1e12:.5g} µm² · min {low:.5g} · mean {mean:.5g} · max {high:.5g} {unit}")
                self.selection_statistics.setToolTip("Statistics use only the displayed finite region. Mean is area-weighted; nodal fields use P1 element means. Manual color clipping does not change values.")
                if self.display_result is not None:
                    metadata = self.display_result.configuration["visualization"]
                    difference_label = "Current − comparison · " + metadata["quantity"]
                    self.legend.setText(f"{metadata['quantity'].capitalize()} · {layer}")
                    self.legend.setToolTip(difference_label + " · " + self.view.legend)
                    source_name = self.difference_name
                    if len(source_name) > 35:
                        source_name = source_name[:32] + "…"
                    message = f"Compared with {source_name} · {scope.lower()}."
                    if not active.converged:
                        message = "NOT CONVERGED: one or both source runs. " + message
            self.field_hint.setText(message)
            if active is not None and "time_s" in active.metrics:
                self.field_hint.setText(f"t = {active.metrics['time_s']:.6g} s · " + message)
            self.field_hint.setToolTip(message if self.display_result is None else
                                       f"Current − {self.difference_name}\n{self.field_region.currentText()}\n{message}")
            self.field_layer.setToolTip(self.field_layer.currentText() + "\n" + message)
            self.layer_tree.blockSignals(True)
            if not self._show_masks:
                for index in range(self.layer_tree.topLevelItemCount()):
                    item = self.layer_tree.topLevelItem(index)
                    item.setSelected(item.data(0, Qt.ItemDataRole.UserRole) == layer)
            self.layer_tree.blockSignals(False)
            if self.mesh:
                self.view.show_mesh(active.mesh if active else self.mesh, self.mesh_toggle.isChecked(),
                                    layer=layer if available else None, region=region if available else "")
            else:
                self.view.show_mesh(None, False)
        except (KeyError, ValueError) as error:
            self.display_result = None
            self.display_sampler = None
            self.view.show_field(None, layer, field)
            self.view.show_mesh(self.mesh, False)
            self.view.set_layer_focus("")
            self.fit_layer_button.setEnabled(False)
            message = f"Cannot display this selection: {error}"
            self.field_hint.setText(message)
            self.legend.setText(message)
            self.field_hint.setToolTip(message)
            self.legend.setToolTip(message)
            self.legend_scale.setVisible(bool(field))
        self.workflow_hint.setVisible(self.result is None or self.prepare_job is not None)
        self.time_history.set_context(layer, self.field_region.currentData() or "")
        if hasattr(self, "guided"):
            self.guided.apply_canvas_context()
            self.guided.refresh_probes()

    def toggle_mesh(self, checked):
        self.update_overlay()

    def get_sampler(self):
        if self.display_result:
            if self.display_sampler is None:
                self.display_sampler = FieldSampler(self.display_result)
            return self.display_sampler
        if self.sampler is None and self.result:
            self.sampler = FieldSampler(self.view_source_result())
        return self.sampler

    def probe(self, x, y):
        layer = self.field_layer.currentData()
        field = self.field_combo.currentData()
        active_result = self.display_result or self.view_source_result()
        if not active_result or self.view.overlay_item is None:
            self.log(f"Probe x={x * 1e6:.7g} µm, y={y * 1e6:.7g} µm. No result on this layer.")
            self.statusBar().showMessage("No result on this layer. Select a solved field to probe.", 8000)
            return
        sampler = self.get_sampler()
        value = sampler.sample(layer, field, np.asarray([[x, y]]), region=self.field_region.currentData() or "",
                               logarithmic=self.log_scale.isChecked())[0]
        label, unit = FIELD_LABELS.get(field, (field, "SI"))
        value = to_display(value, FIELD_UNITS.get(field, ""))
        scope = "Current − comparison · " if self.display_result else "Original result · "
        if "time_s" in active_result.metrics:
            scope += f"t = {active_result.metrics['time_s']:.9g} s · "
        row = f"{label}: {value:.8g} {unit}" if np.isfinite(value) else f"{label}: outside active region"
        self.log(f"Probe {layer} · {self.field_region.currentText()} · {scope}"
                 f"at ({x * 1e6:.6g}, {y * 1e6:.6g}) µm\n{row}")
        if self.result.transient and self.display_result is None:
            self.time_history.set_context(layer, self.field_region.currentData() or "", probe=(x, y))
        self.statusBar().showMessage(f"{layer} · {row} · ({x * 1e6:.6g}, {y * 1e6:.6g}) µm", 12000)

    def locate_hotspot(self):
        if not self.result:
            return
        if self.view.overlay_item is not None:
            layer, field = self.field_layer.currentData(), self.field_combo.currentData()
            active = self.display_result or self.view_source_result()
            support = field_support(active, layer, field, region=self.field_region.currentData() or "",
                                    logarithmic=self.log_scale.isChecked())
            if support.nodal:
                index = support.nodes[int(np.argmax(support.data[support.nodes]))]
                point, value = active.mesh.points[index], support.data[index]
            else:
                offset = int(np.argmax(support.values))
                point, value = active.mesh.centroids[support.indices[offset]], support.values[offset]
            self.view.centerOn(point[0] * 1e6, -point[1] * 1e6)
            label, unit = FIELD_LABELS[field]
            value = to_display(value, FIELD_UNITS.get(field, ""))
            self.log(f"Selected-field peak · {self.view_mode.currentText()} · {layer} · {self.field_region.currentText()}: "
                     f"{label} {value:.8g} {unit} at ({point[0] * 1e6:.7g}, {point[1] * 1e6:.7g}) µm.")
            self.view.show_region(Point(point).buffer(max(np.ptp(active.mesh.points[:, 0]),
                                                         np.ptp(active.mesh.points[:, 1])) * .015), "Selected-field peak", "#B5473C")
            return
        candidate = None
        for layer, fields in self.view_source_result().fields.items():
            data = fields.get("temperature")
            if data is not None and np.isfinite(data).any():
                index = int(np.nanargmax(data))
                if candidate is None or data[index] > candidate[0]:
                    candidate = (float(data[index]), layer, self.mesh.points[index])
        if candidate:
            value, layer, point = candidate
            self.field_layer.setCurrentIndex(self.field_layer.findData(layer))
            self.field_combo.setCurrentIndex(self.field_combo.findData("temperature"))
            self.view.centerOn(point[0] * 1e6, -point[1] * 1e6)
            self.log(f"Hotspot {value:.8g} K on {layer} at ({point[0] * 1e6:.7g}, {point[1] * 1e6:.7g}) µm.")
            self.view.show_region(Point(point).buffer(max(np.ptp(self.mesh.points[:, 0]), np.ptp(self.mesh.points[:, 1])) * .015), "Temperature hotspot", "#B5473C")
        else:
            self.log("No temperature field is available. Run a thermal or electrothermal analysis.")

    def add_material(self):
        identifier = "custom_" + uuid.uuid4().hex[:8]
        model = Material(identifier, "Custom material", reference=False)
        self.edit_material_model(model, new=True)

    def edit_material(self):
        identifier = self.material_panel.selected_id()
        if identifier:
            self.edit_material_model(self.project.materials[identifier])

    def edit_material_model(self, material, new=False):
        if self.model_locked():
            return
        if hasattr(self, "guided") and not self.guided.resolve_draft():
            return
        model = deepcopy(material)
        # Editing a project snapshot never writes to the immutable reference database.
        model.reference = False
        dialog = ModelDialog(model, "Project material — " + model.name, self, readonly=("id",))
        if dialog.exec():
            try:
                from etlayout.materials.database import validate_material
                validate_material(dialog.model)
                self.before_edit()
                self.project.materials[dialog.model.id] = dialog.model
                self.changed()
            except Exception as error:
                self.error("Invalid material", error)

    def copy_material(self):
        identifier = self.material_panel.selected_id()
        if identifier:
            material = deepcopy(self.project.materials[identifier])
            material.id = "custom_" + uuid.uuid4().hex[:8]
            material.name += " (copy)"
            material.reference = False
            self.edit_material_model(material, new=True)

    def remove_material(self):
        if self.model_locked():
            return
        if hasattr(self, "guided") and not self.guided.resolve_draft():
            return
        identifier = self.material_panel.selected_id()
        if not identifier:
            return
        if any(layer.material_id == identifier for layer in self.project.layers) or any(interface.material_id == identifier for interface in self.project.interfaces):
            self.error("Material is in use", "Reassign layers / interfaces before removing this material.")
            return
        if QMessageBox.question(self, "Remove project material", f"Remove {identifier} from this project?") == QMessageBox.StandardButton.Yes:
            self.before_edit()
            del self.project.materials[identifier]
            self.changed()

    def material_library(self):
        if self.model_locked():
            return
        dialog = QDialog(self)
        dialog.setWindowTitle("Persistent material library")
        dialog.resize(750, 520)
        layout = QVBoxLayout(dialog)
        note = QLabel("Reference entries are immutable. Custom entries persist in your local SQLite library. Import a snapshot into the project or save a project material as a custom library entry.")
        note.setWordWrap(True)
        layout.addWidget(note)
        search = QLineEdit()
        search.setPlaceholderText("Search material name / ID…")
        layout.addWidget(search)
        listing = QListWidget()
        layout.addWidget(listing)
        def refresh():
            listing.clear()
            for material in self.database.list_materials():
                item = QListWidgetItem(f"{material.name} · {material.id} · {'Reference' if material.reference else 'Custom'}")
                item.setData(Qt.ItemDataRole.UserRole, material.id)
                listing.addItem(item)
        def selected():
            return listing.currentItem().data(Qt.ItemDataRole.UserRole) if listing.currentItem() else None
        def filtered(text):
            for i in range(listing.count()):
                listing.item(i).setHidden(text.lower() not in listing.item(i).text().lower())
        search.textChanged.connect(filtered)
        def into_project():
            key = selected()
            if key:
                self.before_edit()
                self.project.materials[key] = self.database.get(key)
                self.changed()
        def save_custom():
            key = self.material_panel.selected_id()
            if key:
                material = deepcopy(self.project.materials[key])
                material.id = "user-" + uuid.uuid4().hex[:12]
                material.reference = False
                self.database.save(material)
                refresh()
        def duplicate():
            key = selected()
            if key:
                material = self.database.duplicate(key)
                editor = ModelDialog(material, "Edit custom library material", dialog, readonly=("id",))
                if editor.exec():
                    self.database.save(editor.model)
                refresh()
        def edit_custom():
            key = selected()
            if not key:
                return
            material = self.database.get(key)
            if material.reference:
                duplicate()
                return
            editor = ModelDialog(material, "Edit custom library material", dialog, readonly=("id",))
            if editor.exec():
                try:
                    self.database.save(editor.model)
                    refresh()
                except Exception as error:
                    self.error("Invalid material", error)
        def delete():
            key = selected()
            if key and QMessageBox.question(dialog, "Delete custom material", f"Delete {key} from the user library?") == QMessageBox.StandardButton.Yes:
                try:
                    self.database.delete(key)
                    refresh()
                except Exception as error:
                    self.error("Cannot delete", error)
        def export():
            path, _ = QFileDialog.getSaveFileName(dialog, "Export library", "materials.json", "JSON (*.json)")
            if path:
                self.database.export_json(path)
        def restore():
            self.database.restore_defaults()
            refresh()
        def import_json():
            path, _ = QFileDialog.getOpenFileName(dialog, "Import custom materials", "", "JSON (*.json)")
            if path:
                try:
                    self.database.import_json(path)
                    refresh()
                except Exception as error:
                    self.error("Cannot import materials", error)
        for actions in [[("Into project", into_project), ("Save project entry", save_custom), ("Duplicate / edit", edit_custom), ("Delete", delete)],
                        [("Import JSON", import_json), ("Export library", export), ("Restore references", restore)]]:
            buttons = QHBoxLayout()
            for title, callback in actions:
                button = QPushButton(title)
                button.clicked.connect(callback)
                buttons.addWidget(button)
            layout.addLayout(buttons)
        refresh()
        dialog.exec()

    def run_sweep_dialog(self):
        if self.model_locked():
            return
        spec = deepcopy(self.project.sweeps[-1]) if self.project.sweeps else SweepSpec(
            parameter=f"terminals.{self.project.terminals[-1].id}.value" if self.project.terminals else "mesh.max_size",
            values=[0., .1, .2] if self.project.terminals else [2e-6, 5e-6, 10e-6], mode=self.project.solver.mode)
        choices, parameter_units, drive_units = {}, {}, {}
        shadowed = {drive.target: index for index, drive in enumerate(self.project.transient.drives)} if self.project.transient.enabled else {}
        owned = {landing.terminal_id for port in self.project.power.ports for landing in port.landings}
        def parameter(path, label, unit):
            drive_units[path] = unit
            if path in shadowed:
                return
            choices[path] = label + (f" ({unit})" if unit else "")
            parameter_units[path] = unit
        for terminal in self.project.terminals:
            if terminal.kind in {"voltage", "current", "density", "gate"} and terminal.id not in owned:
                parameter(f"terminals.{terminal.id}.value", f"{terminal.name or terminal.id} · {terminal.kind}",
                          {"voltage": "V", "current": "A", "density": "A/m²", "gate": "V"}[terminal.kind])
        for port in self.project.power.ports:
            if port.kind in {"voltage", "current"}:
                parameter(f"power.ports.{port.id}.value", f"{port.name or port.id} · physical-port drive",
                          "V" if port.kind == "voltage" else "A")
        for layer in self.project.layers:
            parameter(f"layers.{layer.id}.thickness", f"{layer.name} · thickness", "µm")
            if layer.role == "conductor":
                kind = layer.electrical.kind
                if kind in {"semiconductor", "hemt"}:
                    parameter(f"layers.{layer.id}.electrical.mobility", f"{layer.name} · carrier mobility", "m²/(V s)")
                if kind == "hemt" and not layer.electrical.gate_regions:
                    parameter(f"layers.{layer.id}.electrical.gate_voltage", f"{layer.name} · global gate bias", "V")
                if kind == "semiconductor":
                    parameter(f"layers.{layer.id}.electrical.carrier_density", f"{layer.name} · volume carrier density", "1/m³")
                if kind == "sheet":
                    parameter(f"layers.{layer.id}.electrical.sheet_resistance.value", f"{layer.name} · sheet resistance", "Ω/square")
        for sink in self.project.thermal_boundaries:
            if sink.kind != "insulated":
                parameter(f"thermal_boundaries.{sink.id}.value", f"{sink.id} · {sink.kind}",
                          {"temperature": "K", "coefficient": "W/(m² K)", "resistance_area": "m² K/W", "resistance": "K/W"}[sink.kind])
                if sink.kind != "temperature":
                    parameter(f"thermal_boundaries.{sink.id}.ambient", f"{sink.id} · ambient temperature", "K")
        for source in self.project.heat_sources:
            parameter(f"heat_sources.{source.id}.value", f"{source.id} · prescribed heat",
                      "W" if source.kind == "total" else "W/m²")
        if self.project.transient.enabled:
            for index, drive in enumerate(self.project.transient.drives):
                prefix = f"transient.drives.{index}.waveform"
                unit = drive_units.get(drive.target, "SI")
                names = (["value"] if drive.waveform.kind == "constant" else
                         ["low", "high", "delay", "width", "period", "rise_time", "fall_time"]
                         if drive.waveform.kind == "pulse" else [])
                for name in names:
                    parameter(f"{prefix}.{name}", f"{drive.target} · waveform {name.replace('_', ' ')}",
                              unit if name in {"value", "low", "high"} else "s")
            for name in ("stop_time", "time_step", "output_interval"):
                parameter(f"transient.{name}", "Transient · " + name.replace("_", " "), "s")
            if spec.parameter in shadowed:
                index = shadowed[spec.parameter]
                kind = self.project.transient.drives[index].waveform.kind
                if kind in {"constant", "pulse"}:
                    spec.parameter = f"transient.drives.{index}.waveform.{'value' if kind == 'constant' else 'high'}"
        parameter("mesh.max_size", "Global maximum mesh size", "µm")
        parameter("solver.initial_temperature", "Initial temperature", "K")
        if spec.parameter not in choices and choices:
            from etlayout.models import SimulationError
            from etlayout.simulation.sweep import get_parameter, set_parameter
            try:
                if spec.parameter in shadowed:
                    raise SimulationError("A waveform overrides this base value")
                value = get_parameter(self.project, spec.parameter)
                set_parameter(deepcopy(self.project), spec.parameter, value)
                parameter(spec.parameter, spec.parameter, "SI")
            except SimulationError:
                spec.parameter = next(iter(choices))
        dialog = ModelDialog(spec, "Parameter sweep", self, choices={"parameter": choices}, parameter_units=parameter_units)
        if dialog.exec():
            try:
                initial = self.initial_state_argument() or {}
                self.before_edit()
                self.project.sweeps.append(dialog.model)
                self.dirty = True
                self.start_job("sweep", {"spec": dialog.model, **initial})
            except ValueError as error:
                self.error("Initial state needed", error)

    def run_convergence_dialog(self):
        if self.model_locked():
            return
        dialog = QDialog(self)
        dialog.setWindowTitle("Mesh convergence study")
        dialog.resize(710, 410)
        layout = QVBoxLayout(dialog)
        layout.addWidget(QLabel("Global mesh sizes (µm), comma-separated, coarse to fine"))
        sizes_entry = QLineEdit(", ".join(f"{self.project.mesh.max_size * factor * 1e6:g}" for factor in (2, 1, .5)))
        sizes_entry.setObjectName("mesh_study_sizes")
        layout.addWidget(sizes_entry)
        note = QLabel("Track resistance, power, temperature and every terminal current. Select drawn profiles to also compare the same physical cut across meshes; samples and relative changes are retained and exported.")
        note.setWordWrap(True)
        layout.addWidget(note)
        profiles = QListWidget()
        profiles.setObjectName("mesh_study_profiles")
        for profile in self.profiles.profiles:
            item = QListWidgetItem(f"{profile['name']} · {profile['layer']} · {FIELD_LABELS.get(profile['field'], (profile['field'], 'SI'))[0]}")
            item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            item.setCheckState(Qt.CheckState.Checked)
            profiles.addItem(item)
        layout.addWidget(profiles)
        if not self.profiles.profiles:
            layout.addWidget(QLabel("Draw a result line profile first to include profile convergence."))
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.rejected.connect(dialog.reject)
        def accept():
            try:
                sizes = [float(value) * 1e-6 for value in sizes_entry.text().split(",")]
                if not sizes or not all(np.isfinite(value) and value > 0 for value in sizes):
                    raise ValueError("Mesh sizes must be finite and positive.")
                requests = []
                for index, profile in enumerate(self.profiles.profiles):
                    if profiles.item(index).checkState() == Qt.CheckState.Checked:
                        requests.append({"layer_id": profile["layer"], "field": profile["field"],
                                         "start": profile["coordinates"][0].tolist(),
                                         "end": profile["coordinates"][-1].tolist(), "count": len(profile["values"]),
                                         "time_s": profile.get("provenance", {}).get("time_s"),
                                         "region": profile.get("provenance", {}).get("region", "")})
                initial = self.initial_state_argument() or {}
                if initial and any(size != self.project.mesh.max_size for size in sizes):
                    raise ValueError("Saved thermal states require the same mesh. Choose uniform or steady initialization before a mesh refinement study.")
                dialog.accept()
                self.start_job("convergence", {"sizes": sizes, "profiles": requests, **initial})
            except ValueError as error:
                QMessageBox.warning(dialog, "Invalid mesh sizes", str(error))
        buttons.accepted.connect(accept)
        layout.addWidget(buttons)
        dialog.exec()

    def compare_dialog(self):
        if self.model_locked():
            return
        paths, _ = QFileDialog.getOpenFileNames(self, "Compare current project with saved designs", "", "Projects (*.etl *.etlayout *.etproj *.zip)")
        if paths:
            try:
                from etlayout.simulation.sweep import resolve_study_initial_state
                loaded = [self.load_project_data(path) for path in paths]
                projects = [deepcopy(self.project), *[item[0] for item in loaded]]
                sources = [self.initial_state_argument() or {}]
                sources += [resolve_study_initial_state(project, result) for project, result in loaded]
                self.start_job("compare", {"projects": projects,
                    "initial_results": [source.get("initial_result") for source in sources],
                    "resumes": [source.get("resume", False) for source in sources]})
            except Exception as error:
                self.error("Cannot load comparison", error)

    def compare_maps_dialog(self):
        from etlayout.gui.comparison import MapComparisonDialog
        candidates = [(str(getattr(row, "name", f"{self.study_panel.parameter} = {getattr(row, 'value', '')}")), row.result)
                      for row in self.studies if row.result is not None]
        if len(candidates) < 2:
            candidates = [(self.project.name, self.result)] if self.result else []
            paths, _ = QFileDialog.getOpenFileNames(self, "Select saved design results for side-by-side maps", "", "Projects (*.etl *.etlayout *.etproj *.zip)")
            if not paths:
                return
            try:
                for path in paths:
                    project, result = self.load_project_data(path)
                    if result:
                        candidates.append((project.name, result))
                if len(candidates) < 2:
                    raise ValueError("Select at least two projects with saved computed fields, or run a comparison / sweep first.")
            except Exception as error:
                self.error("Cannot load field maps", error)
                return
        dialog = MapComparisonDialog(candidates, self)
        dialog.exec()

    def compare_layers_dialog(self):
        if self.result is None:
            self.error("No layer results", "Run an analysis before comparing layers.")
            return
        from etlayout.gui.comparison import MapComparisonDialog
        source = self.view_source_result()
        time_note = f" · t = {source.metrics['time_s']:.9g} s" if "time_s" in source.metrics else ""
        dialog = MapComparisonDialog([(self.project.name + time_note, source)], self)
        dialog.setWindowTitle("Compare layers · original run" + time_note)
        layer = self.field_layer.currentData()
        if dialog.layers[0].findData(layer) >= 0:
            dialog.layers[0].setCurrentIndex(dialog.layers[0].findData(layer))
        if dialog.layers[1].currentData() == layer and dialog.layers[1].count() > 1:
            dialog.layers[1].setCurrentIndex((dialog.layers[1].currentIndex() + 1) % dialog.layers[1].count())
        field = self.field_combo.currentData()
        if dialog.field.findData(field) >= 0:
            dialog.field.setCurrentIndex(dialog.field.findData(field))
        for combo, value in [(dialog.regions[0], self.field_region.currentData()),
                             (dialog.colormap, self.colormap_combo.currentData())]:
            if combo.findData(value) >= 0:
                combo.setCurrentIndex(combo.findData(value))
        dialog.logarithmic.setChecked(self.log_scale.isChecked())
        dialog.smooth.setChecked(self.smooth_field.isChecked())
        if self.view_mode.currentData() == "original":
            dialog.range_control.restore_state(self.legend_scale.capture_state())
        dialog.exec()

    def field_difference_dialog(self):
        if self.result is None or not self.field_combo.currentData():
            self.error("No reference field", "Run a simulation and select a field to compare.")
            return
        path, _ = QFileDialog.getOpenFileName(self, "Compare current field to saved result", "", "Projects (*.etl *.etlayout *.etproj *.zip)")
        if not path:
            return
        try:
            project, other = self.load_project_data(path)
            if other is None:
                raise ValueError("The selected project has no saved result.")
            self.load_difference_result(other, project.name)
        except Exception as error:
            self.error("Field comparison failed", error)

    def load_difference_result(self, result, name="Comparison"):
        self.difference_source, self.difference_name = result, name
        self.difference_time.set_result(result)
        self.difference_link_time.setVisible(result.transient is not None)
        self._difference_cache.clear()
        self.view_mode.setCurrentIndex(self.view_mode.findData("difference"))
        self.select_view_mode()

    def confirm_discard(self, continuation=None):
        if self.save_job or self._save_queue:
            self.statusBar().showMessage("A project save is still active. Wait for completion or cancel the save before changing project.")
            return False
        if hasattr(self, "guided") and not self.guided.resolve_draft():
            return False
        if self.job or (self.guided.power and self.guided.power.job):
            self.error("Computation running", "Cancel the running computation before changing project.")
            return False
        dirty = self.inspection_context["dirty"] if self.inspection_context else self.dirty
        if not dirty:
            return True
        answer = QMessageBox.question(self, "Unsaved project", "Save changes before continuing?",
                                      QMessageBox.StandardButton.Save | QMessageBox.StandardButton.Discard | QMessageBox.StandardButton.Cancel)
        if answer == QMessageBox.StandardButton.Cancel:
            return False
        if answer == QMessageBox.StandardButton.Save:
            self._save_continuation = continuation
            if not self.save():
                self._save_continuation = None
            return False
        return True

    def new_project(self):
        if not self.confirm_discard(self.new_project):
            return
        wizard = NewProjectWizard(self)
        if wizard.exec():
            project = Project(name=wizard.name.text())
            project.solver.mode = wizard.mode.currentData()
            project.materials = {m.id: m for m in self.database.list_materials()}
            self.set_project(project)
            self.dirty = True
            if wizard.import_after.isChecked():
                self.import_layout()
            self.update_title()

    def load_example(self, name, scenario=None):
        if not self.confirm_discard(lambda: self.load_example(name, scenario)):
            return
        try:
            from etlayout.examples import make_example
            destination = self.recovery_path.parent / "examples"
            project = make_example(name, destination, scenario=scenario)
            self.set_project(project)
            self.log(f"Loaded {project.name}. All physical parameters remain editable. Press F5 to solve.")
        except Exception as error:
            self.error("Example could not be loaded", error)

    def choose_example_scenario(self):
        if self.model_locked():
            return
        from etlayout.example_scenarios import scenarios
        try:
            choices = scenarios(self.project.name)
        except ValueError:
            self.statusBar().showMessage("Open a built-in example first to choose a named operating scenario.")
            return
        labels = [item.title for item in choices]
        selected, accepted = QInputDialog.getItem(self, "Example operating scenario", "Regenerate an editable project; pending edits are resolved first.\nJoule demonstrations change electrical drives, preserving layout/materials/cooling.\nFailure scenarios use a chosen engineering limit, not a material rating.", labels, 0, False)
        if accepted:
            self.load_example(self.project.name, choices[labels.index(selected)].id)

    def apply_example_view_defaults(self):
        """Choose a useful initial map once when opening a catalog project."""
        from etlayout.example_catalog import EXAMPLE_CATALOG

        info = EXAMPLE_CATALOG.get(self.project.name)
        if info is None:
            return
        index = self.field_layer.findData(info.layer)
        if index < 0:
            return  # A modified example may no longer contain the reference layer.
        self._preferred_field = info.field if not self.result or info.field in self.fields_for_layer(info.layer) else None
        self.field_layer.setCurrentIndex(index)
        self.populate_fields()
        index = self.guided.active_layer.findData(info.layer)
        if index >= 0:
            self.guided.active_layer.setCurrentIndex(index)

    def set_project(self, project, result=None, path=None, history=None):
        if hasattr(self, "guided") and not self.guided.resolve_draft():
            return False
        incoming_store = project.__dict__.pop("_history_store", None)
        if not self.history_workflow.close():
            if incoming_store:
                incoming_store.close()
            self.statusBar().showMessage("History read is still stopping; retry this transition after the worker stops.")
            return False
        self.history_opener.cancel()
        self._drop_pending_catalog()
        self.cancel_geometry()
        self.inspection_context = None
        self.inspection_bar.hide()
        self.clear_analysis(clear_studies=True)
        self._preferred_field = None
        self._region_choices.clear()
        self._region_layer = None
        self._run_view_states.clear()
        self.project, self.result, self.path = project, result, Path(path) if path else None
        document_id(project)
        self.document_revision += 1
        self.document_epoch = str(uuid.uuid4())
        self.mesh = result.mesh if result else None
        if hasattr(self, "guided"):
            self.guided.project_replaced()
        self.sampler = None
        self.undo_history.clear()
        self.redo_history.clear()
        self.dirty = False
        self.run_history = list(incoming_store.records) if incoming_store else list(history or [])
        self.history_workflow.attach(incoming_store)
        # Resource review hands history to the GUI once; do not hide old arrays
        # in Project snapshots, undo records or future compute-process inputs.
        project.__dict__.pop("_resource_history", None)
        if result:
            self.remember_result(result)
        self.history_panel.set_results(self.run_history)
        self.profiles.profiles.clear()
        self.profiles.selector.clear()
        self.profiles.refresh()
        self.studies = []
        self.study_panel.set_rows([])
        self.cross_section.plot.clear()
        self.last_cut = None
        self.metrics_table.setRowCount(0)
        self.terminals_table.setRowCount(0)
        self.refresh_project()
        self.apply_example_view_defaults()
        self.set_running(False)
        if result:
            self.show_result(result)
        elif incoming_store:
            self.history_workflow.open_current()

    def load_project_data(self, path, *, external_policy=None, lazy=False):
        from etlayout.gui.resources import open_reviewed_project
        opened = open_reviewed_project(path, self, policy=external_policy, lazy=lazy,
                                       cache_bytes=self.history_cache_mib * 1024**2)
        if opened is None:
            raise SimulationError("Project opening cancelled; the current project was retained")
        return opened

    def review_project_resources(self):
        if self.model_locked():
            self.log("Return to the working setup and finish the active job before reviewing its resources.")
            return
        inspection = getattr(self.project, "_resource_inspection", None)
        if inspection is None:
            self.relink_layout()
            return
        if hasattr(self, "guided") and not self.guided.resolve_draft():
            return
        from etlayout.gui.resources import resolve_reviewed_project
        if (inspection.archive_data is None and inspection.metadata.get("schema_version") == 3
                and getattr(inspection, "_archive_sha256", None)):
            self._catalog_revision = self.document_revision
            self._catalog_review_restore = self.project
            self.history_opener.submit(inspection.source, self.document_epoch,
                                       cache_bytes=self.history_cache_mib * 1024**2, inspection=inspection)
            return
        try:
            opened = resolve_reviewed_project(inspection.for_project(self.project), self)
            if opened is None:
                return
            project, result = opened
            was_dirty = self.dirty
            self.project, self.result = project, result
            self.mesh = result.mesh if result else None
            for run in getattr(project, "_resource_history", []):
                self.remember_result(run)
            if result:
                self.remember_result(result)
            self.refresh_project(preserve_view=True)
            if result:
                self.render_result(result)
            self.dirty = was_dirty
            self.set_running(False)
            self.update_title()
            self.log("Project resources remain unresolved" if getattr(project, "_resource_unresolved", False)
                     else "Resolved resources for the current edited project")
        except Exception as error:
            self.error("Cannot review project resources", error)

    def open_project(self, path=None, *, external_policy=None):
        if not self.confirm_discard(lambda: self.open_project(path, external_policy=external_policy)):
            return
        if not isinstance(path, (str, Path)):
            path, _ = QFileDialog.getOpenFileName(self, "Open project", "", "Electrothermal projects (*.etl *.etlayout *.etproj *.zip);;All files (*)")
        if path:
            if self.history_lazy_enabled:
                self._catalog_revision = self.document_revision
                self._drop_pending_catalog()
                self.history_opener.submit(path, self.document_epoch, policy=external_policy,
                                           cache_bytes=self.history_cache_mib * 1024**2)
                self.statusBar().showMessage("Opening archive metadata in a worker; the current project remains available…")
                return
            try:
                project, result = self.load_project_data(path, external_policy=external_policy, lazy=False)
                self.set_project(project, result, path, getattr(project, "_resource_history", []))
                self.log("Opened project metadata" if getattr(project, "_resource_unresolved", False) else "Opened project")
            except Exception as error:
                self.error("Cannot open project", error)

    def cancel_catalog(self):
        self.history_opener.cancel()
        self._drop_pending_catalog()
        if self._save_continuation == self._apply_pending_catalog:
            self._save_continuation = None

    def _catalog_busy(self, busy):
        self.history_panel.set_loading(busy or bool(self.history_workflow.loader.jobs))

    def _drop_pending_catalog(self):
        self._catalog_review_restore = None
        pending, self._pending_catalog = self._pending_catalog, None
        if pending and pending[3] is not None:
            pending[3].close()

    def _catalog_opened(self, request, project, store):
        self._accept_catalog(request, project, None, store)

    def _accept_catalog(self, request, project, result, store=None):
        current = self.history_opener.request
        if request.epoch != self.document_epoch or current is None or request.generation != current.generation:
            if store:
                store.close()
            return
        power = getattr(self.guided, "power", None)
        if self.job or (power and power.job) or not self.guided.resolve_draft():
            if store:
                store.close()
            return
        self._pending_catalog = (request, project, result, store)
        self._apply_pending_catalog()

    def _apply_pending_catalog(self):
        if self._pending_catalog and (self.save_job or self._save_queue
                or any(job.isRunning() for job in self.history_workflow.loader.jobs)):
            self.history_workflow.loader.cancel()
            QTimer.singleShot(25, self._apply_pending_catalog)
            return
        if self._pending_catalog is None:
            return
        # A read cancelled above can finish after another draft or edit was made.
        # Recheck the transition at its commit point, not just when metadata arrived.
        power = getattr(self.guided, "power", None)
        if self.job or (power and power.job) or not self.guided.resolve_draft():
            self._drop_pending_catalog()
            return
        continuation = self._apply_pending_catalog
        if self._catalog_review_restore is not self.project and self.document_revision != self._catalog_revision:
            if not self.confirm_discard(continuation):
                if self._save_continuation != continuation:
                    self._drop_pending_catalog()
                return
            self._catalog_revision = self.document_revision
        pending, self._pending_catalog = self._pending_catalog, None
        request, project, result, store = pending
        current = self.history_opener.request
        if request.epoch != self.document_epoch or current is None or request.generation != current.generation:
            if store:
                store.close()
            return
        if self._catalog_review_restore is self.project:
            self._catalog_review_restore = None
            if store is None and getattr(project, "_resource_unresolved", False):
                return
            working = self.project
            inspection = getattr(working, "_resource_inspection", None)
            declared = inspection.project.layout if inspection else None
            if declared and (working.layout.path != declared.path or working.layout.sha256 != declared.sha256):
                if store:
                    store.close()
                self.history_workflow.issue(
                    "The layout source declaration changed during resource review. "
                    "Use File → Relink layout to review the new source; the working setup was retained.")
                return
            if not self.history_workflow.close():
                if store:
                    store.close()
                return
            # Resource approval updates the existing document rather than replacing
            # it: cell/instance choices, undo, studies and view context stay owned.
            view, workspace = self.capture_view_state(), self.guided.capture_context()
            working.layout.path, working.layout.sha256 = project.layout.path, project.layout.sha256
            working._resource_unresolved = False
            working.__dict__.pop("_resource_inspection", None)
            if store:
                working._archive_format = store.archive_format
                self.run_history = list(store.records)
                self.history_workflow.attach(store)
            self.refresh_project(preserve_view=True)
            self.guided.restore_context(workspace)
            self.restore_view_state(view)
            self.set_running(False)
            if store:
                self.history_workflow.open_current()
            self.update_title()
            self.log("Resolved resources for the current edited project")
            return
        if self.set_project(project, result, request.path, getattr(project, "_resource_history", [])) is False:
            if store:
                store.close()
            return
        self.log("Opened metadata-first archive; recorded fields load on demand" if store else "Opened project")

    def _catalog_legacy(self, request):
        if request.epoch != self.document_epoch:
            return
        try:
            project, result = self.load_project_data(request.path, external_policy=request.policy)
            self._accept_catalog(request, project, result)
        except Exception as error:
            self._catalog_failed(request, error)

    def _catalog_review(self, request, inspection, error):
        if request.epoch != self.document_epoch:
            return
        from etlayout.gui.resources import ResourceReviewDialog, resolve_reviewed_project
        from etlayout.io.resources import ExternalResourcePolicy
        if inspection is None:
            if getattr(error, "reason", "") != "network":
                self._catalog_failed(request, error)
                return
            if QMessageBox.question(self, "Network project location",
                    "Allow network access for this selected project container? Referenced outside-root resources still need their own approval.",
                    QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                    QMessageBox.StandardButton.No) != QMessageBox.StandardButton.Yes:
                return
            policy = ExternalResourcePolicy(allow_outside_root=request.policy.allow_outside_root,
                allow_network=True, approved_resources=request.policy.approved_resources)
            self.history_opener.submit(request.path, request.epoch, policy=policy, cache_bytes=request.cache_bytes)
            return
        dialog = ResourceReviewDialog(inspection, error, self)
        try:
            if dialog.exec() != QDialog.DialogCode.Accepted:
                return
            if dialog.action == "unresolved":
                self._accept_catalog(request, inspection.unresolved_project(), None)
            elif dialog.action == "relink":
                policy = ExternalResourcePolicy(allow_network=dialog.network.isChecked(),
                                                approved_resources=frozenset({"layout"}))
                opened = resolve_reviewed_project(dialog.inspection, self, policy=policy)
                if opened:
                    self._accept_catalog(request, *opened)
            else:
                self.history_opener.submit(request.path, request.epoch, policy=dialog.policy(),
                                           cache_bytes=request.cache_bytes, inspection=inspection)
        except Exception as failure:
            self._catalog_failed(request, failure)
        finally:
            dialog.deleteLater()

    def _catalog_failed(self, request, error):
        if request.epoch == self.document_epoch:
            self._catalog_review_restore = None
            self.history_workflow.issue("Opening the selected project failed: " + str(error))

    def import_layout(self, path=None):
        if self.model_locked():
            self.log("Finish the current computation and return to the editable project before importing a layout.")
            return
        if not isinstance(path, (str, Path)):
            path, _ = QFileDialog.getOpenFileName(self, "Import GDSII or OASIS", "", "Layout files (*.gds *.gdsii *.oas *.oasis)")
        if not path:
            return
        self.start_job("import", str(Path(path).resolve()))

    def finish_import(self, document):
        try:
            wizard = ImportWizard(document, self.project, self)
            if wizard.exec():
                if self.project.layers and QMessageBox.question(self, "Replace physical mappings", "Replace the current layout, physical layers, contacts and boundary conditions with the imported mappings?") != QMessageBox.StandardButton.Yes:
                    return
                self.before_edit()
                self.project.layout = LayoutSource(str(Path(document.path).resolve()), wizard.cell.currentText(),
                                                   sha256=document.source_sha256, dbu_um=document.dbu_um)
                self.project.layers = wizard.layers()
                self.project.interfaces = []
                self.project.terminals = []
                self.project.thermal_boundaries = []
                self.project.heat_sources = []
                self.project.roi = None
                self.project.display_roi = None
                self.project.mesh.refinements = []
                self.changed()
                self.view.fit_all()
                self.log(f"Imported layout, cell {self.project.layout.cell}. Review stack, via masks and terminal conditions before solving.")
        except Exception as error:
            self.error("Layout import failed", error)

    def relink_layout(self):
        if self.model_locked():
            self.log("Return to the editable project and finish the computation before relinking its layout.")
            return
        path, _ = QFileDialog.getOpenFileName(self, "Relink layout file", "", "Layout files (*.gds *.gdsii *.oas *.oasis)")
        if path:
            try:
                from etlayout.gui.resources import review_layout_relink
                reviewed = review_layout_relink(self.project, path, self.path, self)
                if reviewed is None:
                    return
                project, _ = reviewed
                self.before_edit()
                self.project.layout = project.layout
                self.project._resource_unresolved = getattr(project, "_resource_unresolved", False)
                if self.project._resource_unresolved:
                    self.project._resource_inspection = project._resource_inspection
                self.changed()
                self.log("Relinked source. Review resource status and select a cell in the hierarchy if its name changed.")
            except Exception as error:
                self.error("Cannot relink", error)

    def save(self):
        """Queue an immutable revision; True means accepted, not yet durable."""
        if hasattr(self, "guided") and not self.guided.resolve_draft():
            return False
        path = self.inspection_context["path"] if self.inspection_context else self.path
        if path is None or Path(path).suffix.lower() == ".json":
            return self.save_as()
        return self._queue_save(path)

    def save_as(self, *, archive_format=2):
        if hasattr(self, "guided") and not self.guided.resolve_draft():
            return False
        context = self.inspection_context
        project = context["project"] if context else self.project
        current_path = context["path"] if context else self.path
        suggested = Path(current_path).with_suffix(".etlayout") if current_path else Path(project.name + ".etlayout")
        caption = "Save content-addressed project (schema 3)" if archive_format == 3 else "Save project"
        path, _ = QFileDialog.getSaveFileName(self, caption, str(suggested), "Electrothermal project (*.etlayout)")
        if not path:
            return False
        destination = Path(path)
        if not destination.suffix:
            destination = destination.with_suffix(".etlayout")
        return self._queue_save(destination, archive_format=archive_format)

    def _queue_save(self, path, *, recovery=False, archive_format=None):
        project = self.inspection_context["project"] if self.inspection_context else self.project
        archive_format = 2 if recovery else (archive_format or getattr(project, "_archive_format", 2))
        candidates = ([self.save_job.request] if self.save_job else []) + self._save_queue
        for request in candidates:
            if (request.epoch == self.document_epoch and request.snapshot.revision == self.document_revision
                    and request.snapshot.archive_format == archive_format
                    and (request.recovery_store is not None) == recovery
                    and (recovery or request.destination.absolute() == Path(path).absolute())):
                self.statusBar().showMessage("This document revision is already queued for saving")
                return True
        source_owner = None
        try:
            if not recovery and Path(path).suffix.lower() == ".json":
                raise SimulationError("JSON is a metadata format. Choose a .etlayout destination for the portable project archive.")
            project = self.inspection_context["project"] if self.inspection_context else self.project
            result = self.inspection_context["result"] if self.inspection_context else self.result
            result, history, source_owner = self.history_workflow.save_inputs(project, result, self.run_history)
            snapshot = SaveSnapshot.capture(project, result, history, revision=self.document_revision,
                                            archive_format=archive_format, source_lease=source_owner)
            store = self._recovery_owner() if recovery else None
            self._save_sequence += 1
            request = SaveRequest(snapshot, Path(path), self.document_epoch, self._save_sequence, store,
                str(self.inspection_context["path"] or "") if self.inspection_context else str(self.path or ""),
                () if recovery else SaveOrder.reserve(path), cleanup_store=self.recovery_store)
            if not recovery:
                self._latest_manual_save = request.sequence
            # One latest pending manual revision and one latest recovery revision.
            # An active worker is never fed live state or retargeted in place.
            for old in self._save_queue:
                if (old.recovery_store is None) == (store is None):
                    old.snapshot.release_sources()
            self._save_queue = [old for old in self._save_queue
                                if (old.recovery_store is None) != (store is None)]
            self._save_queue.append(request)
            self._save_queue.sort(key=lambda old: old.recovery_store is not None)
            if self.save_job is None:
                self._start_next_save()
            else:
                self.statusBar().showMessage("Latest project revision queued for saving")
            return True
        except Exception as error:
            if source_owner:
                source_owner.close()
            if recovery:
                self._save_failed(None, str(error), recovery=True)
            else:
                self.error("Cannot prepare project save", error)
            return False

    def _start_next_save(self):
        if not self._save_queue:
            self.save_progress.hide()
            self.cancel_save_button.hide()
            continuation, self._save_continuation = self._save_continuation, None
            dirty = self.inspection_context["dirty"] if self.inspection_context else self.dirty
            if continuation and self._save_success and not dirty:
                QTimer.singleShot(0, continuation)
            return
        request = self._save_queue.pop(0)
        job = SaveJob(request, self)
        self.save_job = job
        self._save_success = False
        self.save_progress.setValue(0)
        self.save_progress.show()
        self.cancel_save_button.show()
        self.statusBar().showMessage("Saving recovery snapshot…" if request.recovery_store else f"Saving {request.destination.name}…")
        job.progress.connect(lambda fraction, message: self._save_progress(job, fraction, message))
        job.saved.connect(self._save_completed)
        job.failed.connect(self._save_failed)
        job.cancelled.connect(self._save_cancelled)
        job.finished.connect(lambda: self._save_thread_finished(job))
        job.start()

    def _save_progress(self, job, fraction, message):
        if self.save_job is job:
            self.save_progress.setValue(round(100 * fraction))
            self.statusBar().showMessage(message)

    def _save_completed(self, request, entry):
        snapshot = request.snapshot
        self._save_success = True
        if entry is not None:
            self.recovery_path = request.recovery_store.snapshot_path(entry)
            self._recovery_current_path = self.recovery_path
            self.recovery_status.setText("Recovery saved")
            self.recovery_status.setToolTip(str(self.recovery_path))
            self._autosave_error = ""
            return
        if request.epoch == self.document_epoch:
            if request.sequence == self._latest_manual_save:
                if self.inspection_context:
                    self.inspection_context["path"] = request.destination
                else:
                    self.path = request.destination
            working = self.inspection_context["project"] if self.inspection_context else self.project
            working._archive_format = snapshot.archive_format
            if snapshot.revision == self.document_revision:
                if self.inspection_context:
                    self.inspection_context["dirty"] = False
                else:
                    self._dirty = False
            if self.save_job and self.save_job.cleanup_errors:
                self.log("Saved project, but recovery cleanup failed: " + "; ".join(self.save_job.cleanup_errors))
            self.recovery_status.clear()
            self.update_title()
        self.log(f"Saved revision {snapshot.revision} to {request.destination}" +
                 ("; newer edits remain unsaved" if snapshot.revision != self.document_revision else ""))

    def _save_failed(self, request, message, recovery=False):
        self._save_success = False
        self._save_continuation = None
        if not recovery and (request is None or request.recovery_store is None):
            self._drop_pending_catalog()
        if recovery or (request is not None and request.recovery_store is not None):
            self.recovery_status.setText("Recovery failed — save your project")
            self.recovery_status.setToolTip(message)
            if self._autosave_error != message:
                self.log(f"Automatic recovery could not be saved: {message}. Use File → Save project as to choose a writable location.")
            self._autosave_error = message
        else:
            self.error("Cannot save project", message)

    def _save_cancelled(self, request, message):
        self._drop_pending_catalog()
        self._save_success = False
        self._save_continuation = None
        self.log(message)

    def _save_thread_finished(self, job):
        if self.save_job is job:
            self.save_job = None
        job.deleteLater()
        if self._close_after_save and not self._save_queue:
            self._close_after_save = False
            QTimer.singleShot(0, self.close)
        else:
            self._start_next_save()

    def cancel_saving(self):
        self._drop_pending_catalog()
        for request in self._save_queue:
            request.snapshot.release_sources()
        self._save_queue.clear()
        self._save_continuation = None
        if self.save_job:
            self.save_job.cancel()
            self.statusBar().showMessage("Save cancellation requested; checking before atomic commit")

    def duplicate_project(self):
        if self.inspection_context:
            self.return_to_project()
        name, okay = QInputDialog.getText(self, "Duplicate project", "Name", text=self.project.name + " copy")
        if okay and name:
            self.before_edit()
            self.project.name = name
            self.project._document_id = str(uuid.uuid4())
            self.path = None
            self.dirty = True
            self.save_as()

    def autosave(self):
        dirty = self.inspection_context["dirty"] if self.inspection_context else self.dirty
        if dirty and not self.job:
            self._queue_save(self.recovery_path, recovery=True)

    def offer_recovery(self):
        from etlayout.gui.recovery import RecoveryDialog

        def recover(entry, original=False):
            try:
                source = Path(entry.original_path) if original else self.recovery_store.snapshot_path(entry)
                project, result = self.load_project_data(source)
                window = self if not self._recovered_windows else MainWindow(database_path=None, recover=False)
                window.set_project(project, result, path=entry.original_path or None, history=getattr(project, "_resource_history", []))
                window.dirty = not original
                window.update_title()
                window.show()
                self._recovered_windows.append(window)
                return True
            except Exception as error:
                self.error("Recovery failed", error)
                return False

        if self.recovery_store is not None:
            dialog = RecoveryDialog(self.recovery_store, recover, self)
            dialog.exec()

    def save_preset(self):
        path, _ = QFileDialog.getSaveFileName(self, "Save simulation preset", "preset.json", "JSON (*.json)")
        if path:
            Path(path).write_text(json.dumps({"mesh": asdict(self.project.mesh), "solver": asdict(self.project.solver),
                                             "transient": asdict(self.project.transient),
                                             "temperature_limits": [asdict(item) for item in self.project.temperature_limits]}, indent=2), encoding="utf-8")

    def edit_temperature_limits(self):
        if self.model_locked():
            return
        if hasattr(self, "guided") and not self.guided.resolve_draft():
            return
        from etlayout.gui.temperature_limits import TemperatureLimitsDialog
        dialog = TemperatureLimitsDialog(self.project, self, mask_keys=self.mask_keys())
        if dialog.exec():
            self.before_edit()
            self.project.temperature_limits = dialog.limits
            self.changed()

    def edit_thresholds(self):
        if self.model_locked():
            return
        if hasattr(self, "guided") and not self.guided.resolve_draft():
            return
        dialog = QDialog(self)
        dialog.setWindowTitle("Engineering warning thresholds")
        dialog.resize(700, 470)
        layout = QVBoxLayout(dialog)
        note = QLabel("A warning appears when a metric exceeds its upper limit. Ordinary metrics refer to the selected time (or steady result); Transient metrics refer to the whole run. Choose Transient peak temperature to check the entire pulse history. Values use SI units. These checks are not failure predictions.")
        note.setWordWrap(True)
        layout.addWidget(note)
        table = QTableWidget(0, 2)
        table.setHorizontalHeaderLabels(["Metric", "Upper limit (SI)"])
        table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        layout.addWidget(table)
        def add_row(key="temperature_max", value=450.):
            row = table.rowCount()
            table.insertRow(row)
            combo = QComboBox()
            for metric in METRIC_UNITS:
                scope = "Whole run · " if metric.startswith("transient_") else "Selected time / steady · "
                combo.addItem(scope + metric_label(metric, display=False), metric)
            if key not in METRIC_UNITS:
                combo.addItem(key, key)
            combo.setCurrentIndex(combo.findData(key))
            table.setCellWidget(row, 0, combo)
            table.setItem(row, 1, QTableWidgetItem(str(value)))
        for key, value in self.project.thresholds.items():
            add_row(key, value)
        row = QHBoxLayout()
        add = QPushButton("Add threshold")
        add.clicked.connect(lambda: add_row())
        row.addWidget(add)
        remove = QPushButton("Remove selected")
        remove.clicked.connect(lambda: table.removeRow(table.currentRow()))
        row.addWidget(remove)
        layout.addLayout(row)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.rejected.connect(dialog.reject)
        def accept():
            try:
                values = {table.cellWidget(row, 0).currentData(): float(table.item(row, 1).text()) for row in range(table.rowCount())}
                if not all(np.isfinite(value) for value in values.values()):
                    raise ValueError("Thresholds must be finite numbers.")
                if len(values) != table.rowCount():
                    raise ValueError("Each metric may have only one upper limit.")
                self.before_edit()
                self.project.thresholds = values
                self.changed()
                dialog.accept()
            except (ValueError, AttributeError) as error:
                QMessageBox.warning(dialog, "Invalid threshold", str(error))
        buttons.accepted.connect(accept)
        layout.addWidget(buttons)
        dialog.exec()

    def load_preset(self):
        if self.model_locked():
            return
        path, _ = QFileDialog.getOpenFileName(self, "Load simulation preset", "", "JSON (*.json)")
        if path:
            try:
                data = json.loads(Path(path).read_text(encoding="utf-8"))
                config = self.project.to_dict()
                config.update({key: data[key] for key in ("mesh", "solver", "transient", "temperature_limits") if key in data})
                # Legacy steady presets must not silently inherit active pulses.
                if "transient" not in data:
                    config["transient"] = {}
                project = project_from_dict(config)
                if project.transient.enabled and project.transient.initialization in {"saved", "resume"}:
                    try:
                        self.initial_state_argument(project)
                    except ValueError:
                        choice = QMessageBox(self)
                        choice.setWindowTitle("Preset requires a saved thermal state")
                        choice.setText("This preset references an initial field that is not available in this project. Choose its source archive or explicitly change initialization.")
                        source_button = choice.addButton("Choose source archive…", QMessageBox.ButtonRole.ActionRole)
                        uniform_button = choice.addButton("Use uniform temperature", QMessageBox.ButtonRole.ActionRole)
                        steady_button = choice.addButton("Use steady initialization", QMessageBox.ButtonRole.ActionRole)
                        choice.addButton(QMessageBox.StandardButton.Cancel)
                        choice.exec()
                        selected = choice.clickedButton()
                        if selected == source_button:
                            source_path, _ = QFileDialog.getOpenFileName(self, "Choose preset initial state", "", "Projects (*.etl *.etlayout)")
                            if not source_path:
                                return
                            _, source = self.load_project_data(source_path)
                            from etlayout.simulation.sweep import resolve_study_initial_state
                            try:
                                source_options = resolve_study_initial_state(project, source)
                            except (SimulationError, ValueError):
                                source_options = resolve_study_initial_state(project, initial_result=source,
                                    resume=project.transient.initialization == "resume")
                            if source_options["initial_result"] is None:
                                raise ValueError("The archive has no compatible initial thermal field.") from None
                            self.run_history.append(source)
                            self.history_panel.set_results(self.run_history)
                        elif selected in (uniform_button, steady_button):
                            project.transient.initialization = "uniform" if selected == uniform_button else "steady"
                            project.transient.initial_state_digest = ""
                        else:
                            return
                if project.transient.enabled:
                    if project.solver.mode == "electrical":
                        project.solver.mode = "electrothermal"
                    from etlayout.solvers.transient import validate_transient
                    validate_transient(project)
                self.before_edit()
                self.project = project
                self.changed()
                self.log(f"Loaded preset: {project.solver.mode} · {'Transient' if project.transient.enabled else 'Steady'} · "
                         f"{len(project.transient.drives)} waveform drives.")
            except Exception as error:
                self.error("Invalid preset", error)

    def edit_notes(self):
        if self.model_locked():
            return
        if hasattr(self, "guided") and not self.guided.resolve_draft():
            return
        text, okay = QInputDialog.getMultiLineText(self, "Project notes", "Process assumptions, sources and design context", self.project.notes)
        if okay:
            self.before_edit()
            self.project.notes = text
            self.dirty = True
            self.update_title()

    def edit_advanced(self):
        if self.model_locked():
            return
        if hasattr(self, "guided") and not self.guided.resolve_draft():
            return
        text, okay = QInputDialog.getMultiLineText(self, "Advanced project JSON", "All values are SI. Normal workflows use the editors; this is for batch configuration.", json.dumps(self.project.to_dict(), indent=2))
        if okay:
            try:
                project = project_from_dict(json.loads(text))
                self.before_edit()
                self.project = project
                self.changed()
            except Exception as error:
                self.error("Invalid project metadata", error)

    def export_results(self):
        if self.history_workflow.record and self.history_workflow.record.times and self.result and not self.result.transient:
            return self.history_workflow.full("bundle")
        if self.result is None:
            self.error("No results", "Run an analysis first.")
            return
        directory = QFileDialog.getExistingDirectory(self, "CONFIDENTIAL complete bundle — all source fields, layout and configuration")
        if directory:
            try:
                from etlayout.io.export import export_results
                export_results(self.result, directory)
                self.log(f"Exported original numerical fields and metrics for all layers and regions to {directory}")
            except Exception as error:
                self.error("Export failed", error)

    def share_export(self, mode="figure", *, formats=None):
        if self.result is None:
            self.error("No results", "Run an analysis or inspect a calculated run first.")
            return
        if hasattr(self, "guided") and not self.guided.resolve_draft():
            return
        if self.result is None:
            self.error("Results invalidated", "The applied physical draft needs a new calculation. Inspect a recorded run or solve before exporting.")
            return
        from etlayout.gui.export_dialog import ExportDialog
        from etlayout.io.sharing import ExportRequest, FigureOptions
        formats = formats or {"figure": ("png", "html"), "data": ("csv", "npz", "vtu"), "archive": ("etl",)}[mode]
        try:
            limits = self.legend_scale.validated_limits(self.log_scale.isChecked()) if mode == "figure" and self.field_combo.currentData() else None
            if mode == "figure" and limits is None and self.history_range.isChecked():
                limits = self.view.field_limits
        except ValueError as error:
            self.error("Invalid figure range", error)
            return
        initial = ExportRequest(mode=mode, layers=(self.field_layer.currentData(),), fields=(self.field_combo.currentData(),),
            region=self.field_region.currentData() or "", colormap=self.colormap_combo.currentData(),
            limits=limits,
            logarithmic=self.log_scale.isChecked() if mode == "figure" else False, formats=formats,
            figure=FigureOptions(smooth=self.smooth_field.isChecked()))
        dialog = None
        try:
            dialog = ExportDialog(self.project, self.result, self, history=self.run_history,
                                  displayed=self.display_result or self.view_source_result(), initial=initial,
                                  archive_provider=lambda: self.history_workflow.export_inputs(self.result, self.run_history))
            if dialog.exec() and dialog.exported:
                self.log("Exported " + dialog.exported["scope"] + " to " + dialog.directory.text())
        finally:
            if dialog is not None:
                dialog.release_archive_sources()
                dialog.run_result = dialog.displayed = dialog.archive_result = None
                dialog.history = []
                dialog.archive_history = []
                dialog.deleteLater()

    def export_displayed_field(self):
        self.share_export("data")

    def export_map(self):
        if self.result is not None and self.field_combo.currentData():
            self.share_export("figure", formats=("png", "pdf", "svg"))
            return
        if self.field_combo.currentData() and self.view.overlay_item is None:
            self.error("No displayed field", "Choose a finite field and valid range, or select Layout geometry before exporting.")
            return
        path, _ = QFileDialog.getSaveFileName(self, "Export current layout / field map", "field-map.svg", "SVG (*.svg);;PNG (*.png)")
        if not path:
            return
        try:
            from etlayout.gui.export import export_view
            export_view(self.view, path, self.legend.text(), provenance=self.displayed_provenance())
            self.log(f"Exported map {path}")
        except Exception as error:
            self.error("Map export failed", error)

    def export_report(self):
        self.share_export("figure", formats=("html",))

    def apply_theme(self, dark=False):
        from etlayout.gui.theme import apply_widget_theme
        apply_widget_theme(self, dark)
        self.settings.setValue("dark", bool(dark))

    def save_workspace_layout(self):
        name, okay = QInputDialog.getText(self, "Save workspace layout", "Layout name", text="My workspace")
        if okay and name.strip():
            name = name.strip().replace("/", "_")
            self.settings.setValue(f"layouts/{name}/state", self.saveState())
            self.settings.setValue(f"layouts/{name}/geometry", self.saveGeometry())
            self.settings.setValue(f"layouts/{name}/workflow", self.workflow_tabs.currentIndex())
            self.settings.setValue(f"layouts/{name}/guided_stage", self.guided.stage)
            self.settings.setValue(f"layouts/{name}/panels_v1", json.dumps(self.guided.panels.capture()))
            self.settings.setValue(f"layouts/{name}/guided_panels", self.guided.splitter.saveState())
            self.settings.setValue(f"layouts/{name}/guided_drawer", self.guided.center_splitter.saveState())
            self.settings.setValue(f"layouts/{name}/drawer_visible", self.guided.drawer.isVisible())
            self.log(f"Saved workspace layout: {name}")

    def restore_workspace_layout(self):
        if not self.guided.resolve_draft():
            return
        self.settings.beginGroup("layouts")
        names = self.settings.childGroups()
        self.settings.endGroup()
        if not names:
            self.log("No saved workspace layouts. Use View → Save workspace layout first.")
            return
        name, okay = QInputDialog.getItem(self, "Restore workspace layout", "Layout", names, editable=False)
        if okay:
            stage = self.settings.value(f"layouts/{name}/guided_stage")
            if stage:
                if not self.guided.navigate(stage):
                    return
                panels = self.settings.value(f"layouts/{name}/panels_v1")
                if panels is not None:
                    self.guided.panels.restore_state(panels)
                else:
                    # Older named workspaces keep their native splitter state.
                    for splitter, key in ((self.guided.splitter, "guided_panels"),
                                          (self.guided.center_splitter, "guided_drawer")):
                        state = self.settings.value(f"layouts/{name}/{key}")
                        if state is not None:
                            try:
                                restored = splitter.restoreState(state)
                            except (TypeError, ValueError):
                                restored = False
                            if not restored:
                                self.guided.panels.restore_default()
                                break
                    self.guided.panels.set_visible("drawer", self.settings.value(f"layouts/{name}/drawer_visible", True, type=bool))
            else:
                self.workflow_tabs.setCurrentIndex(self.settings.value(f"layouts/{name}/workflow", 0, type=int))
            for restore, key in ((self.restoreGeometry, "geometry"), (self.restoreState, "state")):
                state = self.settings.value(f"layouts/{name}/{key}")
                if state is not None:
                    try:
                        restored = restore(state)
                    except (TypeError, ValueError):
                        restored = False
                    if not restored:
                        self.statusBar().showMessage("An older or corrupt workspace preference was ignored.", 6000)

    def reset_workspace_layout(self):
        self.guided.panels.restore_default()

    def open_user_guide(self):
        from PySide6.QtCore import QUrl

        from etlayout.gui.guide import guide_path
        path = guide_path()
        if path:
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(path)))
        else:
            self.help_dialog()

    def help_dialog(self):
        QMessageBox.information(self, "Workflow & physical scope", 
            "1. Create / import a project and select its top cell or instance.\n"
            "2. Edit physical stack: mask Boolean geometry, materials, thickness and z.\n"
            "3. Draw ROI; review any clipped conductor warning.\n"
            "4. Declare actual interlayer interfaces, then draw terminal areas or boundary edges.\n"
            "5. Add thermal boundaries and sources as needed.\n"
            "6. Validate (F6), mesh (F7), run (F5). Cancel any computation with Shift+F5.\n"
            "7. Select fields, probe, draw profiles/cuts; export and save.\n\n"
            "All models are 2.5D resistive sheets with declared vertical coupling. GaN HEMT is an analytical local charge-control model, excluding drift-diffusion, velocity saturation, trapping, breakdown and field-plate electrostatics. Material seeds require calibration. Sharp-corner maxima are mesh-sensitive.\n\n"
            "Polygon tools: click vertices; double-click or Enter finishes. Drag for rectangle/line tools. Shift-select supports multiple polygons; right-click offers region actions. All coordinates display in µm.")

    def closeEvent(self, event):
        if self.save_job or self._save_queue:
            if QMessageBox.question(self, "Project save in progress", "Cancel saving and close after the worker has stopped?",
                    QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No) == QMessageBox.StandardButton.Yes:
                self.cancel_saving()
                self._close_after_save = True
            event.ignore()
            return
        if self.job or (self.guided.power and self.guided.power.job):
            if QMessageBox.question(self, "Computation running", "Cancel computation and close the application?") != QMessageBox.StandardButton.Yes:
                event.ignore()
                return
            self.cancel_job()
            # Cancellation is asynchronous: keep the event loop alive until the
            # worker exits; closing again cannot orphan a child process.
            self.statusBar().showMessage("Cancellation requested. Close again after the computation stops.")
            event.ignore()
            return
        if not self.confirm_discard(self.close):
            event.ignore()
            return
        if not self.history_opener.shutdown():
            self.statusBar().showMessage("Archive opening is still stopping. Close again after it finishes.")
            event.ignore()
            return
        self._drop_pending_catalog()
        if not self.history_workflow.close():
            self.statusBar().showMessage("History read is still stopping. Close again after it finishes.")
            event.ignore()
            return
        self.cancel_geometry()
        self.settings.setValue("geometry", self.saveGeometry())
        self.settings.setValue("state", self.saveState())
        self._cleanup_recovery(self.inspection_context["project"] if self.inspection_context else self.project)
        if self.recovery_store is not None:
            self.recovery_store.close()
        self.dispose_plots()
        event.accept()

    def dispose_plots(self):
        """Release pyqtgraph's unparented menus before Qt deletes its scenes.

        PlotWidget.close() runs the library's explicit PlotItem cleanup. Merely
        deleting the main window leaves menu controls and view registrations
        reachable, which also exposes obsolete widgets to later theme changes.
        """
        from shiboken6 import isValid
        for panel in (self.profiles, self.cross_section, self.study_panel, self.time_history):
            plot = panel.plot
            if isValid(plot) and plot.plotItem is not None:
                plot.close()
                plot.deleteLater()
