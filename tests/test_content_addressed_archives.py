# SPDX-License-Identifier: MPL-2.0
"""Independent schema-3 storage, identity, integrity, and atomicity contracts."""
from __future__ import annotations

import json
import struct
import zipfile
from copy import deepcopy
from pathlib import Path

import numpy as np
import pytest

from etlayout.io import project as project_io
from etlayout.io.archive_v3 import ArchiveV3Reader
from etlayout.io.identity import document_id
from etlayout.io.saving import SaveCancelled
from etlayout.models import Diagnostic, PlanarMesh, Project, SimulationError, SimulationResult

pytestmark = pytest.mark.fast


@pytest.fixture
def specimen():
    project = Project(name="Exact storage specimen")
    mesh = PlanarMesh(np.array([[0., 0.], [1., 0.], [0., 1.]], dtype=">f8"),
        np.array([[0, 1, 2]], dtype=np.int32), {"layer:a": np.array([0], dtype=np.int32)},
        statistics={"tuple": (1, -0., np.float32(2.))})
    result = SimulationResult(mesh, fields={"a": {"temperature": np.array([300., 301., 302.], dtype=">f8")}},
        element_fields={"a": {"temperature": np.array([[300., 301., 302.]])}},
        converged=True, configuration=project.to_dict(), configuration_hash=project_io.configuration_hash(project),
        physics_hash=project_io.physics_hash(project), diagnostics=[Diagnostic("info", "Retained provenance")],
        metrics={"tuple": (1, -0., np.float32(2.)), "not_a_tag": {"type": "float64", "bits": "literal"}},
        execution={"cpu_threads": 4, "backend": "cpu"}, analysis={"units": "A/mm²"}, elapsed_seconds=.125)
    return project, result


def rewrite(source, target, edit):
    with zipfile.ZipFile(source) as archive:
        members = {name: archive.read(name) for name in archive.namelist()}
    edit(members)
    with zipfile.ZipFile(target, "w", zipfile.ZIP_DEFLATED) as archive:
        for name, value in members.items():
            archive.writestr(name, value)


@pytest.mark.parametrize("count", [1, 10, 50])
def test_payload_is_shared_but_ordered_records_are_distinct(specimen, tmp_path, count):
    project, result = specimen
    target = tmp_path / "shared.etl"
    project_io.save_project(project, target, result, history=[result] * (count - 1), archive_format=3)
    reopened, current = project_io.load_project(target)
    history = project_io.load_run_history(target)
    assert document_id(reopened) == document_id(project)
    assert reopened._archive_format == 3
    assert len(history) == count - 1
    with ArchiveV3Reader(target) as reader:
        assert len({record["id"] for record in reader.records}) == count
        assert len({record["descriptor"]["$ref"] for record in reader.records}) == 1
        assert len(reader.required_arrays(reader.current_run_id)) == 6
        assert reader.size["logical_bytes"] >= reader.size["uncompressed_bytes"]
    for saved in [current, *history]:
        assert saved.configuration_hash == result.configuration_hash
        assert saved.physics_hash == result.physics_hash
        assert saved.execution == result.execution and saved.analysis == result.analysis
        assert saved.metrics["tuple"][0] == 1
        assert isinstance(saved.metrics["tuple"], tuple)
        assert type(saved.metrics["tuple"][2]) is np.float32
        assert struct.pack(">d", saved.metrics["tuple"][1]) == struct.pack(">d", -0.)
        assert saved.metrics["not_a_tag"] == result.metrics["not_a_tag"]
        assert saved.diagnostics == result.diagnostics
        for expected, actual in [(result.mesh.points, saved.mesh.points),
                                 (result.mesh.triangles, saved.mesh.triangles),
                                 (result.fields["a"]["temperature"], saved.fields["a"]["temperature"])]:
            assert actual.dtype == expected.dtype and actual.shape == expected.shape
            assert actual.tobytes() == expected.tobytes()
    if history:
        current.fields["a"]["temperature"][0] = 999.
        assert history[0].fields["a"]["temperature"][0] == 300.
        assert not np.shares_memory(current.mesh.points, history[0].mesh.points)


