# SPDX-License-Identifier: MPL-2.0
import numpy as np
import pytest
from shapely.geometry import Polygon

from etlayout.geometry.prepare import prepare_geometry
from etlayout.meshing import generate_mesh
from etlayout.models import (
    HeatSource,
    Interface,
    MeshSettings,
    PhysicalLayer,
    Project,
    RefinementRegion,
    RegionSpec,
    SimulationError,
    Terminal,
    ThermalBoundary,
)


def rect(x0, y0, x1, y1):
    return {"exterior": [[v * 1e-6 for v in p] for p in [[x0,y0],[x1,y0],[x1,y1],[x0,y1]]]}


def assert_conforming(mesh, geometry):
    for prefix, group in [("layer", geometry.layers), ("interface", geometry.interfaces),
                          ("terminal", geometry.terminals), ("thermal", geometry.thermal_boundaries),
                          ("heat", geometry.heat_sources), ("refinement", geometry.refinements)]:
        for key, shape in group.items():
            if shape.area == 0:
                continue
            elements = mesh.regions[prefix + ":" + key]
            assert mesh.areas[elements].sum() == pytest.approx(shape.area, rel=2e-8, abs=1e-27)
            for triangle in mesh.triangles[elements]:
                polygon = Polygon(mesh.points[triangle])
                assert polygon.difference(shape).area < max(shape.area * 1e-9, 1e-28)


def test_conforming_holes_interfaces_and_boundary_terminals():
    shape = rect(0,0,30,20)
    shape["holes"] = [rect(12,7,18,13)["exterior"]]
    project = Project(layers=[PhysicalLayer("a", "A", "cu", geometry=RegionSpec(polygons=[shape])),
                              PhysicalLayer("b", "B", "cu", z=2e-6, geometry=RegionSpec(polygons=[rect(0,0,10,20)]))],
                      interfaces=[Interface("via", "a", "b", RegionSpec(polygons=[rect(3,6,7,14)]), "specific")],
                      terminals=[Terminal("left", "a", RegionSpec(line=[[0., 3e-6],[0., 17e-6]]), "ground"),
                                 Terminal("right", "a", RegionSpec(polygons=[rect(27,4,30,16)]), "voltage", 1.)],
                      thermal_boundaries=[ThermalBoundary("sink", "b", RegionSpec(line=[[0.,0.],[10e-6,0.]]), "temperature",300.)],
                      heat_sources=[HeatSource("heater", "a", RegionSpec(polygons=[rect(20,3,25,7)]), "total",1.)],
                      mesh=MeshSettings(max_size=4e-6, min_size=.2e-6, contact_size=1e-6,
                                        refinements=[RefinementRegion("fine", RegionSpec(polygons=[rect(20,10,25,15)]), .6e-6)]))
    geometry = prepare_geometry(project)
    mesh = generate_mesh(project, geometry)
    assert_conforming(mesh, geometry)
    assert mesh.statistics["minimum_angle_deg"] > 5
    nodes = mesh.points[mesh.terminal_nodes["left"]]
    assert np.allclose(nodes[:,0], 0, atol=1e-15)
    assert nodes[:,1].min() == pytest.approx(3e-6)
    assert nodes[:,1].max() == pytest.approx(17e-6)
    assert len(mesh.boundary_nodes["sink"]) > 2


def test_mesh_point_contact_does_not_add_triangle_bridge():
    shape = RegionSpec(polygons=[rect(0,0,10,5), rect(10,5,15,10)])
    project = Project(layers=[PhysicalLayer("a", "A", "cu", geometry=shape)],
                      mesh=MeshSettings(max_size=3e-6, min_size=.2e-6, contact_size=1e-6))
    geometry = prepare_geometry(project)
    mesh = generate_mesh(project, geometry)
    assert_conforming(mesh, geometry)
    lower = mesh.centroids[:,0] < 10e-6
    nodes_a, nodes_b = np.unique(mesh.triangles[lower]), np.unique(mesh.triangles[~lower])
    assert len(np.intersect1d(nodes_a, nodes_b)) == 1


def test_budget_failure_is_actionable():
    project = Project(layers=[PhysicalLayer("a", "A", "cu", geometry=RegionSpec(polygons=[rect(0,0,100,100)]))],
                      mesh=MeshSettings(max_size=.5e-6,min_size=.1e-6,max_elements=10))
    with pytest.raises(SimulationError, match="budget"):
        generate_mesh(project, prepare_geometry(project))


def test_entered_and_database_roundoff_does_not_create_slivers():
    imported = RegionSpec(polygons=[rect(0,0,100,30)])
    entered = RegionSpec(polygons=[{"exterior": [[0,0],[10e-6,0],[10e-6,30e-6],[0,30e-6]]}])
    project = Project(layers=[PhysicalLayer("a", "A", "cu", geometry=imported)],
                      terminals=[Terminal("pad", "a", entered, "ground"),
                                 Terminal("drain", "a", RegionSpec(line=[[100e-6,0],[100e-6,30e-6]]), "voltage", 1)],
                      mesh=MeshSettings(max_size=7e-6, min_size=.5e-6, contact_size=2e-6))
    geometry = prepare_geometry(project)
    mesh = generate_mesh(project, geometry)
    assert_conforming(mesh, geometry)
    assert mesh.statistics["minimum_angle_deg"] > 20
    assert mesh.boundary_edges["terminal:drain"].shape[1] == 2


