# SPDX-License-Identifier: MPL-2.0
"""Independent raw-shape and portable-project regression tests for vertex edits."""
from copy import deepcopy
from pathlib import Path

import klayout.db as kdb
import pytest
from shapely.geometry import MultiPolygon

import etlayout.geometry.polygon_edit as polygon_edit
from etlayout.geometry.layout import LayoutDocument
from etlayout.geometry.polygon_edit import edit_polygon, editable_polygons, snapped_polygon
from etlayout.io.project import file_hash, load_project, physics_hash, save_project
from etlayout.models import Project, SimulationError


@pytest.fixture
def source(tmp_path):
    layout = kdb.Layout()
    layout.dbu = .001
    root = layout.create_cell("TOP")
    child = layout.create_cell("DEVICE")
    layer = layout.layer(1, 2)
    polygon = kdb.Polygon([kdb.Point(0, 0), kdb.Point(10000, 0), kdb.Point(10000, 8000), kdb.Point(0, 8000)])
    polygon.insert_hole([kdb.Point(2000, 2000), kdb.Point(4000, 2000), kdb.Point(4000, 4000), kdb.Point(2000, 4000)])
    shape = child.shapes(layer).insert(polygon)
    shape.set_property(62, "SOURCE")
    child.shapes(layer).insert(kdb.Text("kept", kdb.Trans(1000, 1000)))
    child.shapes(layout.layer(7, 0)).insert(kdb.Path([kdb.Point(0, 12000), kdb.Point(10000, 12000)], 1000))
    instance = root.insert(kdb.CellInstArray(child.cell_index(), kdb.Trans(), kdb.Vector(20000, 0), kdb.Vector(0, 20000), 2, 1))
    instance.set_property(63, "array")
    other = layout.create_cell("OTHER_TOP")
    other.insert(kdb.CellInstArray(root.cell_index(), kdb.Trans(0, 50000)))
    path = tmp_path / "imported.gds"
    layout.write(str(path))
    project = Project()
    project.layout.path, project.layout.cell = str(path), root.name
    project.layout.sha256 = file_hash(path)
    return project


def changed(target, dbu):
    rings = [list(target.geometry.exterior.coords)[:-1], *[list(r.coords)[:-1] for r in target.geometry.interiors]]
    rings[0][0] = (rings[0][0][0] - 1e-6, rings[0][0][1])
    if len(rings) > 1:
        rings[1][0] = (rings[1][0][0] + .25e-6, rings[1][0][1])
    return snapped_polygon(rings, dbu)


@pytest.mark.parametrize("suffix", [".gds", ".oas"])
def test_exact_instance_copy_preserves_holes_siblings_other_tops_and_properties(source, tmp_path, suffix):
    before = LayoutDocument(source.layout.path)
    old_other = before.extract("OTHER_TOP")
    targets, dbu = editable_polygons(source)
    target = next(t for t in targets if t.mask == "1/2" and t.instance_path == (0,))
    requested = changed(target, dbu)
    candidate = edit_polygon(source, target, requested, tmp_path / ("revision" + suffix))
    assert file_hash(source.layout.path) == source.layout.sha256
    assert candidate.layout.path != source.layout.path and physics_hash(candidate) != physics_hash(source)
    after = LayoutDocument(candidate.layout.path)
    actual, _ = editable_polygons(candidate)
    edited = next(t for t in actual if t.mask == "1/2" and t.instance_path == (0,))
    sibling = next(t for t in actual if t.mask == "1/2" and t.instance_path == (1,))
    assert edited.geometry.equals(requested) and len(edited.geometry.interiors) == 1
    assert sibling.geometry.equals(next(t for t in targets if t.mask == "1/2" and t.instance_path == (1,)).geometry)
    assert all(shape.equals(after.extract("OTHER_TOP")[mask]) for mask, shape in old_other.items())
    cell, _ = after._selection("TOP", [0])
    index = after._layout.find_layer(1, 2)
    polygons = [s for s in cell.shapes(index).each() if s.is_polygon()]
    assert any(s.property(62) == "SOURCE" for s in polygons)
    assert any(s.is_text() and s.text.string == "kept" for s in cell.shapes(index).each())
    assert all(i.property(63) == "array" for i in after._cell("TOP").each_inst())
    assert before.extract("TOP")["7/0"].equals(after.extract("TOP")["7/0"])


