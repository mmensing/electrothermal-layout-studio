# SPDX-License-Identifier: MPL-2.0
"""Physical evidence stays separate from numerical and engineering acceptance."""
import json
from copy import deepcopy
from pathlib import Path

import pytest

from etlayout.example_catalog import EXAMPLE_CATALOG, example_info
from etlayout.example_science import scientific_record
from etlayout.examples import make_example
from etlayout.geometry.prepare import prepare_geometry
from etlayout.models import SimulationError
from etlayout.simulation.runner import run_project


@pytest.mark.fast
def test_every_example_declares_uncalibrated_applicability_without_promoting_mesh_status():
    assert len(EXAMPLE_CATALOG) == 40
    for info in EXAMPLE_CATALOG.values():
        record = info.to_dict()
        assert record["evidence_level"] in {"analytical reference", "synthetic engineering study",
                                             "synthetic functional demonstration"}
        assert info.physical_scope and info.learning_goal
    assert example_info("E1").evidence_level == "analytical reference"
    assert example_info("E39").evidence_level == "synthetic engineering study"
    assert example_info("E39").reference_status == "stress"
    assert example_info("E40").evidence_level == "synthetic engineering study"
    assert example_info("E40").reference_status == "reference"
    assert not any(info.evidence_level == "calibrated physical reference" for info in EXAMPLE_CATALOG.values())


@pytest.mark.fast
@pytest.mark.parametrize("identifier", ["E7", "E8"])
def test_intro_gate_crossing_is_insulated_and_intentional_bypass_is_not_hidden(identifier, tmp_path):
    project = make_example(identifier, tmp_path)
    geometry = prepare_geometry(project)
    source, gate = project.layer("source_metal"), project.layer("gate_metal")
    # The gate bus crosses a source finger in plan, above an omitted insulating
    # gap. Deleting that crossing would disconnect the gate bus from its fingers.
    assert geometry.layers[source.id].intersection(geometry.layers[gate.id]).area > 0
    assert source.z + source.thickness < gate.z
    assert not any({interface.layer_a, interface.layer_b} == {source.id, gate.id}
                   and interface.electrical_kind != "none" for interface in project.interfaces)
    assert not any(item.severity == "error" for item in geometry.diagnostics)
    assert "Ungated channel edges permit bypass" in project.notes


@pytest.mark.numerical
def test_uniform_strip_evidence_uses_exact_fields_and_loss_ledger(tmp_path):
    project = make_example("E1", tmp_path)
    result = run_project(project)
    record = scientific_record(project, result)
    assert record["electrical"]["effective_resistance"] == pytest.approx(.084, rel=1e-10)
    assert record["electrical"]["joule_power"] == pytest.approx(.02**2 / .084, rel=1e-10)
    assert record["thermal"]["temperature_max"] is None  # no invented thermal calculation
    assert record["status"]["numerically_converged"]
    assert not record["status"]["mesh_verified"] and not record["status"]["calibration_verified"]
    changed = deepcopy(project)
    changed.terminals[-1].value *= 2
    with pytest.raises(SimulationError, match="exact solved configuration"):
        scientific_record(changed, result)
    record["loss_budget"].clear()
    assert result.analysis["loss_budget"]  # reporting does not mutate results


@pytest.mark.numerical
def test_transient_evidence_distinguishes_run_peak_and_cooled_frame(tmp_path):
    project = make_example("E17", tmp_path)
    result = run_project(project)
    record = scientific_record(project, result)
    assert record["thermal"]["transient_peak_temperature"] > record["thermal"]["temperature_max"]
    assert record["thermal"]["transient_peak_time"] < project.transient.stop_time
    assert record["thermal"]["initial_temperature_K"] == project.solver.initial_temperature
    assert record["thermal"]["transient_input_energy"] > 0
    assert record["thermal"]["prescribed_power_W"] == pytest.approx(0., abs=1e-12)  # cooled final frame


@pytest.mark.numerical
@pytest.mark.parametrize("identifier", ["E37", "E39"])
def test_device_evidence_keeps_channel_and_vertical_device_losses_separate(identifier, expanded_nominal_factory):
    project, result = expanded_nominal_factory(identifier)
    record = scientific_record(project, result)
    categories = record["loss_budget"]["categories"]
    assert categories["device_load"] > 0 if identifier == "E39" else categories["channel"] > 0
    assert record["loss_budget"]["total_power"] == pytest.approx(result.metrics["electrical_power"])
    assert record["thermal"]["reference_boundary"] == "backside"
    assert record["layers"] and all(layer["material_source"] for layer in record["layers"])
    assert record["devices"] and all(row["status"] == "available" for row in record["devices"].values())


@pytest.mark.fast
@pytest.mark.parametrize("identifier", ["E20", "E21", "E22", "E23", "E24", "E40"])
def test_stack_derived_via_height_is_canonical_at_generation(identifier, tmp_path):
    project = make_example(identifier, tmp_path)
    before = project.to_dict()
    prepare_geometry(project)
    assert project.to_dict() == before


@pytest.mark.fast
def test_maintained_nominal_matrix_covers_the_catalog_and_disjoint_loss_ledger():
    path = Path(__file__).resolve().parents[1] / "docs/example-science/nominal-reference.json"
    manifest = json.loads(path.read_text(encoding="utf-8"))
    assert manifest["units"]["current_density"] == "A/m\u00b2"
    assert manifest["units"]["joule_density"] == "W/m\u00b2"
    records = manifest["examples"]
    assert {row["name"] for row in records} == set(EXAMPLE_CATALOG)
    assert len(records) == 40
    for row in records:
        assert row["status"]["numerically_converged"] and not row["status"]["mesh_verified"]
        assert not row["status"]["calibration_verified"]
        assert all(layer["material_reference"] in manifest["materials"] for layer in row["layers"])
        power = row["electrical"]["electrical_power"]
        if power is not None:
            assert sum(row["loss_categories_W"].values()) == pytest.approx(power, rel=1e-10, abs=1e-15)
    pulse = next(row for row in records if row["id"] == "E18")
    assert pulse["thermal"]["transient_peak_temperature"] > pulse["thermal"]["temperature_max"]
