# SPDX-License-Identifier: MPL-2.0
"""Temperature-aware passive extraction on exact retained meshes and real CLI paths."""
import json
from copy import deepcopy
from pathlib import Path

import numpy as np
import pytest
from solver_helpers import rectangle

from etlayout.cli.main import main
from etlayout.geometry.prepare import prepare_geometry
from etlayout.io.project import configuration_hash, load_project, physics_hash, save_project
from etlayout.models import PhysicalPort, PortLanding, SimulationError, SimulationResult, TransientHistory
from etlayout.power.multiport import extract_multiport
from etlayout.power.multiport_context import retained_temperature_reason, temperature_inputs
from etlayout.power.workflows import execute_operation
from etlayout.simulation.execution import ExecutionSettings, execution_context
from etlayout.simulation.runner import mesh_fingerprint, run_project

ROOT = Path(__file__).parents[1]
EXECUTION = ExecutionSettings(cpu_threads=1, study_workers=1)


def local_temperature_fixture():
    """Analytical P1 fixture: rho varies linearly along an exactly meshed strip."""
    project, mesh = rectangle(nx=60, ny=1)
    law = project.materials["copper"].resistivity
    law.kind, law.alpha, law.reference_temperature = "linear", .002, 300.
    project.power.ports = [PhysicalPort("positive", [PortLanding("right", .1)]),
                          PhysicalPort("negative", [PortLanding("left", .3)], kind="ground")]
    temperatures = 300 + 100 * mesh.points[:, 0] / 10e-6
    local = temperatures[mesh.triangles]
    # A display value must not replace authoritative component-local P1 fields.
    display = np.full(len(mesh.points), 400.)
    display[0] = np.nan
    result = SimulationResult(mesh, fields={"metal": {"temperature": display}},
        element_fields={"metal": {"temperature": local}}, converged=True,
        configuration=deepcopy(project.to_dict()), configuration_hash=configuration_hash(project),
        physics_hash=physics_hash(project))
    return project, result, local


@pytest.mark.numerical
def test_retained_local_p1_temperature_preserves_exact_integral_and_mesh_without_smoothing():
    project, result, local = local_temperature_fixture()
    before = deepcopy(project.to_dict())
    inputs, context = temperature_inputs(project, mode="retained", result=result)
    assert np.array_equal(inputs["metal"], local)
    assert not np.shares_memory(inputs["metal"], result.element_fields["metal"]["temperature"])
    assert context["mesh_identity"] == mesh_fingerprint(result.mesh)
    report = extract_multiport(project, result.mesh, temperatures=inputs)
    # Integral rho(T(x)) dx/(width*thickness) = .22 ohm; finite feeds add .4 ohm.
    # A smoothed/fallback 400 K field would instead yield .64 ohm.
    assert report["resistance_matrix"][0][0] == pytest.approx(.62, rel=2e-5)
    assert report["temperature"]["layers"]["metal"] == {"minimum_K": 300., "maximum_K": 400.}
    assert project.to_dict() == before
    assert np.isnan(result.fields["metal"]["temperature"][0])
    assert np.array_equal(result.element_fields["metal"]["temperature"], local)


@pytest.mark.parametrize("damage,reason", [
    ("absent", "compatible thermal run"), ("nonconverged", "did not converge"),
    ("incomplete", "incomplete"), ("stale", "Stale result"),
    ("no_configuration", "lacks source"), ("no_temperature", "no retained temperature"),
    ("missing_conductor", "lacks temperature on conductor"),
    ("physics_identity", "physical identity is inconsistent"),
    ("configuration_identity", "configuration identity is inconsistent"),
    ("nonfinite_local", "finite positive component-local"),
    ("nonpositive_local", "finite positive component-local"),
    ("local_shape", "component-local temperature shape"),
    ("invalid_mesh", "degenerate triangles"),
])
def test_retained_temperature_rejects_invalid_or_incompatible_authority(damage, reason):
    project, result, _ = local_temperature_fixture()
    if damage == "absent":
        result = None
    elif damage == "nonconverged":
        result.converged = False
    elif damage == "incomplete":
        result.transient = TransientHistory(completed=False)
    elif damage == "stale":
        project.materials["copper"].resistivity.value *= 2
    elif damage == "no_configuration":
        result.configuration = {}
    elif damage == "no_temperature":
        result.fields = {}
    elif damage == "missing_conductor":
        result.fields = {"unrelated": {"temperature": np.full(len(result.mesh.points), 300.)}}
    elif damage == "physics_identity":
        result.physics_hash = "0" * 64
    elif damage == "configuration_identity":
        result.configuration_hash = "0" * 64
    elif damage == "nonfinite_local":
        result.element_fields["metal"]["temperature"][0, 0] = np.nan
    elif damage == "nonpositive_local":
        result.element_fields["metal"]["temperature"][0, 0] = 0
    elif damage == "local_shape":
        result.element_fields["metal"]["temperature"] = np.ones((len(result.mesh.triangles), 2)) * 300
    elif damage == "invalid_mesh":
        result.mesh.points[result.mesh.triangles[0, 0]] = result.mesh.points[result.mesh.triangles[0, 1]]
    with pytest.raises(SimulationError, match=reason):
        temperature_inputs(project, mode="retained", result=result)


