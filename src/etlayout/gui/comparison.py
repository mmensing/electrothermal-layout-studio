# SPDX-License-Identifier: MPL-2.0
"""Linked, quantitative comparisons across runs, layers and solved regions."""
from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QRect, QRectF, QSize, Qt
from PySide6.QtGui import QColor, QFont, QImage, QPainter
from PySide6.QtSvg import QSvgGenerator
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QFileDialog,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSplitter,
    QVBoxLayout,
    QWidget,
)

from etlayout.gui.field_scale import FieldScaleControl
from etlayout.gui.transient import TimeNavigation
from etlayout.gui.viewer import LayoutView
from etlayout.simulation.analysis import FIELD_UNITS
from etlayout.visualization.history import history_field_limits
from etlayout.visualization.palettes import COLORMAPS
from etlayout.visualization.sampling import DISPLAY_FIELD_LABELS as FIELD_LABELS
from etlayout.visualization.support import display_limits, field_support, region_options
from etlayout.visualization.timing import snapshot_metadata
from etlayout.visualization.units import display_factor, to_display


class MapComparisonDialog(QDialog):
    """Two fields with explicit layer correspondence and a shared color range.

    One input run compares its layers. Multiple runs allow independent source,
    layer and region selections; neither map is resampled onto the other's mesh.
    """

    def __init__(self, results, parent=None):
        super().__init__(parent)
        if not results:
            raise ValueError("Field comparison requires at least one saved simulation result.")
        self.results = results
        self.setWindowTitle("Compare physical field maps")
        self.resize(1440, 900)
        self.updating = False
        self._linking = False
        self._needs_fit = True
        self.limits = None
        self.has_comparison = False
        layout = QVBoxLayout(self)
        controls = QGridLayout()
        self.field = QComboBox()
        self.field.setMinimumContentsLength(18)
        self.field.setAccessibleName("Shared comparison field")
        self.field.currentIndexChanged.connect(self.field_changed)
        field_label = QLabel("Shared &field")
        field_label.setBuddy(self.field)
        controls.addWidget(field_label, 0, 0)
        controls.addWidget(self.field, 0, 1)
        self.colormap = QComboBox()
        for key, label in COLORMAPS.items():
            self.colormap.addItem(label, key)
        self.colormap.setAccessibleName("Comparison colormap")
        cmap_label = QLabel("&Colormap")
        cmap_label.setBuddy(self.colormap)
        controls.addWidget(cmap_label, 0, 2)
        controls.addWidget(self.colormap, 0, 3)
        self.colormap.currentIndexChanged.connect(self.update_maps)
        self.logarithmic = QCheckBox("Log scale")
        self.logarithmic.toggled.connect(self.update_maps)
        controls.addWidget(self.logarithmic, 0, 4)
        self.smooth = QCheckBox("Smooth nodal fields")
        self.smooth.setToolTip("Interpolate nodal potential and temperature within each supported triangle. Element fields remain constant per triangle.")
        self.smooth.toggled.connect(self.update_maps)
        controls.addWidget(self.smooth, 0, 5)
        self.shared_extent = QCheckBox("Shared physical extents")
        self.shared_extent.setChecked(True)
        self.shared_extent.toggled.connect(self.request_fit)
        controls.addWidget(self.shared_extent, 1, 0, 1, 2)
        self.link_views = QCheckBox("Link pan / zoom")
        self.link_views.setChecked(True)
        self.link_views.setToolTip("Move either map to inspect the same physical coordinates at the same scale.")
        self.link_views.toggled.connect(lambda checked: self.synchronize_view(0) if checked else None)
        controls.addWidget(self.link_views, 1, 2, 1, 2)
        self.link_times = QCheckBox("Link saved times")
        self.link_times.setChecked(True)
        self.link_times.setToolTip("Selecting a time on either map selects the nearest retained time in the other run. Actual times remain visible.")
        self.link_times.toggled.connect(lambda checked: self.time_changed(0) if checked else None)
        controls.addWidget(self.link_times, 2, 0, 1, 2)
        self.history_range = QCheckBox("Scale across saved times")
        self.history_range.setToolTip("Keep the automatic color range fixed across retained fields from both selected runs and regions.")
        self.history_range.toggled.connect(self.update_maps)
        controls.addWidget(self.history_range, 2, 2, 1, 3)
        self.fit_button = QPushButton("Fit maps")
        self.fit_button.setAutoDefault(False)
        self.fit_button.clicked.connect(self.request_fit)
        controls.addWidget(self.fit_button, 1, 4)
        self.export_button = QPushButton("Export comparison…")
        self.export_button.setAutoDefault(False)
        self.export_button.setEnabled(False)
        self.export_button.clicked.connect(self.export)
        controls.addWidget(self.export_button, 1, 5)
        controls.setColumnStretch(1, 1)
        controls.setColumnStretch(3, 1)
        layout.addLayout(controls)
        splitter = QSplitter(Qt.Orientation.Horizontal)
        self.sources, self.layers, self.regions, self.views, self.bounds_labels = [], [], [], [], []
        self.time_navigations = []
        self._time_linking = False
        for side in range(2):
            panel = QWidget()
            column = QVBoxLayout(panel)
            source = QComboBox()
            source.setAccessibleName(f"{'Left' if side == 0 else 'Right'} comparison run")
            for name, _result in results:
                source.addItem(name)
            source.setCurrentIndex(min(side, len(results) - 1))
            source.currentIndexChanged.connect(lambda _, index=side: self.source_changed(index))
            self.sources.append(source)
            column.addWidget(source)
            timeline = TimeNavigation()
            timeline.snapshot_changed.connect(lambda _, index=side: self.time_changed(index))
            self.time_navigations.append(timeline)
            column.addWidget(timeline)
            layer = QComboBox()
            layer.setAccessibleName(f"{'Left' if side == 0 else 'Right'} result layer")
            layer.currentIndexChanged.connect(lambda _, index=side: self.layer_changed(index))
            self.layers.append(layer)
            column.addWidget(layer)
            region = QComboBox()
            region.setAccessibleName(f"{'Left' if side == 0 else 'Right'} solved region")
            region.currentIndexChanged.connect(self.request_fit)
            self.regions.append(region)
            column.addWidget(region)
            view = LayoutView()
            view.set_mode("pan")
            view.viewport_changed.connect(lambda index=side: self.synchronize_view(index))
            self.views.append(view)
            column.addWidget(view, 1)
            bounds = QLabel()
            bounds.setWordWrap(True)
            self.bounds_labels.append(bounds)
            column.addWidget(bounds)
            splitter.addWidget(panel)
        layout.addWidget(splitter, 1)
        self.legend = QLabel()
        self.legend.setWordWrap(True)
        self.legend.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        legend_row = QHBoxLayout()
        self.range_control = FieldScaleControl()
        self.range_control.range_changed.connect(self.update_maps)
        self.scale = self.range_control.color_bar  # Kept for callers checking map availability.
        legend_row.addWidget(self.range_control)
        legend_row.addWidget(self.legend, 1)
        layout.addLayout(legend_row)
        self.source_changed(0)
        self.source_changed(1)

    def selected_result(self, side):
        result = self.selected_run(side)
        if result.transient:
            return result.transient.snapshots[self.time_navigations[side].slider.value()]
        return result

    def selected_run(self, side):
        return self.results[self.sources[side].currentIndex()][1]

    def time_changed(self, side):
        if self.updating or self._time_linking:
            return
        self._time_linking = True
        try:
            result = self.selected_run(side)
            if self.link_times.isChecked() and result.transient:
                time = result.transient.times[self.time_navigations[side].slider.value()]
                self.time_navigations[1 - side].select_time(time)
            self.populate_fields()
        finally:
            self._time_linking = False

    def source_changed(self, side):
        self.updating = True
        self.time_navigations[side].set_result(self.selected_run(side))
        previous = self.layers[side].currentData()
        self.layers[side].clear()
        result = self.selected_result(side)
        names = {entry['id']: entry.get('name', entry['id']) for entry in result.configuration.get('layers', [])}
        for layer in result.fields:
            self.layers[side].addItem(f"{names.get(layer, layer)} · {layer}", layer)
        if previous and self.layers[side].findData(previous) >= 0:
            self.layers[side].setCurrentIndex(self.layers[side].findData(previous))
        elif len(self.results) == 1 and side == 1 and self.layers[side].count() > 1:
            self.layers[side].setCurrentIndex(1)
        self.populate_regions(side)
        self.updating = False
        self._needs_fit = True
        if len(self.time_navigations) == 2:
            self.link_times.setVisible(any(self.selected_run(i).transient for i in range(2)))
            self.history_range.setVisible(any(self.selected_run(i).transient for i in range(2)))
        self.time_changed(side)

    def populate_regions(self, side):
        combo = self.regions[side]
        previous = combo.currentData()
        combo.blockSignals(True)
        combo.clear()
        for key, label in region_options(self.selected_result(side), self.layers[side].currentData()):
            combo.addItem(label, key)
        if previous and combo.findData(previous) >= 0:
            combo.setCurrentIndex(combo.findData(previous))
        combo.blockSignals(False)

    def layer_changed(self, side):
        if self.updating:
            return
        self.populate_regions(side)
        self._needs_fit = True
        self.populate_fields()

    def populate_fields(self, *_):
        if self.updating:
            return
        previous = self.field.currentData()
        sets = []
        for side in range(2):
            layer = self.layers[side].currentData()
            available = set(self.selected_result(side).fields.get(layer, {}))
            if 'temperature' in available:
                available.add('temperature_rise')
            sets.append(available)
        self.field.blockSignals(True)
        self.field.clear()
        for key in FIELD_LABELS:
            if key in sets[0] & sets[1]:
                label, unit = FIELD_LABELS[key]
                self.field.addItem(f"{label} ({unit})", key)
        if previous:
            index = self.field.findData(previous)
            if index < 0:
                label, unit = FIELD_LABELS.get(previous, (previous, 'SI'))
                self.field.addItem(f"{label} ({unit}) — unavailable for selected layers", previous)
                index = self.field.count() - 1
            self.field.setCurrentIndex(index)
        self.field.blockSignals(False)
        self.update_maps()

    def field_changed(self, *_):
        self.range_control.blockSignals(True)
        self.range_control.reset()
        self.range_control.blockSignals(False)
        self.update_maps()

    def request_fit(self, *_):
        if self.updating:
            return
        self._needs_fit = True
        self.update_maps()

    def synchronize_view(self, side):
        if self.updating or self._linking or not self.link_views.isChecked() or not self.has_comparison:
            return
        self._linking = True
        try:
            source, target = self.views[side], self.views[1 - side]
            center = source.mapToScene(source.viewport().rect().center())
            target.setTransform(source.transform())
            target.centerOn(center)
        finally:
            self._linking = False

    def clear_maps(self, message):
        self.has_comparison = False
        self.limits = None
        self.export_button.setEnabled(False)
        self.fit_button.setEnabled(False)
        self.range_control.set_details(None)
        self.scale.hide()
        for view, label in zip(self.views, self.bounds_labels):
            view.set_geometries({})
            view.show_field(None, '', '')
            label.clear()
        self.legend.setText(message)

    def update_maps(self, *_):
        if self.updating:
            return
        field = self.field.currentData()
        self.smooth.setEnabled(field in {"potential", "temperature", "temperature_rise"})
        if not field:
            self.clear_maps('Select layers with a common computed field.')
            return
        self.has_comparison = False
        self.export_button.setEnabled(False)
        supports = []
        for side in range(2):
            result, layer = self.selected_result(side), self.layers[side].currentData()
            source = f"{self.sources[side].currentText()} · {self.layers[side].currentText()}"
            stored_field = 'temperature' if field == 'temperature_rise' else field
            if stored_field not in result.fields.get(layer, {}):
                label = FIELD_LABELS.get(field, (field, 'SI'))[0]
                self.clear_maps(f"{label} is unavailable on {source}. Select layers with a common computed field.")
                return
            if len(result.mesh.elements(layer)) == 0:
                self.clear_maps(f"No mesh elements on {source}. Select a layer with computed fields.")
                return
            try:
                support = field_support(result, layer, field, region=self.regions[side].currentData() or '',
                                        logarithmic=self.logarithmic.isChecked())
            except (KeyError, ValueError) as error:
                self.clear_maps(f"Cannot display {source}: {error}. Select another field, layer or region.")
                return
            if support.limits is None:
                self.clear_maps(
                    f"No finite positive values for logarithmic comparison on {source}. Choose linear scale or another field."
                    if self.logarithmic.isChecked() else
                    f"No finite field values on {source}. Select another field, layer or region.")
                return
            supports.append(support)
        bounds = [support.bounds for support in supports]
        automatic = min(s.limits[0] for s in supports), max(s.limits[1] for s in supports)
        if self.history_range.isChecked():
            ranges = [history_field_limits(self.selected_run(side), self.layers[side].currentData(), field,
                                           region=self.regions[side].currentData() or "", logarithmic=self.logarithmic.isChecked())
                      for side in range(2)]
            ranges = [limits for limits in ranges if limits is not None]
            if ranges:
                automatic = min(limits[0] for limits in ranges), max(limits[1] for limits in ranges)
        automatic = display_limits(automatic, logarithmic=self.logarithmic.isChecked(),
                                   colormap=self.colormap.currentData(), resolution_scale=max(s.resolution_scale for s in supports))
        label, unit = FIELD_LABELS[field]
        details = {'field': field, 'label': label, 'unit': unit, 'low': automatic[0], 'high': automatic[1],
                   'si_unit': FIELD_UNITS.get(field, ''), 'display_factor': display_factor(FIELD_UNITS.get(field, '')),
                   'logarithmic': self.logarithmic.isChecked(), 'colormap': self.colormap.currentData()}
        self.range_control.set_details(details)
        try:
            self.limits = self.range_control.validated_limits(self.logarithmic.isChecked()) or automatic
        except ValueError as error:
            self.clear_maps(str(error))
            return
        common = [min(b[0] for b in bounds), min(b[1] for b in bounds),
                  max(b[2] for b in bounds), max(b[3] for b in bounds)]
        self._linking = True
        try:
            for side, support in enumerate(supports):
                result, layer = self.selected_result(side), self.layers[side].currentData()
                region = self.regions[side].currentData() or ''
                view = self.views[side]
                transform = view.transform()
                center = view.mapToScene(view.viewport().rect().center())
                view.set_geometries({}, preserve_view=True)
                panel_limits = (None if not self.range_control.lock.isChecked() and self.limits[0] == self.limits[1]
                                else self.limits)
                view.show_field(result, layer, field, logarithmic=self.logarithmic.isChecked(), limits=panel_limits,
                                region=region, colormap=self.colormap.currentData(), smooth=self.smooth.isChecked(),
                                manual_limits=self.range_control.lock.isChecked())
                if view.overlay_item is None:
                    self.clear_maps(f"{view.legend} on {self.sources[side].currentText()}. Choose another field or scale.")
                    return
                shown = common if self.shared_extent.isChecked() else bounds[side]
                x0, y0, x1, y1 = [value * 1e6 for value in shown]
                rectangle = QRectF(x0, -y1, max(x1 - x0, 1e-9), max(y1 - y0, 1e-9))
                rectangle.adjust(-rectangle.width() * .06, -rectangle.height() * .06,
                                 rectangle.width() * .06, rectangle.height() * .06)
                # Leave enough navigable scene outside the support for linked pan.
                scene = QRectF(common[0] * 1e6, -common[3] * 1e6,
                               max((common[2] - common[0]) * 1e6, 1.), max((common[3] - common[1]) * 1e6, 1.))
                scene.adjust(-scene.width() * 10, -scene.height() * 10, scene.width() * 10, scene.height() * 10)
                view.setSceneRect(scene)
                if self._needs_fit:
                    view.fitInView(rectangle, Qt.AspectRatioMode.KeepAspectRatio)
                else:
                    view.setTransform(transform)
                    view.centerOn(center)
                bx0, by0, bx1, by1 = [value * 1e6 for value in bounds[side]]
                self.bounds_labels[side].setText(
                    f"{self.regions[side].currentText()} · active bounds: x {bx0:.6g}…{bx1:.6g} µm · y {by0:.6g}…{by1:.6g} µm\n"
                    f"{len(support.indices):,} displayed / {len(result.mesh.elements(layer)):,} layer elements · configuration {result.configuration_hash[:12]}" +
                    (f" · reference {result.metrics.get('thermal_reference', result.configuration.get('solver', {}).get('initial_temperature', 300.)):.6g} K" if field == 'temperature_rise' else '') +
                    (f" · t = {result.metrics['time_s']:.9g} s" if 'time_s' in result.metrics else ''))
        finally:
            self._linking = False
        self._needs_fit = False
        mode = 'Manual' if self.range_control.lock.isChecked() else 'Auto'
        self.legend.setText(f"{mode} shared color scale · {label} ({unit}) · {'log₁₀' if self.logarithmic.isChecked() else 'linear'} · "
                            f"{to_display(self.limits[0], FIELD_UNITS.get(field, '')):.7g} … {to_display(self.limits[1], FIELD_UNITS.get(field, '')):.7g}. "
                            "Each map uses its own mesh; blank areas have no displayed field." +
                            (' Values outside the manual range use endpoint colors.' if mode == 'Manual' else '') +
                            (' Automatic range covers all retained times.' if self.history_range.isChecked() and mode == 'Auto' else '') +
                            self.time_alignment_label())
        self.range_control.set_details({**details, 'low': self.limits[0], 'high': self.limits[1]})
        self.has_comparison = True
        self.export_button.setEnabled(True)
        self.fit_button.setEnabled(True)
        self.scale.show()
        if self.shared_extent.isChecked():
            self.synchronize_view(0)

    def time_alignment_label(self):
        times = [self.selected_result(side).metrics.get("time_s") for side in range(2)]
        if all(time is None for time in times):
            return ""
        labels = [f"{'Left' if side == 0 else 'right'}: {time:.9g} s" if time is not None
                  else f"{'Left' if side == 0 else 'right'}: steady" for side, time in enumerate(times)]
        alignment = "Linked nearest retained times" if self.link_times.isChecked() else "Independent times"
        return f" {alignment} · {'; '.join(labels)}."

    def showEvent(self, event):
        super().showEvent(event)
        from PySide6.QtCore import QTimer
        QTimer.singleShot(0, self.request_fit)

    def export(self):
        if not self.has_comparison:
            return
        path, _ = QFileDialog.getSaveFileName(self, 'Export side-by-side field comparison', 'field-comparison.svg',
                                              'SVG (*.svg);;PNG (*.png)')
        if path:
            self.export_to(path)

    def export_to(self, path):
        if not self.has_comparison:
            raise ValueError('No valid field comparison to export. ' + self.legend.text())
        from etlayout.gui.export import draw_colorbar
        path = Path(path)
        width, height = 2000, 1260
        if path.suffix.lower() == '.svg':
            device = QSvgGenerator()
            device.setFileName(str(path))
            device.setSize(QSize(width, height))
            device.setViewBox(QRect(0, 0, width, height))
        else:
            device = QImage(width, height, QImage.Format.Format_ARGB32)
        painter = QPainter(device)
        painter.fillRect(QRectF(0, 0, width, height), QColor('white'))
        painter.setPen(QColor('#17221e'))
        for side, view in enumerate(self.views):
            left = 30 + side * 1000
            painter.setFont(QFont('Segoe UI', 18))
            title = self.sources[side].currentText() + ' · ' + self.layers[side].currentText()
            if 'time_s' in self.selected_result(side).metrics:
                time_label = f"t = {self.selected_result(side).metrics['time_s']:.9g} s"
                if time_label not in title:
                    title += ' · ' + time_label
            painter.drawText(QRectF(left, 20, 940, 80), Qt.TextFlag.TextWordWrap, title)
            view.render(painter, QRectF(left, 105, 940, 900), view.viewport().rect())
            painter.setFont(QFont('Segoe UI', 12))
            painter.drawText(QRectF(left, 1015, 940, 65), Qt.TextFlag.TextWordWrap, self.bounds_labels[side].text())
        draw_colorbar(painter, QRectF(30, 1100, width - 60, 140),
                      tuple(to_display(value, FIELD_UNITS.get(self.field.currentData(), '')) for value in self.limits),
                      colormap=self.colormap.currentData(), logarithmic=self.logarithmic.isChecked(),
                      label=self.legend.text(), clipped=self.range_control.lock.isChecked())
        painter.end()
        if isinstance(device, QImage) and not device.save(str(path), 'PNG'):
            raise OSError(f"Cannot save comparison {path}")
        from etlayout.io.project import _json_dump
        label, unit = FIELD_LABELS[self.field.currentData()]
        panels = []
        for side, view in enumerate(self.views):
            result = self.selected_result(side)
            rectangle = view.mapToScene(view.viewport().rect()).boundingRect()
            transform = view.transform()
            panels.append({
                'name': self.sources[side].currentText(), 'configuration_hash': result.configuration_hash,
                'solver_version': result.solver_version, 'converged': result.converged,
                **snapshot_metadata(result),
                'layer': self.layers[side].currentData(), 'layer_label': self.layers[side].currentText(),
                'region': self.regions[side].currentData() or '', 'region_label': self.regions[side].currentText(),
                'smooth': view.field_smooth, 'provenance': result.configuration.get('visualization', {}),
                'viewport_bounds_um': [rectangle.left(), -rectangle.bottom(), rectangle.right(), -rectangle.top()],
                'viewport_transform': [transform.m11(), transform.m12(), transform.m13(),
                                       transform.m21(), transform.m22(), transform.m23(),
                                       transform.m31(), transform.m32(), transform.m33()],
            })
        metadata = {'kind': 'side_by_side', 'field': self.field.currentData(), 'label': label,
                    'unit': FIELD_UNITS.get(self.field.currentData(), ''), 'display_unit': unit,
                    'display_limits': tuple(to_display(value, FIELD_UNITS.get(self.field.currentData(), '')) for value in self.limits),
                    'limits': self.limits, 'manual_limits': self.range_control.lock.isChecked(),
                    'logarithmic': self.logarithmic.isChecked(), 'colormap': self.colormap.currentData(),
                    'shared_extent': self.shared_extent.isChecked(), 'linked_navigation': self.link_views.isChecked(),
                    'linked_times': self.link_times.isChecked(),
                    'history_range': self.history_range.isChecked(),
                    'panels': panels}
        path.with_suffix(path.suffix + '.json').write_text(_json_dump(metadata), encoding='utf-8')
