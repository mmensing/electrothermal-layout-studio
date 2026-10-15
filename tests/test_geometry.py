# SPDX-License-Identifier: MPL-2.0
import klayout.db as kdb
import numpy as np
import pytest
from shapely.geometry import box

from etlayout.geometry.layout import LayoutDocument
from etlayout.geometry.prepare import prepare_geometry, resolve_region
from etlayout.models import (
    LayoutSource,
    PhysicalLayer,
    Project,
    RegionSpec,
    SimulationError,
    Terminal,
    ThermalBoundary,
)


def rectangle(x0=0., y0=0., x1=10e-6, y1=5e-6):
    return RegionSpec(polygons=[{"exterior": [[x0,y0],[x1,y0],[x1,y1],[x0,y1]]}])


@pytest.fixture(params=[".gds", ".oas"])
def hierarchy_file(tmp_path, request):
    layout = kdb.Layout()
    layout.dbu = .001
    layer = layout.layer(1, 0)
    child = layout.create_cell("CHILD")
    ring = kdb.Polygon(kdb.Box(0, 0, 10000, 6000))
    ring.insert_hole(kdb.Box(2000, 2000, 4000, 4000))
    child.shapes(layer).insert(ring)
    child.shapes(layout.layer(2, 5)).insert(kdb.Path([kdb.Point(0, 0), kdb.Point(10000, 0)], 1000, 500, 500))
    child.shapes(layer).insert(kdb.Text("NOT GEOMETRY", kdb.Trans(100000, 100000)))
    top = layout.create_cell("TOP")
    top.insert(kdb.CellInstArray(child.cell_index(), kdb.Trans(1, True, 20000, 30000),
                                 kdb.Vector(15000, 0), kdb.Vector(0, 20000), 2, 2))
    path = tmp_path / ("hierarchy" + request.param)
    layout.write(str(path))
    return path


def test_layout_hierarchy_array_paths_holes_and_text(hierarchy_file):
    document = LayoutDocument(hierarchy_file)
    assert document.top_cells == ["TOP"]
    assert set(document.cells) == {"TOP", "CHILD"}
    assert document.dbu_um == pytest.approx(.001)
    assert {x["key"] for x in document.layers} == {"1/0", "2/5"}
    placements = document.instances("TOP")
    assert len(placements) == 4
    masks = document.extract("TOP")
    assert masks["1/0"].area == pytest.approx(4 * 56e-12)
    assert masks["2/5"].area == pytest.approx(4 * 11e-12)
    selected = document.extract("TOP", placements[0]["path"])
    assert selected["1/0"].area == pytest.approx(56e-12)
    assert np.allclose(selected["1/0"].bounds, [20e-6, 30e-6, 26e-6, 40e-6])
    assert len(selected["1/0"].interiors) == 1
    roi = box(20e-6, 30e-6, 23e-6, 35e-6)
    clipped = document.extract("TOP", placements[0]["path"], roi)
    assert clipped["1/0"].equals_exact(selected["1/0"].intersection(roi), 1e-15)


def test_boolean_regions_holes_and_empty_spec():
    masks = {"1/0": box(0, 0, 10e-6, 10e-6), "2/0": box(2e-6, 2e-6, 8e-6, 8e-6)}
    result = resolve_region(RegionSpec(sources=["1/0", "2/0"], operation="difference"), masks, masks["1/0"])
    assert result.area == pytest.approx(64e-12)
    assert len(result.interiors) == 1
    assert resolve_region(RegionSpec(), masks, masks["1/0"]).is_empty
    with pytest.raises(SimulationError, match="not present"):
        resolve_region(RegionSpec(sources=["99/0"]), masks)
    with pytest.raises(SimulationError, match="Invalid polygon"):
        resolve_region(RegionSpec(polygons=[{"exterior": [[0,0],[1,1],[0,1],[1,0]]}]), {})


def test_live_intersection_exclusions_and_explicit_union_order():
    masks = {"upper": box(0, 0, 10, 8), "lower": box(2, 1, 8, 7),
             "intervening": box(3, 0, 5, 8), "via": box(6, 2, 7, 3)}
    explicit = box(3, 2, 4, 3)
    spec = RegionSpec(sources=["upper", "lower"], operation="intersection",
                      exclude_sources=["intervening", "via"], polygons=[{"bounds": explicit.bounds}])
    expected = masks["upper"].intersection(masks["lower"]).difference(
        masks["intervening"].union(masks["via"])).union(explicit)
    assert resolve_region(spec, masks).equals(expected)
    assert resolve_region(spec, masks).area == 24.
    # Exclusions are live masks, including when the participating layout changes.
    masks["lower"] = box(2, 1, 9, 7)
    assert resolve_region(spec, masks).area == 30.
    with pytest.raises(SimulationError, match="Excluded mask.*not present"):
        resolve_region(RegionSpec(exclude_sources=["missing"]), masks)


