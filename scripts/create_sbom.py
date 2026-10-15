# SPDX-License-Identifier: MPL-2.0
"""Inspect source/wheel/frozen candidates without executing their code.

Produces CycloneDX 1.6 JSON plus detailed artifact/dependency/native/font evidence.
Unknown licenses and corresponding-source gaps never become a release approval.
"""
from __future__ import annotations

import argparse
import hashlib
import importlib.metadata as metadata
import io
import json
import re
import sys
import tomllib
import zipfile
from pathlib import Path, PurePosixPath

ROOT = Path(__file__).resolve().parents[1]
MAX_FILES, MAX_MEMBER, MAX_TOTAL = 20000, 256 * 1024**2, 2 * 1024**3
NATIVE = {".dll", ".pyd", ".exe", ".so", ".dylib"}
FONTS = {".ttf", ".otf", ".afm", ".pfb"}
SOURCE_EXCLUDES = {".git", ".venv", "__pycache__", ".pytest_cache", ".ruff_cache", "build", "dist", "artifacts", ".artifacts"}


def normalized(name):
    return re.sub(r"[-_.]+", "-", name).lower()


def safe_member(name):
    path = PurePosixPath(name.replace("\\", "/"))
    if path.is_absolute() or ".." in path.parts or ":" in name or not path.parts:
        raise ValueError("Unsafe archive member path")
    return path.as_posix()


def artifact_files(artifact, kind, output):
    """Bounded read-only inspection; never extract archive entries or follow symlinks."""
    total = 0
    if artifact.is_dir():
        paths = sorted(artifact.rglob("*"))
        candidates = []
        for path in paths:
            if path.is_symlink():
                raise ValueError("Symbolic links require explicit artifact review")
            if not path.is_file() or path.is_relative_to(output):
                continue
            relative = path.relative_to(artifact)
            if kind == "source" and any(part in SOURCE_EXCLUDES for part in relative.parts):
                continue
            candidates.append((relative.as_posix(), path))
        if len(candidates) > MAX_FILES:
            raise ValueError("Artifact file-count budget exceeded")
        for name, path in candidates:
            size = path.stat().st_size
            total += size
            if size > MAX_MEMBER or total > MAX_TOTAL:
                raise ValueError("Artifact size budget exceeded")
            yield name, path.read_bytes()
    else:
        with zipfile.ZipFile(artifact) as archive:
            entries = archive.infolist()
            if len(entries) > MAX_FILES:
                raise ValueError("Archive file-count budget exceeded")
            seen = set()
            for entry in sorted(entries, key=lambda item: item.filename):
                if entry.is_dir():
                    continue
                name = safe_member(entry.filename)
                if name in seen:
                    raise ValueError("Duplicate archive member")
                seen.add(name)
                total += entry.file_size
                if (entry.file_size > MAX_MEMBER or total > MAX_TOTAL
                        or entry.file_size > 2000 * max(entry.compress_size, 1)
                        or (entry.external_attr >> 16) & 0o170000 == 0o120000):
                    raise ValueError("Unsafe archive member or size budget exceeded")
                yield name, archive.read(entry)


def native_details(data):
    try:
        import pefile
    except ImportError:
        return {"inspection": "pefile unavailable", "imports": [], "version": None}
    try:
        pe = pefile.PE(data=data, fast_load=True)
        pe.parse_data_directories(directories=[pefile.DIRECTORY_ENTRY["IMAGE_DIRECTORY_ENTRY_IMPORT"],
                                              pefile.DIRECTORY_ENTRY["IMAGE_DIRECTORY_ENTRY_RESOURCE"]])
        imports = sorted(item.dll.decode("ascii", errors="replace")
                         for item in getattr(pe, "DIRECTORY_ENTRY_IMPORT", []))
        version = None
        for group in getattr(pe, "FileInfo", []):
            for table in group:
                for strings in getattr(table, "StringTable", []):
                    raw = strings.entries.get(b"FileVersion")
                    if raw:
                        version = raw.decode("utf-8", errors="replace")
        pe.close()
        return {"inspection": "PE imports/resources", "imports": imports, "version": version}
    except pefile.PEFormatError:
        return {"inspection": "Not a PE file; independent native inspection required", "imports": [], "version": None}


