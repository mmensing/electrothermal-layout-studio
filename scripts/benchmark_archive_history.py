# SPDX-License-Identifier: MPL-2.0
"""Opt-in schema-2/3 archive and explicit history-store measurements.

Each retained entry references a small pool of genuinely solved synthetic cases.
Repeating a result stresses storage; it does not represent additional experiments.
Raw archives and receipts belong in disposable artifacts, never the source tree.
"""
from __future__ import annotations

import argparse
import dataclasses
import gc
import hashlib
import io
import json
import math
import os
import platform
import shutil
import statistics
import struct
import subprocess
import sys
import time
import zipfile
from pathlib import Path

import numpy as np
import psutil

FAMILIES = ("shared", "varied-mesh", "varied-layout", "transient")


def array_digest(array):
    data = np.asarray(array)
    prefix = json.dumps([data.dtype.str, data.shape], separators=(",", ":")).encode()
    return hashlib.sha256(prefix + b"\0" + data.tobytes(order="C")).hexdigest()


def typed_value(value):
    """Independent exact fingerprint, including types omitted by JSON coercion.

    This does not call the archive codec. Only the documented location change of
    a SHA-identified source layout is normalized; all result metadata is retained.
    """
    if isinstance(value, np.ndarray):
        return ["ndarray", value.dtype.str, list(value.shape),
                hashlib.sha256(value.tobytes(order="C")).hexdigest()]
    if isinstance(value, np.generic):
        return ["numpy-scalar", value.dtype.str, value.tobytes().hex()]
    if dataclasses.is_dataclass(value):
        entries = []
        for field in dataclasses.fields(value):
            item = getattr(value, field.name)
            if value.__class__.__name__ == "SimulationResult" and field.name == "configuration":
                item = normalize_configuration(item)
            entries.append([field.name, typed_value(item)])
        return ["dataclass", value.__class__.__name__, entries]
    if type(value) is dict:
        entries = [[typed_value(key), typed_value(item)] for key, item in value.items()]
        return ["dict", sorted(entries, key=lambda pair: json.dumps(pair[0], sort_keys=True))]
    if type(value) in (list, tuple):
        return [type(value).__name__, [typed_value(item) for item in value]]
    if type(value) is float:
        return ["float", struct.pack(">d", value).hex()]
    if type(value) in (str, int, bool) or value is None:
        return [type(value).__name__, value]
    raise TypeError(f"Unsupported benchmark metadata type: {type(value).__name__}")


def normalize_configuration(configuration):
    value = dict(configuration)
    if value.get("layout", {}).get("sha256"):
        value["layout"] = {**value["layout"], "path": "<sha256-identified layout>"}
    return value


def exact_digest(value):
    encoded = json.dumps(typed_value(value), ensure_ascii=False, separators=(",", ":"), allow_nan=False)
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def result_digest(result):
    # Dataclass traversal includes diagnostics, execution, convergence, complete
    # configurations, mesh statistics, all frame metadata and future model fields.
    return exact_digest(result)


def require(condition, message):
    if not condition:
        raise RuntimeError(message)


def payload_inventory(path):
    """Hash bounded existing archive leaves; estimate duplication, not v3 size."""
    from etlayout.io.project import (
        MAX_ARCHIVE_BYTES,
        MAX_ARCHIVE_DEPTH,
        MAX_ARCHIVE_ENTRIES,
        inspect_archive_size,
    )
    inspect_archive_size(path)
    inventory = {kind: {"occurrences": 0, "expanded_bytes": 0, "unique": {}}
                 for kind in ("layout", "array", "metadata")}
    entries = 0
    used = 0

    def scan(source, depth=0):
        nonlocal entries, used
        if depth > MAX_ARCHIVE_DEPTH:
            raise RuntimeError("Benchmark archive depth exceeds the existing safety budget")
        with zipfile.ZipFile(source) as archive:
            for member in archive.infolist():
                if member.is_dir():
                    continue
                entries += 1
                if entries > MAX_ARCHIVE_ENTRIES:
                    raise RuntimeError("Benchmark entry budget exceeded")
                content = archive.read(member)
                if member.filename.endswith((".etl", ".npz")):
                    scan(io.BytesIO(content), depth + 1)
                    continue
                used += len(content)
                if used > MAX_ARCHIVE_BYTES:
                    raise RuntimeError("Benchmark expanded byte budget exceeded")
                kind = ("array" if member.filename.endswith(".npy") else "layout"
                        if member.filename.lower().endswith((".gds", ".gdsii", ".oas", ".oasis")) else "metadata")
                record = inventory[kind]
                record["occurrences"] += 1
                record["expanded_bytes"] += len(content)
                record["unique"][hashlib.sha256(content).hexdigest()] = len(content)
    scan(path)
    return {kind: {"occurrences": record["occurrences"], "expanded_bytes": record["expanded_bytes"],
                   "unique_objects": len(record["unique"]), "unique_bytes": sum(record["unique"].values())}
            for kind, record in inventory.items()}


