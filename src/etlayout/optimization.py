# SPDX-License-Identifier: MPL-2.0
"""Deterministic, resumable equal-service search over legal physical layouts.

Search rows are provisional. Only comparable mesh-refined finalists whose
improvement exceeds both meshes' changes can become verified. Every published
layout is independently reimported, solved and checked again in both formats.
"""
from __future__ import annotations

import hashlib
import json
import math
import os
import platform
import shutil
import sys
import tempfile
import time
from copy import deepcopy
from dataclasses import asdict
from importlib.metadata import version
from pathlib import Path

import numpy as np

from etlayout import __version__
from etlayout.geometry.prepare import prepare_geometry
from etlayout.io.project import (
    file_hash,
    load_project,
    physics_hash,
    project_from_dict,
    save_json,
    save_project,
)
from etlayout.models import Cancelled, RefinementRegion, RegionSpec, SimulationError, checkpoint
from etlayout.power.analysis import analyze_power
from etlayout.power.measurement_contract import has_acceptance_limit, validate_limits
from etlayout.routing import export_candidate, generate_candidate, validate_rules
from etlayout.routing.geometry import canonical_geometry_id
from etlayout.simulation.runner import run_project
from etlayout.simulation.sweep import set_parameter

HISTORY_VERSION = 1
FEASIBLE = {"feasible_provisional", "verified_feasible", "mesh_uncertain"}


class SearchBudgetExceeded(Exception):
    """Cooperative deadline reached at a numerical/geometry checkpoint."""


def _digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()).hexdigest()


def _atomic_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=path.name + ".", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            json.dump(value, stream, indent=2, allow_nan=False)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if Path(temporary).exists():
            Path(temporary).unlink()


def _implementation_identity(package, *, frozen=False):
    package = Path(package)
    if frozen:
        stamp = package / "data" / "implementation.json"
        if not stamp.is_file():
            raise SimulationError("Frozen package lacks implementation identity; rebuild before running experiments")
        digest = json.loads(stamp.read_text(encoding="utf-8")).get("source_sha256", "")
        if len(digest) != 64 or any(c not in "0123456789abcdef" for c in digest):
            raise SimulationError("Frozen implementation identity is invalid; rebuild the application")
        return digest
    source = {p.relative_to(package).as_posix(): file_hash(p) for p in sorted(package.rglob("*.py"))}
    return _digest(source)


def _identity(project, experiment):
    return {"history_version": HISTORY_VERSION, "software": __version__, "python": platform.python_version(),
            "implementation_sha256": _implementation_identity(Path(__file__).parent, frozen=getattr(sys, "frozen", False)),
            "dependencies": {name: version(name) for name in ("numpy", "scipy", "shapely", "gmsh", "klayout")},
            "physics_hash": physics_hash(project), "experiment": asdict(experiment)}


