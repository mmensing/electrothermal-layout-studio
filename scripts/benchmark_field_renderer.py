# SPDX-License-Identifier: MPL-2.0
"""Measure field-picture construction and cache reuse on a synthetic grid.

This analytic performance fixture is not a solver result or a scientific
validation case. A square grid with ``cells`` cells along each edge contains
``2 * cells**2`` triangles, spaced 1 micrometre apart. Its prescribed potential
is V = 1e-6 V + x * 1 V/m + y * 2 V/m, with E = (-1, -2) V/m.

Each display state is requested three times: a cold picture recording followed
by two cached requests. Timings cover ``LayoutView.show_field`` only, including
support lookup, picture recording/reuse and optional vector overlays. They do
not measure solver runtime, screen repaint latency, or GPU frame rate. Reported
cache sizes come from the renderer's accounting, not resident process memory.

Run from an environment with the project installed, for example:
    python scripts/benchmark_field_renderer.py --cells 100 --output benchmark.json
"""
from __future__ import annotations

import argparse
import json
import os
import platform
import time
from pathlib import Path

import numpy as np

from etlayout.models import PlanarMesh, SimulationResult


def positive_integer(value: str) -> int:
    number = int(value)
    if number < 1:
        raise argparse.ArgumentTypeError("cells must be a positive integer")
    return number


def make_fixture(cells: int) -> SimulationResult:
    x, y = np.meshgrid(np.arange(cells + 1), np.arange(cells + 1))
    points = np.column_stack([x.ravel(), y.ravel()]) * 1e-6
    first = (y[:-1, :-1] * (cells + 1) + x[:-1, :-1]).ravel()
    triangles = np.concatenate([
        np.column_stack([first, first + 1, first + cells + 1]),
        np.column_stack([first + 1, first + cells + 2, first + cells + 1]),
    ])
    mesh = PlanarMesh(points, triangles, {"layer:synthetic": np.arange(len(triangles))})
    return SimulationResult(mesh, fields={"synthetic": {
        "potential": points[:, 0] + 2 * points[:, 1] + 1e-6,
        "electric_field": np.tile([-1., -2.], (len(triangles), 1)),
    }})


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--cells", type=positive_integer, default=100,
                        help="grid cells along each edge (default: 100, yielding 20,000 triangles)")
    parser.add_argument("--output", type=Path, help="optional JSON receipt path; JSON is always printed")
    args = parser.parse_args(argv)
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtCore import qVersion
    from PySide6.QtWidgets import QApplication

    from etlayout.gui.viewer import LayoutView

    app = QApplication.instance() or QApplication([])
    result = make_fixture(args.cells)
    view = LayoutView()
    measurements = []
    try:
        for field, smooth, vectors in [("potential", False, False), ("potential", True, False),
                                       ("electric_field", False, True)]:
            elapsed = []
            for _repeat in range(3):
                started = time.perf_counter()
                view.show_field(result, "synthetic", field, smooth=smooth, colormap="viridis", vectors=vectors)
                elapsed.append(time.perf_counter() - started)
                if view.overlay_item is None:
                    raise RuntimeError(f"No field picture was recorded for {field}")
            measurements.append({
                "field": field, "smooth": smooth, "vectors": vectors,
                "seconds": elapsed, "cold_seconds": elapsed[0], "cached_seconds": elapsed[1:],
                "picture_bytes": view.overlay_item.picture.size(),
                "cache_entries": len(view._field_cache), "cache_bytes": view._field_cache_bytes,
                "cache_budget_bytes": view._field_cache_budget,
            })
        receipt = {
            "fixture": "Synthetic analytic grid; not a solved physical example",
            "cells_per_edge": args.cells, "nodes": len(result.mesh.points),
            "triangles": len(result.mesh.triangles), "colormap": "viridis",
            "timing_scope": "show_field support lookup, picture recording/cache reuse, and optional vector overlay",
            "environment": {"python": platform.python_version(), "platform": platform.platform(),
                            "qt": qVersion(), "qt_platform": app.platformName(), "numpy": np.__version__},
            "measurements": measurements,
        }
        encoded = json.dumps(receipt, indent=2) + "\n"
        if args.output:
            args.output.parent.mkdir(parents=True, exist_ok=True)
            args.output.write_text(encoded, encoding="utf-8")
        print(encoded, end="")
    finally:
        view.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
