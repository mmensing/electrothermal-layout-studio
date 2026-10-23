# SPDX-License-Identifier: MPL-2.0
"""Opt-in actual C-engine benchmarks; never run by catalog generation or normal CI."""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import platform
import sys
import time
from pathlib import Path

import numpy as np
from shapely.geometry import Point

from etlayout.examples import rectangle
from etlayout.geometry.prepare import geometry_region
from etlayout.models import Material, PhysicalLayer, Project
from etlayout.power import capacitance, external_tool, field_snapshot
from etlayout.power.capacitance import extract_capacitance, run_capacitance_mesh_study
from etlayout.power.field_snapshot import build_field_snapshot


def cube_project(size_m, epsilon_r=1.):
    project = Project(materials={"metal":Material("metal","Synthetic PEC conductor")},layers=[
        PhysicalLayer("cube","Cube","metal",thickness=size_m,
                      geometry=rectangle(0,0,size_m*1e6,size_m*1e6))])
    project.parasitics={"schema_version":1,"background_relative_permittivity":epsilon_r}
    return project


def plate_project():
    project=cube_project(.001)
    project.layers=[PhysicalLayer("lower","Lower plate","metal",thickness=10e-6,
                                  geometry=rectangle(0,0,1000,1000)),
                    PhysicalLayer("upper","Upper plate","metal",thickness=10e-6,z=60e-6,
                                  geometry=rectangle(0,0,1000,1000))]
    return project


