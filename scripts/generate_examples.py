# SPDX-License-Identifier: MPL-2.0
"""Regenerate the portable example library (all examples, or selected IDs)."""
import argparse
import json
from pathlib import Path

from etlayout.cli.main import main

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=Path(__file__).resolve().parents[1] / "examples")
    parser.add_argument("--examples", nargs="+", help="IDs such as E11 E12; omit to regenerate the complete library and index")
    parser.add_argument("--preset")
    parser.add_argument("--variant")
    parser.add_argument("--current", type=float)
    parser.add_argument("--thermal", choices=["attached", "resistive_attach"])
    parser.add_argument("--pulse", action="store_true")
    parser.add_argument("--fingers", type=int)
    parser.add_argument("--gate-width-um", type=float)
    parser.add_argument("--stress", action="store_true")
    args = parser.parse_args()
    options = [argument for key in ("preset", "variant", "current", "thermal", "fingers", "gate_width_um") if getattr(args, key) is not None
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
