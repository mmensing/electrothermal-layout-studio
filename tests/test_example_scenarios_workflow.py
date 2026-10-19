# SPDX-License-Identifier: MPL-2.0
"""Scenario commands reuse the document's existing draft/job transition guards."""
import pytest
from gui_test_helpers import _answer_dialog, _click, _fill, _idle
from gui_test_helpers import guided_window as guided_window
from PySide6.QtWidgets import QInputDialog

from etlayout.example_scenarios import scenarios

pytestmark = pytest.mark.desktop


def test_operating_scenario_opens_complete_editable_project_without_solve(guided_window, qtbot):
    window = guided_window
    window.load_example("E26", "thermal_demo")
    _idle(window, qtbot)
    assert window.project.power.ports[-1].value == 1.25
    assert window.project.temperature_limits[0].maximum_temperature == 423.15
    assert window.geometry is not None and window.mesh is None and window.result is None
    assert window.guided.registry.available("file.example_scenario")
    assert window.guided.actions["file.example_scenario"].isEnabled()


@pytest.mark.parametrize("answer", ["Stay", "Discard"])
def test_named_scenario_resolves_the_existing_inspector_draft(guided_window, qtbot, monkeypatch, answer):
    window = guided_window
    window.load_example("E26")
    _idle(window, qtbot)
    _click(window, window.guided.stage_buttons["layers"])
    _fill(window, "thickness", "3.5")
    project, geometry, editor = window.project, window.geometry, window.guided.editor
    before = project.to_dict()
    scenario = next(item for item in scenarios(project.name) if item.id == "thermal_demo")
    label = scenario.title
    monkeypatch.setattr(QInputDialog, "getItem", lambda *args: (label, True))
    _answer_dialog(answer)
    window.guided.execute("file.example_scenario")
    _idle(window, qtbot)
    assert project.to_dict() == before
    if answer == "Stay":
        assert window.project is project and window.geometry is geometry and window.guided.editor is editor
        assert editor.has_changes()
        _click(window, editor.cancel_button)
    else:
        assert window.project.power.ports[-1].value == 1.25
        assert not window.dirty and not window.result and not window.mesh


def test_scenario_command_disabled_during_real_mesh_job(guided_window, qtbot):
    window = guided_window
    original = window.project
    window.guided.execute("simulation.mesh")
    assert window.job is not None
    assert not window.guided.registry.available("file.example_scenario")
    assert not window.guided.actions["file.example_scenario"].isEnabled()
    window.guided.execute("file.example_scenario")
    assert window.project is original
    _idle(window, qtbot)
    assert window.mesh is not None and window.guided.registry.available("file.example_scenario")
