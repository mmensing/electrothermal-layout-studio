# SPDX-License-Identifier: MPL-2.0
"""Export regressions for scientific support, scope and portable artifact names."""
from __future__ import annotations

import csv
import json
import os
from copy import deepcopy

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import meshio
import numpy as np
import pytest

from etlayout.io.export import _filenames, export_field_plot, export_results
from etlayout.models import PlanarMesh, Project, SimulationResult


@pytest.fixture
def result():
    # Three disjoint triangles plus an unused extreme point. Neither the gap nor
    # the extreme point is part of a layer's quantitative field support.
    points = np.array([[0, 0], [1, 0], [0, 1], [4, 0], [5, 0], [4, 1],
                       [8, 0], [9, 0], [8, 1], [1e6, 1e6]], dtype=float) * 1e-6
    mesh = PlanarMesh(points, np.array([[0, 1, 2], [3, 4, 5], [6, 7, 8]]),
                      {"layer:metal": np.arange(3), "heat:middle": np.array([1])})
    return SimulationResult(mesh=mesh, fields={"metal": {
        "potential": np.array([1, 2, 3, 4, 5, 6, np.nan, 8, 9, 999.]),
        "temperature": np.array([300., 301., 302., 303., 304., 305., 306., 307., 308., 999.]),
        "current_density": np.array([[3., 4.], [0., 10.], [np.nan, np.nan]]),
        "joule_density": np.array([1., 2., np.nan]),
    }}, metrics={"thermal_reference": 300., "total_current": 9.},
        terminals={"drive": {"current": 9., "voltage": 1.}}, converged=True,
        configuration_hash="fixture-sha256")


def read_manifest(path):
    return json.loads((path / "manifest.json").read_text(encoding="utf-8"))


def test_collision_names_are_portable_and_manifest_preserves_ids(result, tmp_path):
    names = ["metal", "Metal", "a/b", "a?b", "CON", "con.txt", "...", "材料",
             "a" * 120 + "x", "a" * 120 + "y", "simple"]
    values = result.fields["metal"]
    result.fields = {name: deepcopy(values) for name in names}
    result.mesh.regions = {"layer:" + name: np.arange(3) for name in names}
    export_results(result, tmp_path, plots=False)
    manifest = read_manifest(tmp_path)
    assert list(manifest["layers"]) == names
    filenames = [value[key] for value in manifest["layers"].values() for key in ("vtu", "nodes", "elements")]
    assert len(filenames) == len(set(name.casefold() for name in filenames))
    assert all((tmp_path / name).is_file() for name in filenames)
    assert all(len(name) <= 120 and not any(character in name for character in '/\\:*?"<>|') for name in filenames)
    assert all(name.split(".")[0].upper() not in {"CON", "NUL", "AUX", "PRN"} for name in filenames)
    assert manifest["layers"]["simple"]["vtu"] == "simple.vtu"
    for entry in manifest["layers"].values():
        assert len(meshio.read(tmp_path / entry["vtu"]).cells_dict["triangle"]) == 3


def test_allocator_checks_cross_artifact_and_reserved_file_collisions():
    specifications = [(("first", "nodes"), "run_nodes", ".csv"),
                      (("second", "data"), "RUN_nodes", ".csv"),
                      (("third", "metadata"), "results", ".json"),
                      (("fourth", "plot"), "simple", ".png")]
    names = _filenames(specifications)
    assert len({name.casefold() for name in names.values()}) == 4
    assert "results.json" not in names.values()
    assert names["fourth", "plot"] == "simple.png"
    assert names == _filenames(list(reversed(specifications)))


def test_selected_temperature_rise_uses_nodal_data_and_remaps_region(result, tmp_path):
    export_results(result, tmp_path, plots=False, layers=["metal"], field="temperature_rise", region="heat:middle",
                   colormap="magma", limits=(3.5, 4.5))
    manifest = read_manifest(tmp_path)
    layer = manifest["layers"]["metal"]
    assert layer["fields"]["temperature_rise"]["location"] == "element_local_node"
    grid = meshio.read(tmp_path / layer["vtu"])
    assert grid.points.shape == (3, 3)
    np.testing.assert_array_equal(grid.cells_dict["triangle"], [[0, 1, 2]])
    np.testing.assert_allclose(grid.point_data["temperature_rise"], [3., 4., 5.])
    assert not grid.cell_data
    with (tmp_path / layer["nodes"]).open(encoding="utf-8-sig", newline="") as stream:
        rows = list(csv.reader(stream))
    assert rows[0][-1] == "temperature_rise [K]"
    assert [int(row[0]) for row in rows[1:]] == [3, 4, 5]
    assert manifest["selection"]["limits"] == [3.5, 4.5]
    assert manifest["selection"]["colormap"] == "magma"


