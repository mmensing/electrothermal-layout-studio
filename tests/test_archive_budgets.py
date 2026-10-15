# SPDX-License-Identifier: MPL-2.0
import io
import json
import zipfile

import numpy as np
import pytest

from etlayout.io import project as archive_io
from etlayout.models import PlanarMesh, Project, SimulationError, SimulationResult


def stored_result():
    project = Project()
    mesh = PlanarMesh(np.array([[0., 0.], [1., 0.], [0., 1.]]), np.array([[0, 1, 2]]),
                      {"layer:a": np.array([0])})
    result = SimulationResult(mesh, fields={"a": {"temperature": np.array([300., 301., 302.])}},
                              converged=True, configuration=project.to_dict(),
                              configuration_hash=archive_io.configuration_hash(project),
                              execution={"backend": "cpu", "cpu_threads": 2})
    return project, result


def test_aggregate_run_budget_rejects_before_replacing_good_archive(tmp_path, monkeypatch):
    project, result = stored_result()
    target = tmp_path / "project.etl"
    archive_io.save_project(project, target, result)
    original = target.read_bytes()
    one = archive_io.estimate_project_archive(project, result)
    two = archive_io.estimate_project_archive(project, result, history=[result])
    assert two["estimated_uncompressed_bytes"] == 2 * one["estimated_uncompressed_bytes"]
    monkeypatch.setattr(archive_io, "MAX_ARCHIVE_BYTES", one["estimated_uncompressed_bytes"] + 500)
    assert not archive_io.estimate_project_archive(project, result, history=[result])["fits_budget"]
    with pytest.raises(SimulationError, match="Aggregate expanded"):
        archive_io.save_project(project, target, result, history=[result])
    assert target.read_bytes() == original
    assert not list(tmp_path.glob("*.tmp"))
    assert archive_io.load_project(target)[1].converged


def test_final_archive_check_preserves_existing_file_on_estimate_miss(tmp_path, monkeypatch):
    project, result = stored_result()
    target = tmp_path / "project.etl"
    archive_io.save_project(project, target, result)
    original = target.read_bytes()
    monkeypatch.setattr(archive_io, "estimate_project_archive", lambda *args, **kwargs: {"fits_budget": True})
    monkeypatch.setattr(archive_io, "MAX_ARCHIVE_BYTES", 100)
    with pytest.raises(SimulationError, match="budget"):
        archive_io.save_project(project, target, result)
    assert target.read_bytes() == original
    assert not list(tmp_path.glob("*.tmp"))


def test_nested_runs_share_reader_budget(tmp_path, monkeypatch):
    project, result = stored_result()
    target = tmp_path / "history.etl"
    archive_io.save_project(project, target, result, history=[result, result])
    exact = archive_io.inspect_archive_size(target)
    assert exact["uncompressed_bytes"] == archive_io.estimate_project_archive(project, result, [result, result])["estimated_uncompressed_bytes"]
    monkeypatch.setattr(archive_io, "MAX_ARCHIVE_BYTES", exact["uncompressed_bytes"] - 1)
    for load in (archive_io.load_project, archive_io.load_run_history):
        with pytest.raises(SimulationError, match="budget"):
            load(target)


@pytest.mark.parametrize("limit", ["MAX_METADATA_BYTES", "MAX_ARCHIVE_ENTRIES"])
def test_metadata_and_entry_preflight(tmp_path, monkeypatch, limit):
    project, result = stored_result()
    monkeypatch.setattr(archive_io, limit, 1)
    with pytest.raises(SimulationError, match="budget"):
        archive_io.save_project(project, tmp_path / "oversized.etl", result)
    assert not (tmp_path / "oversized.etl").exists()


def test_npz_header_cannot_claim_unallocated_array(tmp_path):
    header = io.BytesIO()
    np.lib.format.write_array_header_1_0(header, {"descr": "<f8", "fortran_order": False, "shape": (100_000_000,)})
    payload = io.BytesIO()
    with zipfile.ZipFile(payload, "w", zipfile.ZIP_DEFLATED) as nested:
        nested.writestr("points.npy", header.getvalue())
    target = tmp_path / "bad.etl"
    with zipfile.ZipFile(target, "w") as archive:
        archive.writestr("results/arrays.npz", payload.getvalue())
    with pytest.raises(SimulationError, match="shape does not match"):
        archive_io.load_project(target)


def test_execution_roundtrip_and_legacy_capacity_identity(tmp_path):
    project, result = stored_result()
    target = tmp_path / "execution.etl"
    archive_io.save_project(project, target, result)
    _, loaded = archive_io.load_project(target)
    assert loaded.execution == result.execution
    project.transient.enabled = True
    legacy = json.loads(json.dumps(project.to_dict()))
    legacy["transient"].pop("capacity_scheme", None)
    assert archive_io.physics_hash(legacy) == archive_io.physics_hash(project)
