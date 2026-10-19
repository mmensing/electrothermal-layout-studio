# SPDX-License-Identifier: MPL-2.0
"""Immutable geometry, bounded flux indicators and opt-in grading regression."""

from copy import deepcopy

import numpy as np
import pytest
from shapely.geometry import box
from solver_helpers import rectangle

from etlayout.geometry.prepare import prepare_geometry, resolve_region
from etlayout.io.project import physics_hash, project_from_dict
from etlayout.meshing.generate import generate_mesh
from etlayout.meshing.physical import flux_jump_indicator
from etlayout.meshing.refinement import apply_refinement_plan, screen_resources, suggest_refinements
from etlayout.models import RefinementRegion, SimulationError
from etlayout.simulation.runner import run_project
from etlayout.solvers import solve_electrical
from etlayout.validation.crowding import benchmark_project


@pytest.mark.fast
def test_flux_indicator_preserves_uniform_field_and_component_separation():
    project, mesh = rectangle()
    result = solve_electrical(project, mesh)
    ids, eta = flux_jump_indicator(result, "metal", result.fields["metal"]["sheet_current"])
    assert len(ids) == len(mesh.triangles)
    assert eta.sum() < 1e-20
    modified = result.fields["metal"]["sheet_current"].copy()
    modified[0] *= 2
    _, eta = flux_jump_indicator(result, "metal", modified)
    assert np.count_nonzero(eta) > 0
    assert np.count_nonzero(eta) <= 4


@pytest.mark.fast
def test_radius_relative_sizing_and_grading_do_not_modify_physical_geometry():
    project = benchmark_project("rounded_L", 4e-6, radius=0.5e-6)
    geometry = prepare_geometry(project)
    original = project.to_dict()
    plan = suggest_refinements(project)
    suggestion = next(s for s in plan.suggestions if s.purpose == "Rounded boundary")
    assert suggestion.size == pytest.approx(0.5e-6 / 6, rel=1e-8)
    assert suggestion.grading_length > 0
    candidate = apply_refinement_plan(project, plan, [suggestion.id])
    prepared = prepare_geometry(candidate)
    assert prepared.layers["metal"].equals(geometry.layers["metal"])
    assert project.to_dict() == original
    assert candidate.mesh.min_size == pytest.approx(suggestion.size)
    assert (
        project_from_dict(candidate.to_dict()).mesh.refinements[-1].grading_length
        == suggestion.grading_length
    )
    plan.max_memory_bytes = 1
    with pytest.raises(SimulationError, match="exceeds budget"):
        screen_resources(candidate, plan)


@pytest.mark.fast
def test_legacy_grading_preserves_physics_hash_and_invalid_grading_rejected():
    project = benchmark_project("strip", 4e-6)
    project.mesh.refinements = [RefinementRegion("window", project.layers[0].geometry, 2e-6)]
    legacy = project.to_dict()
    legacy["mesh"]["refinements"][0].pop("grading_length", None)
    assert physics_hash(legacy) == physics_hash(project)
    assert "grading_length" not in project.to_dict()["mesh"]["refinements"][0]
    from etlayout.io.project import configuration_hash

    assert configuration_hash(legacy) == configuration_hash(project)
    project.mesh.refinements[0].grading_length = float("nan")
    with pytest.raises(SimulationError, match="grading"):
        generate_mesh(project, prepare_geometry(project))


@pytest.mark.numerical
def test_bounded_flux_guidance_is_optional_and_rejects_stale_context():
    project = benchmark_project("sharp_L", 4e-6)
    result = run_project(project)
    ordinary = suggest_refinements(project, result)
    assert not any(s.purpose == "Flux-jump indicator" for s in ordinary.suggestions)
    plan = suggest_refinements(project, result, field_driven=True)
    assert plan.indicators["metal"]["marked"] <= 64
    flux = next(s for s in plan.suggestions if s.purpose == "Flux-jump indicator")
    support = resolve_region(flux.geometry, {})
    assert support.intersection(box(0, 0, 100e-6, 100e-6)).area == pytest.approx(support.area)
    assert support.difference(prepare_geometry(project).layers["metal"]).area < 1e-24
    modified = deepcopy(project)
    modified.terminals[0].value *= 2
    with pytest.raises(SimulationError, match="converged result"):
        suggest_refinements(modified, result, field_driven=True)


