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
