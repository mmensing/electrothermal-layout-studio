# SPDX-License-Identifier: MPL-2.0
"""Planar analytic network references, with explicit limits of the 1D reduction."""
from copy import deepcopy

import numpy as np
import pytest

from etlayout.examples import edge, make_example, polygon, rectangle
from etlayout.models import PhysicalLayer, Project, Terminal
from etlayout.simulation.analysis import sample_points
from etlayout.simulation.runner import run_project
from etlayout.solvers import branch_current


def test_e3_planar_unequal_width_parallel_resistance_and_current_division(tmp_path):
    project = make_example("E3", tmp_path)
    # Use the actual E3 GDS masks. Cover both full bus bars with ideal electrodes
    # so the two 80 um branches have exact 1D solutions. Unmodified E3 retains
    # distributed bus access resistance and is tested separately in all modes.
    project.terminals[0].geometry = rectangle(0., 0., 10., 50.)
    project.terminals[1].geometry = rectangle(90., 0., 100., 50.)
    result = run_project(project)
    sheet_resistance = project.materials["copper"].resistivity.value / project.layers[0].thickness
    branch_r = sheet_resistance * 80. / np.array([10., 20.])
    equivalent_r = 1. / np.sum(1. / branch_r)
    assert result.converged
    assert result.metrics["effective_resistance"] == pytest.approx(equivalent_r, rel=1e-9)
    assert result.terminals["drain"]["voltage"] == pytest.approx(.3 * equivalent_r, rel=1e-9)
    currents = np.array([branch_current(result, "metal", [50e-6, low], [50e-6, high])
                         for low, high in [(0., 10e-6), (30e-6, 50e-6)]])
    np.testing.assert_allclose(currents, .3 * equivalent_r / branch_r, rtol=1e-9)
    assert currents[1] / currents[0] == pytest.approx(2., rel=1e-9)
    assert currents.sum() == pytest.approx(result.terminals["drain"]["current"], rel=1e-9)
    assert result.metrics["current_balance_error"] < 1e-10
    assert result.metrics["power_balance_error"] < 1e-10


def test_long_stepped_width_series_reference_and_mesh_convergence(tmp_path):
    materials = make_example("E1", tmp_path).materials
    project = Project(name="Long stepped-width series reference", materials=materials)
    project.layers = [PhysicalLayer("metal", "Stepped-width copper", "copper", thickness=1e-6,
        geometry=polygon([(0., -5.), (500., -5.), (500., -10.), (1000., -10.),
                          (1000., 10.), (500., 10.), (500., 5.), (0., 5.)]))]
    project.terminals = [Terminal("left", "metal", edge(0., -5., 0., 5.), "ground"),
                         Terminal("right", "metal", edge(1000., -10., 1000., 10.), "current", .01)]
    project.mesh.max_size = 10e-6
    project.mesh.min_size = .5e-6
    project.mesh.contact_size = 2e-6
    coarse = run_project(project)
    finer = deepcopy(project)
    finer.mesh.max_size = 5e-6
    finer.mesh.min_size = .25e-6
    finer.mesh.contact_size = 1e-6
    result = run_project(finer)
    assert coarse.converged and result.converged
    rs = materials["copper"].resistivity.value / project.layers[0].thickness
    series_r = rs * (500. / 10. + 500. / 20.)
    # An abrupt width step adds positive 2D spreading resistance. Long segments
    # make the 1D series sum accurate to 1%; it is not an exact step solution.
    measured_r = result.metrics["effective_resistance"]
    assert series_r < measured_r < 1.01 * series_r
    assert measured_r == pytest.approx(coarse.metrics["effective_resistance"], rel=.002)
    # Far from the step, equal physical lengths recover the two independent
    # series drops rho*I*L/(t*w), including their 2:1 ratio.
    potential = sample_points(result, "metal", "potential",
                              np.array([[100., 0.], [400., 0.], [600., 0.], [900., 0.]]) * 1e-6)
    drops = np.array([potential[1] - potential[0], potential[3] - potential[2]])
    np.testing.assert_allclose(drops, .01 * rs * 300. / np.array([10., 20.]), rtol=.001)
    for x, width in [(250e-6, 10e-6), (750e-6, 20e-6)]:
        current = branch_current(result, "metal", [x, -width / 2], [x, width / 2])
        assert current == pytest.approx(.01, rel=.001)
    assert result.metrics["current_balance_error"] < 1e-8
    assert result.metrics["power_balance_error"] < 1e-8
