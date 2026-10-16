# SPDX-License-Identifier: MPL-2.0
"""Actual frozen code membership must have complete preferred-source ownership."""
import importlib.util
import json
import marshal
from pathlib import Path

import pytest
from PyInstaller.archive.writers import CArchiveWriter
from test_embedded_fonts import frozen_module

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("python_source_inventory", ROOT / "scripts/create_sbom.py")
inventory = importlib.util.module_from_spec(spec)
spec.loader.exec_module(inventory)


@pytest.fixture
def catalog(tmp_path, monkeypatch):
    (tmp_path / "packaging").mkdir()
    path = tmp_path / "packaging/third-party-sources.json"
    data = {"sources": [{"id": name} for name in ("python-source", "setuptools-source", "hooks-source")],
        "runtime_python": {"source_id": "python-source"},
        "python_module_sources": {"etlayout": ["application"], "setuptools": ["setuptools-source"],
            "_distutils_hack": ["setuptools-source"]},
        "python_outer_module_sources": {"frozen_entry": ["application"], "pyi_rth_extra": ["hooks-source"]}}
    path.write_text(json.dumps(data))
    monkeypatch.setattr(inventory, "ROOT", tmp_path)
    return path, data


def test_stdlib_application_vendored_alias_and_outer_hook_sources_are_distinct(catalog):
    rows, gaps = inventory.python_source_coverage([
        "json.decoder", "etlayout.cli", "setuptools._vendor.packaging", "_distutils_hack", "pyi_rth_extra"])
    assert not gaps
    actual = {row["root"]: row["source_ids"] for row in rows}
    assert actual == {"json": ["python-source"], "etlayout": ["application"],
        "setuptools": ["setuptools-source"], "_distutils_hack": ["setuptools-source"], "pyi_rth_extra": ["hooks-source"]}


@pytest.mark.parametrize("module", ["unexpected_package", "pyi_rth_new_hook"])
def test_unknown_package_or_bootstrap_is_not_treated_as_stdlib(catalog, module):
    rows, gaps = inventory.python_source_coverage([module])
    assert not rows and gaps[0]["component"] == module


@pytest.mark.parametrize("change", ["missing_setuptools", "missing_python", "missing_outer_map", "invalid_source",
    "duplicate_source", "application_alias", "conflicting_map"])
def test_missing_or_invalid_preferred_source_cannot_be_hidden_by_namespace(catalog, change):
    path, data = catalog
    if change == "missing_setuptools":
        data["sources"] = [row for row in data["sources"] if row["id"] != "setuptools-source"]
    elif change == "missing_python":
        data["sources"] = [row for row in data["sources"] if row["id"] != "python-source"]
    elif change == "missing_outer_map":
        del data["python_outer_module_sources"]
    elif change == "invalid_source":
        data["python_module_sources"]["setuptools"] = ["unprovided-source"]
    elif change == "duplicate_source":
        data["python_module_sources"]["setuptools"] *= 2
    elif change == "application_alias":
        data["python_module_sources"]["setuptools"] = ["application"]
    else:
        data["python_outer_module_sources"]["setuptools"] = ["hooks-source"]
    path.write_text(json.dumps(data))
    rows, gaps = inventory.python_source_coverage(["setuptools._vendor.packaging"])
    assert not rows and gaps


def test_empty_source_or_wheel_does_not_claim_build_environment_modules(monkeypatch):
    monkeypatch.setattr(inventory, "_bounded_bytes", lambda *_args: pytest.fail("No frozen code present"))
    assert inventory.python_source_coverage([]) == ([], [])


def test_outer_bootstrap_membership_is_inspected_without_code_execution(tmp_path, monkeypatch):
    frozen_module(tmp_path, "etlayout.exe", "raise RuntimeError('Not executed')")
    outer = tmp_path / "outer.pyc"
    outer.write_bytes(importlib.util.MAGIC_NUMBER + bytes(12) + marshal.dumps(compile("raise RuntimeError('Not executed')", "pyi_rth_new_hook.py", "exec")))
    CArchiveWriter(str(tmp_path / "etlayout.exe"), [
        ("PYZ.pyz", str(tmp_path / "etlayout.exe.pyz"), False, "z"),
        ("pyi_rth_new_hook", str(outer), True, "m")], "python312.dll")
    monkeypatch.setattr(marshal, "load", lambda *_args: pytest.fail("Code unmarshal forbidden"))
    monkeypatch.setattr(marshal, "loads", lambda *_args: pytest.fail("Code unmarshal forbidden"))
    modules, gap = inventory.frozen_modules(tmp_path)
    assert gap is None and modules == ["PIL.ImageFont", "pyi_rth_new_hook"]
