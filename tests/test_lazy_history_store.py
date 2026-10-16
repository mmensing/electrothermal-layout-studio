# SPDX-License-Identifier: MPL-2.0
"""Real archive reads: catalog I/O, exact frames, cache leases and file ownership."""
from __future__ import annotations

import gc
import json
import os
import threading
import zipfile
from copy import deepcopy
from dataclasses import FrozenInstanceError

import numpy as np
import pytest

from etlayout.io.history_store import (
    CacheBudgetExceeded,
    HistoryReadCancelled,
    open_history_catalog,
)
from etlayout.io.project import configuration_hash, physics_hash, save_project
from etlayout.models import PlanarMesh, Project, SimulationError, SimulationResult, TransientHistory


def small_result(name="working", temperature=300.):
    project = Project(name=name)
    mesh = PlanarMesh(np.array([[0., 0.], [1., 0.], [0., 1.]]), np.array([[0, 1, 2]]),
                      {"layer:m": np.array([0])})
    values = np.array([temperature, temperature + 1., temperature + 2.])
    result = SimulationResult(mesh, fields={"m": {"temperature": values}},
        element_fields={"m": {"temperature": values.reshape(1, 3)}},
        metrics={"temperature_max": temperature + 2.}, converged=True,
        configuration=project.to_dict(), configuration_hash=configuration_hash(project),
        physics_hash=physics_hash(project), elapsed_seconds=.25,
        execution={"cpu_threads": 4}, analysis={"support_version": 2})
    return project, result


@pytest.fixture
def saved_history(tmp_path):
    project, current = small_result()
    _, older = small_result("older", 310.)
    _, oldest = small_result("oldest", 320.)
    path = tmp_path / "history.etlayout"
    save_project(project, path, current, history=[older, oldest], archive_format=3)
    return path, (current, older, oldest)


def track_arrays(monkeypatch):
    opened = []
    real = zipfile.ZipFile.open
    def observed(self, name, *args, **kwargs):
        member = name.filename if isinstance(name, zipfile.ZipInfo) else name
        if member.endswith(".npy"):
            opened.append(member)
        return real(self, name, *args, **kwargs)
    monkeypatch.setattr(zipfile.ZipFile, "open", observed)
    return opened


def test_catalog_opens_no_array_payload_and_records_are_immutable(saved_history, monkeypatch):
    path, expected = saved_history
    opened = track_arrays(monkeypatch)
    with open_history_catalog(path) as store:
        assert opened == []
        assert [record.name for record in store.records] == ["working", "older", "oldest"]
        assert len({record.run_id for record in store.records}) == 3
        assert store.records[0].current and store.current_run_id == store.records[0].run_id
        assert [record.name for record in store.history] == ["older", "oldest"]
        assert store.records[0].configuration_copy() == expected[0].configuration
        assert store.records[0].converged  # Stored convergence, not loaded-field validation.
        assert store.stats.resident_bytes == store.stats.pinned_bytes == 0
        with pytest.raises(FrozenInstanceError):
            store.records[0].name = "changed"
        with pytest.raises(TypeError):
            store.records[0].configuration["name"] = "changed"
        independent = store.records[0].configuration_copy()
        independent["solver"]["mode"] = "thermal"
        assert store.records[0].configuration_copy() == expected[0].configuration