def font_details(name, data):
    if Path(name).suffix.lower() in {".ttf", ".otf"}:
        try:
            from fontTools.ttLib import TTFont
            font = TTFont(io.BytesIO(data), lazy=True)
            result = {str(key): sorted({entry.toUnicode() for entry in font["name"].names if entry.nameID == key})
                      for key in (0, 1, 5, 13, 14)}
            font.close()
            return {"name_table": result}
        except (ImportError, ValueError, KeyError):
            return {"inspection": "Font metadata unavailable; manual review required"}
    return {"inspection": "Type1/AFM font; retain and review Matplotlib wheel copyright notices"}


def frozen_modules(artifact):
    if not artifact.is_dir():
        return [], "Frozen ZIP: inspect the extracted candidate to inventory embedded Python modules"
    executables = sorted(artifact.glob("*.exe"))
    if not executables:
        return [], "No frozen executable found"
    try:
        from PyInstaller.archive.readers import CArchiveReader
        archive = CArchiveReader(str(executables[0]))
        return sorted(archive.open_embedded_archive("PYZ.pyz").toc), None
    except (ImportError, KeyError, ValueError, OSError) as error:
        return [], f"Embedded-module inspection unavailable ({type(error).__name__})"


def distribution_evidence():
    """Use RECORD ownership, not a shared top-level namespace, for split wheels."""
    modules, paths = {}, {}
    for distribution in metadata.distributions():
        owner = normalized(distribution.metadata.get("Name", "unknown"))
        for item in distribution.files or []:
            name = str(item).replace("\\", "/")
            if ".." in PurePosixPath(name).parts:
                continue
            paths.setdefault(name.lower(), set()).add(owner)
            if name.endswith(".py"):
                module = name[:-3].replace("/", ".")
                if module.endswith(".__init__"):
                    module = module[:-9]
                modules.setdefault(module, set()).add(owner)
    return modules, paths


def native_component(path):
    base = PurePosixPath(path).name.lower()
    if base.startswith(("geos", "geos_c")):
        return "GEOS"
    if base.startswith("libscipy_openblas"):
        return "NumPy OpenBLAS/LAPACK" if "numpy.libs" in path else "SciPy OpenBLAS/LAPACK"
    if base.startswith("qt6") or "/plugins/" in path.lower():
        return "Qt"
    if base.startswith("gmsh-"):
        return "Gmsh bundled native code"
    if base.startswith(("vcruntime", "msvcp")):
        return "Microsoft runtime DLLs"
    return None


def component_for(path, modules, package_map):
    parts = PurePosixPath(path).parts
    for part in parts:
        key = part.split(".")[0]
        if key in package_map:
            return package_map[key][0]
    base = PurePosixPath(path).name.lower()
    if base.startswith("gmsh-"):
        return "gmsh"
    if base.startswith("python") or base in {"base_library.zip", "libcrypto-3.dll", "libssl-3.dll", "sqlite3.dll", "libffi-8.dll"}:
        return "CPython"
    if base.startswith(("vcruntime", "msvcp")):
        return "Microsoft runtime"
    if base.startswith("qt6") or "PySide6" in parts:
        return "Qt"
    if base.startswith(("geos", "geos_c")):
        return "GEOS"
    if base.startswith("libscipy_openblas"):
        return "OpenBLAS/LAPACK"
    if base in {"electrothermallayoutstudio.exe", "etlayout.exe"}:
        return "Application/PyInstaller bootloader"
    if any(part == "etlayout" for part in parts):
        return "etlayout"
    return None


