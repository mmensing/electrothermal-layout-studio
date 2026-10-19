# SPDX-License-Identifier: MPL-2.0
"""Time refinement reuses geometry, never an invented thermal initial state."""
import multiprocessing
from copy import deepcopy

import numpy as np
import pytest

from etlayout.examples import make_example
from etlayout.models import Cancelled, SimulationError
from etlayout.simulation.execution import ExecutionSettings
from etlayout.simulation.runner import _capture_mesh_reference, mesh_fingerprint, prepare_mesh, run_project
from etlayout.simulation.sweep import time_step_convergence_study

pytestmark = pytest.mark.numerical


def test_real_parallel_three_step_neck_refinement_has_exact_common_mesh_and_cold_start(tmp_path):
    project = make_example("E36", tmp_path)
    original = deepcopy(project.to_dict())
    rows = time_step_convergence_study(project, [10e-6, 5e-6, 2.5e-6],
                                       execution=ExecutionSettings(cpu_threads=2, study_workers=2))
    assert all(row.result is not None and row.result.converged and not row.error for row in rows), [row.error for row in rows]
    assert len({row.metrics["time_study_mesh_sha256"] for row in rows}) == 1
    first = rows[0].result.mesh
    for row in rows:
        result = row.result
        assert row.metrics["study_execution"]["actual_workers"] == 2
        assert row.metrics["time_study_fixed_mesh"] and row.metrics["time_study_status"] == "available"
        np.testing.assert_array_equal(result.mesh.points, first.points)
        np.testing.assert_array_equal(result.mesh.triangles, first.triangles)
        assert result.mesh.regions.keys() == first.regions.keys()
        for key in first.regions:
            np.testing.assert_array_equal(result.mesh.regions[key], first.regions[key])
        initial = result.transient.snapshots[0]
        assert initial.metrics["state_kind"] == "initial_condition"
        assert initial.metrics["transient_initialization"]["kind"] == "uniform"
        for fields in initial.element_fields.values():
            if "temperature" in fields:
                values = fields["temperature"]
                assert values[np.isfinite(values)] == pytest.approx(project.solver.initial_temperature)
    assert rows[-1].metrics["transient_peak_temperature_absolute_change"] > 0
    assert project.to_dict() == original


@pytest.mark.parametrize("resume", [False, True])
def test_saved_and_resumed_parallel_time_studies_preserve_actual_supplied_state(tmp_path, resume):
    project = make_example("E17", tmp_path)
    project.transient.stop_time = 300e-6
    project.transient.output_interval = 100e-6
    source = run_project(project)
    before = deepcopy(source.fields)
    if resume:
        project.transient.stop_time = 600e-6
    rows = time_step_convergence_study(project, [50e-6, 25e-6], initial_result=source, resume=resume,
                                       execution=ExecutionSettings(cpu_threads=2, study_workers=2))
    assert all(row.result is not None and row.result.converged and not row.error for row in rows)
    digest = mesh_fingerprint(source.mesh)
    for row in rows:
        assert row.metrics["time_study_mesh_sha256"] == digest
        start = row.result.transient.snapshots[0]
        assert start.metrics["time_s"] == pytest.approx(300e-6 if resume else 0.)
        assert start.metrics["transient_initialization"]["kind"] == ("resume" if resume else "saved")
        for layer, fields in source.fields.items():
            if "temperature" in fields:
                np.testing.assert_array_equal(start.fields[layer]["temperature"], fields["temperature"])
    for layer, fields in before.items():
        for field, values in fields.items():
            np.testing.assert_array_equal(source.fields[layer][field], values)


@pytest.mark.parametrize("change", ["geometry", "mesh", "mutation", "source"])
def test_shared_mesh_reference_rejects_mismatched_geometry_settings_or_changed_arrays(tmp_path, change):
    project = make_example("E17", tmp_path)
    _, mesh = prepare_mesh(project)
    reference = _capture_mesh_reference(project, mesh)
    if change == "geometry":
        project.roi = deepcopy(project.layers[0].geometry)
    elif change == "mesh":
        project.mesh.contact_size *= .9
    elif change == "mutation":
        reference.mesh.points[0, 0] += 1e-9
    else:
        project.layout.sha256 = "0" * 64
    with pytest.raises(SimulationError, match="Shared study mesh|Layout content changed"):
        run_project(project, _mesh_reference=reference)


def test_cancel_parallel_fixed_mesh_time_study_stops_owned_children(tmp_path):
    project = make_example("E17", tmp_path)
    initial_children = {child.pid for child in multiprocessing.active_children()}
    ticks = []
    def progress(_fraction, message):
        if message.startswith("Study:"):
            ticks.append(message)
    with pytest.raises(Cancelled):
        time_step_convergence_study(project, [50e-6, 25e-6, 12.5e-6],
            execution=ExecutionSettings(cpu_threads=2, study_workers=2), progress=progress,
            cancel=lambda: len(ticks) >= 2)
    assert len(ticks) == 2
    assert {child.pid for child in multiprocessing.active_children()} <= initial_children


def test_mesh_fingerprint_accepts_empty_tagged_edge_support():
    from solver_helpers import rectangle
    _, mesh = rectangle(nx=1, ny=1)
    mesh.boundary_edges["empty"] = np.empty((0, 2), dtype=np.int64)
    digest = mesh_fingerprint(mesh)
    assert len(digest) == 64
    assert digest == mesh_fingerprint(deepcopy(mesh))
