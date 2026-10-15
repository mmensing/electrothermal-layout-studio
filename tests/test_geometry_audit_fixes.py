# SPDX-License-Identifier: MPL-2.0

import klayout.db as kdb
import pytest
from shapely.geometry import MultiPolygon, box
from shapely.geometry.base import BaseGeometry

from etlayout.connectivity.analyze import analyze_connectivity
from etlayout.geometry.layout import LayoutDocument
from etlayout.geometry.prepare import prepare_geometry
from etlayout.models import (
    Cancelled,
    Interface,
    LayoutSource,
    Material,
    PhysicalLayer,
    PreparedGeometry,
    Project,
    RegionSpec,
    SimulationError,
    Terminal,
)
from etlayout.simulation.runner import run_project


def rectangle(*bounds):
    return RegionSpec(polygons=[{"bounds": [v * 1e-6 for v in bounds]}])


def array_layout(path, nx=200, ny=200):
    layout = kdb.Layout()
    layout.dbu = .001
    child = layout.create_cell("CHILD")
    child.shapes(layout.layer(1, 0)).insert(kdb.Box(0, 0, 1000, 1000))
    top = layout.create_cell("TOP")
    top.insert(kdb.CellInstArray(child.cell_index(), kdb.Trans(),
                                kdb.Vector(2000, 0), kdb.Vector(0, 2000), nx, ny))
    layout.write(str(path))
    return LayoutDocument(path)


def test_explicit_substrate_and_roi_can_surround_imported_mask(tmp_path):
    document = array_layout(tmp_path / "one.gds", 1, 1)
    project = Project(layout=LayoutSource(document.path, "TOP"), layers=[
        PhysicalLayer("metal", "Metal", "cu", geometry=RegionSpec(sources=["1/0"])),
        PhysicalLayer("substrate", "Substrate", "si", z=-1e-6, role="thermal",
                      geometry=rectangle(-4.5, -4.5, 5.5, 5.5)),
    ])
    geometry = prepare_geometry(project, document=document)
    assert geometry.layers["substrate"].area == pytest.approx(100e-12)
    assert geometry.domain.bounds == pytest.approx([-4.5e-6, -4.5e-6, 5.5e-6, 5.5e-6])
    project.roi = rectangle(-10, -10, 10, 10)
    project.layers[1].geometry = RegionSpec(full_domain=True)
    geometry = prepare_geometry(project, document=document)
    assert geometry.layers["substrate"].area == pytest.approx(400e-12)
    assert geometry.layers["metal"].area == pytest.approx(1e-12)


def test_deliberate_thermal_clipping_is_reported():
    project = Project(layers=[PhysicalLayer("s", "Substrate", "si", role="thermal",
                                           geometry=rectangle(0, 0, 100, 100))],
                      roi=rectangle(0, 0, 10, 10))
    geometry = prepare_geometry(project)
    assert geometry.layers["s"].area == pytest.approx(100e-12)
    assert any("clips 99.0%" in item.message for item in geometry.diagnostics)


def test_roi_does_not_expand_unrelated_array_geometry(tmp_path, monkeypatch):
    document = array_layout(tmp_path / "array.gds")
    project = Project(layout=LayoutSource(document.path, "TOP"),
                      roi=rectangle(-.1, -.1, 1.1, 1.1),
                      layers=[PhysicalLayer("m", "Metal", "cu", geometry=RegionSpec(sources=["1/0"]))])
    import etlayout.geometry.layout as layout_module
    original = layout_module.Polygon
    conversions = []

    def counted(*args, **kwargs):
        conversions.append(1)
        return original(*args, **kwargs)

    monkeypatch.setattr(layout_module, "Polygon", counted)
    preview = {}
    result = prepare_geometry(project, document=document, masks_out=preview)
    assert result.layers["m"].area == pytest.approx(1e-12)
    assert preview["1/0"].area == pytest.approx(1e-12)
    assert len(conversions) <= 2  # Original + merged polygon, not 40,000 placements.


def test_mask_roi_and_full_domain_preserve_expected_semantics(tmp_path):
    document = array_layout(tmp_path / "array.gds", 2, 1)
    project = Project(layout=LayoutSource(document.path, "TOP"),
                      roi=RegionSpec(sources=["1/0"]),
                      layers=[PhysicalLayer("m", "Metal", "cu", geometry=RegionSpec(sources=["1/0"]))])
    result = prepare_geometry(project, document=document)
    assert result.domain.area == pytest.approx(2e-12)
    project.roi = RegionSpec(full_domain=True)
    assert prepare_geometry(project, document=document).domain.area == pytest.approx(3e-12)