def validate_experiment(project, experiment):
    if project.transient.enabled:
        raise SimulationError("Routing optimization currently requires steady electrical/electrothermal scenarios")
    if project.solver.mode == "thermal":
        raise SimulationError("Equal-service optimization requires an electrical solution")
    if not experiment.scenarios or not experiment.objectives:
        raise SimulationError("An experiment requires operating scenarios and scoped objectives")
    if experiment.max_evaluations < 1 or experiment.finalists < 1 or not np.isfinite(experiment.max_wall_seconds) or experiment.max_wall_seconds <= 0:
        raise SimulationError("Search requires positive evaluation, finalist and wall-time budgets")
    factors = experiment.refinement_factors
    if len(factors) < 2 or factors[0] != 1 or any(not np.isfinite(f) or f <= 0 for f in factors) or any(a <= b for a, b in zip(factors, factors[1:])):
        raise SimulationError("Verification requires descending positive mesh factors starting at 1")
    if not np.isfinite(experiment.maximum_mesh_change) or experiment.maximum_mesh_change <= 0 or experiment.improvement_margin < 0:
        raise SimulationError("Invalid mesh uncertainty or improvement policy")
    measurements = {m.id: m for m in project.power.measurements}
    if len(measurements) != len(project.power.measurements):
        raise SimulationError("Duplicate measurement IDs")
    for objective in experiment.objectives:
        spec = measurements.get(objective.measurement_id)
        if spec is None or spec.quantity == "effective_current_density":
            raise SimulationError("Objectives require scoped physical measurements; reporting-thickness conversions cannot rank candidates")
        validate_limits(spec)
        if objective.direction not in {"min", "max"} or objective.scenario_aggregation not in {"worst", "weighted_mean"}:
            raise SimulationError("Invalid objective direction or scenario aggregation")
        if spec.quantity in {"metal_current_density", "sheet_current", "contact_normal_density"} and spec.aggregation == "maximum" and spec.averaging_length <= 0:
            raise SimulationError("Peak current objectives require a fixed physical hotspot averaging length")
    for key in experiment.constraints:
        spec = measurements.get(key)
        if spec is None or not has_acceptance_limit(spec) or spec.quantity == "effective_current_density":
            raise SimulationError("Constraints need existing physical measurements with explicit limits")
        validate_limits(spec)
    ports = {p.id for p in project.power.ports} | {t.id for t in project.terminals}
    for scenario in experiment.scenarios:
        if (scenario.service_port not in ports or scenario.reference_port not in ports
                or scenario.service_port == scenario.reference_port):
            raise SimulationError("Each scenario needs distinct existing service/reference ports or terminals")
        if (scenario.service_mode not in {"target_current", "fixed_voltage"}
                or not np.isfinite(scenario.required_current) or scenario.required_current <= 0
                or not np.isfinite(scenario.compliance_voltage) or scenario.compliance_voltage <= 0
                or not 0 <= scenario.service_tolerance < 1 or not np.isfinite(scenario.weight) or scenario.weight <= 0):
            raise SimulationError("Each scenario needs positive delivered current/compliance and a valid service tolerance")
        for key in scenario.settings:
            if not key.startswith(("terminals.", "power.ports.", "solver.initial_temperature", "thermal_boundaries.", "layers.")):
                raise SimulationError(f"Scenario setting {key} is outside the supported bias/thermal conditions")
            if key.startswith("layers.") and not key.endswith((".gate_voltage", ".threshold_voltage")):
                raise SimulationError("Scenario layer settings may change gate/threshold bias, not active geometry or material parameters")
    for items, label in ((experiment.scenarios, "scenario"), (experiment.variables, "variable")):
        if len({item.id for item in items}) != len(items) or any(not item.id for item in items):
            raise SimulationError(f"Experiment requires unique nonempty {label} IDs")
    recipes = {r.id: r for r in project.power.recipes}
    if set(experiment.recipe_ids) - recipes.keys():
        raise SimulationError("Experiment references a stale routing recipe")
    for variable in experiment.variables:
        if variable.recipe_id not in recipes or variable.recipe_id not in experiment.recipe_ids:
            raise SimulationError("Design variables must belong to an enabled experiment recipe")
        if variable.parameter not in recipes[variable.recipe_id].parameters:
            raise SimulationError("Design variable names an absent recipe parameter")
        if variable.kind not in {"continuous", "integer", "choice"}:
            raise SimulationError("Unknown design variable kind")
        if variable.kind == "choice" and not variable.values:
            raise SimulationError("Choice variable requires explicit choices")
        if variable.kind != "choice" and (not np.isfinite([variable.minimum, variable.maximum, variable.step]).all()
                                           or variable.maximum < variable.minimum or variable.step < 0):
            raise SimulationError("Design variable bounds must be finite and ordered")
        if variable.kind == "integer" and any(float(v) != int(v) for v in [variable.minimum, variable.maximum, variable.step, *variable.values]):
            raise SimulationError("Integer design variables must use whole-number bounds, step and values")
    if set(experiment.protected_devices) - {d.id for d in project.power.devices}:
        raise SimulationError("Experiment references a stale protected device group")


def parameter_candidates(project, experiment):
    """Enumerate small spaces; reproducibly sample grid indices in large spaces."""
    axes = []
    for variable in experiment.variables:
        if variable.values:
            values = list(dict.fromkeys(variable.values))
            if variable.kind != "choice" and any(not variable.minimum <= v <= variable.maximum for v in values):
                raise SimulationError("Explicit variable values exceed their declared bounds")
        else:
            step = variable.step or (1 if variable.kind == "integer" else project.power.rules.dbu)
            count = int(np.floor((variable.maximum-variable.minimum)/step + 1e-9)) + 1
            if count > 10**12:
                raise SimulationError("Variable grid has more than 10^12 positions; specify a coarser step")
            values = (variable.minimum, step, count)
        axes.append(values)
    counts = [len(v) if isinstance(v, list) else v[2] for v in axes]
    total = math.prod(counts)
    if total > np.iinfo(np.int64).max:
        raise SimulationError("Combined parameter grid exceeds 64-bit indexing; reduce bounds or increase steps")
    budget = max(0, experiment.max_evaluations - 1)
    if total <= budget:
        indices = range(total)
    else:
        rng = np.random.default_rng(experiment.seed)
        chosen = set()
        if budget:
            chosen.add(total-1)  # include the upper corner, often wider/lower-loss routing
        while len(chosen) < min(total, budget):
            chosen.add(int(rng.integers(total)))
        indices = sorted(chosen)
    for flat in indices:
        value = {}
        for variable, axis, count in reversed(list(zip(experiment.variables, axes, counts))):
            index = flat % count
            flat //= count
            v = axis[index] if isinstance(axis, list) else axis[0] + index * axis[1]
            value[variable.id] = int(v) if variable.kind == "integer" else v
        yield value


def _source(project, key):
    return next((p for p in project.power.ports if p.id == key), None) or next(t for t in project.terminals if t.id == key)


def _scenario(project, scenario):
    candidate = deepcopy(project)
    for key, value in scenario.settings.items():
        set_parameter(candidate, key, value)
    source, reference = _source(candidate, scenario.service_port), _source(candidate, scenario.reference_port)
    reference.kind, reference.value = "ground", 0.
    if scenario.service_mode == "target_current":
        source.kind, source.value = "current", scenario.required_current
    elif source.kind not in {"voltage", "ground"}:
        raise SimulationError("Fixed-voltage service requires a voltage-driven service port")
    return candidate


