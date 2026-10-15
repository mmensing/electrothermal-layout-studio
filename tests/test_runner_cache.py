# SPDX-License-Identifier: MPL-2.0
"""Cache behavior uses real geometry preparation with an independent small mesh."""
from copy import deepcopy
from importlib import import_module

import numpy as np
import pytest
from shapely.geometry import box

from etlayout.io.project import configuration_hash, file_hash
from etlayout.models import Diagnostic, RegionSpec, SimulationError
from etlayout.simulation import runner
from tests.solver_helpers import rectangle


@pytest.fixture
def cached_project(monkeypatch):
    runner.clear_caches()
    project, mesh = rectangle()
    project.layers[0].geometry = RegionSpec(polygons=[{"bounds": [0., 0., 10e-6, 2e-6]}])
    project.terminals[0].geometry = RegionSpec(line=[[0., 0.], [0., 2e-6]])
    project.terminals[1].geometry = RegionSpec(line=[[10e-6, 0.], [10e-6, 2e-6]])
    geometry_module = import_module("etlayout.geometry.prepare")
    mesh_module = import_module("etlayout.meshing.generate")
    original = geometry_module.prepare_geometry
    calls = {"geometry": 0, "mesh": 0}

    def prepare(p, **kwargs):
        calls["geometry"] += 1
        return original(p, **kwargs)

    def generate(*args, **kwargs):
        calls["mesh"] += 1
        return deepcopy(mesh)

    monkeypatch.setattr(geometry_module, "prepare_geometry", prepare)
    monkeypatch.setattr(mesh_module, "generate_mesh", generate)
    yield project, calls
    runner.clear_caches()


def test_bias_and_material_reuse_but_validation_still_runs(cached_project):
    project, calls = cached_project
    runner.prepare_mesh(project)
    project.terminals[1].value = .25
    project.materials["copper"].resistivity.value *= 2
    runner.prepare_mesh(project)
    assert calls == {"geometry": 1, "mesh": 1}
    project.terminals[1].value = float("nan")
    with pytest.raises(SimulationError, match="Terminal value must be finite"):
        runner.prepare_mesh(project)
    assert calls == {"geometry": 1, "mesh": 1}


def test_roi_terminal_shape_and_stack_diagnostic_invalidation(cached_project):
    project, calls = cached_project
    runner.prepare_mesh(project)
    project.roi = RegionSpec(polygons=[{"bounds": [-1e-6, -1e-6, 11e-6, 3e-6]}])
    runner.prepare_mesh(project)
    assert calls == {"geometry": 2, "mesh": 2}
    project.terminals[1].geometry.line[1][1] = 1e-6
    runner.prepare_mesh(project)
    assert calls == {"geometry": 3, "mesh": 3}
    project.layers[0].thickness *= 2
    runner.prepare_mesh(project)
    assert calls == {"geometry": 4, "mesh": 3}
    project.mesh.min_size *= .5
    runner.prepare_mesh(project)
    assert calls == {"geometry": 5, "mesh": 4}


def test_source_content_identity_and_database_units(cached_project, tmp_path):
    import klayout.db as kdb

    project, calls = cached_project
    source = tmp_path / "layout.gds"
    layout = kdb.Layout()
    layout.dbu = .002
    top = layout.create_cell("TOP")
    top.shapes(layout.layer(1, 0)).insert(kdb.Box(0, 0, 5000, 1000))
    layout.write(str(source))
    project.layout.path = str(source)
    project.layout.cell = "TOP"
    runner.prepare_mesh(project)
    first_digest = project.layout.sha256
    assert project.layout.dbu_um == .002
    project.layout.dbu_um = .001  # metadata is refreshed even when geometry is reused
    runner.prepare_mesh(project)
    assert project.layout.dbu_um == .002
    assert calls == {"geometry": 1, "mesh": 1}
    project.layout.dbu_um = .001
    result = runner.run_project(project)
    assert result.converged
    assert result.configuration["layout"]["dbu_um"] == .002
    assert result.configuration_hash == configuration_hash(result.configuration)
    top.shapes(layout.layer(2, 0)).insert(kdb.Box(1000, 100, 2000, 200))
    layout.write(str(source))
    with pytest.raises(SimulationError, match="Layout content changed"):
        runner.prepare_mesh(project)
    project.layout.sha256 = file_hash(source)
    assert project.layout.sha256 != first_digest
    runner.prepare_mesh(project)
    assert calls == {"geometry": 2, "mesh": 2}


