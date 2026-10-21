# SPDX-License-Identifier: MPL-2.0
"""Independent small PE fixtures exercise exact permitted packaging changes."""

from __future__ import annotations

import importlib.util
import json
import struct
from pathlib import Path

import pefile
import pytest

SPEC = importlib.util.spec_from_file_location(
    "windows_runtime_packaging", Path(__file__).resolve().parents[1] / "packaging/windows_runtime.py"
)
runtime = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(runtime)
ORIGINAL_RUNTIME_PATHS = tuple(runtime.REVIEWED_RUNTIME_FILES)


def pe_fixture(dll_name=runtime.NUMPY_ALIAS, *, signed=False, bound=False, delayed=False, machine=0x8664):
    """Build PE32+ headers, code and named-import thunks without a compiler."""
    data = bytearray(0x608 if signed else 0x600)
    data[:2] = b"MZ"
    struct.pack_into("<I", data, 0x3C, 0x80)
    data[0x80:0x84] = b"PE\0\0"
    struct.pack_into("<HHIIIHH", data, 0x84, machine, 2, 0, 0, 0, 240, 0x2022)
    opt = 0x98
    struct.pack_into("<H", data, opt, 0x20B)
    struct.pack_into("<IIII", data, opt + 4, 0x200, 0x200, 0, 0x1000)
    struct.pack_into("<I", data, opt + 20, 0x1000)
    struct.pack_into("<QII", data, opt + 24, 0x140000000, 0x1000, 0x200)
    struct.pack_into("<HH", data, opt + 40, 6, 0)
    struct.pack_into("<III", data, opt + 56, 0x3000, 0x200, 0)
    struct.pack_into("<HH", data, opt + 68, 3, 0x160)
    struct.pack_into("<QQQQII", data, opt + 72, 0x100000, 0x1000, 0x100000, 0x1000, 0, 16)
    directories = opt + 112
    if delayed:
        struct.pack_into("<II", data, directories + 13 * 8, 0x20E0, 64)
        struct.pack_into("<8I", data, 0x4E0, 1, 0x2080, 0, 0x2060, 0x2050, 0, 0, 0)
    else:
        struct.pack_into("<II", data, directories + 8, 0x2000, 40)
        struct.pack_into("<5I", data, 0x400, 0x2050, 0, 0, 0x2080, 0x2060)
    if signed:
        struct.pack_into("<II", data, directories + 4 * 8, 0x600, 8)
        struct.pack_into("<IHH", data, 0x600, 8, 0x200, 2)
    if bound:
        struct.pack_into("<II", data, directories + 11 * 8, 0x20E0, 8)
    for i, (name, va, offset, flags) in enumerate(
        ((b".text", 0x1000, 0x200, 0x60000020), (b".rdata", 0x2000, 0x400, 0x40000040))
    ):
        section = opt + 240 + i * 40
        struct.pack_into("<8sIIIIIIHHI", data, section, name, 0x200, va, 0x200, offset, 0, 0, 0, 0, flags)
    data[0x200] = 0xC3
    struct.pack_into("<Q", data, 0x450, 0x20C0)
    struct.pack_into("<Q", data, 0x460, 0x20C0)
    name = dll_name.encode("ascii") + b"\0"
    data[0x480 : 0x480 + len(name)] = name
    data[0x4C0:0x4CE] = b"\0\0test_symbol\0"
    return bytes(data)


def transform(data):
    return runtime.rewrite_runtime_import(
        data, expected_sha256=runtime.sha256(data), alias=runtime.NUMPY_ALIAS
    )


