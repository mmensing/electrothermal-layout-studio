# SPDX-License-Identifier: MPL-2.0
"""Regression coverage for time/run scope and the audited desktop transitions."""
import json
import os
from copy import deepcopy

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import numpy as np
import pytest
from PySide6.QtCore import QPoint, QRect, Qt
from PySide6.QtTest import QTest

from etlayout.examples import make_example
from etlayout.gui.export import export_html_report
from etlayout.gui.main_window import MainWindow
from etlayout.io.project import annotated_result, save_project
from etlayout.simulation.runner import run_project
from etlayout.visualization.history import history_field_limits
from etlayout.visualization.support import display_limits, field_support


@pytest.fixture
def window(qtbot, tmp_path):
    project = make_example("E17", tmp_path / "inputs")
    project.thresholds = {"transient_peak_temperature": 304., "temperature_max": 304.}
    result = run_project(project)
    widget = MainWindow(project, result, database_path=tmp_path / "materials.sqlite3", recover=False)
    widget.autosave_timer.stop()
    widget.recovery_path = tmp_path / "recovery.etl"
    qtbot.addWidget(widget, before_close_func=lambda w: (w.cancel_geometry(), setattr(w, "dirty", False)))
    widget.resize(1280, 800)
    widget.show()
    qtbot.waitUntil(lambda: widget.prepare_job is None, timeout=30000)
    widget.field_combo.setCurrentIndex(widget.field_combo.findData("temperature"))
    return widget


def warnings(window):
    return [window.diagnostics.topLevelItem(i).text(2) for i in range(window.diagnostics.topLevelItemCount())
            if "Engineering threshold exceeded:" in window.diagnostics.topLevelItem(i).text(2)]


def test_whole_run_threshold_survives_time_selection_and_report(window, tmp_path):
    window.time_navigation.slider.setValue(0)
    assert any("transient_peak_temperature" in text for text in warnings(window))
    assert not any("exceeded: temperature_max=" in text for text in warnings(window))
    frame = window.view_source_result()
    annotations = annotated_result(frame, window.project, run=window.result)
    assert annotations.metrics["transient_input_energy"] == frame.metrics["transient_input_energy"]
    assert "whole_run_metrics" not in frame.metrics
    report = tmp_path / "report.html"
    export_html_report(window.project, frame, window.view, report, window.legend.text(),
                       provenance=window.displayed_provenance(), run=window.result)
    assert "Engineering threshold exceeded: transient_peak_temperature" in report.read_text(encoding="utf-8")
    window.time_navigation.jump_to_peak()
    assert any("exceeded: temperature_max=" in text for text in warnings(window))


def test_mode_transitions_never_leave_electrical_transient(window):
    window.guided.analysis.setCurrentIndex(window.guided.analysis.findData("electrical"))
    assert not window.project.transient.enabled
    assert window.guided.analysis.currentData() == "electrical"
    window.guided.analysis.setCurrentIndex(window.guided.analysis.findData("electrothermal_transient"))
    assert window.project.solver.mode == "electrothermal"
    assert window.guided.analysis.currentData() == "electrothermal_transient"
    assert not window.thermal_joule.isEnabled()


def test_thermal_readiness_distinguishes_insulation_and_adiabatic_transient(window):
    window.project.solver.mode = "thermal"
    window.project.thermal_boundaries[0].kind = "insulated"
    window.update_workflow()
    assert "adiabatic" in window.guided.assessment.stages["conditions"].reason.lower()
    window.project.transient.enabled = False
    window.update_workflow()
    assert any(issue.blocking and "heat" in issue.message.lower() for issue in window.guided.assessment.issues)
    window.project.thermal_boundaries[0].kind = "coefficient"
    window.update_workflow()
    assert not any(issue.blocking and "heat" in issue.message.lower() for issue in window.guided.assessment.issues)


def test_history_range_and_probe_persist_through_navigation(window):
    window.history_range.setChecked(True)
    limits = window.view.field_limits
    point = window.result.mesh.centroids[0]
    window.probe(*point)
    window.time_history.scope_selector.setCurrentIndex(window.time_history.scope_selector.findData("probe"))
    window.time_navigation.slider.setValue(0)
    assert not window.view.field_manual_limits
    assert not window.displayed_provenance()["manual_limits"]
    assert window.time_history.probe == tuple(point)
    assert window.view.field_limits == limits
    assert np.isfinite(window.time_history.trace_values).all()
    window.time_history.time_selected.emit(float(window.result.transient.times[3]))
    assert window.time_navigation.slider.value() == 3


def test_compact_transient_appearance_actions_are_readable_and_reachable(window, qtbot):
    window.guided.navigate("results")
    window.display_tabs.setCurrentIndex(0)
    original_result, original_project = window.result, window.project.to_dict()
    assert window.history_range.isVisible()
    for width, height in ((1586, 992), (1280, 800), (1586, 992), (1280, 800)):
        window.resize(width, height)
        qtbot.wait(50)
        assert (window.width(), window.height()) == (width, height)
        viewport = window.guided.page_scroll.viewport()
        appearance = window.display_tabs.widget(0)
        for control in (window.colormap_combo, window.legend_scale.lock,
                        window.legend_scale.auto_button, window.legend_scale.minimum,
                        window.legend_scale.maximum, window.history_range):
            window.guided.page_scroll.ensureWidgetVisible(control)
            qtbot.wait(10)
            assert control.isVisibleTo(window)
            assert control.height() >= control.sizeHint().height(), control.accessibleName()
            bounds = QRect(control.mapTo(viewport, QPoint(0, 0)), control.size())
            assert viewport.rect().contains(bounds)
            assert appearance.rect().contains(QRect(control.mapTo(appearance, QPoint(0, 0)), control.size()))
    window.history_range.setChecked(True)
    window.guided.page_scroll.ensureWidgetVisible(window.legend_scale.lock)
    QTest.mouseClick(window.legend_scale.lock, Qt.MouseButton.LeftButton)
    window.legend_scale.minimum.setText("300")
    window.legend_scale.maximum.setText("400")
    window.update_overlay()
    assert window.view.field_manual_limits and window.view.field_limits == (300., 400.)
    window.guided.page_scroll.ensureWidgetVisible(window.legend_scale.auto_button)
    QTest.mouseClick(window.legend_scale.auto_button, Qt.MouseButton.LeftButton)
    assert not window.legend_scale.lock.isChecked() and not window.view.field_manual_limits
    assert window.history_range.isChecked()
    assert window.result is original_result and window.project.to_dict() == original_project and not window.dirty


