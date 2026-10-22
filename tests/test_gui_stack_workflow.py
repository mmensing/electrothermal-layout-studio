# SPDX-License-Identifier: MPL-2.0
"""Physical stack interpretation and shared-editor connection intents."""
from copy import deepcopy
from dataclasses import asdict

import pytest
from PySide6.QtCore import QPointF, Qt
from shapely.geometry import box

from etlayout.geometry.prepare import prepare_geometry, resolve_region
from etlayout.gui.plots import CrossSectionPanel
from etlayout.gui.stack_workflow import (
    StackElevation,
    StackOverview,
    connection_draft,
    connection_physics,
    layer_support,
    ordered_layers,
)
from etlayout.materials.database import reference_materials
from etlayout.models import Interface, PhysicalLayer, PreparedGeometry, Project, RegionSpec


@pytest.fixture
def stack_project():
    support = RegionSpec(polygons=[{"bounds": [0, 0, 10e-6, 10e-6]}])
    # List order deliberately differs from elevation; the film is optically thin.
    return Project(materials=reference_materials(), layers=[
        PhysicalLayer("top", "Top copper", "copper", 2e-6, 100.01e-6, geometry=deepcopy(support)),
        PhysicalLayer("bottom", "Substrate", "sic", 100e-6, 0., "thermal", deepcopy(support)),
        PhysicalLayer("film", "Passivation", "sin", .01e-6, 100e-6, "insulator", deepcopy(support)),
        PhysicalLayer("feed", "Lower copper", "copper", 1e-6, -2e-6,
                      geometry=RegionSpec(polygons=[{"bounds": [2e-6, 0, 8e-6, 10e-6]}]))])


def test_stack_order_and_footprint_explain_physics_without_mutating(stack_project):
    snapshot = asdict(stack_project)
    stack_project.layers[0].visible = False
    snapshot["layers"][0]["visible"] = False
    assert [layer.id for layer in ordered_layers(stack_project)] == ["feed", "bottom", "film", "top"]
    assert layer_support(stack_project.layers[0]) == "explicit polygons"
    assert layer_support(PhysicalLayer("empty", "Empty", "copper")) == "No footprint assigned"
    assert asdict(stack_project) == snapshot


@pytest.mark.parametrize("purpose, expected", [
    ("thermal", ("none", "stack", "effective")),
    ("contact", ("specific", "none", "physical_contact")),
    ("via", ("via", "none", "physical_via")),
])
def test_connection_intent_is_unapplied_and_uses_exact_shared_support(stack_project, purpose, expected):
    if purpose == "contact":
        stack_project.layer("film").role = "conductor"
        stack_project.layer("film").material_id = "copper"
    first, second = ("film", "top") if purpose == "contact" else ("feed", "top")
    before = asdict(stack_project)
    geometry = prepare_geometry(stack_project)
    draft = connection_draft(stack_project, first, second, purpose, "new", geometry)
    assert (draft.electrical_kind, draft.thermal_kind, draft.model_kind) == expected
    actual = resolve_region(draft.geometry, {}, geometry.domain)
    assert actual.symmetric_difference(geometry.layers[first].intersection(geometry.layers[second])).area == 0
    assert draft.layer_a == first and draft.layer_b == second
    assert asdict(stack_project) == before
    if purpose == "via":
        assert draft.height_mode == "stack"
    if purpose == "thermal":
        assert draft.material_id is None  # Never invent gap material.
        assert draft.height == pytest.approx(101.01e-6)


@pytest.mark.parametrize("a, b, purpose, message", [
    ("top", "top", "thermal", "different physical layers"),
    ("missing", "top", "thermal", "different physical layers"),
    ("film", "top", "contact", "two conducting layers"),
    ("bottom", "top", "via", "two conducting layers"),
    ("feed", "top", "unknown", "Choose a thermal path"),
])
def test_connection_intent_rejects_ambiguous_or_nonconducting_electrical_pairs(stack_project, a, b, purpose, message):
    with pytest.raises(ValueError, match=message):
        connection_draft(stack_project, a, b, purpose, "invalid")


def test_connection_draft_does_not_invent_support_and_disabled_paths_are_explicit(stack_project):
    draft = connection_draft(stack_project, "bottom", "film", "thermal", "thermal")
    assert not draft.geometry.full_domain and not draft.geometry.polygons
    assert draft.height == 0
    draft.enabled = False
    assert connection_physics(draft) == ("Disabled", "Disabled")
    draft.enabled = True
    assert connection_physics(draft) == ("None", "stack")


def test_expanded_elevation_preserves_common_faces_and_physical_coordinates(stack_project):
    physical = StackElevation.from_project(stack_project)
    expanded = StackElevation.from_project(stack_project, True)
    film = stack_project.layer("film")
    substrate = stack_project.layer("bottom")
    assert physical(film.z + film.thickness) - physical(film.z) == pytest.approx(.01)
    assert expanded(film.z) == expanded(substrate.z + substrate.thickness)
    assert expanded(film.z + film.thickness) - expanded(film.z) == pytest.approx(1.)
    assert [tick[1] for tick in expanded.ticks][-2:] == ["100.01", "102.01"]
    assert StackElevation((), True)(3e-6) == pytest.approx(3.)


