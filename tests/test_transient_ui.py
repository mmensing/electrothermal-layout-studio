# SPDX-License-Identifier: MPL-2.0
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from copy import deepcopy

import numpy as np
import pytest
from PySide6.QtWidgets import QLineEdit, QPushButton, QSlider, QTableWidgetItem

from etlayout.examples import make_example
from etlayout.gui.main_window import MainWindow
from etlayout.gui.transient import TransientDialog
from etlayout.io.project import load_project, save_project
from etlayout.models import Diagnostic
from etlayout.simulation.runner import run_project


@pytest.fixture
def transient_window(qtbot, tmp_path):
    project = make_example("E17", tmp_path / "inputs")
    result = run_project(project)
    assert result.converged
    window = MainWindow(project, result, database_path=tmp_path / "materials.sqlite3", recover=False)
    window.autosave_timer.stop()
    window.recovery_path = tmp_path / "recovery.etl"
    window.error = lambda title, message: pytest.fail(f"{title}: {message}")
    qtbot.addWidget(window, before_close_func=lambda w: (w.cancel_geometry(), setattr(w, "dirty", False)))
    window.resize(1280, 800)
    window.show()
    qtbot.waitUntil(lambda: window.prepare_job is None, timeout=30000)
    return window


def test_time_selection_updates_map_sampler_metrics_and_exports_without_mutating_run(transient_window, tmp_path, qtbot):
    window = transient_window
    run = window.result
    original = deepcopy(run.fields)
    window.field_combo.setCurrentIndex(window.field_combo.findData("temperature"))
    window.time_navigation.slider.setValue(0)
    frame = run.transient.snapshots[0]
    assert window.active_view_result() is frame
    assert window.view.result is frame
    assert window.display_result is None  # a time slice is a physical result
    assert "Selected time: 0 s" in window.guided.result_metrics.text()
    assert f"Selected-time peak temperature: {frame.metrics['temperature_max']:.1f} K" in window.guided.result_metrics.text()
    layer = window.field_layer.currentData()
    point = run.mesh.centroids[0]
    assert window.get_sampler().sample(layer, "temperature", np.asarray([point]))[0] == pytest.approx(300)
    assert window.displayed_provenance()["time_s"] == 0.
    assert "t = 0 s" in window.result_status.text()
    assert window.field_hint.text().startswith("t = 0 s · ")
    assert window.field_hint.toolTip().startswith("t = 0 s · ")
    assert window.time_navigation.isVisible()
    assert not window.analysis_dock.isVisible()
    # Native styles and display scaling change the available logical pixels.
    # Protect usable fitted fields and time controls instead of a pixel budget.
    view = window.view
    canvas = window.guided.canvas_tabs.currentWidget()
    assert view.viewport().height() > canvas.height() / 2
    window.fit_result_layer()
    support = view.mapFromScene(view.overlay_item.sceneBoundingRect()).boundingRect()
    assert view.viewport().rect().contains(support)
    navigation = window.time_navigation
    for kind in (QPushButton, QLineEdit, QSlider):
        for control in navigation.findChildren(kind):
            assert control.isVisible() and control.accessibleName()
            assert control.visibleRegion().contains(control.rect())
    window.time_navigation.slider.setValue(5)
    selected = window.active_view_result()
    assert selected is run.transient.snapshots[5]
    assert selected.metrics["time_s"] == pytest.approx(250e-6)
    assert "Selected time: 250 µs" in window.guided.result_metrics.text()
    assert f"Selected-time peak temperature: {selected.metrics['temperature_max']:.1f} K" in window.guided.result_metrics.text()
    selected_time = "t = 250 µs · "
    assert window.field_hint.text().startswith(selected_time)
    assert window.field_hint.toolTip().startswith(selected_time)
    assert window.get_sampler().sample(layer, "temperature", np.asarray([point]))[0] > 300
    provenance = window.displayed_provenance()
    viewport_width, viewport_height = view.viewport().width(), view.viewport().height()
    panels = window.guided.panels
    panels.maximize_action.trigger()
    qtbot.waitUntil(lambda: view.viewport().width() > viewport_width
                    and view.viewport().height() >= viewport_height)
    assert not any(panels.visible(name) for name in panels.widgets)
    assert window.active_view_result() is selected and view.result is selected and window.result is run
    assert window.displayed_provenance() == provenance
    panels.maximize_action.trigger()
    qtbot.waitUntil(lambda: panels.visible("left") and panels.visible("right"))
    assert window.active_view_result() is selected and view.result is selected and window.result is run
    assert window.displayed_provenance() == provenance
    state = window.capture_view_state()
    window.time_navigation.slider.setValue(0)
    window.restore_view_state(state)
    assert window.active_view_result() is selected
    assert window.result is run
    assert window.field_hint.text().startswith(selected_time)
    assert window.field_hint.toolTip().startswith(selected_time)
    for name, fields in original.items():
        for key, values in fields.items():
            np.testing.assert_array_equal(values, run.fields[name][key])
    save_project(window.project, tmp_path / "full.etl", window.result)
    _, loaded = load_project(tmp_path / "full.etl")
    assert len(loaded.transient.snapshots) == len(run.transient.snapshots)
    np.testing.assert_array_equal(loaded.fields[layer]["temperature"], run.fields[layer]["temperature"])


