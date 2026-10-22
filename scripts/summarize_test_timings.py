# SPDX-License-Identifier: MPL-2.0
"""Summarize explicit JUnit evidence without changing test selection or outcomes."""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import re
import xml.etree.ElementTree as ET
from collections import defaultdict
from pathlib import Path

MAX_REPORT_BYTES = 64 * 1024**2


def _seconds(value):
    seconds = float(value or 0)
    if not math.isfinite(seconds) or seconds < 0:
        raise ValueError("JUnit durations must be finite and nonnegative")
    return seconds


def read_report(path):
    with path.open("rb") as stream:
        raw = stream.read(MAX_REPORT_BYTES + 1)
    if len(raw) > MAX_REPORT_BYTES:
        raise ValueError("JUnit report exceeds the 64 MiB summary budget")
    if b"<!DOCTYPE" in raw.upper() or b"<!ENTITY" in raw.upper():
        raise ValueError("DTD and entity declarations are not JUnit timing input")
    root = ET.fromstring(raw)
    if root.tag not in {"testsuites", "testsuite"}:
        raise ValueError("Expected a JUnit testsuite or testsuites root")
    suites = [root] if root.tag == "testsuite" else []
    suites.extend(root.findall(".//testsuite"))
    leaf_suites = [suite for suite in suites if suite.find("testsuite") is None]
    cases = []
    for case in root.iter("testcase"):
        module = case.get("classname", "unknown")
        cases.append({"id": module + "::" + case.get("name", "unknown"), "module": module,
                      "seconds": _seconds(case.get("time")),
                      "status": "error" if case.find("error") is not None else
                                "failure" if case.find("failure") is not None else
                                "skipped" if case.find("skipped") is not None else "passed"})
    return {"sha256": hashlib.sha256(raw).hexdigest(), "cases": cases,
            "suite_seconds": sum(_seconds(suite.get("time")) for suite in leaf_suites)}


def summarize(reports, limit=15):
    modules = defaultdict(lambda: {"cases": 0, "seconds": 0.0})
    identities = defaultdict(list)
    output = {"schema_version": 1, "case_times_include_fixtures": True,
              "case_times_are_pipeline_wall_time": False, "reports": {}, "missing_reports": []}
    for label, path in reports.items():
        if not path.is_file():
            output["missing_reports"].append(label)
            continue
        report = read_report(path)
        cases = report["cases"]
        counts = {status: sum(case["status"] == status for case in cases)
                  for status in ("passed", "failure", "error", "skipped")}
        output["reports"][label] = {"file": path.name, "sha256": report["sha256"], "cases": len(cases),
            "outcomes": counts, "suite_seconds": round(report["suite_seconds"], 6),
            "case_seconds": round(sum(case["seconds"] for case in cases), 6),
            "slow_tests": sorted(cases, key=lambda case: (-case["seconds"], case["id"]))[:limit]}
        for case in cases:
            modules[case["module"]]["cases"] += 1
            modules[case["module"]]["seconds"] += case["seconds"]
            identities[case["id"]].append(label)
    output["slow_modules"] = [{"module": module, "cases": values["cases"],
                               "seconds": round(values["seconds"], 6)}
        for module, values in sorted(modules.items(), key=lambda pair: (-pair[1]["seconds"], pair[0]))[:limit]]
    repeated = [{"id": identity, "reports": sorted(set(labels)), "executions": len(labels)}
                for identity, labels in sorted(identities.items()) if len(set(labels)) > 1]
    output["repeated_tests_across_reports"] = {"count": len(repeated), "examples": repeated[:limit]}
    return output


def markdown(report):
    def cell(value):
        return str(value).replace("\n", " ").replace("\r", " ").replace("|", "&#124;").replace("<", "&lt;")

    lines = ["### Test timing evidence", "", "Case durations include fixture costs; their sum is not pipeline wall time.",
             "No test selection, outcomes, tolerances or required checks are changed.", "",
             "| Report | Cases | Passed | Failed/errors | Skipped | Suite seconds |",
             "|---|---:|---:|---:|---:|---:|"]
    for label, result in report["reports"].items():
        outcomes = result["outcomes"]
        lines.append(f"| {cell(label)} | {result['cases']} | {outcomes['passed']} | "
                     f"{outcomes['failure'] + outcomes['error']} | {outcomes['skipped']} | {result['suite_seconds']:.2f} |")
    if report["missing_reports"]:
        lines += ["", "Missing report evidence: " + ", ".join(cell(label) for label in report["missing_reports"]) + "."]
    lines += ["", "| Slow modules (accumulated case seconds) | Cases | Seconds |", "|---|---:|---:|"]
    for module in report["slow_modules"]:
        lines.append(f"| {cell(module['module'])} | {module['cases']} | {module['seconds']:.2f} |")
    repeated = report["repeated_tests_across_reports"]["count"]
    lines += ["", f"Tests observed in more than one report: {repeated}. See the JSON for bounded examples.", ""]
    return "\n".join(lines)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--report", action="append", default=[], metavar="LABEL=PATH")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--limit", type=int, default=15)
    parser.add_argument("--github-summary", action="store_true")
    args = parser.parse_args(argv)
    reports = {}
    try:
        if not 1 <= len(args.report) <= 100 or not 1 <= args.limit <= 100:
            raise ValueError("Provide 1–100 reports and a summary limit from 1 to 100")
        for entry in args.report:
            label, separator, path = entry.partition("=")
            if not separator or not re.fullmatch(r"[A-Za-z0-9_-]{1,80}", label) or not path or label in reports:
                raise ValueError("Each report needs a distinct simple LABEL and a nonempty PATH")
            reports[label] = Path(path)
        report = summarize(reports, args.limit)
    except (OSError, ValueError, ET.ParseError) as error:
        parser.error(str(error))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    if args.github_summary and os.environ.get("GITHUB_STEP_SUMMARY"):
        with Path(os.environ["GITHUB_STEP_SUMMARY"]).open("a", encoding="utf-8") as stream:
            stream.write(markdown(report))
    print(f"Summarized {len(report['reports'])} JUnit reports; {len(report['missing_reports'])} missing.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
