# SPDX-License-Identifier: MPL-2.0
import numpy as np
import pytest
from solver_helpers import all_region, rectangle

from etlayout.models import HeatSource, Interface, PropertyModel, SimulationError, ThermalBoundary
from etlayout.solvers import solve_thermal


@pytest.mark.parametrize("kind,value,rise", [("coefficient", 100., 10.),
                                            ("resistance_area", .01, 10.),
                                            ("resistance", 5e8, 10.)])
def test_uniform_heating_boundary_exact(kind, value, rise):
    project, mesh = rectangle()
    project.thermal_boundaries = [ThermalBoundary("sink", "metal", kind=kind, value=value, ambient=300.)]
    project.heat_sources = [HeatSource("heater", "metal", value=1000.)]
    all_region(mesh, "thermal", "sink")
    all_region(mesh, "heat", "heater")
    result = solve_thermal(project, mesh)
    assert result.converged
    assert result.fields["metal"]["temperature"] == pytest.approx(300. + rise, abs=1e-8)
    assert result.metrics["thermal_power"] == pytest.approx(2e-8)
    assert result.metrics["thermal_balance_error"] < 1e-8


def test_fixed_temperature_uniform_strip_analytic_profile():
    project, mesh = rectangle(nx=24)
    mesh.boundary_nodes = {"left": mesh.terminal_nodes["left"], "right": mesh.terminal_nodes["right"]}
    project.thermal_boundaries = [ThermalBoundary("left", "metal", kind="temperature", value=300.),
                                  ThermalBoundary("right", "metal", kind="temperature", value=300.)]
    project.heat_sources = [HeatSource("heater", "metal", value=1e6)]
    all_region(mesh, "heat", "heater")
    result = solve_thermal(project, mesh)
    x = mesh.points[:, 0]
    exact = 300 + 1e6 * x * (10e-6 - x) / (2 * 10. * .5e-6)
    assert result.converged
    assert result.fields["metal"]["temperature"] == pytest.approx(exact, abs=.012)
    assert result.metrics["heat_out"] == pytest.approx(20e-6, rel=1e-9)


def test_two_layer_vertical_resistance_and_contact_exchange():
    project, mesh = rectangle(layers=("top", "bottom"))
    project.thermal_boundaries = [ThermalBoundary("sink", "bottom", kind="temperature", value=300.)]
    project.heat_sources = [HeatSource("heater", "top", value=1e6)]
    project.interfaces = [Interface("junction", "top", "bottom", thermal_kind="resistance", thermal_value=2e-5)]
    for prefix, key in (("interface", "junction"), ("thermal", "sink"), ("heat", "heater")):
        all_region(mesh, prefix, key)
    result = solve_thermal(project, mesh)
    assert result.converged
    assert result.fields["top"]["temperature"] == pytest.approx(320., abs=1e-10)
    assert result.fields["bottom"]["temperature"] == pytest.approx(300., abs=1e-10)
    assert result.metrics["thermal_balance_error"] < 1e-10


def test_stack_uses_anisotropic_vertical_and_gap_resistances():
    project, mesh = rectangle(layers=("top", "bottom"))
    project.materials["copper"].thermal_conductivity_z = 2.
    project.thermal_boundaries = [ThermalBoundary("sink", "bottom", kind="temperature", value=300.)]
    project.heat_sources = [HeatSource("heater", "top", value=1e6)]
    project.interfaces = [Interface("junction", "top", "bottom", thermal_kind="stack", material_id="copper",
                                    height=1e-6, thermal_resistance_area=1e-6)]
    for prefix, key in (("interface", "junction"), ("thermal", "sink"), ("heat", "heater")):
        all_region(mesh, prefix, key)
    result = solve_thermal(project, mesh)
    expected = 300 + 1e6 * (.5e-6 / 2. + 1e-6 / 2. + 1e-6)
    assert result.converged
    assert result.metrics["temperature_max"] == pytest.approx(expected, abs=1e-10)


def test_thermal_without_sink_is_rejected():
    project, mesh = rectangle()
    project.heat_sources = [HeatSource("heater", "metal", value=10.)]
    all_region(mesh, "heat", "heater")
    with pytest.raises(SimulationError, match="unbalanced injection"):
        solve_thermal(project, mesh)


def test_temperature_dependent_conductivity_matches_kirchhoff_solution():
    project, mesh = rectangle(nx=50)
    project.materials["copper"].thermal_conductivity = PropertyModel(kind="linear", value=10., alpha=.01)
    mesh.boundary_nodes = {"left": mesh.terminal_nodes["left"], "right": mesh.terminal_nodes["right"]}
    project.thermal_boundaries = [ThermalBoundary("left", "metal", kind="temperature", value=300.),
                                  ThermalBoundary("right", "metal", kind="temperature", value=400.)]
    result = solve_thermal(project, mesh)
    x = mesh.points[:, 0] / 10e-6
    # Integral k(T)dT = 10*(u+.005*u²), boundary u=100.
    exact = 300 + (-1 + np.sqrt(1 + .02 * 150 * x)) / .01
    assert result.converged
    assert result.fields["metal"]["temperature"] == pytest.approx(exact, abs=.04)
    assert result.metrics["thermal_balance_error"] < 1e-5


def test_total_heat_source_and_lumped_sink():
    project, mesh = rectangle()
    project.thermal_boundaries = [ThermalBoundary("sink", "metal", kind="resistance", value=25.)]
    project.heat_sources = [HeatSource("heater", "metal", kind="total", value=.2)]
    all_region(mesh, "thermal", "sink")
    all_region(mesh, "heat", "heater")
    result = solve_thermal(project, mesh)
    assert result.converged
    assert result.metrics["temperature_max"] == pytest.approx(305., abs=1e-10)
    assert result.metrics["thermal_resistance"] == pytest.approx(25., rel=1e-10)
