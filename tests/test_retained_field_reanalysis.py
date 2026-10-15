# SPDX-License-Identifier: MPL-2.0
"""Reporting revisions reuse real fields without changing historical identity."""
from copy import deepcopy

import numpy as np
import pytest
from solver_helpers import rectangle

from etlayout.io.project import configuration_hash
from etlayout.models import MeasurementSpec, RegionSpec, SimulationError, SupportSelector
from etlayout.power.workflows import execute_operation
from etlayout.solvers import solve_electrical


def test_reanalysis_is_independent_report_and_preserves_calculated_source():
    project, mesh = rectangle(nx=3, ny=2)
    project.layers[0].geometry = RegionSpec(polygons=[{"exterior": [[0, 0], [10e-6, 0], [10e-6, 2e-6], [0, 2e-6]], "holes": []}])
    project.terminals[0].geometry = RegionSpec(line=[[0, 0], [0, 2e-6]])
    project.terminals[1].geometry = RegionSpec(line=[[10e-6, 0], [10e-6, 2e-6]])
    original = solve_electrical(project, mesh)
    recorded = deepcopy(original)
    project.power.measurements.append(MeasurementSpec("mean", "potential", SupportSelector(layers=["metal"]), "mean"))
    output = execute_operation(project, "reanalyze", {"result": original})
    assert output["report"]["measurements"]["mean"]["value"] == pytest.approx(.5, abs=1e-12)
    assert output["result"] is not original
    assert output["result"].configuration_hash == original.configuration_hash
    assert original.analysis == recorded.analysis
    assert original.configuration == recorded.configuration
    assert output["report"]["reporting_context"] == {
        "field_configuration_hash": original.configuration_hash,
        "reporting_configuration_hash": configuration_hash(project), "field_solve_reused": True}
    for layer, fields in original.fields.items():
        for key, values in fields.items():
            np.testing.assert_array_equal(output["result"].fields[layer][key], values)


def test_reanalysis_refuses_changed_boundary_and_missing_source():
    project, mesh = rectangle()
    result = solve_electrical(project, mesh)
    project.terminals[0].value += .1
    with pytest.raises(SimulationError, match="Stale result"):
        execute_operation(project, "reanalyze", {"result": result})
    with pytest.raises(SimulationError, match="compatible calculated run"):
        execute_operation(project, "reanalyze")
