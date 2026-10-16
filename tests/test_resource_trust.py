# SPDX-License-Identifier: MPL-2.0
"""External-resource gates are verified by observing forbidden access, not errors alone."""

import hashlib
import json
import stat
from pathlib import Path
from types import SimpleNamespace

import pytest

from etlayout.io.project import load_project, physics_hash, save_json, save_project
from etlayout.io.resources import (
    ExternalResourcePolicy,
    ResourceAccessError,
    inspect_project,
    is_network_path,
)
from etlayout.models import LayoutSource, Project, SimulationError

pytestmark = pytest.mark.fast


def metadata_file(tmp_path, location, sha=""):
    folder = tmp_path / "project"
    folder.mkdir(exist_ok=True)
    path = folder / "metadata.json"
    path.write_text(
        json.dumps(Project(layout=LayoutSource(str(location), sha256=sha)).to_dict()), encoding="utf-8"
    )
    return path


def forbid_reference(monkeypatch, location):
    calls = []
    for name in ("open", "stat", "resolve", "is_file", "read_bytes", "read_text"):
        original = getattr(Path, name)

        def probe(self, *args, _name=name, _original=original, **kwargs):
            if str(self) == str(location) or str(self).startswith(str(location).rstrip("\\/") + "/"):
                calls.append(_name)
                raise AssertionError("Unapproved resource was accessed")
            return _original(self, *args, **kwargs)

        monkeypatch.setattr(Path, name, probe)
    return calls


@pytest.mark.parametrize(
    "location",
    [
        r"\\server\share\secret.gds",
        r"\\?\UNC\server\share\secret.oas",
        "../private/secret.gds",
        "missing.gds",
    ],
)
def test_metadata_inspection_performs_zero_resource_accesses(tmp_path, monkeypatch, location):
    path = metadata_file(tmp_path, location)
    calls = forbid_reference(monkeypatch, location)
    inspection = inspect_project(path)
    assert inspection.resources[0].location == location
    assert "Not accessed" in inspection.resources[0].status
    assert calls == []


def test_default_denies_traversal_before_access(tmp_path, monkeypatch):
    outside = tmp_path / "private.gds"
    outside.write_bytes(b"synthetic-layout")
    path = metadata_file(tmp_path, "../private.gds")
    calls = forbid_reference(monkeypatch, outside)
    with pytest.raises(ResourceAccessError) as error:
        load_project(path)
    assert error.value.reason == "outside-root" and calls == []


def test_inside_root_and_deliberate_outside_root_preserve_hash(tmp_path):
    path = metadata_file(tmp_path, "layout.gds")
    local = path.parent / "layout.gds"
    local.write_bytes(b"local-layout")
    project, _ = load_project(path)
    assert project.layout.sha256 == hashlib.sha256(local.read_bytes()).hexdigest()
    outside = tmp_path / "outside.gds"
    outside.write_bytes(b"outside-layout")
    path = metadata_file(tmp_path, str(outside))
    approved, _ = load_project(path, external_policy=ExternalResourcePolicy(allow_outside_root=True))
    assert Path(approved.layout.path) == outside
    assert approved.layout.sha256 == hashlib.sha256(outside.read_bytes()).hexdigest()


def test_symlink_escape_is_rejected_before_target_access(tmp_path, monkeypatch):
    path = metadata_file(tmp_path, "link.gds")
    link = path.parent / "link.gds"
    outside = tmp_path / "private.gds"
    outside.write_bytes(b"private-layout")
    original = __import__("os").lstat
    monkeypatch.setattr(
        "etlayout.io.resources.os.lstat",
        lambda value: SimpleNamespace(st_mode=stat.S_IFLNK) if Path(value) == link else original(value),
    )
    monkeypatch.setattr("etlayout.io.resources.os.readlink", lambda value: str(outside))
    calls = forbid_reference(monkeypatch, outside)
    with pytest.raises(ResourceAccessError) as error:
        load_project(path)
    assert error.value.reason == "outside-root" and calls == []


