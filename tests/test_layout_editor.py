# SPDX-License-Identifier: MPL-2.0
"""Observable vertex/draft/history behavior using real preparation/export workers."""
import pytest
from gui_test_helpers import _answer_dialog, _idle
from gui_test_helpers import guided_window as guided_window
from gui_test_helpers import solved_e1 as solved_e1
from PySide6.QtCore import QPointF, Qt, QTimer
from PySide6.QtTest import QTest

from etlayout.geometry.polygon_edit import editable_polygons
from etlayout.gui.editor_smoke import run_editor_smoke
from etlayout.gui.viewer import SelectionShapeItem, geometry_path
from etlayout.io.project import file_hash, load_project, physics_hash


@pytest.fixture
def editor_window(guided_window, tmp_path):
    window = guided_window
    editor = window.guided.layout_editor
    editor.cache_dir = tmp_path / "owned-layouts"
    yield window
    editor.discard()
    editor.leave()


def select_first(window):
    editor = window.guided.layout_editor
    key = next(iter(editor.targets))
    item = window.view.layer_items[key][0]
    window.view.choose_candidate(window.view._candidate(item))
    assert editor.target is editor.targets[key] and editor.handles
    return editor


def draft(window):
    editor = select_first(window)
    handle = editor.handles[0]
    # Extend along the contact edge so the original line support remains valid.
    editor.move_vertex(handle.ring, handle.index, handle.pos() + QPointF(0., .25))
    assert editor.dirty and editor.valid
    return editor


def test_real_mouse_drag_apply_undo_redo_and_portable_reopening(editor_window, qtbot, tmp_path):
    window = editor_window
    editor = window.guided.layout_editor
    original = window.project.to_dict()
    imported_hash = file_hash(window.project.layout.path)
    QTest.mouseClick(editor.toggle, Qt.MouseButton.LeftButton)
    assert editor.active
    editor = select_first(window)
    handle = editor.handles[0]
    start = window.view.mapFromScene(handle.pos())
    end = window.view.mapFromScene(handle.pos() + QPointF(0., .25))
    QTest.mousePress(window.view.viewport(), Qt.MouseButton.LeftButton, pos=start)
    QTest.mouseMove(window.view.viewport(), end, delay=40)
    QTest.mouseRelease(window.view.viewport(), Qt.MouseButton.LeftButton, pos=end)
    assert editor.dirty and editor.valid
    expected = editor.effective
    assert window.project.to_dict() == original  # drag is a draft
    QTest.mouseClick(editor.apply_button, Qt.MouseButton.LeftButton)
    _idle(window, qtbot)
    assert editor.active and not editor.dirty
    edited = window.project.to_dict()
    assert edited["layout"]["path"] != original["layout"]["path"]
    assert any(t.geometry.equals(expected) for t in editable_polygons(window.project)[0])
    assert file_hash(original["layout"]["path"]) == imported_hash
    window.undo()
    _idle(window, qtbot)
    assert window.project.to_dict() == original
    window.redo()
    _idle(window, qtbot)
    assert window.project.to_dict() == edited
    window.path = tmp_path / "edited.etl"
    window.save()
    restored, _ = load_project(window.path)
    assert physics_hash(restored) == physics_hash(window.project)
    assert any(t.geometry.equals(expected) for t in editable_polygons(restored)[0])


@pytest.mark.parametrize("destination", ["save", "history"])
@pytest.mark.parametrize("decision", ["Apply", "Discard", "Stay"])
def test_draft_transition_policy_preserves_working_and_historical_context(editor_window, qtbot, solved_e1, tmp_path, decision, destination):
    window = editor_window
    _, result = solved_e1
    window.show_result(result)
    window.path = tmp_path / "saved.etl"
    assert window.guided.layout_editor.enter()
    editor = draft(window)
    old = window.project.to_dict()
    old_view = window.capture_view_state()
    old_target = editor.target
    old_history = list(window.run_history)
    _answer_dialog(decision)
    window.save() if destination == "save" else window.inspect_result(result)
    _idle(window, qtbot)
    if decision == "Stay":
        assert editor.active and editor.dirty and editor.target is old_target
        assert window.project.to_dict() == old and window.result is result
        assert window.capture_view_state() == old_view
        assert not window.inspection_context and not window.path.exists()
    else:
        assert not editor.active and not editor.dirty
        working = window.inspection_context["project"] if window.inspection_context else window.project
        assert (working.to_dict() == old) == (decision == "Discard")
        if destination == "history":
            assert window.inspection_context and window.result is result
            assert not editor.enter()  # historical inspection remains read-only
            window.return_to_project()
            _idle(window, qtbot)
            assert physics_hash(window.project) == physics_hash(working)
        else:
            restored, _ = load_project(window.path)
            assert physics_hash(restored) == physics_hash(working)
            if decision == "Apply":
                assert window.result is None and window.mesh is None
    assert window.run_history == old_history
    assert file_hash(old["layout"]["path"]) == old["layout"]["sha256"]


