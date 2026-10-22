# SPDX-License-Identifier: MPL-2.0
"""Actual coupled calculations retain one state, never a cross-run solve cache."""
from importlib import import_module

import numpy as np
import pytest
from solver_helpers import all_region, rectangle

from etlayout.models import (
    Cancelled,
    Diagnostic,
    ElectricalModel,
    Interface,
    PropertyModel,
    SimulationError,
    Terminal,
    ThermalBoundary,
)
from etlayout.solvers import solve

coupled = import_module("etlayout.solvers.solve")
pytestmark = pytest.mark.numerical


def specimen(kind="metal"):
    project, mesh = rectangle(layers=("a", "b") if kind == "load" else ("metal",))
    project.solver.mode = "electrothermal"
    project.materials["copper"].resistivity = PropertyModel(kind="linear", value=2e-8, alpha=.003)
    if kind == "load":
        project.terminals = [Terminal("top", "a", value=1.), Terminal("bottom", "b", kind="ground")]
        project.interfaces = [Interface("contact", "a", "b", electrical_kind="load", electrical_value=2e-10,
                                        temperature_coefficient=.003)]
        for prefix, key in (("terminal", "top"), ("terminal", "bottom"), ("interface", "contact")):
            all_region(mesh, prefix, key)
    elif kind == "hemt":
        project.layers[0].electrical = ElectricalModel(kind="hemt", mobility=.1, gate_voltage=0.,
            threshold_voltage=-3., gate_coupling=1e16, smoothing_voltage=.1, maximum_sheet_density=2e17)
        project.terminals[1].value = .5
    project.thermal_boundaries = [ThermalBoundary("sink_" + layer.id, layer.id, value=1e9)
                                  for layer in project.layers]
    for boundary in project.thermal_boundaries:
        all_region(mesh, "thermal", boundary.id)
    return project, mesh


@pytest.mark.parametrize("kind", ["metal", "hemt", "load"])
def test_next_thermal_source_reuses_exact_converged_state_and_final_fields(kind, monkeypatch):
    project, mesh = specimen(kind)
    electrical = coupled.electrical_state
    thermal = coupled.thermal_state
    states, target_sources, evaluated_sources = [], [], []

    def counted(*args, **kwargs):
        state = electrical(*args, **kwargs)
        states.append(state)
        return state

    def checked_thermal(*args, **kwargs):
        heat = args[2]
        destination = evaluated_sources if kwargs.get("evaluate_only") else target_sources
        destination.append(next(index for index, state in enumerate(states) if heat is state.heat))
        return thermal(*args, **kwargs)

    monkeypatch.setattr(coupled, "electrical_state", counted)
    monkeypatch.setattr(coupled, "thermal_state", checked_thermal)
    result = solve(project, mesh)
    assert result.converged
    iterations = len(result.iterations)
    assert iterations > 2
    assert len(states) == iterations + 1
    assert target_sources == list(range(iterations))
    assert evaluated_sources == list(range(1, iterations + 1))

    temperatures = {key: fields["temperature"] for key, fields in result.element_fields.items()}
    # Reconstruct the final solve with exactly its original nonlinear seed.
    independently_resolved = electrical(project, mesh, temperatures, initial=states[-2].values)
    assert independently_resolved.result.converged
    for key, fields in independently_resolved.result.element_fields.items():
        for quantity, values in fields.items():
            assert np.allclose(result.element_fields[key][quantity], values, rtol=1e-11, atol=1e-12, equal_nan=True)
    assert result.terminals == independently_resolved.result.terminals
    assert result.metrics["current_balance_error"] <= project.solver.current_balance_tolerance
    assert result.metrics["power_balance_error"] <= project.solver.power_balance_tolerance
    assert result.metrics["thermal_balance_error"] <= project.solver.thermal_balance_tolerance


@pytest.mark.parametrize("changed", ["drive", "material", "interface", "tolerance", "coordinates", "support"])
def test_mid_run_physical_change_rejects_retained_state(changed):
    project, mesh = specimen("load")

    def progress(_fraction, message):
        if message != "Electrothermal iteration 2":
            return
        if changed == "drive":
            project.terminals[0].value *= 2
        elif changed == "material":
            project.materials["copper"].resistivity.value *= 2
        elif changed == "interface":
            project.interfaces[0].electrical_value *= 2
        elif changed == "tolerance":
            project.solver.relative_tolerance *= .1
        elif changed == "coordinates":
            mesh.points[0, 0] += 1e-9
        else:
            mesh.regions["interface:contact"] = mesh.regions["interface:contact"][:-1]

    with pytest.raises(SimulationError, match="changed during.*restart"):
        solve(project, mesh, progress=progress)


