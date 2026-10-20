# SPDX-License-Identifier: MPL-2.0
"""Discovery metadata and editable copies preserve the historical references."""
from __future__ import annotations

import json
import runpy
from dataclasses import asdict
from pathlib import Path

import pytest

from etlayout.example_catalog import EXAMPLE_CATALOG, LEARNING_PATHS, START_HERE, SUBJECTS, example_info
from etlayout.example_library import (
    COMPANION_CATALOG,
    create_library_example,
    example_card,
    example_choices,
    library_info_for_project,
    load_companion,
    project_summary,
    reference_project,
)
from etlayout.examples import EXTENDED_EXAMPLES, ROUTINE_EXAMPLES
from etlayout.io.project import file_hash, load_project, physics_hash

ROOT = Path(__file__).resolve().parents[1]
pytestmark = pytest.mark.fast


def test_subjects_and_learning_paths_cross_historical_batches_without_changing_ids_or_test_tiers():
    assert [info.id for info in EXAMPLE_CATALOG.values()] == [f"E{number}" for number in range(1, 41)]
    assert len(SUBJECTS) == len(set(SUBJECTS)) == 6
    assert set(SUBJECTS) == {info.category for info in EXAMPLE_CATALOG.values()}
    assert len(ROUTINE_EXAMPLES) == 19 and len(EXTENDED_EXAMPLES) == 16
    assert sum(bool(info.family) for info in EXAMPLE_CATALOG.values()) == 5
    assert {identifier for path in LEARNING_PATHS for identifier in path.examples} == {info.id for info in EXAMPLE_CATALOG.values()}
    assert len(START_HERE) == len(set(START_HERE)) == 6
    assert all(example_info(identifier).level != "Engineering" for identifier in START_HERE)
    assert example_info("E1").category == example_info("E25").category
    assert example_info("E17").category == example_info("E36").category
    assert example_info("E39").reference_status == "stress"
    assert example_info("E40").reference_status == "reference"
    assert example_info("E39").applicability == example_info("E40").applicability
    assert all(info.workload and info.capabilities and info.role for info in EXAMPLE_CATALOG.values())


@pytest.mark.parametrize("identifier", tuple(EXAMPLE_CATALOG) + tuple(COMPANION_CATALOG))
def test_packaged_inputs_are_exact_canonical_archives_with_independent_project_summaries(identifier):
    if identifier in COMPANION_CATALOG:
        relative = Path("power_routing") / COMPANION_CATALOG[identifier].filename
    else:
        relative = Path(example_info(identifier).name + ".etl")
    assert (ROOT / "examples" / relative).read_bytes() == (ROOT / "src/etlayout/data/examples" / relative).read_bytes()
    project = reference_project(identifier)
    original, _ = load_project(ROOT / "examples" / relative)
    assert physics_hash(project) == physics_hash(original)
    card = example_card(identifier)
    summary = card["project_summary"]
    assert card["availability_note"] == "" and summary["dimensions_um"]
    assert summary["budgets"]["max_elements"] == project.mesh.max_elements
    assert summary["budgets"]["max_dofs"] == project.mesh.max_dofs
    assert summary["features"]["layers"] == len(project.layers)
    assert ("transient" in summary["operation"]) == project.transient.enabled
    assert "Configured inputs; not a simulated result" in summary["summary"]
    assert sum(item["value"] for item in summary["heat_sources"]) == sum(item.value for item in project.heat_sources)
    assert all(item["source"] != "terminal" or item["id"] not in {
        landing.terminal_id for port in project.power.ports for landing in port.landings
    } for item in summary["service"])


def test_shipped_index_uses_the_same_navigation_records_as_the_library():
    rows = json.loads((ROOT / "examples/index.json").read_text(encoding="utf-8"))
    assert [row["name"] for row in rows] == list(EXAMPLE_CATALOG)
    for row in rows:
        metadata = json.loads(json.dumps(example_info(row["name"]).to_dict()))
        assert {key: row[key] for key in metadata} == metadata
        assert row["source_sha256"] == reference_project(row["name"]).layout.sha256
        for key in ("project", "gds", "oasis"):
            assert (ROOT / "examples" / row[key]).is_file()


def test_all_packaged_reference_physics_matches_recorded_nominal_identity():
    baseline = json.loads((ROOT / "docs/example-science/nominal-reference.json").read_text(encoding="utf-8"))
    recorded = {row["name"]: row["physics_hash"] for row in baseline["examples"]}
    assert set(recorded) == set(EXAMPLE_CATALOG)
    assert {name: physics_hash(reference_project(name)) for name in EXAMPLE_CATALOG} == recorded


