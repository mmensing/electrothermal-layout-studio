# SPDX-License-Identifier: MPL-2.0
"""Opt-in exact GaN/SiC/package scenarios, archive and independent ledger checks."""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'src'))
CASES=('E37:voltage_controlled','E38:parallel_voltage','E38:unequal_contact','E38:gate_mismatch',
       'E38:source_asymmetry','E39:continuous_reference','E39:semiconductor_heat',
       'E39:stress_semiconductor_heat','E40:reference')


def worker(case,output,factor):
    from verify_extended_examples import validate_nominal, verify_portability

    from etlayout.device_scenarios import engineering_observations
    from etlayout.example_science import scientific_record
    from etlayout.examples import make_example
    from etlayout.io.project import save_project
    from etlayout.simulation.runner import run_project
    identifier,scenario=case.split(':')
    project=make_example(identifier,output/'inputs',scenario=scenario)
    for key in ('max_size','min_size','contact_size'):
        setattr(project.mesh,key,getattr(project.mesh,key)*factor)
    for refinement in project.mesh.refinements:
        refinement.size *= factor
        refinement.axis_sizes = [value*factor for value in refinement.axis_sizes]
    save_project(project,output/'inputs'/(project.name+'.etl'))
    before=project.to_dict()
    start=time.perf_counter()
    result=run_project(project,execution={'cpu_threads':4,'study_workers':1})
    assert project.to_dict()==before
    acceptance=validate_nominal(project,result)
    record=scientific_record(project,result)
    observations=engineering_observations(project,result)
    assert not record['status']['declared_model_violations']
    assert not record['status']['property_range_violations']
    split=sum(observations[key] for key in ('lateral_sheet_power_W','intrinsic_device_power_W',
        'conventional_contact_power_W','via_power_W','external_feed_power_W'))
    assert abs(split-observations['total_input_power_W']) <= 1e-10*max(abs(split),1e-15)
    portability=verify_portability(project,result,output/'portability')
    save_project(project,output/'solved.etl',result,archive_format=3)
    data=dict(case=case,mesh_factor=factor,record=record,observations=observations,acceptance=acceptance,
              portability=portability,wall_seconds=time.perf_counter()-start)
    (output/'receipt.json').write_text(json.dumps(data,ensure_ascii=False,allow_nan=False,indent=2)+'\n',encoding='utf-8')
    print(json.dumps(dict(status='passed',case=case,current_A=observations['current_A'],peak_K=record['thermal']['temperature_max'])))


def main():
    from benchmark_archive_history import observe
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--cases',nargs='+',default=CASES)
    parser.add_argument('--mesh-factors',type=float,nargs='+',default=[1.])
    parser.add_argument('--worker-factor',type=float,default=1.,help=argparse.SUPPRESS)
    parser.add_argument('--worker',help=argparse.SUPPRESS)
    args=parser.parse_args()
    if args.worker:
        worker(args.worker,args.output,args.worker_factor)
        return 0
    if any(not 0 < factor <= 1 for factor in args.mesh_factors) or any(b >= a for a,b in zip(args.mesh_factors,args.mesh_factors[1:])):
        parser.error('Use positive strictly decreasing mesh factors up to one')
    args.output.mkdir(parents=True,exist_ok=False)
    records=[]
    for case in args.cases:
        for factor in args.mesh_factors:
            destination=args.output/(case.replace(':','-')+f'-mesh-{factor:g}')
            observation=observe([sys.executable,str(Path(__file__).resolve()),'--output',str(destination),'--worker',case,'--worker-factor',str(factor)],
                destination.with_name(destination.name+'-process'),max_rss_gib=3.,timeout=600.)
            observation['case'],observation['mesh_factor']=case,factor
            records.append(observation)
            print(case,factor,observation['status'],flush=True)
    (args.output/'index.json').write_text(json.dumps(records,indent=2)+'\n',encoding='utf-8')
    return int(any(row['status']!='passed' for row in records))


if __name__=='__main__':
    raise SystemExit(main())
