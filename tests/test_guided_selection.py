# SPDX-License-Identifier: MPL-2.0
"""Public canvas actions: explicit footprints, display-only filters and drafts."""
from __future__ import annotations

import os
from dataclasses import asdict

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtCore import QPoint, QPointF, Qt
from PySide6.QtTest import QTest
from shapely.geometry import MultiPolygon, box

from etlayout.geometry.prepare import resolve_region
from etlayout.gui.viewer import LayoutView
from etlayout.models import PhysicalLayer


@pytest.fixture
def selection_view(qtbot):
    view = LayoutView()
    qtbot.addWidget(view)
    view.resize(720, 480)
    lower = PhysicalLayer("m1", "Lower metal", "copper", z=1e-6)
    upper = PhysicalLayer("m2", "Upper metal", "aluminum", z=4e-6)
    hidden = PhysicalLayer("hidden", "Reference", "copper", visible=False)
    geometry = {
        "m1": MultiPolygon([box(0, 0, 10e-6, 10e-6), box(15e-6, 0, 20e-6, 5e-6)]),
        "m2": box(0, 0, 10e-6, 10e-6), "hidden": box(0, 0, 10e-6, 10e-6),
    }
    view.set_geometries(geometry, [lower, upper, hidden])
    view.show()
    qtbot.waitExposed(view)
    view.fit_all()
    return view, [lower, upper, hidden]


def test_overlap_popup_requires_explicit_layer_and_preserves_footprint(selection_view, qtbot):
    view, _ = selection_view
    view.set_selection_filter(provenance={"source_hash": "immutable-source", "instance_path": [2, 1]})
    candidates = view.selection_candidates(QPointF(5, -5))
    assert [candidate.layer_id for candidate in candidates] == ["m2", "m1"]
    assert candidates[1].instance_path == "2/1"
    assert "z 1 µm" in candidates[1].label
    assert candidates[1].provenance["source_hash"] == "immutable-source"
    QTest.mouseClick(view.viewport(), Qt.MouseButton.LeftButton, pos=view.mapFromScene(QPointF(5, -5)))
    qtbot.waitUntil(lambda: view.candidate_menu is not None and view.candidate_menu.isVisible())
    assert not view.selected_geometries()
    action = next(action for action in view.candidate_menu.actions()
                  if action.data() is not None and action.data().layer_id == "m1")
    QTest.mouseClick(view.candidate_menu, Qt.MouseButton.LeftButton,
                     pos=view.candidate_menu.actionGeometry(action).center())
    selected = view.selected_geometries()
    assert len(selected) == 1 and selected[0][0] == "m1"
    assert selected[0][1].area == pytest.approx(100e-12)
    assert selected[0][1].bounds == pytest.approx((0, 0, 10e-6, 10e-6))


def test_filters_hidden_layers_and_pickability_never_modify_physics(selection_view):
    view, layers = selection_view
    original = [asdict(layer) for layer in layers]
    view.set_selection_filter(["m1"], purpose="Source landing")
    candidate, = view.selection_candidates(QPointF(5, -5))
    assert candidate.layer_id == "m1"
    assert view.choose_candidate(candidate)
    view.set_layer_visible("m1", False)
    assert not view.selected_geometries()
    assert not view.selection_candidates(QPointF(5, -5))
    assert not view.choose_candidate(candidate)
    view.set_layer_visible("m1", True)
    assert view.choose_candidate(candidate)
    view.set_layer_pickable("m1", False)
    assert not view.selection_candidates(QPointF(5, -5))
    assert not view.selected_geometries()
    view.clear_selection_filter()
    assert [candidate.layer_id for candidate in view.selection_candidates(QPointF(5, -5))] == ["m2"]
    assert [asdict(layer) for layer in layers] == original


def test_ctrl_click_toggles_only_the_requested_footprint(selection_view):
    view, _ = selection_view
    view.set_selection_filter(["m1"])
    first = view.mapFromScene(QPointF(5, -5))
    second = view.mapFromScene(QPointF(17, -2))
    QTest.mouseClick(view.viewport(), Qt.MouseButton.LeftButton, pos=first)
    QTest.mouseClick(view.viewport(), Qt.MouseButton.LeftButton, Qt.KeyboardModifier.ControlModifier, second)
    assert len(view.selected_geometries()) == 2
    QTest.mouseClick(view.viewport(), Qt.MouseButton.LeftButton, Qt.KeyboardModifier.ControlModifier, first)
    selected, = view.selected_geometries()
    assert selected[1].area == pytest.approx(25e-12)