@pytest.mark.parametrize("location", [r"\\server\share\layout.gds", r"\\?\UNC\server\share\layout.oas"])
def test_outside_approval_does_not_authorize_unc(tmp_path, monkeypatch, location):
    path = metadata_file(tmp_path, location)
    calls = forbid_reference(monkeypatch, location)
    with pytest.raises(ResourceAccessError) as error:
        load_project(path, external_policy=ExternalResourcePolicy(allow_outside_root=True))
    assert error.value.reason == "network" and calls == []


def test_mapped_network_requires_its_separate_opt_in(tmp_path, monkeypatch):
    path = metadata_file(tmp_path, "layout.gds")
    layout = path.parent / "layout.gds"
    layout.write_bytes(b"synthetic-network-content")
    monkeypatch.setattr("etlayout.io.resources._network_drive", lambda p: Path(p) == layout)
    with pytest.raises(ResourceAccessError) as error:
        load_project(path, external_policy=ExternalResourcePolicy(allow_outside_root=True))
    assert error.value.reason == "network"
    loaded, _ = load_project(path, external_policy=ExternalResourcePolicy(allow_network=True))
    assert loaded.layout.sha256 == hashlib.sha256(layout.read_bytes()).hexdigest()


def test_missing_and_changed_resources_have_actionable_private_errors(tmp_path):
    path = metadata_file(tmp_path, "layout.gds")
    with pytest.raises(ResourceAccessError) as error:
        load_project(path)
    assert error.value.reason == "missing" and str(tmp_path) not in str(error.value)
    (path.parent / "layout.gds").write_bytes(b"new")
    path = metadata_file(tmp_path, "layout.gds", "0" * 64)
    with pytest.raises(ResourceAccessError) as error:
        load_project(path)
    assert error.value.reason == "changed" and "relink" in str(error.value)


def test_embedded_project_ignores_original_external_location(tmp_path, monkeypatch):
    import zipfile

    layout = tmp_path / "source.gds"
    layout.write_bytes(b"embedded-layout")
    project = Project(layout=LayoutSource(str(layout)))
    target = tmp_path / "embedded.etl"
    save_project(project, target)
    with zipfile.ZipFile(target) as archive:
        members = {name: archive.read(name) for name in archive.namelist()}
    metadata = json.loads(members["project.json"])
    metadata["project"]["layout"]["path"] = r"\\server\secret\original.gds"
    members["project.json"] = json.dumps(metadata).encode()
    with zipfile.ZipFile(target, "w") as archive:
        for name, value in members.items():
            archive.writestr(name, value)
    calls = forbid_reference(monkeypatch, r"\\server\secret\original.gds")
    inspection = inspect_project(target)
    assert inspection.resources[0].category == "embedded"
    loaded, _ = load_project(target)
    assert Path(loaded.layout.path).read_bytes() == b"embedded-layout" and calls == []


def test_unresolved_metadata_is_editable_without_hash_or_save_io(tmp_path, monkeypatch):
    location = r"\\server\private\source.gds"
    inspection = inspect_project(metadata_file(tmp_path, location))
    project = inspection.unresolved_project()
    calls = forbid_reference(monkeypatch, location)
    before = physics_hash(project)
    project.notes = "Editable without trusting resources"
    assert physics_hash(project) == before
    save_json(project, tmp_path / "metadata-only.json")
    with pytest.raises(ResourceAccessError, match="unresolved"):
        save_project(project, tmp_path / "must-not-write.etl")
    assert calls == [] and not (tmp_path / "must-not-write.etl").exists()


def test_cli_metadata_and_trust_controls_fail_closed(tmp_path, capsys):
    from etlayout.cli.main import main

    layout = tmp_path / "outside.gds"
    layout.write_bytes(b"not-opened-by-metadata")
    path = metadata_file(tmp_path, str(layout))
    assert main(["project-info", str(path)]) == 0
    report = json.loads(capsys.readouterr().out)
    assert report["metadata_only"] and report["resources"][0]["category"] == "outside-root"
    assert main(["validate", str(path)]) == 2
    assert "outside-root" in capsys.readouterr().err


@pytest.mark.parametrize(
    "spelling", [r"\\server\share\x.gds", r"\\?\UNC\server\share\x.oas", r"\??\UNC\server\share\x.gds"]
)
def test_windows_network_spellings_are_classified_lexically(spelling):
    assert is_network_path(spelling)


