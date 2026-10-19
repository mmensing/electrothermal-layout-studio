# SPDX-License-Identifier: MPL-2.0
"""Named scenarios are ordinary editable projects, not alternate solver paths."""
from copy import deepcopy

import pytest

from etlayout.cli.main import main
from etlayout.example_scenarios import scenarios
from etlayout.examples import EXAMPLE_NAMES, make_example
from etlayout.io.project import load_project, physics_hash, save_project
from etlayout.simulation.runner import run_project

DEMONSTRATIONS = ("E11", "E15", "E26", "E29", "E30", "E33", "E34", "E35", "E36")


@pytest.fixture(scope="module")
def scenario_solution(tmp_path_factory):
    solved = {}
    def get(identifier, scenario):
        key = identifier, scenario
        if key not in solved:
            project = make_example(identifier, tmp_path_factory.mktemp(identifier + scenario), scenario=scenario)
            before = project.to_dict()
            result = run_project(project)
            assert project.to_dict() == before
            solved[key] = project, result
        return deepcopy(solved[key])
    return get


@pytest.mark.fast
def test_reference_scenario_preserves_all_forty_original_physical_identities(tmp_path):
    import json
    from pathlib import Path

    baseline = json.loads((Path(__file__).resolve().parents[1] / 'docs/example-science/nominal-reference.json').read_text(encoding='utf-8'))
    original_hashes = {row['name']: row['physics_hash'] for row in baseline['examples']}
    for name in EXAMPLE_NAMES:
        original = make_example(name, tmp_path / "original")
        reference = make_example(name, tmp_path / "reference", scenario="reference")
        assert physics_hash(original) == physics_hash(reference) == original_hashes[name]
        assert original.layout.sha256 == reference.layout.sha256
        assert scenarios(name)[0].id == "reference"
    with pytest.raises(ValueError, match="E1 scenarios"):
        make_example("E1", tmp_path / "unsupported", scenario="thermal_demo")


@pytest.mark.fast
@pytest.mark.parametrize("identifier", DEMONSTRATIONS)
def test_thermal_scenario_changes_excitation_without_changing_geometry_material_or_cooling(identifier, tmp_path):
    reference = make_example(identifier, tmp_path / "reference")
    demo = make_example(identifier, tmp_path / "demo", scenario="thermal_demo")
    assert demo.layout.sha256 == reference.layout.sha256
    assert demo.layers == reference.layers and demo.materials == reference.materials
    assert demo.interfaces == reference.interfaces and demo.thermal_boundaries == reference.thermal_boundaries
    assert not demo.heat_sources
    assert demo.temperature_limits[0].maximum_temperature == 423.15
    selected = next(item for item in scenarios(identifier) if item.id == "thermal_demo")
    assert demo.sweeps and demo.sweeps[0].values[-1] == selected.changes[0][1]
    if demo.transient.enabled:
        assert demo.sweeps[0].parameter == "transient.drives.0.waveform.high"


@pytest.mark.numerical
@pytest.mark.parametrize("identifier", DEMONSTRATIONS)
def test_exact_joule_demo_produces_supported_temperature_rise(identifier, scenario_solution):
    project, result = scenario_solution(identifier, "thermal_demo")
    assert result.converged and result.metrics["engineering_accepted"]
    peak = result.metrics.get("transient_peak_temperature", result.metrics["temperature_max"])
    assert 10. <= peak - 300. <= 50.
    assert result.metrics["current_balance_error"] <= project.solver.current_balance_tolerance
    assert result.metrics["power_balance_error"] <= project.solver.power_balance_tolerance
    assert result.metrics["thermal_balance_error"] <= project.solver.thermal_balance_tolerance
    assert result.analysis["loss_budget"]["thermal_deposition"] <= result.analysis["loss_budget"]["total_power"]
    assert not project.heat_sources
    if project.transient.enabled:
        assert result.metrics["transient_peak_temperature"] > result.metrics["temperature_max"]
        assert result.metrics["transient_energy_balance_error"] <= project.solver.thermal_balance_tolerance * result.metrics["transient_input_energy"]


@pytest.mark.numerical
def test_steady_failed_example_keeps_fields_and_reopens(scenario_solution, tmp_path):
    project, result = scenario_solution("E26", "thermal_limit_failure")
    assert result.converged and not result.metrics["engineering_accepted"]
    assert result.metrics["temperature_max"] > 310.
    assert result.metrics["thermal_limit_compliance"]["status"] == "failed"
    path = tmp_path / "failed.etl"
    save_project(project, path, result)
    restored_project, restored = load_project(path)
    assert physics_hash(restored_project) == physics_hash(project)
    assert restored.metrics == result.metrics and restored.fields


@pytest.mark.numerical
@pytest.mark.parametrize("scenario", ["thermal_limit_failure", "thermal_stop"])
def test_pulse_failure_is_based_on_accepted_peak_and_preserves_history(scenario, scenario_solution):
    project, result = scenario_solution("E18", scenario)
    assert result.converged and not result.metrics["engineering_accepted"]
    report = result.metrics["thermal_limit_compliance"]
    assert report["status"] == "failed" and report["first_violation"]["peak"]["time_s"] > 0
    assert result.transient.snapshots and result.metrics["transient_peak_temperature"] >= 310.
    if scenario == "thermal_limit_failure":
        assert result.transient.completed and result.metrics["temperature_max"] < 310.
    else:
        assert report["stopped_early"] and result.metrics["transient_time"] < project.transient.stop_time


@pytest.mark.fast
def test_cli_generates_named_scenario_without_solving(tmp_path, capsys):
    assert main(["examples", str(tmp_path), "--name", "E26", "--scenario", "thermal_demo"]) == 0
    project, result = load_project(tmp_path / "E26_dogbone_coupon.etl")
    assert result is None and project.power.ports[-1].value == 1.25
    assert project.temperature_limits[0].maximum_temperature == 423.15
    assert "E26_dogbone_coupon" in capsys.readouterr().out

@pytest.mark.fast
def test_observed_scenarios_keep_all_forty_references_and_independent_statuses():
    import json
    from pathlib import Path

    path = Path(__file__).resolve().parents[1] / 'docs/example-science/operating-scenarios.json'
    matrix = json.loads(path.read_text(encoding='utf-8'))
    assert len(matrix['examples']) == 40
    cases = []
    for row in matrix['examples']:
        assert row['revised_reference']['same_as'] == 'original'
        assert row['revised_reference']['physics_hash'] == row['original']['physics_hash']
        assert row['revised_reference']['layout_sha256'] == row['original']['layout_sha256']
        for scenario in row['optional_scenarios']:
            observed = scenario['observed']
            cases.append((row['id'], scenario['scenario']))
            assert observed['layout_sha256'] == row['original']['layout_sha256']
            assert observed['status']['numerically_converged']
            assert observed['status']['engineering_accepted'] != scenario['expected_engineering_failure']
            assert not observed['status']['calibration_verified']
            if observed['thermal'].get('transient_peak_retained'):
                assert observed['thermal']['peak_state']['time_s'] == observed['thermal']['transient_peak_time']
    assert len(cases) == len(set(cases)) == 17
    time_rows = matrix['fixed_mesh_time_sensitivity']['rows']
    assert len({row['mesh_sha256'] for row in time_rows}) == 1
    assert [row['accepted_steps'] for row in time_rows] == [40, 80, 160]