def test_default_remains_schema_two_and_unknown_writer_fails(specimen, tmp_path):
    project, result = specimen
    target = tmp_path / "legacy.etl"
    project_io.save_project(project, target, result)
    with zipfile.ZipFile(target) as archive:
        assert json.loads(archive.read("project.json"))["schema_version"] == 2
    old = target.read_bytes()
    for version in [True, 1, 4, "3"]:
        with pytest.raises(SimulationError, match="Unsupported archive format"):
            project_io.save_project(project, target, result, archive_format=version)
    assert target.read_bytes() == old


def test_catalog_opens_without_array_reads_and_detects_selected_corruption(specimen, tmp_path, monkeypatch):
    project, result = specimen
    source, bad = tmp_path / "source.etl", tmp_path / "bad.etl"
    project_io.save_project(project, source, result, archive_format=3)
    with ArchiveV3Reader(source) as reader:
        digest = reader._descriptor(reader.current_run_id)["fields"]["a"]["temperature"]["$ref"]
        name = reader.object_name(digest)
    def corrupt(members):
        raw = members[name]
        members[name] = raw[:-1] + bytes([raw[-1] ^ 1])
    rewrite(source, bad, corrupt)
    opened = []
    original = zipfile.ZipFile.open
    def observed(self, name, *args, **kwargs):
        text = name.filename if isinstance(name, zipfile.ZipInfo) else name
        opened.append(text)
        return original(self, name, *args, **kwargs)
    monkeypatch.setattr(zipfile.ZipFile, "open", observed)
    with ArchiveV3Reader(bad) as reader:
        assert not any(name.endswith(".npy") for name in opened)
        with pytest.raises(SimulationError, match="hash/length mismatch"):
            reader.materialize(reader.current_run_id)


