# SPDX-License-Identifier: MPL-2.0
"""Actual PYZ byte fixtures prove shipping; no frozen module is executed."""
import base64
import hashlib
import importlib.util
import json
import marshal
import struct
from pathlib import Path
from types import SimpleNamespace

import pytest
from PyInstaller.archive.readers import CArchiveReader
from PyInstaller.archive.writers import CArchiveWriter, ZlibArchiveWriter

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("embedded_font_inventory", ROOT / "scripts/create_sbom.py")
inventory = importlib.util.module_from_spec(spec)
spec.loader.exec_module(inventory)


def frozen_module(folder, name, source):
    pyz = folder / (name + ".pyz")
    ZlibArchiveWriter(str(pyz), [("PIL.ImageFont", "ImageFont.py", "PYMODULE")],
                      {"PIL.ImageFont": compile(source, "ImageFont.py", "exec")})
    executable = folder / name
    CArchiveWriter(str(executable), [("PYZ.pyz", str(pyz), False, "z")], "python312.dll")
    return executable


@pytest.fixture
def embedded_case(tmp_path, monkeypatch):
    root, candidate = tmp_path / "source", tmp_path / "candidate"
    (root / "packaging/license-texts").mkdir(parents=True)
    (candidate / "licenses/license-texts").mkdir(parents=True)
    payloads = [b"Synthetic scalable-font bytes", b"Synthetic bitmap descriptor", b"Synthetic bitmap atlas"]
    literals = [base64.b64encode(value) for value in payloads]
    source = ("import base64\n"
              f"def load_default():\n return base64.b64decode({literals[0]!r})\n"
              f"def load_default_imagefont():\n return base64.b64decode({literals[1]!r}),base64.b64decode({literals[2]!r})\n"
              "raise RuntimeError('This frozen module must never execute')\n")
    installed = tmp_path / "ImageFont.py"
    installed.write_bytes(source.encode())
    distribution = SimpleNamespace(version="12.3.0", locate_file=lambda name: installed)
    original_distribution = inventory.metadata.distribution
    monkeypatch.setattr(inventory.metadata, "distribution",
                        lambda name: distribution if name.casefold() == "pillow" else original_distribution(name))
    catalog = {"schema_version": 1, "fonts": []}
    for index, (factory, indices) in enumerate((("load_default", [0]), ("load_default_imagefont", [1, 2]))):
        catalog["fonts"].append({"id": f"synthetic-font-{index}", "name": f"Synthetic font {index}",
            "source_id": "pillow-12.3.0", "module": "PIL.ImageFont", "factory": factory,
            "license_expression": "MIT", "source_sha256": hashlib.sha256(source.encode()).hexdigest(),
            "notice_paths": ["licenses/license-texts/embedded-notice.txt"], "payloads": [
                {"encoded_sha256": hashlib.sha256(literals[i]).hexdigest(),
                 "sha256": hashlib.sha256(payloads[i]).hexdigest(), "bytes": len(payloads[i])} for i in indices]})
    (root / "packaging/embedded-fonts.json").write_text(json.dumps(catalog), encoding="utf8")
    (root / "packaging/third-party-sources.json").write_text(json.dumps({"sources": [{"id": "pillow-12.3.0"}]}))
    for base in (root / "packaging/license-texts", candidate / "licenses/license-texts"):
        (base / "embedded-notice.txt").write_text("Synthetic test font permission notice.\n")
    for name in ("etlayout.exe", "ElectrothermalLayoutStudio.exe"):
        frozen_module(candidate, name, source)
    monkeypatch.setattr(inventory, "ROOT", root)
    return root, candidate, installed, catalog, source


def test_real_pyz_bytes_inventory_binds_both_executables_without_unmarshalling(embedded_case, monkeypatch):
    _root, candidate, _installed, catalog, _source = embedded_case

    def forbidden(*_args, **_kwargs):
        raise AssertionError("Unmarshalling even a module's code object is forbidden")

    monkeypatch.setattr(marshal, "load", forbidden)
    monkeypatch.setattr(marshal, "loads", forbidden)
    modules, gap = inventory.frozen_modules(candidate)
    assert modules == ["PIL.ImageFont"] and gap is None
    fonts, unresolved = inventory.embedded_font_inventory(candidate, "frozen", modules)
    assert not unresolved and len(fonts) == 2
    assert sum(len(font["payloads"]) for font in fonts) == 3
    for actual, expected in zip(fonts, catalog["fonts"]):
        assert actual["payloads"] == expected["payloads"]
        assert {row["executable"] for row in actual["executable_module_evidence"]} == {
            "etlayout.exe", "ElectrothermalLayoutStudio.exe"}
        assert len({row["raw_module_sha256"] for row in actual["executable_module_evidence"]}) == 1
        for row in actual["executable_module_evidence"]:
            assert row["executable_sha256"] == hashlib.sha256((candidate / row["executable"]).read_bytes()).hexdigest()
        assert actual["notices"][0]["sha256"] == hashlib.sha256(
            (candidate / actual["notices"][0]["path"]).read_bytes()).hexdigest()


