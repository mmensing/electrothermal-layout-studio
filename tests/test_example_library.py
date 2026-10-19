# SPDX-License-Identifier: MPL-2.0
"""The downloadable formats must preserve the physical masks used by examples."""
import json
from pathlib import Path

import pytest

from etlayout.example_catalog import example_info
from etlayout.examples import EXAMPLE_NAMES, generate_all, make_example
from etlayout.geometry.layout import LayoutDocument
from etlayout.geometry.prepare import prepare_geometry
from etlayout.io.project import file_hash, load_project, physics_hash


@pytest.mark.parametrize("name", EXAMPLE_NAMES)
def test_layout_formats_and_portable_project_preserve_design(name, tmp_path):
    project = make_example(name, tmp_path)
    assert project.solver.mode == example_info(name).recommended_mode
    gds = LayoutDocument(tmp_path / f"{name}.gds")
    oasis = LayoutDocument(tmp_path / f"{name}.oas")
    assert gds.top_cells == oasis.top_cells == [name]
    first, second = gds.extract(name), oasis.extract(name)
    assert set(first) == set(second)
    for mask, geometry in first.items():
        assert geometry.is_valid and not geometry.is_empty
        assert geometry.symmetric_difference(second[mask]).area == pytest.approx(0., abs=1e-24)
    reopened, result = load_project(tmp_path / f"{name}.etl")
    assert result is None
    assert physics_hash(reopened) == physics_hash(project)
    assert Path(reopened.layout.path).is_file()
    assert file_hash(reopened.layout.path) == project.layout.sha256
    if not example_info(name).family:
        prepared = prepare_geometry(reopened)
        assert not any('clipped' in item.message or 'Physical volumes' in item.message
                       for item in prepared.diagnostics)


def test_generated_gds_identity_does_not_include_wall_clock_timestamps(tmp_path):
    project = make_example("E1", tmp_path / "first")
    data = Path(project.layout.path).read_bytes()
    # GDS HEADER precedes BGNLIB, whose payload consists of two six-short dates.
    assert data[8:10] == b"\x01\x02"
    assert data[10:34] == bytes(24)
    regenerated = make_example("E1", tmp_path / "second")
    assert project.layout.sha256 == regenerated.layout.sha256


def test_via_array_has_all_twenty_translated_contacts(tmp_path):
    project = make_example("E5", tmp_path)
    geometry = prepare_geometry(project)
    via = geometry.interfaces["array"]
    assert len(via.geoms) == 20
    assert via.bounds == pytest.approx((110e-6, 30e-6, 170e-6, 176e-6))
    assert via.area == pytest.approx(20 * (6e-6)**2)


def test_library_index_links_complete_download_sets(tmp_path):
    projects = generate_all(tmp_path)
    entries = json.loads((tmp_path / "index.json").read_text(encoding="utf-8"))
    assert [entry["name"] for entry in entries] == EXAMPLE_NAMES
    for entry, project in zip(entries, projects, strict=True):
        assert entry["title"] and entry["category"] and entry["learning_goal"]
        assert entry["evidence_level"] == example_info(project.name).evidence_level
        for key in ("project", "gds", "oasis"):
            assert (tmp_path / entry[key]).is_file()
        assert entry["source_sha256"] == project.layout.sha256
