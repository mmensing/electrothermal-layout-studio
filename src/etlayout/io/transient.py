# SPDX-License-Identifier: MPL-2.0
"""Validated transient histories in portable JSON/NPZ archives, sharing one mesh."""
from __future__ import annotations

import io
import json
import zipfile
from copy import deepcopy
from dataclasses import asdict
from typing import Any

import numpy as np

from etlayout.models import Diagnostic, SimulationError, SimulationResult, TransientHistory

MAX_HISTORY_BYTES = 1024 * 1024 * 1024
MAX_SNAPSHOTS = 10000


FIELD_GROUPS = ("fields", "element_fields", "interface_fields")


def _field_keys(snapshot, group="fields"):
    return {layer: {name: f"{group}_{layer_index}_{field_index}"
                    for field_index, name in enumerate(fields)}
            for layer_index, (layer, fields) in enumerate(getattr(snapshot, group).items())}


def _encoded_metadata(history):
    from etlayout.io.project import _json_dump
    snapshots = [{**{group: _field_keys(snapshot, group) for group in FIELD_GROUPS},
                  "field_version": snapshot.field_version, "analysis": snapshot.analysis, "metrics": snapshot.metrics,
                  "terminals": snapshot.terminals, "diagnostics": [asdict(item) for item in snapshot.diagnostics],
                  "execution": getattr(snapshot, "execution", {}),
                  "iterations": snapshot.iterations, "converged": snapshot.converged}
                 for snapshot in history.snapshots]
    metadata = {"schema_version": 2, "times_s": history.times.tolist(), "rows": history.rows,
                "completed": history.completed, "snapshots": snapshots,
                "drive_convention": "right limit at the segment start; left limit at each integration-step endpoint"}
    return _json_dump(metadata).encode("utf-8")


def _array_storage_size(array):
    # Validated fields have simple numeric dtypes and at most two dimensions,
    # so NumPy writes its 1.0 header. Count it without copying the field data.
    header = io.BytesIO()
    np.lib.format.write_array_header_1_0(header, np.lib.format.header_data_from_array_1_0(array))
    return header.tell() + array.nbytes


