# SPDX-License-Identifier: MPL-2.0
"""Headless power, physical routing and experiment workflows."""
from __future__ import annotations

import json
from pathlib import Path

from etlayout.cli.resources import policy_from_args, resource_arguments
from etlayout.models import SimulationError

COMMANDS = {"power", "multiport", "routing", "optimize"}


def add_arguments(subparsers, execution_arguments):
    for command, description in (
        ("power", "Solve and export scoped measurements, individual contacts and loss budgets"),
        ("multiport", "Extract a fixed-temperature passive conductance matrix"),
        ("routing", "Validate or generate constrained routing, with export/reimport checks"),
        ("optimize", "Run or resume a bounded multi-scenario routing experiment"),
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