def test_leases_share_only_immutable_array_storage_and_isolate_metadata(saved_history):
    path, expected = saved_history
    with open_history_catalog(path) as store:
        first = store.acquire(store.current_run_id)
        second = store.acquire(store.history[0].run_id)
        try:
            assert isinstance(first.result, SimulationResult)
            np.testing.assert_array_equal(first.result.fields["m"]["temperature"],
                                          expected[0].fields["m"]["temperature"])
            np.testing.assert_array_equal(second.result.fields["m"]["temperature"],
                                          expected[1].fields["m"]["temperature"])
            assert np.shares_memory(first.result.mesh.points, second.result.mesh.points)
            assert first.result.mesh.points is not second.result.mesh.points
            with pytest.raises(ValueError):
                first.result.mesh.points.setflags(write=True)
            first.result.configuration["name"] = "view-local"
            assert second.result.configuration["name"] == "older"
            assert store.record(store.current_run_id).configuration_copy()["name"] == "working"
            # Shared mesh/field aliases consume one allocation per unique object.
            expected_bytes = (expected[0].mesh.points.nbytes + expected[0].mesh.triangles.nbytes
                              + expected[0].mesh.regions["layer:m"].nbytes
                              + 2 * expected[0].fields["m"]["temperature"].nbytes
                              + 2 * expected[0].element_fields["m"]["temperature"].nbytes)
            assert store.stats.resident_bytes == store.stats.pinned_bytes == expected_bytes
            assert store.stats.hits > 0
            store.clear()
            assert store.stats.resident_bytes == expected_bytes
        finally:
            first.close()
            second.close()
        store.clear()
        assert store.stats.resident_bytes == store.stats.pinned_bytes == 0


def test_budget_rejection_precedes_field_reads_and_oversize_is_explicit(saved_history, monkeypatch):
    path, _ = saved_history
    opened = track_arrays(monkeypatch)
    with open_history_catalog(path, cache_bytes=1) as store:
        with pytest.raises(CacheBudgetExceeded) as rejection:
            store.acquire(store.current_run_id)
        assert rejection.value.required_bytes > 1 and opened == []
        assert store.stats.resident_bytes == store.stats.pinned_bytes == 0
        with store.acquire(store.current_run_id, allow_oversize=True) as result:
            assert result.converged and store.stats.over_budget_bytes > 0
        assert store.stats.resident_bytes <= store.stats.budget_bytes


def test_lru_evicts_unpinned_objects_and_reload_is_exact(saved_history):
    path, _ = saved_history
    with open_history_catalog(path) as store:
        with store.acquire(store.current_run_id) as result:
            before = result.fields["m"]["temperature"].copy()
            unique = store.stats.resident_bytes
        store.set_budget(unique)
        for record in store.history:
            with store.acquire(record.run_id):
                assert store.stats.resident_bytes <= unique
        misses = store.stats.misses
        with store.acquire(store.current_run_id) as result:
            np.testing.assert_array_equal(result.fields["m"]["temperature"], before)
        assert store.stats.misses > misses
        assert store.stats.resident_bytes <= unique


def test_native_source_replace_delete_and_new_generation_are_independent(saved_history, tmp_path):
    path, _ = saved_history
    with open_history_catalog(path) as original:
        owned_copy = original._snapshot_path
        changed_project, changed = small_result("replacement", 450.)
        replacement = tmp_path / "replacement.etlayout"
        save_project(changed_project, replacement, changed, archive_format=3)
        os.replace(replacement, path)  # Executed natively on Windows CI/local Windows.
        with open_history_catalog(path) as newer:
            assert newer.generation != original.generation
            assert newer.records[0].name == "replacement"
            path.unlink()
            with newer.acquire(newer.current_run_id) as result:
                assert result.fields["m"]["temperature"][0] == 450.
        with original.acquire(original.current_run_id) as result:
            assert result.fields["m"]["temperature"][0] == 300.
        assert owned_copy.exists()
    assert not owned_copy.parent.exists()


def test_close_cleans_owned_copy_without_invalidating_leased_arrays(saved_history):
    path, _ = saved_history
    store = open_history_catalog(path)
    lease = store.acquire(store.current_run_id)
    private = store._snapshot_path
    store.close()
    assert not private.parent.exists() and path.exists()
    assert lease.result.fields["m"]["temperature"][0] == 300.
    assert store.stats.closed and store.stats.pinned_bytes > 0
    with pytest.raises(HistoryReadCancelled):
        store.acquire(store.current_run_id)
    lease.close()
    lease.close()
    assert store.stats.resident_bytes == store.stats.pinned_bytes == 0
    store.close()


