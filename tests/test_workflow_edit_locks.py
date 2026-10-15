# SPDX-License-Identifier: MPL-2.0
"""Real workers and recorded results preserve shared document edit boundaries."""
from __future__ import annotations

from copy import deepcopy

import pytest
from PySide6.QtCore import Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QPushButton
from test_guided_workflow import guided_window as guided_window

from etlayout.io.project import configuration_hash, physics_hash


def idle(window, qtbot):
    qtbot.waitUntil(lambda: not window.job and not window.prepare_job and not window.hierarchy_jobs
                   and not (window.guided.power and window.guided.power.job), timeout=90000)
    QApplication.processEvents()


def expert_buttons(window):
    return window.panels["terminals"].findChildren(QPushButton)


@pytest.mark.parametrize("owner", ["main", "power"])
def test_shared_workers_exclude_each_other_and_lock_reparented_tables(guided_window, qtbot, owner):
    window = guided_window
    guide = window.guided
    guide.open_expert("terminals")
    guide.open_studies("analysis")
    power = guide.power
    before = configuration_hash(window.project)
    assert window.config_tabs.parentWidget() is not window.config_dock
    assert all(button.isEnabled() for button in expert_buttons(window))

    if owner == "main":
        window.start_job("solve")
        assert window.job is not None
        power.start("power", {})
        assert power.job is None
        assert "active computation" in power.status.text()
    else:
        power.start("power", {})
        assert power.job is not None
        window.start_job("solve")
        assert window.job is None
        guide.navigate("run")
        assert not guide.primary.isEnabled()

    assert not window.config_tabs.isEnabled()
    assert all(not button.isEnabled() for button in expert_buttons(window))
    window.edit_object("terminals", window.project.terminals[0].id)
    assert QApplication.activeModalWidget() is None
    assert configuration_hash(window.project) == before
    idle(window, qtbot)
    assert window.result is not None and window.result.converged
    assert window.result.fields["metal"]["potential"].max() > 0
    assert physics_hash(window.result.configuration) == physics_hash(window.project)
    assert window.config_tabs.isEnabled()


def test_historical_run_locks_retained_expert_forms_and_direct_edit_routes(guided_window, qtbot):
    window = guided_window
    guide = window.guided
    window.start_job("solve")
    idle(window, qtbot)
    result = window.result
    assert result is not None and result.converged
    guide.open_studies("analysis")
    power = guide.power
    guide.document.transact("Change working source", lambda project: setattr(project.terminals[-1], "value", .03))
    working = deepcopy(window.project.to_dict())
    guide.open_expert("terminals")
    window.inspect_result(result)
    idle(window, qtbot)
    assert window.inspection_context is not None
    assert window.project.to_dict() == result.configuration
    guide.show_page(window.config_tabs, "Complete model")
    assert not window.config_tabs.isEnabled()
    assert all(not button.isEnabled() for button in expert_buttons(window))
    frozen = configuration_hash(window.project)
    for action in (lambda: window.edit_object("terminals", window.project.terminals[0].id),
                   lambda: window.remove_object("terminals"),
                   lambda: window.reorder_object("terminals", 1),
                   lambda: window.edit_settings("mesh"),
                   window.undo):
        action()
        assert QApplication.activeModalWidget() is None
        assert configuration_hash(window.project) == frozen
    # Even a previously retained embedded study must not run against the
    # document adapter's working project while inspecting an immutable run.
    power.start("power", {})
    assert power.job is None
    assert window.inspection_context["project"].to_dict() == working
    window.return_to_project()
    idle(window, qtbot)
    assert window.config_tabs.isEnabled()
    assert window.project.to_dict() == working


def test_mesh_invalidation_and_failed_rebuild_remove_old_triangles(guided_window, qtbot):
    window = guided_window
    guide = window.guided
    guide.navigate("mesh")
    window.start_job("mesh")
    idle(window, qtbot)
    assert window.mesh is not None
    window.mesh_toggle.setChecked(True)
    window.update_overlay()
    assert window.view.mesh_item is not None
    guide.document.transact("Reduce mesh element budget", lambda project: setattr(project.mesh, "max_elements", 1))
    assert window.mesh is None
    assert window.view.mesh_item is None
    # Exercise the visible toggle as well as the initial invalidation path.
    QTest.mouseClick(window.mesh_toggle, Qt.MouseButton.LeftButton)
    window.update_overlay()
    assert window.view.mesh_item is None
    window.start_job("mesh")
    idle(window, qtbot)
    assert guide.last_error
    assert window.mesh is None
    assert window.view.mesh_item is None
    assert "No current mesh" in guide.mesh_status.text()
