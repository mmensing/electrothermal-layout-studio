# SPDX-License-Identifier: MPL-2.0
"""Point probes, line profiles and compatible-map differences on real FE fields."""
from __future__ import annotations

import numpy as np

from etlayout.models import SimulationError, SimulationResult

FIELD_UNITS = {
    "potential": "V", "temperature": "K", "temperature_rise": "K",
    "electric_field": "V/m", "sheet_current": "A/m", "current_density": "A/m²",
    "joule_density": "W/m²", "heat_density": "W/m²", "contact_density": "W/m²",
    "thermal_gradient": "K/m", "sheet_conductance": "S",
    "contact_normal_density": "A/m²",
}


def sample_points(result: SimulationResult, layer_id: str, field: str, points) -> np.ndarray:
    from etlayout.visualization.sampling import FieldSampler
    if layer_id not in result.fields:
        raise SimulationError(f"No results for layer {layer_id}")
    try:
        return FieldSampler(result).sample(layer_id, field, np.asarray(points))
    except KeyError as exc:
        raise SimulationError(f"No field '{field}' for layer {layer_id}") from exc


def sample_line(result: SimulationResult, layer_id: str, field: str, start, end, count: int = 201, *, region: str = "") -> dict:
    if count < 2:
        raise SimulationError("Line profile requires at least two points")
    start, end = np.asarray(start), np.asarray(end)
    points = np.linspace(start, end, count)
    if region:
        from etlayout.visualization.sampling import FieldSampler
        values = FieldSampler(result).sample(layer_id, field, points, region=region)
    else:
        values = sample_points(result, layer_id, field, points)
    return {"distance_m": np.linspace(0, np.linalg.norm(end-start), count), "x_m": points[:, 0],
            "y_m": points[:, 1], "value": values,
            "field": field, "unit": FIELD_UNITS.get(field, ""), "layer_id": layer_id,
            "configuration_hash": result.configuration_hash, "region": region}


def field_difference(a: SimulationResult, b: SimulationResult, layer_id: str, field: str) -> np.ndarray:
    """Return B − A on an identical conforming mesh; never compare unrelated positions."""
    if (a.mesh.points.shape != b.mesh.points.shape or not np.allclose(a.mesh.points, b.mesh.points, rtol=0, atol=1e-14)
            or not np.array_equal(a.mesh.triangles, b.mesh.triangles)
            or not np.array_equal(a.mesh.elements(layer_id), b.mesh.elements(layer_id))):
        raise SimulationError("Difference maps require spatially compatible geometry and identical meshes; use side-by-side comparison")
    if field in {"potential", "temperature", "temperature_rise"}:
        from etlayout.visualization.sampling import element_nodal_values
        return element_nodal_values(b, layer_id, field) - element_nodal_values(a, layer_id, field)
    return b.fields[layer_id][field] - a.fields[layer_id][field]