@pytest.mark.parametrize("identifier", ["E20", "E21", "E22", "E23", "E24"])
def test_family_choices_are_the_existing_builders_choices_with_valid_defaults(identifier):
    from etlayout.power_examples import family_choices, settings

    name = example_info(identifier).name
    choices = example_choices(identifier)
    assert {"presets": choices.presets, "variants": choices.variants, "thermal": choices.thermal} == family_choices(name)
    defaults = settings(name)
    assert (choices.default_preset, choices.default_variant, choices.default_thermal) == (
        defaults.preset, defaults.variant, defaults.thermal)
    for preset in choices.presets:
        for variant in choices.variants:
            for thermal in choices.thermal:
                config = settings(name, preset=preset, variant=variant, thermal=thermal)
                assert (config.preset, config.variant, config.thermal) == (preset, variant, thermal)
                assert config.current > 0 and config.max_elements > 0 and config.max_dofs > 0
    assert choices.scenarios[0].id == "reference"


@pytest.mark.parametrize("identifier,scenario", [("E37", "voltage_controlled"), ("E39", "continuous_reference")])
def test_recommended_scenario_inputs_are_explicit_and_do_not_mutate_original(identifier, scenario):
    original = reference_project(identifier)
    before = original.to_dict()
    choices = example_choices(identifier)
    assert choices.recommended_scenario == scenario
    assert scenario in {row.id for row in choices.scenarios}
    card = example_card(identifier, original, scenario=scenario)
    summary = card["project_summary"]
    assert original.to_dict() == before
    if identifier == "E37":
        assert any(row["id"] == "drain_a" and row["kind"] == "voltage" and row["value"] == .2 for row in summary["service"])
    else:
        assert "stress" in card["status_note"]
        assert any(row["id"] == "drain" and row["value"] == 2. for row in summary["service"])
        assert next(port for port in original.power.ports if port.id == "drain").value == 10.


@pytest.mark.parametrize("identifier,options,diagnostic", [
    ("E37", {"scenario": "voltage_controlled", "current": .1}, "Named operating scenarios"),
    ("E39", {"scenario": "continuous_reference", "thermal": "attached"}, "Named operating scenarios"),
    ("E27", {"scenario": "reference", "current": .1}, "power-device family"),
    ("E1", {"tlm_spacing_um": 10.}, "E27 TLM"),
    ("E21", {"variant": "not_a_variant"}, "variants"),
    ("E21", {"preset": "not_a_preset"}, "presets"),
    ("E1", {"scenario": "not_a_scenario"}, "scenarios"),
    ("routing_four_cell_feed", {"preset": "large"}, "fixed original inputs"),
])
def test_incompatible_choices_are_rejected_before_creating_files(identifier, options, diagnostic, tmp_path):
    destination = tmp_path / "must_not_be_created"
    with pytest.raises(ValueError, match=diagnostic):
        create_library_example(identifier, destination, **options)
    assert not destination.exists()


def test_selected_family_options_survive_project_generation(tmp_path):
    from etlayout.power_examples import metadata

    project = create_library_example("E21", tmp_path, preset="compact", variant="dual", thermal="resistive_attach")
    config = metadata(project)
    assert (config["preset"], config["variant"], config["thermal"]) == ("compact", "dual", "resistive_attach")
    assert config["fingers"] == 4 and config["gate_width_um"] == 60.
    assert project_summary(project)["budgets"]["memory_budget_bytes"] == 1024**3


@pytest.mark.parametrize("identifier", tuple(COMPANION_CATALOG))
def test_companion_editable_copy_keeps_layout_hash_recipes_and_embedded_reopening(identifier, tmp_path):
    original = reference_project(identifier)
    before = original.to_dict()
    project = load_companion(identifier, tmp_path / "editable")
    assert library_info_for_project(original) is COMPANION_CATALOG[identifier]
    assert library_info_for_project(project) is COMPANION_CATALOG[identifier]
    output = tmp_path / "editable" / COMPANION_CATALOG[identifier].filename
    assert Path(project.layout.path).is_relative_to(tmp_path / "editable")
    assert file_hash(project.layout.path) == original.layout.sha256
    assert physics_hash(project) == physics_hash(original)
    assert project.power.recipes == original.power.recipes
    assert all(recipe.source_hash == project.layout.sha256 for recipe in project.power.recipes)
    Path(project.layout.path).unlink()  # Embedded source, rather than this local copy, is sufficient.
    reopened, result = load_project(output)
    assert result is None and Path(reopened.layout.path).is_file()
    assert library_info_for_project(reopened) is COMPANION_CATALOG[identifier]
    assert physics_hash(reopened) == physics_hash(original)
    assert original.to_dict() == before


