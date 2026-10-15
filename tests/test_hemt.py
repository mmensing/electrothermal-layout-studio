# SPDX-License-Identifier: MPL-2.0
import numpy as np
import pytest
from scipy.integrate import quad
from solver_helpers import all_region, rectangle

from etlayout.models import ElectricalModel, SimulationError, Terminal
from etlayout.physics import ELEMENTARY_CHARGE, sheet_conductance
from etlayout.solvers import solve_electrical


def channel(nx=40):
    project, mesh = rectangle(nx=nx)
    project.layers[0].electrical = ElectricalModel(kind="hemt", mobility=.1, gate_voltage=0.,
                                                  threshold_voltage=-3., gate_coupling=1e16,
                                                  smoothing_voltage=.1, maximum_sheet_density=2e17)
    project.terminals[1].value = .5
    return project, mesh


def test_semiconductor_density_mobility_and_temperature_power_law():
    project, _ = rectangle()
    layer = project.layers[0]
    layer.electrical = ElectricalModel(kind="semiconductor", carrier_density=1e23, mobility=.12,
                                       mobility_exponent=2.)
    g = sheet_conductance(layer, project.materials["copper"], np.array([300., 600.]))
    expected = ELEMENTARY_CHARGE * 1e23 * .12 * .5e-6
    assert g[:, 0] == pytest.approx([expected, expected / 4])


def test_hemt_derivative_and_temperature_and_local_bias_dependence():
    project, _ = channel()
    layer, material = project.layers[0], project.materials["copper"]
    t = np.array([300., 300., 450.])
    v = np.array([0., 2., 0.])
    g, derivative = sheet_conductance(layer, material, t, v, derivative=True)
    eps = 1e-5
    numerical = (sheet_conductance(layer, material, t, v + eps) - sheet_conductance(layer, material, t, v - eps)) / (2 * eps)
    assert derivative == pytest.approx(numerical, rel=1e-9)
    assert g[1, 0] < g[0, 0]
    assert g[2, 0] < g[0, 0]


def test_hemt_channel_matches_integrated_charge_control_solution():
    project, mesh = channel()
    layer, material = project.layers[0], project.materials["copper"]
    result = solve_electrical(project, mesh)
    exact_current = (2e-6 / 10e-6) * quad(lambda v: float(sheet_conductance(layer, material, np.array([300.]), v)[0, 0]), 0., .5)[0]
    assert result.converged
    assert result.terminals["right"]["current"] == pytest.approx(exact_current, rel=5e-4)
    assert result.metrics["electrical_residual"] < project.solver.relative_tolerance
    assert result.metrics["power_balance_error"] < 1e-6
    g = result.fields["metal"]["sheet_conductance"][:, 0]
    assert g[mesh.centroids[:, 0] > 8e-6].mean() < g[mesh.centroids[:, 0] < 2e-6].mean()


def test_gate_controls_channel_without_clamping_channel_potential():
    project, mesh = channel()
    project.terminals.append(Terminal("gate", "metal", kind="gate", value=-5.))
    all_region(mesh, "terminal", "gate")
    off = solve_electrical(project, mesh)
    project.terminals[-1].value = 1.
    on = solve_electrical(project, mesh)
    assert off.converged and on.converged
    assert on.metrics["total_current"] > 1e4 * off.metrics["total_current"]
    assert on.terminals["gate"]["current"] == 0
    assert on.fields["metal"]["potential"].min() == pytest.approx(0.)
    assert on.fields["metal"]["potential"].max() == pytest.approx(.5)


def test_charge_control_numerically_stable_in_extreme_depletion():
    project, _ = channel()
    layer = project.layers[0]
    g, derivative = sheet_conductance(layer, project.materials["copper"], np.array([300.]),
                                      potential=1e5, derivative=True)
    assert np.isfinite(g).all() and np.isfinite(derivative).all()
    assert g[0, 0] == pytest.approx(layer.electrical.minimum_sheet_conductance)
    assert derivative[0, 0] == 0.


def test_hemt_nonconvergence_is_not_silently_marked_valid():
    project, mesh = channel()
    project.terminals[1].value = 2.
    project.solver.max_iterations = 1
    project.solver.relative_tolerance = 1e-12
    result = solve_electrical(project, mesh)
    assert not result.converged
    assert any(item.severity == "error" for item in result.diagnostics)


def test_spatial_gate_leaves_access_at_configured_density_and_conflicts_fail():
    project, mesh = channel()
    selected = np.flatnonzero((mesh.centroids[:, 0] > 4e-6) & (mesh.centroids[:, 0] < 6e-6))
    mesh.regions["terminal:gate"] = selected
    project.terminals.append(Terminal("gate", "metal", kind="gate", value=0.))
    result = solve_electrical(project, mesh)
    expected_access = 1e-10 + ELEMENTARY_CHARGE * .1 * 2e17
    access = np.setdiff1d(np.arange(len(mesh.triangles)), selected)
    assert result.converged
    assert result.fields["metal"]["sheet_conductance"][access, 0] == pytest.approx(expected_access)
    mesh.regions["terminal:other_gate"] = selected
    project.terminals.append(Terminal("other_gate", "metal", kind="gate", value=1.))
    with pytest.raises(SimulationError, match="conflicting"):
        solve_electrical(project, mesh)
