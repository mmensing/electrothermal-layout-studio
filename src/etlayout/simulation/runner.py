# SPDX-License-Identifier: MPL-2.0
from __future__ import annotations

import hashlib
import json
import time
import warnings
from collections import OrderedDict
from copy import deepcopy
from pathlib import Path

from etlayout.io.project import configuration_hash, file_hash, physics_hash
from etlayout.models import Diagnostic, Project, SimulationError, SimulationResult, checkpoint
from etlayout.technology.validation import validate_project

_MESH_CACHE: OrderedDict = OrderedDict()
_GEOMETRY_CACHE: OrderedDict = OrderedDict()
_RESULT_CACHE: OrderedDict = OrderedDict()
_CACHE_BUDGET = 256 * 1024 * 1024
_GEOMETRY_CACHE_BUDGET = 64 * 1024 * 1024
_CACHE_MAX_ENTRIES = 32


def clear_caches():
    _GEOMETRY_CACHE.clear()
    _MESH_CACHE.clear()
    _RESULT_CACHE.clear()


def _mesh_bytes(mesh):
    groups = (mesh.regions, mesh.terminal_nodes, mesh.boundary_nodes, mesh.boundary_edges)
    return (mesh.points.nbytes + mesh.triangles.nbytes
            + sum(x.nbytes + 256 for group in groups for x in group.values()) + 4096)


def _geometry_bytes(geometry):
    # Conservative accounting for GEOS coordinate storage and Python containers;
    # WKB alone underestimates native geometry memory. Also bound entry count.
    groups = (geometry.layers, geometry.interfaces, geometry.terminals,
              geometry.thermal_boundaries, geometry.heat_sources, geometry.refinements)
    shapes = [geometry.domain, *(shape for group in groups for shape in group.values())]
    return (sum(4 * len(shape.wkb) + 4096 for shape in shapes)
            + sum(1024 + 4 * len(d.message) for d in geometry.diagnostics))


def _cache_bytes(cache):
    return sum(item[1] for item in cache.values())


def cache_info():
    """Return process-local cache counts and estimated retained byte budgets."""
    return {"geometry_entries": len(_GEOMETRY_CACHE), "geometry_bytes": _cache_bytes(_GEOMETRY_CACHE),
            "geometry_budget": _GEOMETRY_CACHE_BUDGET, "mesh_entries": len(_MESH_CACHE),
            "mesh_bytes": _cache_bytes(_MESH_CACHE), "mesh_budget": _CACHE_BUDGET,
            "max_entries": _CACHE_MAX_ENTRIES}


def _remember(cache, key, value, size, budget):
    if size > budget:
        return
    while cache and (_cache_bytes(cache) + size > budget or len(cache) >= _CACHE_MAX_ENTRIES):
        cache.popitem(last=False)
    cache[key] = (deepcopy(value), size)


def _verify_layout(project):
    if project.layout.path:
        if not Path(project.layout.path).is_file():
            raise SimulationError(f"Layout is missing: {project.layout.path}. Relink the source in the project editor.")
        digest = file_hash(project.layout.path)
        if project.layout.sha256 and digest != project.layout.sha256:
            raise SimulationError("Layout content changed. Reimport or relink before running.")
        project.layout.sha256 = digest


def _geometry_key(project):
    data = project.to_dict()
    selected = {key: data[key] for key in ("layout", "roi", "acknowledge_clipped_boundaries")}
    # Actual database units are read from the content and restored on cache hits.
    selected["layout"].pop("dbu_um")
    if selected["layout"].get("sha256"):
        selected["layout"]["path"] = ""
    selected["mesh"] = {k: data["mesh"][k] for k in ("min_size", "refinements")}
    selected["power_rules"] = data["power"]["rules"]
    fields = {
        "layers": {"id", "name", "geometry", "role", "thickness", "z"},
        "interfaces": {"id", "layer_a", "layer_b", "geometry", "enabled", "electrical_kind", "model_kind",
                       "height_mode", "height", "enclosure", "allow_partial", "replaces_layers"},
        "terminals": {"id", "layer_id", "geometry", "kind"},
        "thermal_boundaries": {"id", "layer_id", "geometry", "kind"},
        "heat_sources": {"id", "layer_id", "geometry", "kind"},
    }
    for group, keys in fields.items():
        selected[group] = [{k: v for k, v in item.items() if k in keys} for item in data[group]]
    return hashlib.sha256(json.dumps(selected, sort_keys=True).encode()).hexdigest()