def test_plot_uses_finite_support_only_with_palette_range_and_log(result, tmp_path, monkeypatch):
    from matplotlib.axes import Axes
    from matplotlib.colors import LogNorm
    actual = Axes.tripcolor
    observed = {}

    def inspect(self, triangles, *args, **kwargs):
        observed.update(triangles=triangles, kwargs=kwargs)
        return actual(self, triangles, *args, **kwargs)

    monkeypatch.setattr(Axes, "tripcolor", inspect)
    path = export_field_plot(result, "metal", "current_density", tmp_path / "selected.svg", True,
                             region="heat:middle", colormap="viridis", limits=(2., 8.))
    assert path.is_file()
    tri, kwargs = observed["triangles"], observed["kwargs"]
    assert tri.x.min() == pytest.approx(4.) and tri.x.max() == pytest.approx(5.)
    assert len(tri.x) == 3 and len(tri.triangles) == 1
    assert kwargs["cmap"].name == "viridis"
    assert isinstance(kwargs["norm"], LogNorm)
    assert (kwargs["norm"].vmin, kwargs["norm"].vmax) == (2., 8.)
    np.testing.assert_allclose(kwargs["facecolors"], [10.])
    text = path.read_text(encoding="utf-8")
    assert "log scale" in text and "Manual color range" in text
    assert all(line == line.rstrip() for line in text.splitlines())


def test_nodal_plot_drops_partial_nan_triangles_and_unused_nodes(result, tmp_path, monkeypatch):
    from matplotlib.axes import Axes
    original = Axes.tricontourf
    observed = {}

    def inspect(self, tri, values=None, **kwargs):
        observed.update(tri=tri, values=values)
        return original(self, tri, values, **kwargs)

    monkeypatch.setattr(Axes, "tricontourf", inspect)
    export_field_plot(result, "metal", "potential", tmp_path / "potential.png")
    assert len(observed["tri"].x) == 6
    assert len(observed["tri"].triangles) == 2
    assert np.isfinite(observed["values"]).all()
    assert observed["tri"].x.max() == pytest.approx(5.)


def test_displayed_flat_nodal_plot_uses_element_means(result, tmp_path, monkeypatch):
    from matplotlib.axes import Axes
    original = Axes.tripcolor
    observed = {}

    def inspect(self, tri, **kwargs):
        observed.update(kwargs)
        return original(self, tri, **kwargs)

    monkeypatch.setattr(Axes, "tripcolor", inspect)
    export_field_plot(result, "metal", "potential", tmp_path / "flat.png", provenance={"smooth": False})
    assert observed["shading"] == "flat"
    np.testing.assert_allclose(observed["facecolors"], [2., 5.])


def test_smooth_log_plot_uses_geometric_scalar_contours(result, tmp_path, monkeypatch):
    from matplotlib.axes import Axes
    original = Axes.tricontourf
    observed = {}

    def inspect(self, tri, values, **kwargs):
        observed.update(values=values, **kwargs)
        return original(self, tri, values, **kwargs)

    monkeypatch.setattr(Axes, "tricontourf", inspect)
    export_field_plot(result, "metal", "potential", tmp_path / "smooth.png", True,
                      limits=(1., 100.), colormap="magma", provenance={"smooth": True})
    assert len(observed["levels"]) == 257
    assert observed["levels"][128] == pytest.approx(10.)
    np.testing.assert_allclose(observed["values"], [1., 2., 3., 4., 5., 6.])


def test_constant_field_uses_midpoint_color_and_one_truthful_tick(result, tmp_path, monkeypatch):
    from matplotlib.axes import Axes
    from matplotlib.colorbar import Colorbar

    from etlayout.visualization.palettes import get_colormap
    original, original_ticks = Axes.tricontourf, Colorbar.set_ticks
    observed = {}
    result.fields["metal"]["potential"][:] = 5.

    def inspect(self, tri, values, **kwargs):
        observed["cmap"] = kwargs["cmap"]
        return original(self, tri, values, **kwargs)

    def ticks(self, values, **kwargs):
        observed["ticks"] = values
        return original_ticks(self, values, **kwargs)

    monkeypatch.setattr(Axes, "tricontourf", inspect)
    monkeypatch.setattr(Colorbar, "set_ticks", ticks)
    path = export_field_plot(result, "metal", "potential", tmp_path / "constant.svg", colormap="viridis")
    np.testing.assert_allclose(observed["cmap"](0.), get_colormap("viridis")(.5))
    np.testing.assert_allclose(observed["cmap"](1.), get_colormap("viridis")(.5))
    assert observed["ticks"] == [5.]
    assert "constant" in path.read_text(encoding="utf-8")


@pytest.mark.parametrize("logarithmic", [False, True])
@pytest.mark.parametrize("manual", [False, True])
def test_near_constant_roundoff_range_exports(result, tmp_path, logarithmic, manual, monkeypatch):
    from matplotlib.axes import Axes
    original = Axes.tricontourf
    observed = {}

    def inspect(self, tri, values, **kwargs):
        observed.update(values=values, **kwargs)
        return original(self, tri, values, **kwargs)

    monkeypatch.setattr(Axes, "tricontourf", inspect)
    values = result.fields["metal"]["temperature"]
    values[:] = 300.
    values[1] = np.nextafter(300., 301.)
    raw = values.copy()
    limits = (300., values[1]) if manual else None
    path = export_field_plot(result, "metal", "temperature", tmp_path / "near-constant.png", logarithmic, limits=limits)
    assert path.is_file()
    assert np.all(np.diff(observed["levels"]) > 0)
    normalized = observed["norm"](observed["values"])
    assert np.isfinite(normalized).all()
    np.testing.assert_array_equal(values, raw)
    if manual:
        assert np.ptp(normalized) == 1.
    else:
        assert np.ptp(normalized) < 1e-8
        np.testing.assert_allclose(normalized, .5, atol=1e-8, rtol=0.)


