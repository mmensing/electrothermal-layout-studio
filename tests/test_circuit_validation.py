# SPDX-License-Identifier: MPL-2.0
"""Generated circuit contracts and failure handling; real simulator checks are opt-in."""
import hashlib
import json
import subprocess
import sys
from copy import deepcopy
from pathlib import Path

import numpy as np
import psutil
import pytest
from solver_helpers import rectangle

from etlayout.models import Cancelled, PhysicalPort, PortLanding, SimulationError
from etlayout.power.circuit_validation import (
    _compare_currents,
    prepare_circuit_validation,
    validate_circuit_network,
)
from etlayout.power.multiport import extract_multiport


@pytest.fixture
def passive_report():
    project, mesh = rectangle(nx=5, ny=2)
    project.power.ports = [PhysicalPort("positive", [PortLanding("right", .1)]),
                           PhysicalPort("negative", [PortLanding("left", .3)], kind="ground")]
    return extract_multiport(project, mesh)


def test_generated_bundle_is_deterministic_complete_and_preserves_input(tmp_path, passive_report):
    original = deepcopy(passive_report)
    first = prepare_circuit_validation(passive_report, tmp_path / "first")
    second = prepare_circuit_validation(passive_report, tmp_path / "second")
    assert first == second and passive_report == original
    assert first["status"] == "prepared" and first["absolute_tolerance_A"] == 0
    assert first["relative_tolerance"] <= 1e-8
    assert first["provenance"]["physics_hash"] == passive_report["physics_hash"]
    for name, metadata in first["files"].items():
        data = (tmp_path / "first" / name).read_bytes()
        assert data == (tmp_path / "second" / name).read_bytes()
        assert metadata == {"sha256": hashlib.sha256(data).hexdigest(), "bytes": len(data)}
    for index, case in enumerate(first["cases"]):
        assert case["expected_entering_currents_A"] == pytest.approx(np.array([1., -1.]) * (-1)**index / .6)
        deck = (tmp_path / "first" / case["deck"]).read_text()
        assert f"V1 n1 0 DC {int(index == 0)}" in deck
        assert f"V2 n2 0 DC {int(index == 1)}" in deck
        assert "Xdut n1 n2 ETLAYOUT" in deck
        assert "print i(V1) i(V2)" in deck
        assert ".include network.cir" in deck
    assert json.loads((tmp_path / "first" / "manifest.json").read_bytes()) == first


def test_nonempty_or_tampered_bundle_cannot_be_executed_or_overwritten(tmp_path, passive_report, monkeypatch):
    output = tmp_path / "bundle"
    prepare_circuit_validation(passive_report, output)
    (output / "network.cir").write_text(".control\nshell forbidden\n.endc\n")
    monkeypatch.setattr(subprocess, "Popen", lambda *_a, **_k: pytest.fail("An existing bundle must never execute"))
    with pytest.raises(SimulationError, match="new or empty"):
        validate_circuit_network(passive_report, output, executable=sys.executable)
    assert "forbidden" in (output / "network.cir").read_text()


def test_empty_output_directory_is_supported_and_cancel_leaves_no_partial_bundle(tmp_path, passive_report):
    empty = tmp_path / "empty"
    empty.mkdir()
    assert prepare_circuit_validation(passive_report, empty)["status"] == "prepared"
    cancelled = tmp_path / "cancelled"
    with pytest.raises(Cancelled):
        prepare_circuit_validation(passive_report, cancelled, cancel=lambda: True)
    assert not cancelled.exists()


def _current_block(values):
    return "ETLAYOUT_CURRENT_BEGIN\n" + "\n".join(f"i(v{i+1}) = {-value:.17g}" for i, value in enumerate(values)) + "\nETLAYOUT_CURRENT_END\n"


def test_current_acceptance_checks_every_weak_transfer_without_global_absolute_allowance():
    expected = [2., -2. + 1e-12, -1e-12, 0.]
    actual = expected.copy()
    exact = _compare_currents(_current_block(actual), expected, ["a", "b", "weak", "zero"])
    assert all(row["passed"] for row in exact)
    actual[2] = 0.
    actual[3] = 1e-30
    damaged = _compare_currents(_current_block(actual), expected, ["a", "b", "weak", "zero"])
    assert damaged[0]["passed"] and damaged[1]["passed"]
    assert not damaged[2]["passed"] and damaged[2]["relative_error"] == 1.
    assert not damaged[3]["passed"] and damaged[3]["relative_error"] is None


@pytest.mark.parametrize("damage", ["missing", "duplicate", "nonfinite", "unexpected", "unmarked", "reversed"])
def test_current_records_are_complete_finite_and_unambiguous(damage):
    block = _current_block([1., -1.])
    if damage == "missing":
        block = block.replace("i(v2) = 1\n", "")
    elif damage == "duplicate":
        block = block.replace("i(v2) = 1", "i(v1) = 1")
    elif damage == "nonfinite":
        block = block.replace("i(v2) = 1", "i(v2) = 1e999")
    elif damage == "unexpected":
        block = block.replace("i(v2) = 1", "unexpected record")
    elif damage == "reversed":
        block = block.replace("ETLAYOUT_CURRENT_BEGIN", "TEMP").replace("ETLAYOUT_CURRENT_END", "ETLAYOUT_CURRENT_BEGIN").replace("TEMP", "ETLAYOUT_CURRENT_END")
    else:
        block = block.replace("ETLAYOUT_CURRENT_END", "")
    with pytest.raises(SimulationError):
        _compare_currents(block, [1., -1.], ["a", "b"])


