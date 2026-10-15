# SPDX-License-Identifier: MPL-2.0
import numpy as np
import pytest
from solver_helpers import all_region, rectangle

from etlayout.models import Interface, PlanarMesh, SimulationError, Terminal
from etlayout.solvers import solve_electrical


def test_rectangular_metal_against_exact_resistance_and_field():
    project, mesh = rectangle()
    result = solve_electrical(project, mesh)
    exact_r = 2e-8 * 10e-6 / (2e-6 * .5e-6)
    assert result.converged
    assert result.metrics["effective_resistance"] == pytest.approx(exact_r, rel=2e-12)
    assert result.terminals["right"]["current"] == pytest.approx(1 / exact_r, rel=2e-12)
    assert result.metrics["electrical_power"] == pytest.approx(1 / exact_r, rel=2e-12)
    assert result.fields["metal"]["potential"] == pytest.approx(mesh.points[:, 0] / 10e-6, abs=1e-12)
    assert result.fields["metal"]["electric_field"][:, 0] == pytest.approx(-1e5, rel=2e-12)
    assert result.metrics["current_balance_error"] < 1e-12
    assert result.metrics["power_balance_error"] < 1e-12


def test_current_terminal_solves_unknown_equipotential():
    project, mesh = rectangle()
    project.terminals[1].kind = "current"
    project.terminals[1].value = .03
    result = solve_electrical(project, mesh)
    assert result.converged
    assert result.terminals["right"]["voltage"] == pytest.approx(.03 * .2, rel=1e-12)
    assert result.terminals["right"]["current"] == pytest.approx(.03, rel=1e-12)
    assert np.ptp(result.fields["metal"]["potential"][mesh.terminal_nodes["right"]]) == 0


def test_overlapping_layers_have_independent_potentials():
    project, mesh = rectangle(layers=("a", "b"))
    mesh.terminal_nodes.update(left_b=mesh.terminal_nodes["left"], right_b=mesh.terminal_nodes["right"])
    project.terminals.extend([Terminal("left_b", "b", kind="voltage", value=2.),
                              Terminal("right_b", "b", kind="voltage", value=4.)])
    result = solve_electrical(project, mesh)
    assert result.converged
    assert result.fields["b"]["potential"] == pytest.approx(2 + 2 * result.fields["a"]["potential"], abs=1e-12)
    assert result.terminals["right_b"]["current"] == pytest.approx(10., rel=1e-12)


@pytest.mark.parametrize("kind,value,expected_r", [("specific", 2e-10, 10.), ("lumped", 7., 7.),
                                                     ("conductance", 5e9, 10.)])
def test_contact_models_exact_area_scaling_power_and_current(kind, value, expected_r):
    project, mesh = rectangle(layers=("a", "b"))
    project.terminals = [Terminal("top", "a", value=1.), Terminal("bottom", "b", kind="ground")]
    for prefix, key in (("interface", "contact"), ("terminal", "top"), ("terminal", "bottom")):
        all_region(mesh, prefix, key)
    project.interfaces = [Interface("contact", "a", "b", electrical_kind=kind, electrical_value=value)]
    result = solve_electrical(project, mesh)
    assert result.converged
    assert result.metrics["effective_resistance"] == pytest.approx(expected_r, rel=1e-11)
    assert result.metrics["contact_power"] == pytest.approx(1 / expected_r, rel=1e-11)
    assert result.metrics["joule_power"] < 1e-20


def test_via_uses_resistivity_height_area():
    project, mesh = rectangle(layers=("a", "b"))
    project.terminals = [Terminal("top", "a", value=1.), Terminal("bottom", "b", kind="ground")]
    for prefix, key in (("interface", "via"), ("terminal", "top"), ("terminal", "bottom")):
        all_region(mesh, prefix, key)
    project.interfaces = [Interface("via", "a", "b", electrical_kind="via", material_id="copper", height=3e-6)]
    result = solve_electrical(project, mesh)
    assert result.converged
    assert result.metrics["effective_resistance"] == pytest.approx(2e-8 * 3e-6 / 20e-12, rel=1e-12)


def test_ideal_interface_parallel_layers_and_terminal_reaction():
    project, mesh = rectangle(layers=("a", "b"))
    project.interfaces = [Interface("ideal", "a", "b", electrical_kind="ideal")]
    all_region(mesh, "interface", "ideal")
    result = solve_electrical(project, mesh)
    assert result.converged
    assert result.metrics["effective_resistance"] == pytest.approx(.1, rel=1e-12)
    assert result.fields["a"]["potential"] == pytest.approx(result.fields["b"]["potential"])


