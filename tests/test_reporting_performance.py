# SPDX-License-Identifier: MPL-2.0
"""Exact physical integration and run-owned timing regression contracts."""
from fractions import Fraction
from types import SimpleNamespace

import numpy as np
import pytest
from shapely.geometry import Polygon

from etlayout.examples import make_example
from etlayout.io.project import load_project, save_project
from etlayout.models import Cancelled, MeasurementSpec
from etlayout.power.analysis import _hotspot, _intersect_ids
from etlayout.simulation.execution import (
    ExecutionSettings,
    execution_context,
    execution_provenance,
    phase_timer,
)
from etlayout.simulation.runner import run_project


@pytest.mark.parametrize("squared,expected", [(False, 3.75), (True, float(Fraction(1351, 96)))])
@pytest.mark.parametrize("offset", [0., 1000.])
def test_slanted_triangle_hotspot_matches_exact_affine_integral(offset, squared, expected):
    # V=x+2y on x+y<=2. The hottest supported half-unit window is
    # the triangle (0,1.75),(0,2),(.25,1.75), with V=3.5,4,3.75.
    # Integrals of V and V² follow the exact triangle moments, independently
    # of the number of mesh triangles or the implementation's batching.
    patch = Polygon(np.array([[0., 0.], [2., 0.], [0., 2.]]) + offset)
    value, center = _hotspot([patch], np.array([[0., 2., 4.]]),
                            MeasurementSpec("hot", "temperature", averaging_length=.5), squared=squared)
    assert value == pytest.approx(expected, rel=1e-12)
    assert center == pytest.approx([offset, offset+2.])


def test_reporting_cancellation_propagates_without_favorable_unavailable_record():
    with pytest.raises(Cancelled, match="cancelled"):
        _hotspot([Polygon([[0., 0.], [2., 0.], [0., 2.]])], np.array([[0., 2., 4.]]),
                 MeasurementSpec("hot", "temperature", averaging_length=.5), cancel=lambda: True)


@pytest.mark.parametrize("squared,expected", [(False, 29.25), (True, float(Fraction(2567, 3)))])
def test_hotspot_batches_preserve_unique_maximum_and_exact_square_moments(squared, expected):
    # 441 physical window centers. V=x+2y is affine over both mesh triangles.
    # The unique maximum at(10,10) integrates over [9.5,10]²: mean29.25,
    # variance(V)=5/48, so mean(V²)=2567/3.
    patches = [Polygon([[0., 0.], [10., 0.], [10., 10.]]),
               Polygon([[0., 0.], [10., 10.], [0., 10.]])]
    value, center = _hotspot(patches, np.array([[0., 10., 30.], [0., 30., 20.]]),
                            MeasurementSpec("hot", "temperature", averaging_length=1.), squared=squared)
    assert value == pytest.approx(expected, rel=1e-12)
    assert center == [10., 10.]


def test_reporting_can_cancel_after_some_hotspot_windows_have_been_integrated():
    checkpoints = []

    def cancel():
        checkpoints.append(True)
        return len(checkpoints) > 1

    with pytest.raises(Cancelled):
        _hotspot([Polygon([[0., 0.], [10., 0.], [10., 10.]])], np.array([[0., 10., 30.]]),
                 MeasurementSpec("hot", "temperature", averaging_length=1.), cancel=cancel)
    assert len(checkpoints) == 2


def test_uniform_hotspot_returns_a_valid_equivalent_center():
    from shapely.geometry import Point

    patches = [Polygon([[0., 0.], [10., 0.], [10., 10.]]),
               Polygon([[0., 0.], [10., 10.], [0., 10.]])]
    value, center = _hotspot(patches, np.full((2, 3), 3.),
                            MeasurementSpec("hot", "temperature", averaging_length=.8))
    assert value == pytest.approx(3., rel=1e-12)
    assert Polygon([[0., 0.], [10., 0.], [10., 10.], [0., 10.]]).covers(Point(center))


@pytest.mark.parametrize("left,right,expected", [([1, 3, 7], [2, 3, 7, 9], [3, 7]),
    ([7, 3, 3, 1], [9, 7, 3], [3, 7]), ([], [1], []), ([1], [], []), ([2, 5], [1, 3], [])])
def test_mesh_id_intersection_handles_unsorted_duplicate_and_empty_api_tags(left, right, expected):
    assert _intersect_ids(np.asarray(left, int), np.asarray(right, int)).tolist() == expected


def test_phase_timings_reset_with_run_and_restore_owning_context():
    with execution_context(ExecutionSettings(1, 1)):
        with phase_timer("outer"):
            np.linalg.solve(np.array([[2., -1.], [-1., 2.]]), np.ones(2))
        evidence = execution_provenance()
        assert evidence["timings_seconds"]["outer"] > 0
        with execution_context(ExecutionSettings(1, 1), reset_counts=True):
            assert execution_provenance()["timings_seconds"] == {}
        assert execution_provenance()["timings_seconds"] == evidence["timings_seconds"]
    assert execution_provenance()["timings_seconds"] == {}


def test_historical_timing_display_clears_prior_tooltip_and_identifies_cache(qapp):
    from PySide6.QtWidgets import QLabel

    from etlayout.gui.guided import GuidedWorkspace

    result = SimpleNamespace(execution={"timings_seconds": {"numerical": 2., "sparse_linear_solves": 1.}})
    workspace = SimpleNamespace(window=SimpleNamespace(result=result), job_timings=QLabel())
    GuidedWorkspace.refresh_job_timings(workspace)
    assert "Sparse linear solves: 1.00 s" in workspace.job_timings.toolTip()
    result.execution = {}
    GuidedWorkspace.refresh_job_timings(workspace)
    assert "No measured" in workspace.job_timings.text()
    assert workspace.job_timings.toolTip() == ""
    result.execution = {"timings_seconds": {"cached_result_load": .25, "total": .3}}
    GuidedWorkspace.refresh_job_timings(workspace)
    assert "Cache retrieval: 0.25 s" in workspace.job_timings.text()
    assert "Numerical:" not in workspace.job_timings.text()


@pytest.mark.numerical
def test_actual_run_timings_are_persisted_and_cached_retrieval_is_distinguished(tmp_path):
    project = make_example("E1", tmp_path / "inputs")
    result = run_project(project, use_cache=True, execution=ExecutionSettings(1, 1))
    measured = result.execution["timings_seconds"]
    assert result.converged
    assert all(measured[key] > 0 for key in ("preparation", "meshing", "numerical", "sparse_linear_solves", "total"))
    assert measured["sparse_linear_solves"] <= measured["numerical"] <= measured["total"]
    assert result.elapsed_seconds <= measured["total"]
    archive = tmp_path / "timed.etl"
    save_project(project, archive, result)
    _, restored = load_project(archive)
    assert restored.execution["timings_seconds"] == measured
    cached = run_project(project, use_cache=True, execution=ExecutionSettings(1, 1))
    assert cached.physics_hash == result.physics_hash
    assert "cached_result_load" in cached.execution["timings_seconds"]
    assert "numerical" not in cached.execution["timings_seconds"]
