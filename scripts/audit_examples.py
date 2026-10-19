# SPDX-License-Identifier: MPL-2.0
"""Opt-in scientific audit of exact configured examples; never called by generation.

Uses one fresh bounded process per project. Detailed fields and logs stay in the
chosen artifact directory. A single solve does not establish mesh verification.
"""
from __future__ import annotations

import argparse
import json
import platform
import sys
import time
import traceback
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))


def write(path, value):
    from etlayout.io.project import _json_dump
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(_json_dump(value) + "\n", encoding="utf-8")


def worker(identifier, output, cpu_threads, save_fields):
    from etlayout.example_science import scientific_record
    from etlayout.examples import make_example
    from etlayout.geometry.layout import LayoutDocument
    from etlayout.io.project import load_project, physics_hash, save_project
    from etlayout.routing.geometry import canonical_geometry_id
    from etlayout.simulation.runner import run_project

    started = time.perf_counter()
    output.mkdir(parents=True, exist_ok=True)
    try:
        project = make_example(identifier, output / "inputs")
        original = project.to_dict()
        def progress(fraction, message):
            write(output / "state.json", dict(phase=message, fraction=fraction,
                elapsed_seconds=time.perf_counter() - started))
        result = run_project(project, progress=progress,
            execution={"cpu_threads": cpu_threads, "study_workers": 1})
        record = scientific_record(project, result)
        assert project.to_dict() == original, "Audit changed the nominal physical configuration"
        masks = LayoutDocument(project.layout.path).extract(project.layout.cell)
        oasis = LayoutDocument(Path(project.layout.path).with_suffix(".oas")).extract(project.layout.cell)
        assert masks.keys() == oasis.keys() and all(canonical_geometry_id(masks[key]) == canonical_geometry_id(oasis[key])
            for key in masks), "GDSII/OASIS mask geometry differs"
        record["portability"] = dict(format_geometry_equal=True)
        if save_fields:
            archive = output / "solved.etl"
            start = time.perf_counter()
            save_project(project, archive, result, archive_format=3)
            record["portability"]["save_seconds"] = time.perf_counter() - start
            start = time.perf_counter()
            reopened, restored = load_project(archive)
            record["portability"]["reopen_seconds"] = time.perf_counter() - start
            assert physics_hash(reopened) == physics_hash(project)
            assert restored.configuration_hash == result.configuration_hash
            record["portability"].update(archive_bytes=archive.stat().st_size, reopened=True)
        record["wall_seconds"] = time.perf_counter() - started
        write(output / "receipt.json", record)
        print(json.dumps(dict(status="passed" if result.converged else "not_converged", id=identifier)))
        return 0 if result.converged else 1
    except Exception as error:
        traceback.print_exc()
        write(output / "receipt.json", dict(status="failed", id=identifier,
            error_type=type(error).__name__, error=str(error), wall_seconds=time.perf_counter() - started))
        return 1


def main(argv=None):
    from benchmark_archive_history import observe
    from verify_extended_examples import source_fingerprint

    from etlayout.example_catalog import example_info
    from etlayout.examples import EXAMPLE_NAMES
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--examples", nargs="+", default=EXAMPLE_NAMES)
    parser.add_argument("--output", type=Path, required=True, help="New artifact directory, outside tracked source")
    parser.add_argument("--cpu-threads", type=int, default=4)
    parser.add_argument("--max-rss-gib", type=float, default=6.)
    parser.add_argument("--timeout", type=float, default=1200.)
    parser.add_argument("--save-fields", action="store_true")
    parser.add_argument("--worker", help=argparse.SUPPRESS)
    args = parser.parse_args(argv)
    if args.worker:
        return worker(args.worker, args.output, args.cpu_threads, args.save_fields)
    names = [example_info(value).name for value in args.examples]
    if len(names) != len(set(names)):
        parser.error("Choose distinct example IDs")
    if args.cpu_threads < 1 or args.max_rss_gib <= 0 or args.timeout <= 0:
        parser.error("CPU count, memory and timeout must be positive")
    args.output.mkdir(parents=True, exist_ok=False)
    fingerprint = source_fingerprint()
    records = []
    start = time.perf_counter()
    for name in names:
        identifier = example_info(name).id
        command = [sys.executable, str(Path(__file__).resolve()), "--worker", identifier,
            "--output", str(args.output / identifier), "--cpu-threads", str(args.cpu_threads)]
        if args.save_fields:
            command.append("--save-fields")
        record = observe(command, args.output / (identifier + "-process"),
            max_rss_gib=args.max_rss_gib, timeout=args.timeout)
        receipt_path = args.output / identifier / "receipt.json"
        if receipt_path.is_file():
            scientific = json.loads(receipt_path.read_text(encoding="utf-8"))
            scientific["resource_observation"] = {key: record.get(key) for key in
                ("process_wall_seconds", "peak_rss_bytes", "status")}
            write(receipt_path, scientific)
        records.append(record)
        write(args.output / "index.json", dict(source_fingerprint=fingerprint,
            source_unchanged=source_fingerprint() == fingerprint, python=platform.python_version(),
            platform=platform.platform(), observations=records, wall_seconds=time.perf_counter() - start,
            timing_scope="Fresh process per example; OS caches not flushed; shared host", mesh_verified=False))
        print(f"{identifier}: {record['status']} ({record['process_wall_seconds']:.2f} s)", flush=True)
    return int(any(row["status"] != "passed" for row in records) or source_fingerprint() != fingerprint)


if __name__ == "__main__":
    raise SystemExit(main())
