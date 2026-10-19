# SPDX-License-Identifier: MPL-2.0
"""Physical state continuity, exact peaks, periodic stopping and time studies."""
from copy import deepcopy

import numpy as np
import pytest
from test_transient import thermal_fixture

from etlayout.io.project import load_project, project_from_dict, save_project
from etlayout.models import SimulationError, TransientDrive, Waveform
from etlayout.simulation.execution import ExecutionSettings
from etlayout.solvers import solve
from etlayout.solvers.transient import estimate_transient_resources, time_schedule


def test_exact_peak_between_output_times_is_retained_and_budget_aware():
    project, mesh = thermal_fixture(dt=.05, stop=.6)
    project.transient.output_interval = .2
    project.transient.drives = [TransientDrive("heat_sources.heater.value",
        Waveform(kind="pulse", high=10., low=0., width=.15, period=0.))]
    result = solve(project, mesh)
    assert result.metrics["transient_peak_time"] == pytest.approx(.15)
    index = result.metrics["transient_peak_snapshot_index"]
    assert result.transient.times[index] == pytest.approx(.15)
    assert result.transient.snapshots[index].metrics["temperature_max"] == result.metrics["transient_peak_temperature"]
    assert result.metrics["transient_peak_retained"]
    project.transient.max_snapshots = 4
    limited = solve(project, mesh)
    assert not limited.metrics["transient_peak_retained"]
    assert len(limited.transient.snapshots) == 4
    assert any("exact field" in d.message for d in limited.diagnostics)
    assert limited.metrics["transient_peak_temperature"] == result.metrics["transient_peak_temperature"]


def test_steady_bias_initialization_preserves_nonzero_initial_energy_baseline():
    project, mesh = thermal_fixture(dt=.1, stop=.3)
    project.transient.initialization = "steady"
    project.transient.drives = [TransientDrive("heat_sources.heater.value",
        Waveform(kind="pulse", low=10., high=0., delay=.1, width=1., period=0.))]
    result = solve(project, mesh)
    initial = result.transient.snapshots[0]
    assert initial.metrics["temperature_max"] == pytest.approx(310.)
    assert initial.metrics["thermal_stored_energy"] == pytest.approx(0.)
    assert result.metrics["temperature_max"] == pytest.approx(300 + 10 / 1.1 ** 2)
    assert result.metrics["transient_input_energy"] == pytest.approx(mesh.areas.sum())
    assert result.metrics["transient_output_energy"] > 0
    assert result.metrics["transient_energy_balance_error"] < 1e-15
    assert result.metrics["transient_initialization"]["kind"] == "steady"


def test_saved_steady_warm_start_and_resume_match_uninterrupted_run(tmp_path):
    project, mesh = thermal_fixture(dt=.1, stop=.6)
    project.transient.initialization = "steady"
    project.transient.drives = [TransientDrive("heat_sources.heater.value",
        Waveform(kind="pulse", low=10., high=0., delay=.1, width=1., period=0.))]
    whole = solve(project, mesh)
    short = deepcopy(project)
    short.transient.stop_time = .3
    first = solve(short, mesh)
    path = tmp_path / "checkpoint.etl"
    save_project(short, path, first)
    _, saved = load_project(path)
    resumed = solve(project, mesh, initial_result=saved, resume=True)
    assert resumed.transient.times[0] == .3
    assert resumed.transient.times[-1] == .6
    assert resumed.fields["metal"]["temperature"] == pytest.approx(whole.fields["metal"]["temperature"], abs=1e-7)
    for name in ("transient_input_energy", "transient_output_energy", "transient_stored_energy"):
        assert resumed.metrics[name] == pytest.approx(whole.metrics[name], abs=1e-15)
    assert resumed.metrics["transient_energy_balance_error"] < 1e-15
    resumed_project = project_from_dict(resumed.configuration)
    save_project(resumed_project, tmp_path / "resumed.etl", resumed)
    _, reloaded = load_project(tmp_path / "resumed.etl")
    assert reloaded.metrics == resumed.metrics
    assert reloaded.transient.times == pytest.approx(resumed.transient.times)
    replay = solve(resumed_project, mesh, initial_result=reloaded.transient.snapshots[0])
    assert replay.configuration_hash == resumed.configuration_hash
    assert replay.fields["metal"]["temperature"] == pytest.approx(resumed.fields["metal"]["temperature"])
    assert replay.metrics["transient_input_energy"] == pytest.approx(resumed.metrics["transient_input_energy"])
    with pytest.raises(SimulationError, match="requires its saved initial"):
        solve(resumed_project, mesh)
    bad = deepcopy(resumed)
    bad.transient.snapshots[0].fields["metal"]["temperature"] += 1.
    with pytest.raises(SimulationError, match="digest"):
        save_project(resumed_project, tmp_path / "wrong-state.etl", bad)