def test_history_canvas_and_operation_changes_clear_time_state(transient_window):
    window = transient_window
    window.show_analysis_page(window.time_history)
    assert window.guided.canvas_tabs.currentWidget() is window.time_history
    assert window.time_history.isVisible()
    assert not window.display_dock.isVisible()
    assert not window.analysis_dock.isVisible()
    assert window.time_history.height() >= 350
    assert window.time_history.result is window.result
    window.time_navigation.slider.setValue(1)
    assert window.time_history.cursor.value() == window.result.transient.times[1]
    window.guided.analysis.setCurrentIndex(window.guided.analysis.findData(window.project.solver.mode))
    assert not window.project.transient.enabled
    assert window.result is None
    assert not window.time_navigation.isVisible()
    assert window.time_history.result is None


def test_incomplete_run_keeps_failure_status_and_whole_run_metrics_when_browsing(transient_window):
    window = transient_window
    window.result.converged = False
    window.result.transient.completed = False
    window.result.elapsed_seconds = 12.345
    window.result.diagnostics.append(Diagnostic("error", "Transient step did not converge", "transient_test_failure"))
    window.render_result(window.result)
    window.time_navigation.slider.setValue(0)
    assert window.view_source_result().converged
    assert "INCOMPLETE" in window.result_status.text()
    assert "12.345 s" in window.result_status.text()
    assert window.displayed_provenance()["transient_completed"] is False
    labels = [window.metrics_table.item(i, 0).text() for i in range(window.metrics_table.rowCount())]
    assert "Transient input energy (J)" in labels
    assert "Whole-run peak" in window.key_metrics.text()
    assert any("did not converge" in window.diagnostics.topLevelItem(i).text(2)
               for i in range(window.diagnostics.topLevelItemCount()))


def test_pulse_editor_validates_and_cancel_does_not_mutate(qtbot, tmp_path):
    project = make_example("E17", tmp_path)
    before = deepcopy(project.to_dict())
    dialog = TransientDialog(project)
    qtbot.addWidget(dialog)
    dialog.wave_inputs["width"].setText("-1")
    dialog.accept()
    assert dialog.result() == 0
    assert "positive" in dialog.error.text()
    dialog.wave_inputs["width"].setText("1e-4")
    dialog.wave_inputs["high"].setText("0.03")
    dialog.time_inputs["stop_time"].setText("0.001")
    dialog.accept()
    assert dialog.result() == 1
    assert dialog.project.transient.drives[0].waveform.high == .03
    assert dialog.project.transient.stop_time == .001
    assert project.to_dict() == before


def test_pulse_editor_piecewise_and_duplicate_target_validation(qtbot, tmp_path):
    project = make_example("E17", tmp_path)
    dialog = TransientDialog(project)
    qtbot.addWidget(dialog)
    dialog.kind.setCurrentIndex(dialog.kind.findData("piecewise_linear"))
    dialog.piecewise_table.setRowCount(3)
    unit = dialog.target_units[dialog.target.currentData()]
    for row, pair in enumerate((("0", "0"), ("1 ms", f"10 m{unit}"), ("2 ms", "0"))):
        for column, text in enumerate(pair):
            dialog.piecewise_table.setItem(row, column, QTableWidgetItem(text))
    assert dialog.apply_drive()
    assert dialog.drives[0].waveform.times == [0, .001, .002]
    dialog.add_drive()
    dialog.target.setCurrentIndex(dialog.target.findData("heat_sources.heater.value"))
    assert not dialog.apply_drive()
    assert "already" in dialog.error.text()
