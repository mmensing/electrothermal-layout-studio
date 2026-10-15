# SPDX-License-Identifier: MPL-2.0
"""Project-owned vertex editor using the shared draft and document workflow."""
from __future__ import annotations

from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

from PySide6.QtCore import QEvent, QEventLoop, QObject, QPointF, QSettings, QStandardPaths, Qt
from PySide6.QtGui import QColor, QPen
from PySide6.QtWidgets import (
    QComboBox,
    QFormLayout,
    QFrame,
    QGraphicsItem,
    QGraphicsPathItem,
    QGraphicsRectItem,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QProgressDialog,
    QPushButton,
    QSpinBox,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)
from shapely.geometry import Polygon, box

from etlayout.geometry.edit_impacts import EditImpactPreview
from etlayout.geometry.polygon_edit import (
    delete_vertex,
    insert_midpoint,
    snap_coordinate,
    snapped_polygon,
    translated_rings,
)
from etlayout.gui.drafts import ask_pending, resolve_pending
from etlayout.gui.jobs import ComputeJob
from etlayout.gui.viewer import geometry_path
from etlayout.io.project import configuration_hash


class VertexHandle(QGraphicsRectItem):
    vertex_handle = True

    def __init__(self, editor, ring, index, coordinate):
        super().__init__(-4., -4., 8., 8.)
        self.editor, self.ring, self.index = editor, ring, index
        self.setFlags(QGraphicsItem.GraphicsItemFlag.ItemIsMovable | QGraphicsItem.GraphicsItemFlag.ItemIgnoresTransformations)
        self.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIsFocusable)
        self.setBrush(QColor("#FFFFFF"))
        pen = QPen(QColor("#006AFF"), 2.)
        pen.setCosmetic(True)
        self.setPen(pen)
        self.setZValue(20001)
        self.setPos(coordinate[0] * 1e6, -coordinate[1] * 1e6)
        self.setCursor(Qt.CursorShape.SizeAllCursor)
        self.setToolTip(f"{'Exterior' if ring == 0 else 'Hole ' + str(ring)} vertex {index + 1}; drag to move, Escape to discard")

    def mouseMoveEvent(self, event):
        super().mouseMoveEvent(event)
        self.editor.move_vertex(self.ring, self.index, self.pos())

    def mousePressEvent(self, event):
        if not self.editor.select_vertex(self.ring, self.index):
            event.ignore()
            return
        super().mousePressEvent(event)


class PolygonMoveHandle(QGraphicsRectItem):
    """A distinct drag handle moves all rings without claiming shape selection."""
    vertex_handle = True  # The shared viewer passes mouse events to edit handles.

    def __init__(self, editor, point):
        super().__init__(-5., -5., 10., 10.)
        self.editor = editor
        self.setFlags(QGraphicsItem.GraphicsItemFlag.ItemIsMovable | QGraphicsItem.GraphicsItemFlag.ItemIgnoresTransformations)
        self.setBrush(QColor("#006AFF"))
        self.setPen(QPen(QColor("#FFFFFF"), 1.))
        self.setZValue(20002)
        self.setPos(point.x * 1e6, -point.y * 1e6)
        self.setCursor(Qt.CursorShape.SizeAllCursor)
        self.setToolTip("Move the entire polygon, including its holes; physical definitions stay fixed")

    def mousePressEvent(self, event):
        self.origin = self.pos()
        self.original = deepcopy(self.editor.rings)
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        super().mouseMoveEvent(event)
        delta = self.pos() - self.origin
        self.editor.translate(delta.x() * 1e-6, -delta.y() * 1e-6, original=self.original, anchor=self.origin)