def test_frozen_sbom_separates_embedded_payloads_from_standalone_fonts(embedded_case, tmp_path):
    root, candidate, _installed, _catalog, _source = embedded_case
    (root / "DEPENDENCIES.json").write_text(json.dumps({"dependencies": [], "native_components": []}))
    (root / "pyproject.toml").write_text('[project]\nversion="0.4.0"\nlicense="MPL-2.0"\n')
    (root / "packaging/binary-publication-policy.json").write_text(json.dumps({"publication_allowed": False}))
    output = tmp_path / "inventory"
    result = inventory.create(candidate, "frozen", output)
    assert len(result["embedded_fonts"]) == 2 and result["font_files"] == []
    assert not any(row.get("component") == "Pillow embedded fonts" for row in result["unresolved_review_items"])
    assert not result["binary_publication_allowed"]
    bom = json.loads((output / "sbom.cdx.json").read_text())
    fonts = [row for row in bom["components"] if row["bom-ref"].startswith("embedded-font:")]
    assert len(fonts) == 2 and sum(len(row["components"]) for row in fonts) == 3
    assert all(row["licenses"] == [{"expression": "MIT"}] for row in fonts)


@pytest.mark.parametrize("change", ["missing_catalog", "source", "payload", "wrong_source_id", "missing_notice",
                                    "notice_bytes", "missing_executable", "missing_literal", "malformed_catalog"])
def test_missing_or_mismatched_embedded_font_evidence_stays_unresolved(embedded_case, change):
    root, candidate, installed, catalog, source = embedded_case
    path = root / "packaging/embedded-fonts.json"
    if change == "missing_catalog":
        path.unlink()
    elif change == "source":
        installed.write_text(source + "# Different source bytes.\n")
    elif change == "payload":
        catalog["fonts"][0]["payloads"][0]["bytes"] += 1
        path.write_text(json.dumps(catalog))
    elif change == "wrong_source_id":
        catalog["fonts"][0]["source_id"] = "unreviewed-source"
        path.write_text(json.dumps(catalog))
    elif change == "missing_notice":
        (candidate / "licenses/license-texts/embedded-notice.txt").unlink()
    elif change == "notice_bytes":
        (candidate / "licenses/license-texts/embedded-notice.txt").write_text("Wrong retained notice")
    elif change == "missing_executable":
        (candidate / "etlayout.exe").unlink()
    elif change == "missing_literal":
        frozen_module(candidate, "etlayout.exe", "value = 'Different module with no font data'\n")
    elif change == "malformed_catalog":
        path.write_text("[]")
    fonts, unresolved = inventory.embedded_font_inventory(candidate, "frozen", ["PIL.ImageFont"])
    assert fonts == [] and len(unresolved) == 1
    assert unresolved[0]["component"] == "Pillow embedded fonts"


@pytest.mark.parametrize("kind,modules", [("source", ["PIL.ImageFont"]), ("wheel", ["PIL.ImageFont"]),
                                        ("frozen", ["PIL.Image"])])
def test_build_environment_pillow_is_not_claimed_as_bundled(tmp_path, kind, modules, monkeypatch):
    monkeypatch.setattr(inventory.metadata, "distribution", lambda *_args: pytest.fail("Unbundled Pillow inspected"))
    assert inventory.embedded_font_inventory(tmp_path, kind, modules) == ([], [])


@pytest.mark.parametrize("data", [b"[" + struct.pack("<i", 2_000_000_000), b"c", b"[\x01\x00\x00\x00r\x00\x00\x00\x00",
                                 marshal.dumps([]) + b"hidden tail"])
def test_primitive_toc_rejects_size_bombs_code_objects_cycles_and_tails(data):
    with pytest.raises(ValueError):
        inventory._primitive_pyz_toc(data)


def test_raw_module_decompression_is_bounded_before_code_unmarshal(tmp_path, monkeypatch):
    executable = frozen_module(tmp_path, "candidate.exe", "large = " + repr(b"X" * 65_536))
    monkeypatch.setattr(inventory, "FONT_MODULE_LIMIT", 512)
    with pytest.raises(ValueError, match="budget"):
        inventory._frozen_pyz(executable, "PIL.ImageFont")


def test_outer_archive_budget_is_checked_before_reader_allocation(tmp_path, monkeypatch):
    path = tmp_path / "large.exe"
    path.write_bytes(b"X" * 65)
    monkeypatch.setattr(inventory, "FROZEN_EXE_LIMIT", 64)
    monkeypatch.setattr(CArchiveReader, "_find_magic_pattern", lambda *_args: pytest.fail("Unbounded reader invoked"))
    with pytest.raises(ValueError, match="budget"):
        inventory._frozen_pyz(path)


def test_source_sbom_does_not_gain_build_environment_embedded_fonts(tmp_path):
    artifact = tmp_path / "source"
    artifact.mkdir()
    (artifact / "source.py").write_text("value=1")
    result = inventory.create(artifact, "source", tmp_path / "inventory")
    assert "embedded_fonts" not in result and not result["font_files"]
    assert not result["binary_publication_allowed"]


def test_pyz_zlib_tail_is_not_ignored(embedded_case):
    _root, candidate, _installed, _catalog, _source = embedded_case
    pyz = candidate / "etlayout.exe.pyz"
    data = pyz.read_bytes()
    offset = struct.unpack("!i", data[8:12])[0]
    toc = inventory._primitive_pyz_toc(data[offset:])
    kind, position, size = toc["PIL.ImageFont"]
    toc["PIL.ImageFont"] = (kind, position, size + 6)
    changed = bytearray(data[:offset] + b"hidden")
    struct.pack_into("!i", changed, 8, offset + 6)
    pyz.write_bytes(changed + marshal.dumps(list(toc.items())))
    CArchiveWriter(str(candidate / "etlayout.exe"), [("PYZ.pyz", str(pyz), False, "z")], "python312.dll")
    fonts, unresolved = inventory.embedded_font_inventory(candidate, "frozen", ["PIL.ImageFont"])
    assert not fonts and unresolved
