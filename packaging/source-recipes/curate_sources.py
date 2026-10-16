# SPDX-License-Identifier: MPL-2.0
"""Produce bounded, auditable source sets for the Windows distribution.

Only the named unused data or disabled optional native component is omitted.
Source/build/notice bytes of retained members are unchanged. No archive is run.
"""
from __future__ import annotations

import argparse
import copy
import gzip
import hashlib
import importlib.util
import io
import json
import tarfile
from pathlib import Path, PurePosixPath

_io_spec = importlib.util.spec_from_file_location("source_archive_io", Path(__file__).with_name("source_archive_io.py"))
archive_io = importlib.util.module_from_spec(_io_spec)
_io_spec.loader.exec_module(archive_io)

PROFILES = {
    "matplotlib-3.11.2": {
        "sha256": "cec596316640f2b394b8f0daa0ea61a8eae82d017b620b9f202befb972a59ea4",
        "omit": ("lib/matplotlib/mpl-data/fonts/afm", "lib/matplotlib/mpl-data/fonts/pdfcorefonts",
                 "lib/matplotlib/mpl-data/sample_data"),
        "reason": "Unused optional AFM/core-font data and sample data are excluded from the application and source set. Bundled TTF fonts and all supported renderers remain.",
    },
    "gmsh-4.15.2-source": {
        "sha256": "be3f66f225d27ba9fa014f07e83169285da8a051b0e8ab7103d88066b39bdd3e",
        "omit": ("contrib/untangle", "contrib/hxt", "contrib/mpeg_encode"),
        "reason": "Unused optional tetrahedral Untangle/HXT and MPEG-encode components contain restricted historical source notices. They are disabled with ENABLE_UNTANGLE=OFF, ENABLE_HXT=OFF and ENABLE_MPEG_ENCODE=OFF and are not redistributed. ETLayout uses planar algorithm6; its geometry/mesh features are preserved.",
    },
}


def curate(profile: str, source: Path, output: Path) -> dict:
    settings = PROFILES[profile]
    if source.stat().st_size > archive_io.MAX_COMPRESSED:
        raise ValueError("Source archive compressed-byte budget exceeded")
    with source.open("rb") as handle:
        original_hash = hashlib.file_digest(handle, "sha256").hexdigest()
    if original_hash != settings["sha256"]:
        raise ValueError("Input does not match the pinned upstream source archive")
    sidecar = output.with_suffix(output.suffix + ".json")
    if output.exists() or sidecar.exists():
        raise FileExistsError("Choose a new output path; existing source assets are never replaced")
    with archive_io.source_members(source) as (original, members):
        if len(members) > 50_000 or sum(item.size for item in members) > 800 * 2**20:
            raise ValueError("Source archive exceeds the documented inventory budget")
        kept, omitted = [], []
        for item in members:
            path = PurePosixPath(item.name)
            if path.is_absolute() or ".." in path.parts or not (item.isfile() or item.isdir()):
                raise ValueError(f"Unsupported source archive member: {item.name}")
            if not path.parts or path.parts[0] != profile:
                raise ValueError("Archive has an unexpected source root")
            relative = PurePosixPath(*path.parts[1:]).as_posix()
            if any(relative == prefix or relative.startswith(prefix + "/") for prefix in settings["omit"]):
                omitted.append({"name": item.name, "bytes": item.size})
            else:
                kept.append(item)
        if not omitted:
            raise ValueError("Expected explicit source exclusions were not found")
        metadata = {"schema_version": 1, "upstream_archive": source.name,
                    "upstream_sha256": original_hash, "profile": profile,
                    "source_changes": [], "omitted_subtrees": list(settings["omit"]),
                    "reason": settings["reason"], "omitted_members": omitted,
                    "retained_member_contents": "byte-identical to the pinned archive",
                    "archive_headers": "uid/gid/owner names and timestamps normalized; original modes retained"}
        data = (json.dumps(metadata, indent=2, ensure_ascii=False) + "\n").encode()
        output.parent.mkdir(parents=True, exist_ok=True)
        with output.open("xb") as target, gzip.GzipFile(fileobj=target, mode="wb", mtime=0, filename="") as compressed:
            with tarfile.open(fileobj=compressed, mode="w") as curated:
                for item in kept:
                    clean = copy.copy(item)
                    clean.uid = clean.gid = clean.mtime = 0
                    clean.uname = clean.gname = ""
                    clean.pax_headers = {}
                    stream = original.extractfile(item) if item.isfile() else None
                    curated.addfile(clean, stream)
                added = tarfile.TarInfo(profile + "/SOURCE_SET_CHANGES.json")
                added.size, added.mode = len(data), 0o644
                curated.addfile(added, io.BytesIO(data))
    with output.open("rb") as handle:
        digest = hashlib.file_digest(handle, "sha256").hexdigest()
    receipt = {**metadata, "filename": output.name, "bytes": output.stat().st_size,
               "sha256": digest, "retained_members": len(kept)}
    with sidecar.open("x", encoding="utf-8") as handle:
        handle.write(json.dumps(receipt, indent=2) + "\n")
    return receipt


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("profile", choices=sorted(PROFILES))
    parser.add_argument("source", type=Path)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    receipt = curate(args.profile, args.source, args.output)
    print(json.dumps({key: receipt[key] for key in ("filename", "bytes", "sha256", "retained_members")}, indent=2))


if __name__ == "__main__":
    main()


