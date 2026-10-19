# SPDX-License-Identifier: MPL-2.0
"""Fail-closed scientific CI scope from actual changed paths, never PR prose.

Unknown files and unavailable Git comparisons require validation. Only an
explicit documentation/image allowlist can avoid numerical PR execution.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
from pathlib import Path, PurePosixPath

DOCUMENT_NAMES = frozenset({"README.md", "CONTRIBUTING.md", "SECURITY.md", "LICENSE",
    "THIRD_PARTY_NOTICES.md", "CODE_OF_CONDUCT.md"})
DOCUMENT_SUFFIXES = frozenset({".md", ".rst", ".txt", ".png", ".jpg", ".jpeg", ".svg", ".webp", ".html"})


def documentation_only(paths):
    """Scientific code/data moved into docs still appears as its deleted path."""
    for raw in paths:
        path = PurePosixPath(raw)
        if path.is_absolute() or ".." in path.parts or "\\" in raw:
            return False
        if raw in DOCUMENT_NAMES or raw == "src/etlayout/data/user-guide.html":
            continue
        if path.parts and path.parts[0] == "docs" and path.suffix.lower() in DOCUMENT_SUFFIXES:
            continue
        if raw.startswith(".github/ISSUE_TEMPLATE/") and path.suffix in {".md", ".yml", ".yaml"}:
            continue
        if raw == ".github/PULL_REQUEST_TEMPLATE.md":
            continue
        return False
    return True


def scientific_scope(paths, event="pull_request"):
    paths = tuple(paths)
    required = event == "workflow_dispatch" or not documentation_only(paths)
    return {"version": 1, "event": event, "required": required,
            "reason": "Owner-requested complete validation" if event == "workflow_dispatch" else
                      "Source, configuration, data, tests or unknown paths changed" if required else
                      "Only maintained prose/images/templates changed",
            "changed_paths": list(paths)}


def changed_paths(base):
    if not re.fullmatch(r"[a-fA-F0-9]{40}", base or "") or set(base) == {"0"}:
        raise ValueError("A real 40-digit public base commit is required; scope cannot be inferred")
    exists = subprocess.run(["git", "cat-file", "-e", base + "^{commit}"], capture_output=True)
    if exists.returncode:
        # The input is a validated object ID, passed as data; checkout credentials
        # are disabled and this public repository fetch needs no private token.
        subprocess.run(["git", "fetch", "--no-tags", "--depth=1", "origin", base], check=True)
    raw = subprocess.check_output(["git", "diff", "--no-renames", "--name-only", "-z", base, "HEAD"])
    return [path.decode("utf-8", "surrogateescape") for path in raw.split(b"\0") if path]


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base", default=os.environ.get("ETLAYOUT_BASE_REVISION", ""))
    parser.add_argument("--event", default=os.environ.get("ETLAYOUT_EVENT_NAME", "workflow_dispatch"))
    parser.add_argument("--output", type=Path, default=Path("artifacts/scientific-scope.json"))
    args = parser.parse_args(argv)
    try:
        paths = [] if args.event == "workflow_dispatch" else changed_paths(args.base)
        report = scientific_scope(paths, args.event)
    except (ValueError, subprocess.CalledProcessError, OSError) as error:
        # A missing comparison cannot turn a physical PR into a docs-only pass.
        report = {"version": 1, "event": args.event, "required": True,
                  "reason": f"Scope comparison unavailable; full validation required ({type(error).__name__})",
                  "changed_paths": []}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2), encoding="utf-8")
    if os.environ.get("GITHUB_OUTPUT"):
        with Path(os.environ["GITHUB_OUTPUT"]).open("a", encoding="utf-8") as output:
            output.write(f"required={str(report['required']).lower()}\n")
    print(json.dumps(report))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
