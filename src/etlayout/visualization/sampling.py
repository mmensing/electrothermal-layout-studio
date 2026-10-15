# SPDX-License-Identifier: MPL-2.0
"""Conforming-triangle interpolation, preserving holes and inactive regions."""
from __future__ import annotations

import numpy as np
from shapely.geometry import LineString, Point, Polygon
from shapely.strtree import STRtree

from etlayout.models import SimulationResult
from etlayout.visualization.units import display_unit

FIELD_LABELS = {
    "potential": ("Potential", "V"),
    "temperature": ("Temperature", "K"),
    "temperature_rise": ("Temperature rise", "K"),
    "electric_field": ("Electric field", "V/m"),
    "sheet_current": ("Sheet current density", "A/m"),
    "current_density": ("Volumetric current density", "A/m²"),
    "joule_density": ("Joule power density", "W/m²"),
    "heat_density": ("Total applied heat density", "W/m²"),
    "contact_density": ("Interlayer contact power density", "W/m²"),
    "contact_normal_density": ("Normal current density", "A/m²"),
    "thermal_gradient": ("Temperature gradient", "K/m"),
    "sheet_conductance": ("Sheet conductance tensor norm", "S"),
}

DISPLAY_FIELD_LABELS = {key: (name, display_unit(unit)) for key, (name, unit) in FIELD_LABELS.items()}

METRIC_UNITS = {
    "electrical_power": "W", "joule_power": "W", "contact_power": "W", "thermal_power": "W",
    "heat_out": "W", "effective_resistance": "Ω", "total_current": "A", "thermal_resistance": "K/W",
    "temperature_max": "K", "temperature_min": "K", "temperature_average": "K",
    "temperature_rise": "K", "thermal_reference": "K", "current_density_max": "A/m²",
    "current_density_average": "A/m²", "current_density_p95": "A/m²", "peak_to_average": "ratio",
    "current_balance_error": "fraction", "power_balance_error": "fraction", "thermal_balance_error": "fraction",
    "mesh_max_size_m": "m",
    "time_s": "s", "time_step": "s", "thermal_storage_rate": "W", "thermal_stored_energy": "J",
    "thermal_balance_absolute_error": "W", "thermal_balance_roundoff_allowance": "W",
    "transient_input_energy": "J", "transient_output_energy": "J", "transient_stored_energy": "J",
    "transient_energy_balance_error": "J", "transient_peak_temperature": "K", "transient_peak_time": "s",
    "transient_time": "s", "transient_history_bytes": "bytes",
    "resistance": "Ω", "terminal_power": "W", "voltage_drop": "V",
    "device_current_sharing_error": "fraction", "device_source_voltage_spread": "V",
    "local_refinement_factor": "ratio",
}


def metric_unit(key):
    if key.endswith("_relative_change") or key.endswith("_relative_l2_change"):
        unit = "fraction"
    elif key.startswith("terminal_current:"):
        unit = "A"
    elif key.startswith("regularized_"):
        from etlayout.power.measurement_contract import QUANTITIES
        quantity = key.split(":", 1)[0].removeprefix("regularized_").removesuffix("_absolute_change")
        unit = QUANTITIES.get(quantity, ("", ""))[1]
    elif key.endswith("_common_samples"):
        unit = "samples"
    else:
        unit = METRIC_UNITS.get(key, "")
    return unit


def metric_label(key, *, display=True):
    label = key.replace("_", " ").capitalize()
    unit = metric_unit(key)
    if display:
        unit = display_unit(unit)
    return f"{label} ({unit})" if unit else label


def scalar_field(result: SimulationResult, layer: str, field: str) -> np.ndarray:
    if field == "temperature_rise":
        initial = result.metrics.get("thermal_reference", result.configuration.get("solver", {}).get("initial_temperature", 300.))
        return np.asarray(result.fields[layer]["temperature"]) - (np.nan if initial is None else initial)
    data = np.asarray(result.fields[layer][field])
    return np.linalg.norm(data, axis=1) if data.ndim == 2 else data