def _mesh_key(project):
    data = project.to_dict()
    selected = {key: data[key] for key in ("layout", "roi", "acknowledge_clipped_boundaries", "mesh")}
    if selected["layout"].get("sha256"):
        selected["layout"]["path"] = ""
    for group in ("layers", "interfaces", "terminals", "thermal_boundaries", "heat_sources"):
        selected[group] = [{k: v for k, v in item.items()
                            if k in {"id", "layer_id", "layer_a", "layer_b", "geometry", "enabled", "mesh_size", "role",
                                     "model_kind", "height_mode", "height", "enclosure", "allow_partial", "replaces_layers"}}
                           for item in data[group]]
    return hashlib.sha256(json.dumps(selected, sort_keys=True).encode()).hexdigest()


def prepare_mesh(project: Project, progress=None, cancel=None, *, initial_state_supplied: bool = False,
                 initial_result: SimulationResult | None = None):
    from etlayout.geometry.prepare import prepare_geometry
    from etlayout.meshing.generate import generate_mesh
    from etlayout.simulation.execution import record_phase
    geometry_started = time.perf_counter()
    checkpoint(cancel, progress, .01, "Validating project")
    validate_project(project, initial_state_supplied=initial_state_supplied)
    # Never trust a cached source hash supplied by a caller of this lower-level API.
    _verify_layout(project)
    geometry_key = _geometry_key(project)
    if geometry_key in _GEOMETRY_CACHE:
        geometry, dbu_um = deepcopy(_GEOMETRY_CACHE[geometry_key][0])
        _GEOMETRY_CACHE.move_to_end(geometry_key)
        project.layout.dbu_um = dbu_um
        # Stack-derived height is a physical invariant, not hidden cache state.
        for interface in project.interfaces:
            if interface.model_kind == "physical_via" and interface.height_mode == "stack":
                a, b = project.layer(interface.layer_a), project.layer(interface.layer_b)
                lower, upper = sorted((a, b), key=lambda layer: layer.z)
                interface.height = upper.z - (lower.z + lower.thickness)
        checkpoint(cancel, progress, .18, "Reusing verified geometry")
    else:
        geometry = prepare_geometry(project, progress=lambda f, m: checkpoint(cancel, progress, .02 + .16*f, m), cancel=cancel)
        if not any(d.severity == "error" for d in geometry.diagnostics):
            _remember(_GEOMETRY_CACHE, geometry_key, (geometry, project.layout.dbu_um),
                      _geometry_bytes(geometry), _GEOMETRY_CACHE_BUDGET)
    failures = [d.message for d in geometry.diagnostics if d.severity == "error"]
    if failures:
        raise SimulationError("\n".join(failures))
    record_phase("preparation", time.perf_counter() - geometry_started)
    mesh_started = time.perf_counter()
    key = _mesh_key(project)
    if initial_result is not None:
        from etlayout.io.project import project_from_dict
        if not initial_result.configuration or _mesh_key(project_from_dict(initial_result.configuration)) != key:
            raise SimulationError("Saved thermal states require the original geometry and mesh settings. Use uniform or steady initialization for a changed mesh.")
        # Meshing can vary with native thread counts. An exact saved-state
        # restart must use the recorded mesh; solve checks materials, supports,
        # source fields and fixed-temperature consistency before integration.
        mesh = deepcopy(initial_result.mesh)
        checkpoint(cancel, progress, .35, "Reusing verified initial-state mesh")
    elif key in _MESH_CACHE:
        mesh = deepcopy(_MESH_CACHE[key][0])
        _MESH_CACHE.move_to_end(key)
        checkpoint(cancel, progress, .35, "Reusing conforming mesh")
    else:
        mesh = generate_mesh(project, geometry,
                             progress=lambda f, m: checkpoint(cancel, progress, .18 + .17*f, m), cancel=cancel)
        size = _mesh_bytes(mesh)
        _remember(_MESH_CACHE, key, mesh, size, _CACHE_BUDGET)
    record_phase("meshing", time.perf_counter() - mesh_started)
    return geometry, mesh


