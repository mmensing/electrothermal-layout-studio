# SPDX-License-Identifier: MPL-2.0
"""Pulse-editor workflows preserve pending edits and represent narrow pulses."""
from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import numpy as np
import pytest
from PySide6.QtWidgets import QDialog, QTableWidgetItem, QTabWidget

from etlayout.gui.theme import apply_widget_theme
from etlayout.gui.transient import TimeHistoryPanel, TransientDialog, drive_targets
from etlayout.models import HeatSource, Project, RegionSpec, Terminal, TransientDrive, Waveform


def dialog_for(qtbot, *, drives=True):
    project = Project(heat_sources=[HeatSource("first", "metal"), HeatSource("second", "metal")])
    project.solver.mode = "thermal"
    project.transient.enabled = True
    if drives:
        project.transient.drives = [TransientDrive(f"heat_sources.{name}.value", Waveform(kind="pulse"))
                                    for name in ("first", "second")]
    dialog = TransientDialog(project)
    qtbot.addWidget(dialog)
    return project, dialog


def test_switching_drives_commits_valid_pending_values_without_mutating_source(qtbot):
    project, dialog = dialog_for(qtbot)
    dialog.wave_inputs["high"].setText("7.5")
    dialog.drive_list.setCurrentRow(1)
    assert dialog.drives[0].waveform.high == 7.5
    assert dialog.drive_list.currentRow() == 1
    assert dialog.wave_inputs["high"].text() == "1.0"
    assert project.transient.drives[0].waveform.high == 1.


def test_invalid_pending_values_keep_the_editor_on_the_previous_drive(qtbot):
    _, dialog = dialog_for(qtbot)
    dialog.wave_inputs["width"].setText("-1")
    dialog.drive_list.setCurrentRow(1)
    assert dialog.drive_list.currentRow() == 0
    assert dialog.wave_inputs["width"].text() == "-1"
    assert "positive" in dialog.error.text()
    assert dialog.drives[0].waveform.width > 0


def test_ok_accepts_a_new_edited_waveform_without_a_separate_apply_click(qtbot):
    project, dialog = dialog_for(qtbot, drives=False)
    dialog.wave_inputs["high"].setText("4.2")
    dialog.accept()
    assert dialog.result() == QDialog.DialogCode.Accepted
    assert len(dialog.drives) == 1
    assert dialog.drives[0].waveform.high == 4.2
    assert not project.transient.drives


def test_untouched_empty_editor_does_not_create_an_unrequested_drive(qtbot):
    _, dialog = dialog_for(qtbot, drives=False)
    dialog.accept()
    assert dialog.result() == QDialog.DialogCode.Accepted
    assert not dialog.drives


def test_remove_then_ok_does_not_resurrect_the_removed_waveform(qtbot):
    _, dialog = dialog_for(qtbot)
    dialog.remove_drive()
    assert len(dialog.drives) == 1
    dialog.remove_drive()
    dialog.accept()
    assert dialog.result() == QDialog.DialogCode.Accepted
    assert not dialog.drives


def test_piecewise_table_rejects_invalid_values_with_point_context(qtbot):
    _, dialog = dialog_for(qtbot)
    dialog.kind.setCurrentIndex(dialog.kind.findData("piecewise_linear"))
    dialog.piecewise_table.setRowCount(2)
    dialog.piecewise_table.setItem(0, 0, QTableWidgetItem("invalid"))
    with pytest.raises(ValueError, match="Point 1, time"):
        dialog.read_waveform()


def test_narrow_pulse_preview_contains_both_sides_and_the_actual_unit(qtbot):
    _, dialog = dialog_for(qtbot)
    wave = Waveform(kind="pulse", low=0., high=12., delay=1e-6, width=1e-7, period=0.)
    dialog.plot_preview(wave)
    x, y = dialog.preview.listDataItems()[0].getData()
    assert np.max(y) == 12.  # Uniform 401-point sampling would miss this pulse.
    assert any(x[index] == x[index + 1] and y[index] != y[index + 1] for index in range(len(x) - 1))
    assert dialog.preview.getAxis("left").labelUnits == "W/m²"


def test_dense_pulse_preview_is_bounded_and_labels_its_shorter_time_window(qtbot):
    _, dialog = dialog_for(qtbot)
    dialog.plot_preview(Waveform(kind="pulse", width=1e-9, period=2e-9))
    x, _ = dialog.preview.listDataItems()[0].getData()
    assert len(x) < 1000
    assert np.max(x) == pytest.approx(1e-8)
    assert "first 5 periods" in dialog.preview.plotItem.titleLabel.text


def test_compact_waveform_dialog_keeps_preview_visible(qtbot):
    _, dialog = dialog_for(qtbot)
    dialog.findChild(QTabWidget).setCurrentIndex(1)
    dialog.resize(780, 630)
    dialog.show()
    qtbot.waitUntil(lambda: dialog.preview.height() >= 145)
    assert dialog.height() == 630
    assert dialog.editor_scroll.viewport().height() >= 170


