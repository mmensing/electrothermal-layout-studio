# SPDX-License-Identifier: MPL-2.0
"""The native seven-stage workspace. Widgets adapt the existing application services.

Scientific models remain in the document; only a selected object's unapplied draft
and view preferences live here. All numerical work uses the existing workers.
"""
from __future__ import annotations

from copy import deepcopy
from pathlib import Path

import numpy as np
from PySide6.QtCore import QEvent, QObject, QRectF, Qt, Signal
from PySide6.QtGui import QAction, QKeySequence
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QComboBox,
    QFileDialog,
    QFormLayout,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMenu,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QSplitter,
    QTableWidget,
    QTableWidgetItem,
    QTabWidget,
    QTextEdit,
    QToolButton,
    QVBoxLayout,
    QWidget,
)
from shapely.geometry import box
from shapely.ops import unary_union

from etlayout.geometry.prepare import resolve_region
from etlayout.gui.document import WorkspaceDocument
from etlayout.gui.drafts import ask_pending, resolve_pending
from etlayout.gui.forms import RegionDialog
from etlayout.gui.inspector import InlineInspector
from etlayout.gui.layout_editor import LayoutEditor
from etlayout.gui.selection import PendingSelection
from etlayout.gui.technology_templates import open_technology_templates
from etlayout.gui.viewer import geometry_region
from etlayout.gui.workspace_panels import WorkspacePanels
from etlayout.io.project import configuration_hash, physics_hash, project_from_dict, save_project
from etlayout.models import (
    HeatSource,
    Interface,
    MeasurementSpec,
    PhysicalLayer,
    PhysicalPort,
    PortLanding,
    RefinementRegion,
    RegionSpec,
    SimulationError,
    SupportSelector,
    Terminal,
    ThermalBoundary,
)
from etlayout.simulation.runner import _mesh_key
from etlayout.workflow import (
    ANALYSES,
    STAGES,
    CommandRegistry,
    DiagnosticBatch,
    WorkflowIssue,
    analysis_key,
    assess,
    set_analysis,
)


def text_label(text="", style="", parent=None):
    label = QLabel(text, parent)
    label.setWordWrap(True)
    label.setMinimumWidth(0)
    label.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
    if style:
        label.setProperty(style, True)
    return label


def button(text, callback, *, primary=False):
    control = QPushButton(text.replace("&", "&&"))
    control.setAccessibleName(text)
    control.setProperty("primary", primary)
    control.clicked.connect(lambda checked=False: callback())
    return control


class LegacyStageAdapter(QObject):
    """Compatibility for existing extensions; the four-stage ribbon is not rendered."""
    currentChanged = Signal(int)
    mapping = ("layout", "layers", "run", "results")

    def __init__(self, workspace):
        super().__init__(workspace.window)
        self.workspace = workspace

    def setCurrentIndex(self, index):
        self.workspace.navigate(self.mapping[index])
        self.currentChanged.emit(index)

    def currentIndex(self):
        return {"layout": 0, "layers": 1, "connections": 1, "conditions": 2,
                "mesh": 2, "run": 2, "results": 3, "studies": 3}.get(self.workspace.stage, 0)

    def tabText(self, index):
        return ("Layout", "Setup", "Solve", "Inspect")[index]


