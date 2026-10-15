# SPDX-License-Identifier: MPL-2.0
"""Regression coverage for audit UI-01/UI-02 and unit-aware condition editing."""
from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtGui import QColor, QPalette
from PySide6.QtWidgets import QDialog, QWidget

from etlayout.gui.field_scale import FieldScaleControl
from etlayout.gui.forms import BoundsDialog, ModelDialog
from etlayout.gui.inputs import parse_number
from etlayout.gui.theme import apply_widget_theme
from etlayout.models import (
    Interface,
    MeshSettings,
    PhysicalLayer,
    PhysicalPort,
    PortLanding,
    PropertyModel,
    RegionSpec,
    SolverSettings,
    SweepSpec,
    Terminal,
)


@pytest.mark.parametrize("text,unit,value", [("2", "µm", 2e-6), ("2000 nm", "µm", 2e-6),
                                           ("2e-6 m", "µm", 2e-6), ("20 mV", "V", .02),
                                           ("20m", "V", .02), ("2.5k", "", 2500),
                                           ("1 mm²/(V s)", "m²/(V s)", 1e-6),
                                           ("1 nm²/(V s)", "m²/(V s)", 1e-18),
                                           ("1 mm² K/W", "m² K/W", 1e-6),
                                           ("1 kA/m²", "A/m²", 1000.)])
def test_engineering_inputs_convert_to_si(text, unit, value):
    assert parse_number(text, unit) == pytest.approx(value, rel=1e-12, abs=1e-30)


@pytest.mark.parametrize("text", ["", "NaN", "inf", "-inf", "1e999", "3 bananas"])
def test_nonfinite_or_invalid_inputs_rejected(text):
    with pytest.raises(ValueError):
        parse_number(text)


def test_blank_required_value_keeps_original_model_and_shows_inline_error(qtbot):
    model = MeshSettings()
    dialog = ModelDialog(model)
    qtbot.addWidget(dialog)
    dialog.show()
    dialog.activateWindow()
    qtbot.waitUntil(lambda: dialog.isActiveWindow())
    dialog.widgets["max_size"].clear()
    dialog.widgets["min_size"].setText(".2")
    dialog.accept()
    assert dialog.result() != QDialog.DialogCode.Accepted
    assert dialog.model.max_size == model.max_size
    assert dialog.model.min_size == model.min_size  # no partial mutation
    assert dialog.errors["max_size"].isVisible()
    assert dialog.widgets["max_size"].hasFocus()
    dialog.widgets["max_size"].setText("2000 nm")
    dialog.accept()
    assert dialog.result() == QDialog.DialogCode.Accepted
    assert dialog.model.max_size == pytest.approx(2e-6)


@pytest.mark.parametrize("field,text", [("min_size", "-2"), ("max_elements", "2.5"),
                                       ("max_size", "nan"), ("min_size", "20")])
def test_mesh_range_and_type_errors_stay_in_form(qtbot, field, text):
    dialog = ModelDialog(MeshSettings())
    qtbot.addWidget(dialog)
    dialog.widgets[field].setText(text)
    dialog.accept()
    assert dialog.result() != QDialog.DialogCode.Accepted
    assert dialog.errors[field].text()


def test_declared_float_type_accepts_fraction_with_integer_original(qtbot):
    dialog = ModelDialog(Terminal("drain", "metal", value=1))
    qtbot.addWidget(dialog)
    dialog.widgets["value"].setText("250 mV")
    dialog.accept()
    assert dialog.result() == QDialog.DialogCode.Accepted
    assert dialog.model.value == .25
    assert "V" in dialog.widgets["value"].accessibleName()


@pytest.mark.parametrize("kind,text,unit,value", [("current", "5 A", "A", 5.),
                                                 ("current", "750 mA", "A", .75),
                                                 ("voltage", "20 mV", "V", .02)])
def test_external_port_drive_units_preserve_finite_landings(qtbot, kind, text, unit, value):
    model = PhysicalPort("drive", [PortLanding("footprint", .004, .5)], kind, 1.)
    dialog = ModelDialog(model)
    qtbot.addWidget(dialog)
    assert dialog.unit_for("value") == unit
    assert unit in dialog.widgets["value"].accessibleName()
    dialog.widgets["value"].setText(text)
    dialog.accept()
    assert dialog.result() == QDialog.DialogCode.Accepted
    assert dialog.model.value == value and dialog.model.landings == model.landings
    assert model.value == 1.


