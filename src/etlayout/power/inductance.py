# SPDX-License-Identifier: MPL-2.0
"""Optional FastHenry finite-conductor, explicitly closed-loop RL extraction.

This is a frequency-aware impedance calculation in a declared rectangular-bar
model. It neither invents a return nor converts a positive-frequency inductance
to the static ParasiticMatrix contract. The engine is selected by the user and
is not downloaded, bundled, patched or executed through a shell.
"""
from __future__ import annotations

import hashlib
import json
import math
import os
import re
import tempfile
import time
from pathlib import Path

import numpy as np

from etlayout.models import SimulationError, checkpoint
from etlayout.power.external_tool import run_external_tool
from etlayout.power.field_snapshot import MU_0, FieldSnapshot
from etlayout.simulation.execution import validate_execution

# The reviewed engine prints Zc.mat with C %lg (six significant digits).
# Keep this serialization floor separate from spatial/frequency sensitivity.
NATIVE_RELATIVE_PRECISION_FLOOR = 5e-6
_MAX_LOOPS, _MAX_SEGMENTS, _MAX_FREQUENCIES = 8, 128, 8
_NUMBER = r"[-+]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][-+]?\d+)?"
_ENTRY = re.compile(rf"\s*({_NUMBER})\s*([+-])\s*({_NUMBER})j\s*")
_MATRIX = re.compile(rf"Impedance matrix for frequency = ({_NUMBER}) (\d+) x (\d+)")


def _json(value):
    return (json.dumps(value, sort_keys=True, indent=2, ensure_ascii=True, allow_nan=False) + "\n").encode("ascii")


def _digest(data):
    return hashlib.sha256(data).hexdigest()


def _file_hash(path, cancel):
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while block := stream.read(1024 * 1024):
            checkpoint(cancel)
            digest.update(block)
    return digest.hexdigest()


