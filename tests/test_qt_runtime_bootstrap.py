# SPDX-License-Identifier: MPL-2.0
"""Pinned upstream acquisition contracts; no network or compiler in these tests."""
import hashlib
import json
import runpy
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
bootstrap = runpy.run_path(str(ROOT / "scripts/prepare_qt_runtime.py"))


def test_qt_bootstrap_wheel_matches_existing_hash_lock():
    lock = (ROOT / "requirements-windows-py312.lock").read_text(encoding="utf-8")
    assert "shiboken6==6.12.0" in lock
    assert bootstrap["UPSTREAM_WHEEL_SHA256"] in lock
    source = json.loads((ROOT / "packaging/third-party-sources.json").read_bytes())
    entry = next(row for row in source["sources"] if row["id"] == "pyside6-6.12.0")
    assert entry["normalization"]["input_sha256"] == entry["upstream_input"]["sha256"]
    assert entry["normalization"]["retained_regular_payloads"] == "unchanged"


@pytest.mark.parametrize("document", [
    {}, {"urls": []},
    {"urls": [{"filename": bootstrap["UPSTREAM_WHEEL"], "digests": {"sha256": "0" * 64}}]},
    {"urls": [{"filename": bootstrap["UPSTREAM_WHEEL"],
               "digests": {"sha256": bootstrap["UPSTREAM_WHEEL_SHA256"]},
               "url": "https://unrelated.invalid/runtime.whl"}]},
])
def test_qt_wheel_discovery_rejects_changed_or_ambiguous_inputs(document):
    with pytest.raises(ValueError):
        bootstrap["wheel_url"](document)


def test_qt_wheel_discovery_retains_exact_hash_and_host():
    row = {"filename": bootstrap["UPSTREAM_WHEEL"],
           "digests": {"sha256": bootstrap["UPSTREAM_WHEEL_SHA256"]},
           "url": "https://files.pythonhosted.org/packages/runtime.whl"}
    assert bootstrap["wheel_url"]({"urls": [row]}) == row["url"]
    with pytest.raises(ValueError):
        bootstrap["wheel_url"]({"urls": [row, row]})


def test_qt_input_hash_failure_does_not_write_untrusted_bytes(tmp_path, monkeypatch):
    fetch = bootstrap["fetch_input"]
    monkeypatch.setitem(fetch.__globals__, "download_bytes", lambda *_args: b"changed input")
    output = tmp_path / "source.tar.gz"
    with pytest.raises(ValueError, match="SHA256"):
        fetch("https://download.qt.io/source.tar.gz", output, hashlib.sha256(b"reviewed input").hexdigest())
    assert not output.exists()


@pytest.mark.parametrize("url", [
    "file:///local", "http://pypi.org/input", "https://unrelated.invalid/input",
    "https://" + "username" + ":password" + "@download.qt.io/input",
])
def test_qt_downloader_rejects_unapproved_locations_before_network(url):
    with pytest.raises(ValueError, match="HTTPS"):
        bootstrap["download_bytes"](url, 32)


def test_qt_build_output_stays_inside_checkout(tmp_path):
    with pytest.raises(ValueError, match="children"):
        bootstrap["output_path"](tmp_path)
    with pytest.raises(ValueError, match="children"):
        bootstrap["output_path"](ROOT)



def test_qt_download_redirect_cannot_escape_the_pinned_hosts():
    handler = bootstrap["PinnedRedirects"]()
    with pytest.raises(ValueError, match="HTTPS"):
        handler.redirect_request(None, None, 302, "", {}, "https://unrelated.invalid/input")
    with pytest.raises(ValueError, match="HTTPS"):
        bootstrap["checked_url"]("https://pypi.org:8080/input")
    assert bootstrap["checked_url"](bootstrap["QT_SOURCE_MIRROR"]) == bootstrap["QT_SOURCE_MIRROR"]
