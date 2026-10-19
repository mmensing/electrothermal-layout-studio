# SPDX-License-Identifier: MPL-2.0
"""Device choices use existing ports/loads; independent power and sharing checks."""
from copy import deepcopy

import pytest

from etlayout.device_examples import metadata
from etlayout.device_scenarios import device_scenarios, engineering_observations
from etlayout.examples import make_example
from etlayout.io.project import load_project, physics_hash, save_project
from etlayout.simulation.runner import run_project


@pytest.fixture(scope='module')
def device_solution(tmp_path_factory):
    records={}
    def get(identifier,scenario,mode=None):
        key=identifier,scenario,mode
        if key not in records:
            p=make_example(identifier,tmp_path_factory.mktemp(identifier+scenario),scenario=scenario)
            if mode:
                p.solver.mode=mode
            before=p.to_dict()
            result=run_project(p)
            assert p.to_dict()==before and result.converged
            records[key]=p,result
        return deepcopy(records[key])
    return get


@pytest.mark.fast
@pytest.mark.parametrize('identifier',['E37','E38','E39'])
def test_device_options_preserve_active_geometry_and_roundtrip(identifier,tmp_path):
    original=make_example(identifier,tmp_path/'original')
    for choice in device_scenarios(identifier):
        project=make_example(identifier,tmp_path/choice.id,scenario=choice.id)
        assert project.layers==original.layers and project.layout.sha256==original.layout.sha256
        assert project.materials==original.materials and project.thermal_boundaries==original.thermal_boundaries
        assert project.sweeps and not project.heat_sources
        path=tmp_path/(choice.id+'.etl')
        save_project(project,path)
        restored,result=load_project(path)
        assert result is None and physics_hash(restored)==physics_hash(project)
        assert metadata(restored)['operating_scenario']['id']==choice.id
        if identifier in {'E37','E38'}:
            assert 'requested_current_A' not in metadata(restored)
            assert metadata(restored)['drain_reservoir_voltage_V']
        if identifier=='E38':
            drains=[port for port in project.power.ports if port.id.startswith('drain')]
            assert {port.kind for port in drains}=={'voltage'}
            assert {port.external_node for port in drains}=={'shared_drain'}


@pytest.mark.numerical
def test_voltage_coupon_uses_calculated_current_and_low_field_scope(device_solution):
    project,result=device_solution('E37','voltage_controlled')
    record=engineering_observations(project,result)
    assert result.analysis['ports']['drain_a']['voltage']==pytest.approx(.2,abs=1e-12)
    assert result.analysis['ports']['drain_a']['current']>0
    assert not result.analysis['ports']['drain_a'].get('specified_current')
    assert record['banks']['bank_a']['current_per_width']>0
    assert record['channel_access_power_W']>record['metal_sheet_power_W']>0
    assert record['lateral_sheet_power_W']==pytest.approx(record['channel_access_power_W']+record['metal_sheet_power_W'],rel=1e-10)
    assert record['banks']['bank_a']['effective_gate_bias']<3.
    assert result.analysis['measurements']['field_a']['within_limits']


@pytest.mark.numerical
def test_parallel_bank_currents_emerge_and_gate_mismatch_changes_sharing(device_solution):
    _,symmetric=device_solution('E38','parallel_voltage')
    p,mismatched=device_solution('E38','gate_mismatch')
    for result in (symmetric,mismatched):
        banks=result.analysis['devices']
        current=sum(banks[key]['current'] for key in ('bank_a','bank_b'))
        assert current==pytest.approx(result.metrics['total_current'],rel=1e-8)
        assert result.analysis['ports']['drain_a']['voltage']==result.analysis['ports']['drain_b']['voltage']
        assert result.analysis['ports']['drain_a']['constraint_scope']=='shared_external_node'
        assert sum(banks[f'bank_b_finger_{index}']['current'] for index in range(1,9))==pytest.approx(banks['bank_b']['current'],rel=1e-8)
    bank=mismatched.analysis['devices']
    assert bank['bank_b']['current']<.9*bank['bank_a']['current']
    assert engineering_observations(p,mismatched)['device_source_potential_spread_V']>0