def _options(snapshot, frequencies, refinements, tolerance, max_filaments):
    if not isinstance(snapshot, FieldSnapshot) or snapshot.kind != "loop_rl":
        raise SimulationError("RL extraction requires a prepared loop_rl field snapshot")
    if not 1 <= len(snapshot.loops) <= _MAX_LOOPS or not all(loop.segments for loop in snapshot.loops):
        raise SimulationError(f"RL extraction requires 1 to {_MAX_LOOPS} closed finite-conductor loops")
    if len({loop.component_id for loop in snapshot.loops}) != len(snapshot.loops):
        raise SimulationError("Independent loop ports must use distinct conductive components")
    if sum(len(loop.segments) for loop in snapshot.loops) > _MAX_SEGMENTS:
        raise SimulationError(f"RL extraction is bounded to {_MAX_SEGMENTS} physical segments")
    if isinstance(tolerance, (bool, str, complex)) or not np.isscalar(tolerance) or not np.isfinite(tolerance) or not NATIVE_RELATIVE_PRECISION_FLOOR <= tolerance <= .1:
        raise SimulationError("RL mesh tolerance must be between the native 5e-6 precision floor and 0.1")
    if isinstance(max_filaments, bool) or not isinstance(max_filaments, int) or not 1 <= max_filaments <= 4096:
        raise SimulationError("RL filament budget must be an integer from 1 to 4096")
    try:
        if any(isinstance(value, (bool, str)) for value in frequencies):
            raise ValueError
        frequencies = tuple(map(float, frequencies))
        if not 2 <= len(frequencies) <= _MAX_FREQUENCIES or frequencies[0] != 0 or any(
                not np.isfinite(f) or f < 0 for f in frequencies) or any(a >= b for a, b in zip(frequencies, frequencies[1:])):
            raise ValueError
    except (TypeError, ValueError) as error:
        raise SimulationError("Request 2 to 8 strictly increasing frequencies in Hz, beginning with DC (0 Hz)") from error
    try:
        levels = tuple(tuple(level) for level in refinements)
        if not 3 <= len(levels) <= 5 or any(len(level) != 2 or any(
                isinstance(n, bool) or not isinstance(n, int) or not 1 <= n <= 16 for n in level) for level in levels):
            raise ValueError
        if any(a[0] > b[0] or a[1] > b[1] or a == b for a, b in zip(levels, levels[1:])):
            raise ValueError
    except (TypeError, ValueError) as error:
        raise SimulationError("Supply 3 to 5 increasing (longitudinal, cross-section) integer refinement pairs (1..16)") from error
    points, resistance = [], []
    for loop in snapshot.loops:
        total = 0.
        for index, segment in enumerate(loop.segments):
            start, end = np.array(segment.start_m), np.array(segment.end_m)
            following = np.array(loop.segments[(index + 1) % len(loop.segments)].start_m)
            direction = np.array(segment.width_direction)
            dimensions = np.array([segment.width_m, segment.height_m, segment.conductivity_S_per_m])
            if start.shape != (3,) or end.shape != (3,) or direction.shape != (3,) or not all(
                    np.isfinite(value).all() for value in (start, end, direction, dimensions)) or (dimensions <= 0).any():
                raise SimulationError("RL segments need finite coordinates, positive dimensions and bulk conductivity")
            length = float(np.linalg.norm(end - start))
            if length == 0 or not np.allclose(end, following, rtol=0, atol=1e-15):
                raise SimulationError("RL paths must form explicitly closed loops with finite return segments")
            if abs(np.linalg.norm(direction) - 1) > 1e-12 or abs(float(direction @ (end - start))) > length * 1e-12:
                raise SimulationError("RL width direction must be unit length and perpendicular to its segment")
            denominator = segment.width_m * segment.height_m * segment.conductivity_S_per_m
            if not math.isfinite(denominator) or denominator <= 0:
                raise SimulationError("RL segment cross-section/conductivity is outside representable SI scale")
            total += length / denominator
            points.extend((start, end))
        if not math.isfinite(total) or total <= 0:
            raise SimulationError("RL loop resistance must remain finite and positive in SI units")
        resistance.append(total)
    extent = float(np.linalg.norm(np.ptp(np.asarray(points), axis=0)))
    if not math.isfinite(snapshot.relative_permittivity) or snapshot.relative_permittivity <= 0:
        raise SimulationError("RL background relative permittivity must be finite and positive")
    electrical_size = extent / 299792458. * frequencies[-1] * math.sqrt(snapshot.relative_permittivity)
    if not math.isclose(snapshot.relative_permeability, 1., rel_tol=64 * np.finfo(float).eps, abs_tol=0) or not math.isfinite(electrical_size) or electrical_size > .01:
        raise SimulationError("RL engine supports nonmagnetic, electrically small structures only (extent/wavelength <= 0.01)")
    counts = [sum(len(loop.segments) for loop in snapshot.loops) * a * b * b for a, b in levels]
    if counts[-1] > max_filaments:
        raise SimulationError(f"Requested refinement uses {counts[-1]} filaments, exceeding the explicit {max_filaments} budget")
    return frequencies, levels, counts, resistance, electrical_size


def _deck(snapshot, frequency, longitudinal, cross_section):
    lines = ["ETLayout generated closed-loop finite-conductor RL model", ".units m"]
    ports = []
    for loop_index, loop in enumerate(snapshot.loops):
        lines.append("* loop " + json.dumps(loop.id, ensure_ascii=True))
        pieces = [(segment, fraction) for segment in loop.segments for fraction in range(longitudinal)]
        for node_index, (segment, fraction) in enumerate(pieces):
            point = np.asarray(segment.start_m) + fraction / longitudinal * (np.asarray(segment.end_m) - segment.start_m)
            lines.append(f"N{loop_index}_{node_index} x={point[0]:.17g} y={point[1]:.17g} z={point[2]:.17g}")
        # The physical contour closes at the excitation cut. Keep coincident
        # first/last nodes distinct; no distant-node .equiv erases the return.
        point = loop.segments[-1].end_m
        lines.append(f"N{loop_index}_{len(pieces)} x={point[0]:.17g} y={point[1]:.17g} z={point[2]:.17g}")
        for index, (segment, _fraction) in enumerate(pieces):
            width = segment.width_direction
            lines.append(f"E{loop_index}_{index} N{loop_index}_{index} N{loop_index}_{index + 1} "
                         f"w={segment.width_m:.17g} h={segment.height_m:.17g} sigma={segment.conductivity_S_per_m:.17g} "
                         f"wx={width[0]:.17g} wy={width[1]:.17g} wz={width[2]:.17g} "
                         f"nwinc={cross_section} nhinc={cross_section} rw=1 rh=1")
        ports.append((f"N{loop_index}_0", f"N{loop_index}_{len(pieces)}", f"p{loop_index + 1}"))
    lines.extend(f".external {first} {last} {name}" for first, last, name in ports)
    lines.extend((f".freq fmin={frequency:.17g} fmax={frequency:.17g} ndec=1", ".end", ""))
    return "\n".join(lines).encode("ascii"), ports


