# SPDX-License-Identifier: MPL-2.0
"""Observable publication-inventory boundaries; no candidate code is executed."""
import importlib.util
import json
import zipfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


def load(relative, name):
    spec = importlib.util.spec_from_file_location(name, ROOT / relative)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


inventory = load("scripts/create_sbom.py", "publication_inventory")
qt = load("packaging/qt_contents.py", "publication_qt")


@pytest.mark.parametrize("name", ["../secret", "/absolute", "C:/secret", "folder/../../secret", "folder\\..\\secret"])
def test_archive_paths_reject_escape(name):
    with pytest.raises(ValueError, match="Unsafe"):
        inventory.safe_member(name)


def test_duplicate_archive_members_are_rejected(tmp_path):
    artifact = tmp_path / "candidate.whl"
    with pytest.warns(UserWarning, match="Duplicate"):
        with zipfile.ZipFile(artifact, "w") as archive:
            archive.writestr("same", "first")
            archive.writestr("same", "second")
    with pytest.raises(ValueError, match="Duplicate"):
        list(inventory.artifact_files(artifact, "wheel", tmp_path / "evidence"))


def test_archive_declared_size_is_bounded(tmp_path, monkeypatch):
    artifact = tmp_path / "candidate.whl"
    with zipfile.ZipFile(artifact, "w") as archive:
        archive.writestr("large", b"12345")
    monkeypatch.setattr(inventory, "MAX_MEMBER", 4)
    with pytest.raises(ValueError, match="size budget"):
        list(inventory.artifact_files(artifact, "wheel", tmp_path / "evidence"))


def test_source_inventory_excludes_private_git_and_generated_evidence(tmp_path):
    source, evidence = tmp_path / "source", tmp_path / "source/evidence"
    (source / ".git").mkdir(parents=True)
    (source / ".git/config").write_text("private", encoding="utf-8")
    (source / "module.py").write_text("value = 1", encoding="utf-8")
    evidence.mkdir()
    (evidence / "old.json").write_text("old", encoding="utf-8")
    result = inventory.create(source, "source", evidence)
    assert [item["path"] for item in result["files"]] == ["module.py"]
    assert result["shipped_runtime_dependencies"] == []
    assert result["declared_dependencies_not_bundled"]
    assert not result["binary_publication_allowed"]
    bom = json.loads((evidence / "sbom.cdx.json").read_text(encoding="utf-8"))
    assert bom["bomFormat"] == "CycloneDX"
    assert bom["components"][0]["hashes"][0]["content"] == result["files"][0]["sha256"]


def test_wheel_inventory_detects_unexpected_native_asset(tmp_path):
    artifact = tmp_path / "candidate.whl"
    with zipfile.ZipFile(artifact, "w") as archive:
        archive.writestr("etlayout/__init__.py", "value = 1")
        archive.writestr("etlayout/unexpected.dll", b"unrecognized binary")
    result = inventory.create(artifact, "wheel", tmp_path / "evidence")
    assert len(result["native_files"]) == 1
    assert result["native_files"][0]["inspection"].startswith("Not a PE")
    assert result["unresolved_review_items"]
    assert not result["binary_publication_allowed"]


@pytest.mark.parametrize("path, expected", [
    ("PySide6/Qt6Widgets.dll", True), ("PySide6/Qt6Test.dll", True),
    ("PySide6/plugins/platforms/qwindows.dll", True),
    ("PySide6/Qt6VirtualKeyboard.dll", False),
    ("PySide6/plugins/platforminputcontexts/qtvirtualkeyboardplugin.dll", False),
    ("PySide6/plugins/imageformats/qpdf.dll", False), ("PySide6/Qt6Quick.dll", False),
])
def test_qt_desktop_filter(path, expected):
    assert qt.keep_qt_entry(path) is expected


def test_unknown_qt_module_needs_explicit_review():
    with pytest.raises(ValueError, match="requires explicit"):
        qt.keep_qt_entry("PySide6/Qt6Unexpected.dll")