@pytest.mark.parametrize("one_ulp", [False, True])
def test_saved_time_uniform_auto_range_is_stable_and_keeps_raw_fields(window, one_ulp):
    source = window.result
    original = deepcopy(source.fields)
    # An independent display fixture on the real run's immutable mesh. Only the
    # copied temperatures change; the working run retains its solved arrays.
    run = deepcopy(source)
    low, high = 301.62441496841313, 301.6244149684132 if one_ulp else 301.62441496841313
    for index, frame in enumerate([run, *run.transient.snapshots]):
        value = high if index % 2 else low
        for fields in [*frame.fields.values(), *frame.element_fields.values()]:
            if "temperature" in fields:
                data = fields["temperature"]
                fields["temperature"] = np.where(np.isfinite(data), value, data)
    window.result = run
    window.render_result(run)
    window.field_combo.setCurrentIndex(window.field_combo.findData("temperature"))
    layer = window.field_layer.currentData()
    expected = display_limits((low, high))
    assert history_field_limits(run, layer, "temperature") == (low, high)
    window.history_range.setChecked(True)
    for index in (0, len(run.transient.snapshots) - 1):
        window.time_navigation.slider.setValue(index)
        assert window.view.overlay_item is not None
        assert window.view.field_limits == expected
        assert not window.view.field_manual_limits
        assert not window.legend_scale.details["manual_limits"]
        assert not window.displayed_provenance()["manual_limits"]
        raw = field_support(window.active_view_result(), layer, "temperature").limits
        assert raw in [(low, low), (high, high)]
    for layer, fields in source.fields.items():
        for quantity, values in fields.items():
            np.testing.assert_array_equal(values, original[layer][quantity])


def test_difference_times_link_and_restore(window):
    run = window.result
    window.load_difference_result(run, "Same run")
    window.time_navigation.slider.setValue(0)
    assert window.active_view_result().configuration["visualization"]["comparison_time_s"] == 0.
    window.difference_link_time.setChecked(False)
    window.difference_time.slider.setValue(4)
    state = window.capture_view_state()
    window.difference_time.slider.setValue(1)
    window.restore_view_state(state)
    metadata = window.active_view_result().configuration["visualization"]
    assert metadata["current_time_s"] == 0.
    assert metadata["comparison_time_s"] == run.transient.times[4]


def test_legacy_preset_resets_operation_and_incompatible_targets_reject(window, tmp_path, monkeypatch):
    preset = tmp_path / "preset.json"
    preset.write_text(json.dumps({"solver": {"mode": "electrical"}}), encoding="utf-8")
    monkeypatch.setattr("etlayout.gui.main_window.QFileDialog.getOpenFileName", lambda *a: (str(preset), ""))
    window.load_preset()
    assert window.project.solver.mode == "electrical"
    assert not window.project.transient.enabled
    preset.write_text(json.dumps({"transient": {"enabled": True, "drives": [{"target": "heat_sources.missing.value", "waveform": {}}]}}), encoding="utf-8")
    errors = []
    window.error = lambda title, error: errors.append(str(error))
    window.load_preset()
    assert errors
    assert not window.project.transient.enabled


def test_saved_start_pending_or_failed_never_reverts_to_uniform(window, tmp_path, monkeypatch):
    archive = tmp_path / "source.etl"
    save_project(window.project, archive, window.result)
    monkeypatch.setattr("etlayout.gui.main_window.QFileDialog.getOpenFileName", lambda *a: (str(archive), ""))
    monkeypatch.setattr("etlayout.gui.main_window.QInputDialog.getText", lambda *a, **kw: ("1 ms", True))
    calls = []
    window.start_job = lambda operation, argument: calls.append((operation, argument))
    window.run_saved_state(False)
    assert calls and calls[0][1]["initial_result"] is not None
    assert window.project.transient.initialization == "saved"
    errors = []
    window.error = lambda title, error: errors.append(str(error))
    window.run()
    assert len(calls) == 1 and errors


def test_resume_accepts_exported_frame_with_cumulative_energy(window, tmp_path, monkeypatch):
    archive = tmp_path / "snapshot.etl"
    frame = window.result.transient.snapshots[2]
    save_project(window.project, archive, frame)
    monkeypatch.setattr("etlayout.gui.main_window.QFileDialog.getOpenFileName", lambda *a: (str(archive), ""))
    monkeypatch.setattr("etlayout.gui.main_window.QInputDialog.getText", lambda *a, **kw: ("1 ms", True))
    calls, errors = [], []
    window.start_job = lambda operation, argument: calls.append((operation, argument))
    window.error = lambda title, error: errors.append(str(error))
    window.run_saved_state(True)
    assert not errors and len(calls) == 1
    assert calls[0][1]["resume"]
    assert calls[0][1]["initial_result"].metrics["time_s"] == frame.metrics["time_s"]
    assert window.project.transient.initialization == "resume"
