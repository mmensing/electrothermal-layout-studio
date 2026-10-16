# SPDX-License-Identifier: MPL-2.0
"""Inspect source/wheel/frozen candidates without executing their code.

Produces CycloneDX 1.6 JSON plus detailed artifact/dependency/native/font evidence.
Unknown licenses and corresponding-source gaps never become a release approval.
"""
from __future__ import annotations

import argparse
import ast
import base64
import hashlib
import importlib.metadata as metadata
import io
import json
import re
import struct
import sys
import tomllib
import zipfile
import zlib
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
                                              pefile.DIRECTORY_ENTRY["IMAGE_DIRECTORY_ENTRY_RESOURCE"]],
                                  import_dllnames_only=True)
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


FROZEN_EXE_LIMIT = 128 * 1024**2
PYZ_LIMIT, PYZ_TOC_LIMIT, FONT_MODULE_LIMIT = 64 * 1024**2, 4 * 1024**2, 2 * 1024**2


def _bounded_bytes(path, maximum):
    path = Path(path)
    if path.is_symlink() or path.is_junction() or not path.is_file() or path.stat().st_size > maximum:
        raise ValueError("Embedded-font input is missing, linked or exceeds its budget")
    with path.open("rb") as stream:
        data = stream.read(maximum + 1)
    if len(data) > maximum:
        raise ValueError("Embedded-font input grew beyond its budget")
    return data


def _bounded_file_hash(path, maximum):
    digest, count = hashlib.sha256(), 0
    with Path(path).open("rb") as stream:
        while block := stream.read(1024**2):
            count += len(block)
            if count > maximum:
                raise ValueError("Embedded-font input exceeds its hash budget")
            digest.update(block)
    return digest.hexdigest()


def _primitive_pyz_toc(data):
    """Read only PyInstaller's bounded primitive TOC, never marshal code objects."""
    if len(data) > PYZ_TOC_LIMIT:
        raise ValueError("PYZ table exceeds its budget")
    position, nodes, refs = 0, 0, []

    def take(count):
        nonlocal position
        if count < 0 or position + count > len(data):
            raise ValueError("Truncated PYZ primitive table")
        value = data[position:position + count]
        position += count
        return value

    def read(depth=0):
        nonlocal nodes
        nodes += 1
        if nodes > 200_000 or depth > 6:
            raise ValueError("PYZ primitive table exceeds its node/depth budget")
        tag = take(1)[0]
        flag, tag = bool(tag & 128), chr(tag & 127)
        if tag == "r":
            index = struct.unpack("<i", take(4))[0]
            if flag or not 0 <= index < len(refs) or refs[index] is None:
                raise ValueError("Invalid PYZ primitive reference")
            return refs[index]
        slot = len(refs) if flag else None
        if flag:
            if len(refs) >= 100_000:
                raise ValueError("PYZ reference budget exceeded")
            refs.append(None)
        if tag == "i":
            value = struct.unpack("<i", take(4))[0]
        elif tag in {"a", "A", "u", "z", "Z"}:
            size = take(1)[0] if tag in {"z", "Z"} else struct.unpack("<i", take(4))[0]
            if not 0 <= size <= 512:
                raise ValueError("PYZ module-name budget exceeded")
            value = take(size).decode("utf-8" if tag == "u" else "ascii")
        elif tag in {"[", "(", ")"}:
            size = take(1)[0] if tag == ")" else struct.unpack("<i", take(4))[0]
            if not 0 <= size <= MAX_FILES:
                raise ValueError("PYZ primitive-container budget exceeded")
            values = [read(depth + 1) for _ in range(size)]
            value = values if tag == "[" else tuple(values)
        else:
            raise ValueError("Unsupported PYZ TOC type; code/object unmarshalling is forbidden")
        if flag:
            refs[slot] = value
        return value

    rows = read()
    if position != len(data) or type(rows) not in {list, tuple} or len(rows) > MAX_FILES:
        raise ValueError("Unexpected PYZ primitive table structure or tail")
    result = {}
    for row in rows:
        if (type(row) is not tuple or len(row) != 2 or not isinstance(row[0], str)
                or not row[0] or "\0" in row[0] or row[0] in result
                or type(row[1]) is not tuple or len(row[1]) != 3
                or any(type(value) is not int for value in row[1])):
            raise ValueError("Invalid or duplicate PYZ module entry")
        result[row[0]] = row[1]
    return result


