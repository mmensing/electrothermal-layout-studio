# SPDX-License-Identifier: MPL-2.0
"""Machine-local execution policy and bounded native CPU thread pools.

Project physics never include these preferences. Native BLAS/OpenMP limits are
process-wide, so contexts in separate Python threads are serialized; independent
study points use processes and divide the total CPU budget among their workers.
"""
from __future__ import annotations

import importlib
import os
import threading
import time
from concurrent.futures import ThreadPoolExecutor, wait
from contextlib import contextmanager
from contextvars import ContextVar, copy_context
from dataclasses import asdict, dataclass, field
from typing import Any, Mapping

from etlayout.models import SimulationError


def _cpu_count() -> int:
    """Respect process affinity where available, falling back to logical CPUs."""
    try:
        import psutil
    except ImportError:
        return max(1, os.cpu_count() or 1)
    try:
        return max(1, len(psutil.Process().cpu_affinity()))
    except (AttributeError, OSError, psutil.Error):
        return max(1, os.cpu_count() or 1)


@dataclass(frozen=True)
class ExecutionSettings:
    """Total CPU budget and maximum independent study processes, not per-worker CPUs."""
    cpu_threads: int = field(default_factory=lambda: min(4, _cpu_count()))
    study_workers: int = field(default_factory=lambda: min(4, _cpu_count()))

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, mapping: Mapping[str, Any]) -> ExecutionSettings:
        if not isinstance(mapping, Mapping):
            raise SimulationError("Execution settings must be a mapping")
        unknown = set(mapping) - set(cls.__dataclass_fields__)
        if unknown:
            raise SimulationError(f"Unknown execution settings: {', '.join(sorted(map(str, unknown)))}")
        return validate_execution(cls(**mapping))


@dataclass
class _ExecutionState:
    settings: ExecutionSettings
    linear_solves: dict[str, int] = field(default_factory=lambda: {"cpu": 0})
    linear_factorizations: int = 0
    reused_factorizations: int = 0
    assembly_batches: int = 0
    assembly_tasks: int = 0
    assembly_cpu_seconds: float = 0.
    assembly_wall_seconds: float = 0.
    assembly_thread_ids: set[int] = field(default_factory=set)
    timings: dict[str, float] = field(default_factory=dict)
    executor: ThreadPoolExecutor | None = None

    def close(self):
        if self.executor is not None:
            self.executor.shutdown(wait=True, cancel_futures=True)


_ACTIVE: ContextVar[_ExecutionState | None] = ContextVar("etlayout_execution", default=None)
_ASSEMBLY_WORKER: ContextVar[bool] = ContextVar("etlayout_assembly_worker", default=False)
_NATIVE_LOCK = threading.RLock()

# Small matrices are faster without dispatch and native-pool reconfiguration.
PARALLEL_MIN_WORK = 50_000


def current_execution() -> ExecutionSettings:
    state = _ACTIVE.get()
    return state.settings if state else ExecutionSettings()


def detect_capabilities() -> dict[str, Any]:
    """Report the available CPU budget and native thread-control support."""
    capabilities: dict[str, Any] = {
        "cpu_count": _cpu_count(), "threadpoolctl_available": False,
    }
    try:
        importlib.import_module("threadpoolctl")
        capabilities["threadpoolctl_available"] = True
    except ImportError:
        pass
    return capabilities


def validate_execution(settings: ExecutionSettings | Mapping[str, Any] | None = None) -> ExecutionSettings:
    settings = current_execution() if settings is None else settings
    if isinstance(settings, Mapping):
        return ExecutionSettings.from_dict(settings)
    if not isinstance(settings, ExecutionSettings):
        raise SimulationError("Expected ExecutionSettings or a settings mapping")
    for key in ("cpu_threads", "study_workers"):
        value = getattr(settings, key)
        if isinstance(value, bool) or not isinstance(value, int) or value < 1:
            raise SimulationError(f"Execution {key} must be an integer >= 1")
    available = _cpu_count()
    if settings.cpu_threads > available:
        raise SimulationError(f"CPU budget exceeds {available} available logical CPUs")
    if settings.study_workers > settings.cpu_threads:
        raise SimulationError("Study workers cannot exceed the total CPU budget")
    return settings


@contextmanager
def execution_context(settings: ExecutionSettings | Mapping[str, Any] | None = None, *,
                      reset_counts: bool = False):
    """Apply native thread limits; same-settings nesting shares counters by default.

    ``reset_counts=True`` creates isolated run counters even inside a study's
    context. Provenance must be read before exit. It describes the whole run,
    and may be copied onto retained frames as run-level execution provenance.
    """
    selected = validate_execution(settings)
    previous = _ACTIVE.get()
    if previous is not None and selected == previous.settings:
        if not reset_counts:
            yield selected
            return
        token = _ACTIVE.set(_ExecutionState(selected))
        try:
            yield selected
        finally:
            _ACTIVE.get().close()
            _ACTIVE.reset(token)
        return
    try:
        from threadpoolctl import threadpool_limits
    except ImportError as exc:
        raise SimulationError("CPU thread control requires threadpoolctl; install the project dependencies") from exc
    # Import native numerical runtimes before the controller scans loaded
    # libraries. SuperLU itself is not a threaded BLAS solver; this is a cap on
    # native pools, not a promise that one linear solve uses every selected CPU.
    importlib.import_module("numpy")
    importlib.import_module("scipy.linalg")
    importlib.import_module("scipy.sparse.linalg")
    with _NATIVE_LOCK, threadpool_limits(limits=selected.cpu_threads):
        token = _ACTIVE.set(_ExecutionState(selected))
        try:
            yield selected
        finally:
            _ACTIVE.get().close()
            _ACTIVE.reset(token)