@pytest.mark.parametrize("mode,value", [("uniform", None), ("uniform", 0), ("uniform", -1),
    ("uniform", np.inf), ("uniform", np.nan), ("initial", 350), ("retained", 350), ("auto", None)])
def test_temperature_choice_requires_explicit_nonconflicting_positive_kelvin(mode, value):
    project, result, _ = local_temperature_fixture()
    with pytest.raises(SimulationError):
        temperature_inputs(project, mode=mode, temperature_K=value, result=result)
    assert project.solver.initial_temperature == 300


def test_complete_retained_context_records_actual_final_time_and_changes_with_local_fields():
    project, result, _ = local_temperature_fixture()
    result.transient = TransientHistory(times=np.array([0., .003]), completed=True)
    assert retained_temperature_reason(project, result) == ""
    _, context = temperature_inputs(project, mode="retained", result=result)
    assert context["frame"] == "final" and context["time_s"] == .003
    result.element_fields["metal"]["temperature"][0, 0] += 1
    _, changed = temperature_inputs(project, mode="retained", result=result)
    assert changed["mesh_identity"] == context["mesh_identity"]
    assert changed["temperature_fields_sha256"] != context["temperature_fields_sha256"]


def physical_e1():
    project, _ = load_project(ROOT / "examples/E1_straight.etl")
    project.power.ports = [PhysicalPort("port_" + terminal.id, [PortLanding(terminal.id)],
        kind=terminal.kind, value=terminal.value) for terminal in project.terminals]
    return project


@pytest.fixture(scope="module")
def retained_e1(tmp_path_factory):
    project = physical_e1()
    project.solver.mode = "electrothermal"
    result = run_project(project, execution=EXECUTION)
    assert result.converged
    assert result.metrics["temperature_max"] > project.solver.initial_temperature
    path = tmp_path_factory.mktemp("mt") / "e1.etl"
    save_project(project, path, result)
    return project, result, path


@pytest.mark.numerical
def test_real_e1_uniform_350k_uses_material_law_without_changing_working_physics(retained_e1):
    project, result, _ = retained_e1
    source = deepcopy(project.to_dict())
    local = result.element_fields["metal"]["temperature"].copy()
    with execution_context(EXECUTION):
        initial = execute_operation(project, "multiport")["report"]
        uniform = execute_operation(project, "multiport", {"temperature_mode": "uniform", "temperature_K": 350.})["report"]
    bounds = prepare_geometry(project).layers["metal"].bounds
    length, width = bounds[2] - bounds[0], bounds[3] - bounds[1]
    layer = project.layer("metal")
    law = project.materials[layer.material_id].resistivity
    rho = law.value * (1 + law.alpha * (350 - law.reference_temperature))
    expected_r = rho * length / (width * layer.thickness)
    assert uniform["resistance_matrix"][0][0] == pytest.approx(expected_r, rel=1e-10)
    factor = (1 + law.alpha * (350 - law.reference_temperature))
    assert np.asarray(uniform["conductance_matrix"]) == pytest.approx(np.asarray(initial["conductance_matrix"]) / factor, rel=1e-10)
    assert uniform["temperature"] == {"kind": "uniform_fixed", "temperature_K": 350.}
    assert project.to_dict() == source
    assert np.array_equal(result.element_fields["metal"]["temperature"], local, equal_nan=True)


