# SPDX-License-Identifier: MPL-2.0
"""Layer/component-aware P1 assembly and constrained sparse linear algebra."""
from __future__ import annotations

import hashlib
import time
import warnings
from dataclasses import dataclass

import numpy as np
from scipy.sparse import coo_matrix, csr_matrix
from scipy.sparse.csgraph import connected_components
from scipy.sparse.linalg import MatrixRankWarning, cg, splu, spsolve

from etlayout.models import PlanarMesh, Project, SimulationError
from etlayout.simulation.execution import map_independent, record_linear_solve


class UnionFind:
    def __init__(self, size: int):
        self.parent = np.arange(size)

    def find(self, value: int) -> int:
        while self.parent[value] != value:
            self.parent[value] = self.parent[self.parent[value]]
            value = self.parent[value]
        return int(value)

    def union(self, a: int, b: int) -> None:
        self.parent[self.find(b)] = self.find(a)

    def mapping(self) -> np.ndarray:
        return np.unique([self.find(i) for i in range(len(self.parent))], return_inverse=True)[1]


@dataclass
class LayerSpace:
    elements: np.ndarray
    dofs: np.ndarray
    nodes: np.ndarray
    all_dofs: np.ndarray
    element_lookup: dict[int, int]
    global_to_local: np.ndarray


def _layer_topology(elements: np.ndarray, triangles: np.ndarray, total_elements: int) -> LayerSpace:
    """Edge-connected components and first-appearance DOFs in native array code.

    Only shared complete edges join elements; a point touch remains independent.
    The first-appearance permutation retains the old DOF ordering exactly.
    """
    edges = np.sort(triangles[:, [[0, 1], [1, 2], [2, 0]]].reshape(-1, 2), axis=1)
    order = np.lexsort((edges[:, 1], edges[:, 0]))
    same = np.all(edges[order][1:] == edges[order][:-1], axis=1)
    owners = np.repeat(np.arange(len(elements)), 3)[order]
    graph = coo_matrix((np.ones(np.count_nonzero(same)),
                        (owners[:-1][same], owners[1:][same])), shape=(len(elements),) * 2).tocsr()
    _, labels = connected_components(graph, directed=False)
    keys = np.column_stack((np.repeat(labels, 3), triangles.ravel()))
    _, first, inverse = np.unique(keys, axis=0, return_index=True, return_inverse=True)
    appearance = np.argsort(first)
    renumber = np.empty(len(first), dtype=np.int64)
    renumber[appearance] = np.arange(len(first))
    dofs = renumber[inverse].reshape(triangles.shape)
    nodes = triangles.ravel()[first[appearance]]
    lookup = np.full(total_elements, -1, dtype=np.int64)
    lookup[elements] = np.arange(len(elements))
    return LayerSpace(elements, dofs, nodes, np.arange(len(nodes)),
                      {int(e): i for i, e in enumerate(elements)}, lookup)


