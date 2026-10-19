# SPDX-License-Identifier: MPL-2.0
"""Independent reference networks, exact units and partial physical supports."""

import numpy as np
import pytest
from shapely.geometry import Polygon, box
from solver_helpers import rectangle

from etlayout.geometry.prepare import prepare_geometry
from etlayout.models import SimulationError
from etlayout.power.crowding import corner_features, local_crowding
from etlayout.simulation.runner import run_project
from etlayout.solvers import solve_electrical
from etlayout.validation.crowding import CASES, benchmark_project, exact_resistance, finite_volume_reference


@pytest.mark.fast
def test_oriented_corner_and_hole_diagnostics():
    shape = Polygon([(0, 0), (3, 0), (3, 1), (1, 1), (1, 3), (0, 3)])
    features = corner_features(shape)
    assert sum(f["kind"] == "sharp_reentrant" for f in features) == 1
    assert next(f for f in features if f["kind"] == "sharp_reentrant")["interior_angle_deg"] == 270
    assert (
        len(
            [
                f
                for f in corner_features(box(0, 0, 4, 4).difference(box(1, 1, 2, 2)))
                if f["kind"] == "sharp_reentrant"
            ]
        )
        == 4
    )
    rounded = corner_features(prepare_geometry(benchmark_project("rounded_L")).layers["metal"])
    assert any(f["inferred_radius_m"] == pytest.approx(2e-6) for f in rounded if f["inferred_radius_m"])
    assert not any(f["physical_radius_verified"] for f in rounded)
    assert not any(f["kind"] == "sharp_reentrant" for f in rounded)


@pytest.mark.fast
def test_partial_support_exact_field_heat_and_anisotropic_units():
    project, mesh = rectangle()
    project.materials["copper"].electrical_anisotropy = [2.0, 0.25]
    result = solve_electrical(project, mesh)
    support = box(1.23e-6, 0.37e-6, 7.89e-6, 1.71e-6)
    record = local_crowding(project, result, "metal", support=support, reference_sheet_current=5e6)
    assert record["area_m2"] == pytest.approx(support.area, rel=1e-12)
    assert record["sheet_current_average_A_per_m"] == pytest.approx(5e6, rel=1e-12)
    assert record["raw_volume_current_max_A_per_m2"] == pytest.approx(1e13, rel=1e-12)
    assert record["joule_power_W"] == pytest.approx(5e11 * support.area, rel=1e-12)
    assert record["crowding_factor"] == pytest.approx(1.0, rel=1e-12)
    assert record["raw_peak_status"] == "mesh_unverified"
    for reference in (0.0, -1.0, np.nan):
        with pytest.raises(SimulationError, match="Reference"):
            local_crowding(project, result, "metal", reference_sheet_current=reference)
    with pytest.raises(SimulationError, match="positive-area"):
        local_crowding(project, result, "metal", support=box(1, 1, 2, 2))


@pytest.mark.numerical
@pytest.mark.parametrize("case", CASES)
def test_crowding_conservation_against_independent_finite_volume(case):
    project = benchmark_project(case, 4e-6)
    result = run_project(project)
    reference = finite_volume_reference(project, 0.5e-6)
    assert result.converged
    # Independent staircase-boundary reference. This is an integral verification
    # budget, never a claim of peak convergence or a changed PDE tolerance.
    assert result.metrics["effective_resistance"] == pytest.approx(reference["resistance_ohm"], rel=0.045)
    exact = exact_resistance(case)
    if exact:
        assert result.metrics["effective_resistance"] == pytest.approx(exact, rel=0.002)
    assert result.metrics["power_balance_error"] < project.solver.power_balance_tolerance
    assert result.metrics["thermal_balance_error"] < project.solver.thermal_balance_tolerance
    deposited = sum(
        np.nansum((f["joule_density"] + f["contact_density"]) * result.mesh.areas)
        for f in result.fields.values()
        if "joule_density" in f
    )
    assert deposited == pytest.approx(result.metrics["electrical_power"], rel=1e-8)
    record = result.analysis["current_crowding"]["metal"]
    assert record["raw_peak_status"] in {"mesh_unverified", "singular_peak_unverified"}
    assert all(v == "mesh_unverified" for v in record["metric_verification"].values())


@pytest.mark.numerical
def test_three_meshes_require_identical_physics_and_singular_peaks_stay_unverified():
    from etlayout.power.crowding import verify_mesh_sequence

    project = benchmark_project("sharp_L")
    results = [run_project(benchmark_project("sharp_L", h)) for h in (4e-6, 2e-6, 1e-6)]
    support = box(16e-6, 16e-6, 26e-6, 26e-6)
    record = verify_mesh_sequence(project, results, "metal", support)
    assert record["metrics"]["raw_sheet_current_max"]["status"] == "singular_peak_unverified"
    assert record["metrics"]["joule_power"]["status"] == "mesh_converged"
    assert not any(v["physical_qualification"] for v in record["metrics"].values())
    with pytest.raises(SimulationError, match="three"):
        verify_mesh_sequence(project, results[:2], "metal", support)
    results[-1].configuration["terminals"][0]["value"] *= 2
    with pytest.raises(SimulationError, match="same physical"):
        verify_mesh_sequence(project, results, "metal", support)


