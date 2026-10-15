# SPDX-License-Identifier: MPL-2.0
"""Focused applied-input, draft recovery and real worker workflow regressions."""
from copy import deepcopy

import pytest
from gui_test_helpers import _click, _fill, _idle
from gui_test_helpers import guided_window as guided_window
from gui_test_helpers import solved_e1 as solved_e1

from etlayout.io.project import physics_hash


@pytest.mark.parametrize("stage,field,value,primary", [
    ("layers", "thickness", "3.5", "Define connections"),
    ("mesh", "max_size", "4", "Generate / update mesh"),
])
def test_discard_restores_applied_values_and_usable_stage_action(guided_window, qtbot, stage, field, value, primary):
    window, guided = guided_window, guided_window.guided
    _click(window, guided.stage_buttons[stage])
    original = window.project.to_dict()
    _fill(window, field, value)
    assert guided.primary.text() == "Apply inspector changes"
    _click(window, guided.editor.cancel_button)
    assert not guided.editor.has_changes() and window.project.to_dict() == original
    assert guided.primary.text() == primary
    _click(window, guided.primary)
    _idle(window, qtbot)
    if stage == "layers":
        assert guided.stage == "connections"
    else:
        assert window.mesh is not None and guided.assessment.stages["mesh"].status == "Ready"
        assert len(window.mesh.triangles) > 0


def test_new_project_retires_failed_worker_context_and_recognizes_loaded_mesh(guided_window, qtbot, solved_e1):
    window, guided = guided_window, guided_window.guided
    guided.document.transact("Deliberately insufficient mesh budget", lambda project: setattr(project.mesh, "max_elements", 1))
    guided.navigate("mesh")
    _click(window, guided.primary)
    _idle(window, qtbot)
    assert guided.last_error and window.mesh is None
    assert guided.drawer.currentIndex() == 0 and guided.issue_table.rowCount() > 0
    project, result = deepcopy(solved_e1)
    window.set_project(project, result)
    _idle(window, qtbot)
    assert not guided.last_error and not guided.last_error_detail
    assert guided.check_batch is None and guided.initial_state_evidence is None
    assert guided.execution_state == "Recorded result loaded"
    assert physics_hash(window.project) == physics_hash(result.configuration)
    guided.navigate("mesh")
    assert guided.assessment.stages["mesh"].status == "Ready"
    assert "Current mesh" in guided.mesh_status.text()
    guided.preview_mesh()
    assert window.mesh_toggle.isChecked()
    assert not guided.next_reason.text()