def test_resume_from_incomplete_accepted_history_and_selected_snapshot(monkeypatch):
    import etlayout.solvers.transient as transient
    project, mesh = thermal_fixture(dt=.1, stop=.5)
    original = transient._step
    count = 0
    def fail_after_two(*args, **kwargs):
        nonlocal count
        count += 1
        result, values = original(*args, **kwargs)
        if count == 3:
            result.converged = False
        return result, values
    monkeypatch.setattr(transient, "_step", fail_after_two)
    partial = solve(project, mesh)
    assert not partial.converged and partial.metrics["time_s"] == pytest.approx(.2)
    monkeypatch.setattr(transient, "_step", original)
    resumed = solve(project, mesh, initial_result=partial, resume=True)
    selected = solve(project, mesh, initial_result=partial.transient.snapshots[-1], resume=True)
    whole = solve(project, mesh)
    assert resumed.converged and selected.converged
    assert resumed.metrics["temperature_max"] == pytest.approx(whole.metrics["temperature_max"])
    assert selected.metrics["transient_input_energy"] == pytest.approx(whole.metrics["transient_input_energy"])
    bad = deepcopy(partial)
    bad.transient.snapshots[-1].converged = False
    with pytest.raises(SimulationError, match="converged accepted snapshot"):
        solve(project, mesh, initial_result=bad, resume=True)


def test_saved_warm_start_resets_time_and_energy_and_rejects_incompatible_mesh():
    project, mesh = thermal_fixture(dt=.1, stop=.2)
    source = solve(project, mesh)
    warm = solve(project, mesh, initial_result=source)
    assert warm.transient.times[0] == 0
    assert warm.transient.snapshots[0].metrics["temperature_max"] == source.metrics["temperature_max"]
    assert warm.transient.snapshots[0].metrics["thermal_stored_energy"] == pytest.approx(0.)
    assert warm.metrics["transient_energy_balance_error"] < 1e-15
    assert warm.configuration["transient"]["initialization"] == "saved"
    assert len(warm.configuration["transient"]["initial_state_digest"]) == 64
    replay_project = project_from_dict(warm.configuration)
    replay = solve(replay_project, mesh, initial_result=warm.transient.snapshots[0])
    assert replay.configuration_hash == warm.configuration_hash
    wrong = deepcopy(source)
    wrong.fields["metal"]["temperature"] += 1.
    with pytest.raises(SimulationError, match="digest"):
        solve(replay_project, mesh, initial_result=wrong)
    bad_mesh = deepcopy(mesh)
    bad_mesh.points[0, 0] -= 1e-12
    with pytest.raises(SimulationError, match="identical mesh"):
        solve(project, bad_mesh, initial_result=source)


def test_steady_initialization_uses_waveform_initial_values_and_allows_presentation_changes():
    project, mesh = thermal_fixture(dt=.1, stop=.2)
    project.heat_sources[0].value = 1.
    project.transient.initialization = "steady"
    project.transient.drives = [TransientDrive("heat_sources.heater.value", Waveform(value=20.))]
    source = solve(project, mesh)
    assert source.transient.snapshots[0].metrics["temperature_max"] == pytest.approx(320.)
    project.layers[0].name = "Renamed layer"
    project.layers[0].color = "#ff0000"
    project.materials["copper"].notes = "Annotation"
    warm = solve(project, mesh, initial_result=source)
    assert warm.converged
    project.materials["copper"].specific_heat *= 2
    with pytest.raises(SimulationError, match="incompatible materials"):
        solve(project, mesh, initial_result=source)


def test_saved_boundary_state_supersedes_uniform_initial_temperature_requirement():
    from etlayout.models import ThermalBoundary
    project, mesh = thermal_fixture(dt=.1, stop=.2)
    project.thermal_boundaries = [ThermalBoundary("left", "metal", kind="temperature", value=310.)]
    mesh.boundary_nodes["left"] = mesh.terminal_nodes["left"]
    project.transient.initialization = "steady"
    source = solve(project, mesh)
    project.transient.initialization = "uniform"
    warm = solve(project, mesh, initial_result=source)
    assert warm.converged
    assert warm.fields["metal"]["temperature"][mesh.boundary_nodes["left"]] == pytest.approx(310.)