def test_stack_overview_routes_layer_and_pair_intents_without_changing_project(qtbot, stack_project):
    overview = StackOverview()
    qtbot.addWidget(overview)
    overview.resize(1000, 850)
    snapshot = asdict(stack_project)
    overview.set_project(stack_project, prepare_geometry(stack_project), "film")
    overview.show()
    assert overview.layer_table.rowCount() == 4
    assert "1 µm = 1e-6 m" in overview.layer_table.horizontalHeaderItem(2).toolTip()
    assert "Positive" in overview.layer_a.toolTip()
    assert overview.layer_a.accessibleDescription() == overview.layer_a.toolTip()
    assert overview.layer_table.currentRow() == 2
    assert overview.layer_table.item(0, 0).data(Qt.ItemDataRole.UserRole) == "feed"
    assert overview.layer_table.item(2, 4).text() == "0.01"
    with qtbot.waitSignal(overview.edit_layer_requested) as emitted:
        qtbot.mouseClick(overview.edit_layer, Qt.MouseButton.LeftButton)
    assert emitted.args == ["film"]
    overview.layer_a.setCurrentIndex(overview.layer_a.findData("feed"))
    overview.layer_b.setCurrentIndex(overview.layer_b.findData("top"))
    overview.purpose.setCurrentIndex(overview.purpose.findData("via"))
    overview.new_connection.setChecked(True)
    with qtbot.waitSignal(overview.connection_requested) as emitted:
        qtbot.mouseClick(overview.add_connection, Qt.MouseButton.LeftButton)
    assert emitted.args == ["feed", "top", "via"]
    assert asdict(stack_project) == snapshot


def test_stack_overview_interprets_declared_connections_and_rejects_electrical_dielectric(qtbot, stack_project):
    overview = StackOverview()
    qtbot.addWidget(overview)
    stack_project.interfaces = [Interface("thermal", "bottom", "film", thermal_kind="stack"),
                                Interface("disabled", "feed", "top", electrical_kind="specific", enabled=False)]
    geometry = PreparedGeometry(box(0, 0, 10e-6, 10e-6), {}, {"thermal": box(0, 0, 5e-6, 5e-6)})
    overview.set_project(stack_project, geometry)
    overview.new_connection.setChecked(True)
    assert overview.connection_table.item(0, 3).text() == "stack"
    assert overview.connection_table.item(0, 4).text() == "25 µm²"
    assert overview.connection_table.item(1, 2).text() == "Disabled"
    assert "1 enabled interlayer paths" in overview.summary.text()
    overview.layer_a.setCurrentIndex(overview.layer_a.findData("film"))
    overview.layer_b.setCurrentIndex(overview.layer_b.findData("top"))
    overview.purpose.setCurrentIndex(overview.purpose.findData("via"))
    assert not overview.add_connection.isEnabled()
    assert "two conductors" in overview.pair_hint.text()
    overview.purpose.setCurrentIndex(overview.purpose.findData("thermal"))
    assert overview.add_connection.isEnabled()
    assert "No enabled interface" in overview.pair_hint.text()
    overview.set_project(None)
    assert not overview.add_connection.isEnabled() and overview.layer_table.rowCount() == 0


def test_cross_section_expands_thin_film_without_changing_geometry_and_can_restore_physical_scale(qtbot, stack_project):
    panel = CrossSectionPanel()
    qtbot.addWidget(panel)
    geometry = prepare_geometry(stack_project)
    snapshot = asdict(stack_project)
    panel.show_cut(stack_project, geometry, [0, 5e-6], [10e-6, 5e-6])
    film_rect = next(rect for rect, layer in panel._layer_rectangles if layer.id == "film")
    assert film_rect.rect().height() == pytest.approx(1.)
    assert panel.layer_table.item(2, 3).text() == "0.01"
    assert panel.layer_table.item(2, 6).text() == "100.01"
    assert "expanded" in panel.description.text()
    assert "not its two-dimensional contact area" in panel.connection_table.horizontalHeaderItem(4).toolTip()
    intervals = deepcopy(panel.intervals)
    panel.vertical_mode.setCurrentIndex(panel.vertical_mode.findData("physical"))
    film_rect = next(rect for rect, layer in panel._layer_rectangles if layer.id == "film")
    assert film_rect.rect().height() == pytest.approx(.01)
    assert panel.intervals == intervals and asdict(stack_project) == snapshot