def create(artifact, kind, output):
    artifact, output = Path(artifact).resolve(), Path(output).resolve()
    if not artifact.exists() or artifact == output:
        raise ValueError("Artifact must exist and differ from output")
    catalog = json.loads((ROOT / "DEPENDENCIES.json").read_text(encoding="utf-8"))
    declared = {normalized(item["name"]): item for item in catalog["dependencies"]}
    package_map = metadata.packages_distributions()
    module_owners, path_owners = distribution_evidence()
    modules, module_gap = frozen_modules(artifact) if kind == "frozen" else ([], None)
    shipped, ambiguous_ownership = set(), []
    for module in modules:
        owners = module_owners.get(module, set())
        if len(owners) == 1:
            shipped.update(owners)
        elif owners:
            ambiguous_ownership.append({"module": module, "candidate_wheel_owners": sorted(owners)})
    if kind == "frozen" and modules:
        shipped.add("pyinstaller")  # Bootloader/runtime hooks, not the build tool's whole Python package.
    files, native, fonts, unresolved = [], [], [], []
    project = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))["project"]
    artifact_version, artifact_license = project["version"], project["license"]
    if kind == "frozen" and artifact.is_dir() and (artifact / "BUILD_MANIFEST.json").is_file():
        artifact_version = json.loads((artifact / "BUILD_MANIFEST.json").read_text(encoding="utf-8"))["version"]
        license_path = artifact / "LICENSE"
        license_text = license_path.read_text(encoding="utf-8") if license_path.is_file() else ""
        if license_text.startswith("Mozilla Public License Version 2.0"):
            artifact_license = "MPL-2.0"
        elif "GNU GENERAL PUBLIC LICENSE" in license_text[:150] and "Version 3" in license_text[:200]:
            artifact_license = "GPL-3.0-or-later"
        else:
            artifact_license = "NOASSERTION"
    components = []
    for name, data in artifact_files(artifact, kind, output):
        if kind == "wheel" and name.endswith(".dist-info/METADATA"):
            from email.parser import BytesParser
            wheel_metadata = BytesParser().parsebytes(data)
            artifact_version = wheel_metadata.get("Version", artifact_version)
            artifact_license = wheel_metadata.get("License-Expression", "NOASSERTION")
        sha = hashlib.sha256(data).hexdigest()
        row = {"path": name, "size": len(data), "sha256": sha}
        suffix = Path(name).suffix.lower()
        owner = component_for(name, modules, package_map) if kind == "frozen" else "etlayout"
        runtime_code = suffix in NATIVE | {".py", ".pyc"} and "licenses" not in PurePosixPath(name).parts
        if kind == "frozen" and runtime_code:
            relative = name.removeprefix("_internal/").lower()
            exact_owners = path_owners.get(relative, set())
            if len(exact_owners) == 1:
                shipped.update(exact_owners)
            elif exact_owners:
                ambiguous_ownership.append({"path": name, "candidate_wheel_owners": sorted(exact_owners)})
            if exact_owners:
                owner = ", ".join(sorted(exact_owners))
            elif owner and normalized(owner) in declared:
                shipped.add(normalized(owner))
        if suffix in NATIVE:
            entry = {**row, "component": owner, "native_component": native_component(name), **native_details(data)}
            native.append(entry)
            # Wrapper ownership does not establish licenses of embedded native code.
            unresolved.append({"path": name, "reason": "Review exact native source, embedded components, notices and redistribution obligations"})
        if suffix in FONTS:
            fonts.append({**row, "component": owner, **font_details(name, data)})
            unresolved.append({"path": name, "reason": "Review exact font license, reserved names and notices"})
        files.append(row)
        components.append({"type": "file", "bom-ref": "file:" + name, "name": name,
                           "hashes": [{"alg": "SHA-256", "content": sha}]})
    dependencies = []
    for name in sorted(shipped):
        item = declared.get(name)
        if item is None:
            unresolved.append({"component": name, "reason": "Shipped package absent from dependency catalog"})
            continue
        actual = metadata.version(name)
        if actual != item["version"]:
            unresolved.append({"component": name, "reason": "Build environment differs from locked catalog"})
        record = {**item, "version": actual,
                  "scope": "shipped-bootloader" if name == "pyinstaller" else "shipped-runtime",
                  "evidence": "Recognized frozen executable bootloader/runtime hooks; build tool not bundled" if name == "pyinstaller" else "Artifact paths and/or embedded PYZ module membership"}
        dependencies.append(record)
        components.append({"type": "library", "bom-ref": "pkg:" + name, "name": name, "version": actual,
                           "purl": f"pkg:pypi/{name}@{actual}",
                           "licenses": [{"expression": item["license_expression"]}],
                           "properties": [{"name": "etlayout:evidence", "value": record["evidence"]}]})
    for native_name in sorted({item["native_component"] for item in native if item["native_component"]}):
        entry = next((item for item in catalog["native_components"] if item["name"] == native_name), None)
        expression = entry["license_expression"] if entry else "NOASSERTION"
        components.append({"type": "library", "bom-ref": "native:" + native_name,
                           "name": native_name, "version": entry["version"] if entry else "unreviewed",
                           "licenses": [{"license": {"name": "NOASSERTION"}}] if expression == "NOASSERTION" else [{"expression": expression}],
                           "properties": [{"name": "etlayout:evidence", "value": "Actual native file presence; catalog license/source provenance remains subject to review"}]})
    if any(PurePosixPath(item["path"]).name.lower().startswith("python") for item in native):
        python_version = sys.version.split()[0]
        if artifact.is_dir() and (artifact / "BUILD_MANIFEST.json").is_file():
            python_version = json.loads((artifact / "BUILD_MANIFEST.json").read_text(encoding="utf-8")).get("python", python_version)
        components.append({"type": "library", "bom-ref": "native:CPython", "name": "CPython", "version": python_version,
                           "licenses": [{"expression": "Python-2.0"}]})
    if module_gap:
        unresolved.append({"reason": module_gap})
    policy = json.loads((ROOT / "packaging/binary-publication-policy.json").read_text(encoding="utf-8"))
    payload = {"format_version": 1, "artifact_name": artifact.name, "kind": kind,
               "artifact_sha256": hashlib.sha256(artifact.read_bytes()).hexdigest() if artifact.is_file() else None,
               "files": files, "shipped_runtime_dependencies": dependencies,
               "build_test_environment": [item for item in declared.values() if item["scope"] == "build-or-test"],
               "runtime_environment_not_bundled": [item for key, item in declared.items() if key not in shipped and item["scope"] == "runtime-environment"],
               "declared_dependencies_not_bundled": catalog["dependencies"] if kind != "frozen" else [],
               "embedded_python_modules": modules, "ambiguous_record_ownership": ambiguous_ownership,
               "native_files": native, "font_files": fonts,
               "native_component_catalog": catalog["native_components"],
               "unresolved_review_items": unresolved, "binary_publication_allowed": policy["publication_allowed"],
               "inventory_note": "Read-only inventory, not legal clearance. Build/test environment is not runtime content."}
    bom = {"bomFormat": "CycloneDX", "specVersion": "1.6", "version": 1,
           "metadata": {"component": {"type": "application", "name": "Electrothermal Layout Studio", "version": artifact_version,
                                      "licenses": [{"expression": artifact_license}]},
                        "properties": [{"name": "etlayout:artifact-kind", "value": kind},
                                       {"name": "etlayout:binary-publication", "value": "blocked" if not policy["publication_allowed"] else "requires-owner-release-review"}]},
           "components": components}
    output.mkdir(parents=True, exist_ok=True)
    (output / "artifact-manifest.json").write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    (output / "sbom.cdx.json").write_text(json.dumps(bom, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return payload


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--artifact", required=True, type=Path)
    parser.add_argument("--kind", required=True, choices=("source", "wheel", "frozen"))
    parser.add_argument("--output", required=True, type=Path)
    options = parser.parse_args()
    result = create(options.artifact, options.kind, options.output)
    print(f"Inventoried {len(result['files'])} files, {len(result['native_files'])} native files, {len(result['font_files'])} fonts; binary publication allowed: {result['binary_publication_allowed']}")
