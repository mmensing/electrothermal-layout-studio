# SPDX-License-Identifier: MPL-2.0
"""Normalize bounded upstream source TARs without executing or extracting them.

Retained regular-file payloads are unchanged. Safe internal links are materialized
so every payload can be inspected. Only explicit unused paths and AI-tool metadata
are omitted; required source/build/license inputs must never be selected for omission.
"""
from __future__ import annotations

import argparse
import copy
import gzip
import hashlib
import importlib.util
import io
import json
import posixpath
import tarfile
from pathlib import Path, PurePosixPath

_io_spec = importlib.util.spec_from_file_location("source_archive_io", Path(__file__).with_name("source_archive_io.py"))
archive_io = importlib.util.module_from_spec(_io_spec)
_io_spec.loader.exec_module(archive_io)

AGENT_METADATA = {"AGENTS.md", "CLAUDE.md", "COPILOT.md"}


def attribution_json(data):
    """Escape only literal controls inside strings; retain duplicate keys and notices."""
    text = data.decode("utf-8")
    original = json.loads(text, strict=False, object_pairs_hook=list)
    changed, quoted, escaped = [], False, False
    for char in text:
        if quoted and escaped:
            changed.append(char)
            escaped = False
        elif quoted and char == "\\":
            changed.append(char)
            escaped = True
        elif char == '"':
            changed.append(char)
            quoted = not quoted
        elif quoted and ord(char) < 32:
            changed.append(json.dumps(char)[1:-1])
        else:
            changed.append(char)
    encoded = "".join(changed).encode("utf-8")
    if json.loads(encoded, object_pairs_hook=list) != original:
        raise ValueError("Attribution key/value sequence changed")
    return encoded


