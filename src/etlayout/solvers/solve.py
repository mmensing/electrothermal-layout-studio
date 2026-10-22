# SPDX-License-Identifier: MPL-2.0
"""Public solve entry points and self-consistent electrothermal iteration."""
from __future__ import annotations

import time
from pathlib import Path

import numpy as np

from etlayout.io.project import configuration_hash, file_hash, physics_hash, refresh_threshold_diagnostics
from etlayout.models import (
    Cancel,
    Diagnostic,
    PlanarMesh,
    Progress,
    Project,
    SimulationError,
    SimulationResult,
    checkpoint,
)

from .electrical import solve_state as electrical_state
from .thermal import solve_state as thermal_state


def _validate(project: Project, mesh: PlanarMesh) -> None:
    from etlayout.simulation.temperature import validate_temperature_limits
    errors = validate_temperature_limits(project)
    if errors:
        raise SimulationError("\n".join(item.message for item in errors))
    settings = project.solver
    if settings.initial_temperature <= 0 or not np.isfinite(settings.initial_temperature):
        raise SimulationError("Initial temperature must be finite and above zero kelvin")
    if not 0 < settings.relaxation <= 1:
        raise SimulationError("Relaxation must lie in (0, 1]")
    if settings.max_iterations < 1 or settings.relative_tolerance <= 0 or settings.absolute_tolerance < 0:
        raise SimulationError("Iteration budget and convergence tolerances are invalid")
    if len(mesh.triangles) == 0:
        raise SimulationError("Mesh contains no triangles")
    for layer in project.layers:
        if layer.material_id not in project.materials:
            raise SimulationError(f"Layer {layer.id}: missing material {layer.material_id}")
    for name, items in (("layers", project.layers), ("terminals", project.terminals),
                        ("interfaces", project.interfaces), ("thermal boundaries", project.thermal_boundaries),
                        ("heat sources", project.heat_sources)):
        ids = [item.id for item in items]
        if len(set(ids)) != len(ids):
            raise SimulationError(f"Duplicate identifiers in {name}")


def _finish(result: SimulationResult, project: Project, started: float, progress=None, cancel=None) -> SimulationResult:
    result.configuration = project.to_dict()
    layout = result.configuration["layout"]
    if layout["path"] and not layout["sha256"] and Path(layout["path"]).is_file():
        layout["sha256"] = file_hash(layout["path"])
    result.configuration_hash = configuration_hash(result.configuration)
    result.physics_hash = physics_hash(project)
    result.elapsed_seconds = time.perf_counter() - started
    from etlayout.simulation.temperature import finalize_temperature_limits
    finalize_temperature_limits(project, result)
    refresh_threshold_diagnostics(result, project)
    if project.power.measurements or project.power.devices:
        from etlayout.power.analysis import analyze_power
        from etlayout.simulation.execution import phase_timer
        with phase_timer("reporting"):
            analyze_power(project, result, progress=lambda _, m: checkpoint(cancel, progress, 1., m), cancel=cancel)
    return result


def solve_electrical(project: Project, mesh: PlanarMesh, progress: Progress | None = None,
                     cancel: Cancel | None = None,
                     temperatures: dict[str, np.ndarray] | None = None) -> SimulationResult:
    """Solve DC with temperatures in K as optional global nodal arrays by layer."""
    started = time.perf_counter()
    _validate(project, mesh)
    checkpoint(cancel, progress, 0., "Assembling electrical model")
    result = electrical_state(project, mesh, temperatures, progress, cancel).result
    checkpoint(cancel, progress, 1., "Electrical solution complete" if result.converged else "Electrical solution did not converge")
    return _finish(result, project, started, progress, cancel)


def solve_thermal(project: Project, mesh: PlanarMesh, progress: Progress | None = None,
                  cancel: Cancel | None = None,
                  heat: dict[str, np.ndarray] | None = None) -> SimulationResult:
    """Solve heat flow; optional heat densities are global element arrays in W/m²."""
    started = time.perf_counter()
    _validate(project, mesh)
    checkpoint(cancel, progress, 0., "Assembling thermal model")
    result = thermal_state(project, mesh, heat=heat, progress=progress, cancel=cancel).result
    checkpoint(cancel, progress, 1., "Thermal solution complete" if result.converged else "Thermal solution did not converge")
    return _finish(result, project, started, progress, cancel)


