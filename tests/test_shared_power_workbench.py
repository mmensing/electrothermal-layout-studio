# SPDX-License-Identifier: MPL-2.0
"""Shared-study edits and actual workers retain one authoritative document."""
from copy import deepcopy

import pytest
from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import QApplication, QMessageBox, QWidget
from test_power_optimization import example

from etlayout.examples import make_example
from etlayout.gui.power_workbench import PowerWorkbench
from etlayout.io.project import configuration_hash, physics_hash, project_from_dict
from etlayout.models import MeasurementSpec, SupportSelector
from etlayout.simulation.execution import ExecutionSettings


class Document:
    """Exercise the embedding transaction protocol, including replacement on undo."""
    def __init__(self, project):
        self.project = project
        self.history = []

    def transact(self, label, callback):
        before = deepcopy(self.project)
        candidate = deepcopy(self.project)
        callback(candidate)
        self.project = project_from_dict(candidate.to_dict())
        self.history.append((label, before))

    def undo(self):
        _, self.project = self.history.pop()


def embedded(qtbot, project):
    document = Document(project)
    parent = QWidget()
    qtbot.addWidget(parent)
    panel = PowerWorkbench(project, parent=parent, document=document,
                           execution=ExecutionSettings(cpu_threads=1, study_workers=1))
    panel._test_parent = parent
    return document, panel


def test_embedded_panel_reads_live_document_and_undo_replacements(qtbot, tmp_path):
    project = make_example("E1", tmp_path / "input")
    document, panel = embedded(qtbot, project)
    assert panel.project is document.project and panel._standalone_project is None
    assert panel.windowType() == Qt.WindowType.Widget
    assert panel.buttons.isHidden()
    document.transact("Add measurement", lambda p: p.power.measurements.append(
        MeasurementSpec("loss", "joule_density", SupportSelector(layers=["metal"]), "integral")))
    panel.refresh_document()
    assert panel.project is document.project and panel.setup_table.records[0]["id"] == "loss"
    document.undo()
    panel.refresh_document()
    assert not panel.setup_table.records
    assert panel.project is document.project


def test_reference_draft_apply_discard_and_undo_are_explicit(qtbot, tmp_path):
    document, panel = embedded(qtbot, make_example("E1", tmp_path / "input"))
    panel.reference_temperature.setText("310 K")
    assert document.project.power.thermal_reference_temperature is None
    assert panel.has_pending_reference()
    assert not panel.resolve_pending("stay")
    assert panel.resolve_pending("apply")
    assert document.project.power.thermal_reference_temperature == 310
    assert [label for label, _ in document.history] == ["Edit reporting reference"]
    assert not panel.has_pending_reference()
    panel.reference_temperature.setText("325")
    assert panel.resolve_pending("discard")
    assert document.project.power.thermal_reference_temperature == 310
    assert panel.reference_temperature.text() == "310.0"
    document.undo()
    panel.refresh_document()
    assert panel.reference_temperature.text() == ""


def test_reference_conflict_and_invalid_draft_cannot_change_document(qtbot, tmp_path):
    document, panel = embedded(qtbot, make_example("E1", tmp_path / "input"))
    panel.reference_temperature.setText("-1 K")
    assert not panel.apply_reference()
    assert not document.history and "above zero" in panel.status.text()
    panel.reference_temperature.setText("320 K")
    document.transact("External reference edit", lambda p: setattr(p.power, "thermal_reference_temperature", 330.))
    panel.refresh_document()
    assert panel.reference_temperature.text() == "320 K"
    assert not panel.apply_reference()
    assert document.project.power.thermal_reference_temperature == 330.
    assert "shared setup" in panel.status.text()
    assert panel.resolve_pending("discard")
    assert panel.reference_temperature.text() == "330.0"


def test_run_stay_prompt_does_not_silently_apply_reference(qtbot, tmp_path):
    document, panel = embedded(qtbot, make_example("E1", tmp_path / "input"))
    panel.reference_temperature.setText("320")

    def stay():
        prompt = QApplication.activeModalWidget()
        assert isinstance(prompt, QMessageBox)
        next(button for button in prompt.buttons() if button.text() == "Stay").click()

    QTimer.singleShot(0, stay)
    panel.start("power", {})
    assert panel.job is None
    assert document.project.power.thermal_reference_temperature is None
    assert panel.has_pending_reference()