def _frozen_pyz(executable, module=None, *, outer_modules=None):
    """Bound archive metadata before using the pinned reader's outer TOC."""
    from PyInstaller.archive.readers import CArchiveReader

    executable = Path(executable)
    if (executable.is_symlink() or executable.is_junction() or not executable.is_file()
            or executable.stat().st_size > FROZEN_EXE_LIMIT):
        raise ValueError("Frozen executable exceeds its finite inspection budget")
    before = _bounded_file_hash(executable, FROZEN_EXE_LIMIT)
    with executable.open("rb") as stream:
        cookie = CArchiveReader._find_magic_pattern(stream, CArchiveReader._COOKIE_MAGIC_PATTERN)
        if cookie < 0:
            raise ValueError("Missing PyInstaller archive cookie")
        stream.seek(cookie)
        raw = stream.read(CArchiveReader._COOKIE_LENGTH)
        if len(raw) != CArchiveReader._COOKIE_LENGTH:
            raise ValueError("Truncated PyInstaller cookie")
        _magic, length, offset, size, _version, _library = struct.unpack(CArchiveReader._COOKIE_FORMAT, raw)
        base = cookie + CArchiveReader._COOKIE_LENGTH - length
        if base < 0 or not 0 < size <= 1024**2 or offset < 0 or base + offset + size > cookie:
            raise ValueError("PyInstaller outer table exceeds its finite bounds")
        stream.seek(base + offset)
        table = stream.read(size)
        cursor, names, count = 0, set(), 0
        while cursor < len(table):
            if cursor + CArchiveReader._TOC_ENTRY_LENGTH > len(table):
                raise ValueError("Truncated PyInstaller table entry")
            entry_size, entry_offset, data_size, _unpacked, _compressed, kind = struct.unpack_from(
                CArchiveReader._TOC_ENTRY_FORMAT, table, cursor)
            if (entry_size < CArchiveReader._TOC_ENTRY_LENGTH or cursor + entry_size > len(table)
                    or entry_size > 4096 or entry_offset + data_size > offset):
                raise ValueError("Invalid PyInstaller table bounds")
            name = table[cursor + CArchiveReader._TOC_ENTRY_LENGTH:cursor + entry_size].rstrip(b"\0")
            if not name or (kind != b"o" and name in names):
                raise ValueError("Duplicate or empty PyInstaller entry")
            names.add(name)
            count += 1
            if count > MAX_FILES:
                raise ValueError("PyInstaller entry-count budget exceeded")
            cursor += entry_size
        archive = CArchiveReader(str(executable))  # Outer TOC only; no module unmarshal/extract.
        if outer_modules is not None:
            outer_modules.extend(sorted(name for name, row in archive.toc.items() if row[-1] in {"m", "M", "s"}))
        entry_offset, pyz_size, unpacked, compressed, kind = archive.toc["PYZ.pyz"]
        if (kind != "z" or compressed or pyz_size != unpacked or not 12 <= pyz_size <= PYZ_LIMIT
                or entry_offset + pyz_size > offset):
            raise ValueError("Unsupported or oversized embedded PYZ archive")
        pyz_start = archive._start_offset + entry_offset
        stream.seek(pyz_start)
        header = stream.read(12)
        if len(header) != 12 or header[:4] != b"PYZ\0":
            raise ValueError("Invalid PYZ header")
        toc_offset = struct.unpack("!i", header[8:12])[0]
        if not 12 <= toc_offset < pyz_size or pyz_size - toc_offset > PYZ_TOC_LIMIT:
            raise ValueError("PYZ table offset exceeds its finite bounds")
        stream.seek(pyz_start + toc_offset)
        toc = _primitive_pyz_toc(stream.read(pyz_size - toc_offset))
        for kind, position, size in toc.values():
            if kind not in {0, 1, 3} or position < 0 or size < 0 or position + size > toc_offset:
                raise ValueError("PYZ module entry exceeds its containing archive")
        raw_module = None
        if module is not None:
            kind, position, size = toc[module]
            if kind not in {0, 1} or position < 12 or not 0 < size <= FONT_MODULE_LIMIT:
                raise ValueError("Embedded-font module exceeds its compressed budget")
            stream.seek(pyz_start + position)
            compressed_module = stream.read(size)
            decoder = zlib.decompressobj()
            raw_module = decoder.decompress(compressed_module, FONT_MODULE_LIMIT + 1)
            if (len(raw_module) > FONT_MODULE_LIMIT or not decoder.eof or decoder.unused_data
                    or decoder.unconsumed_tail or len(raw_module) > 2000 * size):
                raise ValueError("Embedded-font module exceeds its decompression/integrity budget")
    if before != _bounded_file_hash(executable, FROZEN_EXE_LIMIT):
        raise ValueError("Frozen executable changed during module inspection")
    return toc, raw_module, before


