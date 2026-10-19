# SPDX-License-Identifier: MPL-2.0
from __future__ import annotations

import multiprocessing
import os
from concurrent.futures import FIRST_COMPLETED, ProcessPoolExecutor, wait
from copy import deepcopy
from dataclasses import dataclass, fields, is_dataclass, replace
from typing import Any, get_args, get_type_hints

import numpy as np

from etlayout.models import (
    Cancelled,
    Diagnostic,
    Project,
    SimulationError,
    SimulationResult,
    SweepSpec,
    checkpoint,
)

from .runner import run_project


@dataclass
class SweepRow:
    value: Any
    result: SimulationResult | None = None
    error: str = ""
    applied_value: Any = None

    @property
    def metrics(self):
        return self.result.metrics if self.result else {}


def sweep_values(start: float, stop: float, count: int, spacing: str = "linear") -> list[float]:
    if isinstance(count, bool) or not isinstance(count, (int, float)) or not np.isfinite(count) or count < 1 or int(count) != count:
        raise SimulationError("Sweep count must be a finite positive whole number")
    count = int(count)
    if not np.isfinite(start) or not np.isfinite(stop):
        raise SimulationError("Sweep bounds must be finite")
    if spacing == "linear":
        return np.linspace(start, stop, count).tolist()
    if spacing == "log":
        if min(start, stop) <= 0:
            raise SimulationError("Logarithmic sweep bounds must be positive")
        return np.geomspace(start, stop, count).tolist()
    raise SimulationError("Sweep spacing must be linear or log")


def _child(obj, key):
    if isinstance(obj, list):
        if key.isdecimal() and not any(hasattr(item, "id") for item in obj):
            index = int(key)
            if index < len(obj):
                return obj[index]
            raise SimulationError(f"Parameter list index '{key}' is outside the available entries")
        matches = [x for x in obj if getattr(x, "id", None) == key]
        if len(matches) != 1:
            raise SimulationError(f"Parameter path object ID '{key}' is missing or ambiguous")
        return matches[0]
    if isinstance(obj, dict):
        if key not in obj:
            raise SimulationError(f"Unknown parameter key {key}")
        return obj[key]
    if is_dataclass(obj) and not key.startswith("_") and hasattr(obj, key):
        return getattr(obj, key)
    raise SimulationError(f"Unknown parameter field {key}")


def get_parameter(project: Project, parameter: str):
    obj = project
    for key in parameter.split("."):
        obj = _child(obj, key)
    return obj


def set_parameter(project: Project, parameter: str, value: Any):
    parts = parameter.split(".")
    if len(parts) < 2 or parts[0] not in {"terminals", "layers", "materials", "interfaces", "thermal_boundaries", "heat_sources", "mesh", "solver", "transient", "power"}:
        raise SimulationError("Parameter must be a dotted path through editable project settings")
    obj = project
    for key in parts[:-1]:
        obj = _child(obj, key)
    _child(obj, parts[-1])
    if not is_dataclass(obj) or parts[-1] not in {f.name for f in fields(obj)}:
        raise SimulationError("Sweep target must be a declared scalar setting")
    annotation = get_type_hints(type(obj))[parts[-1]]
    options = get_args(annotation)
    if type(None) in options:
        if value is None:
            setattr(obj, parts[-1], None)
            return None
        annotation = next(option for option in options if option is not type(None))
    if annotation is bool:
        if type(value) is not bool:
            raise SimulationError("Boolean sweep requires true/false values")
    elif annotation in (int, float):
        if isinstance(value, bool) or value is None:
            raise SimulationError("Numeric sweep requires finite numeric values, not Boolean/null")
        try:
            numeric = float(value)
        except (ValueError, TypeError, OverflowError) as exc:
            raise SimulationError("Numeric sweep requires numeric values") from exc
        if not np.isfinite(numeric):
            raise SimulationError("Numeric sweep requires finite values")
        if annotation is int and not numeric.is_integer():
            raise SimulationError("Integer setting requires a whole number; fractional values are not rounded")
        value = annotation(numeric)
    elif annotation is str:
        if not isinstance(value, str):
            raise SimulationError("Text setting requires a string")
    else:
        raise SimulationError("Sweep target must be a scalar setting")
    setattr(obj, parts[-1], value)
    return value