def test_selected_instance_coordinates_and_embedded_reopening(source, tmp_path):
    source.layout.instance_path = [1]
    target, dbu = editable_polygons(source)
    target = next(t for t in target if t.mask == "1/2")
    assert target.geometry.bounds[0] == pytest.approx(20e-6)
    candidate = edit_polygon(source, target, changed(target, dbu), tmp_path / "owned.oas")
    save_project(candidate, tmp_path / "edited.etl")
    expected_hash = physics_hash(candidate)
    (tmp_path / "owned.oas").unlink()
    restored, _ = load_project(tmp_path / "edited.etl")
    assert physics_hash(restored) == expected_hash
    assert restored.layout.instance_path == [1]
    actual, _ = editable_polygons(restored)
    assert next(t for t in actual if t.mask == "1/2").geometry.equals(changed(target, dbu))


@pytest.mark.parametrize("rings", [
    [[(0, 0), (1e-6, 1e-6), (0, 1e-6), (1e-6, 0)]],
    [[(0, 0), (1e-6, 0), (1e-6, 0)]],
    [[(0, 0), (1e-6, 0), (float("nan"), 1e-6)]],
    [[(0, 0), (.1e-9, 0), (0, .1e-9)]],
    [[(0, 0), (1e-6, 0), (1e-6, 1e-6)], [(2e-6, 2e-6), (3e-6, 2e-6), (2e-6, 3e-6)]],
])
def test_invalid_vertex_drafts_are_rejected(rings):
    with pytest.raises(SimulationError):
        snapped_polygon(rings, 1e-9)


def test_no_overwrite_stale_source_and_bounded_enumeration(source, tmp_path):
    targets, dbu = editable_polygons(source)
    target = targets[0]
    with pytest.raises(SimulationError, match="new GDS"):
        edit_polygon(source, target, changed(target, dbu), source.layout.path)
    with pytest.raises(SimulationError, match="at most"):
        editable_polygons(source, max_shapes=1)
    with pytest.raises(SimulationError, match="budget"):
        editable_polygons(source, max_placements=1)
    stale = deepcopy(source)
    stale.layout.sha256 = "0" * 64
    with pytest.raises(SimulationError, match="changed"):
        edit_polygon(stale, target, changed(target, dbu), tmp_path / "stale.oas")
    assert not (tmp_path / "stale.oas").exists()


def test_path_outline_edit_and_noop_leave_source_unchanged(source, tmp_path):
    targets, dbu = editable_polygons(source)
    target = next(t for t in targets if t.kind == "path outline")
    unchanged = edit_polygon(source, target, target.geometry, tmp_path / "noop.oas")
    assert unchanged.to_dict() == source.to_dict() and not (tmp_path / "noop.oas").exists()
    candidate = edit_polygon(source, target, changed(target, dbu), tmp_path / "path.oas")
    actual, _ = editable_polygons(candidate)
    assert any(t.mask == target.mask and t.geometry.equals(changed(target, dbu)) for t in actual)
    assert file_hash(source.layout.path) == source.layout.sha256


def test_rounding_cannot_turn_a_degenerate_draft_into_new_area():
    # These three requested points lie on one line. DBU rounding alone creates
    # a triangle, which must not count as a valid user-defined polygon.
    with pytest.raises(SimulationError, match="Invalid polygon"):
        snapped_polygon([[(0, 0), (1.2e-9, .4e-9), (2.4e-9, .8e-9)]], 1e-9)