def coax_project():
    project=cube_project(.001)
    inner=Point(0,0).buffer(50e-6,quad_segs=4)
    outer=Point(0,0).buffer(125e-6,quad_segs=4).difference(Point(0,0).buffer(100e-6,quad_segs=4))
    project.layers=[PhysicalLayer("inner","Finite 16-gon inner conductor","metal",thickness=.002,
                                  geometry=geometry_region(inner)),
                    PhysicalLayer("outer","Finite 16-gon hollow shield","metal",thickness=.002,
                                  geometry=geometry_region(outer))]
    return project


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--executable",required=True,type=Path,help="Explicit raw-output-capable FastCap2 executable")
    parser.add_argument("--output",required=True,type=Path,help="New directory for controlled inputs and complete evidence")
    parser.add_argument("--timeout",type=float,default=45.,help="Per actual engine run deadline in seconds")
    args=parser.parse_args(argv)
    if args.output.exists():
        parser.error("output must be a new directory; existing evidence is never overwritten")
    if not args.executable.is_absolute() or not args.executable.is_file():
        parser.error("executable must be an existing absolute path")
    args.output.mkdir(parents=True)
    started=time.perf_counter()
    result={"schema_version":1,"source_revision_instruction":"Record the tested public Git commit alongside this output.",
            "benchmark_source_sha256":hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
            "implementation_source_sha256":{m.__name__:hashlib.sha256(Path(m.__file__).read_bytes()).hexdigest()
                for m in (capacitance,field_snapshot,external_tool)},
            "executable_sha256":hashlib.sha256(args.executable.read_bytes()).hexdigest(),
            "hardware":{"platform":platform.platform(),"processor":platform.processor(),"python":sys.version},
            "settings":{"native_threads":1,"memory_limit_bytes":1024**3,"per_run_timeout_s":args.timeout},
            "checks":{},"reports":{}}
    try:
        cube=build_field_snapshot(cube_project(.001),kind="capacitance")
        study=run_capacitance_mesh_study(cube,args.executable,args.output/"cube",
            [.001,.0005,.00025],timeout=args.timeout)
        result["reports"]["cube"]=study
        result["checks"]["cube_mesh_verified"]=study["mesh_verified"] and study["accepted"]
        fine=study["network"]
        if fine is None:
            raise RuntimeError("No completed cube operator; inspect retained engine evidence")
        cube_c=fine["raw_matrix_F"][0][0]
        scaled=extract_capacitance(build_field_snapshot(cube_project(.002),kind="capacitance"),
            args.executable,args.output/"cube-double-size",mesh_size_m=.0005,timeout=args.timeout)
        dielectric=extract_capacitance(build_field_snapshot(cube_project(.001,4.),kind="capacitance"),
            args.executable,args.output/"cube-epsilon4",mesh_size_m=.00025,timeout=args.timeout)
        result["reports"]["cube_double_size"]=scaled
        result["reports"]["cube_epsilon4"]=dielectric
        for name,report,factor in [("geometry_linear_scaling",scaled,2.),("permittivity_linear_scaling",dielectric,4.)]:
            ratio=report["raw_matrix_F"][0][0]/cube_c if report["converged"] else None
            result["checks"][name]={"ratio":ratio,"expected":factor,
                "passed":bool(ratio is not None and abs(ratio/factor-1)<1e-9)}
        plates=run_capacitance_mesh_study(build_field_snapshot(plate_project(),kind="capacitance"),
            args.executable,args.output/"plates",[.001,.0005,.00025],direct=True,timeout=args.timeout,max_panels=2000)
        result["reports"]["plates"]=plates
        result["checks"]["plates_mesh_verified"]=plates["mesh_verified"]
        result["static_interchange"]={"cube_accepted":study["accepted"],"plates_accepted":plates["accepted"],
            "meaning":"Raw field/mesh checks do not authorize a repaired or approximate static operator. Rejected interchange remains rejected."}
        final=plates["network"]
        if final is not None:
            matrix=np.asarray(final["raw_matrix_F"])
            ideal=final["physical_constants"]["homogeneous_permittivity_F_per_m"]*1e-6/50e-6
            ratio=-matrix[0,1]/ideal
            energy=float(.5*np.array([.5,-.5]) @ matrix @ np.array([.5,-.5]))
            result["checks"]["parallel_plate_limit"]={"reference_F":ideal,"mutual_F":-matrix[0,1],
                "ratio":ratio,"passed":bool(1.<ratio<1.25),
                "meaning":"Finite 20:1 width/gap plates include fringing. This 25% asymptotic-limit check is not a mesh tolerance or exact finite-plate reference."}
            result["checks"]["stored_differential_energy"]={"energy_J":energy,"voltage_V":[.5,-.5],"passed":energy>0}
        coax=run_capacitance_mesh_study(build_field_snapshot(coax_project(),kind="capacitance"),
            args.executable,args.output/"coax",[.001,.0005,.00025],timeout=args.timeout)
        result["reports"]["coax"]=coax
        result["checks"]["coax_mesh_verified"]=coax["mesh_verified"]
        result["static_interchange"]["coax_accepted"]=coax["accepted"]
        if coax["network"] is not None:
            final=coax["network"]
            matrix=np.asarray(final["raw_matrix_F"])
            ideal=2*math.pi*final["physical_constants"]["homogeneous_permittivity_F_per_m"]*.002/math.log(2)
            ratio=-matrix[0,1]/ideal
            result["checks"]["coax_long_circular_limit"]={"reference_F":ideal,"mutual_F":-matrix[0,1],
                "ratio":ratio,"passed":bool(1.<ratio<1.1),
                "meaning":"Finite 2 mm long regular-16-gon coax, radii 50/100 micrometers. End fringing and polygonal boundaries differ from the infinite circular analytic limit; this is not a mesh tolerance."}
        result["passed"]=all(value if isinstance(value,bool) else value["passed"] for value in result["checks"].values())
    except Exception as error:
        result["passed"]=False
        result["error"]=type(error).__name__+": "+str(error)
    result["seconds_total"]=time.perf_counter()-started
    (args.output/"benchmark.json").write_text(json.dumps(result,indent=2,allow_nan=False)+"\n",encoding="utf-8")
    print(json.dumps({"passed":result["passed"],"checks":result["checks"],"seconds_total":result["seconds_total"]},indent=2))
    return 0 if result["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
