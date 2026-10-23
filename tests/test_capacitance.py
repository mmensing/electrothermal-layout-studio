# SPDX-License-Identifier: MPL-2.0
"""Generated geometry, unrepaired raw C, strict acceptance and failed jobs.

Real external-engine benchmarks run through scripts/benchmark_field_capacitance.py;
these tests do not substitute a fake successful process for scientific evidence.
"""
import hashlib
import json
import sys
from dataclasses import replace

import numpy as np
import pytest

from etlayout.examples import rectangle
from etlayout.models import Cancelled, Material, PhysicalLayer, Project, SimulationError
from etlayout.power import capacitance
from etlayout.power.field_snapshot import build_field_snapshot, triangulate_conductors


@pytest.fixture
def plate_snapshot():
    project = Project(materials={"metal": Material("metal", "Synthetic conductor")}, layers=[
        PhysicalLayer("low", "Lower", "metal", thickness=1e-6, geometry=rectangle(0,0,10,10)),
        PhysicalLayer("high", "Upper", "metal", thickness=1e-6, z=3e-6, geometry=rectangle(0,0,10,10))])
    project.parasitics = {"schema_version": 1, "background_relative_permittivity": 2.}
    return build_field_snapshot(project, kind="capacitance")


def raw_output(matrix):
    rows = "\n".join(f"c{i:04d}%GROUP1 " + " ".join(format(v,".17g") for v in row)
                     for i,row in enumerate(matrix))
    return ("Native solver banner\nETLAYOUT_FASTCAP_EPSILON0_F_PER_M 8.854187818e-12\n"
            f"ETLAYOUT_RAW_MAXWELL_BEGIN {len(matrix)} F\n{rows}\nETLAYOUT_RAW_MAXWELL_END\n"
            "CAPACITANCE MATRIX, picofarads\nThis averaged table is deliberately ignored.\n").encode()


def test_panel_input_is_deterministic_si_and_keeps_every_whole_conductor(plate_snapshot):
    mesh = triangulate_conductors(plate_snapshot, 20e-6)
    first, manifest = capacitance.compile_capacitance_input(plate_snapshot, mesh)
    assert (first, manifest) == capacitance.compile_capacitance_input(plate_snapshot, mesh)
    assert first.startswith(b"0 ") and len(manifest["conductors"]) == 2
    assert manifest["reference"] == {"id":"environment", "kind":"infinity", "potential_V":0.}
    assert manifest["deck_sha256"] == hashlib.sha256(first).hexdigest()
    assert manifest["relative_permittivity"] == 2.
    points = []
    names = set()
    for line in first.decode().splitlines()[1:]:
        fields = line.split()
        assert fields[0] == "T" and len(fields) == 11
        names.add(fields[1])
        points.extend(np.asarray(fields[2:],dtype=float).reshape(3,3))
    assert names == {"c0000", "c0001"}
    np.testing.assert_allclose(np.ptp(points,axis=0),[10e-6,10e-6,4e-6],rtol=1e-14,atol=0)
    assert manifest["panels"] == len(first.splitlines()) - 1
    assert manifest["source_context"] == plate_snapshot.source_context


@pytest.mark.parametrize("damage", ["kind", "geometry", "axes", "invalid_panels"])
def test_input_cannot_silently_change_source_or_conductor_basis(plate_snapshot, damage):
    mesh = triangulate_conductors(plate_snapshot,20e-6)
    if damage == "kind":
        plate_snapshot = replace(plate_snapshot,kind="loop_rl")
    elif damage == "geometry":
        mesh = replace(mesh,geometry_sha256="different")
    elif damage == "axes":
        mesh = replace(mesh,surfaces=mesh.surfaces[::-1])
    else:
        mesh = replace(mesh,surfaces=(replace(mesh.surfaces[0],triangles=((0,0,999),)),*mesh.surfaces[1:]))
    with pytest.raises(SimulationError):
        capacitance.compile_capacitance_input(plate_snapshot,mesh)


def test_original_raw_operator_has_exact_basis_units_signs_weak_entries_and_no_repair(plate_snapshot):
    matrix = [[2e-12,-1e-27],[-2e-27,3e-12]]
    parsed = capacitance.parse_fastcap_output(b"Path: \xc2\xb5\n" + raw_output(matrix),["c0000","c0001"])
    np.testing.assert_array_equal(parsed["matrix_F"],matrix)
    assert parsed["vacuum_permittivity_F_per_m"] == 8.854187818e-12
    assert parsed["output_precision_significant_digits"] == 17
    mesh = triangulate_conductors(plate_snapshot,20e-6)
    broken = capacitance.parse_fastcap_output(raw_output([[2e-12,-1e-12],[-.999e-12,2e-12]]),["c0000","c0001"])
    with pytest.raises(ValueError,match="reciprocal"):
        capacitance._operator(plate_snapshot,mesh,broken,"a"*64)
    symmetric = capacitance.parse_fastcap_output(raw_output([[2e-12,-1e-12],[-1e-12,2e-12]]),["c0000","c0001"])
    operator = capacitance._operator(plate_snapshot,mesh,symmetric,"a"*64)
    assert operator.energy_J([1.,-1.]) == pytest.approx(3e-12)
    assert operator.materials[0].value_SI == 2*8.854187818e-12
    quality = capacitance._quality(parsed["matrix_F"])
    assert quality["raw_matrix_preserved"] and quality["symmetry_repair"] is False
    assert quality["maximum_scaled_reciprocity_error"] > 0


