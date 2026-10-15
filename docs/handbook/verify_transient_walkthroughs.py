"""Recompute E17–E19, the uniform thermal RC limit and multilayer pulse delay.

Examples are generated from source into the output directory. No checked-in
project or cached numerical result is modified or reused.
"""
from __future__ import annotations

import argparse
import json
from copy import deepcopy
from pathlib import Path

import numpy as np

from etlayout import __version__
from etlayout.examples import make_example
from etlayout.io.project import load_project, save_project
from etlayout.simulation.runner import run_project


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", default=".artifacts/handbook/transient/walkthroughs.json")
    args = parser.parse_args()
    target = Path(args.output)
    target.parent.mkdir(parents=True, exist_ok=True)
    rows = []
    for name in ("E17_pulsed_thermal", "E18_pulsed_conductor", "E19_pulsed_multilayer"):
        base = make_example(name, target.parent / "inputs")
        for refinement in (1, 2):
            project = deepcopy(base)
            project.transient.time_step /= refinement
            result = run_project(project)
            if not result.converged or not result.transient.completed:
                raise AssertionError(f"{name} did not complete")
            archive = target.parent / f"{name}-dt{refinement}.etl"
            save_project(project, archive, result)
            _, reopened = load_project(archive)
            if reopened is None or reopened.transient is None:
                raise AssertionError("Archive did not retain the transient history")
            if not np.array_equal(reopened.transient.times, result.transient.times):
                raise AssertionError("Reopened snapshot times differ")
            row = {"example": name, "maximum_time_step_s": project.transient.time_step,
                   "output_interval_s": project.transient.output_interval,
                   "configuration_hash": result.configuration_hash, "physics_hash": result.physics_hash,
                   "converged": result.converged, "nodes": len(result.mesh.points),
                   "triangles": len(result.mesh.triangles), "reopened": True,
                   "metrics": result.metrics, "saved_times_s": result.transient.times.tolist()}
            if name.startswith("E17"):
                layer = project.layer("plate")
                material = project.materials[layer.material_id]
                capacity_per_area = material.density * material.specific_heat * layer.thickness
                coefficient = project.thermal_boundaries[0].value
                ambient = project.thermal_boundaries[0].ambient
                tau = capacity_per_area / coefficient
                temperature = project.solver.initial_temperature
                errors = []
                exact = []
                pulse = project.transient.drives[0].waveform
                for step in result.transient.rows:
                    # Ideal pulse events split intervals, so a source value is
                    # constant throughout each interval. This exponential is
                    # the independent continuous-time RC solution there.
                    decay = np.exp(-step["time_step"] / tau)
                    midpoint = step["time_s"] - .5 * step["time_step"]
                    phase = (midpoint - pulse.delay) % pulse.period
                    source = pulse.high if midpoint >= pulse.delay and phase < pulse.width else pulse.low
                    if not np.isclose(step["drives"]["heat_sources.heater.value"], source):
                        raise AssertionError("E17 applied pulse disagrees with its independent interval reference")
                    temperature = ambient + (temperature - ambient) * decay + source / coefficient * (1 - decay)
                    exact.append(float(temperature))
                    errors.append(abs(step["temperature_max"] - temperature))
                row["analytical_rc"] = {"area_capacity_J_m2K": capacity_per_area,
                    "time_constant_s": tau, "high_level_steady_rise_K": 1e6 / coefficient,
                    "maximum_temperature_error_K": float(max(errors)),
                    "peak_temperature_at_accepted_times_K": max(exact),
                    "final_temperature_K": exact[-1]}
                if refinement == 2 and not max(errors) < rows[-1]["analytical_rc"]["maximum_temperature_error_K"]:
                    raise AssertionError("E17 time refinement did not reduce RC error")
            if name.startswith("E19"):
                peaks = {}
                for layer_id in ("heater", "substrate", "receiver"):
                    temperatures = [float(np.nanmax(frame.fields[layer_id]["temperature"]))
                                    for frame in result.transient.snapshots]
                    index = int(np.argmax(temperatures))
                    peaks[layer_id] = {"temperature_K": temperatures[index],
                                       "saved_peak_time_s": float(result.transient.times[index])}
                row["layer_peaks"] = peaks
                if not (peaks["receiver"]["saved_peak_time_s"] > peaks["substrate"]["saved_peak_time_s"]
                        > peaks["heater"]["saved_peak_time_s"]):
                    raise AssertionError("E19 does not show delayed thermal crosstalk")
                if not np.isclose(result.metrics["transient_input_energy"], 1e-6, rtol=1e-10, atol=1e-15):
                    raise AssertionError("E19 heat pulse must inject exactly 1 µJ")
            rows.append(row)
            print(f"Verified {name}, dt={project.transient.time_step:g} s, "
                  f"peak={result.metrics['transient_peak_temperature']:.8g} K", flush=True)
    target.write_text(json.dumps({"solver_version": __version__,
        "purpose": "Pulse examples with time-step comparison; E17 analytical RC and E19 delayed multilayer response",
        "cases": rows}, indent=2, allow_nan=False) + "\n", encoding="utf-8")
    print(target)


if __name__ == "__main__":
    main()
