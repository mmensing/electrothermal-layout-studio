# SPDX-License-Identifier: MPL-2.0
"""Small real ZIP/ZIP64 directory preflights never construct unbounded ZipInfos."""
from __future__ import annotations

import io
import struct
import zipfile

import pytest

from etlayout.io import archive_v3
from etlayout.io.archive_directory import MAX_DIRECTORY_BYTES, inspect_directory
from etlayout.io.saving import SaveCancelled
from etlayout.models import SimulationError


def ordinary(count=1):
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w", zipfile.ZIP_DEFLATED) as archive:
        for index in range(count):
            archive.writestr(f"object-{index}.json", "{}")
    return output.getvalue()


def zip64(raw, *, count=None):
    original = struct.unpack("<4s4H2LH", raw[-22:])
    prefix = raw[:-22]
    count = original[4] if count is None else count
    record = struct.pack("<4sQ2H2L4Q", b"PK\x06\x06", 44, 45, 45, 0, 0, count, count, original[5], original[6])
    locator = struct.pack("<4sLQL", b"PK\x06\x07", 0, len(prefix), 1)
    end = struct.pack("<4s4H2LH", b"PK\x05\x06", 0, 0, 0xffff, 0xffff, 0xffffffff, 0xffffffff, 0)
    return prefix + record + locator + end


@pytest.mark.parametrize("extended", [False, True])
def test_valid_directory_paths_bytes_streams_and_zip64_preserve_position(tmp_path, extended):
    raw = ordinary(3)
    if extended:
        raw = zip64(raw)
    stream = io.BytesIO(raw)
    stream.seek(7)
    result = inspect_directory(stream)
    assert stream.tell() == 7
    assert result["entries"] == 3 and result["zip64"] == extended
    assert inspect_directory(raw) == result
    target = tmp_path / "valid.etl"
    target.write_bytes(raw)
    assert inspect_directory(target) == result
    with zipfile.ZipFile(target) as archive:
        assert len(archive.infolist()) == 3


@pytest.mark.parametrize("attack", ["entry_count", "directory_bytes", "multidisk", "truncated", "lying_count"])
def test_malformed_directories_fail_before_zipinfo_allocation(monkeypatch, attack):
    raw = ordinary()
    end = list(struct.unpack("<4s4H2LH", raw[-22:]))
    if attack == "entry_count":
        raw = zip64(raw, count=100001)
    elif attack == "directory_bytes":
        end[5] = MAX_DIRECTORY_BYTES + 1
        raw = raw[:-22] + struct.pack("<4s4H2LH", *end)
    elif attack == "multidisk":
        end[1] = 1
        raw = raw[:-22] + struct.pack("<4s4H2LH", *end)
    elif attack == "truncated":
        raw = raw[:-1]
    else:
        end[3] = end[4] = 0
        raw = raw[:-22] + struct.pack("<4s4H2LH", *end)
    def forbidden(*args, **kwargs):
        pytest.fail("Directory resource bounds must precede stdlib ZipInfo allocation")
    monkeypatch.setattr(archive_v3.zipfile, "ZipFile", forbidden)
    with pytest.raises(SimulationError):
        archive_v3.ArchiveV3Reader(raw)


def test_actual_header_count_cannot_bypass_the_entry_limit(monkeypatch):
    from etlayout.io import project
    raw = ordinary(3)
    end = list(struct.unpack("<4s4H2LH", raw[-22:]))
    end[3] = end[4] = 1
    raw = raw[:-22] + struct.pack("<4s4H2LH", *end)
    monkeypatch.setattr(project, "MAX_ARCHIVE_ENTRIES", 2)
    def forbidden(*args, **kwargs):
        pytest.fail("A forged low EOCD count must not allocate ZipInfo objects")
    monkeypatch.setattr(archive_v3.zipfile, "ZipFile", forbidden)
    with pytest.raises(SimulationError, match="entry budget"):
        archive_v3.ArchiveV3Reader(raw)


def test_failure_and_cancellation_restore_caller_stream_position():
    stream = io.BytesIO(ordinary(3))
    stream.seek(9)
    with pytest.raises(SimulationError, match="entry budget"):
        inspect_directory(stream, max_entries=2)
    assert stream.tell() == 9
    checks = 0
    def cancel():
        nonlocal checks
        checks += 1
        return checks > 1
    with pytest.raises(SaveCancelled):
        inspect_directory(stream, cancel=cancel)
    assert stream.tell() == 9