def test_escape_cancels_uncommitted_rectangle_and_preserves_picks(selection_view):
    view, _ = selection_view
    view.set_selection_filter(["m1"])
    view.choose_candidate(view.selection_candidates(QPointF(5, -5))[0])
    selected = view.selected_geometries()
    cancelled, committed = [], []
    view.selection_cancelled.connect(lambda: cancelled.append(True))
    view.region_drawn.connect(lambda *args: committed.append(args))
    view.set_mode("roi_rect")
    start, end = view.mapFromScene(QPointF(1, -1)), view.mapFromScene(QPointF(9, -9))
    QTest.mousePress(view.viewport(), Qt.MouseButton.LeftButton, pos=start)
    QTest.mouseMove(view.viewport(), end)
    QTest.keyClick(view, Qt.Key.Key_Escape)
    QTest.mouseRelease(view.viewport(), Qt.MouseButton.LeftButton, pos=end)
    assert cancelled == [True] and committed == []
    assert view.preview is None and view.start is None
    assert view.mode == "select" and view.selected_geometries() == selected


def test_pending_rectangle_handles_resize_only_the_draft(selection_view):
    view, layers = selection_view
    original = [asdict(layer) for layer in layers]
    draft_changes, committed = [], []
    view.pending_region_changed.connect(draft_changes.append)
    view.region_drawn.connect(lambda *args: committed.append(args))
    view.show_pending_region(box(2e-6, 2e-6, 8e-6, 8e-6))
    assert len(view.pending_region_handles) == 4
    handle = view.pending_region_handles[0]
    start = view.mapFromScene(handle.pos())
    end = view.mapFromScene(QPointF(1, -9))
    QTest.mousePress(view.viewport(), Qt.MouseButton.LeftButton, pos=start)
    QTest.mouseMove(view.viewport(), end)
    QTest.mouseRelease(view.viewport(), Qt.MouseButton.LeftButton, pos=end)
    assert len(draft_changes) == 1 and not committed
    geometry = resolve_region(draft_changes[0], {})
    assert geometry.area > 36e-12
    assert geometry.bounds == pytest.approx((1e-6, 2e-6, 8e-6, 9e-6), abs=5e-8)
    assert [asdict(layer) for layer in layers] == original
    QTest.keyClick(view, Qt.Key.Key_Escape)
    assert view.pending_region_item is None and not view.pending_region_handles


def test_pan_and_zoom_during_polygon_keep_pending_vertices(selection_view):
    view, _ = selection_view
    view.set_mode("roi_polygon")
    QTest.mouseClick(view.viewport(), Qt.MouseButton.LeftButton, pos=view.mapFromScene(QPointF(1, -1)))
    points = list(view.points)
    scale = view.transform().m11()
    QTest.keyClick(view, Qt.Key.Key_Plus)
    assert view.transform().m11() > scale
    center = view.mapToScene(view.viewport().rect().center())
    QTest.keyClick(view, Qt.Key.Key_Right)
    assert view.mapToScene(view.viewport().rect().center()) != center
    start = view.viewport().rect().center()
    QTest.mousePress(view.viewport(), Qt.MouseButton.MiddleButton, pos=start)
    QTest.mouseMove(view.viewport(), start + QPoint(30, 10))
    QTest.mouseRelease(view.viewport(), Qt.MouseButton.MiddleButton, pos=start + QPoint(30, 10))
    assert view.mode == "roi_polygon" and view.points == points
    assert view._panning is None


def test_boundary_snap_respects_selection_filter_and_pickability(selection_view):
    view, _ = selection_view
    view.set_selection_filter(["m1"])
    assert view.snap_boundary(QPointF(.01, -5)).x() == pytest.approx(0.)
    view.set_layer_pickable("m1", False)
    assert view.snap_boundary(QPointF(.01, -5)).x() == pytest.approx(.01)


def test_stale_candidate_cannot_assign_replaced_geometry(selection_view):
    view, layers = selection_view
    candidate = view.selection_candidates(QPointF(5, -5))[0]
    view.set_geometries({"m2": box(0, 0, 2e-6, 2e-6)}, layers)
    assert not view.choose_candidate(candidate)
    assert not view.selected_geometries()


def test_stale_candidate_cannot_cross_a_source_identity_change(selection_view):
    view, _ = selection_view
    view.set_selection_filter(provenance={"source_hash": "revision-one"})
    candidate = view.selection_candidates(QPointF(5, -5))[0]
    view.set_selection_filter(provenance={"source_hash": "revision-two"})
    assert not view.choose_candidate(candidate)
    assert not view.selected_geometries()


def test_actual_callouts_preserve_ids_coordinates_and_visibility(selection_view):
    view, _ = selection_view
    view.show_callouts([
        {"id": "probe:p1", "label": "P1 · 0.35 V", "point": (3e-6, 4e-6), "layer_id": "m1"},
        {"id": "port:in", "label": "Input port", "point": (1e-6, 2e-6), "layer_id": "m2"},
    ])
    probe = view.callout_items["probe:p1"]
    assert probe.pos() == QPointF(3., -4.)
    assert probe.text == "P1 · 0.35 V"
    view.set_layer_focus("m1")
    assert probe.isVisible() and not view.callout_items["port:in"].isVisible()
    view.set_layer_visible("m1", False)
    assert not probe.isVisible()
    view.show_callouts([])
    assert not view.callout_items