def test_roi_truncation_requires_acknowledgment():
    project = Project(layers=[PhysicalLayer("m", "Metal", "copper", geometry=rectangle())],
                      roi=rectangle(2e-6, 0, 8e-6, 5e-6))
    with pytest.raises(SimulationError, match="acknowledge"):
        prepare_geometry(project)
    project.acknowledge_clipped_boundaries = True
    result = prepare_geometry(project)
    assert result.layers["m"].area == pytest.approx(30e-12)
    assert any("truncates" in d.message for d in result.diagnostics)


def test_full_domain_thermal_layer_and_polygon_roi():
    metal = PhysicalLayer("m", "Metal", "copper", geometry=rectangle())
    substrate = PhysicalLayer("s", "Substrate", "silicon", role="thermal", geometry=RegionSpec(full_domain=True))
    project = Project(layers=[metal, substrate])
    result = prepare_geometry(project)
    assert result.layers["s"].equals(result.domain)


def test_nested_instance_transform_composition(tmp_path):
    layout = kdb.Layout()
    layout.dbu = .001
    shape = layout.create_cell("SHAPE")
    shape.shapes(layout.layer(1, 0)).insert(kdb.Box(0, 0, 2000, 1000))
    middle = layout.create_cell("MID")
    middle.insert(kdb.CellInstArray(shape.cell_index(), kdb.Trans(1, False, 10000, 5000)))
    top = layout.create_cell("TOP")
    top.insert(kdb.CellInstArray(middle.cell_index(), kdb.Trans(2, True, 30000, 40000)))
    path = tmp_path / "nested.gds"
    layout.write(str(path))
    document = LayoutDocument(path)
    all_geometry = document.extract("TOP")["1/0"]
    nested = document.extract("TOP", [0, 0])["1/0"]
    assert all_geometry.equals(nested)
    assert [i["path"] for i in document.instances("TOP", recursive=True)] == [[0], [0, 0]]


@pytest.fixture
def edge_selection_project(tmp_path):
    layout = kdb.Layout()
    layout.dbu = .001
    cell = layout.create_cell("EDGE_SUPPORT")
    cell.shapes(layout.layer(1, 0)).insert(kdb.Box(0, 0, 10000, 5000))
    source = tmp_path / "edge-support.gds"
    layout.write(str(source))
    return Project(layout=LayoutSource(str(source), cell.name),
                   layers=[PhysicalLayer("m", "Metal", "copper", geometry=RegionSpec(sources=["1/0"]))])


def _edge_selection(project, kind, region):
    if kind == "terminal":
        project.terminals = [Terminal("edge", "m", region, "ground")]
    else:
        project.thermal_boundaries = [ThermalBoundary("edge", "m", region)]


@pytest.mark.parametrize("kind", ["terminal", "thermal"])
@pytest.mark.parametrize("field,match", [("sources", "Mask.*not present"),
                                         ("exclude_sources", "Excluded mask.*not present")])
def test_edge_selection_validates_all_source_masks(edge_selection_project, kind, field, match):
    region = RegionSpec(line=[[0., 0.], [0., 5e-6]], **{field: ["missing/0"]})
    _edge_selection(edge_selection_project, kind, region)
    with pytest.raises(SimulationError, match=match):
        prepare_geometry(edge_selection_project)


@pytest.mark.parametrize("kind", ["terminal", "thermal"])
def test_edge_selection_resolves_cancelled_mask_support_and_preserves_snapping(edge_selection_project, kind):
    line = [[5e-16, 0.], [5e-16, 5e-6 + 5e-16]]
    requested_line = [point.copy() for point in line]
    region = RegionSpec(sources=["1/0"], exclude_sources=["1/0"], line=line)
    _edge_selection(edge_selection_project, kind, region)
    prepared = prepare_geometry(edge_selection_project)
    support = (prepared.terminals if kind == "terminal" else prepared.thermal_boundaries)["edge"]
    assert support.geom_type == "LineString"
    np.testing.assert_allclose(np.asarray(support.coords), [[0., 0.], [0., 5e-6]], rtol=0., atol=1e-20)
    assert region.line == requested_line  # Preparation cannot mutate the project draft.


@pytest.mark.parametrize("kind", ["terminal", "thermal"])
@pytest.mark.parametrize("area_terms", [
    {"sources": ["1/0"]},
    {"sources": ["1/0"], "exclude_sources": ["1/0"],
     "polygons": [{"bounds": [1e-6, 1e-6, 2e-6, 2e-6]}]},
    {"full_domain": True},
], ids=["mask-area", "explicit-union-after-exclusion", "full-domain-area"])
def test_edge_selection_rejects_ambiguous_area_union(edge_selection_project, kind, area_terms):
    region = RegionSpec(line=[[0., 0.], [0., 5e-6]], **area_terms)
    _edge_selection(edge_selection_project, kind, region)
    with pytest.raises(SimulationError, match="combined area and edge support is ambiguous.*separate boundary objects"):
        prepare_geometry(edge_selection_project)
