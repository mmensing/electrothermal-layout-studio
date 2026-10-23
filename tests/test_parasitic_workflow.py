# SPDX-License-Identifier: MPL-2.0
"""Observable atomic setup, honest units and retirement of stale field evidence."""
import json
from copy import deepcopy

import pytest
from gui_test_helpers import _idle
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication, QDialog, QDialogButtonBox, QPushButton
from test_field_snapshot import closed_path_project

from etlayout.gui.main_window import MainWindow
from etlayout.gui.parasitic_workflow import FieldSetupDialog, ParasiticWorkflow
from etlayout.gui.power_workbench import PowerWorkbench
from etlayout.io.project import configuration_hash, physics_hash
from etlayout.models import PropertyModel
from etlayout.parasitic_examples import make_field_reference
from etlayout.power.field_snapshot import EPSILON_0, MU_0, build_field_snapshot
from etlayout.simulation.execution import ExecutionSettings


def _button(dialog, standard):
    return dialog.findChild(QDialogButtonBox).button(standard)


def test_cancel_leaves_no_material_loop_or_environment_edits_in_the_working_project(qtbot):
    project = closed_path_project()
    before = deepcopy(project.to_dict())
    dialog = FieldSetupDialog(project)
    qtbot.addWidget(dialog)
    dialog.show()
    dialog.epsilon.setText("7")
    dialog.materials.item(0,1).setText("3")
    dialog.segments.item(0,7).setText("4")
    dialog.declare_nonmagnetic()
    qtbot.mouseClick(_button(dialog, QDialogButtonBox.StandardButton.Cancel), Qt.MouseButton.LeftButton)
    assert dialog.result() == QDialog.DialogCode.Rejected and dialog.candidate is None
    assert project.to_dict() == before


def test_apply_commits_one_complete_candidate_in_si_and_preserves_dc_identity(qtbot):
    project = closed_path_project()
    before, old_physics, old_configuration = deepcopy(project.to_dict()), physics_hash(project), configuration_hash(project)
    dialog = FieldSetupDialog(project)
    qtbot.addWidget(dialog)
    dialog.show()
    dialog.epsilon.setText("4")
    dialog.materials.item(0,1).setText("4")
    dialog.materials.item(0,2).setText("1")
    dialog.materials.item(0,3).setText("Explicit synthetic εr=4, μr=1 modeling assumption")
    dialog.segments.item(0,1).setText("1000 nm")
    dialog.segments.item(0,7).setText("2000 nm")
    qtbot.mouseClick(_button(dialog, QDialogButtonBox.StandardButton.Apply), Qt.MouseButton.LeftButton)
    assert dialog.result() == QDialog.DialogCode.Accepted
    candidate = dialog.candidate
    assert candidate is not None and project.to_dict() == before
    assert candidate.materials["metal"].permittivity.value == pytest.approx(4*EPSILON_0)
    assert candidate.materials["metal"].permeability.value == pytest.approx(MU_0)
    assert candidate.parasitics["loops"][0]["segments"][0]["start_m"][0] == pytest.approx(1e-6)
    assert candidate.parasitics["loops"][0]["segments"][0]["width_m"] == pytest.approx(2e-6)
    assert len(build_field_snapshot(candidate, kind="loop_rl").loops[0].segments) == 4
    assert physics_hash(candidate) == old_physics and configuration_hash(candidate) != old_configuration


def test_unchanged_material_models_retain_their_declared_validity_and_temperature_laws(qtbot):
    project = closed_path_project()
    project.materials["metal"].permeability = PropertyModel(value=MU_0, minimum_temperature=250., maximum_temperature=350.)
    project.materials["metal"].permittivity = PropertyModel(kind="linear", value=2*EPSILON_0, alpha=1e-4,
        reference_temperature=310., minimum_temperature=260., maximum_temperature=400.)
    dialog = FieldSetupDialog(project)
    qtbot.addWidget(dialog)
    assert dialog.materials.item(0,1).text() == "model"
    candidate = dialog.collect()
    assert candidate.materials["metal"].permeability == project.materials["metal"].permeability
    assert candidate.materials["metal"].permittivity == project.materials["metal"].permittivity


@pytest.mark.parametrize("damage", ["missing_source", "negative_width", "open_return"])
def test_invalid_form_cannot_apply_partial_or_invalid_loop_settings(qtbot, damage):
    project = closed_path_project()
    before = deepcopy(project.to_dict())
    dialog = FieldSetupDialog(project)
    qtbot.addWidget(dialog)
    dialog.show()
    if damage == "missing_source":
        dialog.materials.item(0,3).setText("")
    elif damage == "negative_width":
        dialog.segments.item(0,7).setText("-2")
    else:
        dialog.segments.item(3,5).setText("2")
    qtbot.mouseClick(_button(dialog, QDialogButtonBox.StandardButton.Apply), Qt.MouseButton.LeftButton)
    assert dialog.isVisible() and dialog.candidate is None and dialog.errors.text()
    assert project.to_dict() == before


