# SPDX-License-Identifier: MPL-2.0
"""Analytical port responses and actual conforming-mesh extraction studies."""
import json
from copy import deepcopy

import numpy as np
import pytest
from solver_helpers import rectangle
from test_power_solver_multiport import star_fixture, two_ports

from etlayout.examples import make_example
from etlayout.models import Cancelled, PhysicalPort, PortLanding, SimulationError
from etlayout.power.multiport_study import _entrywise_change, run_multiport_mesh_study
from etlayout.simulation.execution import ExecutionSettings, execution_context


def passive_e1(tmp_path):
    project = make_example("E1", tmp_path)
    project.power.ports = [PhysicalPort("positive", [PortLanding("drain", .01)], value=.02),
                           PhysicalPort("negative", [PortLanding("source", .02)], kind="ground")]
    return project


@pytest.mark.numerical
def test_real_three_mesh_rectangle_study_matches_analytical_resistance_and_keeps_source(tmp_path):
    project = passive_e1(tmp_path)
    before = deepcopy(project.to_dict())
    progress = []
    with execution_context(ExecutionSettings(cpu_threads=1, study_workers=1)):
        study = run_multiport_mesh_study(project, [20e-6, 10e-6, 5e-6], reference="negative",
                                         progress=lambda fraction, message: progress.append(fraction))
    assert study["converged"] and study["mesh_verified"] and study["status"] == "verified", study
    expected_r = .084 + .01 + .02  # rho L/(width * thickness) plus two known feeds.
    expected_g = np.array([[1., -1.], [-1., 1.]]) / expected_r
    assert len(study["levels"]) == 3 and len(study["comparisons"]) == 2
    for level in study["levels"]:
        np.testing.assert_allclose(level["conductance_matrix"], expected_g, rtol=1e-9, atol=0)
        np.testing.assert_allclose(level["comparison_resistance_matrix_ohm"], [[expected_r]], rtol=1e-9, atol=0)
    assert study["network"]["reference"] == "negative"
    assert study["network"]["source_physics_hash"] == study["source_physics_hash"]
    assert study["network"]["source_configuration_hash"] == study["source_configuration_hash"]
    assert study["network"]["configuration_hash"] != study["source_configuration_hash"]
    assert all(row["distinct_refinement"] for row in study["comparisons"])
    assert project.to_dict() == before
    assert progress[-1] == 1. and all(b >= a for a, b in zip(progress, progress[1:]))
    json.dumps(study, allow_nan=False)


@pytest.mark.parametrize("change", ["lost", "sign", "sharing"])
def test_analytical_star_weak_transfer_is_never_hidden_by_global_scale(change):
    # Independent star reduction: currents g_i*(V_i - sum(g_j V_j)/sum(g_j)).
    branches = np.array([1., 2., 1e-12])
    fine = np.diag(branches) - np.outer(branches, branches) / branches.sum()
    coarse = fine.copy()
    if change == "lost":
        coarse[0, 2] = coarse[2, 0] = 0.
    elif change == "sign":
        coarse[0, 2] *= -1
    else:
        coarse[0, 2] *= 1.25
    # A global absolute bound would wrongly pass this weak-path difference.
    assert np.allclose(coarse, fine, atol=1e-8, rtol=1e-8)
    compared = _entrywise_change(coarse, fine, .02)
    assert not compared["within_tolerance"]
    assert compared["maximum_relative_change"] >= .2 - 1e-15
    assert bool(compared["zero_changes"]) == (change == "lost")
    assert bool(compared["sign_changes"]) == (change == "sign")


def test_entrywise_resistance_changes_preserve_zeros_and_extreme_finite_scales():
    values = np.array([[1e-300, 0.], [0., 1e300]])
    assert _entrywise_change(values, values, .02)["within_tolerance"]
    changed = values.copy()
    changed[0, 0] *= 2
    compared = _entrywise_change(changed, values, .02)
    assert not compared["within_tolerance"] and compared["relative_changes"][0][0] == .5
    assert compared["relative_changes"][1][1] == 0


@pytest.mark.parametrize("sizes, tolerance", [
    ([2e-6, 1e-6], .02), ([1e-6] * 3, .02), ([2e-6, 0., -1e-6], .02),
    ([2e-6, 1e-6, float("nan")], .02), ([2e-6, 1e-6, .5e-6], 0.),
    ([2e-6, 1e-6, .5e-6], 1.), ([2e-6, 1e-6, .5e-6], float("nan")),
])
def test_invalid_study_bounds_fail_before_meshing(sizes, tolerance):
    project, _ = two_ports()
    with pytest.raises(SimulationError, match="3–5|tolerance"):
        run_multiport_mesh_study(project, sizes, relative_tolerance=tolerance)


