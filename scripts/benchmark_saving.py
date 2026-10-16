# SPDX-License-Identifier: MPL-2.0
"""Opt-in synthetic storage benchmark; no device solve or GUI setting changes."""
from __future__ import annotations

import argparse
import json
import platform
import threading
import time
from pathlib import Path

import numpy as np
import psutil
from PySide6.QtCore import QCoreApplication, QEventLoop, QTimer

from etlayout.io.project import configuration_hash, load_project, physics_hash, save_project
from etlayout.models import PlanarMesh, Project, SimulationResult


def synthetic_case(size):
    y, x = np.mgrid[0:size, 0:size]
    points = np.column_stack((x.ravel(), y.ravel())).astype(float) * 1e-6
    cells = np.arange((size - 1) ** 2)
    a = cells // (size - 1) * size + cells % (size - 1)
    triangles = np.concatenate((np.column_stack((a, a + 1, a + size)),
                                np.column_stack((a + 1, a + size + 1, a + size))))
    rng = np.random.default_rng(42)
    project = Project(name="Synthetic persistence benchmark")
    mesh = PlanarMesh(points, triangles, {"layer:m": np.arange(len(triangles))})
    result = SimulationResult(mesh, fields={"m": {
        "temperature": 300 + rng.random(len(points)), "potential": rng.random(len(points)),
        "current_density": rng.random((len(triangles), 2))}}, converged=True,
        configuration=project.to_dict(), configuration_hash=configuration_hash(project), physics_hash=physics_hash(project))
    return project, result


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--size", type=int, default=24, help="Square node grid; 512 is an opt-in large case")
    parser.add_argument("--history", type=int, default=0, help="Retained identical run references, 0..10")
    parser.add_argument("--synchronous", action="store_true", help="Measure the blocking Python writer")
    parser.add_argument("--output", type=Path, required=True, help="New output directory; existing outputs are not replaced")
    args = parser.parse_args(argv)
    if not 3 <= args.size <= 768 or not 0 <= args.history <= 10:
        parser.error("Use size 3..768 and history 0..10; keep enough memory/disk space for expanded history")
    args.output.mkdir(parents=True, exist_ok=False)
    target = args.output / "synthetic.etlayout"
    project, result = synthetic_case(args.size)
    history = [result] * args.history
    app = QCoreApplication.instance() or QCoreApplication([])
    ticks = []
    heartbeat = QTimer()
    heartbeat.setInterval(10)
    heartbeat.timeout.connect(lambda: ticks.append(time.perf_counter()))
    heartbeat.start()
    process = psutil.Process()
    rss_start = process.memory_info().rss
    peak = [rss_start]
    stop = threading.Event()
    def sample():
        while not stop.wait(.01):
            peak[0] = max(peak[0], process.memory_info().rss)
    monitor = threading.Thread(target=sample, daemon=True)
    monitor.start()
    freeze_seconds = snapshot_seconds = 0.
    started = time.perf_counter()
    try:
        if args.synchronous:
            save_project(project, target, result, history=history)
        else:
            from etlayout.gui.saving import SaveJob, SaveRequest
            from etlayout.io.saving import SaveOrder, SaveSnapshot, freeze_result_arrays
            start = time.perf_counter()
            freeze_result_arrays(result)
            freeze_seconds = time.perf_counter() - start
            start = time.perf_counter()
            snapshot = SaveSnapshot.capture(project, result, history, revision=1)
            snapshot_seconds = time.perf_counter() - start
            loop = QEventLoop()
            errors = []
            request = SaveRequest(snapshot, target, "benchmark", 1, ticket=SaveOrder.reserve(target))
            job = SaveJob(request)
            job.failed.connect(lambda request, message: errors.append(message))
            job.finished.connect(loop.quit)
            job.start()
            loop.exec()
            job.wait()
            if errors:
                raise RuntimeError(errors)
        elapsed = time.perf_counter() - started
    finally:
        stop.set()
        monitor.join()
        heartbeat.stop()
    reopened, stored = load_project(target)
    assert configuration_hash(reopened) == configuration_hash(project)
    assert np.array_equal(stored.fields["m"]["temperature"], result.fields["m"]["temperature"])
    receipt = dict(synthetic_storage_only=True, mode="synchronous" if args.synchronous else "worker",
        nodes=len(result.mesh.points), elements=len(result.mesh.triangles), history_count=args.history,
        array_bytes=result.mesh.points.nbytes + result.mesh.triangles.nbytes + result.mesh.regions["layer:m"].nbytes
                    + sum(array.nbytes for array in result.fields["m"].values()),
        elapsed_seconds=elapsed, initial_freeze_seconds=freeze_seconds, snapshot_seconds=snapshot_seconds,
        peak_rss_bytes=peak[0], rss_increase_bytes=peak[0] - rss_start, archive_bytes=target.stat().st_size,
        heartbeat_ticks=len(ticks), max_heartbeat_gap_seconds=max(np.diff(ticks), default=0),
        python=platform.python_version(), platform=platform.platform(), cpu=platform.processor(),
        logical_cpus=psutil.cpu_count(), physical_cpus=psutil.cpu_count(logical=False),
        ram_bytes=psutil.virtual_memory().total)
    (args.output / "receipt.json").write_text(json.dumps(receipt, indent=2), encoding="utf-8")
    print(json.dumps(receipt, indent=2))
    # Keep the QCoreApplication alive through worker and signal teardown.
    del app
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