@pytest.mark.numerical
def test_sic_lower_current_and_named_heat_keep_intrinsic_area_normalization(device_solution):
    p,result=device_solution('E39','semiconductor_heat')
    report=engineering_observations(p,result)
    area=4*(320e-6)**2
    assert result.metrics['total_current']==pytest.approx(2.,rel=1e-8)
    assert sum(row.active_area for row in p.power.devices)==pytest.approx(area)
    assert sum(row['current'] for row in result.analysis['devices'].values())==pytest.approx(2.,rel=1e-8)
    assert report['intrinsic_device_power_W']/4.>=1e-7/area
    assert report['intrinsic_device_power_W']>report['via_power_W']>0
    deposition=report['heat_deposition']['on_state']
    assert deposition['model']=='named_sheet'
    assert deposition['deposited_by_layer']=={'substrate':pytest.approx(report['intrinsic_device_power_W'])}
    assert result.analysis['measurements']['kelvin_potential']['status']=='available'


@pytest.mark.numerical
def test_package_isothermal_scaling_and_independent_fixture_deembedding(tmp_path):
    observations=[]
    for current in (2.5,5.):
        p=make_example('E40',tmp_path/str(current))
        p.solver.mode='electrical'
        p.power.ports[-1].value=current
        result=run_project(p)
        assert result.converged
        record=engineering_observations(p,result)
        observations.append(record)
        expected=result.metrics['joule_power']+sum(row['power'] for row in result.metrics['contacts'].values())
        assert record['internal_power_W']==pytest.approx(expected,rel=1e-10)
        assert record['total_input_power_W']==pytest.approx(expected+record['external_feed_power_W'],rel=1e-10)
        assert record['locally_deposited_feed_W']==pytest.approx(.25*record['external_feed_power_W'],rel=1e-10)
        assert record['via_power_W']>0 and record['intrinsic_device_power_W']==0
    assert observations[1]['total_input_power_W']==pytest.approx(4*observations[0]['total_input_power_W'],rel=1e-9)
    assert observations[1]['feed_deembedded_internal_resistance_ohm']==pytest.approx(observations[0]['feed_deembedded_internal_resistance_ohm'],rel=1e-9)

@pytest.mark.fast
def test_combined_scientific_index_preserves_catalog_join_and_disjoint_losses():
    import json
    from pathlib import Path

    root=Path(__file__).resolve().parents[1]/'docs/example-science'
    matrix=json.loads((root/'device-observations.json').read_text(encoding='utf-8'))
    original=json.loads((root/'nominal-reference.json').read_text(encoding='utf-8'))['examples']
    thermal=json.loads((root/'operating-scenarios.json').read_text(encoding='utf-8'))['examples']
    assert len(matrix['examples'])==40
    cases=[]
    for index,row in enumerate(matrix['examples']):
        assert row['id']==original[index]['id']==thermal[index]['id']
        assert row['revised_reference']['physics_hash']==original[index]['physics_hash']
        for case in row['engineering_scenarios']:
            assert case['layout_sha256']==original[index]['layout_sha256']
            report=case['observations']
            total=sum(report[key] for key in ('lateral_sheet_power_W','intrinsic_device_power_W','conventional_contact_power_W','via_power_W','external_feed_power_W'))
            assert total==pytest.approx(report['total_input_power_W'],rel=1e-10)
            assert report['metal_sheet_power_W']+report['channel_access_power_W']==pytest.approx(report['lateral_sheet_power_W'],rel=1e-10)
            assert case['status']['numerically_converged'] and not case['status']['calibration_verified']
            cases.append(case['case'])
    assert len(cases)==len(set(cases))==9
    assert len(matrix['spatial_sensitivity'])==12
    for row in matrix['spatial_sensitivity']:
        assert row['averaged_hotspot']['support']['averaging_length']>0

@pytest.mark.fast
def test_shared_reservoir_has_two_power_nodes_but_controlled_banks_do_not(tmp_path):
    from etlayout.device_scenarios import two_node_service
    assert not two_node_service(make_example('E38',tmp_path/'independent'))
    assert two_node_service(make_example('E38',tmp_path/'parallel',scenario='parallel_voltage'))
    assert two_node_service(make_example('E40',tmp_path/'package'))

@pytest.mark.fast
def test_cli_generates_parameterized_tlm_coupon_without_solve(tmp_path,capsys):
    from etlayout.cli.main import main
    from etlayout.geometry.prepare import prepare_geometry
    assert main(['examples',str(tmp_path),'--name','E27','--tlm-spacing-um','80'])==0
    from etlayout.example_catalog import example_info
    name=example_info('E27').name
    project,result=load_project(tmp_path/(name+'.etl'))
    assert result is None
    prepared=prepare_geometry(project)
    left,right=sorted(prepared.layers['pads'].geoms,key=lambda geometry:geometry.bounds[0])
    assert right.bounds[0]-left.bounds[2]==pytest.approx(80e-6)
    assert name in capsys.readouterr().out
