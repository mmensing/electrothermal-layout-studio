# SPDX-License-Identifier: MPL-2.0
"""Verify the exact configured E25–E40 references in bounded fresh processes.

This is functional engineering validation, not a performance benchmark or mesh
verification. Solved archives and detailed receipts belong in CI/release artifacts.
Generation never changes an operating point, solver tolerance or physical model.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import platform
import sys
import time
import traceback
from dataclasses import asdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def source_fingerprint():
    digest = hashlib.sha256()
    paths = [*sorted((ROOT / "src/etlayout").rglob("*.py")), Path(__file__).resolve(),
             Path(__file__).with_name("power_examples_desktop_acceptance.py"),
             Path(__file__).with_name("benchmark_archive_history.py")]
    for path in paths:
        digest.update(path.relative_to(ROOT).as_posix().encode() + b"\0" + path.read_bytes() + b"\0")
    return digest.hexdigest()


def write_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, allow_nan=False, indent=2) + "\n", encoding="utf-8")


def _require(condition, message):
    if not condition:
        raise RuntimeError(message)


def validate_nominal(project, result):
    """Check stored frames against their actual applied drives and input limits."""
    from power_examples_desktop_acceptance import verify_scientific_acceptance

    from etlayout.io.project import configuration_hash, file_hash, physics_hash

    _require(result.converged, f"{project.name}: calculation did not converge")
    _require(file_hash(project.layout.path) == project.layout.sha256, "Configured layout source hash changed")
    _require(result.configuration_hash == configuration_hash(project), "Configured result identity changed")
    _require(result.physics_hash == physics_hash(project), "Result physics identity differs from configured input")
    _require(physics_hash(result.configuration) == physics_hash(project), "Stored result configuration differs")
    checks = verify_scientific_acceptance(project, result)
    if project.transient.enabled:
        _require(result.transient is not None and result.transient.completed, "Configured transient did not complete")
        _require(result.metrics.get("transient_completed"), "Transient completion flag is absent")
        _require(result.metrics["transient_requested_stop_time"] == project.transient.stop_time, "Transient stop changed")
        _require(result.metrics.get("transient_energy_balance_error", math.inf) <= project.solver.thermal_balance_tolerance
                 * max(abs(result.metrics.get("transient_input_energy", 0.)), 1e-15), "Transient integral energy balance failed")
    for kind, configured in (("measurements", project.power.measurements), ("devices", project.power.devices)):
        records = result.analysis.get(kind, {})
        _require({item.id for item in configured} <= records.keys(), f"Missing final configured {kind}")
        for identifier, record in records.items():
            _require(record.get("status") == "available", f"Unavailable final {kind}/{identifier}: {record.get('reason')}")
            if kind == "measurements":
                _require(record.get("within_limits"), f"Final measurement limit failed: {identifier}")
    frames = result.transient.snapshots if result.transient else [result]
    owned = {landing.terminal_id for port in project.power.ports for landing in port.landings}
    service = []
    for index, frame in enumerate(frames):
        _require(frame.converged, f"Frame {index} did not converge")
        _require(physics_hash(frame.configuration) == physics_hash(project), f"Stored frame {index} physical identity changed")
        drives = frame.metrics.get("applied_drives", {})
        for owner, entries in (("port", project.power.ports), ("terminal", project.terminals)):
            for item in entries:
                if owner == "terminal" and item.id in owned:
                    continue
                branch = frame.analysis.get("ports", {}) if owner == "port" else frame.terminals
                _require(item.id in branch, f"Missing {owner} {item.id} in stored frame {index}")
                prefix = "power.ports" if owner == "port" else "terminals"
                target = f"{prefix}.{item.id}.value"
                expected = drives.get(target, item.value)
                kind = item.kind
                if kind == "current":
                    quantity, required = "current", float(expected)
                    limit = project.solver.current_balance_tolerance
                    scale = max(abs(required), 1e-12)
                elif kind in {"ground", "voltage", "gate"}:
                    quantity, required = "voltage", 0. if kind == "ground" else float(expected)
                    limit = project.solver.relative_tolerance
                    scale = max(abs(required), 1.)
                elif kind in {"floating", "probe"}:
                    quantity, required = "current", 0.
                    limit = project.solver.current_balance_tolerance
                    # Zero-current electrodes are judged relative to actual
                    # circuit service, using the unchanged conservation limit.
                    scale = max(abs(frame.metrics.get("total_current", 0.)), 1e-12)
                elif kind in {"density", "insulated"}:
                    # Density sources are integrated by the solver and checked
                    # by conservation; none of these extended references use one.
                    _require(kind == "insulated", f"Unhandled configured density drive {item.id}")
                    continue
                else:
                    raise RuntimeError(f"Unsupported configured {owner} kind {kind}")
                actual = float(branch[item.id][quantity])
                error = abs(actual - required) / scale
                _require(math.isfinite(error) and error <= limit,
                         f"{project.name} frame {index} {owner} {item.id}: {quantity} {actual} != {required}")
                service.append(dict(frame=index, owner=owner, id=item.id, quantity=quantity,
                                    requested=required, actual=actual, normalization_scale=scale,
                                    relative_error=error, limit=limit))
        for kind in ("measurements", "devices"):
            configured = project.power.measurements if kind == "measurements" else project.power.devices
            records = frame.analysis.get(kind, {})
            if kind == "measurements" and result.transient and not {item.id for item in configured} <= records.keys():
                # Raw retained states store the fields/ports, while the final
                # result owns the scoped report. Re-evaluate those actual fields
                # read-only; do not claim reports were retained at every time.
                from etlayout.power.analysis import evaluate_measurements
                records = evaluate_measurements(project, frame)
            _require({item.id for item in configured} <= records.keys(), f"Missing configured {kind}")
            for identifier, record in records.items():
                _require(record.get("status") == "available", f"Unavailable {kind}/{identifier}: {record.get('reason')}")
                if kind == "measurements":
                    _require(record.get("within_limits"), f"Configured measurement limit failed: {identifier}")
        if "temperature_max" in frame.metrics:
            temperature = float(frame.metrics["temperature_max"])
            _require(math.isfinite(temperature) and temperature > 0, "Nonfinite or invalid absolute temperature")
    statistics = result.mesh.statistics
    elements = len(result.mesh.triangles)
    dofs = max(int(statistics.get("estimated_dofs", 0)),
               int(result.metrics.get("electrical_dofs", 0)), int(result.metrics.get("thermal_dofs", 0)))
    _require(elements <= project.mesh.max_elements, "Configured element budget exceeded")
    _require(dofs <= project.mesh.max_dofs, "Configured DOF budget exceeded")
    return dict(conservation_checks=checks, service_checks=service,
                retained_frames=len(frames), elements=elements, nodes=len(result.mesh.points), dofs=dofs,
                element_budget=project.mesh.max_elements, dof_budget=project.mesh.max_dofs,
                converged=True, mesh_verified=False,
                measurement_scope="Final stored report plus read-only evaluation of every retained transient frame")


def verify_portability(project, result, output):
    """Check live exported geometry and both complete typed archive round trips."""
    from benchmark_archive_history import result_digest

    from etlayout.geometry.layout import LayoutDocument
    from etlayout.io.project import file_hash, load_project, physics_hash, save_project
    from etlayout.routing import export_candidate
    from etlayout.routing.geometry import canonical_geometry_id

    output = Path(output)
    output.mkdir(parents=True, exist_ok=True)
    source = Path(project.layout.path)
    original_hash = file_hash(source)
    original_groups = [asdict(group) for group in project.power.devices]
    masks = LayoutDocument(source).extract(project.layout.cell)
    oasis = LayoutDocument(source.with_suffix(".oas")).extract(project.layout.cell)
    def masks_equal(other):
        return masks.keys() == other.keys() and all(canonical_geometry_id(shape) == canonical_geometry_id(other[key])
                                                    for key, shape in masks.items())
    _require(masks_equal(oasis), "Generated GDSII and OASIS geometry differ")
    exported = export_candidate(project, output / (project.name + "-export.oas"))
    _require(exported.valid, f"Export/reimport failed: {exported.violations}")
    reimported = LayoutDocument(exported.project.layout.path).extract(exported.project.layout.cell)
    _require(masks_equal(reimported), "Export/reimport changed geometric masks")
    _require([asdict(group) for group in exported.project.power.devices] == original_groups, "Export changed device identities")
    _require(exported.project.power == project.power, "Export changed ports, rules or measurement/group identity")
    digest = result_digest(result)
    archives = []
    for archive_format in (2, 3):
        target = output / f"{project.name}-v{archive_format}.etl"
        save_project(project, target, result, archive_format=archive_format)
        # Prove that reopening depends on the embedded bytes, not a surviving
        # source path. Restore even after a failed reader/identity assertion.
        unavailable = source.with_name(source.name + ".temporarily-unavailable")
        _require(not unavailable.exists(), "Refusing to replace an unrelated layout file")
        source.rename(unavailable)
        try:
            reopened, restored = load_project(target)
            _require(restored is not None, "Saved solved result is missing")
            _require(file_hash(reopened.layout.path) == original_hash, "Embedded layout hash changed")
            _require(physics_hash(reopened) == physics_hash(project), "Embedded project physical identity changed")
            _require(reopened.power == project.power, "Archive changed port/device/measurement identities")
            _require(result_digest(restored) == digest, f"Schema {archive_format} complete typed result changed")
        finally:
            unavailable.rename(source)
        archives.append(dict(schema=archive_format, bytes=target.stat().st_size,
                             sha256=file_hash(target), complete_typed_result_sha256=digest, embedded_reopen=True))
    _require(file_hash(source) == original_hash, "Input source changed during portability validation")
    return dict(format_geometry_equal=True, exported_reimport_equal=True, power_identity_equal=True,
                complete_typed_round_trips=archives)


def runtime_info(cpu_threads):
    import psutil

    from etlayout import __version__
    return dict(product_version=__version__, python=platform.python_version(), platform=platform.platform(),
                processor=platform.processor(), logical_cpus=psutil.cpu_count(), physical_cpus=psutil.cpu_count(logical=False),
                installed_ram_bytes=psutil.virtual_memory().total, cpu_threads=cpu_threads, study_workers=1)


def worker(identifier, output, cpu_threads):
    from etlayout.examples import make_example
    from etlayout.io.project import file_hash, physics_hash
    from etlayout.simulation.runner import run_project

    output.mkdir(parents=True, exist_ok=True)
    start = time.perf_counter()
    fingerprint = source_fingerprint()
    state = dict(status="running", phase="generation", source_fingerprint=fingerprint)
    write_json(output / "state.json", state)
    try:
        project = make_example(identifier, output / "inputs")
        before = project.to_dict()
        generation = time.perf_counter() - start
        def progress(_fraction, message):
            state.update(phase=message, elapsed_seconds=time.perf_counter() - start)
            write_json(output / "state.json", state)
        result = run_project(project, progress=progress, execution={"cpu_threads": cpu_threads, "study_workers": 1})
        _require(project.to_dict() == before, "Validation mutated the configured nominal input")
        science = validate_nominal(project, result)
        state["phase"] = "format/archive/export checks"
        write_json(output / "state.json", state)
        from etlayout.examples import make_example as regenerate
        duplicate = regenerate(identifier, output / "regenerated")
        _require(duplicate.layout.sha256 == project.layout.sha256, "Deterministic GDS generation changed bytes")
        _require(file_hash(Path(duplicate.layout.path).with_suffix(".oas")) == file_hash(Path(project.layout.path).with_suffix(".oas")),
                 "Deterministic OASIS generation changed bytes")
        _require(physics_hash(duplicate) == physics_hash(project), "Deterministic generation changed physical input")
        portable = verify_portability(project, result, output / "portability")
        _require(project.to_dict() == before, "Portability validation mutated the configured input")
        _require(source_fingerprint() == fingerprint, "Runtime source changed during validation")
        receipt = dict(status="passed", name=project.name, source_fingerprint=fingerprint,
            layout_sha256=project.layout.sha256, physics_hash=physics_hash(project), runtime=runtime_info(cpu_threads),
            generation_seconds=generation, execution=result.execution, solver=asdict(project.solver),
            mesh_statistics=result.mesh.statistics, metrics=result.metrics, scientific_acceptance=science,
            portability=portable, wall_seconds=time.perf_counter() - start, mesh_verified=False,
            interpretation="Exact configured nominal function checks; one observation, not a timing benchmark or mesh verification")
        write_json(output / "receipt.json", receipt)
        write_json(output / "state.json", {"status": "complete", "phase": "complete"})
        return receipt
    except Exception as error:
        resource_limited = isinstance(error, MemoryError) or any(token in str(error).lower()
            for token in ("budget exceeded", "exceeds the", "exceeds max", "maximum number of elements"))
        state.update(status="resource-limited" if resource_limited else "failed", error_type=type(error).__name__, error=str(error),
                     elapsed_seconds=time.perf_counter() - start)
        write_json(output / "receipt.json", state)
        raise


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--examples", nargs="+", help="Stable example IDs; default: every E25–E40 reference")
    parser.add_argument("--output", type=Path, default=ROOT / ".artifacts/extended-examples")
    parser.add_argument("--cpu-threads", type=int, default=4)
    parser.add_argument("--max-rss-gib", type=float, default=3.)
    parser.add_argument("--timeout", type=float, default=300., help="Per-example seconds, including round-trip checks")
    parser.add_argument("--worker", help=argparse.SUPPRESS)
    args = parser.parse_args(argv)
    import etlayout
    from etlayout.example_catalog import example_info
    from etlayout.examples import EXTENDED_EXAMPLES
    from etlayout.simulation.execution import validate_execution
    _require(Path(etlayout.__file__).resolve().is_relative_to((ROOT / "src").resolve()),
             "Imported ETLayout differs from the selected source checkout; set PYTHONPATH to its src directory")
    validate_execution({"cpu_threads": args.cpu_threads, "study_workers": 1})
    if not all(math.isfinite(value) and value > 0 for value in (args.max_rss_gib, args.timeout)):
        parser.error("Resource limits must be finite and positive")
    names = [example_info(identifier).name for identifier in args.examples] if args.examples else list(EXTENDED_EXAMPLES)
    if len(names) != len(set(names)) or any(name not in EXTENDED_EXAMPLES for name in names):
        parser.error("Choose distinct implemented extended example IDs E25–E40")
    output = args.output.resolve()
    if args.worker:
        _require(example_info(args.worker).name in EXTENDED_EXAMPLES, "Invalid worker example")
        try:
            print(json.dumps(worker(args.worker, output, args.cpu_threads), ensure_ascii=False, allow_nan=False))
            return 0
        except Exception:
            traceback.print_exc()
            return 1
    # Reject an existing output rather than mixing observations or overwriting
    # unrelated data. Every child gets its own short temporary directory.
    output.mkdir(parents=True, exist_ok=False)
    from benchmark_archive_history import observe
    started = time.perf_counter()
    fingerprint, records = source_fingerprint(), []
    for name in names:
        command = [sys.executable, str(Path(__file__).resolve()), "--worker", name,
                   "--output", str(output / name.split("_", 1)[0]), "--cpu-threads", str(args.cpu_threads)]
        record = observe(command, output / (name.split("_", 1)[0] + "-process"),
                         max_rss_gib=args.max_rss_gib, timeout=args.timeout)
        record["name"] = name
        failure_receipt = output / name.split("_", 1)[0] / "receipt.json"
        if record.get("status") != "passed" and failure_receipt.is_file():
            record["failure"] = json.loads(failure_receipt.read_text(encoding="utf-8"))
            if record["failure"].get("status") == "resource-limited":
                record["status"] = "resource-limited"
        records.append(record)
        summary = dict(runtime=runtime_info(args.cpu_threads), source_fingerprint=fingerprint,
            source_unchanged=source_fingerprint() == fingerprint, examples=records,
            wall_seconds=time.perf_counter() - started, max_rss_gib=args.max_rss_gib, timeout_seconds=args.timeout,
            rss_scope="10 ms sampled sum of each owned child process and descendants; imports and validation included",
            cache_scope="Fresh process per example; OS filesystem caches were not flushed",
            mesh_verified=False, not_a_performance_benchmark=True)
        write_json(output / "receipt.json", summary)
        print(f"{name}: {record['status']} ({record['process_wall_seconds']:.2f} s, {record['peak_rss_bytes'] / 1024**2:.1f} MiB)", flush=True)
    return int(any(record.get("status") != "passed" for record in records) or not summary["source_unchanged"])


if __name__ == "__main__":
    raise SystemExit(main())
