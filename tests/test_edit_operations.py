# SPDX-License-Identifier: MPL-2.0
"""Independent edit geometry and exact, read-only support consequences."""
from copy import deepcopy

import klayout.db as kdb
import pytest
from shapely.geometry import Polygon, box

from etlayout.geometry.edit_impacts import EditImpactPreview
from etlayout.geometry.polygon_edit import (
    EditablePolygon,
    delete_vertex,
    editable_polygons,
    insert_midpoint,
    snap_coordinate,
    translated_rings,
)
from etlayout.geometry.prepare import geometry_region, prepare_geometry
from etlayout.io.project import file_hash
from etlayout.models import (
    DeviceGroup,
    Interface,
    Material,
    MeasurementSpec,
    PhysicalLayer,
    PhysicalPort,
    PortLanding,
    Project,
    RegionSpec,
    RoutingRecipe,
    SimulationError,
    SupportSelector,
    Terminal,
)


@pytest.fixture
def holed_rings():
    return [[(0., 0.), (12e-6, 0.), (12e-6, 12e-6), (0., 12e-6)],
            [(3e-6, 3e-6), (6e-6, 3e-6), (6e-6, 6e-6), (3e-6, 6e-6)]]


@pytest.mark.parametrize("ring", [0, 1])
def test_insertion_and_deletion_preserve_exact_support_and_other_ring(holed_rings, ring):
    original = deepcopy(holed_rings)
    inserted = insert_midpoint(original, ring, 3, 1e-9, 100)
    assert len(inserted[ring]) == 5 and Polygon(inserted[0], inserted[1:]).equals(Polygon(original[0], original[1:]))
    restored = delete_vertex(inserted, ring, 4, 1e-9)
    assert restored == original and holed_rings == original
    # Independent closing-edge midpoint and unchanged opposite ring.
    assert inserted[ring][4] == pytest.approx(tuple((a + b) / 2 for a, b in zip(original[ring][3], original[ring][0], strict=True)))
    assert inserted[1 - ring] == original[1 - ring]


@pytest.mark.parametrize("grid", [0, -1, 1.5, True])
def test_grid_must_be_integer_database_units(grid):
    with pytest.raises(SimulationError, match="positive integer"):
        snap_coordinate(1e-6, 1e-9, grid)


def test_safe_deletion_refuses_triangular_hole_and_coarse_insertion_collapse(holed_rings):
    triangle = [holed_rings[0], [(3e-6, 3e-6), (6e-6, 3e-6), (3e-6, 6e-6)]]
    with pytest.raises(SimulationError, match="at least three"):
        delete_vertex(triangle, 1, 0, 1e-9)
    with pytest.raises(SimulationError, match="coincident"):
        insert_midpoint([[(0., 0.), (1e-9, 0.), (0., 1e-9)]], 0, 0, 1e-9)


def test_translation_snaps_displacement_without_resnapping_original_vertices(holed_rings):
    holed_rings[0][1] = (12.001e-6, 0.)
    actual = translated_rings(holed_rings, 1.24e-6, -2.26e-6, 1e-9, 100)
    for before, after in zip(holed_rings, actual, strict=True):
        for (x, y), (a, b) in zip(before, after, strict=True):
            assert a - x == pytest.approx(1.2e-6) and b - y == pytest.approx(-2.3e-6)
    assert actual[0][1][0] == pytest.approx(13.201e-6)
    assert Polygon(actual[0], actual[1:]).area == pytest.approx(Polygon(holed_rings[0], holed_rings[1:]).area)
    with pytest.raises(SimulationError, match="finite"):
        translated_rings(holed_rings, float("inf"), 0., 1e-9)


@pytest.fixture
def preview_setup():
    geometry = box(0., 0., 4e-6, 4e-6)
    target = EditablePolygon("1/0", (), 0, geometry, "TOP", "polygon", "a" * 64)
    p = Project()
    p.roi = geometry_region(geometry)
    p.layers = [PhysicalLayer("source", "Source", "metal", geometry=RegionSpec(sources=["1/0"])),
                PhysicalLayer("drain", "Drain", "metal", geometry=geometry_region(geometry))]
    footprint = geometry_region(box(0., 0., .5e-6, 2e-6))
    p.terminals = [Terminal("power", "source", footprint)]
    p.interfaces = [Interface("device", "source", "drain", footprint)]
    p.power.ports = [PhysicalPort("input", [PortLanding("power", .01)])]
    selector = SupportSelector(layers=["source"], geometry=footprint)
    p.power.devices = [DeviceGroup("inner", selector, active_width=2e-6),
                       DeviceGroup("outer", SupportSelector(devices=["inner"]))]
    p.power.measurements = [MeasurementSpec("direct", selector=selector),
                            MeasurementSpec("nested", selector=SupportSelector(devices=["outer"])),
                            MeasurementSpec("remote", selector=SupportSelector(layers=["source"], geometry=geometry_region(box(2e-6, 0., 3e-6, 2e-6))))]
    p.power.rules.keepouts = [geometry_region(box(0., 0., .2e-6, 1e-6))]
    p.power.rules.protected_masks = ["1/0"]
    p.power.recipes = [RoutingRecipe("feed", mask="1/0", source_hash=target.source_hash)]
    return p, target


