# SPDX-License-Identifier: MPL-2.0
"""Build a release source supplement paired with the original PySide source.

The complete CI preferred-source ZIP stays unchanged. A corresponding-source
release part already supplies the same original PySide tar separately, so this
supplement retains every other byte and documents that required pairing.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import stat
import zipfile
from pathlib import Path

UPSTREAM_MEMBER = "archives/pyside6-6.12.0-etlayout-source.tar.gz"
RETAINED_MEMBERS = frozenset({
    "BUILD_PROVENANCE.json", "licenses/GPL-3.0.txt", "licenses/MPL-2.0.txt",
    "modified/sources/shiboken6/libshiboken/basewrapper.cpp", "recipe/CMakeLists.txt",
    "recipe/README.md", "recipe/build_runtime.py", "recipe/referred_objects_lifetime.patch",
})
MAX_ARCHIVE_BYTES = 64 * 1024**2
MAX_RETAINED_BYTES = 4 * 1024**2


def require(condition, message):
    if not condition:
        raise ValueError(message)


def sha256(path):
    with Path(path).open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def ordinary(path):
    path = Path(path)
    require(not any(part.is_symlink() or part.is_junction() for part in (path, *path.parents)),
            "Source supplement paths must not contain links or junctions")
    return path


def source_supplement(preferred_source, output, *, preferred_sha256, upstream_sha256, upstream_bytes):
    """Retain eight exact files and omit only the separately shipped original tar.

    Both expected hashes must come from reviewed build/source evidence. This
    function establishes byte preservation, not licensing/publication clearance.
    """
    preferred_source, output = ordinary(preferred_source), ordinary(output)
    require(all(isinstance(value, str) and re.fullmatch("[0-9a-f]{64}", value)
                for value in (preferred_sha256, upstream_sha256)), "Exact SHA-256 inputs are required")
    require(type(upstream_bytes) is int and 0 < upstream_bytes <= MAX_ARCHIVE_BYTES,
            "The original source size must have a finite positive bound")
    require(preferred_source.is_file() and 0 < preferred_source.stat().st_size <= MAX_ARCHIVE_BYTES,
            "Preferred-source ZIP is missing or exceeds its finite budget")
    require(not output.exists() and output.parent.is_dir()
            and output.resolve() != preferred_source.resolve(), "Output must be a new file in an existing directory")
    require(sha256(preferred_source) == preferred_sha256, "Preferred-source bytes differ from reviewed evidence")
    retained = {}
    with zipfile.ZipFile(preferred_source) as archive:
        members = archive.infolist()
        names = [member.filename for member in members]
        require(len(names) == len(set(names)) == len(RETAINED_MEMBERS) + 1
                and set(names) == RETAINED_MEMBERS | {UPSTREAM_MEMBER},
                "Preferred-source members differ; an unexpected or missing file requires review")
        for member in members:
            mode = member.external_attr >> 16
            require(not member.is_dir() and not member.flag_bits & 1
                    and stat.S_IFMT(mode) in {0, stat.S_IFREG}
                    and member.compress_type in {zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED},
                    "Unsupported source archive member")
            if member.filename == UPSTREAM_MEMBER:
                require(member.file_size == upstream_bytes, "Original PySide source size differs")
                with archive.open(member) as stream:
                    actual = hashlib.file_digest(stream, "sha256").hexdigest()
                require(actual == upstream_sha256, "Original PySide source hash differs")
            else:
                require(0 < member.file_size <= MAX_RETAINED_BYTES, "Retained source member exceeds its budget")
                data = archive.read(member)
                require(len(data) == member.file_size, "Truncated retained source member")
                retained[member.filename] = data
    require(sum(map(len, retained.values())) <= MAX_RETAINED_BYTES, "Retained source aggregate budget exceeded")
    provenance = json.loads(retained["BUILD_PROVENANCE.json"])
    require(provenance.get("schema_version") == 1 and provenance.get("distribution") == "shiboken6"
            and provenance.get("version") == "6.12.0+etlayout.1"
            and provenance.get("source") == {"name": UPSTREAM_MEMBER.removeprefix("archives/"),
                                             "sha256": upstream_sha256, "bytes": upstream_bytes},
            "Build provenance does not identify the exact paired original source")
    readme = (
        "# Modified Shiboken source supplement\n\n"
        "This is a source supplement for Shiboken 6.12.0+etlayout.1, not a standalone\n"
        "complete source archive. Extract the full corresponding-source Qt part first.\n"
        "The required unmodified original source is supplied alongside this ZIP at:\n\n"
        f"- Path in the Qt part: `{UPSTREAM_MEMBER}`\n"
        f"- SHA-256: `{upstream_sha256}`\n"
        f"- Bytes: {upstream_bytes}\n\n"
        "Use both archives together. This supplement retains every patch, modified\n"
        "source, build recipe, license and build-provenance file from the complete\n"
        "owner-built CI preferred-source ZIP; only its duplicate original source tar\n"
        "was omitted. The CI artifact itself was not modified. Its SHA-256 is:\n\n"
        f"`{preferred_sha256}`\n\n"
        "Extract this supplement into a new directory. Follow `recipe/README.md`,\n"
        "supplying the separately extracted original PySide tar as the source archive\n"
        "to `recipe/build_runtime.py`. The dated patch and modified source are both\n"
        "retained for inspection. Preserve the original notices and the complete\n"
        "matching source set when redistributing the modified runtime.\n"
    ).encode("utf-8")
    with output.open("xb") as stream, zipfile.ZipFile(stream, "w", compression=zipfile.ZIP_DEFLATED,
                                                      compresslevel=9) as archive:
        for name, data in [("README.md", readme), *sorted(retained.items())]:
            member = zipfile.ZipInfo(name, date_time=(1980, 1, 1, 0, 0, 0))
            member.create_system = 3
            member.external_attr = (stat.S_IFREG | 0o644) << 16
            archive.writestr(member, data, compress_type=zipfile.ZIP_DEFLATED, compresslevel=9)
    require(sha256(preferred_source) == preferred_sha256, "Preferred source changed during preparation")
    return {"schema_version": 1, "publication_authorized": False,
            "scope": "Exact source-supplement derivation only; complete corresponding source requires the paired original tar",
            "preferred_source_sha256": preferred_sha256,
            "paired_upstream": {"path": UPSTREAM_MEMBER, "sha256": upstream_sha256, "bytes": upstream_bytes},
            "supplement": {"filename": output.name, "sha256": sha256(output), "bytes": output.stat().st_size},
            "retained_members": {name: {"sha256": hashlib.sha256(data).hexdigest(), "bytes": len(data)}
                                 for name, data in sorted(retained.items())},
            "readme_sha256": hashlib.sha256(readme).hexdigest()}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--preferred-source", type=Path, required=True)
    parser.add_argument("--preferred-sha256", required=True)
    parser.add_argument("--upstream-sha256", required=True)
    parser.add_argument("--upstream-bytes", type=int, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    result = source_supplement(args.preferred_source, args.output,
                               preferred_sha256=args.preferred_sha256,
                               upstream_sha256=args.upstream_sha256, upstream_bytes=args.upstream_bytes)
    print(json.dumps(result, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
