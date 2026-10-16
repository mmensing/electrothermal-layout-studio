# SPDX-License-Identifier: MPL-2.0
"""Every extended reference opens through the existing, categorized desktop flow."""
import pytest
from gui_test_helpers import _idle
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
