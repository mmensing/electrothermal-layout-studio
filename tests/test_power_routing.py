# SPDX-License-Identifier: MPL-2.0
"""Actual KLayout files, integer rules, hierarchy edits and exported FEM solves."""
import copy
import hashlib

import klayout.db as kdb
import pytest
from shapely.geometry import Polygon, box
from shapely.ops import unary_union

from etlayout.geometry.layout import LayoutDocument
from etlayout.geometry.prepare import prepare_geometry
from etlayout.materials.database import reference_materials
from etlayout.meshing.generate import generate_mesh
from etlayout.models import (
    Interface,
    LayoutSource,
    PhysicalLayer,
    Project,
    RegionSpec,
    RoutingRecipe,
    RoutingRules,
    SimulationError,
    Terminal,
)
from etlayout.routing import export_candidate, generate_candidate, validate_rules
from etlayout.routing.geometry import canonical_geometry_id
from etlayout.routing.recipes import recipe_geometry
from etlayout.solvers import solve_electrical


def rect(bounds):
    return RegionSpec(polygons=[dict(bounds=bounds)])


def source_project(tmp_path, *, hierarchy=False):
    layout = kdb.Layout()
    layout.dbu = .001
    top = layout.create_cell("TOP")
    leaf = layout.create_cell("LEAF") if hierarchy else top
    leaf.shapes(layout.layer(1, 0)).insert(kdb.Box(0, 0, 20000, 4000))
    hole = kdb.Polygon(kdb.Box(0, 10000, 10000, 20000))
    hole.insert_hole(kdb.Box(2000, 12000, 5000, 15000))
    leaf.shapes(layout.layer(9, 7)).insert(hole)
    leaf.shapes(layout.layer(9, 7)).insert(kdb.Text("KEEP LABEL", kdb.Trans(123, 456)))
    if hierarchy:
        top.insert(kdb.CellInstArray(leaf.cell_index(), kdb.Trans(0, True, 0, 4000),
                                    kdb.Vector(30000, 0), kdb.Vector(0, 30000), 2, 1))
    path = tmp_path / "original.gds"
    layout.write(str(path))
    project = Project(layout=LayoutSource(str(path), "TOP"), materials=reference_materials(),
                      layers=[PhysicalLayer("metal", "Metal", "copper", thickness=1e-6,
                                            geometry=RegionSpec(sources=["1/0"]))])
    project.mesh.max_size = 1e-6
    project.mesh.min_size = .1e-6
    project.mesh.contact_size = .5e-6
    project.power.rules = RoutingRules(minimum_width={"metal": .5e-6}, protected_masks=["9/7"])
    project.power.recipes = [RoutingRecipe("bus", layer_id="metal", mask="1/0", edit_region=rect([0, 0, 20e-6, 4e-6]),
                                          parameters={"width": 2e-6, "anchors": [[0, 2e-6], [20e-6, 2e-6]]},
                                          instance_path=[0] if hierarchy else [])]
    return project


@pytest.mark.parametrize("suffix", [".gds", ".oas"])
def test_export_replaces_source_and_solves_actual_roundtrip(tmp_path, suffix):
    project = source_project(tmp_path)
    original_hash = hashlib.sha256(open(project.layout.path, "rb").read()).hexdigest()
    project.terminals = [Terminal("left", "metal", rect([0, 1e-6, .5e-6, 3e-6]), "ground"),
                         Terminal("right", "metal", rect([19.5e-6, 1e-6, 20e-6, 3e-6]), "voltage", 1.)]
    generated = generate_candidate(project, {}, tmp_path / ("candidate" + suffix))
    assert generated.valid, generated.violations
    assert generated.provenance["export_reimport_verified"]
    assert hashlib.sha256(open(project.layout.path, "rb").read()).hexdigest() == original_hash
    geometry = prepare_geometry(generated.project)
    assert geometry.layers["metal"].area == pytest.approx(40e-12)
    result = solve_electrical(generated.project, generate_mesh(generated.project, geometry))
    exact = project.materials["copper"].resistivity.value * 19e-6 / (2e-6 * 1e-6)
    assert result.converged
    assert result.metrics["effective_resistance"] == pytest.approx(exact, rel=2e-8)


