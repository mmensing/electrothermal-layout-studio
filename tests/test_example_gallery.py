# SPDX-License-Identifier: MPL-2.0
"""Gallery contracts: executable catalogue, truthful fields, portable links and cache provenance."""
from __future__ import annotations

import importlib.util
import json
import re
from pathlib import Path

import numpy as np
import pytest

from etlayout.example_catalog import EXAMPLE_CATALOG, example_info
from etlayout.examples import EXAMPLE_NAMES, make_example
from etlayout.io.project import physics_hash, save_project
from etlayout.models import PlanarMesh, SimulationResult

_SCRIPT = Path(__file__).resolve().parents[1] / "scripts/build_example_gallery.py"
_SPEC = importlib.util.spec_from_file_location("gallery", _SCRIPT)
gallery = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(gallery)


def test_catalogue_covers_every_executable_example():
    assert list(EXAMPLE_CATALOG) == EXAMPLE_NAMES
    for name, info in EXAMPLE_CATALOG.items():
        assert info.name == name
        assert example_info(info.id.lower()) is info
        assert info.learning_goal and info.physical_scope
        assert info.recommended_mode in {"electrical", "thermal", "electrothermal"}
        assert info.to_dict()["id"] == info.id
    with pytest.raises(ValueError, match="Unknown example"):
        example_info("E100")


def test_field_selection_preserves_holes_and_excludes_inactive_values():
    mesh = PlanarMesh(np.array([[0., 0.], [1., 0.], [0., 1.], [2., 2.]]),
                     np.array([[0, 1, 2], [1, 2, 3]]), {"layer:metal": np.array([0])})
    result = SimulationResult(mesh, fields={"metal": {"potential": np.array([0., 1., 2., 999.]),
                                                     "current_density": np.array([[3., 4.], [np.nan, np.nan]])}})
    _, ids, nodal, low, high = gallery.field_data(result, "metal", "potential")
    assert ids.tolist() == [0] and nodal and (low, high) == (0., 2.)
    values, ids, nodal, low, high = gallery.field_data(result, "metal", "current_density")
    assert not nodal and ids.tolist() == [0] and low == high == 5.
    assert np.isnan(values[1])


def test_geometry_only_build_has_real_downloads_and_no_result_claim(tmp_path):
    output, examples = tmp_path / "assets", tmp_path / "examples"
    markdown = tmp_path / "gallery.md"
    manifest = gallery.build_gallery(["E1"], output, markdown, examples, tmp_path / "cache", no_solve=True)
    record = manifest["examples"][0]
    assert "result" not in record and record["field"] is None
    assert record["mode"] == "electrical"
    assert record["physics_hash"]
    document = markdown.read_text(encoding="utf-8")
    assert "geometry only; no numerical result" in document
    assert "--mode electrical" in document
    for target in re.findall(r"\]\(([^)]+)\)", document):
        if target.startswith("#"):
            continue
        assert (markdown.parent / target).is_file(), target
    for target in record["downloads"].values():
        assert (output / target).is_file()
    assert (output / "E1_straight.png").stat().st_size > 10_000
    assert "Prepared geometry" in (output / "E1_straight.svg").read_text(encoding="utf-8")
    assert json.loads((output / "manifest.json").read_text(encoding="utf-8"))["examples"] == manifest["examples"]


def test_cached_result_requires_matching_physics_source_and_version(tmp_path):
    project = make_example("E1", tmp_path / "examples")
    mesh = PlanarMesh(np.array([[0., 0.], [1., 0.], [0., 1.]]), np.array([[0, 1, 2]]),
                     {"layer:metal": np.array([0])})
    result = SimulationResult(mesh, converged=True, configuration=project.to_dict(),
                              physics_hash=physics_hash(project))
    cache = tmp_path / "cached.etl"
    save_project(project, cache, result)
    receipt = {"physics_hash": physics_hash(project), "source_fingerprint": "source-v1",
               "solver_version": gallery.__version__}
    cache.with_suffix(".json").write_text(json.dumps(receipt), encoding="utf-8")
    assert gallery.cached_result(project, cache, "source-v1") is not None
    assert gallery.cached_result(project, cache, "source-v2") is None
    project.terminals[1].value *= 2
    assert gallery.cached_result(project, cache, "source-v1") is None


