# SPDX-License-Identifier: MPL-2.0
from copy import deepcopy

import numpy as np
import pytest
from solver_helpers import all_region, rectangle

from etlayout.models import (
    Cancelled,
    HeatSource,
    Interface,
    PropertyModel,
    SimulationError,
    ThermalBoundary,
    TransientDrive,
    TransientSettings,
    Waveform,
)
from etlayout.solvers import solve
from etlayout.solvers.fem import Space
from etlayout.solvers.thermal import heat_capacity
from etlayout.solvers.transient import time_schedule, validate_transient, waveform_value


def thermal_fixture(*, sink=True, dt=.1, stop=1., layers=("metal",)):
    project, mesh = rectangle(nx=4, ny=2, layers=layers)
    project.solver.mode = "thermal"
    material = project.materials["copper"]
    # rho * cp * thickness = 1 J/(m² K), Q=10 W/m², h=1 W/(m² K).
    material.density = 2000.
    material.specific_heat = 1000.
    project.heat_sources = [HeatSource("heater", layers[0], value=10.)]
    all_region(mesh, "heat", "heater")
    if sink:
        project.thermal_boundaries = [ThermalBoundary("sink", layers[0], value=1.)]
        all_region(mesh, "thermal", "sink")
    project.transient = TransientSettings(enabled=True, stop_time=stop, time_step=dt,
                                          output_interval=dt)
    return project, mesh


def test_consistent_capacity_has_physical_units_and_separate_layers():
    project, mesh = thermal_fixture(layers=("top", "bottom"))
    project.layers[1].thickness *= 2
    space = Space(project, mesh, electrical=False)
    capacity = heat_capacity(project, space)
    area = mesh.areas.sum()
    assert capacity.sum() == pytest.approx(3 * area)
    assert np.all(capacity.diagonal() > 0)
    assert np.max(abs((capacity - capacity.T).data), initial=0) == 0


def test_uniform_rc_matches_backward_euler_and_refines_to_analytic_solution():
    errors = []
    for dt in (.2, .1, .05):
        project, mesh = thermal_fixture(dt=dt)
        result = solve(project, mesh)
        assert result.converged and result.transient.completed
        expected = 300 + 10 * (1 - (1 + dt) ** (-round(1 / dt)))
        assert result.fields["metal"]["temperature"] == pytest.approx(expected, abs=2e-7)
        errors.append(abs(result.metrics["temperature_max"] - (300 + 10 * (1 - np.exp(-1)))))
        assert result.metrics["transient_energy_balance_error"] < 1e-15
        assert result.transient.times[-1] == 1.
        assert result.transient.snapshots[0].metrics["state_kind"] == "initial_condition"
        assert all(s.transient is None and s.mesh is mesh for s in result.transient.snapshots)
        assert all(s.elapsed_seconds == result.elapsed_seconds and s.metrics["elapsed_seconds_scope"] == "whole_run"
                   for s in result.transient.snapshots)
    assert errors[1] < .55 * errors[0]
    assert errors[2] < .55 * errors[1]


def test_adiabatic_pulses_align_edges_and_integrate_exact_input_energy():
    project, mesh = thermal_fixture(sink=False, dt=.13, stop=.7)
    project.transient.output_interval = .2
    project.transient.drives = [TransientDrive("heat_sources.heater.value",
        Waveform(kind="pulse", low=0., high=10., delay=.05, width=.1, period=.2))]
    before = deepcopy(project.to_dict())
    result = solve(project, mesh)
    assert result.converged
    # [0.05,0.15], [0.25,0.35], [0.45,0.55], [0.65,0.7].
    assert result.metrics["temperature_max"] == pytest.approx(303.5, abs=1e-6)
    assert result.metrics["transient_input_energy"] == pytest.approx(3.5 * mesh.areas.sum())
    assert result.metrics["transient_output_energy"] == 0.
    assert result.metrics["transient_stored_energy"] == pytest.approx(3.5 * mesh.areas.sum(), rel=1e-7)
    assert result.transient.times == pytest.approx([0., .2, .4, .6, .7])
    assert any(np.isclose(row["time"], .05) for row in result.transient.rows)
    assert project.to_dict() == before


def test_two_layers_exchange_heat_and_preserve_total_energy():
    project, mesh = thermal_fixture(sink=False, dt=.1, layers=("top", "bottom"))
    project.interfaces = [Interface("exchange", "top", "bottom", thermal_kind="conductance", thermal_value=2.)]
    all_region(mesh, "interface", "exchange")
    result = solve(project, mesh)
    assert result.converged
    top = result.fields["top"]["temperature"]
    bottom = result.fields["bottom"]["temperature"]
    assert np.mean(top) > np.mean(bottom) > 300.
    assert np.mean(top + bottom) == pytest.approx(610., abs=5e-7)
    assert result.metrics["transient_energy_balance_error"] < 1e-15