@pytest.mark.parametrize("keyword,value", [("timeout", 0), ("timeout", float("inf")),
                                           ("max_output_bytes", 1), ("max_output_bytes", True)])
def test_external_limits_are_validated_before_any_files_or_processes(tmp_path, passive_report, keyword, value):
    with pytest.raises(SimulationError, match="timeout|output limit"):
        validate_circuit_network(passive_report, tmp_path / "output", executable=sys.executable, **{keyword: value})
    assert not (tmp_path / "output").exists()


def test_selected_simulator_must_be_an_explicit_existing_absolute_file(tmp_path, passive_report):
    for executable in ("ngspice", str(tmp_path / "missing.exe")):
        with pytest.raises(SimulationError, match="absolute ngspice"):
            validate_circuit_network(passive_report, tmp_path / "output", executable=executable)
    assert not (tmp_path / "output").exists()


@pytest.mark.parametrize("behavior,status", [("exit", "execution_failed"), ("sleep", "timeout"),
                                             ("flood", "output_limit"), ("cancel", "cancelled")])
def test_real_child_failures_are_bounded_without_simulating_scientific_success(
        tmp_path, passive_report, monkeypatch, behavior, status):
    # The small real child only exits, sleeps or emits junk: no numeric output
    # is substituted for a circuit simulation and no scientific pass is mocked.
    fake = tmp_path / "failure_child.py"
    fake.write_text("import sys,time\n" +
                    ("raise SystemExit(7)\n" if behavior == "exit" else
                     "sys.stdout.write('x'*200000);sys.stdout.flush();time.sleep(1)\n" if behavior == "flood" else
                     "time.sleep(5)\n"))
    original = subprocess.Popen
    launched = []

    def launch(command, **options):
        assert command[1:3] == ["-n", "-b"] and Path(command[3]).name == command[3]
        assert options["shell"] is False and options["stdin"] == subprocess.DEVNULL
        assert "PYTHONPATH" not in options["env"] and "GH_TOKEN" not in options["env"]
        assert options["env"]["OMP_NUM_THREADS"] == "1"
        assert Path(options["cwd"]).resolve() != (tmp_path / "output").resolve()
        child = original([sys.executable, str(fake)], **options)
        launched.append(child)
        return child

    monkeypatch.setattr(subprocess, "Popen", launch)
    kwargs = {"timeout": .15 if behavior == "sleep" else 5., "max_output_bytes": 1024}
    if behavior == "cancel":
        kwargs["cancel"] = lambda: bool(launched)
        with pytest.raises(Cancelled):
            validate_circuit_network(passive_report, tmp_path / "output", executable=sys.executable, **kwargs)
        observed = json.loads((tmp_path / "output" / "validation-result.json").read_bytes())
    else:
        observed = validate_circuit_network(passive_report, tmp_path / "output", executable=sys.executable, **kwargs)
    assert observed["status"] == status
    assert all(child.poll() is not None for child in launched)
    if status == "output_limit":
        assert (tmp_path / "output" / "basis-001.log").stat().st_size <= 1024
    assert not any(row.get("status") == "passed" for row in observed["cases"])


def test_launcher_descendants_are_terminated_even_after_launcher_exits(tmp_path, passive_report, monkeypatch):
    fake = tmp_path / "launcher.py"
    child_pid = tmp_path / "child-pid.txt"
    fake.write_text("import subprocess,sys\nfrom pathlib import Path\n"
                    "child=subprocess.Popen([sys.executable,'-c','import time; time.sleep(20)'])\n"
                    f"Path({str(child_pid)!r}).write_text(str(child.pid))\n")
    original = subprocess.Popen

    def launch(_command, **options):
        return original([sys.executable, str(fake)], **options)

    monkeypatch.setattr(subprocess, "Popen", launch)
    result = validate_circuit_network(passive_report, tmp_path / "output", executable=sys.executable)
    assert result["status"] == "failed" and "current block" in result["cases"][0]["diagnostic"]
    identifier = int(child_pid.read_text())
    try:
        process = psutil.Process(identifier)
    except psutil.NoSuchProcess:
        return
    assert process.status() == psutil.STATUS_ZOMBIE or not process.is_running()


def test_extreme_finite_simulator_disagreement_remains_a_serializable_failure():
    rows = _compare_currents(_current_block([1e308, -1e308]), [-1e308, 1e308], ["a", "b"])
    assert not any(row["passed"] for row in rows)
    assert all(row["relative_error"] is None for row in rows)
    json.dumps(rows, allow_nan=False)


def test_bundle_preserves_original_source_and_temperature_context(tmp_path, passive_report):
    report = deepcopy(passive_report)
    report["source_physics_hash"] = "original-physics"
    report["source_configuration_hash"] = "original-configuration"
    report["extraction_context"] = {"temperature_mode": "compatible_fields", "source_run": "retained"}
    manifest = prepare_circuit_validation(report, tmp_path / "bundle")
    for key in ("source_physics_hash", "source_configuration_hash", "extraction_context"):
        assert manifest["provenance"][key] == report[key]
    assert manifest["provenance"]["physics_hash"] == passive_report["physics_hash"]
