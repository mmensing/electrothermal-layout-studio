# SPDX-License-Identifier: MPL-2.0
"""Study dispatch, saved-source contracts and comparable transient durations."""
import multiprocessing
import os
from copy import deepcopy

import numpy as np
import pytest

from etlayout.examples import make_example
from etlayout.io.project import project_from_dict
from etlayout.models import Cancelled, SimulationError, SweepSpec
from etlayout.simulation.analysis import sample_line
from etlayout.simulation.execution import ExecutionSettings
from etlayout.simulation.runner import run_project
from etlayout.simulation.sweep import (
    compare_projects,
    mesh_refinement_study,
    resolve_study_initial_state,
    run_sweep,
    time_step_convergence_study,
)


@pytest.mark.skipif((os.cpu_count() or 1) < 2, reason="Parallel study requires two logical CPUs")
@pytest.mark.parametrize("kind", ["sweep", "compare", "mesh", "time"])
def test_real_spawn_studies_match_ordered_serial_results(tmp_path, kind):
    project = make_example("E17" if kind == "time" else "E1", tmp_path / kind)
    if kind == "time":
        project.transient.stop_time = 300e-6
        project.transient.output_interval = 100e-6
    def study(execution):
        if kind == "sweep":
            return run_sweep(project, SweepSpec("layers.metal.thickness", [2e-6, -1e-6, 4e-6], "electrical"), execution=execution)
        if kind == "compare":
            second = deepcopy(project)
            second.terminals[-1].value *= 2
            return compare_projects([project, second], mode="electrical", execution=execution)
        if kind == "mesh":
            return mesh_refinement_study(project, [14e-6, 7e-6], execution=execution)
        return time_step_convergence_study(project, [50e-6, 25e-6], execution=execution)
    serial = study(ExecutionSettings(cpu_threads=2, study_workers=1))
    parallel = study(ExecutionSettings(cpu_threads=2, study_workers=2))
    assert len(serial) == len(parallel)
    for one, two in zip(serial, parallel):
        assert getattr(one, "value", one.name if hasattr(one, "name") else None) == getattr(two, "value", two.name if hasattr(two, "name") else None)
        assert one.error == two.error
        if one.result is not None:
            assert two.result.converged
            assert one.result.physics_hash == two.result.physics_hash
            assert two.metrics["study_execution"]["process_id"] != os.getpid()
            assert two.metrics["study_execution"]["study_workers"] == 2
            assert two.metrics["study_execution"]["cpu_threads_per_worker"] == 1
            # Gmsh may choose different triangulations with different native
            # thread caps. Compare physics at common coordinates, not indices.
            layer = next(iter(one.result.fields))
            field = "potential" if "potential" in one.result.fields[layer] else "temperature"
            first = sample_line(one.result, layer, field, [2e-6, 2e-6], [98e-6, 2e-6], 51)
            second = sample_line(two.result, layer, field, [2e-6, 2e-6], [98e-6, 2e-6], 51)
            np.testing.assert_allclose(first["value"], second["value"], rtol=2e-8, atol=1e-9)
            for metric in ("electrical_power", "effective_resistance", "temperature_max", "transient_input_energy"):
                value = one.metrics.get(metric)
                if value is not None:
                    assert two.metrics[metric] == pytest.approx(value, rel=2e-8, abs=1e-15)
    if kind == "sweep":
        assert parallel[1].result is None and parallel[1].error


@pytest.mark.skipif((os.cpu_count() or 1) < 2, reason="Parallel study requires two logical CPUs")
def test_cancel_parallel_study_joins_owned_children(tmp_path):
    project = make_example("E1", tmp_path)
    initial_children = {p.pid for p in multiprocessing.active_children()}
    ticks = []
    def progress(fraction, message):
        if message.startswith("Study:"):
            ticks.append(message)
    with pytest.raises(Cancelled):
        run_sweep(project, SweepSpec("terminals.drain.value", [.01] * 20, "electrical"),
                  execution=ExecutionSettings(cpu_threads=2, study_workers=2), progress=progress,
                  cancel=lambda: len(ticks) >= 2)
    assert len(ticks) == 2
    assert {p.pid for p in multiprocessing.active_children()} <= initial_children


def test_saved_start_studies_resolve_replay_and_reject_incompatible_mesh(tmp_path):
    project = make_example("E17", tmp_path)
    project.transient.stop_time = 300e-6
    source = run_project(project)
    warm = run_project(project, initial_result=source)
    saved = project_from_dict(warm.configuration)
    options = resolve_study_initial_state(saved, warm)
    assert options["initial_result"] is warm.transient.snapshots[0]
    assert not options["resume"]
    spec = SweepSpec("transient.drives.0.waveform.high", [5e5, 1e6], "thermal")
    for rows in (run_sweep(saved, spec, **options),
                 compare_projects([saved], "thermal", initial_results=[options["initial_result"]]),
                 mesh_refinement_study(saved, [saved.mesh.max_size], **options)):
        assert all(row.result is not None and row.result.converged and not row.error for row in rows), [row.error for row in rows]
    with pytest.raises(SimulationError, match="saved initial thermal state"):
        run_sweep(saved, spec)
    with pytest.raises(SimulationError, match="identical mesh"):
        mesh_refinement_study(saved, [saved.mesh.max_size / 2], **options)
    with pytest.raises(SimulationError, match="digest"):
        resolve_study_initial_state(saved, initial_result=warm)
    changed = deepcopy(saved)
    changed.materials["copper"].density *= 2
    with pytest.raises(SimulationError, match="incompatible materials"):
        run_sweep(changed, spec, **options)


def test_periodic_mesh_study_keeps_fixed_duration_and_energy(tmp_path):
    project = make_example("E17", tmp_path)
    project.transient.stop_time = 3.5e-3
    project.transient.time_step = 100e-6
    project.transient.output_interval = 100e-6
    project.transient.periodic_tolerance = 1e6
    rows = mesh_refinement_study(project, [20e-6, 10e-6])
    assert all(row.result.converged for row in rows)
    assert [row.metrics["transient_time"] for row in rows] == [3.5e-3, 3.5e-3]
    assert all(row.metrics["transient_stop_reason"] == "stop_time" for row in rows)
    assert rows[1].metrics["transient_input_energy_relative_change"] < 1e-12
    assert project.transient.periodic_tolerance == 1e6
