# SPDX-License-Identifier: MPL-2.0
"""Reused GUI reference calculations never share mutable document/field state."""
from copy import deepcopy

import numpy as np
import pytest
from gui_test_helpers import solved_e1 as solved_e1

pytestmark = pytest.mark.desktop


def test_gui_reference_consumers_cannot_mutate_the_session_template(solved_e1, e1_reference_solution):
    project, result = solved_e1
    reference_project, reference_result = e1_reference_solution
    before = deepcopy(reference_project.to_dict())
    layer, fields = next(iter(result.fields.items()))
    field, values = next(iter(fields.items()))
    reference_values = reference_result.fields[layer][field].copy()
    reference_points = reference_result.mesh.points.copy()
    assert project is not reference_project and result is not reference_result
    assert not np.shares_memory(values, reference_result.fields[layer][field])
    assert not np.shares_memory(result.mesh.points, reference_result.mesh.points)
    project.name = "Owned by one GUI consumer"
    project.layers[0].thickness *= 2
    values[:] = values + 1
    result.mesh.points[:] += 1
    assert reference_project.to_dict() == before
    np.testing.assert_array_equal(reference_result.fields[layer][field], reference_values)
    np.testing.assert_array_equal(reference_result.mesh.points, reference_points)