def _service(result, scenario):
    rows = {**result.terminals, **result.analysis.get("ports", {})}
    supply, reference = rows.get(scenario.service_port, {}), rows.get(scenario.reference_port, {})
    current = supply.get("current")
    voltage = supply.get("voltage")
    return_current = reference.get("current")
    drop = None if voltage is None or reference.get("voltage") is None else voltage-reference["voltage"]
    finite = all(v is not None and np.isfinite(v) for v in (current, return_current, drop))
    # Positive current flows into supply and out of the selected return; a leak
    # into another terminal cannot satisfy the requested source/load service.
    delivered = min(current, -return_current) if finite else None
    tolerance = scenario.service_tolerance
    adequate = finite and delivered >= scenario.required_current * (1-tolerance)
    if scenario.service_mode == "target_current":
        adequate = adequate and abs(delivered-scenario.required_current) <= max(scenario.required_current*tolerance, 1e-12)
    adequate = adequate and abs(drop) <= scenario.compliance_voltage
    return {"status": "satisfied" if adequate else "failed", "delivered_current": delivered,
            "required_current": scenario.required_current, "voltage": drop,
            "compliance_voltage": scenario.compliance_voltage, "service_mode": scenario.service_mode,
            "service_tolerance": tolerance}


def _protected(project, experiment):
    geometry = prepare_geometry(project)
    active = {layer.id for layer in project.layers if layer.electrical.kind in {"hemt", "semiconductor", "sheet"}}
    from shapely import union_all

    from etlayout.power.analysis import _region_geometry
    device_geometry = {}
    for group in project.power.devices:
        if group.id in experiment.protected_devices:
            selector = group.selector
            support = {}
            for layer in project.layers:
                if ((selector.layers and layer.id not in selector.layers)
                        or (selector.materials and layer.material_id not in selector.materials)
                        or (selector.nets and layer.net_id not in selector.nets)):
                    continue
                shape = geometry.layers[layer.id]
                if selector.geometry:
                    shape = shape.intersection(_region_geometry(project, selector.geometry, geometry.domain))
                if selector.contacts:
                    shape = shape.intersection(union_all([geometry.interfaces[key] for key in selector.contacts]))
                if selector.ports:
                    ids = [landing.terminal_id for port in project.power.ports if port.id in selector.ports for landing in port.landings]
                    shape = shape.intersection(union_all([geometry.terminals[key] for key in ids]))
                if selector.regions or selector.devices:
                    raise SimulationError("Protected device groups require physical geometry/layer/contact/port selectors; replace mesh-relative regions or nested groups")
                if not shape.is_empty:
                    support[layer.id] = canonical_geometry_id(shape)
            if not support:
                raise SimulationError("Protected active device has empty physical support")
            device_geometry[group.id] = support
    return {"layers": {layer.id: {"geometry": canonical_geometry_id(geometry.layers[layer.id]),
                      "electrical": asdict(layer.electrical), "thickness": layer.thickness,
                      "material": asdict(project.materials[layer.material_id])}
                       for layer in project.layers if layer.id in active},
            "devices": [asdict(d) for d in project.power.devices if d.id in experiment.protected_devices],
            "device_geometry": device_geometry,
            "electrodes": {terminal.id: {"geometry": canonical_geometry_id(geometry.terminals[terminal.id]),
                                           "layer": terminal.layer_id, "kind": terminal.kind}
                           for terminal in project.terminals},
            "active_contacts": [{"interface": asdict(interface),
                                 "geometry": canonical_geometry_id(geometry.interfaces[interface.id])}
                                for interface in project.interfaces if interface.enabled and
                                {interface.layer_a, interface.layer_b}.intersection(active)],
            "loads": [{"interface": asdict(i), "geometry": canonical_geometry_id(geometry.interfaces[i.id])}
                      for i in project.interfaces if i.purpose == "device_load" or i.electrical_kind == "load"]}


def _balance_ok(project, result):
    for name, tolerance in (("current_balance_error", project.solver.current_balance_tolerance),
                            ("power_balance_error", project.solver.power_balance_tolerance),
                            ("thermal_balance_error", project.solver.thermal_balance_tolerance)):
        value = result.metrics.get(name)
        required = name != "thermal_balance_error" or project.solver.mode == "electrothermal"
        if required and (value is None or not np.isfinite(value) or value > tolerance):
            return False
    return True


def _objectives(experiment, scenarios):
    values = {}
    for objective in experiment.objectives:
        raw = [row["measurements"][objective.measurement_id]["value"] for row in scenarios]
        if any(x is None or not np.isfinite(x) for x in raw):
            raise SimulationError("Objective unavailable")
        if objective.scenario_aggregation == "weighted_mean":
            value = np.average(raw, weights=[s.weight for s in experiment.scenarios])
        else:
            value = max(raw) if objective.direction == "min" else min(raw)
        values[objective.measurement_id] = float(value)
    return values


def _classify_exception(error):
    message = str(error).lower()
    if "mesh" in message or "gmsh" in message:
        return "mesh_failure"
    if "selector" in message:
        return "stale_selector"
    return "invalid_physical_model"