@pytest.mark.parametrize("suffix", [".gds", ".oas"])
def test_selected_mirrored_array_clones_without_changing_other_instance_labels_holes(tmp_path, suffix):
    project = source_project(tmp_path, hierarchy=True)
    before = LayoutDocument(project.layout.path).extract("TOP")
    generated = generate_candidate(project, {}, tmp_path / ("hierarchy" + suffix))
    assert generated.valid, generated.violations
    document = LayoutDocument(generated.project.layout.path)
    after = document.extract("TOP")
    assert canonical_geometry_id(before["9/7"]) == canonical_geometry_id(after["9/7"])
    assert after["1/0"].intersection(box(30e-6, 0, 50e-6, 4e-6)).area == pytest.approx(80e-12)
    assert after["1/0"].intersection(box(0, 0, 20e-6, 4e-6)).area == pytest.approx(40e-12)
    assert len(document.cells) >= 3
    texts = [shape.text.string for cell in document._layout.each_cell() for layer in document._layout.layer_indexes()
             for shape in cell.shapes(layer).each() if shape.is_text()]
    assert texts.count("KEEP LABEL") >= 2


def test_roi_crossing_route_keeps_external_source_geometry(tmp_path):
    project = source_project(tmp_path)
    project.roi = rect([5e-6, 0, 15e-6, 4e-6])
    project.acknowledge_clipped_boundaries = True
    recipe = project.power.recipes[0]
    recipe.edit_region = copy.deepcopy(project.roi)
    recipe.parameters.pop("anchors")
    generated = generate_candidate(project, {}, tmp_path / "roi.gds")
    assert generated.valid, generated.violations
    masks = LayoutDocument(generated.project.layout.path).extract("TOP")
    assert masks["1/0"].area == pytest.approx(60e-12)
    assert masks["1/0"].intersection(box(0, 0, 5e-6, 4e-6)).area == pytest.approx(20e-12)


def test_protected_keepout_width_connectivity_and_overwrite_fail_explicitly(tmp_path):
    project = source_project(tmp_path)
    with pytest.raises(SimulationError, match="original"):
        generate_candidate(project, {}, project.layout.path)
    project.power.rules.fixed_regions = [rect([0, 0, 2e-6, 4e-6])]
    assert any(v["rule_id"] == "fixed_region" for v in generate_candidate(project, {}, tmp_path / "fixed.gds").violations)
    project.power.rules.fixed_regions = []
    project.power.recipes[0].parameters["width"] = .2e-6
    assert any(v["rule_id"] == "minimum_width" for v in generate_candidate(project, {}, tmp_path / "narrow.gds").violations)
    project.power.recipes[0].parameters["anchors"] = [[0, 0], [20e-6, 0]]
    assert any(v["rule_id"] == "connectivity" for v in generate_candidate(project, {}, tmp_path / "disconnected.gds").violations)
    project.power.recipes[0].parameters["width"] = 5e-6
    assert not generate_candidate(project, {}, tmp_path / "outside.gds").valid


def test_canonical_identity_subdivision_ring_order_and_determinism(tmp_path):
    a = box(0, 0, 4e-6, 2e-6)
    b = unary_union([box(0, 0, 2e-6, 2e-6), box(2e-6, 0, 4e-6, 2e-6)])
    c = Polygon(list(a.exterior.coords)[::-1])
    assert canonical_geometry_id(a) == canonical_geometry_id(b) == canonical_geometry_id(c)
    project = source_project(tmp_path)
    first = generate_candidate(project, {"bus.width": 2.0014e-6}, tmp_path / "one.gds")
    second = generate_candidate(project, {"bus.width": 2.0014e-6}, tmp_path / "two.oas")
    assert first.valid and second.valid
    assert first.provenance["canonical_masks"] == second.provenance["canonical_masks"]


