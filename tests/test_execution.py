# SPDX-License-Identifier: MPL-2.0
"""Actual CPU pool caps, context restoration and per-run execution provenance."""
import json
import pickle
import sys

import numpy as np
import pytest
from scipy.sparse import csr_matrix
from threadpoolctl import threadpool_info

from etlayout.models import SimulationError
from etlayout.simulation import execution
from etlayout.simulation.execution import (
    ExecutionSettings,
    current_execution,
    detect_capabilities,
    execution_context,
    execution_provenance,
    validate_execution,
)
from etlayout.solvers.fem import linear_solve


def test_cpu_settings_defaults_follow_available_cpu_budget_and_serialize(monkeypatch):
    monkeypatch.setattr(execution, "_cpu_count", lambda: 16)
    settings = ExecutionSettings()
    assert settings.cpu_threads == settings.study_workers == 4
    assert ExecutionSettings.from_dict(json.loads(json.dumps(settings.to_dict()))) == settings
    assert pickle.loads(pickle.dumps(settings)) == settings
    monkeypatch.setattr(execution, "_cpu_count", lambda: 2)
    assert ExecutionSettings() == ExecutionSettings(2, 2)
    assert detect_capabilities() == {"cpu_count": 2, "threadpoolctl_available": True}


@pytest.mark.parametrize("settings,message", [
    (ExecutionSettings(0, 1), "cpu_threads"),
    (ExecutionSettings(1, 0), "study_workers"),
    (ExecutionSettings(True, 1), "cpu_threads"),
    (ExecutionSettings(2., 1), "cpu_threads"),
    (ExecutionSettings(2, 3), "total CPU budget"),
    (ExecutionSettings(17, 1), "16 available"),
    ({"gpu_device": 0}, "Unknown execution"),
    ({"backend": "cuda"}, "Unknown execution"),
    ("cpu", "Expected ExecutionSettings"),
])
def test_cpu_settings_reject_invalid_or_oversubscribed_values(monkeypatch, settings, message):
    monkeypatch.setattr(execution, "_cpu_count", lambda: 16)
    with pytest.raises(SimulationError, match=message):
        validate_execution(settings)


def _solve():
    return linear_solve(csr_matrix([[2., -1.], [-1., 2.]]), np.array([1., 0.]), {})


def test_native_limits_apply_restore_and_record_actual_solves():
    before = {pool["prefix"]: pool["num_threads"] for pool in threadpool_info()}
    with execution_context(ExecutionSettings(1, 1)):
        assert current_execution() == validate_execution(None) == ExecutionSettings(1, 1)
        assert execution_provenance()["actual_backend"] == "not_run"
        assert _solve() == pytest.approx([2 / 3, 1 / 3])
        evidence = execution_provenance()
        assert evidence["actual_backend"] == "cpu"
        assert evidence["linear_solves"] == {"cpu": 1}
        assert evidence["effective_cpu_threads"] == 1
        assert evidence["native_threadpools"]
        assert all(pool["num_threads"] == 1 for pool in evidence["native_threadpools"])
        json.dumps(evidence)
    assert execution_provenance()["linear_solves"] == {"cpu": 0}
    after = {pool["prefix"]: pool["num_threads"] for pool in threadpool_info()}
    assert all(after[key] == value for key, value in before.items())


def test_nested_context_shares_or_resets_counters_and_restores_after_exception():
    with execution_context(ExecutionSettings(1, 1)):
        _solve()
        with execution_context():
            _solve()
        assert execution_provenance()["linear_solves"] == {"cpu": 2}
        with pytest.raises(RuntimeError, match="cancel"):
            with execution_context(reset_counts=True):
                assert execution_provenance()["linear_solves"] == {"cpu": 0}
                _solve()
                assert execution_provenance()["linear_solves"] == {"cpu": 1}
                raise RuntimeError("cancel")
        assert execution_provenance()["linear_solves"] == {"cpu": 2}
    assert execution_provenance()["actual_backend"] == "not_run"


def test_changed_nested_limits_restore_after_error():
    if detect_capabilities()["cpu_count"] < 2:
        pytest.skip("Two logical CPUs are required to exercise differing pool limits")
    with execution_context(ExecutionSettings(2, 1)):
        _solve()
        with pytest.raises(RuntimeError):
            with execution_context(ExecutionSettings(1, 1)):
                assert all(pool["num_threads"] == 1 for pool in threadpool_info())
                raise RuntimeError("cancel")
        assert current_execution().cpu_threads == 2
        assert all(pool["num_threads"] == 2 for pool in threadpool_info())
        assert execution_provenance()["linear_solves"] == {"cpu": 1}


def test_failed_and_fully_constrained_solves_are_not_counted():
    with execution_context(ExecutionSettings(1, 1)):
        result = linear_solve(csr_matrix([[1.]]), np.array([0.]), {0: 300.})
        assert result == pytest.approx([300.])
        with pytest.raises(SimulationError):
            linear_solve(csr_matrix([[0.]]), np.array([1.]), {})
        assert execution_provenance()["linear_solves"] == {"cpu": 0}


def test_missing_thread_control_fails_honestly(monkeypatch):
    monkeypatch.setitem(sys.modules, "threadpoolctl", None)
    assert not detect_capabilities()["threadpoolctl_available"]
    with pytest.raises(SimulationError, match="requires threadpoolctl"):
        with execution_context(ExecutionSettings(1, 1)):
            pytest.fail("An unavailable execution limiter must not be silently ignored")
