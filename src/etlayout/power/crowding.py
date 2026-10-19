# SPDX-License-Identifier: MPL-2.0
"""Physical-support current-crowding diagnostics; no smoothing or certification.

P1 electrical gradients are elementwise constant. Intersections integrate the
actual partial element area. Temperature averages use authoritative element-local
P1 fields evaluated at each clipped polygon centroid, preserving point contacts.
"""

from __future__ import annotations

import math

import numpy as np
from shapely.geometry import Point, Polygon
from shapely.geometry.polygon import orient

from etlayout.geometry.prepare import polygon_parts
from etlayout.models import SimulationError


def corner_features(shape):
    """Classify oriented boundary vertices; curved facets are only inferred.

    A polygon cannot establish a fabricated radius. A stable circumradius across
    adjacent low-angle facets is reported as an estimate, never calibration.
    """
    features = []
    for part in polygon_parts(shape):
        part = orient(part, sign=1.0)
        for ring in (part.exterior, *part.interiors):
            xy = np.asarray(ring.coords)[:-1]
            incoming = xy - np.roll(xy, 1, axis=0)
            outgoing = np.roll(xy, -1, axis=0) - xy
            cross = incoming[:, 0] * outgoing[:, 1] - incoming[:, 1] * outgoing[:, 0]
            turns = np.arctan2(cross, np.sum(incoming * outgoing, axis=1))
            for i, turn in enumerate(turns):
                if abs(turn) < 1e-8:
                    continue
                radius = None
                neighbours = turns[[(i - 1) % len(xy), i, (i + 1) % len(xy)]]
                if (
                    abs(turn) < math.pi / 4
                    and np.all(np.sign(neighbours) == np.sign(turn))
                    and np.ptp(neighbours) < max(abs(turn) * 0.15, 1e-8)
                ):
                    a, b = np.linalg.norm(incoming[i]), np.linalg.norm(outgoing[i])
                    chord = np.linalg.norm(incoming[i] + outgoing[i])
                    radius = float(a * b * chord / (2 * abs(cross[i])))
                kind = "faceted_curve" if radius else "sharp_reentrant" if turn < 0 else "sharp_convex"
                features.append(
                    {
                        "kind": kind,
                        "xy": xy[i].tolist(),
                        "interior_angle_deg": float(180 - np.degrees(turn)),
                        "inferred_radius_m": radius,
                        "physical_radius_verified": False,
                    }
                )
    # Tangent-to-facet joins have half the interior arc turn. Infer continuity
    # only from an adjacent stable circular run, never a large sharp turn.
    for feature in features:
        if feature["inferred_radius_m"] is not None or abs(180 - feature["interior_angle_deg"]) >= 45:
            continue
        candidates = [
            other
            for other in features
            if other["kind"] == "faceted_curve"
            and abs(180 - other["interior_angle_deg"]) < 45
            and (180 - other["interior_angle_deg"]) * (180 - feature["interior_angle_deg"]) > 0
        ]
        if candidates:
            closest = min(candidates, key=lambda f: np.linalg.norm(np.array(f["xy"]) - feature["xy"]))
            radius = closest["inferred_radius_m"]
            if radius and np.linalg.norm(np.array(closest["xy"]) - feature["xy"]) < radius * 0.3:
                feature.update(kind="faceted_curve", inferred_radius_m=radius)
    return features


def _weighted_percentile(values, weights, fraction=0.95):
    order = np.argsort(values)
    index = np.searchsorted(np.cumsum(weights[order]), fraction * weights.sum())
    return float(values[order[min(index, len(order) - 1)]])


