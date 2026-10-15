# SPDX-License-Identifier: MPL-2.0
"""Real GDS → conforming mesh → transient physics for teaching projects."""
from copy import deepcopy

import numpy as np
import pytest

from etlayout.examples import make_example
from etlayout.simulation.runner import run_project


def test_pulsed_thermal_example_follows_uniform_rc_recurrence(tmp_path):
    project = make_example("E17", tmp_path)
    original = deepcopy(project.to_dict())
    result = run_project(project)
    assert result.converged and result.transient.completed
    layer = project.layers[0]
    material = project.materials[layer.material_id]
    capacity = material.density * material.specific_heat * layer.thickness
    h = project.thermal_boundaries[0].value
    temperature = 300.
    for row in result.transient.rows[1:]:
        dt = row["time_step"]
        heat = row["drives"]["heat_sources.heater.value"]
        temperature = (capacity * temperature + dt * (heat + h * 300)) / (capacity + dt * h)
        assert row["temperature_max"] == pytest.approx(temperature, abs=1e-7)
    assert 300 < result.metrics["transient_peak_temperature"] < 310
    assert result.metrics["temperature_max"] < result.metrics["transient_peak_temperature"]
    assert result.metrics["transient_energy_balance_error"] < 1e-12
    assert project.to_dict() == original


def test_pulsed_conductor_example_has_quasistatic_feedback_and_cooling_tail(tmp_path):
    project = make_example("E18", tmp_path)
    result = run_project(project)
    assert result.converged and result.transient.completed
    for row in result.transient.rows:
        prescribed = row["drives"]["terminals.drain.value"]
        assert row["terminals"]["drain"]["current"] == pytest.approx(prescribed, abs=1e-10)
        assert row["thermal_power"] == pytest.approx(row["electrical_power"], abs=1e-12)
    assert result.metrics["electrical_power"] == 0
    assert 300 < result.metrics["temperature_max"] < result.metrics["transient_peak_temperature"]
    assert result.metrics["transient_peak_temperature"] < 400
    assert np.max([row["current_density_max"] for row in result.transient.rows]) > 1e9
    assert result.metrics["transient_energy_balance_error"] < 1e-9


def test_multilayer_pulse_has_delayed_unpowered_receiver_and_conserves_energy(tmp_path):
    project = make_example("E19", tmp_path)
    result = run_project(project)
    assert result.converged and result.transient.completed
    assert result.metrics["transient_input_energy"] == pytest.approx(1e-6, rel=1e-10)
    assert result.metrics["transient_energy_balance_error"] < 1e-12
    peaks = {}
    for layer in ("heater", "substrate", "receiver"):
        values = [np.nanmax(frame.fields[layer]["temperature"]) for frame in result.transient.snapshots]
        index = int(np.argmax(values))
        peaks[layer] = values[index], result.transient.times[index]
    assert peaks["heater"][0] > peaks["substrate"][0] > peaks["receiver"][0] > 301.
    assert peaks["heater"][1] == pytest.approx(150e-6)
    assert peaks["receiver"][1] > peaks["substrate"][1] > peaks["heater"][1]
    # Removing the only receiver heat path leaves that unpowered sheet at 300 K.
    project.interfaces[1].enabled = False
    isolated = run_project(project)
    assert isolated.converged
    for frame in isolated.transient.snapshots:
        values = frame.fields["receiver"]["temperature"]
        assert values[np.isfinite(values)] == pytest.approx(300., abs=1e-7)