class GuidedWorkspace(QObject):
    def __init__(self, window):
        super().__init__(window)
        self.window = window
        self.document = WorkspaceDocument(window)
        self.stage = "layout"
        self.editor = None
        self.editor_key = ""
        self.editor_id = ""
        self.selection = PendingSelection(purpose="domain")
        self.selected_layer = ""
        self.check_batch = None
        self.mesh_identity = _mesh_key(window.project) if window.mesh else ""
        self.initial_state_evidence = None
        self.execution_state = "Not started"
        self.last_error = ""
        self.last_error_stage = "run"
        self.last_error_detail = ""
        self.probes = []
        self.refreshing = False
        self.power = None
        self._page_identity = None
        self._page_primary = None
        self._condition_group = ""
        self.registry = CommandRegistry()
        self.actions = {}
        QApplication.instance().installEventFilter(self)
        self._make_canvas_controls()
        self._build_shell()
        self.panels = WorkspacePanels(self)
        self._build_menus()
        window.workflow_tabs = LegacyStageAdapter(self)
        window.default_workspace_state = window.saveState()
        window.view.selection_changed.connect(self.selection_changed)
        if hasattr(window.view, "selection_cancelled"):
            window.view.selection_cancelled.connect(self.cancel_selection)
            window.view.pending_region_changed.connect(lambda region: self.preview_region("roi_rect", region))
        window.view.point_probed.connect(self.record_probe)
        window.view.viewport_changed.connect(self.update_zoom)

    def eventFilter(self, watched, event):
        if event.type() == QEvent.Type.ShortcutOverride:
            focus = QApplication.focusWidget()
            canvas_focus = event.key() == Qt.Key.Key_F and event.modifiers() == (
                Qt.KeyboardModifier.ControlModifier | Qt.KeyboardModifier.ShiftModifier)
            if not canvas_focus and isinstance(focus, (QLineEdit, QTextEdit, QComboBox)) and event.key() in {
                    Qt.Key.Key_Home, Qt.Key.Key_End, Qt.Key.Key_Delete, Qt.Key.Key_F,
                    Qt.Key.Key_F5, Qt.Key.Key_F6, Qt.Key.Key_F7}:
                event.accept()
                return True
        return super().eventFilter(watched, event)

    def _make_canvas_controls(self):
        w = self.window
        w.thermal_joule = QCheckBox("Compute electrical heating once at the initial temperature", w)
        w.thermal_joule.toggled.connect(w.toggle_thermal_joule)
        w.thermal_joule.hide()
        w.log_scale = QCheckBox("Logarithmic scale")
        w.vector_toggle = QCheckBox("Show vectors")
        w.mesh_toggle = QCheckBox("Show mesh")
        w.log_scale.toggled.connect(w.update_overlay)
        w.vector_toggle.toggled.connect(w.update_overlay)
        w.mesh_toggle.toggled.connect(w.toggle_mesh)
        for i, control in enumerate((w.log_scale, w.vector_toggle, w.mesh_toggle)):
            w.appearance_layout.insertWidget(i + 1, control)
        w.tool_combo = QComboBox()
        w.tool_combo.setAccessibleName("Canvas interaction tool")
        for title, key in (("Select objects", "select"), ("Pan", "pan"), ("Rectangle", "roi_rect"),
                           ("Polygon", "roi_polygon"), ("View crop", "display_rect"),
                           ("Port area", "terminal_rect"), ("Port polygon", "terminal_polygon"),
                           ("Boundary edge", "terminal_edge"), ("Contact area", "interface_rect"),
                           ("Measure distance", "measure"), ("Probe result", "probe"),
                           ("Line profile", "profile"), ("Branch current", "branch_current"),
                           ("Cross-section", "cross_section"), ("Refinement area", "refine")):
            w.tool_combo.addItem(title, key)
        w.tool_combo.currentIndexChanged.connect(w.change_tool)

    def _build_shell(self):
        w = self.window
        self.parking = w.takeCentralWidget()
        self.parking.setParent(w)
        self.parking.hide()
        for dock in (w.design_dock, w.config_dock, w.display_dock, w.analysis_dock):
            w.removeDockWidget(dock)
            dock.hide()
        root = QWidget()
        root.setObjectName("guidedWorkspace")
        outer = QVBoxLayout(root)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(4)
        header = QFrame()
        header.setObjectName("projectHeader")
        header_row = QHBoxLayout(header)
        header_row.setContentsMargins(16, 8, 16, 8)
        brand = QLabel("ETLayout")
        brand.setProperty("brand", True)
        header_row.addWidget(brand)
        self.project_name = text_label()
        self.project_name.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        self.project_name.setAccessibleName("Current project")
        header_row.addWidget(self.project_name, 1)
        self.analysis = QComboBox()
        self.analysis.setAccessibleName("Analysis type")
        self.analysis.setMaximumWidth(360)
        self.analysis.setMinimumContentsLength(18)
        self.analysis.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
        for choice in ANALYSES:
            self.analysis.addItem(choice.label, choice.key)
        self.analysis.currentIndexChanged.connect(self.change_analysis)
        label = QLabel("&Analysis type")
        label.setBuddy(self.analysis)
        header_row.addWidget(label)
        header_row.addWidget(self.analysis, 2)
        self.save_state = QLabel()
        self.save_state.setAccessibleName("Save state")
        header_row.addWidget(self.save_state)
        self.save_button = button("Save", w.save)
        header_row.addWidget(self.save_button)
        self.setup_context = QLabel("Working setup")
        self.setup_context.setProperty("hint", True)
        header_row.addWidget(self.setup_context)
        outer.addWidget(header)
        self.splitter = QSplitter(Qt.Orientation.Horizontal)
        self.splitter.setChildrenCollapsible(False)
        outer.addWidget(self.splitter, 1)
        nav = QWidget()
        nav.setObjectName("workflowNavigator")
        nav.setMinimumWidth(0)
        nav.setMaximumWidth(360)
        nav_layout = QVBoxLayout(nav)
        nav_layout.setContentsMargins(10, 12, 10, 10)
        nav_layout.setSpacing(4)
        nav_layout.addWidget(text_label("Workflow", "section"))
        self.stage_buttons, self.stage_status = {}, {}
        for number, stage in enumerate(STAGES, 1):
            control = QPushButton()
            control.setCheckable(True)
            control.setObjectName("stage_" + stage.key)
            control.setAccessibleName(f"{number}. {stage.title}")
            control.setProperty("stage", True)
            control.setMinimumHeight(62)
            control.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Minimum)
            control.clicked.connect(lambda checked=False, key=stage.key: self.navigate(key))
            row = QHBoxLayout(control)
            row.setContentsMargins(8, 8, 6, 8)
            number_label = QLabel(str(number))
            number_label.setObjectName("stageNumber")
            number_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
            number_label.setFixedSize(30, 30)
            row.addWidget(number_label)
            copy = QVBoxLayout()
            copy.setSpacing(3)
            title = QLabel(stage.title)
            title.setWordWrap(True)
            title.setMinimumWidth(0)
            title.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
            title.setProperty("stageTitle", True)
            status = QLabel(stage.subtitle)
            status.setWordWrap(True)
            status.setProperty("subtle", True)
            copy.addWidget(title)
            copy.addWidget(status)
            row.addLayout(copy, 1)
            for child in control.findChildren(QLabel):
                child.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
            nav_layout.addWidget(control)
            self.stage_buttons[stage.key], self.stage_status[stage.key] = control, status
        nav_layout.addSpacing(8)
        nav_layout.addWidget(text_label("Studies", "section"))
        for label, callback in (("Sweeps & convergence", lambda: self.navigate("studies")),
                                 ("Routing & optimization", self.open_studies),
                                 ("Saved studies", lambda: self.show_page(w.study_panel, "Study results"))):
            nav_layout.addWidget(button(label, callback))
        nav_layout.addStretch(1)
        nav_layout.addWidget(button("Project settings", lambda: self.open_expert("settings")))
        nav_layout.addWidget(button("Help & documentation", w.help_dialog))
        nav_scroll = QScrollArea()
        nav_scroll.setFrameShape(QFrame.Shape.NoFrame)
        nav_scroll.setWidgetResizable(True)
        nav_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        nav_scroll.setWidget(nav)
        nav_scroll.setMinimumWidth(200)
        self.navigation = nav_scroll
        self.splitter.addWidget(nav_scroll)
        center = QWidget()
        center_layout = QVBoxLayout(center)
        center_layout.setContentsMargins(0, 0, 0, 0)
        center_layout.setSpacing(4)
        self.context = text_label("Working setup · layout geometry", "context")
        self.context.setAccessibleName("Canvas configuration and run context")
        workspace_row = QHBoxLayout()
        self.left_toggle = QToolButton()
        self.left_toggle.setAccessibleName("Toggle workflow navigation")
        self.right_toggle = QToolButton()
        self.right_toggle.setAccessibleName("Toggle contextual inspector")
        workspace_row.addWidget(self.left_toggle)
        workspace_row.addWidget(self.context, 1)
        workspace_row.addWidget(self.right_toggle)
        center_layout.addLayout(workspace_row)
        self.center_splitter = QSplitter(Qt.Orientation.Vertical)
        self.center_splitter.setChildrenCollapsible(False)
        center_layout.addWidget(self.center_splitter, 1)
        self.canvas_tabs = QTabWidget()
        self.canvas_tabs.setDocumentMode(True)
        self.canvas_tabs.setAccessibleName("Canvas and quantitative views")
        canvas = QWidget()
        canvas_layout = QVBoxLayout(canvas)
        canvas_layout.setContentsMargins(8, 4, 8, 4)
        canvas_layout.setSpacing(4)
        tool_row = QHBoxLayout()
        for label, callback in (("Select", lambda: w.set_tool("select")), ("Pan", lambda: w.set_tool("pan")),
                                ("+", lambda: w.view.scale(1.2, 1.2)), ("−", lambda: w.view.scale(1 / 1.2, 1 / 1.2)),
                                ("Fit", w.view.fit_all)):
            tool_row.addWidget(button(label, callback))
        tool_row.addWidget(w.tool_combo, 1)
        self.active_layer = QComboBox()
        self.active_layer.setMinimumContentsLength(9)
        self.active_layer.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
        self.active_layer.setAccessibleName("Selection physical layer")
        self.active_layer.currentIndexChanged.connect(self.change_layer)
        tool_row.addWidget(self.active_layer, 1)
        canvas_layout.addLayout(tool_row)
        self.layout_editor = LayoutEditor(self, canvas_layout)
        edit_layout = QPushButton("Edit layout")
        edit_layout.setCheckable(True)
        edit_layout.setAccessibleName("Edit layout polygon vertices")
        edit_layout.setToolTip("Edit a project-owned layout copy; the imported file stays unchanged (Ctrl+Shift+E)")
        self.layout_editor.toggle = edit_layout
        edit_layout.clicked.connect(self.layout_editor.toggle_mode)
        tool_row.addWidget(edit_layout)
        self.selection_banner = QFrame()
        self.selection_banner.setProperty("selectionBanner", True)
        banner_row = QHBoxLayout(self.selection_banner)
        banner_row.setContentsMargins(8, 6, 8, 6)
        self.selection_text = text_label()
        banner_row.addWidget(self.selection_text, 1)
        self.use_selection = button("Use selection", self.commit_selection, primary=True)
        banner_row.addWidget(self.use_selection)
        banner_row.addWidget(button("Clear", self.clear_selection))
        banner_row.addWidget(button("Cancel", self.cancel_selection))
        canvas_layout.addWidget(self.selection_banner)
        self.selection_banner.hide()
        canvas_layout.addWidget(w.view, 1)
        canvas_layout.addWidget(w.time_navigation)
        canvas_layout.addWidget(w.legend_scale.color_bar)
        canvas_layout.addWidget(w.legend)
        canvas_layout.addWidget(w.selection_statistics)
        canvas_footer = QHBoxLayout()
        self.canvas_hint = text_label("Select an object to inspect it.", "subtle")
        canvas_footer.addWidget(self.canvas_hint, 1)
        self.zoom = QLabel("Fit")
        canvas_footer.addWidget(self.zoom)
        canvas_layout.addLayout(canvas_footer)
        self.canvas_tabs.addTab(canvas, "Layout view")
        self.canvas_tabs.addTab(w.cross_section, "Cross-section")
        self.canvas_tabs.addTab(w.profiles, "Profiles")
        self.canvas_tabs.addTab(w.results_page, "Quantitative results")
        self.canvas_tabs.addTab(w.time_history, "Time history")
        self.center_splitter.addWidget(self.canvas_tabs)
        self.drawer = QTabWidget()
        self.drawer.setAccessibleName("Issues jobs logs and history")
        self.drawer.setDocumentMode(True)
        self.issues_page = QWidget()
        issues_layout = QVBoxLayout(self.issues_page)
        issues_layout.setContentsMargins(6, 4, 6, 4)
        filter_row = QHBoxLayout()
        self.severity = QComboBox()
        self.severity.addItems(["All severities", "error", "warning", "info"])
        self.issue_search = QLineEdit()
        self.issue_search.setPlaceholderText("Search issues…")
        self.issue_search.setAccessibleName("Search issues")
        self.issue_search.textChanged.connect(self.refresh_issues)
        self.severity.currentIndexChanged.connect(self.refresh_issues)
        filter_row.addWidget(self.severity)
        filter_row.addWidget(self.issue_search, 1)
        issues_layout.addLayout(filter_row)
        self.issue_table = QTableWidget(0, 4)
        self.issue_table.setHorizontalHeaderLabels(["Severity", "Message", "Context", "Actions"])
        self.issue_table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.issue_table.verticalHeader().hide()
        header = self.issue_table.horizontalHeader()
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        for column, width in ((0, 120), (2, 85), (3, 185)):
            header.setSectionResizeMode(column, QHeaderView.ResizeMode.Fixed)
            self.issue_table.setColumnWidth(column, width)
        issues_layout.addWidget(self.issue_table)
        self.drawer.addTab(self.issues_page, "Issues")
        self.jobs_page = QWidget()
        jobs_layout = QVBoxLayout(self.jobs_page)
        self.job_message = text_label("No job running")
        jobs_layout.addWidget(self.job_message)
        self.job_timings = text_label("Stage timings appear after a calculation.", "subtle")
        self.job_timings.setAccessibleName("Measured calculation stage timings")
        jobs_layout.addWidget(self.job_timings)
        jobs_layout.addWidget(w.progress)
        self.job_cancel = button("Cancel computation", w.cancel_job)
        jobs_layout.addWidget(self.job_cancel)
        self.drawer.addTab(self.jobs_page, "Jobs")
        self.drawer.addTab(w.console, "Logs")
        self.drawer.addTab(w.history_panel, "History")
        self.center_splitter.addWidget(self.drawer)
        self.center_splitter.setStretchFactor(0, 1)
        self.center_splitter.setSizes([650, 190])
        self.drawer_toggle = button("Issues / jobs / history", self.toggle_drawer)
        self.drawer_toggle.setMinimumWidth(0)
        self.drawer_toggle.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Fixed)
        center_layout.addWidget(self.drawer_toggle)
        self.splitter.addWidget(center)
        self.inspector = QWidget()
        self.inspector.setObjectName("contextInspector")
        self.inspector.setMinimumWidth(290)
        right = QVBoxLayout(self.inspector)
        right.setContentsMargins(12, 12, 12, 8)
        right.setSpacing(8)
        self.heading = text_label("Simulation domain", "heading")
        self.description = text_label("Define the layout region to include in the simulation.", "subtle")
        right.addWidget(self.heading)
        right.addWidget(self.description)
        self.page_scroll = QScrollArea()
        self.page_scroll.setWidgetResizable(True)
        self.page_scroll.setFrameShape(QFrame.Shape.NoFrame)
        self.page = QWidget()
        self.page_layout = QVBoxLayout(self.page)
        self.page_layout.setContentsMargins(0, 0, 4, 0)
        self.page_layout.setSpacing(10)
        self.page_scroll.setWidget(self.page)
        right.addWidget(self.page_scroll, 1)
        self.primary = button("Review physical layers", lambda: self.navigate("layers"), primary=True)
        self.primary.setObjectName("workflow_primary")
        right.addWidget(self.primary)
        self.next_reason = text_label("", "subtle")
        self.next_reason.setAccessibleName("Primary action prerequisites")
        right.addWidget(self.next_reason)
        self.splitter.addWidget(self.inspector)
        self.splitter.setStretchFactor(1, 1)
        self.splitter.setSizes([250, 890, 400])
        w.setCentralWidget(root)
        w.welcome.hide()
        w.workflow_hint.hide()
        w.inspection_bar.hide()

    def register(self, key, label, callback, menu, shortcut=None, action=None):
        self.registry.register(key, label, callback, enabled=lambda name=key: self.command_availability(name))
        existing = action is not None
        action = action or QAction(label, self.window)
        if key not in self.actions:
            if existing:
                action.triggered.disconnect()
            action.triggered.connect(lambda checked=False, name=key: self.execute(name))
        action.setText(label)
        action.setObjectName(key)
        if shortcut:
            action.setShortcut(shortcut)
        menu.addAction(action)
        self.actions[key] = action
        return action

    def command_availability(self, key):
        busy = bool(self.window.job or (self.power and self.power.job))
        read_only = key.startswith(("view.", "results.", "help.", "file.export"))
        if (getattr(self.window.project, "_resource_unresolved", False)
                and (key.startswith(("simulation.", "studies.")) and key not in {"simulation.cancel", "studies.saved"}
                     or key in {"edit.layout", "file.save", "file.save_as"})):
            return "Review project resources or relink the layout before this operation."
        if key == "simulation.cancel":
            return busy or bool(self.window.prepare_job or self.window.hierarchy_jobs)
        if busy and not read_only and key not in {"file.save", "file.save_as", "file.exit"}:
            return "Finish or cancel the active computation before changing the working setup."
        if self.window.inspection_context and key.startswith(("edit.", "simulation.", "model.keepout", "model.measurement")):
            return "Return to the working setup before editing or running."
        return True

    def execute(self, key):
        try:
            if key.startswith(("file.new", "file.open", "file.import", "file.duplicate", "file.relink",
                               "edit.", "model.", "simulation.")) and key not in {"simulation.cancel", "edit.layout"}:
                if not self.resolve_draft():
                    return False
            return self.registry.execute(key)
        except (ValueError, RuntimeError) as error:
            self.window.log(str(error))
            self.next_reason.setText(str(error))

    def _build_menus(self):
        w = self.window
        previous = list(w.menuBar().actions())
        file_menu = previous[0].menu()
        help_menu = previous[-1].menu()
        # Retain File/examples and Help actions, moving exports to their proper owner.
        w.menuBar().clear()
        w.menuBar().addMenu(file_menu)
        self.menus = {name: w.menuBar().addMenu(label) for name, label in
                      (("Edit", "&Edit"), ("Model", "&Model"), ("Simulation", "&Simulation"),
                       ("Results", "Res&ults"), ("Studies", "S&tudies"), ("View", "&View"))}
        w.menuBar().addMenu(help_menu)
        for i, action in enumerate(list(file_menu.actions())):
            if action.menu() or action.isSeparator():
                continue
            if action.text().startswith("Export"):
                file_menu.removeAction(action)
                self.menus["Results"].addAction(action)
            # Existing file callbacks remain the authoritative implementations.
            self.actions[f"file.{i}"] = action
        file_commands = {
            "New project…": ("new", w.new_project), "Open project…": ("open", w.open_project),
            "Import layout…": ("import", w.import_layout), "Save project": ("save", w.save),
            "Save project as…": ("save_as", w.save_as),
            "Save content-addressed copy…": ("save_v3", lambda: w.save_as(archive_format=3)),
            "Save legacy-compatible copy…": ("save_legacy", lambda: w.save_as(archive_format=2)),
            "Duplicate project…": ("duplicate", w.duplicate_project),
            "Relink layout…": ("relink", w.relink_layout), "Exit": ("exit", w.close),
            "Export / share results…": ("export_share", lambda: w.share_export()),
            "Export complete numerical bundle (confidential)…": ("export_full", w.export_results),
            "Export displayed field / region…": ("export_field", w.export_displayed_field),
            "Export map…": ("export_map", w.export_map), "Export report…": ("export_report", w.export_report),
        }
        def register_examples(menu):
            for action in list(menu.actions()):
                if action.menu():
                    register_examples(action.menu())
                elif not action.isSeparator():
                    name = action.text().split(" · ", 1)[0]
                    self.register("example." + name, action.text(), lambda name=name: w.load_example(name),
                                  menu, action=action)

        for menu in (file_menu, self.menus["Results"]):
            for action in list(menu.actions()):
                if action.text() in file_commands:
                    key, callback = file_commands[action.text()]
                    self.register("file." + key, action.text(), callback, menu, action=action)
                elif action.menu() and "examples" in action.text():
                    register_examples(action.menu())
        for key, label, fn, shortcut, action in (
            ("edit.undo", "Undo", w.undo, QKeySequence.StandardKey.Undo, w.undo_action),
            ("edit.redo", "Redo", w.redo, QKeySequence.StandardKey.Redo, w.redo_action),
            ("edit.preferences", "Execution settings…", w.edit_execution_settings, None, w.execution_action),
            ("edit.history", "Archive and history settings…", w.edit_history_settings, None, w.history_settings_action)):
            self.register(key, label, fn, self.menus["Edit"], shortcut, action)
        for stage in STAGES[:4]:
            self.register("model." + stage.key, stage.title, lambda key=stage.key: self.navigate(key), self.menus["Model"])
        self.register("model.expert", "Complete model tables", self.open_expert, self.menus["Model"])
        self.register("model.materials", "Materials and library", lambda: self.open_expert("materials"), self.menus["Model"])
        self.register("model.measurement", "Define measurement support", lambda: self.begin_selection("measurement"), self.menus["Model"])
        self.register("model.keepout", "Define routing keepout", lambda: self.begin_selection("keepout"), self.menus["Model"])
        self.register("model.connected", "Select connected geometry", lambda: w.selection_action("connected"), self.menus["Model"])
        self.register("model.trace", "Trace path between terminals…", w.trace_terminals, self.menus["Model"])
        self.register("model.clipped", "Acknowledge reviewed clipped boundaries", w.acknowledge_boundaries, self.menus["Model"], action=w.acknowledge_action)
        self.register("edit.selected", "Edit selected object", self.edit_selected, self.menus["Edit"])
        self.register("edit.layout", "Edit layout polygon vertices", self.layout_editor.toggle_mode,
                      self.menus["Edit"], "Ctrl+Shift+E")
        self.register("edit.remove", "Remove selected object", self.remove_selected, self.menus["Edit"])
        for key, label, fn, shortcut, action in (
            ("simulation.validate", "Validate geometry & connectivity", self.validate, "F6", w.validate_action),
            ("simulation.mesh", "Generate / update mesh", self.mesh, "F7", w.mesh_action),
            ("simulation.run", "Run analysis", self.run, "F5", w.run_action),
            ("simulation.cancel", "Cancel computation", w.cancel_job, "Shift+F5", w.cancel_action)):
            self.register(key, label, fn, self.menus["Simulation"], shortcut, action)
        for key, label, fn in (("solver", "Numerical settings", lambda: self.edit_settings("solver")),
                                ("pulse", "Time and load settings…", w.edit_transient),
                                ("restart", "Start transient from saved state…", lambda: w.run_saved_state(False)),
                                ("resume", "Resume saved transient…", lambda: w.run_saved_state(True))):
            self.register("simulation." + key, label, fn, self.menus["Simulation"])
        for key, label, fn in (("overview", "Fields and overview", lambda: self.navigate("results")),
                               ("history", "Inspect saved runs", lambda: self.open_drawer(3)),
                               ("return", "Return to working setup", self.return_to_setup),
                               ("compare", "Compare field maps…", w.compare_maps_dialog),
                               ("layers", "Compare layers…", w.compare_layers_dialog),
                               ("difference", "Load field difference…", w.field_difference_dialog),
                               ("config", "Compare run configuration…", w.compare_run_configuration),
                               ("hotspot", "Locate hotspot", w.locate_hotspot),
                               ("profile", "Draw line profile", lambda: w.set_tool("profile")),
                               ("branch", "Integrate branch current", lambda: w.set_tool("branch_current")),
                               ("analysis", "Contacts, devices and loss budget", lambda: self.open_studies("analysis")),
                               ("export", "Export task", self.export_task)):
            self.register("results." + key, label, fn, self.menus["Results"])
        for key, label, fn in (("sweep", "Parameter sweep…", w.run_sweep_dialog),
                               ("mesh", "Mesh convergence…", w.run_convergence_dialog),
                               ("time", "Time-step convergence…", w.run_time_convergence_dialog),
                               ("compare", "Compare project designs…", w.compare_dialog),
                               ("routing", "Routing & optimization", self.open_studies),
                               ("saved", "Saved study results", lambda: self.show_page(w.study_panel, "Study results"))):
            self.register("studies." + key, label, fn, self.menus["Studies"], action=w.power_action if key == "routing" else None)
        self.register("view.fit", "Fit layout", w.view.fit_all, self.menus["View"], "Home")
        self.register("view.selection", "Fit selection / ROI", self.fit_selection_or_roi, self.menus["View"], "Shift+Home")
        self.register("view.crop", "Draw view crop", lambda: self.begin_selection("view_crop"), self.menus["View"])
        self.register("view.clear_crop", "Clear view crop", w.clear_display_roi, self.menus["View"])
        self.register("view.browser", "Hierarchy / layers / nets", lambda: self.show_page(w.design_dock.widget(), "Model browser"), self.menus["View"])
        self.menus["View"].addSeparator()
        panel_actions = {}
        for name, label, shortcut in (("left", "Workflow navigation", "Ctrl+Shift+L"),
                                      ("right", "Contextual inspector", "Ctrl+Shift+R"),
                                      ("drawer", "Issues / jobs / history", "Ctrl+J")):
            panel_actions[name] = self.register("view." + ("drawer" if name == "drawer" else name + "_panel"),
                label, lambda name=name: self.panels.toggle(name), self.menus["View"], shortcut)
        panel_actions["left"].setIconText("Workflow")
        panel_actions["right"].setIconText("Inspector")
        self.left_toggle.setDefaultAction(panel_actions["left"])
        self.left_toggle.setText("Workflow")
        self.right_toggle.setDefaultAction(panel_actions["right"])
        self.right_toggle.setText("Inspector")
        self.register("view.focus_canvas", "Focus layout canvas", self.panels.focus_canvas,
                      self.menus["View"], "Ctrl+Shift+F")
        maximize = self.register("view.maximize_canvas", "Maximize canvas / restore panels", self.panels.maximize_canvas,
                                self.menus["View"], "Ctrl+Shift+M")
        self.panels.bind_actions(panel_actions, maximize)
        self.menus["View"].addAction(w.dark_action)
        for action in w.workspace_actions:
            if "workspace layout" in action.text():
                key = action.text().split()[0].lower()
                self.register("view.workspace_" + key, action.text(), getattr(w, key + "_workspace_layout"),
                              self.menus["View"], action=action)
        for number, stage in enumerate(STAGES, 1):
            action = QAction(stage.title, w)
            action.setShortcut(f"Ctrl+{number}")
            action.triggered.connect(lambda checked=False, key=stage.key: self.navigate(key))
            w.addAction(action)

    def start(self):
        self.navigate("results" if self.window.result else "layout", force=True)
        if not self.assessment.issues:
            self.drawer.hide()
        self.panels.restore_preferences()

    def project_replaced(self):
        """Retire presentation evidence belonging to the previous document."""
        self.check_batch = None
        self.mesh_identity = _mesh_key(self.window.project) if self.window.mesh else ""
        self.initial_state_evidence = None
        self.execution_state = "Recorded result loaded" if self.window.result else "Not started"
        self.last_error = self.last_error_detail = ""
        self.last_error_stage = "run"
        self.probes.clear()
        self._condition_group = ""

    def edit_selected(self):
        if self.editor_id:
            self.edit_model(self.editor_key, self.editor_id)
        elif self.selected_layer:
            self.navigate("layers")
            self.edit_model("layers", self.selected_layer)

    def remove_selected(self):
        key, identifier = self.editor_key, self.editor_id
        if not identifier or key in {"mesh", "solver"} or not self.resolve_draft():
            return
        if QMessageBox.question(self.window, "Remove selected object", f"Remove {identifier}? References to it will require correction.") != QMessageBox.StandardButton.Yes:
            return
        def remove(project):
            objects = self.objects(key, project)
            objects[:] = [obj for obj in objects if obj.id != identifier]
        self.document.transact("Removed " + identifier, remove)
        self.navigate(self.stage, force=True)

    def update_zoom(self):
        if hasattr(self, "zoom"):
            self.zoom.setText(f"{self.window.view.transform().m11():.3g} px/µm")

    def fit_selection_or_roi(self):
        if self.window.view.scene().selectedItems():
            self.window.view.fit_selection()
        elif self.window.project.display_roi or self.window.project.roi:
            region = self.window.project.display_roi or self.window.project.roi
            bounds = resolve_region(region, self.window.masks,
                                    self.window.geometry.domain if self.window.geometry else None).bounds
            x0, y0, x1, y1 = bounds
            self.window.view.fitInView(QRectF(x0 * 1e6, -y1 * 1e6, (x1 - x0) * 1e6, (y1 - y0) * 1e6),
                                       Qt.AspectRatioMode.KeepAspectRatio)
        else:
            self.window.view.fit_all()

    def toggle_drawer(self):
        self.panels.toggle("drawer")

    def open_drawer(self, index=0):
        self.panels.open_drawer(index)

    def show_page(self, widget, title):
        if self.canvas_tabs.indexOf(widget) < 0:
            self.canvas_tabs.addTab(widget, title)
        self.canvas_tabs.setCurrentWidget(widget)

    def resolve_draft(self):
        if not self.layout_editor.finish():
            return False
        if not resolve_pending(changed=bool(self.pending_region is not None or (self.editor and self.editor.has_changes())),
                choose=lambda: ask_pending(self.window, "Unapplied draft",
                    "Apply the complete inspector/selection draft before leaving, discard it, or stay here?"),
                apply=self.apply_draft, discard=self.discard_draft):
            return False
        return self.power.resolve_pending() if self.power else True

    def discard_draft(self):
        context = ((self.editor_key, self.editor_id, self.editor_is_new) if self.editor else None)
        self.cancel_selection()
        self.clear_editor()
        if context and not context[2]:
            key, identifier, _ = context
            model = (getattr(self.window.project, key) if key in {"mesh", "solver"} else
                     next((x for x in self.objects(key) if x.id == identifier), None))
            if model is not None:
                self.open_editor(key, model)
        self.next_reason.clear()
        # Restore the stage action, not an Apply callback for a now-clean draft.
        if self.editor and self._page_primary:
            self.set_primary(*self._page_primary)
            self.refresh()
        else:
            self.build_page()
            self.refresh()

    def apply_draft(self):
        if self.pending_region is not None and self.pending_purpose == "editor":
            if not self.editor:
                return False
            self.editor.set_region(self.pending_region)
        if self.editor and self.editor.has_changes():
            # Validate every field before any unrelated region can be committed.
            candidate = self.editor.form.parse_candidate()
            if candidate is None:
                return False
            try:
                self.validate_editor_model(candidate, self.editor_key, self.editor_is_port)
            except ValueError as error:
                self.next_reason.setText(str(error))
                return False
            if self.pending_purpose == "editor":
                self.cancel_selection()
                pending = None
            else:
                pending = deepcopy(self.selection) if self.pending_region is not None else None
                # A forced page refresh during editor commit must not open a
                # second transition prompt or retire an unrelated region draft.
                self.selection.clear()
            if not self.editor.apply():
                if pending:
                    self.selection = pending
                return False
            if pending:
                self.selection = pending
        return self.commit_selection() if self.pending_region is not None else True

    @property
    def pending_region(self):
        return self.selection.region

    @pending_region.setter
    def pending_region(self, region):
        if region is None:
            self.selection.clear()
        else:
            self.selection.region = region

    @property
    def pending_purpose(self):
        return self.selection.purpose

    @pending_purpose.setter
    def pending_purpose(self, purpose):
        self.selection.purpose = purpose
        self.selection.explicit = True

    def navigate(self, stage, force=False):
        if not force and stage == self.stage:
            if stage in self.stage_buttons:
                self.stage_buttons[stage].setChecked(True)
            return True
        if not force and not self.resolve_draft():
            if self.stage in self.stage_buttons:
                self.stage_buttons[self.stage].setChecked(True)
            return False
        if stage != "results":
            self.window.history_workflow.loader.cancel()
        if self.pending_purpose == "editor" and self.selection_banner.isVisible():
            # Explicit Apply/Cancel and forced refreshes retire the owning
            # inspector, so its selection session cannot outlive that page.
            self.cancel_selection()
        if stage != "results" and self.window.inspection_context:
            self.window.return_to_project()
        self.stage = stage
        self._page_identity = None
        for key, control in self.stage_buttons.items():
            control.setChecked(key == stage)
            for label in control.findChildren(QLabel):
                label.setProperty("stageActive", key == stage)
                label.style().unpolish(label)
                label.style().polish(label)
        self.canvas_tabs.setCurrentIndex(0)
        self.build_page()
        self.refresh()
        self.window.update_overlay()
        self.apply_canvas_context()
        return True

    def _clear_page(self):
        self.editor = None
        self._page_primary = None
        for name in ("editor_host", "editor_layout"):
            if hasattr(self, name):
                delattr(self, name)
        # Retire the entire page including nested form/row layouts. Removing only
        # top-level widgets leaves their children visible at stale coordinates.
        for widget in (self.window.result_controls, self.window.display_tabs, self.window.thermal_joule):
            widget.hide()
            widget.setParent(self.parking)
        previous = self.page_scroll.takeWidget()
        previous.hide()
        previous.deleteLater()
        self.page = QWidget()
        self.page_layout = QVBoxLayout(self.page)
        self.page_layout.setContentsMargins(0, 0, 4, 0)
        self.page_layout.setSpacing(10)
        self.page_scroll.setWidget(self.page)
        self.primary.setEnabled(True)
        self.next_reason.clear()

    def set_primary(self, text, callback):
        self.primary_callback = callback
        self.primary.setText(text)
        self.primary.setAccessibleName(text)
        try:
            self.primary.clicked.disconnect()
        except RuntimeError:
            pass
        self.primary.clicked.connect(lambda checked=False: callback())

    def build_page(self):
        self._clear_page()
        if self.layout_editor.active:
            self.layout_editor.build_page()
            self._page_identity = configuration_hash(self.window.project)
            return
        builders = {"layout": self.layout_page, "layers": self.layers_page, "connections": self.connections_page,
                    "conditions": self.conditions_page, "mesh": self.mesh_page, "run": self.run_page,
                    "results": self.results_page, "studies": self.studies_page}
        builders[self.stage]()
        self._page_primary = (self.primary.text(), self.primary_callback)
        self._page_identity = configuration_hash(self.window.project)

    def layout_page(self):
        w = self.window
        self.heading.setText("Simulation domain")
        self.description.setText("Choose the physical scope. View crop changes only the framing.")
        if not w.project.layout.path and not w.project.layers:
            self.page_layout.addWidget(text_label("Choose an analysis type above, then open your layout or an example.", "hint"))
            for label, fn in (("New from layout…", w.new_project), ("Open project…", w.open_project),
                               ("Open metal example", lambda: w.load_example("E1")),
                               ("Open power HEMT example", lambda: w.load_example("E7"))):
                self.page_layout.addWidget(button(label, fn))
        source = w.project.layout
        filename = Path(source.path).name or "Explicit model geometry"
        display_name = filename if len(filename) < 44 else filename[:24] + "…" + filename[-15:]
        source_label = text_label(f"{display_name}\nCell: {source.cell or '—'}"
                                 f" · instance: {source.instance_path or '(top)'}\nDBU: {source.dbu_um:g} µm", "hint")
        source_label.setToolTip(source.path)
        self.page_layout.addWidget(source_label)
        self.page_layout.addWidget(button("Browse hierarchy / instances", lambda: self.show_page(w.design_dock.widget(), "Model browser")))
        self.page_layout.addWidget(button("Use selected item as simulation scope", self.apply_scope))
        self.region_kind = QComboBox()
        for label, key in (("Simulation domain", "domain"), ("View crop · display only", "view_crop"),
                           ("Measurement region", "measurement"), ("Routing keepout", "keepout")):
            self.region_kind.addItem(label, key)
        self.region_kind.setAccessibleName("Region purpose")
        if self.selection.explicit:
            self.region_kind.setCurrentIndex(max(0, self.region_kind.findData(self.pending_purpose)))
        self.region_kind.currentIndexChanged.connect(self.change_region_purpose)
        self.page_layout.addWidget(self.region_kind)
        form = QFormLayout()
        self.bounds_fields = {}
        bounds = w.geometry.domain.bounds if w.geometry else w.document.bounds if w.document else (0, 0, 100e-6, 100e-6)
        for key, label, value in (("x", "X origin (µm)", bounds[0]), ("y", "Y origin (µm)", bounds[1]),
                                   ("width", "Width (µm)", bounds[2] - bounds[0]), ("height", "Height (µm)", bounds[3] - bounds[1])):
            field = QLineEdit(f"{value * 1e6:.9g}")
            field.setAccessibleName(label)
            field.setObjectName("domain_" + key)
            self.bounds_fields[key] = field
            form.addRow(label, field)
        self.page_layout.addLayout(form)
        for label, fn in (("Preview numeric region", self.numeric_domain),
                           ("Draw rectangle", lambda: self.begin_selection(self.region_kind.currentData())),
                           ("Draw polygon", lambda: self.begin_selection(self.region_kind.currentData(), polygon=True)),
                           ("From layout selection", self.selection_from_canvas),
                           ("Use entire layout as simulation domain", w.clear_roi)):
            self.page_layout.addWidget(button(label, fn))
        self.domain_preview = text_label("Drawing or numeric entry is a preview until Use selection.", "subtle")
        self.page_layout.addWidget(self.domain_preview)
        self.page_layout.addStretch()
        self.set_primary("Review physical layers", lambda: self.navigate("layers"))

    def layers_page(self):
        w = self.window
        self.heading.setText("Layer mapping & materials")
        self.description.setText("Map source masks to physical layers. Visibility and picking never change participation.")
        table = self.mapping = QTableWidget(0, 4)
        table.setHorizontalHeaderLabels(["Mask / name", "Role", "Material", "z / t (µm)"])
        table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        table.verticalHeader().hide()
        table.setMaximumHeight(205)
        rows = []
        mapped = set()
        for layer in w.project.layers:
            mapped.update(layer.geometry.sources)
            rows.append((layer.id, layer.name, layer.role, layer.material_id, f"{layer.z * 1e6:g} / {layer.thickness * 1e6:g}"))
        for key in w.mask_keys():
            if key not in mapped:
                rows.append(("mask:" + key, key, "Display only", "Unassigned", "—"))
        table.setRowCount(len(rows))
        for row, (key, *values) in enumerate(rows):
            for col, value in enumerate(values):
                cell = QTableWidgetItem(value)
                cell.setData(Qt.ItemDataRole.UserRole, key)
                table.setItem(row, col, cell)
        table.cellClicked.connect(lambda row, col: self.select_mapping(table.item(row, 0).data(Qt.ItemDataRole.UserRole)))
        table.horizontalHeader().setStretchLastSection(True)
        table.resizeColumnsToContents()
        self.page_layout.addWidget(table)
        row = QHBoxLayout()
        row.addWidget(button("Add physical layer", self.add_layer))
        row.addWidget(button("Mesh / solver preset…", w.load_preset))
        self.page_layout.addLayout(row)
        row2 = QHBoxLayout()
        row2.addWidget(button("Material library…", w.material_library))
        row2.addWidget(button("Project material override", lambda: self.open_expert("materials")))
        self.page_layout.addLayout(row2)
        self.page_layout.addWidget(button("Technology templates…", lambda: open_technology_templates(self)))
        self.layer_visible = QCheckBox("Visible in layout")
        self.layer_pickable = QCheckBox("Pickable")
        self.layer_visible.setChecked(True)
        self.layer_pickable.setChecked(True)
        self.layer_visible.toggled.connect(self.set_visible)
        self.layer_pickable.toggled.connect(lambda value: w.view.set_layer_pickable(self.selected_layer, value))
        row3 = QHBoxLayout()
        row3.addWidget(self.layer_visible)
        row3.addWidget(self.layer_pickable)
        self.page_layout.addLayout(row3)
        self.editor_host = QWidget()
        self.editor_layout = QVBoxLayout(self.editor_host)
        self.editor_layout.setContentsMargins(0, 0, 0, 0)
        self.page_layout.addWidget(self.editor_host, 1)
        self.page_layout.addWidget(button("Inspect actual cross-section", self.show_stack))
        self.set_primary("Define connections", lambda: self.navigate("connections"))
        if w.project.layers:
            layer = next((x for x in w.project.layers if x.id == self.selected_layer), w.project.layers[0])
            self.selected_layer = layer.id
            self.edit_model("layers", layer.id, resolve=False)

    def select_mapping(self, key):
        if key.startswith("mask:"):
            self.add_layer(key[5:])
        else:
            self.selected_layer = key
            self.edit_model("layers", key)
            self.locate_object("layers", key, frame=False)

    def add_layer(self, mask=""):
        w = self.window
        if not self.resolve_draft():
            return
        model = PhysicalLayer(w.unique_id("layer", w.project.layers), "New physical layer", "",
                              geometry=RegionSpec(sources=[mask] if mask else []), color="#2588ff")
        self.open_editor("layers", model, is_new=True)

    def connections_page(self):
        self.heading.setText("Add connection or probe")
        self.description.setText("Choose purpose, physical layer and footprint. XY overlap alone does not create a connection.")
        grid = QGridLayout()
        for i, (label, key) in enumerate((("External port", "port"), ("Interlayer via/contact", "interface"),
                                         ("Passive voltage probe", "probe"), ("Floating electrode", "floating"),
                                         ("Gate control", "gate"), ("Boundary terminal", "terminal"))):
            grid.addWidget(button(label, lambda kind=key: self.new_connection(kind)), i // 2, i % 2)
        self.page_layout.addLayout(grid)
        self.object_list = QListWidget()
        self.object_list.setMaximumHeight(135)
        self.object_list.setAccessibleName("Connections and probes")
        for key, objects in (("ports", self.window.project.power.ports), ("terminals", self.window.project.terminals),
                              ("interfaces", self.window.project.interfaces)):
            for obj in objects:
                owner = next((port.id for port in self.window.project.power.ports
                              if any(landing.terminal_id == obj.id for landing in port.landings)), "") if key == "terminals" else ""
                description = f"landing of {owner}" if owner else getattr(obj, "kind", "via/contact")
                item = QListWidgetItem(f"{obj.id} · {description}")
                if owner:
                    item.setToolTip(f"Physical footprint of external port {owner}. Edit its drive in Operating conditions → External port drives.")
                item.setData(Qt.ItemDataRole.UserRole, (key, obj.id))
                self.object_list.addItem(item)
        self.object_list.itemClicked.connect(lambda item: self.edit_model(*item.data(Qt.ItemDataRole.UserRole)))
        self.page_layout.addWidget(self.object_list)
        self.editor_host = QWidget()
        self.editor_layout = QVBoxLayout(self.editor_host)
        self.editor_layout.setContentsMargins(0, 0, 0, 0)
        self.page_layout.addWidget(self.editor_host, 1)
        self.set_primary("Set operating conditions", lambda: self.navigate("conditions"))

    def conditions_page(self):
        w = self.window
        self.heading.setText("Operating conditions")
        self.description.setText(next(x.summary for x in ANALYSES if x.key == analysis_key(w.project)))
        self.page_layout.addWidget(button("Maximum operating temperature…", w.edit_temperature_limits))
        self.condition_kind = QComboBox()
        for text, key in (("Electrical inputs / gate controls", "terminals"), ("External port drives", "ports"),
                           ("Thermal boundaries / reservoirs", "thermal_boundaries"), ("Prescribed heat inputs", "heat_sources"),
                           ("Initial state and numerical controls", "solver")):
            self.condition_kind.addItem(text, key)
        self.condition_kind.setAccessibleName("Operating condition group")
        electrical = w.project.solver.mode != "thermal" or w.project.solver.thermal_include_joule
        default_group = "ports" if electrical and w.project.power.ports else "terminals" if electrical else "thermal_boundaries"
        self.condition_kind.setCurrentIndex(self.condition_kind.findData(self._condition_group or default_group))
        self.page_layout.addWidget(self.condition_kind)
        self.condition_hint = text_label("", "subtle")
        self.page_layout.addWidget(self.condition_hint)
        self.object_list = QListWidget()
        self.object_list.setMaximumHeight(140)
        self.object_list.setAccessibleName("Applied operating conditions")
        self.page_layout.addWidget(self.object_list)
        self.add_condition = button("Add current / voltage input", self.new_condition)
        self.page_layout.addWidget(self.add_condition)
        if w.project.solver.mode == "thermal":
            self.page_layout.addWidget(w.thermal_joule)
            w.thermal_joule.show()
        if w.project.transient.enabled:
            self.page_layout.addWidget(text_label("Thermal storage with quasi-static electrical fields; no electromagnetic switching model.", "hint"))
            self.page_layout.addWidget(button("Time, waveform & initial-state settings…", w.edit_transient))
            if w.project.transient.initialization in {"saved", "resume"}:
                self.page_layout.addWidget(button("Resolve and validate saved initial state", w.validate_setup))
        self.editor_host = QWidget()
        self.editor_layout = QVBoxLayout(self.editor_host)
        self.editor_layout.setContentsMargins(0, 0, 0, 0)
        self.page_layout.addWidget(self.editor_host, 1)
        self.condition_kind.currentIndexChanged.connect(self.populate_conditions)
        self.object_list.itemClicked.connect(lambda item: self.edit_model(*item.data(Qt.ItemDataRole.UserRole)))
        self.populate_conditions()
        self.set_primary("Review mesh & checks", lambda: self.navigate("mesh"))

    def populate_conditions(self):
        if not self.resolve_draft():
            self.condition_kind.blockSignals(True)
            self.condition_kind.setCurrentIndex(self.condition_kind.findData(self._condition_group))
            self.condition_kind.blockSignals(False)
            return
        key = self.condition_kind.currentData()
        self._condition_group = key
        self.object_list.clear()
        self.clear_editor()
        labels = {"terminals": "Add current / voltage input", "ports": "Add external port",
                  "thermal_boundaries": "Add thermal boundary", "heat_sources": "Add heat source", "solver": "Edit solver controls"}
        self.add_condition.setText(labels[key])
        electrical = self.window.project.solver.mode != "thermal" or self.window.project.solver.thermal_include_joule
        hints = {
            "terminals": "Independent electrical inputs and gate controls. Port-owned landing footprints are driven by their external port.",
            "ports": "These applied drives set the total input across each port's landings; finite landing resistance is retained.",
            "thermal_boundaries": "Heat-removal boundaries and their named reservoir temperatures.",
            "heat_sources": "Prescribed heat is separate from the Joule losses calculated in coupled analysis.",
            "solver": "Initial temperature and numerical controls for the applied analysis.",
        }
        hint = hints[key]
        if key in {"terminals", "ports"} and not electrical:
            hint += " Electrical drives are retained but inactive in this prescribed-source thermal analysis."
        elif key == "thermal_boundaries" and self.window.project.solver.mode == "electrical":
            hint += " Thermal boundaries are retained but inactive in this fixed-temperature electrical analysis."
        self.condition_hint.setText(hint)
        if key == "solver":
            self.open_editor("solver", self.window.project.solver)
            return
        for obj in self.objects(key):
            if key == "terminals" and any(landing.terminal_id == obj.id for port in self.window.project.power.ports for landing in port.landings):
                continue
            unit = {"voltage": "V", "ground": "V", "gate": "V", "current": "A", "temperature": "K",
                    "coefficient": "W/(m² K)", "resistance_area": "m² K/W", "resistance": "K/W",
                    "density": "W/m²" if key == "heat_sources" else "A/m²", "total": "W"}.get(obj.kind, "")
            value = 0. if obj.kind == "ground" else obj.value
            item = QListWidgetItem(f"{obj.id} · {obj.kind} · {value:g} {unit}")
            item.setData(Qt.ItemDataRole.UserRole, (key, obj.id))
            self.object_list.addItem(item)

    def new_condition(self):
        key = self.condition_kind.currentData()
        if key in {"terminals", "ports"}:
            self.new_connection("port" if key == "ports" else "terminal")
        elif key == "solver":
            self.edit_settings("solver")
        else:
            self.new_model(key)

    def mesh_page(self):
        self.heading.setText("Validation & meshing")
        self.description.setText("Check current inputs and generate a real mesh. Convergence is a separate study.")
        self.readiness = text_label("", "hint")
        self.page_layout.addWidget(self.readiness)
        self.mesh_status = text_label()
        self.page_layout.addWidget(self.mesh_status)
        self.page_layout.addWidget(button("Run validation", self.validate))
        self.page_layout.addWidget(button("Locate / fix issues", lambda: self.open_drawer(0)))
        self.editor_host = QWidget()
        self.editor_layout = QVBoxLayout(self.editor_host)
        self.editor_layout.setContentsMargins(0, 0, 0, 0)
        self.page_layout.addWidget(self.editor_host, 1)
        self.open_editor("mesh", self.window.project.mesh)
        self.page_layout.addWidget(button("Add local refinement", lambda: self.new_model("refinements")))
        self.refinement_flux = QCheckBox("Include bounded flux-jump guidance from current result")
        self.refinement_flux.setObjectName("refinement_flux_guidance")
        self.refinement_flux.setToolTip("Optional error indicator; a compatible converged result is required. Does not automatically change or rerun the mesh.")
        self.page_layout.addWidget(self.refinement_flux)
        self.page_layout.addWidget(button("Suggest local refinement…", self.suggest_local_refinement))
        self.page_layout.addWidget(button("Preview existing mesh", self.preview_mesh))
        self.set_primary("Generate / update mesh", self.mesh)

    def run_page(self):
        w = self.window
        self.heading.setText("Run analysis")
        self.description.setText("The worker receives an immutable copy of the applied setup. Earlier results remain in History.")
        self.run_summary = text_label("", "hint")
        self.page_layout.addWidget(self.run_summary)
        self.run_progress = text_label(self.execution_state)
        self.run_progress.setAccessibleName("Execution state")
        self.page_layout.addWidget(self.run_progress)
        self.page_layout.addWidget(button("Review blockers / warnings", lambda: self.open_drawer(0)))
        self.page_layout.addWidget(button("Execution settings…", w.edit_execution_settings))
        self.page_layout.addWidget(button("Show job & log", lambda: self.open_drawer(1)))
        self.cancel_run = button("Cancel computation", w.cancel_job)
        self.page_layout.addWidget(self.cancel_run)
        self.view_run = button("View current result", lambda: self.navigate("results"))
        self.page_layout.addWidget(self.view_run)
        self.page_layout.addStretch()
        self.set_primary("Run analysis", self.run)

    def results_page(self):
        w = self.window
        self.heading.setText("Results & analysis")
        self.description.setText("One selected run drives fields, probes, metrics and export.")
        self.result_evidence = text_label("", "hint")
        self.page_layout.addWidget(self.result_evidence)
        # Compact the existing bound controls into a single column for the inspector.
        if not getattr(self, "_result_form_compact", False):
            old = w.result_controls.layout()
            for control in (w.field_layer, w.field_combo, w.field_region, w.view_mode, w.field_hint):
                old.removeWidget(control)
                control.setParent(self.parking)
            holder = QWidget()
            holder.setLayout(old)
            holder.setParent(self.parking)
            holder.hide()
            form = QFormLayout(w.result_controls)
            form.setContentsMargins(0, 0, 0, 0)
            form.setRowWrapPolicy(QFormLayout.RowWrapPolicy.WrapLongRows)
            for title, control in (("&Layer", w.field_layer), ("&Quantity", w.field_combo), ("&Region", w.field_region), ("Map", w.view_mode)):
                control.setMinimumContentsLength(12)
                label = w.view_mode_label if control is w.view_mode else QLabel(title)
                label.setBuddy(control)
                form.addRow(label, control)
                control.show()
            form.addRow(w.isolate_layer)
            w.isolate_layer.show()
            form.addRow(w.fit_layer_button)
            w.fit_layer_button.show()
            form.addRow(w.field_hint)
            self._result_form_compact = True
        self.page_layout.addWidget(w.result_controls)
        w.result_controls.show()
        self.page_layout.addWidget(w.display_tabs)
        w.display_tabs.setMinimumHeight(220)
        w.display_tabs.show()
        for entry in (w.legend_scale.minimum, w.legend_scale.maximum):
            entry.setMinimumWidth(60)
        self.probe_table = QTableWidget(0, 3)
        self.probe_table.setHorizontalHeaderLabels(["X / Y (µm)", "Value", "Scope"])
        self.probe_table.setMaximumHeight(140)
        self.probe_table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.probe_table.horizontalHeader().setStretchLastSection(True)
        self.page_layout.addWidget(text_label("Probe readout", "section"))
        self.page_layout.addWidget(self.probe_table)
        self.result_metrics = text_label("", "hint")
        self.page_layout.addWidget(self.result_metrics)
        for title, callback in (("Place result probe", lambda: w.set_tool("probe")),
                                ("Metrics & loss budget", lambda: self.show_page(w.results_page, "Quantitative results")),
                                ("Compare saved runs…", w.compare_maps_dialog),
                                ("Return to working setup", self.return_to_setup)):
            control = button(title, callback)
            control.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Fixed)
            control.setToolTip(title)
            self.page_layout.addWidget(control)
        self.set_primary("Export results…", self.export_task)
        self.refresh_probes()

    def studies_page(self):
        self.heading.setText("Studies")
        self.description.setText("Baseline → Allowed changes → Scenarios → Goals / limits → Preview → Evaluate → Compare / verify")
        self.page_layout.addWidget(text_label("Single runs do not require a study. Draft a study from the current setup, then satisfy its backend checks before evaluation.", "hint"))
        for title, fn in (("Parameter sweep…", self.window.run_sweep_dialog), ("Mesh convergence…", self.window.run_convergence_dialog),
                           ("Time-step convergence…", self.window.run_time_convergence_dialog), ("Compare project designs…", self.window.compare_dialog),
                           ("Routing alternatives", lambda: self.open_studies("routing")), ("Constrained optimization", self.open_studies),
                           ("Passive multiport / power analysis", lambda: self.open_studies("analysis")),
                           ("Saved study results", lambda: self.show_page(self.window.study_panel, "Study results"))):
            self.page_layout.addWidget(button(title, fn))
        self.page_layout.addStretch()
        self.set_primary("Open routing & optimization", self.open_studies)

    def objects(self, key, project=None):
        project = project or self.window.project
        if key in {"ports", "measurements", "devices", "recipes", "experiments"}:
            return getattr(project.power, key)
        if key == "refinements":
            return project.mesh.refinements
        return getattr(project, key)

    def clear_editor(self):
        self.editor = None
        if not hasattr(self, "editor_layout"):
            return
        while self.editor_layout.count():
            item = self.editor_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

    def open_editor(self, key, model, *, is_new=False, port=False):
        self.clear_editor()
        choices = self.window.choices()
        if key == "layers":
            choices["material_id"] = {"": "Choose a material…", **{x: self.window.project.materials[x].name for x in sorted(self.window.project.materials)}}
        if port:
            choices["kind"] = {"voltage": "Voltage source (V)", "current": "Total current (A)",
                               "ground": "Reference / return (0 V)", "floating": "Floating equipotential"}
        if key == "ports":
            choices["terminal_id"] = [t.id for t in self.window.project.terminals]
        self.editor = InlineInspector(model, choices, self.window.mask_keys(), is_new=is_new)
        self.editor.apply_button.clicked.disconnect()
        self.editor.apply_button.clicked.connect(self.apply_draft)
        self.editor_key, self.editor_id = key, getattr(model, "id", "")
        self.editor_is_new, self.editor_is_port = is_new, port
        self.editor.applied.connect(self.commit_editor)
        self.editor.cancelled.connect(self.discard_draft)
        self.editor.changed.connect(self.update_draft_state)
        self.editor.pick_requested.connect(lambda: self.begin_selection("editor"))
        self.editor.numeric_requested.connect(self.numeric_editor_region)
        self.editor_layout.addWidget(self.editor, 1)
        self.editor.show()
        if port:
            self.landing_mode = QComboBox()
            self.landing_mode.addItem("One combined footprint", "combined")
            self.landing_mode.addItem("Separate footprints · one external port", "separate")
            self.landing_mode.setAccessibleName("Multi-landing assignment")
            self.feed_resistance = QLineEdit("0")
            self.feed_resistance.setAccessibleName("Resistance per landing (ohm)")
            self.feed_fraction = QLineEdit("0")
            self.feed_fraction.setAccessibleName("Fraction of feed loss deposited in landing")
            self.feed_resistance.textChanged.connect(self.editor.mark_dirty)
            self.feed_fraction.textChanged.connect(self.editor.mark_dirty)
            self.landing_mode.currentIndexChanged.connect(self.editor.mark_dirty)
            port_form = QFormLayout()
            port_form.addRow("Assignment", self.landing_mode)
            port_form.addRow("Feed resistance / landing (Ω)", self.feed_resistance)
            port_form.addRow("Local heat fraction (0–1)", self.feed_fraction)
            host = QWidget()
            host.setLayout(port_form)
            self.editor_layout.addWidget(host)
        self.update_draft_state()

    def edit_model(self, key, identifier, resolve=True):
        if resolve and not self.resolve_draft():
            return
        if not hasattr(self, "editor_host") or self.stage not in {"layers", "connections", "conditions", "mesh"}:
            self.navigate({"layers": "layers", "interfaces": "connections", "terminals": "connections", "ports": "connections",
                           "thermal_boundaries": "conditions", "heat_sources": "conditions", "refinements": "mesh"}.get(key, "conditions"))
        model = next((x for x in self.objects(key) if x.id == identifier), None)
        if model is None:
            return
        self.open_editor(key, model)
        if key == "layers":
            self.selected_layer = identifier
            self.layer_visible.blockSignals(True)
            self.layer_visible.setChecked(model.visible)
            self.layer_visible.blockSignals(False)
            self.active_layer.setCurrentIndex(self.active_layer.findData(identifier))
        self.locate_object(key, identifier, frame=False)

    def new_model(self, key, region=None):
        if not self.resolve_draft():
            return
        w = self.window
        layer = self.active_layer.currentData() or (w.project.layers[0].id if w.project.layers else "")
        if not layer:
            self.next_reason.setText("Add a physical layer before assigning conditions.")
            return
        identifier = w.unique_id({"thermal_boundaries": "sink", "heat_sources": "heat", "refinements": "refinement"}[key], self.objects(key))
        support = region or RegionSpec(full_domain=key in {"thermal_boundaries", "heat_sources"})
        model = (ThermalBoundary(identifier, layer, support) if key == "thermal_boundaries" else
                 HeatSource(identifier, layer, support) if key == "heat_sources" else
                 RefinementRegion(identifier, support, w.project.mesh.contact_size))
        self.open_editor(key, model, is_new=True)

    def new_connection(self, kind="port", region=None):
        if not self.resolve_draft():
            return
        w = self.window
        layer = self.active_layer.currentData() or next((x.id for x in w.project.layers if x.role == "conductor"), "")
        if not layer:
            self.next_reason.setText("Map a conducting physical layer before adding an electrical connection.")
            return
        if self.stage not in {"connections", "conditions"}:
            self.navigate("connections")
        support = region or RegionSpec()
        if kind == "interface":
            other = next((x.id for x in w.project.layers if x.id != layer), "")
            model = Interface(w.unique_id("contact", w.project.interfaces), layer, other, support)
            self.open_editor("interfaces", model, is_new=True)
        else:
            identifier = w.unique_id(kind, [*w.project.terminals, *w.project.power.ports])
            model = Terminal(identifier, layer, support,
                             kind="voltage" if kind in {"terminal", "port"} else kind,
                             name=identifier)
            self.open_editor("terminals", model, is_new=True, port=kind == "port")

    def commit_editor(self, model):
        key, identifier, new, port = self.editor_key, self.editor_id, self.editor_is_new, self.editor_is_port
        try:
            self.validate_editor_model(model, key, port)
            resistance = float(self.feed_resistance.text()) if port else 0.
            fraction = float(self.feed_fraction.text()) if port else 0.
            separate = port and self.landing_mode.currentData() == "separate"

            def mutate(project):
                if key in {"mesh", "solver"}:
                    setattr(project, key, deepcopy(model))
                    return
                objects = self.objects(key, project)
                if new and any(x.id == model.id for x in objects):
                    raise ValueError(f"Identifier {model.id} already exists.")
                if port:
                    shapes = [model.geometry]
                    if separate:
                        shape = resolve_region(model.geometry, self.window.masks, self.window.geometry.domain if self.window.geometry else None)
                        shapes = [geometry_region(piece) for piece in (list(shape.geoms) if hasattr(shape, "geoms") else [shape])]
                    landings = []
                    for support in shapes:
                        footprint = deepcopy(model)
                        footprint.id = self.window.unique_id(model.id + "_landing", objects)
                        footprint.geometry, footprint.kind, footprint.value = support, "floating", 0.
                        objects.append(footprint)
                        landings.append(PortLanding(footprint.id, resistance, fraction))
                    if any(x.id == model.id for x in project.power.ports):
                        raise ValueError("External port identifier is already used.")
                    project.power.ports.append(PhysicalPort(model.id, landings, model.kind, model.value, name=model.name))
                elif new:
                    objects.append(deepcopy(model))
                else:
                    index = next(i for i, x in enumerate(objects) if x.id == identifier)
                    objects[index] = deepcopy(model)
            self.editor.dirty = False
            self.document.transact("Applied " + (getattr(model, "id", key)), mutate)
            self.navigate(self.stage, force=True)
        except (ValueError, StopIteration) as error:
            if self.editor:
                self.editor.form.show()
                self.editor.form.setResult(0)
                self.editor.mark_dirty()
            self.next_reason.setText(str(error))

    def validate_editor_model(self, model, key, port=False):
        if key == "layers" and model.material_id not in self.window.project.materials:
            raise ValueError("Choose a material explicitly; an unassigned layer cannot participate in physics.")
        if hasattr(model, "geometry") and key not in {"layers", "refinements"}:
            self.validate_support(model, key)
        if port:
            resistance, fraction = float(self.feed_resistance.text()), float(self.feed_fraction.text())
            if not np.isfinite(resistance) or resistance < 0 or not np.isfinite(fraction) or not 0 <= fraction <= 1:
                raise ValueError("Landing resistance must be finite and nonnegative; heat fraction must be 0–1.")

    def validate_support(self, model, key):
        geometry = self.window.geometry
        if geometry is None:
            raise ValueError("Wait for current layout preparation before assigning a footprint.")
        support = resolve_region(model.geometry, self.window.masks, geometry.domain)
        if support.is_empty:
            raise ValueError("Choose a nonempty geometric support before applying.")
        layer_ids = [model.layer_a, model.layer_b] if key == "interfaces" else [model.layer_id]
        if key == "interfaces" and model.heat_layer_id:
            target = geometry.layers.get(model.heat_layer_id)
            if target is None or support.difference(target).area > max(support.area * 1e-9, 1e-30):
                raise ValueError("The heat-deposition layer must cover the complete interface support. Choose a supported layer or correct its geometry.")
        for layer_id in layer_ids:
            layer = geometry.layers.get(layer_id)
            if layer is None or support.intersection(layer).is_empty:
                raise ValueError(f"The footprint does not intersect physical layer {layer_id}. Choose the correct layer or redraw it.")

    def update_draft_state(self):
        if self.editor and self.editor.has_changes():
            self.next_reason.setText("Unapplied inspector draft. Apply or Cancel before leaving or running.")
            self.set_primary("Apply inspector changes", self.apply_draft)

    def numeric_editor_region(self):
        if not self.editor:
            return
        dialog = RegionDialog(self.editor.form.model.geometry, self.window.mask_keys(), self.window)
        if dialog.exec():
            self.preview_region("numeric", dialog.region, purpose="editor")

    def capture_context(self):
        return {"stage": self.stage, "layer": self.selected_layer,
                "canvas": self.canvas_tabs.tabText(self.canvas_tabs.currentIndex()),
                "drawer": self.drawer.currentIndex(), "drawer_visible": not self.drawer.isHidden()}

    def restore_context(self, state):
        self.selected_layer = state.get("layer", "")
        self.navigate(state.get("stage", "results"), force=True)
        for index in range(self.canvas_tabs.count()):
            if self.canvas_tabs.tabText(index) == state.get("canvas"):
                self.canvas_tabs.setCurrentIndex(index)
                break
        self.drawer.setCurrentIndex(state.get("drawer", 3))
        self.panels.set_visible("drawer", state.get("drawer_visible", True))

    def edit_settings(self, key):
        if not self.resolve_draft():
            return
        self.navigate("mesh" if key == "mesh" else "conditions", force=True)
        self.open_editor(key, getattr(self.window.project, key))

    def open_expert(self, key=""):
        if not self.resolve_draft():
            return
        w = self.window
        if key == "materials":
            w.config_tabs.setCurrentWidget(w.material_panel)
        elif key == "settings":
            w.config_tabs.setCurrentIndex(w.config_tabs.count() - 1)
        elif key in w.panels:
            w.config_tabs.setCurrentWidget(w.panels[key])
        self.show_page(w.config_tabs, "Complete model")

    def change_analysis(self):
        if self.refreshing:
            return
        key = self.analysis.currentData()
        if key == analysis_key(self.window.project):
            return
        if not self.resolve_draft() or self.window.job or self.window.inspection_context:
            self.refresh()
            return
        # The summary describes new prerequisites immediately; assignments and
        # dormant waveforms stay in the same undoable document transaction.
        self.document.transact("Analysis changed to " + self.analysis.currentText(), lambda project: set_analysis(project, key))
        self.navigate(self.stage, force=True)

    def change_layer(self):
        if self.refreshing:
            return
        self.selected_layer = self.active_layer.currentData() or ""
        if self.pending_purpose == "editor" and self.editor:
            field = self.editor.form.widgets.get("layer_id")
            if field is not None:
                field.setCurrentIndex(field.findData(self.selected_layer))
        if self.selection_banner.isVisible():
            self.apply_pick_filter()

    def set_visible(self, visible):
        if self.refreshing or not self.selected_layer:
            return
        self.document.transact("Changed display visibility", lambda project: setattr(project.layer(self.selected_layer), "visible", visible))

    def apply_pick_filter(self):
        view = self.window.view
        if not hasattr(view, "set_selection_filter"):
            return
        source = self.window.project.layout
        allowed = None
        if self.pending_purpose == "editor" and self.editor:
            fields = self.editor.form.widgets
            if "layer_id" in fields:
                allowed = [self.editor.form.choice("layer_id")]
            elif "layer_a" in fields:
                allowed = [self.editor.form.choice("layer_a"), self.editor.form.choice("layer_b")]
        view.set_selection_filter(allowed, purpose=self.pending_purpose,
                                  provenance={"source_path": source.path, "source_hash": source.sha256,
                                              "instance_path": source.instance_path})

    def begin_selection(self, purpose="domain", polygon=False):
        if self.layout_editor.active and not self.layout_editor.finish():
            return
        if self.window.job or self.window.inspection_context:
            return
        self.selection.begin(purpose)
        if self.stage == "layout" and purpose != "editor":
            self.region_kind.blockSignals(True)
            self.region_kind.setCurrentIndex(self.region_kind.findData(purpose))
            self.region_kind.blockSignals(False)
        self.selection_banner.show()
        self.apply_pick_filter()
        mode = "display_rect" if purpose == "view_crop" else "terminal_polygon" if polygon and purpose == "editor" else "roi_polygon" if polygon else "terminal_rect" if purpose == "editor" else "roi_rect"
        self.window.set_tool(mode)
        self.selection_text.setText(f"Selecting {purpose.replace('_', ' ')} · draw a region or select visible footprints")
        self.use_selection.setEnabled(False)

    def change_region_purpose(self, *_):
        purpose = self.region_kind.currentData()
        requested = self.selection.requested
        self.pending_purpose = purpose
        self.apply_pick_filter()
        if requested is not None:
            # This is an explicit draft edit. Drawing/preview modes never infer
            # a replacement purpose; the same requested geometry is re-clipped.
            self.preview_region("purpose", geometry_region(requested), purpose=purpose)

    def preview_region(self, mode, region, *, purpose=None):
        if self.window.job or self.window.inspection_context:
            return
        if purpose is not None:
            self.selection.begin(purpose)
        if not self.selection.explicit:
            if mode.startswith("terminal"):
                self.new_connection("terminal")
                self.pending_purpose = "editor"
            elif mode.startswith("interface"):
                self.new_connection("interface")
                self.pending_purpose = "editor"
            elif mode == "refine":
                self.navigate("mesh")
                self.new_model("refinements")
                self.pending_purpose = "editor"
            else:
                self.pending_purpose = "view_crop" if mode.startswith("display") else "domain"
        if not hasattr(self, "_selection_primary"):
            self._selection_primary = (self.primary.text(), self.primary_callback)
        self.set_primary("Use selected support" if self.pending_purpose == "editor" else "Use selected region", self.commit_selection)
        self.selection_banner.show()
        try:
            domain = self.window.geometry.domain if self.window.geometry else None
            support = domain if self.pending_purpose == "measurement" else None
            if self.pending_purpose == "editor" and self.editor:
                support = domain
                fields = self.editor.form.widgets
                for key in ("layer_id", "layer_a", "layer_b"):
                    if key in fields and self.window.geometry:
                        layer = self.window.geometry.layers.get(self.editor.form.choice(key))
                        if layer is not None:
                            support = layer if support is None else support.intersection(layer)
            shape = self.selection.preview(region, self.window.masks, domain, support)
            if hasattr(self.window.view, "show_pending_region"):
                self.window.view.show_pending_region(shape, "Pending " + self.pending_purpose.replace("_", " "),
                                                     requested=self.selection.requested)
            components = len(shape.geoms) if hasattr(shape, "geoms") else 1
            x0, y0, x1, y1 = shape.bounds
            scope = "visible source / physical geometry"
            if self.pending_purpose == "editor" and self.editor:
                fields = self.editor.form.widgets
                keys = [key for key in ("layer_id", "layer_a", "layer_b") if key in fields]
                scope = ", ".join(self.editor.form.choice(key) for key in keys) or scope
            self.selection_text.setText(f"Selecting {self.pending_purpose.replace('_', ' ')} · {scope} · {components} footprint(s)\n"
                                       f"Area {shape.area * 1e12:.6g} µm² · {(x1-x0)*1e6:.5g} × {(y1-y0)*1e6:.5g} µm · not yet applied")
            if not shape.equals(self.selection.requested):
                self.selection_text.setText(self.selection_text.text() +
                    f"\nRequested {self.selection.requested.area * 1e12:.6g} µm² → effective {shape.area * 1e12:.6g} µm² (clipped to physical support)")
                self.selection_text.setToolTip(f"Requested bounds: {self.selection.requested.bounds}; effective bounds: {shape.bounds} (SI m)")
            self.selection_text.setToolTip(self.selection_text.toolTip() + f"\nBounds in µm: ({x0*1e6:.9g}, {y0*1e6:.9g}) to ({x1*1e6:.9g}, {y1*1e6:.9g}). "
                                          "Ctrl+click adds/removes footprints. Connected geometry and validated nets are explicit selection actions.")
            self.use_selection.setEnabled(True)
            if self.stage == "layout" and hasattr(self, "domain_preview"):
                x0, y0, x1, y1 = shape.bounds
                for key, value in {"x": x0, "y": y0, "width": x1 - x0, "height": y1 - y0}.items():
                    self.bounds_fields[key].setText(f"{value * 1e6:.9g}")
                affected = self.affected_by_domain(shape)
                self.domain_preview.setText("Affected objects: " + (", ".join(affected) or "none") + ". Cut conducting paths require validation and real boundary correction.")
        except (SimulationError, ValueError, TypeError) as error:
            self.selection.clear()
            self.window.view.clear_pending_region()
            self.selection_text.setText(str(error))
            self.use_selection.setEnabled(False)

    def numeric_domain(self):
        try:
            values = {key: float(field.text()) * 1e-6 for key, field in self.bounds_fields.items()}
            if not all(np.isfinite(x) for x in values.values()) or values["width"] <= 0 or values["height"] <= 0:
                raise ValueError("Enter finite coordinates and positive width and height.")
            self.preview_region("numeric", geometry_region(box(values["x"], values["y"], values["x"] + values["width"], values["y"] + values["height"])),
                                purpose=self.region_kind.currentData())
        except ValueError as error:
            self.next_reason.setText(str(error))

    def selection_from_canvas(self):
        selected = self.window.view.selected_geometries()
        if not selected:
            self.next_reason.setText("Select visible geometry in the canvas first.")
            return
        purpose = self.region_kind.currentData() if self.stage == "layout" and not self.selection.explicit else None
        self.preview_region("selection", geometry_region(unary_union([shape for _, shape in selected])), purpose=purpose)

    def selection_changed(self):
        if self.layout_editor.active:
            return
        if self.selection_banner.isVisible() and self.window.view.selected_geometries():
            self.selection_from_canvas()

    def clear_selection(self):
        self.pending_region = None
        self.window.view.scene().clearSelection()
        if hasattr(self.window.view, "clear_pending_region"):
            self.window.view.clear_pending_region()
        self.selection_text.setText("Selection cleared · choose a footprint")
        self.use_selection.setEnabled(False)

    def cancel_selection(self):
        self.pending_region = None
        self.selection.explicit = False
        self.selection_banner.hide()
        self.window.view.scene().clearSelection()
        if hasattr(self.window.view, "clear_pending_region"):
            self.window.view.clear_pending_region()
            self.window.view.clear_selection_filter()
        self.window.set_tool("select")
        previous = getattr(self, "_selection_primary", None)
        if previous:
            del self._selection_primary
            self.set_primary(*previous)
        self.update_draft_state()

    def commit_selection(self):
        region = self.pending_region
        if region is None:
            self.selection_from_canvas()
            region = self.pending_region
        if region is None:
            return False
        purpose = self.pending_purpose
        if purpose == "editor":
            if self.editor is None:
                self.next_reason.setText("The target inspector is no longer open. Discard this selection and choose a connection to edit.")
                return False
            self.editor.set_region(region)
            self.cancel_selection()
            return True
        if purpose not in {"measurement", "keepout", "view_crop", "domain"}:
            self.next_reason.setText("Unknown selection purpose. Discard this selection and start a new one.")
            return False
        if purpose == "measurement":
            model = MeasurementSpec(self.window.unique_id("measurement", self.window.project.power.measurements),
                                    "temperature", SupportSelector(geometry=deepcopy(region)))
            self.document.transact("Added measurement support", lambda project: project.power.measurements.append(model))
        elif purpose == "keepout":
            self.document.transact("Added routing keepout", lambda project: project.power.rules.keepouts.append(deepcopy(region)))
        else:
            def mutate(project):
                if purpose == "view_crop":
                    project.display_roi = deepcopy(region)
                else:
                    project.roi = deepcopy(region)
                    project.acknowledge_clipped_boundaries = False
            self.document.transact("Applied " + purpose.replace("_", " "), mutate)
        self.cancel_selection()
        return True

    def affected_by_domain(self, domain):
        geometry = self.window.geometry
        if geometry is None:
            return []
        return [key for group in (geometry.terminals, geometry.interfaces, geometry.thermal_boundaries, geometry.heat_sources)
                for key, support in group.items() if not domain.covers(support)]

    def apply_scope(self):
        if not self.resolve_draft():
            return
        w = self.window
        item = w.hierarchy.currentItem()
        data = item.data(0, Qt.ItemDataRole.UserRole) if item else None
        if not data or data[0] == "__page__":
            self.next_reason.setText("Select a cell or occurrence in the hierarchy first.")
            return
        affected = [x.id for group in (w.project.terminals, w.project.interfaces, w.project.thermal_boundaries, w.project.heat_sources) for x in group]
        detail = f"Cell: {data[0]}\nOccurrence: {data[1] or '(top)'}\nDomain will be cleared. Existing supports require revalidation:\n" + (", ".join(affected) or "No assigned supports")
        if QMessageBox.question(w, "Use as simulation scope", detail) != QMessageBox.StandardButton.Yes:
            return
        def mutate(project):
            project.layout.cell, project.layout.instance_path = data
            project.roi = None
            project.acknowledge_clipped_boundaries = False
        self.document.transact("Changed explicit simulation scope", mutate)

    def locate_object(self, key, identifier, frame=True):
        w = self.window
        geometry = w.geometry
        if geometry is None:
            return
        group = getattr(geometry, key, {})
        shape = group.get(identifier) if isinstance(group, dict) else None
        if key == "ports":
            port = next((x for x in w.project.power.ports if x.id == identifier), None)
            if port:
                shape = unary_union([geometry.terminals[x.terminal_id] for x in port.landings if x.terminal_id in geometry.terminals])
        if shape is not None and not shape.is_empty:
            w.view.show_region(shape, identifier, "#006bff")
            if frame:
                self.frame_bounds(shape.bounds)

    def frame_bounds(self, bounds):
        x0, y0, x1, y1 = bounds
        self.window.view.fitInView(QRectF(x0 * 1e6 - 2, -y1 * 1e6 - 2, max((x1 - x0) * 1e6, 1) + 4,
                                         max((y1 - y0) * 1e6, 1) + 4), Qt.AspectRatioMode.KeepAspectRatio)

    def show_stack(self):
        w = self.window
        if not w.geometry:
            return
        shape = w.geometry.layers.get(self.selected_layer, w.geometry.domain)
        x0, y0, x1, y1 = shape.bounds
        line = ((x0, (y0 + y1) / 2), (x1, (y0 + y1) / 2))
        w.last_cut = line
        w.cross_section.show_cut(w.project, w.geometry, *line)
        self.canvas_tabs.setCurrentWidget(w.cross_section)

    def refresh(self, rebuild=False):
        if self.refreshing:
            return
        self.refreshing = True
        w = self.window
        try:
            self.job_cancel.setEnabled(self.command_availability("simulation.cancel") is True)
            if not w.job:
                self.refresh_job_timings()
            self.project_name.setText(w.project.name)
            self.project_name.setToolTip(w.project.name)
            self.save_state.setText("Unsaved changes" if w.dirty else "Saved" if w.path else "Not saved")
            self.setup_context.setText("Inspecting run" if w.inspection_context else "Working setup")
            self.analysis.setCurrentIndex(self.analysis.findData(analysis_key(w.project)))
            self.analysis.setEnabled(not w.job and not w.inspection_context and not (self.power and self.power.job))
            previous_layer = self.active_layer.currentData() or self.selected_layer
            self.active_layer.clear()
            for layer in w.project.layers:
                self.active_layer.addItem(layer.name, layer.id)
            if previous_layer:
                self.active_layer.setCurrentIndex(max(0, self.active_layer.findData(previous_layer)))
            self.assessment = assess(w.project, geometry=w.geometry, diagnostics=self.check_batch or (),
                                     mesh_current=bool(w.mesh and self.mesh_identity == _mesh_key(w.project)), result=w.result,
                                     initial_state_supplied=self.initial_state_evidence == self.initial_state_key())
            for stage, status in self.assessment.stages.items():
                self.stage_status[stage].setText(status.status)
                self.stage_buttons[stage].setToolTip(status.reason)
            for key, action in self.actions.items():
                if any(command.key == key for command in self.registry):
                    action.setEnabled(self.registry.available(key))
            self.refresh_issues()
            if self.stage == "mesh" and hasattr(self, "readiness"):
                self.readiness.setText("\n".join(self.assessment.stages[x].status + " · " + self.assessment.stages[x].reason for x in ("connections", "conditions")))
                self.mesh_status.setText("Current mesh · " + str(len(w.mesh.triangles)) + " elements\nMesh convergence: not checked by mesh generation"
                                         if w.mesh and self.mesh_identity == _mesh_key(w.project) else "No current mesh · generate after reviewing inputs")
            if self.stage == "run" and hasattr(self, "run_summary"):
                project = w.job_context["project"] if w.job_context else w.project
                self.run_summary.setText(f"{'Running immutable' if w.job else 'Applied'} setup {configuration_hash(project)[:12]}\n"
                                         f"{next(x.label for x in ANALYSES if x.key == analysis_key(project))}\n"
                                         f"{len(project.layers)} physical layers · {len(project.terminals)} footprints\n"
                                         f"{len(project.power.ports)} external ports · {len(project.thermal_boundaries)} thermal boundaries\n"
                                         f"CPU budget: {w.execution_settings.cpu_threads}")
                drives = [f"{port.id}: {port.value:g} {'A' if port.kind == 'current' else 'V'}"
                          for port in project.power.ports if port.kind in {"current", "voltage"}]
                if drives and (project.solver.mode != "thermal" or project.solver.thermal_include_joule):
                    self.run_summary.setText(self.run_summary.text() + "\nExternal drives · " + " · ".join(drives))
                self.run_progress.setText(self.execution_state)
                self.cancel_run.setEnabled(bool(w.job or w.prepare_job or (self.power and self.power.job)))
                self.view_run.setEnabled(w.result is not None)
                self.primary.setEnabled(not w.model_locked() and self.assessment.ready)
                self.next_reason.setText(self.assessment.stages["run"].reason)
            if self.stage == "results" and hasattr(self, "result_evidence"):
                result = w.result
                if result:
                    working = w.inspection_context["project"] if w.inspection_context else w.project
                    # inspection_context stores the editable setup while w.project
                    # displays the immutable historical source.
                    fresh = physics_hash(result.configuration) == physics_hash(working)
                    result_state = "Current" if fresh else "Out of date for working setup"
                    if not fresh:
                        from etlayout.power.analysis import _validate_result_context
                        try:
                            _validate_result_context(working, result)
                        except SimulationError:
                            self.stage_status["results"].setText("Historical · out of date")
                        else:
                            result_state = "Fields reusable · reporting/study setup changed"
                            self.stage_status["results"].setText("Historical · fields reusable")
                    self.result_evidence.setText(f"Run {result.configuration_hash[:12]}\n"
                                                f"{result_state} · "
                                                f"{'Solver converged' if result.converged else 'Solver did not converge'}\n"
                                                "Mesh convergence: not established for this run")
                    if result.metrics.get("thermal_limit_compliance", {}).get("status") in {"failed", "unavailable"}:
                        self.result_evidence.setText(w.result_status.text())
                    self.result_metrics.setText(w.key_metrics.text())
                    self.stage_buttons["results"].setToolTip(result_state + ". Fields and metrics retain this run's recorded configuration.")
                else:
                    self.result_evidence.setText("No selected result. Run the current setup or inspect History.")
                self.primary.setEnabled(bool(result))
            busy = bool(w.job or (self.power and self.power.job))
            self.drawer_toggle.setText(f"{len(self.assessment.blockers)} blockers · {self.visible_issue_count} issues · "
                                       f"{'Job running · Cancel: Shift+F5' if busy else self.execution_state} · History {len(w.run_history)}")
            self.drawer_toggle.setToolTip(self.drawer_toggle.text())
            self.inspector.setEnabled(not busy or self.stage in {"run", "results"})
            self.canvas_hint.setText(self.assessment.stages[self.stage].reason if self.stage in self.assessment.stages else "Study definitions share the working setup.")
            self.apply_canvas_context()
        finally:
            self.refreshing = False
        if rebuild and not (self.selection.explicit and not self.selection_banner.isHidden()) and not (self.editor and self.editor.has_changes()):
            self.build_page()
            self.refresh()

    def refresh_issues(self):
        if not hasattr(self, "assessment"):
            return
        issues = list(self.assessment.issues)
        result = self.window.result
        if result is not None:
            issues.extend(WorkflowIssue(f"result.temperature.{index}", diagnostic.severity,
                diagnostic.message, detail=diagnostic.message, object_id=diagnostic.object_id,
                bounds=tuple(diagnostic.bounds) if diagnostic.bounds else None, stage="conditions")
                for index, diagnostic in enumerate(result.diagnostics)
                if diagnostic.message.startswith("Maximum temperature limit:"))
        if self.last_error:
            issues.insert(0, WorkflowIssue("job.failed", "error", "Last computation failed: " + self.last_error,
                                        detail=self.last_error_detail or "Open the setup, correct inputs and retry. Logs contain the worker traceback.",
                                        stage=self.last_error_stage))
        self.visible_issue_count = len(issues)
        wanted = self.severity.currentText()
        query = self.issue_search.text().lower()
        rows = [issue for issue in issues if (wanted == "All severities" or issue.severity == wanted)
                and query in (issue.message + issue.object_id + issue.stage).lower()]
        self.issue_table.setRowCount(len(rows))
        for row, issue in enumerate(rows):
            self.issue_table.setItem(row, 0, QTableWidgetItem(issue.severity.capitalize() + (" · blocks run" if issue.blocking else "")))
            message = QTableWidgetItem(issue.message)
            message.setToolTip(issue.detail or issue.message)
            self.issue_table.setItem(row, 1, message)
            self.issue_table.setItem(row, 2, QTableWidgetItem(issue.object_id or issue.stage))
            controls = QWidget()
            layout = QHBoxLayout(controls)
            layout.setContentsMargins(0, 0, 0, 0)
            locate = button("Locate", lambda item=issue: self.fix_issue(item, locate_only=True))
            locate.setEnabled(bool(issue.bounds or issue.object_id))
            layout.addWidget(locate)
            layout.addWidget(button("Open setup" if issue.key == "job.failed" or issue.key.startswith("result.temperature.") else "Fix…", lambda item=issue: self.fix_issue(item)))
            self.issue_table.setCellWidget(row, 3, controls)
        self.issue_table.resizeRowsToContents()
        self.drawer.setTabText(0, f"Issues ({len(issues)})")

    def fix_issue(self, issue, locate_only=False):
        if not locate_only and not self.resolve_draft():
            return
        if not locate_only and issue.key.startswith("result.temperature."):
            self.navigate("conditions")
            self.window.edit_temperature_limits()
            return
        if not locate_only:
            for key in ("measurements", "devices", "recipes", "experiments"):
                if any(x.id == issue.object_id for x in self.objects(key)):
                    self.open_studies(key)
                    return
        if issue.bounds:
            self.frame_bounds(issue.bounds)
        for key in ("layers", "terminals", "interfaces", "thermal_boundaries", "heat_sources", "refinements", "ports"):
            if any(x.id == issue.object_id for x in self.objects(key)):
                self.locate_object(key, issue.object_id)
                if not locate_only:
                    self.navigate(issue.stage)
                    self.edit_model(key, issue.object_id)
                    if self.editor and issue.field in self.editor.form.widgets:
                        self.editor.form.widgets[issue.field].setFocus()
                return
        if not locate_only:
            self.navigate(issue.stage)
            self.next_reason.setText(issue.message)
            if issue.stage == "conditions" and "thermal" in issue.message.lower():
                self.condition_kind.setCurrentIndex(self.condition_kind.findData("thermal_boundaries"))

    def apply_canvas_context(self):
        if self.layout_editor.active:
            self.canvas_hint.setText("Layout editor · edit vertices or move a polygon; inspect affected supports, then Apply. Undo restores the previous revision.")
            return
        w = self.window
        self.active_layer.setVisible(self.stage != "results")
        if self.stage != "results":
            w.view.show_field(None, "", "", False, False)
            w.view.set_layer_focus("")
            w.legend_scale.color_bar.hide()
            w.time_navigation.hide()
            w.selection_statistics.hide()
            self.context.setText(f"Working setup · {w.project.name} · {next(x.label for x in ANALYSES if x.key == analysis_key(w.project))}")
            self.canvas_tabs.setTabText(0, "Layout view")
            callouts = []
            if self.stage == "connections" and w.geometry:
                for terminal in w.project.terminals:
                    shape = w.geometry.terminals.get(terminal.id)
                    if shape is not None and not shape.is_empty:
                        point = shape.representative_point()
                        port = next((p for p in w.project.power.ports if any(x.terminal_id == terminal.id for x in p.landings)), None)
                        label = f"{port.id} · {port.kind}" if port else f"{terminal.name or terminal.id} · {terminal.kind}"
                        callouts.append(dict(id=terminal.id, label=label, layer_id=terminal.layer_id, point=(point.x, point.y)))
            w.view.show_callouts(callouts)
        else:
            w.time_navigation.setVisible(bool(w.result and w.result.transient))
            w.selection_statistics.show()
            self.canvas_tabs.setTabText(0, "Results view")
            self.context.setText(f"Inspecting run {w.result.configuration_hash[:12]} · {w.field_layer.currentText()} · {w.field_region.currentText()}"
                                 if w.result else "No run selected · use History to inspect an earlier calculation")
        allowed = ({"select", "pan", "probe", "profile", "branch_current", "cross_section", "measure"}
                   if self.stage == "results" else
                   {"select", "pan", "roi_rect", "roi_polygon", "display_rect", "measure"}
                   if self.stage == "layout" else
                   {"select", "pan", "terminal_rect", "terminal_polygon", "terminal_edge", "interface_rect", "refine", "cross_section", "roi_rect", "roi_polygon"})
        for index in range(w.tool_combo.count()):
            w.tool_combo.view().setRowHidden(index, w.tool_combo.itemData(index) not in allowed)

    def validate(self):
        if self.resolve_draft():
            self.window.validate_setup()

    def mesh(self):
        if self.resolve_draft():
            self.window.start_job("mesh")

    def suggest_local_refinement(self):
        if not self.window.model_locked() and self.resolve_draft():
            layer_id = self.active_layer.currentData() or None
            if layer_id and self.window.project.solver.mode == "electrical" and self.window.project.layer(layer_id).role != "conductor":
                layer_id = None
            self.window.start_job("refinement-guidance", {"result": self.window.result,
                                   "hotspot_layer": layer_id,
                                   "field_driven": self.refinement_flux.isChecked()})

    def review_refinements(self, data):
        from etlayout.gui.refinement import RefinementPreview, RefinementReviewDialog
        from etlayout.meshing.refinement import RefinementPlan, apply_refinement_plan
        plan = RefinementPlan.from_dict(data["plan"])

        def apply(plan, selected):
            self.document.transact("Applied reviewed local mesh suggestions", lambda project:
                                   apply_refinement_plan(project, plan, selected))

        def run(plan, selected, factors):
            if (self.window.project.transient.enabled and
                    self.window.project.transient.initialization in {"saved", "resume"}):
                raise SimulationError("Local refinement changes the mesh. Choose uniform or steady transient initialization before this study.")
            self.window.start_job("local-refine", {"sizes": factors, "local_plan": plan.to_dict(), "selected": selected})

        preview = RefinementPreview(self.window.view)
        dialog = RefinementReviewDialog(self.window.project, plan, self.window, geometry=data["geometry"],
                                       apply=apply, run=run, preview=preview.show)
        dialog.finished.connect(preview.clear)
        try:
            dialog.exec()
        finally:
            preview.clear()
            dialog.deleteLater()

    def preview_mesh(self):
        if self.window.mesh and self.mesh_identity == _mesh_key(self.window.project):
            self.window.mesh_toggle.setChecked(True)
            self.window.view.show_mesh(self.window.mesh)
            self.canvas_tabs.setCurrentIndex(0)
        else:
            self.next_reason.setText("Generate a mesh before previewing it. No mesh has been fabricated for this display.")

    def run(self):
        if self.window.model_locked():
            return
        if not self.resolve_draft():
            return
        self.refresh()
        if not self.assessment.ready:
            self.navigate("mesh", force=True)
            self.open_drawer(0)
            self.next_reason.setText(self.assessment.stages["run"].reason)
            return
        self.navigate("run", force=True)
        self.window.run()

    def job_started(self):
        self.execution_state = "Running " + self.window.operation
        self.last_error = ""
        self.refresh()
        self.job_message.setText(self.execution_state)
        self.job_timings.setText("Measuring this calculation; stage timings appear on completion.")
        self.open_drawer(1)

    def job_progress(self, message):
        self.execution_state = message
        self.job_message.setText(message)
        if self.stage == "run":
            self.run_progress.setText(message)

    def job_completed(self, operation, source):
        w = self.window
        self.execution_state = operation.capitalize() + " completed"
        if operation == "solve" and w.result and w.result.metrics.get("thermal_limit_compliance", {}).get("status") in {"failed", "unavailable"}:
            self.execution_state = "Failed engineering · maximum-temperature compliance"
        self.refresh_job_timings()
        if source and source.get("supplied_initial_state"):
            self.initial_state_evidence = self.initial_state_key()
        if source and physics_hash(source["project"]) == physics_hash(w.project):
            if operation in {"validate", "mesh"}:
                diagnostics = [*w.geometry.diagnostics, *(w.net_report.diagnostics if w.net_report else [])]
                self.check_batch = DiagnosticBatch(configuration_hash(source["project"]), tuple(diagnostics))
            if operation in {"mesh", "solve"} and w.mesh:
                self.mesh_identity = _mesh_key(source["project"])
        if operation == "solve":
            self.navigate("results", force=True)
        self.refresh(rebuild=True)

    def refresh_job_timings(self):
        result = self.window.result
        timings = result.execution.get("timings_seconds", {}) if result else {}
        if not timings:
            self.job_timings.setText("No measured stage timings for this recorded run.")
            self.job_timings.setToolTip("")
            return
        labels = (("preparation", "Preparation"), ("meshing", "Mesh"), ("numerical", "Numerical"),
                  ("reporting", "Reporting"), ("cached_result_load", "Cache retrieval"), ("total", "Total"))
        self.job_timings.setText(" · ".join(f"{label}: {timings[key]:.2f} s" for key, label in labels if key in timings))
        linear = timings.get("sparse_linear_solves")
        self.job_timings.setToolTip((f"Sparse linear solves: {linear:.2f} s (included in Numerical).\n" if linear is not None else "") +
            "Measured wall time for the recorded run. CPU budget and assembly concurrency do not imply a total speedup.")

    def initial_state_key(self):
        """Only reuse expensive restart validation while document and runs match."""
        w = self.window
        return physics_hash(w.project), tuple(id(run) for run in w.run_history), id(w.result)

    def job_failed(self, message, stage=None, detail=""):
        self.execution_state = "Failed · " + message
        self.last_error = message
        self.last_error_stage = stage or ("mesh" if self.window.operation in
                                         {"mesh", "refinement-guidance", "local-refine"} else "run")
        self.last_error_detail = detail
        self.job_message.setText(self.execution_state)
        self.refresh()
        self.open_drawer(0)
        self.next_reason.setText(message)

    def return_to_setup(self):
        self.window.return_to_project()
        self.navigate("conditions", force=True)

    def record_probe(self, x, y):
        w = self.window
        if self.stage != "results" or not w.result or w.view.overlay_item is None:
            return
        from etlayout.simulation.analysis import FIELD_UNITS
        from etlayout.visualization.sampling import DISPLAY_FIELD_LABELS as FIELD_LABELS
        from etlayout.visualization.units import to_display
        field, layer, region = w.field_combo.currentData(), w.field_layer.currentData(), w.field_region.currentData() or ""
        value = w.get_sampler().sample(layer, field, np.array([[x, y]]), region=region, logarithmic=w.log_scale.isChecked())[0]
        _, unit = FIELD_LABELS.get(field, (field, "SI"))
        value = to_display(value, FIELD_UNITS.get(field, ""))
        self.probes.append({"x": x, "y": y, "value": float(value), "unit": unit, "layer": layer, "region": region,
                            "field": field, "run": w.result.configuration_hash, "time": w.view_source_result().metrics.get("time_s")})
        self.refresh_probes()

    def refresh_probes(self):
        if self.stage != "results" or not hasattr(self, "probe_table"):
            return
        w = self.window
        rows = [p for p in self.probes if w.result and p["run"] == w.result.configuration_hash
                and p["time"] == w.view_source_result().metrics.get("time_s") and p["layer"] == w.field_layer.currentData()
                and p["field"] == w.field_combo.currentData() and p["region"] == (w.field_region.currentData() or "")]
        self.probe_table.setRowCount(len(rows))
        for i, row in enumerate(rows):
            for j, value in enumerate((f"{row['x'] * 1e6:.5g}, {row['y'] * 1e6:.5g}",
                                       f"{row['value']:.7g} {row['unit']}" if np.isfinite(row["value"]) else "Unavailable / outside support",
                                       row["layer"] + " / " + (row["region"] or "all"))):
                self.probe_table.setItem(i, j, QTableWidgetItem(value))
        w.view.show_callouts([dict(id=f"probe-{index}", label=f"P{index + 1} · {p['value']:.6g} {p['unit']}",
                                  layer_id=p["layer"], point=(p["x"], p["y"]))
                             for index, p in enumerate(rows) if np.isfinite(p["value"])])

    def open_studies(self, section="experiments"):
        if not self.resolve_draft() or self.window.job or self.window.inspection_context:
            return
        self.navigate("studies", force=True)
        retained = self.window.result
        if retained is None:
            from etlayout.models import SimulationError
            from etlayout.power.analysis import _validate_result_context
            for candidate in reversed(self.window.run_history):
                from etlayout.gui.history_workflow import is_record
                if is_record(candidate):
                    continue
                try:
                    _validate_result_context(self.document.project, candidate)
                    retained = candidate
                    break
                except SimulationError:
                    continue
        if self.power is None:
            from etlayout.gui.power_workbench import PowerWorkbench
            self.power = PowerWorkbench(self.document.project, retained, self.window,
                                        execution=self.window.execution_settings, mask_keys=self.window.mask_keys(), document=self.document)
            self.power.busy_changed.connect(self.power_busy)
            self.power.result_ready.connect(self.window.show_result)
            self.power.document_changed.connect(lambda: self.refresh(rebuild=True))
        else:
            self.power.refresh_document()
            self.power.bind_result(retained)
        if section in self.power.record_types:
            self.power.tabs.setCurrentIndex(0)
            self.power.setup_kind.setCurrentIndex(self.power.setup_kind.findData(section))
        else:
            self.power.tabs.setCurrentIndex({"setup": 0, "analysis": 1, "routing": 2, "experiments": 3}.get(section, 3))
        self.show_page(self.power, "Power & routing studies")

    def power_busy(self, busy):
        self.execution_state = "Study running" if busy else "Study idle"
        self.window.set_running(busy)
        self.refresh()

    def export_task(self):
        if not self.window.result:
            self.next_reason.setText("Select a calculated run first.")
            return
        menu = QMenu(self.window)
        result = self.window.view_source_result()
        menu.addSection("Run " + self.window.result.configuration_hash[:12])
        menu.addSection(self.window.field_combo.currentText() + " · " + self.window.field_layer.currentText())
        menu.addSection(self.window.field_region.currentText())
        if result.metrics.get("time_s") is not None:
            menu.addSection(f"Snapshot {result.metrics['time_s']:.9g} s")
        for _key, label, fn in (("figure", "Share figure or report…", lambda: self.window.share_export()),
                               ("data", "Share selected numerical data…", lambda: self.window.share_export("data")),
                               ("archive", "Archive complete experiment (confidential)…", lambda: self.window.share_export("archive")),
                               ("full", "Legacy complete numerical bundle (confidential)…", self.window.export_results),
                               ("candidate", "Verified candidate layout…", lambda: self.open_studies("experiments"))):
            menu.addAction(label, fn)
        menu.setTitle("Selected run " + self.window.result.configuration_hash[:12])
        menu.exec(self.primary.mapToGlobal(self.primary.rect().topLeft()))

    def export_run_archive(self):
        result = self.window.result
        if result is None:
            return
        path, _ = QFileDialog.getSaveFileName(self.window, "Export selected run archive", "selected-run.etlayout",
                                            "Electrothermal project (*.etlayout)")
        if path:
            try:
                save_project(project_from_dict(result.configuration), path, result, history=[result])
                self.window.log("Exported immutable run " + result.configuration_hash[:12] + " to " + path)
            except (OSError, ValueError) as error:
                self.window.error("Archive export failed", error)
