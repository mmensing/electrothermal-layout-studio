# SPDX-License-Identifier: MPL-2.0
"""Editor operations through native controls, mouse handles and the document."""
from copy import deepcopy

import klayout.db as kdb
import pytest
from gui_test_helpers import _idle
from gui_test_helpers import guided_window as guided_window
from PySide6.QtCore import QPointF, Qt
from PySide6.QtTest import QTest
from shapely.affinity import translate
from test_layout_editor import editor_window as editor_window
from test_layout_editor import select_first

from etlayout.io.project import file_hash, physics_hash


def type_coordinate(field, text):
    field.setFocus()
    field.selectAll()
    QTest.keyClicks(field, text)


def test_numeric_vertex_live_draft_and_grid_preference_do_not_change_physics(editor_window):
    window = editor_window
    editor = window.guided.layout_editor
    assert editor.enter()
    select_first(window)
    original = window.project.to_dict()
    original_hash = physics_hash(window.project)
    editor.snap_grid.setValue(100)
    assert not editor.dirty and physics_hash(window.project) == original_hash
    x, y = editor.rings[0][0]
    type_coordinate(editor.x_field, f"{x * 1e6 + .24:g}")
    assert editor.rings[0][0] == pytest.approx((x + .2e-6, y))
    assert editor.dirty and editor.valid and window.project.to_dict() == original
    QTest.mouseClick(editor.coordinate_button, Qt.MouseButton.LeftButton)
    assert float(editor.x_field.text()) == pytest.approx(x * 1e6 + .2)
    type_coordinate(editor.x_field, "nan")
    assert editor.dirty and not editor.valid and not editor.apply_button.isEnabled()
    assert not editor.apply()
    editor.vertex_choice.setCurrentIndex(1)
    assert editor.selected_vertex == (0, 0) and editor.x_field.text() == "nan" and not editor.valid
    handle = editor.move_handle
    start = window.view.mapFromScene(handle.pos())
    old_position = handle.pos()
    QTest.mousePress(window.view.viewport(), Qt.MouseButton.LeftButton, pos=start)
    QTest.mouseMove(window.view.viewport(), window.view.mapFromScene(old_position + QPointF(2., 0.)), delay=40)
    QTest.mouseRelease(window.view.viewport(), Qt.MouseButton.LeftButton, pos=window.view.mapFromScene(old_position + QPointF(2., 0.)))
    assert handle.pos() == old_position and editor.x_field.text() == "nan" and not editor.valid
    QTest.mouseClick(editor.discard_button, Qt.MouseButton.LeftButton)
    assert not editor.dirty and editor.valid and editor.rings[0][0] == (x, y)
    # A remembered snap preference is separate from portable physical identity.
    assert editor.grid_units == 100 and physics_hash(window.project) == original_hash


@pytest.mark.parametrize("ring", [0, 1])
def test_numeric_ring_selection_insert_delete_and_hole_roundtrip(editor_window, qtbot, tmp_path, ring):
    window = editor_window
    p = deepcopy(window.project)
    layout = kdb.Layout()
    layout.read(p.layout.path)
    cell = layout.cell(p.layout.cell)
    layer = layout.find_layer(1, 0)
    shape = next(cell.shapes(layer).each())
    polygon = shape.polygon
    grid = layout.dbu
    polygon.insert_hole([kdb.Point(round(x / grid), round(y / grid)) for x, y in [(40, 2), (60, 2), (60, 8), (40, 8)]])
    shape.delete()
    cell.shapes(layer).insert(polygon)
    p.layout.path = str(tmp_path / "holed.gds")
    layout.write(p.layout.path)
    p.layout.sha256 = file_hash(p.layout.path)
    window.set_project(p)
    _idle(window, qtbot)
    editor = window.guided.layout_editor
    assert editor.enter()
    select_first(window)
    assert len(editor.rings) == 2
    original = deepcopy(editor.rings)
    editor.ring_choice.setCurrentIndex(ring)
    editor.vertex_choice.setCurrentIndex(3)
    assert editor.selected_vertex == (ring, 3)
    QTest.mouseClick(editor.insert_button, Qt.MouseButton.LeftButton)
    assert len(editor.rings[ring]) == 5 and editor.selected_vertex == (ring, 4)
    assert editor.rings[1 - ring] == original[1 - ring]
    QTest.mouseClick(editor.delete_button, Qt.MouseButton.LeftButton)
    assert editor.rings == original and not editor.dirty
    editor.vertex_choice.setCurrentIndex(0)
    x, y = editor.rings[ring][0]
    type_coordinate(editor.y_field, f"{y * 1e6 + .25:g}")
    assert editor.rings[ring][0][1] == pytest.approx(y + .25e-6)
    assert editor.valid and editor.dirty
    expected = editor.effective
    assert editor.apply()
    _idle(window, qtbot)
    actual = next(target.geometry for target in editor.targets.values())
    assert actual.equals(expected) and len(actual.interiors) == 1
    assert file_hash(p.layout.path) == p.layout.sha256


def test_whole_polygon_mouse_and_numeric_move_preserve_rings_and_show_lost_contacts(editor_window):
    window = editor_window
    editor = window.guided.layout_editor
    assert editor.enter()
    select_first(window)
    original = editor.effective
    before = window.project.to_dict()
    handle = editor.move_handle
    start = window.view.mapFromScene(handle.pos())
    end = window.view.mapFromScene(handle.pos() + QPointF(2., 0.))
    QTest.mousePress(window.view.viewport(), Qt.MouseButton.LeftButton, pos=start)
    QTest.mouseMove(window.view.viewport(), end, delay=40)
    QTest.mouseRelease(window.view.viewport(), Qt.MouseButton.LeftButton, pos=end)
    assert editor.dirty and editor.valid
    delta = editor.effective.bounds[0] - original.bounds[0]
    assert delta > 0 and editor.effective.equals_exact(translate(original, xoff=delta), 1e-15)
    assert any(item.category == "Contact" and item.object_id == "source" and "lost" in item.message for item in editor.impacts)
    assert window.project.to_dict() == before
    editor.tabs.setCurrentIndex(2)
    index = next(i for i, item in enumerate(editor.impacts) if item.object_id == "source")
    editor.impact_list.setCurrentRow(index)
    assert "Support is lost" in editor.impact_summary.text() and editor.impact_outline is not None
    editor.discard()
    editor.tabs.setCurrentIndex(1)
    editor.dx_field.setText("1.24")
    editor.dy_field.setText("-0.26")
    editor.snap_grid.setValue(100)
    QTest.mouseClick(editor.move_button, Qt.MouseButton.LeftButton)
    assert editor.effective.equals_exact(translate(original, xoff=1.2e-6, yoff=-.3e-6), 1e-15)
    assert editor.dx_field.text() == editor.dy_field.text() == "0"
    assert window.project.to_dict() == before
