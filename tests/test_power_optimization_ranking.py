# SPDX-License-Identifier: MPL-2.0
"""Comparable-mesh rankings from real mesh/solve/refinement evidence."""
from copy import deepcopy

import pytest
from test_power_optimization import example

from etlayout.io.resources import ExternalResourcePolicy
from etlayout.models import MeasurementSpec, ObjectiveSpec, SimulationError, SupportSelector
from etlayout.optimization import _update_pareto, export_finalist, ranking_rows, run_experiment


@pytest.fixture(scope="module")
def limited_finalists(tmp_path_factory):
    directory = tmp_path_factory.mktemp("ranked-resolution")
    project = example(directory)
    experiment = project.power.experiments[0]
    experiment.scenarios = experiment.scenarios[:1]
    experiment.variables[0].values = [3e-6, 4e-6]
    experiment.finalists = 1
    # At constant 300 K this integral is exactly 300 times routing area,
    # creating a real area/loss trade-off and leaving one frontier row coarse.
    project.power.measurements.append(MeasurementSpec("area_proxy", "temperature",
        SupportSelector(layers=["metal"]), "integral"))
    experiment.objectives.append(ObjectiveSpec("area_proxy"))
    history = run_experiment(project, "width", directory / "search")
    assert history["status"] == "complete", history
    return project, history


def test_truncated_finalist_budget_keeps_coarse_and_refined_frontiers_separate(limited_finalists):
    _project, history = limited_finalists
    coarse, refined = ranking_rows(history, "coarse"), ranking_rows(history, "refined")
    assert len(coarse) == 3 and len(refined) == 2
    assert len(history["coarse_pareto"]) == 3
    assert history["pareto_resolution"] == "refined"
    assert history["pareto"] == history["refined_pareto"]
    refined_ids = {row["id"] for row in refined}
    assert set(history["pareto"]) <= refined_ids
    assert len(set(history["coarse_pareto"]) - refined_ids) == 1
    assert ranking_rows(history) == refined
    for row in coarse:
        width = row["parameters"].get("width", 2e-6)
        assert row["objectives"]["area_proxy"] == pytest.approx(300. * 10e-6 * width)
    for row in refined:
        assert row["objectives"] == row["verification"]["levels"][-1]["objectives"]
        assert row["objective_resolution"] == "refined"


def test_failed_or_partial_fine_evidence_cannot_be_ranked_as_refined(limited_finalists):
    project, original = limited_finalists
    history = deepcopy(original)
    candidate = next(row for row in history["rows"] if not row["baseline"] and row.get("verification"))
    candidate["verification"]["levels"][-1]["status"] = "constraint_failure"
    _update_pareto(history, project.power.experiments[0].objectives)
    assert candidate["id"] not in history["refined_pareto"]
    assert candidate["id"] in history["coarse_pareto"]
    assert history["pareto"] == ["baseline"]
    baseline = next(row for row in history["rows"] if row["baseline"])
    baseline["verification"]["levels"].pop()
    _update_pareto(history, project.power.experiments[0].objectives)
    assert not history["refined_pareto"]
    assert history["pareto_resolution"] == "coarse"
    assert history["pareto"] == history["coarse_pareto"]
    assert len(history["rows"]) == 3
    assert len(ranking_rows(original, "refined")) == 2
    with pytest.raises(SimulationError, match="resolution"):
        ranking_rows(history, "mixed")


def test_ranking_views_do_not_overwrite_recorded_objectives(limited_finalists):
    _project, history = limited_finalists
    original = deepcopy(history)
    view = ranking_rows(history, "coarse")
    view[0]["objectives"]["loss"] = -1.
    assert history == original


def test_failed_export_reevaluation_removes_obsolete_refined_frontier(tmp_path):
    project = example(tmp_path)
    experiment = project.power.experiments[0]
    experiment.scenarios = experiment.scenarios[:1]
    experiment.variables[0].values = [4e-6]
    experiment.max_evaluations = 2
    history = run_experiment(project, "width", tmp_path / "search")
    candidate_id = history["published"][0]
    candidate = next(row for row in history["rows"] if row["id"] == candidate_id)
    assert candidate_id in history["refined_pareto"]
    # Withdraw the evidence of positive baseline improvement. The actual two
    # exported layouts are still independently solved; neither can establish
    # strictly positive gain beyond its export difference with this evidence.
    comparison = candidate["verification"]["baseline_comparison"]["loss"]
    comparison["gain"] = comparison["required_gain"] = 0.
    report = export_finalist(project, history, candidate_id, tmp_path / "recheck",
                             resource_policy=ExternalResourcePolicy(allowed_root=tmp_path / "search"))
    assert report["status"] == "verification_failed"
    assert all(record["scenarios"][0]["converged"] for record in report["formats"].values())
    assert candidate["export_status"] == "verification_failed"
    assert candidate_id not in history["refined_pareto"]
    assert candidate_id not in history["pareto"]
    assert candidate_id not in history["verified"]
    assert candidate_id not in history["published"]
    assert candidate_id in history["coarse_pareto"]


def test_two_real_finalists_report_overlapping_mesh_ranking_uncertainty(tmp_path):
    project = example(tmp_path)
    experiment = project.power.experiments[0]
    experiment.scenarios = experiment.scenarios[:1]
    experiment.variables[0].values = [3.998e-6, 4e-6]
    experiment.finalists = 2
    # At fixed current, lower loss and larger voltage headroom are competing
    # criteria, so both nearly equal routing widths reach finalist verification.
    project.power.measurements.append(MeasurementSpec("drive_voltage", "potential",
        SupportSelector(layers=["metal"]), "maximum"))
    experiment.objectives.append(ObjectiveSpec("drive_voltage", direction="max"))
    history = run_experiment(project, "width", tmp_path / "search")
    assert history["status"] == "complete", history
    assert len(history["ranking_comparisons"]) == 1
    comparison = history["ranking_comparisons"][0]
    rows = {row["id"]: row for row in history["rows"]}
    first, second = rows[comparison["first"]], rows[comparison["second"]]
    reversed_order, overlaps = False, True
    for objective in experiment.objectives:
        key = objective.measurement_id
        coarse_delta = first["coarse_objectives"][key] - second["coarse_objectives"][key]
        fine_delta = first["objectives"][key] - second["objectives"][key]
        uncertainty = first["verification"]["absolute_changes"][key] + second["verification"]["absolute_changes"][key]
        reversed_order |= coarse_delta * fine_delta < 0
        overlaps &= abs(fine_delta) <= uncertainty
    assert comparison["ranking_reversed"] == reversed_order
    assert comparison["mesh_intervals_overlap"] == overlaps
    assert reversed_order or overlaps, comparison
    assert comparison["status"] == "uncertain"
    assert not history["published"]
