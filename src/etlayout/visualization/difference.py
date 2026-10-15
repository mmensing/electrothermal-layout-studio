# SPDX-License-Identifier: MPL-2.0
"""Explicit derived comparisons with conservative common support and provenance."""
from __future__ import annotations

from copy import deepcopy
from dataclasses import replace

import numpy as np
from shapely import covers, polygons, union_all

from etlayout.models import SimulationResult
from etlayout.visualization.sampling import FieldSampler, scalar_field
from etlayout.visualization.support import field_support, region_elements, region_options
from etlayout.visualization.timing import snapshot_metadata


def make_field_difference(current: SimulationResult, comparison: SimulationResult,
                          layer: str, field: str, comparison_name="Comparison") -> SimulationResult:
    """Current minus comparison; vector/tensor fields compare their scalar magnitudes.

    A triangle is retained only when it lies wholly within both valid domains. This
    conservative rule never paints across holes on an independently meshed design.
    """
    first = field_support(current, layer, field)
    second = field_support(comparison, layer, field)
    if not len(first.indices) or not len(second.indices):
        raise ValueError("Both results need finite values on the selected layer and field.")
    domain = union_all(polygons(comparison.mesh.points[comparison.mesh.triangles[second.indices]]))
    candidates = polygons(current.mesh.points[current.mesh.triangles[first.indices]])
    shared = first.indices[covers(domain, candidates)]
    if not len(shared):
        raise ValueError("No complete mesh triangles lie inside both finite field domains.")
    local_difference = None
    if first.nodal:
        if (np.array_equal(current.mesh.points, comparison.mesh.points)
                and np.array_equal(current.mesh.triangles, comparison.mesh.triangles)):
            local_difference = first.element_data - second.element_data
        else:
            # Approach a shared boundary from inside each target triangle. A
            # coordinate-only query at a point touch has no unique component.
            barycentric = np.full((3, 3), 1e-8)
            np.fill_diagonal(barycentric, 1 - 2e-8)
            xy = current.mesh.points[current.mesh.triangles[shared]]
            interior = np.einsum("ij,mjk->mik", barycentric, xy)
            sampled = FieldSampler(comparison).sample(layer, field, interior.reshape(-1, 2)).reshape(-1, 3)
            local_difference = np.full_like(first.element_data, np.nan)
            local_difference[shared] = first.element_data[shared] - np.linalg.solve(barycentric, sampled.T).T
        # Legacy coordinate arrays are descriptive only. Local fields below are
        # authoritative for display, sampling and exported values.
        difference = np.full(len(current.mesh.points), np.nan)
        for element in shared:
            difference[current.mesh.triangles[element]] = local_difference[element]
    else:
        coordinates = current.mesh.centroids
        reference = scalar_field(current, layer, field)
        other_values = FieldSampler(comparison).sample(layer, field, coordinates)
        difference = reference - other_values
    # Discard unrelated finite values so derived arrays cannot accidentally imply
    # support beyond the common domain in downstream consumers.
    active = np.unique(current.mesh.triangles[shared]) if first.nodal else shared
    data = np.full_like(difference, np.nan, dtype=float)
    data[active] = difference[active]
    configuration = deepcopy(current.configuration)
    quantity = "difference of magnitudes" if np.asarray(current.fields[layer].get(field, [])).ndim == 2 else "scalar difference"
    sources = [{"name": current.configuration.get("name", "Current result"),
                "configuration_hash": current.configuration_hash, "solver_version": current.solver_version,
                "converged": bool(current.converged)},
               {"name": comparison_name, "configuration_hash": comparison.configuration_hash,
                "solver_version": comparison.solver_version, "converged": bool(comparison.converged)}]
    for source, result in zip(sources, (current, comparison), strict=True):
        source.update(snapshot_metadata(result))
    configuration["visualization"] = {
        "kind": "difference", "operation": "current_minus_comparison", "quantity": quantity,
        "layer": layer, "field": field, "sources": sources,
        "source_components": {layer: {key: region_elements(current, layer, key).tolist()
                                      for key, _ in region_options(current, layer) if key.startswith("component:")}},
        "support": "Complete current-mesh triangles inside both finite field domains",
        "thermal_references_K": [result.metrics.get("thermal_reference",
            result.configuration.get("solver", {}).get("initial_temperature", 300.))
            for result in (current, comparison)] if field == "temperature_rise" else [],
    }
    if "time_s" in current.metrics or "time_s" in comparison.metrics:
        configuration["visualization"].update(current_time_s=current.metrics.get("time_s"),
                                              comparison_time_s=comparison.metrics.get("time_s"))
    mesh = replace(current.mesh, regions={**current.mesh.regions, "layer:" + layer: shared})
    stored_field = "temperature" if field == "temperature_rise" else field
    derived = SimulationResult(mesh=mesh, fields={layer: {stored_field: data}},
        element_fields={layer: {stored_field: local_difference}} if first.nodal else {},
        metrics={"thermal_reference": 0.} if field == "temperature_rise" else {},
        configuration=configuration, configuration_hash=current.configuration_hash,
        converged=current.converged and comparison.converged, solver_version=current.solver_version)
    if not len(field_support(derived, layer, field).indices):
        raise ValueError("No finite common field values remain after resampling.")
    return derived