def prepare_inductance(snapshot, directory, *, frequencies_Hz=(0., 1., 10.),
                       refinements=((1, 1), (2, 3), (4, 5)), relative_tolerance=.02,
                       max_filaments=1024, cancel=None):
    """Write a new deterministic bundle; generation never executes an engine."""
    checkpoint(cancel)
    frequencies, levels, counts, resistance, electrical_size = _options(
        snapshot, frequencies_Hz, refinements, relative_tolerance, max_filaments)
    destination = Path(directory).resolve()
    if destination.exists() and (not destination.is_dir() or any(destination.iterdir())):
        raise SimulationError("RL validation requires a new or empty output directory; existing decks are never executed")
    destination.parent.mkdir(parents=True, exist_ok=True)
    files = {"snapshot.json": _json(snapshot.to_dict())}
    cases = []
    for level, ((longitudinal, cross), count) in enumerate(zip(levels, counts)):
        for index, frequency in enumerate(frequencies):
            name = f"level-{level + 1:02d}-frequency-{index + 1:02d}"
            data, ports = _deck(snapshot, frequency, longitudinal, cross)
            files[f"{name}/model.inp"] = data
            cases.append({"id": name, "level": level, "frequency_Hz": frequency, "ports": ports,
                          "longitudinal_subdivisions": longitudinal, "cross_section_subdivisions": cross,
                          "filaments": count, "deck": f"{name}/model.inp"})
    manifest = {"schema_version": 1, "status": "prepared", "model": "finite-conductor closed-loop magnetoquasistatic impedance",
                "loop_ids": [loop.id for loop in snapshot.loops], "frequencies_Hz": frequencies,
                "relative_tolerance": float(relative_tolerance), "native_relative_precision_floor": NATIVE_RELATIVE_PRECISION_FLOOR,
                "provenance": {"source_context": snapshot.source_context, "geometry_sha256": snapshot.geometry_sha256,
                               "field_model_hash": snapshot.field_model_hash, "temperature_K": snapshot.temperature_K},
                "vacuum_permeability_H_per_m": MU_0, "maximum_extent_over_wavelength": electrical_size,
                "series_bar_resistance_ohm": resistance, "cases": cases,
                "resources": {"max_filaments": max_filaments, "maximum_filaments": counts[-1],
                              "dense_storage_estimate_bytes": 32 * counts[-1] ** 2},
                "files": {name: {"sha256": _digest(data), "bytes": len(data)} for name, data in sorted(files.items())},
                "limitations": ["Declared rectangular-bar routes with ideal end-node junctions; not the complete sheet-network resistance.",
                                "Explicit finite return conductors only; independent loops cannot share conductive components.",
                                "Linear nonmagnetic bulk conductors at fixed temperature; no dielectric loss or radiation.",
                                "Positive-frequency L(f)=Im(Z)/(2*pi*f); no zero-frequency inductance is inferred.",
                                "Six-significant-digit engine output limits precision; convergence does not establish calibration."]}
    files["manifest.json"] = _json(manifest)
    with tempfile.TemporaryDirectory(prefix=".etlayout-rl-", dir=destination.parent) as temporary:
        staging = Path(temporary) / "bundle"
        staging.mkdir()
        for name, data in files.items():
            checkpoint(cancel)
            target = staging / name
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(data)
        checkpoint(cancel)
        if destination.exists():
            destination.rmdir()
        os.replace(staging, destination)
    # Match the portable on-disk JSON contract (lists rather than Python tuples).
    return json.loads(files["manifest.json"])