def test_difference_exports_scalar_array_and_paired_provenance_not_original_metrics(result, tmp_path):
    result.fields = {"metal": {"current_density": np.array([-2., 3., np.nan])}}
    provenance = {"kind": "difference", "operation": "current_minus_comparison",
                  "quantity": "difference of magnitudes", "field": "current_density", "layer": "metal",
                  "sources": [{"configuration_hash": "current-hash"}, {"configuration_hash": "comparison-hash"}]}
    result.configuration = {"visualization": provenance}
    result.converged = False
    export_results(result, tmp_path, plots=False, layers=["metal"], field="current_density", colormap="coolwarm")
    manifest = read_manifest(tmp_path)
    metadata = json.loads((tmp_path / "results.json").read_text(encoding="utf-8"))
    assert metadata["metrics"] == {} and metadata["terminals"] == {}
    assert manifest["visualization"]["sources"][1]["configuration_hash"] == "comparison-hash"
    layer = manifest["layers"]["metal"]
    assert layer["fields"]["current_density"]["quantity"] == "scalar"
    assert layer["fields"]["current_density"]["display_limits"] == [-3., 3.]
    grid = meshio.read(tmp_path / layer["vtu"])
    np.testing.assert_allclose(grid.cell_data_dict["current_density"]["triangle"], [-2., 3.])
    csv_text = (tmp_path / layer["elements"]).read_text(encoding="utf-8-sig")
    assert "current_density [A/m²]" in csv_text and "current_density_x" not in csv_text
    with np.load(tmp_path / "difference-fields.npz") as arrays:
        key = layer["fields"]["current_density"]["array_key"]
        np.testing.assert_allclose(arrays[key], [-2., 3., np.nan])
    assert not (tmp_path / "simulation.etl").exists()
    report = (tmp_path / "report.html").read_text(encoding="utf-8")
    assert "Derived difference" in report and "Paired-run provenance" in report
    assert "NOT CONVERGED source" in report
    assert "total current</td>" not in report
    export_results(result, tmp_path / "region", plots=False, layers=["metal"], field="current_density", region="heat:middle")
    selection = read_manifest(tmp_path / "region")["layers"]["metal"]
    with np.load(tmp_path / "region" / "difference-fields.npz") as arrays:
        np.testing.assert_array_equal(arrays[selection["element_indices_key"]], [1])
        np.testing.assert_allclose(arrays[selection["fields"]["current_density"]["array_key"]], [np.nan, 3., np.nan])


@pytest.mark.parametrize("limits,logarithmic", [((2., 1.), False), ((1., 1.), False), ((0., 1.), True), ((1., np.inf), False)])
def test_invalid_ranges_are_rejected_before_export(result, tmp_path, limits, logarithmic):
    with pytest.raises(ValueError, match="color limits|Color limits"):
        export_results(result, tmp_path / "invalid", limits=limits, logarithmic=logarithmic)
    assert not (tmp_path / "invalid").exists()


def test_gui_export_has_actual_colorbar_metadata_and_difference_report(qtbot, result, tmp_path):
    from etlayout.gui.export import export_html_report, export_view
    from etlayout.gui.viewer import LayoutView
    view = LayoutView()
    qtbot.addWidget(view)
    view.resize(800, 600)
    view.show()
    view.show_field(result, "metal", "current_density", limits=(2., 8.), colormap="magma", region="heat:middle")
    view.fit_field()
    qtbot.wait(30)
    path = tmp_path / "map.svg"
    export_view(view, path, "Current density · middle region")
    metadata = json.loads(path.with_suffix(".svg.json").read_text(encoding="utf-8"))
    assert metadata["limits"] == [2., 8.]
    assert metadata["colormap"] == "magma" and metadata["region"] == "heat:middle"
    assert metadata["manual_limits"] is True
    svg = path.read_text(encoding="utf-8")
    assert "out-of-range colors clipped" in svg and "Volumetric current density" in svg
    project = Project(name="Difference fixture")
    project.thresholds = {"total_current": 1.}
    result.configuration = project.to_dict()
    provenance = {"kind": "difference", "sources": [{"configuration_hash": "A"}, {"configuration_hash": "B"}]}
    result.configuration["visualization"] = provenance
    result.converged = False
    export_html_report(project, result, view, tmp_path / "report.html", "Difference map")
    report = (tmp_path / "report.html").read_text(encoding="utf-8")
    assert "Derived difference" in report and "Paired-run provenance" in report
    assert "NOT CONVERGED source" in report
    assert "Engineering threshold exceeded:" not in report
    assert "<h2>Computed metrics</h2>" not in report
    assert not result.diagnostics
