# SPDX-License-Identifier: MPL-2.0
"""Optional geometry and flux-jump guidance on immutable physical supports.

These are bounded refinement indicators, not rigorous energy-error bounds. A
single reviewed pass is proposed; no automatic chase of a singular cell occurs.
"""

from __future__ import annotations

import math
import time

import numpy as np
from shapely import polygons, union_all
from shapely.geometry import Point

from etlayout.geometry.prepare import geometry_region
from etlayout.power.crowding import corner_features


def geometry_suggestions(project, geometry, plan):
    from etlayout.meshing.refinement import RefinementSuggestion

    for layer in project.layers:
        shape = geometry.layers[layer.id]
        features = corner_features(shape)
        curved = [f for f in features if f["kind"] == "faceted_curve" and f["inferred_radius_m"]]
        sharp = [f for f in features if f["kind"] == "sharp_reentrant"]
        buckets = {}
        for feature in curved:
            buckets.setdefault(round(math.log10(feature["inferred_radius_m"]) * 8), []).append(feature)
        groups = [("radius-" + str(key), values) for key, values in sorted(buckets.items())]
        groups.append(("reentrant", sharp))
        for key, values in groups:
            if not values:
                continue
            length = (
                min(f["inferred_radius_m"] for f in values)
                if key.startswith("radius")
                else project.mesh.max_size
            )
            size = min(project.mesh.max_size, length / (6 if key.startswith("radius") else 8))
            support = union_all(
                [Point(f["xy"]).buffer(max(length * 1.5, size * 4), quad_segs=8) for f in values]
            ).intersection(shape)
            reason = (
                "Six elements per locally inferred radius; polygon facets do not certify a fabricated radius."
                if key.startswith("radius")
                else "Bounded sizing around sharp reentrant corners/width transitions. Raw singular peaks remain unverified; refine fixed-area quantities instead."
            )
            plan.suggestions.append(
                RefinementSuggestion(
                    "physical-" + key + "-" + layer.id,
                    "Rounded boundary" if key.startswith("radius") else "Reentrant / neck boundary",
                    layer.id,
                    geometry_region(support),
                    size,
                    reason,
                    length,
                    max(2 * project.mesh.max_size, 2 * length),
                )
            )
        # Contact/via perimeters already receive Gmsh distance sizing and exact
        # transfer-length guidance. No fabricated width/radius is inferred from
        # a bounding box here. Gate and thin-island controls remain independent.


def flux_jump_indicator(result, layer_id, flux):
    """Interior-edge normal flux jumps: eta_T²=sum(h_e*|e|*[flux.n]²)/2.

    Flux is K[A/m] or lateral thermal sheet flux[W/m]. Indices are global
    triangles; no exchange occurs across point-only contacts or separate layers.
    Nonlinear coefficients and source residuals are not rigorous error bounds.
    """
    ids = np.asarray(result.mesh.elements(layer_id), dtype=int)
    tri = result.mesh.triangles[ids]
    edges = np.sort(tri[:, [[0, 1], [1, 2], [2, 0]]].reshape(-1, 2), axis=1)
    owners = np.repeat(np.arange(len(ids)), 3)
    order = np.lexsort((edges[:, 1], edges[:, 0]))
    ordered = edges[order]
    shared = np.all(ordered[:-1] == ordered[1:], axis=1)
    a, b = owners[order[:-1][shared]], owners[order[1:][shared]]
    edge = ordered[:-1][shared]
    vector = result.mesh.points[edge[:, 1]] - result.mesh.points[edge[:, 0]]
    length = np.linalg.norm(vector, axis=1)
    normal = np.column_stack((vector[:, 1], -vector[:, 0])) / length[:, None]
    local = np.asarray(flux)[ids]
    jump = np.sum((local[a] - local[b]) * normal, axis=1)
    value = 0.5 * (length * jump) ** 2
    eta = np.bincount(a, weights=value, minlength=len(ids)) + np.bincount(
        b, weights=value, minlength=len(ids)
    )
    scale = np.max(np.linalg.norm(local, axis=1), initial=0.0)
    # Suppress only floating-point flux cancellation, never physical small
    # conductance links or solver residuals. This controls optional suggestions.
    floor = (128 * np.finfo(float).eps * scale * math.sqrt(result.mesh.areas[ids].sum())) ** 2
    eta[eta < floor] = 0.0
    return ids, eta


