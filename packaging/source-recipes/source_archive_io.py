# SPDX-License-Identifier: MPL-2.0
"""Finite TAR metadata/member preflight shared by source-asset recipes."""
from __future__ import annotations

import tarfile
from contextlib import contextmanager
from pathlib import PurePosixPath

MAX_COMPRESSED = 2 * 2**30
MAX_HEADERS = 400_000
MAX_MEMBERS = 200_000
MAX_MEMBER_BYTES = 512 * 2**20
MAX_EXPANDED = 8 * 2**30
MAX_METADATA_MEMBER = 2**20
MAX_METADATA_TOTAL = 64 * 2**20


class BoundedSourceTarInfo(tarfile.TarInfo):
    @classmethod
    def fromtarfile(cls, archive):
        count = getattr(archive, "_source_header_count", 0) + 1
        if count > MAX_HEADERS:
            raise ValueError("Source TAR header budget exceeded")
        archive._source_header_count = count
        return super().fromtarfile(archive)

    def _proc_member(self, archive):
        if self.size < 0 or self.size > MAX_MEMBER_BYTES:
            raise ValueError("Source TAR member byte budget exceeded")
        if self.type == tarfile.GNUTYPE_SPARSE:
            raise ValueError("Sparse source TAR entries are unsupported")
        if self.type in {tarfile.XHDTYPE, tarfile.XGLTYPE, tarfile.GNUTYPE_LONGNAME, tarfile.GNUTYPE_LONGLINK}:
            total = getattr(archive, "_source_metadata_bytes", 0) + self.size
            if self.size > MAX_METADATA_MEMBER or total > MAX_METADATA_TOTAL:
                raise ValueError("Source TAR metadata budget exceeded")
            archive._source_metadata_bytes = total
        return super()._proc_member(archive)

    def _proc_pax(self, archive):
        # Inspect the already-bounded raw metadata before tarfile's sparse handler
        # could allocate from an attacker-controlled sparse-map count.
        offset = archive.fileobj.tell()
        data = archive.fileobj.read(self.size)
        archive.fileobj.seek(offset)
        if len(data) != self.size or b"GNU.sparse" in data:
            raise ValueError("Unsupported or truncated source TAR metadata")
        return super()._proc_pax(archive)


def safe_name(name):
    raw = name[:-1] if name.endswith("/") else name
    if any(part in {"", ".", ".."} for part in raw.split("/")):
        raise ValueError("Unsafe source archive path")
    path = PurePosixPath(raw)
    if (not path.parts or path.is_absolute() or "\\" in name
            or any(part in {".", "..", ".git"} or ":" in part or part.rstrip(" .") != part
                   or any(ord(char) < 32 for char in part) for part in path.parts)):
        raise ValueError("Unsafe source archive path")
    return path.as_posix()


def register_name(seen, name, *, directory):
    canonical = safe_name(name).casefold()
    ancestors = ["/".join(canonical.split("/")[:index]) for index in range(1, len(canonical.split("/")))]
    existing = seen.get(canonical)
    if (existing in {"file", "directory"} or (existing == "implicit" and not directory)
            or any(seen.get(parent) == "file" for parent in ancestors)):
        raise ValueError("Duplicate or conflicting source archive path")
    for parent in ancestors:
        seen.setdefault(parent, "implicit")
    seen[canonical] = "directory" if directory else "file"


@contextmanager
def source_members(source):
    if source.stat().st_size > MAX_COMPRESSED:
        raise ValueError("Source archive compressed-byte budget exceeded")
    with tarfile.open(source, tarinfo=BoundedSourceTarInfo) as archive:
        members, expanded = [], 0
        for item in archive:
            expanded += item.size
            if (len(members) >= MAX_MEMBERS or item.size < 0 or item.size > MAX_MEMBER_BYTES
                    or expanded > MAX_EXPANDED):
                raise ValueError("Source archive member/expanded-byte budget exceeded")
            safe_name(item.name)
            members.append(item)
        # tarfile stops at the first zero terminator. Inspect all remaining
        # decompressed bytes so a second TAR/GZIP segment cannot disappear.
        archive.fileobj.seek(archive.offset)
        position = archive.offset
        while chunk := archive.fileobj.read(2**20):
            position += len(chunk)
            if position > MAX_EXPANDED:
                raise ValueError("Source TAR expanded stream budget exceeded")
            if any(chunk):
                raise ValueError("Nonzero trailing source TAR data is unsupported")
        yield archive, members
