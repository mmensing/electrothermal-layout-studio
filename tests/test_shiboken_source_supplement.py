# SPDX-License-Identifier: MPL-2.0
"""Release source pairing preserves all modified Qt material and original identity."""
import hashlib
import json
import runpy
import zipfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SUPPLEMENT = runpy.run_path(str(ROOT / "packaging/source-recipes/shiboken6-6.12.0/source_supplement.py"))


@pytest.fixture
def original(tmp_path):
    upstream = b"A distinct original source archive supplied in the outer Qt source part."
    source = {"name": "pyside6-6.12.0-etlayout-source.tar.gz",
              "sha256": hashlib.sha256(upstream).hexdigest(), "bytes": len(upstream)}
    files = {name: ("Retained: " + name).encode() for name in SUPPLEMENT["RETAINED_MEMBERS"]}
    files["BUILD_PROVENANCE.json"] = json.dumps({"schema_version": 1, "distribution": "shiboken6",
                                               "version": "6.12.0+etlayout.1", "source": source}).encode()
    files[SUPPLEMENT["UPSTREAM_MEMBER"]] = upstream
    path = tmp_path / "preferred-source.zip"
    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for name, data in files.items():
            archive.writestr(name, data)
    return path, files, {"preferred_sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
                         "upstream_sha256": source["sha256"], "upstream_bytes": len(upstream)}


def test_complete_source_pair_preserves_every_modified_byte(original, tmp_path):
    path, files, identity = original
    output = tmp_path / "supplement.zip"
    receipt = SUPPLEMENT["source_supplement"](path, output, **identity)
    with zipfile.ZipFile(output) as archive:
        assert set(archive.namelist()) == SUPPLEMENT["RETAINED_MEMBERS"] | {"README.md"}
        for name in SUPPLEMENT["RETAINED_MEMBERS"]:
            assert archive.read(name) == files[name]
        readme = archive.read("README.md").decode()
        assert "not a standalone" in readme
        assert SUPPLEMENT["UPSTREAM_MEMBER"] in readme
        assert identity["upstream_sha256"] in readme
        assert identity["preferred_sha256"] in readme
    assert receipt["publication_authorized"] is False
    assert len(receipt["retained_members"]) == 8
    assert hashlib.sha256(path.read_bytes()).hexdigest() == identity["preferred_sha256"]
    second = tmp_path / "repeat.zip"
    SUPPLEMENT["source_supplement"](path, second, **identity)
    assert output.read_bytes() == second.read_bytes()
    with pytest.raises(ValueError, match="new file"):
        SUPPLEMENT["source_supplement"](path, output, **identity)


@pytest.mark.parametrize("change", ["preferred-hash", "upstream-hash", "upstream-size", "missing-patch", "extra-file"])
def test_incomplete_or_different_source_cannot_be_repackaged(original, tmp_path, change):
    path, files, identity = original
    if change == "preferred-hash":
        identity["preferred_sha256"] = "0" * 64
    elif change == "upstream-hash":
        identity["upstream_sha256"] = "0" * 64
    elif change == "upstream-size":
        identity["upstream_bytes"] += 1
    else:
        if change == "missing-patch":
            del files["recipe/referred_objects_lifetime.patch"]
        else:
            files["unexpected.txt"] = b"Not an approved source member"
        with zipfile.ZipFile(path, "w") as archive:
            for name, data in files.items():
                archive.writestr(name, data)
        identity["preferred_sha256"] = hashlib.sha256(path.read_bytes()).hexdigest()
    output = tmp_path / "rejected.zip"
    with pytest.raises(ValueError):
        SUPPLEMENT["source_supplement"](path, output, **identity)
    assert not output.exists()


def test_release_catalog_delivers_original_and_supplement_in_one_qt_part():
    catalog_path = ROOT / "packaging/third-party-sources.json"
    catalog = json.loads(catalog_path.read_text())
    sources = {row["id"]: row for row in catalog["sources"]}
    runtime = next(row for row in catalog["compiled_native"] if row["id"] == "shiboken6-runtime")
    original = sources[runtime["upstream_source_id"]]
    supplement = sources[runtime["source_id"]]
    assert runtime["source_archive_sha256"] == original["sha256"]
    assert original["source_bundle"] == supplement["source_bundle"]
    assert original["bundle_path"] == SUPPLEMENT["UPSTREAM_MEMBER"]
    assert supplement["filename"].endswith("-source-supplement.zip")
    assert "supplement" in supplement["build_role"]
    policy = json.loads((ROOT / "packaging/binary-publication-policy.json").read_text())
    assert policy["source_catalog_sha256"] == hashlib.sha256(catalog_path.read_bytes()).hexdigest()