def prepare_inputs(output, families=FAMILIES):
    from copy import deepcopy

    from etlayout.examples import make_example
    from etlayout.io.project import configuration_hash, physics_hash, project_from_dict, save_project
    from etlayout.simulation.runner import run_project

    manifests = {}
    for family in families:
        cases = [("E19", None, None)] if family == "transient" else (
            [("E1", .6e-6, voltage) for voltage in (.01, .02, .03)] if family == "shared" else
            [("E1", size, .02) for size in (.4e-6, .6e-6, .9e-6)] if family == "varied-mesh" else
            [(name, .6e-6, None) for name in ("E1", "E2", "E3")])
        rows = []
        # The first two families deliberately reuse one exact source layout.
        shared = make_example("E1", output / "inputs" / family) if family in {"shared", "varied-mesh"} else None
        for index, (name, size, voltage) in enumerate(cases):
            project = deepcopy(shared) if shared else make_example(name, output / "inputs" / family / str(index))
            if size is not None:
                project.solver.mode = "electrical"
                project.mesh.max_size, project.mesh.min_size, project.mesh.contact_size = size, .2e-6, .2e-6
            if voltage is not None:
                next(terminal for terminal in project.terminals if terminal.id == "drain").value = voltage
            result = run_project(project)
            require(result.converged and (not result.transient or result.transient.completed),
                    f"Input {family}/{index} did not complete")
            project = project_from_dict(result.configuration)
            if family in {"shared", "varied-mesh"}:
                require(np.isclose(result.metrics["resistance"], .084, rtol=1e-9),
                        "E1 input does not agree with its independent analytical resistance")
            target = output / "inputs" / f"{family}-{index}.etl"
            save_project(project, target, result)
            mesh_hash = hashlib.sha256((array_digest(result.mesh.points) + array_digest(result.mesh.triangles)).encode()).hexdigest()
            rows.append({"file": target.relative_to(output).as_posix(), "example": name,
                "nodes": len(result.mesh.points), "elements": len(result.mesh.triangles),
                "mesh_hash": mesh_hash, "layout_sha256": project.layout.sha256,
                "configuration_hash": configuration_hash(project), "physics_hash": physics_hash(project),
                "snapshots": len(result.transient.snapshots) if result.transient else 0,
                "solve_seconds": result.elapsed_seconds, "mode": project.solver.mode})
        manifests[family] = rows
    if "shared" in manifests:
        require(len({row["mesh_hash"] for row in manifests["shared"]}) == 1, "Shared mesh changed")
    if "varied-mesh" in manifests:
        require(len({row["layout_sha256"] for row in manifests["varied-mesh"]}) == 1, "Shared source changed")
        require(len({row["mesh_hash"] for row in manifests["varied-mesh"]}) == 3, "Mesh variants are not distinct")
    if "varied-layout" in manifests:
        require(len({row["layout_sha256"] for row in manifests["varied-layout"]}) == 3, "Layout variants are not distinct")
    (output / "inputs.json").write_text(json.dumps(manifests, indent=2), encoding="utf-8")
    return manifests