def _evaluate(project, experiment, folder, *, cancel=None, progress=None, execution=None):
    folder = Path(folder)
    folder.mkdir(parents=True, exist_ok=True)
    record = {"status": "feasible_provisional", "scenarios": [], "objectives": {}, "reason": ""}
    started = time.perf_counter()
    for index, scenario in enumerate(experiment.scenarios):
        checkpoint(cancel, progress, index/len(experiment.scenarios), f"Scenario {scenario.id}")
        try:
            setup = _scenario(project, scenario)
            result = run_project(setup, cancel=cancel, execution=execution)
            if "measurements" not in result.analysis:
                analyze_power(setup, result)
            service = _service(result, scenario)
            measurements = result.analysis["measurements"]
            row = {"id": scenario.id, "service": service, "measurements": measurements,
                   "metrics": result.metrics, "contacts": result.analysis.get("contacts", {}),
                   "loss_budget": result.analysis.get("loss_budget", {}),
                   "nodes": len(result.mesh.points), "elements": len(result.mesh.triangles),
                   "elapsed_seconds": result.elapsed_seconds, "converged": result.converged,
                   "diagnostics": [asdict(d) for d in result.diagnostics]}
            needed = {o.measurement_id for o in experiment.objectives} | set(experiment.constraints)
            if not result.converged:
                status, reason = "nonconverged", "Numerical solve did not converge"
            elif not _balance_ok(setup, result):
                status, reason = "balance_failure", "Conservation tolerance exceeded or unavailable"
            elif any("outside" in d.message.lower() and ("temperature" in d.message.lower() or "range" in d.message.lower()) for d in result.diagnostics):
                status, reason = "invalid_physical_model", "Material calibration range exceeded"
            elif service["status"] != "satisfied":
                status, reason = "service_failure", "Delivered current or voltage compliance requirement failed"
            elif any(measurements[k]["status"] != "available" for k in needed):
                status, reason = "stale_selector", "; ".join(measurements[k]["reason"] for k in needed if measurements[k]["status"] != "available")
            elif any(not measurements[k]["within_limits"] for k in experiment.constraints):
                status, reason = "constraint_failure", "Scoped measurement limit exceeded"
            else:
                status, reason = "feasible_provisional", ""
            row.update(status=status, reason=reason)
            archive = folder / f"scenario-{index}.etl"
            save_project(project_from_dict(result.configuration), archive, result)
            row.update(archive=str(archive.resolve()), archive_sha256=file_hash(archive))
            record["scenarios"].append(row)
            if status != "feasible_provisional" and record["status"] == "feasible_provisional":
                record.update(status=status, reason=f"{scenario.id}: {reason}")
        except (Cancelled, SearchBudgetExceeded):
            raise
        except SimulationError as error:
            status = _classify_exception(error)
            record["scenarios"].append({"id": scenario.id, "status": status, "reason": str(error)})
            record.update(status=status, reason=f"{scenario.id}: {error}")
        except Exception as error:
            record["scenarios"].append({"id": scenario.id, "status": "internal_error", "reason": f"{type(error).__name__}: {error}"})
            record.update(status="internal_error", reason=f"{type(error).__name__}: {error}")
    if record["status"] == "feasible_provisional":
        record["objectives"] = _objectives(experiment, record["scenarios"])
    record["elapsed_seconds"] = time.perf_counter()-started
    return record


def pareto_front(rows, objectives):
    candidates = [row for row in rows if row.get("status") in FEASIBLE and row.get("objectives")
                  and all(np.isfinite(row["objectives"].get(o.measurement_id, np.nan)) for o in objectives)]
    def vector(row):
        return np.array([row["objectives"][o.measurement_id] * (1 if o.direction == "min" else -1) for o in objectives])
    return [row["id"] for row in candidates if not any(
        np.all(vector(other) <= vector(row)) and np.any(vector(other) < vector(row)) for other in candidates)]


def ranking_rows(history, resolution=None):
    """Return feasible objective views at one common verification level.

    Coarse results remain inspectable after refinement. Refined views require
    feasible evidence on the finest requested mesh, including every scenario;
    failed export verification cannot remain on the accepted refined frontier.
    The returned dictionaries do not replace the recorded candidate evidence.
    """
    resolution = resolution or history.get("pareto_resolution", "coarse")
    if resolution not in {"coarse", "refined"}:
        raise SimulationError("Ranking resolution must be coarse or refined")
    output = []
    requested = history.get("identity", {}).get("experiment", {}).get("refinement_factors", [])
    for row in history.get("rows", []):
        evidence = row.get("verification", {})
        levels = evidence.get("levels", [])
        if resolution == "coarse":
            evaluation = levels[0] if levels else row
            objectives = row.get("coarse_objectives", evaluation.get("objectives", {}))
            status = "feasible_provisional"
        else:
            factors = requested or evidence.get("factors", [])
            if len(factors) < 2 or len(levels) != len(factors) or row.get("export_status") == "verification_failed":
                continue
            evaluation = levels[-1]
            objectives = evaluation.get("objectives", {})
            status = row.get("status")
        if evaluation.get("status") not in FEASIBLE or not objectives or status not in FEASIBLE:
            continue
        output.append({**row, "objectives": dict(objectives), "status": status,
                       "objective_resolution": resolution})
    return output


def _update_pareto(history, objectives):
    coarse, refined = ranking_rows(history, "coarse"), ranking_rows(history, "refined")
    history["coarse_pareto"] = pareto_front(coarse, objectives)
    history["refined_pareto"] = pareto_front(refined, objectives)
    history["pareto_resolution"] = "refined" if refined else "coarse"
    history["pareto"] = list(history[history["pareto_resolution"] + "_pareto"])
    history["ranking_definition"] = "Separate coarse and finest-feasible mesh frontiers; default to refined when available"
    refined_ids = {row["id"] for row in refined}
    for row in history["rows"]:
        row["objective_resolution"] = "refined" if row["id"] in refined_ids else "coarse"


