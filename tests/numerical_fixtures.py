# SPDX-License-Identifier: MPL-2.0
"""Immutable analytical solutions shared only within each requesting module."""
import pytest
from solver_helpers import rectangle

from etlayout.solvers import solve_electrical


@pytest.fixture(scope='module', params=[(2, 1), (13, 5)])
def linear_strip_solution(request):
    project, mesh = rectangle(nx=request.param[0], ny=request.param[1])
    result = solve_electrical(project, mesh)
    for collection in (result.fields, result.element_fields, result.interface_fields):
        for fields in collection.values():
            for values in fields.values():
                values.setflags(write=False)
    return project, result