def test_cross_section_picking_and_visibility_are_view_state_only(qtbot, stack_project):
    panel = CrossSectionPanel()
    qtbot.addWidget(panel)
    panel.resize(1100, 750)
    panel.show()
    stack_project.layer("film").visible = False
    snapshot = asdict(stack_project)
    panel.show_cut(stack_project, prepare_geometry(stack_project), [0, 5e-6], [10e-6, 5e-6])
    assert "film" in panel.intervals  # Hidden is still physical by default.
    with qtbot.waitSignal(panel.layer_selected) as emitted:
        panel.layer_table.selectRow(2)
    assert emitted.args == ["film"]
    assert panel._selected_layer == "film"
    qtbot.wait(20)
    rectangle = next(rect for rect, layer in panel._layer_rectangles if layer.id == "top")
    point = rectangle.rect().center()
    scene = panel.plot.getViewBox().mapViewToScene(QPointF(point.x(), point.y()))
    with qtbot.waitSignal(panel.layer_selected) as emitted:
        qtbot.mouseClick(panel.plot.viewport(), Qt.MouseButton.LeftButton, pos=panel.plot.mapFromScene(scene))
    assert emitted.args == ["top"]
    panel.include_hidden.setChecked(False)
    assert "film" not in panel.intervals
    assert asdict(stack_project) == snapshot


def test_cross_section_lists_only_actual_cut_links_and_clears_retained_context(qtbot, stack_project):
    domain = RegionSpec(polygons=[{"bounds": [0, 0, 10e-6, 10e-6]}])
    stack_project.interfaces = [
        Interface("on_cut", "bottom", "film", deepcopy(domain), thermal_kind="stack"),
        Interface("outside_cut", "bottom", "film", RegionSpec(polygons=[{"bounds": [0, 0, 10e-6, 2e-6]}]), thermal_kind="stack"),
        Interface("disabled", "feed", "top", deepcopy(domain), enabled=False)]
    panel = CrossSectionPanel()
    qtbot.addWidget(panel)
    panel.show_cut(stack_project, prepare_geometry(stack_project), [0, 5e-6], [10e-6, 5e-6])
    assert panel.connection_table.rowCount() == 1
    assert panel.connection_table.item(0, 0).text() == "on_cut"
    assert "no automatic heat path" in panel.stack_summary.text()
    panel.clear()
    panel.vertical_mode.setCurrentIndex(panel.vertical_mode.findData("physical"))
    assert panel._cut is None and not panel.intervals
    assert panel.layer_table.rowCount() == 0 and panel.connection_table.rowCount() == 0
    panel.show_cut(Project(), PreparedGeometry(box(0, 0, 1, 1), {}), [0, 0], [1, 0])
    assert "Add physical layers" in panel.description.text()


def test_compact_stack_keeps_dimensions_visible_without_horizontal_table_sprawl(qtbot, stack_project):
    overview = StackOverview(compact=True)
    qtbot.addWidget(overview)
    overview.resize(390, 600)
    overview.set_project(stack_project, prepare_geometry(stack_project), "film")
    overview.show()
    assert overview.layer_table.isColumnHidden(2)
    assert "100 → 100.01 µm · t 0.01 µm" in overview.layer_table.item(2, 0).text()
    assert "bottom 100 → top 100.01 µm" in overview.layer_detail.text()
    assert "thickness 0.01 µm" in overview.layer_detail.text()
    assert not overview.new_connection.isChecked()
    qtbot.waitUntil(lambda: overview.layer_table.rowHeight(2) >=
                    3 * overview.layer_table.fontMetrics().lineSpacing() + 12)
    assert overview.layer_table.textElideMode() == Qt.TextElideMode.ElideNone
    overview.layer_table.setFixedWidth(300)
    qtbot.waitUntil(lambda: overview.layer_table.columnWidth(0) < 180)
    qtbot.waitUntil(lambda: overview.layer_table.rowHeight(2) >=
                    3 * overview.layer_table.fontMetrics().lineSpacing() + 12)
    overview.set_compact(False)
    assert not overview.layer_table.isColumnHidden(2)
    assert overview.layer_table.horizontalHeaderItem(0).text() == "Layer / physics"
    assert overview.layer_table.currentRow() == 2


def test_contact_and_thermal_drafts_accept_valid_touching_stack_heights(stack_project):
    from etlayout.technology.validation import validate_project

    stack_project.layer("film").role = "conductor"
    stack_project.layer("film").material_id = "copper"
    for purpose in ("contact", "thermal"):
        draft = connection_draft(stack_project, "film", "top", purpose, purpose, prepare_geometry(stack_project))
        assert draft.height == pytest.approx(0.) and draft.height_mode == "stack"
        candidate = deepcopy(stack_project)
        candidate.interfaces.append(draft)
        errors = [item.message for item in validate_project(candidate, raise_errors=False)
                  if item.severity == "error" and item.object_id == purpose]
        assert not errors
        prepare_geometry(candidate)
    with pytest.raises(ValueError, match="do not touch"):
        connection_draft(stack_project, "feed", "top", "contact", "bad")
    with pytest.raises(ValueError, match="positive surface gap"):
        connection_draft(stack_project, "film", "top", "via", "bad")
