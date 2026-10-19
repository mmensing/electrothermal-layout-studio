# SPDX-License-Identifier: MPL-2.0
"""Run bounded saved device sweeps and fixed-temperature reference calculations."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'src'))


def worker(identifier,output):
    from verify_extended_examples import validate_nominal

    from etlayout.device_scenarios import engineering_observations
    from etlayout.example_science import scientific_record
    from etlayout.examples import make_example
    from etlayout.io.project import project_from_dict
    from etlayout.simulation.runner import run_project
    from etlayout.simulation.sweep import run_sweep
    scenario={'E37':'voltage_controlled','E38':'parallel_voltage','E39':'semiconductor_heat','E40':'reference'}[identifier]
    p=make_example(identifier,output/'inputs',scenario=scenario)
    studies=[]
    for spec in p.sweeps:
        rows=run_sweep(p,spec,execution={'cpu_threads':4,'study_workers':1})
        observations=[]
        for row in rows:
            assert not row.error and row.result.converged
            actual=project_from_dict(row.result.configuration)
            acceptance=validate_nominal(actual,row.result)
            record=scientific_record(actual,row.result)
            assert not record['status']['declared_model_violations']
            observations.append(dict(value=row.value,engineering=engineering_observations(actual,row.result),
                peak_K=record['thermal']['temperature_max'],measurements=record['measurements'],
                acceptance=acceptance,phase_seconds=row.result.execution.get('timings_seconds',{})))
        studies.append(dict(parameter=spec.parameter,mode=spec.mode,observations=observations))
    references=[]
    if identifier=='E37':
        p.solver.mode='electrical'
        result=run_project(p,execution={'cpu_threads':4,'study_workers':1})
        assert result.converged
        references.append(dict(kind='fixed_temperature_300_K',engineering=engineering_observations(p,result)))
    if identifier=='E40':
        for current in (2.5,5.):
            p.solver.mode='electrical'
            p.power.ports[-1].value=current
            result=run_project(p,execution={'cpu_threads':4,'study_workers':1})
            assert result.converged
            references.append(dict(kind='fixed_temperature_300_K',engineering=engineering_observations(p,result)))
        a,b=[item['engineering'] for item in references]
        assert abs(b['internal_power_W']/a['internal_power_W']-4)<1e-9
        assert abs(b['feed_deembedded_internal_resistance_ohm']/a['feed_deembedded_internal_resistance_ohm']-1)<1e-9
    (output/'receipt.json').write_text(json.dumps(dict(id=identifier,studies=studies,references=references),ensure_ascii=False,allow_nan=False,indent=2)+'\n',encoding='utf-8')
    print(json.dumps(dict(status='passed',id=identifier,studies=len(studies),points=sum(len(item['observations']) for item in studies))))


def main():
    from benchmark_archive_history import observe
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--examples',nargs='+',choices=['E37','E38','E39','E40'],default=['E37','E38','E39','E40'])
    parser.add_argument('--worker',help=argparse.SUPPRESS)
    args=parser.parse_args()
    if args.worker:
        worker(args.worker,args.output)
        return 0
    args.output.mkdir(parents=True,exist_ok=False)
    observations=[]
    for identifier in args.examples:
        destination=args.output/identifier
        item=observe([sys.executable,str(Path(__file__).resolve()),'--output',str(destination),'--worker',identifier],
            destination.with_name(destination.name+'-process'),max_rss_gib=3.,timeout=900.)
        item['id']=identifier
        observations.append(item)
        print(identifier,item['status'],flush=True)
    (args.output/'index.json').write_text(json.dumps(observations,indent=2)+'\n',encoding='utf-8')
    return int(any(row['status']!='passed' for row in observations))


if __name__=='__main__':
    raise SystemExit(main())