def test_periodic_stopping_compares_spatial_cycle_state_and_roundtrips(tmp_path):
    project, mesh = thermal_fixture(dt=.1, stop=20.)
    project.transient.output_interval = .4
    project.transient.drives = [TransientDrive("heat_sources.heater.value",
        Waveform(kind="pulse", low=0., high=10., width=.5, period=1.))]
    project.transient.periodic_tolerance = .002
    result = solve(project, mesh)
    assert result.converged
    assert result.metrics["transient_periodic_converged"]
    assert result.metrics["transient_time"] < project.transient.stop_time
    assert result.metrics["transient_periodic_temperature_change"] <= .002
    assert result.metrics["transient_periodic_cycles"] >= 3
    save_project(project, tmp_path / "periodic.etl", result)
    _, saved = load_project(tmp_path / "periodic.etl")
    assert saved.metrics == result.metrics
    bad = deepcopy(result)
    bad.metrics["transient_periodic_temperature_change"] = 1.
    with pytest.raises(SimulationError, match="periodic convergence"):
        save_project(project, tmp_path / "bad.etl", bad)


def test_periodic_convergence_requires_common_repeating_drive_period():
    project, _ = thermal_fixture()
    project.transient.periodic_tolerance = .01
    with pytest.raises(SimulationError, match="repeating pulse"):
        time_schedule(project)
    project.transient.drives = [TransientDrive("heat_sources.heater.value",
        Waveform(kind="piecewise_linear", times=[0., 1.], values=[0., 10.]))]
    with pytest.raises(SimulationError, match="repeating pulse"):
        time_schedule(project)


def test_periodic_budget_end_warns_without_claiming_a_periodic_state():
    project, mesh = thermal_fixture(dt=.1, stop=.8)
    project.transient.drives = [TransientDrive("heat_sources.heater.value",
        Waveform(kind="pulse", low=0., high=10., width=.5, period=1.))]
    project.transient.periodic_tolerance = .001
    result = solve(project, mesh)
    assert result.converged and result.transient.completed
    assert not result.metrics["transient_periodic_converged"]
    assert result.metrics["transient_stop_reason"] == "stop_time"
    assert any(d.severity == "warning" and "before periodic convergence" in d.message for d in result.diagnostics)


def test_preflight_grid_and_storage_estimates_are_explicit():
    project, mesh = thermal_fixture(dt=.1, stop=.5)
    project.transient.output_interval = .25
    before_mesh = estimate_transient_resources(project)
    assert before_mesh["estimated_history_bytes"] is None
    estimate = estimate_transient_resources(project, mesh)
    assert estimate["steps"] == 6  # output at .25 splits the .2 -> .3 interval
    assert estimate["output_snapshots"] == 3
    assert estimate["peak_reserve_snapshots"] == 1
    assert estimate["fits_history_budget"]
    assert estimate["estimated_history_bytes"] > 0


def test_time_step_study_fixes_output_cadence_and_duration(tmp_path):
    from etlayout.examples import make_example
    from etlayout.simulation import sweep
    project = make_example("E17", tmp_path)
    project.transient.stop_time = 300e-6
    project.transient.output_interval = 100e-6
    original = deepcopy(project.to_dict())
    rows = sweep.time_step_convergence_study(project, [50e-6, 25e-6, 12.5e-6],
                                             execution=ExecutionSettings(study_workers=1))
    assert all(not row.error for row in rows)
    assert [row.result.configuration["transient"]["output_interval"] for row in rows] == [100e-6] * 3
    assert [row.result.configuration["transient"]["stop_time"] for row in rows] == [300e-6] * 3
    assert rows[-1].metrics["transient_peak_temperature_absolute_change"] > 0
    assert rows[-1].metrics["transient_energy_balance_error"] < 1e-15
    assert len({row.metrics["time_study_mesh_sha256"] for row in rows}) == 1
    assert all(row.metrics["time_study_fixed_mesh"] for row in rows)
    assert project.to_dict() == original
    with pytest.raises(SimulationError, match="strictly decreasing"):
        sweep.time_step_convergence_study(project, [25e-6, 50e-6])


def test_refinement_profile_uses_selected_time_region_and_discloses_nearest_frame(monkeypatch):
    from etlayout.simulation import sweep
    project, mesh = thermal_fixture(dt=.1, stop=.5)
    mesh.regions["heat:heater"] = np.flatnonzero(mesh.centroids[:, 0] < 5e-6)
    monkeypatch.setattr(sweep, "run_project", lambda candidate, **kwargs: solve(candidate, mesh))
    profile = {"layer_id": "metal", "field": "temperature", "start": [1e-6, 1e-6],
               "end": [9e-6, 1e-6], "count": 9, "time_s": .21, "region": "heat:heater"}
    rows = sweep.mesh_refinement_study(project, [project.mesh.max_size], profiles=[profile])
    assert not rows[0].error
    saved = rows[0].metrics["mesh_profiles"][0]
    assert saved["time_s"] == pytest.approx(.2)
    assert saved["requested_time_s"] == .21
    assert saved["region"] == "heat:heater"
    assert np.isnan(saved["value"][-1]) and np.isfinite(saved["value"][0])
    assert any("nearest retained frame" in d.message for d in rows[0].result.diagnostics)