def frozen_modules(artifact):
    if not artifact.is_dir():
        return [], "Frozen ZIP: inspect the extracted candidate to inventory embedded Python modules"
    executables = sorted(artifact.glob("*.exe"))
    if not executables:
        return [], "No frozen executable found"
    try:
        outer = []
        toc, _raw, _digest = _frozen_pyz(executables[0], outer_modules=outer)
        return sorted(set(toc) | set(outer)), None
    except (ImportError, KeyError, ValueError, OSError, RuntimeError, struct.error) as error:
        return [], f"Embedded-module inspection unavailable ({type(error).__name__})"


def python_source_coverage(modules):
    """Require an explicit preferred-source owner for every non-stdlib code root."""
    if not modules:
        return [], []
    try:
        catalog = json.loads(_bounded_bytes(ROOT / "packaging/third-party-sources.json", 4 * 1024**2))
        sources = {row["id"] for row in catalog["sources"]}
        runtime = catalog["runtime_python"]["source_id"]
        if runtime not in sources:
            raise ValueError("Python runtime preferred source is missing")
        mapping = {}
        for field in ("python_module_sources", "python_outer_module_sources"):
            entries = catalog[field]
            if type(entries) is not dict or len(entries) > MAX_FILES:
                raise ValueError("Frozen source-owner map exceeds its bounded schema")
            for name, identities in entries.items():
                if (not isinstance(name, str) or not name.isidentifier() or type(identities) is not list
                        or not 1 <= len(identities) <= 16 or any(not isinstance(value, str) for value in identities)
                        or len(set(identities)) != len(identities) or not set(identities) <= sources | {"application"}
                        or ("application" in identities and name not in {"etlayout", "frozen_entry"})
                        or (name in mapping and mapping[name] != identities)):
                    raise ValueError("Frozen source-owner map has an invalid, missing or conflicting source")
                mapping[name] = identities
        counts = {}
        for module in modules:
            if (not isinstance(module, str) or not module or not module.split(".")[0].isidentifier()
                    or not all(part.isidentifier() or re.fullmatch(r"[A-Za-z_][A-Za-z0-9_-]*", part)
                               for part in module.split("."))):
                raise ValueError("Invalid frozen Python module identity")
            name = module.split(".")[0]
            counts[name] = counts.get(name, 0) + 1
        owners, unresolved = [], []
        for name, count in sorted(counts.items()):
            identities = mapping.get(name)
            if identities is None and name in sys.stdlib_module_names:
                identities = [runtime]
            if identities is None:
                unresolved.append({"component": name, "reason": "Frozen Python code lacks an explicit corresponding-source owner"})
            else:
                owners.append({"root": name, "source_ids": identities, "module_count": count})
        return owners, unresolved
    except (KeyError, TypeError, ValueError, OSError, RuntimeError) as error:
        return [], [{"component": "Frozen Python sources", "reason":
                     f"Preferred-source ownership inventory incomplete ({type(error).__name__})"}]


