# SPDX-License-Identifier: MPL-2.0
"""Transient thin-layer heat flow with quasi-static electrical feedback.

Backward Euler uses a consistent or row-sum lumped heat-capacity matrix. Excitation events
and requested output times split steps; ideal edges are evaluated from the
incoming interval at its right endpoint. This is not an RLC/EM transient model.
"""
from __future__ import annotations

import hashlib
import json
import math
import time
from copy import copy, deepcopy

import numpy as np

from etlayout.models import (
    Diagnostic,
    PlanarMesh,
    Project,
    SimulationError,
    SimulationResult,
    TransientHistory,
    Waveform,
    checkpoint,
)

from .electrical import solve_state as electrical_state
from .fem import Space
from .thermal import heat_capacity
from .thermal import solve_state as thermal_state

MAX_TRANSIENT_SNAPSHOTS = 10000
MAX_TRANSIENT_HISTORY_BYTES = 1024 * 1024 * 1024


def validate_waveform(waveform: Waveform) -> None:
    if waveform.kind == "constant":
        values = [waveform.value]
    elif waveform.kind == "piecewise_linear":
        if (len(waveform.times) < 2 or len(waveform.times) != len(waveform.values)
                or np.any(np.diff(waveform.times) <= 0) or waveform.times[0] < 0):
            raise SimulationError("Piecewise-linear drive needs matching values and at least two increasing nonnegative times")
        values = waveform.times + waveform.values
    elif waveform.kind == "pulse":
        values = [waveform.low, waveform.high, waveform.delay, waveform.width,
                  waveform.period, waveform.rise_time, waveform.fall_time]
        if min(waveform.delay, waveform.period, waveform.rise_time, waveform.fall_time) < 0 or waveform.width <= 0:
            raise SimulationError("Pulse delay, period and edge times must be nonnegative; plateau width must be positive")
        active = waveform.rise_time + waveform.width + waveform.fall_time
        if waveform.period and active > waveform.period:
            raise SimulationError("Pulse rise + plateau width + fall cannot exceed its period")
    else:
        raise SimulationError(f"Unknown waveform kind {waveform.kind!r}")
    if not np.all(np.isfinite(values)):
        raise SimulationError("Waveform parameters must be finite")


def waveform_value(waveform: Waveform, time_s: float, *, left_limit: bool = False,
                   interval_start: float | None = None) -> float:
    """Evaluate one validated waveform in its target's SI unit.

    For an event-aligned interval, ``interval_start`` selects the incoming pulse
    segment without floating-point modulo ambiguity at an exact pulse edge.
    Continuous ramp values are still evaluated at the interval endpoint.
    """
    endpoint = time_s
    if waveform.kind == "constant":
        return waveform.value
    if waveform.kind == "piecewise_linear":
        return float(np.interp(time_s, waveform.times, waveform.values))
    incoming_interval = left_limit and interval_start is not None
    classification_time = (interval_start + endpoint) / 2 if incoming_interval else time_s
    offset = classification_time - waveform.delay
    if offset < 0:
        return waveform.low
    if waveform.period:
        cycle = math.floor(offset / waveform.period)
        if not incoming_interval:
            # Restore exact event-side semantics when subtraction/division puts
            # an ideal edge a few ulps into the preceding or following period.
            nearest = round(offset / waveform.period)
            edge = waveform.delay + nearest * waveform.period
            tolerance = 8 * np.spacing(max(abs(time_s), abs(edge), np.finfo(float).tiny))
            if abs(time_s - edge) <= tolerance:
                cycle = nearest - (1 if left_limit and time_s > 0 else 0)
            if cycle < 0:
                return waveform.low
        start = waveform.delay + cycle * waveform.period
    else:
        start = waveform.delay
    offset = classification_time - start
    endpoint_offset = (endpoint if interval_start is not None else time_s) - start
    rise, width, fall = waveform.rise_time, waveform.width, waveform.fall_time
    if not incoming_interval:
        for boundary in (0., rise, rise + width, rise + width + fall):
            tolerance = 8 * np.spacing(max(abs(time_s), abs(start + boundary), np.finfo(float).tiny))
            if abs(offset - boundary) <= tolerance:
                endpoint_offset = boundary
                offset = float(np.nextafter(boundary, -np.inf)) if left_limit and time_s > 0 else boundary
                break
    if offset < 0:
        return waveform.low
    if rise and offset < rise:
        fraction = float(np.clip(endpoint_offset / rise, 0., 1.))
        return waveform.low + (waveform.high - waveform.low) * fraction
    if offset < rise + width:
        return waveform.high
    if fall and offset < rise + width + fall:
        fraction = float(np.clip((endpoint_offset - rise - width) / fall, 0., 1.))
        return waveform.high + (waveform.low - waveform.high) * fraction
    return waveform.low