@pytest.mark.parametrize("geometry", [RegionSpec(line=[(0., 0.), (1e-6, 0.)]), RegionSpec(full_domain=True)])
def test_terminal_density_form_labels_and_parses_current_per_area(qtbot, geometry):
    dialog = ModelDialog(Terminal("injection", "metal", geometry, kind="density", value=2000.))
    qtbot.addWidget(dialog)
    assert dialog.unit_for("value") == "A/m²"
    assert "A/m²" in dialog.widgets["value"].accessibleName()
    assert "edge length × layer thickness" in dialog.description("Terminal", "")
    dialog.widgets["value"].setText("3 kA/m²")
    dialog.accept()
    assert dialog.result() == QDialog.DialogCode.Accepted
    assert dialog.model.value == 3000.
    assert dialog.model.geometry == geometry


def test_optional_fields_and_string_lists_remain_typed(qtbot):
    layer = ModelDialog(PhysicalLayer("m", "Metal", "copper"))
    qtbot.addWidget(layer)
    layer.widgets["mesh_size"].clear()
    layer.accept()
    assert layer.model.mesh_size is None
    interface = ModelDialog(Interface("i", "a", "b"))
    qtbot.addWidget(interface)
    interface.widgets["material_id"].setText("copper")
    interface.accept()
    assert interface.model.material_id == "copper"
    solver = ModelDialog(SolverSettings())
    qtbot.addWidget(solver)
    solver.widgets["resistance_terminals"].setText("source, drain")
    solver.accept()
    assert solver.model.resistance_terminals == ["source", "drain"]


def test_friendly_sweep_choice_preserves_parameter_path(qtbot):
    path = "terminals.drain.value"
    dialog = ModelDialog(SweepSpec(parameter=path, values=[.01, .02]),
                         choices={"parameter": {path: "Drain · Voltage"}}, parameter_units={path: "V"})
    qtbot.addWidget(dialog)
    assert dialog.widgets["parameter"].currentText() == "Drain · Voltage"
    dialog.widgets["values"].setText("20 mV, .5, 1 V")
    dialog.accept()
    assert dialog.model.parameter == path
    assert dialog.model.values == [.02, .5, 1.]


def test_property_table_rejects_unordered_temperatures(qtbot):
    dialog = ModelDialog(PropertyModel(kind="table", temperatures=[300., 200.], values=[1., 2.]))
    qtbot.addWidget(dialog)
    dialog.accept()
    assert dialog.result() != QDialog.DialogCode.Accepted
    assert "increasing" in dialog.errors["temperatures"].text()


def test_sweep_length_values_display_and_roundtrip_in_micrometers(qtbot):
    path = "mesh.max_size"
    dialog = ModelDialog(SweepSpec(parameter=path, values=[2e-6, 5e-6]),
                         choices={"parameter": {path: "Mesh · Maximum size"}}, parameter_units={path: "µm"})
    qtbot.addWidget(dialog)
    assert dialog.widgets["values"].text() == "2.0, 5.0"
    dialog.accept()
    assert dialog.model.values == pytest.approx([2e-6, 5e-6])


def test_bounds_reject_nonfinite_coordinates(qtbot):
    dialog = BoundsDialog()
    qtbot.addWidget(dialog)
    dialog.entries[0].setText("nan")
    with pytest.raises(ValueError):
        dialog.region()


def test_light_theme_form_does_not_inherit_dark_os_window_palette(qtbot, qapp):
    original = QPalette(qapp.palette())
    original_style = qapp.styleSheet()
    try:
        dark_os = QPalette(original)
        dark_os.setColor(QPalette.ColorRole.Window, QColor("#202020"))
        qapp.setPalette(dark_os)
        host = QWidget()
        qtbot.addWidget(host)
        apply_widget_theme(host, False)
        dialog = ModelDialog(Terminal("drain", "metal"), parent=host)
        qtbot.addWidget(dialog)
        dialog.show()
        qapp.processEvents()
        body = dialog.scroll.widget()
        assert body.palette().color(QPalette.ColorRole.Window).lightness() > 200
        assert dialog.labels["value"].palette().color(QPalette.ColorRole.WindowText).lightness() < 80
        apply_widget_theme(host, True)
        qapp.processEvents()
        assert body.palette().color(QPalette.ColorRole.Window).lightness() < 80
        assert dialog.labels["value"].palette().color(QPalette.ColorRole.WindowText).lightness() > 200
    finally:
        qapp.setStyleSheet(original_style)
        qapp.setPalette(original)


def test_color_scale_locks_values_and_rejects_invalid_log_limits(qtbot):
    control = FieldScaleControl()
    qtbot.addWidget(control)
    control.set_details({"field": "potential", "unit": "V", "low": 0., "high": 1.})
    control.lock.setChecked(True)
    control.minimum.setText("20 mV")
    control.maximum.setText(".5")
    assert control.limits() == (.02, .5)
    control.minimum.setText("-1")
    assert control.limits(logarithmic=True) is None
    assert "positive" in control.error.text()
    control.set_details({"field": "temperature", "unit": "K", "low": 300., "high": 350.})
    assert not control.lock.isChecked()
