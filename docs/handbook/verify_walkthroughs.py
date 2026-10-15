"""Recompute the handbook's examples from the checked-in portable projects.

Run from the repository root with the installed etlayout environment. This
records actual solver output, not hard-coded reference values or cached fields.
"""
from __future__ import annotations

import argparse
import json
import platform
from pathlib import Path

import numpy as np

from etlayout import __version__
from etlayout.io.project import load_project
from etlayout.materials.models import evaluate_property
from etlayout.simulation.runner import run_project
from etlayout.visualization.sampling import FieldSampler


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", default=".artifacts/handbook/walkthroughs.json")
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[2]
    cases = [("E1_straight", "electrical"), ("E10_thermal_stack", "thermal"),
             ("E10_thermal_stack", "electrothermal"),
             ("E12_via_ladder", "electrical"), ("E12_via_ladder", "electrothermal")]
    rows = []
    for example, mode in cases:
        project, _ = load_project(root / "examples" / (example + ".etl"))
        project.solver.mode = mode
        result = run_project(project)
        if not result.converged:
            raise RuntimeError(f"{example} ({mode}) did not converge")
        row = {"example": example, "mode": mode, "converged": result.converged,
               "configuration_hash": result.configuration_hash, "physics_hash": result.physics_hash,
               "nodes": len(result.mesh.points), "triangles": len(result.mesh.triangles),
               "metrics": result.metrics, "terminals": result.terminals,
               "diagnostics": [vars(d) for d in result.diagnostics]}
        if example == "E1_straight":
            layer = project.layer("metal")
            rho = float(evaluate_property(project.materials[layer.material_id].resistivity, 300.))
            exact_r = rho * 100e-6 / (10e-6 * layer.thickness)
            exact_i = .02 / exact_r
            row["analytic"] = {"resistivity_ohm_m": rho, "resistance_ohm": exact_r,
                               "current_A": exact_i, "power_W": .02 * exact_i}
            if not np.isclose(result.metrics["resistance"], exact_r, rtol=1e-9):
                raise AssertionError("E1 resistance disagrees with the uniform-strip reference")
        if example == "E10_thermal_stack" and mode == "thermal":
            # E10 has no independent heat source and this case does not request
            # one-way Joule heat: the correct result is the 300 K equilibrium.
            if not np.isclose(result.metrics["temperature_max"], 300., atol=1e-8):
                raise AssertionError("Unloaded E10 thermal mode should remain at 300 K")
        if example == "E10_thermal_stack" and mode == "electrothermal":
            points = np.array([[100e-6, 50e-6]])
            sampler = FieldSampler(result)
            row["same_xy_temperature_K"] = {
                "point_m": points[0].tolist(),
                "layers": {layer: float(sampler.sample(layer, "temperature", points)[0])
                           for layer in ("metal", "oxide", "substrate")}}
        if example == "E12_via_ladder":
            for terminal in ("source", "drain"):
                if not np.isclose(abs(result.terminals[terminal]["current"]), .3, rtol=1e-8):
                    raise AssertionError("E12 terminal current does not match the imposed 0.3 A")
            if mode == "electrothermal":
                source_power = sum(source.value for source in project.heat_sources if source.kind == "total")
                if not np.isclose(result.metrics["thermal_power"],
                                  result.metrics["electrical_power"] + source_power, rtol=1e-8):
                    raise AssertionError("E12 thermal power omits an independent or electrical source")
        rows.append(row)
        print(f"Verified {example} ({mode})", flush=True)
    receipt = {"purpose": "Recomputed handbook examples; no experimental calibration implied",
               "solver_version": __version__, "python": platform.python_version(),
               "platform": platform.system(), "cases": rows}
    target = Path(args.output)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(receipt, indent=2, allow_nan=False) + "\n", encoding="utf-8")
    print(target)


if __name__ == "__main__":
    main()
