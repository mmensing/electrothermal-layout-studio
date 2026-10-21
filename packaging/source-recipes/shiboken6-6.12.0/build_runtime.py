# SPDX-License-Identifier: MPL-2.0
"""Build a source-patched, tested Shiboken runtime wheel; no downloads or publication.

The original module and bindings remain 6.12.0. Distribution metadata identifies
our local runtime patch and RECORD binds every delivered byte. No compiler or
system installation, in-place dependency edit, binary patch or GC workaround occurs.
"""
from __future__ import annotations

import argparse
import base64
import csv
import hashlib
import io
import json
import os
import re
import shutil
import stat
import subprocess
import sys
import tarfile
import time
import zipfile
from email import policy
from email.parser import BytesParser
from pathlib import Path, PurePosixPath

RECIPE = Path(__file__).resolve().parent
ROOT = RECIPE.parents[2]
SOURCE_NAME = "pyside6-6.12.0-etlayout-source.tar.gz"
SOURCE_SHA256 = "e659fbccafdc0ca7a0235188355837b67274e4e18e893b0b98a5a76c859d13ef"
SOURCE_BYTES = 21_104_154
WHEEL_NAME = "shiboken6-6.12.0-cp310-abi3-win_amd64.whl"
WHEEL_SHA256 = "a1906cb8116869178c64b6bab52623bbc8b4ceed7b30bfc3c9aa0f6e67f6438e"
VERSION = "6.12.0+etlayout.1"
OUTPUT_WHEEL = f"shiboken6-{VERSION}-cp310-abi3-win_amd64.whl"
SOURCE_ROOT = "pyside-setup-everywhere-src-6.12.0"
PATCH_TARGET = "sources/shiboken6/libshiboken/basewrapper.cpp"
OLD_INFO = "shiboken6-6.12.0.dist-info"
NEW_INFO = f"shiboken6-{VERSION}.dist-info"
MAX_EXPANDED = 512 * 1024**2
MS_RUNTIME = re.compile(r"(?i)^(?:vcruntime|msvcp|msvcr|concrt|vcomp|vccorlib|vcamp|ucrtbase|api-ms-win-crt-).*\.dll$")


def require(condition, message):
    if not condition:
        raise ValueError(message)


def sha256(path):
    with Path(path).open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def safe_member(name):
    path = PurePosixPath(name)
    require(name and not path.is_absolute() and "\\" not in name
            and all(part not in {"", ".", ".."} and ":" not in part for part in name.rstrip("/").split("/")),
            "Unsafe archive member path")
    return path



def checked_directories(build_dir, output_dir):
    """Validate original paths before resolution can hide links or junctions."""
    paths = [Path(name).absolute() for name in (build_dir, output_dir)]
    for path in paths:
        require(not any(part.is_symlink() or part.is_junction() for part in (path, *path.parents)),
                "Build/output paths cannot contain links or junctions")
    build, output = (path.resolve() for path in paths)
    require(build != output and not build.is_relative_to(output) and not output.is_relative_to(build),
            "Build and output directories must be separate, non-overlapping paths")
    require(not build.exists(), "Use a fresh build directory; nothing is overwritten or removed")
    require(not output.exists() or output.is_dir(), "Output must be an ordinary directory")
    require(not any((output / name).exists() for name in
                    (OUTPUT_WHEEL, "BUILD_PROVENANCE.json", "preferred-source.zip", "regression.xml")),
            "Reviewed outputs cannot be replaced")
    return build, output