def _drive_target(project, target):
    parts = target.split(".")
    if len(parts) == 4 and parts[:2] == ["power", "ports"] and parts[3] == "value":
        port = next((port for port in project.power.ports if port.id == parts[2]), None)
        if port is None or port.kind not in {"current", "voltage"}:
            raise SimulationError(f"Transient power port {parts[2]} must exist and use current or voltage drive")
        if project.solver.mode != "electrothermal" and not project.solver.thermal_include_joule:
            raise SimulationError("Port waveforms require electrothermal mode or thermal mode with Joule heating")
        return port
    if len(parts) != 3 or parts[0] not in {"terminals", "heat_sources"} or parts[2] != "value":
        raise SimulationError(f"Unsupported transient drive {target!r}; choose terminals.<id>.value, power.ports.<id>.value or heat_sources.<id>.value")
    item = next((item for item in getattr(project, parts[0]) if item.id == parts[1]), None)
    if item is None:
        raise SimulationError(f"Transient drive refers to missing {target}")
    if parts[0] == "terminals" and any(landing.terminal_id == item.id for port in project.power.ports for landing in port.landings):
        raise SimulationError(f"Terminal {item.id} is a physical-port landing; drive power.ports.<port_id>.value instead")
    if parts[0] == "terminals" and item.kind not in {"voltage", "current", "density", "gate"}:
        raise SimulationError(f"Transient terminal {item.id} must use voltage, current, density or gate")
    if parts[0] == "terminals" and project.solver.mode != "electrothermal" and not project.solver.thermal_include_joule:
        raise SimulationError("Terminal waveforms require electrothermal mode or thermal mode with Joule heating")
    return item


def validate_transient(project: Project, *, initial_state_supplied: bool = False) -> None:
    settings = project.transient
    if settings.initialization not in {"uniform", "steady", "saved", "resume"}:
        raise SimulationError("Transient initialization must be uniform, steady, saved or resume")
    if settings.capacity_scheme not in {"consistent", "lumped"}:
        raise SimulationError("Transient capacity scheme must be consistent or lumped")
    if (not isinstance(settings.initial_state_digest, str) or settings.initial_state_digest
            and (len(settings.initial_state_digest) != 64 or any(c not in "0123456789abcdef" for c in settings.initial_state_digest))):
        raise SimulationError("Initial thermal state digest must be an empty value or SHA-256 hex digest")
    if type(settings.retain_peak) is not bool:
        raise SimulationError("Transient retain_peak must be true or false")
    if not np.isfinite(settings.periodic_tolerance) or settings.periodic_tolerance < 0:
        raise SimulationError("Periodic temperature tolerance must be finite and nonnegative")
    for name in ("periodic_min_cycles", "periodic_required_cycles"):
        if type(getattr(settings, name)) is not int or getattr(settings, name) < 2:
            raise SimulationError(f"Transient {name} must be an integer of at least two")
    if project.solver.mode not in {"thermal", "electrothermal"}:
        raise SimulationError("Transient operation requires thermal or electrothermal mode")
    if not np.all(np.isfinite([settings.stop_time, settings.time_step, settings.output_interval])) or min(
            settings.stop_time, settings.time_step, settings.output_interval) <= 0:
        raise SimulationError("Transient stop time, maximum time step and output interval must be finite and positive")
    for name, minimum in (("max_steps", 1), ("max_snapshots", 2), ("max_history_bytes", 1)):
        value = getattr(settings, name)
        if type(value) is not int or value < minimum:
            raise SimulationError(f"Transient {name} must be an integer of at least {minimum}")
    if settings.max_snapshots > MAX_TRANSIENT_SNAPSHOTS:
        raise SimulationError(f"Transient snapshot budget cannot exceed the portable archive limit of {MAX_TRANSIENT_SNAPSHOTS}")
    if settings.max_history_bytes > MAX_TRANSIENT_HISTORY_BYTES:
        raise SimulationError("Transient history byte budget cannot exceed the portable archive limit of 1 GiB")
    paths = [drive.target for drive in settings.drives]
    if len(paths) != len(set(paths)):
        raise SimulationError("Each transient target may have only one waveform")
    for drive in settings.drives:
        _drive_target(project, drive.target)
        validate_waveform(drive.waveform)
    if settings.periodic_tolerance:
        _periodic_spec(project)
    for boundary in project.thermal_boundaries:
        if not initial_state_supplied and settings.initialization == "uniform" and boundary.kind == "temperature" and not math.isclose(
                boundary.value, project.solver.initial_temperature, rel_tol=1e-12, abs_tol=1e-10):
            raise SimulationError(f"Transient initial temperature must match fixed-temperature boundary {boundary.id}; "
                                  "an instantaneous imposed temperature jump is not supported")


def _periodic_spec(project):
    waves = [drive.waveform for drive in project.transient.drives if drive.waveform.kind != "constant"]
    if not waves or any(w.kind != "pulse" or w.period <= 0 for w in waves):
        raise SimulationError("Periodic convergence needs repeating pulse drives with a common period (plus optional constants)")
    period = waves[0].period
    if any(not math.isclose(w.period, period, rel_tol=1e-12, abs_tol=0.) for w in waves):
        raise SimulationError("Periodic convergence requires all pulse drives to have the same period")
    return period, max(w.delay for w in waves)