def test_cooling_from_explicit_initial_temperature_has_signed_energy_accounting():
    project, mesh = thermal_fixture(dt=.1, stop=.3)
    project.solver.initial_temperature = 310.
    project.heat_sources = []
    result = solve(project, mesh)
    assert result.converged
    assert result.fields["metal"]["temperature"] == pytest.approx(300 + 10 / 1.1**3, abs=2e-7)
    assert result.metrics["transient_peak_temperature"] == 310.
    assert result.metrics["transient_peak_time"] == 0.
    assert result.metrics["transient_stored_energy"] < 0
    assert result.metrics["transient_input_energy"] == 0
    assert result.metrics["transient_output_energy"] > 0
    assert result.metrics["transient_energy_balance_error"] < 1e-15


def test_fixed_temperature_reactions_include_storage_and_exchange():
    project, mesh = thermal_fixture(dt=.01, stop=.02)
    project.thermal_boundaries = [ThermalBoundary("left", "metal", kind="temperature", value=300.)]
    mesh.boundary_nodes["left"] = mesh.terminal_nodes["left"]
    result = solve(project, mesh)
    assert result.converged
    assert result.fields["metal"]["temperature"][mesh.boundary_nodes["left"]] == pytest.approx(300.)
    assert result.metrics["heat_out"] > 0
    assert result.metrics["transient_energy_balance_error"] < 1e-15


def test_current_pulse_couples_resistivity_at_same_temperature():
    project, mesh = thermal_fixture(dt=.1, stop=.2)
    project.heat_sources = []
    project.solver.mode = "electrothermal"
    project.materials["copper"].resistivity = PropertyModel(kind="linear", value=2e-8, alpha=.003)
    project.terminals[1].kind = "current"
    project.transient.drives = [TransientDrive("terminals.right.value", Waveform(kind="constant", value=1e-5))]
    result = solve(project, mesh)
    assert result.converged
    temperature = result.metrics["temperature_max"]
    assert temperature > 300.
    expected_resistance = .2 * (1 + .003 * (temperature - 300))
    assert result.metrics["effective_resistance"] == pytest.approx(expected_resistance, rel=1e-6)
    assert result.terminals["right"]["current"] == pytest.approx(1e-5, rel=1e-9)
    assert result.metrics["thermal_power"] == pytest.approx(result.metrics["electrical_power"], rel=1e-9)


def test_piecewise_linear_and_pulse_ramps_and_single_pulse():
    wave = Waveform(kind="piecewise_linear", times=[.1, .3, .5], values=[2., 6., -2.])
    assert [waveform_value(wave, t) for t in (0., .2, .4, 1.)] == pytest.approx([2., 4., 2., -2.])
    wave = Waveform(kind="pulse", low=2., high=6., delay=.1, rise_time=.1, width=.2, fall_time=.2, period=0.)
    assert [waveform_value(wave, t) for t in (0., .15, .3, .5, .7, 2.)] == pytest.approx([2., 4., 6., 4., 2., 2.])


@pytest.mark.parametrize("change,message", [
    (lambda p: setattr(p.transient, "time_step", 0.), "time step"),
    (lambda p: setattr(p.transient, "max_steps", 2), "max_steps"),
    (lambda p: setattr(p.transient, "max_snapshots", 2), "max_snapshots"),
    (lambda p: setattr(p.transient, "max_history_bytes", 1), "max_history_bytes"),
    (lambda p: setattr(p.materials["copper"], "density", 0.), "density"),
    (lambda p: setattr(p.solver, "mode", "electrical"), "requires thermal"),
])
def test_invalid_setups_are_rejected_before_integration(change, message):
    project, mesh = thermal_fixture()
    change(project)
    with pytest.raises(SimulationError, match=message):
        solve(project, mesh)


def test_fixed_initial_temperature_and_invalid_drive_guards():
    project, _ = thermal_fixture()
    project.thermal_boundaries[0].kind = "temperature"
    project.thermal_boundaries[0].value = 310.
    with pytest.raises(SimulationError, match="initial temperature must match"):
        validate_transient(project)
    project.thermal_boundaries = []
    project.transient.drives = [TransientDrive("layers.metal.thickness")]
    with pytest.raises(SimulationError, match="Unsupported transient drive"):
        validate_transient(project)
    project.transient.drives = [TransientDrive("heat_sources.heater.value", Waveform(kind="piecewise_linear", times=[0., 0.], values=[1., 2.]))]
    with pytest.raises(SimulationError, match="increasing"):
        validate_transient(project)


def test_nonconverged_step_does_not_enter_accepted_history_and_cancellation():
    project, mesh = thermal_fixture()
    project.solver.mode = "electrothermal"
    project.solver.max_iterations = 1
    project.transient.drives = [TransientDrive("terminals.right.value", Waveform(kind="constant", value=1e-5))]
    result = solve(project, mesh)
    assert not result.converged and not result.transient.completed
    assert result.transient.times.tolist() == [0.]
    assert result.metrics["transient_time"] == 0.
    assert result.metrics["transient_steps"] == 0
    assert any("last accepted state" in d.message for d in result.diagnostics)
    with pytest.raises(Cancelled):
        solve(project, mesh, cancel=lambda: True)


