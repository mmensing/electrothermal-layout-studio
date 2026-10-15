# SPDX-License-Identifier: MPL-2.0
import numpy as np
import pytest
from solver_helpers import all_region, rectangle

from etlayout.models import Cancelled, Interface, PropertyModel, Terminal, ThermalBoundary
from etlayout.solvers import solve


def test_voltage_driven_temperature_dependent_metal_exact_uniform_solution():
    project, mesh = rectangle()
    project.solver.mode = "electrothermal"
    project.materials["copper"].resistivity = PropertyModel(kind="linear", value=2e-8, alpha=.003)
    project.thermal_boundaries = [ThermalBoundary("sink", "metal", value=1e10)]
    all_region(mesh, "thermal", "sink")
    result = solve(project, mesh)
    exact_rise = (-1 + np.sqrt(1 + 4 * .003 * 25.)) / (.006)
    assert result.converged
    assert result.metrics["temperature_max"] == pytest.approx(300 + exact_rise, abs=3e-5)
    assert result.metrics["effective_resistance"] == pytest.approx(.2 * (1 + .003 * exact_rise), rel=1e-6)
    assert result.metrics["thermal_residual"] < 1e-6
    assert result.metrics["thermal_balance_error"] < 1e-6
    assert result.metrics["power_balance_error"] < 1e-8
    assert len(result.iterations) > 2


def test_current_driven_positive_feedback_exact_solution():
    project, mesh = rectangle()
    project.solver.mode = "electrothermal"
    project.materials["copper"].resistivity = PropertyModel(kind="linear", value=2e-8, alpha=.003)
    project.terminals[1].kind = "current"
    project.terminals[1].value = .01
    project.thermal_boundaries = [ThermalBoundary("sink", "metal", value=1e5)]
    all_region(mesh, "thermal", "sink")
    result = solve(project, mesh)
    exact_rise = 10. / (1. - .003 * 10.)
    assert result.converged
    assert result.metrics["temperature_max"] == pytest.approx(300 + exact_rise, abs=2e-5)
    assert result.terminals["right"]["current"] == pytest.approx(.01, rel=1e-9)


def test_contact_power_is_included_in_thermal_sources_without_rescaling():
    project, mesh = rectangle(layers=("a", "b"))
    project.solver.mode = "electrothermal"
    project.terminals = [Terminal("top", "a", value=1.), Terminal("bottom", "b", kind="ground")]
    project.interfaces = [Interface("contact", "a", "b", electrical_kind="lumped", electrical_value=10.)]
    project.thermal_boundaries = [ThermalBoundary("sink_a", "a", kind="resistance", value=100.),
                                  ThermalBoundary("sink_b", "b", kind="resistance", value=100.)]
    for prefix, key in (("terminal", "top"), ("terminal", "bottom"), ("interface", "contact"),
                        ("thermal", "sink_a"), ("thermal", "sink_b")):
        all_region(mesh, prefix, key)
    result = solve(project, mesh)
    assert result.converged
    assert result.metrics["contact_power"] == pytest.approx(.1, rel=1e-12)
    assert result.metrics["thermal_power"] == pytest.approx(.1, rel=1e-12)
    assert result.metrics["temperature_max"] == pytest.approx(305., abs=1e-5)
    assert result.metrics["joule_power"] < 1e-20


def test_coupled_nonconvergence_preserves_diagnostics_and_cancellation():
    project, mesh = rectangle()
    project.solver.mode = "electrothermal"
    project.solver.max_iterations = 1
    project.thermal_boundaries = [ThermalBoundary("sink", "metal", value=1e10)]
    all_region(mesh, "thermal", "sink")
    result = solve(project, mesh)
    assert not result.converged
    assert result.iterations and result.fields
    assert any(item.severity == "error" for item in result.diagnostics)
    with pytest.raises(Cancelled):
        solve(project, mesh, cancel=lambda: True)


def test_one_way_heat_api_keeps_contact_and_joule_integrals():
    from etlayout.solvers import electrical_heat, solve_electrical, solve_thermal

    project, mesh = rectangle()
    project.thermal_boundaries = [ThermalBoundary("sink", "metal", value=1e10)]
    all_region(mesh, "thermal", "sink")
    electrical = solve_electrical(project, mesh)
    heat = electrical_heat(electrical)
    thermal = solve_thermal(project, mesh, heat=heat)
    assert thermal.converged
    assert thermal.metrics["thermal_power"] == pytest.approx(electrical.metrics["electrical_power"], rel=1e-12)
    assert thermal.metrics["temperature_max"] == pytest.approx(325., abs=1e-10)


def test_public_dispatch_honors_one_way_setting_and_canonical_configuration_hash():
    from etlayout.io.project import configuration_hash

    project, mesh = rectangle()
    project.layout.path = "machine/specific/source.gds"
    project.layout.sha256 = "dummy-content-identity"
    project.solver.mode = "thermal"
    project.solver.thermal_include_joule = True
    project.thermal_boundaries = [ThermalBoundary("sink", "metal", value=1e10)]
    all_region(mesh, "thermal", "sink")
    result = solve(project, mesh)
    assert result.converged
    assert result.configuration_hash == configuration_hash(project)
    assert result.metrics["temperature_max"] == pytest.approx(325., abs=1e-10)
    assert result.metrics["thermal_power"] == pytest.approx(result.metrics["electrical_power"])
