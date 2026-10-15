# SPDX-License-Identifier: MPL-2.0
"""Imported GDS → conforming mesh → every requested solve mode, all examples."""
from copy import deepcopy

import pytest

from etlayout.examples import ROUTINE_EXAMPLES, make_example
from etlayout.models import HeatSource
from etlayout.simulation.runner import prepare_mesh
from etlayout.solvers import solve


@pytest.mark.parametrize("name", ROUTINE_EXAMPLES)
def test_all_examples_electrical_independent_thermal_and_coupled(name, tmp_path):
    project = make_example(name, tmp_path)
    # Exercise the three steady backends independently of an example's default
    # transient workflow. Pulsed examples have separate integration benchmarks.
    project.transient.enabled = False
    conductors = [layer for layer in project.layers if layer.role == "conductor"]
    # Prescribed thermal-only examples have no electrical degrees of freedom.
    # Still verify their independent steady heat solve; pulse dynamics and
    # unpowered-receiver isolation are covered in test_transient_examples.
    first = conductors[0] if conductors else project.layers[0]
    project.heat_sources = [HeatSource("independent_test", first.id, deepcopy(first.geometry),
                                       kind="total", value=.001)]
    _, mesh = prepare_mesh(project)
    for mode in (("electrical", "thermal", "electrothermal") if conductors else ("thermal",)):
        project.solver.mode = mode
        result = solve(project, mesh)
        assert result.converged, f"{name}/{mode}: {result.diagnostics}; {result.metrics}"
        if mode != "thermal":
            assert result.metrics["current_balance_error"] < project.solver.current_balance_tolerance
            assert result.metrics["power_balance_error"] < project.solver.power_balance_tolerance
        if mode != "electrical":
            assert result.metrics["thermal_balance_error"] < project.solver.thermal_balance_tolerance
            assert result.metrics["temperature_max"] >= 300.