def test_floating_electrode_is_equipotential_with_zero_net_current():
    project, mesh = rectangle(nx=16)
    selected = np.flatnonzero((mesh.centroids[:, 0] > 4e-6) & (mesh.centroids[:, 0] < 6e-6))
    mesh.regions["terminal:float"] = selected
    project.terminals.append(Terminal("float", "metal", kind="floating"))
    result = solve_electrical(project, mesh)
    nodes = np.unique(mesh.triangles[selected])
    assert result.converged
    assert np.ptp(result.fields["metal"]["potential"][nodes]) == 0
    assert abs(result.terminals["float"]["current"]) < 1e-12


def test_density_integrates_requested_current_and_electrical_work():
    project, mesh = rectangle()
    project.terminals[1].kind = "density"
    project.terminals[1].value = 3e9
    # Edge J[A/m²] is integrated over width times thickness.
    result = solve_electrical(project, mesh)
    assert result.converged
    assert result.terminals["right"]["current"] == pytest.approx(3e-3, rel=1e-12)
    assert result.metrics["power_balance_error"] < 1e-12


def test_area_density_integrates_over_selected_area():
    project, mesh = rectangle()
    selected = np.flatnonzero(mesh.centroids[:, 0] > 8e-6)
    mesh.regions["terminal:right"] = selected
    project.terminals[1].kind = "density"
    project.terminals[1].value = 1e9
    result = solve_electrical(project, mesh)
    assert result.converged
    assert result.terminals["right"]["current"] == pytest.approx(mesh.areas[selected].sum() * 1e9)
    assert result.metrics["power_balance_error"] < 1e-12


def test_missing_reference_and_unbalanced_sources_are_diagnosed():
    project, mesh = rectangle()
    project.terminals = []
    with pytest.raises(SimulationError, match="reference"):
        solve_electrical(project, mesh)
    project.solver.floating_policy = "reference"
    result = solve_electrical(project, mesh)
    assert result.converged
    assert any("gauge" in item.message for item in result.diagnostics)
    project.terminals = [Terminal("right", "metal", kind="current", value=1.)]
    with pytest.raises(SimulationError, match="unbalanced injection"):
        solve_electrical(project, mesh)


def test_point_only_touch_does_not_conduct():
    project, _ = rectangle()
    mesh = PlanarMesh(np.array([[0., 0.], [1., 0.], [0., 1.], [-1., 0.], [0., -1.]]),
                      np.array([[0, 1, 2], [0, 3, 4]]), {"layer:metal": np.array([0, 1])},
                      terminal_nodes={"left": np.array([1]), "right": np.array([3])})
    result = solve_electrical(project, mesh)
    assert result.converged
    assert result.metrics["electrical_dofs"] == 6
    assert result.metrics["electrical_power"] == pytest.approx(0., abs=1e-20)


def test_cg_matches_direct_and_anisotropy():
    project, mesh = rectangle()
    project.materials["copper"].electrical_anisotropy = [2., .25]
    project.solver.linear_solver = "cg"
    result = solve_electrical(project, mesh)
    assert result.converged
    assert result.metrics["effective_resistance"] == pytest.approx(.1, rel=1e-8)


def test_conflicting_fixed_voltages_rejected_before_solve():
    project, mesh = rectangle()
    mesh.terminal_nodes["right"] = mesh.terminal_nodes["left"]
    with pytest.raises(SimulationError, match="overlap"):
        solve_electrical(project, mesh)


def test_unbiased_nonzero_common_potential_has_exactly_zero_current():
    project, mesh = rectangle()
    project.terminals = project.terminals[1:]
    result = solve_electrical(project, mesh)
    assert result.converged
    assert result.metrics["electrical_power"] == 0.
    assert result.metrics["current_density_max"] == 0.
    assert result.terminals["right"]["current"] == 0.


def test_arbitrary_branch_cuts_and_weighted_current_metrics():
    from etlayout.solvers import branch_current

    project, mesh = rectangle()
    result = solve_electrical(project, mesh)
    assert branch_current(result, "metal", [5e-6, 0], [5e-6, 2e-6]) == pytest.approx(5., rel=1e-12)
    assert branch_current(result, "metal", [3e-6, 0], [7e-6, 2e-6]) == pytest.approx(5., rel=1e-12)
    assert branch_current(result, "metal", [5e-6, 2e-6], [5e-6, 0]) == pytest.approx(-5., rel=1e-12)
    assert result.metrics["peak_to_average"] == pytest.approx(1., rel=1e-12)
    assert result.metrics["current_density_p95"] == pytest.approx(5e12, rel=1e-12)
    assert result.metrics["current_density_average"] == pytest.approx(5e12, rel=1e-12)