def time_schedule(project: Project, *, start_time: float = 0., initial_state_supplied=False) -> tuple[np.ndarray, np.ndarray]:
    """Bounded event-aligned integration grid and saved-output grid, both in s."""
    validate_transient(project, initial_state_supplied=initial_state_supplied)
    settings = project.transient
    stop = settings.stop_time
    if not np.isfinite(start_time) or start_time < 0 or start_time >= stop:
        raise SimulationError("Transient start time must be nonnegative and earlier than stop time")
    counts = [(stop - start_time) / settings.time_step, (stop - start_time) / settings.output_interval]
    if not np.all(np.isfinite(counts)) or max(counts) > settings.max_steps:
        raise SimulationError("Transient time grid exceeds max_steps; increase the time step/output interval or budget")
    steps, outputs = (math.ceil(value) for value in counts)
    if steps > settings.max_steps or outputs > settings.max_steps:
        raise SimulationError("Transient time grid exceeds max_steps; increase the time step/output interval or budget")
    if outputs + 1 > settings.max_snapshots:
        raise SimulationError("Requested output times exceed max_snapshots; increase output interval or snapshot budget")
    def uniform_grid(interval):
        first = math.floor(start_time / interval) + 1
        last = math.ceil(stop / interval)
        return np.unique(np.r_[start_time, np.minimum(np.arange(first, last + 1) * interval, stop)])
    output_times = uniform_grid(settings.output_interval)
    if len(output_times) > settings.max_snapshots:
        raise SimulationError("Requested output times exceed max_snapshots; increase output interval or snapshot budget")
    times = uniform_grid(settings.time_step).tolist() + output_times.tolist()
    for drive in settings.drives:
        wave = drive.waveform
        if wave.kind == "piecewise_linear":
            times.extend(t for t in wave.times if start_time <= t <= stop)
        elif wave.kind == "pulse":
            first_cycle = max(0, math.floor((start_time - wave.delay) / wave.period)) if wave.period else 0
            count = (stop - wave.delay) / wave.period - first_cycle if wave.period and wave.delay <= stop else 0.
            if not np.isfinite(count) or count + 1 > settings.max_steps:
                raise SimulationError("Pulse events exceed max_steps; shorten the run or increase the period")
            cycles = max(0, math.floor(count) + 1) if wave.period else 1
            if cycles > settings.max_steps:
                raise SimulationError("Pulse events exceed max_steps; shorten the run or increase the period")
            for cycle in range(first_cycle, first_cycle + cycles):
                start = wave.delay + cycle * wave.period
                times.extend(t for t in (start, start + wave.rise_time,
                    start + wave.rise_time + wave.width,
                    start + wave.rise_time + wave.width + wave.fall_time) if start_time <= t <= stop)
            if len(times) > 8 * settings.max_steps + 8:
                raise SimulationError("Too many transient drive events for the configured step budget")
    if settings.periodic_tolerance:
        period, origin = _periodic_spec(project)
        first = max(0, math.ceil((start_time - origin) / period))
        count = max(0, math.floor((stop - origin) / period) - first + 1)
        if count > settings.max_steps:
            raise SimulationError("Periodic cycle grid exceeds max_steps")
        times.extend(origin + cycle * period for cycle in range(first, first + count))
    # Arithmetic such as 3 * period and 6 * dt may differ by a few ulps.
    # Merge these aliases without losing physically distinct short pulses.
    ordered = sorted(set(times))
    merged = [ordered[0]]
    for value in ordered[1:]:
        if value - merged[-1] > 8 * np.spacing(max(abs(value), abs(merged[-1]))):
            merged.append(value)
        elif value == stop:
            merged[-1] = value
    if len(merged) - 1 > settings.max_steps:
        raise SimulationError("Event-aligned transient grid exceeds max_steps; increase the step budget")
    # Select the exact integration value for each requested output alias.
    grid = np.asarray(merged)
    above = np.minimum(np.searchsorted(grid, output_times), len(grid) - 1)
    below = np.maximum(above - 1, 0)
    indices = np.where(np.abs(grid[above] - output_times) < np.abs(grid[below] - output_times), above, below)
    output_times = grid[indices]
    return grid, np.unique(output_times)


def _apply_drives(project, time_s, *, left_limit, interval_start=None):
    values = {}
    for drive in project.transient.drives:
        value = waveform_value(drive.waveform, time_s, left_limit=left_limit, interval_start=interval_start)
        _drive_target(project, drive.target).value = value
        values[drive.target] = value
    return values


def _periodic_cycle_steps(project, grid):
    """Match each physical cycle boundary to one canonical integration index.

    Relative comparisons against an absolute timestamp can turn adjacent time
    steps into duplicate cycle checks after a long-time restart. The schedule
    already contains these boundaries; only its few-ulp alias merging applies.
    """
    period, origin = _periodic_spec(project)
    first = max(1, math.floor((float(grid[0]) - origin) / period) + 1)
    last = math.ceil((float(grid[-1]) - origin) / period)
    output = {}
    for cycle in range(first, last + 1):
        time_s = origin + cycle * period
        if time_s > grid[-1] + 8 * np.spacing(max(abs(time_s), abs(float(grid[-1])))):
            continue
        right = min(int(np.searchsorted(grid, time_s)), len(grid) - 1)
        left = max(right - 1, 0)
        index = min((left, right), key=lambda i: abs(float(grid[i]) - time_s))
        tolerance = 8 * np.spacing(max(abs(time_s), abs(float(grid[index])), np.finfo(float).tiny))
        if abs(float(grid[index]) - time_s) > tolerance:
            raise SimulationError("Periodic boundary is missing from the canonical time grid")
        if index == 0:
            continue
        if index in output:
            raise SimulationError("Pulse period cannot be resolved at this absolute time; use a smaller time origin")
        output[index] = cycle
    return output


def _merge(electrical, thermal):
    result = thermal.result
    if electrical is not None:
        for layer, fields in electrical.result.fields.items():
            result.fields.setdefault(layer, {}).update({k: v for k, v in fields.items() if k != "temperature"})
        for layer, fields in electrical.result.element_fields.items():
            result.element_fields.setdefault(layer, {}).update({k: v for k, v in fields.items() if k != "temperature"})
        result.interface_fields = electrical.result.interface_fields
        result.analysis.update(electrical.result.analysis)
        result.metrics.update(electrical.result.metrics)
        result.terminals = electrical.result.terminals
        result.diagnostics += electrical.result.diagnostics
        result.converged &= electrical.result.converged
    return result


