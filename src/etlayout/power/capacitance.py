# SPDX-License-Identifier: MPL-2.0
"""Homogeneous-dielectric 3D capacitance through an optional external FastCap2.

Only generated closed-conductor panels are run. Every finite conductor remains
an axis; infinity is an explicit zero-potential environment. Stock FastCap's
symmetrized display is not accepted as raw reciprocity evidence.
"""
from __future__ import annotations

import hashlib
import json
import math
import re
import time
from itertools import islice
from pathlib import Path

import numpy as np

from etlayout.models import SimulationError, checkpoint
from etlayout.power.external_tool import run_external_tool
from etlayout.power.field_snapshot import triangulate_conductors
from etlayout.power.parasitic_models import FieldMaterial, FieldProvenance, ParasiticMatrix, PotentialBoundary

_BEGIN = "ETLAYOUT_RAW_MAXWELL_BEGIN"
_END = "ETLAYOUT_RAW_MAXWELL_END"
_FLOAT = re.compile(r"[-+]?(?:[0-9]+(?:\.[0-9]*)?|\.[0-9]+)(?:[eE][-+]?[0-9]+)?\Z")
_MAX_INPUT_BYTES = 32 * 1024**2


def _file_sha256(path, cancel=None):
    if path.stat().st_size > 512 * 1024**2:
        raise SimulationError("Selected capacitance executable exceeds the 512 MiB inspection budget")
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024**2), b""):
            checkpoint(cancel)
            digest.update(chunk)
    return digest.hexdigest()


def _json(value):
    return (json.dumps(value, ensure_ascii=True, sort_keys=True, indent=2, allow_nan=False) + "\n").encode("ascii")


def compile_capacitance_input(snapshot, mesh, *, cancel=None):
    """Return a deterministic SI panel deck and a label/identity manifest."""
    if snapshot.kind != "capacitance" or mesh.geometry_sha256 != snapshot.geometry_sha256:
        raise SimulationError("Capacitance panels must match the explicit electrostatic geometry snapshot")
    if (not 1 <= len(snapshot.components) <= 128 or not np.isfinite(snapshot.relative_permittivity)
            or snapshot.relative_permittivity <= 0):
        raise SimulationError("Capacitance needs 1 to 128 conductors and an explicit positive homogeneous permittivity")
    if tuple(surface.id for surface in mesh.surfaces) != snapshot.components:
        raise SimulationError("Surface conductor order must match the whole-conductor snapshot")
    lines = ["0 ETLayout generated closed conductor surfaces; coordinates in metres"]
    mapping = []
    panels = 0
    for index, surface in enumerate(mesh.surfaces):
        checkpoint(cancel)
        name = f"c{index:04d}"
        mapping.append({"axis_id": surface.id, "solver_name": name, "support_id": "component:" + surface.id})
        points, triangles = np.asarray(surface.vertices_m), np.asarray(surface.triangles)
        if (points.ndim != 2 or points.shape[1] != 3 or points.dtype.kind not in "iuf" or not np.isfinite(points).all()
                or triangles.ndim != 2 or triangles.shape[1] != 3 or triangles.dtype.kind not in "iu"
                or not len(triangles) or np.any(triangles < 0) or np.any(triangles >= len(points))):
            raise SimulationError("Capacitance needs finite 3D triangular conductor surfaces")
        for panel_index, triangle in enumerate(triangles):
            if panel_index % 128 == 0:
                checkpoint(cancel)
            coordinates = points[triangle]
            lines.append("T " + name + " " + " ".join(format(float(value), ".17g") for value in coordinates.ravel()))
        panels += len(triangles)
    data = ("\n".join(lines) + "\n").encode("ascii")
    if len(data) > _MAX_INPUT_BYTES:
        raise SimulationError("Capacitance panel input exceeds the 32 MiB budget; use a coarser surface mesh")
    manifest = {"schema_version": 1, "kind": "electrostatic 3D homogeneous dielectric",
                "geometry_sha256": snapshot.geometry_sha256, "field_model_hash": snapshot.field_model_hash,
                "surface_mesh_sha256": mesh.mesh_sha256, "length_unit": "m", "matrix_unit": "F",
                "relative_permittivity": snapshot.relative_permittivity, "temperature_K": snapshot.temperature_K,
                "reference": {"id": "environment", "kind": "infinity", "potential_V": 0.},
                "conductors": mapping, "panels": panels,
                "deck_sha256": hashlib.sha256(data).hexdigest(), "deck_bytes": len(data),
                "source_context": snapshot.source_context}
    return data, manifest