def embedded_font_inventory(artifact, kind, modules):
    """Prove payload presence; source factory provenance is not code execution."""
    if kind != "frozen" or not artifact.is_dir() or "PIL.ImageFont" not in modules:
        return [], []
    try:
        catalog_path = ROOT / "packaging/embedded-fonts.json"
        catalog_bytes = _bounded_bytes(catalog_path, 128 * 1024)
        catalog = json.loads(catalog_bytes)
        rows = catalog.get("fonts") if type(catalog) is dict else None
        if (type(catalog) is not dict or catalog.get("schema_version") != 1 or type(rows) is not list or len(rows) != 2
                or not all(type(row) is dict for row in rows)
                or {row.get("factory") for row in rows} != {"load_default", "load_default_imagefont"}):
            raise ValueError("Embedded-font catalog does not cover both supported factories")
        distribution = metadata.distribution("Pillow")
        source_path = distribution.locate_file("PIL/ImageFont.py")
        source_bytes = _bounded_bytes(source_path, 1024**2)
        source_hash = hashlib.sha256(source_bytes).hexdigest()
        tree = ast.parse(source_bytes)
        if sum(1 for _ in ast.walk(tree)) > 50_000:
            raise ValueError("Embedded-font source AST exceeds its node budget")
        sources = {row["id"] for row in json.loads(_bounded_bytes(
            ROOT / "packaging/third-party-sources.json", 4 * 1024**2))["sources"]}
        executables = [artifact / name for name in ("etlayout.exe", "ElectrothermalLayoutStudio.exe")]
        observed = []
        for executable in executables:
            _toc, raw, digest = _frozen_pyz(executable, "PIL.ImageFont")
            observed.append((raw, {"executable": executable.name, "executable_sha256": digest,
                "raw_module_sha256": hashlib.sha256(raw).hexdigest(), "raw_module_bytes": len(raw)}))
        results, identities = [], set()
        for row in rows:
            if (row.get("id") in identities or not isinstance(row.get("id"), str)
                    or not isinstance(row.get("name"), str) or not row["name"].strip()
                    or row.get("module") != "PIL.ImageFont" or row.get("source_id") not in sources
                    or row["source_id"] != "pillow-" + distribution.version
                    or row.get("source_sha256") != source_hash
                    or not isinstance(row.get("license_expression"), str) or not row["license_expression"].strip()
                    or "NOASSERTION" in row["license_expression"]):
                raise ValueError("Embedded-font source, identity, license or source ID is unreviewed")
            identities.add(row["id"])
            factory = next(node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == row["factory"])
            literals = []
            for node in ast.walk(factory):
                if (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                        and isinstance(node.func.value, ast.Name) and node.func.value.id == "base64"
                        and node.func.attr == "b64decode"):
                    if not node.args or not isinstance(node.args[0], ast.Constant) or not isinstance(node.args[0].value, (bytes, str)):
                        raise ValueError("Embedded font must use an exact constant base64 literal")
                    encoded = node.args[0].value
                    encoded = encoded.encode("ascii") if isinstance(encoded, str) else encoded
                    if not 0 < len(encoded) <= 256 * 1024:
                        raise ValueError("Embedded-font literal exceeds its budget")
                    decoded = base64.b64decode(re.sub(rb"[ \t\r\n\v\f]", b"", encoded), validate=True)
                    literals.append((encoded, {"encoded_sha256": hashlib.sha256(encoded).hexdigest(),
                        "sha256": hashlib.sha256(decoded).hexdigest(), "bytes": len(decoded)}))
            payloads = row.get("payloads")
            if (not literals or type(payloads) is not list or len(payloads) != len(literals)
                    or sorted(payloads, key=lambda item: item["encoded_sha256"]) !=
                    sorted((item for _encoded, item in literals), key=lambda item: item["encoded_sha256"])):
                raise ValueError("Embedded-font payload hashes/sizes differ from the pinned source")
            if any(encoded not in raw for encoded, _item in literals for raw, _evidence in observed):
                raise ValueError("Expected embedded-font literal is absent from a frozen executable")
            paths = row.get("notice_paths")
            if (type(paths) is not list or not 1 <= len(paths) <= 8
                    or not all(isinstance(name, str) for name in paths) or len(set(paths)) != len(paths)):
                raise ValueError("Embedded-font notices are missing")
            notices = []
            for name in paths:
                notice = artifact / safe_member(name)
                if not notice.resolve().is_relative_to(artifact.resolve()):
                    raise ValueError("Embedded-font notice escapes the candidate")
                content = _bounded_bytes(notice, 1024**2)
                if (not name.startswith("licenses/license-texts/") or not content
                        or content != _bounded_bytes(ROOT / "packaging" / name.removeprefix("licenses/"), 1024**2)):
                    raise ValueError("Embedded-font notice differs from its retained source notice")
                notices.append({"path": name, "sha256": hashlib.sha256(content).hexdigest()})
            results.append({**row, "catalog_sha256": hashlib.sha256(catalog_bytes).hexdigest(),
                "notices": notices, "executable_module_evidence": [evidence for _raw, evidence in observed],
                "inspection": "Exact literal presence in bounded raw PYZ bytes; source AST provenance, no module code executed"})
        if (_bounded_bytes(catalog_path, 128 * 1024) != catalog_bytes
                or _bounded_bytes(source_path, 1024**2) != source_bytes):
            raise ValueError("Embedded-font source/catalog changed during inspection")
        return results, []
    except (ImportError, KeyError, TypeError, ValueError, OSError, RuntimeError, StopIteration,
            SyntaxError, struct.error, zlib.error) as error:
        return [], [{"component": "Pillow embedded fonts", "reason":
                    f"Exact embedded-font payload/source/notice review incomplete ({type(error).__name__})"}]