class Space:
    """Separate layer dofs, further split at point-only triangle contacts."""
    def __init__(self, project: Project, mesh: PlanarMesh, electrical: bool):
        self.mesh = mesh
        self.layers: dict[str, LayerSpace] = {}
        self.size = 0
        # Region and interface addressing belong to this immutable mesh/space.
        self._regions = {}
        self._interfaces = {}
        p = mesh.points[mesh.triangles]
        v1, v2 = p[:, 1] - p[:, 0], p[:, 2] - p[:, 0]
        twice_area = v1[:, 0] * v2[:, 1] - v1[:, 1] * v2[:, 0]
        if np.any(~np.isfinite(p)) or np.any(twice_area == 0):
            raise SimulationError("Mesh contains nonfinite coordinates or degenerate triangles")
        self.areas = np.abs(twice_area) / 2
        self.gradients = np.stack((
            np.stack((p[:, 1, 1] - p[:, 2, 1], p[:, 2, 0] - p[:, 1, 0]), axis=1),
            np.stack((p[:, 2, 1] - p[:, 0, 1], p[:, 0, 0] - p[:, 2, 0]), axis=1),
            np.stack((p[:, 0, 1] - p[:, 1, 1], p[:, 1, 0] - p[:, 0, 0]), axis=1),
        ), axis=1) / twice_area[:, None, None]
        selected_layers = []
        for layer in project.layers:
            if electrical and layer.role != "conductor":
                continue
            elements = np.asarray(mesh.elements(layer.id), dtype=np.int64)
            if not len(elements):
                continue
            selected_layers.append((layer.id, elements))

        def prepare(item):
            key, elements = item
            return key, _layer_topology(elements, mesh.triangles[elements], len(mesh.triangles))

        for key, topology in map_independent(prepare, selected_layers,
                                             work_units=sum(len(e) for _, e in selected_layers)):
            topology.dofs += self.size
            topology.all_dofs += self.size
            self.size += len(topology.nodes)
            self.layers[key] = topology
        if not self.size:
            raise SimulationError("No active elements for the selected physics")
        if self.size > project.mesh.max_dofs:
            raise SimulationError(f"{self.size} degrees of freedom exceed budget {project.mesh.max_dofs}")

    def region(self, layer_id: str, prefix: str, identifier: str) -> tuple[np.ndarray, np.ndarray]:
        key = (layer_id, prefix, identifier)
        if key in self._regions:
            return self._regions[key]
        layer = self.layers[layer_id]
        selected = np.asarray(self.mesh.regions.get(f"{prefix}:{identifier}", []), dtype=int)
        selected = selected[(selected >= 0) & (selected < len(layer.global_to_local))]
        local = layer.global_to_local[selected]
        local = local[local >= 0]
        self._regions[key] = layer.elements[local], layer.dofs[local]
        return self._regions[key]

    def nodes_for(self, layer_id: str, prefix: str, identifier: str) -> np.ndarray:
        layer = self.layers[layer_id]
        elements, dofs = self.region(layer_id, prefix, identifier)
        if len(elements):
            return np.unique(dofs)
        mapping = self.mesh.terminal_nodes if prefix == "terminal" else self.mesh.boundary_nodes
        nodes = mapping.get(identifier, np.array([], dtype=int))
        selected = layer.all_dofs[np.isin(layer.nodes, nodes)]
        support_key = f"{prefix}_support:{identifier}"
        if support_key in self.mesh.regions:
            supported = [layer.element_lookup[int(e)] for e in self.mesh.regions[support_key]
                         if int(e) in layer.element_lookup]
            selected = np.intersect1d(selected, np.unique(layer.dofs[supported]))
        return selected

    def edge_measure(self, layer_id: str, prefix: str, identifier: str) -> tuple[np.ndarray, np.ndarray]:
        layer = self.layers[layer_id]
        selected = set(self.nodes_for(layer_id, prefix, identifier).tolist())
        edges: dict[tuple[int, int], tuple[int, int, int, int]] = {}
        counts: dict[tuple[int, int], int] = {}
        for tri, dofs in zip(self.mesh.triangles[layer.elements], layer.dofs):
            for a, b in ((0, 1), (1, 2), (2, 0)):
                key = tuple(sorted((int(dofs[a]), int(dofs[b]))))
                counts[key] = counts.get(key, 0) + 1
                edges[key] = (int(dofs[a]), int(dofs[b]), int(tri[a]), int(tri[b]))
        explicit = getattr(self.mesh, "boundary_edges", {}).get(f"{prefix}:{identifier}")
        if explicit is not None:
            global_edges = {tuple(sorted((int(a), int(b)))) for a, b in explicit}
            chosen = [value for key, value in edges.items() if key[0] in selected and key[1] in selected
                      and tuple(sorted(value[2:])) in global_edges]
        else:
            chosen = [value for key, value in edges.items() if counts[key] == 1 and key[0] in selected and key[1] in selected]
        if not chosen:
            raise SimulationError(f"{identifier}: boundary selection contains no complete exterior edge")
        values = np.asarray(chosen)
        return values[:, :2], np.linalg.norm(self.mesh.points[values[:, 2]] - self.mesh.points[values[:, 3]], axis=1)

    def nodal_field(self, layer_id: str, values: np.ndarray) -> np.ndarray:
        layer = self.layers[layer_id]
        result = np.full(len(self.mesh.points), np.nan)
        # Rendering has one entry per planar vertex. A point-touch vertex is
        # averaged here only; the physical dofs remain entirely independent.
        total = np.bincount(layer.nodes, weights=values[layer.all_dofs], minlength=len(result))
        count = np.bincount(layer.nodes, minlength=len(result))
        active = count > 0
        result[active] = total[active] / count[active]
        return result

    def values_from_fields(self, fields: dict[str, np.ndarray] | None, default: float) -> np.ndarray:
        result = np.full(self.size, default, dtype=float)
        if fields:
            for layer_id, layer in self.layers.items():
                if layer_id in fields:
                    data = np.asarray(fields[layer_id])
                    if data.ndim == 2 and data.shape == (len(self.mesh.triangles), 3):
                        # Internal element-node data preserve independent values
                        # on either side of a point-only geometric contact.
                        values = data[layer.elements].ravel()
                        if not np.all(np.isfinite(values)):
                            raise SimulationError(f"Layer {layer_id}: authoritative element fields contain nonfinite active values")
                        dofs, first = np.unique(layer.dofs.ravel(), return_index=True)
                        result[dofs] = values[first]
                        if not np.allclose(result[layer.dofs.ravel()], values, rtol=1e-12, atol=1e-14):
                            raise SimulationError(f"Layer {layer_id}: inconsistent element-local values on a shared physical DOF")
                    else:
                        if data.shape != (len(self.mesh.points),):
                            raise SimulationError(f"Layer {layer_id}: field must be nodal (N,) or element-local (M,3)")
                        if len(np.unique(layer.nodes)) != len(layer.nodes):
                            raise SimulationError(f"Layer {layer_id}: legacy nodal fields lose independent point-touch states; recompute with element-local fields")
                        values = data[layer.nodes]
                        if not np.all(np.isfinite(values)):
                            raise SimulationError(f"Layer {layer_id}: supplied field contains nonfinite active values")
                        result[layer.all_dofs] = values
        return result

    def element_node_fields(self, values: np.ndarray) -> dict[str, np.ndarray]:
        fields = {}
        for key, layer in self.layers.items():
            array = np.full((len(self.mesh.triangles), 3), np.nan)
            array[layer.elements] = values[layer.dofs]
            fields[key] = array
        return fields


