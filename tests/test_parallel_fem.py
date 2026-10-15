# SPDX-License-Identifier: MPL-2.0
"""Independent topology, threaded operators, cache fidelity and failure lifecycle."""
import threading
from copy import deepcopy

import numpy as np
import pytest
from scipy.sparse import csr_matrix
from solver_helpers import all_region, rectangle

from etlayout.examples import make_example
from etlayout.geometry.prepare import prepare_geometry
from etlayout.meshing.generate import generate_mesh
from etlayout.models import HeatSource, Interface, Terminal, ThermalBoundary
from etlayout.power_examples import metadata
from etlayout.simulation import execution
from etlayout.simulation.execution import (
    ExecutionSettings,
    execution_context,
    execution_provenance,
    map_independent,
)
from etlayout.solvers import solve
from etlayout.solvers.fem import _layer_topology, layer_matrix, linear_solve
from etlayout.solvers.thermal import solve_state


def test_vectorized_topology_keeps_edge_components_and_first_appearance_dofs():
    triangles = np.array([[0, 1, 2], [1, 3, 2], [0, 4, 5], [4, 6, 5], [1, 2, 7]])
    topology = _layer_topology(np.arange(5), triangles, 7)
    assert topology.dofs.tolist() == [[0, 1, 2], [1, 3, 2], [4, 5, 6], [5, 7, 6], [1, 2, 8]]
    assert topology.nodes.tolist() == [0, 1, 2, 3, 0, 4, 5, 6, 7]
    assert topology.global_to_local.tolist() == [0, 1, 2, 3, 4, -1, -1]


def test_joined_layer_operators_preserve_independent_and_external_port_rows():
    joined = layer_matrix([csr_matrix([[2., -1.], [-1., 2.]]), csr_matrix([[3.]])], 4)
    assert joined.toarray() == pytest.approx(np.array([[2., -1., 0., 0.], [-1., 2., 0., 0.],
                                                      [0., 0., 3., 0.], [0., 0., 0., 0.]]))


@pytest.mark.numerical
def test_exact_factor_cache_changes_rhs_without_reusing_a_different_operator():
    cache = {}
    with execution_context(ExecutionSettings(1, 1)):
        assert linear_solve(csr_matrix([[2., -1.], [-1., 2.]]), np.array([1., 0.]), {},
                            factor_cache=cache) == pytest.approx([2/3, 1/3])
        original = cache["factor"]
        assert linear_solve(csr_matrix([[2., -1.], [-1., 2.]]), np.array([0., 1.]), {},
                            factor_cache=cache) == pytest.approx([1/3, 2/3])
        assert cache["factor"] is original
        assert linear_solve(csr_matrix([[3., -1.], [-1., 2.]]), np.array([1., 0.]), {},
                            factor_cache=cache) == pytest.approx([.4, .2])
        assert cache["factor"] is not original
        assert execution_provenance()["linear_factorizations"] == 2
        assert execution_provenance()["reused_factorizations"] == 1


@pytest.mark.numerical
def test_parallel_ohmic_layers_match_analytical_currents_and_serial_fields(monkeypatch):
    monkeypatch.setattr(execution, "PARALLEL_MIN_WORK", 0)
    project, mesh = rectangle(nx=24, ny=12, layers=("thin", "thick"))
    project.layers[1].thickness *= 2
    mesh.terminal_nodes.update(thick_left=mesh.terminal_nodes["left"], thick_right=mesh.terminal_nodes["right"])
    project.terminals += [Terminal("thick_left", "thick", kind="ground"),
                          Terminal("thick_right", "thick", kind="voltage", value=1.)]
    results = []
    for cpus in (1, min(4, execution.detect_capabilities()["cpu_count"])):
        with execution_context(ExecutionSettings(cpus, 1)):
            result = solve(project, mesh)
            assert result.converged
            assert abs(result.terminals["right"]["current"]) == pytest.approx(5., rel=1e-10)
            assert abs(result.terminals["thick_right"]["current"]) == pytest.approx(10., rel=1e-10)
            assert result.metrics["power_balance_error"] < 1e-10
            results.append((result, execution_provenance()))
    for layer in ("thin", "thick"):
        assert results[0][0].fields[layer]["potential"] == pytest.approx(results[1][0].fields[layer]["potential"], abs=1e-12)
    if execution.detect_capabilities()["cpu_count"] > 1:
        assert results[1][1]["parallel_assembly"]["batches"] > 0
        assert 1 <= len(results[1][1]["parallel_assembly"]["worker_thread_ids"]) <= 4


@pytest.mark.numerical
@pytest.mark.parametrize("name", ["E21", "E23"])
def test_parallel_exact_nonlinear_and_vertical_load_examples_preserve_fields_and_service(name, tmp_path, monkeypatch):
    monkeypatch.setattr(execution, "PARALLEL_MIN_WORK", 0)
    project = make_example(name, tmp_path / name, preset="compact")
    mesh = generate_mesh(project, prepare_geometry(project))
    results = []
    for budget in (1, min(4, execution.detect_capabilities()["cpu_count"])):
        with execution_context(ExecutionSettings(budget, 1)):
            result = solve(project, mesh)
            assert result.converged, result.diagnostics
            assert result.analysis["ports"]["drain"]["current"] == pytest.approx(
                metadata(project)["requested_current_A"], rel=1e-8)
            for metric, tolerance in (("power_balance_error", project.solver.power_balance_tolerance),
                                      ("current_balance_error", project.solver.current_balance_tolerance),
                                      ("thermal_balance_error", project.solver.thermal_balance_tolerance)):
                assert result.metrics[metric] < tolerance
            ledger = result.analysis["loss_budget"]
            assert sum(ledger["categories"].values()) == pytest.approx(result.metrics["electrical_power"], rel=1e-8)
            if budget > 1:
                assert execution_provenance()["parallel_assembly"]["batches"] > 0
            results.append(result)
    for layer, fields in results[0].fields.items():
        for quantity, values in fields.items():
            np.testing.assert_allclose(results[1].fields[layer][quantity], values, rtol=1e-11, atol=1e-11)
    assert results[1].metrics["temperature_max"] == pytest.approx(results[0].metrics["temperature_max"], rel=1e-11)