def _mesh_metrics(evaluation):
    values = dict(evaluation["objectives"])
    for row in evaluation["scenarios"]:
        prefix = row["id"] + ":"
        for key in ("delivered_current", "voltage"):
            values[prefix+key] = row["service"][key]
        for key in ("electrical_power", "temperature_max"):
            if row["metrics"].get(key) is not None:
                values[prefix+key] = row["metrics"][key]
        for key, measurement in row.get("measurements", {}).items():
            if measurement.get("status") == "available" and measurement.get("value") is not None:
                values[prefix+"measurement:"+key] = measurement["value"]
        for key, contact in row.get("contacts", {}).items():
            if contact.get("current_a_to_b") is not None:
                values[prefix+key] = contact["current_a_to_b"]
    return values


def _refined_project(project, factor, previous=None):
    candidate = deepcopy(project)
    candidate.mesh.max_size *= factor
    candidate.mesh.min_size *= factor
    candidate.mesh.contact_size *= factor
    for layer in candidate.layers:
        if layer.mesh_size:
            layer.mesh_size *= factor
    for region in candidate.mesh.refinements:
        region.size *= factor
        region.axis_sizes = [value * factor for value in region.axis_sizes]
    guidance = []
    seen_hotspots = set()
    for scenario in (previous or {}).get("scenarios", []):
        centers = [(record.get("window_center"), record.get("specification", {}).get("averaging_length", 0.))
                   for record in scenario.get("measurements", {}).values() if record.get("window_center")]
        hotspot = scenario.get("metrics", {}).get("hotspot")
        if hotspot:
            centers.append(([hotspot["x"], hotspot["y"]], project.mesh.max_size*2))
        for center, length in centers:
            identity = (*center, length)
            if identity in seen_hotspots:
                continue
            seen_hotspots.add(identity)
            x, y = center
            region = RegionSpec(polygons=[{"bounds": [x-length/2, y-length/2, x+length/2, y+length/2]}])
            size = max(candidate.mesh.min_size, min(candidate.mesh.max_size, length/4*factor))
            candidate.mesh.refinements.append(RefinementRegion(f"verification-hotspot-{len(seen_hotspots)}", region, size))
            guidance.append({"hotspot": center, "window_length_m": length, "target_size_m": size, "resolved": True})
    # Use solved local constitutive states, including temperature-dependent
    # metals and biased HEMT sheets. The smallest directional conductance gives
    # the shortest local transfer length across the recorded scenarios.
    coarse_states = [load_project(row["archive"])[1] for row in (previous or {}).get("scenarios", [])
                     if row.get("archive")]
    for interface in candidate.interfaces:
        if not interface.enabled or interface.electrical_kind != "specific":
            continue
        from etlayout.meshing.refinement import contact_transfer_estimate
        for layer_id in (interface.layer_a, interface.layer_b):
            length, estimate = contact_transfer_estimate(candidate, interface, layer_id, coarse_states)
            if length is None:
                guidance.append({"interface": interface.id, "layer": layer_id, "resolved": False,
                                 "reason": "Local sheet conductance unavailable for transfer-length estimate"})
                continue
            if length > 0:
                size = max(candidate.mesh.min_size, min(candidate.mesh.contact_size, length/4))
                candidate.mesh.refinements.append(RefinementRegion(f"verification-{interface.id}-{layer_id}", deepcopy(interface.geometry), size))
                guidance.append({"interface": interface.id, "layer": layer_id, "transfer_length_m": length,
                                 "target_size_m": size, "resolved": size <= length/4*(1+1e-12),
                                 "estimate": estimate})
    return candidate, guidance


def _refine(project, experiment, coarse, folder, *, cancel=None, execution=None):
    first = {key: deepcopy(coarse[key]) for key in ("status", "scenarios", "objectives")}
    evidence = {"levels": [first], "factors": experiment.refinement_factors, "guidance": [],
                "status": "mesh_uncertain", "uncertainty_definition": "absolute difference of last two meshes; empirical estimate, not a rigorous error bound"}
    for index, factor in enumerate(experiment.refinement_factors[1:], 1):
        refined, guidance = _refined_project(project, factor, coarse)
        evidence["guidance"].extend(guidance)
        value = _evaluate(refined, experiment, Path(folder)/f"mesh-{index}", cancel=cancel, execution=execution)
        evidence["levels"].append(value)
        if value["status"] != "feasible_provisional":
            evidence["reason"] = "Refined mesh is not feasible"
            return evidence
    a, b = map(_mesh_metrics, evidence["levels"][-2:])
    if a.keys() != b.keys():
        evidence["reason"] = "Tracked physical quantities changed identity during mesh verification"
        return evidence
    changes = {key: abs(b[key]-a[key]) for key in a}
    relative = {key: changes[key]/max(abs(a[key]), abs(b[key]), 1e-12) for key in a}
    evidence.update(absolute_changes=changes, relative_changes=relative,
                    objectives=evidence["levels"][-1]["objectives"])
    if max(relative.values(), default=np.inf) <= experiment.maximum_mesh_change and all(g["resolved"] for g in evidence["guidance"]):
        evidence["status"] = "mesh_resolved"
    else:
        evidence["reason"] = "Mesh changes exceed policy or contact transfer-length target remains unresolved"
    return evidence