def _validate_times(history, result, project, snapshot_count):
    from etlayout.io.project import _decode
    if not project.transient.enabled:
        raise SimulationError("A transient history needs an enabled transient setup")
    times = np.asarray(history.times)
    start = _decode(float, result.metrics.get("transient_start_time", 0.))
    if start < 0 or start >= project.transient.stop_time:
        raise SimulationError("Transient start time must precede its requested stop time")
    if (times.ndim != 1 or times.dtype.kind not in "fiu" or not len(times)
            or not np.isfinite(times).all() or times[0] != start or np.any(np.diff(times) <= 0)):
        raise SimulationError("Transient snapshot times must start at the recorded start time and increase strictly")
    if len(times) != snapshot_count or len(times) > min(project.transient.max_snapshots, MAX_SNAPSHOTS):
        raise SimulationError("Transient snapshot count is inconsistent or exceeds its budget")
    thermal_stop = (not history.completed and result.converged
                    and result.metrics.get("transient_stop_reason") == "maximum_temperature"
                    and result.metrics.get("thermal_limit_compliance", {}).get("status") == "failed"
                    and result.metrics.get("thermal_limit_compliance", {}).get("stopped_early") is True
                    and any(limit.enabled and limit.evaluation == "stop_transient" for limit in project.temperature_limits))
    if type(history.completed) is not bool or (history.completed != result.converged and not thermal_stop):
        raise SimulationError("Transient completion and result convergence disagree")
    tolerance = max(project.transient.stop_time * 1e-12, np.finfo(float).tiny)
    if times[-1] > project.transient.stop_time + tolerance:
        raise SimulationError("Transient snapshot lies after the requested stop time")
    if history.completed and abs(times[-1] - project.transient.stop_time) > tolerance:
        periodic = result.metrics.get("transient_periodic_converged") is True
        if (not periodic or project.transient.periodic_tolerance <= 0
                or result.metrics.get("transient_stop_reason") != "periodic_convergence"
                or not isinstance(result.metrics.get("transient_periodic_temperature_change"), (int, float))
                or not 0 <= result.metrics["transient_periodic_temperature_change"] <= project.transient.periodic_tolerance):
            raise SimulationError("Completed transient history does not reach the stop time or valid periodic convergence")
        from etlayout.solvers.transient import _periodic_spec
        period, origin = _periodic_spec(project)
        cycles = result.metrics.get("transient_periodic_cycles")
        if (type(cycles) is not int or cycles < max(project.transient.periodic_min_cycles,
                                                   project.transient.periodic_required_cycles + 1)
                or abs(times[-1] - (origin + cycles * period)) > tolerance):
            raise SimulationError("Periodic completion time must match its accepted convergence cycle")
    if (not isinstance(result.metrics, dict) or "time_s" not in result.metrics
            or abs(_decode(float, result.metrics["time_s"]) - times[-1]) > tolerance):
        raise SimulationError("Final transient time metadata does not match the last saved snapshot")
    if not history.rows or len(history.rows) > project.transient.max_steps + 1:
        raise SimulationError("Transient step history is missing or exceeds its step budget")
    try:
        row_times = np.asarray([_decode(float, row["time_s"]) for row in history.rows], dtype=float)
        row_steps = np.asarray([_decode(float, row["time_step"]) for row in history.rows], dtype=float)
    except (KeyError, TypeError, ValueError) as error:
        raise SimulationError("Transient step history needs numeric time_s values") from error
    if (not np.isfinite(row_times).all() or row_times[0] != start or np.any(np.diff(row_times) <= 0)
            or abs(row_times[-1] - times[-1]) > tolerance):
        raise SimulationError("Transient step times are inconsistent with saved snapshots")
    if not np.allclose(row_steps, np.diff(row_times, prepend=start), rtol=1e-12, atol=tolerance):
        raise SimulationError("Transient step durations are inconsistent with step times")
    positions = np.clip(np.searchsorted(row_times, times), 0, len(row_times) - 1)
    prior = np.maximum(positions - 1, 0)
    if np.any(np.minimum(abs(row_times[positions] - times), abs(row_times[prior] - times)) > tolerance):
        raise SimulationError("Each saved transient snapshot must match an accepted integration step")
    try:
        json.dumps(history.rows, allow_nan=False)
    except (TypeError, ValueError) as error:
        raise SimulationError("Transient step metadata must contain finite JSON values") from error
    return times, tolerance


def validate_history(result, project):
    from etlayout.io.project import _decode, _validate_local_fields, _validate_result_arrays
    history = result.transient
    if history is None:
        return
    times, tolerance = _validate_times(history, result, project, len(history.snapshots))
    used = 0
    for index, snapshot in enumerate(history.snapshots):
        if snapshot.transient is not None:
            raise SimulationError("Nested transient histories are not supported")
        if snapshot.mesh is not result.mesh:
            if (not np.array_equal(snapshot.mesh.points, result.mesh.points)
                    or not np.array_equal(snapshot.mesh.triangles, result.mesh.triangles)
                    or any(getattr(snapshot.mesh, group).keys() != getattr(result.mesh, group).keys()
                           or any(not np.array_equal(ids, getattr(result.mesh, group)[key])
                                  for key, ids in getattr(snapshot.mesh, group).items())
                           for group in ("regions", "terminal_nodes", "boundary_nodes", "boundary_edges"))):
                raise SimulationError("Transient snapshots must share the result mesh")
        _validate_result_arrays(result.mesh, snapshot.fields, snapshot.converged)
        _validate_local_fields(snapshot)
        if result.converged and not snapshot.converged:
            raise SimulationError("Completed transient history contains an unconverged snapshot")
        if not isinstance(snapshot.metrics, dict) or "time_s" not in snapshot.metrics:
            raise SimulationError("Transient snapshot needs numeric time_s metadata")
        if abs(_decode(float, snapshot.metrics["time_s"]) - times[index]) > tolerance:
            raise SimulationError("Transient snapshot time metadata does not match its index")
        used += sum(_array_storage_size(array) for group in FIELD_GROUPS for fields in getattr(snapshot, group).values() for array in fields.values())
    used += len(_encoded_metadata(history))
    if used > min(project.transient.max_history_bytes, MAX_HISTORY_BYTES):
        raise SimulationError("Transient history exceeds its byte budget")
    if result.converged and not history.completed:
        from etlayout.simulation.temperature import evaluate_temperature_limits, stop_requested
        actual = evaluate_temperature_limits(project, result, time_s=float(times[-1]))
        report = result.metrics.get("thermal_limit_compliance", {})
        if (not stop_requested(actual) or report.get("actual_stop_time") != float(times[-1])
                or report.get("requested_stop_time") != project.transient.stop_time
                or result.metrics.get("engineering_accepted") is not False):
            raise SimulationError("Thermal-stop metadata must match an actual violating accepted state and requested time")
    if project.transient.initialization in {"saved", "resume"}:
        from etlayout.solvers.transient import configure_initial_state
        if not project.transient.initial_state_digest:
            raise SimulationError("Saved-state transient history is missing its initial-state digest")
        configure_initial_state(deepcopy(project), history.snapshots[0])
    for group in FIELD_GROUPS:
        last = getattr(history.snapshots[-1], group)
        current = getattr(result, group)
        if (last.keys() != current.keys()
                or any(fields.keys() != current[layer].keys() for layer, fields in last.items())
                or any(not np.array_equal(array, current[layer][name], equal_nan=True)
                       for layer, fields in last.items() for name, array in fields.items())):
            raise SimulationError("Final transient snapshot does not match the result fields")