def parse_fastcap_output(output, solver_names):
    """Parse the original unsymmetrized SI operator, never the stock display.

    The optional external tool must expose this output-only protocol. It emits
    its original charge-row/voltage-column coefficients at 17 significant digits
    before its usual symmetrized display. Unknown, repeated, truncated or changed
    axes fail rather than falling back to the averaged table.
    """
    if not isinstance(output, bytes) or len(output) > 16 * 1024**2:
        raise SimulationError("Capacitance output exceeds its bounded byte contract")
    # Tool banners may contain a non-ASCII executable path; numerical tokens
    # below must still match the strict ASCII SI grammar.
    text = output.decode("utf-8", errors="replace")
    if "NONCONVERGENCE" in text.upper():
        raise SimulationError("The external capacitance solve did not converge")
    lines = text.splitlines()
    starts = [i for i, line in enumerate(lines) if line.startswith(_BEGIN)]
    ends = [i for i, line in enumerate(lines) if line.strip() == _END]
    if len(starts) != 1 or len(ends) != 1 or ends[0] <= starts[0]:
        raise SimulationError("Select a FastCap2 tool with unaveraged SI output support; the stock averaged display cannot establish reciprocity")
    begin = lines[starts[0]].split()
    count = len(solver_names)
    if begin != [_BEGIN, str(count), "F"] or not 1 <= count <= 128:
        raise SimulationError("Unaveraged capacitance dimensions/units do not match the generated conductor basis")
    constants = [line.split() for line in lines if line.startswith("ETLAYOUT_FASTCAP_EPSILON0_F_PER_M")]
    if (len(constants) != 1 or len(constants[0]) != 2 or _FLOAT.fullmatch(constants[0][1]) is None):
        raise SimulationError("Capacitance tool must declare its native vacuum permittivity")
    epsilon0 = float(constants[0][1])
    if not math.isfinite(epsilon0) or not 8e-12 < epsilon0 < 9e-12:
        raise SimulationError("Capacitance tool declared an invalid SI vacuum permittivity")
    rows = [line.split() for line in lines[starts[0] + 1:ends[0]] if line.strip()]
    if len(rows) != count:
        raise SimulationError("Incomplete unaveraged capacitance matrix")
    matrix, actual_names = [], []
    for index, row in enumerate(rows):
        if len(row) != count + 1 or row[0] not in {solver_names[index], solver_names[index] + "%GROUP1"}:
            raise SimulationError("Capacitance output conductor names/order differ from the generated basis")
        if any(_FLOAT.fullmatch(value) is None for value in row[1:]):
            raise SimulationError("Capacitance output contains an invalid, nonfinite or complex coefficient")
        values = [float(value) for value in row[1:]]
        if not np.isfinite(values).all():
            raise SimulationError("Capacitance output needs finite real SI coefficients")
        matrix.append(values)
        actual_names.append(row[0])
    version = re.search(r"Running [^\r\n]+ ([0-9]+\.[0-9]+wr) \(([^)\r\n]{1,120})\)", text)
    observed_version = " ".join(version.groups()) if version else "unknown (no recognized native version banner)"
    return {"matrix_F": matrix, "solver_names": actual_names, "observed_engine_version": observed_version,
            "output_precision_significant_digits": 17, "vacuum_permittivity_F_per_m": epsilon0,
            "matrix_convention": "charge rows / voltage columns; all finite conductors; infinity fixed at 0 V",
            "source_sha256": hashlib.sha256(output).hexdigest()}


