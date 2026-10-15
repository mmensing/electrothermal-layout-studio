# SPDX-License-Identifier: MPL-2.0
"""Vector layout viewport, graphical selections, mesh and quantitative overlays."""
from __future__ import annotations

import math
from collections import OrderedDict
from types import SimpleNamespace

import numpy as np
from PySide6.QtCore import QPointF, QRectF, Qt, Signal
from PySide6.QtGui import (
    QColor,
    QFontMetricsF,
    QLinearGradient,
    QPainter,
    QPainterPath,
    QPalette,
    QPen,
    QPicture,
    QPolygonF,
)
from PySide6.QtWidgets import (
    QGraphicsItem,
    QGraphicsPathItem,
    QGraphicsRectItem,
    QGraphicsScene,
    QGraphicsView,
    QMenu,
)

from etlayout.geometry.prepare import geometry_region as geometry_region
from etlayout.gui.selection import SelectionCandidate
from etlayout.models import RegionSpec
from etlayout.simulation.analysis import FIELD_UNITS
from etlayout.visualization.formatting import distinct_labels
from etlayout.visualization.palettes import COLORMAPS, rgba
from etlayout.visualization.sampling import DISPLAY_FIELD_LABELS as FIELD_LABELS
from etlayout.visualization.support import display_limits, field_support, region_elements
from etlayout.visualization.units import display_factor, to_display

VECTOR_FIELDS = frozenset({"electric_field", "sheet_current", "current_density", "thermal_gradient"})


def geometry_path(geometry) -> QPainterPath:
    path = QPainterPath()
    path.setFillRule(Qt.FillRule.OddEvenFill)
    if geometry is None or geometry.is_empty:
        return path
    geometries = list(geometry.geoms) if hasattr(geometry, "geoms") else [geometry]
    for polygon in geometries:
        if polygon.geom_type == "Polygon":
            for ring in [polygon.exterior, *polygon.interiors]:
                points = list(ring.coords)
                path.moveTo(points[0][0] * 1e6, -points[0][1] * 1e6)
                for x, y in points[1:]:
                    path.lineTo(x * 1e6, -y * 1e6)
                path.closeSubpath()
        elif polygon.geom_type == "LineString":
            points = list(polygon.coords)
            path.moveTo(points[0][0] * 1e6, -points[0][1] * 1e6)
            for x, y in points[1:]:
                path.lineTo(x * 1e6, -y * 1e6)
    return path


def field_color(value: float, colormap: str = "teal") -> QColor:
    return QColor.fromRgbF(*map(float, rgba(value, colormap)))


class PictureItem(QGraphicsItem):
    def __init__(self, picture: QPicture, bounds: QRectF):
        super().__init__()
        self.picture, self.bounds = picture, bounds

    def boundingRect(self):
        return self.bounds

    def paint(self, painter, option, widget=None):
        painter.drawPicture(0, 0, self.picture)


class SelectionShapeItem(QGraphicsPathItem):
    """Blue selection stays distinct from materials, warnings and result fields."""

    def paint(self, painter, option, widget=None):
        if not self.isSelected():
            return super().paint(painter, option, widget)
        painter.save()
        pen = QPen(QColor("#006AFF"), 2.5)
        pen.setCosmetic(True)
        painter.setPen(pen)
        painter.setBrush(QColor(0, 106, 255, 55))
        painter.drawPath(self.path())
        painter.restore()


class RegionResizeHandle(QGraphicsRectItem):
    """A display draft handle. Moving it only emits a pending region."""

    def __init__(self, view, index):
        super().__init__(-4., -4., 8., 8.)
        self.view, self.index = view, index
        self.setFlags(QGraphicsItem.GraphicsItemFlag.ItemIgnoresTransformations |
                      QGraphicsItem.GraphicsItemFlag.ItemIsMovable)
        self.setBrush(QColor("#FFFFFF"))
        pen = QPen(QColor("#006AFF"), 2.)
        pen.setCosmetic(True)
        self.setPen(pen)
        self.setZValue(2010)
        self.setCursor(Qt.CursorShape.SizeFDiagCursor if index in (0, 2)
                       else Qt.CursorShape.SizeBDiagCursor)
        self.setToolTip("Resize pending region; Apply commits it, Escape cancels")

    def mouseMoveEvent(self, event):
        super().mouseMoveEvent(event)
        self.view._resize_pending_region(self.index, self.pos())

    def mouseReleaseEvent(self, event):
        super().mouseReleaseEvent(event)
        self.view._emit_pending_region()


class ObjectCallout(QGraphicsItem):
    """A screen-sized label anchored to a supplied, actual physical coordinate."""

    def __init__(self, view, text):
        super().__init__()
        self.view = view
        self.text = text
        metrics = QFontMetricsF(view.font())
        self.label = metrics.elidedText(text, Qt.TextElideMode.ElideRight, 270.)
        self.box = QRectF(12., -34., metrics.horizontalAdvance(self.label) + 16., 25.)
        self.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIgnoresTransformations)
        self.setAcceptedMouseButtons(Qt.MouseButton.NoButton)
        self.setToolTip(text)
        self.setZValue(2015)
        self.label_visible = True

    def place_box(self, x, y, visible=True):
        self.prepareGeometryChange()
        self.box.moveTopLeft(QPointF(x, y))
        self.label_visible = visible
        self.update()

    def boundingRect(self):
        return self.box.united(QRectF(-5., -5., 10., 10.)).adjusted(-1., -1., 1., 1.)

    def paint(self, painter, option, widget=None):
        if not self.label_visible:
            return
        dark = self.view.palette().color(QPalette.ColorRole.Base).lightness() < 128
        painter.setPen(QPen(QColor("#579AFF" if dark else "#006AFF"), 1.4))
        painter.setBrush(QColor("#213651" if dark else "#FFFFFF"))
        painter.drawLine(QPointF(0., 0.), self.box.bottomLeft())
        painter.drawEllipse(QPointF(0., 0.), 3.5, 3.5)
        painter.drawRoundedRect(self.box, 3., 3.)
        painter.setPen(QColor("#E6EFFC" if dark else "#223955"))
        painter.setFont(self.view.font())
        painter.drawText(self.box.adjusted(8., 0., -8., 0.), Qt.AlignmentFlag.AlignVCenter, self.label)


