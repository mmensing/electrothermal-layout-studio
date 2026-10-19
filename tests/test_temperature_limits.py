# SPDX-License-Identifier: MPL-2.0
"""Hard thermal acceptance uses physical states, exact P1 support and real solves."""
import json
import zipfile
from copy import deepcopy
from dataclasses import asdict

import numpy as np
import pytest
from solver_helpers import all_region, rectangle
from test_transient import thermal_fixture

from etlayout.io.project import (
    configuration_hash,
    load_project,
    physics_hash,
    project_from_dict,
    save_project,
)
from etlayout.models import (
    HeatSource,
    RegionSpec,
    SimulationError,
    TemperatureLimit,
    ThermalBoundary,
    TransientDrive,
    Waveform,
)
from etlayout.simulation.temperature import evaluate_temperature_limits, validate_temperature_limits
from etlayout.solvers import solve, solve_thermal

pytestmark = pytest.mark.numerical


def steady_fixture():
    project, mesh = rectangle(nx=4, ny=2)
    project.solver.mode = "thermal"
    project.thermal_boundaries = [ThermalBoundary("sink", "metal", value=100., ambient=300.)]
    project.heat_sources = [HeatSource("heater", "metal", value=1000.)]
    all_region(mesh, "thermal", "sink")
    all_region(mesh, "heat", "heater")
    return project, mesh


@pytest.mark.parametrize("offset,status", [(1., "passed"), (0., "failed"), (-1., "failed")])
def test_steady_below_equal_above_retains_numerically_converged_fields(offset, status):
    project, mesh = steady_fixture()
    baseline = solve(project, mesh)
    assert baseline.metrics["temperature_max"] == pytest.approx(310., abs=1e-8)
    project.temperature_limits = [TemperatureLimit(maximum_temperature=baseline.metrics["temperature_max"] + offset)]
    result = solve(project, mesh)
    report = result.metrics["thermal_limit_compliance"]
    assert result.converged
    assert report["status"] == status
    assert result.metrics["engineering_accepted"] is (status == "passed")
    assert result.fields["metal"]["temperature"] == pytest.approx(baseline.fields["metal"]["temperature"])
    assert report["limits"][0]["peak"]["temperature_rise"] == pytest.approx(10.)
    assert "mesh/time verification is independent" in report["peak_verification"]


def test_exact_partial_element_support_uses_clipped_p1_vertices_not_centroid_or_smoothing():
    project, mesh = rectangle(nx=1, ny=1)
    project.solver.mode = "thermal"
    project.thermal_boundaries = [ThermalBoundary("left", "metal", kind="temperature", value=300.),
                                  ThermalBoundary("right", "metal", kind="temperature", value=400.)]
    mesh.boundary_nodes = dict(mesh.terminal_nodes)
    project.temperature_limits = [TemperatureLimit(maximum_temperature=326.,
        geometry=RegionSpec(polygons=[{"bounds": [0., 0., 2.5e-6, 2e-6]}]))]
    result = solve_thermal(project, mesh)
    record = result.metrics["thermal_limit_compliance"]["limits"][0]
    assert record["status"] == "passed"
    assert record["peak"]["temperature"] == pytest.approx(325., abs=1e-9)
    assert record["peak"]["location"][0] == pytest.approx(2.5e-6)
    # Deliberately alter the presentation nodal field; authoritative component P1 wins.
    result.fields["metal"]["temperature"][:] = 1e6
    assert evaluate_temperature_limits(project, result)["limits"][0]["peak"]["temperature"] == pytest.approx(325.)


def test_layer_scope_and_unavailable_area_cannot_silently_pass():
    project, mesh = thermal_fixture(sink=False, layers=("top", "bottom"))
    project.temperature_limits = [TemperatureLimit("bottom", maximum_temperature=301., layer_ids=["bottom"])]
    result = solve(project, mesh)
    assert result.converged and result.metrics["engineering_accepted"]
    assert result.metrics["temperature_max"] > 309.
    project.temperature_limits[0].geometry = RegionSpec(polygons=[{"bounds": [1., 1., 2., 2.]}])
    result = solve(project, mesh)
    assert result.converged and not result.metrics["engineering_accepted"]
    assert result.metrics["thermal_limit_compliance"]["status"] == "unavailable"