@pytest.mark.parametrize("angle,mirror,suffix", [(90, True, ".gdsii"), (270, False, ".oasis")])
def test_nested_rotated_array_placement_and_all_format_aliases(source, tmp_path, angle, mirror, suffix):
    layout = kdb.Layout()
    layout.read(source.layout.path)
    root, child = layout.cell("TOP"), layout.cell("DEVICE")
    layout.set_property(59, "layout metadata")
    root.set_property(60, "top metadata")
    leaf = layout.create_cell("NESTED")
    leaf.set_property(60, "leaf metadata")
    shape = leaf.shapes(layout.layer(3, 1)).insert(kdb.Box(0, 0, 6000, 4000))
    shape.set_property(61, "nested polygon")
    nested = child.insert(kdb.CellInstArray(leaf.cell_index(), kdb.Trans(1000, -8000)))
    nested.set_property(63, "nested placement")
    instance = next(root.each_inst())
    instance.cell_inst = kdb.CellInstArray(child.cell_index(), kdb.ICplxTrans(1., angle, mirror, -3000, 4500),
                                          kdb.Vector(20000, 0), kdb.Vector(0, 20000), 2, 2)
    instance.set_property(63, "array")
    options = kdb.SaveLayoutOptions()
    options.format = "GDS2" if suffix == ".gdsii" else "OASIS"
    options.gds2_write_cell_properties = True
    options.gds2_write_file_properties = True
    source.layout.path = str(tmp_path / ("imported" + suffix))
    layout.write(source.layout.path, options)
    source.layout.sha256 = file_hash(source.layout.path)
    old = LayoutDocument(source.layout.path)
    leaf_properties = old._layout.cell("NESTED").properties()
    targets, dbu = editable_polygons(source)
    target = next(t for t in targets if t.mask == "3/1" and t.instance_path == (2, 0))
    candidate = edit_polygon(source, target, changed(target, dbu), tmp_path / ("nested" + suffix))
    actual, _ = editable_polygons(candidate)
    after = LayoutDocument(candidate.layout.path)
    assert file_hash(source.layout.path) == source.layout.sha256
    assert after._layout.property(59) == "layout metadata"
    assert after._cell("TOP").property(60) == "top metadata"
    for previous in targets:
        current = next(t for t in actual if t.mask == previous.mask and t.instance_path == previous.instance_path)
        assert current.geometry.equals(changed(target, dbu) if previous is target else previous.geometry)
    for mask, geometry in old.extract("OTHER_TOP").items():
        assert geometry.equals(after.extract("OTHER_TOP")[mask])
    leaf, _ = after._selection("TOP", [2, 0])
    assert leaf.properties() == leaf_properties
    assert any(s.property(61) == "nested polygon" for s in leaf.shapes(after._layout.find_layer(3, 1)).each())
    assert all(i.property(63) == "array" for i in after._cell("TOP").each_inst())


@pytest.mark.parametrize("operation", ["load", "edit"])
def test_source_change_during_loading_rejects_stale_snapshot(source, tmp_path, monkeypatch, operation):
    targets, dbu = editable_polygons(source)
    target = targets[0]
    document_type = polygon_edit.LayoutDocument

    def changing_document(path, **options):
        document = document_type(path, **options)
        # Modify unrelated geometry after loading. Comparing only the selected
        # polygon cannot detect this source-identity race.
        layout = kdb.Layout()
        layout.read(str(path))
        layout.cell("DEVICE").shapes(layout.layer(99, 0)).insert(kdb.Box(0, 0, 2000, 1000))
        layout.write(str(path))
        return document

    monkeypatch.setattr(polygon_edit, "LayoutDocument", changing_document)
    with pytest.raises(SimulationError, match="changed while loading"):
        if operation == "load":
            editable_polygons(source)
        else:
            edit_polygon(source, target, changed(target, dbu), tmp_path / "stale.oas")
    assert not (tmp_path / "stale.oas").exists()


def test_exclusive_publication_never_overwrites_a_destination_race(source, tmp_path, monkeypatch):
    targets, dbu = editable_polygons(source)
    target = targets[0]
    destination = tmp_path / "immutable.oas"
    publication = "rename" if polygon_edit.os.name == "nt" else "link"
    publish = getattr(polygon_edit.os, publication)

    def occupied_link(temporary, path):
        Path(path).write_bytes(b"existing revision created during export")
        return publish(temporary, path)

    monkeypatch.setattr(polygon_edit.os, publication, occupied_link)
    with pytest.raises(SimulationError, match="existing files are never replaced"):
        edit_polygon(source, target, changed(target, dbu), destination)
    assert destination.read_bytes() == b"existing revision created during export"
    assert not list(tmp_path.glob(".layout-edit-*"))
    assert file_hash(source.layout.path) == source.layout.sha256


def test_reimport_failure_and_multi_polygon_are_not_published(source, tmp_path, monkeypatch):
    targets, dbu = editable_polygons(source)
    target = targets[0]
    with pytest.raises(SimulationError, match="exactly one polygon"):
        edit_polygon(source, target, MultiPolygon([target.geometry]), tmp_path / "multi.oas")
    document_type = polygon_edit.LayoutDocument

    def failing_reimport(path, **options):
        if Path(path).name.startswith(".layout-edit-"):
            raise SimulationError("Independent reimport failure")
        return document_type(path, **options)

    monkeypatch.setattr(polygon_edit, "LayoutDocument", failing_reimport)
    with pytest.raises(SimulationError, match="Independent reimport failure"):
        edit_polygon(source, target, changed(target, dbu), tmp_path / "failed.oas")
    assert not (tmp_path / "failed.oas").exists() and not (tmp_path / "multi.oas").exists()
    assert not list(tmp_path.glob(".layout-edit-*"))
    assert file_hash(source.layout.path) == source.layout.sha256