def test_exact_nested_and_direct_support_preview_loss_without_mutating_definitions(preview_setup):
    p, target = preview_setup
    original = p.to_dict()
    impacts = EditImpactPreview(p, [target], target).compare(box(1e-6, 0., 4e-6, 4e-6))
    by_key = {(item.category, item.object_id): item for item in impacts}
    assert all((category, key) in by_key for category, key in [
        ("Contact", "power"), ("Power port", "input"), ("Interface", "device"),
        ("Device group", "inner"), ("Device group", "outer"), ("Measurement", "direct"),
        ("Measurement", "nested"), ("Routing keepout", "1"), ("Protected mask", "1/0"), ("Routing recipe", "feed")])
    assert ("Measurement", "remote") not in by_key
    assert by_key["Measurement", "direct"].message == by_key["Measurement", "nested"].message
    assert "Support is lost" in by_key["Measurement", "direct"].message
    assert "Area 1 → 0 µm²" in by_key["Measurement", "direct"].message
    assert "active width and area stay unchanged" in by_key["Device group", "inner"].message
    assert "stale" in by_key["Routing recipe", "feed"].message and p.to_dict() == original


def test_partial_nested_support_and_overlapping_original_shape_are_preserved(preview_setup):
    p, target = preview_setup
    preview = EditImpactPreview(p, [target], target)
    impacts = preview.compare(box(.25e-6, 0., 4e-6, 4e-6))
    measurements = [item for item in impacts if item.category == "Measurement"]
    assert len(measurements) == 2 and all("Area 1 → 0.5 µm²" in item.message for item in measurements)
    # An overlapping original shape keeps the same effective mask support.
    overlap = EditablePolygon(target.mask, (), 1, target.geometry, "TOP", "polygon", target.source_hash)
    remaining = EditImpactPreview(p, [target, overlap], target).compare(box(1e-6, 0., 4e-6, 4e-6))
    assert remaining and all(item.category in {"Routing keepout", "Routing recipe"} for item in remaining)


def test_component_selector_is_explicitly_unassessed_not_a_false_pass(preview_setup):
    p, target = preview_setup
    p.power.measurements = [MeasurementSpec("component", selector=SupportSelector(regions=["component:1"]))]
    effects = EditImpactPreview(p, [target], target).compare(box(1e-6, 0., 4e-6, 4e-6))
    record = next(item for item in effects if item.category == "Measurement")
    assert "cannot be assessed" in record.message and "regenerated mesh" in record.message


def test_physical_via_replacement_and_parallel_thermal_support_use_shared_stack_resolution():
    outer = box(0., 0., 4e-6, 4e-6)
    via = box(1e-6, 1e-6, 2e-6, 2e-6)
    target = EditablePolygon("2/0", (), 0, via, "TOP", "polygon", "a" * 64)
    p = Project()
    p.roi = geometry_region(outer)
    p.layers = [PhysicalLayer("lower", "Lower", "metal", thickness=1e-6, z=0., geometry=geometry_region(outer)),
                PhysicalLayer("upper", "Upper", "metal", thickness=1e-6, z=3e-6, geometry=geometry_region(outer)),
                PhysicalLayer("oxide", "Oxide", "oxide", thickness=2e-6, z=1e-6, role="insulator", geometry=geometry_region(outer))]
    p.interfaces = [Interface("via", "lower", "upper", RegionSpec(sources=["2/0"]),
                              model_kind="physical_via", height_mode="stack", replaces_layers=["oxide"]),
                    Interface("gap", "lower", "upper", RegionSpec(full_domain=True), thermal_kind="conductance")]
    p.power.measurements = [MeasurementSpec("oxide_area", selector=SupportSelector(layers=["oxide"]))]
    original = p.to_dict()
    preview = EditImpactPreview(p, [target], target)
    records = {(item.category, item.object_id): item for item in preview.compare(box(1e-6, 1e-6, 2.5e-6, 2e-6))}
    assert "Area 15 → 14.5 µm²" in records["Measurement", "oxide_area"].message
    assert "Area 15 → 14.5 µm²" in records["Interface", "gap"].message
    assert "Area 1 → 1.5 µm²" in records["Interface", "via"].message
    assert p.to_dict() == original and preview.project.to_dict() == original
    p.interfaces[0].enclosure = .1e-6
    guarded = EditImpactPreview(p, [target], target)
    with pytest.raises(SimulationError, match="enclosure"):
        guarded.compare(box(0., 1e-6, 2e-6, 2e-6))


def test_edge_boundary_preview_explicitly_requires_backend_revalidation(preview_setup):
    p, target = preview_setup
    p.terminals = [Terminal("edge", "source", RegionSpec(line=[[0., 0.], [0., 4e-6]]), kind="ground")]
    p.power.ports = []
    effects = EditImpactPreview(p, [target], target).compare(box(1e-6, 0., 4e-6, 4e-6))
    record = next(item for item in effects if item.category == "Contact")
    assert "Revalidation required (F6)" in record.message and "Support is lost" in record.message


def test_non_area_text_does_not_change_the_backend_or_preview_domain(tmp_path):
    layout = kdb.Layout()
    layout.dbu = .001
    top = layout.create_cell("TOP")
    layer = layout.layer(1, 0)
    top.shapes(layer).insert(kdb.Box(0, 0, 4000, 4000))
    top.shapes(layer).insert(kdb.Text("outside", kdb.Trans(1000000, 1000000)))
    path = tmp_path / "with-text.gds"
    layout.write(str(path))
    p = Project()
    p.layout.path, p.layout.cell, p.layout.sha256 = str(path), "TOP", file_hash(path)
    p.materials = {"metal": Material("metal", "Metal")}
    p.layers = [PhysicalLayer("metal", "Metal", "metal", geometry=RegionSpec(sources=["1/0"]))]
    targets, _ = editable_polygons(p)
    preview = EditImpactPreview(p, targets, targets[0])
    size_si = 4000 * (layout.dbu * 1e-6)
    assert preview.before[0].bounds == prepare_geometry(p).domain.bounds == (0., 0., size_si, size_si)
