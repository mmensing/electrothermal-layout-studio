# SPDX-License-Identifier: MPL-2.0
"""Crowding fixtures and independent Cartesian finite-volume references.

The reference has no dependency on ETLayout assembly, meshing or solvers. Its
staircase boundary error is measured by a separate grid sequence; it is not an
analytical oracle. Strip and finite-contact cases additionally have exact limits.
"""

from __future__ import annotations

import numpy as np
from scipy.sparse import coo_matrix
from scipy.sparse.linalg import spsolve
from shapely import contains_xy
from shapely.affinity import scale
from shapely.geometry import LineString, Point, Polygon, box

from etlayout.geometry.prepare import geometry_region
from etlayout.models import (
    Interface,
    Material,
    MeshSettings,
    PhysicalLayer,
    Project,
    PropertyModel,
    Terminal,
    ThermalBoundary,
)

CASES = (
    "strip",
    "abrupt_neck",
    "sharp_L",
    "rounded_L",
    "dogbone",
    "taper",
    "slot",
    "finite_contact",
    "via_landing",
    "runner_pad",
)


def _si(shape):
    return scale(shape, xfact=1e-6, yfact=1e-6, origin=(0, 0))


def benchmark_project(case, mesh_size=2e-6, *, radius=2e-6):
    """Synthetic fixed-temperature properties, finite physical heat sink.

    2 µm metal, rho=2e-8 ohm m, k=200 W/mK. Backside area resistance 1e-5
    m²K/W is a declared effective boundary, not a calibrated device stack.
    """
    if case not in CASES:
        raise ValueError(f"Unknown crowding benchmark {case}")
    shape = box(0, 0, 80, 40)
    left, right = LineString([(0, 0), (0, 40)]), LineString([(80, 0), (80, 40)])
    if case == "abrupt_neck":
        shape = box(0, 0, 40, 40).union(box(40, 15, 80, 25))
        right = LineString([(80, 15), (80, 25)])
    elif case in {"sharp_L", "rounded_L"}:
        shape = box(0, 0, 60, 20).union(box(0, 0, 20, 60))
        left, right = LineString([(0, 60), (20, 60)]), LineString([(60, 0), (60, 20)])
        if case == "rounded_L":
            r = radius * 1e6
            shape = shape.union(
                box(20, 20, 20 + r, 20 + r).difference(Point(20 + r, 20 + r).buffer(r, quad_segs=12))
            )
    elif case == "dogbone":
        shape = box(0, 0, 25, 40).union(box(25, 15, 55, 25)).union(box(55, 0, 80, 40))
    elif case == "taper":
        shape = Polygon([(0, 0), (20, 0), (60, 15), (80, 15), (80, 25), (60, 25), (20, 40), (0, 40)])
        right = LineString([(80, 15), (80, 25)])
    elif case == "slot":
        shape = shape.difference(box(36, 8, 44, 32))
    elif case == "runner_pad":
        shape = box(0, 15, 45, 25).union(box(45, 0, 80, 40))
        left = LineString([(0, 15), (0, 25)])
    material = Material(
        "metal",
        "Synthetic constant conductor",
        resistivity=PropertyModel(value=2e-8),
        thermal_conductivity=PropertyModel(value=200.0),
    )
    project = Project(
        name="Crowding reference: " + case,
        materials={"metal": material},
        layers=[PhysicalLayer("metal", "Conductor", "metal", 2e-6, geometry=geometry_region(_si(shape)))],
        terminals=[
            Terminal("source", "metal", geometry_region(_si(left)), "voltage", 0.01),
            Terminal("return", "metal", geometry_region(_si(right)), "ground"),
        ],
        mesh=MeshSettings(
            max_size=mesh_size,
            min_size=mesh_size / 4,
            contact_size=mesh_size,
            max_elements=200000,
            max_dofs=400000,
        ),
    )
    if case in {"finite_contact", "via_landing"}:
        if case == "finite_contact":
            shape = box(0, 0, 80, 20)
            upper = contact = box(0, 0, 16, 20)
            source = upper
        else:
            upper = shape
            contact = box(36, 8, 44, 32)
            source = box(0, 0, 8, 40)
        project.layers[0].geometry = geometry_region(_si(shape))
        project.layers.append(
            PhysicalLayer(
                "upper", "Injection metal", "metal", 2e-6, z=4e-6, geometry=geometry_region(_si(upper))
            )
        )
        project.terminals = [
            Terminal("source", "upper", geometry_region(_si(source)), "voltage", 0.01),
            Terminal(
                "return",
                "metal",
                geometry_region(_si(LineString([(80, 0), (80, shape.bounds[3])]))),
                "ground",
            ),
        ]
        # Finite specific contact enables an exact TLM limit. The via's bulk
        # rho*height gives the same units without claiming an ideal connection.
        project.interfaces = [
            Interface(
                "transfer",
                "upper",
                "metal",
                geometry_region(_si(contact)),
                "specific" if case == "finite_contact" else "via",
                1e-11,
                thermal_kind="conductance",
                thermal_value=1e7,
                material_id="metal",
                height=2e-6,
            )
        ]
    project.thermal_boundaries = [
        ThermalBoundary("reservoir", "metal", project.layers[0].geometry, "resistance_area", 1e-5, 300.0)
    ]
    project.solver.mode = "electrothermal"
    project.solver.relaxation = 1.0
    return project


