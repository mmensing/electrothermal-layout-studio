# SPDX-License-Identifier: MPL-2.0
"""Bound ZIP directory allocation before constructing stdlib ZipInfo objects.

Canonical project names need substantially less than 64 MiB for 100,000 entries.
The explicit directory byte ceiling also bounds oversized names/comments/extra
fields. Ordinary ZIP and fixed-record ZIP64 are supported; multidisk containers
and unsupported ZIP64 extensions fail closed.
"""
from __future__ import annotations

import os
import struct
from pathlib import Path

from etlayout.models import SimulationError

MAX_DIRECTORY_BYTES = 64 * 1024**2


def inspect_directory(source, *, max_bytes=2_000_000_000, max_entries=100_000,
                      max_directory_bytes=MAX_DIRECTORY_BYTES, cancel=None):
    """Read a bounded tail, fixed ZIP64 record, and bounded 46-byte entry headers.

    Accept a path, bytes, or a seekable binary stream; caller stream positions are
    restored even on failure. This establishes resource bounds, not authenticity,
    safe member names, object integrity, or external-resource authorization.
    """
    def checkpoint():
        if cancel is not None and cancel():
            from etlayout.io.saving import SaveCancelled
            raise SaveCancelled("Archive directory inspection cancelled")
    checkpoint()
    owned = None
    stream = None
    position = None
    if isinstance(source, (str, os.PathLike, Path)):
        owned = stream = Path(source).open("rb")
    elif not isinstance(source, bytes):
        stream = source
        position = stream.tell()
    try:
        if stream is None:
            length = len(source)
            def read_at(offset, count):
                return source[offset:offset + count]
        else:
            stream.seek(0, os.SEEK_END)
            length = stream.tell()
            def read_at(offset, count):
                stream.seek(offset)
                return stream.read(count)
        if length > max_bytes:
            raise SimulationError("Selected archive container exceeds its byte budget")
        if length < 22:
            raise SimulationError("Truncated ZIP end-of-directory record")
        tail_start = max(0, length - 22 - 65535)
        tail = read_at(tail_start, length - tail_start)
        if len(tail) != length - tail_start:
            raise SimulationError("Truncated ZIP directory tail")
        offset = tail.rfind(b"PK\x05\x06")
        while offset >= 0:
            if len(tail) - offset >= 22:
                comment = struct.unpack_from("<H", tail, offset + 20)[0]
                if len(tail) - offset == 22 + comment:
                    break
            offset = tail.rfind(b"PK\x05\x06", 0, offset)
        if offset < 0:
            raise SimulationError("Missing or truncated ZIP end-of-directory record")
        _, disk, directory_disk, disk_count, count, size, directory_offset, _ = struct.unpack_from("<4s4H2LH", tail, offset)
        end_offset = tail_start + offset
        if disk != 0 or directory_disk != 0 or disk_count != count:
            raise SimulationError("Multidisk project archives are unsupported")
        locator_offset = end_offset - 20
        locator = read_at(locator_offset, 20) if locator_offset >= 0 else b""
        zip64 = locator.startswith(b"PK\x06\x07")
        # Exactly 65,535 ordinary entries are emitted without ZIP64 by stdlib;
        # that count alone is ambiguous, and the actual bounded scan resolves it.
        needs_zip64 = size == 0xffffffff or directory_offset == 0xffffffff
        directory_end = end_offset
        if needs_zip64 and not zip64:
            raise SimulationError("Missing ZIP64 directory locator")
        if zip64:
            if len(locator) != 20:
                raise SimulationError("Truncated ZIP64 directory locator")
            _, locator_disk, record_offset, disks = struct.unpack("<4sLQL", locator)
            if locator_disk != 0 or disks != 1:
                raise SimulationError("Multidisk ZIP64 archives are unsupported")
            if record_offset > locator_offset - 56:
                raise SimulationError("Invalid ZIP64 directory offset")
            record = read_at(record_offset, 56)
            if len(record) != 56 or record[:4] != b"PK\x06\x06":
                raise SimulationError("Missing or truncated ZIP64 directory record")
            (_, record_size, _, _, disk64, directory_disk64, disk_count64,
             count64, size64, directory_offset64) = struct.unpack("<4sQ2H2L4Q", record)
            if record_size != 44 or record_offset + 56 != locator_offset:
                raise SimulationError("Unsupported ZIP64 directory extension or offset")
            if disk64 != 0 or directory_disk64 != 0 or disk_count64 != count64:
                raise SimulationError("Multidisk ZIP64 archives are unsupported")
            if ((count != 0xffff and count != count64) or (size != 0xffffffff and size != size64)
                    or (directory_offset != 0xffffffff and directory_offset != directory_offset64)):
                raise SimulationError("Conflicting ZIP and ZIP64 directory declarations")
            count, size, directory_offset = count64, size64, directory_offset64
            directory_end = record_offset
        if count > max_entries:
            raise SimulationError("ZIP central directory exceeds its entry budget")
        if size > max_directory_bytes:
            raise SimulationError("ZIP central directory exceeds its byte budget")
        if directory_offset > directory_end or size > directory_end - directory_offset:
            raise SimulationError("ZIP central directory lies outside the selected container")
        if count and (size < count * 46 or size == 0):
            raise SimulationError("ZIP central directory cannot contain its declared entries")
        # EOCD counts are untrusted. Scan fixed headers without allocating names
        # or ZipInfo objects, stopping at the real entry ceiling even if they lie.
        cursor, actual_count = directory_end - size, 0
        while cursor < directory_end:
            checkpoint()
            header = read_at(cursor, 46)
            if len(header) != 46 or header[:4] != b"PK\x01\x02":
                raise SimulationError("Invalid or unsupported ZIP central-directory record")
            name_size, extra_size, comment_size, entry_disk = struct.unpack_from("<4H", header, 28)
            if entry_disk != 0:
                raise SimulationError("Multidisk ZIP members are unsupported")
            if name_size == 0:
                raise SimulationError("ZIP central-directory member has an empty name")
            cursor += 46 + name_size + extra_size + comment_size
            if cursor > directory_end:
                raise SimulationError("Truncated ZIP central-directory member")
            actual_count += 1
            if actual_count > max_entries:
                raise SimulationError("ZIP central directory exceeds its entry budget")
        if cursor != directory_end or actual_count != count:
            raise SimulationError("ZIP central-directory entry count disagrees with its records")
        return {"entries": actual_count, "directory_bytes": size, "container_bytes": length, "zip64": zip64}
    except (OSError, ValueError, TypeError, struct.error, OverflowError) as error:
        if isinstance(error, SimulationError):
            raise
        raise SimulationError("Cannot inspect the bounded ZIP central directory") from error
    finally:
        if owned is not None:
            owned.close()
        elif stream is not None and position is not None:
            stream.seek(position)
