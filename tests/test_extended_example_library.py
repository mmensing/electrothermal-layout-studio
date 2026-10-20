# SPDX-License-Identifier: MPL-2.0
"""Exact nominal service and complete portable results, with shared solved fixtures."""
from __future__ import annotations

import importlib.util
import json
import re
from pathlib import Path

import pytest

from etlayout.example_catalog import example_info
from etlayout.examples import EXAMPLE_NAMES, EXTENDED_EXAMPLES, ROUTINE_EXAMPLES, generate_all, make_example

ROOT = Path(__file__).resolve().parents[1]


def load_script(name):
    spec = importlib.util.spec_from_file_location(name, ROOT / "scripts" / (name + ".py"))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def verifier(monkeypatch):
    monkeypatch.syspath_prepend(str(ROOT / "scripts"))
    return load_script("verify_extended_examples")


@pytest.mark.numerical
@pytest.mark.parametrize("name", EXTENDED_EXAMPLES)
def test_exact_extended_nominal_service_and_full_result_portability(name, expanded_nominal_factory, verifier, tmp_path):
    project, result = expanded_nominal_factory(name)
    before = project.to_dict()
    science = verifier.validate_nominal(project, result)
    assert science["converged"] and not science["mesh_verified"]
    assert science["service_checks"] and science["conservation_checks"]
    assert science["elements"] <= project.mesh.max_elements
    assert science["dofs"] <= project.mesh.max_dofs
    portable = verifier.verify_portability(project, result, tmp_path)
    assert portable["exported_reimport_equal"] and portable["power_identity_equal"]
    assert [row["schema"] for row in portable["complete_typed_round_trips"]] == [2, 3]
    assert all(row["embedded_reopen"] for row in portable["complete_typed_round_trips"])
    assert project.to_dict() == before


@pytest.mark.fast
def test_gallery_reports_signed_ports_without_owned_zero_drive_placeholders(tmp_path):
    gallery = load_script("build_example_gallery")
    project = make_example("E28", tmp_path)
    summary, terminals, ports = gallery.operating_point(project)
    assert "branch -100 mA (current port" in summary
    assert "drain 300 mA (current port" in summary
    assert "branch_landing 0" not in summary and "drain_landing 0" not in summary
    assert next(item for item in ports if item["id"] == "branch")["value"] == -.1
    # Zero-valued owned placeholders remain available as faithful provenance;
    # only the applied operating-point summary excludes their misleading drive.
    assert next(item for item in terminals if item["id"] == "branch_landing")["value"] == 0.


@pytest.mark.fast
def test_partial_gallery_discloses_one_of_the_forty_examples(tmp_path):
    gallery = load_script("build_example_gallery")
    document = tmp_path / "partial.md"
    manifest = gallery.build_gallery(["E28"], tmp_path / "assets", document, tmp_path / "examples",
                                     tmp_path / "cache", no_solve=True, overview=False)
    assert len(EXAMPLE_NAMES) == 40
    assert len(manifest["examples"]) == 1
    assert "covers 1 of the library's 40 runnable reference examples" in document.read_text(encoding="utf-8")
    assert "The library contains 40 runnable reference examples" not in document.read_text(encoding="utf-8")
    assert "result" not in manifest["examples"][0]


@pytest.mark.fast
def test_full_foundation_gallery_links_the_complete_forty_project_catalog(tmp_path):
    gallery = load_script("build_example_gallery")
    records = json.loads((ROOT / "docs/example-gallery/manifest.json").read_text(encoding="utf-8"))["examples"]
    assert {record["name"] for record in records} == set(ROUTINE_EXAMPLES)
    assert len(records) == 19
    examples = tmp_path / "examples"
    generate_all(examples)
    markdown = tmp_path / "all.md"
    gallery.write_example_index(markdown, examples)
    text = markdown.read_text(encoding="utf-8")
    assert "The library contains 40 runnable reference examples" in text
    links = re.findall(r"\| <a id=\"e\d+\"></a>\[(E\d+) · [^\]]+\]\(([^)]+)\)", text)
    assert {identifier for identifier, _target in links} == {example_info(name).id for name in EXAMPLE_NAMES}
    index = json.loads((examples / "index.json").read_text(encoding="utf-8"))
    assert [row["name"] for row in index] == EXAMPLE_NAMES
    for row in index:
        for field in ("project", "gds", "oasis"):
            assert (examples / row[field]).is_file()
    for identifier, target in links:
        if int(identifier[1:]) <= 19:
            assert target.endswith("foundation-examples.md#" + identifier.lower())
        else:
            assert target.endswith("#" + identifier.lower())
            assert "extended-examples.md" in target if int(identifier[1:]) >= 25 else "power-examples.md" in target


@pytest.mark.fast
def test_complete_chooser_matches_shared_topics_modes_scenarios_and_workload(tmp_path):
    from etlayout.example_catalog import EXAMPLE_CATALOG, SUBJECTS
    from etlayout.example_library import example_card

    gallery = load_script("build_example_gallery")
    path = tmp_path / "chooser.md"
    gallery.write_example_index(path, ROOT / "examples")
    text = path.read_text(encoding="utf-8")
    assert "See configured walkthrough" not in text
    for subject in SUBJECTS:
        assert text.count("## " + subject + "\n") == 1
    for info in EXAMPLE_CATALOG.values():
        row = next(line for line in text.splitlines() if f'<a id="{info.id.lower()}"></a>' in line)
        card = example_card(info.id, scenario=info.recommended_scenario)
        assert card["project_summary"]["analysis_label"] in row
        assert info.workload in row
        scenario = next(item for item in card["choices"]["scenarios"] if item["id"] == info.recommended_scenario)
        assert scenario["title"] in row
        assert info.learning_goal in row
    assert "original is stress" in next(line for line in text.splitlines() if '<a id="e39">' in line)
    assert "Older diagnostics" in text or "older diagnostics" in text
