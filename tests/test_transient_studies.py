# SPDX-License-Identifier: MPL-2.0
from copy import deepcopy

import pytest

from etlayout.examples import make_example
from etlayout.models import SimulationError, SimulationResult, SweepSpec
from etlayout.simulation.execution import ExecutionSettings
from etlayout.simulation.sweep import get_parameter, run_sweep, set_parameter


def test_waveform_scalar_sweep_retains_enabled_operation_and_changes_actual_drive(tmp_path, monkeypatch):
    project = make_example("E18", tmp_path)
    original = deepcopy(project.to_dict())
    applied = []

    def solve(candidate, **kwargs):
        applied.append((candidate.transient.enabled, candidate.transient.drives[0].waveform.high))
        return SimulationResult(mesh=None, converged=True)

    monkeypatch.setattr("etlayout.simulation.sweep.run_project", solve)
    rows = run_sweep(project, project.sweeps[0], execution=ExecutionSettings(study_workers=1))
    assert applied == [(True, .05), (True, .15), (True, .25)]
    assert all(row.result.converged for row in rows)
    assert project.to_dict() == original
    assert get_parameter(project, "transient.drives.0.waveform.high") == .25


def test_sweeping_overridden_base_terminal_is_rejected(tmp_path):
    project = make_example("E18", tmp_path)
    with pytest.raises(SimulationError, match="waveform overrides.*waveform.high"):
        run_sweep(project, SweepSpec("terminals.drain.value", [.1, .2], "electrothermal"))


def test_transient_sweep_target_stays_typed_and_bounded(tmp_path):
    project = make_example("E17", tmp_path)
    with pytest.raises(SimulationError, match="outside"):
        set_parameter(project, "transient.drives.2.waveform.high", 1.)
    with pytest.raises(SimulationError, match="whole number"):
        set_parameter(project, "transient.max_steps", 2.5)
    with pytest.raises(SimulationError, match="Boolean"):
        set_parameter(project, "transient.enabled", "true")