@pytest.mark.parametrize("pattern", ["uniform", "graded", "staggered"])
def test_via_recipe_real_arrays(pattern):
    rules = RoutingRules(via_size=1e-6, via_pitch=2e-6)
    recipe = RoutingRecipe("vias", "via_array", parameters={"columns": 3, "rows": 2, "pattern": pattern, "grade": .5})
    shape = recipe_geometry(recipe, box(0, 0, 10e-6, 6e-6), rules)
    assert len(shape.geoms) == 6
    assert shape.area == pytest.approx(6e-12)
    assert canonical_geometry_id(shape) == canonical_geometry_id(recipe_geometry(recipe, box(0, 0, 10e-6, 6e-6), rules))


def test_finger_dual_feed_and_tapered_bus_have_expected_geometry():
    edit = box(0, 0, 20e-6, 20e-6)
    rules = RoutingRules()
    single = recipe_geometry(RoutingRecipe("f", "finger_feed", parameters={"end": "single"}), edit, rules)
    dual = recipe_geometry(RoutingRecipe("f", "finger_feed", parameters={"end": "dual"}), edit, rules)
    assert dual.area > single.area
    taper = recipe_geometry(RoutingRecipe("b", "bus", parameters={"start_width": 2e-6, "end_width": 4e-6}), edit, rules)
    assert taper.area == pytest.approx(60e-12)


def test_rule_notch_spacing_and_unsupported_radius():
    geometry = unary_union([box(0, 0, 2e-6, 4e-6), box(2.2e-6, 0, 4e-6, 4e-6)])
    project = Project(layers=[PhysicalLayer("m", "Metal", "copper", geometry=RegionSpec(polygons=[
        dict(exterior=list(p.exterior.coords)) for p in geometry.geoms]))])
    project.power.rules.minimum_spacing = {"m": .5e-6}
    assert any(v["rule_id"] == "minimum_spacing" for v in validate_rules(project))
    project.power.rules.minimum_bend_radius = 1e-6
    assert any(v["rule_id"] == "unsupported_bend_radius" for v in validate_rules(project))


def test_via_export_interface_mapping_and_rules(tmp_path):
    project = source_project(tmp_path)
    project.layers.append(PhysicalLayer("upper", "Upper", "copper", z=3e-6, geometry=RegionSpec(sources=["1/0"])))
    project.interfaces = [Interface("v", "metal", "upper", RegionSpec(sources=["3/0"]), electrical_kind="via", material_id="copper",
                                    model_kind="physical_via", height_mode="stack", enclosure=.2e-6)]
    layout = kdb.Layout()
    layout.read(project.layout.path)
    layout.cell("TOP").shapes(layout.layer(3, 0)).insert(kdb.Box(9000, 1500, 10000, 2500))
    layout.write(project.layout.path)
    project.power.recipes = [RoutingRecipe("vias", "via_array", layer_id="upper", mask="3/0", interface_id="v",
                                           edit_region=rect([0, 0, 20e-6, 4e-6]), parameters={"columns": 4, "rows": 1})]
    result = generate_candidate(project, {}, tmp_path / "vias.oas")
    assert result.valid, result.violations
    prepared = prepare_geometry(result.project)
    assert len(prepared.interfaces["v"].geoms) == 4
    assert result.project.interfaces[0].height == pytest.approx(2e-6)


def test_intentional_all_instance_edit_changes_both_and_finalist_copy_preserves_it(tmp_path):
    project = source_project(tmp_path, hierarchy=True)
    project.power.recipes[0].all_instances = True
    generated = generate_candidate(project, {}, tmp_path / "all.gds")
    assert generated.valid, generated.violations
    masks = LayoutDocument(generated.project.layout.path).extract("TOP")
    assert masks["1/0"].area == pytest.approx(80e-12)
    exported = export_candidate(generated.project, tmp_path / "finalist.oas")
    assert exported.valid, exported.violations
    assert exported.provenance["canonical_masks"] == generated.provenance["canonical_masks"]
    assert exported.provenance["operation"] == "copy_finalist"


