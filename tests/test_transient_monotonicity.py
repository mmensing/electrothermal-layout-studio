# SPDX-License-Identifier: MPL-2.0
"""Localized transient loads distinguish conservation from nodal bounds."""
from copy import deepcopy

import numpy as np
import pytest
from test_transient import thermal_fixture

from etlayout.io.project import load_project, save_project
from etlayout.models import HeatSource, PlanarMesh, SimulationError, TransientDrive, Waveform
from etlayout.solvers import solve
from etlayout.solvers.fem import Space
from etlayout.solvers.thermal import heat_capacity
from etlayout.solvers.transient import _periodic_cycle_steps, time_schedule


def localized_fixture(scheme="consistent", value=1e8, stop=1e-8):
    project, mesh = thermal_fixture(sink=False, dt=1e-8, stop=stop)
    project.heat_sources[0].value = value
    mesh.regions["heat:heater"] = np.array([0])
    project.transient.capacity_scheme = scheme
    return project, mesh


def test_consistent_local_pulse_preserves_fields_and_energy_but_reports_bound_violation():
    project, mesh = localized_fixture()
    result = solve(project, mesh)
    assert result.converged
    assert result.metrics["temperature_min"] == pytest.approx(299.91433094383825)
    assert result.metrics["transient_energy_balance_error"] < 1e-24
    assert result.metrics["thermal_expected_min"] == 300.
    assert result.metrics["thermal_expected_max"] is None
    assert result.metrics["transient_bound_undershoot"] == pytest.approx(.08566905616)
    assert result.metrics["transient_bound_violation_steps"] == 1
    assert result.metrics["transient_first_bound_violation_time"] == 1e-8
    assert result.transient.rows[-1]["thermal_bound_violation"]
    assert any(d.severity == "warning" and "bound violations" in d.message for d in result.diagnostics)


def test_lumping_conserves_capacity_and_removes_pulse_undershoot_on_nonobtuse_mesh(tmp_path):
    project, mesh = localized_fixture()
    space = Space(project, mesh, electrical=False)
    consistent = heat_capacity(project, space)
    project.transient.capacity_scheme = "lumped"
    lumped = heat_capacity(project, space)
    assert lumped.nnz == space.size
    assert lumped.diagonal() == pytest.approx(np.asarray(consistent.sum(axis=1)).ravel())
    result = solve(project, mesh)
    assert result.converged
    assert result.metrics["temperature_min"] >= 300 - 1e-9
    assert result.metrics["transient_bound_violation_steps"] == 0
    assert result.metrics["thermal_monotonicity_sign_condition"]
    assert result.metrics["transient_input_energy"] == pytest.approx(1.25e-12)
    assert result.metrics["transient_energy_balance_error"] < 1e-24
    path = tmp_path / "lumped.etl"
    save_project(project, path, result)
    restored_project, restored_result = load_project(path)
    assert restored_project.transient.capacity_scheme == "lumped"
    assert restored_result.metrics["transient_capacity_scheme"] == "lumped"


@pytest.mark.parametrize("scheme", ["consistent", "lumped"])
def test_capacity_choices_reproduce_uniform_rc_solution(scheme):
    project, mesh = thermal_fixture(dt=.1, stop=1.)
    project.transient.capacity_scheme = scheme
    result = solve(project, mesh)
    assert result.converged
    assert result.fields["metal"]["temperature"] == pytest.approx(300 + 10 * (1 - 1.1 ** -10), abs=2e-7)
    assert result.metrics["transient_bound_violation_steps"] == 0
    assert result.metrics["transient_energy_balance_error"] < 1e-15


def test_negative_local_source_uses_upper_bound_without_imposing_lower_bound():
    project, mesh = localized_fixture(value=-1e8)
    result = solve(project, mesh)
    assert result.converged
    assert result.metrics["thermal_expected_min"] is None
    assert result.metrics["thermal_expected_max"] == 300.
    assert result.metrics["transient_bound_overshoot"] > .08
    assert result.metrics["transient_bound_undershoot"] == 0.