def test_geometry_specific_validation_is_not_hidden(cached_project):
    project, calls = cached_project
    project.terminals[1].geometry.line = [[5e-6, 0.], [5e-6, 2e-6]]
    runner.prepare_mesh(project)  # internal equipotential electrode is valid
    project.terminals[1].kind = "density"
    with pytest.raises(SimulationError, match="must.*boundary"):
        runner.prepare_mesh(project)
    assert calls == {"geometry": 2, "mesh": 1}


def test_thickness_updates_volume_overlap_diagnostics(cached_project):
    project, calls = cached_project
    upper = deepcopy(project.layers[0])
    upper.id, upper.name, upper.z, upper.role = "upper", "Upper", 1e-6, "thermal"
    project.layers.append(upper)
    first, _ = runner.prepare_mesh(project)
    assert not any("Physical volumes" in d.message for d in first.diagnostics)
    project.layers[0].thickness = 2e-6
    changed, _ = runner.prepare_mesh(project)
    assert any("Physical volumes" in d.message for d in changed.diagnostics)
    assert calls == {"geometry": 2, "mesh": 1}


def test_returned_objects_cannot_mutate_retained_caches(cached_project):
    project, calls = cached_project
    geometry, mesh = runner.prepare_mesh(project)
    area = geometry.layers["metal"].area
    points, triangles = mesh.points.copy(), mesh.triangles.copy()
    geometry.layers["metal"] = box(0., 0., 1., 1.)
    geometry.diagnostics.append(Diagnostic("error", "caller mutation"))
    mesh.points[:] = 0.
    mesh.triangles[:] = 0
    mesh.regions.clear()
    restored_geometry, restored_mesh = runner.prepare_mesh(project)
    assert restored_geometry.layers["metal"].area == area
    assert all(d.message != "caller mutation" for d in restored_geometry.diagnostics)
    np.testing.assert_array_equal(restored_mesh.points, points)
    np.testing.assert_array_equal(restored_mesh.triangles, triangles)
    assert "layer:metal" in restored_mesh.regions
    restored_geometry.layers.clear()
    restored_mesh.points[:] = 99.
    again, next_mesh = runner.prepare_mesh(project)
    assert again.layers["metal"].area == area
    np.testing.assert_array_equal(next_mesh.points, points)
    assert calls == {"geometry": 1, "mesh": 1}


def test_lru_bounds_and_clear(cached_project, monkeypatch):
    project, calls = cached_project
    runner.prepare_mesh(project)
    size = runner.cache_info()["geometry_bytes"]
    monkeypatch.setattr(runner, "_GEOMETRY_CACHE_BUDGET", 2 * size)
    for index in range(5):
        project.layers[0].name = f"Geometry {index}"
        runner.prepare_mesh(project)
    info = runner.cache_info()
    assert 0 < info["geometry_entries"] <= 2
    assert info["geometry_bytes"] <= info["geometry_budget"]
    assert calls["mesh"] == 1
    previous = calls["geometry"]
    runner.prepare_mesh(project)  # most recent entry survived eviction
    assert calls["geometry"] == previous
    project.layers[0].name = "metal"  # original entry was evicted
    runner.prepare_mesh(project)
    assert calls["geometry"] == previous + 1
    runner.clear_caches()
    assert runner.cache_info()["geometry_entries"] == runner.cache_info()["mesh_entries"] == 0
    monkeypatch.setattr(runner, "_GEOMETRY_CACHE_BUDGET", 1)
    monkeypatch.setattr(runner, "_CACHE_BUDGET", 1)
    runner.prepare_mesh(project)
    assert runner.cache_info()["geometry_entries"] == runner.cache_info()["mesh_entries"] == 0
