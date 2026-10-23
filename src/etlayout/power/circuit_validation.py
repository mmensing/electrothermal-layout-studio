# SPDX-License-Identifier: MPL-2.0
"""Portable DC circuit checks with an explicitly selected external simulator.

Only freshly generated, fixed-temperature resistor subcircuits and basis decks
are executable here. Imported circuit text, simulator initialization files and
shell commands are never used. An external check is separate from extraction
convergence and mesh verification.
"""
from __future__ import annotations

import hashlib
import json
import math
import os
import re
import signal
import subprocess
import tempfile
import threading
import time
from pathlib import Path

import numpy as np
import psutil

from etlayout.models import Cancelled, SimulationError, checkpoint
from etlayout.power.multiport import spice_netlist
from etlayout.simulation.execution import validate_execution

CURRENT_RTOL = 1e-8
_MAX_PORTS = 128
_BEGIN = "ETLAYOUT_CURRENT_BEGIN"
_END = "ETLAYOUT_CURRENT_END"
_CURRENT = re.compile(r"^i\(v(\d+)\)\s*=\s*([-+\d.eE]+)\s*$", re.IGNORECASE)


def _json_bytes(value):
    return (json.dumps(value, ensure_ascii=True, sort_keys=True, indent=2, allow_nan=False) + "\n").encode("ascii")


def _file_sha256(path, cancel=None):
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while block := stream.read(1024 * 1024):
            checkpoint(cancel)
            digest.update(block)
    return digest.hexdigest()


def _basis_deck(count, basis):
    voltages = "\n".join(f"V{i + 1} n{i + 1} 0 DC {int(i == basis)}" for i in range(count))
    nodes = " ".join(f"n{i + 1}" for i in range(count))
    currents = " ".join(f"i(V{i + 1})" for i in range(count))
    return ("ETLayout generated passive DC basis check\n.include network.cir\n"
            f"{voltages}\nXdut {nodes} ETLAYOUT\n.control\nset noaskquit\nset numdgt=17\n"
            f"op\necho {_BEGIN}\nprint {currents}\necho {_END}\nquit\n.endc\n.end\n")


def _bundle_contents(report):
    count = len(report["port_ids"])
    if count > _MAX_PORTS:
        raise SimulationError(f"Circuit validation is bounded to {_MAX_PORTS} ports")
    # Reuse the authoritative positive-resistor/per-port fidelity acceptance.
    netlist = spice_netlist(report)
    operator = np.asarray(report["conductance_matrix"], dtype=float)
    provenance = {key: report[key] for key in ("configuration_hash", "physics_hash", "mesh", "temperature",
                 "build", "solver_version", "extraction_context", "source_physics_hash",
                 "source_configuration_hash") if key in report}
    model = {"port_ids": list(report["port_ids"]), "reference": report["reference"],
             "conductance_matrix_S": operator.tolist(), "provenance": provenance}
    files = {"network.cir": netlist.encode("ascii"), "operator.json": _json_bytes(model)}
    cases = []
    for basis in range(count):
        name = f"basis-{basis + 1:03d}.cir"
        files[name] = _basis_deck(count, basis).encode("ascii")
        cases.append({"deck": name, "excited_port": report["port_ids"][basis], "basis_index": basis,
                      "expected_entering_currents_A": operator[:, basis].tolist()})
    manifest = {"schema_version": 1, "status": "prepared", "model": "fixed-temperature passive DC",
                "current_sign": "positive entering network; negative of simulator voltage-source current",
                "relative_tolerance": CURRENT_RTOL, "absolute_tolerance_A": 0., "port_ids": model["port_ids"],
                "reference": model["reference"], "provenance": provenance, "cases": cases,
                "files": {name: {"sha256": hashlib.sha256(data).hexdigest(), "bytes": len(data)}
                          for name, data in sorted(files.items())},
                "limitations": "External DC agreement does not establish mesh convergence, calibration, RLC or thermal feedback."}
    files["manifest.json"] = _json_bytes(manifest)
    return manifest, files


def prepare_circuit_validation(report, directory, *, cancel=None):
    """Write a deterministic self-contained bundle to a new or empty directory.

    The manifest records exact file hashes, source identities and all N expected
    current responses for each of N unit-voltage excitations. No solve is run.
    Existing nonempty directories are rejected without replacing their files.
    """
    checkpoint(cancel)
    manifest, files = _bundle_contents(report)
    destination = Path(directory).expanduser().absolute()
    if destination.exists() and (not destination.is_dir() or any(destination.iterdir())):
        raise SimulationError("Circuit-validation output must be a new or empty directory")
    destination.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=".etlayout-circuit-", dir=destination.parent) as temporary:
        staging = Path(temporary)
        for name, data in files.items():
            checkpoint(cancel)
            (staging / name).write_bytes(data)
        checkpoint(cancel)
        if destination.exists():
            destination.rmdir()  # Only the already checked empty directory.
        os.replace(staging, destination)
    return manifest