def unpack_source(archive_path, destination):
    """Inspect the full pinned archive; materialize its runtime/build inputs only."""
    archive_path, destination = Path(archive_path), Path(destination)
    require(archive_path.name == SOURCE_NAME and archive_path.stat().st_size == SOURCE_BYTES
            and sha256(archive_path) == SOURCE_SHA256, "PySide source differs from the audited archive")
    destination.mkdir()
    total, count, seen = 0, 0, set()
    with tarfile.open(archive_path, "r:gz") as archive:
        for member in archive:
            relative = safe_member(member.name)
            require((relative.parts[0] == SOURCE_ROOT or member.name == "SOURCE_ARCHIVE_CHANGES.json")
                    and member.name not in seen,
                    "Unexpected source root or duplicate member")
            seen.add(member.name)
            count += 1
            total += member.size
            require(count <= 40_000 and total <= MAX_EXPANDED and member.size <= 32 * 1024**2,
                    "Source archive exceeds bounded extraction limits")
            require(member.isdir() or member.isfile(), "Source archive links or special members are forbidden")
            # The full pristine archive accompanies the wheel. Avoid materializing
            # unrelated Qt bindings/examples (some exceed Windows MAX_PATH).
            runtime_prefixes = (SOURCE_ROOT + "/sources/shiboken6/libshiboken/",
                                SOURCE_ROOT + "/sources/shiboken6/cmake/",
                                SOURCE_ROOT + "/sources/shiboken6/shibokenmodule/files.dir/",
                                SOURCE_ROOT + "/build_scripts/")
            if not (member.name.startswith(runtime_prefixes)
                    or member.name in {SOURCE_ROOT + "/sources/shiboken6/.cmake.conf",
                                       "SOURCE_ARCHIVE_CHANGES.json"}):
                continue
            target = destination.joinpath(*relative.parts)
            if member.isdir():
                target.mkdir(parents=True, exist_ok=True)
            else:
                target.parent.mkdir(parents=True, exist_ok=True)
                with archive.extractfile(member) as source, target.open("xb") as output:
                    shutil.copyfileobj(source, output, length=1024**2)
    return destination / SOURCE_ROOT


def wheel_payloads(path, *, expected_sha256=WHEEL_SHA256):
    """Read the locked upstream wheel, preserving notices and unrelated payloads."""
    path = Path(path)
    require(path.stat().st_size <= 32 * 1024**2 and sha256(path) == expected_sha256,
            "Upstream Shiboken wheel differs from the locked input")
    result, total = {}, 0
    with zipfile.ZipFile(path) as archive:
        require(len(archive.infolist()) <= 5_000, "Wheel member budget exceeded")
        for member in archive.infolist():
            name = str(safe_member(member.filename))
            require(name not in result and not member.flag_bits & 1
                    and not stat.S_ISLNK(member.external_attr >> 16), "Unsafe or duplicate wheel member")
            total += member.file_size
            require(total <= MAX_EXPANDED and member.file_size <= 32 * 1024**2,
                    "Expanded wheel budget exceeded")
            if not member.is_dir():
                require(name.startswith(("shiboken6/", OLD_INFO + "/")), "Unexpected upstream wheel package")
                result[name] = archive.read(member)
    require({"shiboken6/Shiboken.pyd", "shiboken6/shiboken6.abi3.dll",
             "shiboken6/shiboken6.abi3.lib", OLD_INFO + "/METADATA", OLD_INFO + "/WHEEL"} <= result.keys(),
            "Upstream wheel is missing its runtime, module, import library or metadata")
    metadata = BytesParser(policy=policy.default).parsebytes(result[OLD_INFO + "/METADATA"])
    require(metadata["Name"] == "shiboken6" and metadata["Version"] == "6.12.0",
            "Unexpected upstream wheel identity")
    wheel = result[OLD_INFO + "/WHEEL"]
    require(b"Tag: cp310-abi3-win_amd64" in wheel and b"Root-Is-Purelib: false" in wheel,
            "The official Windows stable-ABI wheel is required")
    return result


def apply_patch(source, patch, run):
    """Use Git's text patcher outside repository discovery; one source file only."""
    text = patch.read_text(encoding="utf-8")
    targets = re.findall(r"^\+\+\+ b/(.+)$", text, flags=re.MULTILINE)
    require(targets == [PATCH_TARGET] and "GIT binary patch" not in text
            and not re.search(r"^(?:new|deleted) file mode|^rename (?:from|to) ", text, re.MULTILINE),
            "Lifetime patch may modify only the expected upstream C++ file")
    environment = os.environ.copy()
    environment["GIT_CEILING_DIRECTORIES"] = str(source.parent)
    run(["git", "apply", "--check", str(patch)], cwd=source, environment=environment)
    run(["git", "apply", str(patch)], cwd=source, environment=environment)