def _step(project, mesh, previous, thermal_space, capacity, dt, cancel):
    settings = project.solver
    temperatures = thermal_space.element_node_fields(previous)
    electrical = None
    electrical_space = electrical_values = None
    coupled = settings.mode == "electrothermal"
    include_electrical = coupled or settings.thermal_include_joule
    if not coupled:
        if include_electrical:
            electrical = electrical_state(project, mesh, cancel=cancel)
            if not electrical.result.converged:
                return electrical.result, previous
        thermal = thermal_state(project, mesh, heat=electrical.heat if electrical else None,
                                temperatures=temperatures, cancel=cancel, space=thermal_space,
                                previous_values=previous, time_step=dt, capacity_matrix=capacity)
        return _merge(electrical, thermal), thermal.values
    records = []
    result = None
    for iteration in range(settings.max_iterations):
        checkpoint(cancel)
        electrical = electrical_state(project, mesh, temperatures, cancel=cancel,
                                       space=electrical_space, initial=electrical_values)
        electrical_space, electrical_values = electrical.space, electrical.values
        if not electrical.result.converged:
            return electrical.result, previous
        thermal = thermal_state(project, mesh, electrical.heat, temperatures, cancel=cancel,
                                space=thermal_space, previous_values=previous,
                                time_step=dt, capacity_matrix=capacity)
        if not thermal.result.converged:
            return _merge(electrical, thermal), thermal.values
        old = thermal_space.values_from_fields(temperatures, settings.initial_temperature)
        delta = float(np.max(np.abs(thermal.values - old)))
        updated = old + settings.relaxation * (thermal.values - old)
        temperatures = thermal_space.element_node_fields(updated)
        electrical = electrical_state(project, mesh, temperatures, cancel=cancel,
                                       space=electrical_space, initial=electrical_values)
        electrical_values = electrical.values
        evaluated = thermal_state(project, mesh, electrical.heat, temperatures, cancel=cancel,
                                  space=thermal_space, evaluate_only=True, previous_values=previous,
                                  time_step=dt, capacity_matrix=capacity)
        result = _merge(electrical, evaluated)
        tolerance = settings.absolute_tolerance + settings.relative_tolerance * float(np.max(updated))
        records.append({"iteration": float(iteration + 1), "temperature_change": delta,
                        "thermal_residual": result.metrics["thermal_residual"],
                        "thermal_balance_error": result.metrics["thermal_balance_error"]})
        if delta <= tolerance and result.converged:
            result.iterations = records
            return result, evaluated.values
    assert result is not None
    result.converged = False
    result.iterations = records
    result.diagnostics.append(Diagnostic("error", "Transient electrothermal step exceeded its nonlinear iteration budget"))
    return result, evaluated.values


def _snapshot_bytes(result):
    return (sum(a.nbytes for group in (result.fields, result.element_fields, result.interface_fields)
                for fields in group.values() for a in fields.values())
            + 8192 + 512 * len(result.iterations))


def configure_initial_state(project: Project, source: SimulationResult | None, resume: bool = False) -> bool:
    """Bind a supplied state into a project copy's physical identity before solving.

    Saved/resume projects require their archived first snapshot (or the matching
    source state) on replay. They never silently fall back to uniform startup.
    """
    settings = project.transient
    resume = resume or settings.initialization == "resume"
    if source is None:
        if resume or settings.initialization == "saved":
            raise SimulationError("This project requires its saved initial thermal state; load its result history or supply an initial-state archive")
        if settings.initialization in {"uniform", "steady"}:
            settings.initial_state_digest = ""
        return False
    if not settings.enabled:
        raise SimulationError("Initial/restart states require transient operation")
    digest = hashlib.sha256(b"etlayout-initial-thermal-state-v1")
    for layer, fields in sorted(source.fields.items()):
        if "temperature" in fields:
            array = np.asarray(fields["temperature"], dtype="<f8")
            digest.update(json.dumps([layer, list(array.shape)]).encode("utf-8"))
            digest.update(array.tobytes())
            local = source.element_fields.get(layer, {}).get("temperature")
            if local is not None:
                array = np.asarray(local, dtype="<f8")
                digest.update(json.dumps([layer, "element_temperature", list(array.shape)]).encode("utf-8"))
                digest.update(array.tobytes())
    if resume:
        keys = ("time_s", "transient_input_energy", "transient_output_energy", "transient_energy_reference_offset")
        try:
            metadata = {key: float(source.metrics[key]) for key in keys}
            digest.update(json.dumps(metadata, sort_keys=True, allow_nan=False).encode("ascii"))
        except (KeyError, TypeError, ValueError) as error:
            raise SimulationError("Saved transient state lacks finite restart energy metadata; initialize from it at t=0 instead") from error
    identity = digest.hexdigest()
    if settings.initial_state_digest and settings.initial_state_digest != identity:
        raise SimulationError("Supplied thermal state does not match this project's initial-state digest")
    settings.initialization = "resume" if resume else "saved"
    settings.initial_state_digest = identity
    return resume