def test_materialize_returns_real_result_with_releasable_automatic_pin(saved_history):
    path, _ = saved_history
    with open_history_catalog(path) as store:
        result = store.materialize(store.current_run_id)
        assert type(result) is SimulationResult and store.stats.pinned_bytes > 0
        store.release_result(result)
        assert store.stats.pinned_bytes == 0
        another = store.materialize(store.current_run_id)
        assert store.stats.pinned_bytes > 0
        del another
        gc.collect()
        assert store.stats.pinned_bytes == 0


def test_cancelled_copy_is_removed_and_cancelled_read_does_not_poison_run(saved_history, tmp_path):
    path, _ = saved_history
    copies = tmp_path / "read-copies"
    copies.mkdir()
    with pytest.raises(HistoryReadCancelled):
        open_history_catalog(path, copy_directory=copies, cancel=lambda: True)
    assert list(copies.iterdir()) == []
    with open_history_catalog(path) as store:
        with pytest.raises(HistoryReadCancelled):
            store.acquire(store.current_run_id, cancel=lambda: True)
        assert store.record(store.current_run_id).available
        with store.acquire(store.current_run_id) as result:
            assert result.converged


def test_active_reader_cancellation_releases_reservations(saved_history, monkeypatch):
    path, _ = saved_history
    with open_history_catalog(path) as store:
        cancelled = threading.Event()
        real = store._reader.read_array
        calls = []
        def cancel_after_first(digest, **kwargs):
            array = real(digest, **kwargs)
            calls.append(digest)
            cancelled.set()
            return array
        monkeypatch.setattr(store._reader, "read_array", cancel_after_first)
        with pytest.raises(HistoryReadCancelled):
            store.acquire(store.current_run_id, cancel=cancelled.is_set)
        assert len(calls) == 1
        assert store.stats.pinned_bytes == store.stats.active_reads == 0
        assert store.record(store.current_run_id).available
        monkeypatch.setattr(store._reader, "read_array", real)
        with store.acquire(store.current_run_id) as result:
            assert result.converged


def test_exact_frame_avoids_unselected_frame_arrays(tmp_path, monkeypatch):
    project, result = small_result()
    project.transient.enabled = True
    project.transient.stop_time = 1.
    result.configuration = project.to_dict()
    result.configuration_hash = configuration_hash(project)
    result.physics_hash = physics_hash(project)
    first = deepcopy(result)
    first.metrics["time_s"] = 0.
    last = deepcopy(result)
    last.fields["m"]["temperature"] += 20.
    last.element_fields["m"]["temperature"] += 20.
    last.metrics["time_s"] = 1.
    first.mesh = last.mesh = result.mesh
    result.fields = last.fields
    result.element_fields = last.element_fields
    result.metrics = {"time_s": 1., "transient_completed": True}
    result.transient = TransientHistory(np.array([0., 1.]), [first, last],
                                       [{"time_s": 0., "time_step": 0.},
                                        {"time_s": 1., "time_step": 1.}], True)
    path = tmp_path / "transient.etlayout"
    save_project(project, path, result, archive_format=3)
    opened = track_arrays(monkeypatch)
    with open_history_catalog(path) as store:
        assert store.records[0].times == (0., 1.) and opened == []
        wanted = set(store._reader.required_arrays(store.current_run_id, 0))
        unwanted = set(store._reader.required_arrays(store.current_run_id, 1)) - wanted
        with store.acquire_frame(store.current_run_id, 0) as frame:
            assert frame.transient is None and frame.metrics["time_s"] == 0.
            np.testing.assert_array_equal(frame.fields["m"]["temperature"], first.fields["m"]["temperature"])
        assert opened and not any(digest in name for digest in unwanted for name in opened)
        for bad in (-1, True, 2):
            with pytest.raises(SimulationError):
                store.acquire_frame(store.current_run_id, bad)


