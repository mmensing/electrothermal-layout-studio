# SPDX-License-Identifier: MPL-2.0
"""Archive, wheel and dependency contracts; actual MSVC builds run in Windows CI."""
import base64
import csv
import hashlib
import importlib.util
import io
import tarfile
import zipfile
from email import policy
from email.parser import BytesParser
from pathlib import Path

import pytest
from packaging.requirements import Requirement

ROOT = Path(__file__).resolve().parents[1]
RECIPE = ROOT / "packaging/source-recipes/shiboken6-6.12.0"
spec = importlib.util.spec_from_file_location("shiboken_runtime_build", RECIPE / "build_runtime.py")
builder = importlib.util.module_from_spec(spec)
spec.loader.exec_module(builder)


@pytest.fixture
def upstream_payloads():
    return {
        "shiboken6/Shiboken.pyd": b"unchanged public module",
        "shiboken6/shiboken6.abi3.dll": b"old native runtime",
        "shiboken6/shiboken6.abi3.lib": b"old import library",
        "shiboken6/__init__.py": b"# Retained upstream API\n",
        "shiboken6/msvcp140.dll": b"system prerequisite; do not bundle",
        builder.OLD_INFO + "/METADATA": (
            b"Metadata-Version: 2.4\nName: shiboken6\nVersion: 6.12.0\n"
            b"Author: Qt for Python Team\nLicense: LGPL-3.0-only OR GPL-3.0-only\n"
            b"License-File: LicenseRef-Qt-Commercial.txt\n\nOriginal upstream description.\n"),
        builder.OLD_INFO + "/WHEEL": (
            b"Wheel-Version: 1.0\nRoot-Is-Purelib: false\nTag: cp310-abi3-win_amd64\n"),
        builder.OLD_INFO + "/licenses/LicenseRef-Qt-Commercial.txt": b"Retained upstream license text",
        builder.OLD_INFO + "/RECORD": b"old record is superseded",
    }


def test_custom_wheel_keeps_api_and_notices_with_truthful_native_record(tmp_path, upstream_payloads):
    outputs = builder.make_wheel(upstream_payloads, b"rebuilt runtime", b"rebuilt import library",
                                 {"patch_sha256": "a" * 64}, b"GPL text fixture")
    metadata = BytesParser(policy=policy.default).parsebytes(outputs[builder.NEW_INFO + "/METADATA"])
    assert metadata["Version"] == builder.VERSION
    assert metadata["Author"] == "Qt for Python Team"
    assert metadata["License-Expression"] == "GPL-3.0-only"
    assert "License" not in metadata
    wheel_metadata = BytesParser(policy=policy.default).parsebytes(outputs[builder.NEW_INFO + "/WHEEL"])
    assert wheel_metadata["Generator"] == "etlayout source-runtime recipe (" + builder.VERSION + ")"
    assert wheel_metadata["Tag"] == "cp310-abi3-win_amd64"
    assert Requirement("shiboken6==6.12.0").specifier.contains(metadata["Version"])
    assert outputs["shiboken6/Shiboken.pyd"] == upstream_payloads["shiboken6/Shiboken.pyd"]
    assert outputs["shiboken6/__init__.py"] == upstream_payloads["shiboken6/__init__.py"]
    assert outputs["shiboken6/shiboken6.abi3.dll"] == b"rebuilt runtime"
    assert outputs["shiboken6/shiboken6.abi3.lib"] == b"rebuilt import library"
    assert outputs[builder.NEW_INFO + "/licenses/LicenseRef-Qt-Commercial.txt"] == b"Retained upstream license text"
    assert not any("msvcp140.dll" in name or builder.OLD_INFO in name for name in outputs)
    records = list(csv.reader(io.StringIO(outputs[builder.NEW_INFO + "/RECORD"].decode())))
    assert len(records) == len(outputs) == len({row[0] for row in records})
    for name, digest, size in records:
        if name.endswith("/RECORD"):
            assert digest == size == ""
        else:
            data = outputs[name]
            expected = base64.urlsafe_b64encode(hashlib.sha256(data).digest()).rstrip(b"=").decode()
            assert digest == "sha256=" + expected
            assert int(size) == len(data)
    path = tmp_path / builder.OUTPUT_WHEEL
    builder.write_zip(path, outputs)
    with zipfile.ZipFile(path) as archive:
        assert archive.testzip() is None
        assert {name: archive.read(name) for name in archive.namelist()} == outputs
    with pytest.raises(FileExistsError):
        builder.write_zip(path, outputs)


@pytest.mark.parametrize("name", ["../outside", "/absolute", "C:/drive", "inside/../outside",
                                  "inside\\outside", "inside/./other", "inside//other"])
def test_archive_member_cannot_escape_or_alias_a_destination(name):
    with pytest.raises(ValueError, match="Unsafe archive member"):
        builder.safe_member(name)


def test_upstream_wheel_hash_and_namespace_are_required(tmp_path, upstream_payloads):
    path = tmp_path / "upstream.whl"
    builder.write_zip(path, upstream_payloads)
    assert builder.wheel_payloads(path, expected_sha256=builder.sha256(path)) == upstream_payloads
    with pytest.raises(ValueError, match="locked input"):
        builder.wheel_payloads(path)
    unsafe = tmp_path / "unexpected.whl"
    builder.write_zip(unsafe, {**upstream_payloads, "unrelated/payload": b"unexpected"})
    with pytest.raises(ValueError, match="Unexpected upstream wheel package"):
        builder.wheel_payloads(unsafe, expected_sha256=builder.sha256(unsafe))


def test_source_extraction_accepts_audited_change_record_without_links(tmp_path, monkeypatch):
    path = tmp_path / builder.SOURCE_NAME
    members = {builder.SOURCE_ROOT + "/" + builder.PATCH_TARGET: b"preferred C++ source\n",
               "SOURCE_ARCHIVE_CHANGES.json": b"{}"}
    with tarfile.open(path, "w:gz") as archive:
        for name, data in members.items():
            member = tarfile.TarInfo(name)
            member.size = len(data)
            archive.addfile(member, io.BytesIO(data))
    monkeypatch.setattr(builder, "SOURCE_BYTES", path.stat().st_size)
    monkeypatch.setattr(builder, "SOURCE_SHA256", builder.sha256(path))
    source = builder.unpack_source(path, tmp_path / "source")
    assert (source / builder.PATCH_TARGET).read_bytes() == b"preferred C++ source\n"
    assert (source.parent / "SOURCE_ARCHIVE_CHANGES.json").read_bytes() == b"{}"
    with pytest.raises(FileExistsError):
        builder.unpack_source(path, tmp_path / "source")


@pytest.mark.parametrize("overlap", ["same", "output-inside", "build-inside"])
def test_build_paths_reject_overlap_before_creating_directories(tmp_path, overlap):
    build = tmp_path / "fresh"
    output = {"same": build, "output-inside": build / "output", "build-inside": tmp_path}[overlap]
    with pytest.raises(ValueError, match="non-overlapping"):
        builder.checked_directories(build, output)
    assert not build.exists()


def test_existing_output_is_never_replaced(tmp_path):
    build, output = tmp_path / "build", tmp_path / "output"
    output.mkdir()
    (output / "regression.xml").write_text("existing evidence")
    with pytest.raises(ValueError, match="cannot be replaced"):
        builder.checked_directories(build, output)
    assert (output / "regression.xml").read_text() == "existing evidence"
    assert not build.exists()