def pe_profile(data):
    """Inspect, rather than execute, the rebuilt library's public ABI and imports."""
    import pefile
    image = pefile.PE(data=data)
    try:
        require(image.FILE_HEADER.Machine == 0x8664, "An AMD64 native library is required")
        exports = sorted(symbol.name.decode("ascii") for symbol in image.DIRECTORY_ENTRY_EXPORT.symbols
                         if symbol.name is not None)
        imports = sorted(entry.dll.decode("ascii") for entry in image.DIRECTORY_ENTRY_IMPORT)
        require(len(exports) == len(set(exports)), "Duplicate native export names")
        return {"exports": exports, "imports": imports,
                "export_names_sha256": hashlib.sha256("\n".join(exports).encode()).hexdigest()}
    finally:
        image.close()


def make_wheel(payloads, dll, import_library, provenance, license_text):
    """Create truthful local-version metadata and RECORD without Microsoft DLLs."""
    result, omitted = {}, []
    for name, data in payloads.items():
        if MS_RUNTIME.fullmatch(PurePosixPath(name).name):
            omitted.append(name)
            continue
        if name == OLD_INFO + "/RECORD":
            continue
        name = name.replace(OLD_INFO + "/", NEW_INFO + "/", 1)
        result[name] = data
    result["shiboken6/shiboken6.abi3.dll"] = dll
    result["shiboken6/shiboken6.abi3.lib"] = import_library
    metadata = BytesParser(policy=policy.default).parsebytes(result[NEW_INFO + "/METADATA"])
    metadata.replace_header("Version", VERSION)
    if "License" in metadata:
        del metadata["License"]
    if "License-Expression" in metadata:
        del metadata["License-Expression"]
    metadata["License-Expression"] = "GPL-3.0-only"
    metadata["License-File"] = "GPL-3.0-only.txt"
    result[NEW_INFO + "/METADATA"] = metadata.as_bytes(policy=policy.default.clone(linesep="\n", utf8=True))
    wheel_metadata = BytesParser(policy=policy.default).parsebytes(result[NEW_INFO + "/WHEEL"])
    if "Generator" in wheel_metadata:
        del wheel_metadata["Generator"]
    wheel_metadata["Generator"] = "etlayout source-runtime recipe (" + VERSION + ")"
    result[NEW_INFO + "/WHEEL"] = wheel_metadata.as_bytes(policy=policy.default.clone(linesep="\n"))
    result[NEW_INFO + "/licenses/GPL-3.0-only.txt"] = license_text
    record = {**provenance, "omitted_microsoft_runtime_files": sorted(omitted),
              "runtime_prerequisite": "Microsoft Visual C++ x64 v14 runtime; not bundled",
              "license_route": "GPL-3.0-only; upstream notices and offered grants retained"}
    result["shiboken6/ETLAYOUT_RUNTIME_BUILD.json"] = (json.dumps(record, indent=2) + "\n").encode()
    output = io.StringIO(newline="")
    writer = csv.writer(output, lineterminator="\n")
    for name, data in sorted(result.items()):
        digest = base64.urlsafe_b64encode(hashlib.sha256(data).digest()).rstrip(b"=").decode()
        writer.writerow((name, "sha256=" + digest, len(data)))
    writer.writerow((NEW_INFO + "/RECORD", "", ""))
    result[NEW_INFO + "/RECORD"] = output.getvalue().encode()
    return result