def runtime_hook_alias(distribution, item, owner):
    """Recognize relocated hooks only from their exact installed RECORD entry."""
    prefixes = {"pyinstaller": "PyInstaller/hooks/rthooks/",
                "pyinstaller-hooks-contrib": "_pyinstaller_hooks_contrib/rthooks/"}
    prefix = prefixes.get(owner)
    name = str(item).replace("\\", "/")
    if not prefix or not name.startswith(prefix):
        return None
    leaf = name[len(prefix):]
    if "/" in leaf or not leaf.startswith("pyi_rth_") or not leaf.endswith(".py") or not leaf[:-3].isidentifier():
        return None
    record_hash, record_size = getattr(item, "hash", None), getattr(item, "size", None)
    if record_hash is None or record_hash.mode != "sha256" or type(record_size) is not int:
        return None
    try:
        source = Path(distribution.locate_file(item))
        base = Path(distribution.locate_file("")).resolve()
        if (not source.resolve().is_relative_to(base)
                or any(path.is_symlink() or path.is_junction() for path in (source, *source.parents))):
            return None
        content = _bounded_bytes(source, 1024**2)
    except (OSError, RuntimeError, ValueError):
        return None
    encoded = base64.urlsafe_b64encode(hashlib.sha256(content).digest()).rstrip(b"=").decode("ascii")
    if record_size != len(content) or record_hash.value != encoded:
        return None
    return leaf[:-3]


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
                if not module.startswith("pyi_rth_"):
                    modules.setdefault(module, set()).add(owner)
                alias = runtime_hook_alias(distribution, item, owner)
                if alias is not None:
                    modules.setdefault(alias, set()).add(owner)
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