def _artifacts_valid(row):
    records = list(row.get("scenarios", []))
    records.extend(s for level in row.get("verification", {}).get("levels", []) for s in level.get("scenarios", []))
    if row.get("project_path") and (not Path(row["project_path"]).is_file()
            or file_hash(row["project_path"]) != row.get("project_sha256")):
        return False
    for record in records:
        if record.get("archive") and (not Path(record["archive"]).is_file() or file_hash(record["archive"]) != record["archive_sha256"]):
            return False
    if row.get("layout_path") and (not Path(row["layout_path"]).is_file() or file_hash(row["layout_path"]) != row["layout_sha256"]):
        return False
    return True


def _exports_valid(history):
    for entry in history.get("exports", {}).values():
        root = Path(entry["directory"])
        for relative, digest in entry.get("asset_sha256", {}).items():
            path = root / relative
            if not path.is_file() or file_hash(path) != digest:
                return False
    return True


def _stage_source_layout(project, root, recorded=None):
    """Retain the immutable source beyond embedded-archive extraction lifetime."""
    if not project.layout.path:
        return None
    source = Path(project.layout.path)
    digest = project.layout.sha256
    directory = root / "source"
    if recorded is not None:
        target = Path(recorded.get("path", "")).resolve()
        if (recorded.get("sha256") != digest or target.parent != directory
                or not target.is_file() or file_hash(target) != digest):
            raise SimulationError("Cached source layout artifact is missing or changed; use a new history directory")
    else:
        directory.mkdir(exist_ok=True)
        target = directory / (digest + source.suffix.lower())
        if not target.exists():
            # Exclusive creation preserves an existing experiment source copy.
            with source.open("rb") as original, target.open("xb") as copied:
                shutil.copyfileobj(original, copied)
        if file_hash(target) != digest:
            raise SimulationError("Staged source layout hash mismatch; use a new history directory")
        recorded = {"path": str(target), "sha256": digest}
    project.layout.path = str(target)
    return recorded