def write_zip(path, payloads):
    with zipfile.ZipFile(path, "x", zipfile.ZIP_DEFLATED, compresslevel=6) as archive:
        for name, data in sorted(payloads.items()):
            member = zipfile.ZipInfo(name, date_time=(1980, 1, 1, 0, 0, 0))
            member.compress_type = zipfile.ZIP_DEFLATED
            member.external_attr = (stat.S_IFREG | 0o644) << 16
            archive.writestr(member, data)
    with zipfile.ZipFile(path) as archive:
        require(archive.testzip() is None, "New archive failed CRC validation")


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-archive", type=Path, required=True)
    parser.add_argument("--upstream-wheel", type=Path, required=True)
    parser.add_argument("--build-dir", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--jobs", type=int, default=4)
    parser.add_argument("--cmake", default="cmake")
    args = parser.parse_args(argv)
    require(sys.platform == "win32" and sys.version_info[:2] == (3, 12),
            "Build with native Windows CPython 3.12")
    require(1 <= args.jobs <= 32, "Choose 1–32 build workers")
    build, output = checked_directories(args.build_dir, args.output_dir)
    output.mkdir(parents=True, exist_ok=True)
    build.mkdir(parents=True)
    source_archive, upstream_wheel = args.source_archive.resolve(), args.upstream_wheel.resolve()
    require(upstream_wheel.name == WHEEL_NAME, "Use the locked upstream Windows wheel")
    payloads = wheel_payloads(upstream_wheel)
    source = unpack_source(source_archive, build / "source")
    command_results = []

    def run(command, *, cwd=build, environment=None, timeout=900):
        started = time.perf_counter()
        result = subprocess.run(list(map(str, command)), cwd=cwd, env=environment, capture_output=True,
                                text=True, encoding="utf-8", errors="replace", timeout=timeout)
        log = build / f"command-{len(command_results):02d}.log"
        log.write_text(result.stdout + result.stderr, encoding="utf-8")
        command_results.append({"step": len(command_results), "exit_code": result.returncode,
                                "seconds": time.perf_counter() - started, "log": log.name})
        require(result.returncode == 0, f"Build or regression step failed; inspect {log.name}")
        return result.stdout

    patch = RECIPE / "referred_objects_lifetime.patch"
    before = sha256(source / PATCH_TARGET)
    apply_patch(source, patch, run)
    after = sha256(source / PATCH_TARGET)
    require(before != after, "The source lifetime patch did not change the expected file")
    cmake_build = build / "cmake"
    run([args.cmake, "-S", RECIPE, "-B", cmake_build, "-DCMAKE_BUILD_TYPE=Release",
         f"-DPYSIDE_SOURCE={source}", f"-DPython_EXECUTABLE={sys.executable}"])
    run([args.cmake, "--build", cmake_build, "--config", "Release", "--target", "libshiboken",
         "--parallel", args.jobs])
    dlls = list(cmake_build.rglob("shiboken6.abi3.dll"))
    libraries = list(cmake_build.rglob("shiboken6.abi3.lib"))
    require(len(dlls) == len(libraries) == 1, "Build must produce one stable-ABI DLL and import library")
    dll, library = dlls[0].read_bytes(), libraries[0].read_bytes()
    old_pe, new_pe = pe_profile(payloads["shiboken6/shiboken6.abi3.dll"]), pe_profile(dll)
    require(old_pe["exports"] == new_pe["exports"], "Rebuilt DLL changed the upstream public MSVC export ABI")
    require("python3.dll" in [name.lower() for name in new_pe["imports"]]
            and not any(name.lower().startswith("qt") for name in new_pe["imports"]),
            "Runtime must use the Python stable ABI without Qt SDK linkage")
    compiler = next(cmake_build.rglob("CMakeCXXCompiler.cmake")).read_text(encoding="utf-8")
    compiler_version = re.search(r'set\(CMAKE_CXX_COMPILER_VERSION "([^"]+)"\)', compiler)
    compiler_id = re.search(r'set\(CMAKE_CXX_COMPILER_ID "([^"]+)"\)', compiler)
    require(compiler_version and compiler_id and compiler_id[1] == "MSVC", "MSVC compiler provenance is required")
    provenance = {"schema_version": 1, "distribution": "shiboken6", "version": VERSION,
                  "upstream_version": "6.12.0", "python": ".".join(map(str, sys.version_info[:3])),
                  "abi": "cp310-abi3-win_amd64", "compiler": {"id": compiler_id[1], "version": compiler_version[1]},
                  "source": {"name": SOURCE_NAME, "sha256": SOURCE_SHA256, "bytes": SOURCE_BYTES},
                  "upstream_wheel": {"name": WHEEL_NAME, "sha256": WHEEL_SHA256},
                  "patch": {"name": patch.name, "sha256": sha256(patch), "target": PATCH_TARGET,
                            "before_sha256": before, "after_sha256": after},
                  "recipe": {name: sha256(RECIPE / name) for name in ("CMakeLists.txt", "build_runtime.py", "README.md")},
                  "dll_sha256": hashlib.sha256(dll).hexdigest(),
                  "import_library_sha256": hashlib.sha256(library).hexdigest(),
                  "unchanged_module_sha256": hashlib.sha256(payloads["shiboken6/Shiboken.pyd"]).hexdigest(),
                  "export_names_sha256": new_pe["export_names_sha256"], "imports": new_pe["imports"],
                  "scope": "Source-built libshiboken runtime; original Qt bindings and Shiboken module retained"}
    gpl = ROOT / "packaging/license-texts/GPL-3.0.txt"
    wheel = make_wheel(payloads, dll, library, provenance, gpl.read_bytes())
    wheel_path = build / OUTPUT_WHEEL
    write_zip(wheel_path, wheel)
    runtime = build / "runtime"
    runtime.mkdir()
    for name, data in wheel.items():
        target = runtime.joinpath(*safe_member(name).parts)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
    environment = os.environ.copy()
    environment["PYTHONPATH"] = os.pathsep.join((str(runtime), str(ROOT / "src")))
    environment["QT_QPA_PLATFORM"] = "offscreen"
    environment["PYTHONUTF8"] = "1"
    probe = ("import json,pathlib,shiboken6; from importlib.metadata import version; "
             f"assert pathlib.Path(shiboken6.__file__).resolve().parent == pathlib.Path({str(runtime / 'shiboken6')!r}); "
             f"assert version('shiboken6') == {VERSION!r}; "
             "from PySide6.QtWidgets import QApplication; app=QApplication([]); print('Patched runtime imported')")
    run([sys.executable, "-c", probe], environment=environment)
    regression = ROOT / "tests/test_qt_reference_lifetime.py"
    require(regression.is_file(), "The actual Qt lifetime regression gate is required")
    run([sys.executable, "-m", "pytest", regression, "-q", "--basetemp", build / "regression-temp",
         "--junitxml", build / "regression.xml"], cwd=ROOT, environment=environment, timeout=300)
    provenance["verification"] = {"regression_sha256": sha256(regression), "performed": True,
                                  "scope": "Actual rebuilt DLL with stock PySide6 bindings; no GC workaround",
                                  "commands": command_results, "wheel_sha256": sha256(wheel_path)}
    provenance_bytes = (json.dumps(provenance, indent=2) + "\n").encode()
    sources = {"archives/" + SOURCE_NAME: source_archive.read_bytes(),
               "modified/" + PATCH_TARGET: (source / PATCH_TARGET).read_bytes(),
               "BUILD_PROVENANCE.json": provenance_bytes,
               "licenses/GPL-3.0.txt": gpl.read_bytes(), "licenses/MPL-2.0.txt": (ROOT / "LICENSE").read_bytes()}
    for path in sorted(RECIPE.iterdir()):
        if path.is_file():
            sources["recipe/" + path.name] = path.read_bytes()
    source_path = build / "preferred-source.zip"
    write_zip(source_path, sources)
    for artifact_path, name in ((wheel_path, OUTPUT_WHEEL), (source_path, "preferred-source.zip")):
        with artifact_path.open("rb") as source_stream, (output / name).open("xb") as destination:
            shutil.copyfileobj(source_stream, destination)
    (output / "BUILD_PROVENANCE.json").write_bytes(provenance_bytes)
    shutil.copyfile(build / "regression.xml", output / "regression.xml")
    print(json.dumps({"wheel": OUTPUT_WHEEL, "sha256": sha256(output / OUTPUT_WHEEL),
                      "source_sha256": sha256(output / "preferred-source.zip"), "tests": "passed"}))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (ValueError, OSError, subprocess.SubprocessError) as error:
        print("Shiboken runtime build stopped: " + str(error), file=sys.stderr)
        raise SystemExit(1) from None