@pytest.mark.numerical
def test_smooth_grading_runs_real_mesh_preserving_boundary_and_conservation():
    project = benchmark_project("rounded_L", 4e-6)
    result = run_project(project)
    plan = suggest_refinements(project, result)
    selected = [s.id for s in plan.suggestions if s.purpose == "Rounded boundary"]
    candidate = apply_refinement_plan(project, plan, selected)
    screen_resources(candidate, plan)
    refined = run_project(candidate)
    assert refined.converged
    assert len(refined.mesh.triangles) > len(result.mesh.triangles)
    assert refined.mesh.statistics["minimum_quality"] > 0.05
    assert refined.metrics["power_balance_error"] < project.solver.power_balance_tolerance
    assert refined.metrics["thermal_balance_error"] < project.solver.thermal_balance_tolerance
    assert prepare_geometry(candidate).layers["metal"].equals(prepare_geometry(project).layers["metal"])


@pytest.fixture(scope="module")
def local_sequences():
    from etlayout.simulation.sweep import mesh_refinement_study

    sequences = {}
    for case, purpose in (
        ("sharp_L", "Reentrant / neck boundary"),
        ("rounded_L", "Rounded boundary"),
        ("finite_contact", "Contact transfer"),
    ):
        project = benchmark_project(case, 4e-6)
        if case == "finite_contact":
            # A shorter 2 µm transfer length makes local contact resolution the
            # variable, rather than refining an already over-resolved contact.
            project.interfaces[0].electrical_value = 4e-14
        result = run_project(project)
        plan = suggest_refinements(project, result)
        selected = [suggestion.id for suggestion in plan.suggestions if suggestion.purpose == purpose]
        rows = mesh_refinement_study(project, [1.0, 0.5, 0.25], local_plan=plan, selected=selected)
        sequences[case] = (project, plan, selected, rows)
    return sequences


@pytest.mark.numerical
@pytest.mark.parametrize("case", ["sharp_L", "rounded_L", "finite_contact"])
def test_three_local_mesh_levels_report_actual_metric_confidence(case, local_sequences):
    project, plan, selected, rows = local_sequences[case]
    assert all(row.result and row.result.converged and not row.error for row in rows)
    receipt = rows[-1].result.analysis["local_mesh_verification"]
    assert receipt["status"] == "available", receipt
    metrics = receipt["layers"]["metal"]["metrics"]
    assert metrics["joule_power_W"]["status"] == "mesh_converged"
    assert metrics["sheet_current_average_A_per_m"]["status"] == "mesh_converged"
    assert metrics["temperature_max_K"]["status"] == "mesh_converged"
    if case == "sharp_L":
        assert metrics["raw_sheet_current_max_A_per_m"]["status"] == "singular_peak_unverified"
    assert not any(record["physical_qualification"] for record in metrics.values())
    assert receipt["levels"][-1]["elements"] > receipt["levels"][0]["elements"]
    assert all(level["execution"]["timings_seconds"]["numerical"] > 0 for level in receipt["levels"])
    from etlayout.meshing.physical import local_mesh_verification

    assert local_mesh_verification(project, rows[:2], plan, selected)["status"] == "unavailable"
    modified = deepcopy(project)
    modified.materials["metal"].resistivity.value *= 2
    assert local_mesh_verification(modified, rows, plan, selected)["status"] == "unavailable"


@pytest.mark.desktop
def test_mesh_confidence_dialog_displays_authoritative_results(qapp, local_sequences):
    from PySide6.QtWidgets import QTableWidget

    from etlayout.gui.plots import StudyPanel
    from etlayout.gui.refinement import MeshVerificationDialog

    rows = local_sequences["sharp_L"][-1]
    receipt = rows[-1].result.analysis["local_mesh_verification"]
    panel = StudyPanel()
    panel.set_rows(rows, "Local refinement factor")
    assert not panel.mesh_confidence.isHidden()
    dialog = MeshVerificationDialog(receipt, panel)
    table = dialog.findChild(QTableWidget)
    assert any(table.item(row, 1).text() == "Singular · unverified" for row in range(table.rowCount()))
    assert any("Fixed support" in table.item(row, 0).toolTip() for row in range(table.rowCount()))
    assert all(table.item(row, 2).text() for row in range(table.rowCount()))
    dialog.close()
    panel.close()
    dialog.deleteLater()
    panel.deleteLater()


@pytest.mark.fast
def test_via_and_two_resistive_sheets_transfer_length_bound():
    from etlayout.meshing.refinement import contact_transfer_estimate

    project = benchmark_project("via_landing", 4e-6)
    interface = project.interfaces[0]
    length, _ = contact_transfer_estimate(project, interface, "metal")
    expected = np.sqrt(2e-8 * 2e-6 * (2e-6 / 2e-8))
    assert length == pytest.approx(expected)
    plan = suggest_refinements(project)
    bound = next(s for s in plan.suggestions if s.purpose == "Coupled transfer bound")
    assert bound.length == pytest.approx(expected / np.sqrt(2))
    assert bound.size == pytest.approx(expected / (4 * np.sqrt(2)))
    assert "two-sheet" in bound.reason


