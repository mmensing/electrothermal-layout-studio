# SPDX-License-Identifier: MPL-2.0
import numpy as np
import pytest

from etlayout.examples import make_example
from etlayout.io.export import export_profile, export_results, export_study
from etlayout.io.project import load_project
from etlayout.models import Cancelled, SimulationError, SweepSpec
from etlayout.simulation.analysis import field_difference, sample_line
from etlayout.simulation.runner import run_project
from etlayout.simulation.sweep import (
    compare_projects,
    mesh_refinement_study,
    run_sweep,
    set_parameter,
    sweep_values,
)


def test_straight_project_end_to_end_and_profile(tmp_path):
    p = make_example("E1", tmp_path / "source")
    result = run_project(p)
    assert result.converged
    assert result.metrics["effective_resistance"] == pytest.approx(.084, rel=.01)
    profile = sample_line(result, "metal", "potential", [0,5e-6], [100e-6,5e-6], 101)
    np.testing.assert_allclose(profile["value"][1:-1], np.linspace(0,.02,101)[1:-1], atol=2e-5)
    export_profile(profile, tmp_path / "profile.csv")
    path = export_results(result, tmp_path / "export", plots=False)
    assert path.exists() and (tmp_path / "export" / "metal.vtu").is_file()
    restored, rerun = load_project(tmp_path / "export" / "simulation.etl")
    assert rerun.configuration_hash == result.configuration_hash
    assert restored.name == p.name


def test_sweep_failure_rows_and_parameter_updates(tmp_path):
    p = make_example("E1", tmp_path)
    spec = SweepSpec("terminals.drain.value", [.01,.02,.04], "electrical")
    rows = run_sweep(p, spec)
    assert all(not r.error for r in rows)
    np.testing.assert_allclose([r.metrics["effective_resistance"] for r in rows], .084, rtol=.01)
    powers = [r.metrics["electrical_power"] for r in rows]
    assert powers[2] / powers[1] == pytest.approx(4., rel=1e-5)
    export_study(rows, tmp_path / "study", spec.parameter)
    assert (tmp_path / "study" / "run_002.etl").exists()
    assert p.terminals[1].value == .02  # sweeps preserve original
    bad = run_sweep(p, SweepSpec("layers.metal.thickness", [-1e-6, 2e-6]))
    assert bad[0].error and bad[1].result.converged
    with pytest.raises(SimulationError):
        set_parameter(p,"__class__.arbitrary",0)
    assert sweep_values(1.,100.,3,"log") == pytest.approx([1,10,100])


def test_comparison_difference_and_cancel(tmp_path):
    a = make_example("E1", tmp_path)
    from copy import deepcopy
    b = deepcopy(a)
    b.name = "Thicker metal"
    b.layers[0].thickness *= 2
    from etlayout.simulation.execution import ExecutionSettings
    # The low-level difference API requires an identical mesh; serial points
    # reuse that mesh, whereas independent processes may triangulate differently.
    rows = compare_projects([a,b], "electrical", execution=ExecutionSettings(study_workers=1))
    assert rows[1].metrics["effective_resistance"] == pytest.approx(rows[0].metrics["effective_resistance"] / 2, rel=1e-5)
    difference = field_difference(rows[0].result, rows[1].result, "metal", "potential")
    assert np.nanmax(np.abs(difference)) < 1e-6
    with pytest.raises(Cancelled):
        run_project(a,cancel=lambda: True)


def test_mesh_study_tracks_terminal_currents_and_spatial_profiles(tmp_path):
    p = make_example("E1", tmp_path / "source")
    cuts = [{"layer_id": "metal", "field": "potential", "start": [1e-6, 5e-6],
             "end": [99e-6, 5e-6], "count": 41}]
    rows = mesh_refinement_study(p, [1e-5, 5e-6], profiles=cuts)
    assert all(row.result.converged and not row.error for row in rows)
    final = rows[-1].metrics
    assert final["terminal_current:drain"] == pytest.approx(.02 / .084, rel=1e-6)
    assert final["terminal_current:drain_relative_change"] < 1e-6
    assert final["profile_0_common_samples"] == 41
    assert final["profile_0_relative_l2_change"] < 1e-5
    np.testing.assert_allclose(final["mesh_profiles"][0]["value"], np.linspace(.0002, .0198, 41), atol=1e-7)
    export_study(rows, tmp_path / "convergence", "mesh_max_size_m")
    assert (tmp_path / "convergence" / "profile_run_001_cut_00.csv").exists()
    assert "terminal_current:drain" in (tmp_path / "convergence" / "study.csv").read_text(encoding="utf-8-sig")
    _, reloaded = load_project(tmp_path / "convergence" / "run_001.etl")
    assert reloaded.metrics["mesh_profiles"][0]["start"] == [1e-6, 5e-6]
    with pytest.raises(SimulationError, match="finite positive"):
        mesh_refinement_study(p, [0])
    with pytest.raises(SimulationError, match="requires"):
        mesh_refinement_study(p, [1e-5], profiles=[{}])