def _parse_impedance(raw, frequency, ports):
    try:
        lines = raw.decode("ascii").splitlines()
    except UnicodeError as error:
        raise SimulationError("FastHenry matrix output must be ASCII numerical data") from error
    count = len(ports)
    if len(lines) != 2 * count + 1:
        raise SimulationError("FastHenry omitted or added matrix/port records")
    seen = set()
    for line in lines[:count]:
        header = re.fullmatch(r"Row (\d+):\s+(\S+)\s+to\s+(\S+), port name: (\S+)", line.strip(), re.IGNORECASE)
        if header is None or not 1 <= int(header[1]) <= count or int(header[1]) in seen:
            raise SimulationError("FastHenry port mapping is duplicate, missing or malformed")
        index = int(header[1])
        seen.add(index)
        first, last, name = ports[index - 1]
        # Native FastHenry prints this linked-list metadata in reverse order;
        # explicit Row indices, not text-line position, bind the matrix basis.
        if tuple(value.lower() for value in header.groups()[1:]) != (first.lower(), last.lower(), name.lower()):
            raise SimulationError("FastHenry port ordering or physical excitation cut differs from the generated model")
    header = _MATRIX.fullmatch(lines[count].strip())
    if header is None or int(header[2]) != count or int(header[3]) != count:
        raise SimulationError("FastHenry impedance matrix has an unexpected dimension/header")
    reported = float(header[1])
    if not math.isfinite(reported) or not math.isclose(reported, frequency, rel_tol=NATIVE_RELATIVE_PRECISION_FLOOR, abs_tol=0.):
        raise SimulationError("FastHenry matrix frequency differs from the actual requested frequency")
    matrix = np.zeros((count, count), dtype=complex)
    for index, row in enumerate(lines[count + 1:]):
        matches = list(_ENTRY.finditer(row))
        if len(matches) != count:
            raise SimulationError("FastHenry returned an incomplete or malformed complex matrix row")
        cursor = 0
        for column, match in enumerate(matches):
            if row[cursor:match.start()].strip():
                raise SimulationError("Unexpected text in FastHenry impedance matrix")
            matrix[index, column] = complex(float(match[1]), float(match[3]) * (1 if match[2] == "+" else -1))
            cursor = match.end()
        if row[cursor:].strip():
            raise SimulationError("Unexpected trailing text in FastHenry impedance matrix")
    if not np.isfinite(matrix).all():
        raise SimulationError("FastHenry returned a nonfinite impedance matrix")
    return matrix


def _relative_change(first, second):
    first, second = np.asarray(first), np.asarray(second)
    scale = np.maximum(abs(first), abs(second))
    dtype = np.result_type(first, second, float)
    scaled_first = np.divide(first, scale, out=np.zeros_like(first, dtype=dtype), where=scale != 0)
    scaled_second = np.divide(second, scale, out=np.zeros_like(second, dtype=dtype), where=scale != 0)
    change = abs(scaled_second - scaled_first)
    return float(np.max(change)), change.tolist()


def _physical_checks(matrix, frequency, expected_R):
    tolerance = 4 * NATIVE_RELATIVE_PRECISION_FLOOR
    entries = {}
    components = {"R_ohm": matrix.real}
    if frequency:
        components["L_H"] = matrix.imag / (2 * math.pi * frequency)
    for name, component in components.items():
        reciprocity, _ = _relative_change(component, component.T)
        diagonal = np.diag(component)
        positive_self = bool((diagonal > 0).all())
        eigenvalue = None
        bounded_coupling = False
        if positive_self:
            # Equilibrate each physical loop separately, so a weak negative
            # self term/coupling is never hidden by a strong loop's scale.
            scale = np.sqrt(diagonal)
            normalized = component / scale[:, None] / scale[None, :]
            # Inspect its symmetric part for energy only; keep and separately
            # test the original unsymmetrized matrix, without repairing it.
            bounded_coupling = bool(np.isfinite(normalized).all() and (abs(normalized) <= 1 + tolerance).all())
            if bounded_coupling:
                eigenvalue = float(np.linalg.eigvalsh((normalized + normalized.T) / 2).min())
        entries[name] = {"reciprocity_relative_error": reciprocity, "positive_self_entries": positive_self,
                         "minimum_normalized_energy_eigenvalue": eigenvalue,
                         "positive_energy": positive_self and bounded_coupling and eigenvalue >= -tolerance,
                         "reciprocal": reciprocity <= tolerance}
    checks = {"components": entries, "reciprocity_relative_error": max(row["reciprocity_relative_error"] for row in entries.values()),
              "reciprocal": all(row["reciprocal"] for row in entries.values()),
              "positive_energy": all(row["positive_energy"] for row in entries.values()),
              "serialization_acceptance_floor": tolerance}
    if not frequency:
        expected = np.diag(expected_R)
        error, entries = _relative_change(matrix.real, expected)
        checks.update(dc_series_resistance_relative_error=error, dc_series_resistance_entry_errors=entries,
                      dc_series_resistance_passed=error <= tolerance, dc_imaginary_zero=bool((matrix.imag == 0).all()))
    checks["passed"] = checks["reciprocal"] and checks["positive_energy"] and checks.get(
        "dc_series_resistance_passed", True) and checks.get("dc_imaginary_zero", True)
    return checks


