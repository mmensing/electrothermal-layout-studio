# SPDX-License-Identifier: MPL-2.0
"""Conforming PSLG triangulation through Gmsh's planar geometry kernel.

Shapely nodes the complete arrangement before it reaches Gmsh, preserving all
region boundaries, holes and edge-terminal endpoints. The shared x–y mesh is
only geometric: solver unknowns remain independent per layer/component.
"""
from __future__ import annotations

import math
import threading
import time

import gmsh
import numpy as np
import shapely
from shapely.geometry import LineString, box
from shapely.ops import polygonize, unary_union

from etlayout.geometry.prepare import polygon_parts
from etlayout.models import PlanarMesh, PreparedGeometry, Project, SimulationError, checkpoint

_GMSH_LOCK = threading.Lock()
_SCALE = 1e6  # Work in micrometers so Gmsh tolerances are far below narrow features.


def _regions(geometry):
    return {**{"layer:" + k: v for k, v in geometry.layers.items()},
            **{"interface:" + k: v for k, v in geometry.interfaces.items()},
            **{"terminal:" + k: v for k, v in geometry.terminals.items()},
            **{"thermal:" + k: v for k, v in geometry.thermal_boundaries.items()},
            **{"heat:" + k: v for k, v in geometry.heat_sources.items()},
            **{"refinement:" + k: v for k, v in geometry.refinements.items()}}


def _lines(geometry):
    if geometry.geom_type in {"LineString", "LinearRing"}:
        yield geometry
    elif geometry.geom_type == "Polygon":
        yield geometry.exterior
        yield from geometry.interiors
    elif hasattr(geometry, "geoms"):
        for part in geometry.geoms:
            yield from _lines(part)


def directional_faces(project, geometry, faces):
    """Opt-in rectangular transfinite triangles; retain every physical boundary.

    Global coordinate intervals give shared/opposite curves compatible node
    counts even at T junctions. Nonrectangular faces retain ordinary meshing.
    No sheet or contact is homogenized by this geometric sizing option.
    """
    selected, steps = {}, []
    for refinement in project.mesh.refinements:
        axes = refinement.axis_sizes
        if not axes:
            continue
        if len(axes) != 2 or not all(math.isfinite(v) and v >= project.mesh.min_size for v in axes):
            raise SimulationError(f"Refinement {refinement.id}: axis_sizes need two finite x/y spacings at least min_size")
        support = geometry.refinements[refinement.id]
        found = False
        for index, face in enumerate(faces):
            if support.covers(face.representative_point()) and not face.interiors and (
                    face.symmetric_difference(box(*face.bounds)).area <= max(face.area * 1e-12, 1e-30)):
                selected[index] = face
                found = True
        if not found:
            raise SimulationError(f"Refinement {refinement.id}: directional sizing requires rectangular partition faces")
        steps.append(axes)
    if not selected:
        return {}, None
    spacing = [min(pair[axis] for pair in steps) for axis in (0, 1)]
    # Other explicit scalar controls remain hard upper bounds. In particular,
    # optimizer contact-transfer refinement must never be ignored by a mesh
    # generated with directional faces. Propagation can exceed a case's budget;
    # that is a resource-limited verification, not permission to under-resolve.
    scalar_controls = [(geometry.layers[layer.id], layer.mesh_size) for layer in project.layers if layer.mesh_size]
    scalar_controls += [(geometry.refinements[region.id], region.size)
                        for region in project.mesh.refinements if not region.axis_sizes]
    scalar_controls += [(support, project.mesh.contact_size) for support in (
        *geometry.interfaces.values(), *geometry.terminals.values()) if support.area > 0]
    for face in selected.values():
        for support, size in scalar_controls:
            if size > 0 and support.covers(face.representative_point()):
                spacing = [min(value, max(size, project.mesh.min_size)) for value in spacing]
    cuts = [sorted({round(coordinate[axis], 15) for face in selected.values()
                    for coordinate in face.exterior.coords}) for axis in (0, 1)]

    def intervals(low, high, axis):
        values = [value for value in cuts[axis] if round(low, 15) <= value <= round(high, 15)]
        return sum(max(1, math.ceil((b - a) / spacing[axis] - 1e-9)) for a, b in zip(values, values[1:]))

    return selected, intervals