def test_schedule_exact_final_time_output_and_pulse_knots():
    project, _ = thermal_fixture(dt=.3, stop=1.)
    project.transient.output_interval = .4
    project.transient.drives = [TransientDrive("heat_sources.heater.value", Waveform(kind="pulse", delay=.05,
        rise_time=.01, width=.1, fall_time=.02, period=.2))]
    grid, outputs = time_schedule(project)
    assert grid[0] == 0 and grid[-1] == 1.
    assert outputs == pytest.approx([0., .4, .8, 1.])
    assert np.all(np.diff(grid) > 0)
    for value in (.05, .06, .16, .18, .25, .9):
        assert np.min(abs(grid - value)) < 1e-14


def test_nonuniform_pwl_step_loads_are_evaluated_at_endpoints():
    project, mesh = thermal_fixture(dt=.11, stop=.3, sink=False)
    project.transient.drives = [TransientDrive("heat_sources.heater.value",
        Waveform(kind="piecewise_linear", times=[0., .15, .3], values=[0., 10., 0.]))]
    result = solve(project, mesh)
    assert result.converged
    expected = sum(row["time_step"] * row["drives"]["heat_sources.heater.value"]
                   for row in result.transient.rows)
    assert result.metrics["temperature_max"] == pytest.approx(300 + expected, abs=2e-7)
    assert any(row["time"] == .15 for row in result.transient.rows)


def test_cancellation_during_integration_and_progress_are_monotonic():
    project, mesh = thermal_fixture()
    progress = []
    cancelled = False

    def receive(fraction, message):
        nonlocal cancelled
        progress.append(fraction)
        if "step 3/" in message:
            cancelled = True

    with pytest.raises(Cancelled):
        solve(project, mesh, progress=receive, cancel=lambda: cancelled)
    assert progress == sorted(progress)
    assert max(progress) < 1.


def test_density_and_capacity_cannot_cancel_each_others_negative_sign():
    project, mesh = thermal_fixture()
    project.materials["copper"].density = -1.
    project.materials["copper"].specific_heat = -1.
    with pytest.raises(SimulationError, match="positive and finite"):
        solve(project, mesh)


def test_extreme_finite_time_ratios_fail_as_actionable_budget_errors():
    project, _ = thermal_fixture()
    project.transient.time_step = 1e-320
    with pytest.raises(SimulationError, match="exceeds max_steps"):
        time_schedule(project)
    project.transient.time_step = .1
    project.transient.drives = [TransientDrive("heat_sources.heater.value",
        Waveform(kind="pulse", period=1e-320, width=1e-321))]
    with pytest.raises(SimulationError, match="events exceed max_steps"):
        time_schedule(project)


@pytest.mark.parametrize("name,value", [("max_snapshots", 10001), ("max_history_bytes", 1024**3 + 1)])
def test_history_limits_match_portable_archive_capacity(name, value):
    project, _ = thermal_fixture()
    setattr(project.transient, name, value)
    with pytest.raises(SimulationError, match="portable archive limit"):
        validate_transient(project)


@pytest.mark.parametrize("period", [.1, .2, .003, .000013, 1.234567e-9])
@pytest.mark.parametrize("delay_factor", [0., .1, .5, 1.3333])
@pytest.mark.parametrize("width_fraction", [.1, .5, .75, 1.])
def test_fractional_pulse_event_aliases_preserve_integrated_load(period, delay_factor, width_fraction):
    project, _ = thermal_fixture(sink=False)
    delay, width, stop = period * delay_factor, period * width_fraction, period * 6.7
    project.transient = TransientSettings(enabled=True, stop_time=stop, time_step=period * .31,
                                          output_interval=period * .55)
    wave = Waveform(kind="pulse", low=0., high=1., delay=delay, width=width, period=period)
    project.transient.drives = [TransientDrive("heat_sources.heater.value", wave)]
    grid, _ = time_schedule(project)
    energy = sum((b - a) * waveform_value(wave, float(b), left_limit=True, interval_start=float(a))
                 for a, b in zip(grid[:-1], grid[1:]))
    exact = sum(max(0., min(stop, delay + i * period + width) - (delay + i * period)) for i in range(8))
    assert energy == pytest.approx(exact, abs=period * 1e-12)
    for cycle in range(6):
        start = delay + cycle * period
        assert waveform_value(wave, start) == 1.
        if start > 0:
            assert waveform_value(wave, start, left_limit=True) == (1. if cycle and width_fraction == 1. else 0.)
        assert waveform_value(wave, start + width, left_limit=True) == 1.
