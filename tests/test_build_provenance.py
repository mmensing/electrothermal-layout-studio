# SPDX-License-Identifier: MPL-2.0
"""Build labels describe recorded implementations without changing physics."""
import hashlib
import json
import runpy
import subprocess
import sys
from pathlib import Path

import pytest

from etlayout import __version__
from etlayout.cli.main import main
from etlayout.provenance import build_info, dependency_versions, implementation_identity, result_build_info


def test_source_receipt_and_cli_are_safe_and_non_qt(capsys):
    info = build_info()
    assert info["version"] == __version__
    assert info["edition"] == "source"
    assert info["identity_status"] == "available"
    assert len(info["implementation_sha256"]) == 64
    assert all(info["dependencies"].values())
    assert set(info["runtime"]) == {"python", "implementation", "system", "release", "machine"}
    assert main(["build-info"]) == 0
    assert json.loads(capsys.readouterr().out) == info
    result = subprocess.run([sys.executable, "-c", "from etlayout.provenance import build_info; import sys; build_info(); assert not any(k.startswith('PySide6') for k in sys.modules)"], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    serialized = json.dumps(info)
    assert str(Path.home()) not in serialized
    assert "hostname" not in serialized


def test_missing_optional_metadata_is_explicit():
    assert dependency_versions(("a-distribution-that-is-not-installed",)) == {"a-distribution-that-is-not-installed": None}


@pytest.mark.parametrize("frozen", [False, True])
def test_implementation_digest_matches_independent_source_stamp(tmp_path, frozen):
    package = tmp_path / "etlayout"
    (package / "data").mkdir(parents=True)
    (package / "a.py").write_bytes(b"a = 1\n")
    (package / "sub").mkdir()
    (package / "sub/b.py").write_bytes(b"b = 2\r\n")
    files = {name: hashlib.sha256((package / name).read_bytes()).hexdigest() for name in ("a.py", "sub/b.py")}
    expected = hashlib.sha256(json.dumps(files, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    (package / "data/implementation.json").write_text(json.dumps({"source_sha256": expected}), encoding="utf-8")
    assert implementation_identity(package, frozen=frozen) == expected
    (package / "data/unrelated.bin").write_bytes(b"not implementation")
    assert implementation_identity(package, frozen=frozen) == expected
    if not frozen:
        (package / "a.py").write_bytes(b"a = 2\n")
        assert implementation_identity(package, frozen=False) != expected


def test_frozen_spec_stamps_version_and_copies_runtime_metadata():
    # Inspect the maintained spec contract without running a frozen build.
    # The independent digest and real archive tests above exercise identity values.
    spec = Path(__file__).resolve().parents[1] / "packaging/etlayout.spec"
    source = spec.read_text(encoding="utf-8")
    assert '"version": runpy.run_path(str(package / "__init__.py"))["__version__"]' in source
    assert '"source_sha256"' in source
    for name in dependency_versions():
        assert f'"{name}"' in source


@pytest.mark.parametrize("archive_format", [2, 3])
@pytest.mark.numerical
def test_real_run_provenance_survives_archives_and_reports_without_relabeling(tmp_path, archive_format):
    from etlayout.examples import make_example
    from etlayout.io.export import export_results
    from etlayout.io.project import configuration_hash, load_project, physics_hash, save_project
    from etlayout.simulation.runner import run_project

    project = make_example("E1", tmp_path / "inputs")
    before_physics = physics_hash(project)
    before_configuration = configuration_hash(project)
    result = run_project(project, execution={"cpu_threads": 1, "study_workers": 1})
    assert result.converged
    assert result.metrics["resistance"] == pytest.approx(.084, rel=1e-8)
    assert physics_hash(project) == before_physics
    assert configuration_hash(project) == before_configuration
    original = result_build_info(result)
    assert original["implementation_sha256"] == implementation_identity()
    save_project(project, tmp_path / "new.etl", result, archive_format=archive_format)
    _, loaded = load_project(tmp_path / "new.etl")
    assert result_build_info(loaded) == original
    copied = result_build_info(loaded)
    copied["version"] = "changed detached copy"
    assert result_build_info(loaded) == original
    export_results(loaded, tmp_path / "new-report", plots=False)
    metadata = json.loads((tmp_path / "new-report/results.json").read_text(encoding="utf-8"))
    assert metadata["build"] == original
    assert original["implementation_sha256"] in (tmp_path / "new-report/report.html").read_text(encoding="utf-8")
    loaded.execution.pop("build")
    loaded.solver_version = "0.1.0"
    save_project(project, tmp_path / "legacy.etl", loaded, archive_format=archive_format)
    _, legacy = load_project(tmp_path / "legacy.etl")
    missing = result_build_info(legacy)
    assert missing["identity_status"] == "unavailable" and missing["solver_version"] == "0.1.0"
    assert "implementation_sha256" not in missing
    export_results(legacy, tmp_path / "old-report", plots=False)
    assert "predates recorded build provenance" in (tmp_path / "old-report/report.html").read_text(encoding="utf-8")
    assert "build" not in legacy.execution


def test_cpu_comparison_checks_all_supports_and_detects_changes(tmp_path):
    import numpy as np

    script = runpy.run_path(str(Path(__file__).resolve().parents[1] / "scripts/benchmark_cpu.py"))
    first, second = tmp_path / "one.npz", tmp_path / "four.npz"
    np.savez(first, **{"fields/a/temperature": [300., 301.], "interface_fields/via/current": [1.]})
    np.savez(second, **{"fields/a/temperature": [300., 301.], "interface_fields/via/current": [1.]})
    assert all(item["equivalent"] for item in script["compare_fields"](first, second).values())
    np.savez(second, **{"fields/a/temperature": [300., 301.], "interface_fields/via/current": [2.]})
    assert not script["compare_fields"](first, second)["interface_fields/via/current"]["equivalent"]


@pytest.mark.parametrize("stamp,match", [(None, "lacks implementation"),
    ({"source_sha256": "z"*64}, "invalid"),
    ({"source_sha256": "a"*64, "version": "9.9.9"}, "version differs")])
def test_frozen_current_build_refuses_missing_invalid_or_inconsistent_stamp(tmp_path, monkeypatch, stamp, match):
    from etlayout import provenance
    from etlayout.models import SimulationError
    package = tmp_path / "etlayout"
    (package / "data").mkdir(parents=True)
    if stamp is not None:
        (package / "data/implementation.json").write_text(json.dumps(stamp), encoding="utf-8")
    monkeypatch.setattr(provenance, "__file__", str(package / "provenance.py"))
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    with pytest.raises(SimulationError, match=match):
        provenance.build_info()


def test_explicit_sharing_identity_never_copies_arbitrary_saved_metadata():
    from types import SimpleNamespace

    from etlayout.provenance import shared_result_identity
    result = SimpleNamespace(solver_version="old", execution={"build": {"version": "0.4.1",
        "implementation_sha256": "a"*64, "private_path": "unshared", "dependencies": {"secret": "unshared"}}})
    assert shared_result_identity(result) == {"identity_status": "available", "version": "0.4.1", "implementation_sha256": "a"*64}
    result.execution["build"]["version"] = "private layout name"
    assert "version" not in shared_result_identity(result)
    result.execution["build"]["implementation_sha256"] = "not a digest"
    assert shared_result_identity(result) == {"identity_status": "unavailable"}


@pytest.mark.numerical
def test_result_cache_is_scoped_to_recorded_implementation(tmp_path, monkeypatch):
    from etlayout import provenance
    from etlayout.examples import make_example
    from etlayout.simulation.runner import clear_caches, run_project
    project = make_example("E1", tmp_path / "inputs")
    clear_caches()
    first = run_project(project, use_cache=True, execution={"cpu_threads": 1, "study_workers": 1})
    repeated = run_project(project, use_cache=True, execution={"cpu_threads": 1, "study_workers": 1})
    assert repeated.execution["linear_solves"]["cpu"] == 0
    assert repeated.execution["timings_seconds"]["cached_result_load"] >= 0
    changed = build_info()
    changed["implementation_sha256"] = "b"*64
    monkeypatch.setattr(provenance, "implementation_identity", lambda *args, **kwargs: changed["implementation_sha256"])
    rerun = run_project(project, use_cache=True, execution={"cpu_threads": 1, "study_workers": 1})
    assert rerun.execution["linear_solves"]["cpu"] > 0
    assert rerun.execution["build"]["implementation_sha256"] == "b"*64
    assert first.metrics["resistance"] == repeated.metrics["resistance"] == rerun.metrics["resistance"]
    assert first.physics_hash == repeated.physics_hash == rerun.physics_hash
    clear_caches()


@pytest.mark.parametrize("changed", ["service", "conservation", "integral"])
def test_cpu_receipt_rejects_wrong_service_or_balance_despite_equal_fields(changed):
    from copy import deepcopy

    from etlayout.models import Project
    script = runpy.run_path(str(Path(__file__).resolve().parents[1] / "scripts/benchmark_cpu.py"))
    row = {"ports": {"drain": {"current": 5.}}, "metrics": {"terminal_power": 2.,
           "current_balance_error": 1e-10, "power_balance_error": 1e-10, "thermal_balance_error": 1e-10}}
    other = deepcopy(row)
    assert script["compare_observables"]([row, other], Project(), 5.)["accepted"]
    if changed == "service":
        other["ports"]["drain"]["current"] = 4.
    elif changed == "conservation":
        other["metrics"]["power_balance_error"] = .1
    else:
        other["metrics"]["terminal_power"] = 1.
    assert not script["compare_observables"]([row, other], Project(), 5.)["accepted"]


def test_cpu_benchmark_alternates_repeated_pairs_and_compares_every_repetition():
    from copy import deepcopy

    from etlayout.models import Project

    script = runpy.run_path(str(Path(__file__).resolve().parents[1] / "scripts/benchmark_cpu.py"))
    assert script["run_plan"](3) == [(1, 1), (1, 4), (2, 4), (2, 1), (3, 1), (3, 4)]
    row = {"ports": {"drain": {"current": 5.}}, "metrics": {"terminal_power": 2.,
           "current_balance_error": 1e-10, "power_balance_error": 1e-10, "thermal_balance_error": 1e-10}}
    rows = [deepcopy(row) for _ in range(4)]
    assert script["compare_observables"](rows, Project(), 5.)["accepted"]
    rows[-1]["metrics"]["terminal_power"] = 2.1
    assert not script["compare_observables"](rows, Project(), 5.)["accepted"]


@pytest.mark.parametrize("mode", ["electrical", "thermal", "electrothermal"])
def test_cpu_receipt_requires_the_conservation_equations_actually_solved(mode):
    from etlayout.models import Project

    script = runpy.run_path(str(Path(__file__).resolve().parents[1] / "scripts/benchmark_cpu.py"))
    project = Project()
    project.solver.mode = mode
    row = {"metrics": {"temperature_max": 300., "terminal_power": 2.,
           "current_balance_error": 1e-10, "power_balance_error": 1e-10}}
    # A missing thermal balance is legitimate only for electrical-only runs.
    assert script["compare_observables"]([row, row], project)["accepted"] == (mode == "electrical")
    row["metrics"]["thermal_balance_error"] = 1e-10
    assert script["compare_observables"]([row, row], project)["accepted"]
    if mode == "thermal":
        del row["metrics"]["current_balance_error"]
        assert script["compare_observables"]([row, row], project)["accepted"]
        project.solver.thermal_include_joule = True
        assert not script["compare_observables"]([row, row], project)["accepted"]
    else:
        row["metrics"]["power_balance_error"] = .1
        assert not script["compare_observables"]([row, row], project)["accepted"]