@pytest.mark.parametrize("damage", ["averaged_only", "duplicate", "truncated", "units", "dimension", "axis",
                                    "nonfinite", "complex", "extra", "nonconverged", "constant"])
def test_invalid_or_averaged_native_results_are_not_accepted(damage):
    original = raw_output([[2e-12,-1e-12],[-1e-12,2e-12]])
    changes = {"units":(b"BEGIN 2 F",b"BEGIN 2 pF"), "dimension":(b"BEGIN 2 F",b"BEGIN 3 F"),
               "axis":(b"c0001%GROUP1",b"wrong"), "nonfinite":(b"2e-12",b"1e999"),
               "complex":(b"2e-12",b"2e-12j"), "extra":(b"END\n",b"END extra\n"),
               "constant":(b"8.854187818e-12",b"1e-9")}
    if damage == "averaged_only":
        original = b"CAPACITANCE MATRIX, picofarads\n1 2 3\n"
    elif damage == "duplicate":
        original += original
    elif damage == "truncated":
        original = original.replace(b"c0001%GROUP1 -9.9999999999999998e-13 2e-12\n",b"")
        original = original[:original.index(b"ETLAYOUT_RAW_MAXWELL_END")]
    elif damage == "nonconverged":
        original += b"NONCONVERGENCE AFTER 100 ITERATIONS\n"
    else:
        original = original.replace(*changes[damage])
    with pytest.raises(SimulationError):
        capacitance.parse_fastcap_output(original,["c0000","c0001"])


@pytest.mark.parametrize("status", ["execution_failed", "timeout", "output_limit", "memory_limit"])
def test_real_job_failure_evidence_cannot_claim_field_completion(tmp_path, plate_snapshot, monkeypatch, status):
    calls=[]
    def failure(executable,args,directory,**settings):
        calls.append((args,settings))
        return {"status":status,"output":b"Actual diagnostic from failed job","returncode":1,"seconds":.01}
    monkeypatch.setattr(capacitance,"run_external_tool",failure)
    report = capacitance.extract_capacitance(plate_snapshot,sys.executable,tmp_path/"failure",
                                            mesh_size_m=20e-6,direct=True)
    assert report["status"] == status and report["raw_matrix_F"] is None
    assert not report["converged"] and not report["accepted"] and not report["mesh_verified"]
    assert calls[0][0] == ["geometry.qui","-p2","-o6","-t1e-10","-d0"]
    assert calls[0][1]["memory_limit_bytes"] == 1024**3
    assert json.loads((tmp_path/"failure/capacitance.json").read_bytes()) == report


def test_cancellation_existing_directory_and_direct_budget_prevent_execution(tmp_path,plate_snapshot,monkeypatch):
    monkeypatch.setattr(capacitance,"run_external_tool",lambda *_a,**_k:pytest.fail("Must not execute"))
    with pytest.raises(Cancelled):
        capacitance.extract_capacitance(plate_snapshot,sys.executable,tmp_path/"cancel",
                                       mesh_size_m=20e-6,cancel=lambda:True)
    assert not (tmp_path/"cancel").exists()
    occupied=tmp_path/"occupied"
    occupied.mkdir()
    (occupied/"geometry.qui").write_text("Existing input is never reused")
    with pytest.raises(SimulationError,match="new or empty"):
        capacitance.extract_capacitance(plate_snapshot,sys.executable,occupied,mesh_size_m=20e-6)
    with pytest.raises(SimulationError,match="2000"):
        capacitance.extract_capacitance(plate_snapshot,sys.executable,tmp_path/"budget",mesh_size_m=2e-6,direct=True)
    assert not (tmp_path/"budget").exists()


@pytest.mark.parametrize("sizes", [[1.,.5],[1.,.5,.5],[1.,.5,float("nan")],[True,.5,.25],range(100)])
def test_invalid_mesh_sequences_are_bounded_and_cannot_start_a_job(tmp_path,plate_snapshot,sizes):
    with pytest.raises(SimulationError,match="3 to 5"):
        capacitance.run_capacitance_mesh_study(plate_snapshot,sys.executable,tmp_path/"bad",sizes)
    assert not (tmp_path/"bad").exists()