def test_nested_path_clone_keeps_unselected_parent_and_real_transforms(tmp_path):
    project = source_project(tmp_path)
    layout = kdb.Layout()
    layout.read(project.layout.path)
    original = layout.cell("TOP")
    middle = layout.create_cell("MIDDLE")
    middle.insert(kdb.CellInstArray(original.cell_index(), kdb.Trans(0, True, 0, 4000)))
    root = layout.create_cell("ROOT")
    root.insert(kdb.CellInstArray(middle.cell_index(), kdb.Trans(0, 0)))
    root.insert(kdb.CellInstArray(middle.cell_index(), kdb.Trans(30000, 0)))
    layout.write(project.layout.path)
    project.layout.cell = "ROOT"
    project.power.recipes[0].instance_path = [0, 0]
    generated = generate_candidate(project, {}, tmp_path / "nested.oas")
    assert generated.valid, generated.violations
    result = LayoutDocument(generated.project.layout.path).extract("ROOT")["1/0"]
    assert result.area == pytest.approx(120e-12)


def test_generated_routing_rejects_clipped_terminal_and_preserves_source_identity(tmp_path):
    project = source_project(tmp_path)
    project.terminals = [Terminal("landing", "metal", rect([0, 0, 2e-6, 4e-6]), "ground")]
    result = generate_candidate(project, {}, tmp_path / "landing.gds")
    assert not result.valid
    assert any(item["rule_id"] == "terminal_support" for item in result.violations)
    project.power.recipes[0].source_hash = "stale"
    result = generate_candidate(project, {}, tmp_path / "stale.gds")
    assert any(item["rule_id"] == "source_identity" for item in result.violations)


def test_explicit_project_materializes_real_source_and_dbu_safety(tmp_path):
    project = Project(layers=[PhysicalLayer("m", "M", "copper", geometry=rect([0, 0, 10e-6, 4e-6]))])
    project.power.recipes = [RoutingRecipe("r", layer_id="m", mask="1/0", edit_region=rect([0, 0, 10e-6, 4e-6]), parameters={"width": 2e-6})]
    result = generate_candidate(project, {}, tmp_path / "explicit.oas")
    assert result.valid, result.violations
    assert LayoutDocument(result.project.layout.path).extract("ETLAYOUT")["1/0"].area == pytest.approx(20e-12)
    assert not (tmp_path / "explicit.explicit-source.gds").exists()
    project = source_project(tmp_path)
    project.power.rules.dbu = 2e-9
    assert generate_candidate(project, {}, tmp_path / "dbu.gds").violations[0]["rule_id"] == "dbu_mismatch"


def test_net_spacing_keepout_and_invalid_recipe_parameters(tmp_path):
    project = source_project(tmp_path)
    project.power.rules.keepouts = [rect([0, 0, 20e-6, 1e-6])]
    # Existing metal in keepout is preserved; only newly added metal is illegal.
    assert generate_candidate(project, {}, tmp_path / "retained.gds").valid
    project.power.recipes[0].parameters["mystery"] = 1
    assert not generate_candidate(project, {}, tmp_path / "unknown.gds").valid
    project = Project(layers=[PhysicalLayer("a", "A", "copper", geometry=rect([0, 0, 2e-6, 4e-6]), net_id="n1"),
                              PhysicalLayer("b", "B", "copper", geometry=rect([2.2e-6, 0, 4e-6, 4e-6]), net_id="n2")])
    project.power.rules.net_spacing = {"n1:n2": .5e-6}
    assert any(item["rule_id"] == "net_spacing" for item in validate_rules(project))


@pytest.mark.parametrize("suffix", [".gds", ".oas"])
def test_import_selected_instance_preserves_its_coordinate_frame(tmp_path, suffix):
    project = source_project(tmp_path, hierarchy=True)
    project.layout.instance_path = [0]
    project.power.recipes[0].instance_path = []
    result = generate_candidate(project, {}, tmp_path / ("selected" + suffix))
    assert result.valid, result.violations
    assert prepare_geometry(result.project).layers["metal"].area == pytest.approx(40e-12)


def test_source_content_hash_is_checked_without_recipe_hash(tmp_path):
    project = source_project(tmp_path)
    project.layout.sha256 = "outdated"
    result = generate_candidate(project, {}, tmp_path / "changed.gds")
    assert not result.valid
    assert result.violations[0]["rule_id"] == "source_identity"