def _environment(executable, working):
    # Do not leak credentials, PYTHONPATH, startup scripts or numerical pool
    # preferences from the application into the external simulator.
    environment = {key: os.environ[key] for key in ("SystemRoot", "WINDIR", "LANG", "LC_ALL")
                   if key in os.environ}
    system = Path(os.environ.get("SystemRoot", "/usr"))
    environment.update(PATH=os.pathsep.join(map(str, (executable.parent, system / "System32", system))),
                       HOME=str(working), USERPROFILE=str(working), XDG_CONFIG_HOME=str(working),
                       TEMP=str(working), TMP=str(working), OMP_NUM_THREADS="1", OPENBLAS_NUM_THREADS="1",
                       MKL_NUM_THREADS="1", BLIS_NUM_THREADS="1", VECLIB_MAXIMUM_THREADS="1")
    return environment


class _WindowsJob:
    """Own all simulator descendants before its first instruction can execute."""

    def __init__(self):
        import ctypes
        from ctypes import wintypes

        class BasicLimits(ctypes.Structure):
            _fields_ = [("process_time", ctypes.c_longlong), ("job_time", ctypes.c_longlong),
                        ("flags", wintypes.DWORD), ("minimum_working_set", ctypes.c_size_t),
                        ("maximum_working_set", ctypes.c_size_t), ("active_processes", wintypes.DWORD),
                        ("affinity", ctypes.c_size_t), ("priority", wintypes.DWORD),
                        ("scheduling", wintypes.DWORD)]

        class IoCounters(ctypes.Structure):
            _fields_ = [(name, ctypes.c_ulonglong) for name in ("reads", "writes", "other", "read_bytes",
                        "write_bytes", "other_bytes")]

        class ExtendedLimits(ctypes.Structure):
            _fields_ = [("basic", BasicLimits), ("io", IoCounters),
                        ("process_memory", ctypes.c_size_t), ("job_memory", ctypes.c_size_t),
                        ("peak_process_memory", ctypes.c_size_t), ("peak_job_memory", ctypes.c_size_t)]

        self.ctypes = ctypes
        self.kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        signatures = {
            "CreateJobObjectW": ([ctypes.c_void_p, wintypes.LPCWSTR], wintypes.HANDLE),
            "SetInformationJobObject": ([wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD], wintypes.BOOL),
            "AssignProcessToJobObject": ([wintypes.HANDLE, wintypes.HANDLE], wintypes.BOOL),
            "OpenThread": ([wintypes.DWORD, wintypes.BOOL, wintypes.DWORD], wintypes.HANDLE),
            "ResumeThread": ([wintypes.HANDLE], wintypes.DWORD),
            "CloseHandle": ([wintypes.HANDLE], wintypes.BOOL),
        }
        for name, (arguments, result) in signatures.items():
            function = getattr(self.kernel, name)
            function.argtypes, function.restype = arguments, result
        self.handle = self.kernel.CreateJobObjectW(None, None)
        if not self.handle:
            raise ctypes.WinError(ctypes.get_last_error())
        limits = ExtendedLimits()
        limits.basic.flags = 0x2000  # JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
        if not self.kernel.SetInformationJobObject(self.handle, 9, ctypes.byref(limits), ctypes.sizeof(limits)):
            error = ctypes.WinError(ctypes.get_last_error())
            self.close()
            raise error

    def attach_and_resume(self, process):
        if not self.kernel.AssignProcessToJobObject(self.handle, int(process._handle)):
            raise self.ctypes.WinError(self.ctypes.get_last_error())
        threads = psutil.Process(process.pid).threads()
        if len(threads) != 1:
            raise OSError("Could not identify the suspended simulator's initial thread")
        thread = self.kernel.OpenThread(0x0002, False, threads[0].id)  # THREAD_SUSPEND_RESUME
        if not thread:
            raise self.ctypes.WinError(self.ctypes.get_last_error())
        try:
            if self.kernel.ResumeThread(thread) == 0xffffffff:
                raise self.ctypes.WinError(self.ctypes.get_last_error())
        finally:
            self.kernel.CloseHandle(thread)

    def close(self):
        if self.handle:
            self.kernel.CloseHandle(self.handle)
            self.handle = None


