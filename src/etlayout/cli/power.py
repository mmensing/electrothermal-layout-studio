# SPDX-License-Identifier: MPL-2.0
"""Headless power, physical routing and experiment workflows."""
from __future__ import annotations

import json
from pathlib import Path

from etlayout.cli.resources import policy_from_args, resource_arguments
from etlayout.models import SimulationError

COMMANDS = {"power", "multiport", "routing", "optimize", "parasitic", "field-reference"}


def add_arguments(subparsers, execution_arguments):
    reference = subparsers.add_parser("field-reference", help="Generate a small ready-to-open C or closed-return RL project; no solver is run")
    reference.add_argument("kind", choices=["capacitance", "loop_rl"])
    reference.add_argument("directory", help="New or empty output directory")
    for command, description in (
        ("power", "Solve and export scoped measurements, individual contacts and loss budgets"),
        ("multiport", "Extract a fixed-temperature passive conductance matrix"),
        ("routing", "Validate or generate constrained routing, with export/reimport checks"),
        ("optimize", "Run or resume a bounded multi-scenario routing experiment"),
        ("parasitic", "Extract 3D homogeneous capacitance or explicit closed-return RL using a separate field engine"),
    ):
        parser = subparsers.add_parser(command, help=description)
        execution_arguments(parser)
        parser.add_argument("project")
        resource_arguments(parser)
        parser.add_argument("--output", "-o", default="power-results")
        if command == "power":
            parser.add_argument("--no-plots", action="store_true")
        elif command == "multiport":
            parser.add_argument("--ports", help="Comma-separated physical port IDs")
            parser.add_argument("--reference", help="Reference port ID")
            parser.add_argument("--format", choices=["json", "csv", "spice"], default="json")
            parser.add_argument("--temperature-source", choices=["initial", "uniform", "retained"],
                                help="Freeze the initial, explicit uniform or saved run's final temperature")
            parser.add_argument("--temperature-k", type=float, help="Explicit fixed absolute temperature in K (implies uniform)")
            parser.add_argument("--mesh-study", action="store_true", help="Run three to five extraction-specific mesh levels; output JSON evidence")
            parser.add_argument("--mesh-sizes-um", type=float, nargs="+", help="Coarse-to-fine global sizes in micrometers; implies --mesh-study")
            parser.add_argument("--mesh-tolerance", type=float, help="Per-entry relative change limit, dimensionless; default 0.02")
            parser.add_argument("--validation-directory", help="New/empty self-contained circuit validation bundle folder")
            parser.add_argument("--ngspice-executable", help="Explicit trusted console executable; runs generated decks only (Windows: ngspice_con.exe)")
            parser.add_argument("--simulator-timeout", type=float, help="Total simulator time budget in seconds")
        elif command == "parasitic":
            parser.add_argument("--kind", choices=["capacitance", "loop_rl"], required=True)
            parser.add_argument("--prepare-only", action="store_true", help="Generate field inputs without a calculation")
            parser.add_argument("--executable", help="Explicit trusted external FastCap2/raw-output or FastHenry console executable")
            parser.add_argument("--temperature-k", type=float, help="Uniform fixed field temperature; default project initial temperature")
            parser.add_argument("--panel-sizes-um", type=float, nargs="+", help="C: 3–5 decreasing panel sizes; default 100 50 25 µm")
            parser.add_argument("--direct", action="store_true", help="C: slower direct interactions, hard cap 2000 panels; no symmetrization")
            parser.add_argument("--max-panels", type=int, help="C: panel budget, default 20000")
            parser.add_argument("--frequencies-hz", type=float, nargs="+", help="RL: 2–8 increasing frequencies beginning at DC, default 0 1 10 Hz")
            parser.add_argument("--refinements", help="RL: 3–5 longitudinal:cross-section pairs, default 1:1,2:3,4:5")
            parser.add_argument("--max-filaments", type=int, help="RL: volume-filament budget, default 1024")
            parser.add_argument("--mesh-tolerance", type=float, default=.02, help="Per-entry relative sensitivity limit, default 0.02")
            parser.add_argument("--timeout", type=float, default=180., help="Total external-engine time budget in seconds")
        elif command == "routing":
            parser.add_argument("--action", choices=["validate", "generate", "export"], default="validate")
            parser.add_argument("--parameters", default="{}", help="JSON object or path to a JSON file")
        elif command == "optimize":
            parser.add_argument("--experiment", required=True, help="Saved experiment ID")
            parser.add_argument("--no-resume", action="store_true", help="Start a new experiment history")
            parser.add_argument("--export-candidate", help="Export this verified candidate from saved history")
            parser.add_argument("--export-directory", help="Output folder for verified GDS/OASIS finalist")


