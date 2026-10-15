# SPDX-License-Identifier: MPL-2.0
"""Existing browser and dialogs expose physical ports and complete references."""
import json
from copy import deepcopy

import pytest
from gui_test_helpers import _idle
from gui_test_helpers import guided_window as guided_window
from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import QApplication, QDialog, QMenu

from etlayout.cli.main import main
from etlayout.example_catalog import example_info
from etlayout.examples import make_example
from etlayout.io.project import physics_hash
from etlayout.power_examples import FAMILIES, metadata
from etlayout.simulation.runner import run_project
from etlayout.workflow import STAGES


@pytest.mark.parametrize("name", FAMILIES)
def test_reference_browser_opens_complete_model_and_real_port_sweep(guided_window, qtbot, name):
    window = guided_window
    menu = next(menu for menu in window.findChildren(QMenu) if menu.title() == "Ready-to-run examples")
    action = next(action for action in menu.actions() if action.text().startswith(example_info(name).id + " ·"))
    assert "nominal" in action.toolTip() and "Synthetic" in action.toolTip()
    action.trigger()
    _idle(window, qtbot)
    assert window.project.name == name and window.project.solver.mode == "electrothermal"
    assert window.geometry is not None and window.guided.active_layer.currentData() == example_info(name).layer
    assert window.mesh is None and not window.run_history  # opening does not simulate
    assert window.project.interfaces and window.project.power.measurements and window.project.power.ports
    applied = window.project.to_dict()
    for stage in STAGES:
        assert window.guided.navigate(stage.key)
        QApplication.processEvents()
        assert window.guided.stage == stage.key and window.guided.inspector.isVisible()
        assert window.guided.primary.isEnabled() == (stage.key != "results")
        if stage.key == "run":
            assert not window.guided.cancel_run.isEnabled() and not window.guided.view_run.isEnabled()
        assert window.project.to_dict() == applied and not window.dirty
        assert not window.job and window.mesh is None
    window.guided.navigate("conditions")
    assert window.guided.condition_kind.currentData() == "ports"
    drain = next(port for port in window.project.power.ports if port.id == "drain")
    labels = [window.guided.object_list.item(index).text() for index in range(window.guided.object_list.count())]
    assert f"drain · current · {drain.value:g} A" in labels
    window.guided.condition_kind.setCurrentIndex(window.guided.condition_kind.findData("terminals"))
    owned = {landing.terminal_id for port in window.project.power.ports for landing in port.landings}
    listed = {window.guided.object_list.item(index).data(Qt.ItemDataRole.UserRole)[1]
              for index in range(window.guided.object_list.count())}
    assert not owned.intersection(listed)
    assert listed == {terminal.id for terminal in window.project.terminals if terminal.id not in owned}
    observed = []

    def inspect_dialog():
        dialog = QApplication.activeModalWidget()
        try:
            assert isinstance(dialog, QDialog)
            choices = dialog.widgets["parameter"]
            paths = [choices.itemData(index) for index in range(choices.count())]
            observed.extend(paths)
        finally:
            if dialog:
                dialog.reject()

    QTimer.singleShot(0, inspect_dialog)
    window.run_sweep_dialog()
    assert "power.ports.drain.value" in observed
    assert not any("landing" in path for path in observed)


@pytest.mark.parametrize("identifier", ["E20", "E21", "E22", "E23", "E24"])
def test_cli_generator_selects_preset_and_current_without_solving(identifier, tmp_path, capsys):
    assert main(["examples", str(tmp_path), "--name", identifier, "--preset", "compact", "--current", ".1"]) == 0
    report = json.loads(capsys.readouterr().out)
    from etlayout.io.project import load_project
    project, result = load_project(tmp_path / (report[0]["name"] + ".etl"))
    assert result is None and metadata(project)["requested_current_A"] == .1


def test_cli_rejects_ambiguous_global_preset_options(tmp_path, capsys):
    assert main(["examples", str(tmp_path), "--preset", "large"]) == 2
    assert "require --name" in capsys.readouterr().err


@pytest.mark.parametrize("identifier", ["E2", "E4", "E10", "E18"])
def test_introductory_browser_uses_catalog_view_without_changing_physics(guided_window, qtbot, identifier):
    window = guided_window
    info = example_info(identifier)
    window.load_example(info.name)
    _idle(window, qtbot)
    assert window.guided.active_layer.currentData() == info.layer
    assert window.field_layer.currentData() == info.layer
    assert window._preferred_field == info.field
    assert window.project.solver.mode == info.recommended_mode
    assert window.result is None and not window.dirty


@pytest.mark.parametrize("identifier", ["E2", "E10"])
def test_opened_reference_fields_and_history_preserve_user_view(guided_window, qtbot, tmp_path, identifier):
    window = guided_window
    info = example_info(identifier)
    project = make_example(identifier, tmp_path / "reference")
    result = run_project(project)
    assert result.converged
    expected = physics_hash(project)
    window.set_project(project, result)
    _idle(window, qtbot)
    assert (window.field_layer.currentData(), window.field_combo.currentData()) == (info.layer, info.field)
    assert window.view.overlay_item is not None
    # Refresh and recorded-run inspection must not reapply the opening defaults.
    window.field_combo.setCurrentIndex(window.field_combo.findData(""))
    window.refresh_project(preserve_view=True, refresh_geometry=False)
    window.inspect_result(deepcopy(result))
    _idle(window, qtbot)
    window.return_to_project()
    _idle(window, qtbot)
    assert window.field_combo.currentData() == ""
    assert window.field_layer.currentData() == info.layer
    assert window.result is result and physics_hash(window.project) == expected and not window.dirty
