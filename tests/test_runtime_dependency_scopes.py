# SPDX-License-Identifier: MPL-2.0
"""Frozen hook ownership and runtime licenses follow actual installed evidence."""
import base64
import csv
import hashlib
import importlib.metadata as metadata
import importlib.util
import json
import marshal
from pathlib import Path

import pytest
from PyInstaller.archive.writers import CArchiveWriter, ZlibArchiveWriter

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("runtime_dependency_inventory", ROOT / "scripts/create_sbom.py")
inventory = importlib.util.module_from_spec(spec)
spec.loader.exec_module(inventory)

pytestmark = pytest.mark.fast


def installed_distribution(site, name, files, state="valid"):
    """Write real dist-info/RECORD evidence; source files are never imported."""
    folder = site / (name.replace("-", "_") + "-1.0.dist-info")
    folder.mkdir(parents=True)
    (folder / "METADATA").write_text(f"Metadata-Version: 2.3\nName: {name}\nVersion: 1.0\n")
    rows = []
    for relative, content in files.items():
        source = site / relative
        source.parent.mkdir(parents=True, exist_ok=True)
        source.write_bytes(content)
        encoded = base64.urlsafe_b64encode(hashlib.sha256(content).digest()).rstrip(b"=").decode()
        rows.append((relative, "" if state == "missing_hash" else "sha256=" + encoded,
                     str(len(content) + (1 if state == "wrong_size" else 0))))
        if state == "tampered":
            source.write_bytes(content + b"# changed after RECORD\n")
    if state != "missing_record":
        with (folder / "RECORD").open("w", newline="") as stream:
            csv.writer(stream).writerows(rows)
    return metadata.PathDistribution(folder)


@pytest.mark.parametrize("state", ["valid", "missing_record", "missing_hash", "wrong_size", "tampered", "misplaced", "foreign_owner", "bare_unscoped"])
def test_hook_alias_requires_exact_scoped_record(tmp_path, monkeypatch, state):
    prefix = "_pyinstaller_hooks_contrib/rthooks/"
    if state == "misplaced":
        prefix = "_pyinstaller_hooks_contrib/ordinary/"
    elif state == "bare_unscoped":
        prefix = ""
    owner = "foreign-hooks" if state == "foreign_owner" else "pyinstaller-hooks-contrib"
    dist = installed_distribution(tmp_path, owner, {prefix + "pyi_rth_extra.py": b"raise RuntimeError('Never imported')\n"}, state)
    monkeypatch.setattr(inventory.metadata, "distributions", lambda **_kwargs: [dist])
    modules, _paths = inventory.distribution_evidence()
    if state == "valid":
        assert modules["pyi_rth_extra"] == {"pyinstaller-hooks-contrib"}
    else:
        assert "pyi_rth_extra" not in modules


@pytest.fixture
def frozen_case(tmp_path, monkeypatch):
    root, candidate, site = tmp_path / "source", tmp_path / "candidate", tmp_path / "site"
    (root / "packaging").mkdir(parents=True)
    candidate.mkdir()
    (root / "pyproject.toml").write_text('[project]\nname="etlayout"\nversion="0.4.0"\nlicense="MPL-2.0"\n')
    (root / "packaging/binary-publication-policy.json").write_text('{"publication_allowed":false}')
    dependencies = [
        {"name": "pyinstaller", "version": "1.0", "scope": "runtime-environment", "license_expression": "GPL-2.0-or-later WITH Bootloader-exception", "upstream_urls": []},
        {"name": "pyinstaller-hooks-contrib", "version": "1.0", "scope": "runtime-environment", "license_expression": "GPL-2.0-or-later AND Apache-2.0", "frozen_subset_license_expression": "Apache-2.0", "upstream_urls": []}]
    (root / "DEPENDENCIES.json").write_text(json.dumps({"dependencies": dependencies, "native_components": []}))
    sources = {"sources": [{"id": name} for name in ["python-source", "core-source", "extra-source"]],
        "runtime_python": {"source_id": "python-source"}, "python_module_sources": {"etlayout": ["application"]},
        "python_outer_module_sources": {"pyi_rth_extra": ["extra-source"]}}
    (root / "packaging/third-party-sources.json").write_text(json.dumps(sources))
    monkeypatch.setattr(inventory, "ROOT", root)
    monkeypatch.setattr(inventory.metadata, "version", lambda _name: "1.0")
    hook = tmp_path / "pyi_rth_extra.pyc"
    hook.write_bytes(importlib.util.MAGIC_NUMBER + bytes(12) + marshal.dumps(compile("raise RuntimeError('Never executed')", "pyi_rth_extra.py", "exec")))
    pyz = tmp_path / "modules.pyz"
    ZlibArchiveWriter(str(pyz), [("etlayout.cli", "etlayout/cli.py", "PYMODULE")],
                      {"etlayout.cli": compile("raise RuntimeError('Never executed')", "etlayout/cli.py", "exec")})
    for name in ("etlayout.exe", "ElectrothermalLayoutStudio.exe"):
        CArchiveWriter(str(candidate / name), [("PYZ.pyz", str(pyz), False, "z"),
                       ("pyi_rth_extra", str(hook), True, "m")], "python312.dll")
    # Load the trusted reader before forbidding code-object unmarshalling; lazy
    # importlib loading of its installed .pyc is separate from artifact parsing.
    importlib.import_module("PyInstaller.archive.readers")
    return root, candidate, site, dependencies