@pytest.mark.parametrize("budget", [0, -1, True, 1.5, 8 * 1024**3 + 1])
def test_invalid_cache_budget_is_rejected_before_copy(saved_history, budget):
    path, _ = saved_history
    with pytest.raises(SimulationError, match="cache bytes"):
        open_history_catalog(path, cache_bytes=budget)


def test_save_source_lease_outlives_view_store_without_field_reads(saved_history, monkeypatch):
    path, _ = saved_history
    opened = track_arrays(monkeypatch)
    store = open_history_catalog(path)
    ids = tuple(item.run_id for item in store.records)
    source = store.capture_history(ids)
    assert [reference.run_id for reference in source.references] == list(ids)
    assert deepcopy(source.references) == source.references
    private = store._snapshot_path
    store.close()
    assert private.exists() and opened == []
    source.references[0].require_open()
    assert source.references[0].reader.required_arrays(ids[0])
    source.close()
    source.close()
    assert not private.parent.exists()
    with pytest.raises(SimulationError, match="source-generation lease"):
        source.references[0].require_open()


@pytest.mark.parametrize("damage", ["missing", "hash"])
def test_corrupt_run_is_unavailable_without_substituting_another_run(saved_history, tmp_path, damage):
    from etlayout.io.archive_v3 import ArchiveV3Reader

    path, _ = saved_history
    with ArchiveV3Reader(path) as reader:
        broken_id = reader.records[1]["id"]
        descriptor = reader._descriptor(broken_id)
        digest = descriptor["fields"]["m"]["temperature"]["$ref"]
        member = reader.object_name(digest)
    damaged = tmp_path / "damaged.etlayout"
    with zipfile.ZipFile(path) as original, zipfile.ZipFile(damaged, "w") as output:
        for entry in original.infolist():
            data = original.read(entry)
            if entry.filename == member:
                if damage == "missing":
                    continue
                data = data[:-1] + bytes([data[-1] ^ 1])
            output.writestr(entry, data)
    with open_history_catalog(damaged) as store:
        assert store.record(broken_id).available == (damage == "hash")
        with pytest.raises(SimulationError):
            store.acquire(broken_id)
        assert not store.record(broken_id).available
        assert store.stats.pinned_bytes == 0
        with store.acquire(store.current_run_id) as current:
            assert current.fields["m"]["temperature"][0] == 300.
        with store.acquire(store.history[1].run_id) as oldest:
            assert oldest.fields["m"]["temperature"][0] == 320.


def test_reviewed_inspection_is_bound_to_exact_original_bytes(saved_history, tmp_path):
    from etlayout.io.resources import inspect_project

    path, _ = saved_history
    reviewed = inspect_project(path)
    changed_project, changed = small_result("replacement", 450.)
    replacement = tmp_path / "replacement.etlayout"
    save_project(changed_project, replacement, changed, archive_format=3)
    os.replace(replacement, path)
    with open_history_catalog(reviewed) as store:
        assert store.records[0].name == "working"
        with store.acquire(store.current_run_id) as result:
            assert result.fields["m"]["temperature"][0] == 300.
    # No plain metadata field can authorize reopening a changed pathname.
    reviewed.archive_data = None
    reviewed.metadata["_archive_sha256"] = "0" * 64
    with pytest.raises(SimulationError, match="exact inspected"):
        open_history_catalog(reviewed)


def test_reader_established_generation_is_checked_before_materialization(saved_history):
    from etlayout.io.resources import inspect_project

    path, _ = saved_history
    reviewed = inspect_project(path)
    reviewed.archive_data = None
    reviewed._archive_sha256 = "0" * 64
    with pytest.raises(SimulationError, match="differs from the reviewed"):
        open_history_catalog(reviewed)