def test_unsafe_archive_members_are_rejected(tmp_path):
    import zipfile

    p = tmp_path / "unsafe.etl"
    with zipfile.ZipFile(p, "w") as archive:
        archive.writestr("../escape.json", "{}")
        archive.writestr(
            "project.json",
            json.dumps({"format": "etlayout", "schema_version": 2, "project": Project().to_dict()}),
        )
    with pytest.raises(SimulationError, match="unsafe member"):
        inspect_project(p)


def test_selected_network_container_requires_opt_in_before_open(tmp_path, monkeypatch):
    path = metadata_file(tmp_path, "")
    import etlayout.io.resources as resources

    monkeypatch.setattr(resources, "_network_drive", lambda p: Path(p) == path)
    calls = forbid_reference(monkeypatch, path)
    with pytest.raises(ResourceAccessError) as error:
        inspect_project(path)
    assert error.value.reason == "network" and calls == []
    monkeypatch.undo()
    monkeypatch.setattr(resources, "_network_drive", lambda p: Path(p) == path)
    inspection = inspect_project(path, allow_network_container=True)
    assert inspection.summary()["metadata_only"]


def test_review_redacts_forward_and_backslash_credentials():
    from etlayout.io.resources import display_location

    for location in ("//user:secret@server/share/a.gds", r"\\user:secret@server\share\a.gds"):
        assert "secret" not in display_location(location)
        assert "credentials omitted" in display_location(location)


def test_explicit_parent_scope_authorizes_only_its_own_tree(tmp_path):
    path = metadata_file(tmp_path, "../source.gds")
    (tmp_path / "source.gds").write_bytes(b"scoped-artifact")
    policy = ExternalResourcePolicy(allowed_root=tmp_path)
    project, _ = load_project(path, external_policy=policy)
    assert Path(project.layout.path).parent == tmp_path
    path = metadata_file(tmp_path, "../../outside.gds")
    with pytest.raises(ResourceAccessError) as error:
        load_project(path, external_policy=policy)
    assert error.value.reason == "outside-root"


def test_current_metadata_review_is_a_pure_copy_with_original_resource_boundaries(tmp_path, monkeypatch):
    path = metadata_file(tmp_path, "../private.gds")
    inspection = inspect_project(path)
    current = inspection.unresolved_project()
    current.notes = "Keep this unsaved review note"
    current._document_id = "9b3d9dd9-4f06-4fde-a966-90b3c3c5cac0"
    calls = forbid_reference(monkeypatch, tmp_path / "private.gds")
    reviewed = inspection.for_project(current)
    assert calls == [] and reviewed.archive_data is inspection.archive_data
    assert reviewed.project.notes == current.notes and inspection.project.notes == ""
    assert reviewed.project._document_id == current._document_id
    assert reviewed.metadata["document_id"] == current._document_id
    assert not hasattr(reviewed.project, "_resource_unresolved")
    assert not hasattr(reviewed.project, "_resource_inspection")
    assert reviewed.resources == inspection.resources
    assert not reviewed.discard_stale_result  # Notes do not change scientific identity.
    current.solver.initial_temperature += 10.
    assert inspection.for_project(current).discard_stale_result


@pytest.mark.parametrize("suffix", ["json", "etl"])
def test_container_inspection_bounds_each_read_allocation(tmp_path, monkeypatch, suffix):
    import io

    from etlayout.io import resources
    path = tmp_path / ("small." + suffix)
    if suffix == "json":
        save_json(Project(), path)
    else:
        save_project(Project(), path)
    payload = path.read_bytes()
    requested = []
    class TrackedStream(io.BytesIO):
        def read(self, size=-1):
            requested.append(size)
            assert 0 < size <= 1024 * 1024
            return super().read(size)
    original = Path.open
    def tracked_open(self, *args, **kwargs):
        if self == path:
            return TrackedStream(payload)
        return original(self, *args, **kwargs)
    monkeypatch.setattr(Path, "open", tracked_open)
    inspected = resources.inspect_project(path)
    assert inspected.project.name == "Untitled"
    assert requested and max(requested) <= 1024 * 1024


def test_chunked_container_read_retains_aggregate_budget():
    import io

    from etlayout.io.resources import _read_container
    assert _read_container(io.BytesIO(b"0123456789extra"), 10) == b"0123456789e"