def test_spatial_temperature_is_explicitly_rejected_and_cancellation_propagates():
    project, _ = two_ports()
    with pytest.raises(SimulationError, match="do not project spatial temperature"):
        run_multiport_mesh_study(project, temperatures={"metal": np.ones(10) * 300})
    with pytest.raises(Cancelled):
        run_multiport_mesh_study(project, cancel=lambda: True)


@pytest.mark.numerical
def test_cancellation_during_actual_preparation_does_not_return_partial_success(tmp_path):
    project = passive_e1(tmp_path)
    before = deepcopy(project.to_dict())
    stop = []
    def progress(fraction, message):
        if "Extracting network on mesh level" in message:
            stop.append(True)
    with pytest.raises(Cancelled):
        run_multiport_mesh_study(project, progress=progress, cancel=lambda: bool(stop))
    assert stop and project.to_dict() == before


@pytest.mark.numerical
def test_real_resource_limited_study_does_not_claim_verification(tmp_path):
    project = passive_e1(tmp_path)
    project.mesh.max_elements = 1
    before = deepcopy(project.to_dict())
    study = run_multiport_mesh_study(project, [20e-6, 10e-6, 5e-6])
    assert study["status"] == "not-established" and not study["converged"] and not study["mesh_verified"]
    assert study["network"] is None and study["levels"] == []
    assert study["failure"]["level"] == 0 and "budget" in study["failure"]["message"]
    assert project.to_dict() == before


@pytest.mark.numerical
def test_unchanged_actual_mesh_is_not_a_refinement_and_temperature_remains_fixed(monkeypatch):
    # Real analytical FEM solves on a held mesh: a mesher failing to refine must
    # not gain verification merely because repeated operators are identical.
    project, mesh = two_ports()
    project.materials["copper"].resistivity.kind = "linear"
    project.materials["copper"].resistivity.alpha = .002
    observed = []
    def held_mesh(candidate, progress, cancel=None):
        observed.append((candidate.mesh.max_size, candidate.mesh.min_size, candidate.mesh.contact_size))
        return None, mesh
    monkeypatch.setattr("etlayout.power.multiport_study.prepare_mesh", held_mesh)
    before = deepcopy(project.to_dict())
    study = run_multiport_mesh_study(project, temperatures=350., reference="positive")
    assert study["converged"] and not study["mesh_verified"] and study["status"] == "not-established"
    assert not any(comparison["distinct_refinement"] for comparison in study["comparisons"])
    assert study["network"]["reference"] == "positive"  # User coordinate survives stable comparison ground.
    # Sheet R .2Ω at300K becomes .22Ω at350K; feeds .1+.3Ω stay fixed.
    assert study["network"]["resistance_matrix"][0][0] == pytest.approx(.62, rel=1e-12)
    assert all(level["comparison_resistance_matrix_ohm"][0][0] == pytest.approx(.62, rel=1e-12)
               for level in study["levels"])
    assert [item[0] for item in observed] == study["requested_sizes_m"]
    assert observed[0][1] / observed[-1][1] == 4
    assert observed[0][2] / observed[-1][2] == 4
    assert project.to_dict() == before


@pytest.mark.numerical
def test_independent_three_port_star_keeps_weak_path_and_uses_one_strong_comparison_ground(monkeypatch):
    project, _ = star_fixture()
    project.power.ports[2].landings[0].resistance = 1e9
    serial = iter((4, 8, 16))
    def structured_mesh(candidate, progress, cancel=None):
        _, mesh = rectangle(nx=next(serial), ny=2, layers=("a", "b", "c"))
        for layer in candidate.layers:
            for side in ("left", "right"):
                mesh.terminal_nodes[f"{layer.id}_{side}"] = mesh.terminal_nodes[side]
        return None, mesh
    monkeypatch.setattr("etlayout.power.multiport_study.prepare_mesh", structured_mesh)
    study = run_multiport_mesh_study(project, port_ids=["a", "b", "c"], reference="c")
    assert study["converged"] and study["mesh_verified"], study
    assert study["comparison_reference"] == "a" and study["network"]["reference"] == "c"
    branches = 1 / np.array([.3, .6, 1e9 + .6])
    expected_g = np.diag(branches) - np.outer(branches, branches) / branches.sum()
    for level in study["levels"]:
        np.testing.assert_allclose(level["conductance_matrix"], expected_g, rtol=1e-8, atol=0)
        np.testing.assert_allclose(level["comparison_resistance_matrix_ohm"],
                                   [[.9, .3], [.3, 1e9 + .9]], rtol=1e-8, atol=0)