def test_density_drive_units_match_volumetric_injection_for_line_and_area_targets(qtbot):
    project = Project(terminals=[
        Terminal("line", "metal", RegionSpec(line=[(0., 0.), (1e-6, 0.)]), kind="density"),
        Terminal("area", "metal", RegionSpec(full_domain=True), kind="density"),
        Terminal("total", "metal", kind="current"),
    ])
    assert dict(drive_targets(project)) == {
        "terminals.line.value": "A/m²", "terminals.area.value": "A/m²", "terminals.total.value": "A",
    }
    dialog = TransientDialog(project)
    qtbot.addWidget(dialog)
    dialog.plot_preview(Waveform())
    assert dialog.preview.getAxis("left").labelUnits == "A/m²"


def test_engineering_units_budget_and_readable_targets_round_trip(qtbot):
    project, dialog = dialog_for(qtbot)
    dialog.time_inputs["stop_time"].setText("2 ms")
    dialog.time_inputs["time_step"].setText("25 µs")
    dialog.time_inputs["max_history_bytes"].setText("128 MiB")
    dialog.wave_inputs["width"].setText("100 us")
    dialog.wave_inputs["high"].setText("20 mW/m²")
    assert "heat_sources." not in dialog.target.currentText()
    dialog.accept()
    assert dialog.result() == QDialog.DialogCode.Accepted
    assert dialog.project.transient.stop_time == .002
    assert dialog.project.transient.time_step == pytest.approx(25e-6)
    assert dialog.project.transient.max_history_bytes == 128 * 1024**2
    assert dialog.drives[0].waveform.width == pytest.approx(100e-6)
    assert dialog.drives[0].waveform.high == .02
    assert project.transient.max_history_bytes == 256 * 1024**2


def test_piecewise_table_parses_mixed_time_and_amplitude_units(qtbot):
    _, dialog = dialog_for(qtbot)
    dialog.kind.setCurrentIndex(dialog.kind.findData("piecewise_linear"))
    dialog.piecewise_table.setRowCount(3)
    for row, pair in enumerate((("0", "0"), ("25 µs", "20 mW/m²"), ("0.1 ms", "0"))):
        for col, text in enumerate(pair):
            dialog.piecewise_table.setItem(row, col, QTableWidgetItem(text))
    assert dialog.apply_drive()
    assert dialog.drives[0].waveform.times == pytest.approx([0, 25e-6, .0001])
    assert dialog.drives[0].waveform.values == [0, .02, 0]


def test_enabling_transient_promotes_electrical_mode_without_changing_source(qtbot):
    project, dialog = dialog_for(qtbot)
    dialog.project.solver.mode = "electrical"
    dialog.accept()
    assert dialog.result() == QDialog.DialogCode.Accepted
    assert dialog.project.solver.mode == "electrothermal"
    assert project.solver.mode == "thermal"


def test_capacity_scheme_selection_round_trips_without_mutating_source(qtbot):
    project, dialog = dialog_for(qtbot)
    assert dialog.capacity_scheme.currentData() == "consistent"
    dialog.capacity_scheme.setCurrentIndex(dialog.capacity_scheme.findData("lumped"))
    dialog.accept()
    assert dialog.result() == QDialog.DialogCode.Accepted
    assert dialog.project.transient.capacity_scheme == "lumped"
    assert project.transient.capacity_scheme == "consistent"
    reopened = TransientDialog(dialog.project)
    qtbot.addWidget(reopened)
    assert reopened.capacity_scheme.currentData() == "lumped"


def test_preflight_uses_resume_segment_and_resets_when_initialization_changes(qtbot):
    project = Project()
    project.solver.mode = "thermal"
    project.transient.enabled = True
    project.transient.initialization = "resume"
    project.transient.stop_time = 1.
    project.transient.time_step = .1
    project.transient.output_interval = .1
    dialog = TransientDialog(project, start_time=.5)
    qtbot.addWidget(dialog)
    assert "5 steps · 6 scheduled fields" in dialog.estimates.text()
    for mode in ("uniform", "steady"):
        dialog.initialization.setCurrentIndex(dialog.initialization.findData(mode))
        assert "10 steps · 11 scheduled fields" in dialog.estimates.text()
    dialog.initialization.setCurrentIndex(dialog.initialization.findData("resume"))
    assert "5 steps · 6 scheduled fields" in dialog.estimates.text()
    assert project.transient.initialization == "resume"


def test_history_panel_supports_light_and_dark_themes_without_shadowing_qt(qtbot, qapp):
    panel = TimeHistoryPanel()
    qtbot.addWidget(panel)
    original_palette = qapp.palette()
    try:
        for dark in (False, True):
            apply_widget_theme(panel, dark)
            panel.show()
            qapp.processEvents()
            assert callable(panel.metric)
            assert panel.metric_selector.count() == 7
    finally:
        qapp.setPalette(original_palette)