def test_pinned_view_budget_and_large_local_budget_are_explicit(saved_history, monkeypatch):
    path, _ = saved_history
    with open_history_catalog(path) as store:
        first = store.acquire(store.current_run_id)
        required = store.stats.resident_bytes
        store.set_budget(required)
        opened = track_arrays(monkeypatch)
        with pytest.raises(CacheBudgetExceeded) as rejection:
            store.acquire(store.history[0].run_id)
        assert rejection.value.pinned_bytes > 0 and opened == []
        first.close()
        with store.acquire(store.history[0].run_id):
            assert store.stats.resident_bytes <= required
        store.set_budget(3 * 1024**3)
        assert store.stats.budget_bytes == 3 * 1024**3  # No allocation implied by a local setting.


def test_close_cancels_active_reader_before_disposing_its_copy(saved_history, monkeypatch):
    path, _ = saved_history
    store = open_history_catalog(path)
    entered, release = threading.Event(), threading.Event()
    errors = []
    real = store._reader.read_array
    def blocked(digest, **kwargs):
        entered.set()
        assert release.wait(10)
        return real(digest, **kwargs)
    monkeypatch.setattr(store._reader, "read_array", blocked)
    def read():
        try:
            store.acquire(store.current_run_id)
        except Exception as error:
            errors.append(error)
    reader = threading.Thread(target=read, daemon=True)
    reader.start()
    assert entered.wait(10)
    private = store._snapshot_path
    closer = threading.Thread(target=store.close, daemon=True)
    closer.start()
    assert store._cancel.wait(10) and private.exists()
    release.set()
    reader.join(10)
    closer.join(10)
    assert not reader.is_alive() and not closer.is_alive()
    assert len(errors) == 1 and isinstance(errors[0], HistoryReadCancelled)
    assert not private.parent.exists() and store.stats.pinned_bytes == 0


def test_complete_save_of_unbrowsed_references_survives_view_close(saved_history, tmp_path):
    from etlayout.io.archive_v3 import ArchiveV3Reader

    path, expected = saved_history
    store = open_history_catalog(path)
    project = store.resolve_project()
    ids = tuple(record.run_id for record in store.records)
    source = store.capture_history(ids)
    assert store.stats.resident_bytes == 0
    store.close()
    target = tmp_path / "complete-copy.etlayout"
    try:
        save_project(project, target, source.references[0], history=source.references[1:], archive_format=3)
    finally:
        source.close()
    with ArchiveV3Reader(target) as reader:
        assert tuple(record["id"] for record in reader.records) == ids
        for record, original in zip(reader.records, expected, strict=True):
            loaded = reader.materialize(record["id"])
            assert loaded.configuration_hash == original.configuration_hash
            np.testing.assert_array_equal(loaded.fields["m"]["temperature"], original.fields["m"]["temperature"])


@pytest.mark.parametrize("key,value", [
    ("metrics", {"temperature_max": 9e9}), ("field_keys", {"fields": {"fake": ["temperature"]}}),
    ("mode", "invented"), ("name", "invented"), ("diagnostics", [{"message": "invented"}]),
    ("times_s", ["bad"]), ("configuration", "bad"), ("elapsed_seconds", "bad"),
    ("metrics", ["bad"]), ("converged", "bad"), ("configuration_hash", ["bad"]),
])
def test_forged_catalog_summaries_are_isolated_before_any_field_read(saved_history, tmp_path, monkeypatch, key, value):
    path, _ = saved_history
    damaged = tmp_path / "forged.etlayout"
    with zipfile.ZipFile(path) as original, zipfile.ZipFile(damaged, "w") as output:
        for entry in original.infolist():
            data = original.read(entry)
            if entry.filename == "runs.json":
                catalog = json.loads(data)
                catalog["records"][0]["summary"][key] = value
                data = json.dumps(catalog, allow_nan=False).encode()
            output.writestr(entry, data)
    opened = track_arrays(monkeypatch)
    with open_history_catalog(damaged) as store:
        assert not store.record(store.current_run_id).available
        assert opened == []
        with pytest.raises(SimulationError):
            store.acquire(store.current_run_id)
        with store.acquire(store.history[0].run_id) as valid:
            assert valid.fields["m"]["temperature"][0] == 310.