def test_mixed_element_source_signs_do_not_claim_uniform_physical_bounds():
    project, mesh = thermal_fixture(sink=False, dt=1e-8, stop=1e-8)
    # The negative element is concealed by positive neighbors in the assembled
    # nodal source vector, so bounds must use physical element source signs.
    project.heat_sources.append(HeatSource("cooler", "metal", value=-11.))
    mesh.regions["heat:cooler"] = np.array([0])
    result = solve(project, mesh)
    assert result.metrics["thermal_expected_min"] is None
    assert result.metrics["thermal_expected_max"] is None


def test_lumping_does_not_claim_monotonicity_on_obtuse_mesh():
    project, _ = localized_fixture("lumped")
    mesh = PlanarMesh(np.array([[0., 0.], [2e-6, 0.], [.3e-6, .1e-6]]), np.array([[0, 1, 2]]),
                      {"layer:metal": np.array([0]), "heat:heater": np.array([0])})
    result = solve(project, mesh)
    assert result.converged
    assert result.metrics["thermal_positive_offdiagonal_count"] > 0
    assert not result.metrics["thermal_monotonicity_sign_condition"]
    assert any("lumping alone is not a guarantee" in d.message for d in result.diagnostics)


def test_early_bound_violation_remains_visible_after_later_recovery():
    project, mesh = localized_fixture(stop=2e-6)
    project.transient.output_interval = 2e-6
    project.transient.drives = [TransientDrive("heat_sources.heater.value",
        Waveform(kind="pulse", low=0., high=1e8, width=1e-8))]
    result = solve(project, mesh)
    assert not result.metrics["thermal_bound_violation"]
    assert result.metrics["transient_bound_violation_steps"] > 0
    assert result.metrics["transient_bound_undershoot"] > .08
    assert any("bound violations occurred" in d.message for d in result.diagnostics)


def test_unknown_capacity_scheme_is_rejected():
    project, mesh = localized_fixture("unknown")
    with pytest.raises(SimulationError, match="capacity scheme"):
        solve(project, mesh)


def test_periodic_convergence_is_invariant_to_large_restart_time_origin():
    project, mesh = thermal_fixture(dt=10000., stop=10000.)
    project.heat_sources[0].value = 0.
    project.thermal_boundaries[0].value = 1e6
    source = solve(project, mesh)
    results = []
    for origin in (0., 10000.):
        candidate = deepcopy(project)
        candidate.transient.time_step = 5e-9
        candidate.transient.output_interval = 1e-6
        candidate.transient.stop_time = origin + 10e-6
        candidate.transient.periodic_tolerance = .5
        candidate.transient.drives = [TransientDrive("heat_sources.heater.value",
            Waveform(kind="pulse", delay=origin, low=0., high=1e8, width=.5e-6, period=1e-6))]
        result = solve(candidate, mesh, initial_result=source if origin else None, resume=bool(origin))
        results.append(result)
        assert result.metrics["transient_periodic_converged"]
        assert result.metrics["transient_periodic_cycles"] == 6
        cycle_rows = [row for row in result.transient.rows if "periodic_cycle" in row]
        assert [row["periodic_cycle"] for row in cycle_rows] == list(range(1, 7))
        assert cycle_rows[-1]["periodic_temperature_change"] < .5
        assert cycle_rows[-2]["periodic_temperature_change"] < .5
        assert cycle_rows[-3]["periodic_temperature_change"] > .5
    assert results[1].fields["metal"]["temperature"] == pytest.approx(
        results[0].fields["metal"]["temperature"], abs=2e-4)


def test_periodic_grid_ulp_aliases_map_to_one_boundary_per_cycle():
    project, _ = thermal_fixture(dt=.01, stop=.6)
    project.transient.periodic_tolerance = .01
    project.transient.drives = [TransientDrive("heat_sources.heater.value",
        Waveform(kind="pulse", low=0., high=10., width=.1, period=.2))]
    grid, _ = time_schedule(project)
    boundaries = _periodic_cycle_steps(project, grid)
    assert list(boundaries.values()) == [1, 2, 3]
    assert grid[list(boundaries)] == pytest.approx([.2, .4, .6])