@pytest.mark.fast
def test_via_transfer_uses_only_authoritative_interface_temperature():
    from types import SimpleNamespace

    from etlayout.meshing.refinement import contact_transfer_estimate
    from etlayout.models import PropertyModel

    project = benchmark_project("via_landing", 4e-6)
    interface = project.interfaces[0]
    project.materials[interface.material_id].resistivity = PropertyModel(
        kind="linear", value=2e-8, alpha=-0.001, reference_temperature=300.0
    )
    state = SimpleNamespace(
        mesh=SimpleNamespace(regions={"interface:" + interface.id: [0]}),
        fields={
            key: {
                "temperature": np.array([900.0, 900.0, 900.0]),
                "sheet_conductance": np.array([100.0, 100.0]),
            }
            for key in (interface.layer_a, interface.layer_b)
        },
        element_fields={
            interface.layer_a: {"temperature": np.array([[400.0, 400.0, 400.0], [900.0, 900.0, 900.0]])},
            interface.layer_b: {"temperature": np.array([[600.0, 600.0, 600.0], [900.0, 900.0, 900.0]])},
        },
    )
    length, _ = contact_transfer_estimate(project, interface, "metal", [state])
    # 500 K interface temperature, rather than the ambiguous 900 K nodal view.
    assert length == pytest.approx(np.sqrt(2e-8 * 0.8 * interface.height * 100.0))


@pytest.mark.numerical
@pytest.mark.parametrize("completed,end", [(False, 1e-3), (True, 0.5e-3)])
def test_local_receipt_rejects_incomplete_or_short_transient(local_sequences, completed, end):
    from dataclasses import replace

    from etlayout.meshing.physical import local_mesh_verification
    from etlayout.models import TransientHistory

    project, plan, selected, rows = local_sequences["sharp_L"]
    rows = [
        replace(
            row,
            result=replace(row.result, transient=TransientHistory(rows=[{"time": end}], completed=completed)),
        )
        for row in rows
    ]
    receipt = local_mesh_verification(project, rows, plan, selected)
    assert receipt["status"] == "unavailable"
    assert "same requested physical duration" in receipt["reason"]


@pytest.mark.numerical
@pytest.mark.parametrize(
    "change,reason",
    [("size", "sizes must decrease"), ("geometry", "supports changed"), ("missing", "supports changed")],
)
def test_local_receipt_checks_actual_selected_controls(local_sequences, change, reason):
    from dataclasses import replace

    from etlayout.meshing.physical import local_mesh_verification

    project, plan, selected, rows = local_sequences["sharp_L"]
    rows = [
        replace(row, result=replace(row.result, configuration=deepcopy(row.result.configuration)))
        for row in rows
    ]
    controls = rows[-1].result.configuration["mesh"]["refinements"]
    target = next(r for r in controls if r["id"] == "guided-" + selected[0])
    if change == "size":
        target["size"] = plan.suggestions[0].size * 100
    elif change == "geometry":
        target["geometry"]["polygons"][0]["exterior"][0][0] += 1e-6
    else:
        controls.remove(target)
    receipt = local_mesh_verification(project, rows, plan, selected)
    assert receipt["status"] == "unavailable"
    assert reason in receipt["reason"]


@pytest.mark.numerical
def test_engineering_readout_matches_real_receipt_support_without_reusing_it_on_other_windows(local_sequences):
    from etlayout.io.project import from_dict
    from etlayout.models import RegionSpec
    from etlayout.power.engineering import scoped_engineering

    _, _, _, rows = local_sequences["rounded_L"]
    result = rows[-1].result
    receipt = result.analysis["local_mesh_verification"]
    support = from_dict(RegionSpec, receipt["layers"]["metal"]["support"])
    report = scoped_engineering(result, "metal", window=support)
    assert report["mesh_evidence"]["status"] == "selected_metric_evidence"
    assert report["mesh_evidence"]["metrics"] == receipt["layers"]["metal"]["metrics"]
    assert report["mesh_evidence"]["physical_qualification"] is False
    shape = resolve_region(support, {})
    center = shape.representative_point()
    tiny = RegionSpec(polygons=[dict(bounds=[center.x-.02e-6, center.y-.02e-6, center.x+.02e-6, center.y+.02e-6])])
    other = scoped_engineering(result, "metal", window=tiny)
    assert other["mesh_evidence"]["status"] == "not_established"
    assert "different physical support" in other["mesh_evidence"]["reason"]