def local_crowding(project, result, layer_id, *, support=None, geometry=None, reference_sheet_current=None):
    """Return SI metrics on one exact physical support, with explicit confidence.

    ``support`` is fixed geometry in metres, independent of this mesh. A crowding
    factor is available only with a caller-declared positive reference K [A/m].
    No single mesh can establish convergence or a physical peak qualification.
    """
    layer = project.layer(layer_id)
    fields = result.fields.get(layer_id, {})
    if "sheet_current" not in fields:
        raise SimulationError(f"Layer {layer_id}: electrical current fields unavailable")
    if reference_sheet_current is not None and (
        not math.isfinite(reference_sheet_current) or reference_sheet_current <= 0
    ):
        raise SimulationError("Reference sheet current must be finite and positive in A/m")
    ids = result.mesh.elements(layer_id)
    if support is None:
        weights = result.mesh.areas[ids]
        centers = result.mesh.centroids[ids]
    else:
        weights, centers, selected = [], [], []
        for identifier in ids:
            clipped = Polygon(result.mesh.points[result.mesh.triangles[identifier]]).intersection(support)
            if clipped.area > 0:
                selected.append(identifier)
                weights.append(clipped.area)
                centers.append([clipped.centroid.x, clipped.centroid.y])
        if not selected:
            raise SimulationError("Selected physical support has no positive-area electrical elements")
        ids, weights, centers = np.asarray(selected), np.asarray(weights), np.asarray(centers)
    sheet = np.linalg.norm(fields["sheet_current"][ids], axis=1)
    electric = np.linalg.norm(fields["electric_field"][ids], axis=1)
    q = fields["joule_density"][ids]
    peak_index = int(np.argmax(sheet))
    peak_element = int(ids[peak_index])
    peak_shape = Polygon(result.mesh.points[result.mesh.triangles[peak_element]])
    features = corner_features(geometry.layers[layer_id]) if geometry is not None else []
    near = [
        item for item in features if peak_shape.distance(Point(item["xy"])) <= math.sqrt(peak_shape.area) * 2
    ]
    boundaries = []
    if geometry is not None:
        for name, shapes in (("terminal_edge", geometry.terminals), ("interface_edge", geometry.interfaces)):
            for identifier, shape in shapes.items():
                boundary = shape.boundary if shape.area else shape
                if peak_shape.distance(boundary) <= math.sqrt(peak_shape.area) * 2:
                    boundaries.append({"kind": name, "id": identifier})
        for other, shape in geometry.layers.items():
            if other != layer_id and peak_shape.intersects(shape.boundary):
                boundaries.append({"kind": "layer_boundary", "id": other})
    status = (
        "singular_peak_unverified" if any(f["kind"] == "sharp_reentrant" for f in near) else "mesh_unverified"
    )
    record = {
        "layer": layer_id,
        "area_m2": float(weights.sum()),
        "support_definition": "entire active layer" if support is None else "exact polygon intersection",
        "raw_sheet_current_max_A_per_m": float(sheet.max()),
        "sheet_current_p95_A_per_m": _weighted_percentile(sheet, weights),
        "sheet_current_average_A_per_m": float(np.average(sheet, weights=weights)),
        "electric_field_max_V_per_m": float(electric.max()),
        "joule_power_W": float(q @ weights),
        "joule_density_average_W_per_m2": float(np.average(q, weights=weights)),
        "reference_sheet_current_A_per_m": reference_sheet_current,
        "crowding_factor": float(sheet.max() / reference_sheet_current) if reference_sheet_current else None,
        "peak_element": peak_element,
        "peak_xy": list(peak_shape.centroid.coords[0]),
        "nearby_geometry": near,
        "nearby_boundaries": boundaries,
        "raw_peak_status": status,
        "metric_verification": {
            key: "mesh_unverified"
            for key in ("integral_power", "physical_average", "temperature_max", "temperature_average")
        },
        "qualification": "Illustrative numerical maximum; mesh and physical radius require verification",
    }
    if layer.electrical.kind in {"bulk", "semiconductor"}:
        record["raw_volume_current_max_A_per_m2"] = float(sheet.max() / layer.thickness)
    else:
        record["volumetric_current_note"] = (
            "Sheet/2DEG current has no calibrated bulk thickness interpretation"
        )
    local = result.element_fields.get(layer_id, {}).get("temperature")
    if local is not None and "temperature_max" in result.metrics and support is None:
        record.update(
            temperature_max_K=float(np.max(local[ids])),
            temperature_average_K=float(np.average(local[ids].mean(axis=1), weights=weights)),
        )
    elif local is not None and "temperature_max" in result.metrics:
        temperatures = []
        maximum = -math.inf
        for identifier, center in zip(ids, centers):
            triangle = result.mesh.points[result.mesh.triangles[identifier]]
            bary = np.linalg.solve(np.vstack((triangle.T, np.ones(3))), [*center, 1.0])
            temperatures.append(float(bary @ local[identifier]))
            clipped = Polygon(triangle) if support is None else Polygon(triangle).intersection(support)
            for part in polygon_parts(clipped):
                for xy in part.exterior.coords:
                    coordinates = np.linalg.solve(np.vstack((triangle.T, np.ones(3))), [*xy, 1.0])
                    maximum = max(maximum, float(coordinates @ local[identifier]))
        record.update(
            temperature_max_K=maximum, temperature_average_K=float(np.average(temperatures, weights=weights))
        )
    return record