def resolve_study_initial_state(project: Project, archived_result: SimulationResult | None = None, *,
                                initial_result: SimulationResult | None = None, resume: bool = False) -> dict:
    """Resolve archived replay and reject incompatible startup before a study.

    An archived run replays its FIRST frame. An explicitly supplied state uses
    the given field (the final field when a complete run is supplied). Caller
    configuration and source fields are never modified.
    """
    from etlayout.solvers.fem import Space
    from etlayout.solvers.transient import _initial_values, configure_initial_state

    from .runner import _mesh_key, _verify_layout
    candidate = deepcopy(project)
    if candidate.transient.enabled and candidate.transient.initialization in {"saved", "resume"} and initial_result is None:
        if archived_result is None or archived_result.transient is None or not archived_result.transient.snapshots:
            raise SimulationError("This study requires its saved initial thermal state. Open its result archive or select an initial-state archive before starting the study.")
        initial_result = archived_result.transient.snapshots[0]
    if not candidate.transient.enabled and initial_result is None and not resume:
        return {"initial_result": None, "resume": False}
    resume = configure_initial_state(candidate, initial_result, resume)
    if initial_result is not None:
        if not initial_result.configuration:
            raise SimulationError("Initial thermal state is missing its source configuration")
        from etlayout.io.project import project_from_dict
        source_project = project_from_dict(initial_result.configuration)
        _verify_layout(candidate)
        # Recorded layout identity remains valid after a source file is moved.
        if _mesh_key(candidate) != _mesh_key(source_project):
            raise SimulationError("Saved-state studies require identical mesh and region supports; use uniform or steady initialization for a changed geometry or mesh. State interpolation is not supported.")
        _initial_values(candidate, initial_result.mesh, Space(candidate, initial_result.mesh, electrical=False), initial_result)
        if resume:
            start = float(initial_result.metrics["time_s"])
            if start < 0 or start >= candidate.transient.stop_time:
                raise SimulationError("Continuation study stop time must be later than its saved initial time")
    return {"initial_result": initial_result, "resume": resume}


@dataclass
class _StudyPoint:
    project: Project
    initial_result: SimulationResult | None = None
    resume: bool = False