def flux_suggestions(project, result, geometry, plan, *, fraction=0.6, max_marked=64):
    from etlayout.materials.models import evaluate_property
    from etlayout.meshing.refinement import RefinementSuggestion

    for layer in project.layers:
        fields = result.fields.get(layer.id, {})
        if "sheet_current" in fields:
            flux = fields["sheet_current"]
            quantity = "sheet current normal flux"
        elif "thermal_gradient" in fields:
            local = result.element_fields[layer.id]["temperature"].mean(axis=1)
            material = project.materials[layer.material_id]
            ids = result.mesh.elements(layer.id)
            k = evaluate_property(material.thermal_conductivity, local[ids])
            flux = np.full_like(fields["thermal_gradient"], np.nan)
            flux[ids] = (
                -fields["thermal_gradient"][ids]
                * k[:, None]
                * layer.thickness
                * np.array(material.thermal_anisotropy)
            )
            quantity = "lateral thermal sheet flux"
        else:
            continue
        ids, eta = flux_jump_indicator(result, layer.id, flux)
        if not np.any(eta):
            plan.indicators[layer.id] = {"quantity": quantity, "status": "no_resolved_flux_jump", "marked": 0}
            continue
        # Exclude the innermost cells near ideal reentrant points. The physical
        # reporting window sets a finite stop scale instead of chasing r=0.
        guard = project.mesh.max_size / 2
        sharp = [
            Point(f["xy"])
            for f in corner_features(geometry.layers[layer.id])
            if f["kind"] == "sharp_reentrant"
        ]
        for point in sharp:
            distance = np.linalg.norm(result.mesh.centroids[ids] - np.array(point.coords[0]), axis=1)
            eta[distance < guard] = 0.0
        ranking = np.argsort(-eta)
        positive = ranking[eta[ranking] > 0]
        if not len(positive):
            plan.indicators[layer.id] = {"quantity": quantity, "status": "singular_guard_only", "marked": 0}
            continue
        count = min(max_marked, int(np.searchsorted(np.cumsum(eta[positive]), fraction * eta.sum())) + 1)
        chosen = ids[positive[:count]]
        support = union_all(
            polygons(result.mesh.points[result.mesh.triangles[chosen]]), grid_size=1e-15
        ).intersection(geometry.layers[layer.id])
        length = float(np.sqrt(np.median(result.mesh.areas[chosen]) * 2))
        size = min(project.mesh.max_size, length / 2)
        plan.suggestions.append(
            RefinementSuggestion(
                "flux-" + layer.id,
                "Flux-jump indicator",
                layer.id,
                geometry_region(support),
                size,
                f"{quantity}; at most {max_marked} marked cells, target {fraction:.0%} squared jump weight. Excludes r < {guard * 1e6:g} µm around ideal reentrant vertices. Not a rigorous error bound.",
                length,
                project.mesh.max_size * 2,
            )
        )
        plan.indicators[layer.id] = {
            "quantity": quantity,
            "status": "available",
            "marked": count,
            "fraction_target": fraction,
            "fraction_marked": float(eta[positive[:count]].sum() / eta.sum()),
            "singular_guard_m": guard,
            "normalization": "sum h_e |e| [flux.n]^2 / 2 per adjacent triangle",
        }