@pytest.mark.numerical
def test_thermal_cache_tracks_boundary_and_material_changes_and_final_validity():
    project, mesh = rectangle(nx=8, ny=4)
    project.solver.mode = "thermal"
    project.heat_sources = [HeatSource("uniform", "metal", value=10.)]
    project.thermal_boundaries = [ThermalBoundary("sink", "metal", kind="coefficient", value=2., ambient=300.)]
    all_region(mesh, "heat", "uniform")
    all_region(mesh, "thermal", "sink")
    state = solve_state(project, mesh)
    assert state.result.fields["metal"]["temperature"] == pytest.approx(305., abs=1e-8)
    old_key = state.space._thermal_operator[0]
    project.thermal_boundaries[0].value = 5.
    changed = solve_state(project, mesh, space=state.space)
    assert changed.result.fields["metal"]["temperature"] == pytest.approx(302., abs=1e-8)
    project.materials["copper"].thermal_conductivity.value = 20.
    changed = solve_state(project, mesh, space=state.space)
    assert changed.space._thermal_operator[0] != old_key
    project.materials["copper"].thermal_conductivity.maximum_temperature = 301.
    with pytest.warns(RuntimeWarning, match="outside validity range"):
        changed = solve_state(project, mesh, space=state.space)
    assert changed.result.fields["metal"]["temperature"] == pytest.approx(302., abs=1e-8)


@pytest.mark.numerical
def test_thermal_cache_tracks_stack_area_resistance_and_matches_fresh_analytical_state():
    project, mesh = rectangle(layers=("top", "bottom"))
    project.materials["copper"].thermal_conductivity_z = 2.
    project.thermal_boundaries = [ThermalBoundary("sink", "bottom", kind="temperature", value=300.)]
    project.heat_sources = [HeatSource("heater", "top", value=1e6)]
    project.interfaces = [Interface("junction", "top", "bottom", thermal_kind="stack", material_id="copper",
                                    height=1e-6, thermal_resistance_area=1e-6)]
    for prefix, key in (("interface", "junction"), ("thermal", "sink"), ("heat", "heater")):
        all_region(mesh, prefix, key)
    initial = solve_state(project, mesh)
    original_key = initial.space._thermal_operator[0]
    project.interfaces[0].thermal_resistance_area = 4e-6
    reused = solve_state(project, mesh, space=initial.space)
    fresh = solve_state(project, mesh)
    expected = 300 + 1e6 * (.5e-6 / 2. + 1e-6 / 2. + 4e-6)
    assert reused.space._thermal_operator[0] != original_key
    for result in (reused.result, fresh.result):
        assert result.converged
        assert result.fields["top"]["temperature"] == pytest.approx(expected, abs=1e-10)
        assert result.fields["bottom"]["temperature"] == pytest.approx(300., abs=1e-10)
        assert result.metrics["thermal_power"] == pytest.approx(20e-6, rel=1e-10)
        assert result.metrics["heat_out"] == pytest.approx(20e-6, rel=1e-10)
        assert result.metrics["thermal_balance_error"] < 1e-10


def test_worker_exception_finishes_other_tasks_before_return_and_keeps_native_cap(monkeypatch):
    monkeypatch.setattr(execution, "PARALLEL_MIN_WORK", 0)
    budget = min(2, execution.detect_capabilities()["cpu_count"])
    started = threading.Event()
    failed = threading.Event()
    completed = threading.Event()

    def task(index):
        if index == 0:
            assert started.wait(5.), "The independent native task never started"
            failed.set()
            raise ValueError("Invalid layer input")
        started.set()
        if budget > 1:
            assert failed.wait(5.), "The failure task never reached its exception"
        data = np.linspace(0., 1., 262144)
        for _ in range(64):
            data = np.sqrt(data + .1)
        from threadpoolctl import threadpool_info
        assert all(pool["num_threads"] == 1 for pool in threadpool_info())
        completed.set()
        return data

    with execution_context(ExecutionSettings(budget, 1)):
        with pytest.raises(ValueError, match="Invalid layer input"):
            map_independent(task, [0, 1] if budget > 1 else [1, 0], work_units=8192)
        assert completed.is_set()
        assert execution_provenance()["parallel_assembly"]["batches"] == 0
        assert map_independent(lambda x: x*x, [2, 3], work_units=8192) == [4, 9]


def test_nested_batch_never_multiplies_worker_budget(monkeypatch):
    monkeypatch.setattr(execution, "PARALLEL_MIN_WORK", 0)
    budget = min(2, execution.detect_capabilities()["cpu_count"])
    before = deepcopy(execution_provenance())
    with execution_context(ExecutionSettings(budget, 1)):
        outputs = map_independent(lambda value: map_independent(lambda x: x + value, [1, 2], work_units=8192),
                                  [3, 4], work_units=8192)
        assert outputs == [[4, 5], [5, 6]]
        assert execution_provenance()["parallel_assembly"]["batches"] == int(budget > 1)
    assert execution_provenance()["parallel_assembly"] == before["parallel_assembly"]