class Assembly:
    def __init__(self, size: int):
        self.size = size
        self.rows: list[np.ndarray] = []
        self.cols: list[np.ndarray] = []
        self.values: list[np.ndarray] = []

    def add(self, a: np.ndarray, b: np.ndarray, blocks: np.ndarray) -> None:
        if not len(a):
            return
        self.rows.append(np.broadcast_to(a[:, :, None], blocks.shape).ravel())
        self.cols.append(np.broadcast_to(b[:, None, :], blocks.shape).ravel())
        self.values.append(blocks.ravel())

    def matrix(self, mapping: np.ndarray | None = None) -> csr_matrix:
        n = self.size if mapping is None else int(mapping.max()) + 1
        if not self.values:
            return csr_matrix((n, n))
        rows, cols = np.concatenate(self.rows), np.concatenate(self.cols)
        if mapping is not None:
            rows, cols = mapping[rows], mapping[cols]
        matrix = coo_matrix((np.concatenate(self.values), (rows, cols)), shape=(n, n)).tocsr()
        matrix.eliminate_zeros()
        return matrix


def layer_matrix(blocks: list[csr_matrix], size: int) -> csr_matrix:
    """Join independent contiguous layer operators without another COO sort."""
    data, columns, pointers = [], [], []
    row_offset = value_offset = 0
    for block in blocks:
        if block.shape[0] != block.shape[1]:
            raise SimulationError("Layer operator must be square")
        data.append(block.data)
        columns.append(block.indices + row_offset)
        pointers.append(block.indptr[:-1] + value_offset)
        row_offset += block.shape[0]
        value_offset += len(block.data)
    if row_offset > size:
        raise SimulationError("Layer operators exceed the physical space")
    pointers.append(np.full(size - row_offset + 1, value_offset, dtype=np.int64))
    return csr_matrix((np.concatenate(data) if data else np.array([], dtype=float),
                       np.concatenate(columns) if columns else np.array([], dtype=int),
                       np.concatenate(pointers)), shape=(size, size))


def stiffness(areas: np.ndarray, gradients: np.ndarray, conductivity: np.ndarray) -> np.ndarray:
    return areas[:, None, None] * np.einsum("eik,ek,ejk->eij", gradients, conductivity, gradients)


def mass(areas: np.ndarray) -> np.ndarray:
    return areas[:, None, None] * (np.ones((3, 3)) + np.eye(3))[None] / 12.


def reduce_vector(vector: np.ndarray, mapping: np.ndarray) -> np.ndarray:
    return np.bincount(mapping, weights=vector, minlength=int(mapping.max()) + 1)


def reduce_matrix(matrix: csr_matrix, mapping: np.ndarray) -> csr_matrix:
    entries = matrix.tocoo()
    result = coo_matrix((entries.data, (mapping[entries.row], mapping[entries.col])),
                        shape=(int(mapping.max()) + 1,) * 2).tocsr()
    result.eliminate_zeros()
    return result


