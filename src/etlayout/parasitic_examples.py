# SPDX-License-Identifier: MPL-2.0
"""Small deterministic 3D field references, separate from conduction examples.

Generation writes layouts and a portable project; it never starts a field solver.
"""
from __future__ import annotations

from pathlib import Path

from shapely.geometry import box
from shapely.ops import unary_union

from etlayout.examples import LayoutBuilder
from etlayout.io.project import file_hash, save_project
from etlayout.models import LayoutSource, Material, PhysicalLayer, Project, PropertyModel, RegionSpec
from etlayout.power.field_snapshot import MU_0


def rectangular_loop(identifier, x0, y0, x1, y1, z, width, height, support):
    """SI centerline rectangle with an explicit physical return and oriented cut."""
    if x1 <= x0 or y1 <= y0 or min(width, height) <= 0 or min(x1-x0, y1-y0) <= width:
        raise ValueError("Loop dimensions must be positive with centerline extents greater than the bar width")
    points = [(x0,y0,z), (x1,y0,z), (x1,y1,z), (x0,y1,z)]
    return {"id": identifier, "segments": [
        {"start_m": list(a), "end_m": list(b), "width_m": width, "height_m": height,
         "width_direction": [0,1,0] if a[0] != b[0] else [1,0,0], "support_ids": [support]}
        for a,b in zip(points, points[1:]+points[:1])]}


def validate_loop_entries(loops):
    """Use the shared pure path validator; source-volume checks stay in workers."""
    from etlayout.models import SimulationError
    from etlayout.power.field_snapshot import normalize_loop_entries
    try:
        return normalize_loop_entries(loops, allow_empty=True)
    except SimulationError as error:
        raise ValueError(str(error)) from error


def make_field_reference(kind, directory):
    """Create a ready-to-open reference with no precomputed numerical claims."""
    if kind not in {"capacitance", "loop_rl"}:
        raise ValueError("Field reference must be capacitance or loop_rl")
    directory = Path(directory).expanduser().absolute()
    if directory.exists() and (not directory.is_dir() or any(directory.iterdir())):
        raise ValueError("Choose a new or empty reference directory")
    directory.mkdir(parents=True, exist_ok=True)
    name = "field_plates" if kind == "capacitance" else "field_closed_return"
    builder = LayoutBuilder(name)
    copper = Material("copper", "Synthetic field-reference copper",
        resistivity=PropertyModel(value=1.724e-8), permeability=PropertyModel(value=MU_0),
        source="Synthetic 300 K bulk resistivity, not a calibrated process",
        notes="Uniform bulk material; no contact/skin correction is hidden in resistivity.")
    project = Project(name=name, materials={"copper": copper})
    project.parasitics = {"schema_version": 1, "background_relative_permittivity": 1.,
        "background_relative_permeability": 1., "material_sources": {
            "copper": "Explicit synthetic nonmagnetic assumption: relative permeability 1; uniform vacuum environment."}}
    if kind == "capacitance":
        builder.rect(1, 0, 0, 200, 200)
        builder.rect(2, 0, 0, 200, 200)
        project.layers = [PhysicalLayer("lower", "Lower finite plate", "copper", 5e-6, 0.,
            geometry=RegionSpec(sources=["1/0"])),
            PhysicalLayer("upper", "Upper finite plate", "copper", 5e-6, 25e-6,
            geometry=RegionSpec(sources=["2/0"]))]
        project.notes = ("Two finite 200 x 200 x 5 micrometer PEC plates; 20 micrometer face gap in vacuum. "
            "Every conductor is an independent voltage axis, with infinity fixed at zero. Fringing is part of the 3D field model. "
            "This field reference has no conduction source and is run from Parasitics, not the DC Run page.")
    else:
        loop = rectangular_loop("closed_return", 0., 0., 1e-3, .5e-3, 5e-6, 50e-6, 10e-6, "layer:loop")
        project.parasitics["loops"] = [loop]
        bars = []
        for segment in loop["segments"]:
            a, b, w = segment["start_m"], segment["end_m"], segment["width_m"]
            bars.append(box(min(a[0],b[0]),a[1]-w/2,max(a[0],b[0]),a[1]+w/2) if a[0] != b[0]
                        else box(a[0]-w/2,min(a[1],b[1]),a[0]+w/2,max(a[1],b[1])))
        shape = unary_union(bars)
        builder.add(1, [(x*1e6,y*1e6) for x,y in shape.exterior.coords[:-1]],
                    holes=[[(x*1e6,y*1e6) for x,y in ring.coords[:-1]] for ring in shape.interiors])
        project.layers = [PhysicalLayer("loop", "Finite forward and return bars", "copper", 10e-6, 0.,
                                       geometry=RegionSpec(sources=["1/0"]))]
        project.notes = ("Closed 1 x 0.5 mm centerline copper return, 50 x 10 micrometer rectangular bars. "
            "GDS is the union of the actual bars. FastHenry uses ideal bar-end junctions at corners, an explicit junction approximation. "
            "DC establishes bar resistance; positive frequencies establish loop L(f), with no inferred ground return. "
            "Run from Parasitics. This is not an automatically reduced arbitrary sheet network.")
    project.mesh.max_size = 50e-6
    project.mesh.min_size = 2e-6
    for extension in ("gds", "oas"):
        builder.write(directory / f"{name}.{extension}")
    source = directory / f"{name}.gds"
    project.layout = LayoutSource(str(source), name, file_hash(source), .001)
    save_project(project, directory / f"{name}.etl")
    return project
