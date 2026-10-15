# SPDX-License-Identifier: MPL-2.0
"""Transport and topology checks for the complex interconnect teaching cases."""
from copy import deepcopy

import numpy as np
import pytest
from shapely.geometry import Point

from etlayout.examples import make_example, rectangle
from etlayout.geometry.layout import LayoutDocument
from etlayout.simulation.runner import prepare_mesh
from etlayout.solvers import solve


@pytest.mark.parametrize("name", ["E11", "E12", "E13"])
def test_independent_heat_reaches_larger_substrate_and_balances(name, tmp_path):
    project = make_example(name, tmp_path)
    project.solver.mode = "thermal"
    geometry, mesh = prepare_mesh(project)
    result = solve(project, mesh)
    expected_power = sum(source.value for source in project.heat_sources)
    assert expected_power > 0
    assert result.converged
    assert result.metrics["thermal_power"] == pytest.approx(expected_power)
    assert result.metrics["heat_out"] == pytest.approx(expected_power, rel=1e-6)
    assert result.metrics["temperature_max"] > 300.
    conductor = geometry.layers[project.layers[0].id]
    substrate = geometry.layers["substrate"]
    # Heat can spread beyond the entire conductor's bounds, rather than being
    # silently clipped to its imported mask or staying confined to the trace.
    assert substrate.contains(conductor.envelope)
    outside = ~np.array([conductor.envelope.covers(Point(point)) for point in mesh.points])
    temperature = result.fields["substrate"]["temperature"]
    assert np.nanmax(temperature[outside]) > 300.
    assert len(mesh.triangles) < 20000


def test_serpentine_positive_temperature_feedback_and_power_accounting(tmp_path):
    project = make_example("E11", tmp_path)
    _, mesh = prepare_mesh(project)
    project.solver.mode = "electrical"
    cold = solve(project, mesh)
    project.solver.mode = "electrothermal"
    warm = solve(project, mesh)
    assert cold.converged and warm.converged
    assert warm.terminals["drain"]["current"] == pytest.approx(.02, rel=1e-6)
    assert warm.metrics["resistance"] > cold.metrics["resistance"]
    # For positive linear TCR, the effective resistance rise must be bounded by
    # the largest local resistivity rise, regardless of the detailed turn mesh.
    alpha = project.materials["platinum"].resistivity.alpha
    upper_ratio = 1 + alpha * (warm.metrics["temperature_max"] - 300.)
    assert warm.metrics["resistance"] / cold.metrics["resistance"] <= upper_ratio
    assert warm.metrics["thermal_power"] == pytest.approx(warm.metrics["electrical_power"] + .002)
    assert warm.metrics["thermal_balance_error"] < project.solver.thermal_balance_tolerance
    assert warm.metrics["power_balance_error"] < project.solver.power_balance_tolerance


def test_both_hierarchical_via_banks_carry_the_series_current(tmp_path):
    project = make_example("E12", tmp_path)
    project.solver.mode = "electrical"
    geometry, mesh = prepare_mesh(project)
    result = solve(project, mesh)
    assert result.converged
    for interface in ("via12", "via23"):
        area = geometry.interfaces[interface]
        # A misplaced hierarchy transform could leave these valid-looking masks
        # outside the intended metal overlap, or merge the separated vias.
        assert len(area.geoms) == 9
        assert area.area == pytest.approx(9 * (6e-6)**2)
        contact = result.metrics["contacts"][interface]
        assert contact["current_a_to_b"] == pytest.approx(-.3, rel=1e-6)
        assert contact["power"] > 0.
    assert result.metrics["power_balance_error"] < 1e-6
    document = LayoutDocument(project.layout.path)
    assert len(document.instances(project.layout.cell, recursive=True)) == 20
    taller = deepcopy(project)
    taller.interfaces[0].height *= 2
    increased_resistance = solve(taller, mesh)
    assert increased_resistance.converged
    assert increased_resistance.metrics["resistance"] > result.metrics["resistance"]


def test_real_holes_remove_conductor_and_raise_resistance(tmp_path):
    project = make_example("E13", tmp_path)
    project.solver.mode = "electrical"
    geometry, mesh = prepare_mesh(project)
    perforated = solve(project, mesh)
    assert perforated.converged
    sheet = geometry.layers["spreader"]
    assert len(sheet.interiors) == 5
    for x, y in ((65, 50), (120, 70), (175, 50), (30, 70), (210, 70)):
        point = Point(x * 1e-6, y * 1e-6)
        assert not sheet.covers(point)
        assert geometry.layers["substrate"].covers(point)
    filled_project = deepcopy(project)
    filled_project.layers[0].geometry = rectangle(0, 0, 240, 140)
    _, filled_mesh = prepare_mesh(filled_project)
    filled = solve(filled_project, filled_mesh)
    assert filled.converged
    analytical = project.materials["copper"].resistivity.value * 240e-6 / (140e-6 * 2e-6)
    assert filled.metrics["resistance"] == pytest.approx(analytical, rel=.005)
    assert perforated.metrics["resistance"] > 1.5 * filled.metrics["resistance"]
    # A broad concentration check is robust to mesh details; a pinned sharp-
    # corner maximum would incorrectly imply a mesh-independent peak value.
    assert perforated.metrics["current_density_p95"] > 1.5 * filled.metrics["current_density_p95"]