def map_independent(function, items, *, work_units: int):
    """Compute independent FEM buffers in bounded threads, preserving input order.

    Inputs are read-only; each task owns its returned array/sparse matrix. NumPy
    and SciPy kernels release the GIL. Inner BLAS pools use one thread during the
    batch so outer assembly workers cannot multiply the configured CPU budget.
    A run owns and joins its reusable executor; nested kernels remain serial.
    """
    items = list(items)
    state = _ACTIVE.get()
    if (state is None or state.settings.cpu_threads == 1 or len(items) < 2
            or work_units < PARALLEL_MIN_WORK or _ASSEMBLY_WORKER.get()):
        return [function(item) for item in items]
    if state.executor is None:
        state.executor = ThreadPoolExecutor(max_workers=state.settings.cpu_threads,
                                            thread_name_prefix="etlayout-assembly")

    def invoke(item):
        token = _ASSEMBLY_WORKER.set(True)
        started = time.thread_time()
        try:
            value = function(item)
            return value, threading.get_native_id(), time.thread_time() - started
        finally:
            _ASSEMBLY_WORKER.reset(token)

    from threadpoolctl import threadpool_limits
    started = time.perf_counter()
    with _NATIVE_LOCK, threadpool_limits(limits=1):
        futures = [state.executor.submit(copy_context().run, invoke, item) for item in items]
        try:
            outputs = [future.result() for future in futures]
        except BaseException:
            for future in futures:
                future.cancel()
            # Running tasks must finish under the batch's native cap before a
            # caller can catch the error and mutate inputs or start another job.
            wait(futures)
            raise
    state.assembly_batches += 1
    state.assembly_tasks += len(outputs)
    state.assembly_wall_seconds += time.perf_counter() - started
    state.assembly_cpu_seconds += sum(output[2] for output in outputs)
    state.assembly_thread_ids.update(output[1] for output in outputs)
    return [output[0] for output in outputs]


def record_phase(name: str, seconds: float) -> None:
    """Accumulate measured wall time within the owning run, outside physics identity."""
    state = _ACTIVE.get()
    if state is not None:
        state.timings[name] = state.timings.get(name, 0.) + seconds


def phase_seconds(name: str) -> float:
    state = _ACTIVE.get()
    return state.timings.get(name, 0.) if state is not None else 0.


@contextmanager
def phase_timer(name: str):
    started = time.perf_counter()
    try:
        yield
    finally:
        record_phase(name, time.perf_counter() - started)


def record_linear_solve(backend: str, *, factorized: bool | None = None, seconds: float = 0.) -> None:
    state = _ACTIVE.get()
    if state is not None:
        state.linear_solves[backend] += 1
        state.linear_factorizations += factorized is True
        state.reused_factorizations += factorized is False
        record_phase("sparse_linear_solves", seconds)


def execution_provenance() -> dict[str, Any]:
    state = _ACTIVE.get()
    settings = current_execution()
    counts = dict(state.linear_solves) if state else {"cpu": 0}
    used = [backend for backend, count in counts.items() if count]
    try:
        from threadpoolctl import threadpool_info
        pools = [{key: pool.get(key) for key in
                  ("user_api", "internal_api", "prefix", "version", "num_threads")}
                 for pool in threadpool_info()]
    except ImportError:
        pools = []
    return {**settings.to_dict(), "requested_backend": "cpu",
            "actual_backend": used[0] if len(used) == 1 else ("mixed" if used else "not_run"),
            "linear_solves": counts, "native_threadpools": pools,
            "linear_factorizations": state.linear_factorizations if state else 0,
            "reused_factorizations": state.reused_factorizations if state else 0,
            "timings_seconds": dict(state.timings) if state else {},
            "timing_definition": "Measured wall time; sparse linear solves are a subset of numerical time. Cached runs record retrieval only.",
            "effective_cpu_threads": settings.cpu_threads,
            "process_id": os.getpid(), "scope": "whole_run",
            "acceleration_scope": ("parallel_fem_assembly_and_native_cpu_thread_pools"
                                   if state and state.assembly_batches else "native_cpu_thread_pools"),
            "parallel_assembly": {"batches": state.assembly_batches if state else 0,
                                  "tasks": state.assembly_tasks if state else 0,
                                  "worker_thread_ids": sorted(state.assembly_thread_ids) if state else [],
                                  "worker_cpu_seconds": state.assembly_cpu_seconds if state else 0.,
                                  "wall_seconds": state.assembly_wall_seconds if state else 0.,
                                  "linear_solver": "SciPy sparse solver; SuperLU factorization remains serial"}}