def _operator(snapshot, mesh, parsed, execution_sha):
    return ParasiticMatrix("capacitance", tuple(map(tuple, parsed["matrix_F"])),
        (FieldMaterial("homogeneous_background", ("space:background",), snapshot.relative_permittivity * parsed["vacuum_permittivity_F_per_m"], "F/m"),),
        FieldProvenance(snapshot.geometry_sha256, mesh.mesh_sha256, parsed["source_sha256"],
            "FastCap2 with unaveraged output", parsed["observed_engine_version"] + "; executable SHA-256 " + execution_sha,
            snapshot.temperature_K, "3D electrostatic PEC conductors in a homogeneous real linear dielectric; infinity fixed at 0 V"),
        boundaries=tuple(PotentialBoundary(identifier, ("component:" + identifier,)) for identifier in snapshot.components),
        reference=PotentialBoundary("environment", ("boundary:infinity",)))


def _quality(matrix):
    values = np.asarray(matrix)
    diagonal = np.diag(values)
    result = {"raw_matrix_preserved": True, "symmetry_repair": False, "mesh_verified": False}
    if np.any(diagonal <= 0):
        return {**result, "diagnostic": "Nonpositive self-capacitance"}
    with np.errstate(over="ignore", divide="ignore", invalid="ignore"):
        scaled = values / np.sqrt(diagonal)[:, None] / np.sqrt(diagonal)[None, :]
    if not np.isfinite(scaled).all():
        return {**result, "diagnostic": "Per-axis energy scaling is not finite"}
    result["maximum_scaled_reciprocity_error"] = float(np.max(np.abs(scaled - scaled.T)))
    result["minimum_scaled_energy_eigenvalue"] = float(np.linalg.eigvalsh((scaled + scaled.T) / 2).min())
    result["row_sums_F"] = values.sum(axis=1).tolist()
    return result