def copy_inputs(source, output, families):
    """Reuse exact solved inputs; manifests cannot escape their explicitly selected root."""
    source = source.resolve(strict=True)
    manifest_path = source / "inputs.json"
    require(manifest_path.stat().st_size <= 20_000_000, "Input manifest exceeds metadata budget")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    selected, hashes = {}, {}
    for family in families:
        rows = manifest[family]
        require(isinstance(rows, list) and 0 < len(rows) <= 3, "Unexpected input pool size")
        selected[family] = rows
        for row in rows:
            relative = Path(row["file"])
            require(not relative.is_absolute() and relative.parts[0] == "inputs"
                    and all(part not in {".", ".."} and ":" not in part for part in relative.parts)
                    and relative.suffix == ".etl", "Input manifest contains an unsafe file name")
            original = (source / relative).resolve(strict=True)
            require(original.is_relative_to(source), "Input manifest escapes its selected root")
            target = output / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            require(original.stat().st_size <= 2_000_000_000, "Input archive exceeds storage budget")
            digest = hashlib.sha256()
            with original.open("rb") as reader, target.open("xb") as writer:
                while chunk := reader.read(1024 * 1024):
                    digest.update(chunk)
                    writer.write(chunk)
            hashes[relative.as_posix()] = digest.hexdigest()
    (output / "inputs.json").write_text(json.dumps(selected, indent=2), encoding="utf-8")
    return selected, hashes


def case_path(output, family, count, archive_format, replicate):
    return output / f"schema-{archive_format}" / f"replicate-{replicate}" / f"{family}-{count}.etl"


def run_case(output, family, count, archive_format=2, replicate=1):
    from etlayout.io.project import (
        configuration_hash,
        document_id,
        estimate_project_archive,
        load_project,
        load_run_history,
        save_project,
    )
    from etlayout.io.saving import SaveSnapshot, freeze_result_arrays

    manifests = json.loads((output / "inputs.json").read_text(encoding="utf-8"))
    pool = [load_project(output / record["file"]) for record in manifests[family]]
    project, result = pool[0]
    require(result is not None, "A solved input lost its result")
    history = [pool[(index + 1) % len(pool)][1] for index in range(count - 1)]
    expected = [result_digest(value) for value in [result, *history]]
    frame_digests = ([result_digest(frame) for frame in result.transient.snapshots]
                     if result.transient else [])
    before = configuration_hash(project)
    before_project = exact_digest(normalize_configuration(project.to_dict()))
    before_document = document_id(project)
    start = time.perf_counter()
    for _, value in pool:
        freeze_result_arrays(value)
    freeze_seconds = time.perf_counter() - start
    started = time.perf_counter()
    snapshot = SaveSnapshot.capture(project, result, history, revision=count)
    snapshot_seconds = time.perf_counter() - started
    require(configuration_hash(project) == before, "Snapshot capture changed the input configuration")
    estimate = estimate_project_archive(snapshot.project, snapshot.result, list(snapshot.history))
    target = case_path(output, family, count, archive_format, replicate)
    target.parent.mkdir(parents=True, exist_ok=True)
    started = time.perf_counter()
    options = {"archive_format": 3} if archive_format == 3 else {}
    save_project(snapshot.project, target, snapshot.result, history=list(snapshot.history), **options)
    save_seconds = time.perf_counter() - started
    del snapshot, pool, history, result, project
    gc.collect()
    started = time.perf_counter()
    reopened, current = load_project(target)
    current_load_seconds = time.perf_counter() - started
    started = time.perf_counter()
    loaded = load_run_history(target)
    history_load_seconds = time.perf_counter() - started
    require(len(loaded) == count - 1, "Stored history count changed")
    require([result_digest(value) for value in [current, *loaded]] == expected,
            "Reopened arrays or typed result metadata changed")
    require(configuration_hash(reopened) == before, "Reopened configuration identity changed")
    require(exact_digest(normalize_configuration(reopened.to_dict())) == before_project,
            "Reopened complete project values or types changed")
    require(document_id(reopened) == before_document, "Reopened document identity changed")
    expected_path = target.with_suffix(".expected.json")
    expected_path.write_text(json.dumps({"results": expected, "frames": frame_digests,
                                       "configuration_hash": before, "document_id": before_document}), encoding="utf-8")
    return {"family": family, "archive_format": archive_format, "replicate": replicate,
        "total_retained_records": count, "historical_entries": count - 1,
        "initial_freeze_seconds": freeze_seconds, "snapshot_seconds": snapshot_seconds,
        "save_seconds": save_seconds, "current_load_seconds": current_load_seconds,
        "eager_history_load_seconds": history_load_seconds, "archive_bytes": target.stat().st_size,
        "schema2_logical_estimate": estimate, "payload_inventory": payload_inventory(target),
        "expected_result_digests": expected, "round_trip_exact": True}


