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
        file_name, _, fragment = target.partition("#")
        destination = markdown.parent / file_name
        assert destination.is_file(), target
        if fragment:
            source = destination.read_text(encoding="utf-8")
            assert f'id="{fragment}"' in source, target
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


@pytest.mark.parametrize("prefix,suffix", [
    ("Maintained introduction\n", "\n## Scenario guidance\nKeep [thermal](thermal-example-scenarios.md).\n"),
    ("", "\nKeep [device](device-engineering-examples.md) and [evidence](example-science.md).\n"),
    ("Maintained Windows introduction\r\n", "\r\nKeep mixed-newline appendices exactly.\n"),
])
def test_regeneration_preserves_contributor_guidance_and_evidence_links(tmp_path, prefix, suffix):
    root = _SCRIPT.parents[1]
    path = tmp_path / "guide.md"
    records = json.loads((root / "docs/example-gallery/manifest.json").read_text(encoding="utf-8"))["examples"][:1]
    path.write_bytes((prefix + gallery.GENERATED_BEGIN + "\nold generated content\n" + gallery.GENERATED_END + suffix).encode("utf-8"))
    gallery.write_markdown(records, root / "docs/example-gallery", path, root / "examples")
    once = path.read_bytes().decode("utf-8")
    gallery.write_markdown(records, root / "docs/example-gallery", path, root / "examples")
    assert path.read_bytes().decode("utf-8") == once
    assert once.startswith(prefix + gallery.GENERATED_BEGIN)
    assert once.endswith(gallery.GENERATED_END + suffix)
    assert "nominal-reference.md" in once and "example-science.md" in once
    assert "old generated content" not in once


@pytest.mark.parametrize("existing", ["Contributor prose", gallery.GENERATED_BEGIN + " incomplete",
                                      gallery.GENERATED_END + gallery.GENERATED_BEGIN])
def test_regeneration_refuses_unmarked_or_broken_contributor_documents(tmp_path, existing):
    path = tmp_path / "guide.md"
    path.write_text(existing, encoding="utf-8")
    with pytest.raises(ValueError, match="markers|generated-content"):
        gallery._write_managed(path, "replacement")
    assert path.read_text(encoding="utf-8") == existing


def test_markdown_only_keeps_result_manifests_and_images_byte_identical(tmp_path):
    root = _SCRIPT.parents[1]
    output = tmp_path / "assets"
    output.mkdir()
    source = root / "docs/example-gallery/manifest.json"
    record = json.loads(source.read_text(encoding="utf-8"))["examples"][0]
    manifest = output / "manifest.json"
    manifest.write_text(json.dumps({"examples": [record]}), encoding="utf-8")
    image = output / (record["name"] + ".png")
    image.write_bytes((source.parent / image.name).read_bytes())
    before = {path.name: path.read_bytes() for path in output.iterdir()}
    markdown = tmp_path / "guide.md"
    assert gallery.main(["--markdown-only", "--output", str(output), "--markdown", str(markdown)]) == 0
    assert {path.name: path.read_bytes() for path in output.iterdir()} == before
    text = markdown.read_text(encoding="utf-8")
    assert "historical result illustrations" in text and "not been recalculated" in text
    assert "source hashes" in text or "hashes" in text


@pytest.mark.parametrize("value,unit,expected", [(.002, "W", "2 mW"), (1e-6, "J", "1 µJ"),
                                               (20e-6, "s", "20 µs"), (-.1, "A", "-100 mA"),
                                               (300., "K", "300 K"), (0., "A", "0 A")])
def test_scalar_readings_use_engineering_units_without_changing_values(value, unit, expected):
    assert gallery._number(value, unit) == expected