def extract_capacitance(snapshot, executable, directory, *, mesh_size_m, max_panels=20000,
                        timeout=30., native_threads=1, relative_tolerance=.02, direct=False, cancel=None, progress=None):
    """Compute one real 3D surface-field C matrix with explicit evidence states.

    Linear solver convergence, strict operator acceptance and mesh sensitivity
    are separate. Failed tool execution retains its controlled inputs/log and
    never produces a successful field result or an invented operator.
    """
    started = time.perf_counter()
    if (not isinstance(relative_tolerance, (int, float)) or isinstance(relative_tolerance, bool)
            or not math.isfinite(relative_tolerance) or not 0 < relative_tolerance < 1):
        raise SimulationError("Capacitance mesh relative tolerance must be finite and between zero and one")
    if not isinstance(direct, bool):
        raise SimulationError("Capacitance direct interaction choice must be Boolean")
    executable = Path(executable).expanduser()
    if not executable.is_absolute() or not executable.is_file():
        raise SimulationError("Select an existing absolute FastCap2 executable path")
    destination = Path(directory).expanduser().absolute()
    if destination.exists() and (not destination.is_dir() or destination.is_symlink() or destination.is_junction()
                                 or any(destination.iterdir())):
        raise SimulationError("Capacitance output must be a new or empty directory")
    checkpoint(cancel, progress, 0., "Preparing explicit 3D electrostatic conductor surfaces")
    mesh = triangulate_conductors(snapshot, mesh_size_m, max_panels=max_panels, cancel=cancel, progress=progress)
    data, manifest = compile_capacitance_input(snapshot, mesh, cancel=cancel)
    if direct and manifest["panels"] > 2000:
        raise SimulationError("Direct capacitance interactions are limited to 2000 panels; use a coarser surface mesh or multipole mode")
    surface_seconds = time.perf_counter() - started
    manifest["algorithm"] = {"interactions": "direct" if direct else "multipole", "multipole_order": 6,
                             "linear_solver_relative_tolerance": 1e-10}
    destination.mkdir(parents=True, exist_ok=True)
    (destination / "geometry.qui").write_bytes(data)
    (destination / "input-manifest.json").write_bytes(_json(manifest))
    executable_sha = _file_sha256(executable, cancel)
    report = {"schema_version": 1, "kind": "capacitance", "model": "3D electrostatic homogeneous-dielectric conductor field solve",
              "engine": "FastCap2 with unaveraged output", "status": "not-established", "converged": False,
              "accepted": False, "mesh_verified": False, "axis_ids": list(snapshot.components),
              "reference": manifest["reference"], "units": "F", "raw_matrix_F": None, "operator": None,
              "source_context": snapshot.source_context, "geometry_sha256": snapshot.geometry_sha256,
              "field_model_hash": snapshot.field_model_hash,
              "mesh": {"identity": mesh.mesh_sha256, "size_m": mesh_size_m, "panels": manifest["panels"]},
              "resources": {"max_panels": max_panels, "memory_limit_bytes": 1024**3, "timeout_s": timeout,
                            "native_threads": native_threads, "simultaneous_field_solves": 1},
              "executable_sha256": executable_sha, "input_manifest": manifest,
              "algorithm": {"interactions": "direct" if direct else "multipole", "multipole_order": 6,
                            "linear_solver_relative_tolerance": 1e-10, "direct_panel_limit": 2000},
              "limitations": "Static PEC conductor capacitance; no charge storage in the ETLayout conduction solve, mixed dielectrics, RLC frequency response, transistor small-signal law or implicit magnetic return. A single solve is not mesh verification."}
    checkpoint(cancel, progress, .35, "Computing capacitance using the explicitly selected external FastCap2")
    arguments = ["geometry.qui", "-p" + format(snapshot.relative_permittivity, ".17g"), "-o6", "-t1e-10"]
    if direct:
        arguments.append("-d0")
    execution = run_external_tool(executable, arguments, destination, timeout=timeout, max_output_bytes=4 * 1024**2,
        native_threads=native_threads, memory_limit_bytes=1024**3, cancel=cancel)
    post_started = time.perf_counter()
    output = execution.pop("output")
    (destination / "solver-output.txt").write_bytes(output)
    report["execution"] = execution
    report["solver_output_sha256"] = hashlib.sha256(output).hexdigest()
    if execution["status"] != "completed":
        report["status"] = execution["status"]
        report["diagnostic"] = "External capacitance calculation did not complete; inspect execution status and retained solver output."
    else:
        try:
            parsed = parse_fastcap_output(output, [entry["solver_name"] for entry in manifest["conductors"]])
            report["raw_matrix_F"] = parsed["matrix_F"]
            report["observed_engine_version"] = parsed["observed_engine_version"]
            report["output_precision_significant_digits"] = parsed["output_precision_significant_digits"]
            report["physical_constants"] = {"native_vacuum_permittivity_F_per_m": parsed["vacuum_permittivity_F_per_m"],
                "homogeneous_permittivity_F_per_m": snapshot.relative_permittivity * parsed["vacuum_permittivity_F_per_m"]}
            report["validation"] = _quality(parsed["matrix_F"])
            report["converged"] = True
            report["status"] = "computed"
            try:
                operator = _operator(snapshot, mesh, parsed, executable_sha)
                report["operator"] = json.loads(operator.to_json())
                report["accepted"] = True
            except ValueError as error:
                report["validation"]["strict_operator_diagnostic"] = str(error)
                report["diagnostic"] = "Raw field calculation retained; strict static operator acceptance is not established. " + str(error)
        except SimulationError as error:
            report["status"] = "invalid-output"
            report["diagnostic"] = str(error)
    report["timings"] = {"surface_preparation_s": surface_seconds, "external_execution_s": execution["seconds"],
                         "postprocessing_s": time.perf_counter() - post_started,
                         "total_s": time.perf_counter() - started}
    (destination / "capacitance.json").write_bytes(_json(report))
    checkpoint(cancel, progress, 1., "Capacitance field result: " + report["status"])
    return report