def write_history(archive, result):
    history = result.transient
    for index, snapshot in enumerate(history.snapshots):
        arrays = {}
        for group in FIELD_GROUPS:
            keys = _field_keys(snapshot, group)
            arrays.update({keys[layer][name]: values
                           for layer, fields in getattr(snapshot, group).items() for name, values in fields.items()})
        buffer = io.BytesIO()
        np.savez_compressed(buffer, **arrays)
        archive.writestr(f"transient/frame_{index:05d}.npz", buffer.getvalue())
    archive.writestr("transient/metadata.json", _encoded_metadata(history))


def read_history(archive, result, project):
    from etlayout.io.project import _decode
    budget = min(project.transient.max_history_bytes, MAX_HISTORY_BYTES)
    used = archive.getinfo("transient/metadata.json").file_size
    if used > budget:
        raise SimulationError("Stored transient metadata exceeds its byte budget")
    metadata = json.loads(archive.read("transient/metadata.json"))
    if not isinstance(metadata, dict) or type(metadata.get("schema_version")) is not int or metadata["schema_version"] not in (1, 2):
        raise SimulationError("Unsupported transient history format")
    frames = metadata.get("snapshots")
    if not isinstance(frames, list) or not 0 < len(frames) <= min(project.transient.max_snapshots, MAX_SNAPSHOTS):
        raise SimulationError("Invalid transient snapshot count")
    history = TransientHistory(times=np.asarray(_decode(list[float], metadata["times_s"])),
                               rows=_decode(list[dict[str, Any]], metadata["rows"]),
                               completed=_decode(bool, metadata["completed"]))
    # Reject inconsistent time metadata before expanding any field payloads.
    _validate_times(history, result, project, len(frames))
    for index, frame in enumerate(frames):
        raw = archive.read(f"transient/frame_{index:05d}.npz")
        with zipfile.ZipFile(io.BytesIO(raw)) as payload:
            used += sum(item.file_size for item in payload.infolist())
        if used > budget:
            raise SimulationError("Stored transient history exceeds its byte budget")
        groups = {}
        with np.load(io.BytesIO(raw), allow_pickle=False) as arrays:
            for group in FIELD_GROUPS:
                keys = _decode(dict[str, dict[str, str]], frame.get(group, {}))
                groups[group] = {layer: {name: np.array(arrays[key]) for name, key in names.items()}
                                 for layer, names in keys.items()}
        history.snapshots.append(SimulationResult(
            mesh=result.mesh, **groups, metrics=frame["metrics"], terminals=frame["terminals"],
            field_version=_decode(int, frame.get("field_version", 1)), analysis=frame.get("analysis", {}),
            diagnostics=[Diagnostic(**item) for item in frame["diagnostics"]], iterations=frame["iterations"],
            converged=_decode(bool, frame["converged"]), configuration=result.configuration,
            configuration_hash=result.configuration_hash, physics_hash=result.physics_hash,
            execution=frame.get("execution", result.execution),
            solver_version=result.solver_version, elapsed_seconds=result.elapsed_seconds))
    result.transient = history
    validate_history(result, project)
    return history
