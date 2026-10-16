# SPDX-License-Identifier: MPL-2.0
"""Build, exercise and archive the native Windows distribution reproducibly.

No recursive deletion is performed. PyInstaller receives explicit workspace
output paths and may replace its own previous build artifacts with --noconfirm.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import platform
import runpy
import shutil
import subprocess
import sys
import tempfile
import time
import tomllib
import zipfile
from datetime import datetime, timezone
from importlib import metadata
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
_resources = runpy.run_path(str(ROOT / "packaging" / "resources.py"))
distributable = _resources["distributable"]
synchronize_resources = _resources["synchronize_resources"]


def sha256(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def workspace_path(path):
    path = Path(path).resolve()
    if path == ROOT or not path.is_relative_to(ROOT):
        raise ValueError(f"Build output must be a child of the workspace: {path}")
    return path


def execute(command, *, cwd=ROOT, timeout=None, logfile=None, environment=None):
    started = time.perf_counter()
    print("Running:", subprocess.list2cmdline([str(value) for value in command]), flush=True)
    # CREATE_NO_WINDOW keeps build and smoke-test helpers hidden. The Qt GUI
    # still initializes its native Windows platform plugin during its smoke run.
    flags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
    if logfile:
        # Stream native tool output straight to a persistent log; build progress
        # remains inspectable while PyInstaller is scanning large scientific DLLs.
        with Path(logfile).open("w", encoding="utf-8") as stream:
            result = subprocess.run([str(value) for value in command], cwd=cwd, env=environment,
                                    stdout=stream, stderr=subprocess.STDOUT,
                                    timeout=timeout, creationflags=flags)
        output = Path(logfile).read_text(encoding="utf-8", errors="replace")
    else:
        result = subprocess.run([str(value) for value in command], cwd=cwd, env=environment,
                                text=True, encoding="utf-8", errors="replace", capture_output=True,
                                timeout=timeout, creationflags=flags)
        output = result.stdout + result.stderr
    if result.returncode:
        print(output[-16000:], flush=True)
        raise RuntimeError(f"Command exited {result.returncode}; see {logfile or 'output above'}")
    return {"check_name": Path(logfile).stem if logfile else "command",
            "command": [str(v) for v in command], "exit_code": result.returncode,
            "elapsed_seconds": time.perf_counter() - started, "output": output[-12000:]}


def public_check_summary(check):
    """Keep build-host paths and command output in private verification receipts."""
    return {"name": check.get("check_name", "command"),
            "executable": Path(check["command"][0]).name,
            "exit_code": check["exit_code"], "elapsed_seconds": check["elapsed_seconds"]}


def source_files():
    roots = ["src", "tests", "docs", "scripts", "packaging", "examples", "benchmarks", "LICENSES", ".github"]
    for directory in roots:
        folder = ROOT / directory
        if not folder.exists():
            continue
        for path in sorted(folder.rglob("*")):
            if path.is_file() and distributable(path.relative_to(ROOT)):
                yield path
    for name in ("pyproject.toml", "requirements-windows-py312.lock", "README.md", "CHANGELOG.md", "CONTRIBUTING.md", "LICENSE",
                 "THIRD_PARTY_NOTICES.md", "DEPENDENCIES.json", "REUSE.toml", "SECURITY.md", "MANIFEST.in", ".gitignore"):
        path = ROOT / name
        if path.exists():
            yield path


def write_json(path, value):
    Path(path).write_text(json.dumps(value, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def source_fingerprint():
    return {path.relative_to(ROOT).as_posix(): sha256(path) for path in source_files()}


def require_source_snapshot(expected):
    if source_fingerprint() != expected:
        raise RuntimeError("Source or resources changed during the build; finish edits and rebuild before producing archives")


def code_fingerprint():
    return {path.relative_to(ROOT).as_posix(): sha256(path) for path in source_files()
            if path.suffix in {".py", ".spec", ".lock", ".toml"}}


def native_build_profile(library, descriptor, *, python_library=None):
    """Bind a release profile to reviewed source recipes and exact native bytes.

    A normal stock-wheel candidate remains useful locally. It is explicitly
    ineligible for this release profile, even if its smoke tests pass.
    """
    if library is None and descriptor is None:
        return {"schema_version": 1, "audited_source_profile": False,
                "reason": "Stock-wheel local candidate; no source-build provenance selected"}
    if library is None or descriptor is None:
        raise ValueError("--gmsh-library and --native-build-manifest must be supplied together")
    library, descriptor = Path(library), Path(descriptor)
    if (not library.is_file() or library.is_symlink() or not descriptor.is_file()
            or descriptor.is_symlink() or descriptor.stat().st_size > 1024**2):
        raise ValueError("Source-built native inputs must be ordinary bounded files")
    catalog = json.loads((ROOT / "packaging/third-party-sources.json").read_text(encoding="utf-8"))
    proof = json.loads(descriptor.read_text(encoding="utf-8"))
    gmsh = [row for row in catalog["compiled_native"] if row["id"] == "gmsh"]
    if len(gmsh) != 1:
        raise ValueError("Exactly one audited Gmsh source build is required")
    python_library = Path(python_library or (Path(sys.base_prefix) / "python312.dll"))
    runtime = catalog["runtime_python"]
    if (proof.get("schema_version") != 1 or proof.get("runtime_python") != runtime
            or any(proof.get("gmsh", {}).get(key) != value for key, value in gmsh[0].items() if key != "id")
            or library.name != gmsh[0]["filename"] or sha256(library) != gmsh[0]["sha256"]
            or not python_library.is_file() or sha256(python_library) != runtime["python312_dll_sha256"]):
        raise ValueError("Native library, Python runtime or build provenance differs from the audited profile")
    # Copy only public catalog data. Private paths/build logs never enter a bundle.
    return {"schema_version": 1, "audited_source_profile": True,
            "compiled_native": catalog["compiled_native"], "runtime_python": runtime,
            "build_descriptor_sha256": sha256(descriptor),
            "source_catalog_sha256": sha256(ROOT / "packaging/third-party-sources.json")}


def isolated_environment(build=False):
    """Prevent DLLs from unrelated PATH tools contaminating the frozen bundle.

    In particular Qt uses Windows' ICU exports; an unrelated Poppler installation
    may carry an identically named icuuc.dll with incompatible versioned exports.
    """
    env = os.environ.copy()
    for key in ("PYTHONPATH", "PYTHONHOME", "VIRTUAL_ENV", "QT_PLUGIN_PATH", "QT_QPA_PLATFORM_PLUGIN_PATH",
                "COVERAGE_PROCESS_START", "COVERAGE_RCFILE"):
        env.pop(key, None)
    system_root = Path(env.get("SystemRoot", "C:/Windows"))
    search = [system_root / "System32", system_root]
    if build:
        search = [Path(sys.executable).parent, Path(sys.base_prefix), *search]
    env["PATH"] = os.pathsep.join(map(str, search))
    env["QT_QPA_PLATFORM"] = "windows"
    return env


def verify_extracted_archive(application_zip, verification, environment):
    """Exercise the delivered payload outside the checkout, with no Python PATH.

    This is a build-host deployment smoke test, not clean-machine certification.
    A temporary location also catches accidental dependence on source paths or
    the working directory. Logs/receipts stay in the normal verification output.
    """
    checks = []
    # Reject malformed archives before creating a deployment folder. This check
    # is independent of the subsequent recipient-like native executable tests.
    extraction_root = Path(tempfile.gettempdir()).resolve() / "etlayout-extracted-root"
    with zipfile.ZipFile(application_zip) as archive:
        if any(not (extraction_root / entry.filename).resolve().is_relative_to(extraction_root)
               for entry in archive.infolist()):
            raise RuntimeError("Application archive contains an unsafe extraction path")
    with tempfile.TemporaryDirectory(prefix="etlayout-extracted-") as temporary:
        destination = Path(temporary).resolve()
        outside_checkout = not destination.is_relative_to(ROOT.resolve())
        if not outside_checkout:
            raise RuntimeError("Extracted deployment validation requires a TEMP directory outside the checkout")
        with zipfile.ZipFile(application_zip) as archive:
            failed = archive.testzip()
            if failed:
                raise RuntimeError(f"Archive CRC verification failed: {failed}")
            archive.extractall(destination)
        bundle = destination / "ElectrothermalLayoutStudio"
        cli = bundle / "etlayout.exe"
        gui = bundle / "ElectrothermalLayoutStudio.exe"
        examples = bundle / "examples"
        commands = {
            "version": [cli, "--version"],
            "inspect-gds": [cli, "inspect", examples / "E1_straight.gds"],
            "inspect-oas": [cli, "inspect", examples / "E1_straight.oas"],
            "solve": [cli, "solve", examples / "E1_straight.etl", "--no-plots", "--output", destination / "solved"],
            "gui": [gui, "gui", destination / "solved" / "simulation.etl", "--smoke-test", "--no-recovery",
                    "--screenshot", verification / "extracted-gui.png"],
            "worker": [gui, "--worker-smoke", examples / "E1_straight.etl", "--output", verification / "extracted-worker.json"],
        }
        for name, command in commands.items():
            checks.append(execute(command, cwd=bundle, timeout=180,
                                  logfile=verification / f"archive-{name}.log", environment=environment))
        with zipfile.ZipFile(destination / "solved" / "simulation.etl") as result_archive:
            result = json.loads(result_archive.read("results/metadata.json"))
        if not result.get("converged") or result["metrics"]["effective_resistance"] <= 0:
            raise RuntimeError("Extracted package failed its configured E1 numerical solve")
        with zipfile.ZipFile(verification / "E1" / "simulation.etl") as original_archive:
            original_result = json.loads(original_archive.read("results/metadata.json"))
        comparisons = {}
        for key in ("effective_resistance", "total_current", "electrical_power", "temperature_max"):
            current, previous = result["metrics"].get(key), original_result["metrics"].get(key)
            if current is None and previous is None:
                continue
            # Each process meshes the portable input independently; numbering
            # and floating-point elimination order can differ. This strict
            # integral comparison is separate from unchanged solver acceptance.
            if current is None or previous is None or not math.isclose(current, previous, rel_tol=1e-9, abs_tol=0.):
                raise RuntimeError(f"Extracted-package E1 changed the frozen-build numerical metric {key}")
            comparisons[key] = {"build": previous, "extracted": current, "relative_tolerance": 1e-9}
        worker = json.loads((verification / "extracted-worker.json").read_text(encoding="utf-8"))
        if not worker.get("success") or worker.get("heartbeat_ticks", 0) < 5:
            raise RuntimeError("Extracted package failed its responsive frozen GUI worker solve")
        receipt = {"archive": application_zip.name, "sha256": sha256(application_zip), "crc_verified": True,
                   "host_scope": "Native build host; not a clean-machine test", "outside_checkout": outside_checkout,
                   "extraction_directory": str(destination),
                   "path": environment["PATH"], "qpa": environment["QT_QPA_PLATFORM"],
                   "checks": checks, "solved_metrics": result["metrics"], "matches_build_host_E1": True,
                   "integral_comparisons": comparisons,
                   "worker_heartbeat_ticks": worker["heartbeat_ticks"]}
        write_json(verification / "archive-results.json", receipt)
    return receipt


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--skip-build", action="store_true", help="Re-test/repackage an existing onedir build")
    parser.add_argument("--skip-tests", action="store_true", help="Build only; never mark runtime verification passed")
    parser.add_argument("--no-archive", action="store_true", help="Skip ZIP creation during development iterations")
    parser.add_argument("--gmsh-library", type=Path, help="Exact reviewed source-built Gmsh DLL")
    parser.add_argument("--native-build-manifest", type=Path, help="Hash-bound source-build evidence; used with --gmsh-library")
    args = parser.parse_args(argv)
    if sys.platform != "win32" or platform.architecture()[0] != "64bit":
        parser.error("Build requires a native 64-bit Windows Python environment")
    profile = native_build_profile(args.gmsh_library, args.native_build_manifest)
    project_metadata = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))["project"]
    installed = metadata.metadata(project_metadata["name"])
    if (installed.get("Version") != project_metadata["version"]
            or installed.get("License-Expression") != project_metadata["license"]):
        raise RuntimeError("Installed application metadata differs from this source edition. Build/install its wheel in an isolated build environment before freezing; do not reuse metadata from another checkout.")
    distribution_root = workspace_path(ROOT / "dist")
    build_root = workspace_path(ROOT / "build")
    bundle = workspace_path(distribution_root / "ElectrothermalLayoutStudio")
    verification = workspace_path(build_root / "verification")
    distribution_root.mkdir(exist_ok=True)
    verification.mkdir(parents=True, exist_ok=True)
    source_before = source_fingerprint()
    code_before = {name: digest for name, digest in source_before.items()
                   if Path(name).suffix in {".py", ".spec", ".lock", ".toml"}}
    if args.skip_build:
        manifest_path = bundle / "BUILD_MANIFEST.json"
        if not manifest_path.is_file():
            raise RuntimeError("Existing build has no source manifest; omit --skip-build to establish source provenance")
        previous = json.loads(manifest_path.read_text(encoding="utf-8"))
        if (previous.get("code_sha256") != code_before
                or previous.get("source_sha256") != source_before):
            raise RuntimeError("Source changed since the existing build; omit --skip-build to keep binary and source archives consistent")
    if not args.skip_build:
        if not (ROOT / "LICENSE").is_file() or not (ROOT / "THIRD_PARTY_NOTICES.md").is_file():
            raise RuntimeError("LICENSE and THIRD_PARTY_NOTICES.md are required for a distributable build")
        execute([sys.executable, ROOT / "scripts" / "collect_licenses.py", ROOT / "artifacts" / "licenses"],
                timeout=120, logfile=verification / "licenses.log")
        build_environment = isolated_environment(build=True)
        if args.gmsh_library is not None:
            build_environment["ETLAYOUT_GMSH_LIBRARY"] = str(args.gmsh_library.resolve())
        execute([sys.executable, "-m", "PyInstaller", "--noconfirm", "--distpath", distribution_root,
                 "--workpath", build_root / "pyinstaller-windows", ROOT / "packaging" / "etlayout.spec"],
                timeout=1800, logfile=verification / "pyinstaller.log", environment=build_environment)
    cli, gui = bundle / "etlayout.exe", bundle / "ElectrothermalLayoutStudio.exe"
    if not cli.is_file() or not gui.is_file():
        raise RuntimeError("Expected GUI and CLI executables are absent; run without --skip-build")
    # User-facing resources live next to the executables as well as in _internal
    # so they are easy to find without knowing PyInstaller's layout.
    for folder in ("examples", "docs"):
        if (ROOT / folder).is_dir():
            synchronize_resources(ROOT, bundle, folder)
            # Keep the runtime resource copy and adjacent readable copy identical.
            synchronize_resources(ROOT, bundle / "_internal", folder)
    if (ROOT / "artifacts" / "licenses").is_dir():
        shutil.copytree(ROOT / "artifacts" / "licenses", bundle / "licenses", dirs_exist_ok=True)
    if (ROOT / "packaging" / "license-texts").is_dir():
        shutil.copytree(ROOT / "packaging" / "license-texts", bundle / "licenses" / "license-texts", dirs_exist_ok=True)
    python_license = Path(sys.base_prefix) / "LICENSE.txt"
    if python_license.is_file():
        (bundle / "licenses" / "Python").mkdir(parents=True, exist_ok=True)
        shutil.copy2(python_license, bundle / "licenses" / "Python" / "LICENSE.txt")
    for filename in ("README.md", "CHANGELOG.md", "CONTRIBUTING.md", "LICENSE", "THIRD_PARTY_NOTICES.md", "DEPENDENCIES.json", "REUSE.toml", "requirements-windows-py312.lock"):
        if (ROOT / filename).is_file():
            shutil.copy2(ROOT / filename, bundle / filename)
    runtime = runpy.run_path(str(ROOT / "packaging/windows_runtime.py"))["prepare_runtime_prerequisite"](bundle)
    if profile["audited_source_profile"]:
        gmsh = next(row for row in profile["compiled_native"] if row["id"] == "gmsh")
        if sha256(bundle / "_internal" / gmsh["filename"]) != gmsh["sha256"]:
            raise RuntimeError("Collected Gmsh differs from the reviewed source build")
        if sha256(bundle / "_internal" / "python312.dll") != profile["runtime_python"]["python312_dll_sha256"]:
            raise RuntimeError("Collected Python differs from the reviewed public runtime")
    write_json(bundle / "SOURCE_BUILD_PROVENANCE.json", profile)
    checks = []
    if not args.skip_tests:
        # Avoid development PYTHONPATH and venv PATH entries during frozen checks.
        env = isolated_environment()
        examples = bundle / "examples"
        checks.append(execute([cli, "--version"], cwd=bundle, timeout=60,
                              logfile=verification / "version.log", environment=env))
        for extension in ("gds", "oas"):
            checks.append(execute([cli, "inspect", examples / f"E1_straight.{extension}"], cwd=bundle,
                                  timeout=60, logfile=verification / f"inspect-{extension}.log", environment=env))
        checks.append(execute([cli, "solve", examples / "E1_straight.etl", "--output", verification / "E1"],
                              cwd=bundle, timeout=180, logfile=verification / "solve-E1.log", environment=env))
        solved_project = verification / "E1" / "simulation.etl"
        checks.append(execute([cli, "gui", solved_project, "--smoke-test", "--no-recovery",
                               "--screenshot", verification / "frozen-gui.png"],
                              cwd=bundle, timeout=90, logfile=verification / "gui-smoke.log", environment=env))
        checks.append(execute([gui, "gui", solved_project, "--smoke-test", "--no-recovery"],
                              cwd=bundle, timeout=90, logfile=verification / "windowed-smoke.log", environment=env))
        checks.append(execute([gui, "--worker-smoke", examples / "E1_straight.etl", "--output", verification / "frozen-worker.json"],
                              cwd=bundle, timeout=150, logfile=verification / "worker-smoke.log", environment=env))
        pulsed_project = examples / "E19_pulsed_multilayer.etl"
        checks.append(execute([cli, "solve", pulsed_project, "--no-plots", "--output", verification / "E19"],
                              cwd=bundle, timeout=180, logfile=verification / "solve-E19.log", environment=env))
        pulsed_result = verification / "E19" / "simulation.etl"
        checks.append(execute([cli, "transient", pulsed_project, "--resume", pulsed_result,
                               "--stop-time", ".0014", "--no-plots", "--output", verification / "E19-resume"],
                              cwd=bundle, timeout=180, logfile=verification / "resume-E19.log", environment=env))
        checks.append(execute([cli, "solve", verification / "E19-resume" / "simulation.etl", "--no-plots",
                               "--output", verification / "E19-replay"],
                              cwd=bundle, timeout=180, logfile=verification / "replay-E19.log", environment=env))
        checks.append(execute([gui, "--worker-smoke", pulsed_project, "--time-study",
                               "--output", verification / "frozen-transient-study.json"],
                              cwd=bundle, timeout=180, logfile=verification / "transient-worker-smoke.log", environment=env))
        # Generate real synthetic input with the source tool, then perform all
        # numerical work below in the frozen executable with an isolated PATH.
        from etlayout.io.project import save_project
        fixture = runpy.run_path(str(ROOT / "examples" / "power_routing" / "generate.py"))
        # Routing intentionally refuses to overwrite a candidate. Keep each
        # repeated build/--skip-build check in its own auditable directory.
        power_checks = verification / ("power-" + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%f"))
        power_input = power_checks / "inputs"
        power_project = fixture["build_project"](power_input)
        power_project.power.ports[0].value = .01
        power_project.power.experiments[0].max_evaluations = 1
        power_path = power_input / "frozen-power.etl"
        save_project(power_project, power_path)
        commands = {
            "power": ["power", power_path, "--no-plots", "--output", power_checks / "analysis"],
            "multiport": ["multiport", power_path, "--output", power_checks / "multiport.json"],
            "routing": ["routing", power_path, "--action", "generate", "--parameters", '{"bus.width": 0.000006}',
                        "--output", power_checks / "routed.oas"],
            "optimization": ["optimize", power_path, "--experiment", "four-cell-feed", "--output", power_checks / "search"],
            "optimization-resume": ["optimize", power_path, "--experiment", "four-cell-feed", "--output", power_checks / "search"],
        }
        history_digest = None
        for name, arguments in commands.items():
            checks.append(execute([cli, *arguments], cwd=bundle, timeout=300,
                                  logfile=verification / f"frozen-{name}.log", environment=env))
            if name.startswith("optimization"):
                history_path = power_checks / "search" / "history.json"
                recorded = json.loads(history_path.read_text(encoding="utf-8"))
                baseline = recorded["rows"][0]
                if (recorded["status"] != "complete" or baseline["status"] != "feasible_provisional"
                        or baseline.get("verification", {}).get("status") != "mesh_resolved"):
                    raise RuntimeError("Frozen baseline experiment did not complete feasible mesh verification")
                if history_digest is not None and history_digest != sha256(history_path):
                    raise RuntimeError("Frozen resume changed an unchanged completed experiment history")
                history_digest = sha256(history_path)
        # Exercise dynamic example registration and physical-port drives inside
        # the frozen program, without starting large engineering benchmarks.
        for identifier, name in (("E21", "E21_long_finger_gan"), ("E23", "E23_sic_stripes")):
            compact = power_checks / identifier
            checks.append(execute([cli, "examples", compact, "--name", identifier, "--preset", "compact"],
                                  cwd=bundle, timeout=90, logfile=verification / f"frozen-generate-{identifier}.log", environment=env))
            checks.append(execute([cli, "solve", compact / (name + ".etl"), "--no-plots", "--output", compact / "solved"],
                                  cwd=bundle, timeout=240, logfile=verification / f"frozen-solve-{identifier}.log", environment=env))
            checks.append(execute([gui, "gui", examples / (name + ".etl"), "--smoke-test", "--no-recovery"],
                                  cwd=bundle, timeout=120, logfile=verification / f"frozen-reference-{identifier}.log", environment=env))
        # The compact inputs can deliberately stay serial. Exercise a genuine
        # engineering-size assembly in the frozen program, at its shipped 5 A
        # operating point, and inspect persisted execution and service evidence.
        threaded_output = power_checks / "E21-reference"
        checks.append(execute([cli, "solve", examples / "E21_long_finger_gan.etl", "--no-plots",
                               "--output", threaded_output], cwd=bundle, timeout=600,
                              logfile=verification / "frozen-E21-parallel.log", environment=env))
        with zipfile.ZipFile(threaded_output / "simulation.etl") as archive:
            threaded_result = json.loads(archive.read("results/metadata.json"))
        execution = threaded_result["execution"]
        assembly = execution["parallel_assembly"]
        if (not threaded_result["converged"]
                or abs(threaded_result["analysis"]["ports"]["drain"]["current"] / 5. - 1.) > 1e-6):
            raise RuntimeError("Frozen E21 reference did not deliver its nominal 5 A coupled solution")
        if execution["cpu_threads"] > 1 and (not assembly["batches"] or len(assembly["worker_thread_ids"]) < 2):
            raise RuntimeError("Frozen E21 reference did not execute parallel assembly within its CPU budget")
        write_json(verification / "frozen-E21-parallel.json", {
            "execution": execution, "metrics": threaded_result["metrics"],
            "physics_hash": threaded_result["physics_hash"],
            "converged": threaded_result["converged"],
            "delivered_current_A": threaded_result["analysis"]["ports"]["drain"]["current"],
        })
        editor_output = verification / ("editor-" + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%f"))
        checks.append(execute([gui, "gui", examples / "E1_straight.etl", "--smoke-test", "--no-recovery",
                               "--editor-smoke-output", editor_output], cwd=bundle, timeout=180,
                              logfile=verification / "frozen-editor.log", environment=env))
        editor_receipt = json.loads((editor_output / "receipt.json").read_text(encoding="utf-8"))
        if editor_receipt.get("status") != "passed" or not editor_receipt.get("frozen"):
            raise RuntimeError("Frozen editor workers, persistence and source-immutability verification did not pass")
        checks.append(execute([cli, "solve", editor_output / "edited.etl", "--no-plots", "--output", editor_output / "solved"],
                              cwd=bundle, timeout=180, logfile=verification / "frozen-editor-solve.log", environment=env))
    # Keep both readable resource copies identical before the final snapshot check.
    for target in (bundle, bundle / "_internal"):
        synchronize_resources(ROOT, target, "docs")
    for filename in ("README.md", "CHANGELOG.md", "CONTRIBUTING.md", "THIRD_PARTY_NOTICES.md", "DEPENDENCIES.json"):
        if (ROOT / filename).is_file():
            shutil.copy2(ROOT / filename, bundle / filename)
            shutil.copy2(ROOT / filename, bundle / "_internal" / filename)
    source = sorted(set(source_files()))
    require_source_snapshot(source_before)
    revision = subprocess.run(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True, capture_output=True)
    manifest = {
        "application": "Electrothermal Layout Studio", "version": metadata.version("etlayout"),
        "built_utc": datetime.now(timezone.utc).isoformat(), "platform": platform.platform(),
        "python": platform.python_version(), "pyinstaller": metadata.version("pyinstaller"),
        "git_head": revision.stdout.strip() if revision.returncode == 0 else None,
        "source_tree": "Working tree snapshot; per-file hashes identify exact source, including uncommitted edits",
        "source_sha256": source_before,
        "code_sha256": code_before,
        "installed_packages": sorted(f"{d.metadata['Name']}=={d.version}" for d in metadata.distributions()),
        "verification": {"performed": not args.skip_tests, "host": "Native build host; not a clean-machine test", "checks": [public_check_summary(check) for check in checks]},
        "native_source_profile": profile,
        "windows_runtime": runtime,
        "dependency_source": "See THIRD_PARTY_NOTICES.md for bundled dependencies and corresponding-source obligations",
    }
    write_json(bundle / "BUILD_MANIFEST.json", manifest)
    write_json(verification / "smoke-results.json", {
        "performed": not args.skip_tests, "host": "Native build host; not a clean-machine test", "checks": checks})
    # Candidate-only evidence. No script in this build publishes an artifact.
    execute([sys.executable, ROOT / "scripts" / "create_sbom.py", "--kind", "frozen",
             "--artifact", bundle, "--output", bundle / "compliance"], timeout=180,
            logfile=verification / "sbom.log")
    shutil.copyfile(ROOT / "packaging" / "binary-publication-policy.json", bundle / "compliance" / "binary-publication-policy.json")
    print("Local candidate only: publication requires exact artifact review and owner release preflight", flush=True)
    if not args.no_archive:
        name = f"ElectrothermalLayoutStudio-{manifest['version']}-windows-x64"
        application_zip = distribution_root / (name + ".zip")
        with zipfile.ZipFile(application_zip, "w", zipfile.ZIP_DEFLATED, compresslevel=6) as archive:
            for path in sorted(bundle.rglob("*")):
                if path.is_file():
                    archive.write(path, str(Path(bundle.name) / path.relative_to(bundle)))
        source_zip = distribution_root / f"etlayout-{manifest['version']}-source.zip"
        with zipfile.ZipFile(source_zip, "w", zipfile.ZIP_DEFLATED) as archive:
            for path in source:
                archive.write(path, str(Path("etlayout") / path.relative_to(ROOT)))
            archive.writestr("etlayout/SOURCE_MANIFEST.json", json.dumps(manifest, indent=2))
        write_json(distribution_root / "SHA256SUMS.json", {
            application_zip.name: sha256(application_zip), source_zip.name: sha256(source_zip)})
        if not args.skip_tests:
            # Validate ZIP integrity and the extracted payload, exercising the
            # distribution exactly as a recipient will unpack it.
            verify_extracted_archive(application_zip, verification, env)
        print(f"Distribution: {application_zip}\nApplication source: {source_zip}", flush=True)
    require_source_snapshot(source_before)
    print(f"Native smoke checks passed: {len(checks)}; details in {verification}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