@pytest.mark.parametrize("suffix", [".gds", ".oas"])
@pytest.mark.parametrize("protection", ["fixed_region", "keepout"])
def test_selected_import_all_instance_edit_checks_protected_siblings(tmp_path, suffix, protection):
    project = source_project(tmp_path, hierarchy=True)
    project.layout.instance_path = [0]
    recipe = project.power.recipes[0]
    recipe.instance_path = []
    recipe.all_instances = True
    if protection == "fixed_region":
        protected = box(30e-6, 0, 50e-6, 4e-6)
        project.power.rules.fixed_regions = [rect(list(protected.bounds))]
    else:
        protected = box(30e-6, 4e-6, 50e-6, 5e-6)
        project.power.rules.keepouts = [rect(list(protected.bounds))]
        recipe.edit_region = rect([0, -1e-6, 20e-6, 5e-6])
        recipe.parameters["width"] = 6e-6
    source_hash = hashlib.sha256(open(project.layout.path, "rb").read()).hexdigest()
    result = generate_candidate(project, {}, tmp_path / ("protected-sibling" + suffix))
    assert not result.valid
    assert any(item["rule_id"] == protection for item in result.violations)
    assert hashlib.sha256(open(project.layout.path, "rb").read()).hexdigest() == source_hash
    # The rejected export really changed the other placement; checking only the
    # imported instance would miss this forbidden edit.
    before = LayoutDocument(project.layout.path).extract("TOP")["1/0"]
    after = LayoutDocument(result.project.layout.path).extract("TOP")["1/0"]
    assert before.symmetric_difference(after).intersection(protected).area > 0


@pytest.mark.parametrize("suffix", [".gds", ".oas"])
def test_selected_import_export_provenance_covers_complete_source(tmp_path, suffix):
    project = source_project(tmp_path, hierarchy=True)
    project.layout.instance_path = [0]
    project.power.recipes[0].instance_path = []
    generated = generate_candidate(project, {}, tmp_path / ("selected-generated" + suffix))
    assert generated.valid, generated.violations
    masks = LayoutDocument(generated.project.layout.path).extract("TOP")
    expected = {key: canonical_geometry_id(value) for key, value in masks.items()}
    assert generated.provenance["canonical_masks"] == expected
    other_format = ".oas" if suffix == ".gds" else ".gds"
    exported = export_candidate(generated.project, tmp_path / ("selected-copy" + other_format))
    assert exported.valid, exported.violations
    assert exported.provenance["canonical_masks"] == expected


@pytest.mark.parametrize("suffix", [".gds", ".oas"])
def test_export_rejects_modified_candidate_source_before_writing(tmp_path, suffix):
    project = source_project(tmp_path)
    generated = generate_candidate(project, {}, tmp_path / "generated.gds")
    assert generated.valid, generated.violations
    layout = kdb.Layout()
    layout.read(generated.project.layout.path)
    layout.cell("TOP").shapes(layout.layer(9, 7)).insert(kdb.Box(30000, 0, 32000, 2000))
    layout.write(generated.project.layout.path)
    output = tmp_path / ("stale-copy" + suffix)
    result = export_candidate(generated.project, output)
    assert not result.valid
    assert result.violations[0]["rule_id"] == "source_identity"
    assert not result.provenance.get("export_reimport_verified", False)
    assert not output.exists()
    assert not output.with_suffix(output.suffix + ".provenance.json").exists()


def test_small_notch_is_reported_in_integer_database_units():
    u_shape = box(0, 0, 4e-6, 4e-6).difference(box(1.9e-6, 2e-6, 2.1e-6, 4e-6))
    project = Project(layers=[PhysicalLayer("m", "M", "copper", geometry=RegionSpec(polygons=[dict(exterior=list(u_shape.exterior.coords))]))])
    project.power.rules.minimum_notch = .5e-6
    assert any(item["rule_id"] == "minimum_notch" for item in validate_rules(project))