def electrical_heat(result: SimulationResult) -> dict[str, np.ndarray]:
    """Return Joule plus allocated contact heat in W/m² for one-way thermal use.

    Pass the same result.mesh to solve_thermal; remeshing requires an explicit
    conservative transfer and must not reuse these element arrays by index.
    """
    if not result.converged:
        raise SimulationError("Cannot use heating from a nonconverged electrical result")
    if "electrical_power" not in result.metrics:
        raise SimulationError("Result does not contain an electrical solution")
    zero = np.zeros(len(result.mesh.triangles))
    return {layer: np.nan_to_num(fields.get("joule_density", zero), nan=0.) +
                   np.nan_to_num(fields.get("contact_density", zero), nan=0.)
            for layer, fields in result.fields.items()
            if "joule_density" in fields or "contact_density" in fields}


def solve(project: Project, mesh: PlanarMesh, progress: Progress | None = None,
          cancel: Cancel | None = None, *, initial_result: SimulationResult | None = None,
          resume: bool = False) -> SimulationResult:
    """Solve the configured electrical, thermal or coupled electrothermal model.

    A returned result is valid only when ``converged`` is true. Nonconverged
    finite iterates retain diagnostic fields; invalid/singular setups raise
    SimulationError. Cancellation raises Cancelled at assembly/iteration steps.
    """
    if project.transient.enabled:
        from .transient import solve_transient
        return solve_transient(project, mesh, progress, cancel, initial_result=initial_result, resume=resume)
    if initial_result is not None or resume:
        raise SimulationError("Initial/restart states require transient operation")
    if project.solver.mode == "electrical":
        return solve_electrical(project, mesh, progress, cancel)
    if project.solver.mode == "thermal":
        if project.solver.thermal_include_joule:
            started = time.perf_counter()
            electrical = solve_electrical(project, mesh, progress=lambda f, m: checkpoint(
                cancel, progress, .4 * f, m), cancel=cancel)
            result = solve_thermal(project, mesh, progress=lambda f, m: checkpoint(
                cancel, progress, .4 + .6 * f, m), cancel=cancel, heat=electrical_heat(electrical))
            for layer_id, fields in electrical.fields.items():
                result.fields.setdefault(layer_id, {}).update({k: v for k, v in fields.items() if k != "temperature"})
            for layer_id, fields in electrical.element_fields.items():
                result.element_fields.setdefault(layer_id, {}).update({k: v for k, v in fields.items() if k != "temperature"})
            result.interface_fields = electrical.interface_fields
            result.analysis.update(electrical.analysis)
            result.metrics.update(electrical.metrics)
            result.terminals = electrical.terminals
            result.diagnostics += electrical.diagnostics
            result.diagnostics.append(Diagnostic("info", "One-way Joule heating evaluated at "
                f"{project.solver.initial_temperature:g} K; use electrothermal mode for temperature feedback."))
            return _finish(result, project, started, progress, cancel)
        return solve_thermal(project, mesh, progress, cancel)
    if project.solver.mode != "electrothermal":
        raise SimulationError(f"Unknown simulation mode {project.solver.mode!r}")
    started = time.perf_counter()
    _validate(project, mesh)
    settings = project.solver
    temperatures = None
    electrical_space = thermal_space = None
    electrical_values = None
    history = []
    result = None
    converged = False
    # This state belongs only to the current coupled run. If thermal evaluation
    # preserves its temperature, the damped electrical solution is also the next
    # thermal source; solving it again would repeat assembly and factorization.
    electrical = None
    from etlayout.simulation.runner import mesh_fingerprint
    input_physics = physics_hash(project, resolve_layout=False)
    input_mesh = mesh_fingerprint(mesh)

    def check_inputs():
        # Progress/cancellation callbacks must not replace physical inputs while
        # a solved state is retained. Names, notes, display preferences and
        # reporting thresholds are excluded by the existing physics identity.
        # No layout files are reread here.
        if (physics_hash(project, resolve_layout=False) != input_physics
                or mesh_fingerprint(mesh) != input_mesh):
            raise SimulationError("Physical inputs or mesh changed during the electrothermal calculation; "
                                  "restart the calculation with the updated setup")

    for iteration in range(settings.max_iterations):
        checkpoint(cancel, progress, iteration / settings.max_iterations,
                   f"Electrothermal iteration {iteration + 1}")
        check_inputs()
        if electrical is None or not electrical.result.converged:
            electrical = electrical_state(project, mesh, temperatures, cancel=cancel,
                                           space=electrical_space, initial=electrical_values)
        electrical_space = electrical.space
        electrical_values = electrical.values
        if not electrical.result.converged:
            result = electrical.result
            result.diagnostics.append(Diagnostic("error", "Coupling stopped because the electrical subproblem did not converge"))
            break
        target = thermal_state(project, mesh, electrical.heat, temperatures, cancel=cancel, space=thermal_space)
        thermal_space = target.space
        if not target.result.converged:
            result = target.result
            result.diagnostics.append(Diagnostic("error", "Coupling stopped because the thermal subproblem did not converge"))
            break
        previous = thermal_space.values_from_fields(temperatures, settings.initial_temperature)
        difference = target.values - previous
        delta = float(np.max(np.abs(difference)))
        updated = previous + settings.relaxation * difference
        temperatures = thermal_space.element_node_fields(updated)
        # Evaluate both PDEs at exactly the same damped temperature iterate.
        # Conservation is measured, never imposed by rescaling generated heat.
        electrical = electrical_state(project, mesh, temperatures, cancel=cancel,
                                       space=electrical_space, initial=electrical_values)
        evaluated = thermal_state(project, mesh, electrical.heat, temperatures, cancel=cancel,
                                  space=thermal_space, evaluate_only=True)
        temperatures = thermal_space.element_node_fields(evaluated.values)
        electrical_values = electrical.values
        result = electrical.result
        for layer_id, fields in evaluated.result.fields.items():
            result.fields.setdefault(layer_id, {}).update(fields)
        for layer_id, fields in evaluated.result.element_fields.items():
            result.element_fields.setdefault(layer_id, {}).update(fields)
        result.analysis.update(evaluated.result.analysis)
        result.metrics.update(evaluated.result.metrics)
        result.diagnostics = [d for d in result.diagnostics if d.severity != "error"]
        tolerance = settings.absolute_tolerance + settings.relative_tolerance * float(np.max(np.abs(evaluated.values)))
        converged = (delta <= tolerance and electrical.result.converged and evaluated.result.converged)
        row = {"iteration": float(iteration + 1), "temperature_change": delta,
               "electrical_residual": result.metrics["electrical_residual"],
               "thermal_residual": result.metrics["thermal_residual"],
               "current_balance_error": result.metrics["current_balance_error"],
               "power_balance_error": result.metrics["power_balance_error"],
               "thermal_balance_error": result.metrics["thermal_balance_error"],
               "temperature_max": result.metrics["temperature_max"],
               "electrical_power": result.metrics["electrical_power"]}
        history.append(row)
        # Fixed-temperature boundaries may replace damped values during thermal
        # evaluation. Keep the nonlinear seed, but never carry heat evaluated at
        # a different temperature into the next thermal step.
        if not np.array_equal(updated, evaluated.values):
            electrical = None
        if converged:
            break
    assert result is not None
    result.iterations = history or result.iterations
    result.converged = converged
    if not converged:
        result.diagnostics.append(Diagnostic("error", f"Electrothermal solution did not converge within {len(history)} coupled iterations"))
    checkpoint(cancel, progress, 1., "Electrothermal solution complete" if converged else "Electrothermal solution did not converge")
    check_inputs()
    return _finish(result, project, started, progress, cancel)
