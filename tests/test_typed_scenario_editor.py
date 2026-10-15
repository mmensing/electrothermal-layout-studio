# SPDX-License-Identifier: MPL-2.0
"""Scenario rows preserve SI values and drive actual fixed-service calculations."""
from __future__ import annotations

from copy import deepcopy

import pytest
from PySide6.QtCore import Qt, QTimer
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QDialog
from test_shared_power_workbench import embedded

from etlayout.examples import make_example
from etlayout.gui.power_workbench import ScenarioDialog
from etlayout.models import OperatingScenario, OptimizationExperiment, ThermalBoundary
from etlayout.optimization import _scenario
from etlayout.simulation.runner import run_project


def setup(tmp_path):
    project = make_example("E1", tmp_path / "input")
    source = next(terminal.id for terminal in project.terminals if terminal.kind == "voltage")
    reference = next(terminal.id for terminal in project.terminals if terminal.kind == "ground")
    scenario = OperatingScenario("nominal", service_port=source, reference_port=reference,
                                 service_mode="fixed_voltage", required_current=1e-9)
    return project, scenario, f"terminals.{source}.value"


def test_typed_bias_ambient_and_advanced_settings_round_trip(qtbot, tmp_path):
    project, scenario, source = setup(tmp_path)
    project.thermal_boundaries.append(ThermalBoundary("backside", "metal", kind="coefficient"))
    advanced_path = f"terminals.{scenario.service_port}.name"
    scenario.settings = {source: .025, "thermal_boundaries.backside.ambient": 315., advanced_path: "Supply at corner"}
    saved = deepcopy(scenario.settings)
    dialog = ScenarioDialog(scenario, project)
    qtbot.addWidget(dialog)
    dialog.show()
    assert dialog.setting_table.rowCount() == 2
    assert not dialog.advanced_settings.isChecked()
    assert dialog.widgets["settings"].isHidden()
    dialog.setting_table.cellWidget(0, 1).setText("40 mV")
    dialog.setting_table.cellWidget(1, 1).setText("320 K")
    dialog.accept()
    assert dialog.result() == QDialog.DialogCode.Accepted
    assert dialog.model.settings == {source: .04, "thermal_boundaries.backside.ambient": 320., advanced_path: "Supply at corner"}
    assert scenario.settings == saved


@pytest.mark.parametrize("invalid", ["duplicate", "bad_units", "nonfinite", "advanced_duplicate", "unsupported", "overridden_source"])
def test_invalid_scenario_settings_cannot_partially_apply(qtbot, tmp_path, invalid):
    project, scenario, source = setup(tmp_path)
    scenario.settings = {source: .025}
    dialog = ScenarioDialog(scenario, project)
    qtbot.addWidget(dialog)
    dialog.show()
    if invalid == "duplicate":
        dialog.add_setting(source, .04)
    elif invalid == "bad_units":
        dialog.setting_table.cellWidget(0, 1).setText("15 K")
    elif invalid == "nonfinite":
        dialog.advanced_values.setPlainText('{"solver.initial_temperature": NaN}')
    elif invalid == "advanced_duplicate":
        dialog.advanced_values.setPlainText('{"' + source + '": 0.05}')
    elif invalid == "unsupported":
        dialog.advanced_values.setPlainText('{"mesh.max_size": 0.000001}')
    else:
        dialog.widgets["service_mode"].setCurrentIndex(dialog.widgets["service_mode"].findData("target_current"))
    dialog.accept()
    assert dialog.result() != QDialog.DialogCode.Accepted
    assert not dialog.errors["settings"].isHidden()
    assert dialog.model.settings == scenario.settings == {source: .025}


def test_add_remove_parameter_rows_restore_baseline_and_validate_service(qtbot, tmp_path):
    project, scenario, source = setup(tmp_path)
    scenario.settings = {source: .025}
    dialog = ScenarioDialog(scenario, project)
    qtbot.addWidget(dialog)
    dialog.show()
    QTest.mouseClick(dialog.remove_setting_button, Qt.MouseButton.LeftButton)
    assert dialog.setting_table.rowCount() == 0
    QTest.mouseClick(dialog.add_setting_button, Qt.MouseButton.LeftButton)
    assert dialog.setting_table.rowCount() == 1
    dialog.remove_setting()
    dialog.widgets["required_current"].setText("0")
    dialog.accept()
    assert dialog.result() != QDialog.DialogCode.Accepted
    assert not dialog.errors["required_current"].isHidden()
    dialog.widgets["required_current"].setText("1 mA")
    dialog.accept()
    assert dialog.result() == QDialog.DialogCode.Accepted
    assert dialog.model.settings == {}
    assert dialog.model.required_current == .001


def test_shared_scenario_apply_is_one_undoable_edit_and_calculates_requested_bias(qtbot, tmp_path):
    project, scenario, source = setup(tmp_path)
    project.power.experiments.append(OptimizationExperiment("study", scenarios=[scenario]))
    baseline = run_project(_scenario(project, scenario))
    document, panel = embedded(qtbot, project)
    panel.scenario_table.selectRow(0)
    expected = next(t.value for t in project.terminals if t.id == scenario.service_port) * 2

    def edit():
        dialog = QApplication.activeModalWidget()
        assert isinstance(dialog, ScenarioDialog)
        dialog.add_setting(source)
        entry = dialog.setting_table.cellWidget(0, 1)
        entry.selectAll()
        QTest.keyClicks(entry, f"{expected * 1000:g} mV")
        dialog.accept()
        assert dialog.result() == QDialog.DialogCode.Accepted

    QTimer.singleShot(0, edit)
    panel.edit_scenario()
    assert len(document.history) == 1
    applied = document.project.power.experiments[0].scenarios[0]
    assert applied.settings[source] == expected
    solved = run_project(_scenario(document.project, applied))
    assert solved.converged
    assert solved.terminals[scenario.service_port]["current"] == pytest.approx(
        2 * baseline.terminals[scenario.service_port]["current"], rel=1e-6)
    document.undo()
    assert document.project.power.experiments[0].scenarios[0].settings == {}
