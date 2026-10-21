# SPDX-License-Identifier: MPL-2.0
"""Build and install the reviewed Qt reference-lifetime fix from pinned inputs.

Windows source installs need CMake and the x64 MSVC C++ build tools. This local
build never publishes, updates a lock file, or consumes a PR/cached artifact.
"""
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import os
import struct
import subprocess
import sys
import urllib.parse
import urllib.request
from importlib import metadata
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
UPSTREAM_WHEEL = "shiboken6-6.12.0-cp310-abi3-win_amd64.whl"
UPSTREAM_WHEEL_SHA256 = "a1906cb8116869178c64b6bab52623bbc8b4ceed7b30bfc3c9aa0f6e67f6438e"
PATCHED_VERSION = "6.12.0+etlayout.1"
MAX_METADATA = 2 * 1024**2
MAX_INPUT = 32 * 1024**2
# This is the public HTTPS mirror selected by the official Qt download endpoint.
# Pinning it avoids location-dependent redirects; payload SHA256 remains mandatory.
QT_SOURCE_MIRROR = ("https://ftp.fau.de/qtproject/official_releases/QtForPython/"
                    "pyside6/PySide6-6.12.0-src/pyside-setup-everywhere-src-6.12.0.tar.xz")
DOWNLOAD_HOSTS = {"download.qt.io", "ftp.fau.de", "pypi.org", "files.pythonhosted.org"}


def sha256(path):
    with Path(path).open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def checked_url(url):
    parsed = urllib.parse.urlsplit(url)
    if (parsed.scheme != "https" or parsed.username or parsed.password
            or parsed.hostname not in DOWNLOAD_HOSTS or parsed.port not in {None, 443}):
        raise ValueError("Only the pinned upstream HTTPS download hosts are accepted")
    return url


class PinnedRedirects(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, request, stream, code, message, headers, url):
        checked_url(url)
        return super().redirect_request(request, stream, code, message, headers, url)


def download_bytes(url, limit):
    checked_url(url)
    opener = urllib.request.build_opener(PinnedRedirects())
    with opener.open(url, timeout=120) as response:
        checked_url(response.geturl())
        data = response.read(limit + 1)
    if len(data) > limit:
        raise ValueError("Upstream download exceeds its bounded input size")
    return data


def fetch_input(url, path, expected_sha256):
    data = download_bytes(url, MAX_INPUT)
    if hashlib.sha256(data).hexdigest() != expected_sha256:
        raise ValueError("Upstream input does not match its reviewed SHA256")
    with Path(path).open("xb") as stream:
        stream.write(data)


def wheel_url(document):
    matches = [row for row in document.get("urls", []) if row.get("filename") == UPSTREAM_WHEEL]
    if len(matches) != 1 or matches[0].get("digests", {}).get("sha256") != UPSTREAM_WHEEL_SHA256:
        raise ValueError("PyPI does not identify the exact reviewed upstream wheel")
    url = matches[0]["url"]
    parsed = urllib.parse.urlsplit(url)
    if parsed.scheme != "https" or parsed.hostname != "files.pythonhosted.org" or parsed.query or parsed.fragment:
        raise ValueError("Unexpected upstream wheel download location")
    return url


def output_path(value):
    path = Path(value).absolute()
    resolved = path.resolve()
    if resolved == ROOT or not resolved.is_relative_to(ROOT):
        raise ValueError("Build and output directories must be children of this checkout")
    if any(part.is_symlink() or part.is_junction() for part in (path, *path.parents)):
        raise ValueError("Build/output paths cannot contain links or junctions")
    return resolved


def prepare(build_dir, output_dir):
    if os.name != "nt" or struct.calcsize("P") != 8 or sys.version_info[:2] != (3, 12):
        raise ValueError("Use the supported Windows x64 Python 3.12 environment")
    if metadata.version("PySide6") != "6.12.0" or metadata.version("shiboken6") != "6.12.0":
        raise ValueError("Start with the hash-locked PySide6/Shiboken 6.12.0 bootstrap wheels")
    build_dir, output_dir = output_path(build_dir), output_path(output_dir)
    inputs = build_dir.with_name(build_dir.name + "-inputs")
    if any(path.exists() for path in (build_dir, output_dir, inputs)):
        raise FileExistsError("Use fresh build, output and input directories; existing evidence is preserved")
    inputs.mkdir(parents=True)
    source_catalog = json.loads((ROOT / "packaging/third-party-sources.json").read_bytes())
    rows = [row for row in source_catalog["sources"] if row["id"] == "pyside6-6.12.0"]
    if len(rows) != 1:
        raise ValueError("The reviewed PySide6 source catalog entry is missing or ambiguous")
    source = rows[0]
    upstream = source["upstream_input"]
    original = inputs / upstream["filename"]
    curated = inputs / source["filename"]
    wheel = inputs / UPSTREAM_WHEEL
    fetch_input(QT_SOURCE_MIRROR, original, upstream["sha256"])
    helper = ROOT / "packaging/source-recipes/normalize_source_archive.py"
    spec = importlib.util.spec_from_file_location("qt_source_normalizer", helper)
    normalizer = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(normalizer)
    normalizer.normalize(original, curated, upstream["sha256"],
                         omit=source["normalization"]["omission_prefixes"])
    if sha256(curated) != source["sha256"] or curated.stat().st_size != source["bytes"]:
        raise ValueError("Normalized PySide6 source differs from the reviewed corresponding source")
    document = json.loads(download_bytes("https://pypi.org/pypi/shiboken6/6.12.0/json", MAX_METADATA))
    fetch_input(wheel_url(document), wheel, UPSTREAM_WHEEL_SHA256)
    recipe = ROOT / "packaging/source-recipes/shiboken6-6.12.0/build_runtime.py"
    subprocess.run([sys.executable, str(recipe), "--source-archive", str(curated),
                    "--upstream-wheel", str(wheel), "--build-dir", str(build_dir),
                    "--output-dir", str(output_dir), "--jobs", "4"], check=True)
    wheels = list(output_dir.glob("shiboken6-6.12.0+etlayout.1-*.whl"))
    if len(wheels) != 1:
        raise ValueError("The source build did not produce exactly one reviewed runtime wheel")
    subprocess.run([sys.executable, "-m", "pip", "install", "--no-index", "--no-deps",
                    "--force-reinstall", str(wheels[0])], check=True)
    if metadata.version("shiboken6") != PATCHED_VERSION:
        raise ValueError("The installed Qt runtime does not identify the built patch")
    subprocess.run([sys.executable, "-m", "pip", "check"], check=True)
    subprocess.run([sys.executable, "-m", "pytest", str(ROOT / "tests/test_qt_reference_lifetime.py"),
                    "--basetemp=" + str(build_dir / "installed-tests"),
                    "--junitxml=" + str(output_dir / "pytest-installed.xml")], check=True)
    return wheels[0]


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--build-dir", type=Path, default=ROOT / "build/shiboken-runtime")
    parser.add_argument("--output-dir", type=Path, default=ROOT / "artifacts/shiboken-runtime")
    args = parser.parse_args(argv)
    try:
        wheel = prepare(args.build_dir, args.output_dir)
    except (OSError, ValueError, subprocess.CalledProcessError) as error:
        print(f"Qt runtime preparation failed: {error}", file=sys.stderr)
        return 1
    print(f"Built and tested {wheel.name}; SHA256 {sha256(wheel)}. No publication performed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

