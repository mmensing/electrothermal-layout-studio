# SPDX-License-Identifier: MPL-2.0
from __future__ import annotations

import argparse
import json
import sys
from copy import deepcopy
from pathlib import Path

from etlayout import __version__
from etlayout.models import SimulationError, SimulationResult, SweepSpec


def execution_arguments(parser):
    from etlayout.simulation.execution import ExecutionSettings
    defaults = ExecutionSettings()
    parser.add_argument("--cpu-threads", type=int, default=defaults.cpu_threads,
                        help="Total CPU budget, shared across simultaneous study points (default: 4)")
    parser.add_argument("--study-workers", type=int, default=None,
                        help="Concurrent independent study points (default: CPU budget)")


def parser():
    p = argparse.ArgumentParser(prog="etlayout", description="Electrothermal Layout Studio · SI-unit headless API")
    p.add_argument("--version", action="version", version=__version__)
    sub = p.add_subparsers(dest="command")
    sub.add_parser("capabilities", help="Report available CPUs and native thread control")
    gui = sub.add_parser("gui", help="Launch native Windows desktop")
    gui.add_argument("project", nargs="?")
    gui.add_argument("--no-recovery", action="store_true")
    gui.add_argument("--smoke-test", action="store_true")
    gui.add_argument("--screenshot")
    gui.add_argument("--editor-smoke-output", help=argparse.SUPPRESS)
    ex = sub.add_parser("examples", help="Generate actual GDS/OASIS and ready-to-run projects")
    ex.add_argument("directory", nargs="?", default="examples")
    ex.add_argument("--name", help="Stable example ID or full name; omit to generate reference layouts only (no solves)")
    ex.add_argument("--preset", help="Power-device size: compact/reference/large; GaN also small, banks also stress")
    ex.add_argument("--variant", help="Family routing/feed variant; see power-examples documentation")
    ex.add_argument("--current", type=float, help="Requested total current in A; never silently reduced")
    ex.add_argument("--thermal", choices=["attached", "resistive_attach"], help="Named synthetic package boundary")
    ex.add_argument("--pulse", action="store_true", help="Generate an optional power-port pulse project; no automatic solve")
    ex.add_argument("--fingers", type=int, help="Actual gated paths per GaN bank, independently of conducting width")
    ex.add_argument("--gate-width-um", type=float, help="GaN conducting gate width along each finger, in micrometers")
    ex.add_argument("--stress", action="store_true", help="Label an overload point and disable verified-ranking experiments")
    inspect = sub.add_parser("inspect", help="Inspect GDS/OASIS hierarchy and masks")
    inspect.add_argument("layout")
    for command in ("validate", "mesh", "mesh-plan", "solve", "transient", "sweep", "refine", "time-refine"):
        q = sub.add_parser(command)
        execution_arguments(q)
        q.add_argument("project")
        q.add_argument("--output", "-o", default="results")
        q.add_argument("--set", action="append", default=[], metavar="PATH=VALUE")
        q.add_argument("--mode", choices=["electrical", "thermal", "electrothermal"],
                       help="Physics scope: electrical is DC only; transient requires thermal or electrothermal")
        q.add_argument("--operation", choices=["steady", "transient"],
                       help="Override saved time operation independently of physics mode")
        if command in {"solve", "transient"}:
            q.add_argument("--no-plots", action="store_true")
            q.add_argument("--stop-time", type=float, help="Stop time in seconds")
            q.add_argument("--time-step", type=float, help="Maximum integration step in seconds")
            q.add_argument("--output-interval", type=float, help="Saved field interval in seconds")
        if command in {"solve", "transient", "time-refine", "sweep", "refine"}:
            q.add_argument("--initialization", choices=["uniform", "steady"], help="Initial thermal state")
            initial = q.add_mutually_exclusive_group()
            initial.add_argument("--initial-state", metavar="RESULT.etl", help="Start at t=0 using a compatible saved temperature field")
            initial.add_argument("--resume", metavar="RESULT.etl", help="Continue a saved transient to the absolute --stop-time")
            q.add_argument("--periodic-tolerance", type=float, help="Cycle-to-cycle temperature tolerance in K; 0 disables early stopping")
            q.add_argument("--periodic-min-cycles", type=int, help="Minimum cycles before testing periodic convergence")
            q.add_argument("--periodic-required-cycles", type=int, help="Consecutive converged cycles required")
        if command == "sweep":
            q.add_argument("--parameter")
            q.add_argument("--values", help="JSON list, e.g. '[0.1,0.2,0.5]'")
            q.add_argument("--range", nargs=3, type=float, metavar=("START", "STOP", "COUNT"))
            q.add_argument("--spacing", choices=["linear", "log"], default="linear")
        if command == "refine":
            source = q.add_mutually_exclusive_group(required=True)
            source.add_argument("--sizes", help="Comma-separated maximum mesh sizes in meters")
            source.add_argument("--local-plan", help="Reviewed JSON from mesh-plan; refine selected local supports only")
            q.add_argument("--factors", default="1,0.75,0.5", help="2–4 decreasing local factors starting at 1")
            q.add_argument("--selected", help="Comma-separated suggestion IDs; default selects all")
            q.add_argument("--profiles", help="JSON file: list of {layer_id,field,start,end,count} profile requests in SI")
        if command == "mesh-plan":
            q.add_argument("--memory-mib", type=int, default=4096, help="Additional numerical-memory screen, excluding process baseline; not a peak-RSS guarantee")
            q.add_argument("--max-elements", type=int, help="Explicit reviewed candidate element budget")
            q.add_argument("--max-dofs", type=int, help="Explicit reviewed candidate DOF budget")
            q.add_argument("--averaging-length", type=float, help="Generated hotspot square side in meters, fixed across levels; default 4 × original global mesh size")
            q.add_argument("--hotspot-layer", help="Generated averaged-metric physical layer; default first conductor")
            q.add_argument("--apply", metavar="PROJECT.etl", help="Explicitly save a new project with all suggested sizing; imported layout is preserved")
        if command == "time-refine":
            q.add_argument("--steps", required=True, help="Comma-separated maximum time steps in seconds; output interval remains fixed")
    export = sub.add_parser("export", help="Explicit figure/data sharing or confidential complete experiment archive")
    export.add_argument("project")
    export.add_argument("--mode", required=True, choices=["figure", "data", "archive"])
    export.add_argument("--output", "-o", required=True, help="Requested destination directory; no silent overwrites")
    export.add_argument("--name", default="selection", help="Artifact basename, without a path")
    export.add_argument("--format", action="append", dest="formats", help="Repeat PNG/PDF/SVG/HTML for figures, CSV/NPZ/VTU for data, ETL for archive")
    export.add_argument("--layer", action="append", default=[], help="Repeat explicitly selected layer IDs")
    export.add_argument("--field", action="append", default=[], help="Repeat selected field quantities")
    export.add_argument("--region", default="", help="Named solved support or connected region")
    export.add_argument("--roi", nargs=4, type=float, metavar=("XMIN", "YMIN", "XMAX", "YMAX"), help="Exact rectangle clipping in meters")
    export.add_argument("--time", type=float, help="Seconds; nearest retained snapshot is explicitly recorded")
    export.add_argument("--colormap", default="teal")
    export.add_argument("--color-limits", nargs=2, type=float, metavar=("MIN", "MAX"), help="SI display range; raw numerical data remain unclipped")
    export.add_argument("--log", action="store_true", help="Logarithmic figure colors; numerical datasets retain nonpositive values")
    export.add_argument("--width", type=float, default=7.1, help="Figure width in inches")
    export.add_argument("--height", type=float, default=4.8, help="Figure height in inches")
    export.add_argument("--dpi", type=int, default=300)
    export.add_argument("--pdf-page", choices=["A4", "A3", "Letter", "Custom"], default="A4")
    export.add_argument("--orientation", choices=["landscape", "portrait"], default="landscape")
    export.add_argument("--title", default="", help="Optional explicitly shared title; project name is excluded")
    export.add_argument("--annotation", default="", help="Only this annotation is included; project notes are excluded")
    export.add_argument("--colorbar", action=argparse.BooleanOptionalAction, default=True)
    export.add_argument("--legend", action=argparse.BooleanOptionalAction, default=True)
    export.add_argument("--smooth", action=argparse.BooleanOptionalAction, default=True)
    export.add_argument("--overwrite", action="store_true", help="Explicit replacement of only the listed artifacts")
    export.add_argument("--preview", action="store_true", help="Print exact information/file disclosure without writing exports")
    compare = sub.add_parser("compare", help="Run equivalent studies across saved projects")
    execution_arguments(compare)
    compare.add_argument("projects", nargs="+")
    compare.add_argument("--mode", default="electrothermal", choices=["electrical", "thermal", "electrothermal"])
    compare.add_argument("--output", "-o", default="comparison")
    materials = sub.add_parser("materials", help="Export the reference and custom material library")
    materials.add_argument("output")
    materials.add_argument("--database")
    from etlayout.cli.power import add_arguments
    add_arguments(sub, execution_arguments)
    return p


