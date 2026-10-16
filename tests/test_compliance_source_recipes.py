# SPDX-License-Identifier: MPL-2.0
"""Source publication transformations preserve code/notice bytes and fail closed."""
from __future__ import annotations

import hashlib
import importlib.util
import io
import tarfile
from pathlib import Path

import pytest

pytestmark = pytest.mark.fast
ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location(
    "source_archive_recipe", ROOT / "packaging/source-recipes/normalize_source_archive.py")
recipe = importlib.util.module_from_spec(spec)
spec.loader.exec_module(recipe)


def source_archive(path, entries):
    with tarfile.open(path, "w:gz") as archive:
        for name, content, kind, link in entries:
            item = tarfile.TarInfo(name)
            item.uid, item.gid, item.uname, item.gname, item.mtime = 1001, 1002, "upstream", "builders", 123456
            item.mode, item.type, item.linkname = 0o644, kind, link
            item.size = len(content) if kind == tarfile.REGTYPE else 0
            archive.addfile(item, io.BytesIO(content) if kind == tarfile.REGTYPE else None)
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_normalization_preserves_required_source_notice_and_internal_aliases(tmp_path):
    source, output = tmp_path / "upstream.tgz", tmp_path / "normalized.tgz"
    code, license_text = b"int original_function(void) { return 7; }\n", b"Required upstream license and copyright\n"
    digest = source_archive(source, [
        ("upstream/src/library.c", code, tarfile.REGTYPE, ""),
        ("upstream/COPYING.LIB", license_text, tarfile.REGTYPE, ""),
        ("upstream/AGENTS.md", b"Unused tool metadata", tarfile.REGTYPE, ""),
        ("upstream/docs/unused-example.txt", b"Unshipped documentation data", tarfile.REGTYPE, ""),
        ("upstream/src/alias.c", b"", tarfile.SYMTYPE, "library.c"),
        ("upstream/license-copy", b"", tarfile.LNKTYPE, "upstream/COPYING.LIB"),
    ])
    original = source.read_bytes()
    receipt = recipe.normalize(source, output, digest, omit=("upstream/docs/unused-example.txt",))
    assert source.read_bytes() == original
    assert receipt["retained_regular_payloads"] == "unchanged"
    with tarfile.open(output) as archive:
        assert archive.extractfile("upstream/src/library.c").read() == code
        assert archive.extractfile("upstream/src/alias.c").read() == code
        assert archive.extractfile("upstream/COPYING.LIB").read() == license_text
        assert archive.extractfile("upstream/license-copy").read() == license_text
        assert {"upstream/AGENTS.md", "upstream/docs/unused-example.txt"}.isdisjoint(archive.getnames())
        for item in archive.getmembers():
            assert item.isfile() and not item.issym() and not item.islnk()
            assert (item.uid, item.gid, item.uname, item.gname, item.mtime) == (0, 0, "", "", 0)
    again = tmp_path / "again.tgz"
    recipe.normalize(source, again, digest, omit=("upstream/docs/unused-example.txt",))
    assert again.read_bytes() == output.read_bytes()
    with pytest.raises(FileExistsError):
        recipe.normalize(source, output, digest)


@pytest.mark.parametrize("entries", [
    [("../escape.c", b"x", tarfile.REGTYPE, "")],
    [("/absolute.c", b"x", tarfile.REGTYPE, "")],
    [("source.c", b"x", tarfile.REGTYPE, ""), ("source.c", b"y", tarfile.REGTYPE, "")],
    [("source.c", b"", tarfile.SYMTYPE, "../../outside.c")],
    [("source.c", b"", tarfile.SYMTYPE, "missing.c")],
    [("a.c", b"", tarfile.SYMTYPE, "b.c"), ("b.c", b"", tarfile.SYMTYPE, "a.c")],
    [("device", b"", tarfile.FIFOTYPE, "")],
])
def test_unsafe_or_ambiguous_source_archives_are_rejected_before_output(tmp_path, entries):
    source, output = tmp_path / "upstream.tgz", tmp_path / "output.tgz"
    digest = source_archive(source, entries)
    with pytest.raises(ValueError):
        recipe.normalize(source, output, digest)
    assert not output.exists()


def test_input_hash_is_required_before_transforming_source(tmp_path):
    source, output = tmp_path / "upstream.tgz", tmp_path / "output.tgz"
    source_archive(source, [("source.c", b"int unchanged;", tarfile.REGTYPE, "")])
    with pytest.raises(ValueError, match="exact upstream"):
        recipe.normalize(source, output, "0" * 64)
    assert not output.exists()


