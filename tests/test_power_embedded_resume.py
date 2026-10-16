# SPDX-License-Identifier: MPL-2.0
"""An experiment must outlive temporary extraction of its embedded source."""
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest
from test_power_optimization import example

from etlayout.io.project import file_hash, load_project, save_project
from etlayout.io.resources import ExternalResourcePolicy


def _process(arguments, temporary):
    temporary.mkdir()
    environment = os.environ.copy()
    environment.update(TEMP=str(temporary), TMP=str(temporary), TMPDIR=str(temporary),
                       PYTHONPATH=str(Path(__file__).resolve().parents[1] / "src"),
                       PYTHONUTF8="1")
    return subprocess.run([sys.executable, *arguments], env=environment, text=True,
                          encoding="utf-8", capture_output=True, timeout=90, check=False)


def test_embedded_source_survives_new_process_resume_without_original_or_extraction(tmp_path):
    project = example(tmp_path)
    project.power.experiments[0].max_evaluations = 1
    project.power.experiments[0].scenarios = project.power.experiments[0].scenarios[:1]
    archive, directory = tmp_path / "embedded.etl", tmp_path / "search"
    save_project(project, archive)
    original_hash = project.layout.sha256
    Path(project.layout.path).unlink()  # The embedded archive is the only input.
    script = """
import json, sys
from etlayout.io.project import load_project
from etlayout.optimization import run_experiment
project, _ = load_project(sys.argv[1])
stopped = False
def progress(fraction, message):
    global stopped
    stopped = message == 'Refining baseline'
history = run_experiment(project, 'width', sys.argv[2], progress=progress, cancel=lambda: stopped)
print(json.dumps(history))
"""
    first_temp = tmp_path / "first-process-temp"
    first = _process(["-c", script, str(archive), str(directory)], first_temp)
    assert first.returncode == 0, first.stderr
    interrupted = json.loads(first.stdout)
    assert interrupted["status"] == "cancelled"
    baseline = interrupted["rows"][0]
    scenario_archive = Path(baseline["scenarios"][0]["archive"])
    previous = scenario_archive.stat().st_mtime_ns, file_hash(scenario_archive)
    retained = Path(interrupted["source_layout"]["path"])
    assert retained.parent == directory / "source"
    assert file_hash(retained) == original_hash
    assert baseline["layout_path"] == str(retained)
    baseline_project, _ = load_project(baseline["project_path"], external_policy=ExternalResourcePolicy(allowed_root=directory))
    assert baseline_project.layout.path == str(retained)
    assert baseline_project.power.recipes[0].source_hash == original_hash
    extracted = list((first_temp / "etlayout-layouts").glob("*.gds"))
    assert extracted
    for path in extracted:
        path.unlink()
    args = ["-m", "etlayout", "optimize", str(archive), "--experiment", "width",
            "--output", str(directory), "--cpu-threads", "1"]
    second_temp = tmp_path / "second-process-temp"
    second = _process(args, second_temp)
    assert second.returncode == 0, second.stderr
    resumed = json.loads(second.stdout)
    assert resumed["status"] == "complete"
    assert resumed["cache_key"] == interrupted["cache_key"]
    assert len(resumed["rows"][0]["verification"]["levels"]) == 2
    assert (scenario_archive.stat().st_mtime_ns, file_hash(scenario_archive)) == previous
    for path in (second_temp / "etlayout-layouts").glob("*.gds"):
        path.unlink()
    third = _process(args, tmp_path / "third-process-temp")
    assert third.returncode == 0, third.stderr
    assert json.loads(third.stdout) == resumed


@pytest.mark.parametrize("change", ["modified", "missing"])
def test_fresh_cli_resume_rejects_changed_retained_source(tmp_path, change):
    project = example(tmp_path)
    project.power.experiments[0].max_evaluations = 1
    archive, directory = tmp_path / "embedded.etl", tmp_path / "search"
    save_project(project, archive)
    args = ["-m", "etlayout", "optimize", str(archive), "--experiment", "width",
            "--output", str(directory), "--cpu-threads", "1"]
    first = _process(args, tmp_path / "first-process-temp")
    assert first.returncode == 0, first.stderr
    history = json.loads(first.stdout)
    retained = Path(history["source_layout"]["path"])
    if change == "modified":
        retained.write_bytes(retained.read_bytes() + b"unexpected modification")
    else:
        retained.unlink()
    second = _process(args, tmp_path / "second-process-temp")
    assert second.returncode == 2
    assert "source layout artifact is missing or changed" in second.stderr
    assert json.loads((directory / "history.json").read_text()) == history