def test_exact_native_change_preserves_code_import_functions_and_layout():
    original = pe_fixture()
    changed, receipt = transform(original)
    before = pefile.PE(data=original)
    after = pefile.PE(data=changed)
    try:
        assert before.DIRECTORY_ENTRY_IMPORT[0].dll == runtime.NUMPY_ALIAS.encode()
        assert after.DIRECTORY_ENTRY_IMPORT[0].dll == b"msvcp140.dll"
        assert after.DIRECTORY_ENTRY_IMPORT[0].imports[0].name == b"test_symbol"
        assert original[0x200:0x400] == changed[0x200:0x400]
        assert len(original) == len(changed) and after.verify_checksum()
        permitted = set(range(0x480, 0x480 + len(runtime.NUMPY_ALIAS) + 1)) | set(range(0xD8, 0xDC))
        assert {
            i for i, pair in enumerate(zip(original, changed, strict=True)) if pair[0] != pair[1]
        } <= permitted
        assert receipt["executable_code_unchanged"] and receipt["imported_functions_and_ordinals_unchanged"]
        assert transform(original)[0] == changed
    finally:
        before.close()
        after.close()


@pytest.mark.parametrize(
    "changes,message",
    [
        ({"signed": True}, "signed"),
        ({"bound": True}, "Bound"),
        ({"delayed": True}, "Delayed"),
        ({"machine": 0x14C}, "x64"),
    ],
)
def test_unsupported_pe_cases_are_rejected(changes, message):
    with pytest.raises(ValueError, match=message):
        transform(pe_fixture(**changes))


def test_wrong_input_hash_is_rejected_before_parsing():
    with pytest.raises(ValueError, match="reviewed hash"):
        runtime.rewrite_runtime_import(b"not a PE", expected_sha256="0" * 64, alias=runtime.NUMPY_ALIAS)


@pytest.fixture
def candidate(tmp_path, monkeypatch):
    folder = tmp_path / "candidate"
    native = folder / "_internal/native.pyd"
    native.parent.mkdir(parents=True)
    original = pe_fixture()
    native.write_bytes(original)
    output, _ = transform(original)
    rule = {
        "path": "_internal/native.pyd",
        "before_sha256": runtime.sha256(original),
        "after_sha256": runtime.sha256(output),
        "alias": runtime.NUMPY_ALIAS,
        "package": "synthetic PE fixture",
    }
    omitted = folder / "_internal" / runtime.NUMPY_ALIAS
    omitted.write_bytes(b"reviewed runtime fixture")
    monkeypatch.setattr(runtime, "REVIEWED_IMPORTS", (rule,))
    monkeypatch.setattr(
        runtime,
        "REVIEWED_RUNTIME_FILES",
        {omitted.relative_to(folder).as_posix(): runtime.file_sha256(omitted)},
    )
    return folder, native, omitted, original


def test_preparation_is_hash_bound_idempotent_and_records_modifications(candidate):
    folder, native, omitted, original = candidate
    receipt = runtime.prepare_runtime_prerequisite(folder)
    assert not omitted.exists() and native.read_bytes() != original
    assert receipt["required_system_runtime_dlls"] == ["msvcp140.dll"]
    assert receipt["installer_bundled_or_executed"] is False
    assert receipt["microsoft_runtime_files_bundled"] is False
    assert receipt["license_or_publication_clearance"] is False
    assert (folder / runtime.NOTICE_NAME).is_file()
    metadata = (folder / runtime.MANIFEST_NAME).read_bytes()
    assert runtime.prepare_runtime_prerequisite(folder) == receipt
    assert (folder / runtime.MANIFEST_NAME).read_bytes() == metadata


@pytest.mark.parametrize("failure", ["changed_consumer", "unexpected_runtime", "unsupported_import"])
def test_preflight_failures_leave_every_original_file_untouched(candidate, failure):
    folder, native, omitted, original = candidate
    if failure == "changed_consumer":
        native.write_bytes(original + b"changed")
    elif failure == "unexpected_runtime":
        (folder / "_internal/concrt140.dll").write_bytes(b"unreviewed")
    else:
        (folder / "_internal/extra.pyd").write_bytes(pe_fixture("concrt999.dll"))
    before = {p.relative_to(folder): p.read_bytes() for p in folder.rglob("*") if p.is_file()}
    with pytest.raises(ValueError):
        runtime.prepare_runtime_prerequisite(folder)
    assert {p.relative_to(folder): p.read_bytes() for p in folder.rglob("*") if p.is_file()} == before
    assert omitted.exists()