def estimate_transient_resources(project: Project, mesh: PlanarMesh | None = None, *, start_time=0.) -> dict:
    """Preflight the exact event/output grid and a conservative field-storage estimate.

    Byte estimates are unavailable before meshing, and are estimates rather than
    an archive-size guarantee. Runtime and archive checks enforce actual limits.
    """
    grid, outputs = time_schedule(project, start_time=start_time)
    reserve = int(project.transient.retain_peak and len(grid) > len(outputs))
    estimated = None
    if mesh is not None:
        n, m = len(mesh.points), len(mesh.triangles)
        thermal_layers = sum(bool(len(mesh.elements(layer.id))) for layer in project.layers)
        electrical_layers = sum(layer.role == "conductor" and bool(len(mesh.elements(layer.id))) for layer in project.layers)
        fields = 8 * thermal_layers * (n + 6 * m)
        if project.solver.mode == "electrothermal" or project.solver.thermal_include_joule:
            fields += 8 * electrical_layers * (n + 13 * m)
            fields += 8 * len(project.interfaces) * 10 * m
        frame = fields + 8192 + 512 * project.solver.max_iterations
        row = 2048 + 256 * (len(project.transient.drives) + len(project.terminals))
        estimated = int((len(outputs) + reserve) * frame + len(grid) * row)
    return {"steps": len(grid) - 1, "output_snapshots": len(outputs),
            "peak_reserve_snapshots": reserve, "estimated_history_bytes": estimated,
            "fits_snapshot_budget": len(outputs) + reserve <= project.transient.max_snapshots,
            "fits_history_budget": None if estimated is None else estimated <= project.transient.max_history_bytes}


def _initial_values(project, mesh, space, source):
    """Require exact mesh/support and thermal material compatibility; never interpolate."""
    if not source.converged and source.transient is None:
        raise SimulationError("Initial thermal state must be converged or an accepted state of an incomplete transient")
    if source.transient is not None:
        if not source.transient.snapshots or not source.transient.snapshots[-1].converged:
            raise SimulationError("Initial transient state must end at a converged accepted snapshot")
        accepted = source.transient.snapshots[-1]
        for layer, fields in source.fields.items():
            if "temperature" in fields and not np.array_equal(
                    fields["temperature"], accepted.fields.get(layer, {}).get("temperature"), equal_nan=True):
                raise SimulationError("Initial transient thermal fields must match the last accepted snapshot")
    if not source.configuration:
        raise SimulationError("Initial thermal state is missing its source configuration")
    for key in ("layers", "materials", "interfaces"):
        def physical(data):
            data = deepcopy(data)
            items = data.values() if isinstance(data, dict) else data
            for item in items:
                for annotation in ("name", "visible", "color", "opacity", "notes", "source", "version", "reference"):
                    item.pop(annotation, None)
            return data
        if key not in source.configuration or physical(source.configuration[key]) != physical(project.to_dict()[key]):
            raise SimulationError(f"Initial thermal state has incompatible {key}")
    if (not np.array_equal(mesh.points, source.mesh.points)
            or not np.array_equal(mesh.triangles, source.mesh.triangles)
            or mesh.regions.keys() != source.mesh.regions.keys()
            or any(not np.array_equal(value, source.mesh.regions[key]) for key, value in mesh.regions.items())):
        raise SimulationError("Initial thermal state must use the identical mesh and region supports")
    fields = {}
    for key, layer in space.layers.items():
        data = source.element_fields.get(key, {}).get("temperature")
        if data is not None:
            if data.shape != (len(mesh.triangles), 3) or not np.all(np.isfinite(data[layer.elements])):
                raise SimulationError(f"Initial thermal state is missing finite element temperatures for layer {key}")
        else:
            data = source.fields.get(key, {}).get("temperature")
            if data is None or data.shape != (len(mesh.points),) or not np.all(np.isfinite(data[layer.nodes])):
                raise SimulationError(f"Initial thermal state is missing finite temperatures for layer {key}")
            if len(np.unique(layer.nodes)) != len(layer.nodes):
                raise SimulationError("Legacy nodal temperatures cannot restart independent point-touch thermal domains; recompute")
        fields[key] = data
    values = space.values_from_fields(fields, project.solver.initial_temperature)
    if np.any(values <= 0):
        raise SimulationError("Initial thermal state temperatures must be positive")
    for boundary in project.thermal_boundaries:
        if boundary.kind == "temperature":
            dofs = space.nodes_for(boundary.layer_id, "thermal", boundary.id)
            if not np.allclose(values[dofs], boundary.value, rtol=1e-12, atol=1e-10):
                raise SimulationError(f"Initial thermal state does not match fixed-temperature boundary {boundary.id}")
    return values


