# SPDX-License-Identifier: MPL-2.0
"""Independent performance receipts must not conceal persistence changes."""
import importlib.util
import json
import sys
from copy import deepcopy
from pathlib import Path

import numpy as np
import pytest

from etlayout.models import Diagnostic, PlanarMesh, SimulationResult, TransientHistory

SPEC = importlib.util.spec_from_file_location(
    "archive_benchmark", Path(__file__).resolve().parents[1] / "scripts/benchmark_archive_history.py")
benchmark = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(benchmark)


def result():
    mesh = PlanarMesh(np.array([[0., 0.], [1., 0.], [0., 1.]]), np.array([[0, 1, 2]]),
                      {"layer:a": np.array([0])}, statistics={"provenance": "reference"})
    return SimulationResult(mesh, fields={"a": {"temperature": np.array([300., 301., 302.])}},
                            diagnostics=[Diagnostic("warning", "stored diagnosis")],
                            execution={"threads": 4}, configuration={"layout": {"sha256": "a" * 64, "path": "old.gds"}},
                            analysis={"supported": True})


@pytest.mark.parametrize("field,value", [
    ("metrics", {"current": 1.}), ("terminals", {"source": {"voltage": .01}}),
    ("diagnostics", [Diagnostic("error", "changed diagnosis")]),
    ("iterations", [{"iteration": 1, "residual": 1e-9}]), ("converged", True),
    ("configuration", {"layout": {"sha256": "b" * 64, "path": "old.gds"}}),
    ("configuration_hash", "new configuration"), ("elapsed_seconds", 1.), ("solver_version", "other"),
    ("physics_hash", "new physics"), ("transient", TransientHistory(rows=[{"time_s": 0., "accepted": True}])),
    ("execution", {"threads": 8}), ("element_fields", {"a": {"potential": np.array([[0., 1., 2.]])}}),
    ("interface_fields", {"via": {"current": np.array([.1])}}),
    ("analysis", {"supported": False}), ("field_version", 1),
])
def test_typed_fingerprint_covers_complete_result_metadata(field, value):
    original = result()
    changed = deepcopy(original)
    setattr(changed, field, value)
    assert benchmark.result_digest(original) != benchmark.result_digest(changed)


def test_mesh_support_and_snapshot_metadata_are_part_of_fingerprint():
    original = result()
    original.transient = TransientHistory(np.array([0.]), [result()], [{"time_s": 0.}], False)
    changed = deepcopy(original)
    changed.mesh.boundary_edges["sink"] = np.array([[0, 1]])
    assert benchmark.result_digest(original) != benchmark.result_digest(changed)
    changed = deepcopy(original)
    changed.transient.snapshots[0].execution["threads"] = 1
    assert benchmark.result_digest(original) != benchmark.result_digest(changed)
    changed = deepcopy(original)
    changed.mesh.statistics["provenance"] = "changed"
    assert benchmark.result_digest(original) != benchmark.result_digest(changed)


@pytest.mark.parametrize("left,right", [
    (1, 1.), (True, 1), ([1], (1,)), ({1: "value"}, {"1": "value"}),
    (np.float32(1), np.float64(1)), (0., -0.),
    (np.array([1.], dtype="<f8"), np.array([1.], dtype=">f8")),
    (np.array([1, 2]), np.array([[1, 2]])),
    (np.array([0x7ff8000000000001], dtype=np.uint64).view(np.float64),
     np.array([0x7ff8000000000002], dtype=np.uint64).view(np.float64)),
])
def test_typed_fingerprint_does_not_coerce_values(left, right):
    assert benchmark.exact_digest(left) != benchmark.exact_digest(right)


def test_fingerprint_normalizes_only_identified_source_location():
    original = result()
    changed = deepcopy(original)
    changed.configuration["layout"]["path"] = "elsewhere.gds"
    assert benchmark.result_digest(original) == benchmark.result_digest(changed)
    original.configuration["layout"]["sha256"] = ""
    changed.configuration["layout"]["sha256"] = ""
    assert benchmark.result_digest(original) != benchmark.result_digest(changed)


@pytest.mark.parametrize("name", ["../outside.etl", "inputs/../../outside.etl", "C:/outside.etl"])
def test_reused_input_manifest_cannot_read_outside_root(tmp_path, name):
    source, target = tmp_path / "source", tmp_path / "target"
    source.mkdir()
    target.mkdir()
    (source / "inputs.json").write_text(json.dumps({"shared": [{"file": name}]}), encoding="utf-8")
    with pytest.raises(RuntimeError, match="unsafe file name"):
        benchmark.copy_inputs(source, target, ["shared"])
    assert not list(target.rglob("*.etl"))


def test_summary_retains_failed_observation_and_reports_ranges():
    rows = [{"family": "shared", "total_retained_records": 10, "archive_format": 2,
             "save_seconds": value, "round_trip_exact": exact}
            for value, exact in [(2., True), (4., False), (3., True)]]
    summary, = benchmark.summarize(rows)
    assert summary["all_exact"] is False
    assert summary["observations"] == 3
    assert summary["measurements"]["save_seconds"] == {"median": 3., "min": 2., "max": 4.}


@pytest.mark.parametrize("option,value", [("--max-rss-gib", "nan"), ("--max-rss-gib", "0"),
                                          ("--timeout", "inf"), ("--timeout", "-1")])
def test_nonfinite_or_nonpositive_budgets_are_rejected_before_output(tmp_path, option, value):
    target = tmp_path / "not-created"
    with pytest.raises(SystemExit) as error:
        benchmark.main(["--output", str(target), option, value])
    assert error.value.code == 2
    assert not target.exists()


@pytest.mark.parametrize("limit,budgets", [("peak RSS", {"max_rss_gib": .00001, "timeout": 5.}),
                                          ("wall time", {"max_rss_gib": 3., "timeout": .05})])
def test_real_resource_limit_is_retained_as_failure(tmp_path, limit, budgets):
    receipt = benchmark.observe([sys.executable, "-c", "import time; time.sleep(10)"],
                                tmp_path / "bounded-owned-process", **budgets)
    assert receipt["status"] == "resource-limited"
    assert receipt["limitation"] == limit
    assert receipt["exit_code"] != 0
    assert not receipt.get("round_trip_exact", False)
    assert (tmp_path / "bounded-owned-process.stderr.log").is_file()
