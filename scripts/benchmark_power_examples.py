# SPDX-License-Identifier: MPL-2.0
"""Explicit, resource-bounded power-example calculations (never run by generation).

Receipts and optional fields belong in artifacts, not the tracked example tree.
Each case runs in a fresh process with measured phase times and peak RSS.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import platform
import subprocess
import sys
import time
import traceback
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))


def write(path, value):
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, indent=2, sort_keys=True, allow_nan=False) + "\n", encoding="utf-8")
    # Windows readers/antivirus can hold a brief handle that forbids replacement.
    # Retry only publication of this receipt, never a calculation or drive.
    for attempt in range(20):
        try:
            temporary.replace(path)
            return
        except PermissionError:
            if attempt == 19:
                raise
            time.sleep(.05)


def source_fingerprint():
    digest = hashlib.sha256()
    for path in sorted((ROOT / "src/etlayout").rglob("*.py")):
        digest.update(path.relative_to(ROOT).as_posix().encode())
        digest.update(b"\0" + path.read_text(encoding="utf-8").encode() + b"\0")
    digest.update(Path(__file__).read_bytes())
    return digest.hexdigest()


def summarize(project, result):
    import numpy as np

    from etlayout.power_examples import metadata
    config = metadata(project)
    measurements = result.analysis.get("measurements", {})
    unavailable = {key: row["reason"] for key, row in measurements.items() if row["status"] != "available"}
    failures = [key for key, row in measurements.items() if row["status"] == "available" and not row["within_limits"]]
    groups = result.analysis.get("devices", {})
    sharing = {}
    for label, names, normalization in (
        ("fingers", [name for name in groups if "_finger_" in name], "active_width"),
        ("banks", [name for name in groups if name.startswith("bank_") and "_finger_" not in name], "active_width"),
        ("macrocells", [name for name in groups if name.startswith("macrocell_")], "active_area"),
    ):
        rows = [groups[name] for name in names if groups[name]["status"] == "available"]
        if len(rows) != len(names):
            unavailable.update({name: groups[name]["reason"] for name in names if groups[name]["status"] != "available"})
        if rows:
            loading = np.asarray([row["current"] / row[normalization] for row in rows])
            sources = np.asarray([row["source_voltage"] for row in rows])
            sharing[label] = dict(count=len(rows), current_sum_A=sum(row["current"] for row in rows),
                maximum_relative_loading_deviation=float(np.max(np.abs(loading / loading.mean() - 1))),
                source_potential_spread_V=float(np.ptp(sources)),
                loading_units="A/m" if normalization == "active_width" else "A/m²",
                loading_min=float(loading.min()), loading_max=float(loading.max()))
    ports = result.analysis.get("ports", {})
    current = ports.get("drain", {}).get("current")
    voltage = (ports["drain"]["voltage"] - ports["source"]["voltage"]) if ports else None
    response = dict(delivered_current_A=current, terminal_voltage_V=voltage,
                    effective_on_state_resistance_ohm=voltage / current if current else None)
    if config.get("total_gate_width_m") and current is not None:
        response["current_per_total_gate_width_A_m"] = current / config["total_gate_width_m"]
        ids = result.mesh.elements("channel")
        field = np.linalg.norm(result.fields["channel"]["electric_field"][ids], axis=1)
        response["maximum_channel_field_V_m"] = float(field.max())
        if field.max() > config["maximum_channel_field_V_m"]:
            failures.append("low_field_transport_assumption")
    if "kelvin_potential" in measurements and measurements["kelvin_potential"]["status"] == "available":
        response["kelvin_error_V"] = measurements["kelvin_potential"]["value"] - ports["source"]["voltage"]
    maximum = result.metrics.get("temperature_max")
    if maximum is not None and maximum > config["model_temperature_range_K"][1]:
        failures.append("material_temperature_assumption")
    if current is None or abs(current / config["requested_current_A"] - 1) > project.solver.current_balance_tolerance:
        failures.append("delivered_service")
    if config["stress"]:
        failures.append("explicit_stress_case_excluded_from_rankings")
    return dict(status="converged_provisional" if result.converged and not failures and not unavailable else "invalid",
        converged=result.converged, mesh_verified=False, model_failures=sorted(set(failures)), unavailable=unavailable,
        response=response, sharing=sharing, configuration=config, mesh=result.mesh.statistics,
        metrics=result.metrics, measurements=measurements, loss_budget=result.analysis.get("loss_budget"),
        individual_contacts=[{key: value for key, value in row.items() if key not in {"elements", "geometry_wkt"}}
                             for row in result.analysis.get("contacts", {}).values()],
        execution=result.execution)


def worker(args, name, output):
    from etlayout.examples import make_example
    from etlayout.io.project import physics_hash, save_project
    from etlayout.power_examples import metadata
    from etlayout.routing import export_candidate
    from etlayout.simulation.runner import run_project

    output.mkdir(parents=True, exist_ok=True)
    started = time.perf_counter()
    phases = {}
    state = dict(status="running", phase="generation", source_fingerprint=source_fingerprint())
    write(output / "state.json", state)
    try:
        project = make_example(name, output / "inputs", preset=args.preset, variant=args.variant,
                               current=args.current, thermal=args.thermal, fingers=args.fingers,
                               gate_width_um=args.gate_width_um, stress=args.stress)
        phases["generation_seconds"] = time.perf_counter() - started
        if args.mesh_factor != 1:
            for key in ("max_size", "min_size", "contact_size"):
                setattr(project.mesh, key, getattr(project.mesh, key) * args.mesh_factor)
            for refinement in project.mesh.refinements:
                refinement.size *= args.mesh_factor
                refinement.axis_sizes = [value * args.mesh_factor for value in refinement.axis_sizes]
        if args.mode:
            project.solver.mode = args.mode
        save_project(project, output / "inputs" / (project.name + ".etl"))
        state["configuration"] = metadata(project)
        write(output / "state.json", state)
        calculation_started = time.perf_counter()
        milestones = {}

        def progress(fraction, message):
            elapsed = time.perf_counter() - calculation_started
            if message == "Geometry verified":
                milestones["geometry"] = elapsed
            if message.startswith("Mesh ready:"):
                milestones["mesh"] = elapsed
            if message.endswith("solution complete"):
                milestones["physics"] = elapsed
            state.update(phase="postprocessing" if message.endswith("solution complete") else message, elapsed_seconds=elapsed)
            write(output / "state.json", state)
            print(f"{name} {elapsed:.2f}s {fraction:.1%} {message}", flush=True)

        result = run_project(project, progress=progress, execution={"cpu_threads": args.cpu_threads, "study_workers": 1})
        complete = time.perf_counter() - calculation_started
        phases.update(preparation_seconds=milestones.get("geometry"),
                      meshing_seconds=milestones.get("mesh", 0) - milestones.get("geometry", 0),
                      physics_solve_seconds=milestones.get("physics", complete) - milestones.get("mesh", 0),
                      postprocessing_seconds=complete - milestones.get("physics", complete),
                      calculation_seconds=complete)
        summary = summarize(project, result)
        before = physics_hash(project)
        state.update(phase="export/reimport")
        write(output / "state.json", state)
        exported = export_candidate(project, output / "reimport" / (name + ".oas"))
        summary["export_reimport"] = dict(valid=exported.valid, violations=exported.violations,
                                         physics_hash_equal=physics_hash(exported.project) == before)
        # Export changes the layout byte hash by design; compare exact masks and
        # preserved parameter/object identities rather than claiming equal hashes.
        from etlayout.geometry.layout import LayoutDocument
        from etlayout.routing.geometry import canonical_geometry_id
        first = LayoutDocument(project.layout.path).extract(project.layout.cell)
        second = LayoutDocument(exported.project.layout.path).extract(exported.project.layout.cell)
        summary["export_reimport"]["masks_identical"] = first.keys() == second.keys() and all(
            canonical_geometry_id(first[key]) == canonical_geometry_id(second[key]) for key in first)
        summary["export_reimport"]["groups_preserved"] = project.power.devices == exported.project.power.devices
        if not exported.valid or not summary["export_reimport"]["masks_identical"] or not summary["export_reimport"]["groups_preserved"]:
            summary["status"] = "invalid_export"
        if args.save_fields:
            save_project(project, output / "simulation.etl", result)
        summary.update(source_fingerprint=state["source_fingerprint"], phases=phases,
                       wall_seconds=time.perf_counter() - started, mesh_factor=args.mesh_factor, mode=project.solver.mode)
        summary["source_unchanged_during_run"] = source_fingerprint() == state["source_fingerprint"]
        if not summary["source_unchanged_during_run"]:
            summary["status"] = "source_changed_during_run"
        write(output / "receipt.json", summary)
        write(output / "state.json", dict(status="complete", phase="complete"))
        return 0
    except Exception as error:
        traceback.print_exc()
        write(output / "receipt.json", dict(status="resource_limited" if "budget" in str(error) else "failed",
            error=str(error), phase=state["phase"], source_fingerprint=state["source_fingerprint"],
            configuration=state.get("configuration"), wall_seconds=time.perf_counter() - started))
        return 2


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--examples", nargs="+", default=["E21", "E23"])
    parser.add_argument("--preset", default="reference")
    parser.add_argument("--variant")
    parser.add_argument("--current", type=float)
    parser.add_argument("--fingers", type=int)
    parser.add_argument("--gate-width-um", type=float)
    parser.add_argument("--stress", action="store_true")
    parser.add_argument("--thermal", default="attached")
    parser.add_argument("--mode", choices=["electrical", "electrothermal"])
    parser.add_argument("--mesh-factor", type=float, default=1.)
    parser.add_argument("--cpu-threads", type=int, default=4)
    parser.add_argument("--timeout", type=float, default=900.)
    parser.add_argument("--output", type=Path, default=ROOT / ".artifacts/power-benchmarks")
    parser.add_argument("--save-fields", action="store_true", help="Retain potentially large solved fields in the artifact directory")
    parser.add_argument("--worker", action="store_true", help=argparse.SUPPRESS)
    args = parser.parse_args(argv)
    if not 0 < args.mesh_factor <= 4 or not math.isfinite(args.timeout) or not 0 < args.timeout:
        parser.error("Use a finite positive mesh factor up to 4 and positive timeout")
    from etlayout.example_catalog import example_info
    from etlayout.power_examples import settings
    names = [example_info(name).name for name in args.examples]
    if args.worker:
        return worker(args, names[0], args.output)
    import psutil

    args.output.mkdir(parents=True, exist_ok=True)
    records = []
    for name in names:
        config = settings(name, preset=args.preset, variant=args.variant, current=args.current, thermal=args.thermal,
                          fingers=args.fingers, gate_width_um=args.gate_width_um)
        destination = args.output / name
        destination.mkdir(parents=True, exist_ok=True)
        command = [sys.executable, str(Path(__file__).resolve()), "--worker", "--examples", name,
                   "--preset", args.preset, "--thermal", args.thermal, "--mesh-factor", str(args.mesh_factor),
                   "--cpu-threads", str(args.cpu_threads), "--output", str(destination)]
        for key in ("variant", "current", "mode", "fingers", "gate_width_um"):
            if getattr(args, key) is not None:
                command += ["--" + key.replace("_", "-"), str(getattr(args, key))]
        if args.stress:
            command.append("--stress")
        if args.save_fields:
            command.append("--save-fields")
        start, peak, stopped = time.perf_counter(), 0, None
        memory_limit = min(config.memory_budget_bytes, int(psutil.virtual_memory().available * .8))
        with (destination / "worker.log").open("w", encoding="utf-8") as log:
            process = subprocess.Popen(command, stdout=log, stderr=subprocess.STDOUT)
            monitored = psutil.Process(process.pid)
            while process.poll() is None:
                try:
                    children = monitored.children(recursive=True)
                    rss = sum(child.memory_info().rss for child in [monitored, *children] if child.is_running())
                    peak = max(peak, rss)
                    reason = ("memory budget" if peak > memory_limit else
                              "wall-time budget" if time.perf_counter() - start > args.timeout else None)
                    if reason:
                        stopped = reason
                        for child in reversed(children):
                            child.kill()
                        process.kill()
                        break
                except psutil.NoSuchProcess:
                    pass
                time.sleep(.1)
            process.wait()
        receipt_path = destination / "receipt.json"
        if stopped or not receipt_path.exists():
            state = json.loads((destination / "state.json").read_text()) if (destination / "state.json").exists() else {}
            row = dict(status="resource_limited" if stopped else "failed", reason=stopped,
                       phase=state.get("phase"), configuration=state.get("configuration"))
        else:
            row = json.loads(receipt_path.read_text())
        row.update(name=name, peak_process_tree_RSS_bytes=peak, exit_code=process.returncode,
                   process_wall_seconds=time.perf_counter() - start,
                   hardware=dict(platform=platform.platform(), processor=platform.processor(),
                                 logical_cpus=psutil.cpu_count(), physical_cpus=psutil.cpu_count(logical=False),
                                 physical_memory_bytes=psutil.virtual_memory().total),
                   limits=dict(memory_bytes=memory_limit, declared_memory_bytes=config.memory_budget_bytes,
                               wall_seconds=args.timeout))
        write(receipt_path, row)
        records.append(row)
        write(args.output / "summary.json", records)
        print(name, row["status"], f"peak RSS {peak / 1024**2:.1f} MiB", f"{row['process_wall_seconds']:.2f}s", flush=True)
    return int(any(row["status"] in {"failed", "invalid_export", "source_changed_during_run"} for row in records))


if __name__ == "__main__":
    raise SystemExit(main())