@pytest.mark.numerical
def test_annular_bend_local_field_against_exact_harmonic_solution():
    from etlayout.models import PlanarMesh, Terminal

    project, _ = rectangle()
    inner, outer, angle = 5e-6, 25e-6, np.pi / 2
    nr, nt = 512, 128
    points = np.array(
        [
            [r * np.cos(t), r * np.sin(t)]
            for r in np.linspace(inner, outer, nr + 1)
            for t in np.linspace(0, angle, nt + 1)
        ]
    )
    triangles = []
    for i in range(nr):
        for j in range(nt):
            a = i * (nt + 1) + j
            triangles.extend([[a, a + nt + 1, a + nt + 2], [a, a + nt + 2, a + 1]])
    triangles = np.array(triangles)
    mesh = PlanarMesh(
        points,
        triangles,
        {"layer:metal": np.arange(len(triangles))},
        terminal_nodes={
            "start": np.arange(0, len(points), nt + 1),
            "end": np.arange(nt, len(points), nt + 1),
        },
    )
    project.terminals = [Terminal("start", "metal", kind="ground"), Terminal("end", "metal", value=0.01)]
    result = solve_electrical(project, mesh)
    gs = project.layers[0].thickness / 2e-8
    exact_r = angle / (gs * np.log(outer / inner))
    assert result.metrics["effective_resistance"] == pytest.approx(exact_r, rel=0.001)
    centers = mesh.centroids
    radius = np.linalg.norm(centers, axis=1)
    expected = gs * 0.01 / (angle * radius)
    measured = np.linalg.norm(result.fields["metal"]["sheet_current"], axis=1)
    assert np.average(abs(measured / expected - 1), weights=mesh.areas) < 0.001
    assert result.metrics["joule_power"] == pytest.approx(0.01**2 / exact_r, rel=0.001)
    assert result.converged


@pytest.mark.numerical
@pytest.mark.parametrize("archive_format", [2, 3])
def test_crowding_complete_typed_archive_identity(tmp_path, archive_format):
    import importlib.util
    from pathlib import Path

    from etlayout.io.project import load_project, save_project

    spec = importlib.util.spec_from_file_location(
        "crowding_archive_benchmark",
        Path(__file__).resolve().parents[1] / "scripts/benchmark_archive_history.py")
    benchmark = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(benchmark)
    project = benchmark_project("sharp_L", 4e-6)
    result = run_project(project)
    assert isinstance(result.analysis["current_crowding"]["metal"]["peak_xy"], list)
    path = tmp_path / ("crowding-" + str(archive_format) + ".etl")
    before = benchmark.result_digest(result)
    save_project(project, path, result, archive_format=archive_format)
    _, restored = load_project(path)
    assert benchmark.result_digest(restored) == before


@pytest.mark.fast
def test_nearby_circular_hole_cannot_round_an_unchanged_sharp_exterior():
    from shapely.geometry import Point

    exterior = Polygon(np.array([(-5, -5), (5, -5), (5, 1), (0, 0), (-5, 1)]) * 1e-6)
    with_hole = exterior.difference(Point(0., -1.1e-6).buffer(1e-6, quad_segs=16))
    assert with_hole.is_valid and len(with_hole.interiors) == 1
    assert exterior.exterior.equals(with_hole.exterior)
    for shape in (exterior, with_hole):
        tip = next(f for f in corner_features(shape) if f["xy"] == [0., 0.])
        assert tip["interior_angle_deg"] == pytest.approx(202.61986494804043)
        assert tip["kind"] == "sharp_reentrant"
        assert tip["inferred_radius_m"] is None


@pytest.mark.fast
def test_sharp_kink_adjacent_to_circular_facets_is_not_a_tangent_join():
    from shapely.geometry import Point

    hole = list(Point(0., 0.).buffer(1e-6, quad_segs=16).exterior.coords)
    hole[-2] = (9.667459675604486e-7, 9.232938850721083e-8)
    shape = Polygon(box(-2e-6, -2e-6, 2e-6, 2e-6).exterior.coords, [hole])
    assert shape.is_valid
    tip = next(f for f in corner_features(shape) if f["xy"] == [1e-6, 0.])
    assert tip["interior_angle_deg"] == pytest.approx(202.61986494804043)
    assert tip["kind"] == "sharp_reentrant"
    assert tip["inferred_radius_m"] is None
