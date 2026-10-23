# SPDX-License-Identifier: MPL-2.0
"""Shared, bounded field-extraction jobs for the desktop and CLI."""
from __future__ import annotations

import json
import math
from pathlib import Path

from etlayout.models import SimulationError, checkpoint
from etlayout.power.field_snapshot import build_field_snapshot, triangulate_conductors


def _empty_directory(directory):
    destination = Path(directory).expanduser().absolute()
    if destination.is_symlink() or destination.is_junction() or (destination.exists() and (
            not destination.is_dir() or any(destination.iterdir()))):
        raise SimulationError("Choose a new or empty field-output directory; existing inputs are never executed")
    return destination


def run_field_operation(project, options, *, progress=None, cancel=None, execution=None):
    """Freeze actual geometry once; generate only our own decks before execution."""
    from etlayout.io.project import _json_dump
    kind = options.get("kind", "capacitance")
    snapshot = build_field_snapshot(project, kind=kind, temperature_K=options.get("temperature_K"),
                                    cancel=cancel, progress=progress)
    destination = _empty_directory(options.get("output") or "parasitic-results")
    executable = options.get("executable")
    prepare_only = bool(options.get("prepare_only", False))
    if not prepare_only and not executable:
        raise SimulationError("Choose an explicit trusted external field executable, or prepare inputs only")
    tolerance = options.get("relative_tolerance", .02)
    timeout = options.get("timeout", 180.)
    if isinstance(timeout, bool) or not isinstance(timeout, (int, float)) or not math.isfinite(timeout) or not 0 < timeout <= 1800:
        raise SimulationError("Field engine time budget must be between zero and 1800 seconds")
    if isinstance(tolerance, bool) or not isinstance(tolerance, (int, float)) or not math.isfinite(tolerance) or not 0 < tolerance <= .1:
        raise SimulationError("Field mesh sensitivity limit must be greater than zero and at most 0.1")
    if kind == "capacitance":
        from etlayout.power.capacitance import compile_capacitance_input, run_capacitance_mesh_study
        sizes = options.get("mesh_sizes_m", (100e-6, 50e-6, 25e-6))
        if not isinstance(sizes, (list, tuple)) or not 3 <= len(sizes) <= 5 or any(
                isinstance(value, bool) or not isinstance(value, (float, int)) or not math.isfinite(value) or value <= 0 for value in sizes) or any(b >= a for a,b in zip(sizes, sizes[1:])):
            raise SimulationError("Choose three to five strictly decreasing positive panel sizes in meters")
        if prepare_only:
            mesh = triangulate_conductors(snapshot, sizes[0], max_panels=min(options.get("max_panels", 20000), 2000) if options.get("direct") else options.get("max_panels", 20000),
                                          cancel=cancel, progress=progress)
            data, manifest = compile_capacitance_input(snapshot, mesh)
            report = {"status": "prepared", "kind": kind, "accepted": False, "mesh_verified": False,
                      "input_manifest": manifest, "field_model_hash": snapshot.field_model_hash,
                      "geometry_sha256": snapshot.geometry_sha256,
                      "diagnostic": "Inputs only. No capacitance calculation or verification has been performed."}
            destination.mkdir(parents=True, exist_ok=True)
            (destination / "geometry.qui").write_bytes(data)
            (destination / "input-manifest.json").write_text(_json_dump(manifest), encoding="utf-8")
        else:
            report = run_capacitance_mesh_study(snapshot, executable, destination, sizes,
                max_panels=options.get("max_panels", 20000), relative_tolerance=tolerance,
                timeout=timeout / len(sizes), native_threads=1, direct=options.get("direct", False),
                cancel=cancel, progress=progress)
    else:
        from etlayout.power.inductance import extract_inductance, prepare_inductance
        keywords = {"frequencies_Hz": options.get("frequencies_Hz", (0., 1., 10.)),
                    "refinements": options.get("refinements", ((1, 1), (2, 3), (4, 5))),
                    "relative_tolerance": tolerance, "max_filaments": options.get("max_filaments", 1024),
                    "cancel": cancel}
        if prepare_only:
            report = prepare_inductance(snapshot, destination, **keywords)
        else:
            report = extract_inductance(snapshot, destination, executable=executable,
                timeout=timeout, progress=progress, execution=execution, **keywords)
    checkpoint(cancel)
    if not (destination / "snapshot.json").exists():
        (destination / "snapshot.json").write_text(_json_dump(snapshot.to_dict()), encoding="utf-8")
    (destination / "field-report.json").write_text(_json_dump(report), encoding="utf-8")
    # Force the exact same portable contract in process workers and the CLI.
    report = json.loads(_json_dump(report))
    return {"report": report, "field_snapshot": snapshot.to_dict(),
            "exit_code": 0 if report.get("status") in {"prepared", "verified"} else 2}
