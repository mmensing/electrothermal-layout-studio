# SPDX-License-Identifier: MPL-2.0
"""Collect verbatim installed notices; collection is not a binary-release approval."""
from __future__ import annotations

import argparse
import hashlib
import importlib.metadata as metadata
import json
import re
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
NOTICE_WORDS = ("license", "copying", "notice", "copyright")


def normalized(name):
    return re.sub(r"[-_.]+", "-", name).lower()


def collect(destination, include_build_tools=False):
    root = Path(destination).resolve()
    root.mkdir(parents=True, exist_ok=True)
    catalog = json.loads((ROOT / "DEPENDENCIES.json").read_text(encoding="utf-8"))
    selected = {normalized(item["name"]): item for item in catalog["dependencies"]
                if item["scope"] == "runtime-environment" or include_build_tools
                or normalized(item["name"]) == "pyinstaller"}
    records = []
    for name, declared in sorted(selected.items()):
        distribution = metadata.distribution(name)
        if distribution.version != declared["version"]:
            raise ValueError(f"Installed {name}=={distribution.version} differs from catalog {declared['version']}")
        folder = root / name
        found = []
        for item in distribution.files or []:
            if not any(word in Path(str(item)).name.lower() for word in NOTICE_WORDS):
                continue
            source = Path(distribution.locate_file(item))
            if (not source.is_file() or source.suffix.lower() in {".py", ".pyc", ".pyd", ".dll", ".exe"}
                    or source.stat().st_size > 16 * 1024 * 1024):
                continue
            folder.mkdir(parents=True, exist_ok=True)
            target = folder / f"{len(found):02d}_{source.name}"
            shutil.copyfile(source, target)
            found.append({"file": target.relative_to(root).as_posix(),
                          "wheel_record_path": str(item).replace("\\", "/"),
                          "sha256": hashlib.sha256(target.read_bytes()).hexdigest()})
        records.append({"name": name, "version": distribution.version, "scope": declared["scope"],
                        "license_expression": declared["license_expression"], "notice_files": found,
                        "upstream_urls": declared["upstream_urls"],
                        "corresponding_source_evaluated": False,
                        "notice_review": "Unreviewed collection; native and Qt subcomponents require independent review"})
    for name in ("LICENSE", "THIRD_PARTY_NOTICES.md", "DEPENDENCIES.json"):
        shutil.copyfile(ROOT / name, root / name)
    for directory in ("LICENSES", "packaging/license-texts"):
        shutil.copytree(ROOT / directory, root / Path(directory).name, dirs_exist_ok=True)
    python_license = Path(sys.base_prefix) / "LICENSE.txt"
    if python_license.is_file():
        (root / "Python").mkdir(exist_ok=True)
        shutil.copyfile(python_license, root / "Python/LICENSE.txt")
    (root / "installed-dependencies.json").write_text(json.dumps(records, indent=2) + "\n", encoding="utf-8")
    shutil.copyfile(ROOT / "packaging/binary-publication-policy.json", root / "binary-publication-policy.json")
    return records


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("destination", nargs="?", default="artifacts/licenses")
    parser.add_argument("--include-build-tools", action="store_true")
    args = parser.parse_args()
    print(f"Collected {len(collect(args.destination, args.include_build_tools))} installed-package notice records; exact artifact/source review is separate")