def _stop_process(process, descendants):
    # Terminate only descendants observed under this exact child process. psutil
    # guards against PID reuse; never enumerate or stop unrelated simulator jobs.
    if os.name != "nt":
        try:
            os.killpg(process.pid, signal.SIGTERM)
        except ProcessLookupError:
            pass
    for child in reversed(list(descendants.values())):
        try:
            child.terminate()
        except psutil.Error:
            pass
    if process.poll() is None:
        process.terminate()
        try:
            process.wait(timeout=1.)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=1.)
    _, alive = psutil.wait_procs(list(descendants.values()), timeout=1.)
    for child in alive:
        try:
            child.kill()
        except psutil.Error:
            pass
    psutil.wait_procs(alive, timeout=1.)
    if os.name != "nt":
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass


def _execute_deck(executable, working, deck, *, timeout, max_output_bytes, cancel):
    output = bytearray()
    overflow = threading.Event()
    started = time.monotonic()
    command = [str(executable), "-n", "-b", deck]
    flags = (subprocess.CREATE_NO_WINDOW | 0x00000004) if os.name == "nt" else 0  # CREATE_SUSPENDED
    job = None
    process = None
    try:
        if os.name == "nt":
            job = _WindowsJob()
        process = subprocess.Popen(command, cwd=working, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                                   stderr=subprocess.STDOUT, shell=False, env=_environment(executable, working),
                                   creationflags=flags, start_new_session=os.name != "nt")
        if job is not None:
            job.attach_and_resume(process)
    except (OSError, psutil.Error) as error:
        if process is not None:
            _stop_process(process, {})
            process.stdout.close()
        if job is not None:
            job.close()
        return {"status": "execution_failed", "diagnostic": f"Could not start selected simulator: {error}",
                "output": b"", "returncode": None, "seconds": time.monotonic() - started}

    def capture():
        while True:
            chunk = process.stdout.read(65536)
            if not chunk:
                return
            remaining = max_output_bytes - len(output)
            output.extend(chunk[:max(0, remaining)])
            if len(chunk) > remaining:
                overflow.set()
                return

    reader = threading.Thread(target=capture, daemon=True, name="etlayout-circuit-output")
    reader.start()
    descendants = {}
    try:
        managed = psutil.Process(process.pid)
    except psutil.NoSuchProcess:
        managed = None
    status = None
    try:
        while process.poll() is None:
            checkpoint(cancel)
            if managed is not None:
                try:
                    for child in managed.children(recursive=True):
                        descendants[child.pid] = child
                except psutil.Error:
                    pass
            if overflow.is_set():
                status = "output_limit"
                break
            if time.monotonic() - started > timeout:
                status = "timeout"
                break
            time.sleep(.02)
    finally:
        _stop_process(process, descendants)
        if job is not None:
            job.close()  # Also kills children of a launcher that exited between polls.
        reader.join(timeout=3.)
        process.stdout.close()
    status = status or ("output_limit" if overflow.is_set() else
                        "completed" if process.returncode == 0 else "execution_failed")
    return {"status": status, "returncode": process.returncode, "seconds": time.monotonic() - started,
            "output": bytes(output)}


def _compare_currents(output, expected, port_ids):
    if output.count(_BEGIN) != 1 or output.count(_END) != 1:
        raise SimulationError("Simulator did not produce one complete generated current block; select the console/batch executable (ngspice_con.exe on Windows)")
    if output.index(_BEGIN) >= output.index(_END):
        raise SimulationError("Simulator current-block markers are out of order")
    block = output.split(_BEGIN, 1)[1].split(_END, 1)[0]
    actual = {}
    for line in block.splitlines():
        if not line.strip():
            continue
        match = _CURRENT.fullmatch(line.strip())
        if match is None:
            raise SimulationError("Simulator returned an unrecognized current record")
        index = int(match[1]) - 1
        value = -float(match[2])
        if index in actual or not 0 <= index < len(expected) or not math.isfinite(value):
            raise SimulationError("Simulator current records are duplicate, out of range or nonfinite")
        actual[index] = value
    if len(actual) != len(expected):
        raise SimulationError("Simulator omitted one or more physical port currents")
    currents = []
    for index, target in enumerate(expected):
        target = float(target)
        measured = actual[index]
        absolute = abs(measured - target)
        relative = absolute / abs(target) if target else (0. if measured == 0 else None)
        if relative is not None and not math.isfinite(relative):
            relative = None
        passed = relative is not None and relative <= CURRENT_RTOL
        currents.append({"port_id": port_ids[index], "expected_A": target, "actual_A": measured,
                         "absolute_error_A": absolute if math.isfinite(absolute) else None,
                         "relative_error": relative, "passed": passed})
    return currents