def solve_transient(project: Project, mesh: PlanarMesh, progress=None, cancel=None, *,
                    initial_result: SimulationResult | None = None, resume: bool = False) -> SimulationResult:
    """Integrate the configured transient; failed steps retain only accepted history.

    The outer result is the last accepted state. ``converged`` means all time
    steps finished and passed nonlinear and energy checks, not steady state.
    Cancellation raises Cancelled; it never returns a partially valid run.
    """
    from etlayout.simulation.temperature import (
        accumulate_temperature_limits,
        evaluate_temperature_limits,
        stop_requested,
    )

    from .solve import _finish, _validate

    started = time.perf_counter()
    project = deepcopy(project)
    resume = configure_initial_state(project, initial_result, resume)
    _validate(project, mesh)
    validate_transient(project, initial_state_supplied=initial_result is not None)
    original = deepcopy(project)
    project = deepcopy(project)
    settings = project.transient
    checkpoint(cancel, progress, 0., "Preparing transient thermal capacity")
    space = Space(project, mesh, electrical=False)
    capacity = heat_capacity(project, space)
    previous = np.full(space.size, project.solver.initial_temperature)
    source = initial_result
    if resume and source is None:
        raise SimulationError("Resume requires a saved transient thermal state")
    if source is None and settings.initialization == "steady":
        from .solve import solve
        bias = deepcopy(project)
        _apply_drives(bias, 0., left_limit=False)
        bias.transient.enabled = False
        checkpoint(cancel, progress, 0., "Solving steady bias for transient initialization")
        source = solve(bias, mesh, cancel=cancel)
    if source is not None:
        previous = _initial_values(project, mesh, space, source)
    start_time = 0.
    input_energy = output_energy = 0.
    energy_reference = float(np.sum(capacity @ (previous - project.solver.initial_temperature)))
    provenance = {"kind": "steady" if settings.initialization == "steady" and initial_result is None else
                  ("resume" if resume else "saved") if source is not None else "uniform"}
    provenance["thermal_state_sha256"] = hashlib.sha256(np.asarray(previous, dtype="<f8").tobytes()).hexdigest()
    if source is not None:
        provenance.update(configuration_hash=source.configuration_hash, physics_hash=source.physics_hash,
                          source_time_s=source.metrics.get("time_s"))
    if resume:
        if source.metrics.get("state_kind") not in {"initial_condition", "transient_snapshot"}:
            raise SimulationError("Resume requires a transient snapshot with time and cumulative energy metadata")
        try:
            start_time = float(source.metrics["time_s"])
            input_energy = float(source.metrics["transient_input_energy"])
            output_energy = float(source.metrics["transient_output_energy"])
            energy_reference = float(source.metrics["transient_energy_reference_offset"])
        except (KeyError, TypeError, ValueError) as error:
            raise SimulationError("Saved transient state lacks restart energy metadata; initialize from it at t=0 instead") from error
        if not np.isfinite([start_time, input_energy, output_energy, energy_reference]).all():
            raise SimulationError("Saved restart time and energies must be finite")
        # The energy baseline is defined relative to the original reference temperature.
        source_reference = source.configuration.get("solver", {}).get("initial_temperature")
        if source_reference != project.solver.initial_temperature:
            raise SimulationError("Resume requires the same reference initial temperature")
    grid, output_times = time_schedule(project, start_time=start_time, initial_state_supplied=source is not None)
    drive_values = _apply_drives(project, start_time, left_limit=False)
    electrical = None
    if project.solver.mode == "electrothermal" or project.solver.thermal_include_joule:
        electrical = electrical_state(project, mesh, space.element_node_fields(previous)
                                      if project.solver.mode == "electrothermal" else None, cancel=cancel)
        if not electrical.result.converged:
            raise SimulationError("Initial electrical state did not converge")
    initial = thermal_state(project, mesh, electrical.heat if electrical else None,
                            space.element_node_fields(previous), cancel=cancel, space=space,
                            evaluate_only=True, previous_values=previous, time_step=1., capacity_matrix=capacity)
    current = _merge(electrical, initial)
    # The initial snapshot is the exact supplied state. Reconstructing it via
    # element-node averaging in an evaluate-only assembly can change a last bit,
    # which would make a saved startup's digest fail on replay.
    for layer in space.layers:
        current.fields[layer]["temperature"] = space.nodal_field(layer, previous)
        current.element_fields.setdefault(layer, {})["temperature"] = space.element_node_fields(previous)[layer]
    current.metrics.update(temperature_max=float(previous.max()), temperature_min=float(previous.min()),
                           thermal_stored_energy=float(np.sum(capacity @ (previous - project.solver.initial_temperature))))
    current.converged = True
    current.diagnostics = [d for d in current.diagnostics if d.severity != "error"]
    current.metrics.update(time=start_time, time_s=start_time, time_step=0., state_kind="initial_condition",
                           thermal_storage_rate=None, thermal_residual=None, thermal_balance_error=None)
    current.metrics["thermal_stored_energy"] -= energy_reference
    history = TransientHistory()
    saved_times = []
    history_bytes = 0
    peak_temperature = float(current.metrics["temperature_max"])
    peak_time = start_time
    peak_result = current
    failure = None
    periodic_converged = False
    periodic_cycle = 0
    periodic_change = None
    previous_cycle = None
    matched_cycles = 0
    periodic_steps = _periodic_cycle_steps(project, grid) if settings.periodic_tolerance else {}
    bound_undershoot = bound_overshoot = 0.
    bound_violation_steps = positive_offdiagonal_count = 0
    first_bound_violation_time = None
    configuration = original.to_dict()

    def retain(result, time_s, save_fields, drive_values):
        nonlocal history_bytes
        result.metrics.update(transient_input_energy=input_energy, transient_output_energy=output_energy,
                              transient_energy_reference_offset=energy_reference,
                              transient_initialization=provenance, transient_start_time=start_time,
                              drive_time_convention="right_limit" if time_s == start_time else "left_limit_at_step_end",
                              drive_values=dict(drive_values), applied_drives=dict(drive_values))
        result.configuration = configuration
        row = {"time": float(time_s), "time_s": float(time_s), "time_step": float(result.metrics["time_step"]),
               "temperature_max": result.metrics["temperature_max"],
               "temperature_min": result.metrics["temperature_min"],
               "temperature_average": result.metrics["temperature_average"],
               "thermal_power": result.metrics["thermal_power"], "heat_out": result.metrics["heat_out"],
               "thermal_storage_rate": result.metrics["thermal_storage_rate"],
               "thermal_stored_energy": result.metrics["thermal_stored_energy"],
               "input_energy": input_energy, "output_energy": output_energy,
               "energy_balance_error": abs(input_energy - output_energy - result.metrics["thermal_stored_energy"]),
               "thermal_balance_error": result.metrics["thermal_balance_error"],
               "electrical_power": result.metrics.get("electrical_power"),
               "current_density_max": result.metrics.get("current_density_max"),
               "drives": dict(drive_values), "terminals": deepcopy(result.terminals),
               "nonlinear_iterations": len(result.iterations),
               "thermal_bound_undershoot": result.metrics.get("thermal_bound_undershoot", 0.),
               "thermal_bound_overshoot": result.metrics.get("thermal_bound_overshoot", 0.),
               "thermal_bound_violation": result.metrics.get("thermal_bound_violation", False),
               "drive_time_convention": result.metrics["drive_time_convention"]}
        size = 2048 + 256 * (len(drive_values) + len(result.terminals))
        if save_fields:
            size += _snapshot_bytes(result)
        if history_bytes + size > settings.max_history_bytes:
            raise SimulationError("Transient output exceeds max_history_bytes; increase output interval or the memory budget")
        history_bytes += size
        history.rows.append(row)
        if save_fields:
            result.configuration = configuration
            result.metrics["drive_time_convention"] = row["drive_time_convention"]
            result.metrics["drive_values"] = dict(drive_values)
            result.metrics["applied_drives"] = dict(drive_values)
            history.snapshots.append(result)
            saved_times.append(float(time_s))

    # Conservative preflight includes scalar history; no unbounded arrays are retained.
    expected_bytes = len(output_times) * _snapshot_bytes(current) + len(grid) * (
        2048 + 256 * (len(drive_values) + len(current.terminals)))
    if expected_bytes > settings.max_history_bytes:
        raise SimulationError("Requested transient history exceeds max_history_bytes; increase output interval or the memory budget")
    limit_support_cache = {}  # immutable mesh/selection geometry, bounded to this integration
    limit_report = evaluate_temperature_limits(original, current, time_s=start_time, _support_cache=limit_support_cache)
    thermal_stopped = stop_requested(limit_report)
    current.metrics["thermal_limit_compliance"] = deepcopy(limit_report)
    retain(current, start_time, True, drive_values)
    output_set = set(output_times.tolist())
    for step, time_s in enumerate(grid[1:], 1):
        if thermal_stopped:
            break
        checkpoint(cancel, progress, step / (len(grid) - 1),
                   f"Transient step {step}/{len(grid) - 1}, t={time_s:.6g} s")
        dt = float(time_s - grid[step - 1])
        drive_values = _apply_drives(project, float(time_s), left_limit=True, interval_start=float(grid[step - 1]))
        candidate, values = _step(project, mesh, previous, space, capacity, dt, cancel)
        if not candidate.converged:
            failure = candidate
            break
        previous = values
        current = candidate
        current.metrics.update(time=float(time_s), time_s=float(time_s), time_step=dt, state_kind="transient_snapshot")
        current.metrics["thermal_stored_energy"] -= energy_reference
        input_energy += float(current.metrics["thermal_power"]) * dt
        output_energy += float(current.metrics["heat_out"]) * dt
        temperature = float(current.metrics["temperature_max"])
        bound_undershoot = max(bound_undershoot, current.metrics.get("thermal_bound_undershoot", 0.))
        bound_overshoot = max(bound_overshoot, current.metrics.get("thermal_bound_overshoot", 0.))
        positive_offdiagonal_count = max(positive_offdiagonal_count,
                                        current.metrics.get("thermal_positive_offdiagonal_count", 0))
        if current.metrics.get("thermal_bound_violation"):
            bound_violation_steps += 1
            if first_bound_violation_time is None:
                first_bound_violation_time = float(time_s)
        if temperature > peak_temperature:
            peak_temperature, peak_time = temperature, float(time_s)
            peak_result = current
        accepted_limits = evaluate_temperature_limits(original, current, time_s=float(time_s), _support_cache=limit_support_cache)
        limit_report = accumulate_temperature_limits(limit_report, accepted_limits)
        current.metrics["thermal_limit_compliance"] = deepcopy(accepted_limits)
        thermal_stopped = stop_requested(accepted_limits) and float(time_s) < settings.stop_time
        retain(current, float(time_s), float(time_s) in output_set or thermal_stopped, drive_values)
        history.rows[-1]["thermal_limit_status"] = accepted_limits["status"]
        if thermal_stopped:
            break
        if step in periodic_steps:
            periodic_cycle = periodic_steps[step]
            if previous_cycle is not None:
                periodic_change = float(np.max(np.abs(previous - previous_cycle)))
                matched_cycles = matched_cycles + 1 if periodic_change <= settings.periodic_tolerance else 0
                if periodic_cycle >= settings.periodic_min_cycles and matched_cycles >= settings.periodic_required_cycles:
                    periodic_converged = True
            previous_cycle = previous.copy()
            history.rows[-1].update(periodic_cycle=periodic_cycle, periodic_temperature_change=periodic_change,
                                    periodic_consecutive_matches=matched_cycles)
            if periodic_converged:
                break
    if saved_times[-1] != current.metrics["time"]:
        # Preserve the last accepted state if failure occurred between outputs.
        if len(history.snapshots) >= settings.max_snapshots or history_bytes + _snapshot_bytes(current) > settings.max_history_bytes:
            raise SimulationError("Cannot retain the last accepted transient state within the configured output budget")
        current.configuration = configuration
        current.metrics["drive_time_convention"] = "left_limit_at_step_end"
        current.metrics["drive_values"] = history.rows[-1]["drives"]
        current.metrics["applied_drives"] = history.rows[-1]["drives"]
        history.snapshots.append(current)
        saved_times.append(float(current.metrics["time"]))
        history_bytes += _snapshot_bytes(current)
    peak_retained = peak_time in saved_times
    if settings.retain_peak and not peak_retained:
        peak_bytes = _snapshot_bytes(peak_result)
        if len(history.snapshots) < settings.max_snapshots and history_bytes + peak_bytes <= settings.max_history_bytes:
            index = int(np.searchsorted(saved_times, peak_time))
            history.snapshots.insert(index, peak_result)
            saved_times.insert(index, peak_time)
            history_bytes += peak_bytes
            peak_retained = True
    history.times = np.asarray(saved_times)
    history.completed = failure is None and not thermal_stopped
    result = copy(current)
    result.metrics = dict(current.metrics)
    result.diagnostics = list(current.diagnostics)
    result.transient = history
    # A user-requested engineering stop does not invalidate accepted PDE states.
    result.converged = failure is None
    limit_report.update(stopped_early=thermal_stopped, requested_stop_time=settings.stop_time,
                        actual_stop_time=float(history.rows[-1]["time_s"]))
    result.metrics["thermal_limit_compliance"] = limit_report
    stored = float(current.metrics["thermal_stored_energy"])
    result.metrics.update(transient_completed=history.completed, transient_steps=len(history.rows) - 1,
                          transient_snapshot_count=len(history.snapshots), transient_time=float(history.rows[-1]["time"]),
                          transient_requested_stop_time=settings.stop_time,
                          transient_peak_temperature=peak_temperature, transient_peak_time=peak_time,
                          transient_peak_scope="current_segment" if resume else "whole_run",
                          transient_energy_scope="cumulative_from_origin" if resume else "whole_run",
                          transient_peak_retained=peak_retained,
                          transient_peak_snapshot_index=saved_times.index(peak_time) if peak_retained else None,
                          transient_periodic_converged=periodic_converged,
                          transient_periodic_cycles=periodic_cycle,
                          transient_periodic_temperature_change=periodic_change,
                          transient_stop_reason="maximum_temperature" if thermal_stopped else "periodic_convergence" if periodic_converged else "stop_time" if failure is None else "failed_step",
                          transient_input_energy=input_energy, transient_output_energy=output_energy,
                          transient_stored_energy=stored,
                          transient_energy_balance_error=abs(input_energy - output_energy - stored),
                          transient_history_bytes=history_bytes)
    result.metrics.update(transient_capacity_scheme=settings.capacity_scheme,
                          transient_bound_undershoot=bound_undershoot, transient_bound_overshoot=bound_overshoot,
                          transient_bound_violation_steps=bound_violation_steps,
                          transient_first_bound_violation_time=first_bound_violation_time,
                          transient_positive_offdiagonal_count=positive_offdiagonal_count)
    if bound_violation_steps:
        result.diagnostics = [d for d in result.diagnostics if not d.message.startswith("Transient temperature violates")]
        result.diagnostics.append(Diagnostic("warning", f"Transient temperature bound violations occurred in {bound_violation_steps} "
            f"accepted steps, starting at t={first_bound_violation_time:.6g} s; maximum undershoot {bound_undershoot:.6g} K, "
            f"overshoot {bound_overshoot:.6g} K. Conservation/convergence alone does not establish monotonicity. "
            "Check space/time convergence and the capacity scheme; fields were not clipped."))
    if settings.capacity_scheme == "lumped" and positive_offdiagonal_count:
        result.diagnostics.append(Diagnostic("info", "Lumped capacity is enabled, but positive off-diagonal operator entries remain. "
            "Mesh anisotropy/angles and exchange terms can still prevent monotonicity; lumping alone is not a guarantee."))
    if settings.retain_peak and not peak_retained:
        result.diagnostics.append(Diagnostic("warning", "Peak temperature was measured, but its exact field could not be retained within the snapshot/byte budget"))
    if settings.periodic_tolerance and not periodic_converged and failure is None and not thermal_stopped:
        result.diagnostics.append(Diagnostic("warning", "Requested stop time reached before periodic convergence. "
                                            "The transient segment completed, but a periodic thermal state was not established."))
    if failure is not None:
        result.diagnostics += failure.diagnostics
        result.diagnostics.append(Diagnostic("error", f"Transient integration stopped before t={time_s:.6g} s; "
                                            "fields and history show the last accepted state"))
    result.diagnostics.append(Diagnostic("info", "Transient heat flow uses backward Euler and quasi-static electrical states; "
                                        "electrical capacitance, inductance and wave propagation are not modeled."))
    result = _finish(result, original, started, progress, cancel)
    for snapshot in history.snapshots:
        snapshot.configuration_hash = result.configuration_hash
        snapshot.physics_hash = result.physics_hash
        snapshot.elapsed_seconds = result.elapsed_seconds
        snapshot.metrics["elapsed_seconds_scope"] = "whole_run"
    checkpoint(cancel, progress, 1., "Transient stopped at maximum-temperature limit" if thermal_stopped else "Transient simulation complete" if history.completed else "Transient integration stopped")
    return result