def test_public_candidate_summary_omits_build_host_paths_and_output():
    build = load("scripts/build_windows.py", "publication_build")
    detailed = {"check_name": "solve-E1", "command": [str(Path("private-build") / "etlayout.exe"), "solve", "private-project.etl"],
                "output": "private diagnostic details", "exit_code": 0, "elapsed_seconds": 1.25}
    summary = build.public_check_summary(detailed)
    assert summary == {"name": "solve-E1", "executable": "etlayout.exe", "exit_code": 0, "elapsed_seconds": 1.25}
    assert "private" not in json.dumps(summary)


def test_source_manifest_retains_maintained_resources_and_excludes_generated_content():
    # Exercise setuptools' actual source-list parser instead of asserting text patterns.
    from setuptools import Distribution
    from setuptools.command.sdist import sdist
    distribution = Distribution({"name": "etlayout", "version": "0.4.0.dev0"})
    command = sdist(distribution)
    command.ensure_finalized()
    command.filelist = __import__("setuptools._distutils.filelist", fromlist=["FileList"]).FileList()
    command.filelist.allfiles = ["README.md", "docs/images/layout-editor.png", "docs/licensing.md",
                                "examples/E1_straight.etl", "scripts/build_windows.py", "SECURITY.md",
                                "tests/fixtures/legacy_transient_e18.etl", "tests/fixtures/legacy_transient_e18.md",
                                "docs/verification/private.json", "docs/mockups/generated.png",
                                "build/private.log", "src/etlayout/__pycache__/module.pyc",
                                "docs/OLD_HANDOFF.md", ".git/config", ".github/CODEOWNERS"]
    command.filelist.allfiles = [str(Path(name)) for name in command.filelist.allfiles]
    command.read_template()
    files = {Path(name).as_posix() for name in command.filelist.files}
    assert {"README.md", "docs/images/layout-editor.png", "docs/licensing.md", "examples/E1_straight.etl",
            "scripts/build_windows.py", "SECURITY.md", ".github/CODEOWNERS",
            "tests/fixtures/legacy_transient_e18.etl", "tests/fixtures/legacy_transient_e18.md"} <= files
    assert not any("private" in name or "mockups" in name or "__pycache__" in name or "HANDOFF" in name or name.startswith(".git/") for name in files)


@pytest.mark.parametrize("destination, expected", [
    ("etlayout-0.4.0.dev0.dist-info/direct_url.json", False),
    ("numpy-2.5.4.dist-info/direct_url.json", False),
    ("Package.DIST-INFO\\DIRECT_URL.JSON", False),
    ("etlayout-0.4.0.dev0.dist-info/METADATA", True),
    ("etlayout-0.4.0.dev0.dist-info/WHEEL", True),
    ("etlayout-0.4.0.dev0.dist-info/RECORD", True),
    ("etlayout-0.4.0.dev0.dist-info/licenses/LICENSE", True),
    ("etlayout-0.4.0.dev0.dist-info/licenses/direct_url.json", True),
    ("examples/etlayout.dist-info/direct_url.json", True),
    ("etlayout/data/direct_url.json", True),
    ("direct_url.json", True),
])
def test_installed_metadata_omits_only_origin_sidecar(destination, expected):
    resources = load("packaging/resources.py", "publication_metadata_policy")
    assert resources.distributable_metadata(destination) is expected


def test_filtered_metadata_copy_preserves_identity_notices_and_resources(tmp_path):
    import shutil

    resources = load("packaging/resources.py", "publication_metadata_copy_policy")
    source, delivered = tmp_path / "installed", tmp_path / "delivered"
    originals = {
        "etlayout.dist-info/direct_url.json": b'{"url":"file:///local-source"}',
        "etlayout.dist-info/METADATA": b"Name: etlayout\nVersion: 0.4.0.dev0\n",
        "etlayout.dist-info/WHEEL": b"Root-Is-Purelib: true\n",
        "etlayout.dist-info/RECORD": b"record contents",
        "etlayout.dist-info/licenses/LICENSE": b"license contents",
        "etlayout/data/direct_url.json": b"application resource",
    }
    for name, data in originals.items():
        path = source / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        if resources.distributable_metadata(name):
            target = delivered / name
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(path, target)
    assert not (delivered / "etlayout.dist-info/direct_url.json").exists()
    assert {path.relative_to(delivered).as_posix(): path.read_bytes()
            for path in delivered.rglob("*") if path.is_file()} == {
                name: data for name, data in originals.items() if name != "etlayout.dist-info/direct_url.json"}
