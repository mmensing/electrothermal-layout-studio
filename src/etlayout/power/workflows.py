# SPDX-License-Identifier: MPL-2.0
"""Qt-free orchestration shared by desktop workers and the command line."""
from __future__ import annotations

import json
from pathlib import Path

from etlayout.models import SimulationError


def _write_json(path, report):
    from etlayout.io.project import _json_dump
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(_json_dump(report), encoding="utf-8")


def execute_operation(project, operation, options=None, *, progress=None, cancel=None, execution=None):
    """Return a report plus optional real result/geometry for desktop display."""
    options = dict(options or {})
    progress = progress or (lambda *_: None)
    if operation in {"power", "reanalyze"}:
        from etlayout.geometry.prepare import prepare_geometry
        from etlayout.io.export import export_results
        from etlayout.power.analysis import analyze_power, export_power_report
        from etlayout.simulation.runner import run_project
        if operation == "reanalyze":
            from copy import deepcopy

            from etlayout.models import checkpoint
            from etlayout.power.analysis import _validate_result_context
            source = options.get("result")
            if source is None:
                raise SimulationError("Select a compatible calculated run before reanalysis")
            _validate_result_context(project, source)
            checkpoint(cancel, progress, 0.1, "Reanalyzing retained fields; no new field solve")
            result = deepcopy(source)
        else:
            result = run_project(project, progress=progress, cancel=cancel, execution=execution)
        report = analyze_power(project, result)
        if operation == "reanalyze":
            from etlayout.io.project import configuration_hash
            report["reporting_context"] = {"field_configuration_hash": source.configuration_hash,
                                           "reporting_configuration_hash": configuration_hash(project),
                                           "field_solve_reused": True}
        result.analysis = report
        if options.get("output"):
            destination = Path(options["output"])
            export_results(result, destination, plots=options.get("plots", False))
            export_power_report(result, destination)
        return {"result": result, "report": report, "geometry": prepare_geometry(project),
                "exit_code": 0 if result.converged else 2}
    if operation == "multiport":
        from etlayout.power.multiport import export_multiport, extract_multiport
        from etlayout.simulation.runner import prepare_mesh
        _, mesh = prepare_mesh(project, progress, cancel=cancel)
        report = extract_multiport(project, mesh, port_ids=options.get("port_ids"),
            reference=options.get("reference"), cancel=cancel)
        if options.get("output"):
            export_multiport(report, options["output"], format=options.get("format", "json"))
        return {"report": report}
    if operation.startswith("routing_"):
        from etlayout.geometry.prepare import prepare_geometry
        from etlayout.routing import export_candidate, generate_candidate, validate_rules
        if operation == "routing_validate":
            geometry = prepare_geometry(project)
            issues = validate_rules(project, geometry)
            report = {"valid": not any(item["severity"] == "error" for item in issues), "violations": issues}
            if options.get("output"):
                _write_json(Path(options["output"]) / "routing-validation.json", report)
            return {"report": report, "geometry": geometry, "project": project,
                    "exit_code": 0 if report["valid"] else 2}
        if not options.get("output"):
            raise SimulationError("Routing generation requires a new GDS/OASIS output path")
        original = prepare_geometry(project)
        generated = (export_candidate(project, options["output"], options.get("parameters") or None, cancel=cancel)
                     if operation == "routing_export" else
                     generate_candidate(project, options.get("parameters", {}), options["output"], cancel=cancel))
        report = {"valid": generated.valid, "violations": generated.violations, "provenance": generated.provenance}
        geometry = prepare_geometry(generated.project) if generated.valid else None
        if geometry is not None:
            changes = {}
            for layer, before in original.layers.items():
                after = geometry.layers[layer]
                changes[layer] = {"added_area_m2": after.difference(before).area,
                    "removed_area_m2": before.difference(after).area,
                    "added_wkt": after.difference(before).wkt, "removed_wkt": before.difference(after).wkt}
            report["geometry_changes"] = changes
        if generated.valid:
            from etlayout.io.project import save_project
            save_project(generated.project, Path(options["output"]).with_suffix(".etl"))
        _write_json(str(options["output"]) + ".report.json", report)
        return {"report": report, "project": generated.project, "geometry": geometry,
                "exit_code": 0 if generated.valid else 2}
    if operation == "optimize":
        from etlayout.optimization import run_experiment
        report = run_experiment(project, options["experiment_id"], options["output"],
            progress=progress, cancel=cancel, resume=options.get("resume", True), execution=execution)
        return {"report": report}
    if operation == "export_finalist":
        from etlayout.optimization import export_finalist
        history = options.get("history")
        if history is None:
            path = Path(options["output"])
            history = json.loads((path if path.is_file() else path / "history.json").read_text(encoding="utf-8"))
        destination = options.get("export_directory") or str(Path(options["output"]) / "export")
        report = export_finalist(project, history, options["candidate_id"], destination, cancel=cancel)
        return {"report": report}
    raise SimulationError(f"Unknown power workflow {operation}")
