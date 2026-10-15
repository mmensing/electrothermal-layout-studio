# SPDX-License-Identifier: MPL-2.0
"""Physical and topological regressions for the advanced device gallery."""
from copy import deepcopy

import numpy as np
import pytest

from etlayout.advanced_devices import build_device_example
from etlayout.examples import LayoutBuilder, make_example
from etlayout.models import Project
from etlayout.simulation.runner import prepare_mesh
from etlayout.solvers import solve


def test_unknown_device_does_not_mutate_project():
    project = Project()
    before = deepcopy(project)
    assert not build_device_example("unknown", LayoutBuilder("unknown"), project)
    assert project == before


def test_bridge_balance_and_signed_output_with_nonloading_sense_probes(tmp_path):
    project = make_example("E14", tmp_path)
    _, mesh = prepare_mesh(project)
    project.solver.mode = "electrical"
    output = []
    for sheet_resistance in (80., 100., 120.):
        project.layer("arm_br").electrical.sheet_resistance.value = sheet_resistance
        result = solve(project, mesh)
        assert result.converged
        output.append(result.terminals["sense_top"]["voltage"] - result.terminals["sense_bottom"]["voltage"])
        # Equal exposed arm aspect ratios give the ordinary bridge divider limit;
        # finite metal bus resistance produces a small, deliberately retained correction.
        expected = .1 * (.5 - 100. / (100. + sheet_resistance))
        assert output[-1] == pytest.approx(expected, abs=2e-7)
        for identifier in ("sense_top", "sense_bottom"):
            assert abs(result.terminals[identifier]["current"]) < 1e-10
        assert result.metrics["resistance_terminal_pair"] == ["source", "drain"]
        assert result.metrics["resistance_current_terminal"] == "drain"
    assert output[0] < 0 < output[2]
    assert abs(output[1]) < 1e-8


def test_heaters_are_electrically_independent_but_thermally_coupled(tmp_path):
    project = make_example("E15", tmp_path)
    geometry, mesh = prepare_mesh(project)
    assert geometry.layers["substrate"].area > 8 * geometry.layers["heater_a"].area
    assert geometry.layers["heater_a"].disjoint(geometry.layers["heater_b"])
    assert len(mesh.triangles) < 20000
    project.solver.mode = "electrical"
    both = solve(project, mesh)
    next(t for t in project.terminals if t.id == "drain_a").value = 0.
    only_b = solve(project, mesh)
    assert both.converged and only_b.converged
    assert only_b.terminals["drain_a"]["current"] == pytest.approx(0., abs=1e-12)
    assert only_b.terminals["drain_b"]["current"] == pytest.approx(both.terminals["drain_b"]["current"], rel=1e-9)
    project.solver.mode = "thermal"
    project.heat_sources[0].value = 0.
    baseline = solve(project, mesh)
    project.heat_sources[0].value = .002
    heated = solve(project, mesh)
    assert baseline.converged and heated.converged
    baseline_b = np.nanmean(baseline.fields["heater_b"]["temperature"])
    heated_b = np.nanmean(heated.fields["heater_b"]["temperature"])
    assert heated_b > baseline_b + 1.
    assert heated.metrics["thermal_power"] == pytest.approx(.00215)
    assert heated.metrics["heat_out"] == pytest.approx(.00215, rel=1e-6)


def test_full_width_gate_cuts_every_channel_path_and_has_no_electrical_contact(tmp_path):
    project = make_example("E16", tmp_path)
    geometry, mesh = prepare_mesh(project)
    channels = geometry.layers["channel"]
    assert len(channels.geoms) == 4
    ungated = channels.difference(geometry.terminals["gate_control"])
    assert len(ungated.geoms) == 8
    # Removing the controlled region must leave no piece joining both contacts.
    for piece in ungated.geoms:
        touches_source = piece.intersection(geometry.interfaces["source_contact"]).area > 0
        touches_drain = piece.intersection(geometry.interfaces["drain_contact"]).area > 0
        assert touches_source != touches_drain
    assert project.layer("gate_metal").role == "thermal"
    assert all(interface.electrical_kind == "none" for interface in project.interfaces
               if "gate_metal" in (interface.layer_a, interface.layer_b))
    assert len(mesh.triangles) < 20000


@pytest.mark.parametrize("mode", ["electrical", "electrothermal"])
def test_full_gate_transfer_suppresses_current_and_preserves_conservation(tmp_path, mode):
    project = make_example("E16", tmp_path)
    _, mesh = prepare_mesh(project)
    project.solver.mode = mode
    gate = next(terminal for terminal in project.terminals if terminal.id == "gate_control")
    currents = []
    for voltage in (-5., 1.):
        gate.value = voltage
        result = solve(project, mesh)
        assert result.converged, result.diagnostics
        assert result.metrics["current_balance_error"] < project.solver.current_balance_tolerance
        assert result.metrics["power_balance_error"] < project.solver.power_balance_tolerance
        assert result.terminals["gate_control"]["current"] == 0
        assert result.metrics["contact_power"] > 0
        currents.append(result.terminals["drain"]["current"])
        if mode == "electrothermal":
            assert result.metrics["thermal_balance_error"] < project.solver.thermal_balance_tolerance
    assert currents[1] > 1000 * currents[0] > 0