def test_cut_detection_accepts_whole_island_but_rejects_true_cut():
    region = RegionSpec(polygons=[{"bounds": [0, 0, 10e-6, 10e-6]},
                                  {"bounds": [20e-6, 0, 30e-6, 10e-6]}])
    project = Project(layers=[PhysicalLayer("m", "Metal", "cu", geometry=region)],
                      roi=rectangle(0, 0, 10, 10))
    assert prepare_geometry(project).layers["m"].area == pytest.approx(100e-12)
    project.roi = rectangle(0, 0, 9, 10)
    with pytest.raises(SimulationError, match="acknowledge"):
        prepare_geometry(project)
    project.acknowledge_clipped_boundaries = True
    assert any("truncates" in item.message for item in prepare_geometry(project).diagnostics)


def test_hierarchy_pages_access_late_arrays_without_enumeration(tmp_path, monkeypatch):
    document = array_layout(tmp_path / "array.oas")
    monkeypatch.setattr(document, "_placements", lambda *args: pytest.fail("Array was expanded"))
    page = document.instances_page("TOP", offset=39990, limit=7)
    assert page["total"] == 40000
    assert page["has_more"]
    assert [item["path"] for item in page["items"]] == [[i] for i in range(39990, 39997)]
    last = document.instances_page("TOP", offset=39999, limit=7)
    assert not last["has_more"]
    assert document.extract("TOP", [39999])["1/0"].bounds == pytest.approx([398e-6, 398e-6, 399e-6, 399e-6])
    assert document.instances_page("TOP", offset=50000)["items"] == []


def test_immediate_child_page_has_no_global_recursive_cap(tmp_path):
    layout = kdb.Layout()
    leaf = layout.create_cell("LEAF")
    leaf.shapes(layout.layer(1, 0)).insert(kdb.Box(0, 0, 1000, 1000))
    middle = layout.create_cell("MIDDLE")
    middle.insert(kdb.CellInstArray(leaf.cell_index(), kdb.Trans(), kdb.Vector(2000, 0), kdb.Vector(), 10, 1))
    top = layout.create_cell("TOP")
    top.insert(kdb.CellInstArray(middle.cell_index(), kdb.Trans(), kdb.Vector(22000, 0), kdb.Vector(), 1001, 1))
    path = tmp_path / "nested.gds"
    layout.write(str(path))
    document = LayoutDocument(path)
    page = document.instances_page("TOP", [999], offset=7, limit=5)
    assert page["total"] == 10
    assert [item["path"] for item in page["items"]] == [[999, 7], [999, 8], [999, 9]]
    assert page["items"][0]["bounds"] == pytest.approx(document.cell_bounds("TOP", [999, 7]))


def test_paged_array_transform_matches_klayout_iterator(tmp_path):
    layout = kdb.Layout()
    leaf = layout.create_cell("LEAF")
    leaf.shapes(layout.layer(1, 0)).insert(kdb.Box(0, 0, 2000, 1000))
    top = layout.create_cell("TOP")
    top.insert(kdb.CellInstArray(leaf.cell_index(), kdb.ICplxTrans(2., 30., True, 5000, 6000),
                                 kdb.Vector(10000, 3000), kdb.Vector(-2000, 7000), 3, 2))
    path = tmp_path / "complex.oas"
    layout.write(str(path))
    document = LayoutDocument(path)
    legacy = document.instances("TOP")
    paged = document.instances_page("TOP", offset=1, limit=4)["items"]
    assert paged == legacy[1:5]
    assert document.geometry_bounds("TOP", [4]) == pytest.approx(document.extract("TOP", [4])["1/0"].bounds)


def test_extraction_cache_is_defensive_and_cancellable(tmp_path, monkeypatch):
    document = array_layout(tmp_path / "array.gds", 1, 1)
    result = document.extract("TOP")
    result.clear()
    monkeypatch.setattr(document, "_selection", lambda *args: pytest.fail("Cache was not reused"))
    assert document.extract("TOP")["1/0"].area == pytest.approx(1e-12)
    with pytest.raises(Cancelled):
        document.extract("TOP", cancel=lambda: True)