def _summarize(report):
    completed = [case for case in report["cases"] if case.get("status") == "completed"]
    sensitivity, finest = [], []
    for frequency in report["frequencies_Hz"]:
        rows = [case for case in completed if case["frequency_Hz"] == frequency]
        if rows:
            finest.append(rows[-1])
        if len(rows) != len(report["cases"]) // len(report["frequencies_Hz"]):
            continue
        for first, second in zip(rows, rows[1:]):
            quantities = [("R_ohm", first["R_ohm"], second["R_ohm"])]
            if frequency:
                quantities.append(("L_H", first["L_H"], second["L_H"]))
            for quantity, a, b in quantities:
                change, entries = _relative_change(a, b)
                sensitivity.append({"frequency_Hz": frequency, "levels": [first["level"], second["level"]],
                                    "quantity": quantity, "maximum_relative_change": change, "entry_changes": entries,
                                    "passed": change <= report["relative_tolerance"]})
    report["frequency_results"] = finest
    # Two successive differences, including all requested entries/frequencies.
    complete = len(completed) == len(report["cases"])
    mesh_passed = complete and all(row["passed"] for row in sensitivity) and all(
        case["physical_checks"]["passed"] for case in completed)
    report["mesh_verification"] = {"status": "passed" if mesh_passed else "not_verified", "comparisons": sensitivity,
                                   "criterion": "per-entry relative R and L changes across every consecutive refinement; no global absolute floor"}
    positive = [case for case in finest if case["frequency_Hz"] > 0]
    low = {"status": "not_established", "reason": "Two completed positive-frequency points are required; DC output contains no inductance"}
    if len(positive) >= 2:
        change, entries = _relative_change(positive[0]["L_H"], positive[1]["L_H"])
        low = {"status": "passed" if change <= report["relative_tolerance"] else "frequency_dependent",
               "frequencies_Hz": [case["frequency_Hz"] for case in positive[:2]], "maximum_relative_change": change,
               "entry_changes": entries, "meaning": "Observed low-frequency sensitivity only; not an exact static-limit proof"}
    report["low_frequency_limit"] = low
    return mesh_passed