def test_embedded_floating_probe_and_point_touch_support():
    shape = RegionSpec(polygons=[rect(0,0,20,10), rect(20,10,30,20)])
    project = Project(layers=[PhysicalLayer("a", "A", "cu", geometry=shape)],
                      terminals=[Terminal("probe", "a", RegionSpec(line=[[8e-6,3e-6],[14e-6,7e-6]]), "floating"),
                                 Terminal("edge", "a", RegionSpec(line=[[20e-6,0],[20e-6,10e-6]]), "ground")],
                      mesh=MeshSettings(max_size=3e-6,min_size=.2e-6,contact_size=1e-6))
    geometry = prepare_geometry(project)
    mesh = generate_mesh(project, geometry)
    assert_conforming(mesh, geometry)
    assert len(mesh.boundary_edges["terminal:probe"]) > 2
    support = mesh.regions["terminal_support:edge"]
    assert np.all(mesh.centroids[support, 0] < 20e-6)
    edge_pairs = mesh.boundary_edges["terminal:edge"]
    assert np.sum(np.linalg.norm(np.diff(mesh.points[edge_pairs], axis=1)[:,0,:], axis=1)) == pytest.approx(10e-6)


def test_meshing_preserves_native_environment_and_borrowed_model(tmp_path):
    """A real E1 mesh must preserve long host paths and external Gmsh ownership."""
    import json
    import os
    import subprocess
    import sys
    import textwrap

    child = textwrap.dedent("""
        import ctypes
        import hashlib
        import json
        import os
        import subprocess
        import sys

        import gmsh
        import numpy as np

        from etlayout.examples import make_example
        from etlayout.geometry.prepare import prepare_geometry
        from etlayout.meshing import generate_mesh
        from etlayout.simulation.execution import ExecutionSettings, execution_context

        if os.name == 'nt':
            kernel = ctypes.WinDLL('kernel32', use_last_error=True)
            kernel.GetEnvironmentVariableW.argtypes = [ctypes.c_wchar_p, ctypes.c_wchar_p, ctypes.c_ulong]
            kernel.GetEnvironmentVariableW.restype = ctypes.c_ulong

            def native_environment(name):
                size = kernel.GetEnvironmentVariableW(name, None, 0)
                buffer = ctypes.create_unicode_buffer(size)
                assert 0 < kernel.GetEnvironmentVariableW(name, buffer, size) < size
                return buffer.value
        else:
            libc = ctypes.CDLL(None)
            libc.getenv.argtypes = [ctypes.c_char_p]
            libc.getenv.restype = ctypes.c_char_p

            def native_environment(name):
                return os.fsdecode(libc.getenv(os.fsencode(name)))

        expected = {name: os.environ[name] for name in ('PATH', 'PYTHONPATH')}
        assert all(len(value) > 512 for value in expected.values())
        assert all(native_environment(name) == value for name, value in expected.items())
        assert subprocess.check_output(['git', '--version'], text=True).startswith('git version')
        borrowed = sys.argv[1] == 'borrowed'
        assert not gmsh.isInitialized()
        if borrowed:
            gmsh.initialize(['-noenv'], readConfigFiles=False, interruptible=False)
            gmsh.model.add('external-owner')
            point = gmsh.model.geo.addPoint(0, 0, 0)
            gmsh.model.geo.synchronize()
        project = make_example('E1', sys.argv[2])
        geometry = prepare_geometry(project)
        with execution_context(ExecutionSettings(1, 1)):
            mesh = generate_mesh(project, geometry)
        assert len(mesh.points) > 3 and len(mesh.triangles) > 1
        assert np.isclose(mesh.areas.sum(), 100e-6 * 10e-6, rtol=1e-12, atol=0)
        assert all(native_environment(name) == value == os.environ[name] for name, value in expected.items())
        assert subprocess.check_output(['git', '--version'], text=True).startswith('git version')
        assert bool(gmsh.isInitialized()) == borrowed
        if borrowed:
            assert gmsh.model.getCurrent() == 'external-owner'
            assert gmsh.model.getEntities(0) == [(0, point)]
            gmsh.finalize()
        assert all(native_environment(name) == value for name, value in expected.items())
        digest = hashlib.sha256()
        arrays = {'points': mesh.points, 'triangles': mesh.triangles, 'areas': mesh.areas}
        for field in ('regions', 'terminal_nodes', 'boundary_nodes', 'boundary_edges'):
            arrays.update({field + ':' + name: array for name, array in getattr(mesh, field).items()})
        for name, array in sorted(arrays.items()):
            digest.update(name.encode())
            digest.update(str((array.shape, array.dtype)).encode())
            digest.update(np.ascontiguousarray(array).tobytes())
        print(json.dumps({'mesh_sha256': digest.hexdigest(), 'nodes': len(mesh.points), 'triangles': len(mesh.triangles)}))
    """)
    environment = os.environ.copy()
    # Nonexistent entries force the Windows API past MAX_PATH without creating
    # directories or relying on a particular developer/runner installation path.
    prefix = os.pathsep.join(str(tmp_path / ('uncreated-environment-entry-' + str(i))) for i in range(12))
    for name in ('PATH', 'PYTHONPATH'):
        environment[name] = prefix + os.pathsep + environment.get(name, '')
    results = []
    for ownership in ('owned', 'borrowed'):
        completed = subprocess.run([sys.executable, '-c', child, ownership, str(tmp_path / ownership)],
                                   env=environment, text=True, encoding='utf-8', capture_output=True, timeout=60)
        assert completed.returncode == 0, completed.stderr
        results.append(json.loads(completed.stdout.strip().splitlines()[-1]))
    assert results[0] == results[1]