def test_invalid_drag_escape_and_keyboard_nudge(editor_window, qtbot):
    window = editor_window
    editor = window.guided.layout_editor
    assert editor.enter()
    editor = select_first(window)
    original = editor.rings[0][0]
    first, other = editor.handles[:2]
    editor.move_vertex(first.ring, first.index, other.pos())
    assert editor.dirty and not editor.valid and not editor.apply_button.isEnabled()
    assert not editor.apply()
    QTest.keyClick(window.view, Qt.Key.Key_Escape)
    assert editor.active and not editor.dirty and editor.valid
    first = editor.handles[0]
    first.setFocus()
    QTest.keyClick(window.view, Qt.Key.Key_Right, Qt.KeyboardModifier.ShiftModifier)
    assert editor.rings[0][0][0] == pytest.approx(original[0] + 10 * editor.dbu)
    assert editor.dirty


def test_document_transaction_resolves_vertices_before_copying_other_draft(editor_window, qtbot):
    window = editor_window
    editor = window.guided.layout_editor
    assert editor.enter()
    draft(window)
    original_hash = window.project.layout.sha256
    _answer_dialog("Apply")
    window.guided.document.transact("Rename project", lambda project: setattr(project, "name", "Renamed"))
    _idle(window, qtbot)
    assert window.project.name == "Renamed" and window.project.layout.sha256 != original_hash
    assert not editor.active and not editor.dirty


def test_editor_shortcut_toggles_without_reentering(editor_window):
    window = editor_window
    window.guided.execute("edit.layout")
    assert window.guided.layout_editor.active
    window.guided.execute("edit.layout")
    assert not window.guided.layout_editor.active


def test_editor_preparation_locks_document_before_mutation(editor_window):
    window = editor_window
    invoked = []
    rejected = []

    def attempt():
        try:
            window.guided.document.transact("Nested mutation", lambda project: invoked.append(project))
        except ValueError:
            rejected.append(True)

    QTimer.singleShot(0, attempt)
    assert window.guided.layout_editor.enter()
    assert rejected == [True] and not invoked


def test_changed_context_rejects_prepared_output_in_visible_drawer(editor_window):
    window = editor_window
    original_hash = window.project.layout.sha256
    QTimer.singleShot(0, lambda: setattr(window.project, "name", "Changed during preparation"))
    assert not window.guided.layout_editor.enter()
    assert window.project.name == "Changed during preparation"
    assert window.project.layout.sha256 == original_hash
    assert "working setup changed" in window.guided.last_error
    assert window.guided.drawer.isVisible()


def test_vertex_handles_remain_pickable_above_large_layout_shape_order(editor_window):
    window = editor_window
    editor = window.guided.layout_editor
    assert editor.enter()
    editor = select_first(window)
    covering_shape = SelectionShapeItem(geometry_path(editor.target.geometry))
    covering_shape.setZValue(9999)  # Highest possible ordinary shape in the bounded editor.
    window.view.scene().addItem(covering_shape)
    handle = editor.handles[0]
    start = window.view.mapFromScene(handle.pos())
    end = window.view.mapFromScene(handle.pos() + QPointF(0., .25))
    assert window.view.itemAt(start) is handle
    QTest.mousePress(window.view.viewport(), Qt.MouseButton.LeftButton, pos=start)
    QTest.mouseMove(window.view.viewport(), end, delay=40)
    QTest.mouseRelease(window.view.viewport(), Qt.MouseButton.LeftButton, pos=end)
    assert editor.dirty and editor.valid


def test_packaging_editor_helper_uses_real_workers_and_portable_save(editor_window, tmp_path):
    assert run_editor_smoke(editor_window, tmp_path / "smoke")
    import json
    receipt = json.loads((tmp_path / "smoke" / "receipt.json").read_text(encoding="utf-8"))
    assert receipt["status"] == "passed" and not receipt["frozen"]
    assert receipt["heartbeat_ticks"] >= 5
    assert receipt["edited_layout_sha256"] != receipt["imported_sha256"]
