# SPDX-License-Identifier: MPL-2.0
"""Explicit fixed-temperature inputs for passive extraction; no thermal solve."""
from __future__ import annotations

import hashlib

import numpy as np

from etlayout.io.project import configuration_hash, physics_hash
from etlayout.models import SimulationError


def retained_temperature_reason(project, result):
    """Cheap availability guidance; authoritative arrays are checked in the worker."""
    if result is None:
        return "Select or calculate a compatible thermal run first."
    if not result.converged:
        return "The selected run did not converge; calculate a valid thermal run."
    if result.transient is not None and not result.transient.completed:
        return "The selected transient is incomplete; use a completed run."
    from etlayout.power.analysis import _validate_result_context
    try:
        _validate_result_context(project, result)
    except SimulationError as error:
        return str(error)
    if not any("temperature" in fields for fields in result.fields.values()):
        return "The selected run has no retained temperature fields."
    return ""


def temperature_inputs(project, *, mode="initial", temperature_K=None, result=None):
    """Return scalar/local P1 fields and provenance, without modifying the source.

    Retained fields stay on their exact source mesh. No interpolation, nodal
    smoothing or fallback to initial temperature is allowed on conductors.
    """
    if mode not in {"initial", "uniform", "retained"}:
        raise SimulationError("Temperature source must be initial, uniform or retained")
    if mode != "retained":
        value = project.solver.initial_temperature if mode == "initial" else temperature_K
        try:
            value = float(value)
        except (TypeError, ValueError) as error:
            raise SimulationError("Enter an explicit fixed temperature in kelvin") from error
        if not np.isfinite(value) or value <= 0:
            raise SimulationError("Fixed temperature must be finite and above zero kelvin")
        if mode == "initial" and temperature_K is not None:
            raise SimulationError("Choose uniform temperature to override the initial temperature")
        return value, {"version": 1, "mode": mode, "temperature_K": value}
    if temperature_K is not None:
        raise SimulationError("Retained temperature fields cannot be combined with a uniform override")
    reason = retained_temperature_reason(project, result)
    if reason:
        raise SimulationError(reason)
    from etlayout.io.project import _validate_result_arrays
    from etlayout.simulation.runner import mesh_fingerprint
    from etlayout.visualization.sampling import element_nodal_values
    _validate_result_arrays(result.mesh, {}, False)
    recorded = physics_hash(result.configuration)
    if result.physics_hash and result.physics_hash != recorded:
        raise SimulationError("Selected run physical identity is inconsistent; reopen or recompute it")
    if result.configuration_hash and result.configuration_hash != configuration_hash(result.configuration):
        raise SimulationError("Selected run configuration identity is inconsistent; reopen or recompute it")
    temperatures = {}
    digest = hashlib.sha256()
    for layer in project.layers:
        ids = result.mesh.elements(layer.id)
        if not len(ids):
            continue
        if "temperature" not in result.fields.get(layer.id, {}):
            if layer.role == "conductor":
                raise SimulationError(f"Selected run lacks temperature on conductor {layer.id}; recompute the thermal run")
            continue
        values = np.asarray(element_nodal_values(result, layer.id, "temperature"), dtype=float)
        if values.shape != (len(result.mesh.triangles), 3):
            raise SimulationError(f"Invalid component-local temperature shape on {layer.id}")
        if not np.isfinite(values[ids]).all() or np.any(values[ids] <= 0):
            raise SimulationError(f"Selected run needs finite positive component-local temperatures on {layer.id}")
        temperatures[layer.id] = values.copy()
        digest.update(layer.id.encode("utf-8"))
        digest.update(np.ascontiguousarray(values[ids], dtype="<f8").tobytes())
    if not temperatures:
        raise SimulationError("No retained temperatures are available on the active mesh")
    context = {"version": 1, "mode": mode, "source_configuration_hash": result.configuration_hash,
               "source_physics_hash": recorded, "mesh_identity": mesh_fingerprint(result.mesh),
               "temperature_fields_sha256": digest.hexdigest(), "frame": "final"}
    if result.transient is not None and len(result.transient.times):
        context["time_s"] = float(result.transient.times[-1])
    return temperatures, context