def run_reader_case(output, family, count, archive_format, replicate, mode):
    target = case_path(output, family, count, archive_format, replicate)
    expected = json.loads(target.with_suffix(".expected.json").read_text(encoding="utf-8"))
    receipt = {"family": family, "archive_format": archive_format, "replicate": replicate,
               "total_retained_records": count, "reader_mode": mode, "archive_bytes": target.stat().st_size}
    if mode == "eager":
        from etlayout.io.project import load_project, load_run_history
        started = time.perf_counter()
        _, current = load_project(target)
        receipt["current_load_seconds"] = time.perf_counter() - started
        started = time.perf_counter()
        history = load_run_history(target)
        receipt["history_load_seconds"] = time.perf_counter() - started
        receipt["history_ready_seconds"] = receipt["current_load_seconds"] + receipt["history_load_seconds"]
        require([result_digest(value) for value in [current, *history]] == expected["results"],
                "Fresh eager reader changed typed results")
    else:
        from etlayout.io.history_store import open_history_catalog
        copies = target.parent / f"{target.stem}-{mode}-readcopies"
        copies.mkdir(exist_ok=False)
        started = time.perf_counter()
        with open_history_catalog(target, copy_directory=copies) as store:
            receipt["catalog_seconds"] = time.perf_counter() - started
            records = store.records
            require(len(records) == count and len({record.run_id for record in records}) == count,
                    "Catalog lost or conflated repeated run records")
            require(records[0].run_id == store.current_run_id, "Catalog changed current/history order")
            receipt["catalog_stats"] = dataclasses.asdict(store.stats)
            if mode == "lazy-startup":
                # Display exactly the first stored frame for a transient. Do not
                # describe a partial result as full materialization.
                frame = bool(expected["frames"])
                expected_digest = expected["frames"][0] if frame else expected["results"][0]
                def acquire():
                    return (store.acquire_frame(store.current_run_id, 0) if frame
                            else store.acquire(store.current_run_id))
                started = time.perf_counter()
                with acquire() as selected:
                    receipt["selected_seconds"] = time.perf_counter() - started
                    require(result_digest(selected) == expected_digest, "Selected run/frame changed")
                    receipt["selected_stats"] = dataclasses.asdict(store.stats)
                started = time.perf_counter()
                with acquire() as selected:
                    receipt["warm_selection_seconds"] = time.perf_counter() - started
                    require(result_digest(selected) == expected_digest, "Warm cache changed selected fields")
                receipt["warm_stats"] = dataclasses.asdict(store.stats)
                del selected
                store.clear()
                receipt["cleared_stats"] = dataclasses.asdict(store.stats)
                require(store.stats.pinned_bytes == 0, "Closed view retained a pinned lease")
                started = time.perf_counter()
                with acquire() as selected:
                    receipt["cold_cache_selection_seconds"] = time.perf_counter() - started
                    require(result_digest(selected) == expected_digest, "Reload after eviction changed fields")
                receipt["history_ready_seconds"] = receipt["catalog_seconds"] + receipt["selected_seconds"]
            elif mode == "lazy-full":
                started = time.perf_counter()
                actual = []
                for record in records:
                    with store.acquire(record.run_id) as result:
                        actual.append(result_digest(result))
                receipt["full_scan_seconds"] = time.perf_counter() - started
                require(actual == expected["results"], "Sequential lazy materialization changed typed results/order")
            else:
                raise ValueError(f"Unknown reader mode {mode}")
            receipt["final_stats"] = dataclasses.asdict(store.stats)
    receipt["round_trip_exact"] = True
    return receipt


def process_tree_rss(process):
    total = 0
    for member in [process, *process.children(recursive=True)]:
        try:
            total += member.memory_info().rss
        except psutil.NoSuchProcess:
            pass
    return total


def stop_process_tree(process):
    owned = [*reversed(process.children(recursive=True)), process]
    for member in owned:
        try:
            member.terminate()
        except psutil.NoSuchProcess:
            pass
    _, alive = psutil.wait_procs(owned, timeout=5)
    for member in alive:
        try:
            member.kill()
        except psutil.NoSuchProcess:
            pass
    psutil.wait_procs(alive, timeout=5)


