# SPDX-License-Identifier: MPL-2.0
"""A quantitative map legend with an explicit, editable fixed range."""
from __future__ import annotations

import numpy as np
from PySide6.QtCore import QRectF, Qt, Signal
from PySide6.QtGui import QColor, QLinearGradient, QPainter, QPen
from PySide6.QtWidgets import QCheckBox, QHBoxLayout, QLabel, QLineEdit, QPushButton, QVBoxLayout, QWidget

from etlayout.gui.inputs import parse_number
from etlayout.simulation.analysis import FIELD_UNITS
from etlayout.visualization.formatting import distinct_labels, scale_ticks
from etlayout.visualization.units import display_factor, display_unit, to_display, to_si


class ColorLegend(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.details = None
        self.setMinimumSize(220, 45)
        self.setAccessibleName("Field color scale with minimum, midpoint and maximum")

    def set_details(self, details):
        self.details = details
        self._label_layout()
        self.update()

    def _label_layout(self):
        if not self.details:
            self.setToolTip("")
            return
        fractions, labels = scale_ticks(self.details["low"], self.details["high"],
                                        logarithmic=self.details.get("logarithmic", False),
                                        factor=self.details.get("display_factor", 1.))
        self._ticks = fractions, labels
        self._crowded = len(labels) > 1 and sum(self.fontMetrics().horizontalAdvance(label) for label in labels) + 18 > self.width() - 14
        self.setMinimumHeight(62 if self._crowded else 45)
        self.setToolTip(" · ".join(labels) + ("\n" + self.details["precision_note"]
                                             if self.details.get("precision_note") else ""))

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._label_layout()

    def paintEvent(self, event):
        if not self.details:
            return
        painter = QPainter(self)
        width = self.width() - 14
        from etlayout.visualization.palettes import rgba
        low, high = self.details["low"], self.details["high"]
        colormap = self.details.get("colormap", "teal")
        if low == high:
            fill = QColor.fromRgbF(*map(float, rgba(np.array([.5]), colormap)[0]))
        else:
            fill = QLinearGradient(7, 0, width + 7, 0)
            fractions = np.linspace(0., 1., 65)
            for fraction, color in zip(fractions, rgba(fractions, colormap)):
                fill.setColorAt(float(fraction), QColor.fromRgbF(*map(float, color)))
        painter.fillRect(QRectF(7, 3, width, 12), fill)
        painter.setPen(QPen(self.palette().text().color(), 1))
        # Wide precision-sensitive labels use two endpoint rows. A midpoint is
        # omitted only when its text would collide; the tooltip retains all ticks.
        fractions, labels = self._ticks
        crowded = self._crowded
        for fraction, label in zip(fractions, labels):
            if crowded and fraction == .5:
                continue
            x = 7 + fraction * width
            painter.drawLine(int(x), 15, int(x), 20)
            alignment = {0.: Qt.AlignmentFlag.AlignLeft, .5: Qt.AlignmentFlag.AlignHCenter,
                         1.: Qt.AlignmentFlag.AlignRight}[fraction]
            bounds = QRectF(7, 38 if crowded and fraction == 1. else 20, width, 22)
            painter.drawText(bounds, alignment | Qt.AlignmentFlag.AlignTop, label)


class FieldScaleControl(QWidget):
    range_changed = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.details = None
        self.valid = True
        self._input_details = None
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(2)
        self.color_bar = ColorLegend()
        layout.addWidget(self.color_bar)
        row = QHBoxLayout()
        self.lock = QCheckBox("Manual range")
        self.lock.setAccessibleName("Use manual color range")
        self.lock.setToolTip("Apply fixed numeric limits to this quantity. Values outside the range use its endpoint colors.")
        row.addWidget(self.lock)
        self.auto_button = QPushButton("Auto")
        self.auto_button.setAutoDefault(False)
        self.auto_button.setAccessibleName("Use automatic color range")
        self.auto_button.setToolTip("Reset to the range of the currently displayed field support.")
        self.auto_button.clicked.connect(self.use_auto)
        row.addWidget(self.auto_button)
        layout.addLayout(row)
        row = QHBoxLayout()
        self.minimum, self.maximum = QLineEdit(), QLineEdit()
        for label, entry in [("Minimum color scale value", self.minimum), ("Maximum color scale value", self.maximum)]:
            entry.setMinimumWidth(100)
            entry.setMaximumWidth(110)
            entry.setPlaceholderText("Minimum" if entry is self.minimum else "Maximum")
            entry.setAccessibleName(label)
            entry.setToolTip(label + "; use the field unit or an engineering suffix.")
            entry.setEnabled(False)
            entry.editingFinished.connect(self.range_changed)
            entry.editingFinished.connect(lambda control=entry: control.setCursorPosition(0))
            entry.textChanged.connect(lambda text, control=entry: self._describe_range_input(control, text))
            caption = QLabel("Mi&n" if entry is self.minimum else "Ma&x")
            caption.setBuddy(entry)
            row.addWidget(caption)
            row.addWidget(entry)
        self.unit_label = QLabel()
        row.addWidget(self.unit_label)
        layout.addLayout(row)
        self.error = QLabel()
        self.error.setProperty("error", True)
        self.error.setWordWrap(True)
        self.error.hide()
        layout.addWidget(self.error)
        self.lock.toggled.connect(self._toggle)

    @staticmethod
    def _describe_range_input(entry, text):
        entry.setToolTip(f"{entry.accessibleName()}: {text}\nUse the field unit or an engineering suffix.")
        if not entry.hasFocus():
            entry.setCursorPosition(0)

    def _toggle(self, enabled):
        self.minimum.setEnabled(enabled)
        self.maximum.setEnabled(enabled)
        if not enabled:
            self.error.hide()
            self.valid = True
        self.range_changed.emit()

    def reset(self, *_):
        self.lock.setChecked(False)
        self.valid = True
        self.error.hide()

    def use_auto(self, *_):
        changed = self.lock.isChecked()
        self.reset()
        if not changed:
            self.range_changed.emit()

    def set_details(self, details):
        previous = self.details or self._input_details
        if details and previous and details["field"] != previous["field"]:
            # Changing the quantity changes units; a prior lock must not carry over.
            self.lock.blockSignals(True)
            self.lock.setChecked(False)
            self.lock.blockSignals(False)
            self.minimum.setEnabled(False)
            self.maximum.setEnabled(False)
            self.valid = True
            self.error.hide()
        self.details = details
        if details:
            self._input_details = details
        self.color_bar.set_details(details)
        self.unit_label.setText((details or self._input_details or {}).get("unit", ""))
        if details and not self.lock.isChecked():
            factor = details.get("display_factor", 1.)
            span = details['high'] - details['low']
            precision = 17 if 0 < span < 1e-8 * max(abs(details['low']), abs(details['high'])) else 7
            low, high = distinct_labels((details['low'] * factor, details['high'] * factor), minimum_precision=precision)
            self.minimum.setText(low)
            self.maximum.setText(high)

    def limits(self, logarithmic=False):
        if not self.lock.isChecked():
            self.valid = True
            return None
        try:
            unit = (self.details or self._input_details or {}).get("unit", "")
            low = parse_number(self.minimum.text(), unit)
            high = parse_number(self.maximum.text(), unit)
            if low >= high:
                raise ValueError("Minimum must be smaller than maximum.")
            if logarithmic and low <= 0:
                raise ValueError("Log limits must be positive.")
        except ValueError as error:
            self.valid = False
            self.error.setText("Manual range not applied. " + str(error))
            self.error.show()
            return None
        self.valid = True
        self.error.hide()
        factor = (self.details or self._input_details or {}).get("display_factor", 1.)
        si_unit = (self.details or self._input_details or {}).get("si_unit")
        if si_unit is not None:
            return to_si(low, si_unit), to_si(high, si_unit)
        return low / factor, high / factor

    def validated_limits(self, logarithmic=False):
        """Return manual limits, or raise so callers cannot silently display Auto."""
        limits = self.limits(logarithmic)
        if not self.valid:
            raise ValueError(self.error.text())
        return limits

    @property
    def current_colormap(self):
        return (self.details or {}).get("colormap", "teal")

    def set_colormap(self, colormap):
        if self.details:
            self.set_details({**self.details, "colormap": colormap})

    def capture_state(self):
        details = self.details or self._input_details or {}
        field = details.get("field")
        si_unit = FIELD_UNITS.get(field, details.get("unit", ""))
        unit = details.get("unit", si_unit)
        state = {"field": field, "manual": self.lock.isChecked(), "unit": si_unit,
                 "display_unit": unit, "display_minimum": self.minimum.text(), "display_maximum": self.maximum.text()}
        for key, entry in (("minimum", self.minimum), ("maximum", self.maximum)):
            text = entry.text()
            if unit != si_unit:
                try:
                    text = f"{to_si(parse_number(text, unit), si_unit):.17g}"
                except ValueError:
                    text = "Invalid display range: " + text
            state[key] = text
        # The original keys remain SI for older readers; the extra display text
        # retains exact editing state, including invalid drafts, in this reader.
        return state

    def restore_state(self, state, *, emit=True):
        if not self.details and not self._input_details and state.get("field") in FIELD_UNITS:
            si_unit = FIELD_UNITS[state["field"]]
            self._input_details = {"field": state["field"], "si_unit": si_unit,
                                   "unit": display_unit(si_unit), "display_factor": display_factor(si_unit)}
            self.unit_label.setText(display_unit(si_unit))
        same_field = not self.details or not state.get("field") or state["field"] == self.details.get("field")
        self.lock.blockSignals(True)
        self.lock.setChecked(bool(state.get("manual")) and same_field)
        self.lock.blockSignals(False)
        self.minimum.setEnabled(self.lock.isChecked())
        self.maximum.setEnabled(self.lock.isChecked())
        if same_field:
            details = self.details or self._input_details or {}
            si_unit = FIELD_UNITS.get(state.get("field"), "")
            old_unit = state.get("unit", si_unit)
            for key, entry in (("minimum", self.minimum), ("maximum", self.maximum)):
                if state.get("display_unit") == details.get("unit") and "display_" + key in state:
                    entry.setText(str(state["display_" + key]))
                    continue
                text = str(state.get(key, entry.text()))
                if old_unit != details.get("unit", old_unit):
                    try:
                        value = parse_number(text, old_unit)
                        # Older workspace states stored SI text without a unit.
                        if old_unit == si_unit:
                            text = f"{to_display(value, si_unit):.17g}"
                        elif old_unit == display_unit(si_unit):
                            text = f"{to_si(value, si_unit):.17g}"
                        else:
                            text += " " + old_unit
                    except ValueError:
                        # Never make an invalid legacy SI draft valid merely by
                        # changing the default display unit.
                        text += " " + old_unit
                entry.setText(text)
        self.error.hide()
        self.valid = True
        if emit:
            self.range_changed.emit()