def run_capacitance_mesh_study(snapshot, executable, directory, mesh_sizes_m, *, max_panels=20000,
                              timeout=30., native_threads=1, relative_tolerance=.02, direct=False, cancel=None, progress=None):
    """Run 3 to 5 bounded distinct surface meshes, comparing every raw C entry."""
    from etlayout.power.multiport_study import _entrywise_change
    try:
        requested = list(islice(iter(mesh_sizes_m), 6))
        if any(isinstance(value, (bool, np.bool_)) for value in requested):
            raise ValueError("Boolean surface size")
        sizes = [float(value) for value in requested]
    except (TypeError, ValueError, OverflowError) as error:
        raise SimulationError("Capacitance mesh study needs 3 to 5 decreasing finite positive SI lengths") from error
    if (not 3 <= len(sizes) <= 5 or not np.isfinite(sizes).all() or min(sizes) <= 0
            or any(b >= a for a, b in zip(sizes, sizes[1:]))):
        raise SimulationError("Capacitance mesh study needs 3 to 5 decreasing finite positive SI lengths")
    destination = Path(directory).expanduser().absolute()
    if destination.exists() and (not destination.is_dir() or destination.is_symlink() or destination.is_junction()
                                 or any(destination.iterdir())):
        raise SimulationError("Capacitance mesh study requires a new or empty output directory")
    destination.mkdir(parents=True, exist_ok=True)
    study = {"schema_version": 1, "kind": "capacitance mesh study", "status": "not-established",
             "converged": False, "accepted": False, "mesh_verified": False, "levels": [], "comparisons": [],
             "requested_sizes_m": sizes, "relative_tolerance": relative_tolerance, "network": None,
             "geometry_sha256": snapshot.geometry_sha256, "field_model_hash": snapshot.field_model_hash,
             "source_context": snapshot.source_context,
             "limitations": "Two successive per-entry mesh differences are sensitivity evidence, not a rigorous error bound or material calibration."}
    for index, size in enumerate(sizes):
        checkpoint(cancel)
        def phase(fraction, message, index=index):
            checkpoint(cancel, progress, (index + fraction) / len(sizes), message)
        result = extract_capacitance(snapshot, executable, destination / f"level-{index + 1}", mesh_size_m=size,
            max_panels=max_panels, timeout=timeout, native_threads=native_threads, relative_tolerance=relative_tolerance,
            direct=direct, cancel=cancel, progress=phase)
        study["levels"].append(result)
        if not result["converged"]:
            study["diagnostic"] = result.get("diagnostic", "A capacitance mesh level did not complete")
            break
        study["network"] = result
        if index:
            previous = study["levels"][-2]
            change = _entrywise_change(previous["raw_matrix_F"], result["raw_matrix_F"], relative_tolerance)
            distinct = (previous["mesh"]["identity"] != result["mesh"]["identity"]
                        and previous["mesh"]["panels"] < result["mesh"]["panels"])
            study["comparisons"].append({"coarse_level": index - 1, "fine_level": index,
                "distinct_refinement": distinct, "capacitance": change,
                "within_tolerance": distinct and change["within_tolerance"]})
    study["converged"] = len(study["levels"]) == len(sizes) and all(level["converged"] for level in study["levels"])
    study["accepted"] = study["converged"] and all(level["accepted"] for level in study["levels"][-3:])
    study["mesh_verified"] = (study["converged"] and len(study["comparisons"]) >= 2
        and all(item["within_tolerance"] for item in study["comparisons"][-2:]))
    study["status"] = ("verified" if study["mesh_verified"] and study["accepted"] else
                       "computed-unverified" if study["converged"] else study["levels"][-1]["status"])
    (destination / "capacitance-mesh.json").write_bytes(_json(study))
    return study
