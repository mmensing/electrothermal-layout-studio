# SPDX-License-Identifier: MPL-2.0
"""Public commands generate/reopen literal field inputs without executing tools."""
import json
from pathlib import Path

import pytest

from etlayout.cli.main import parser
from etlayout.cli.power import run_command
from etlayout.models import SimulationError


@pytest.mark.parametrize("kind", ["capacitance", "loop_rl"])
def test_reference_and_prepare_commands_preserve_project_and_layout(tmp_path, kind):
    source = tmp_path / "reference"
    report, code = run_command(parser().parse_args(["field-reference", kind, str(source)]))
    assert code == 0 and report["calculated"] is False
    project = next(source.glob("*.etl"))
    before = {file.name: file.read_bytes() for file in source.iterdir()}
    output = tmp_path / "prepared"
    report, code = run_command(parser().parse_args(["parasitic", str(project), "--kind", kind,
        "--prepare-only", "--cpu-threads", "1", "-o", str(output)]))
    assert code == 0 and report["status"] == "prepared"
    snapshot = json.loads((output / "snapshot.json").read_text())
    assert snapshot["kind"] == kind and snapshot["geometry_sha256"]
    assert json.loads((output / "field-report.json").read_text())["status"] == "prepared"
    if kind == "loop_rl":
        import hashlib
        manifest = json.loads((output / "manifest.json").read_text())
        for name, record in manifest["files"].items():
            assert hashlib.sha256((output / name).read_bytes()).hexdigest() == record["sha256"]
    assert {file.name: file.read_bytes() for file in source.iterdir()} == before
    with pytest.raises(SimulationError, match="new or empty"):
        run_command(parser().parse_args(["parasitic", str(project), "--kind", kind,
            "--prepare-only", "-o", str(output)]))


@pytest.mark.parametrize("kind,extra,diagnostic", [
    ("capacitance", ["--frequencies-hz", "0", "1"], "loop_rl"),
    ("loop_rl", ["--panel-sizes-um", "100", "50", "25"], "capacitance"),
    ("capacitance", ["--executable", "unused.exe"], "omit --executable"),
])
def test_irrelevant_or_ambiguous_tool_options_fail_explicitly(tmp_path, kind, extra, diagnostic):
    source = tmp_path / "reference"
    run_command(parser().parse_args(["field-reference", kind, str(source)]))
    args = parser().parse_args(["parasitic", str(next(source.glob("*.etl"))),
        "--kind", kind, "--prepare-only", *extra, "-o", str(tmp_path / "out")])
    with pytest.raises(SimulationError, match=diagnostic):
        run_command(args)
    assert not Path(args.output).exists()