def local_mesh_verification(project, rows, plan, selected=None):
    """Conservative three-level receipt for unchanged physical local supports."""
    from copy import deepcopy

    from etlayout.geometry.prepare import prepare_geometry, resolve_region
    from etlayout.io.project import physics_hash, project_from_dict
    from etlayout.models import MeasurementSpec, SupportSelector
    from etlayout.power.analysis import evaluate_measurement
    from etlayout.power.crowding import local_crowding

    started = time.perf_counter()
    receipt = {
        "version": 1,
        "status": "unavailable",
        "layers": {},
        "levels": [],
        "recommendation": "Use three levels of the same physical problem; refine failed averaged/integral metrics. A raw peak is not a material qualification.",
    }
    if len(rows) < 3 or any(not row.result or not row.result.converged or row.error for row in rows):
        receipt["reason"] = (
            "At least three converged, complete study levels with available measurements are required"
        )
        return receipt
    histories = [row.result.transient for row in rows]
    if any(history is not None for history in histories):
        ends = [
            history.rows[-1]["time"] if history is not None and history.rows else None
            for history in histories
        ]
        starts = [
            history.rows[0]["time"] if history is not None and history.rows else None for history in histories
        ]
        requested = project.transient.stop_time
        if (
            not all(history is not None and history.completed for history in histories)
            or any(
                end is None or not math.isclose(end, requested, rel_tol=1e-12, abs_tol=0.0) for end in ends
            )
            or any(
                start is None
                or starts[0] is None
                or not math.isclose(start, starts[0], rel_tol=1e-12, abs_tol=0.0)
                for start in starts
            )
        ):
            receipt["reason"] = "Transient mesh levels must complete the same requested physical duration"
            return receipt
    configurations = [deepcopy(row.result.configuration) for row in rows]
    for configuration in configurations:
        configuration["mesh"] = {}
        configuration["power"]["measurements"] = []
    expected = project.to_dict()
    expected["mesh"] = {}
    expected["power"]["measurements"] = []
    if any(physics_hash(configuration) != physics_hash(expected) for configuration in configurations):
        receipt["reason"] = "Physical configuration changed between meshes"
        return receipt
    counts = [len(row.result.mesh.triangles) for row in rows]
    if not all(a < b for a, b in zip(counts, counts[1:])):
        receipt["reason"] = "Element counts did not increase at every refinement level"
        return receipt
    chosen = set(selected if selected is not None else [s.id for s in plan.suggestions])
    suggestions = {s.id: s for s in plan.suggestions if s.id in chosen}
    if not chosen or chosen - set(suggestions):
        receipt["reason"] = "Selected local sizing controls are unavailable"
        return receipt
    for identifier, suggestion in suggestions.items():
        controls = [
            next(
                (
                    r
                    for r in row.result.configuration["mesh"]["refinements"]
                    if r["id"] == "guided-" + identifier
                ),
                None,
            )
            for row in rows
        ]
        if any(
            control is None
            or control["geometry"] != suggestion.geometry.__dict__
            or control.get("grading_length", 0.0) != suggestion.grading_length
            for control in controls
        ):
            receipt["reason"] = "Selected local sizing supports changed or are missing"
            return receipt
        sizes = [control["size"] for control in controls]
        if any(not math.isfinite(size) or size <= 0 for size in sizes) or not all(
            a > b for a, b in zip(sizes, sizes[1:])
        ):
            receipt["reason"] = "Selected local sizes must decrease at every refinement level"
            return receipt
    shapes = [resolve_region(s.geometry, {}) for s in plan.suggestions if s.id in chosen]
    geometry = prepare_geometry(deepcopy(project))
    for row in rows:
        result = row.result
        receipt["levels"].append(
            {
                "factor": row.value,
                "elements": len(result.mesh.triangles),
                "dofs": result.metrics.get("electrical_dofs", 0) + result.metrics.get("thermal_dofs", 0),
                "elapsed_s": result.elapsed_seconds,
                "execution": result.execution,
                "local_sizes_m": {
                    r["id"]: r["size"]
                    for r in result.configuration["mesh"]["refinements"]
                    if r["id"].removeprefix("guided-") in chosen
                },
            }
        )
    for layer in project.layers:
        support = union_all(shapes).intersection(geometry.layers[layer.id])
        if support.area <= 0 or any(layer.id not in row.result.fields for row in rows):
            continue
        records = []
        for row in rows:
            result = row.result
            context = project_from_dict(result.configuration)
            record = {}
            if "sheet_current" in result.fields[layer.id]:
                record = local_crowding(context, result, layer.id, support=support, geometry=geometry)
            elif "temperature_max" in result.metrics:
                selector = SupportSelector(layers=[layer.id], geometry=geometry_region(support))
                for aggregation, key in (("maximum", "temperature_max_K"), ("mean", "temperature_average_K")):
                    measurement = evaluate_measurement(
                        context,
                        result,
                        MeasurementSpec("local-mesh-" + key, "temperature", selector, aggregation),
                    )
                    if measurement["status"] == "available":
                        record[key] = measurement["value"]
            records.append(record)
        metrics = {}
        for key, criterion in (
            ("joule_power_W", 0.02),
            ("sheet_current_average_A_per_m", 0.05),
            ("raw_sheet_current_max_A_per_m", 0.05),
            ("temperature_max_K", 0.05),
            ("temperature_average_K", 0.05),
        ):
            if not all(key in record for record in records):
                continue
            values = [record[key] for record in records]
            reference = [row.result.metrics.get("thermal_reference") for row in rows]
            denominator = [
                value - ref if key.startswith("temperature") and ref is not None else value
                for value, ref in zip(values, reference)
            ]
            changes = [
                abs(b - a) / max(abs(scale), 1e-30)
                for a, b, scale in zip(values, values[1:], denominator[1:])
            ]
            status = (
                "mesh_converged" if all(change <= criterion for change in changes[-2:]) else "refine_required"
            )
            singular = any(
                feature["kind"] == "sharp_reentrant" and support.covers(Point(feature["xy"]))
                for feature in corner_features(geometry.layers[layer.id])
            )
            if key == "raw_sheet_current_max_A_per_m" and singular:
                status = "singular_peak_unverified"
            if key.startswith("temperature") and any(value is None for value in reference):
                status = "reference_unavailable"
            metrics[key] = {
                "values": values,
                "relative_changes": changes,
                "criterion": criterion,
                "status": status,
                "physical_qualification": False,
            }
        receipt["layers"][layer.id] = {
            "support": geometry_region(support).__dict__,
            "area_m2": support.area,
            "purpose_targets": [s.purpose + ": " + s.target for s in plan.suggestions if s.id in chosen],
            "metrics": metrics,
        }
    receipt["status"] = "available" if receipt["layers"] else "unavailable"
    receipt["reporting_seconds"] = time.perf_counter() - started
    return receipt