@pytest.mark.parametrize("evaluation", ["fail_after", "stop_transient"])
def test_accepted_transient_states_detect_first_violation_and_retain_history(evaluation, tmp_path):
    project, mesh = thermal_fixture(sink=False, dt=.1, stop=1.)
    project.transient.output_interval = .5
    project.temperature_limits = [TemperatureLimit(maximum_temperature=302.5, evaluation=evaluation)]
    result = solve(project, mesh)
    report = result.metrics["thermal_limit_compliance"]
    assert result.converged and not result.metrics["engineering_accepted"]
    assert report["first_violation"]["peak"]["time_s"] == pytest.approx(.3)
    assert report["first_violation"]["peak"]["temperature"] == pytest.approx(303.)
    early = evaluation == "stop_transient"
    assert report["stopped_early"] is early
    assert result.transient.completed is not early
    assert result.metrics["time_s"] == pytest.approx(.3 if early else 1.)
    assert result.transient.snapshots[-1].converged
    assert result.metrics["transient_energy_balance_error"] < 1e-15
    assert report["requested_stop_time"] == 1.
    assert report["actual_stop_time"] == pytest.approx(.3 if early else 1.)
    for format_version in (2, 3):
        target = tmp_path / f"failed-{format_version}.etl"
        save_project(project, target, result, archive_format=format_version)
        reopened, restored = load_project(target)
        assert reopened.temperature_limits == project.temperature_limits
        assert restored.metrics["thermal_limit_compliance"] == report
        assert restored.converged and restored.transient.completed is not early
        assert restored.fields["metal"]["temperature"] == pytest.approx(result.fields["metal"]["temperature"])


def test_intermediate_pulse_failure_survives_later_cooling_and_sparse_field_outputs():
    project, mesh = thermal_fixture(dt=.05, stop=2.)
    project.transient.output_interval = 1.
    project.transient.drives = [TransientDrive("heat_sources.heater.value",
        Waveform(kind="pulse", high=100., low=0., width=.1, period=0.))]
    project.temperature_limits = [TemperatureLimit(maximum_temperature=305.)]
    result = solve(project, mesh)
    assert result.converged and result.transient.completed
    report = result.metrics["thermal_limit_compliance"]
    assert report["status"] == "failed"
    assert result.metrics["temperature_max"] < 305.
    assert report["limits"][0]["peak"]["temperature"] == result.metrics["transient_peak_temperature"]
    assert report["first_violation"]["peak"]["time_s"] < .2
    assert not result.metrics["engineering_accepted"]


def test_initial_state_at_limit_stops_without_inventing_timestep():
    project, mesh = thermal_fixture()
    project.temperature_limits = [TemperatureLimit(maximum_temperature=300., evaluation="stop_transient")]
    result = solve(project, mesh)
    assert result.converged and not result.transient.completed
    assert result.metrics["transient_steps"] == 0
    assert result.transient.times.tolist() == [0.]
    assert result.metrics["thermal_limit_compliance"]["first_violation"]["peak"]["time_s"] == 0.


def test_coupled_limit_and_unconverged_result_remain_independent():
    project, mesh = steady_fixture()
    project.solver.mode = "electrothermal"
    project.terminals[1].value = .00001
    project.temperature_limits = [TemperatureLimit(maximum_temperature=300.)]
    result = solve(project, mesh)
    assert result.converged and not result.metrics["engineering_accepted"]
    assert result.metrics["power_balance_error"] < project.solver.power_balance_tolerance
    project.solver.max_iterations = 1
    result = solve(project, mesh)
    assert not result.converged and not result.metrics["engineering_accepted"]
    assert result.metrics["thermal_limit_compliance"]["status"] == "failed"


@pytest.mark.parametrize("change", ["negative", "nan", "missing_layer", "duplicate_layer", "policy", "empty", "line", "duplicate_id"])
def test_invalid_limit_configuration_is_actionable(change):
    project, mesh = steady_fixture()
    limit = TemperatureLimit()
    project.temperature_limits = [limit]
    if change == "negative":
        limit.maximum_temperature = 0.
    elif change == "nan":
        limit.maximum_temperature = np.nan
    elif change == "missing_layer":
        limit.layer_ids = ["absent"]
    elif change == "duplicate_layer":
        limit.layer_ids = ["metal", "metal"]
    elif change == "policy":
        limit.evaluation = "trial_iterate"
    elif change == "empty":
        limit.geometry = RegionSpec()
    elif change == "line":
        limit.geometry = RegionSpec(line=[[0., 0.], [1e-6, 0.]])
    elif change == "duplicate_id":
        project.temperature_limits.append(deepcopy(limit))
    assert validate_temperature_limits(project)
    with pytest.raises(SimulationError):
        solve(project, mesh)