def test_point_touch_terminals_do_not_conflict_on_independent_components():
    first, second = rectangle(0, 0, 10, 10), rectangle(10, 10, 20, 20)
    project = Project(layers=[PhysicalLayer("m", "Metal", "cu", geometry=RegionSpec(polygons=first.polygons + second.polygons))],
                      terminals=[Terminal("a", "m", first, "ground"), Terminal("b", "m", second, "voltage", 1)])
    report = analyze_connectivity(project, prepare_geometry(project))
    assert len(report.nets) == 2
    assert not [item for item in report.diagnostics if item.severity == "error"]
    project.layers[0].geometry = rectangle(0, 0, 20, 20)
    report = analyze_connectivity(project, prepare_geometry(project))
    assert any("Conflicting" in item.message for item in report.diagnostics)


def test_density_current_is_integrated_separately_per_net():
    regions = RegionSpec(polygons=rectangle(0, 0, 10, 10).polygons + rectangle(20, 0, 30, 10).polygons)
    density = RegionSpec(polygons=rectangle(4, 4, 5, 5).polygons + rectangle(24, 4, 25, 5).polygons)
    project = Project(layers=[PhysicalLayer("m", "Metal", "cu", geometry=regions)],
                      materials={"cu": Material("cu", "Copper")},
                      terminals=[Terminal("density", "m", density, "density", 1e6),
                                 Terminal("a", "m", rectangle(0, 0, 1, 10), "current", -1e-6),
                                 Terminal("b", "m", rectangle(29, 0, 30, 10), "current", -1e-6)])
    project.solver.floating_policy = "reference"
    report = analyze_connectivity(project, prepare_geometry(project))
    assert not [item for item in report.diagnostics if item.severity == "error"]
    result = run_project(project)
    assert result.converged
    assert result.metrics["current_balance_error"] < 1e-10
    project.terminals[2].value = -2e-6
    report = analyze_connectivity(project, prepare_geometry(project))
    assert len([item for item in report.diagnostics if "unbalanced" in item.message]) == 1


def test_sparse_connectivity_uses_spatial_candidates(monkeypatch):
    polygons = [box(i * 2e-6, 0, (i * 2 + 1) * 1e-6, 1e-6) for i in range(1000)]
    shape = MultiPolygon(polygons)
    project = Project(layers=[PhysicalLayer("a", "A", "cu"), PhysicalLayer("b", "B", "cu")],
                      interfaces=[Interface("via", "a", "b", electrical_kind="ideal")])
    geometry = PreparedGeometry(domain=box(*shape.bounds), layers={"a": shape, "b": shape}, interfaces={"via": shape})
    original = BaseGeometry.intersection
    calls = []

    def counted(self, other, *args, **kwargs):
        calls.append(1)
        return original(self, other, *args, **kwargs)

    monkeypatch.setattr(BaseGeometry, "intersection", counted)
    report = analyze_connectivity(project, geometry)
    assert len(report.nets) == 1000
    assert len(report.interfaces["via"]) == 1000
    assert len(calls) <= 3 * len(polygons)  # No within/cross-layer Cartesian scans.
    with pytest.raises(Cancelled):
        analyze_connectivity(project, geometry, cancel=lambda: True)


def test_geometry_extent_excludes_text(tmp_path):
    document = array_layout(tmp_path / "simple.gds", 1, 1)
    layout = document._layout
    layout.cell("TOP").shapes(layout.layer(1, 0)).insert(kdb.Text("Label", kdb.Trans(1000000, 1000000)))
    assert document.geometry_bounds("TOP") == pytest.approx([0, 0, 1e-6, 1e-6])


@pytest.mark.parametrize("mode,joule,expects_error", [("thermal", False, False),
                                                     ("thermal", True, True),
                                                     ("electrothermal", False, True)])
def test_unused_electrical_reference_does_not_invalidate_thermal_setup(mode, joule, expects_error):
    project = Project(layers=[PhysicalLayer("m", "Metal", "cu", geometry=rectangle(0, 0, 10, 10))])
    project.solver.mode = mode
    project.solver.thermal_include_joule = joule
    report = analyze_connectivity(project, prepare_geometry(project))
    assert any(item.severity == "error" for item in report.diagnostics) == expects_error