def test_high_aspect_ratio_support_retains_equal_physical_scale(tmp_path):
    from matplotlib import pyplot as plt

    mesh = PlanarMesh(np.array([[0., 0.], [1e-6, 0.], [0., 100e-6], [1e-6, 100e-6]]),
                     np.array([[0, 1, 2], [1, 2, 3]]), {"layer:metal": np.array([0, 1])})
    result = SimulationResult(mesh, fields={"metal": {"potential": np.array([0., 0., .02, .02])}})
    gallery._style()
    figure, axis = plt.subplots()
    _, details = gallery.draw_field(axis, result, example_info("E1"))
    figure.canvas.draw()
    assert axis.get_aspect() == 1.
    assert details["support_aspect_ratio"] == pytest.approx(100.)
    assert details["support_bounds_m"] == pytest.approx([0., 0., 1e-6, 100e-6])
    assert details["display_unit"] == "mV" and details["display_maximum"] == pytest.approx(20.)
    assert details["minimum"] == 0. and details["maximum"] == .02
    plt.close(figure)


@pytest.mark.parametrize("name,link", [("examples.md", "thermal-example-scenarios.md"),
                                       ("extended-examples.md", "device-engineering-examples.md")])
def test_maintained_scenario_guidance_remains_outside_generated_regions(name, link):
    document = (_SCRIPT.parents[1] / "docs" / name).read_text(encoding="utf-8")
    assert document.count(gallery.GENERATED_BEGIN) == document.count(gallery.GENERATED_END) == 1
    maintained = document.split(gallery.GENERATED_END, 1)[1]
    assert link in maintained


def test_partial_manifest_cannot_replace_the_canonical_chooser(tmp_path, monkeypatch):
    root = _SCRIPT.parents[1]
    records = json.loads((root / "docs/example-gallery/manifest.json").read_text(encoding="utf-8"))["examples"][:1]
    monkeypatch.setattr(gallery, "ROOT", tmp_path)
    path = tmp_path / "docs/examples.md"
    with pytest.raises(ValueError, match="complete foundational manifest"):
        gallery.write_markdown(records, root / "docs/example-gallery", path, root / "examples")
    assert not path.exists()


def test_recommended_device_exercises_distinguish_original_current_and_voltage_control(tmp_path):
    root = _SCRIPT.parents[1]
    records = json.loads((root / "docs/example-gallery/extended/manifest.json").read_text(encoding="utf-8"))["examples"]
    path = tmp_path / "device-guide.md"
    gallery.write_markdown([row for row in records if row["catalog"]["id"] in {"E37", "E38", "E39"}],
                           root / "docs/example-gallery/extended", path, root / "examples")
    text = path.read_text(encoding="utf-8")
    e37 = text.split('## E37 ·', 1)[1].split('## E38 ·', 1)[0]
    assert "Voltage-controlled GaN coupon" in e37
    assert "drain voltage stays fixed and current emerges" in e37
    assert "Original reference instead holds delivered current" in e37
    e38 = text.split('## E38 ·', 1)[1].split('## E39 ·', 1)[0]
    assert "Independent drain drives" in e38 and "both requested currents" in e38
    e39 = text.split('## E39 ·', 1)[1]
    assert "Lower-current SiC reference (2 A)" in e39
    assert "original stress case is the one shown in the historical preview" in e39
    assert "calculate the lower-current scenario separately" in e39


def test_partial_generation_cannot_overwrite_canonical_evidence_before_document_rejection(tmp_path, monkeypatch):
    monkeypatch.setattr(gallery, "ROOT", tmp_path)
    output = tmp_path / "docs/example-gallery"
    output.mkdir(parents=True)
    evidence = output / "manifest.json"
    evidence.write_bytes(b'original numerical evidence')
    path = tmp_path / "docs/examples.md"
    with pytest.raises(ValueError, match="separate --output and --markdown"):
        gallery.build_gallery(["E1"], output, path, tmp_path / "examples", tmp_path / "cache", no_solve=True)
    assert evidence.read_bytes() == b'original numerical evidence'
    assert not (tmp_path / "examples").exists()
    assert not (tmp_path / "cache").exists()