@pytest.mark.parametrize("state", ["valid", "missing_record", "ambiguous"])
def test_actual_frozen_hook_membership_uses_verified_record_and_runtime_grant(frozen_case, tmp_path, monkeypatch, state):
    root, candidate, site, dependencies = frozen_case
    hook_source = b"raise RuntimeError('Never imported')\n"
    distributions = [installed_distribution(site, "pyinstaller-hooks-contrib",
        {"_pyinstaller_hooks_contrib/rthooks/pyi_rth_extra.py": hook_source}, state)]
    if state == "ambiguous":
        distributions.append(installed_distribution(site, "pyinstaller",
            {"PyInstaller/hooks/rthooks/pyi_rth_extra.py": hook_source}))
    monkeypatch.setattr(inventory.metadata, "distributions", lambda **_kwargs: distributions)
    monkeypatch.setattr(marshal, "loads", lambda *_args: pytest.fail("Code unmarshal forbidden"))
    result = inventory.create(candidate, "frozen", tmp_path / "output")
    shipped = {row["name"]: row for row in result["shipped_runtime_dependencies"]}
    hooks = [row for row in result["unresolved_review_items"] if row.get("component") == "pyi_rth_extra"]
    if state != "valid":
        assert hooks and "pyinstaller-hooks-contrib" not in shipped
        assert state != "ambiguous" or result["ambiguous_record_ownership"]
        return
    assert not hooks
    assert shipped["pyinstaller-hooks-contrib"]["license_expression"] == "Apache-2.0"
    assert shipped["pyinstaller-hooks-contrib"]["source_license_expression"] == dependencies[1]["license_expression"]
    assert "pyinstaller-hooks-contrib" not in {row["name"] for row in result["runtime_environment_not_bundled"]}
    bom = json.loads((tmp_path / "output/sbom.cdx.json").read_text())
    record = next(row for row in bom["components"] if row.get("bom-ref") == "pkg:pyinstaller-hooks-contrib")
    assert record["licenses"] == [{"expression": "Apache-2.0"}]
    assert {row["name"]: row["value"] for row in record["properties"]}["etlayout:source-license-expression"] == dependencies[1]["license_expression"]


@pytest.mark.parametrize("expression", ["", "NOASSERTION"])
def test_unreviewed_runtime_expression_remains_a_visible_gap(frozen_case, tmp_path, monkeypatch, expression):
    root, candidate, site, dependencies = frozen_case
    dependencies[1]["frozen_subset_license_expression"] = expression
    (root / "DEPENDENCIES.json").write_text(json.dumps({"dependencies": dependencies, "native_components": []}))
    dist = installed_distribution(site, "pyinstaller-hooks-contrib", {"_pyinstaller_hooks_contrib/rthooks/pyi_rth_extra.py": b"pass\n"})
    monkeypatch.setattr(inventory.metadata, "distributions", lambda **_kwargs: [dist])
    result = inventory.create(candidate, "frozen", tmp_path / "output")
    assert any(row.get("component") == "pyinstaller-hooks-contrib" and "license expression" in row["reason"] for row in result["unresolved_review_items"])


@pytest.mark.parametrize("state", ["valid", "missing", "empty", "unsafe"])
def test_actual_frozen_dependency_requires_declared_supplier_notice(frozen_case, tmp_path, monkeypatch, state):
    root, candidate, site, dependencies = frozen_case
    notice = "licenses/license-texts/supplier.txt"
    dependencies[1]["frozen_notice_files"] = ["../private.txt" if state == "unsafe" else notice]
    (root / "DEPENDENCIES.json").write_text(json.dumps({"dependencies": dependencies, "native_components": []}))
    if state != "missing":
        target = candidate / notice
        target.parent.mkdir(parents=True)
        target.write_bytes(b"" if state == "empty" else b"Copyright original supplier; full permission retained.\n")
    dist = installed_distribution(site, "pyinstaller-hooks-contrib", {"_pyinstaller_hooks_contrib/rthooks/pyi_rth_extra.py": b"pass\n"})
    monkeypatch.setattr(inventory.metadata, "distributions", lambda **_kwargs: [dist])
    result = inventory.create(candidate, "frozen", tmp_path / "output")
    row = next(row for row in result["shipped_runtime_dependencies"] if row["name"] == "pyinstaller-hooks-contrib")
    gaps = [row for row in result["unresolved_review_items"] if row.get("component") == "pyinstaller-hooks-contrib" and "supplemental notice" in row["reason"]]
    if state == "valid":
        assert not gaps
        assert row["supplemental_notices"] == [{"path": notice, "sha256": hashlib.sha256((candidate / notice).read_bytes()).hexdigest()}]
    else:
        assert gaps and not row["supplemental_notices"]