def test_solved_gallery_records_real_metrics_and_compact_hybrid_svg(tmp_path):
    output = tmp_path / "gallery"
    manifest = gallery.build_gallery(["E1"], output, tmp_path / "gallery.md", tmp_path / "examples",
                                     tmp_path / "cache")
    record = manifest["examples"][0]
    assert record["result"]["converged"]
    assert record["result"]["metrics"]["effective_resistance"] == pytest.approx(.084, rel=.01)
    assert record["field"]["minimum"] == pytest.approx(0., abs=1e-12)
    assert record["field"]["maximum"] == pytest.approx(.02, abs=1e-12)
    svg = output / "E1_straight.svg"
    source = svg.read_text(encoding="utf-8")
    assert "<image" in source and "Prepared geometry" in source
    assert svg.stat().st_size < 500_000
    assert "SVG previews retain vector geometry" in (tmp_path / "gallery.md").read_text(encoding="utf-8")


@pytest.mark.parametrize("name", list(EXAMPLE_CATALOG))
def test_catalogue_field_layer_and_sweeps_exist(name, tmp_path):
    from etlayout.simulation.sweep import set_parameter
    project = make_example(name, tmp_path)
    info = example_info(name)
    assert project.layer(info.layer).id == info.layer
    for sweep in project.sweeps:
        for value in sweep.values:
            set_parameter(project, sweep.parameter, value)


def test_transient_gallery_discloses_time_waveform_and_uniform_scale(tmp_path):
    output = tmp_path / "gallery"
    markdown = tmp_path / "gallery.md"
    manifest = gallery.build_gallery(["E17"], output, markdown, tmp_path / "examples", tmp_path / "cache")
    record = manifest["examples"][0]
    assert record["transient"]["enabled"]
    assert record["field"]["time_s"] == pytest.approx(2.1e-3)
    assert record["field"]["uniform"]
    assert record["field"]["display_maximum"] - record["field"]["display_minimum"] > .1
    text = markdown.read_text(encoding="utf-8")
    assert "transient thermal" in text and "plateau" in text
    assert "maximum temperature at final time" in text and "peak over all time steps" in text
    svg = (output / "E17_pulsed_thermal.svg").read_text(encoding="utf-8")
    assert "2100 µs" in svg and "Uniform:" in svg

@pytest.mark.parametrize("kind", ["extended", "introductory", "partial"])
def test_actual_desktop_capture_is_scoped_to_the_extended_bridge_guide(kind, tmp_path):
    root = _SCRIPT.parents[1]
    manifest = root / "docs/example-gallery" / ("manifest.json" if kind == "introductory" else "extended/manifest.json")
    records = json.loads(manifest.read_text(encoding="utf-8"))["examples"]
    if kind == "partial":
        records = [record for record in records if record["catalog"]["id"] == "E25"]
    markdown = tmp_path / "guide.md"
    gallery.write_markdown(records, manifest.parent, markdown, root / "examples")
    text = markdown.read_text(encoding="utf-8")
    screenshot = "extended-bridge-results.png"
    assert (screenshot in text) == (kind == "extended")
    if kind == "extended":
        linked = next(target for target in re.findall(r"\]\(([^)]+)\)", text) if screenshot in target)
        assert (markdown.parent / linked).resolve() == root / "docs/images" / screenshot
        assert "0.4.0 source GUI on native Windows at 125% Qt scale" in text
        assert "300 K backside" in text and "mesh verification is not established for this run" in text
        assert "zero blockers and one electrically connected network" in text
        assert "Do not add a direct Drain–Collector contact" in text
        assert "rather than turning warnings off" in text