def observe(command, prefix, *, max_rss_gib, timeout):
    stderr, stdout = prefix.with_suffix(".stderr.log"), prefix.with_suffix(".json")
    prefix.parent.mkdir(parents=True, exist_ok=True)
    temporary = prefix.with_name(prefix.name + "-temp")
    temporary.mkdir(exist_ok=False)
    environment = {**os.environ, "TEMP": str(temporary), "TMP": str(temporary)}
    with stderr.open("w", encoding="utf-8") as error_file, stdout.open("w", encoding="utf-8") as result_file:
        child = subprocess.Popen(command, stdout=result_file, stderr=error_file, env=environment)
        process = psutil.Process(child.pid)
        peak, limited = 0, ""
        start = time.perf_counter()
        while child.poll() is None:
            try:
                peak = max(peak, process_tree_rss(process))
            except psutil.NoSuchProcess:
                break
            if peak > max_rss_gib * 1024 ** 3 or time.perf_counter() - start > timeout:
                limited = "peak RSS" if peak > max_rss_gib * 1024 ** 3 else "wall time"
                stop_process_tree(process)
                break
            time.sleep(.01)
        code = child.wait(timeout=30)
    record = json.loads(stdout.read_text(encoding="utf-8")) if code == 0 else {
        "status": "resource-limited" if limited else "failed",
        "limitation": limited or "Inspect retained stderr; no successful load is claimed", "exit_code": code}
    record.update(process_wall_seconds=time.perf_counter() - start, peak_rss_bytes=peak)
    return record