def run_experiment(project, experiment_id, output_dir, progress=None, cancel=None, resume=True, execution=None):
    project = deepcopy(project)
    experiment = next((e for e in project.power.experiments if e.id == experiment_id), None)
    if experiment is None:
        raise SimulationError(f"Unknown experiment {experiment_id!r}")
    validate_experiment(project, experiment)
    if project.layout.path:
        actual = file_hash(project.layout.path)
        if project.layout.sha256 and actual != project.layout.sha256:
            raise SimulationError("Experiment source layout changed; relink and start a new history")
        project.layout.sha256 = actual
    root = Path(output_dir).resolve()
    root.mkdir(parents=True, exist_ok=True)
    path = root / "history.json"
    identity = _identity(project, experiment)
    cache_key = _digest(identity)
    history = {"schema_version": HISTORY_VERSION, "cache_key": cache_key, "identity": identity,
               "experiment_id": experiment_id, "rows": [], "pareto": [], "status": "running",
               "output_dir": str(root), "termination_reason": "", "assumptions": experiment.assumptions,
               "search_method": "bounded grid enumeration, or seeded sampling without replacement when the grid exceeds budget"}
    if path.exists():
        if not resume:
            raise SimulationError("History already exists; choose a new output directory or resume")
        history = json.loads(path.read_text(encoding="utf-8"))
        if history.get("cache_key") != cache_key or history.get("schema_version") != HISTORY_VERSION:
            raise SimulationError("History cache identity differs in source/physics/scenarios/software/mesh policy; use a new directory")
        if project.layout.path and not history.get("source_layout"):
            raise SimulationError("Cached source layout artifact is missing; use a new history directory")
        _stage_source_layout(project, root, history.get("source_layout"))
        if any(not _artifacts_valid(row) for row in history["rows"]) or not _exports_valid(history):
            raise SimulationError("Cached candidate artifact is missing or changed; use a new history directory")
        if history.get("status") == "complete":
            return history
    else:
        history["source_layout"] = _stage_source_layout(project, root)
    protected = _protected(project, experiment)
    started = time.perf_counter()
    user_cancel = cancel

    def budget_cancel():
        if time.perf_counter()-started > experiment.max_wall_seconds:
            raise SearchBudgetExceeded("Wall-time budget reached at a safe checkpoint")
        return bool(user_cancel and user_cancel())

    cancel = budget_cancel
    history["status"] = "running"
    candidates = [("baseline", None), *[("candidate-"+_digest(p)[:16], p) for p in parameter_candidates(project, experiment)]]
    completed = {row["id"] for row in history["rows"] if row["status"] not in {"cancelled", "budget_exhausted"}}
    history["rows"] = [row for row in history["rows"] if row["status"] not in {"cancelled", "budget_exhausted"}]
    seen_geometry = {row.get("geometry_key"): row["id"] for row in history["rows"] if row.get("geometry_key")}
    current = None
    try:
        for index, (key, parameters) in enumerate(candidates):
            if key in completed:
                continue
            checkpoint(cancel, progress, .7*index/max(1,len(candidates)), f"Evaluating {key}")
            if time.perf_counter()-started > experiment.max_wall_seconds:
                history.update(status="interrupted", termination_reason="wall_time_budget")
                _atomic_json(path, history)
                return history
            # New attempt paths retain cancelled/failed raw artifacts for audit.
            folder = root / key
            folder.mkdir(exist_ok=True)
            attempt = Path(tempfile.mkdtemp(prefix="attempt-", dir=folder))
            current = {"id": key, "parameters": parameters or {}, "baseline": key == "baseline",
                       "status": "running", "scenarios": [], "objectives": {}, "violations": []}
            try:
                if parameters is None:
                    candidate = deepcopy(project)
                    issues = validate_rules(candidate)
                    current["violations"] = issues
                else:
                    generated = generate_candidate(project, parameters, attempt / "layout.gds", cancel=cancel, experiment=experiment)
                    candidate = generated.project
                    issues = generated.violations
                    current.update(violations=issues, provenance=generated.provenance)
                if issues:
                    current.update(status="rule_violation", reason="Candidate violates physical geometry/routing rules")
                elif _protected(candidate, experiment) != protected:
                    current.update(status="service_failure", reason="Fixed active device geometry or parameters changed")
                else:
                    geometry = prepare_geometry(candidate)
                    geometry_key = _digest({layer: canonical_geometry_id(shape) for layer, shape in geometry.layers.items()} |
                                           {"interface:"+key: canonical_geometry_id(shape) for key, shape in geometry.interfaces.items()})
                    current["geometry_key"] = geometry_key
                    if geometry_key in seen_geometry:
                        current.update(status="duplicate", reason="Canonical geometry already evaluated", duplicate_of=seen_geometry[geometry_key])
                    else:
                        seen_geometry[geometry_key] = key
                        project_path = attempt / "project.json"
                        save_json(candidate, project_path)
                        current["project_path"] = str(project_path)
                        current["project_sha256"] = file_hash(project_path)
                        if candidate.layout.path:
                            current.update(layout_path=candidate.layout.path, layout_sha256=file_hash(candidate.layout.path))
                        current.update(_evaluate(candidate, experiment, attempt, cancel=cancel, execution=execution))
            except (Cancelled, SearchBudgetExceeded):
                raise
            except SimulationError as error:
                current.update(status=_classify_exception(error), reason=str(error))
            except Exception as error:
                current.update(status="internal_error", reason=f"{type(error).__name__}: {error}")
            history["rows"].append(current)
            _update_pareto(history, experiment.objectives)
            _atomic_json(path, history)
            current = None
        baseline = next(row for row in history["rows"] if row["baseline"])
        _update_pareto(history, experiment.objectives)
        front = [row for row in history["rows"] if row["id"] in history["coarse_pareto"] and not row["baseline"]]
        finalists = front[:experiment.finalists]
        verification = [baseline, *finalists] if baseline["status"] in FEASIBLE else []
        for index, row in enumerate(verification):
            checkpoint(cancel, progress, .7+.25*index/max(1,len(verification)), f"Refining {row['id']}")
            if row.get("verification", {}).get("levels"):
                continue
            candidate, _ = load_project(row["project_path"])
            row["verification"] = _refine(candidate, experiment, row, root/row["id"]/"verification", cancel=cancel, execution=execution)
            _atomic_json(path, history)
        base_evidence = baseline.get("verification", {})
        if base_evidence.get("objectives"):
            baseline.setdefault("coarse_objectives", dict(baseline["objectives"]))
            baseline["objectives"] = dict(base_evidence["objectives"])
        for row in finalists:
            evidence = row.get("verification", {})
            if evidence.get("objectives"):
                row.setdefault("coarse_objectives", dict(row["objectives"]))
                row["objectives"] = dict(evidence["objectives"])
            row["status"] = "mesh_uncertain"
            if base_evidence.get("status") != "mesh_resolved" or evidence.get("status") != "mesh_resolved":
                row["reason"] = "Baseline and finalist must both pass comparable mesh verification"
                continue
            robust, improved, margins = True, False, {}
            for objective in experiment.objectives:
                key = objective.measurement_id
                sign = 1 if objective.direction == "min" else -1
                gain = sign*(base_evidence["objectives"][key]-evidence["objectives"][key])
                uncertainty = base_evidence["absolute_changes"][key] + evidence["absolute_changes"][key]
                required = uncertainty + experiment.improvement_margin * max(abs(base_evidence["objectives"][key]), 1e-12)
                margins[key] = {"gain": gain, "combined_mesh_change": uncertainty, "required_gain": required}
                robust &= gain >= -uncertainty
                improved |= gain > required
            evidence["baseline_comparison"] = margins
            if robust and improved:
                row.update(status="verified_feasible", reason="Improvement exceeds combined empirical mesh-change estimate")
            else:
                row["reason"] = "Ranking overlaps mesh uncertainty or reverses; no verified improvement"
        _update_pareto(history, experiment.objectives)
        history["ranking_comparisons"] = []
        for first_index, first in enumerate(finalists):
            for second in finalists[first_index+1:]:
                a, b = first.get("verification", {}), second.get("verification", {})
                if not a.get("absolute_changes") or not b.get("absolute_changes"):
                    continue
                reversed_order, overlaps = False, True
                for objective in experiment.objectives:
                    key = objective.measurement_id
                    coarse = first.get("coarse_objectives", first["objectives"])[key] - second.get("coarse_objectives", second["objectives"])[key]
                    fine = first["objectives"][key] - second["objectives"][key]
                    reversed_order |= coarse*fine < 0
                    overlaps &= abs(fine) <= a["absolute_changes"][key]+b["absolute_changes"][key]
                history["ranking_comparisons"].append({"first": first["id"], "second": second["id"],
                    "status": "uncertain" if reversed_order or overlaps else "resolved",
                    "ranking_reversed": bool(reversed_order), "mesh_intervals_overlap": bool(overlaps)})
        history.update(status="verifying_exports", termination_reason="evaluation_budget_or_grid_exhausted",
                       verified=[row["id"] for row in history["rows"] if row["status"] == "verified_feasible"],
                       published=[], exports_status="incomplete")
        _atomic_json(path, history)
        for key in list(history["verified"]):
            checkpoint(cancel, progress, .96, f"Reimporting and verifying exported {key}")
            export_folder = Path(tempfile.mkdtemp(prefix="verified-", dir=root/key))
            exported = export_finalist(project, history, key, export_folder, cancel=cancel, execution=execution)
            history.setdefault("exports", {})[key] = {"status": exported["status"], "directory": str(export_folder),
                "asset_sha256": {p.relative_to(export_folder).as_posix(): file_hash(p) for p in export_folder.rglob("*") if p.is_file()}}
            if exported["status"] == "verified_export":
                history["published"].append(key)
            _atomic_json(path, history)
        history["status"] = "complete"
        history["exports_status"] = "complete"
    except SearchBudgetExceeded:
        if current is not None:
            current.update(status="budget_exhausted", reason="Wall-time budget reached; attempt was not ranked")
            history["rows"].append(current)
        history.update(status="interrupted", termination_reason="wall_time_budget")
    except Cancelled:
        if current is not None:
            current.update(status="cancelled", reason="User cancellation; this attempt was not ranked")
            history["rows"].append(current)
        history.update(status="cancelled", termination_reason="user_cancelled")
    history["elapsed_seconds"] = history.get("elapsed_seconds", 0.) + time.perf_counter()-started
    _update_pareto(history, experiment.objectives)
    _atomic_json(path, history)
    return history