def generate_mesh(project: Project, geometry: PreparedGeometry, progress=None, cancel=None) -> PlanarMesh:
    """Generate SI points/triangles and global triangle IDs for every physical region.

    Gmsh is process-global and is serialized between GUI workers. Budget checks
    run before geometry construction and after meshing. Cancellation is checked
    between kernel stages (Gmsh's own synchronous meshing call is not interruptible).
    """
    started = time.perf_counter()
    settings = project.mesh
    if not all(math.isfinite(v) and v > 0 for v in (settings.min_size, settings.max_size, settings.contact_size)):
        raise SimulationError("Mesh sizes must be finite positive SI lengths")
    if settings.min_size > settings.max_size:
        raise SimulationError("Minimum mesh size exceeds maximum mesh size")
    if settings.max_elements < 1 or settings.max_dofs < 1:
        raise SimulationError("Mesh element and degree-of-freedom budgets must be positive")
    checkpoint(cancel, progress, 0., "Noding material and contact boundaries")
    regions = _regions(geometry)
    footprint = unary_union(list(geometry.layers.values()))
    # A lower bound, deliberately independent of local refinement assumptions.
    lower_estimate = math.ceil(footprint.area / (settings.max_size ** 2))
    if lower_estimate > settings.max_elements:
        raise SimulationError(f"Mesh needs at least about {lower_estimate:,} elements at this maximum size; increase the element budget or mesh size")
    boundaries = []
    for region in regions.values():
        boundaries.extend(line.intersection(footprint) for line in _lines(region))
    # Database coordinates and entered SI coordinates commonly differ at the
    # final floating-point bit. Snap-round the whole arrangement at 1 fm rather
    # than passing numerical slivers or near-duplicate curves to Gmsh.
    noded = shapely.union_all(boundaries, grid_size=1e-15)
    faces = [p for p in polygonize(noded) if footprint.covers(p.representative_point())]
    if not faces:
        raise SimulationError("No polygon faces remain for meshing")
    if len(faces) > settings.max_elements:
        raise SimulationError("The number of geometry partitions already exceeds the element budget")
    directional, intervals = directional_faces(project, geometry, faces)
    size_regions = [(geometry.layers[layer.id], layer.mesh_size)
                    for layer in project.layers if layer.mesh_size is not None]
    size_regions += [(geometry.refinements[r.id], r.size) for r in settings.refinements]
    size_regions += [(g, settings.contact_size)
                     for g in [*geometry.interfaces.values(), *geometry.terminals.values()] if g.area > 0]
    refined_estimate = 0.
    for index, face in enumerate(faces):
        center = face.representative_point()
        requested = [size for region, size in size_regions if size > 0 and region.covers(center)]
        size = max(settings.min_size, min([settings.max_size, *requested]))
        if index in directional:
            x0, y0, x1, y1 = face.bounds
            refined_estimate += 2 * intervals(x0, x1, 0) * intervals(y0, y1, 1)
        else:
            refined_estimate += face.area / size ** 2
    if refined_estimate > settings.max_elements:
        raise SimulationError(f"Local refinement requires at least about {math.ceil(refined_estimate):,} elements, exceeding budget {settings.max_elements:,}; enlarge local sizes or the budget")
    # Translate origin as well as scaling: avoid loss of precision in global chip coordinates.
    origin = np.array(footprint.bounds[:2], dtype=float)
    with _GMSH_LOCK:
        owned = not gmsh.isInitialized()
        old_model = None
        if owned:
            gmsh.initialize([], readConfigFiles=False, interruptible=False)
        else:
            old_model = gmsh.model.getCurrent()
        model_name = f"etlayout-{time.time_ns()}"
        gmsh.model.add(model_name)
        gmsh.logger.start()
        try:
            gmsh.option.setNumber("General.Terminal", 0)
            from etlayout.simulation.execution import current_execution
            threads = current_execution().cpu_threads
            gmsh.option.setNumber("General.NumThreads", threads)
            for dimension in (1, 2, 3):
                gmsh.option.setNumber(f"Mesh.MaxNumThreads{dimension}D", threads)
            gmsh.option.setNumber("Geometry.Tolerance", 1e-10)
            gmsh.option.setNumber("Mesh.MeshSizeMin", settings.min_size * _SCALE)
            gmsh.option.setNumber("Mesh.MeshSizeMax", settings.max_size * _SCALE)
            gmsh.option.setNumber("Mesh.MeshSizeFromPoints", 1)
            gmsh.option.setNumber("Mesh.MeshSizeFromCurvature", 0)
            gmsh.option.setNumber("Mesh.MeshSizeExtendFromBoundary", 1)
            gmsh.option.setNumber("Mesh.Algorithm", 6)
            gmsh.option.setNumber("Mesh.ElementOrder", 1)
            gmsh.option.setNumber("Mesh.RandomFactor", 1e-9)
            points, point_xy, edges, edge_xy, surfaces = {}, {}, {}, {}, []
            # Precision is 1 femtometer in physical coordinates; never collapse a
            # micron feature just because its absolute chip position is large.
            def point_tag(xy):
                key = tuple(np.round((np.asarray(xy) - origin) * _SCALE, 9))
                if key not in points:
                    tag = gmsh.model.geo.addPoint(float(key[0]), float(key[1]), 0., settings.max_size * _SCALE)
                    points[key] = tag
                    point_xy[tag] = np.asarray(xy, dtype=float)
                return points[key]

            def ring_tag(ring):
                vertices = list(ring.coords)
                tags = []
                for a, b in zip(vertices[:-1], vertices[1:]):
                    p, q = point_tag(a), point_tag(b)
                    if p == q:
                        continue
                    key = (min(p, q), max(p, q))
                    if key not in edges:
                        edges[key] = gmsh.model.geo.addLine(*key)
                        edge_xy[edges[key]] = LineString([point_xy[key[0]], point_xy[key[1]]])
                    tags.append(edges[key] if p < q else -edges[key])
                if len(tags) < 3:
                    raise SimulationError("A polygon face is below geometric precision; repair tiny edges/slivers")
                return gmsh.model.geo.addCurveLoop(tags)

            for face in faces:
                checkpoint(cancel)
                loops = [ring_tag(face.exterior)] + [ring_tag(ring) for ring in face.interiors]
                surfaces.append(gmsh.model.geo.addPlaneSurface(loops))
            embedded = {}
            for line in _lines(noded):
                coordinates = list(line.coords)
                for a, b in zip(coordinates[:-1], coordinates[1:]):
                    p, q = point_tag(a), point_tag(b)
                    key = (min(p, q), max(p, q))
                    if p == q or key in edges:
                        continue
                    segment = LineString([a, b])
                    midpoint = segment.interpolate(.5, normalized=True)
                    containing = next((tag for tag, face in zip(surfaces, faces) if face.contains(midpoint)), None)
                    if containing is None:
                        continue
                    edges[key] = gmsh.model.geo.addLine(*key)
                    edge_xy[edges[key]] = segment
                    embedded.setdefault(containing, []).append(edges[key])
            gmsh.model.geo.synchronize()
            for index, face in directional.items():
                vertices = list(face.exterior.coords)
                for a, b in zip(vertices, vertices[1:]):
                    key = tuple(sorted((point_tag(a), point_tag(b))))
                    axis = 0 if abs(a[1] - b[1]) < 1e-15 else 1
                    count = intervals(min(a[axis], b[axis]), max(a[axis], b[axis]), axis)
                    gmsh.model.mesh.setTransfiniteCurve(edges[key], count + 1)
                x0, y0, x1, y1 = face.bounds
                corners = [point_tag(xy) for xy in ((x0, y0), (x1, y0), (x1, y1), (x0, y1))]
                gmsh.model.mesh.setTransfiniteSurface(surfaces[index], "AlternateLeft", corners)
            for surface, curves in embedded.items():
                gmsh.model.mesh.embed(1, curves, 2, surface)
            checkpoint(cancel, progress, .25, f"Created {len(faces):,} conforming regions")
            fields = []
            # Size restrictions are exact face assignments, including arbitrary
            # polygon refinement regions rather than just their bounding boxes.
            sizing = []
            for layer in project.layers:
                if layer.mesh_size is not None:
                    if not math.isfinite(layer.mesh_size) or layer.mesh_size <= 0:
                        raise SimulationError(f"Layer {layer.name}: mesh size must be positive")
                    sizing.append((geometry.layers[layer.id], layer.mesh_size))
            for refinement in settings.refinements:
                sizing.append((geometry.refinements[refinement.id], refinement.size))
            contacts = list(geometry.interfaces.values()) + list(geometry.terminals.values())
            sizing.extend((contact, settings.contact_size) for contact in contacts if contact.area > 0)
            for region, size in sizing:
                selected = [tag for tag, face in zip(surfaces, faces) if region.covers(face.representative_point())]
                if not selected:
                    continue
                field = gmsh.model.mesh.field.add("MathEval")
                gmsh.model.mesh.field.setString(field, "F", str(max(settings.min_size, min(size, settings.max_size)) * _SCALE))
                restrict = gmsh.model.mesh.field.add("Restrict")
                gmsh.model.mesh.field.setNumber(restrict, "InField", field)
                gmsh.model.mesh.field.setNumbers(restrict, "SurfacesList", selected)
                fields.append(restrict)
            if contacts:
                contact_lines = unary_union([line for g in contacts for line in _lines(g)])
                selected_edges = [tag for tag, segment in edge_xy.items()
                                  if segment.intersection(contact_lines).length >= segment.length * .999999]
                if selected_edges:
                    distance = gmsh.model.mesh.field.add("Distance")
                    gmsh.model.mesh.field.setNumbers(distance, "CurvesList", selected_edges)
                    gmsh.model.mesh.field.setNumber(distance, "Sampling", 30)
                    threshold = gmsh.model.mesh.field.add("Threshold")
                    gmsh.model.mesh.field.setNumber(threshold, "InField", distance)
                    gmsh.model.mesh.field.setNumber(threshold, "SizeMin", max(settings.contact_size, settings.min_size) * _SCALE)
                    gmsh.model.mesh.field.setNumber(threshold, "SizeMax", settings.max_size * _SCALE)
                    gmsh.model.mesh.field.setNumber(threshold, "DistMin", settings.contact_size * _SCALE)
                    gmsh.model.mesh.field.setNumber(threshold, "DistMax", settings.max_size * 2 * _SCALE)
                    fields.append(threshold)
            if settings.corner_refinement:
                corner_tags = set()
                for region in geometry.layers.values():
                    for polygon in polygon_parts(region):
                        for ring in [polygon.exterior, *polygon.interiors]:
                            xy = np.asarray(ring.coords)[:-1]
                            for i, current in enumerate(xy):
                                a, b = xy[i - 1] - current, xy[(i + 1) % len(xy)] - current
                                cosine = np.dot(a, b) / (np.linalg.norm(a) * np.linalg.norm(b))
                                if cosine > math.cos(math.radians(160)):
                                    key = tuple(np.round((current - origin) * _SCALE, 9))
                                    if key in points:
                                        corner_tags.add(points[key])
                size = max(settings.min_size, min(settings.contact_size, settings.max_size / 3))
                gmsh.model.mesh.setSize([(0, tag) for tag in corner_tags], size * _SCALE)
            if fields:
                minimum = gmsh.model.mesh.field.add("Min")
                gmsh.model.mesh.field.setNumbers(minimum, "FieldsList", fields)
                gmsh.model.mesh.field.setAsBackgroundMesh(minimum)
            checkpoint(cancel, progress, .4, "Generating triangular mesh")
            gmsh.model.mesh.generate(2)
            checkpoint(cancel, progress, .8, "Tagging physical mesh regions")
            node_tags, coordinates, _ = gmsh.model.mesh.getNodes()
            xyz = np.asarray(coordinates).reshape(-1, 3)
            order = np.argsort(node_tags)
            node_tags = np.asarray(node_tags)[order]
            xy = xyz[order, :2] / _SCALE + origin
            triangle_blocks = []
            types, _, node_blocks = gmsh.model.mesh.getElements(2)
            for element_type, block in zip(types, node_blocks):
                if element_type == 2:
                    triangle_blocks.append(np.asarray(block).reshape(-1, 3))
            if not triangle_blocks:
                raise SimulationError("Gmsh produced no first-order triangles")
            triangles = np.searchsorted(node_tags, np.concatenate(triangle_blocks)).astype(np.int64)
            # Keep the output compact and free of non-element nodes.
            used, inverse = np.unique(triangles, return_inverse=True)
            xy, triangles = xy[used], inverse.reshape(-1, 3)
            if len(triangles) > settings.max_elements:
                raise SimulationError(f"Generated {len(triangles):,} elements, exceeding budget {settings.max_elements:,}; increase sizes or budget")
            centroids = xy[triangles].mean(axis=1)
            center_points = shapely.points(centroids)
            tagged = {key: np.flatnonzero(shapely.covers(region, center_points)).astype(np.int64)
                      if region.area > 0 else np.empty(0, np.int64) for key, region in regions.items()}
            nodal_points = shapely.points(xy)
            tolerance = max(settings.max_size * 1e-8, 1e-15)
            terminal_nodes, boundary_nodes, boundary_edges = {}, {}, {}
            for name, items, target in (("terminal", geometry.terminals, terminal_nodes),
                                        ("thermal", geometry.thermal_boundaries, boundary_nodes)):
                for key, region in items.items():
                    target[key] = np.flatnonzero(shapely.dwithin(region, nodal_points, tolerance)).astype(np.int64)
                    if len(target[key]) == 0:
                        raise SimulationError(f"{name.capitalize()} {key} contains no mesh nodes; reduce mesh size or correct geometry")
                    if region.area == 0:
                        # A selected XY corner must not impose a condition on a
                        # second component which only touches it at that point.
                        candidate = np.flatnonzero(np.count_nonzero(np.isin(triangles, target[key]), axis=1) >= 2)
                        selected_edges, supporting = set(), []
                        region_buffer = region.buffer(tolerance)
                        for element in candidate:
                            tri = triangles[element]
                            supported = False
                            for i, j in ((0, 1), (1, 2), (2, 0)):
                                a, b = int(tri[i]), int(tri[j])
                                if region_buffer.covers(LineString(xy[[a, b]])):
                                    selected_edges.add(tuple(sorted((a, b))))
                                    supported = True
                            if supported:
                                supporting.append(element)
                        if not selected_edges:
                            raise SimulationError(f"{name.capitalize()} {key} contains no complete mesh edge")
                        boundary_edges[name + ":" + key] = np.asarray(sorted(selected_edges), dtype=np.int64)
                        target[key] = np.unique(boundary_edges[name + ":" + key])
                        tagged[name + "_support:" + key] = np.asarray(supporting, dtype=np.int64)
            for key, region in geometry.layers.items():
                boundary_nodes["layer:" + key] = np.flatnonzero(shapely.dwithin(region.boundary, nodal_points, tolerance)).astype(np.int64)
            dofs = sum(len(np.unique(triangles[tagged["layer:" + layer.id]])) * (2 if layer.role == "conductor" else 1)
                       for layer in project.layers)
            if dofs > settings.max_dofs:
                raise SimulationError(f"Estimated {dofs:,} combined electrical/thermal DOFs exceed budget {settings.max_dofs:,}")
            p = xy[triangles]
            side_a, side_b = p[:, 1] - p[:, 0], p[:, 2] - p[:, 0]
            twice_area = np.abs(side_a[:, 0] * side_b[:, 1] - side_a[:, 1] * side_b[:, 0])
            if np.any(twice_area <= 0):
                raise SimulationError("Mesher produced degenerate triangles")
            lengths = np.linalg.norm(p - np.roll(p, -1, axis=1), axis=2)
            quality = 2 * np.sqrt(3.) * twice_area / np.sum(lengths ** 2, axis=1)
            angles = []
            for i in range(3):
                a, b, c = lengths[:, i], lengths[:, (i + 1) % 3], lengths[:, (i + 2) % 3]
                angles.append(np.degrees(np.arccos(np.clip((b * b + c * c - a * a) / (2 * b * c), -1, 1))))
            statistics = dict(nodes=len(xy), elements=len(triangles), estimated_dofs=dofs,
                              directional_partitions=len(directional),
                              partitions=len(faces), minimum_quality=float(quality.min()),
                              mean_quality=float(quality.mean()), minimum_angle_deg=float(np.min(angles)),
                              area_m2=float(twice_area.sum() / 2),
                              elapsed_seconds=time.perf_counter() - started,
                              backend="Gmsh " + gmsh.__version__, warnings=[])
            if quality.min() < .05:
                statistics["warnings"].append("Very slender triangles detected; inspect narrow geometry and mesh-size controls")
            result = PlanarMesh(xy, triangles, tagged, terminal_nodes, boundary_nodes, statistics,
                                boundary_edges=boundary_edges)
            checkpoint(cancel, progress, 1., f"Mesh ready: {len(triangles):,} triangles")
            return result
        except SimulationError:
            raise
        except Exception as exc:
            messages = gmsh.logger.get()
            tail = " ".join(messages[-4:])
            raise SimulationError(f"Mesh generation failed: {exc}. {tail}") from exc
        finally:
            gmsh.logger.stop()
            gmsh.model.remove()
            if owned:
                gmsh.finalize()
            elif old_model:
                gmsh.model.setCurrent(old_model)