def test_exact_pyqtgraph_icon_metadata_change_is_recorded_and_keeps_other_payloads(tmp_path):
    location = importlib.util.find_spec("pyqtgraph")
    icon = Path(location.origin).parent / "icons/icons.svg"
    original = icon.read_bytes()
    source, output = tmp_path / "upstream.tgz", tmp_path / "output.tgz"
    digest = source_archive(source, [
        ("upstream/pyqtgraph/icons/icons.svg", original, tarfile.REGTYPE, ""),
        ("upstream/COPYRIGHT", b"Required contributor credit remains", tarfile.REGTYPE, ""),
    ])
    receipt = recipe.normalize(source, output, digest, sanitize_pyqtgraph_svg=True)
    assert len(receipt["payload_changes"]) == 1
    change = receipt["payload_changes"][0]
    assert change["before_sha256"] == hashlib.sha256(original).hexdigest()
    with tarfile.open(output) as archive:
        changed = archive.extractfile("upstream/pyqtgraph/icons/icons.svg").read()
        assert change["after_sha256"] == hashlib.sha256(changed).hexdigest()
        assert archive.extractfile("upstream/COPYRIGHT").read() == b"Required contributor credit remains"
    assert b"inkscape:export-filename=" not in changed
    assert source.read_bytes() != output.read_bytes()


@pytest.mark.parametrize("entries", [
    [("C:/drive.c", b"x", tarfile.REGTYPE, "")],
    [("folder\\source.c", b"x", tarfile.REGTYPE, "")],
    [("folder", b"x", tarfile.REGTYPE, ""), ("folder/source.c", b"y", tarfile.REGTYPE, "")],
    [("folder/source.c", b"x", tarfile.REGTYPE, ""), ("folder", b"y", tarfile.REGTYPE, "")],
    [("Source.c", b"x", tarfile.REGTYPE, ""), ("source.c", b"y", tarfile.REGTYPE, "")],
    [("real", b"", tarfile.DIRTYPE, ""), ("real/a.c", b"x", tarfile.REGTYPE, ""),
     ("alias", b"", tarfile.SYMTYPE, "real")],
])
def test_windows_unsafe_conflicting_and_directory_alias_paths_fail_closed(tmp_path, entries):
    source, output = tmp_path / "upstream.tgz", tmp_path / "output.tgz"
    digest = source_archive(source, entries)
    with pytest.raises(ValueError):
        recipe.normalize(source, output, digest)
    assert not output.exists()


def test_existing_receipt_is_never_replaced(tmp_path):
    source, output = tmp_path / "upstream.tgz", tmp_path / "output.tgz"
    digest = source_archive(source, [("source.c", b"int original;", tarfile.REGTYPE, "")])
    receipt = output.with_suffix(output.suffix + ".json")
    receipt.write_text("Preserve existing evidence", encoding="utf-8")
    with pytest.raises(FileExistsError):
        recipe.normalize(source, output, digest)
    assert receipt.read_text(encoding="utf-8") == "Preserve existing evidence"
    assert not output.exists()


@pytest.mark.parametrize("limit,value", [("MAX_MEMBERS", 1), ("MAX_EXPANDED", 1), ("MAX_HEADERS", 1)])
def test_source_preflight_enforces_incremental_finite_limits(tmp_path, monkeypatch, limit, value):
    source, output = tmp_path / "upstream.tgz", tmp_path / "output.tgz"
    digest = source_archive(source, [("a.c", b"aa", tarfile.REGTYPE, ""), ("b.c", b"bb", tarfile.REGTYPE, "")])
    monkeypatch.setattr(recipe.archive_io, limit, value)
    with pytest.raises(ValueError, match="budget"):
        recipe.normalize(source, output, digest)
    assert not output.exists()


def test_raw_pax_size_is_bounded_before_payload_allocation(tmp_path, monkeypatch):
    source, output = tmp_path / "oversized.tar", tmp_path / "output.tgz"
    metadata = tarfile.TarInfo("pax")
    metadata.type, metadata.size = tarfile.XHDTYPE, 2**30
    source.write_bytes(metadata.tobuf(format=tarfile.USTAR_FORMAT))
    with pytest.raises(ValueError, match="budget"):
        recipe.normalize(source, output, hashlib.sha256(source.read_bytes()).hexdigest())
    assert not output.exists()


@pytest.mark.parametrize("compressed", [False, True])
def test_appended_tar_segments_cannot_silently_drop_source(tmp_path, compressed):
    import gzip
    first, second, output = tmp_path / "first.tgz", tmp_path / "second.tgz", tmp_path / "output.tgz"
    source_archive(first, [("first.c", b"original", tarfile.REGTYPE, "")])
    source_archive(second, [("second.c", b"appended", tarfile.REGTYPE, "")])
    combined = (first.read_bytes() + second.read_bytes()) if compressed else (
        gzip.decompress(first.read_bytes()) + gzip.decompress(second.read_bytes()))
    first.write_bytes(combined)
    with pytest.raises(ValueError, match="trailing"):
        recipe.normalize(first, output, hashlib.sha256(combined).hexdigest())
    assert not output.exists()


@pytest.mark.parametrize("name", ["source/./file.c", "source//file.c", "source//"])
def test_raw_noncanonical_path_segments_are_rejected(tmp_path, name):
    source, output = tmp_path / "source.tgz", tmp_path / "output.tgz"
    digest = source_archive(source, [(name, b"code", tarfile.REGTYPE, "")])
    with pytest.raises(ValueError, match="Unsafe"):
        recipe.normalize(source, output, digest)
    assert not output.exists()