def extract_inductance(snapshot, directory, *, executable, frequencies_Hz=(0., 1., 10.),
                       refinements=((1, 1), (2, 3), (4, 5)), relative_tolerance=.02, max_filaments=1024,
                       timeout=180., max_output_bytes=4 * 1024**2, memory_limit_bytes=1024**3,
                       cancel=None, progress=None, execution=None):
    """Actually run selected FastHenry, retaining frequency, raw Z and resources.

    All job budgets apply across the entire refinement/frequency calculation.
    The fixed arguments use the direct LU backend and disable multipole
    approximation; numerical solver tolerances are never loosened.
    """
    policy = validate_execution(execution)
    if isinstance(timeout, (bool, str, complex)) or not np.isscalar(timeout) or not math.isfinite(timeout) or not 0 < timeout <= 900:
        raise SimulationError("RL execution timeout must be positive and at most 900 seconds")
    if isinstance(max_output_bytes, bool) or not isinstance(max_output_bytes, int) or not 1024 <= max_output_bytes <= 16 * 1024**2:
        raise SimulationError("RL output budget must be 1024 bytes to 16 MiB")
    if isinstance(memory_limit_bytes, bool) or not isinstance(memory_limit_bytes, int) or not 16 * 1024**2 <= memory_limit_bytes <= 2 * 1024**3:
        raise SimulationError("RL memory budget must be 16 MiB to 2 GiB")
    executable = Path(executable).expanduser().resolve()
    if not executable.is_file():
        raise SimulationError("Select an installed console FastHenry executable; it is not bundled or downloaded")
    executable_hash = _file_hash(executable, cancel)
    report = prepare_inductance(snapshot, directory, frequencies_Hz=frequencies_Hz, refinements=refinements,
                                relative_tolerance=relative_tolerance, max_filaments=max_filaments, cancel=cancel)
    report["engine"] = {"name": "FastHenry", "executable_sha256": executable_hash,
                        "arguments": ["model.inp", "-sludecomp", "-aoff", "-mdirect"],
                        "version": None, "output_significant_digits": 6}
    report["resources"].update(timeout_seconds=timeout, max_output_bytes=max_output_bytes,
                               memory_limit_bytes=memory_limit_bytes, cpu_budget=policy.cpu_threads,
                               external_native_threads=1, peak_rss_sampled_bytes=0, peak_job_commit_bytes=None)
    destination = Path(directory).resolve()
    started, output_bytes = time.monotonic(), 0
    report["status"] = "not_verified"
    for index, case in enumerate(report["cases"]):
        checkpoint(cancel)
        remaining = timeout - (time.monotonic() - started)
        available = max_output_bytes - output_bytes
        if remaining <= 0 or available < 1024:
            report["status"] = "timeout" if remaining <= 0 else "output_limit"
            break
        checkpoint(cancel, progress, index / len(report["cases"]),
                   f"FastHenry {index + 1}/{len(report['cases'])}: {case['frequency_Hz']:g} Hz, {case['filaments']} filaments")
        working = destination / case["id"]
        result = run_external_tool(executable, report["engine"]["arguments"], working, timeout=remaining,
                                   max_output_bytes=available, memory_limit_bytes=memory_limit_bytes,
                                   cancel=cancel, native_threads=1)
        log = result.pop("output")
        output_bytes += len(log)
        (working / "engine.log").write_bytes(log)
        case.update(status=result["status"], execution=result, log_sha256=_digest(log))
        resources = report["resources"]
        resources["peak_rss_sampled_bytes"] = max(resources["peak_rss_sampled_bytes"], result.get("peak_rss_sampled_bytes", 0))
        if result.get("peak_job_commit_bytes") is not None:
            resources["peak_job_commit_bytes"] = max(resources["peak_job_commit_bytes"] or 0, result["peak_job_commit_bytes"])
        version = re.search(rb"FastHenry\s+[^\r\n]+", log)
        if version:
            report["engine"]["version"] = version[0].decode("ascii", errors="replace")
        if result["status"] != "completed":
            report["status"] = result["status"]
            break
        try:
            counts = re.findall(rb"(?m)^Number of filaments:\s+(\d+)\s*$", log)
            if len(counts) != 1 or int(counts[0]) != case["filaments"]:
                raise SimulationError("FastHenry did not confirm the exact generated filament count")
            case["actual_filaments"] = int(counts[0])
            matrix_path = working / "Zc.mat"
            if not matrix_path.is_file() or matrix_path.stat().st_size > min(65536, max_output_bytes - output_bytes):
                raise SimulationError("FastHenry omitted its bounded native Zc.mat result")
            raw = matrix_path.read_bytes()
            output_bytes += len(raw)
            matrix = _parse_impedance(raw, case["frequency_Hz"], case["ports"])
            frequency = case["frequency_Hz"]
            with np.errstate(over="ignore", invalid="ignore", divide="ignore"):
                inductance = matrix.imag / (2 * math.pi * frequency) if frequency else None
            if inductance is not None and not np.isfinite(inductance).all():
                raise SimulationError("Derived frequency-dependent inductance is outside representable SI scale")
            case.update(Z_real_ohm=matrix.real.tolist(), Z_imag_ohm=matrix.imag.tolist(),
                        R_ohm=matrix.real.tolist(), L_H=inductance.tolist() if frequency else None,
                        matrix_sha256=_digest(raw), physical_checks=_physical_checks(matrix, frequency, report["series_bar_resistance_ohm"]))
            if frequency:
                delta = [math.sqrt(1 / (math.pi * frequency * MU_0 * segment.conductivity_S_per_m))
                         for loop in snapshot.loops for segment in loop.segments]
                ratios = [max(segment.width_m, segment.height_m) / case["cross_section_subdivisions"] / depth
                          for segment, depth in zip((s for loop in snapshot.loops for s in loop.segments), delta)]
                case["skin_depth"] = {"minimum_m": min(delta), "maximum_filament_dimension_over_skin_depth": max(ratios),
                                      "meaning": "Resolution diagnostic; actual convergence must still be demonstrated"}
        except (OSError, SimulationError) as error:
            case.update(status="invalid_result", diagnostic=str(error))
            report["status"] = "not_verified"
            break
    passed = _summarize(report)
    if passed:
        report["status"] = "verified"
    report["resources"].update(total_seconds=time.monotonic() - started, captured_output_bytes=output_bytes)
    (destination / "report.json").write_bytes(_json(report))
    checkpoint(cancel, progress, 1., "RL field calculation " + report["status"])
    return report