def read_object(value):
    """Accept a JSON object or an existing file; never execute parameter input."""
    raw = str(value).strip()
    if not raw.startswith("{"):
        raw = Path(raw).read_text(encoding="utf-8")
    data = json.loads(raw)
    if not isinstance(data, dict):
        raise SimulationError("Routing parameters must be a JSON object")
    return data


def run_command(args, *, progress=None):
    from etlayout.io.project import load_project
    from etlayout.power.workflows import execute_operation
    from etlayout.simulation.execution import ExecutionSettings, execution_context, validate_execution

    if args.command == "field-reference":
        from etlayout.parasitic_examples import make_field_reference
        project = make_field_reference(args.kind, args.directory)
        return {"status": "generated", "project": project.name,
                "directory": str(Path(args.directory).absolute()), "calculated": False}, 0
    execution = ExecutionSettings(cpu_threads=args.cpu_threads,
        study_workers=args.study_workers if args.study_workers is not None else args.cpu_threads)
    validate_execution(execution)
    project, retained = load_project(args.project, external_policy=policy_from_args(args))
    options = {"output": args.output}
    operation = args.command
    if operation == "power":
        options["plots"] = not args.no_plots
    elif operation == "multiport":
        options.update(port_ids=[key.strip() for key in args.ports.split(",")] if args.ports else None,
                       reference=args.reference, format=args.format,
                       temperature_mode=args.temperature_source or ("uniform" if args.temperature_k is not None else "initial"),
                       temperature_K=args.temperature_k, validation_directory=args.validation_directory,
                       ngspice_executable=args.ngspice_executable,
                       simulator_timeout=args.simulator_timeout if args.simulator_timeout is not None else 30.)
        if options["temperature_mode"] == "retained":
            options["result"] = retained
        if args.mesh_tolerance is not None and not (args.mesh_study or args.mesh_sizes_um is not None):
            raise SimulationError("--mesh-tolerance requires --mesh-study or --mesh-sizes-um")
        if args.simulator_timeout is not None and not args.ngspice_executable:
            raise SimulationError("--simulator-timeout requires --ngspice-executable")
        if args.ngspice_executable and not args.validation_directory:
            raise SimulationError("--ngspice-executable requires a new/empty --validation-directory")
        if args.mesh_study or args.mesh_sizes_um is not None:
            if args.format != "json" or args.validation_directory or args.ngspice_executable:
                raise SimulationError("Mesh studies output JSON evidence; extract/export or validate a circuit in a separate command")
            operation = "multiport_mesh"
            options.update(mesh_sizes=[value * 1e-6 for value in args.mesh_sizes_um] if args.mesh_sizes_um else None,
                           relative_tolerance=args.mesh_tolerance if args.mesh_tolerance is not None else .02)
    elif operation == "parasitic":
        if args.prepare_only and args.executable:
            raise SimulationError("--prepare-only does not execute a tool; omit --executable")
        if not args.prepare_only and not args.executable:
            raise SimulationError("--executable is required for a calculation; use --prepare-only to generate inputs")
        if args.kind == "capacitance" and any(value is not None for value in (args.frequencies_hz, args.refinements, args.max_filaments)):
            raise SimulationError("Frequency/filament options require --kind loop_rl")
        if args.kind == "loop_rl" and (args.direct or any(value is not None for value in (args.panel_sizes_um, args.max_panels))):
            raise SimulationError("Panel options require --kind capacitance")
        options.update(kind=args.kind, prepare_only=args.prepare_only, executable=args.executable,
            temperature_K=args.temperature_k, relative_tolerance=args.mesh_tolerance, timeout=args.timeout, direct=args.direct)
        if args.panel_sizes_um is not None:
            options["mesh_sizes_m"] = [value*1e-6 for value in args.panel_sizes_um]
        if args.max_panels is not None:
            options["max_panels"] = args.max_panels
        if args.frequencies_hz is not None:
            options["frequencies_Hz"] = args.frequencies_hz
        if args.refinements is not None:
            try:
                options["refinements"] = [tuple(int(value) for value in pair.split(":")) for pair in args.refinements.split(",")]
            except ValueError as error:
                raise SimulationError("Use increasing integer refinement pairs, e.g. 1:1,2:3,4:5") from error
        if args.max_filaments is not None:
            options["max_filaments"] = args.max_filaments
    elif operation == "routing":
        operation = "routing_" + args.action
        options["parameters"] = read_object(args.parameters)
    elif operation == "optimize":
        options.update(experiment_id=args.experiment, resume=not args.no_resume)
        if args.export_candidate:
            operation = "export_finalist"
            options.update(candidate_id=args.export_candidate, export_directory=args.export_directory)
    with execution_context(execution):
        output = execute_operation(project, operation, options, progress=progress, execution=execution)
    return output["report"], output.get("exit_code", 0)