def test_calculation_choices_retire_stale_evidence_and_do_not_change_the_document(qtbot):
    project = closed_path_project()
    before = deepcopy(project.to_dict())
    view = ParasiticWorkflow()
    qtbot.addWidget(view)
    view.set_project(project)
    prepared = {"status": "prepared", "kind": "capacitance", "diagnostic": "Inputs only; no calculation"}
    for entry, text in ((view.temperature,"350 K"), (view.mesh_sizes,"120, 60, 30"),
                        (view.tolerance,"3"), (view.timeout,"120")):
        view.show_report(prepared)
        entry.setText(text)
        assert view.report is None and not view.details.toPlainText()
    view.kind.setCurrentIndex(view.kind.findData("loop_rl"))
    for entry, text in ((view.frequencies,"0, 10, 100"), (view.refinements,"1:1, 2:2, 4:3")):
        view.show_report(prepared)
        entry.setText(text)
        assert view.report is None
    assert project.to_dict() == before


def test_rl_readout_has_explicit_frequency_units_and_no_invented_dc_inductance(qtbot):
    view = ParasiticWorkflow()
    qtbot.addWidget(view)
    view.executable.setText("selected-capacitance-engine.exe")
    view.budget.setText("19000")
    view.kind.setCurrentIndex(view.kind.findData("loop_rl"))
    view.executable.setText("selected-loop-engine.exe")
    view.budget.setText("900")
    # Explicit arithmetic fixture checks display conversion, not engine fidelity.
    view.show_report({"status": "computed-unverified", "loop_ids": ["finite_loop"], "frequency_results": [
        {"frequency_Hz": 0., "R_ohm": [[.003]], "L_H": None},
        {"frequency_Hz": 10., "R_ohm": [[.003]], "L_H": [[2e-9]]}]})
    assert view.matrix.rowCount() == 2
    assert view.matrix.item(0,3).text() == "3" and view.matrix.item(0,4).text() == "Requires f > 0"
    assert view.matrix.item(1,4).text() == "2"
    assert view.matrix.horizontalHeaderItem(3).text() == "R (mΩ)"
    assert view.matrix.horizontalHeaderItem(4).text() == "L (nH)"
    view.kind.setCurrentIndex(view.kind.findData("capacitance"))
    assert view.executable.text() == "selected-capacitance-engine.exe" and view.budget.text() == "19000"
    assert view.report is None and not view.matrix.rowCount()
    view.kind.setCurrentIndex(view.kind.findData("loop_rl"))
    assert view.executable.text() == "selected-loop-engine.exe" and view.budget.text() == "900"


@pytest.mark.parametrize("status", ["prepared", "timeout"])
def test_noncalculation_reports_replace_all_earlier_numeric_evidence(qtbot,status):
    view = ParasiticWorkflow()
    qtbot.addWidget(view)
    # Arithmetic rendering fixture, not a claimed external calculation.
    view.show_report({"status":"computed-unverified","raw_matrix_F":[[1e-12]],"axis_ids":["body"]})
    assert view.matrix.rowCount() == 1
    view.show_report({"status":status,"diagnostic":"No verified field operator"})
    assert view.matrix.rowCount() == view.matrix.columnCount() == 0
    assert json.loads(view.details.toPlainText())["status"] == status


