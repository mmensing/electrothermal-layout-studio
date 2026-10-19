# SPDX-License-Identifier: MPL-2.0
"""Build a compact all-catalog index of original, thermal and device observations."""
from __future__ import annotations

import argparse
import json
from pathlib import Path


def measurement(item):
    values = {key: value for key, value in item.items() if key in {'quantity','status','value','unit','within_limits','reason','support_area_m2','window_center','window_definition','mesh_sensitive_raw_maximum'}}
    spec = item['specification']
    values['support'] = {key: value for key, value in spec.items() if key in {'selector','averaging_length','aggregation','weighting'} and value}
    return values


def compact(receipt):
    record=receipt['record']
    return dict(case=receipt['case'],mesh_factor=receipt['mesh_factor'] if 'mesh_factor' in receipt else 1.,
        configuration_hash=record['configuration_hash'],physics_hash=record['physics_hash'],layout_sha256=record['layout_sha256'],
        electrical=record['electrical'],observations=receipt['observations'],thermal=record['thermal'],
        layers=[{key: layer[key] for key in ('id','role','area_m2','thickness_m','field_ranges','temperature_range_K','current_density_interpretation')} for layer in record['layers']],
        measurements={key:measurement(item) for key,item in record['measurements'].items()},
        mesh={key:record['mesh'][key] for key in ('nodes','elements')},status=record['status'],
        phase_seconds=record['performance'].get('timings_seconds',{}),portability=receipt['portability'],resource_observation=receipt.get('resource_observation',{}))


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--thermal',type=Path,required=True)
    parser.add_argument('--nominals',type=Path,required=True)
    parser.add_argument('--mesh',type=Path,required=True)
    parser.add_argument('--studies',type=Path,required=True)
    parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args()
    thermal=json.loads(args.thermal.read_text(encoding='utf-8'))
    cases=[json.loads(path.read_text(encoding='utf-8')) for path in args.nominals.glob('*/receipt.json')]
    import sys
    sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'src'))
    from verify_device_examples import CASES

    from etlayout.device_scenarios import engineering_observations
    from etlayout.example_science import scientific_record
    from etlayout.io.project import load_project
    if {case['case'] for case in cases} != set(CASES) or len(cases) != len(CASES):
        raise ValueError('The exact nine registered engineering cases are required')
    process_rows = json.loads((args.nominals/'index.json').read_text(encoding='utf-8'))
    for case in cases:
        process = next(row for row in process_rows if row['case']==case['case'])
        case['resource_observation'] = {key:process[key] for key in ('process_wall_seconds','peak_rss_bytes')}
        project,result = load_project(args.nominals/case['case'].replace(':','-')/'solved.etl')
        if result.configuration_hash != case['record']['configuration_hash']:
            raise ValueError('Recorded observation differs from its archived calculation')
        case['record'] = scientific_record(project,result)
        case['observations'] = engineering_observations(project,result)
    meshes=[json.loads(path.read_text(encoding='utf-8')) for path in args.mesh.glob('*/receipt.json')]
    studies=[json.loads(path.read_text(encoding='utf-8')) for path in args.studies.glob('*/receipt.json')]
    if len(cases)!=9 or len(meshes)!=12 or len(studies)!=4:
        raise ValueError('Require nine exact device configurations, twelve mesh observations and all four saved-study reports')
    for directory in (args.nominals,args.mesh,args.studies):
        index=json.loads((directory/'index.json').read_text(encoding='utf-8'))
        if any(row['status']!='passed' for row in index):
            raise ValueError('A device validation process did not pass')
    for study in studies:
        for spec in study['studies']:
            for row in spec['observations']:
                evidence = row['engineering']
                if 'lateral_sheet_power_W' not in evidence:
                    evidence['lateral_sheet_power_W'] = evidence['metal_sheet_power_W']
                    evidence['metal_sheet_power_W'] = row['measurements']['routing_loss']['value']
                    evidence['channel_access_power_W'] = evidence['lateral_sheet_power_W']-evidence['metal_sheet_power_W']
                row['measurements'] = {key:measurement(item) for key,item in row['measurements'].items()}
    examples=[]
    for index,row in enumerate(thermal['examples']):
        selected=[compact(receipt) for receipt in cases if receipt['record']['id']==row['id']]
        for item in selected:
            if item['layout_sha256']!=row['original']['layout_sha256']:
                raise ValueError('A scenario changed reference active-layout bytes')
        examples.append(dict(id=row['id'],name=row['name'],
            original_observation=f'nominal-reference.json#/examples/{index}',
            thermal_observations=f'operating-scenarios.json#/examples/{index}',
            revised_reference=row['revised_reference'],engineering_scenarios=selected))
    sensitivity=[]
    for receipt in meshes:
        record=receipt['record']
        sensitivity.append(dict(case=receipt['case'],factor=receipt['mesh_factor'],
            elements=record['mesh']['elements'],configuration_hash=record['configuration_hash'],
            current_A=receipt['observations']['current_A'],power_W=receipt['observations']['total_input_power_W'],
            temperature_max_K=record['thermal']['temperature_max'],
            averaged_hotspot=measurement(record['measurements']['regularized_hotspot']),
            finger_sharing=receipt['observations']['finger_current_spread_relative_to_mean'],
            source_spread_V=receipt['observations']['device_source_potential_spread_V'],
            conservation={key:value for key,value in record['electrical'].items() if key.endswith('balance_error')},
            phase_seconds=record['performance'].get('timings_seconds',{})))
    args.output.mkdir(parents=True,exist_ok=True)
    data=dict(schema_version=1,units='SI; temperature K; phases s',
        interpretation='Actual source calculations under synthetic, uncalibrated device/material assumptions. Independent pointers join all forty originals with unchanged references and optional thermal/device configurations. Voltage-biased alternatives provide unequal service and are not improvement rankings.',
        examples=examples,spatial_sensitivity=sensitivity,saved_studies=studies)
    (args.output/'device-observations.json').write_text(json.dumps(data,ensure_ascii=False,allow_nan=False,indent=2)+'\n',encoding='utf-8')
    lines=['# Calculated device operating cases','',
        'All default references remain unchanged. The [combined E1–E40 index](device-observations.json) links original and thermal observations without duplicating their full material/geometry registries. Voltage-biased GaN alternatives deliver different currents; these comparisons do not rank routing improvements at equal service.','',
        '| Case | Delivered (A) | Input (W) | Device load (W) | Peak (K) | Triangles |',
        '| --- | ---: | ---: | ---: | ---: | ---: |']
    for receipt in sorted(cases,key=lambda row:row['case']):
        r,o=receipt['record'],receipt['observations']
        lines.append(f"| {receipt['case']} | {o['current_A']:.7g} | {o['total_input_power_W']:.7g} | {o['intrinsic_device_power_W']:.7g} | {r['thermal']['temperature_max']:.5f} | {r['mesh']['elements']} |")
    lines += ['','## Mesh sensitivity of selected metrics','',
        'All global, contact, minimum and explicit local/axis sizes scale together. The averaging support/length is fixed in physical units across levels. Differences below compare the last two levels; temperature means the regularized hotspot **rise above the named 300 K reservoir**. Raw maxima and physical calibration remain separate.','',
        '| Case | Triangles (coarse / medium / fine) | Current difference | Input-power difference | Averaged-rise difference |',
        '| --- | ---: | ---: | ---: | ---: |']
    for case in dict.fromkeys(row['case'] for row in sensitivity):
        group=sorted([row for row in sensitivity if row['case']==case],key=lambda row:-row['factor'])
        medium,fine=group[-2:]
        def delta(key, medium=medium, fine=fine):
            return abs(medium[key]-fine[key])/abs(fine[key])
        hm=medium['averaged_hotspot']['value']-300
        hf=fine['averaged_hotspot']['value']-300
        lines.append(f"| {case} | {' / '.join(str(row['elements']) for row in group)} | {delta('current_A'):.3%} | {delta('power_W'):.3%} | {abs(hm-hf)/hf:.3%} |")
    lines += ['','Saved-study points, exact source/configuration hashes, finite contacts, disjoint loss components, 2DEG versus bulk loading, conserved currents, timing and averaging support remain in the machine-readable observations. Each run is numerically converged; a selected-metric sensitivity observation does not change its independent verification badges.','']
    (args.output/'device-observations.md').write_text('\n'.join(lines),encoding='utf-8')


if __name__=='__main__':
    main()