def test_default_legacy_and_disabled_limits_preserve_physics_identity():
    project, mesh = steady_fixture()
    legacy = asdict(project)
    legacy.pop("temperature_limits")
    assert "temperature_limits" not in project.to_dict()
    assert configuration_hash(project) == configuration_hash(legacy)
    original = physics_hash(legacy)
    assert physics_hash(project_from_dict(legacy)) == original
    project.temperature_limits = [TemperatureLimit(enabled=False)]
    assert physics_hash(project) == original
    assert solve(project, mesh).metrics["thermal_limit_compliance"]["status"] == "disabled"
    project.temperature_limits[0].enabled = True
    assert physics_hash(project) != original


def test_legacy_warning_and_measurement_constraints_are_not_promoted_to_hard_limits():
    project, mesh = steady_fixture()
    project.thresholds = {"temperature_max": 301.}
    result = solve(project, mesh)
    assert result.converged and result.metrics["engineering_accepted"]
    assert any("Engineering threshold exceeded" in d.message for d in result.diagnostics)
    assert result.metrics["thermal_limit_compliance"]["status"] == "disabled"


@pytest.mark.parametrize("archive_format", [2, 3])
def test_limit_free_archives_preserve_prelimit_configuration_and_omit_new_field(tmp_path, archive_format):
    project, mesh = steady_fixture()
    legacy = asdict(project)
    legacy.pop("temperature_limits")
    expected_hash = configuration_hash(legacy)
    result = solve(project, mesh)
    assert result.configuration_hash == expected_hash
    target = tmp_path / f"limit-free-{archive_format}.etl"
    save_project(project, target, result, archive_format=archive_format)
    reopened, restored = load_project(target)
    assert reopened.temperature_limits == []
    assert configuration_hash(reopened) == expected_hash
    assert restored.configuration_hash == expected_hash
    assert "temperature_limits" not in restored.configuration
    if archive_format == 2:
        with zipfile.ZipFile(target) as archive:
            assert "temperature_limits" not in json.loads(archive.read("project.json"))["project"]
    # Nonempty disabled records are deliberate user configuration, not lost drafts.
    project.temperature_limits = [TemperatureLimit(enabled=False)]
    assert project.to_dict()["temperature_limits"][0]["enabled"] is False
    assert configuration_hash(project) != expected_hash


@pytest.mark.parametrize("clipped", [False, True])
def test_legacy_point_touch_temperature_cannot_certify_hard_limit(clipped):
    from test_power_local_fields import triangles

    from etlayout.simulation.temperature import finalize_temperature_limits

    project, mesh = triangles()
    project.solver.mode = "thermal"
    region = RegionSpec(polygons=[{"bounds": [.1, .1, .2, .2]}]) if clipped else None
    project.temperature_limits = [TemperatureLimit(maximum_temperature=450., geometry=region)]
    result = solve_thermal(project, mesh)
    assert result.converged and result.metrics["engineering_accepted"]
    assert result.metrics["thermal_limit_compliance"]["status"] == "passed"
    # A real solved pair of independent domains has different limits at their
    # common planar node. Legacy storage retains only its rendering average.
    assert result.fields["metal"]["temperature"][0] == pytest.approx(350.)
    result.element_fields = {}
    result.field_version = 1
    report = evaluate_temperature_limits(project, result)
    assert report["status"] == "unavailable"
    assert report["limits"][0]["peak"] is None
    assert "Recompute" in report["limits"][0]["reason"]
    finalize_temperature_limits(project, result)
    assert result.converged and not result.metrics["engineering_accepted"]
    assert any("recompute" in item.message.lower() for item in result.diagnostics)


def test_unambiguous_connected_legacy_temperature_remains_supported():
    project, mesh = steady_fixture()
    project.temperature_limits = [TemperatureLimit(maximum_temperature=311.)]
    result = solve_thermal(project, mesh)
    before = evaluate_temperature_limits(project, result)
    result.element_fields = {}
    result.field_version = 1
    report = evaluate_temperature_limits(project, result)
    assert report["status"] == "passed"
    assert report["limits"][0]["peak"]["temperature"] == pytest.approx(before["limits"][0]["peak"]["temperature"])