def reviewed_native_fonts(review_path, native, fonts, artifact):
    """Check exact per-file component evidence; never infer clearance from a lock."""
    if review_path is None:
        return None
    review_path = Path(review_path)
    if review_path.is_symlink() or not review_path.is_file() or review_path.stat().st_size > 4 * 1024**2:
        raise ValueError("Component review must be an ordinary bounded file")
    with review_path.open("rb") as stream:
        review_bytes = stream.read(4 * 1024**2 + 1)
    if len(review_bytes) > 4 * 1024**2:
        raise ValueError("Component review grew beyond its budget")
    review_hash = hashlib.sha256(review_bytes).hexdigest()
    review = json.loads(review_bytes.decode("utf-8"))
    catalog_file = ROOT / "packaging/third-party-sources.json"
    catalog = json.loads(catalog_file.read_text(encoding="utf-8"))
    if (review.get("schema_version") != 1 or review.get("reviewer") != "mmensing"
            or review.get("source_catalog_sha256") != hashlib.sha256(catalog_file.read_bytes()).hexdigest()):
        raise ValueError("Component review does not match the current source catalog")
    sources = {item["id"] for item in catalog["sources"]}
    expected = {item["path"]: item["sha256"] for item in [*native, *fonts]}
    rows = review.get("files")
    if not isinstance(rows, list) or len(rows) != len(expected):
        raise ValueError("Component review must cover every native and font file exactly")
    resolved = {}
    for row in rows:
        name = row.get("path")
        expression = row.get("license_expression")
        ids, notices = row.get("source_ids"), row.get("notice_files")
        if (name not in expected or name in resolved or row.get("sha256") != expected[name]
                or not isinstance(expression, str) or not expression.strip() or "NOASSERTION" in expression
                or not isinstance(ids, list) or not ids or not set(ids) <= sources
                or not isinstance(notices, list) or not notices):
            raise ValueError("Missing, stale, duplicate or incomplete native/font review")
        for notice in notices:
            relative = safe_member(notice)
            path = artifact / relative
            if (not path.is_file() or path.is_symlink() or not path.resolve().is_relative_to(artifact.resolve())
                    or path.stat().st_size > 4 * 1024**2):
                raise ValueError("Reviewed component notice is missing or unsafe")
        resolved[name] = {"sha256": row["sha256"], "license_expression": expression,
                          "source_ids": ids, "notice_files": notices}
    with review_path.open("rb") as stream:
        current_review = stream.read(4 * 1024**2 + 1)
    if hashlib.sha256(current_review).hexdigest() != review_hash:
        raise ValueError("Component review changed during inspection")
    return {"manifest_sha256": review_hash, "files": resolved}