def test_logical_budget_cannot_be_bypassed_by_shared_payload(specimen, tmp_path, monkeypatch):
    project, result = specimen
    target = tmp_path / "repeated.etl"
    project_io.save_project(project, target, result, history=[result] * 9, archive_format=3)
    with ArchiveV3Reader(target) as reader:
        unique, logical = reader.size["uncompressed_bytes"], reader.size["logical_bytes"]
    assert logical > unique
    monkeypatch.setattr(project_io, "MAX_ARCHIVE_BYTES", (unique + logical) // 2)
    with pytest.raises(SimulationError, match="Logical materialization"):
        ArchiveV3Reader(target)


@pytest.mark.parametrize("fault", ["cancel", "write", "verify", "commit"])
def test_faults_preserve_durable_archive_and_remove_only_owned_temporary(specimen, tmp_path, monkeypatch, fault):
    from etlayout.io import archive_v3
    project, result = specimen
    target = tmp_path / "durable.etl"
    project_io.save_project(project, target, result)
    before = target.read_bytes()
    unrelated = tmp_path / "unrelated.tmp"
    unrelated.write_text("keep")
    options = {}
    def fail(*args, **kwargs):
        raise SimulationError("Injected failure")
    if fault == "cancel":
        options["cancel"] = lambda: True
    elif fault == "write":
        monkeypatch.setattr(archive_v3._Collector, "write", fail)
    elif fault == "verify":
        monkeypatch.setattr(ArchiveV3Reader, "verify_all", fail)
    else:
        options["commit"] = fail
    with pytest.raises((SimulationError, SaveCancelled)):
        project_io.save_project(project, target, result, archive_format=3, **options)
    assert target.read_bytes() == before
    assert list(tmp_path.glob("*.tmp")) == [unrelated]


def test_nan_payload_and_noncontiguous_numeric_metadata_preserve_bits(specimen, tmp_path):
    project, result = specimen
    bits = np.array([0x7ff8000000001234, 0x8000000000000000], dtype=">u8")
    values = bits.view(">f8")
    result.execution["numeric"] = values
    result.analysis["strided"] = np.arange(24, dtype=">i4").reshape(4, 6)[:, ::2]
    target = tmp_path / "bits.etl"
    project_io.save_project(project, target, result, archive_format=3)
    _, saved = project_io.load_project(target)
    for group, key in [("execution", "numeric"), ("analysis", "strided")]:
        expected, actual = getattr(result, group)[key], getattr(saved, group)[key]
        assert expected.dtype == actual.dtype and expected.shape == actual.shape
        assert expected.tobytes(order="C") == actual.tobytes(order="C")


def test_legacy_migration_and_explicit_downgrade_are_source_preserving(specimen, tmp_path):
    project, result = specimen
    # Start with exactly what schema 2 can represent. No silent coercion of a
    # schema-3-only tuple/NumPy scalar is accepted on explicit downgrade.
    result.metrics = {"resistance": .084}
    result.mesh.statistics = {"nodes": 3}
    source, addressed, downgraded = (tmp_path / name for name in ("old.etl", "v3.etl", "v2.etl"))
    project_io.save_project(project, source, result, history=[result, result])
    original = source.read_bytes()
    receipt = project_io.migrate_project(source, addressed)
    assert receipt["verified"] and receipt["records"] == 3
    assert receipt["archive_format"] == 3
    assert source.read_bytes() == original
    assert project_io.migrate_project(addressed, downgraded, archive_format=2)["verified"]
    assert len(project_io.load_run_history(downgraded)) == 2
    for path in (source, addressed, downgraded):
        assert document_id(project_io.load_project(path)[0]) == document_id(project)
    for destination in (source, addressed):
        before = destination.read_bytes()
        with pytest.raises(SimulationError, match="new destination"):
            project_io.migrate_project(source, destination)
        assert destination.read_bytes() == before


def test_downgrade_rejects_typed_loss_without_publishing_destination(specimen, tmp_path):
    project, result = specimen
    source, destination = tmp_path / "typed.etl", tmp_path / "legacy.etl"
    project_io.save_project(project, source, result, archive_format=3)
    before = source.read_bytes()
    with pytest.raises(SimulationError, match="cannot preserve these exact typed values"):
        project_io.migrate_project(source, destination, archive_format=2)
    assert source.read_bytes() == before and not destination.exists()
    assert not list(tmp_path.glob("*.migration-*"))


@pytest.mark.parametrize("attack", ["unknown_codec", "unsafe_name", "duplicate", "orphan", "huge_shape", "object_dtype"])
def test_manifest_and_directory_attacks_fail_before_array_reads(specimen, tmp_path, monkeypatch, attack):
    project, result = specimen
    source, target = tmp_path / "good.etl", tmp_path / "attack.etl"
    project_io.save_project(project, source, result, archive_format=3)
    def edit(members):
        root = json.loads(members["project.json"])
        if attack == "unknown_codec":
            root["object_codec"] = 99
        elif attack == "unsafe_name":
            members["../outside.npy"] = b"unread"
        elif attack == "orphan":
            members["objects/sha256/" + "0" * 64 + ".npy"] = b"unread"
        elif attack in {"huge_shape", "object_dtype"}:
            item = next(record for record in root["objects"].values() if record["kind"] == "array")
            if attack == "huge_shape":
                item["shape"] = [10 ** 18]
            else:
                item["dtype"] = "|O"
        members["project.json"] = json.dumps(root).encode()
    rewrite(source, target, edit)
    if attack == "duplicate":
        with zipfile.ZipFile(target, "a") as archive:
            with pytest.warns(UserWarning, match="Duplicate"):
                archive.writestr("project.json", "{}")
    def forbidden(*args, **kwargs):
        pytest.fail("Unsafe metadata must fail before numeric array allocation")
    monkeypatch.setattr(ArchiveV3Reader, "read_array", forbidden)
    with pytest.raises(SimulationError):
        ArchiveV3Reader(target)


def test_missing_payload_isolates_dependent_run(specimen, tmp_path):
    project, result = specimen
    other = deepcopy(result)
    other.fields["a"]["temperature"] = np.array([400., 401., 402.])
    other.element_fields["a"]["temperature"] = np.array([[400., 401., 402.]])
    source, target = tmp_path / "good.etl", tmp_path / "missing.etl"
    project_io.save_project(project, source, result, history=[other], archive_format=3)
    with ArchiveV3Reader(source) as reader:
        name = reader.object_name(reader._descriptor(reader.current_run_id)["fields"]["a"]["temperature"]["$ref"])
    rewrite(source, target, lambda members: members.pop(name))
    with ArchiveV3Reader(target) as reader:
        assert [record["available"] for record in reader.records] == [False, True]
        with pytest.raises(SimulationError, match="Missing"):
            reader.materialize(reader.current_run_id)
        saved = reader.materialize(reader.records[1]["id"])
        np.testing.assert_array_equal(saved.fields["a"]["temperature"], [400., 401., 402.])


def test_explicit_exact_legacy_copy_fails_before_replacing_durable_file(specimen, tmp_path):
    project, result = specimen
    target = tmp_path / "previous.etl"
    project_io.save_project(project, target)
    before = target.read_bytes()
    with pytest.raises(SimulationError, match="cannot preserve these exact typed values"):
        project_io.save_project(project, target, result, archive_format=2, verify_exact=True)
    assert target.read_bytes() == before
    result.metrics = {"resistance": .084}
    result.mesh.statistics = {"nodes": 3}
    project_io.save_project(project, target, result, history=[result], archive_format=2, verify_exact=True)
    assert len(project_io.load_run_history(target)) == 1


def test_corrupt_historical_metadata_keeps_valid_working_run_available(specimen, tmp_path):
    project, result = specimen
    other = deepcopy(result)
    other.metrics["historical_only"] = 42.
    source, target = tmp_path / "source.etl", tmp_path / "bad-history.etl"
    project_io.save_project(project, source, result, history=[other], archive_format=3)
    with ArchiveV3Reader(source) as reader:
        descriptor = reader._descriptor(reader.records[1]["id"])
        name = reader.object_name(descriptor["metadata"]["$ref"])
    def corrupt(members):
        raw = members[name]
        members[name] = raw[:-1] + b"!"
    rewrite(source, target, corrupt)
    with ArchiveV3Reader(target) as reader:
        assert [record["available"] for record in reader.records] == [True, False]
        assert reader.materialize(reader.current_run_id).converged
    assert project_io.load_project(target)[1].converged
    with pytest.raises(SimulationError, match="hash/length mismatch"):
        project_io.load_run_history(target)


def test_surviving_loaded_run_ids_stay_stable_after_pruning_and_reordering(specimen, tmp_path):
    project, result = specimen
    source, changed = tmp_path / "all.etl", tmp_path / "pruned.etl"
    project_io.save_project(project, source, result, history=[result, result], archive_format=3)
    reopened, current = project_io.load_project(source)
    history = project_io.load_run_history(source)
    original_ids = [current._storage_run_id, *(run._storage_run_id for run in history)]
    project_io.save_project(reopened, changed, current, history=[history[1]], archive_format=3)
    with ArchiveV3Reader(changed) as reader:
        assert [record["id"] for record in reader.records] == [original_ids[0], original_ids[2]]


def test_source_mutation_after_serialization_preserves_previous_archive(specimen, tmp_path):
    project, result = specimen
    source = tmp_path / "layout.gds"
    source.write_bytes((Path(__file__).parents[1] / "examples" / "E1_straight.gds").read_bytes())
    project.layout.path = str(source)
    project.layout.sha256 = project_io.file_hash(source)
    result.configuration = project.to_dict()
    result.configuration_hash = project_io.configuration_hash(project)
    result.physics_hash = project_io.physics_hash(project)
    target = tmp_path / "durable.etl"
    project_io.save_project(project, target)
    before = target.read_bytes()
    def progress(fraction, message):
        if fraction == .8:
            source.write_bytes(source.read_bytes() + b"changed")
    with pytest.raises(SimulationError, match="Source layout changed"):
        project_io.save_project(project, target, result, archive_format=3, progress=progress)
    assert target.read_bytes() == before


def test_actual_legacy_transient_migrates_and_downgrades_without_changing_values(tmp_path):
    source = Path(__file__).parent / "fixtures" / "legacy_transient_e18.etl"
    first, second = tmp_path / "addressed.etl", tmp_path / "legacy.etl"
    before = source.read_bytes()
    assert project_io.migrate_project(source, first)["verified"]
    assert project_io.migrate_project(first, second, archive_format=2)["verified"]
    assert source.read_bytes() == before
    _, expected = project_io.load_project(source)
    _, actual = project_io.load_project(second)
    assert actual.physics_hash == expected.physics_hash
    assert actual.transient.times.tobytes() == expected.transient.times.tobytes()
    assert actual.transient.rows == expected.transient.rows


def test_array_bytes_base_contains_only_the_accounted_numeric_payload(specimen, tmp_path):
    project, result = specimen
    target = tmp_path / "accounted.etl"
    project_io.save_project(project, target, result, archive_format=3)
    with ArchiveV3Reader(target) as reader:
        for digest in reader.required_arrays(reader.current_run_id):
            array = reader.read_array(digest)
            base = array
            while isinstance(base, np.ndarray) and base.base is not None:
                base = base.base
            assert isinstance(base, bytes)
            assert len(base) == array.nbytes == reader.array_info(digest)["nbytes"]
            assert not array.flags.writeable
            with pytest.raises(ValueError):
                array.setflags(write=True)


def test_identical_current_external_source_uses_one_explicit_root_approval(specimen, tmp_path):
    from etlayout.io.resources import ExternalResourcePolicy, inspect_project

    project, result = specimen
    source = tmp_path / "source.gds"
    source.write_bytes((Path(__file__).parents[1] / "examples" / "E1_straight.gds").read_bytes())
    project.layout.path, project.layout.sha256 = str(source), project_io.file_hash(source)
    result.configuration = project.to_dict()
    result.configuration_hash, result.physics_hash = project_io.configuration_hash(project), project_io.physics_hash(project)
    folder = tmp_path / "archives"
    folder.mkdir()
    target = folder / "external.etl"
    project_io.save_project(project, target, result, archive_format=3, embed_layout=False)
    inspection = inspect_project(target)
    assert [resource.key for resource in inspection.resources] == ["layout"]
    approved = ExternalResourcePolicy(approved_resources=frozenset({"layout"}))
    with ArchiveV3Reader(target, policy=approved) as reader:
        assert reader.layout_resource_key(reader.current_run_id) == "layout"
        assert reader.resolve_project().layout.sha256 == project.layout.sha256
        saved = reader.materialize(reader.current_run_id)
        assert saved.physics_hash == result.physics_hash
        np.testing.assert_array_equal(saved.fields["a"]["temperature"], result.fields["a"]["temperature"])


@pytest.mark.parametrize("phase", ["objects", "catalog"])
def test_opening_cancellation_checks_metadata_and_graph_before_arrays(specimen, tmp_path, monkeypatch, phase):
    from etlayout.io import archive_v3
    project, result = specimen
    target = tmp_path / "cancel-open.etl"
    project_io.save_project(project, target, result, history=[result] * 3, archive_format=3)
    cancelled = False
    original = archive_v3._parse
    def observed(raw):
        nonlocal cancelled
        value = original(raw)
        if isinstance(value, dict) and (phase == "objects" and "typed_codec" in value
                                      or phase == "catalog" and "record_codec" in value):
            cancelled = True
        return value
    monkeypatch.setattr(archive_v3, "_parse", observed)
    def forbidden(*args, **kwargs):
        pytest.fail("Opening cancellation must not load numerical arrays")
    monkeypatch.setattr(ArchiveV3Reader, "read_array", forbidden)
    with pytest.raises(SaveCancelled, match="cancelled"):
        ArchiveV3Reader(target, cancel=lambda: cancelled)
    assert cancelled
    target.unlink()  # Native Windows: cancelled reads retained no file handle.


def test_opening_cancel_callback_is_not_retained_by_completed_reader(specimen, tmp_path):
    project, result = specimen
    target = tmp_path / "independent-read.etl"
    project_io.save_project(project, target, result, archive_format=3)
    cancelled = False
    with ArchiveV3Reader(target, cancel=lambda: cancelled) as reader:
        cancelled = True
        assert reader._opening_cancel is None
        saved = reader.materialize(reader.current_run_id)
        np.testing.assert_array_equal(saved.fields["a"]["temperature"], result.fields["a"]["temperature"])


def test_zip_directory_is_parsed_once_per_catalog_and_materialization(specimen, tmp_path, monkeypatch):
    project, result = specimen
    target = tmp_path / "batched.etl"
    project_io.save_project(project, target, result, history=[result] * 5, archive_format=3)
    opened = []
    real = zipfile.ZipFile
    class ObservedZip(real):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            opened.append(self)
    monkeypatch.setattr(zipfile, "ZipFile", ObservedZip)
    with ArchiveV3Reader(target) as reader:
        assert len(opened) == 1 and all(item.fp is None for item in opened)
        loaded = reader.materialize(reader.current_run_id)
        assert len(opened) == 2 and all(item.fp is None for item in opened)
        np.testing.assert_array_equal(loaded.fields["a"]["temperature"], result.fields["a"]["temperature"])
        reader.verify_all()
        assert len(opened) == 3 and all(item.fp is None for item in opened)
        with pytest.raises(SaveCancelled):
            reader.materialize(reader.current_run_id, cancel=lambda: True)
        assert len(opened) == 4 and all(item.fp is None for item in opened)


def test_concurrent_materializations_own_separate_zip_streams(specimen, tmp_path, monkeypatch):
    import threading
    from concurrent.futures import ThreadPoolExecutor

    project, result = specimen
    target = tmp_path / "concurrent.etl"
    project_io.save_project(project, target, result, archive_format=3)
    with ArchiveV3Reader(target) as reader:
        barrier, opened = threading.Barrier(2), []
        real_zip = zipfile.ZipFile
        mesh = reader._mesh
        class ObservedZip(real_zip):
            def __init__(self, *args, **kwargs):
                super().__init__(*args, **kwargs)
                opened.append((threading.get_ident(), self))
        def simultaneous(*args):
            barrier.wait(timeout=5)
            return mesh(*args)
        monkeypatch.setattr(zipfile, "ZipFile", ObservedZip)
        monkeypatch.setattr(reader, "_mesh", simultaneous)
        with ThreadPoolExecutor(max_workers=2) as workers:
            futures = [workers.submit(reader.materialize, reader.current_run_id) for _ in range(2)]
            first, second = (future.result(timeout=10) for future in futures)
        assert len(opened) == 2 and len({owner for owner, _ in opened}) == 2
        assert all(archive.fp is None for _, archive in opened)
        assert not np.shares_memory(first.mesh.points, second.mesh.points)
        assert first.mesh.points.tobytes() == second.mesh.points.tobytes()
        assert first.fields["a"]["temperature"].tobytes() == second.fields["a"]["temperature"].tobytes()
    replacement = tmp_path / "moved.etl"
    target.replace(replacement)  # Native Windows handle-release check.
    replacement.unlink()


def test_streaming_save_reuses_each_source_directory_for_the_operation(specimen, tmp_path, monkeypatch):
    from etlayout.io.archive_v3 import StoredRunReference

    project, result = specimen
    source, target = tmp_path / "source.etl", tmp_path / "copy.etl"
    project_io.save_project(project, source, result, history=[result] * 4, archive_format=3)
    class Token:
        closed = False
    with ArchiveV3Reader(source) as reader:
        references = [StoredRunReference(record["id"], "test-generation", reader, Token()) for record in reader.records]
        opened = []
        real = zipfile.ZipFile
        class ObservedZip(real):
            def __init__(self, *args, **kwargs):
                super().__init__(*args, **kwargs)
                opened.append(self)
        monkeypatch.setattr(zipfile, "ZipFile", ObservedZip)
        project_io.save_project(project, target, references[0], history=references[1:], archive_format=3)
        # One output writer, one source stream, one finished catalog, one final
        # physical verification stream, independent of object/run count.
        assert len(opened) == 4 and all(archive.fp is None for archive in opened)
    assert len(project_io.load_run_history(target)) == 4


@pytest.mark.parametrize("suffix,alias", [(".gds", ".gdsii"), (".oas", ".oasis")])
def test_source_extension_aliases_share_payload_and_keep_source_hash(specimen, tmp_path, suffix, alias):
    import klayout.db as kdb

    from etlayout.geometry.layout import LayoutDocument

    project, result = specimen
    layout = kdb.Layout()
    layout.dbu = .001
    top = layout.create_cell("TOP")
    top.shapes(layout.layer(1, 0)).insert(kdb.Box(0, 0, 1000, 1000))
    source = tmp_path / ("first" + suffix)
    layout.write(str(source))
    other = tmp_path / ("alias" + alias)
    other.write_bytes(source.read_bytes())
    project.layout.path, project.layout.cell = str(source), "TOP"
    project.layout.sha256 = project_io.file_hash(source)
    result.configuration = project.to_dict()
    result.configuration_hash = project_io.configuration_hash(project)
    result.physics_hash = project_io.physics_hash(project)
    historical = deepcopy(result)
    historical.configuration["layout"]["path"] = str(other)
    target = tmp_path / "aliases.etlayout"
    project_io.save_project(project, target, result, history=[historical], archive_format=3)
    with ArchiveV3Reader(target) as reader:
        assert len([item for item in reader._index.values() if item["kind"] == "layout"]) == 1
    reopened, current = project_io.load_project(target)
    history = project_io.load_run_history(target)
    assert reopened.layout.sha256 == project.layout.sha256
    assert current.configuration_hash == history[0].configuration_hash == result.configuration_hash
    assert LayoutDocument(reopened.layout.path).top_cells == ["TOP"]