def run_project(project: Project, progress=None, cancel=None, use_cache: bool = False, *,
                initial_result: SimulationResult | None = None, resume: bool = False,
                execution=None) -> SimulationResult:
    """Run in the caller's process with bounded native CPU threads.

    Desktop callers isolate this function in a spawned process; study workers use
    one independent process per point. Execution preferences are machine-local.
    """
    from etlayout.simulation.execution import execution_context, execution_provenance, phase_timer
    with execution_context(execution, reset_counts=True):
        with phase_timer("total"):
            result = _run_project(project, progress, cancel, use_cache,
                                  initial_result=initial_result, resume=resume)
        result.execution = execution_provenance()
        if result.transient is not None:
            for snapshot in result.transient.snapshots:
                snapshot.execution = deepcopy(result.execution)
        return result


def _run_project(project: Project, progress=None, cancel=None, use_cache: bool = False, *,
                 initial_result: SimulationResult | None = None, resume: bool = False) -> SimulationResult:
    from etlayout.solvers.solve import solve
    start = time.perf_counter()
    project = deepcopy(project)
    if project.transient.enabled or initial_result is not None or resume:
        from etlayout.solvers.transient import configure_initial_state
        resume = configure_initial_state(project, initial_result, resume)
    _verify_layout(project)
    from etlayout.simulation.execution import current_execution, phase_seconds, phase_timer, record_phase
    key = (configuration_hash(project), current_execution())
    if use_cache and initial_result is None and not resume and key in _RESULT_CACHE:
        checkpoint(cancel, progress, 1., "Loaded identical cached simulation")
        with phase_timer("cached_result_load"):
            cached = deepcopy(_RESULT_CACHE[key])
            cached.elapsed_seconds = time.perf_counter() - start
            return cached
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always", RuntimeWarning)
        geometry, mesh = prepare_mesh(project, progress, cancel, initial_state_supplied=initial_result is not None,
                                      initial_result=initial_result)
        key = configuration_hash(project)  # layout import may update its actual database unit
        solve_started = time.perf_counter()
        prior_reporting = phase_seconds("reporting")
        result = solve(project, mesh, progress=lambda f, m: checkpoint(cancel, progress, .35 + .63*f, m), cancel=cancel,
                       initial_result=initial_result, resume=resume)
        record_phase("numerical", max(0., time.perf_counter() - solve_started -
                                     (phase_seconds("reporting") - prior_reporting)))
    result.configuration = project.to_dict()
    result.configuration_hash = key
    result.physics_hash = physics_hash(project)
    result.elapsed_seconds = time.perf_counter() - start
    if result.transient is not None:
        for snapshot in result.transient.snapshots:
            snapshot.elapsed_seconds = result.elapsed_seconds
    result.diagnostics = geometry.diagnostics + result.diagnostics
    for message in dict.fromkeys(str(w.message) for w in caught):
        result.diagnostics.append(Diagnostic("warning", message))
    if (project.power.measurements or project.power.devices) and (caught or "measurements" not in result.analysis):
        from etlayout.power.analysis import analyze_power
        with phase_timer("reporting"):
            analyze_power(project, result, progress=lambda _, m: checkpoint(cancel, progress, .98, m), cancel=cancel)
    if result.converged and use_cache and result.transient is None:
        bytes_used = _mesh_bytes(mesh) + sum(a.nbytes for group in (result.fields, result.element_fields, result.interface_fields)
                                             for f in group.values() for a in f.values())
        if bytes_used < _CACHE_BUDGET // 2:
            _RESULT_CACHE.clear()  # one bounded full result; mesh cache is separate
            _RESULT_CACHE[(key, current_execution())] = deepcopy(result)
    checkpoint(cancel, progress, 1., "Simulation complete" if result.converged else "Simulation did not converge")
    result.elapsed_seconds = time.perf_counter() - start
    if result.transient is not None:
        for snapshot in result.transient.snapshots:
            snapshot.elapsed_seconds = result.elapsed_seconds
    return result