def create(artifact, kind, output, component_review=None):
    artifact, output = Path(artifact).resolve(), Path(output).resolve()
    if not artifact.exists() or artifact == output:
        raise ValueError("Artifact must exist and differ from output")
    catalog = json.loads((ROOT / "DEPENDENCIES.json").read_text(encoding="utf-8"))
    declared = {normalized(item["name"]): item for item in catalog["dependencies"]}
    package_map = metadata.packages_distributions()
    module_owners, path_owners = distribution_evidence()
    modules, module_gap = frozen_modules(artifact) if kind == "frozen" else ([], None)
    shipped, ambiguous_ownership, hook_gaps = set(), [], []
    for module in modules:
        owners = module_owners.get(module, set())
        if len(owners) == 1:
            shipped.update(owners)
        elif owners:
            ambiguous_ownership.append({"module": module, "candidate_wheel_owners": sorted(owners)})
        if module.startswith("pyi_rth_") and len(owners) != 1:
            hook_gaps.append({"component": module, "reason":
                "Frozen runtime hook lacks unambiguous, hash-verified installed RECORD ownership"})
    if kind == "frozen" and modules:
        shipped.add("pyinstaller")  # Bootloader/runtime hooks, not the build tool's whole Python package.
    python_owners, source_gaps = python_source_coverage(modules)
    files, native, fonts, unresolved = [], [], [], [*source_gaps, *hook_gaps]
    project = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))["project"]
    artifact_version, artifact_license = project["version"], project["license"]
    if kind == "frozen" and artifact.is_dir() and (artifact / "BUILD_MANIFEST.json").is_file():
        artifact_version = json.loads((artifact / "BUILD_MANIFEST.json").read_text(encoding="utf-8"))["version"]
        license_path = artifact / "LICENSE"
        license_text = license_path.read_text(encoding="utf-8") if license_path.is_file() else ""
        provenance_file = artifact / "SOURCE_BUILD_PROVENANCE.json"
        selected_gpl = (provenance_file.is_file() and json.loads(provenance_file.read_text(encoding="utf-8")).get("audited_source_profile") is True)
        if selected_gpl:
            artifact_license = "GPL-3.0-only"
        elif license_text.startswith("Mozilla Public License Version 2.0"):
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
    file_evidence = {item["path"]: item for item in files}
    for name in sorted(shipped):
        if name == normalized(project["name"]):
            continue  # The application is represented by the SBOM metadata component.
        item = declared.get(name)
        if item is None:
            unresolved.append({"component": name, "reason": "Shipped package absent from dependency catalog"})
            continue
        actual = metadata.version(name)
        if actual != item["version"]:
            unresolved.append({"component": name, "reason": "Build environment differs from locked catalog"})
        expression = item.get("frozen_subset_license_expression", item["license_expression"])
        if not isinstance(expression, str) or not expression.strip() or "NOASSERTION" in expression:
            unresolved.append({"component": name, "reason": "Shipped runtime license expression requires review"})
            expression = "NOASSERTION"
        supplemental = []
        notice_paths = item.get("frozen_notice_files", [])
        if not isinstance(notice_paths, list) or len(notice_paths) > 20:
            unresolved.append({"component": name, "reason": "Invalid frozen supplemental notice declaration"})
            notice_paths = []
        for notice in notice_paths:
            try:
                valid = isinstance(notice, str) and notice == safe_member(notice) and notice.startswith("licenses/")
            except ValueError:
                valid = False
            evidence = file_evidence.get(notice) if valid else None
            if not evidence or not evidence["size"]:
                unresolved.append({"component": name, "reason": "Required frozen supplemental notice is missing, empty or unsafe"})
            else:
                supplemental.append({"path": notice, "sha256": evidence["sha256"]})
        record = {**item, "version": actual, "license_expression": expression,
                  "source_license_expression": item["license_expression"],
                  "supplemental_notices": supplemental,
                  "scope": "shipped-bootloader" if name == "pyinstaller" else "shipped-runtime",
                  "evidence": "Recognized frozen executable bootloader/runtime hooks; build tool not bundled" if name == "pyinstaller" else "Artifact paths and/or embedded PYZ module membership"}
        dependencies.append(record)
        components.append({"type": "library", "bom-ref": "pkg:" + name, "name": name, "version": actual,
                           "purl": f"pkg:pypi/{name}@{actual}",
                           "licenses": [{"expression": expression}],
                           "properties": [{"name": "etlayout:evidence", "value": record["evidence"]},
                                          {"name": "etlayout:source-license-expression", "value": item["license_expression"]}]})
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
    embedded, embedded_gaps = embedded_font_inventory(artifact, kind, modules)
    unresolved.extend(embedded_gaps)
    for font in embedded:
        components.append({"type": "file", "bom-ref": "embedded-font:" + font["id"], "name": font["name"],
            "licenses": [{"expression": font["license_expression"]}],
            "properties": [{"name": "etlayout:embedded-font-evidence", "value": json.dumps(font, sort_keys=True)}],
            "components": [{"type": "file", "bom-ref": "embedded-font:" + font["id"] + ":" + str(index),
                "name": font["name"] + " payload " + str(index + 1),
                "hashes": [{"alg": "SHA-256", "content": payload["sha256"]}],
                "properties": [{"name": "etlayout:encoded-sha256", "value": payload["encoded_sha256"]}]}
                for index, payload in enumerate(font["payloads"])]})
    if component_review is not None and (kind != "frozen" or not artifact.is_dir()):
        raise ValueError("Exact native/font review requires an extracted frozen candidate")
    reviewed = reviewed_native_fonts(component_review, native, fonts, artifact)
    if reviewed is not None:
        for item in [*native, *fonts]:
            item["license_review"] = reviewed["files"][item["path"]]
        unresolved = [item for item in unresolved if item.get("path") not in reviewed["files"]]
        for item in components:
            name = item.get("bom-ref", "").removeprefix("file:")
            if item.get("type") == "file" and name in reviewed["files"]:
                item["licenses"] = [{"expression": reviewed["files"][name]["license_expression"]}]
    policy = json.loads((ROOT / "packaging/binary-publication-policy.json").read_text(encoding="utf-8"))
    payload = {"format_version": 1, "artifact_name": artifact.name, "kind": kind,
               "artifact_sha256": hashlib.sha256(artifact.read_bytes()).hexdigest() if artifact.is_file() else None,
               "files": files, "shipped_runtime_dependencies": dependencies,
               "build_test_environment": [item for item in declared.values() if item["scope"] == "build-or-test"],
               "runtime_environment_not_bundled": [item for key, item in declared.items() if key not in shipped and item["scope"] == "runtime-environment"],
               "declared_dependencies_not_bundled": catalog["dependencies"] if kind != "frozen" else [],
               "embedded_python_modules": modules, "python_source_owners": python_owners,
               "ambiguous_record_ownership": ambiguous_ownership,
               "native_files": native, "font_files": fonts,
               "native_component_catalog": catalog["native_components"],
               "unresolved_review_items": unresolved, "component_review": reviewed,
               "binary_publication_allowed": False,
               "binary_policy_allows_owner_review": policy["publication_allowed"],
               "inventory_note": "Read-only inventory, not legal clearance. Build/test environment is not runtime content."}
    if kind == "frozen" and "PIL.ImageFont" in modules:
        payload["embedded_fonts"] = embedded
    bom = {"bomFormat": "CycloneDX", "specVersion": "1.6", "version": 1,
           "metadata": {"component": {"type": "application", "name": "Electrothermal Layout Studio", "version": artifact_version,
                                      "licenses": [{"expression": artifact_license}]},
                        "properties": [{"name": "etlayout:artifact-kind", "value": kind},
                                       {"name": "etlayout:binary-publication", "value": "blocked" if not policy["publication_allowed"] else "requires-owner-release-review"}]},
           "components": components}
    if kind == "frozen":
        bom["metadata"]["properties"].append({"name": "etlayout:python-preferred-source-owners",
            "value": json.dumps(python_owners, sort_keys=True)})
    output.mkdir(parents=True, exist_ok=True)
    (output / "artifact-manifest.json").write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    (output / "sbom.cdx.json").write_text(json.dumps(bom, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return payload


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--artifact", required=True, type=Path)
    parser.add_argument("--kind", required=True, choices=("source", "wheel", "frozen"))
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--component-review", type=Path, help="Private exact native/font source-notice evidence")
    options = parser.parse_args()
    result = create(options.artifact, options.kind, options.output, options.component_review)
    print(f"Inventoried {len(result['files'])} files, {len(result['native_files'])} native files, {len(result['font_files'])} fonts; binary publication allowed: {result['binary_publication_allowed']}")