@pytest.mark.numerical
def test_real_saved_retained_run_cli_reuses_exact_mesh_and_temperature_operator(retained_e1, tmp_path, capsys):
    _, _, path = retained_e1
    reopened, result = load_project(path)
    with execution_context(EXECUTION):
        expected = execute_operation(reopened, "multiport", {"temperature_mode": "retained", "result": result})["report"]
    output = tmp_path / "retained.json"
    assert main(["multiport", str(path), "--temperature-source", "retained", "--cpu-threads", "1", "-o", str(output)]) == 0
    report = json.loads(output.read_text(encoding="utf-8"))
    assert report["conductance_matrix"] == expected["conductance_matrix"]
    assert report["mesh"] == expected["mesh"]
    assert report["extraction_context"] == expected["extraction_context"]
    assert report["extraction_context"]["mesh_identity"] == mesh_fingerprint(result.mesh)
    assert report["temperature"]["kind"] == "fixed_spatial_fields"
    capsys.readouterr()


@pytest.mark.numerical
def test_cli_uniform_kelvin_prepares_real_port_basis_bundle_without_claiming_simulator_run(retained_e1, tmp_path, capsys):
    _, _, path = retained_e1
    output, bundle = tmp_path / "uniform.json", tmp_path / "basis"
    assert main(["multiport", str(path), "--temperature-k", "350", "--cpu-threads", "1",
        "--validation-directory", str(bundle), "-o", str(output)]) == 0
    report = json.loads(output.read_text(encoding="utf-8"))
    manifest = json.loads((bundle / "manifest.json").read_text(encoding="ascii"))
    assert report["extraction_context"] == {"version": 1, "mode": "uniform", "temperature_K": 350.}
    assert report["circuit_validation"]["status"] == manifest["status"] == "prepared"
    assert len(manifest["cases"]) == 2 and (bundle / "network.cir").is_file()
    for index, case in enumerate(manifest["cases"]):
        assert case["expected_entering_currents_A"] == np.asarray(report["conductance_matrix"])[:, index].tolist()
        assert (bundle / case["deck"]).is_file()
    capsys.readouterr()


@pytest.mark.numerical
def test_cli_real_three_level_mesh_study_keeps_actual_finest_operator(retained_e1, tmp_path, capsys):
    project, _, path = retained_e1
    source = deepcopy(project.to_dict())
    output = tmp_path / "mesh.json"
    assert main(["multiport", str(path), "--mesh-sizes-um", "14", "7", "3.5",
        "--cpu-threads", "1", "--temperature-k", "350", "-o", str(output)]) == 0
    report = json.loads(output.read_text(encoding="utf-8"))
    assert report["converged"] and report["mesh_verified"]
    assert len(report["levels"]) == 3 and len(report["comparisons"]) == 2
    assert report["requested_sizes_m"] == pytest.approx([14e-6, 7e-6, 3.5e-6])
    assert all(item["distinct_refinement"] and item["within_tolerance"] for item in report["comparisons"])
    fine = report["levels"][-1]
    assert report["network"]["mesh"] == fine["mesh"]
    assert report["network"]["conductance_matrix"] == fine["conductance_matrix"]
    assert report["network"]["source_physics_hash"] == report["source_physics_hash"]
    assert fine["mesh"]["elements"] > report["levels"][0]["mesh"]["elements"]
    assert report["temperature"] == {"kind": "uniform_fixed", "temperature_K": 350.}
    assert project.to_dict() == source
    capsys.readouterr()


@pytest.mark.parametrize("arguments,reason", [
    (["--ngspice-executable", "missing.exe"], "requires a new/empty --validation-directory"),
    (["--mesh-tolerance", "-1"], "--mesh-tolerance requires --mesh-study"),
    (["--simulator-timeout", "-1"], "--simulator-timeout requires --ngspice-executable"),
    (["--mesh-study", "--format", "spice"], "Mesh studies output JSON"),
    (["--mesh-study", "--validation-directory", "unused"], "Mesh studies output JSON"),
    (["--temperature-source", "uniform"], "explicit fixed temperature"),
    (["--temperature-source", "initial", "--temperature-k", "350"], "Choose uniform temperature"),
    (["--temperature-source", "retained"], "compatible thermal run"),
])
def test_cli_rejects_missing_or_conflicting_inputs_before_numerical_execution(tmp_path, capsys, arguments, reason):
    path = tmp_path / "e1.etl"
    save_project(physical_e1(), path)
    output = tmp_path / "not_written.json"
    assert main(["multiport", str(path), "--cpu-threads", "1", "-o", str(output), *arguments]) == 2
    assert reason in capsys.readouterr().err
    assert not output.exists()