def attach_crowding(project, result, geometry):
    """Attach diagnostics using already prepared geometry; never change fields."""
    result.analysis["current_crowding"] = {
        layer: local_crowding(project, result, layer, geometry=geometry)
        for layer, fields in result.fields.items()
        if "sheet_current" in fields
    }


def verify_mesh_sequence(project, results, layer_id, support, *, reference_sheet_current=None):
    """Compare three or more meshes of the identical configured physical problem.

    Acceptance budgets are explicit reporting criteria, not solver tolerances.
    Both last refinement intervals must pass. Temperature changes are normalized
    by rise over the named reservoir, not absolute Kelvin temperature.
    """
    from copy import deepcopy

    from etlayout.geometry.prepare import prepare_geometry
    from etlayout.io.project import configuration_hash

    if len(results) < 3:
        raise SimulationError("Mesh verification requires at least three independent mesh levels")
    identities = []
    records = []
    for result in results:
        configuration = deepcopy(result.configuration)
        configuration["mesh"] = {}
        identities.append(configuration_hash(configuration))
        records.append(
            local_crowding(
                project, result, layer_id, support=support, reference_sheet_current=reference_sheet_current
            )
        )
    expected = project.to_dict()
    expected["mesh"] = {}
    if (
        len(set(identities)) != 1
        or identities[0] != configuration_hash(expected)
        or not all(result.converged for result in results)
    ):
        raise SimulationError("Mesh sequence needs converged solutions of the same physical configuration")
    sizes = [result.configuration["mesh"]["max_size"] for result in results]
    counts = [len(result.mesh.triangles) for result in results]
    if not all(a > b for a, b in zip(sizes, sizes[1:])) or not all(a < b for a, b in zip(counts, counts[1:])):
        raise SimulationError("Mesh verification requires decreasing sizes and increasing element counts")
    quantities = {
        "total_current": ([r.metrics["total_current"] for r in results], 0.02),
        "joule_power": ([r.metrics["joule_power"] for r in results], 0.02),
        "physical_average_sheet_current": ([r["sheet_current_average_A_per_m"] for r in records], 0.05),
        "local_joule_power": ([r["joule_power_W"] for r in records], 0.05),
        "raw_sheet_current_max": ([r["raw_sheet_current_max_A_per_m"] for r in records], 0.05),
    }
    if all("temperature_max_K" in r for r in records):
        references = [r.metrics.get("thermal_reference") for r in results]
        if all(v is not None for v in references):
            quantities["temperature_rise"] = (
                [r["temperature_max_K"] - ref for r, ref in zip(records, references)],
                0.05,
            )
            quantities["averaged_temperature_rise"] = (
                [r["temperature_average_K"] - ref for r, ref in zip(records, references)],
                0.05,
            )
    geometry_features = corner_features(prepare_geometry(project).layers[layer_id])
    singular = any(
        f["kind"] == "sharp_reentrant" and support.covers(Point(f["xy"])) for f in geometry_features
    )
    metrics = {}
    for key, (values, tolerance) in quantities.items():
        changes = [abs(b - a) / max(abs(b), 1e-30) for a, b in zip(values, values[1:])]
        status = "mesh_converged" if all(c <= tolerance for c in changes[-2:]) else "refine_required"
        if key == "raw_sheet_current_max" and singular:
            status = "singular_peak_unverified"
        metrics[key] = {
            "values": values,
            "relative_changes": changes,
            "criterion": tolerance,
            "status": status,
            "physical_qualification": False,
        }
    return {
        "sizes_m": sizes,
        "elements": counts,
        "support_wkt": support.wkt,
        "metrics": metrics,
        "method": "both final refinement intervals; fixed physical support",
        "recommendation": "Resolve physical radius or compare integral/averaged metrics"
        if singular
        else "Refine failed metrics; numerical mesh convergence does not establish material calibration",
    }