def exact_resistance(case):
    if case == "strip":
        return 2e-8 * 80e-6 / (40e-6 * 2e-6)
    if case == "finite_contact":
        rs, rho_c, width, length, contact_length = 0.01, 1e-11, 20e-6, 80e-6, 16e-6
        transfer_length = np.sqrt(rho_c / rs)
        return (
            rs * (length - contact_length) + np.sqrt(rs * rho_c) / np.tanh(contact_length / transfer_length)
        ) / width
    return None


def finite_volume_reference(project, spacing=1e-6):
    """Cell-centred five-point resistor network with finite interface coupling.

    Half-cell Dirichlet face resistances preserve exact rectangular strip length.
    Staircase boundaries remain an explicit independent discretization error.
    Constant isotropic material values only; invalid inputs are rejected.
    """
    from etlayout.geometry.prepare import resolve_region

    layers = {layer.id: resolve_region(layer.geometry, {}) for layer in project.layers}
    bounds = np.array([shape.bounds for shape in layers.values()])
    x0, y0 = bounds[:, :2].min(axis=0)
    x1, y1 = bounds[:, 2:].max(axis=0)
    nx, ny = int(np.ceil((x1 - x0) / spacing)), int(np.ceil((y1 - y0) / spacing))
    dx, dy = (x1 - x0) / nx, (y1 - y0) / ny
    xy = np.array([(x0 + (i + 0.5) * dx, y0 + (j + 0.5) * dy) for j in range(ny) for i in range(nx)])
    maps, nodes, conductance, count = {}, {}, {}, 0
    for layer in project.layers:
        if layer.electrical.kind != "bulk":
            raise ValueError("Reference supports constant isotropic bulk sheets only")
        material = project.materials[layer.material_id]
        if material.resistivity.kind != "constant" or material.electrical_anisotropy != [1.0, 1.0]:
            raise ValueError("Reference supports constant isotropic bulk sheets only")
        active = np.flatnonzero(contains_xy(layers[layer.id], xy[:, 0], xy[:, 1]))
        mapping = np.full(nx * ny, -1)
        mapping[active] = np.arange(len(active)) + count
        maps[layer.id] = mapping
        nodes[layer.id] = active
        count += len(active)
        conductance[layer.id] = layer.thickness / material.resistivity.value
    rows, cols, data = [], [], []

    def link(a, b, g):
        rows.extend([a, a, b, b])
        cols.extend([a, b, a, b])
        data.extend([g, -g, -g, g])

    for key, mapping in maps.items():
        for index in nodes[key]:
            i, j = index % nx, index // nx
            for other, g in (
                (index + 1, conductance[key] * dy / dx),
                (index + nx, conductance[key] * dx / dy),
            ):
                if (
                    other >= len(mapping)
                    or (other == index + 1 and i == nx - 1)
                    or (other == index + nx and j == ny - 1)
                    or mapping[other] < 0
                ):
                    continue
                link(int(mapping[index]), int(mapping[other]), g)
    for interface in project.interfaces:
        shape = resolve_region(interface.geometry, {})
        active = np.flatnonzero(contains_xy(shape, xy[:, 0], xy[:, 1]))
        rho_c = (
            interface.electrical_value
            if interface.electrical_kind == "specific"
            else (project.materials[interface.material_id].resistivity.value * interface.height)
        )
        for index in active:
            a, b = maps[interface.layer_a][index], maps[interface.layer_b][index]
            if min(a, b) >= 0:
                link(int(a), int(b), dx * dy / rho_c)
    matrix = coo_matrix((data, (rows, cols)), shape=(count, count)).tocsr()
    rhs = np.zeros(count)
    fixed = {}
    edges = []
    for terminal in project.terminals:
        shape = resolve_region(terminal.geometry, {})
        key = terminal.layer_id
        mapping = maps[key]
        for index in nodes[key]:
            p = Point(xy[index])
            dof = int(mapping[index])
            voltage = terminal.value
            if shape.area:
                if shape.covers(p):
                    fixed[dof] = voltage
            elif shape.distance(p) <= min(dx, dy) * 0.50001:
                # All benchmark edge electrodes are axis aligned.
                vertical = abs(shape.bounds[2] - shape.bounds[0]) < 1e-15
                g = 2 * conductance[key] * (dy / dx if vertical else dx / dy)
                edges.append((terminal.id, dof, g, voltage))
    diagonal = np.zeros(count)
    for _, dof, g, value in edges:
        diagonal[dof] += g
        rhs[dof] += g * value
    matrix = (
        matrix + coo_matrix((diagonal, (np.arange(count), np.arange(count))), shape=(count, count)).tocsr()
    )
    free = np.setdiff1d(np.arange(count), list(fixed))
    values = np.zeros(count)
    for dof, value in fixed.items():
        values[dof] = value
    values[free] = spsolve(matrix[free][:, free], rhs[free] - matrix[free] @ values)
    current = sum(g * (voltage - values[dof]) for key, dof, g, voltage in edges if key == "source")
    if any(t.id == "source" and resolve_region(t.geometry, {}).area for t in project.terminals):
        source = next(t for t in project.terminals if t.id == "source")
        shape = resolve_region(source.geometry, {})
        selected = [
            int(maps[source.layer_id][index])
            for index in nodes[source.layer_id]
            if shape.covers(Point(xy[index]))
        ]
        current = float((matrix @ values - rhs)[selected].sum())
    voltage = next(t.value for t in project.terminals if t.id == "source")
    return {
        "resistance_ohm": float(voltage / current),
        "current_A": float(current),
        "dofs": count,
        "spacing_m": spacing,
        "method": "independent cell-centred five-point finite volume",
    }