def test_compressed_size_is_bounded_before_hashing(tmp_path, monkeypatch):
    source, output = tmp_path / "source.tgz", tmp_path / "output.tgz"
    source.write_bytes(b"too large")
    monkeypatch.setattr(recipe.archive_io, "MAX_COMPRESSED", 1)
    monkeypatch.setattr(recipe.hashlib, "file_digest", lambda *args: pytest.fail("oversized input was hashed"))
    with pytest.raises(ValueError, match="compressed-byte"):
        recipe.normalize(source, output, "0" * 64)


def test_independent_pthread_compatibility_changes_only_target_definitions(tmp_path, monkeypatch):
    spec = importlib.util.spec_from_file_location(
        "pthread_compatibility_recipe", ROOT / "packaging/source-recipes/klayout-0.30.12/configure_pthreads.py")
    pthread = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(pthread)
    license_text = b"Required original LGPL notice remains byte-for-byte\n"
    inputs = {"implement.h": license_text + pthread.ABI_OLD + b"\nint unchanged;\n",
              "Makefile": license_text + pthread.FLAGS_OLD + b"\nall: original-target\n"}
    for name, data in inputs.items():
        (tmp_path / name).write_bytes(data)
    monkeypatch.setattr(pthread, "INPUTS", {name: hashlib.sha256(data).hexdigest() for name, data in inputs.items()})
    monkeypatch.setattr(pthread, "OUTPUTS", {name: hashlib.sha256(pthread.transform(name, data)).hexdigest()
                                           for name, data in inputs.items()})
    receipt = pthread.configure(tmp_path)
    changed = (tmp_path / "implement.h").read_bytes()
    assert b"LONG64*" in changed and b"#if defined(_WIN64)" in changed
    assert changed.replace(pthread.ABI_NEW, pthread.ABI_OLD) == inputs["implement.h"]
    assert (tmp_path / "Makefile").read_bytes().replace(pthread.FLAGS_NEW, pthread.FLAGS_OLD) == inputs["Makefile"]
    assert receipt["vendor_binary_reproduced"] is False
    assert receipt["upstream_license"] == "LGPL-2.1-or-later"
    with pytest.raises(FileExistsError):
        pthread.configure(tmp_path)


def test_qt_attribution_normalization_keeps_all_notice_values(tmp_path):
    original = b'{"Copyright": "First line\nSecond line", "License": "MIT", "Files": ["code.c"]}'
    source, output = tmp_path / "source.tgz", tmp_path / "output.tgz"
    name = "qt/src/3rdparty/qt_attribution.json"
    digest = source_archive(source, [(name, original, tarfile.REGTYPE, "")])
    receipt = recipe.normalize(source, output, digest, normalize_attribution=(name,))
    import json
    with tarfile.open(output) as archive:
        changed = archive.extractfile(name).read()
    assert json.loads(changed) == json.loads(original, strict=False)
    assert receipt["payload_changes"][0]["before_sha256"] == hashlib.sha256(original).hexdigest()
    assert receipt["retained_regular_payloads"] == "unchanged except payload_changes"
    duplicates = b'{"Comment":"first\nline", "Comment":"second", "Copyright":"credit"}'
    normalized = recipe.attribution_json(duplicates)
    assert normalized.count(b'"Comment"') == 2
    assert json.loads(normalized, object_pairs_hook=list) == json.loads(
        duplicates, strict=False, object_pairs_hook=list)


@pytest.mark.parametrize("source_id,generator,entries", [
    ("wcwidth-0.3.0", "upstream-make-width-tables.py.txt", 21),
    ("emoji-0.5.4", "upstream-make-emoji.py.txt", 3608),
])
def test_rich_generated_runtime_data_preserves_preferred_sources(source_id, generator, entries):
    import json
    catalog = json.loads((ROOT / "packaging/third-party-sources.json").read_text(encoding="utf-8"))
    sources = {row["id"]: row for row in catalog["sources"]}
    owners = catalog["python_module_sources"]["rich"]
    assert "rich-15.0.0" in owners and source_id in owners
    assert source_id in sources["rich-15.0.0"]["derived_table_source_ids"]
    row = sources[source_id]
    assert row["normalization"]["omission_prefixes"] == []
    assert row["generated_runtime_data"].get("tables", row["generated_runtime_data"].get("entries")) == entries
    relative = "packaging/source-recipes/rich-15.0.0/" + generator
    assert row["generated_runtime_data"]["upstream_generator"] == relative
    recipe_hashes = {item["path"]: item["sha256"] for item in catalog["source_recipe_files"]}
    assert hashlib.sha256((ROOT / relative).read_bytes()).hexdigest() == recipe_hashes[relative]
    assert "not asserted" in str(row["generated_runtime_data"])