class LayoutView(QGraphicsView):
    scene_replacing = Signal()
    position_changed = Signal(float, float)
    region_drawn = Signal(str, object)
    line_drawn = Signal(str, object)
    point_probed = Signal(float, float)
    selection_changed = Signal()
    context_action = Signal(str)
    legend_changed = Signal(str)
    legend_details = Signal(object)
    mode_changed = Signal(str)
    viewport_changed = Signal()
    selection_cancelled = Signal()
    pending_region_changed = Signal(object)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setScene(QGraphicsScene(self))
        self.setRenderHint(QPainter.RenderHint.Antialiasing)
        self.setBackgroundBrush(QColor("#F9FBFE"))
        self.setTransformationAnchor(QGraphicsView.ViewportAnchor.AnchorUnderMouse)
        self.setResizeAnchor(QGraphicsView.ViewportAnchor.AnchorViewCenter)
        self.setDragMode(QGraphicsView.DragMode.RubberBandDrag)
        self.setMouseTracking(True)
        self.mode = "select"
        self.points = []
        self.preview = None
        self.start = None
        self.layer_items = {}
        self.terminal_items = {}
        self.callout_items = {}
        self._callout_layers = {}
        self._layer_visibility = {}
        self._focused_layer = ""
        self._terminal_layers = {}
        self._terminal_geometries = {}
        self._annotation_scale = None
        self._layer_info = {}
        self._layer_pickability = {}
        self._selection_layers = None
        self.selection_purpose = ""
        self.selection_provenance = {}
        self.hover_item = None
        self.pending_region_item = None
        self.requested_region_item = None
        self.pending_region_handles = []
        self._pending_bounds = None
        self._panning = None
        self.candidate_menu = None
        self.show_grid = True
        self.mesh_item = None
        self.overlay_item = None
        self.vector_item = None
        self.annotations = []
        self.result = None
        self.legend = ""
        self.field_key = ""
        self.field_layer = ""
        self.field_region = ""
        self.field_colormap = "teal"
        self.field_log = False
        self.field_limits = None
        self.field_manual_limits = False
        self.field_smooth = False
        self._field_cache = OrderedDict()
        self._field_cache_result = None
        self._field_cache_bytes = 0
        self._field_cache_budget = 64 * 1024 * 1024
        self._field_cache_entries = 8
        self.scene().selectionChanged.connect(self.selection_changed)
        self.viewport_changed.connect(self._update_annotations)
        self.setMinimumSize(400, 300)
        self.setAccessibleName("Layout and result field canvas")
        self.setAccessibleDescription(
            "Mouse wheel or plus/minus zooms; middle mouse or arrow keys pan. "
            "Overlapping shapes open an explicit layer and footprint choice. "
            "Control toggles a footprint. Escape cancels a pending selection.")

    def set_mode(self, mode):
        self.mode = mode
        self.points.clear()
        self.start = None
        self.clear_preview()
        self.clear_hover()
        self.setDragMode(QGraphicsView.DragMode.ScrollHandDrag if mode == "pan" else
                         QGraphicsView.DragMode.RubberBandDrag if mode == "select" else
                         QGraphicsView.DragMode.NoDrag)
        self.setCursor(Qt.CursorShape.ArrowCursor if mode == "select" else
                       Qt.CursorShape.OpenHandCursor if mode == "pan" else Qt.CursorShape.CrossCursor)
        self.mode_changed.emit(mode)

    @staticmethod
    def tool_hint(mode):
        if "polygon" in mode:
            return "Click each vertex. Enter or double-click finishes the polygon; Escape cancels."
        return {
            "select": "Click a shape; choose its layer when shapes overlap. Ctrl-click adds/removes a footprint; right-click shows actions.",
            "pan": "Drag to pan. Scroll to zoom; middle mouse also pans in any tool.",
            "probe": "Click inside a result field to read its value. Escape returns to selection.",
            "profile": "Drag from start to end across the field to create a line profile. Escape cancels.",
            "cross_section": "Drag a line through the stack to inspect its cross-section. Escape cancels.",
            "branch_current": "Drag a cut across a conductor to integrate the current through it. Escape cancels.",
            "measure": "Drag between two points to measure their physical distance. Escape cancels.",
            "terminal_edge": "Drag along a conductor boundary to place an edge terminal. Endpoints snap within 8 pixels; Escape cancels.",
            "display_roi": "Drag a rectangle to crop the displayed area. The physical model and saved results are preserved.",
            "display_rect": "Drag a rectangle to crop the displayed area. The physical model and saved results are preserved.",
            "roi_rect": "Draw a pending simulation domain, then use Apply to change the model. Escape cancels.",
            "refine": "Drag a rectangle where the mesh needs finer elements. Escape cancels.",
        }.get(mode, "Drag from one corner to the opposite corner to draw a region. Escape cancels.")

    def wheelEvent(self, event):
        factor = 1.2 if event.angleDelta().y() > 0 else 1 / 1.2
        if 1e-4 < self.transform().m11() * factor < 1e7:
            self.scale(factor, factor)

    def scale(self, sx, sy):
        super().scale(sx, sy)
        self.viewport_changed.emit()

    def setTransform(self, matrix, combine=False):
        super().setTransform(matrix, combine)
        self.viewport_changed.emit()

    def fitInView(self, *args):
        super().fitInView(*args)
        self.viewport_changed.emit()

    def centerOn(self, *args):
        super().centerOn(*args)
        self.viewport_changed.emit()

    def scrollContentsBy(self, dx, dy):
        super().scrollContentsBy(dx, dy)
        self.viewport_changed.emit()

    def fit_all(self):
        bounds = self.scene().itemsBoundingRect()
        if bounds.width() > 0 and bounds.height() > 0:
            self.fitInView(bounds.adjusted(-bounds.width() * .06, -bounds.height() * .06,
                                          bounds.width() * .06, bounds.height() * .06), Qt.AspectRatioMode.KeepAspectRatio)

    def fit_selection(self):
        items = self.scene().selectedItems()
        if not items:
            return self.fit_all()
        bounds = items[0].sceneBoundingRect()
        for item in items[1:]:
            bounds = bounds.united(item.sceneBoundingRect())
        self.fitInView(bounds.adjusted(-1, -1, 1, 1), Qt.AspectRatioMode.KeepAspectRatio)

    def fit_field(self):
        """Fit only the rendered field support, ignoring other layers and labels."""
        if self.overlay_item is None:
            return False
        bounds = self.overlay_item.sceneBoundingRect()
        if bounds.width() <= 0 or bounds.height() <= 0:
            return False
        self.fitInView(bounds.adjusted(-bounds.width() * .06, -bounds.height() * .06,
                                      bounds.width() * .06, bounds.height() * .06), Qt.AspectRatioMode.KeepAspectRatio)
        return True

    def set_geometries(self, geometries, layers=(), terminals=None, preserve_view=False):
        self.scene_replacing.emit()
        transform = self.transform()
        center = self.mapToScene(self.viewport().rect().center())
        self.scene().clear()
        self.preview = self.mesh_item = self.overlay_item = self.vector_item = None
        self.hover_item = self.pending_region_item = None
        self.requested_region_item = None
        self.pending_region_handles = []
        self._pending_bounds = None
        self.annotations = []
        self.layer_items = {}
        self.terminal_items = {}
        self._terminal_geometries = dict(terminals or {})
        self.callout_items = {}
        self._callout_layers = {}
        info = {layer.id: layer for layer in layers}
        self._layer_info = info
        self._layer_visibility = {key: bool(info[key].visible) if key in info else True for key in geometries}
        for i, (key, geometry) in enumerate(geometries.items()):
            layer = info.get(key)
            color = QColor(layer.color if layer else ["#397F70", "#818F9B", "#B79A69", "#8C84A1"][i % 4])
            opacity = layer.opacity if layer else .55
            shapes = list(geometry.geoms) if hasattr(geometry, "geoms") else [geometry]
            items = []
            for component_index, shape in enumerate(shapes):
                item = SelectionShapeItem(geometry_path(shape))
                pen = QPen(color.darker(140), .8)
                pen.setCosmetic(True)
                item.setPen(pen)
                fill = QColor(color)
                fill.setAlphaF(opacity)
                item.setBrush(fill)
                item.setFlags(QGraphicsItem.GraphicsItemFlag.ItemIsSelectable)
                item.setData(0, key)
                item.setData(1, shape)
                item.setData(2, component_index)
                item.setData(3, 2 * shape.area / shape.length if shape.area and shape.length else shape.length)
                item.setZValue(i)
                item.setToolTip(f"{layer.name if layer else key}\nArea {shape.area * 1e12:.5g} µm²")
                self.scene().addItem(item)
                items.append(item)
            self.layer_items[key] = items
        for key, geometry in (terminals or {}).items():
            item = QGraphicsPathItem(geometry_path(geometry))
            pen = QPen(QColor("#A95630"), 2)
            pen.setCosmetic(True)
            item.setPen(pen)
            item.setBrush(QColor(193, 124, 74, 60))
            item.setZValue(1000)
            item.setToolTip("Terminal " + key)
            self.scene().addItem(item)
            self.terminal_items[key] = [item]
            if not geometry.is_empty:
                point = geometry.representative_point()
                label = ObjectCallout(self, key)
                label.setPos(point.x * 1e6, -point.y * 1e6)
                label.setZValue(1001)
                self.scene().addItem(label)
                self.terminal_items[key].append(label)
        self._apply_layer_focus()
        self._apply_pickability()
        self._update_annotations()
        bounds = self.scene().itemsBoundingRect()
        self.scene().setSceneRect(bounds.adjusted(-bounds.width(), -bounds.height(), bounds.width(), bounds.height()))
        if preserve_view:
            self.setTransform(transform)
            self.centerOn(center)
        else:
            self.fit_all()

    def set_layer_visible(self, key, visible):
        self._layer_visibility[key] = bool(visible)
        self._apply_layer_focus()

    def _update_annotations(self):
        """Place actual anchors' screen-sized labels without covering each other.

        Labels that cannot fit at this zoom retain the physical marker/tooltip
        and return when zooming in. Geometry and contact coordinates never move.
        Thin contact/gate outlines scale down before they obscure narrow fingers.
        """
        transform = self.viewportTransform()
        scale = abs(self.transform().m11())
        if scale != self._annotation_scale:
            self._annotation_scale = scale
            for key, items in self.terminal_items.items():
                geometry = self._terminal_geometries[key]
                width = 2 * geometry.area / geometry.length if geometry.area and geometry.length else geometry.length
                pen = items[0].pen()
                pen.setWidthF(max(.35, min(1.5, width * 1e6 * scale * .3)))
                items[0].setPen(pen)
            for items in self.layer_items.values():
                for item in items:
                    pen = item.pen()
                    pen.setWidthF(max(.2, min(.8, item.data(3) * 1e6 * scale * .3)))
                    item.setPen(pen)
        labels = []
        callout_anchors = {tuple((item.pos().x(), item.pos().y())) for item in self.callout_items.values() if item.isVisible()}
        for key, items in self.terminal_items.items():
            if len(items) > 1 and items[1].isVisible():
                label = items[1]
                if any(math.hypot(label.pos().x() - x, label.pos().y() - y) * scale < .25 for x, y in callout_anchors):
                    label.place_box(12., -34., visible=False)
                else:
                    labels.append((key, label))
        # A richer contact/probe callout owns its label when both use the same
        # physical anchor. This avoids displaying duplicate names at one landing.
        labels.extend((key, item) for key, item in self.callout_items.items() if item.isVisible())
        occupied = []
        viewport = QRectF(self.viewport().rect()).adjusted(3, 3, -3, -3)
        for _, item in sorted(labels, key=lambda pair: pair[0]):
            anchor = transform.map(item.pos())
            width, height = item.box.width(), item.box.height()
            placed = False
            for distance in (0., 28., 56., 84.):
                candidates = ((12., -height - 9. - distance), (12., 9. + distance),
                              (-width - 12., -height - 9. - distance), (-width - 12., 9. + distance))
                for x, y in candidates:
                    bounds = QRectF(anchor.x() + x, anchor.y() + y, width, height)
                    if viewport.contains(bounds) and not any(bounds.adjusted(-4, -3, 4, 3).intersects(other) for other in occupied):
                        item.place_box(x, y)
                        occupied.append(bounds)
                        placed = True
                        break
                if placed:
                    break
            if not placed:
                item.place_box(12., -height - 9., visible=False)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._update_annotations()

    def set_layer_focus(self, layer="", terminal_layers=None):
        """Temporarily isolate geometry without modifying saved visibility flags."""
        self._focused_layer = layer or ""
        if terminal_layers is not None:
            self._terminal_layers = dict(terminal_layers)
        self._apply_layer_focus()

    def _apply_layer_focus(self):
        for layer, items in self.layer_items.items():
            visible = self._layer_visibility.get(layer, True) and (not self._focused_layer or layer == self._focused_layer)
            for item in items:
                item.setVisible(visible)
                if not visible:
                    item.setSelected(False)
        for terminal, items in self.terminal_items.items():
            layer = self._terminal_layers.get(terminal)
            visible = self._layer_visibility.get(layer, True) and (not self._focused_layer or layer == self._focused_layer)
            for item in items:
                item.setVisible(visible)
        for identifier, item in self.callout_items.items():
            layer = self._callout_layers[identifier]
            item.setVisible(self._layer_visibility.get(layer, True) and
                            (not self._focused_layer or layer == self._focused_layer))
        self._apply_pickability()
        self._annotation_scale = None
        self._update_annotations()

    def show_callouts(self, callouts):
        """Replace display annotations for real terminals, ports or probes.

        Each mapping contains ``id``, ``label``, ``layer_id`` and an SI ``point``
        pair supplied by the document or selected result. No values, positions,
        role assignments or connectivity are inferred by the renderer.
        """
        for item in self.callout_items.values():
            self.scene().removeItem(item)
        self.callout_items = {}
        self._callout_layers = {}
        for callout in callouts:
            key = str(callout["id"])
            if key in self.callout_items:
                raise ValueError(f"Duplicate callout identity: {key}")
            x, y = callout["point"]
            if not math.isfinite(x) or not math.isfinite(y):
                raise ValueError(f"Callout {key} needs a finite physical coordinate")
            item = ObjectCallout(self, str(callout["label"]))
            item.setPos(x * 1e6, -y * 1e6)
            self.scene().addItem(item)
            self.callout_items[key] = item
            self._callout_layers[key] = callout.get("layer_id", "")
        self._apply_layer_focus()

    def set_selection_filter(self, layer_ids=None, *, purpose="", provenance=None):
        """Restrict picking only, never material visibility or physical participation.

        ``provenance`` may include source_path/source_hash, instance_path and a
        per_layer mapping with more specific source or instance descriptions.
        """
        self._selection_layers = None if layer_ids is None else frozenset(layer_ids)
        self.selection_purpose = purpose
        self.selection_provenance = dict(provenance or {})
        self._apply_pickability()

    def clear_selection_filter(self):
        self.set_selection_filter()

    def set_layer_pickable(self, layer_id, pickable):
        self._layer_pickability[layer_id] = bool(pickable)
        self._apply_pickability()

    def _is_pickable(self, item):
        key = item.data(0)
        return (item.isVisible() and self._layer_pickability.get(key, True) and
                (self._selection_layers is None or key in self._selection_layers))

    def _apply_pickability(self):
        for items in self.layer_items.values():
            for item in items:
                pickable = self._is_pickable(item)
                if not pickable:
                    item.setSelected(False)
                item.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIsSelectable, pickable)
        self.clear_hover()

    def _candidate(self, item):
        key = item.data(0)
        layer = self._layer_info.get(key)
        provenance = dict(self.selection_provenance)
        provenance.update(provenance.pop("per_layer", {}).get(key, {}))
        instance = provenance.get("instance_path", "")
        if isinstance(instance, (list, tuple)):
            instance = "/".join(map(str, instance)) or "/ (top)"
        masks = getattr(getattr(layer, "geometry", None), "sources", [])
        source = str(provenance.get("source_path", provenance.get("source", "")))
        if masks:
            source = ", ".join(map(str, masks)) + (f" · {source}" if source else "")
        return SelectionCandidate(
            layer_id=key, layer_name=layer.name if layer else key,
            component_index=item.data(2), geometry=item.data(1),
            z=layer.z if layer else None, role=layer.role if layer else "source mask",
            material=layer.material_id if layer else "", source=source,
            instance_path=str(instance), provenance=provenance,
        )

    def selection_candidates(self, point):
        """Return eligible footprints under a scene point (µm, inverted y).

        Ordering is a presentation convenience; an overlap is never implicitly
        converted into a connected net or assigned to the uppermost layer.
        """
        candidates = [self._candidate(item) for item in self.scene().items(point)
                      if isinstance(item, SelectionShapeItem) and self._is_pickable(item)]
        return sorted(candidates, key=lambda candidate: (
            -(candidate.z or 0.), candidate.layer_id, candidate.component_index))

    def choose_candidate(self, candidate, *, toggle=False):
        items = self.layer_items.get(candidate.layer_id, [])
        if not 0 <= candidate.component_index < len(items):
            return False
        item = items[candidate.component_index]
        if (not self._is_pickable(item) or item.data(1) is not candidate.geometry or
                candidate.provenance != self._candidate(item).provenance):
            return False
        if not toggle:
            self.scene().clearSelection()
        item.setSelected(not item.isSelected() if toggle else True)
        return True

    def show_candidate_menu(self, candidates, position, *, toggle=False):
        if self.candidate_menu is not None:
            self.candidate_menu.close()
            self.candidate_menu.deleteLater()
        menu = self.candidate_menu = QMenu(self)
        menu.setObjectName("overlappingGeometryCandidates")
        menu.setAccessibleName("Choose overlapping layer and footprint")
        menu.addSection("Choose footprint · no automatic net selection")
        for candidate in candidates:
            action = menu.addAction(candidate.label)
            action.setData(candidate)
            action.triggered.connect(
                lambda checked=False, target=candidate: self.choose_candidate(target, toggle=toggle))
            action.hovered.connect(lambda target=candidate: self._show_hover(target.geometry))
        menu.aboutToHide.connect(self.clear_hover)
        menu.popup(position)

    def clear_hover(self):
        if self.hover_item is not None:
            self.scene().removeItem(self.hover_item)
            self.hover_item = None

    def _show_hover(self, geometry):
        self.clear_hover()
        self.hover_item = QGraphicsPathItem(geometry_path(geometry))
        pen = QPen(QColor("#528DDA"), 1.5, Qt.PenStyle.DotLine)
        pen.setCosmetic(True)
        self.hover_item.setPen(pen)
        self.hover_item.setBrush(QColor(82, 141, 218, 18))
        self.hover_item.setZValue(1900)
        self.hover_item.setAcceptedMouseButtons(Qt.MouseButton.NoButton)
        self.scene().addItem(self.hover_item)

    def selected_geometries(self):
        return [(item.data(0), item.data(1)) for item in self.scene().selectedItems()
                if isinstance(item, SelectionShapeItem) and self._is_pickable(item)]

    def show_region(self, region_geometry, label="ROI", color="#9A681A"):
        item = QGraphicsPathItem(geometry_path(region_geometry))
        pen = QPen(QColor(color), 2, Qt.PenStyle.DashLine)
        pen.setCosmetic(True)
        item.setPen(pen)
        item.setZValue(1003)
        item.setToolTip(label)
        self.scene().addItem(item)
        self.annotations.append(item)

    def show_pending_region(self, geometry, label="Pending simulation domain", *, requested=None):
        """Display an unapplied geometric draft, with handles for rectangles."""
        self.clear_pending_region()
        if geometry is None or geometry.is_empty:
            return
        from shapely.geometry import box
        self.pending_region_item = QGraphicsPathItem(geometry_path(geometry))
        pen = QPen(QColor("#006AFF"), 2., Qt.PenStyle.DashLine)
        pen.setCosmetic(True)
        self.pending_region_item.setPen(pen)
        self.pending_region_item.setBrush(QColor(0, 106, 255, 25))
        self.pending_region_item.setZValue(2005)
        self.pending_region_item.setToolTip(label + " · Apply or Cancel")
        self.pending_region_item.setAcceptedMouseButtons(Qt.MouseButton.NoButton)
        self.scene().addItem(self.pending_region_item)
        if requested is not None and not requested.equals(geometry):
            self.requested_region_item = QGraphicsPathItem(geometry_path(requested))
            requested_pen = QPen(QColor("#9A681A"), 1.5, Qt.PenStyle.DashDotLine)
            requested_pen.setCosmetic(True)
            self.requested_region_item.setPen(requested_pen)
            self.requested_region_item.setZValue(2004)
            self.requested_region_item.setToolTip("Requested support · blue preview is the effective physical clip")
            self.requested_region_item.setAcceptedMouseButtons(Qt.MouseButton.NoButton)
            self.scene().addItem(self.requested_region_item)
        self._pending_geometry = geometry
        if geometry.equals(box(*geometry.bounds)):
            xmin, ymin, xmax, ymax = geometry.bounds
            self._pending_bounds = QRectF(xmin * 1e6, -ymax * 1e6,
                                         (xmax - xmin) * 1e6, (ymax - ymin) * 1e6)
            for index, corner in enumerate(self._pending_corners()):
                handle = RegionResizeHandle(self, index)
                self.scene().addItem(handle)
                handle.setPos(corner)
                self.pending_region_handles.append(handle)

    def clear_pending_region(self):
        for item in [self.pending_region_item, self.requested_region_item, *self.pending_region_handles]:
            if item is not None:
                self.scene().removeItem(item)
        self.pending_region_item = None
        self.requested_region_item = None
        self.pending_region_handles = []
        self._pending_bounds = None
        self._pending_geometry = None

    def _pending_corners(self):
        bounds = self._pending_bounds
        return [bounds.topLeft(), bounds.topRight(), bounds.bottomRight(), bounds.bottomLeft()]

    def _resize_pending_region(self, index, position):
        if self._pending_bounds is None:
            return
        from shapely.geometry import box
        opposite = self._pending_corners()[(index + 2) % 4]
        bounds = QRectF(position, opposite).normalized()
        if min(bounds.width(), bounds.height()) <= 1e-9:
            return
        self._pending_bounds = bounds
        geometry = box(bounds.left() * 1e-6, -bounds.bottom() * 1e-6,
                       bounds.right() * 1e-6, -bounds.top() * 1e-6)
        self._pending_geometry = geometry
        self.pending_region_item.setPath(geometry_path(geometry))
        for handle, corner in zip(self.pending_region_handles, self._pending_corners()):
            handle.setPos(corner)

    def _emit_pending_region(self):
        if self._pending_geometry is not None:
            self.pending_region_changed.emit(geometry_region(self._pending_geometry))

    def show_mesh(self, mesh, visible=True, *, layer=None, region=""):
        if self.mesh_item is not None:
            self.scene().removeItem(self.mesh_item)
            self.mesh_item = None
        if not visible:
            return
        if layer is None:
            if region:
                raise ValueError("A layer is required to select a mesh region")
            indices = np.arange(len(mesh.triangles))
        else:
            source = self.result if self.result is not None and self.result.mesh is mesh else SimpleNamespace(mesh=mesh)
            indices = region_elements(source, layer, region)
        if not len(indices):
            return
        picture = QPicture()
        painter = QPainter(picture)
        pen = QPen(QColor(36, 48, 44, 110), .6)
        pen.setCosmetic(True)
        painter.setPen(pen)
        path = QPainterPath()
        for triangle in mesh.points[mesh.triangles[indices]] * np.array([1e6, -1e6]):
            path.moveTo(*triangle[0])
            path.lineTo(*triangle[1])
            path.lineTo(*triangle[2])
            path.closeSubpath()
        painter.drawPath(path)
        painter.end()
        self.mesh_item = PictureItem(picture, path.boundingRect())
        self.mesh_item.setZValue(502)
        self.scene().addItem(self.mesh_item)

    def _build_field_picture(self, mesh, support, limits, logarithmic, colormap, smooth):
        """Record scalar P1 gradients for nodes, or constant colors for elements."""
        low, high = limits
        vmin, vmax = (math.log10(low), math.log10(high)) if logarithmic else limits
        display = np.log10(support.values) if logarithmic else support.values
        log_roundoff = logarithmic and low != high and vmin == vmax
        if low == high:
            normalized = np.full(len(display), .5)
        elif log_roundoff:
            # Distinct manual limits can have identical logarithms. Across this
            # span the logarithm is linear to float precision, as in exports.
            normalized = (support.values - low) / (high - low)
        else:
            normalized = (display - vmin) / (vmax - vmin)
        colors = rgba(normalized, colormap)
        triangles = mesh.points[mesh.triangles[support.indices]] * np.array([1e6, -1e6])
        picture = QPicture()
        painter = QPainter(picture)
        painter.setPen(Qt.PenStyle.NoPen)
        # Coverage antialiasing blends both sides of shared edges with the
        # background, producing false mesh lines. Triangles tile exactly.
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, False)
        # The gradient coordinate is the original P1 scalar, including on log
        # maps. Logarithmic color stops transform its color, not its physics.
        if smooth and support.nodal and high > low:
            # 64 palette stops preserve the continuous scalar gradient while
            # keeping a 20k-element recording small enough to cache.
            fractions = np.linspace(0., 1., 64)
            positions = ((np.power(10., vmin + fractions * (vmax - vmin)) - low) / (high - low)
                         if logarithmic and not log_roundoff else fractions)
            positions[0], positions[-1] = 0., 1.
            stops = [(float(position), QColor.fromRgbF(*map(float, color)))
                     for position, color in zip(positions, rgba(fractions, colormap))]
            node_values = support.element_data[support.indices]
        else:
            node_values = None
        for i, triangle in enumerate(triangles):
            color = QColor.fromRgbF(*map(float, colors[i]))
            painter.setBrush(color)
            if node_values is not None:
                differences = triangle[1:] - triangle[0]
                gradient = np.linalg.solve(differences, node_values[i, 1:] - node_values[i, 0])
                norm_squared = float(gradient @ gradient)
                if norm_squared > 0:
                    start = triangle[0] + gradient * ((low - node_values[i, 0]) / norm_squared)
                    end = triangle[0] + gradient * ((high - node_values[i, 0]) / norm_squared)
                    brush = QLinearGradient(QPointF(*start), QPointF(*end))
                    brush.setStops(stops)
                    painter.setBrush(brush)
                painter.setPen(Qt.PenStyle.NoPen)
            painter.drawPolygon(QPolygonF([QPointF(*point) for point in triangle]))
        painter.end()
        return picture

    def show_field(self, result, layer, field, logarithmic=False, vectors=False, limits=None, *,
                   region="", colormap="teal", smooth=False, manual_limits=None):
        for item in (self.overlay_item, self.vector_item):
            if item is not None:
                self.scene().removeItem(item)
        self.overlay_item = self.vector_item = None
        self.result = result
        self.field_layer, self.field_key = layer, field
        self.field_region, self.field_colormap = region, colormap
        self.field_log, self.field_smooth = bool(logarithmic), False
        self.field_limits = None
        # Shared/history automatic ranges are explicit tuples too. Keep their
        # purpose truthful in both the emitted legend and exported metadata.
        self.field_manual_limits = limits is not None and (manual_limits is None or bool(manual_limits))
        if self._field_cache_result is not result:
            self._field_cache.clear()
            self._field_cache_bytes = 0
            self._field_cache_result = result
        if result is None or not layer or not field:
            self.legend = "Layout geometry · coordinates in µm"
            self.legend_changed.emit(self.legend)
            self.legend_details.emit(None)
            return
        if colormap not in COLORMAPS:
            raise ValueError(f"Unknown colormap {colormap!r}")
        mesh = result.mesh
        support = field_support(result, layer, field, region=region, logarithmic=logarithmic)
        if support.limits is None:
            self.legend = "No finite positive values for log scale" if logarithmic else "No field values in this selection"
            self.legend_changed.emit(self.legend)
            self.legend_details.emit(None)
            return
        try:
            low, high = display_limits(support.limits, limits=limits, logarithmic=logarithmic,
                                       colormap=colormap, provenance=result.configuration.get("visualization"),
                                       resolution_scale=support.resolution_scale)
        except ValueError:
            self.legend = "Invalid color limits: use finite increasing limits, positive for log scale."
            self.legend_changed.emit(self.legend)
            self.legend_details.emit(None)
            return
        self.field_limits = (low, high)
        self.field_smooth = bool(smooth and support.nodal)
        raw = result.fields[layer].get("temperature" if field == "temperature_rise" else field)
        key = (layer, field, region, logarithmic, low, high, colormap, self.field_smooth,
               id(raw), result.metrics.get("thermal_reference") if field == "temperature_rise" else None)
        picture = self._field_cache.get(key)
        if picture is None:
            picture = self._build_field_picture(mesh, support, (low, high), logarithmic, colormap, self.field_smooth)
            size = picture.size()
            if size <= self._field_cache_budget:
                self._field_cache[key] = picture
                self._field_cache_bytes += size
                while (len(self._field_cache) > self._field_cache_entries or
                       self._field_cache_bytes > self._field_cache_budget):
                    self._field_cache_bytes -= self._field_cache.popitem(last=False)[1].size()
        else:
            self._field_cache.move_to_end(key)
        xmin, ymin, xmax, ymax = support.bounds
        bounds = QRectF(xmin * 1e6, -ymax * 1e6, (xmax - xmin) * 1e6, (ymax - ymin) * 1e6)
        self.overlay_item = PictureItem(picture, bounds)
        self.overlay_item.setZValue(500)
        self.scene().addItem(self.overlay_item)
        label, unit = FIELD_LABELS.get(field, (field, "SI"))
        si_unit = FIELD_UNITS.get(field, "")
        shown_low, shown_high = to_display(low, si_unit), to_display(high, si_unit)
        low_label, high_label = distinct_labels((shown_low, shown_high), minimum_precision=5)
        precision_note = ("Temperature-rise variation is at floating-point resolution of absolute temperature; automatic color range is padded. Raw values are preserved."
                          if not self.field_manual_limits and not logarithmic and support.resolution_scale and
                          support.limits[1] - support.limits[0] <= 8 * np.finfo(float).eps * support.resolution_scale else "")
        arrows = vectors and field in VECTOR_FIELDS and raw is not None and raw.ndim == 2
        self.legend = f"{layer} · {label} ({unit}) · {'log₁₀' if logarithmic else 'linear'} · {low_label} … {high_label}"
        if precision_note:
            self.legend += " · numerical precision range"
        if arrows:
            self.legend += " · normalized direction arrows"
        self.legend_changed.emit(self.legend)
        self.legend_details.emit({"low": low, "high": high, "logarithmic": logarithmic,
                                  "field": field, "layer": layer, "unit": unit, "label": label,
                                  "si_unit": si_unit, "display_factor": display_factor(si_unit),
                                  "colormap": colormap, "region": region, "smooth": self.field_smooth,
                                  "elements": len(support.indices), "nodes": len(support.nodes),
                                  "manual_limits": self.field_manual_limits,
                                  "precision_note": precision_note,
                                  "vector_mode": "normalized direction" if arrows else None})
        if arrows:
            self.show_vectors(mesh, support.indices, raw)

    def show_vectors(self, mesh, indices, vectors):
        step = max(1, len(indices) // 350)
        indices = indices[::step]
        bounds = self.overlay_item.boundingRect()
        scale = max(bounds.width(), bounds.height()) * .022
        picture = QPicture()
        painter = QPainter(picture)
        pen = QPen(QColor("#192A25"), 1.)
        pen.setCosmetic(True)
        painter.setPen(pen)
        centroids = mesh.points[mesh.triangles[indices]].mean(axis=1) * np.array([1e6, -1e6])
        for index, start in zip(indices, centroids):
            vector = vectors[index]
            norm = np.linalg.norm(vector)
            if not np.isfinite(norm) or norm <= 0:
                continue
            direction = vector / norm * np.array([1., -1.])
            end = start + direction * scale
            perpendicular = np.array([-direction[1], direction[0]])
            painter.drawLine(QPointF(*start), QPointF(*end))
            for sign in (-1, 1):
                tip = end - direction * scale * .3 + sign * perpendicular * scale * .15
                painter.drawLine(QPointF(*end), QPointF(*tip))
        painter.end()
        self.vector_item = PictureItem(picture, self.overlay_item.boundingRect())
        self.vector_item.setZValue(503)
        self.scene().addItem(self.vector_item)

    def clear_preview(self):
        if self.preview is not None:
            self.scene().removeItem(self.preview)
            self.preview = None

    def update_preview(self, points, closed=False):
        self.clear_preview()
        if not points:
            return
        path = QPainterPath(points[0])
        for point in points[1:]:
            path.lineTo(point)
        if closed:
            path.closeSubpath()
        self.preview = QGraphicsPathItem(path)
        pen = QPen(QColor("#006AFF"), 2, Qt.PenStyle.DashLine)
        pen.setCosmetic(True)
        self.preview.setPen(pen)
        self.preview.setZValue(2000)
        self.scene().addItem(self.preview)

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.MiddleButton:
            self._panning = event.position().toPoint()
            self.setCursor(Qt.CursorShape.ClosedHandCursor)
            event.accept()
            return
        point = self.mapToScene(event.position().toPoint())
        if self.mode == "terminal_edge":
            point = self.snap_boundary(point)
        if event.button() == Qt.MouseButton.LeftButton:
            hit = self.itemAt(event.position().toPoint())
            if isinstance(hit, RegionResizeHandle) or getattr(hit, "vertex_handle", False):
                super().mousePressEvent(event)
                return
            if self.mode == "select":
                candidates = self.selection_candidates(point)
                toggle = bool(event.modifiers() & Qt.KeyboardModifier.ControlModifier)
                if len(candidates) > 1:
                    self.show_candidate_menu(candidates, event.globalPosition().toPoint(), toggle=toggle)
                    event.accept()
                    return
                if candidates:
                    self.choose_candidate(candidates[0], toggle=toggle)
                    event.accept()
                    return
            if self.mode == "probe":
                self.point_probed.emit(point.x() * 1e-6, -point.y() * 1e-6)
                return
            if "polygon" in self.mode:
                self.points.append(point)
                self.update_preview(self.points)
                return
            if self.mode not in {"select", "pan"}:
                self.start = point
                return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        if self._panning is not None:
            position = event.position().toPoint()
            delta = position - self._panning
            self._panning = position
            self.horizontalScrollBar().setValue(self.horizontalScrollBar().value() - delta.x())
            self.verticalScrollBar().setValue(self.verticalScrollBar().value() - delta.y())
            event.accept()
            return
        point = self.mapToScene(event.position().toPoint())
        self.position_changed.emit(point.x() * 1e-6, -point.y() * 1e-6)
        if self.mode == "select" and not event.buttons():
            candidates = self.selection_candidates(point)
            if len(candidates) == 1:
                self._show_hover(candidates[0].geometry)
            else:
                self.clear_hover()
        if self.start is not None:
            if "rect" in self.mode or self.mode == "refine":
                self.update_preview([self.start, QPointF(point.x(), self.start.y()), point,
                                     QPointF(self.start.x(), point.y())], closed=True)
            else:
                self.update_preview([self.start, point])
        elif self.points:
            self.update_preview([*self.points, point])
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.MouseButton.MiddleButton and self._panning is not None:
            self._panning = None
            self.setCursor(Qt.CursorShape.ArrowCursor if self.mode == "select" else
                           Qt.CursorShape.OpenHandCursor if self.mode == "pan" else Qt.CursorShape.CrossCursor)
            event.accept()
            return
        if self.start is not None and event.button() == Qt.MouseButton.LeftButton:
            point = self.mapToScene(event.position().toPoint())
            if self.mode == "terminal_edge":
                point = self.snap_boundary(point)
            start = self.start
            self.start = None
            self.clear_preview()
            if (point - start).manhattanLength() < 1e-10:
                return
            if "rect" in self.mode or self.mode == "refine":
                coordinates = [[start.x(), -start.y()], [point.x(), -start.y()],
                               [point.x(), -point.y()], [start.x(), -point.y()]]
                region = RegionSpec(polygons=[{"exterior": (np.asarray(coordinates) * 1e-6).tolist(), "holes": []}])
                self.region_drawn.emit(self.mode, region)
            else:
                coordinates = [[start.x() * 1e-6, -start.y() * 1e-6], [point.x() * 1e-6, -point.y() * 1e-6]]
                self.line_drawn.emit(self.mode, coordinates)
            return
        super().mouseReleaseEvent(event)

    def snap_boundary(self, point):
        """Snap graphical edge terminals to real polygon boundaries within 8 px."""
        from shapely.geometry import Point
        from shapely.ops import nearest_points
        location = Point(point.x() * 1e-6, -point.y() * 1e-6)
        limit = 8e-6 / max(self.transform().m11(), 1e-12)
        closest, distance = None, limit
        for items in self.layer_items.values():
            for item in items:
                geometry = item.data(1)
                if not self._is_pickable(item) or geometry is None or geometry.is_empty:
                    continue
                candidate = nearest_points(location, geometry.boundary)[1]
                gap = location.distance(candidate)
                if gap < distance:
                    closest, distance = candidate, gap
        return QPointF(closest.x * 1e6, -closest.y * 1e6) if closest is not None else point

    def mouseDoubleClickEvent(self, event):
        if "polygon" in self.mode:
            self.finish_polygon()
        else:
            super().mouseDoubleClickEvent(event)

    def finish_polygon(self):
        if len(self.points) >= 3:
            coordinates = [[p.x() * 1e-6, -p.y() * 1e-6] for p in self.points]
            self.region_drawn.emit(self.mode, RegionSpec(polygons=[{"exterior": coordinates, "holes": []}]))
        self.points.clear()
        self.clear_preview()

    def keyPressEvent(self, event):
        if event.key() in {Qt.Key.Key_Return, Qt.Key.Key_Enter} and self.points:
            self.finish_polygon()
        elif event.key() == Qt.Key.Key_Escape:
            self.clear_pending_region()
            self.set_mode("select")
            self.selection_cancelled.emit()
        elif event.key() in {Qt.Key.Key_Plus, Qt.Key.Key_Equal, Qt.Key.Key_Minus}:
            factor = 1 / 1.2 if event.key() == Qt.Key.Key_Minus else 1.2
            if 1e-4 < self.transform().m11() * factor < 1e7:
                self.scale(factor, factor)
        elif event.key() in {Qt.Key.Key_Left, Qt.Key.Key_Right, Qt.Key.Key_Up, Qt.Key.Key_Down}:
            horizontal = event.key() in {Qt.Key.Key_Left, Qt.Key.Key_Right}
            bar = self.horizontalScrollBar() if horizontal else self.verticalScrollBar()
            direction = -1 if event.key() in {Qt.Key.Key_Left, Qt.Key.Key_Up} else 1
            bar.setValue(bar.value() + direction * 40)
        else:
            super().keyPressEvent(event)

    def leaveEvent(self, event):
        self.clear_hover()
        super().leaveEvent(event)

    def contextMenuEvent(self, event):
        menu = QMenu(self)
        for title, action in [("Fit selection", "fit"), ("Select connected region", "connected"),
                              ("Preview selection as simulation domain", "roi"),
                              ("Create terminal on selection…", "terminal"),
                              ("Create physical layer from selection…", "layer"),
                              ("Create contact / interface on selection…", "interface")]:
            menu.addAction(title, lambda key=action: self.context_action.emit(key))
        menu.exec(event.globalPos())

    def drawBackground(self, painter, rect):
        super().drawBackground(painter, rect)
        dark = self.palette().color(QPalette.ColorRole.Base).lightness() < 128
        painter.fillRect(rect, QColor("#172233" if dark else "#F9FBFE"))
        scale = abs(self.transform().m11())
        if not self.show_grid or scale <= 0:
            return
        ideal = 90. / scale
        decade = 10 ** math.floor(math.log10(max(ideal, 1e-20)))
        spacing = max(value * decade for value in (1., 2., 5., 10.) if value * decade <= ideal)
        painter.save()
        colors = ("#1C293D", "#26374D") if dark else ("#F0F4FA", "#E6EDF6")
        for interval, color in zip((spacing / 5., spacing), colors):
            pen = QPen(QColor(color), 1.)
            pen.setCosmetic(True)
            painter.setPen(pen)
            x = math.floor(rect.left() / interval) * interval
            for _ in range(min(500, math.ceil(rect.width() / interval) + 2)):
                painter.drawLine(QPointF(x, rect.top()), QPointF(x, rect.bottom()))
                x += interval
            y = math.floor(rect.top() / interval) * interval
            for _ in range(min(500, math.ceil(rect.height() / interval) + 2)):
                painter.drawLine(QPointF(rect.left(), y), QPointF(rect.right(), y))
                y += interval
        painter.restore()

    def drawForeground(self, painter, rect):
        super().drawForeground(painter, rect)
        painter.save()
        pen = QPen(QColor("#006AFF"), 2.5)
        pen.setCosmetic(True)
        painter.setPen(pen)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        for item in self.scene().selectedItems():
            if isinstance(item, SelectionShapeItem) and self._is_pickable(item):
                painter.drawPath(item.path())
        painter.restore()
        # Coordinate orientation and physical scale remain visible at every zoom.
        painter.save()
        painter.resetTransform()
        dark = self.palette().color(QPalette.ColorRole.Base).lightness() < 128
        pen = QPen(QColor("#BDCCE4" if dark else "#465776"), 1)
        painter.setPen(pen)
        scale = self.transform().m11()
        if scale > 0:
            ideal = 110. / scale
            decade = 10 ** math.floor(math.log10(max(ideal, 1e-20)))
            length = max(v * decade for v in [1, 2, 5, 10] if v * decade <= ideal)
            y = self.viewport().height() - 24
            background = QColor(23, 34, 51, 225) if dark else QColor(249, 251, 254, 225)
            painter.fillRect(QRectF(16, y - 28, max(180., length * scale + 20), 40), background)
            painter.drawLine(24, y, int(24 + length * scale), y)
            painter.drawLine(24, y - 4, 24, y + 4)
            painter.drawLine(int(24 + length * scale), y - 4, int(24 + length * scale), y + 4)
            painter.drawText(24, y - 8, f"{length:.4g} µm  ·  +x →  +y ↑")
        painter.restore()
