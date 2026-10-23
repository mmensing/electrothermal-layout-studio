# SPDX-License-Identifier: MPL-2.0
"""Owned, bounded external field/circuit tool execution; no shell or credentials."""
from __future__ import annotations

import math
import os
import signal
import subprocess
import threading
import time
from pathlib import Path

import psutil

from etlayout.models import SimulationError, checkpoint


def _environment(executable, working, native_threads):
    # Do not leak credentials, PYTHONPATH, startup scripts or numerical pool
    # preferences from the application into the external simulator.
    environment = {key: os.environ[key] for key in ("SystemRoot", "WINDIR", "LANG", "LC_ALL")
                   if key in os.environ}
    system = Path(os.environ.get("SystemRoot", "/usr"))
    environment.update(PATH=os.pathsep.join(map(str, (executable.parent, system / "System32", system))),
                       HOME=str(working), USERPROFILE=str(working), XDG_CONFIG_HOME=str(working),
                       TEMP=str(working), TMP=str(working), OMP_NUM_THREADS=str(native_threads), OPENBLAS_NUM_THREADS=str(native_threads),
                       MKL_NUM_THREADS=str(native_threads), BLIS_NUM_THREADS=str(native_threads), VECLIB_MAXIMUM_THREADS=str(native_threads))
    return environment


class _WindowsJob:
    """Own all simulator descendants before its first instruction can execute."""

    def __init__(self, memory_limit_bytes=None):
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
            "QueryInformationJobObject": ([wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD, ctypes.c_void_p], wintypes.BOOL),
            "CloseHandle": ([wintypes.HANDLE], wintypes.BOOL),
        }
        for name, (arguments, result) in signatures.items():
            function = getattr(self.kernel, name)
            function.argtypes, function.restype = arguments, result
        self.handle = self.kernel.CreateJobObjectW(None, None)
        if not self.handle:
            raise ctypes.WinError(ctypes.get_last_error())
        self.ExtendedLimits = ExtendedLimits
        limits = ExtendedLimits()
        limits.basic.flags = 0x2000  # JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
        if memory_limit_bytes is not None:
            limits.basic.flags |= 0x200  # JOB_OBJECT_LIMIT_JOB_MEMORY: commit bytes for the owned group.
            limits.job_memory = memory_limit_bytes
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

    def peak_commit_bytes(self):
        limits = self.ExtendedLimits()
        if self.handle and self.kernel.QueryInformationJobObject(self.handle, 9, self.ctypes.byref(limits), self.ctypes.sizeof(limits), None):
            return int(limits.peak_job_memory)
        return None

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


def run_external_tool(executable, args, working, *, timeout=30., max_output_bytes=4 * 1024**2,
                      cancel=None, native_threads=1, memory_limit_bytes=None):
    """Run one explicitly selected trusted tool with bounded, owned execution.

    Callers generate controlled input files/arguments; imported scripts or shell
    commands are not accepted as part of a project. Output is bounded raw data.
    """
    executable, working = Path(executable), Path(working)
    if not executable.is_absolute() or not executable.is_file():
        raise SimulationError("Select an existing absolute external-tool executable path")
    if os.name == "nt" and executable.suffix.lower() != ".exe":
        raise SimulationError("Select the external .exe directly, not a shell script")
    if not working.is_dir() or working.is_symlink() or working.is_junction():
        raise SimulationError("External-tool working directory must be an existing owned directory")
    if not isinstance(args, (list, tuple)) or any(not isinstance(item, str) or "\0" in item for item in args):
        raise SimulationError("External-tool arguments must be a controlled list of strings")
    if isinstance(timeout, bool) or not isinstance(timeout, (int, float)) or not math.isfinite(timeout) or not 0 < timeout <= 1800:
        raise SimulationError("External-tool timeout must be finite and between 0 and 1800 seconds")
    if isinstance(max_output_bytes, bool) or not isinstance(max_output_bytes, int) or not 1024 <= max_output_bytes <= 16 * 1024**2:
        raise SimulationError("External-tool output budget must be between 1024 and 16777216 bytes")
    from etlayout.simulation.execution import current_execution
    if isinstance(native_threads, bool) or not isinstance(native_threads, int) or not 1 <= native_threads <= current_execution().cpu_threads:
        raise SimulationError("External-tool thread request must fit the current CPU budget")
    if memory_limit_bytes is not None and (isinstance(memory_limit_bytes, bool) or not isinstance(memory_limit_bytes, int)
            or not 16 * 1024**2 <= memory_limit_bytes <= 16 * 1024**3):
        raise SimulationError("External-tool memory budget must be 16 MiB to 16 GiB")
    checkpoint(cancel)
    output = bytearray()
    overflow = threading.Event()
    started = time.monotonic()
    command = [str(executable), *args]
    flags = (subprocess.CREATE_NO_WINDOW | 0x00000004) if os.name == "nt" else 0  # CREATE_SUSPENDED
    job = None
    process = None
    try:
        if os.name == "nt":
            job = _WindowsJob(memory_limit_bytes)
        process = subprocess.Popen(command, cwd=working, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                                   stderr=subprocess.STDOUT, shell=False, env=_environment(executable, working, native_threads),
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

    reader = threading.Thread(target=capture, daemon=True, name="etlayout-external-output")
    reader.start()
    descendants = {}
    try:
        managed = psutil.Process(process.pid)
    except psutil.NoSuchProcess:
        managed = None
    status = None
    peak_rss = 0
    peak_commit = None
    try:
        while process.poll() is None:
            checkpoint(cancel)
            if managed is not None:
                try:
                    for child in managed.children(recursive=True):
                        descendants[child.pid] = child
                    resident = managed.memory_info().rss
                    for child in descendants.values():
                        try:
                            resident += child.memory_info().rss
                        except psutil.Error:
                            pass
                    peak_rss = max(peak_rss, resident)
                    if memory_limit_bytes is not None and resident > memory_limit_bytes:
                        status = "memory_limit"
                        break
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
            peak_commit = job.peak_commit_bytes()
            job.close()  # Also kills children of a launcher that exited between polls.
        reader.join(timeout=3.)
        process.stdout.close()
    status = status or ("output_limit" if overflow.is_set() else
                        "completed" if process.returncode == 0 else "execution_failed")
    return {"status": status, "returncode": process.returncode, "seconds": time.monotonic() - started,
            "output": bytes(output), "peak_rss_sampled_bytes": peak_rss,
            "peak_job_commit_bytes": peak_commit, "memory_limit_bytes": memory_limit_bytes,
            "memory_limit_policy": "Windows job commit cap and sampled RSS" if os.name == "nt" else "sampled process-group RSS"}
