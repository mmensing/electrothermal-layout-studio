# SPDX-License-Identifier: MPL-2.0
"""Reviewed Windows-only native packaging: use Microsoft's external v14 runtime.

This changes four unsigned, hash-pinned NumPy/GEOS import-name strings, never
implementation code or import thunks, and omits the reviewed runtime copies.
No Microsoft installer is downloaded, run, accepted or redistributed.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import struct
from datetime import datetime, timezone
from pathlib import Path

MANIFEST_NAME = "WINDOWS_RUNTIME_MODIFICATIONS.json"
NOTICE_NAME = "WINDOWS_RUNTIME_MODIFICATIONS.md"
SCHEMA_VERSION = 1
TRANSFORM = "canonical-msvc-imports-v1"
MAX_FILES = 25_000
MAX_FILE_BYTES = 512 * 1024**2
MAX_TOTAL_BYTES = 4 * 1024**3
MAX_PATCH_BYTES = 64 * 1024**2
RUNTIME_PATTERN = re.compile(r"^(?:msvcp|msvcr|vcruntime|concrt|vcomp|vccorlib|vcamp)[a-z0-9_-]*\.dll$", re.I)
CANONICAL_IMPORTS = {
    "msvcp140.dll",
    "msvcp140_1.dll",
    "msvcp140_2.dll",
    "vcruntime140.dll",
    "vcruntime140_1.dll",
    "msvcrt.dll",
    "ucrtbase.dll",
}
NUMPY_ALIAS = "msvcp140-a4c2229bdc2a2a630acdc095b4d86008.dll"
GEOS_ALIAS = "msvcp140-0fa7eb792d3fbcf2233e4ea47e9144b9.dll"
# Exact locked-wheel inputs. Output hashes are independently reviewed alongside
# byte-span/import/code-section invariants; a dependency upgrade fails closed.
REVIEWED_IMPORTS = (
    {
        "path": "_internal/numpy/_core/_multiarray_umath.cp312-win_amd64.pyd",
        "before_sha256": "9c10617e66e992b5ac6d9f78fe7cedd0011618e1c2f89d09a185bb63073e2c90",
        "after_sha256": "df490c3b84e96e6916d29fb704e380607c4f8fa25ffacb59cd0f22e459f6e984",
        "alias": NUMPY_ALIAS,
        "package": "NumPy 2.5.4",
    },
    {
        "path": "_internal/numpy/fft/_pocketfft_umath.cp312-win_amd64.pyd",
        "before_sha256": "4f008612d6b9e2ab3820ee17a1c758c499e45e80a36764c9dfb7bf304b066f03",
        "after_sha256": "ca5f483e96f628f14589202f8ff9ed7a8d8fd1e0014d489eecf85219d96328d3",
        "alias": NUMPY_ALIAS,
        "package": "NumPy 2.5.4",
    },
    {
        "path": "_internal/Shapely.libs/geos-bf067cd6ff74ee0ad5f3ff52c7ef08c9.dll",
        "before_sha256": "0948bdb5c70b82e5055b55e9419b78d68372b1d0ec877277fa6e79276f6299b9",
        "after_sha256": "ac236e44bae28eed03ebedf598bf7d6d5dd593a7e67d2326670582155d1a38ac",
        "alias": GEOS_ALIAS,
        "package": "GEOS 3.14.1 / Shapely 2.2.0",
    },
    {
        "path": "_internal/Shapely.libs/geos_c-6dd9fd915eef8a7928285416bef1e666.dll",
        "before_sha256": "6e65985b30831761887de3217853a720147626b18cb41668cd2bdc43d1bcded1",
        "after_sha256": "cbc0586d6fcb34f40c8a939236deba053b27f5f180b8d261b60eab6fafc63430",
        "alias": GEOS_ALIAS,
        "package": "GEOS 3.14.1 / Shapely 2.2.0",
    },
)
REVIEWED_RUNTIME_FILES = {
    "_internal/numpy.libs/" + NUMPY_ALIAS: "a4c2229bdc2a2a630acdc095b4d86008e5c3e3bc7773174354f3da4f5beb9cde",
    "_internal/PySide6/MSVCP140.dll": "f6f1d55bfd64027820ddce48f9cd8cf46d53bfa34047e6acf4ef348d492a05cb",
    "_internal/PySide6/MSVCP140_1.dll": "2be87527c41e1ba8aff48e326e0adcd4743516e0bbcc9304986c726e27de6aca",
    "_internal/PySide6/MSVCP140_2.dll": "6a72ed8a87eae1671640bd1ca98eccb989f1772ae46ab84594e385bf6d4ac4fb",
    "_internal/PySide6/VCRUNTIME140.dll": "a36e0cb185eb89533ed96d4568138f86a1315756a8b2644c5963a8ce23e32101",
    "_internal/PySide6/VCRUNTIME140_1.dll": "302b1e160e858602490a55e32ae2e29ddfc0bec2a188e19550cb655cdaa58f9c",
    "_internal/Shapely.libs/"
    + GEOS_ALIAS: "0fa7eb792d3fbcf2233e4ea47e9144b9b1309ba8ed5d4731a72fffa8f4f556d6",
    "_internal/shiboken6/MSVCP140.dll": "1ba4d7c0a95d375da138a057a57593b33510ad5bda6c5e2686afd68faa5b4231",
    "_internal/shiboken6/VCRUNTIME140.dll": "e7be9112ee9a5b9f44b44bff24cb761cca9b1ed7550b612f77b70edfcf5fdabf",
    "_internal/shiboken6/VCRUNTIME140_1.dll": "f02cd4a862b85a9a3153d27e15e5a213f5df8e3d77004ed2e87b8a7e8873d97c",
    "_internal/VCRUNTIME140.dll": "d5e4d9a3e835fa679450145d6a7d94e36573a509317111904d9b3712c30d9066",
    "_internal/VCRUNTIME140_1.dll": "1f2d41c4aa5db0bc33ebf7b66d72943a817d7ce6cbe880502a9403823633093f",
}
WHEEL_PROVENANCE = (
    {
        "filename": "numpy-2.5.4-cp312-cp312-win_amd64.whl",
        "sha256": "a7b1b6353e36a7e50de2973a38d705c88ee93adcf120673cee7f45a4a3fa223a",
    },
    {
        "filename": "shapely-2.2.0-cp312-cp312-win_amd64.whl",
        "sha256": "90895df6542ae039fc6557dec6194e3509e883fbd6f5788e3c3e7a38fe46b257",
    },
)


def sha256(data):
    return hashlib.sha256(data).hexdigest()


def file_sha256(path):
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024**2), b""):
            digest.update(block)
    return digest.hexdigest()


def file_inventory(bundle):
    """Bound candidate traversal and reject filesystem indirection."""
    pending = [bundle]
    files = {}
    total = 0
    directories = 0
    while pending:
        folder = pending.pop()
        directories += 1
        if directories + len(pending) > MAX_FILES:
            raise ValueError("Runtime preparation directory budget exceeded")
        with os.scandir(folder) as entries:
            for entry in entries:
                path = Path(entry.path)
                if path.is_symlink() or path.is_junction():
                    raise ValueError("Runtime preparation rejects links and junctions")
                if entry.is_dir(follow_symlinks=False):
                    pending.append(path)
                    if len(pending) + directories > MAX_FILES:
                        raise ValueError("Runtime preparation directory discovery budget exceeded")
                elif entry.is_file(follow_symlinks=False):
                    size = entry.stat(follow_symlinks=False).st_size
                    total += size
                    if len(files) >= MAX_FILES or size > MAX_FILE_BYTES or total > MAX_TOTAL_BYTES:
                        raise ValueError("Runtime preparation file/byte budget exceeded")
                    name = path.relative_to(bundle).as_posix()
                    if len(name) > 2048 or name.casefold() in files:
                        raise ValueError("Runtime preparation path collision/budget")
                    files[name.casefold()] = (name, size, file_sha256(path))
                else:
                    raise ValueError("Runtime preparation rejects special filesystem objects")
    return files


def import_signature(pe, replacements=None):
    replacements = replacements or {}
    result = []
    for kind in ("DIRECTORY_ENTRY_IMPORT", "DIRECTORY_ENTRY_DELAY_IMPORT"):
        for descriptor in getattr(pe, kind, ()):
            name = descriptor.dll.decode("ascii").lower()
            functions = tuple((item.name, item.ordinal) for item in descriptor.imports)
            result.append((kind, replacements.get(name, name), functions))
    return tuple(result)


def rewrite_runtime_import(data, *, expected_sha256, alias):
    """Patch one reviewed unsigned x64 PE without changing code/import functions."""
    import pefile

    if len(data) > MAX_PATCH_BYTES or sha256(data) != expected_sha256:
        raise ValueError("Native input differs from reviewed hash or exceeds patch budget")
    alias_bytes = alias.encode("ascii")
    replacement = b"msvcp140.dll"
    if not re.fullmatch(rb"msvcp140-[0-9a-f]{32}\.dll", alias_bytes):
        raise ValueError("Only the reviewed MSVCP140 alias form can be restored")
    pe = pefile.PE(data=data, fast_load=False)
    try:
        if pe.FILE_HEADER.Machine != 0x8664 or pe.OPTIONAL_HEADER.Magic != 0x20B:
            raise ValueError("Runtime import restoration supports x64 PE32+ only")
        if any(
            (pe.OPTIONAL_HEADER.DATA_DIRECTORY[4].VirtualAddress, pe.OPTIONAL_HEADER.DATA_DIRECTORY[4].Size)
        ):
            raise ValueError("Refusing to modify a signed native library")
        if pe.OPTIONAL_HEADER.DATA_DIRECTORY[11].Size:
            raise ValueError("Bound imports require a separate reviewed transformation")
        if any(
            alias_bytes.lower() == item.dll.lower()
            for item in getattr(pe, "DIRECTORY_ENTRY_DELAY_IMPORT", ())
        ):
            raise ValueError("Delayed alias imports are not supported by this transformation")
        matches = [d for d in getattr(pe, "DIRECTORY_ENTRY_IMPORT", ()) if d.dll == alias_bytes]
        if len(matches) != 1 or matches[0].struct.TimeDateStamp:
            raise ValueError("Expected one unbound exact alias import")
        offset = pe.get_offset_from_rva(matches[0].struct.Name)
        original = alias_bytes + b"\0"
        if offset < 0 or data[offset : offset + len(original)] != original:
            raise ValueError("Import name does not occupy the expected bounded byte span")
        checksum_offset = pe.OPTIONAL_HEADER.get_field_absolute_offset("CheckSum")
        if checksum_offset < offset + len(original) and checksum_offset + 4 > offset:
            raise ValueError("Import and checksum spans overlap")
        output = bytearray(data)
        output[offset : offset + len(original)] = replacement.ljust(len(original), b"\0")
        modified = pefile.PE(data=bytes(output), fast_load=False)
        try:
            struct.pack_into("<I", output, checksum_offset, modified.generate_checksum())
        finally:
            modified.close()
        checked = pefile.PE(data=bytes(output), fast_load=False)
        try:
            expected = import_signature(pe, {alias.lower(): replacement.decode()})
            if import_signature(checked) != expected:
                raise ValueError("Import functions or descriptors changed unexpectedly")

            def section_layout(parsed):
                return tuple(
                    (s.Name, s.VirtualAddress, s.PointerToRawData, s.SizeOfRawData, s.Characteristics)
                    for s in parsed.sections
                )

            if section_layout(pe) != section_layout(checked):
                raise ValueError("PE section layout changed")
            if any(
                s.get_data() != t.get_data()
                for s, t in zip(pe.sections, checked.sections, strict=True)
                if s.Characteristics & 0x20000000
            ):
                raise ValueError("Executable native code changed")
            allowed = set(range(offset, offset + len(original))) | set(
                range(checksum_offset, checksum_offset + 4)
            )
            if len(output) != len(data) or any(
                a != b and i not in allowed for i, (a, b) in enumerate(zip(data, output, strict=True))
            ):
                raise ValueError("Native bytes changed outside approved import/checksum spans")
            return bytes(output), {
                "before_sha256": expected_sha256,
                "after_sha256": sha256(output),
                "import_name_before": alias,
                "import_name_after": replacement.decode(),
                "import_name_offset": offset,
                "import_name_span_bytes": len(original),
                "checksum_offset": checksum_offset,
                "checksum_span_bytes": 4,
                "section_layout_unchanged": True,
                "executable_code_unchanged": True,
                "imported_functions_and_ordinals_unchanged": True,
            }
        finally:
            checked.close()
    finally:
        pe.close()


def verify_runtime_imports(bundle, files, *, allow_reviewed_aliases=False):
    import pefile

    required = set()
    for name, _, _ in files.values():
        if Path(name).suffix.lower() not in {".exe", ".dll", ".pyd"}:
            continue
        pe = pefile.PE(str(bundle / name), fast_load=True)
        try:
            pe.parse_data_directories(directories=[1, 13])
            for _, imported, _ in import_signature(pe):
                if allow_reviewed_aliases and imported in {NUMPY_ALIAS, GEOS_ALIAS}:
                    imported = "msvcp140.dll"
                if RUNTIME_PATTERN.fullmatch(imported) and imported not in CANONICAL_IMPORTS:
                    raise ValueError("Unsupported or private Microsoft runtime import remains")
                if imported in CANONICAL_IMPORTS and imported != "msvcrt.dll":
                    required.add(imported)
        finally:
            pe.close()
    return sorted(required)


def modification_notice(manifest):
    return (
        "# Native dependency packaging modifications\n\n"
        f"Modified on {manifest['modified_date_utc']} by the Electrothermal Layout Studio packaging process. "
        "NumPy 2.5.4 and GEOS 3.14.1 from Shapely 2.2.0 are modified only in their MSVCP140 import-name strings "
        "and PE checksums. Executable code, imported functions/ordinals and section layout are unchanged. "
        "Original copyright/license notices are retained. Exact original/output hashes, byte spans, pinned wheel "
        f"origins and omitted Microsoft runtime files are recorded in {MANIFEST_NAME}.\n\n"
        "Install the current supported Microsoft Visual C++ x64 v14 Redistributable directly from Microsoft: "
        "https://learn.microsoft.com/en-us/cpp/windows/latest-supported-vc-redist . "
        "No Microsoft DLLs or installer are included. See docs/windows-runtime.md and packaging/windows_runtime.py "
        "in the matching source archive for reproduction. This notice does not establish other distribution obligations.\n"
    )


def validate_prepared_record(bundle, files, manifest):
    from datetime import date

    import pefile

    if not isinstance(manifest.get("modified_date_utc"), str):
        raise ValueError("Prepared candidate modification date is missing")
    try:
        date.fromisoformat(manifest["modified_date_utc"])
    except ValueError as exc:
        raise ValueError("Prepared candidate modification date is invalid") from exc
    expected = []
    for rule in REVIEWED_IMPORTS:
        actual = files[rule["path"].casefold()][0]
        pe = pefile.PE(str(bundle / actual), fast_load=False)
        try:
            matches = [
                d for d in getattr(pe, "DIRECTORY_ENTRY_IMPORT", ()) if d.dll.lower() == b"msvcp140.dll"
            ]
            if len(matches) != 1:
                raise ValueError("Prepared native library lacks one canonical MSVCP140 import")
            expected.append(
                {
                    "path": actual,
                    "package": rule["package"],
                    "before_sha256": rule["before_sha256"],
                    "after_sha256": rule["after_sha256"],
                    "import_name_before": rule["alias"],
                    "import_name_after": "msvcp140.dll",
                    "import_name_offset": pe.get_offset_from_rva(matches[0].struct.Name),
                    "import_name_span_bytes": len(rule["alias"]) + 1,
                    "checksum_offset": pe.OPTIONAL_HEADER.get_field_absolute_offset("CheckSum"),
                    "checksum_span_bytes": 4,
                    "section_layout_unchanged": True,
                    "executable_code_unchanged": True,
                    "imported_functions_and_ordinals_unchanged": True,
                }
            )
        finally:
            pe.close()
    required = verify_runtime_imports(bundle, files)
    if (
        manifest.get("modifications") != expected
        or manifest.get("wheel_provenance") != list(WHEEL_PROVENANCE)
        or manifest.get("required_system_runtime_dlls") != required
        or any(
            manifest.get(key) is not False
            for key in (
                "installer_bundled_or_executed",
                "microsoft_runtime_files_bundled",
                "license_or_publication_clearance",
            )
        )
    ):
        raise ValueError("Prepared candidate modification details differ from reviewed native bytes")
    notice = bundle / NOTICE_NAME
    if (
        not notice.is_file()
        or notice.stat().st_size > 1024**2
        or notice.read_text(encoding="utf8") != modification_notice(manifest)
    ):
        raise ValueError("Prepared candidate modification notice is missing or changed")


def prepare_runtime_prerequisite(bundle):
    """Prepare only an isolated onedir candidate; preflight every change first."""
    bundle = Path(bundle).resolve()
    if bundle == Path(bundle.anchor) or not (bundle / "_internal").is_dir():
        raise ValueError("Expected an isolated PyInstaller onedir candidate")
    files = file_inventory(bundle)
    runtime_files = {
        key: row
        for key, row in files.items()
        if RUNTIME_PATTERN.fullmatch(Path(row[0]).name)
        or Path(row[0]).name.lower() == "ucrtbase.dll"
        or Path(row[0]).name.lower().startswith(("api-ms-win-crt-", "vc_redist", "vcredist"))
    }
    if any(not rule["after_sha256"] for rule in REVIEWED_IMPORTS):
        raise ValueError("Reviewed output hashes are not configured")
    rules = [(rule, files.get(rule["path"].casefold())) for rule in REVIEWED_IMPORTS]
    if any(row is None for _, row in rules):
        raise ValueError("Reviewed native consumer is missing")
    if not runtime_files and all(row[2] == rule["after_sha256"] for rule, row in rules):
        manifest_path = bundle / MANIFEST_NAME
        if not manifest_path.is_file() or manifest_path.stat().st_size > 1024**2:
            raise ValueError("Prepared candidate lacks its bounded modification manifest")
        manifest = json.loads(manifest_path.read_text(encoding="utf8"))
        if (
            manifest.get("transformation") != TRANSFORM
            or manifest.get("schema_version") != SCHEMA_VERSION
            or manifest.get("reviewed_inputs") != list(REVIEWED_IMPORTS)
            or manifest.get("omitted_runtime_files") != REVIEWED_RUNTIME_FILES
        ):
            raise ValueError("Prepared candidate modification record differs from reviewed policy")
        validate_prepared_record(bundle, files, manifest)
        return manifest
    expected_runtime = {k.casefold(): value for k, value in REVIEWED_RUNTIME_FILES.items()}
    if set(runtime_files) != set(expected_runtime) or any(
        runtime_files[k][2] != value for k, value in expected_runtime.items()
    ):
        raise ValueError("Runtime copies differ from reviewed paths/hashes; no files were modified")
    verify_runtime_imports(
        bundle, {k: v for k, v in files.items() if k not in runtime_files}, allow_reviewed_aliases=True
    )
    patches = []
    for rule, row in rules:
        path = bundle / row[0]
        data = path.read_bytes()
        output, details = rewrite_runtime_import(
            data, expected_sha256=rule["before_sha256"], alias=rule["alias"]
        )
        if details["after_sha256"] != rule["after_sha256"]:
            raise ValueError("Import restoration output differs from reviewed hash")
        patches.append((path, output, {"path": row[0], "package": rule["package"], **details}))
    # All consumers and runtime copies have now passed before the first mutation.
    for path, output, _ in patches:
        path.write_bytes(output)
    for name, _, _ in runtime_files.values():
        (bundle / name).unlink()
    prepared = file_inventory(bundle)
    required = verify_runtime_imports(bundle, prepared)
    manifest = {
        "schema_version": SCHEMA_VERSION,
        "transformation": TRANSFORM,
        "modified_date_utc": datetime.now(timezone.utc).date().isoformat(),
        "description": "Restore reviewed NumPy/GEOS MSVCP140 aliases to standard runtime imports; omit Microsoft runtime copies.",
        "reviewed_inputs": list(REVIEWED_IMPORTS),
        "wheel_provenance": list(WHEEL_PROVENANCE),
        "modifications": [details for _, _, details in patches],
        "omitted_runtime_files": REVIEWED_RUNTIME_FILES,
        "required_system_runtime_dlls": required,
        "prerequisite": "Install the current supported Microsoft Visual C++ x64 v14 Redistributable directly from Microsoft.",
        "prerequisite_url": "https://learn.microsoft.com/en-us/cpp/windows/latest-supported-vc-redist",
        "installer_bundled_or_executed": False,
        "microsoft_runtime_files_bundled": False,
        "license_or_publication_clearance": False,
    }
    (bundle / MANIFEST_NAME).write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf8")
    (bundle / NOTICE_NAME).write_text(modification_notice(manifest), encoding="utf8")
    return manifest


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("bundle", type=Path)
    args = parser.parse_args()
    manifest = prepare_runtime_prerequisite(args.bundle)
    print(
        json.dumps(
            {
                "transformation": manifest["transformation"],
                "modified_native_files": len(manifest["modifications"]),
                "omitted_runtime_files": len(manifest["omitted_runtime_files"]),
                "license_or_publication_clearance": False,
            }
        )
    )


if __name__ == "__main__":
    main()