def test_shared_em_edits_and_undo_retire_evidence_without_discarding_mesh_or_retained_fields(
        qtbot,tmp_path,monkeypatch,e1_reference_solution):
    project,result = deepcopy(e1_reference_solution)
    window = MainWindow(project,result,database_path=tmp_path / "materials.sqlite3",recover=False)
    qtbot.addWidget(window)
    try:
        window.autosave_timer.stop()
        window.show()
        _idle(window,qtbot)
        window.open_power_workbench()
        panel = window.guided.power
        view = panel.parasitic_workflow
        retained,mesh = window.result,window.mesh
        original_physics = physics_hash(window.project)
        prepared = {"status":"prepared","diagnostic":"Inputs only; no numerical operator"}
        view.show_report(prepared)
        def configure(candidate):
            candidate.materials["copper"].permittivity = PropertyModel(value=4*EPSILON_0)
            candidate.parasitics = {"schema_version":1,"background_relative_permittivity":4.,
                "material_sources":{"copper":"Explicit synthetic dielectric assumption"}}
        assert window.guided.document.transact("Configure optional EM fields",configure)
        assert view.report is None and not view.details.toPlainText()
        assert window.result is retained and window.mesh is mesh and panel.result is retained
        assert physics_hash(window.project) == original_physics
        view.show_report(prepared)
        window.undo()
        assert view.report is None and not view.details.toPlainText()
        assert window.result is retained and window.mesh is mesh
        assert not window.project.parasitics and window.project.materials["copper"].permittivity is None
        window.dirty = False
        # A real worker failure (unconfigured environment) must reach the visible
        # workflow drawer, while the independent retained DC fields remain usable.
        failed_output = tmp_path / "failed-field-inputs"
        failed_output.mkdir()
        monkeypatch.setattr("etlayout.gui.power_workbench.QFileDialog.getExistingDirectory",lambda *_:str(failed_output))
        panel.run_parasitics(True)
        assert panel.job is not None
        qtbot.waitUntil(lambda:panel.job is None,timeout=30000)
        assert window.result is retained and window.mesh is mesh and view.report is None
        issues = window.guided.issue_table
        row = next(index for index in range(issues.rowCount()) if "Field extraction:" in issues.item(index,1).text())
        assert "Configure explicit parasitic settings" in issues.item(row,1).text()
        assert issues.item(row,1).toolTip()
        assert window.guided.drawer.currentIndex() == 0
        assert any(button.text() == "Open setup" for button in issues.cellWidget(row,3).findChildren(QPushButton))
        window.inspect_result(retained)
        _idle(window,qtbot)
        assert window.inspection_context is not None
        inspected = deepcopy(window.project.to_dict())
        def unexpected_dialog(*_args,**_kwargs):
            pytest.fail("Historical configuration must not open an editable field modal")
        monkeypatch.setattr("etlayout.gui.parasitic_workflow.FieldSetupDialog",unexpected_dialog)
        panel.configure_parasitics()
        assert "working setup" in panel.status.text()
        assert window.project.to_dict() == inspected
        window.return_to_project()
        _idle(window,qtbot)
    finally:
        from etlayout.app.main import _close_smoke_window
        _close_smoke_window(window,QApplication.instance())


@pytest.mark.parametrize("kind", ["capacitance", "loop_rl"])
def test_real_worker_prepares_supported_reference_without_claiming_a_solve_or_changing_setup(qtbot, tmp_path, monkeypatch, kind):
    project = make_field_reference(kind,tmp_path / "source")
    before = deepcopy(project.to_dict())
    window = PowerWorkbench(project,execution=ExecutionSettings(cpu_threads=1,study_workers=1))
    qtbot.addWidget(window)
    view = window.parasitic_workflow
    view.kind.setCurrentIndex(view.kind.findData(kind))
    output = tmp_path / "prepared-field-inputs"
    output.mkdir()
    monkeypatch.setattr("etlayout.gui.power_workbench.QFileDialog.getExistingDirectory",lambda *_:str(output))
    window.run_parasitics(True)
    assert window.job is not None and not view.isEnabled()
    qtbot.waitUntil(lambda:window.job is None,timeout=30000)
    assert view.report is not None,window.status.text()
    assert view.report["status"] == "prepared" and not view.report.get("mesh_verified",False)
    assert not view.report.get("accepted",False)
    assert window.tabs.currentWidget() is view
    snapshot = json.loads((output / "snapshot.json").read_text(encoding="utf-8"))
    assert snapshot["kind"] == kind and snapshot["source_context"]["physics_hash"] == physics_hash(project)
    assert json.loads((output / "field-report.json").read_text(encoding="utf-8")) == view.report
    assert project.to_dict() == before


def test_result_focus_folds_setup_and_names_conductors_without_losing_stable_axes(qtbot):
    view = ParasiticWorkflow()
    qtbot.addWidget(view)
    view.set_snapshot({"solids": [
        {"role": "conductor", "layer_id": "upper", "origin_id": "layer:upper", "component_id": "stable-upper"},
        {"role": "conductor", "layer_id": "lower", "origin_id": "layer:lower", "component_id": "stable-lower"}]})
    view.show_report({"status": "computed-unverified", "kind": "capacitance", "converged": True,
        "mesh_verified": True, "accepted": False, "axis_ids": ["stable-lower", "stable-upper"],
        "raw_matrix_F": [[2e-12, -1e-12], [-1e-12, 2e-12]]})
    assert not view.setup_group.isChecked() and view.setup_body.isHidden()
    assert view.matrix.horizontalHeaderItem(0).text() == "lower (pF)"
    assert view.matrix.horizontalHeaderItem(0).toolTip() == "stable-lower"
    assert view.matrix.item(0,1).text() == "-1"
    assert "not accepted" in view.summary.text()
    view.pages.setCurrentIndex(0)
    assert view.setup_group.isChecked() and not view.setup_body.isHidden()
    assert view.engine_form.isRowVisible(view.mesh_sizes)
    assert not view.engine_form.isRowVisible(view.frequencies)
    view.kind.setCurrentIndex(view.kind.findData("loop_rl"))
    assert not view.engine_form.isRowVisible(view.mesh_sizes)
    assert view.engine_form.isRowVisible(view.frequencies)
