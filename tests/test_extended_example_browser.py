# SPDX-License-Identifier: MPL-2.0
"""Every extended reference opens through the existing, categorized desktop flow."""
import pytest
from gui_test_helpers import _answer_dialog, _click, _fill, _idle
from gui_test_helpers import guided_window as guided_window
from PySide6.QtWidgets import QApplication, QMenu

from etlayout.example_catalog import EXAMPLE_CATALOG, example_info
from etlayout.examples import EXTENDED_EXAMPLES
from etlayout.workflow import STAGES

pytestmark = pytest.mark.desktop


@pytest.mark.parametrize("name", EXTENDED_EXAMPLES)
def test_extended_reference_opens_complete_setup_without_starting_a_solve(guided_window, qtbot, name):
    window = guided_window
    root = next(menu for menu in window.findChildren(QMenu) if menu.title() == "Ready-to-run examples")
    categories = root.findChildren(QMenu)
    actions = [action for menu in categories for action in menu.actions() if not action.isSeparator()]
    labels = [action.text() for action in actions]
    assert len(labels) == len(set(labels)) == len(EXAMPLE_CATALOG)
    assert {menu.title() for menu in categories} == {info.category for info in EXAMPLE_CATALOG.values()}
    info = example_info(name)
    category = next(menu for menu in categories if menu.title() == info.category)
    action = next(action for action in category.actions() if action.text().startswith(info.id + " ·"))
    assert info.physical_scope in action.toolTip()
    assert info.evidence_level in action.toolTip()
    assert "not mesh verification" in action.toolTip()
    action.trigger()
    _idle(window, qtbot)
    assert window.project.name == name
    assert window.project.solver.mode == info.recommended_mode
    assert window.guided.active_layer.currentData() == window.field_layer.currentData() == info.layer
    assert window._preferred_field == info.field
    assert window.geometry is not None and window.mesh is None and window.result is None
    assert window.project.power.measurements and window.project.sweeps
    assert window.project.layout.sha256 and not window.run_history and not window.job
    original = window.project.to_dict()
    for stage in STAGES:
        assert window.guided.navigate(stage.key)
        QApplication.processEvents()
        assert window.project.to_dict() == original
        assert window.guided.inspector.isVisible()
        assert not window.dirty and not window.job and window.mesh is None


def test_categorized_menu_registers_stable_leaf_commands(guided_window):
    window = guided_window
    guided = window.guided
    root = next(menu for menu in window.findChildren(QMenu) if menu.title() == "Ready-to-run examples")
    expected = {"example." + info.id for info in EXAMPLE_CATALOG.values()}
    registered = {command.key for command in guided.registry if command.key.startswith("example.")}
    assert registered == expected
    for category in root.actions():
        assert category.menu() is not None
        assert "example." + category.text() not in registered
        for action in category.menu().actions():
            key = "example." + action.text().split(" · ", 1)[0]
            assert guided.actions[key] is action
            assert action.objectName() == key
            assert guided.registry.available(key) and action.isEnabled()


def test_stable_example_command_dispatches_configured_reference(guided_window, qtbot):
    window = guided_window
    window.guided.execute("example.E25")
    _idle(window, qtbot)
    assert window.project.name == "E25_stepped_width_series"
    assert window.geometry is not None and window.job is None
    assert window.project.power.ports and window.project.power.measurements


@pytest.mark.parametrize("answer", ["Discard", "Stay"])
def test_example_command_resolves_inspector_draft(guided_window, qtbot, answer):
    window = guided_window
    guided = window.guided
    _click(window, guided.stage_buttons["layers"])
    project, geometry = window.project, window.geometry
    original = project.to_dict()
    revision = guided.document.revision
    _fill(window, "thickness", "3.5")
    editor = guided.editor
    assert editor.has_changes() and project.to_dict() == original
    _answer_dialog(answer)
    guided.execute("example.E25")
    _idle(window, qtbot)
    if answer == "Stay":
        assert window.project is project and window.geometry is geometry
        assert window.project.to_dict() == original
        assert guided.stage == "layers" and guided.editor is editor
        assert editor.has_changes() and editor.form.widgets["thickness"].text() == "3.5"
        assert guided.document.revision == revision
        _click(window, editor.cancel_button)
    else:
        assert project.to_dict() == original
        assert window.project.name == "E25_stepped_width_series"
        assert guided.editor is None or not guided.editor.has_changes()
        assert not window.dirty


def test_example_commands_disabled_during_real_mesh_job_and_restored(guided_window, qtbot):
    window = guided_window
    guided = window.guided
    project = window.project
    original = project.to_dict()
    example_actions = {key: action for key, action in guided.actions.items() if key.startswith("example.")}
    guided.execute("simulation.mesh")
    assert window.job is not None
    assert all(not action.isEnabled() and not guided.registry.available(key)
               for key, action in example_actions.items())
    guided.execute("example.E25")
    assert window.project is project and project.to_dict() == original
    assert "Finish or cancel" in guided.next_reason.text()
    _idle(window, qtbot)
    assert window.mesh is not None
    assert all(action.isEnabled() and guided.registry.available(key)
               for key, action in example_actions.items())
    guided.execute("example.E25")
    _idle(window, qtbot)
    assert window.project.name == "E25_stepped_width_series"