def _solve_point(point, execution, workers=1, progress=None, cancel=None):
    """Spawn-picklable point; each process has its own Gmsh/native solver state."""
    from .execution import execution_context
    try:
        threads = max(1, execution.cpu_threads // workers)
        with execution_context(replace(execution, cpu_threads=threads, study_workers=1)):
            result = run_project(point.project, progress=progress, cancel=cancel,
                                 initial_result=point.initial_result, resume=point.resume)
        result.metrics["study_execution"] = {**execution.to_dict(), "actual_workers": workers,
                                              "cpu_threads_per_worker": threads, "process_id": os.getpid()}
        return result, "" if result.converged else "Solver did not converge"
    except Cancelled:
        raise
    except (SimulationError, ValueError) as error:
        return None, str(error)


def _solve_points(points, progress=None, cancel=None, execution=None):
    """Bounded spawn workers, ordered results and prompt cancellation cleanup."""
    from .execution import validate_execution
    execution = validate_execution(execution)
    if not points:
        return []
    count = len(points)
    workers = min(execution.study_workers, count)
    if workers == 1:
        return [_solve_point(point, execution,
                    progress=lambda f, message, i=i: checkpoint(cancel, progress, (i + f) / count,
                                                                 f"Point {i + 1}/{count}: {message}"),
                    cancel=cancel) for i, point in enumerate(points)]
    if multiprocessing.current_process().daemon:
        raise SimulationError("Parallel studies require a non-daemon worker process")
    output = [None] * count
    pool = ProcessPoolExecutor(max_workers=workers, mp_context=multiprocessing.get_context("spawn"))
    pending = {}
    next_index = 0
    completed = 0
    success = False
    try:
        while next_index < count or pending:
            checkpoint(cancel, progress, completed / count, f"Study: {completed}/{count} points complete ({workers} workers)")
            while next_index < count and len(pending) < workers:
                pending[pool.submit(_solve_point, points[next_index], execution, workers)] = next_index
                next_index += 1
            done, _ = wait(pending, timeout=.1, return_when=FIRST_COMPLETED)
            for future in done:
                index = pending.pop(future)
                output[index] = future.result()
                completed += 1
        success = True
    finally:
        if not success:
            # Python 3.12 has no public terminate_workers(). Terminating the
            # pool's owned processes prevents cancel from waiting on a solve.
            processes = list(pool._processes.values())
            for process in processes:
                if process.is_alive():
                    process.terminate()
            for process in processes:
                process.join(timeout=5.)
        pool.shutdown(wait=True, cancel_futures=True)
    return output


def run_sweep(project: Project, spec: SweepSpec, progress=None, cancel=None, *,
              initial_result: SimulationResult | None = None, resume: bool = False, execution=None) -> list[SweepRow]:
    if not spec.values:
        raise SimulationError("Supply at least one sweep value")
    if project.transient.enabled:
        for index, drive in enumerate(project.transient.drives):
            if spec.parameter == drive.target:
                raise SimulationError(f"Transient waveform overrides {spec.parameter}; sweep "
                    f"transient.drives.{index}.waveform.high (or another waveform setting) instead, "
                    "or explicitly disable transient operation for a steady sweep")
    options = resolve_study_initial_state(project, initial_result=initial_result, resume=resume)
    rows, points, indices = [], [], []
    for index, value in enumerate(spec.values):
        checkpoint(cancel, progress, 0., f"Preparing sweep {index + 1}/{len(spec.values)}: {value}")
        candidate = deepcopy(project)
        candidate.solver.mode = spec.mode
        applied = None
        try:
            applied = set_parameter(candidate, spec.parameter, value)
            candidate_options = resolve_study_initial_state(candidate, **options)
            indices.append(index)
            points.append(_StudyPoint(candidate, **candidate_options))
            rows.append(SweepRow(value, applied_value=applied))
        except Cancelled:
            raise
        except (SimulationError, ValueError) as exc:
            rows.append(SweepRow(value, error=str(exc), applied_value=applied))
    for index, (result, error) in zip(indices, _solve_points(points, progress, cancel, execution)):
        row = rows[index]
        row.result, row.error = result, error
        if result is not None:
            result.metrics.update(sweep_parameter=spec.parameter, sweep_requested_value=row.value,
                                  sweep_applied_value=row.applied_value)
    checkpoint(cancel, progress, 1., "Sweep complete")
    return rows


@dataclass
class ComparisonRow:
    name: str
    result: SimulationResult | None = None
    error: str = ""

    @property
    def metrics(self):
        return self.result.metrics if self.result else {}


def compare_projects(projects: list[Project], mode: str = "electrothermal", progress=None, cancel=None, *,
                     initial_results=None, resumes=None, execution=None):
    rows = []
    if not projects:
        raise SimulationError("Select at least one design")
    initial_results = initial_results if initial_results is not None else [None] * len(projects)
    resumes = resumes if resumes is not None else [False] * len(projects)
    if len(initial_results) != len(projects) or len(resumes) != len(projects):
        raise SimulationError("Design comparison needs one initial-state/resume selection per project")
    points = []
    for i, project in enumerate(projects):
        checkpoint(cancel, progress, 0., f"Preparing design {i + 1}: {project.name}")
        candidate = deepcopy(project)
        candidate.solver.mode = mode
        options = resolve_study_initial_state(candidate, initial_result=initial_results[i], resume=resumes[i])
        points.append(_StudyPoint(candidate, **options))
    for project, (result, error) in zip(projects, _solve_points(points, progress, cancel, execution)):
        rows.append(ComparisonRow(project.name, result, error))
    checkpoint(cancel, progress, 1., "Comparison complete")
    return rows


def mesh_refinement_study(project: Project, sizes: list[float], progress=None, cancel=None,
                          profiles: list[dict] | None = None, *, initial_result: SimulationResult | None = None,
                          resume: bool = False, execution=None, local_plan=None, selected=None):
    """Refine geometry sizes and compare integrals and fixed spatial profile samples.

    Each profile specifies layer_id, field, start/end (meters), and optional count.
    Profile samples and terminal currents remain with each configuration-linked result.
    """
    from .analysis import sample_line
    if not sizes or any(not np.isfinite(size) or size <= 0 for size in sizes):
        raise SimulationError("Mesh study requires finite positive sizes in meters")
    if profiles is not None and not isinstance(profiles, list):
        raise SimulationError("Mesh profiles must be a list of profile requests")
    for profile in profiles or []:
        if not isinstance(profile, dict) or not all(key in profile for key in ("layer_id", "field", "start", "end")):
            raise SimulationError("Each profile requires layer_id, field, start and end")
        try:
            start, end = np.asarray(profile["start"], dtype=float), np.asarray(profile["end"], dtype=float)
        except (TypeError, ValueError) as exc:
            raise SimulationError("Profile endpoints must be two finite SI coordinates") from exc
        count = profile.get("count", 201)
        if (start.shape != (2,) or end.shape != (2,) or not np.isfinite([start, end]).all()
                or np.array_equal(start, end) or type(count) is not int or count < 2):
            raise SimulationError("Profile requires distinct finite 2D endpoints and an integer sample count >= 2")
    if local_plan is not None:
        from etlayout.meshing.refinement import RefinementPlan, apply_refinement_plan, screen_resources
        local_plan = RefinementPlan.from_dict(local_plan) if isinstance(local_plan, dict) else local_plan
        if (not 2 <= len(sizes) <= 4 or sizes[0] != 1. or
                any(b >= a for a, b in zip(sizes, sizes[1:]))):
            raise SimulationError("Local mesh study needs 2–4 strictly decreasing factors starting at 1 (for example 1,0.75,0.5)")
        from .execution import validate_execution
        # One refinement level at a time bounds simultaneous memory use. The
        # selected CPU thread budget still applies within each numerical solve.
        execution = replace(validate_execution(execution), study_workers=1)
    options = resolve_study_initial_state(project, initial_result=initial_result, resume=resume)
    rows, points, resource_screens = [], [], []
    for size in sizes:
        if local_plan is not None:
            candidate = apply_refinement_plan(project, local_plan, selected, factor=size)
            resource_screens.append(screen_resources(candidate, local_plan))
        else:
            resource_screens.append(None)
            candidate = deepcopy(project)
            factor = size / project.mesh.max_size
            candidate.mesh.max_size = size
            candidate.mesh.min_size = project.mesh.min_size * factor
            candidate.mesh.contact_size = project.mesh.contact_size * factor
            for region in candidate.mesh.refinements:
                region.size *= factor
                region.axis_sizes = [value * factor for value in region.axis_sizes]
            for layer in candidate.layers:
                if layer.mesh_size:
                    layer.mesh_size *= factor
        # Equal physical durations are required for discretization deltas.
        candidate.transient.periodic_tolerance = 0.
        candidate_options = resolve_study_initial_state(candidate, **options)
        points.append(_StudyPoint(candidate, **candidate_options))
    for size, (result, error), resources in zip(sizes, _solve_points(points, progress, cancel, execution), resource_screens):
        checkpoint(cancel)
        if result is None:
            rows.append(SweepRow(size, error=error))
            continue
        try:
            result.metrics["mesh_max_size_m"] = result.configuration["mesh"]["max_size"]
            if local_plan is not None:
                from etlayout.io.project import project_from_dict
                from etlayout.meshing.refinement import local_study_metrics
                result.metrics["local_refinement_factor"] = size
                result.analysis["local_refinement"] = {
                    "plan": local_plan.to_dict(), "selected": selected,
                    "planning_resources": resources,
                    "verification": "Successive mesh changes are empirical sensitivity, not a rigorous error bound."}
                result.metrics.update(local_study_metrics(project_from_dict(result.configuration), result, local_plan))
            if result.transient is not None:
                result.metrics["mesh_study_stop_time_s"] = project.transient.stop_time
            for name, terminal in result.terminals.items():
                result.metrics[f"terminal_current:{name}"] = terminal["current"]
            sampled = []
            for j, profile in enumerate(profiles or []):
                sample_result = result
                requested_time = profile.get("time_s")
                if requested_time is not None and result.transient is not None:
                    if not np.isfinite(requested_time) or not result.transient.times[0] <= requested_time <= result.transient.times[-1]:
                        raise SimulationError("Profile time lies outside the retained transient history")
                    index = int(np.argmin(np.abs(result.transient.times - requested_time)))
                    sample_result = result.transient.snapshots[index]
                cut = sample_line(sample_result, profile["layer_id"], profile["field"],
                                  profile["start"], profile["end"], profile.get("count", 201), region=profile.get("region", ""))
                cut["start"], cut["end"] = list(profile["start"]), list(profile["end"])
                if result.transient is not None:
                    cut["time_s"] = float(sample_result.metrics["time_s"])
                    cut["requested_time_s"] = requested_time
                    if requested_time is not None and not np.isclose(cut["time_s"], requested_time, rtol=1e-12, atol=0.):
                        result.diagnostics.append(Diagnostic("warning", f"Profile {j + 1} requested t={requested_time:.6g} s; "
                                                             f"nearest retained frame is t={cut['time_s']:.6g} s"))
                for key in ("distance_m", "x_m", "y_m", "value"):
                    cut[key] = cut[key].tolist()
                sampled.append(cut)
                if rows and rows[-1].result and not rows[-1].error and not error and rows[-1].result.converged and result.converged:
                    earlier = rows[-1].metrics.get("mesh_profiles", [])
                    if j < len(earlier) and earlier[j].get("time_s") == cut.get("time_s"):
                        old, new = np.asarray(earlier[j]["value"]), np.asarray(cut["value"])
                        valid = np.isfinite(old) & np.isfinite(new)
                        result.metrics[f"profile_{j}_common_samples"] = int(valid.sum())
                        if valid.any():
                            delta = new[valid] - old[valid]
                            result.metrics[f"profile_{j}_max_absolute_change"] = float(np.max(np.abs(delta)))
                            reference_norm = np.linalg.norm(old[valid])
                            if reference_norm > 0:
                                result.metrics[f"profile_{j}_relative_l2_change"] = float(np.linalg.norm(delta) / reference_norm)
            if sampled:
                result.metrics["mesh_profiles"] = sampled
            if rows and rows[-1].result and not rows[-1].error and not error and rows[-1].result.converged and result.converged:
                previous = rows[-1].metrics
                tracked = ["effective_resistance", "electrical_power", "temperature_max", "temperature_average"]
                tracked += ["transient_peak_temperature", "transient_peak_time", "transient_input_energy",
                            "transient_energy_balance_error"]
                tracked += [key for key in result.metrics if key.startswith("terminal_current:")]
                tracked += [key for key in result.metrics if key.startswith("regularized_")]
                tracked += ["device_current_sharing_error", "device_source_voltage_spread"]
                for metric in tracked:
                    if (isinstance(previous.get(metric), (int, float))
                            and isinstance(result.metrics.get(metric), (int, float))
                            and np.isfinite(previous[metric]) and np.isfinite(result.metrics[metric])):
                        result.metrics[metric + "_absolute_change"] = abs(result.metrics[metric] - previous[metric])
                        if previous[metric]:
                            result.metrics[metric + "_relative_change"] = abs(result.metrics[metric] / previous[metric] - 1)
            rows.append(SweepRow(size, result, error))
        except Cancelled:
            raise
        except (SimulationError, ValueError) as exc:
            if local_plan is not None:
                result.diagnostics.append(Diagnostic("warning", "Local mesh study measurement unavailable: " + str(exc)))
            rows.append(SweepRow(size, result if local_plan is not None else None, str(exc)))
    if local_plan is not None and rows and rows[-1].result is not None:
        from etlayout.meshing.physical import local_mesh_verification
        rows[-1].result.analysis["local_mesh_verification"] = local_mesh_verification(project, rows, local_plan, selected)
    checkpoint(cancel, progress, 1., "Mesh study complete")
    return rows


def time_step_convergence_study(project: Project, time_steps: list[float], progress=None, cancel=None, *,
                                initial_result: SimulationResult | None = None, resume: bool = False,
                                execution=None):
    """Refine maximum integration steps with the same requested output cadence.

    Output/event times still split backward-Euler steps. The report records the
    effective accepted count, peak time/temperature and integrated energy, so
    equal schedules and output-limited refinement remain visible.
    """
    if not project.transient.enabled:
        raise SimulationError("Time-step convergence requires transient operation")
    if (not time_steps or any(isinstance(value, bool) or not np.isfinite(value) or value <= 0 for value in time_steps)
            or any(later >= earlier for earlier, later in zip(time_steps, time_steps[1:]))):
        raise SimulationError("Time-step study requires strictly decreasing finite positive steps in seconds")
    options = resolve_study_initial_state(project, initial_result=initial_result, resume=resume)
    rows, points = [], []
    tracked = ("transient_peak_temperature", "transient_peak_time", "transient_input_energy",
               "transient_output_energy", "transient_stored_energy", "transient_energy_balance_error")
    for index, step in enumerate(time_steps):
        checkpoint(cancel, progress, 0., f"Preparing time-step study {index + 1}/{len(time_steps)}")
        candidate = deepcopy(project)
        candidate.transient.time_step = float(step)
        # Compare the same physical duration even when the production setup uses
        # early periodic stopping. Different stop times confound discretization.
        candidate.transient.periodic_tolerance = 0.
        points.append(_StudyPoint(candidate, **options))
    for step, (result, error) in zip(time_steps, _solve_points(points, progress, cancel, execution)):
        checkpoint(cancel)
        if result is None:
            rows.append(SweepRow(float(step), error=error, applied_value=float(step)))
            continue
        try:
            result.metrics.update(time_study_step_s=float(step),
                                  time_study_output_interval_s=project.transient.output_interval,
                                  time_study_stop_time_s=project.transient.stop_time)
            if rows and rows[-1].result and rows[-1].result.converged and result.converged:
                previous = rows[-1].metrics
                for metric in tracked:
                    old, new = previous.get(metric), result.metrics.get(metric)
                    if isinstance(old, (float, int)) and isinstance(new, (float, int)):
                        result.metrics[metric + "_absolute_change"] = abs(new - old)
                        if old:
                            result.metrics[metric + "_relative_change"] = abs(new / old - 1)
                result.metrics["time_study_same_step_count"] = result.metrics["transient_steps"] == previous.get("transient_steps")
            rows.append(SweepRow(float(step), result, error, float(step)))
        except Cancelled:
            raise
        except (SimulationError, ValueError) as error:
            rows.append(SweepRow(float(step), error=str(error), applied_value=float(step)))
    checkpoint(cancel, progress, 1., "Time-step convergence study complete")
    return rows
