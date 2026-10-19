# SPDX-License-Identifier: MPL-2.0
"""Opt-in thermal-stack sensitivity and configured E37/E39 comparisons.

No simulation runs on import. Outputs are reproducible engineering evidence,
not a calibrated device rating or a 3D validation. See docs/thermal-stacks.md.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import platform
import time
from copy import deepcopy
from pathlib import Path

import psutil

from etlayout.examples import make_example
from etlayout.geometry.prepare import geometry_region, prepare_geometry
from etlayout.io.project import load_project, physics_hash, save_project
from etlayout.materials.database import reference_materials
from etlayout.models import (
    HeatSource,
    Interface,
    MeasurementSpec,
    PhysicalLayer,
    Project,
    RegionSpec,
    SupportSelector,
    ThermalBoundary,
    TransientSettings,
)
from etlayout.simulation.runner import run_project


def rect(bounds):
    return RegionSpec(polygons=[{"bounds": bounds}])


def coupon(family, variant="explicit", mesh_size=10e-6):
    """100 x 100 µm prescribed-heating coupon; fixed power, not a transistor.

    Source and passive receiver centers are 40 µm apart. The source is a
    20 x 20 µm footprint on a GaN or SiC sheet. The 1 µm SiN film lies between
    that sheet and an electrically isolated 5 µm Cu spreading cap. Both models
    retain the same cap and substrate; only the film's lateral conduction and
    heat capacity are omitted in the effective-gap representation.
    """
    if family not in {"gan", "sic"}:
        raise ValueError("Choose gan or sic")
    materials = reference_materials()
    thickness = 2e-6 if family == "gan" else 20e-6
    film_t = 2e-6 if variant == "thick_film" else 1e-6
    materials["sin"].thermal_conductivity.value = {"low_k": 1., "high_k": 10.}.get(variant, 3.)
    interface_r = {"low_tbr": 0., "high_tbr": 2e-7}.get(variant, 2e-8)
    domain = rect([0., 0., 100e-6, 100e-6])
    layers = [PhysicalLayer("substrate", "SiC substrate", "sic", 100e-6, -100e-6, "thermal", deepcopy(domain)),
              PhysicalLayer("active", family.upper() + " thermal sheet", family, thickness, 0., "thermal", deepcopy(domain))]
    interfaces = [Interface("active_substrate", "active", "substrate", deepcopy(domain), thermal_kind="stack")]
    if variant != "no_cap":
        layers.append(PhysicalLayer("cap", "Insulated Cu spreader", "copper", 5e-6,
                                    thickness + film_t, "thermal", deepcopy(domain)))
        if variant == "effective":
            interfaces.append(Interface("film_gap", "active", "cap", deepcopy(domain), thermal_kind="stack",
                                        material_id="sin", height=film_t, thermal_resistance_area=2 * interface_r))
        else:
            layers.append(PhysicalLayer("passivation", "SiN passivation", "sin", film_t, thickness,
                                        "insulator", deepcopy(domain)))
            interfaces.extend(Interface(key, a, b, deepcopy(domain), thermal_kind="stack",
                                        thermal_resistance_area=interface_r)
                              for key, a, b in (("active_film", "active", "passivation"), ("film_cap", "passivation", "cap")))
    project = Project(name=family.upper() + " thermal-stack coupon: " + variant, materials=materials,
                       layers=layers, interfaces=interfaces)
    project.solver.mode = "thermal"
    project.mesh.max_size = mesh_size
    project.mesh.min_size = mesh_size / 4
    project.mesh.contact_size = mesh_size / 2
    project.mesh.corner_refinement = False
    project.heat_sources = [HeatSource("fixed_power", "active", rect([20e-6, 40e-6, 40e-6, 60e-6]),
                                      "total", .02 if family == "gan" else .05)]
    # Backside boundary includes the substrate's external half-thickness plus
    # a declared synthetic package/attachment area resistance of 2e-6 m² K/W.
    project.thermal_boundaries = [ThermalBoundary("backside", "substrate", deepcopy(domain), "resistance_area",
                                      50e-6 / 370. + 2e-6, 300.)]
    project.power.measurements = [MeasurementSpec(identifier, "temperature",
        SupportSelector(layers=["active"], geometry=rect(bounds)), "mean")
        for identifier, bounds in (("hotspot_mean_20um", [20e-6, 40e-6, 40e-6, 60e-6]),
                                   ("receiver_mean_20um", [60e-6, 40e-6, 80e-6, 60e-6]))]
    project.notes = "Synthetic thermal sensitivity coupon. Prescribed heat only; no transistor transport or certified calibration."
    return project


def explicit_gap(project, identifier):
    """Replace one existing omitted gap by the same material as an explicit sheet.

    The prepared footprint excludes other intervening structures/openings. The
    original effective bridge is removed, preventing a parallel duplicate path.
    Each new link includes half of the film, so the old total gap resistance is
    represented exactly once in the uniform-flux limit.
    """
    project = deepcopy(project)
    geometry = prepare_geometry(project)
    original = next(item for item in project.interfaces if item.id == identifier)
    if original.electrical_kind != "none" or original.thermal_kind != "stack" or not original.material_id or original.height <= 0:
        raise ValueError("Choose a stack interface with a positive material-filled gap")
    a, b = sorted((project.layer(original.layer_a), project.layer(original.layer_b)), key=lambda item: item.z)
    spec = geometry_region(geometry.interfaces[identifier])
    film_id = "explicit_" + identifier
    project.layers.append(PhysicalLayer(film_id, "Explicit gap dielectric", original.material_id,
        original.height, a.z + a.thickness, "insulator", spec))
    project.interfaces.remove(original)
    for endpoint, name in ((a, "lower"), (b, "upper")):
        project.interfaces.append(Interface(identifier + "_" + name, endpoint.id, film_id, deepcopy(spec),
            thermal_kind="stack", thermal_resistance_area=original.thermal_resistance_area / 2.))
    return project


def measure(project, output, family, variant, transient=False, time_step=10e-6):
    if transient:
        project.transient = TransientSettings(enabled=True, stop_time=100e-6, time_step=time_step, output_interval=time_step)
    started = time.perf_counter()
    result = run_project(project)
    elapsed = time.perf_counter() - started
    if not result.converged:
        raise RuntimeError(f"{family}/{variant}: numerical solve failed: {result.diagnostics}")
    measurements = result.analysis.get("measurements", {})
    receiver = measurements.get("receiver_mean_20um", {}).get("value")
    hotspot_mean = measurements.get("hotspot_mean_20um", {}).get("value")
    artifact = output / (family + "_" + variant + (f"_transient_dt{time_step:.9g}" if transient else "") + ".etl")
    save_started = time.perf_counter()
    save_project(project, artifact, result)
    save_seconds = time.perf_counter() - save_started
    open_started = time.perf_counter()
    reopened, restored = load_project(artifact)
    open_seconds = time.perf_counter() - open_started
    assert restored.converged and physics_hash(reopened) == physics_hash(project)
    row = dict(family=family, variant=variant, transient=transient, time_step_s=time_step if transient else None,
        mesh_sha256=hashlib.sha256(result.mesh.points.tobytes() + result.mesh.triangles.tobytes()).hexdigest(),
        transient_peak_temperature_K=result.metrics.get("transient_peak_temperature"),
        transient_peak_time_s=result.metrics.get("transient_peak_time"), temperature_max_K=result.metrics["temperature_max"],
        rise_K=result.metrics["temperature_max"]-300., thermal_power_W=result.metrics["thermal_power"],
        heat_out_W=result.metrics["heat_out"], thermal_balance_error=result.metrics["thermal_balance_error"],
        thermal_resistance_K_per_W=result.metrics["thermal_resistance"], receiver_mean_20um_K=receiver,
        hotspot_mean_20um_K=hotspot_mean,
        hotspot=result.metrics["hotspot"], elements=len(result.mesh.triangles), thermal_dofs=result.metrics["thermal_dofs"],
        wall_seconds=elapsed, save_seconds=save_seconds, reopen_seconds=open_seconds, archive_bytes=artifact.stat().st_size,
        process_peak_working_set_bytes=psutil.Process().memory_info().peak_wset, execution=result.execution,
        final_stored_energy_J=result.metrics.get("thermal_stored_energy"),
        transient_energy={key: value for key, value in result.metrics.items() if "energy" in key} if result.transient else None,
        mesh_verification="unverified; run --mesh-size with three levels", external_3d_reference="not performed")
    print(f"{family}/{variant} transient={transient}: {row['temperature_max_K']:.8g} K, {len(result.mesh.triangles)} elements, {elapsed:.3f} s", flush=True)
    return row


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True, help="Evidence directory outside the source tree")
    parser.add_argument("--mesh-size", type=float, default=10e-6, help="Maximum size, SI meters")
    parser.add_argument("--time-step", "--time-steps", dest="time_steps", nargs="+", type=float, default=[10e-6],
                        help="Transient backward-Euler steps, seconds; each case reuses the identical cached mesh")
    parser.add_argument("--variants", nargs="+", choices=("effective", "explicit", "low_k", "high_k", "low_tbr", "high_tbr", "thick_film", "no_cap"),
                        default=("effective", "explicit", "low_k", "high_k", "low_tbr", "high_tbr", "thick_film", "no_cap"))
    parser.add_argument("--transient", action="store_true", help="Also calculate all coupon 100 µs step responses")
    parser.add_argument("--device-examples", action="store_true", help="Also solve unchanged configured E37/E39 and explicit-gap versions")
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    rows = []
    for family in ("gan", "sic"):
        for variant in args.variants:
            rows.append(measure(coupon(family, variant, args.mesh_size), args.output, family, variant))
            if args.transient:
                for time_step in args.time_steps:
                    rows.append(measure(coupon(family, variant, args.mesh_size), args.output, family, variant,
                                        transient=True, time_step=time_step))
    if args.device_examples:
        for identifier, gap in (("E37", "heat_drain_a_buffer"), ("E39", "heat_gate_substrate")):
            project = make_example(identifier, args.output / identifier)
            rows.append(measure(project, args.output, identifier, "configured"))
            rows.append(measure(explicit_gap(project, gap), args.output, identifier, "explicit_gap"))
    evidence = dict(platform=platform.platform(), python=platform.python_version(), cpu=platform.processor(),
                    logical_cpus=psutil.cpu_count(), mesh_size_m=args.mesh_size, time_steps_s=args.time_steps, rows=rows)
    (args.output / "thermal-stacks.json").write_text(json.dumps(evidence, indent=2, allow_nan=False) + "\n", encoding="utf-8")
    fields = [key for key in rows[0] if key not in {"hotspot", "execution", "transient_energy"}]
    with (args.output / "thermal-stacks.csv").open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


if __name__ == "__main__":
    main()
