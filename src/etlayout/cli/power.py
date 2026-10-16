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
    project, _ = load_project(args.project, external_policy=policy_from_args(args))
    options = {"output": args.output}
    operation = args.command
    if operation == "power":
        options["plots"] = not args.no_plots
    elif operation == "multiport":
        options.update(port_ids=[key.strip() for key in args.ports.split(",")] if args.ports else None,
                       reference=args.reference, format=args.format)
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
