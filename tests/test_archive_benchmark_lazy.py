# SPDX-License-Identifier: MPL-2.0
"""Cross-reader benchmark isolation requires the explicit phase-two history store."""
import json
import os
import sys
from pathlib import Path

from test_archive_benchmark import benchmark


def test_real_reader_failure_does_not_suppress_independent_modes(tmp_path, monkeypatch):
    from test_archive_budgets import stored_result

    import etlayout
    from etlayout.io.project import save_project

    source = tmp_path / "solved-fixture"
    (source / "inputs").mkdir(parents=True)
    project, fixture_result = stored_result()
    save_project(project, source / "inputs/reference.etl", fixture_result)
    (source / "inputs.json").write_text(
        json.dumps({"shared": [{"file": "inputs/reference.etl"}]}), encoding="utf-8")
    observe = benchmark.observe
    def inject_reader_process_exit(command, prefix, **budgets):
        if "--reader-case" in command and command[-1] == "eager":
            command = [sys.executable, "-c", "raise SystemExit(7)"]
        return observe(command, prefix, **budgets)
    monkeypatch.setattr(benchmark, "observe", inject_reader_process_exit)
    target = tmp_path / "measurements"
    # main is normally a standalone process; restore its owned-temp environment
    # changes when exercising the real CLI orchestrator inside this test process.
    for name in ("TEMP", "TMP"):
        monkeypatch.setenv(name, os.environ.get(name, str(tmp_path)))
    runtime = Path(etlayout.__file__).resolve().parents[2]
    code = benchmark.main(["--output", str(target), "--inputs-from", str(source), "--runtime-root", str(runtime),
                           "--families", "shared", "--counts", "1", "--formats", "3", "--repeats", "1",
                           "--reader-phases", "eager", "lazy-startup", "lazy-full"])
    assert code == 1
    records = json.loads((target / "receipt.json").read_text(encoding="utf-8"))["measurements"]
    assert [row["reader_mode"] for row in records] == ["save-reopen", "eager", "lazy-startup", "lazy-full"]
    assert records[0]["round_trip_exact"] is True
    assert records[1]["status"] == "failed" and records[1]["exit_code"] == 7
    assert all(row["round_trip_exact"] for row in records[2:])