def check_references(matrix: csr_matrix, rhs: np.ndarray, fixed: dict[int, float],
                     policy: str = "error", thermal: bool = False,
                     sinks: np.ndarray | None = None) -> list[str]:
    n_components, labels = connected_components(matrix, directed=False)
    diagnostics = []
    fixed_labels = set(labels[list(fixed)].tolist())
    if sinks is not None:
        fixed_labels.update(labels[np.flatnonzero(sinks > 0)].tolist())
    for component in range(n_components):
        if component in fixed_labels:
            continue
        nodes = np.flatnonzero(labels == component)
        net = float(rhs[nodes].sum())
        scale = max(float(np.abs(rhs[nodes]).sum()), 1e-30)
        if abs(net) > 1e-9 * scale:
            unit = "W" if thermal else "A"
            raise SimulationError(f"Unreferenced component {component + 1} has unbalanced injection {net:.6g} {unit}")
        if thermal or policy != "reference":
            noun = "thermal sink / fixed temperature" if thermal else "reference potential"
            raise SimulationError(f"Floating component {component + 1} has no {noun} ({len(nodes)} dofs)")
        fixed[int(nodes[0])] = 0.
        diagnostics.append(f"Explicit gauge: component {component + 1}, dof {nodes[0]}, V=0")
    return diagnostics


def linear_solve(matrix: csr_matrix, rhs: np.ndarray, fixed: dict[int, float],
                 method: str = "direct", tolerance: float = 1e-10, *, factor_cache=None) -> np.ndarray:
    started = time.perf_counter()
    n = len(rhs)
    values = np.zeros(n)
    fixed_nodes = np.array(sorted(fixed), dtype=int)
    values[fixed_nodes] = [fixed[int(i)] for i in fixed_nodes]
    free = np.ones(n, dtype=bool)
    free[fixed_nodes] = False
    if not free.any():
        return values
    block = matrix[free][:, free].tocsr()
    vector = rhs[free] - (matrix @ values)[free]
    diagonal = block.diagonal()
    if np.any(diagonal <= 0) or np.any(~np.isfinite(diagonal)):
        raise SimulationError("Nonpositive or nonfinite matrix diagonal; check materials, boundaries and mesh")
    # Symmetric Jacobi scaling improves the mixed contact/lateral SI system.
    scaling = 1. / np.sqrt(diagonal)
    scaled = block.multiply(scaling[:, None]).multiply(scaling[None, :]).tocsr()
    factorized = None
    with warnings.catch_warnings():
        warnings.simplefilter("error", MatrixRankWarning)
        try:
            if method == "cg":
                answer, info = cg(scaled, vector * scaling, rtol=tolerance, atol=0., maxiter=max(1000, n * 2))
                if info:
                    raise SimulationError(f"Conjugate-gradient solve failed to converge (status {info})")
            elif method == "direct":
                if factor_cache is None:
                    answer = spsolve(scaled, vector * scaling)
                    factorized = True
                else:
                    # Hash exact operator bytes, not rounded values or an old
                    # project identity. A changed BC, step, material or mesh
                    # cannot reuse an incompatible decomposition.
                    digest = hashlib.sha256(np.asarray(scaled.shape, dtype=np.int64).tobytes())
                    for array in (scaled.indptr, scaled.indices, scaled.data):
                        digest.update(array.dtype.str.encode("ascii"))
                        digest.update(np.asarray(array.shape, dtype=np.int64).tobytes())
                        digest.update(array.tobytes())
                    key = digest.digest()
                    factorized = factor_cache.get("key") != key
                    if factorized:
                        factor = splu(scaled.tocsc())
                        factor_cache.clear()  # At most one bounded thermal factor.
                        factor_cache.update(key=key, factor=factor)
                    answer = factor_cache["factor"].solve(vector * scaling)
            else:
                raise SimulationError(f"Unknown linear solver {method!r}")
        except SimulationError:
            raise
        except (MatrixRankWarning, RuntimeError) as exc:
            raise SimulationError(f"Singular or ill-conditioned matrix: {exc}") from exc
    values[free] = answer * scaling
    if not np.all(np.isfinite(values)):
        raise SimulationError("Linear solver produced nonfinite values")
    record_linear_solve("cpu", factorized=factorized, seconds=time.perf_counter() - started)
    return values
