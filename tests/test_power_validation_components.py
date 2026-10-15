# SPDX-License-Identifier: MPL-2.0
"""Public solver preflight preserves signed normal and in-plane components."""
from copy import deepcopy

import pytest
from test_power_optimization import example

from etlayout.models import (
    Interface,
    MeasurementSpec,
    PowerSettings,
    RegionSpec,
    SimulationError,
    SupportSelector,
    Terminal,
)
from etlayout.simulation.runner import run_project


def _contact_project(tmp_path, component):
    project = example(tmp_path)
    upper = deepcopy(project.layers[0])
    upper.id, upper.name, upper.z = "upper", "Upper", 2e-6
    project.layers.append(upper)
    project.terminals = [Terminal("anode", "metal", RegionSpec(full_domain=True), value=-1.),
                         Terminal("cathode", "upper", RegionSpec(full_domain=True), kind="ground")]
    project.interfaces = [Interface("contact", "metal", "upper", RegionSpec(full_domain=True),
                                    electrical_kind="conductance", electrical_value=1e10)]
    project.power = PowerSettings(measurements=[MeasurementSpec("transfer", "contact_normal_density",
        SupportSelector(contacts=["contact"]), "integral", vector_component=component)])
    return project


@pytest.mark.parametrize("component,expected", [("signed", -.2), ("magnitude", .2)])
def test_public_solver_accepts_signed_normal_contact_measurement(tmp_path, component, expected):
    project = _contact_project(tmp_path, component)
    result = run_project(project)
    assert result.converged
    record = result.analysis["measurements"]["transfer"]
    assert record["status"] == "available", record
    # Independent interface law: I = g_area * 20 um² * (-1 V).
    assert record["value"] == pytest.approx(expected)
    assert result.metrics["contacts"]["contact"]["current_a_to_b"] == pytest.approx(-.2)


@pytest.mark.parametrize("component", ["x", "y"])
def test_public_solver_preserves_in_plane_vector_components(tmp_path, component):
    project = example(tmp_path)
    project.power = PowerSettings(measurements=[MeasurementSpec("in_plane", "metal_current_density",
        SupportSelector(layers=["metal"]), "mean", vector_component=component)])
    result = run_project(project)
    assert result.converged
    record = result.analysis["measurements"]["in_plane"]
    expected_x = -(.1 / 10e-6) / project.materials["copper"].resistivity.value
    assert record["status"] == "available", record
    assert record["value"] == pytest.approx(expected_x if component == "x" else 0., abs=1.)


@pytest.mark.parametrize("quantity,component", [("contact_normal_density", "x"),
    ("contact_normal_density", "y"), ("sheet_current", "signed")])
def test_public_preflight_rejects_component_from_wrong_physical_quantity(tmp_path, quantity, component):
    project = _contact_project(tmp_path, component)
    project.power.measurements[0].quantity = quantity
    with pytest.raises(SimulationError, match="vector component"):
        run_project(project)
