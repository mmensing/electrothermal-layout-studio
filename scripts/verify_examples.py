# SPDX-License-Identifier: MPL-2.0
"""Reproduce introductory designs through geometry/mesh/physics/persistence.

Power engineering references use benchmark_power_examples.py with exact inputs,
resource monitoring and physical acceptance, not this three-mode teaching loop.
"""
from __future__ import annotations

import argparse
import time
from copy import deepcopy
from pathlib import Path

from etlayout.examples import ROUTINE_EXAMPLES, make_example
from etlayout.io.project import _json_dump, load_project, physics_hash, save_project
from etlayout.models import HeatSource, RegionSpec
from etlayout.simulation.runner import run_project


def verify(directory, names=None):
    root = Path(directory)
    root.mkdir(parents=True, exist_ok=True)
    rows = []
    for requested_name in names or ROUTINE_EXAMPLES:
        from etlayout.example_catalog import example_info
        if example_info(requested_name).family:
            raise ValueError("Power families require scripts/benchmark_power_examples.py; do not add prescribed heat or substitute their nominal drive")
        original = make_example(requested_name, root / "inputs")
        name = original.name
        for mode in ("electrical", "thermal", "electrothermal"):
            p = deepcopy(original)
            p.solver.mode = mode
            if mode == "thermal" and not p.heat_sources:
                p.heat_sources = [HeatSource("validation_heat", p.layers[0].id, RegionSpec(full_domain=True), "total", .01)]
            started = time.perf_counter()
            result = run_project(p)
            row = {"example": name, "mode": mode, "converged": result.converged,
                   "elapsed_s": time.perf_counter()-started, "nodes": len(result.mesh.points),
                   "triangles": len(result.mesh.triangles), "metrics": result.metrics,
                   "configuration_hash": result.configuration_hash,
                   "physics_hash": physics_hash(p),
                   "source_sha256": p.layout.sha256,
                   "thermal_sources": [{"id": source.id, "layer": source.layer_id,
                                        "kind": source.kind, "value": source.value} for source in p.heat_sources]}
            assert result.converged, row
            for metric, tolerance in (("current_balance_error",.001),("power_balance_error",.005),("thermal_balance_error",.01)):
                if metric in result.metrics:
                    assert result.metrics[metric] < tolerance, (name,mode,metric,result.metrics[metric])
            archive = root / (name+"_"+mode+".etl")
            save_project(p, archive, result)
            loaded, saved = load_project(archive)
            assert saved.configuration_hash == result.configuration_hash
            assert loaded.solver.mode == mode
            assert saved.physics_hash == physics_hash(loaded)
            row["reopened"] = True
            rows.append(row)
            print(f"{name} {mode}: converged, {row['triangles']:,} triangles, {row['elapsed_s']:.3f} s", flush=True)
    (root / "verification.json").write_text(_json_dump(rows), encoding="utf-8")
    return rows


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", default="artifacts/example-verification")
    parser.add_argument("--examples", nargs="+", help="Introductory IDs; default verifies E1–E19. Power families use the explicit benchmark driver")
    args = parser.parse_args()
    verify(args.output, args.examples)