def export_finalist(project, history, candidate_id, output_dir, progress=None, cancel=None, execution=None):
    if isinstance(history, (str, Path)):
        history = json.loads(Path(history).read_text(encoding="utf-8"))
    experiment = next((e for e in project.power.experiments if e.id == history.get("experiment_id")), None)
    if experiment is None or _digest(_identity(project, experiment)) != history.get("cache_key"):
        raise SimulationError("Export setup differs from the verified experiment")
    row = next((r for r in history["rows"] if r["id"] == candidate_id), None)
    if row is None or row["status"] != "verified_feasible" or not _artifacts_valid(row):
        raise SimulationError("Only an unchanged mesh-verified feasible finalist may be published")
    root = Path(output_dir).resolve()
    root.mkdir(parents=True, exist_ok=True)
    if (root / "verification.json").exists():
        raise SimulationError("Choose a new verification export directory")
    candidate, _ = load_project(row["project_path"])
    candidate, _ = _refined_project(candidate, experiment.refinement_factors[-1], row)
    report = {"candidate_id": candidate_id, "status": "unverified", "formats": {}, "baseline": "baseline",
              "cache_key": history["cache_key"], "mesh_comparison": row["verification"]["baseline_comparison"]}
    for index, extension in enumerate(("gds", "oas")):
        checkpoint(cancel, progress, index/2, f"Exporting and independently solving {extension.upper()}")
        exported = export_candidate(candidate, root/f"{candidate_id}.{extension}", cancel=cancel)
        if not exported.valid:
            report["formats"][extension] = {"status": "rule_violation", "violations": exported.violations}
            continue
        evaluation = _evaluate(exported.project, experiment, root/extension, cancel=cancel, execution=execution)
        previous = row["verification"]["objectives"]
        comparison = row["verification"]["baseline_comparison"]
        differences, robust, improved = {}, evaluation["status"] == "feasible_provisional", False
        for objective in experiment.objectives:
            key = objective.measurement_id
            value = evaluation.get("objectives", {}).get(key)
            if value is None:
                robust = False
                continue
            delta = value - previous[key]
            sign = 1 if objective.direction == "min" else -1
            gain = comparison[key]["gain"] - sign*delta
            uncertainty = comparison[key]["required_gain"] + abs(delta)
            robust &= abs(delta)/max(abs(previous[key]), 1e-12) <= experiment.maximum_mesh_change
            robust &= gain >= -uncertainty
            improved |= gain > uncertainty
            differences[key] = {"export_delta": delta, "baseline_gain": gain, "required_gain": uncertainty}
        evaluation.update(provenance=exported.provenance, geometry_changed=False,
                          reevaluation_comparison=differences, reevaluation_passed=bool(robust and improved))
        report["formats"][extension] = evaluation
    valid = all(v.get("status") == "feasible_provisional" and v.get("reevaluation_passed", False) for v in report["formats"].values())
    report["status"] = "verified_export" if valid and len(report["formats"]) == 2 else "verification_failed"
    _atomic_json(root/"verification.json", report)
    row["export_status"] = report["status"]
    if report["status"] != "verified_export":
        row.update(status="mesh_uncertain", reason="Export/reimport reevaluation changed feasibility or objective values")
        history["verified"] = [key for key in history.get("verified", []) if key != candidate_id]
        history["published"] = [key for key in history.get("published", []) if key != candidate_id]
        _update_pareto(history, experiment.objectives)
        if history.get("output_dir"):
            _atomic_json(Path(history["output_dir"])/"history.json", history)
    return report