def summarize(records):
    groups = {}
    for row in records:
        key = (row["family"], row["total_retained_records"], row["archive_format"], row.get("reader_mode", "save-reopen"))
        groups.setdefault(key, []).append(row)
    summaries = []
    for (family, count, archive_format, mode), rows in groups.items():
        summary = {"family": family, "total_retained_records": count, "archive_format": archive_format,
                   "mode": mode, "observations": len(rows), "all_exact": all(row.get("round_trip_exact") for row in rows)}
        values = {}
        for field in rows[0]:
            if field.endswith(("_seconds", "_bytes")) and all(type(row.get(field)) in (int, float) for row in rows):
                samples = [row[field] for row in rows]
                values[field] = {"median": statistics.median(samples), "min": min(samples), "max": max(samples)}
        summary["measurements"] = values
        summaries.append(summary)
    return summaries


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True, help="New output directory; existing directories are refused")
    parser.add_argument("--counts", type=int, nargs="+", default=[1, 10, 50], choices=range(1, 51))
    parser.add_argument("--families", nargs="+", default=list(FAMILIES), choices=FAMILIES)
    parser.add_argument("--formats", type=int, nargs="+", default=[2], choices=(2, 3))
    parser.add_argument("--repeats", type=int, default=3, help="Fresh process observations; timings include no cache flush")
    parser.add_argument("--inputs-from", type=Path, help="Reuse byte-identical solved inputs from a previous output directory")
    parser.add_argument("--reader-phases", nargs="*", default=[], choices=("eager", "lazy-startup", "lazy-full"),
                        help="Additional fresh reader-only processes; lazy modes require schema 3")
    parser.add_argument("--max-rss-gib", type=float, default=3.)
    parser.add_argument("--timeout", type=float, default=600., help="Per observation; resource limits are failures")
    parser.add_argument("--case", nargs=2, metavar=("FAMILY", "COUNT"), help=argparse.SUPPRESS)
    parser.add_argument("--archive-format", type=int, default=2, choices=(2, 3), help=argparse.SUPPRESS)
    parser.add_argument("--replicate", type=int, default=1, help=argparse.SUPPRESS)
    parser.add_argument("--reader-case", choices=("eager", "lazy-startup", "lazy-full"), help=argparse.SUPPRESS)
    args = parser.parse_args(argv)
    output = args.output.resolve()
    if args.case:
        family, count = args.case[0], int(args.case[1])
        require(family in FAMILIES and 1 <= count <= 50, "Invalid internal case")
        record = (run_reader_case(output, family, count, args.archive_format, args.replicate, args.reader_case)
                  if args.reader_case else run_case(output, family, count, args.archive_format, args.replicate))
        print(json.dumps(record, allow_nan=False))
        return 0
    if not math.isfinite(args.max_rss_gib) or not math.isfinite(args.timeout) or args.max_rss_gib <= 0 or args.timeout <= 0:
        parser.error("Resource budgets must be positive and finite")
    if not 1 <= args.repeats <= 20:
        parser.error("Repeats must be between 1 and 20")
    for name in ("counts", "families", "formats", "reader_phases"):
        if len(getattr(args, name)) != len(set(getattr(args, name))):
            parser.error(f"Duplicate {name.replace('_', ' ')} are not independent observations")
    source = Path(__file__).resolve().parents[1]
    git = shutil.which("git")
    if git is None:
        parser.error("Run this source benchmark with Git available for exact revision provenance")
    revision = subprocess.check_output([git, "-C", str(source), "rev-parse", "HEAD"], text=True).strip()
    dirty = subprocess.check_output([git, "-C", str(source), "status", "--porcelain"], text=True).strip()
    import etlayout
    require(Path(etlayout.__file__).resolve().is_relative_to(source / "src"),
            "Benchmark runtime differs from this checkout; set PYTHONPATH to this checkout's src")
    output.mkdir(parents=True, exist_ok=False)
    temporary = output / "preparation-temp"
    temporary.mkdir()
    os.environ.update(TEMP=str(temporary), TMP=str(temporary))
    started = time.perf_counter()
    if args.inputs_from:
        inputs, input_hashes = copy_inputs(args.inputs_from, output, args.families)
    else:
        inputs = prepare_inputs(output, args.families)
        input_hashes = {row["file"]: hashlib.sha256((output / row["file"]).read_bytes()).hexdigest()
                        for rows in inputs.values() for row in rows}
    records = []
    for replicate in range(1, args.repeats + 1):
        for family in args.families:
            for count in args.counts:
                for archive_format in args.formats:
                    base = [sys.executable, str(Path(__file__).resolve()), "--output", str(output), "--case", family,
                            str(count), "--archive-format", str(archive_format), "--replicate", str(replicate)]
                    target = case_path(output, family, count, archive_format, replicate)
                    phases = [None, *[mode for mode in args.reader_phases
                                     if archive_format == 3 or mode == "eager"]]
                    for mode in phases:
                        command = [*base, "--reader-case", mode] if mode else base
                        prefix = target.with_name(f"{target.stem}-{mode or 'save-reopen'}")
                        record = observe(command, prefix, max_rss_gib=args.max_rss_gib, timeout=args.timeout)
                        record.update(family=family, total_retained_records=count, archive_format=archive_format,
                                      replicate=replicate, reader_mode=mode or "save-reopen")
                        records.append(record)
                        print(json.dumps({key: record.get(key) for key in ("family", "total_retained_records", "archive_format",
                              "replicate", "reader_mode", "save_seconds", "history_ready_seconds", "peak_rss_bytes", "status")}), flush=True)
                        (output / "measurements.json").write_text(json.dumps(records, indent=2), encoding="utf-8")
                        (output / "summary.json").write_text(json.dumps(summarize(records), indent=2), encoding="utf-8")
                        if not record.get("round_trip_exact"):
                            break
    receipt = {"writer_revision": revision, "worktree_dirty": bool(dirty),
        "harness_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(), "input_sha256": input_hashes,
        "peak_rss_scope": "Sampled 10ms aggregate of owned launcher and descendant Python processes; imports and validation included",
        "storage_only": True, "repeated_pool_entries_are_not_independent_analyses": True,
        "cache_state": "Fresh process per observation; OS filesystem caches were not flushed; cold-cache means application cache eviction",
        "typed_equality": "Every model dataclass field, recursive metadata types, float bits, and array dtype/shape/logical bytes; only SHA-identified layout path relocation normalized",
        "python": platform.python_version(), "numpy": np.__version__, "platform": platform.platform(), "processor": platform.processor(),
        "logical_cpus": psutil.cpu_count(), "physical_cpus": psutil.cpu_count(logical=False),
        "ram_bytes": psutil.virtual_memory().total, "max_rss_gib": args.max_rss_gib, "repeats": args.repeats,
        "inputs": inputs, "measurements": records, "summaries": summarize(records), "elapsed_seconds": time.perf_counter() - started}
    (output / "receipt.json").write_text(json.dumps(receipt, indent=2), encoding="utf-8")
    return int(any(not row.get("round_trip_exact") for row in records))


if __name__ == "__main__":
    raise SystemExit(main())