def normalize(source: Path, output: Path, expected_sha256: str, *, omit=(),
              sanitize_pyqtgraph_svg=False, normalize_attribution=()):
    if source.stat().st_size > archive_io.MAX_COMPRESSED:
        raise ValueError("Source archive compressed-byte budget exceeded")
    with source.open("rb") as handle:
        digest = hashlib.file_digest(handle, "sha256").hexdigest()
    if digest != expected_sha256:
        raise ValueError("Input is not the reviewed exact upstream archive")
    sidecar = output.with_suffix(output.suffix + ".json")
    if output.exists() or sidecar.exists():
        raise FileExistsError("Existing source assets are never overwritten")
    omitted, links, kept = [], [], []
    kept_size, emitted = 0, {}

    def append(name, item):
        nonlocal kept_size
        kept_size += item.size
        if len(kept) >= archive_io.MAX_MEMBERS or kept_size > archive_io.MAX_EXPANDED:
            raise ValueError("Materialized source inventory exceeds finite bounds")
        archive_io.register_name(emitted, name, directory=item.isdir())
        kept.append((name, item))
    omitted_prefixes = tuple(value.rstrip("/") for value in omit)
    with archive_io.source_members(source) as (original, members):
        if len(members) > 200_000 or sum(item.size for item in members) > 8 * 2**30:
            raise ValueError("Source inventory exceeds finite bounds")
        index = {}
        for item in members:
            name = item.name.rstrip("/")
            path = PurePosixPath(name)
            if path.is_absolute() or ".." in path.parts or not path.parts:
                raise ValueError("Unsafe upstream archive path")
            if name in index:
                raise ValueError("Duplicate upstream archive path")
            index[name] = item

        def excluded(name):
            return (PurePosixPath(name).name in AGENT_METADATA
                    or any(name == prefix or name.startswith(prefix + "/") for prefix in omitted_prefixes))

        def resolve(item, visited=()):
            if item.name in visited or len(visited) >= 16:
                raise ValueError("Cyclic upstream archive link")
            if not (item.issym() or item.islnk()):
                return item
            target = (posixpath.join(posixpath.dirname(item.name), item.linkname)
                      if item.issym() else item.linkname)
            target = posixpath.normpath(target)
            if target.startswith("../") or target.startswith("/") or target not in index:
                raise ValueError("Link does not resolve inside the supplied source archive")
            return resolve(index[target], (*visited, item.name))

        for item in members:
            if excluded(item.name):
                omitted.append(item.name)
                continue
            resolved = resolve(item)
            if not (resolved.isfile() or resolved.isdir()):
                raise ValueError("Unsupported special source archive entry")
            if resolved is not item:
                links.append({"name": item.name, "target": resolved.name})
                if resolved.isdir():
                    raise ValueError("Directory aliases require explicit review and are unsupported; no contents are dropped")
            append(item.name, resolved)
        if len(kept) > 200_000 or sum(item.size for _, item in kept) > 8 * 2**30:
            raise ValueError("Materialized source inventory exceeds finite bounds")
        names = [name.rstrip("/") for name, _ in kept]
        if "SOURCE_ARCHIVE_CHANGES.json" in names:
            raise ValueError("Explicitly omit the prior generated SOURCE_ARCHIVE_CHANGES.json before a documented second transformation")
        if len(names) != len(set(names)):
            raise ValueError("Materialized source names overlap")
        payloads, payload_changes = {}, []
        if sanitize_pyqtgraph_svg:
            spec = importlib.util.spec_from_file_location(
                "publication_asset_recipe", Path(__file__).resolve().parents[1] / "publication_assets.py")
            helper = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(helper)
            matches = [(name, item) for name, item in kept if name.endswith("/pyqtgraph/icons/icons.svg")]
            if len(matches) != 1:
                raise ValueError("Expected exactly one reviewed pyqtgraph icon source asset")
            for name, item in matches:
                if item.size > helper.MAX_SVG_BYTES:
                    raise ValueError("Unexpected pyqtgraph icon source size")
                data = original.extractfile(item).read()
                changed = helper.sanitize_pyqtgraph_svg(data)
                payloads[name] = changed
                payload_changes.append({"name": name, "before_sha256": hashlib.sha256(data).hexdigest(),
                                        "after_sha256": hashlib.sha256(changed).hexdigest(),
                                        "recipe": "packaging/publication_assets.py:sanitize_pyqtgraph_svg",
                                        "change": "Remove only three unnecessary Inkscape export-filename attributes"})
        for name in normalize_attribution:
            if not name.endswith("/qt_attribution.json"):
                raise ValueError("Only explicitly selected Qt attribution metadata may be normalized")
            selected = [(kept_name, item) for kept_name, item in kept if kept_name == name]
            if len(selected) != 1 or not selected[0][1].isfile() or selected[0][1].size > 2**20:
                raise ValueError("Expected one bounded original attribution metadata member")
            data = original.extractfile(selected[0][1]).read()
            changed = attribution_json(data)
            payloads[name] = changed
            payload_changes.append({"name": name, "before_sha256": hashlib.sha256(data).hexdigest(),
                                    "after_sha256": hashlib.sha256(changed).hexdigest(),
                                    "recipe": "normalize_source_archive.py:attribution_json",
                                    "change": "Strict JSON encoding; every key/value and copyright retained"})
        metadata = {"schema_version": 1, "input_filename": source.name,
                    "input_sha256": digest, "omitted_paths": omitted,
                    "explicit_omission_prefixes": list(omit), "agent_metadata_names": sorted(AGENT_METADATA),
                    "materialized_internal_links": links, "payload_changes": payload_changes,
                    "retained_regular_payloads": "unchanged except payload_changes" if payload_changes else "unchanged",
                    "header_changes": "uid/gid/owner names and timestamps normalized; modes retained",
                    "scope": "Unused dependency-only fixtures/docs may be omitted only when documented; this does not authorize removal of required build/source/license inputs."}
        encoded = (json.dumps(metadata, indent=2, ensure_ascii=False) + "\n").encode()
        output.parent.mkdir(parents=True, exist_ok=True)
        with output.open("xb") as handle, gzip.GzipFile(fileobj=handle, mode="wb", mtime=0, filename="") as compressed:
            with tarfile.open(fileobj=compressed, mode="w") as curated:
                for name, item in kept:
                    clean = copy.copy(item)
                    clean.name, clean.uid, clean.gid, clean.mtime = name, 0, 0, 0
                    clean.uname = clean.gname = clean.linkname = ""
                    clean.pax_headers = {}
                    clean.type = tarfile.DIRTYPE if item.isdir() else tarfile.REGTYPE
                    stream = io.BytesIO(payloads[name]) if name in payloads else (
                        original.extractfile(item) if item.isfile() else None)
                    if name in payloads:
                        clean.size = len(payloads[name])
                    curated.addfile(clean, stream)
                added = tarfile.TarInfo("SOURCE_ARCHIVE_CHANGES.json")
                added.size, added.mode = len(encoded), 0o644
                curated.addfile(added, io.BytesIO(encoded))
    with output.open("rb") as handle:
        output_hash = hashlib.file_digest(handle, "sha256").hexdigest()
    receipt = {**metadata, "filename": output.name, "sha256": output_hash,
               "bytes": output.stat().st_size, "retained_members": len(kept)}
    with sidecar.open("x", encoding="utf-8") as handle:
        handle.write(json.dumps(receipt, indent=2) + "\n")
    return receipt


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path)
    parser.add_argument("output", type=Path)
    parser.add_argument("--sha256", required=True)
    parser.add_argument("--omit", action="append", default=[], help="Exact unused archive path or subtree; repeat as needed")
    parser.add_argument("--normalize-attribution", action="append", default=[], help="Exact Qt attribution member with upstream literal-newline extension")
    parser.add_argument("--sanitize-pyqtgraph-svg", action="store_true", help="Apply only the reviewed exact-byte icon metadata transformation")
    args = parser.parse_args()
    receipt = normalize(args.source, args.output, args.sha256, omit=args.omit,
                        sanitize_pyqtgraph_svg=args.sanitize_pyqtgraph_svg,
                        normalize_attribution=args.normalize_attribution)
    print(json.dumps({key: receipt[key] for key in ("filename", "sha256", "bytes", "retained_members")}, indent=2))


if __name__ == "__main__":
    main()