def validate_circuit_network(report, directory, *, executable, timeout=30., max_output_bytes=4 * 1024 ** 2,
                             cancel=None, progress=None, execution=None):
    """Generate and run bounded DC checks with an explicitly chosen ngspice.

    Timeout and output limits apply to the complete check, across all ports.
    Execution is serial and native pools are capped at one thread. This stays
    within the existing machine-local total CPU budget; it does not start a new
    study pool. Imported bundle/netlist files are never accepted as executable
    input. Cancellation propagates through the standard job cancellation path.
    """
    selected = validate_execution(execution)
    executable = Path(executable).expanduser()
    if not executable.is_absolute() or not executable.is_file():
        raise SimulationError("Select an existing absolute ngspice executable path")
    if os.name == "nt" and executable.suffix.lower() != ".exe":
        raise SimulationError("Select the ngspice .exe directly, rather than a shell script")
    if not isinstance(timeout, (int, float)) or isinstance(timeout, bool) or not math.isfinite(timeout) or not 0 < timeout <= 1800:
        raise SimulationError("Circuit timeout must be finite, positive and no larger than 1800 seconds for the complete check")
    if isinstance(max_output_bytes, bool) or not isinstance(max_output_bytes, int) or not 1024 <= max_output_bytes <= 16 * 1024 ** 2:
        raise SimulationError("Circuit output limit must be an integer from 1024 to 16777216 bytes for the complete check")
    checkpoint(cancel)
    manifest = prepare_circuit_validation(report, directory, cancel=cancel)
    directory = Path(directory).expanduser().absolute()
    result = {"schema_version": 1, "status": "running", "simulator": executable.name,
              "executable_sha256": _file_sha256(executable, cancel),
              "manifest_sha256": hashlib.sha256((directory / "manifest.json").read_bytes()).hexdigest(),
              "execution": {"cpu_budget": selected.cpu_threads, "simultaneous_simulator_jobs": 1, "native_thread_limit_requested": 1},
              "relative_tolerance": CURRENT_RTOL, "absolute_tolerance_A": 0., "cases": [],
              "provenance": manifest["provenance"], "limitations": manifest["limitations"]}
    _, files = _bundle_contents(report)
    started = time.monotonic()
    remaining_output = max_output_bytes
    result.update(timeout_seconds=timeout, max_output_bytes=max_output_bytes)
    try:
        # Run an isolated regenerated copy. Edits to the exported bundle can
        # never add a statement or init file to the executed decks.
        with tempfile.TemporaryDirectory(prefix=".etlayout-circuit-run-", dir=directory.parent) as temporary:
            working = Path(temporary)
            for name, data in files.items():
                (working / name).write_bytes(data)
            for index, case in enumerate(manifest["cases"]):
                checkpoint(cancel, progress, index / len(manifest["cases"]),
                           f"Checking circuit port {index + 1} of {len(manifest['cases'])}")
                remaining_time = timeout - (time.monotonic() - started)
                if remaining_time <= 0 or remaining_output <= 0:
                    result["status"] = "timeout" if remaining_time <= 0 else "output_limit"
                    break
                observed = _execute_deck(executable, working, case["deck"], timeout=remaining_time,
                                         max_output_bytes=remaining_output, cancel=cancel)
                output = observed.pop("output")
                remaining_output -= len(output)
                log_name = f"basis-{index + 1:03d}.log"
                (directory / log_name).write_bytes(output)
                observed.update(basis_index=index, excited_port=case["excited_port"], log=log_name,
                                output_sha256=hashlib.sha256(output).hexdigest())
                if observed["status"] == "completed":
                    try:
                        observed["currents"] = _compare_currents(output.decode("utf-8", errors="replace"), case["expected_entering_currents_A"],
                                                                  manifest["port_ids"])
                    except (SimulationError, ValueError, OverflowError) as error:
                        observed.update(status="failed", diagnostic=str(error))
                    else:
                        observed["status"] = "passed" if all(row["passed"] for row in observed["currents"]) else "failed"
                result["cases"].append(observed)
                if observed["status"] != "passed":
                    result["status"] = observed["status"]
                    break
            else:
                result["status"] = "passed"
            checkpoint(cancel)
    except Cancelled:
        result["status"] = "cancelled"
        (directory / "validation-result.json").write_bytes(_json_bytes(result))
        raise
    result["expected_cases"] = len(manifest["cases"])
    result["passed_cases"] = sum(row["status"] == "passed" for row in result["cases"])
    result["compared_currents"] = sum(len(row.get("currents", [])) for row in result["cases"])
    errors = [entry["relative_error"] for case in result["cases"] for entry in case.get("currents", [])
              if entry["relative_error"] is not None]
    result["maximum_relative_error"] = max(errors, default=None)
    (directory / "validation-result.json").write_bytes(_json_bytes(result))
    if progress:
        progress(1., "Circuit validation complete")
    return result
