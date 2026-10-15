# SPDX-License-Identifier: MPL-2.0
"""Independent structured test meshes; solver tests do not require Gmsh."""
import numpy as np

from etlayout.models import Material, PhysicalLayer, PlanarMesh, Project, PropertyModel, Terminal


def rectangle(length=10e-6, width=2e-6, nx=12, ny=4, layers=("metal",)):
    points = np.array([(x, y) for y in np.linspace(0, width, ny + 1)
                       for x in np.linspace(0, length, nx + 1)])
    triangles = []
    for j in range(ny):
        for i in range(nx):
            a = j * (nx + 1) + i
            triangles.extend([[a, a + 1, a + nx + 2], [a, a + nx + 2, a + nx + 1]])
    triangles = np.asarray(triangles, dtype=int)
    elements = np.arange(len(triangles), dtype=int)
    mesh = PlanarMesh(points, triangles, {f"layer:{key}": elements for key in layers})
    mesh.terminal_nodes = {"left": np.flatnonzero(points[:, 0] == 0),
                           "right": np.flatnonzero(points[:, 0] == length)}
    material = Material("copper", "Test copper", resistivity=PropertyModel(value=2e-8),
                        thermal_conductivity=PropertyModel(value=10.))
    project = Project(materials={"copper": material},
                      layers=[PhysicalLayer(key, key, "copper", thickness=.5e-6) for key in layers])
    project.terminals = [Terminal("left", layers[0], kind="ground"),
                         Terminal("right", layers[0], kind="voltage", value=1.)]
    return project, mesh


def all_region(mesh, prefix, identifier):
    mesh.regions[f"{prefix}:{identifier}"] = np.arange(len(mesh.triangles), dtype=int)
