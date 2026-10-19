# SPDX-License-Identifier: MPL-2.0
"""Join actual baseline/scenario receipts into a small maintained comparison."""
from __future__ import annotations

import argparse
import json
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))


def summary(record):
    categories = record.get('loss_categories_W', record.get('loss_budget', {}).get('categories', {}))
    return dict(configuration_hash=record['configuration_hash'], physics_hash=record['physics_hash'],
        layout_sha256=record['layout_sha256'], mode=record['analysis_mode'], electrical=record['electrical'],
        loss_categories_W=categories, thermal=record['thermal'], status=record['status'],
        measurements={identifier: {**{key: value[key] for key in ('quantity', 'status', 'value', 'unit', 'within_limits', 'support_area_m2', 'window_center', 'window_definition', 'mesh_sensitive_raw_maximum') if key in value},
            'support': {key: item for key, item in value.get('specification', {}).items() if key in ('aggregation', 'averaging_length', 'weighting', 'selector') and item}} for identifier, value in record.get('measurements', {}).items()},
        layers=[{key: layer[key] for key in ('id', 'role', 'area_m2', 'thickness_m', 'temperature_range_K', 'field_ranges', 'current_density_interpretation') if key in layer} for layer in record['layers']],
        mesh={key: record['mesh'][key] for key in ('nodes', 'elements')},
        phase_seconds=record.get('performance', {}).get('timings_seconds', record.get('performance', {}).get('phase_seconds', {})))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--baseline', type=Path, required=True)
    parser.add_argument('--scenarios', type=Path, required=True)
    parser.add_argument('--spatial', type=Path, required=True)
    parser.add_argument('--temporal', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    baseline = json.loads(args.baseline.read_text(encoding='utf-8'))
    index = json.loads((args.scenarios / 'index.json').read_text(encoding='utf-8'))
    if not index['source_unchanged'] or any(row['status'] != 'passed' for row in index['observations']):
        raise ValueError('Scenario validation or source-integrity check failed')
    records = [json.loads(path.read_text(encoding='utf-8')) for path in args.scenarios.glob('*-mesh-1/receipt.json')]
    if len(records) != 17:
        raise ValueError('All seventeen configured scenarios are required')
    from etlayout.examples import make_example
    from etlayout.io.project import configuration_hash, physics_hash

    regenerated = {}
    with tempfile.TemporaryDirectory(prefix='etl-scenario-report-') as temporary:
        for original in baseline['examples']:
            project = make_example(original['id'], Path(temporary) / original['id'])
            if physics_hash(project) != original['physics_hash']:
                raise ValueError('A default reference changed physical identity: ' + original['id'])
            regenerated[original['id']] = dict(configuration_hash=configuration_hash(project),
                physics_hash=physics_hash(project), layout_sha256=project.layout.sha256)
    rows = []
    for original in baseline['examples']:
        variants = []
        for record in sorted(records, key=lambda row: row['case']):
            if record['id'] == original['id']:
                if record['layout_sha256'] != original['layout_sha256']:
                    raise ValueError('An operating scenario changed the reference layout')
                variants.append(dict(scenario=record['case'].split(':')[1],
                    expected_engineering_failure=record['expected_engineering_failure'],
                    observed=summary(record), portability=record['portability'],
                    process_observation={key: next(item for item in index['observations'] if item['case'] == record['case'])[key] for key in ('process_wall_seconds', 'peak_rss_bytes')}))
        rows.append(dict(id=original['id'], name=original['name'], original=summary(original),
            revised_reference=dict(same_as='original', scope='Scientific quantities reuse original observations; these hashes identify the current regenerated project',
                **regenerated[original['id']]),
            optional_scenarios=variants))
    spatial = json.loads(args.spatial.read_text(encoding='utf-8'))
    spatial = [{key: row[key] for key in ('id', 'factor', 'elements', 'nodes', 'current_A', 'input_power_W',
        'temperature_max_K', 'run_peak_temperature_K', 'thermal_rise_K', 'energy_in_J', 'conservation', 'duration_seconds',
        'process_wall_seconds', 'peak_rss_bytes')} for row in spatial if row['kind'] == 'mesh']
    temporal = json.loads(args.temporal.read_text(encoding='utf-8'))
    if temporal['status'] != 'passed' or len({row['mesh_sha256'] for row in temporal['rows']}) != 1:
        raise ValueError('Time study requires one exact mesh and successful accepted states')
    temporal_rows = [{key: row[key] for key in ('step_s', 'mesh_sha256', 'elements', 'peak_K', 'peak_time_s',
        'energy_in_J', 'energy_out_J', 'stored_J', 'balance_error_J', 'accepted_steps')} for row in temporal['rows']]
    data = dict(schema_version=1, baseline_source_revision=baseline['source_revision'],
        scenario_source_fingerprint=index['source_fingerprint'], units='SI; temperatures in K; phase times in s',
        evidence='Actual configured calculations; synthetic and uncalibrated. Independent sensitivity observations do not modify run verification badges.',
        hardware='Windows 11; Python 3.12.14; 8 physical/16 logical AMD64 cores; 33.48 GB RAM; 4 native CPU threads, one study worker; shared host',
        examples=rows, spatial_sensitivity=spatial, fixed_mesh_time_sensitivity=dict(case='E36:thermal_demo', rows=temporal_rows))
    args.output.mkdir(parents=True, exist_ok=True)
    (args.output / 'operating-scenarios.json').write_text(json.dumps(data, ensure_ascii=False, allow_nan=False, indent=2) + '\n', encoding='utf-8')
    lines = ['# Observed operating scenarios', '',
        'Every E1–E40 default remains the [original reference](nominal-reference.md). The [complete comparison](operating-scenarios.json) joins all forty originals with their unchanged defaults and the seventeen optional calculated scenarios. Values below use the complete run peak, including retained hot states before pulse cooling.', '',
        '| Example / scenario | Current at hot state (A) | Electrical power at hot state (W) | Peak (K) | Final (K) | Engineering result | Triangles |',
        '| --- | ---: | ---: | ---: | ---: | --- | ---: |']
    for record in sorted(records, key=lambda row: (int(row['id'][1:]), row['case'])):
        t = record['thermal']
        state = t.get('peak_state') or record['electrical']
        peak = t.get('transient_peak_temperature') or t['temperature_max']
        status = 'expected failure' if record['expected_engineering_failure'] else 'passed chosen limit' if record['status']['thermal_limit_compliance']['status'] == 'passed' else 'no chosen limit'
        lines.append(f"| {record['case']} | {state.get('total_current', 0):.6g} | {state.get('electrical_power', 0):.6g} | {peak:.5f} | {t['temperature_max']:.5f} | {status} | {record['mesh']['elements']} |")
    lines += ['', 'The failed-limit and early-stop rows are diagnostic failures, not accepted designs. A stop occurs at the first accepted violating state; it does not interpolate an exact crossing. Passive cooling after the pulse remains present in the full-duration rows.', '',
        '## Three spatial levels', '',
        'All electrical/material/thermal inputs stay fixed. All global, minimum, contact and explicit local refinement sizes are multiplied by 1, 0.8 and 0.65. The last-two differences below use temperature **rise relative to the named 300 K reservoir**, not percent of absolute temperature. These observations support the selected integral/thermal metrics; sharp-corner raw current maxima remain unverified.', '',
        '| Example | Triangles (coarse / medium / fine) | Fine peak (K) | Last-two power / pulse-energy difference | Last-two rise difference |',
        '| --- | ---: | ---: | ---: | ---: |']
    for identifier in dict.fromkeys(row['id'] for row in spatial):
        group = [row for row in spatial if row['id'] == identifier]
        medium, fine = group[-2:]
        def rise(row):
            return (row['run_peak_temperature_K'] or row['temperature_max_K']) - 300
        metric = 'energy_in_J' if fine['energy_in_J'] is not None else 'input_power_W'
        power_delta = abs(medium[metric] - fine[metric]) / abs(fine[metric])
        lines.append(f"| {identifier} | {' / '.join(str(row['elements']) for row in group)} | {rise(fine)+300:.5f} | {power_delta:.3%} | {abs(rise(medium)-rise(fine))/rise(fine):.3%} |")
    lines += ['', '## One exact mesh, three time steps', '',
        'E36 uses identical point/connectivity arrays at 10, 5 and 2.5 µs; each starts physically at 300 K and reaches 400 µs. This serial study took 79.50 s on the shared host. Independently remeshed preliminary time runs were excluded because they confounded spatial and temporal error.', '',
        '| Step (µs) | Accepted states | Peak (K) | Peak time (µs) | Input energy (µJ) | Stored energy (µJ) | Absolute energy residual (nJ) |',
        '| ---: | ---: | ---: | ---: | ---: | ---: | ---: |']
    for row in temporal_rows:
        lines.append(f"| {row['step_s']*1e6:g} | {row['accepted_steps']} | {row['peak_K']:.5f} | {row['peak_time_s']*1e6:g} | {row['energy_in_J']*1e6:.6g} | {row['stored_J']*1e6:.6g} | {row['balance_error_J']*1e9:.6g} |")
    lines += ['', 'The final two steps differ by about 0.16% in temperature rise and 0.34% in input energy. Numerical convergence, engineering-limit acceptance, mesh/time sensitivity and physical calibration are separate statements.', '']
    (args.output / 'operating-scenarios.md').write_text('\n'.join(lines), encoding='utf-8')


if __name__ == '__main__':
    main()
