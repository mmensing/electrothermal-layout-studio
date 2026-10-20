# SPDX-License-Identifier: MPL-2.0
"""Regenerate the portable example library (all examples, or selected IDs)."""
import argparse
import json
from pathlib import Path

from etlayout.cli.main import main
from etlayout.models import SimulationError


def synchronize_runtime_resources(root: Path, *, check=False):
    """Refresh/check navigation metadata and exact portable runtime copies.

    This maintainer operation reads canonical archives; it never regenerates
    geometry or runs simulations. Scientific reference records remain unchanged.
    """
    from etlayout.example_catalog import EXAMPLE_CATALOG, example_info
    from etlayout.example_library import COMPANION_CATALOG
    from etlayout.io.project import inspect_project

    root = Path(root).resolve()
    canonical = root / "examples"
    destination = root / "src/etlayout/data/examples"
    index = canonical / "index.json"
    rows = json.loads(index.read_text(encoding="utf-8"))
    if [row["name"] for row in rows] != list(EXAMPLE_CATALOG):
        raise ValueError("Canonical index IDs/order differ from the catalog; regenerate the complete reference index first")
    expected_rows = []
    resources = [Path(name + ".etl") for name in EXAMPLE_CATALOG]
    resources += [Path("power_routing") / info.filename for info in COMPANION_CATALOG.values()]
    for row in rows:
        info = example_info(row["name"])
        inspection = inspect_project(canonical / (info.name + ".etl"))
        if not inspection.metadata.get("embedded_layout") or inspection.project.name != info.name:
            raise ValueError(f"Canonical reference {info.id} must embed its layout and retain its stable name")
        expected_rows.append({**row, **info.to_dict(), "project": info.name + ".etl", "gds": info.name + ".gds",
                              "oasis": info.name + ".oas", "description": inspection.project.notes,
                              "source_sha256": inspection.project.layout.sha256})
    # JSON-normalize tuples so comparison reflects the actual maintained index.
    expected_rows = json.loads(json.dumps(expected_rows, ensure_ascii=False))
    stale_index = rows != expected_rows
    expected_paths = set(resources)
    unexpected = [path.relative_to(destination).as_posix() for path in destination.rglob("*.etl")
                  if path.relative_to(destination) not in expected_paths] if destination.exists() else []
    if unexpected:
        raise ValueError("Unexpected runtime archives require maintainer review: " + ", ".join(unexpected))
    stale_resources = [relative for relative in resources if not (destination / relative).is_file()
                       or (canonical / relative).read_bytes() != (destination / relative).read_bytes()]
    if check and (stale_index or stale_resources):
        details = (["examples/index.json metadata"] if stale_index else [])
        details += ["runtime " + path.as_posix() for path in stale_resources]
        raise ValueError("Stale example resources: " + ", ".join(details) + "; run --sync-runtime-resources")
    if not check:
        for relative in stale_resources:
            target = destination / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes((canonical / relative).read_bytes())
        if stale_index:
            index.write_text(json.dumps(expected_rows, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return dict(operation="check" if check else "sync", reference_projects=len(EXAMPLE_CATALOG),
                companion_projects=len(COMPANION_CATALOG), changed_archives=0 if check else len(stale_resources),
                index_updated=bool(stale_index and not check), calculations_run=0)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=Path(__file__).resolve().parents[1] / "examples")
    parser.add_argument("--examples", nargs="+", help="IDs such as E11 E12; omit to regenerate the complete library and index")
    parser.add_argument("--scenario")
    parser.add_argument("--preset")
    parser.add_argument("--variant")
    parser.add_argument("--current", type=float)
    parser.add_argument("--thermal", choices=["attached", "resistive_attach"])
    parser.add_argument("--pulse", action="store_true")
    parser.add_argument("--fingers", type=int)
    parser.add_argument("--gate-width-um", type=float)
    parser.add_argument("--stress", action="store_true")
    runtime = parser.add_mutually_exclusive_group()
    runtime.add_argument("--sync-runtime-resources", action="store_true",
                         help="Maintainer: sync canonical index metadata and portable runtime archives; no generation or solve")
    runtime.add_argument("--check-runtime-resources", action="store_true",
                         help="Check canonical index metadata and portable runtime archives; fail if stale, no generation or solve")
    args = parser.parse_args()
    if args.sync_runtime_resources or args.check_runtime_resources:
        generation = (args.examples or args.scenario is not None or args.preset is not None or args.variant is not None
                      or args.current is not None or args.thermal is not None or args.pulse or args.fingers is not None
                      or args.gate_width_um is not None or args.stress
                      or args.output.resolve() != Path(__file__).resolve().parents[1] / "examples")
        if generation:
            parser.error("Runtime resource synchronization cannot be combined with generation options")
        try:
            report = synchronize_runtime_resources(Path(__file__).resolve().parents[1], check=args.check_runtime_resources)
        except (ValueError, OSError, SimulationError) as error:
            parser.error(str(error))
        print(json.dumps(report))
        raise SystemExit(0)
    options = [argument for key in ("scenario", "preset", "variant", "current", "thermal", "fingers", "gate_width_um") if getattr(args, key) is not None
               for argument in ("--" + key.replace("_", "-"), str(getattr(args, key)))]
    if args.pulse:
        options.append("--pulse")
    if args.stress:
        options.append("--stress")
    if not args.examples:
        raise SystemExit(main(["examples", str(args.output), *options]))
    for name in args.examples:
        code = main(["examples", str(args.output), "--name", name, *options])
        if code:
            raise SystemExit(code)
    print(json.dumps({"generated": len(args.examples), "index": "Unchanged; regenerate all reference examples to refresh the index."}))