def element_nodal_values(result: SimulationResult, layer: str, field: str) -> np.ndarray:
    """Authoritative element-local P1 values; never reconstruct lost point-touch DOFs."""
    name = "temperature" if field == "temperature_rise" else field
    local = result.element_fields.get(layer, {}).get(name)
    if local is None:
        from etlayout.visualization.support import _components
        local = np.asarray(result.fields[layer][name])[result.mesh.triangles].copy()
        owners = {}
        ambiguous_nodes = set()
        for component, elements in enumerate(_components(result.mesh, layer)):
            for node in np.unique(result.mesh.triangles[elements]):
                if node in owners and owners[node] != component:
                    ambiguous_nodes.add(node)
                owners[node] = component
        # Even triangle interiors depend on the unrecoverable shared-node value.
        if ambiguous_nodes:
            local[np.isin(result.mesh.triangles, list(ambiguous_nodes)).any(axis=1)] = np.nan
    if field == "temperature_rise":
        reference = result.metrics.get("thermal_reference", result.configuration.get("solver", {}).get("initial_temperature", 300.))
        return local - (np.nan if reference is None else reference)
    return local


class FieldSampler:
    """Exact element lookup and P1 nodal interpolation, never across holes."""

    def __init__(self, result: SimulationResult):
        self.result = result
        self.mesh = result.mesh
        self.polygons = [Polygon(p) for p in self.mesh.points[self.mesh.triangles]]
        self.tree = STRtree(self.polygons)

    def sample(self, layer: str, field: str, points: np.ndarray, *, region: str = "",
               logarithmic: bool = False) -> np.ndarray:
        records = self.sample_with_status(layer, field, points, region=region, logarithmic=logarithmic)
        return np.array([record["value"] if record["status"] == "ok" else np.nan for record in records])

    def sample_with_status(self, layer: str, field: str, points: np.ndarray, *, region: str = "",
                           logarithmic: bool = False) -> list[dict]:
        from etlayout.visualization.support import _components, field_support
        support = field_support(self.result, layer, field, region=region, logarithmic=logarithmic)
        data, nodal = support.data, support.nodal
        allowed = set(support.indices.tolist())
        component_for = {int(element): component for component, elements in enumerate(_components(self.mesh, layer), 1)
                         for element in elements}
        output = []
        for coordinate in points:
            point = Point(coordinate)
            indices = self.tree.query(point, predicate="intersects")
            hits = {}
            for raw in indices:
                element = int(raw)
                if element not in allowed:
                    continue
                if nodal:
                    nodes = self.mesh.triangles[element]
                    xy = self.mesh.points[nodes]
                    matrix = np.vstack((xy.T, np.ones(3)))
                    weights = np.linalg.solve(matrix, [*coordinate, 1.])
                    value = weights @ support.element_data[element]
                else:
                    value = data[element]
                if np.isfinite(value):
                    hits.setdefault(component_for[element], []).append(float(value))
            values = [{"component": f"component:{component}", "value": float(np.mean(samples))}
                      for component, samples in sorted(hits.items())]
            status = "outside_or_unavailable" if not values else "ambiguous" if len(values) > 1 else "ok"
            output.append({"status": status, "value": values[0]["value"] if status == "ok" else None,
                           "values": values})
        return output

    def profile(self, layer: str, field: str, start, end, count: int = 401, *, region: str = "",
                logarithmic: bool = False):
        start, end = np.asarray(start), np.asarray(end)
        coordinates = np.linspace(start, end, count)
        distance = np.linalg.norm(end - start) * np.linspace(0., 1., count)
        return distance, self.sample(layer, field, coordinates, region=region, logarithmic=logarithmic), coordinates


def cross_section_intervals(geometry, start, end) -> list[tuple[float, float]]:
    """Return real occupied intervals along a cut, including holes and gaps."""
    line = LineString([start, end])
    if line.length == 0 or geometry.is_empty:
        return []
    intersection = geometry.intersection(line)
    pieces = list(intersection.geoms) if hasattr(intersection, "geoms") else [intersection]
    intervals = []
    for part in pieces:
        if part.geom_type == "LineString" and part.length > 0:
            positions = [line.project(Point(p)) for p in part.coords]
            intervals.append((min(positions), max(positions)))
    return sorted(intervals)