def test_missing_packaged_reference_is_visible_without_fabricated_input_summary(monkeypatch, tmp_path):
    monkeypatch.setattr("etlayout.example_library._reference_resource", lambda _: tmp_path / "absent.etl")
    card = example_card("E1")
    assert card["project_summary"] is None
    assert "Packaged reference is unavailable" in card["availability_note"]
    with pytest.raises(FileNotFoundError, match="unavailable"):
        reference_project("E1")


def test_card_choices_are_plain_serializable_records_and_reject_unknown_scenarios():
    card = example_card("E39", scenario="continuous_reference")
    encoded = json.loads(json.dumps(card))
    assert any(row["id"] == "continuous_reference" for row in encoded["choices"]["scenarios"])
    assert card["choices"] == asdict(example_choices("E39"))
    with pytest.raises(ValueError, match="Unknown operating scenario"):
        example_card("E1", scenario="not_a_scenario")

def _runtime_sync_fixture(tmp_path):
    synchronize = runpy.run_path(str(ROOT / "scripts/generate_examples.py"))["synchronize_runtime_resources"]
    canonical = tmp_path / "examples"
    canonical.mkdir()
    (canonical / "index.json").write_bytes((ROOT / "examples/index.json").read_bytes())
    for path in (ROOT / "src/etlayout/data/examples").rglob("*.etl"):
        relative = path.relative_to(ROOT / "src/etlayout/data/examples")
        target = canonical / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes((ROOT / "examples" / relative).read_bytes())
    return synchronize


def test_maintainer_sync_checks_and_repairs_metadata_and_exact_runtime_copies_without_generation(tmp_path):
    synchronize = _runtime_sync_fixture(tmp_path)
    first = synchronize(tmp_path)
    assert first["reference_projects"] == 40 and first["companion_projects"] == 2
    assert first["changed_archives"] == 42 and first["calculations_run"] == 0
    runtime = tmp_path / "src/etlayout/data/examples/E1_straight.etl"
    original = (tmp_path / "examples/E1_straight.etl").read_bytes()
    runtime.write_bytes(b"stale runtime copy")
    index = tmp_path / "examples/index.json"
    rows = json.loads(index.read_text(encoding="utf-8"))
    rows[0]["category"] = "old chronological category"
    index.write_text(json.dumps(rows), encoding="utf-8")
    with pytest.raises(ValueError, match="Stale example resources") as error:
        synchronize(tmp_path, check=True)
    assert "examples/index.json metadata" in str(error.value) and "E1_straight.etl" in str(error.value)
    assert runtime.read_bytes() == b"stale runtime copy"  # Check mode writes nothing.
    changed = synchronize(tmp_path)
    assert changed["index_updated"] and changed["changed_archives"] == 1
    assert runtime.read_bytes() == original == (tmp_path / "examples/E1_straight.etl").read_bytes()
    checked = synchronize(tmp_path, check=True)
    assert checked["changed_archives"] == 0 and not checked["index_updated"]


def test_runtime_sync_keeps_unregistered_archives_for_explicit_maintainer_review(tmp_path):
    synchronize = _runtime_sync_fixture(tmp_path)
    synchronize(tmp_path)
    extra = tmp_path / "src/etlayout/data/examples/unregistered.etl"
    extra.write_bytes(b"must not be silently removed")
    for check in (False, True):
        with pytest.raises(ValueError, match="Unexpected runtime archives require maintainer review"):
            synchronize(tmp_path, check=check)
        assert extra.read_bytes() == b"must not be silently removed"


def test_library_project_recognition_does_not_keep_mutable_projects_or_match_unrelated_names():
    from etlayout.models import Project

    assert library_info_for_project(Project(name="E1_straight")) is example_info("E1")
    project = reference_project("routing_four_cell_feed")
    original_name = project.name
    assert library_info_for_project(project) is COMPANION_CATALOG["routing_four_cell_feed"]
    project.name = "Unrelated edited project"
    assert library_info_for_project(project) is None
    assert library_info_for_project(Project(name=original_name)) is COMPANION_CATALOG["routing_four_cell_feed"]


@pytest.mark.parametrize("identifier,scenario,expected", [
    ("E1", "reference", "DC electrical"),
    ("E10", "reference", "Coupled DC electrothermal"),
    ("E17", "reference", "Thermal transient"),
    ("E18", "reference", "Electrothermal transient"),
    ("E37", "voltage_controlled", "Coupled DC electrothermal"),
])
def test_input_cards_use_the_exact_shared_gui_analysis_label(identifier, scenario, expected):
    summary = example_card(identifier, scenario=scenario)["project_summary"]
    assert summary["analysis_label"] == expected
    assert expected in summary["summary"]
    assert summary["operation"]  # Compatibility for recorded presentation consumers.