def test_display_annotations_do_not_invalidate_the_owned_state():
    project, mesh = specimen()

    def progress(_fraction, message):
        if message == "Electrothermal iteration 2":
            project.name = "Renamed during calculation"
            project.notes = "Display annotation"
            project.layers[0].visible = False
            project.layers[0].color = "#f00"
            project.thresholds = {"temperature_max": 1000.}

    result = solve(project, mesh, progress=progress)
    assert result.converged
    assert result.configuration["name"] == project.name
    assert not any("changed during" in item.message for item in result.diagnostics)


def test_cancellation_checkpoint_remains_active_when_state_is_reused(monkeypatch):
    project, mesh = specimen()
    electrical = coupled.electrical_state
    calls, messages = [], []
    cancel_requested = False

    def counted(*args, **kwargs):
        calls.append(True)
        return electrical(*args, **kwargs)

    def progress(_fraction, message):
        nonlocal cancel_requested
        messages.append(message)
        if message == "Electrothermal iteration 2":
            cancel_requested = True

    monkeypatch.setattr(coupled, "electrical_state", counted)
    with pytest.raises(Cancelled):
        solve(project, mesh, progress=progress, cancel=lambda: cancel_requested)
    assert len(calls) == 2
    assert messages == ["Electrothermal iteration 1", "Electrothermal iteration 2"]


def test_unconverged_electrical_state_is_not_reused_as_a_success(monkeypatch):
    project, mesh = specimen()
    electrical = coupled.electrical_state
    states = []

    def failed_first_updated_state(*args, **kwargs):
        state = electrical(*args, **kwargs)
        states.append(state)
        if len(states) == 2:
            state.result.converged = False
            state.result.diagnostics.append(Diagnostic("error", "Injected failed electrical acceptance"))
        return state

    monkeypatch.setattr(coupled, "electrical_state", failed_first_updated_state)
    result = solve(project, mesh)
    assert result.converged
    assert len(states) == len(result.iterations) + 2
    assert np.array_equal(states[1].values, states[2].values)


def test_final_progress_change_cannot_publish_a_mismatched_result_identity():
    project, mesh = specimen()

    def progress(_fraction, message):
        if message == "Electrothermal solution complete":
            project.terminals[1].value *= 2

    with pytest.raises(SimulationError, match="changed during.*restart"):
        solve(project, mesh, progress=progress)



def test_fixed_temperature_projection_retires_state_before_next_thermal_source(monkeypatch):
    project, mesh = specimen()
    project.thermal_boundaries[0].kind = "temperature"
    project.thermal_boundaries[0].value = 350.
    project.solver.initial_temperature = 300.
    electrical = coupled.electrical_state
    thermal = coupled.thermal_state
    states, target_temperatures = [], []

    def counted(*args, **kwargs):
        state = electrical(*args, **kwargs)
        states.append(state)
        return state

    def check_source(*args, **kwargs):
        if not kwargs.get("evaluate_only"):
            temperatures = args[3]
            fresh = electrical(project, mesh, temperatures)
            assert set(args[2]) == set(fresh.heat)
            for layer, density in fresh.heat.items():
                assert np.array_equal(args[2][layer], density)
            target_temperatures.append(fresh.space.values_from_fields(temperatures, 300.).copy())
        return thermal(*args, **kwargs)

    monkeypatch.setattr(coupled, "electrical_state", counted)
    monkeypatch.setattr(coupled, "thermal_state", check_source)
    result = solve(project, mesh)
    assert result.converged and len(result.iterations) == 2
    assert len(states) == 4  # Projection changes 330 K to the exact 350 K boundary.
    assert np.array_equal(target_temperatures[0], np.full(states[0].space.size, 300.))
    assert np.array_equal(target_temperatures[1], np.full(states[0].space.size, 350.))
    final = electrical(project, mesh, {
        layer: fields["temperature"] for layer, fields in result.element_fields.items()})
    assert final.result.converged
    assert result.metrics["effective_resistance"] == pytest.approx(.2 * (1. + .003 * 50.), rel=1e-12)
    assert result.metrics["electrical_power"] == pytest.approx(1. / .23, rel=1e-12)
    for layer, fields in final.result.element_fields.items():
        for name, values in fields.items():
            assert np.array_equal(result.element_fields[layer][name], values, equal_nan=True)
    assert result.metrics["current_balance_error"] <= project.solver.current_balance_tolerance
    assert result.metrics["power_balance_error"] <= project.solver.power_balance_tolerance
    assert result.metrics["thermal_balance_error"] <= project.solver.thermal_balance_tolerance
