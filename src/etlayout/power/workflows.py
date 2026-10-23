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
    if operation == "parasitic":
        from etlayout.power.field_workflows import run_field_operation
        return run_field_operation(project, options, progress=progress, cancel=cancel, execution=execution)
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
    if operation in {"multiport", "multiport_mesh", "multiport_validate", "multiport_bundle"}:
        from etlayout.power.multiport import export_multiport, extract_multiport
        from etlayout.power.multiport_context import temperature_inputs
        from etlayout.simulation.runner import prepare_mesh
        # All options are job-local. They never change project temperature or drives.
        temperature, context = temperature_inputs(project, mode=options.get("temperature_mode", "initial"),
            temperature_K=options.get("temperature_K"), result=options.get("result"))
        if operation == "multiport_mesh":
            from etlayout.power.multiport_study import run_multiport_mesh_study
            report = run_multiport_mesh_study(project, mesh_sizes=options.get("mesh_sizes"),
                port_ids=options.get("port_ids"), reference=options.get("reference"), temperatures=temperature,
                relative_tolerance=options.get("relative_tolerance", .02), progress=progress, cancel=cancel)
            report["extraction_context"] = context
            if options.get("output"):
                _write_json(options["output"], report)
            from etlayout.geometry.prepare import prepare_geometry
            return {"report": report, "geometry": prepare_geometry(project) if report.get("network") else None,
                    "exit_code": 0 if report.get("mesh_verified") else 2}
        if operation in {"multiport_validate", "multiport_bundle"}:
            from etlayout.io.project import physics_hash
            report = options.get("network")
            if report is None or report.get("source_physics_hash", report.get("physics_hash")) != physics_hash(project):
                raise SimulationError("Extract a compatible network before circuit validation")
            if report.get("extraction_context") != context:
                raise SimulationError("Temperature context changed; extract this network again before validation")
        else:
            retained = options.get("result") if context["mode"] == "retained" else None
            geometry, mesh = prepare_mesh(project, progress, cancel=cancel,
                initial_state_supplied=retained is not None, initial_result=retained)
            report = extract_multiport(project, mesh, port_ids=options.get("port_ids"),
                reference=options.get("reference"), temperatures=temperature, cancel=cancel)
            report["extraction_context"] = context
        if operation in {"multiport_validate", "multiport_bundle"} or options.get("validation_directory"):
            from etlayout.power.circuit_validation import prepare_circuit_validation, validate_circuit_network
            directory = options.get("validation_directory") or options.get("output")
            if not directory:
                raise SimulationError("Choose a new validation bundle directory")
            if options.get("ngspice_executable"):
                validation = validate_circuit_network(report, directory, executable=options["ngspice_executable"],
                    timeout=options.get("simulator_timeout", 30.), progress=progress, cancel=cancel, execution=execution)
            else:
                validation = prepare_circuit_validation(report, directory, cancel=cancel)
            if operation in {"multiport_validate", "multiport_bundle"}:
                return {"report": validation, "exit_code": 2 if validation.get("status") not in {"passed", "prepared"} else 0}
            report["circuit_validation"] = validation
        if options.get("output"):
            export_multiport(report, options["output"], format=options.get("format", "json"))
        return {"report": report, "geometry": geometry,
                "exit_code": 2 if report.get("circuit_validation", {}).get("status") not in {None, "passed", "prepared"} else 0}
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
        workspace = Path(options["output"])
        root = workspace.parent if workspace.is_file() else workspace
        history = options.get("history")
        if history is None:
            path = Path(options["output"])
            history = json.loads((path if path.is_file() else path / "history.json").read_text(encoding="utf-8"))
        destination = options.get("export_directory") or str(Path(options["output"]) / "export")
        from etlayout.io.resources import ExternalResourcePolicy
        report = export_finalist(project, history, options["candidate_id"], destination, cancel=cancel,
                                 resource_policy=ExternalResourcePolicy(allowed_root=root))
        return {"report": report}
    raise SimulationError(f"Unknown power workflow {operation}")
