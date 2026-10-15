# SPDX-License-Identifier: MPL-2.0
import csv
import json

import pytest
from test_transient import thermal_fixture

from etlayout.io.project import load_project, project_from_dict, save_project
from etlayout.io.transient_export import export_transient_history
from etlayout.solvers import solve


def test_resumed_export_explains_source_energy_and_segment_scopes(tmp_path):
    project, mesh = thermal_fixture(dt=.1, stop=.2)
    source = solve(project, mesh)
    project.transient.stop_time = .4
    resumed = solve(project, mesh, initial_result=source, resume=True)
    resumed.execution = {"backend": "cpu", "cpu_threads": 2}
    for frame in resumed.transient.snapshots:
        frame.execution = resumed.execution.copy()
    files = export_transient_history(resumed, tmp_path, plots=False)
    assert "transient_history.metadata.json" in files
    data = json.loads((tmp_path / "transient_history.json").read_text())
    sidecar = json.loads((tmp_path / "transient_history.metadata.json").read_text())
    context = data["context"]
    assert sidecar["context"] == context
    assert context["segment_start_time_s"] == .2
    assert context["segment_end_time_s"] == .4
    assert context["requested_stop_time_s"] == .4
    assert context["energy_scope"] == "cumulative_from_origin"
    assert context["peak_scope"] == "current_segment"
    assert context["initialization"]["configuration_hash"] == source.configuration_hash
    assert context["initialization"]["source_time_s"] == .2
    assert context["initial_input_energy_J"] == pytest.approx(source.metrics["transient_input_energy"])
    assert context["stop_reason"] == "stop_time"
    assert data["execution"] == resumed.execution
    with (tmp_path / "transient_history.csv").open(encoding="utf-8-sig") as stream:
        row = next(csv.DictReader(stream))
    assert float(row["input_energy_J"]) == context["initial_input_energy_J"]
    save_project(project_from_dict(resumed.configuration), tmp_path / "resumed.etl", resumed)
    _, loaded = load_project(tmp_path / "resumed.etl")
    assert loaded.execution == resumed.execution
    assert all(frame.execution == resumed.execution for frame in loaded.transient.snapshots)