class LayoutEditor(QObject):
    def __init__(self, workspace, canvas_layout):
        super().__init__(workspace.window)
        self.workspace, self.window = workspace, workspace.window
        self.active = False
        self.job = None
        self.target = None
        self.targets = {}
        self.rings = []
        self.handles = []
        self.outline = None
        self.effective = None
        self.valid = False
        self.dbu = 1e-9
        self.cache_dir = None
        self.inspector_details = None
        self.tabs = None
        self.selected_vertex = (0, 0)
        self.move_handle = None
        self.impact_outline = None
        self.impact_preview = None
        self.impacts = []
        self.impact_error = ""
        self.coordinate_input_error = ""
        self._typing_coordinates = False
        try:
            self.grid_units = max(1, min(1000000000, int(QSettings().value("layout_editor/snap_grid_dbu", 1))))
        except (ValueError, TypeError):
            self.grid_units = 1
        self.bar = QFrame()
        self.bar.setProperty("selectionBanner", True)
        row = QHBoxLayout(self.bar)
        self.message = QLabel()
        self.message.setWordWrap(True)
        self.message.setMinimumWidth(0)
        row.addWidget(self.message, 1)
        self.apply_button = QPushButton("Apply layout edit")
        self.apply_button.setProperty("primary", True)
        self.apply_button.clicked.connect(self.apply)
        row.addWidget(self.apply_button)
        self.discard_button = QPushButton("Discard")
        self.discard_button.clicked.connect(self.discard)
        row.addWidget(self.discard_button)
        self.done_button = QPushButton("Done")
        self.done_button.clicked.connect(self.finish)
        row.addWidget(self.done_button)
        canvas_layout.addWidget(self.bar)
        self.bar.hide()
        self.window.view.selection_changed.connect(self.select_polygon)
        self.window.view.scene_replacing.connect(self.clear_handles)
        self.window.view.installEventFilter(self)

    @property
    def dirty(self):
        if self.target is None:
            return False
        original = self.coordinates(self.target.geometry)
        return self.rings != original or bool(self.coordinate_input_error)

    @staticmethod
    def coordinates(polygon):
        return [list(polygon.exterior.coords)[:-1], *[list(r.coords)[:-1] for r in polygon.interiors]]

    def _run(self, operation, argument=None):
        """Responsive modal preparation/export with the existing cancellable worker."""
        identity = configuration_hash(self.window.project)
        dialog = QProgressDialog("Preparing editable layout", "Cancel", 0, 100, self.window)
        dialog.setWindowTitle("Layout editor")
        dialog.setWindowModality(Qt.WindowModality.WindowModal)
        dialog.setMinimumDuration(0)
        dialog.setAutoClose(False)
        loop = QEventLoop()
        outcome = {}
        job = ComputeJob(self.window.project, operation, argument, parent=self, execution=self.window.execution_settings)
        self.job = job

        def finished(value):
            outcome["value"] = value
            loop.quit()

        def failed(message, detail):
            outcome["error"] = (message, detail)
            loop.quit()

        def progress(fraction, message):
            dialog.setValue(round(fraction * 100))
            dialog.setLabelText(message)

        job.finished.connect(finished)
        job.failed.connect(failed)
        job.cancelled.connect(loop.quit)
        job.progress.connect(progress)
        dialog.canceled.connect(job.cancel)
        try:
            job.start()
            dialog.show()
            loop.exec()
        except Exception as error:
            outcome["error"] = (str(error), "")
            job.cleanup()
        finally:
            self.job = None
            dialog.blockSignals(True)
            dialog.close()
            dialog.deleteLater()
        if "error" in outcome:
            message, detail = outcome["error"]
            self.message.setText("Layout edit failed: " + message)
            self.workspace.job_failed("Layout edit failed: " + message, stage="layout", detail=detail)
        if "value" in outcome and configuration_hash(self.window.project) != identity:
            self.workspace.job_failed("The working setup changed while the layout editor was preparing its output. Retry on the current setup.", stage="layout")
            return None
        return outcome.get("value")

    def enter(self):
        w = self.window
        if self.active:
            return self.finish()
        if not self.workspace.resolve_draft():
            return False
        if w.model_locked() or not w.project.layout.path:
            self.workspace.next_reason.setText("Open an imported GDSII/OASIS layout in the working setup before vertex editing.")
            return False
        if not self.workspace.navigate("layout"):
            return False
        w.cancel_geometry()
        output = self._run("layout:load")
        if output is None:
            if w.geometry is None:
                w.schedule_geometry(True)
            return False
        self.active = True
        self.toggle.setChecked(True)
        self.bar.show()
        self.workspace.active_layer.setEnabled(False)
        w.tool_combo.setEnabled(False)
        w.set_tool("select")
        self.show_targets(output)
        self.workspace.build_page()
        self.workspace.refresh()
        return True

    def toggle_mode(self):
        self.enter()
        self.toggle.setChecked(self.active)

    def build_page(self):
        workspace = self.workspace
        workspace.heading.setText("Layout editor")
        workspace.description.setText("Edit a project-owned copy. The imported file stays unchanged.")
        hint = QLabel("Pick a polygon. Drag white vertex handles or the blue whole-polygon handle. "
                      "Arrow keys nudge a focused vertex by one snap step; Shift moves ten. Escape discards. "
                      "Review affected supports before Apply; physical definitions stay fixed.")
        hint.setWordWrap(True)
        workspace.page_layout.addWidget(hint)
        self.mask_filter = QComboBox()
        self.mask_filter.setAccessibleName("Layout editor source mask selection filter")
        self.mask_filter.addItem("Pick shapes on all masks", "")
        for mask in sorted({target.mask for target in self.targets.values()}):
            self.mask_filter.addItem("Pick source mask " + mask, mask)
        self.mask_filter.currentIndexChanged.connect(self.filter_mask)
        workspace.page_layout.addWidget(self.mask_filter)
        self.inspector_details = QLabel()
        self.inspector_details.setWordWrap(True)
        self.inspector_details.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        workspace.page_layout.addWidget(self.inspector_details)
        grid = QFormLayout()
        self.snap_grid = QSpinBox()
        self.snap_grid.setRange(1, 1000000000)
        self.snap_grid.setValue(self.grid_units)
        self.snap_grid.setAccessibleName("Layout editor snap grid in database units")
        self.snap_grid.setToolTip("Integer multiples of the source database grid. This display preference does not alter the physical setup.")
        self.snap_grid.valueChanged.connect(self.change_grid)
        grid.addRow("Snap grid (DBU)", self.snap_grid)
        self.grid_label = QLabel()
        grid.addRow("Snap distance", self.grid_label)
        workspace.page_layout.addLayout(grid)
        self.tabs = QTabWidget()
        self.tabs.setAccessibleName("Layout edit operation groups")
        vertices = QWidget()
        form = QFormLayout(vertices)
        self.ring_choice, self.vertex_choice = QComboBox(), QComboBox()
        self.ring_choice.setAccessibleName("Polygon exterior or hole ring")
        self.vertex_choice.setAccessibleName("Polygon vertex")
        self.ring_choice.currentIndexChanged.connect(self.change_ring)
        self.vertex_choice.currentIndexChanged.connect(self.change_vertex)
        form.addRow("Ring", self.ring_choice)
        form.addRow("Vertex", self.vertex_choice)
        self.x_field, self.y_field = QLineEdit(), QLineEdit()
        for field, label in ((self.x_field, "Vertex X (µm)"), (self.y_field, "Vertex Y (µm)")):
            field.setAccessibleName(label)
            field.returnPressed.connect(self.set_numeric_vertex)
            field.textEdited.connect(self.numeric_input_changed)
            form.addRow(label, field)
        self.coordinate_button = QPushButton("Update vertex")
        self.coordinate_button.clicked.connect(self.set_numeric_vertex)
        form.addRow(self.coordinate_button)
        actions = QHBoxLayout()
        self.insert_button, self.delete_button = QPushButton("Insert midpoint"), QPushButton("Delete vertex")
        self.insert_button.clicked.connect(self.insert_selected)
        self.delete_button.clicked.connect(self.delete_selected)
        actions.addWidget(self.insert_button)
        actions.addWidget(self.delete_button)
        form.addRow(actions)
        self.tabs.addTab(vertices, "Vertices")
        movement = QWidget()
        move_form = QFormLayout(movement)
        self.dx_field, self.dy_field = QLineEdit("0"), QLineEdit("0")
        for field, label in ((self.dx_field, "Move ΔX (µm)"), (self.dy_field, "Move ΔY (µm)")):
            field.setAccessibleName(label)
            field.returnPressed.connect(self.move_numeric)
            move_form.addRow(label, field)
        self.move_button = QPushButton("Move whole polygon")
        self.move_button.clicked.connect(self.move_numeric)
        move_form.addRow(self.move_button)
        notice = QLabel("Offsets move exterior and holes together. Contacts, domains and measurement coordinates do not follow the polygon.")
        notice.setWordWrap(True)
        move_form.addRow(notice)
        self.tabs.addTab(movement, "Move")
        effects = QWidget()
        effect_layout = QVBoxLayout(effects)
        self.impact_summary = QLabel()
        self.impact_summary.setWordWrap(True)
        effect_layout.addWidget(self.impact_summary)
        self.impact_list = QListWidget()
        self.impact_list.setAccessibleName("Affected physical definitions")
        self.impact_list.setMinimumHeight(130)
        self.impact_list.currentRowChanged.connect(self.locate_impact)
        effect_layout.addWidget(self.impact_list)
        effects_notice = QLabel("Geometry preview only. Full connectivity, stack and fabrication checks run with F6 after Apply. Select an item to highlight its bounds.")
        effects_notice.setWordWrap(True)
        effect_layout.addWidget(effects_notice)
        self.tabs.addTab(effects, "Affected")
        workspace.page_layout.addWidget(self.tabs)
        workspace.page_layout.addStretch()
        workspace.set_primary("Finish layout editing", self.finish)
        self.describe_selection()
        self.sync_controls()
        self.refresh_impacts()
        self.settle_layout()

    def settle_layout(self):
        # Settle new banner/inspector size hints before callers capture a camera
        # or ask Apply/Discard/Stay. A modal must not flush an older deferred
        # resize and change the canvas after Stay. No user events are processed.
        for parent in (self.workspace.page, self.bar):
            while parent is not None and parent is not self.window:
                if parent.layout() is not None:
                    parent.layout().activate()
                parent = parent.parentWidget()

    def filter_mask(self):
        mask = self.mask_filter.currentData()
        keys = None if not mask else [key for key, target in self.targets.items() if target.mask == mask]
        self.window.view.set_selection_filter(keys, purpose="layout_vertices")

    def describe_selection(self):
        if self.inspector_details is None:
            return
        if self.target is None:
            self.inspector_details.setText(f"{len(self.targets):,} original shapes available.\nDatabase grid: {self.dbu * 1e6:g} µm.\n\nNo polygon selected.")
        else:
            x0, y0, x1, y1 = self.target.geometry.bounds
            self.inspector_details.setText(f"{self.target.label}\n"
                f"{sum(len(ring) for ring in self.rings)} vertices · {len(self.rings) - 1} hole(s)\n"
                f"Applied width {(x1 - x0) * 1e6:g} µm · height {(y1 - y0) * 1e6:g} µm\n"
                + ("Path outline becomes a polygon on Apply.\n" if self.target.kind == "path outline" else "")
                + "Blue is the pending outline; red geometry cannot be applied.")

    def show_targets(self, output):
        self.clear_handles()
        self.target = None
        self.impact_preview = None
        self.impact_error = ""
        targets, self.dbu = output
        self.targets = {f"vertex:{index}": target for index, target in enumerate(targets)}
        view = self.window.view
        view.clear_selection_filter()
        view.set_layer_focus("")
        layers = []
        for key, target in self.targets.items():
            physical = next((layer for layer in self.window.project.layers if target.mask in layer.geometry.sources), None)
            layers.append(SimpleNamespace(id=key, name=target.label, role="original shape", z=None,
                material_id="", visible=True, color=physical.color if physical else "#397F70", opacity=.45))
        view.set_geometries({key: target.geometry for key, target in self.targets.items()}, layers, preserve_view=True)
        for key, target in self.targets.items():
            for item in view.layer_items[key]:
                item.setToolTip(target.label)
        self.message.setText(f"Edit layout · original file stays unchanged · click a polygon, then drag its vertices. Grid: {self.dbu * 1e6:g} µm.")
        self.apply_button.setEnabled(False)
        self.discard_button.setEnabled(False)
        self.describe_selection()
        self.sync_controls()
        self.refresh_impacts()

    def select_polygon(self):
        if not self.active or self.job:
            return
        selected = self.window.view.selected_geometries()
        if not selected:
            return
        key = selected[-1][0]
        target = self.targets.get(key)
        if target is None or target is self.target:
            return
        if not self.resolve_vertices():
            # Stay restores the previous selection and leaves its handles/draft.
            view = self.window.view
            view.scene().blockSignals(True)
            view.scene().clearSelection()
            if self.target:
                previous = next(k for k, item in self.targets.items() if item is self.target)
                view.layer_items[previous][0].setSelected(True)
            view.scene().blockSignals(False)
            return
        if self.window.project.layout.sha256 and target.source_hash != self.window.project.layout.sha256:
            return  # Apply changed the source; background preparation reloads addresses.
        self.target = target
        try:
            self.impact_preview = EditImpactPreview(self.window.project, list(self.targets.values()), target)
            self.impact_error = ""
        except (ValueError, RuntimeError, KeyError) as error:
            self.impact_preview = None
            self.impact_error = "Revalidation required (F6): support preview unavailable: " + str(error)
        self.selected_vertex = (0, 0)
        self.discard()

    def clear_handles(self):
        scene = self.window.view.scene()
        for item in [*self.handles, self.outline, self.move_handle, self.impact_outline]:
            if item is not None and item.scene() is scene:
                scene.removeItem(item)
        self.handles, self.outline = [], None
        self.move_handle = self.impact_outline = None

    def discard(self):
        self.coordinate_input_error = ""
        self.clear_handles()
        self.rings = self.coordinates(self.target.geometry) if self.target else []
        self.effective = self.target.geometry if self.target else None
        self.valid = bool(self.target)
        if self.target:
            self.outline = QGraphicsPathItem(geometry_path(self.effective))
            self.outline.setAcceptedMouseButtons(Qt.MouseButton.NoButton)
            self.outline.setZValue(20000)
            self.window.view.scene().addItem(self.outline)
            self.rebuild_handles()
        self.update_preview()

    def move_vertex(self, ring, index, point):
        self.coordinate_input_error = ""
        x = snap_coordinate(point.x() * 1e-6, self.dbu, self.grid_units)
        y = snap_coordinate(-point.y() * 1e-6, self.dbu, self.grid_units)
        self.rings[ring][index] = (x, y)
        handle = next(h for h in self.handles if h.ring == ring and h.index == index)
        handle.setPos(x * 1e6, -y * 1e6)
        self.selected_vertex = ring, index
        self.update_preview()

    def update_preview(self):
        error = self.coordinate_input_error
        try:
            self.effective = snapped_polygon(self.rings, self.dbu) if self.target else None
            self.valid = bool(self.target) and not error
        except ValueError as exc:
            error = str(exc)
            self.valid = False
        except RuntimeError as exc:
            error = str(exc)
            self.valid = False
        if self.target:
            preview = Polygon(self.rings[0], self.rings[1:])
            self.outline.setPath(geometry_path(preview))
            pen = QPen(QColor("#006AFF" if self.valid else "#D22F2F"), 2.)
            pen.setCosmetic(True)
            self.outline.setPen(pen)
            self.outline.setBrush(QColor(0, 106, 255, 35))
            if self.move_handle is not None:
                self.move_handle.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIsMovable, self.valid)
            self.message.setText(self.target.label + "\n" + (error or ("Pending vertices · Apply commits; Escape discards." if self.dirty else "Drag a vertex. Apply writes a project-owned copy; Undo restores the previous layout.")))
        self.apply_button.setEnabled(self.dirty and self.valid)
        self.discard_button.setEnabled(self.dirty)
        self.describe_selection()
        self.sync_controls()
        self.refresh_impacts()
        self.settle_layout()

    def rebuild_handles(self):
        scene = self.window.view.scene()
        for item in [*self.handles, self.move_handle]:
            if item is not None and item.scene() is scene:
                scene.removeItem(item)
        self.handles = []
        for ring, coordinates in enumerate(self.rings):
            for index, coordinate in enumerate(coordinates):
                handle = VertexHandle(self, ring, index, coordinate)
                scene.addItem(handle)
                self.handles.append(handle)
        self.move_handle = PolygonMoveHandle(self, Polygon(self.rings[0], self.rings[1:]).representative_point())
        scene.addItem(self.move_handle)

    def sync_controls(self):
        if self.tabs is None:
            return
        self.grid_label.setText(f"{self.grid_units * self.dbu * 1e6:.9g} µm · source DBU {self.dbu * 1e6:.9g} µm")
        self.tabs.setEnabled(bool(self.target))
        if not self.target:
            return
        ring, vertex = self.selected_vertex
        ring = min(ring, len(self.rings) - 1)
        vertex = min(vertex, len(self.rings[ring]) - 1)
        self.selected_vertex = ring, vertex
        for control in (self.ring_choice, self.vertex_choice):
            control.blockSignals(True)
            control.clear()
        for index in range(len(self.rings)):
            self.ring_choice.addItem("Exterior" if index == 0 else f"Hole {index}", index)
        self.ring_choice.setCurrentIndex(ring)
        for index in range(len(self.rings[ring])):
            self.vertex_choice.addItem(str(index + 1), index)
        self.vertex_choice.setCurrentIndex(vertex)
        for control in (self.ring_choice, self.vertex_choice):
            control.blockSignals(False)
        if not self._typing_coordinates and not self.coordinate_input_error:
            x, y = self.rings[ring][vertex]
            self.x_field.setText(f"{x * 1e6:.12g}")
            self.y_field.setText(f"{y * 1e6:.12g}")
        self.delete_button.setEnabled(len(self.rings[ring]) > 3)
        for handle in self.handles:
            handle.setBrush(QColor("#D9EAFF" if (handle.ring, handle.index) == self.selected_vertex else "#FFFFFF"))

    def select_vertex(self, ring, index):
        if not self.target:
            return False
        if self.coordinate_input_error:
            self._typing_coordinates = True
            try:
                self.sync_controls()
            finally:
                self._typing_coordinates = False
            self.message.setText("Correct the invalid vertex coordinates or Discard before selecting another vertex.")
            return False
        self.selected_vertex = ring, index
        self.sync_controls()
        return True

    def change_ring(self):
        if self.target:
            self.select_vertex(self.ring_choice.currentIndex(), 0)

    def change_vertex(self):
        if self.target:
            self.select_vertex(self.ring_choice.currentIndex(), self.vertex_choice.currentIndex())

    def change_grid(self, units):
        self.grid_units = units
        QSettings().setValue("layout_editor/snap_grid_dbu", units)
        self.sync_controls()

    def numeric_input_changed(self):
        self._typing_coordinates = True
        try:
            self.set_numeric_vertex()
        finally:
            self._typing_coordinates = False

    def set_numeric_vertex(self):
        if not self.target:
            return False
        try:
            x, y = (snap_coordinate(float(field.text()) * 1e-6, self.dbu, self.grid_units)
                    for field in (self.x_field, self.y_field))
        except (ValueError, RuntimeError) as error:
            self.coordinate_input_error = "Enter finite X and Y coordinates in µm. " + str(error)
            self.update_preview()
            return False
        self.coordinate_input_error = ""
        ring, index = self.selected_vertex
        self.move_vertex(ring, index, QPointF(x * 1e6, -y * 1e6))
        return self.valid

    def replace_rings(self, rings, selection):
        self.coordinate_input_error = ""
        self.rings = rings
        self.selected_vertex = selection
        self.rebuild_handles()
        next(handle for handle in self.handles if (handle.ring, handle.index) == selection).setFocus()
        self.update_preview()

    def insert_selected(self):
        if not self.target or self.coordinate_input_error:
            return False
        ring, index = self.selected_vertex
        try:
            rings = insert_midpoint(self.rings, ring, index, self.dbu, self.grid_units)
        except (ValueError, RuntimeError) as error:
            self.message.setText("Cannot insert vertex: " + str(error))
            return False
        self.replace_rings(rings, (ring, index + 1))
        return True

    def delete_selected(self):
        if not self.target or self.coordinate_input_error:
            return False
        ring, index = self.selected_vertex
        try:
            rings = delete_vertex(self.rings, ring, index, self.dbu)
        except (ValueError, RuntimeError) as error:
            self.message.setText("Cannot delete vertex: " + str(error))
            return False
        self.replace_rings(rings, (ring, min(index, len(rings[ring]) - 1)))
        return True

    def translate(self, dx, dy, *, original=None, anchor=None):
        if not self.target or self.coordinate_input_error:
            return False
        try:
            rings = translated_rings(self.rings if original is None else original, dx, dy, self.dbu, self.grid_units)
        except (ValueError, RuntimeError) as error:
            self.message.setText("Cannot move polygon: " + str(error))
            return False
        self.coordinate_input_error = ""
        self.rings = rings
        for handle in self.handles:
            x, y = rings[handle.ring][handle.index]
            handle.setPos(x * 1e6, -y * 1e6)
        if anchor is not None:
            self.move_handle.setPos(anchor + QPointF(snap_coordinate(dx, self.dbu, self.grid_units) * 1e6,
                                                    -snap_coordinate(dy, self.dbu, self.grid_units) * 1e6))
        else:
            point = Polygon(rings[0], rings[1:]).representative_point()
            self.move_handle.setPos(point.x * 1e6, -point.y * 1e6)
        self.update_preview()
        return True

    def move_numeric(self):
        try:
            dx, dy = (float(field.text()) * 1e-6 for field in (self.dx_field, self.dy_field))
        except ValueError:
            self.message.setText("Enter finite polygon offsets in µm.")
            return False
        if not self.translate(dx, dy):
            return False
        self.dx_field.setText("0")
        self.dy_field.setText("0")
        return True

    def refresh_impacts(self):
        self.impacts = []
        error = self.impact_error
        if self.target and self.dirty and self.valid and self.impact_preview:
            try:
                self.impacts = self.impact_preview.compare(self.effective)
            except (ValueError, RuntimeError, KeyError) as exc:
                error = "Revalidation required (F6): support preview unavailable: " + str(exc)
        if self.tabs is None:
            return
        if self.target and self.dirty and self.valid:
            self.message.setText(self.target.label + "\nPending layout edit · " +
                                 (error if error else f"{len(self.impacts)} affected/revalidation item(s); review the Affected tab before Apply."))
        self.tabs.setTabText(2, f"Affected ({len(self.impacts)})")
        self.impact_summary.setText(error or ("Correct the invalid draft before checking supports." if not self.valid and self.target else
                                   f"{len(self.impacts)} affected/revalidation item(s). Coordinates stay fixed; Apply invalidates mesh and fields." if self.dirty else
                                   "No pending geometry change."))
        self.impact_list.blockSignals(True)
        self.impact_list.clear()
        for impact in self.impacts:
            item = QListWidgetItem(f"{impact.category} · {impact.object_id}")
            item.setToolTip(impact.message)
            self.impact_list.addItem(item)
        self.impact_list.blockSignals(False)
        self.locate_impact(-1)

    def locate_impact(self, index):
        scene = self.window.view.scene()
        if self.impact_outline is not None and self.impact_outline.scene() is scene:
            scene.removeItem(self.impact_outline)
        self.impact_outline = None
        if not 0 <= index < len(self.impacts):
            return
        impact = self.impacts[index]
        self.impact_summary.setText(impact.message)
        if impact.bounds:
            x0, y0, x1, y1 = impact.bounds
            padding = max(x1 - x0, y1 - y0) * .02 or self.dbu
            self.impact_outline = QGraphicsPathItem(geometry_path(box(x0 - padding, y0 - padding, x1 + padding, y1 + padding)))
            self.impact_outline.setAcceptedMouseButtons(Qt.MouseButton.NoButton)
            pen = QPen(QColor("#D48700"), 2., Qt.PenStyle.DashLine)
            pen.setCosmetic(True)
            self.impact_outline.setPen(pen)
            self.impact_outline.setZValue(19999)
            scene.addItem(self.impact_outline)

    def apply(self):
        if not self.dirty:
            return True
        if not self.valid or self.window.model_locked():
            return False
        cache = self.cache_dir or Path(QStandardPaths.writableLocation(QStandardPaths.StandardLocation.CacheLocation)) / "layout-edits"
        destination = Path(cache) / (uuid4().hex + ".oas")
        candidate = self._run("layout:edit", (self.target, self.effective, str(destination)))
        if candidate is None:
            return False
        # Retire the committed draft before document refresh/selection signals.
        self.clear_handles()
        self.target = None
        self.rings = []
        self.impact_preview = None
        self.workspace.document.transact("Layout polygon edited in a project-owned copy", lambda project: candidate)
        # Geometry refresh owns the next raw-shape load, with stale outputs
        # rejected by the existing generation guard.
        self.message.setText("Layout applied · preparing the updated layout…")
        self.apply_button.setEnabled(False)
        return True

    def resolve_vertices(self):
        if self.job:
            return False
        return resolve_pending(changed=self.dirty,
            choose=lambda: ask_pending(self.window, "Unapplied layout edit", "Apply this polygon draft, discard it, or stay in the layout editor?"),
            apply=self.apply, discard=self.discard)

    def finish(self):
        if self.job:
            return False
        if not self.active:
            return True
        if not self.resolve_vertices():
            self.toggle.setChecked(True)
            return False
        self.leave()
        return True

    def leave(self, restore=True):
        self.active = False
        self.clear_handles()
        self.target, self.targets, self.rings = None, {}, []
        self.bar.hide()
        self.toggle.setChecked(False)
        self.workspace.active_layer.setEnabled(True)
        self.window.tool_combo.setEnabled(True)
        self.window.view.clear_selection_filter()
        self.inspector_details = None
        self.tabs = None
        self.impact_preview = None
        self.impacts = []
        if restore:
            self.window.set_tool("select")
            if self.window.geometry is None and self.window.prepare_job is None:
                self.window.schedule_geometry(True)
            elif self.window.prepare_job is None:
                self.window.refresh_view(True)
                self.window.update_overlay()
            self.workspace.build_page()
            self.workspace.refresh()

    def geometry_ready(self):
        if self.active and not self.job:
            output = self._run("layout:load")
            if output is not None:
                self.show_targets(output)
            else:
                self.leave()

    def eventFilter(self, watched, event):
        if self.active and watched is self.window.view and event.type() == QEvent.Type.KeyPress and event.key() == Qt.Key.Key_Escape:
            self.discard()
            event.accept()
            return True
        if self.active and watched is self.window.view and event.type() == QEvent.Type.KeyPress:
            handle = self.window.view.scene().focusItem()
            directions = {Qt.Key.Key_Left: (-1, 0), Qt.Key.Key_Right: (1, 0),
                          Qt.Key.Key_Up: (0, -1), Qt.Key.Key_Down: (0, 1)}
            if isinstance(handle, VertexHandle) and event.key() in directions:
                x, y = directions[event.key()]
                step = self.dbu * self.grid_units * 1e6 * (10 if event.modifiers() & Qt.KeyboardModifier.ShiftModifier else 1)
                self.move_vertex(handle.ring, handle.index, handle.pos() + QPointF(x * step, y * step))
                event.accept()
                return True
            if isinstance(handle, VertexHandle) and event.key() in {Qt.Key.Key_Insert, Qt.Key.Key_Delete}:
                if self.select_vertex(handle.ring, handle.index):
                    self.insert_selected() if event.key() == Qt.Key.Key_Insert else self.delete_selected()
                event.accept()
                return True
        return super().eventFilter(watched, event)
