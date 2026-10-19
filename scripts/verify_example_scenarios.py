# SPDX-License-Identifier: MPL-2.0
"""Opt-in exact scenario, mesh and time observations with bounded resources.

Generation never calls this driver. Failed engineering demonstrations must still
converge numerically and retain real fields; they are not accepted designs.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import sys
import time
from copy import deepcopy
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))


def write(path, value):
    from etlayout.io.project import _json_dump
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(_json_dump(value) + "\n", encoding="utf-8")


def default_cases():
    from etlayout.example_scenarios import scenarios
    return [identifier + ":" + scenario.id for identifier in
        ("E11", "E15", "E16", "E18", "E26", "E29", "E30", "E33", "E34", "E35", "E36")
        for scenario in scenarios(identifier) if scenario.id != "reference"]


def worker(case, factor, output, threads, save_fields):
    from verify_extended_examples import validate_nominal, verify_portability

    from etlayout.example_science import scientific_record
    from etlayout.examples import make_example
    from etlayout.io.project import save_project
    from etlayout.simulation.runner import run_project

    identifier, scenario = case.split(":")
    project = make_example(identifier, output / "inputs", scenario=scenario)
    for key in ("max_size", "min_size", "contact_size"):
        setattr(project.mesh, key, getattr(project.mesh, key) * factor)
    for refinement in project.mesh.refinements:
        refinement.size *= factor
        refinement.axis_sizes = [value * factor for value in refinement.axis_sizes]
    save_project(project, output / "inputs" / (project.name + ".etl"))
    before, start = project.to_dict(), time.perf_counter()
    result = run_project(project, execution={"cpu_threads": threads, "study_workers": 1})
    assert project.to_dict() == before, "Scenario verification changed the configured project"
    if scenario == "thermal_stop":
        from power_examples_desktop_acceptance import verify_scientific_acceptance
        checks = dict(conservation_checks=verify_scientific_acceptance(project, result),
            accepted_partial=True, requested_stop_time=project.transient.stop_time,
            actual_stop_time=result.metrics["transient_time"])
        assert not result.transient.completed and result.metrics["thermal_limit_compliance"]["stopped_early"]
        assert result.metrics["transient_time"] < project.transient.stop_time
    else:
        checks = validate_nominal(project, result)
    expected_failure = scenario in {"thermal_limit_failure", "thermal_stop"}
    assert result.metrics["engineering_accepted"] != expected_failure
    record = scientific_record(project, result)
    record.update(case=case, mesh_factor=factor, numerical_acceptance=checks,
        wall_seconds=time.perf_counter() - start, expected_engineering_failure=expected_failure)
    if save_fields:
        record["portability"] = verify_portability(project, result, output / "portability")
        save_project(project, output / "solved.etl", result, archive_format=3)
    write(output / "receipt.json", record)
    print(json.dumps(dict(status="passed", case=case, expected_engineering_failure=expected_failure)))


def time_study(case, steps, output, threads):
    from etlayout.example_science import scientific_record
    from etlayout.examples import make_example
    from etlayout.simulation.runner import clear_caches
    from etlayout.simulation.sweep import time_step_convergence_study

    identifier, scenario = case.split(":")
    project = make_example(identifier, output / "inputs", scenario=scenario)
    if not project.transient.enabled or any(limit.evaluation == "stop_transient" for limit in project.temperature_limits):
        raise ValueError("Time verification requires a full-duration transient, without early stopping")
    clear_caches()
    start = time.perf_counter()
    rows = time_step_convergence_study(project, steps, execution={"cpu_threads": threads, "study_workers": 1})
    records, hashes = [], []
    for row in rows:
        assert not row.error and row.result.converged
        candidate = deepcopy(project)
        candidate.transient.time_step = row.value
        candidate.transient.periodic_tolerance = 0.
        result = row.result
        digest = hashlib.sha256(result.mesh.points.tobytes() + result.mesh.triangles.tobytes()).hexdigest()
        hashes.append(digest)
        record = scientific_record(candidate, result)
        record.update(case=case, time_step_s=row.value, exact_mesh_sha256=digest)
        records.append(record)
    assert len(set(hashes)) == 1, "Timestep verification changed the exact mesh"
    write(output / "receipt.json", dict(status="passed", case=case, exact_mesh_sha256=hashes[0],
        wall_seconds=time.perf_counter() - start, rows=records, scope="Full-duration accepted states on one byte-identical mesh"))
    print(json.dumps(dict(status="passed", case=case, time_steps=steps)))


def main(argv=None):
    from benchmark_archive_history import observe
    from verify_extended_examples import source_fingerprint

    from etlayout.example_scenarios import scenarios
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cases", nargs="+", default=default_cases(), help="ID:scenario pairs; default all 17 thermal scenarios")
    parser.add_argument("--output", type=Path, required=True, help="New artifact directory")
    parser.add_argument("--mesh-factors", nargs="+", type=float, default=[1.])
    parser.add_argument("--time-steps", nargs="+", type=float, help="Also verify a selected thermal_demo transient on one exact mesh")
    parser.add_argument("--cpu-threads", type=int, default=4)
    parser.add_argument("--max-rss-gib", type=float, default=3.)
    parser.add_argument("--timeout", type=float, default=600.)
    parser.add_argument("--save-fields", action="store_true")
    parser.add_argument("--worker", nargs=2, help=argparse.SUPPRESS)
    parser.add_argument("--time-worker", help=argparse.SUPPRESS)
    args = parser.parse_args(argv)
    if args.worker:
        worker(args.worker[0], float(args.worker[1]), args.output, args.cpu_threads, args.save_fields)
        return 0
    if args.time_worker:
        time_study(args.time_worker, args.time_steps, args.output, args.cpu_threads)
        return 0
    if (any(not 0 < factor <= 1 for factor in args.mesh_factors) or args.cpu_threads < 1
            or args.max_rss_gib <= 0 or args.timeout <= 0
            or not math.isfinite(args.max_rss_gib) or not math.isfinite(args.timeout)):

        parser.error("Use positive resource bounds and mesh factors up to one")
    if len(set(args.cases)) != len(args.cases) or any(b >= a for a, b in zip(args.mesh_factors, args.mesh_factors[1:])):
        parser.error("Use distinct cases and strictly decreasing mesh factors")
    for case in args.cases:
        if case.count(":") != 1:
            parser.error("Each case needs ID:scenario")
        identifier, scenario = case.split(":")
        if scenario not in {item.id for item in scenarios(identifier)}:
            parser.error("Unsupported named scenario: " + case)
    args.output.mkdir(parents=True, exist_ok=False)
    fingerprint = hashlib.sha256(source_fingerprint().encode() + Path(__file__).read_bytes()).hexdigest()
    observations = []
    cases = [(case, factor) for case in args.cases for factor in args.mesh_factors]
    for case, factor in cases:
        destination = args.output / (case.replace(":", "-") + f"-mesh-{factor:g}")
        command = [sys.executable, str(Path(__file__).resolve()), "--output", str(destination),
            "--worker", case, str(factor), "--cpu-threads", str(args.cpu_threads)]
        if args.save_fields:
            command.append("--save-fields")
        observation = observe(command, destination.with_name(destination.name + "-process"),
            max_rss_gib=args.max_rss_gib, timeout=args.timeout)
        observations.append(observation)
        print(f"{case}, mesh factor {factor:g}: {observation['status']}", flush=True)
    if args.time_steps:
        for case in args.cases:
            if case.endswith(":thermal_demo"):
                identifier = case.split(":")[0]
                from etlayout.examples import make_example
                if not make_example(identifier, args.output / "time-check-inputs", scenario="thermal_demo").transient.enabled:
                    continue
                destination = args.output / (identifier + "-time")
                command = [sys.executable, str(Path(__file__).resolve()), "--output", str(destination),
                    "--time-worker", case, "--time-steps", *map(str, args.time_steps), "--cpu-threads", str(args.cpu_threads)]
                observations.append(observe(command, destination.with_name(destination.name + "-process"),
                    max_rss_gib=args.max_rss_gib, timeout=args.timeout))
    unchanged = fingerprint == hashlib.sha256(source_fingerprint().encode() + Path(__file__).read_bytes()).hexdigest()
    write(args.output / "index.json", dict(source_fingerprint=fingerprint, source_unchanged=unchanged,
        observations=observations, scope="Real exact configured scenarios; expected engineering failures are retained failures, not accepted designs"))
    return int(not unchanged or any(item["status"] != "passed" for item in observations))


if __name__ == "__main__":
    raise SystemExit(main())