def test_shared_worker_emits_actual_result_and_freezes_its_source(qtbot, tmp_path):
    project = make_example("E1", tmp_path / "input")
    project.power.measurements.append(MeasurementSpec("loss", "joule_density", SupportSelector(layers=["metal"]), "integral"))
    document, panel = embedded(qtbot, project)
    expected_hash = physics_hash(document.project)
    events, results = [], []
    panel.busy_changed.connect(events.append)
    panel.result_ready.connect(results.append)
    panel.start("power", {})
    assert panel.job is not None and events == [True]
    document.transact("Change working input", lambda p: setattr(p.terminals[-1], "value", .03))
    qtbot.waitUntil(lambda: panel.job is None, timeout=90000)
    assert events == [True, False]
    assert len(results) == 1 and results[0].converged
    assert physics_hash(results[0].configuration) == expected_hash
    assert physics_hash(document.project) != expected_hash
    assert "earlier setup" in panel.status.text()
    assert panel.analysis_tables["measurements"].records[0]["value"] > 0
    panel.bind_result(results[0])
    assert panel.result is results[0] and "historical" in panel.status.text()


def test_typed_routing_generates_and_commits_real_candidate_once(qtbot, tmp_path):
    project = example(tmp_path)
    document, panel = embedded(qtbot, project)
    original_hash = configuration_hash(document.project)
    width = panel._routing_inputs["bus.width"][0]
    width.setText("4 µm")
    assert panel.candidate_parameters()["bus.width"] == pytest.approx(4e-6)
    panel.routing_output.setText(str(tmp_path / "guided-candidate.oas"))
    panel.generate_candidate()
    qtbot.waitUntil(lambda: panel.job is None, timeout=90000)
    assert panel.candidate and panel.candidate["report"]["valid"], panel.status.text()
    assert configuration_hash(document.project) == original_hash
    panel.use_candidate()
    assert len(document.history) == 1
    assert document.history[0][0] == "Use generated routing candidate"
    assert document.project.layout.path.endswith("guided-candidate.oas")
    assert document.project.power.recipes[0].parameters["width"] == pytest.approx(4e-6)
    document.undo()
    panel.refresh_document()
    assert configuration_hash(document.project) == original_hash


def test_candidate_cannot_overwrite_newer_shared_edits(qtbot, tmp_path):
    document, panel = embedded(qtbot, example(tmp_path))
    panel.routing_output.setText(str(tmp_path / "stale-candidate.oas"))
    panel.generate_candidate()
    qtbot.waitUntil(lambda: panel.job is None, timeout=90000)
    assert panel.candidate and panel.candidate["report"]["valid"], panel.status.text()
    document.transact("Rename working project", lambda p: setattr(p, "name", "Keep this newer edit"))
    panel.use_candidate()
    assert document.project.name == "Keep this newer edit"
    assert len(document.history) == 1
    assert "Regenerate" in panel.status.text()


def test_shared_worker_cancel_releases_busy_state(qtbot, tmp_path):
    document, panel = embedded(qtbot, make_example("E1", tmp_path / "input"))
    events = []
    panel.busy_changed.connect(events.append)
    before = configuration_hash(document.project)
    panel.start("power", {})
    job = panel.job
    assert job is not None
    panel.cancel_job()
    assert panel.job is None and not job.process.is_alive()
    assert events == [True, False]
    assert configuration_hash(document.project) == before
    assert "Cancelled" in panel.status.text()


def test_operating_scenario_editor_commits_same_shared_experiment(qtbot, tmp_path):
    from etlayout.gui.forms import ModelDialog

    document, panel = embedded(qtbot, example(tmp_path))
    assert panel.scenario_table.records
    panel.scenario_table.selectRow(0)
    selected = panel.scenario_table.selected_record()["id"]

    def edit():
        dialog = QApplication.activeModalWidget()
        assert isinstance(dialog, ModelDialog)
        dialog.widgets["compliance_voltage"].setText("85 V")
        dialog.accept()

    QTimer.singleShot(0, edit)
    panel.edit_scenario()
    changed = next(scenario for scenario in document.project.power.experiments[0].scenarios if scenario.id == selected)
    assert changed.compliance_voltage == 85.
    assert document.history[-1][0] == "Edit operating scenario " + selected
    assert panel.scenario_table.records[0]["compliance_voltage"] == 85.


def test_hidden_advanced_parameters_cannot_override_typed_values(qtbot, tmp_path):
    _, panel = embedded(qtbot, example(tmp_path))
    panel._routing_inputs["bus.width"][0].setText("4 µm")
    panel.routing_parameters.setText('{"bus.width": 0.000008}')
    assert panel.candidate_parameters()["bus.width"] == pytest.approx(4e-6)
    panel.routing_advanced.setChecked(True)
    assert panel.candidate_parameters()["bus.width"] == pytest.approx(8e-6)