@pytest.mark.parametrize("change", ["transformation", "modified_date_utc", "modifications", "notice"])
def test_prepared_candidate_requires_untampered_reviewed_manifest(candidate, change):
    folder, _, _, _ = candidate
    runtime.prepare_runtime_prerequisite(folder)
    path = folder / runtime.MANIFEST_NAME
    data = json.loads(path.read_text())
    if change == "notice":
        (folder / runtime.NOTICE_NAME).write_text("tampered notice")
    else:
        data[change] = "unreviewed"
        path.write_text(json.dumps(data))
    with pytest.raises(ValueError):
        runtime.prepare_runtime_prerequisite(folder)


@pytest.fixture
def full_profile_candidate(candidate, tmp_path, monkeypatch):
    folder, native, omitted, original = candidate
    omitted.unlink()
    # Retain all twelve production paths, with independent small fixture bytes.
    reviewed = {}
    for name in ORIGINAL_RUNTIME_PATHS:
        path = folder / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(("reviewed runtime fixture: " + name).encode())
        reviewed[name] = runtime.file_sha256(path)
    monkeypatch.setattr(runtime, "REVIEWED_RUNTIME_FILES", reviewed)
    catalog = {"compiled_native": [{"id": "shiboken6-runtime", "version": "6.12.0+etlayout.1",
                "filename": "shiboken6.abi3.dll", "abi": "cp310-abi3-win_amd64"}]}
    row = catalog["compiled_native"][0]
    for name, key in (("shiboken6.abi3.dll", "sha256"),
                      ("Shiboken.pyd", "unchanged_module_sha256"),
                      ("ETLAYOUT_RUNTIME_BUILD.json", "receipt_sha256")):
        path = folder / "_internal/shiboken6" / name
        path.write_bytes(pe_fixture("msvcp140.dll") if name.endswith((".dll", ".pyd"))
                         else b'{"synthetic": "audited source-build receipt fixture"}')
        row[key] = runtime.file_sha256(path)
    source_catalog = tmp_path / "source-catalog.json"
    source_catalog.write_text(json.dumps(catalog), encoding="utf8")
    monkeypatch.setattr(runtime, "SOURCE_CATALOG", source_catalog)
    return folder, native, original, reviewed


@pytest.mark.parametrize("source_built,count", [(False, 12), (True, 9)])
def test_complete_runtime_profiles_record_only_actual_omissions_and_reenter(full_profile_candidate,
                                                                          source_built, count):
    folder, native, original, reviewed = full_profile_candidate
    expected = dict(reviewed)
    if source_built:
        for name in runtime.SHIBOKEN_RUNTIME_COPIES:
            (folder / name).unlink()
            expected.pop(name)
    if not source_built:
        (folder / "_internal/shiboken6/ETLAYOUT_RUNTIME_BUILD.json").unlink()
        (folder / "_internal/shiboken6/shiboken6.abi3.dll").write_bytes(pe_fixture("msvcp140.dll") + b"stock")
    receipt = runtime.prepare_runtime_prerequisite(folder)
    assert native.read_bytes() != original
    assert receipt["omitted_runtime_files"] == expected
    assert len(receipt["omitted_runtime_files"]) == count
    assert not any((folder / name).exists() for name in reviewed)
    assert runtime.prepare_runtime_prerequisite(folder) == receipt


@pytest.mark.parametrize("failure", ["missing_common", "changed_common", "extra_runtime", "partial_stock",
                                      "dll", "module", "receipt", "missing_receipt", "stock_version"])
