# SPDX-License-Identifier: MPL-2.0
"""Independent disconnected P1 states survive every numerical consumer."""
import json

import meshio
import numpy as np
import pytest

from etlayout.io.export import export_results
from etlayout.io.project import load_project, save_project
from etlayout.models import (
    Material,
    PhysicalLayer,
    PlanarMesh,
    Project,
    SimulationError,
    Terminal,
    ThermalBoundary,
)
from etlayout.simulation.analysis import sample_points
from etlayout.solvers import solve_electrical, solve_thermal
from etlayout.visualization.difference import make_field_difference
from etlayout.visualization.sampling import FieldSampler
from etlayout.visualization.support import field_support


def triangles():
    mesh = PlanarMesh(np.array([[0., 0.], [1., 0.], [0., 1.], [-1., 0.], [0., -1.]]),
                      np.array([[0, 1, 2], [0, 3, 4]]),
                      {"layer:metal": np.array([0, 1]), "terminal:a": np.array([0]), "terminal:b": np.array([1]),
                       "thermal:cold": np.array([0]), "thermal:hot": np.array([1])})
    project = Project(materials={"metal": Material("metal", "metal")},
                       layers=[PhysicalLayer("metal", "Metal", "metal")],
                       terminals=[Terminal("a", "metal", kind="ground"), Terminal("b", "metal", value=1.)],
                       thermal_boundaries=[ThermalBoundary("cold", "metal", kind="temperature", value=300.),
                                           ThermalBoundary("hot", "metal", kind="temperature", value=400.)])
    return project, mesh


@pytest.mark.parametrize("thermal", [False, True])
def test_point_touch_local_probe_profile_archive_and_export(tmp_path, thermal):
    project, mesh = triangles()
    project.solver.mode = "thermal" if thermal else "electrical"
    result = (solve_thermal if thermal else solve_electrical)(project, mesh)
    field = "temperature" if thermal else "potential"
    expected = [300., 400.] if thermal else [0., 1.]
    coordinates = np.array([[1/3, 1/3], [-1/3, -1/3]])
    assert sample_points(result, "metal", field, coordinates) == pytest.approx(expected)
    sampler = FieldSampler(result)
    records = sampler.sample_with_status("metal", field, [[0, 0], [2, 2]])
    assert records[0]["status"] == "ambiguous" and len(records[0]["values"]) == 2
    assert records[1]["status"] == "outside_or_unavailable"
    assert np.isnan(sampler.sample("metal", field, [[0, 0]])[0])
    assert sampler.sample("metal", field, [[0, 0]], region="component:2")[0] == expected[1]
    _, values, _ = sampler.profile("metal", field, [0, 0], [1/3, 1/3], 3, region="component:1")
    assert values == pytest.approx(np.full(3, expected[0]))
    if not thermal:
        assert result.metrics["joule_power"] == pytest.approx(0, abs=1e-12)
        assert all(abs(v["current"]) < 1e-12 for v in result.terminals.values())
    archive = tmp_path / "state.etl"
    save_project(project, archive, result)
    migrated, restored = load_project(archive)
    assert migrated.schema_version == 2
    np.testing.assert_array_equal(restored.element_fields["metal"][field], result.element_fields["metal"][field])
    assert sample_points(restored, "metal", field, coordinates) == pytest.approx(expected)
    folder = tmp_path / "export"
    export_results(restored, folder, plots=True, layers=["metal"], field=field)
    manifest = json.loads((folder / "manifest.json").read_text())
    vtu = meshio.read(folder / manifest["layers"]["metal"]["vtu"])
    zero = np.linalg.norm(vtu.points, axis=1) == 0
    assert sorted(vtu.point_data[field][zero]) == expected
    support = field_support(restored, "metal", field)
    assert support.limits == tuple(expected)
    assert support.element_data[0] == pytest.approx(np.full(3, expected[0]))


def test_local_difference_preserves_two_independent_components():
    project, mesh = triangles()
    first = solve_electrical(project, mesh)
    project.terminals[1].value = 3
    second = solve_electrical(project, mesh)
    difference = make_field_difference(second, first, "metal", "potential")
    assert sample_points(difference, "metal", "potential", [[.2, .2], [-.2, -.2]]) == pytest.approx([0., 2.])


@pytest.mark.parametrize("thermal", [False, True])
def test_remeshed_difference_retains_independent_point_touch_limits(thermal):
    project, coarse = triangles()
    solve = solve_thermal if thermal else solve_electrical
    reference = solve(project, coarse)
    # Split each independent triangle around its own centroid. Their only common
    # coordinate remains the origin, which has two physically different limits.
    points = np.vstack((coarse.points, coarse.centroids))
    elements = np.array([[0, 1, 5], [1, 2, 5], [2, 0, 5],
                         [0, 3, 6], [3, 4, 6], [4, 0, 6]])
    refined = PlanarMesh(points, elements, {
        "layer:metal": np.arange(6),
        "terminal:a": np.arange(3), "terminal:b": np.arange(3, 6),
        "thermal:cold": np.arange(3), "thermal:hot": np.arange(3, 6),
    })
    if thermal:
        project.thermal_boundaries[0].value = 310.
        project.thermal_boundaries[1].value = 420.
        field, expected = "temperature", [10., 20.]
    else:
        project.terminals[1].value = 3.
        field, expected = "potential", [0., 2.]
    current = solve(project, refined)
    difference = make_field_difference(current, reference, "metal", field)
    np.testing.assert_allclose(difference.element_fields["metal"][field][:3], expected[0], atol=1e-10)
    np.testing.assert_allclose(difference.element_fields["metal"][field][3:], expected[1], atol=1e-10)
    assert sample_points(difference, "metal", field, [[.2, .2], [-.2, -.2]]) == pytest.approx(expected)
    boundary = FieldSampler(difference).sample_with_status("metal", field, [[0., 0.]])[0]
    assert boundary["status"] == "ambiguous"
    assert [item["value"] for item in boundary["values"]] == pytest.approx(expected)


def test_legacy_averaged_values_are_diagnosed_and_unavailable(tmp_path):
    project, mesh = triangles()
    result = solve_electrical(project, mesh)
    result.element_fields = {}
    result.field_version = 1
    target = tmp_path / "legacy.etl"
    save_project(project, target, result)
    _, loaded = load_project(target)
    assert any("recompute" in d.message for d in loaded.diagnostics)
    assert loaded.analysis["legacy_ambiguous_layers"] == ["metal"]
    assert np.isnan(sample_points(loaded, "metal", "potential", [[.2, .2], [-.2, -.2]])).all()


def test_authoritative_local_array_validation_rejects_bad_shape_or_nan(tmp_path):
    project, mesh = triangles()
    result = solve_electrical(project, mesh)
    for bad in (np.zeros((2, 2)), np.full((2, 3), np.nan)):
        result.element_fields["metal"]["potential"] = bad
        with pytest.raises(SimulationError, match="local"):
            save_project(project, tmp_path/"bad.etl", result)


def test_version_one_real_transient_fixture_keeps_physics_identity():
    from pathlib import Path
    project, result = load_project(Path(__file__).parent/"fixtures"/"legacy_transient_e18.etl")
    assert project.schema_version == 2 and result.transient.completed
    assert result.field_version == 1
    assert not result.analysis.get("legacy_ambiguous_layers")
    assert result.transient.snapshots[-1].field_version == 1