def main(argv=None):
    args = parser().parse_args(argv)
    if args.command in {None, "gui"}:
        from etlayout.app.main import main as gui_main
        gui_args = [args.project] if getattr(args, "project", None) else []
        if getattr(args, "no_recovery", False):
            gui_args.append("--no-recovery")
        if getattr(args, "smoke_test", False):
            gui_args.append("--smoke-test")
        if getattr(args, "screenshot", None):
            gui_args.extend(["--screenshot", args.screenshot])
        if getattr(args, "editor_smoke_output", None):
            gui_args.extend(["--editor-smoke-output", args.editor_smoke_output])
        return gui_main(gui_args)
    try:
        from etlayout.cli.power import COMMANDS, run_command
        from etlayout.io.project import _json_dump, load_project, save_project
        if args.command in COMMANDS:
            report, exit_code = run_command(args, progress=lambda fraction, message:
                print(f"[{fraction:6.1%}] {message}", file=sys.stderr))
            print(_json_dump(report))
            return exit_code
        if args.command == "capabilities":
            from etlayout.simulation.execution import detect_capabilities
            print(_json_dump(detect_capabilities()))
            return 0
        if args.command == "examples":
            from etlayout.examples import generate_all, make_example
            options = dict(preset=args.preset, variant=args.variant, current=args.current, thermal=args.thermal,
                           fingers=args.fingers, gate_width_um=args.gate_width_um)
            if not args.name and (args.pulse or args.stress or any(value is not None for value in options.values())):
                raise ValueError("Preset/variant/current/thermal options require --name")
            projects = [make_example(args.name, args.directory, pulse=args.pulse, stress=args.stress, **options)] if args.name else generate_all(args.directory)
            print(_json_dump([{"name": p.name, "layout": p.layout.path} for p in projects]))
            return 0
        if args.command == "inspect":
            from etlayout.geometry.layout import LayoutDocument
            doc = LayoutDocument(args.layout)
            print(_json_dump({"cells": doc.cells, "top_cells": doc.top_cells, "layers": doc.layers,
                              "dbu_um": doc.dbu_um, "bounds_m": doc.bounds, "instances": doc.instances()}))
            return 0
        if args.command == "materials":
            from etlayout.materials.database import MaterialDatabase
            MaterialDatabase(args.database).export_json(args.output)
            print(args.output)
            return 0
        if args.command == "export":
            from etlayout.io.project import load_run_history
            from etlayout.io.sharing import ExportRequest, FigureOptions, execute_export, preview_export
            project, result = load_project(args.project)
            if result is None and args.mode != "archive":
                raise ValueError("Sharing requires saved calculated fields; run and save the project first")
            formats = tuple(item.lower() for item in args.formats) if args.formats else {
                "figure": ("png", "html"), "data": ("csv", "npz", "vtu"), "archive": ("etl",)}[args.mode]
            request = ExportRequest(mode=args.mode, layers=tuple(args.layer), fields=tuple(args.field), region=args.region,
                roi=tuple(args.roi) if args.roi else None, time_s=args.time, formats=formats, colormap=args.colormap,
                limits=tuple(args.color_limits) if args.color_limits else None, logarithmic=args.log,
                figure=FigureOptions(args.width, args.height, args.dpi, args.pdf_page, args.orientation,
                    args.title, args.annotation, args.colorbar, args.legend, args.smooth), stem=args.name, overwrite=args.overwrite)
            history = load_run_history(args.project) if args.mode == "archive" else []
            manifest = preview_export(result, request, project=project, history=history) if args.preview else execute_export(
                result, args.output, request, project=project, history=history)
            print(_json_dump(manifest))
            return 0
        from etlayout.io.export import export_results, export_study
        from etlayout.simulation.execution import ExecutionSettings, execution_context, validate_execution
        from etlayout.simulation.runner import prepare_mesh, run_project
        execution = ExecutionSettings(cpu_threads=args.cpu_threads,
                                      study_workers=args.study_workers if args.study_workers is not None else args.cpu_threads)
        validate_execution(execution)
        from etlayout.simulation.sweep import (
            compare_projects,
            mesh_refinement_study,
            resolve_study_initial_state,
            run_sweep,
            set_parameter,
            sweep_values,
        )
        def progress(fraction, message):
            print(f"[{fraction:6.1%}] {message}", file=sys.stderr)
        if args.command == "compare":
            loaded = [load_project(x) for x in args.projects]
            sources = [resolve_study_initial_state(project, result) for project, result in loaded]
            rows = compare_projects([item[0] for item in loaded], args.mode, progress=progress,
                                    initial_results=[item["initial_result"] for item in sources],
                                    resumes=[item["resume"] for item in sources], execution=execution)
            print(export_study(rows, args.output, "design"))
            return int(any(r.error for r in rows))
        project, loaded_result = load_project(args.project)
        if args.mode:
            project.solver.mode = args.mode
        for setting in args.set:
            if "=" not in setting:
                raise SimulationError("--set expects PATH=VALUE")
            parameter, value = setting.split("=", 1)
            try:
                value = json.loads(value)
            except json.JSONDecodeError:
                pass
            set_parameter(project, parameter, value)
        if args.operation:
            project.transient.enabled = args.operation == "transient"
        if args.command in {"transient", "time-refine"}:
            if args.operation == "steady":
                raise SimulationError(f"{args.command} requires --operation transient")
            project.transient.enabled = True
        if args.command in {"solve", "transient", "time-refine", "sweep", "refine"}:
            for name in ("stop_time", "time_step", "output_interval", "initialization", "periodic_tolerance",
                         "periodic_min_cycles", "periodic_required_cycles"):
                if getattr(args, name, None) is not None:
                    if not project.transient.enabled:
                        raise SimulationError(f"--{name.replace('_', '-')} requires --operation transient")
                    setattr(project.transient, name, getattr(args, name))
                    if name == "initialization":
                        project.transient.initial_state_digest = ""
        if args.command == "validate":
            from etlayout.connectivity.analyze import analyze_connectivity
            from etlayout.geometry.prepare import prepare_geometry
            from etlayout.technology.validation import validate_project
            start_time = 0.
            if project.transient.enabled and project.transient.initialization == "resume":
                if loaded_result is None or loaded_result.transient is None or not loaded_result.transient.snapshots:
                    raise SimulationError("Resume validation requires the original saved history to determine its start time; open the continuation archive with results")
                start_time = float(loaded_result.transient.times[0])
            validate_project(project)
            geometry = prepare_geometry(project)
            report = analyze_connectivity(project, geometry, start_time=start_time)
            output = {"geometry": [d.__dict__ for d in geometry.diagnostics], "connectivity": report.to_dict()}
            if project.transient.enabled:
                from etlayout.solvers.transient import estimate_transient_resources
                output["transient_resources"] = estimate_transient_resources(project, start_time=start_time)
                output["transient_resources"]["start_time_s"] = start_time
            print(_json_dump(output))
            return 2 if any(d.severity == "error" for d in [*geometry.diagnostics, *report.diagnostics]) else 0
        if args.command == "mesh-plan":
            from etlayout.meshing.refinement import (
                apply_refinement_plan,
                estimate_resources,
                screen_resources,
                suggest_refinements,
            )
            if args.memory_mib <= 0:
                raise SimulationError("Memory planning budget must be positive")
            plan = suggest_refinements(project, loaded_result, progress=progress,
                                       max_memory_bytes=args.memory_mib * 1024**2,
                                       averaging_length=args.averaging_length, hotspot_layer=args.hotspot_layer)
            if args.max_elements is not None:
                plan.max_elements = args.max_elements
            if args.max_dofs is not None:
                plan.max_dofs = args.max_dofs
            candidate = apply_refinement_plan(project, plan) if plan.suggestions else None
            root = Path(args.output)
            root.mkdir(parents=True, exist_ok=True)
            path = root / "local-mesh-plan.json"
            path.write_text(_json_dump(plan.to_dict()), encoding="utf-8")
            estimate = estimate_resources(candidate) if candidate is not None else None
            if args.apply:
                if candidate is None:
                    raise SimulationError("No local sizing suggestions are available")
                screen_resources(candidate, plan)
                save_project(candidate, args.apply)
            print(_json_dump({"plan": str(path), "suggestions": len(plan.suggestions),
                              "warnings": plan.warnings, "planning_estimate": estimate,
                              "applied_project": args.apply}))
            return 0
        if args.command == "mesh":
            with execution_context(execution):
                _, mesh = prepare_mesh(project, progress)
            root = Path(args.output)
            root.mkdir(parents=True, exist_ok=True)
            result = SimulationResult(mesh, configuration=project.to_dict())
            save_project(project, root / "mesh.etl", result)
            print(_json_dump(mesh.statistics))
            return 0
        if args.command in {"solve", "transient", "time-refine", "sweep", "refine"}:
            initial_result = None
            initial_path = args.initial_state or args.resume
            resume = bool(args.resume)
            if initial_path:
                if not project.transient.enabled:
                    raise SimulationError("Saved initial states and resume require --operation transient")
                _, initial_result = load_project(initial_path)
                if initial_result is None:
                    raise SimulationError("Initial state archive contains no saved result")
                project.transient.initial_state_digest = ""
                project.transient.initialization = "resume" if resume else "saved"
            sources = resolve_study_initial_state(project, loaded_result, initial_result=initial_result, resume=resume)
            initial_result, resume = sources["initial_result"], sources["resume"]
        if args.command in {"solve", "transient"}:
            result = run_project(project, progress=progress, initial_result=initial_result, resume=resume, execution=execution)
            export_results(result, args.output, plots=not args.no_plots)
            print(_json_dump({"converged": result.converged, "metrics": result.metrics, "terminals": result.terminals}))
            return 0 if result.converged else 2
        if args.command == "sweep":
            if args.parameter:
                if args.values:
                    values = json.loads(args.values)
                elif args.range:
                    values = sweep_values(args.range[0], args.range[1], args.range[2], args.spacing)
                else:
                    raise SimulationError("Sweep requires --values or --range")
                spec = SweepSpec(args.parameter, values, project.solver.mode)
            elif project.sweeps:
                spec = deepcopy(project.sweeps[0])
                if args.mode:
                    spec.mode = args.mode
            else:
                raise SimulationError("Project has no saved sweep; specify --parameter and values")
            rows = run_sweep(project, spec, progress=progress, initial_result=initial_result, resume=resume,
                             execution=execution)
            print(export_study(rows, args.output, spec.parameter))
            return int(any(r.error for r in rows))
        if args.command == "refine":
            profiles = json.loads(Path(args.profiles).read_text(encoding="utf-8")) if args.profiles else None
            local_plan = json.loads(Path(args.local_plan).read_text(encoding="utf-8")) if args.local_plan else None
            sizes = [float(x) for x in (args.factors if local_plan is not None else args.sizes).split(",")]
            selected = args.selected.split(",") if args.selected else None
            if local_plan is None and args.selected:
                raise SimulationError("--selected requires --local-plan")
            rows = mesh_refinement_study(project, sizes,
                                         progress=progress, profiles=profiles, initial_result=initial_result,
                                         resume=resume, execution=execution, local_plan=local_plan, selected=selected)
            print(export_study(rows, args.output, "local_refinement_factor" if local_plan is not None else "mesh_max_size_m"))
            return int(any(r.error for r in rows))
        if args.command == "time-refine":
            from etlayout.simulation.sweep import time_step_convergence_study
            rows = time_step_convergence_study(project, [float(x) for x in args.steps.split(",")], progress=progress,
                                               initial_result=initial_result, resume=resume, execution=execution)
            print(export_study(rows, args.output, "transient.time_step"))
            return int(any(row.error for row in rows))
    except (SimulationError, ValueError, OSError) as exc:
        print(f"etlayout: {exc}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