def test_source_built_profile_requires_exact_copies_and_native_identity(full_profile_candidate, failure):
    folder, _, _, reviewed = full_profile_candidate
    for name in runtime.SHIBOKEN_RUNTIME_COPIES:
        (folder / name).unlink()
    common = folder / next(name for name in reviewed if name not in runtime.SHIBOKEN_RUNTIME_COPIES)
    if failure == "missing_common":
        common.unlink()
    elif failure == "changed_common":
        common.write_bytes(b"changed runtime")
    elif failure == "extra_runtime":
        (folder / "_internal/concrt140.dll").write_bytes(b"unreviewed runtime")
    elif failure == "partial_stock":
        (folder / next(iter(runtime.SHIBOKEN_RUNTIME_COPIES))).write_bytes(b"partial legacy wheel")
    elif failure == "stock_version":
        catalog = json.loads(runtime.SOURCE_CATALOG.read_text())
        catalog["compiled_native"][0]["version"] = "6.12.0"
        runtime.SOURCE_CATALOG.write_text(json.dumps(catalog))
    else:
        name = {"dll": "shiboken6.abi3.dll", "module": "Shiboken.pyd",
                "receipt": "ETLAYOUT_RUNTIME_BUILD.json", "missing_receipt": "ETLAYOUT_RUNTIME_BUILD.json"}[failure]
        path = folder / "_internal/shiboken6" / name
        path.unlink() if failure == "missing_receipt" else path.write_bytes(b"unreviewed native payload")
    before = {p.relative_to(folder): p.read_bytes() for p in folder.rglob("*") if p.is_file()}
    with pytest.raises(ValueError):
        runtime.prepare_runtime_prerequisite(folder)
    assert {p.relative_to(folder): p.read_bytes() for p in folder.rglob("*") if p.is_file()} == before


@pytest.mark.parametrize("failure", ["native_identity", "omitted_map", "legacy_omitted_map"])
def test_prepared_source_built_profile_revalidates_identity_and_exact_record(full_profile_candidate, failure):
    folder, _, _, _ = full_profile_candidate
    for name in runtime.SHIBOKEN_RUNTIME_COPIES:
        (folder / name).unlink()
    runtime.prepare_runtime_prerequisite(folder)
    if failure == "native_identity":
        (folder / "_internal/shiboken6/shiboken6.abi3.dll").write_bytes(b"changed")
    else:
        path = folder / runtime.MANIFEST_NAME
        manifest = json.loads(path.read_text())
        if failure == "legacy_omitted_map":
            manifest["omitted_runtime_files"] = runtime.REVIEWED_RUNTIME_FILES
        else:
            manifest["omitted_runtime_files"].pop(next(iter(manifest["omitted_runtime_files"])))
        path.write_text(json.dumps(manifest))
    before = {p.relative_to(folder): p.read_bytes() for p in folder.rglob("*") if p.is_file()}
    with pytest.raises(ValueError):
        runtime.prepare_runtime_prerequisite(folder)
    assert {p.relative_to(folder): p.read_bytes() for p in folder.rglob("*") if p.is_file()} == before


@pytest.mark.parametrize("source_built", [False, True])
def test_runtime_inventory_paths_are_case_insensitive_but_record_is_canonical(full_profile_candidate,
                                                                            source_built):
    folder, _, _, reviewed = full_profile_candidate
    expected = dict(reviewed)
    for name in reviewed:
        path = folder / name
        if source_built and name in runtime.SHIBOKEN_RUNTIME_COPIES:
            path.unlink()
            expected.pop(name)
        else:
            path.rename(path.with_name(path.name.lower()))
    if not source_built:
        (folder / "_internal/shiboken6/ETLAYOUT_RUNTIME_BUILD.json").unlink()
        (folder / "_internal/shiboken6/shiboken6.abi3.dll").write_bytes(pe_fixture("msvcp140.dll") + b"stock")
    receipt = runtime.prepare_runtime_prerequisite(folder)
    assert receipt["omitted_runtime_files"] == expected
    assert runtime.prepare_runtime_prerequisite(folder) == receipt
    manifest_path = folder / runtime.MANIFEST_NAME
    receipt["omitted_runtime_files"] = {key.casefold(): value for key, value in expected.items()}
    manifest_path.write_text(json.dumps(receipt))
    with pytest.raises(ValueError):
        runtime.prepare_runtime_prerequisite(folder)
