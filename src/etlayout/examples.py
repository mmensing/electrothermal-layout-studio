# SPDX-License-Identifier: MPL-2.0
"""Reproducible real GDSII/OASIS examples, with ready-to-run physical projects.

Geometry builder coordinates are micrometers; public project coordinates are SI.
Values are illustrative engineering configurations, not calibrated devices.
"""
from __future__ import annotations

import json
from copy import deepcopy
from pathlib import Path

import klayout.db as kdb

from etlayout.example_catalog import EXAMPLE_CATALOG, example_info
from etlayout.io.project import file_hash, save_project
from etlayout.materials.database import reference_materials
from etlayout.models import (
    ElectricalModel,
    Interface,
    LayoutSource,
    PhysicalLayer,
    Project,
    PropertyModel,
    RegionSpec,
    SweepSpec,
    Terminal,
    ThermalBoundary,
)

UM = 1e-6

EXAMPLE_NAMES = list(EXAMPLE_CATALOG)
# Explicit registration: catalog order never decides which physical builder runs.
EXAMPLE_BUILDERS = {
    **{name: ("etlayout.examples", "_build_intro") for name in (
        "E1_straight", "E2_bent_taper", "E3_parallel", "E4_multilayer", "E5_via_array",
        "E6_kelvin", "E7_hemt", "E8_hemt_routing", "E9_sic", "E10_thermal_stack")},
    **{name: ("etlayout.advanced_interconnects", "build_interconnect_example") for name in (
        "E11_serpentine_heater", "E12_via_ladder", "E13_perforated_spreader")},
    **{name: ("etlayout.advanced_devices", "build_device_example") for name in (
        "E14_bridge_sensor", "E15_thermal_crosstalk", "E16_full_gate_hemt_array")},
    **{name: ("etlayout.pulsed_examples", "build_pulsed_example") for name in (
        "E17_pulsed_thermal", "E18_pulsed_conductor", "E19_pulsed_multilayer")},
    **{name: ("etlayout.power_examples", "build_power_example") for name in (
        "E20_high_current_rdl", "E21_long_finger_gan", "E22_multibank_gan",
        "E23_sic_stripes", "E24_sic_cells")},
    **{name: ("etlayout.teaching_examples", "build_teaching_example") for name in (
        "E25_stepped_width_series", "E26_dogbone_coupon", "E27_transfer_length_coupon", "E28_t_junction_splitter",
        "E29_annular_bus", "E30_via_daisy_chain", "E31_resistor_ladder", "E32_parallel_resistor_array")},
    **{name: ("etlayout.device_examples", "build_device_example") for name in (
        "E33_suspended_bridge_heater", "E34_interleaved_heater_sensor", "E35_localized_die_attach", "E36_pulsed_hotspot_neck",
        "E37_gated_hemt_coupon", "E38_dual_bank_gan", "E39_sic_island_die", "E40_package_interposer")},
}
# Validation tiers are explicit catalog policy; moving a builder module does not
# accidentally add engineering/transient cases to the three-mode teaching loop.
ROUTINE_EXAMPLES = tuple(name for name, info in EXAMPLE_CATALOG.items() if info.reference_tier == "introductory")
EXTENDED_EXAMPLES = tuple(name for name, info in EXAMPLE_CATALOG.items() if info.reference_tier == "extended")



def rectangle(x0, y0, x1, y1) -> RegionSpec:
    return polygon([(x0, y0), (x1, y0), (x1, y1), (x0, y1)])


def polygon(points, holes=None) -> RegionSpec:
    return RegionSpec(polygons=[{"exterior": [[x*UM, y*UM] for x, y in points],
                                 "holes": [[[x*UM, y*UM] for x, y in ring] for ring in (holes or [])]}])


def edge(x0, y0, x1, y1) -> RegionSpec:
    return RegionSpec(line=[[x0*UM, y0*UM], [x1*UM, y1*UM]])


def _metal(layer_id="metal", mask="1/0", z=0., thickness=2e-6, name=None):
    return PhysicalLayer(layer_id, name or layer_id.replace("_", " ").title(), "copper", thickness, z,
                         geometry=RegionSpec(sources=[mask]))


def _terminal(name, layer, region, kind, value=0.):
    return Terminal(name, layer, region, kind, value, name.title())


class LayoutBuilder:
    def __init__(self, name):
        self.layout = kdb.Layout()
        self.layout.dbu = .001
        self.cell = self.layout.create_cell(name)

    def add(self, mask: int, points, cell=None, holes=None):
        shape = kdb.DPolygon([kdb.DPoint(float(x), float(y)) for x, y in points])
        for ring in holes or []:
            shape.insert_hole([kdb.DPoint(float(x), float(y)) for x, y in ring])
        (cell or self.cell).shapes(self.layout.layer(mask, 0)).insert(shape)

    def rect(self, mask, x0, y0, x1, y1, cell=None):
        self.add(mask, [(x0,y0),(x1,y0),(x1,y1),(x0,y1)], cell)

    def write(self, path):
        options = kdb.SaveLayoutOptions()
        options.set_format_from_filename(str(path))
        options.gds2_write_timestamps = False
        self.layout.write(str(path), options)


def make_example(name: str, directory: str | Path, *, preset=None, variant=None, current=None, thermal=None, pulse=False,
                 fingers=None, gate_width_um=None, stress=False) -> Project:
    name = example_info(name).name
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    builder = LayoutBuilder(name)
    p = Project(name=name, materials=reference_materials())
    p.mesh.max_size = 7e-6
    p.mesh.min_size = .5e-6
    p.mesh.contact_size = 2e-6
    p.solver.max_iterations = 150
    p.solver.relative_tolerance = 1e-5
    p.solver.absolute_tolerance = 1e-5
    p.solver.relaxation = .6
    p.layers = [_metal()]
    import importlib
    module, function = EXAMPLE_BUILDERS[name]
    build = getattr(importlib.import_module(module), function)
    if module == "etlayout.power_examples":
        build(name, builder, p, preset=preset, variant=variant, current=current, thermal=thermal, pulse=pulse,
              fingers=fingers, gate_width_um=gate_width_um, stress=stress)
    else:
        if pulse or stress or any(value is not None for value in (preset, variant, current, thermal, fingers, gate_width_um)):
            raise ValueError("Preset/variant/current/thermal options require a power-device family (E20–E24)")
        if build(name, builder, p) is False:
            raise ValueError(f"Example has no builder: {name}")
        p.solver.mode = example_info(name).recommended_mode
    if not p.thermal_boundaries:
        p.thermal_boundaries = [ThermalBoundary("sink_" + x.id, x.id, deepcopy(x.geometry), kind="coefficient", value=2e6)
                                for x in p.layers]
    if not p.sweeps:
        p.sweeps = [SweepSpec("terminals.drain.value", [.01,.02,.04], "electrothermal", "Bias sweep")]
    source_path = directory / (name + ".gds")
    builder.write(source_path)
    builder.write(directory / (name + ".oas"))
    p.layout = LayoutSource(str(source_path.resolve()), name, file_hash(source_path), .001)
    for recipe in p.power.recipes:
        recipe.source_hash = p.layout.sha256
    save_project(p, directory / (name + ".etl"))
    return p


def _build_intro(name, builder, p):
    if name.startswith("E1_"):
        builder.rect(1,0,0,100,10)
        p.terminals = [_terminal("source", "metal", edge(0,0,0,10), "ground"),
                       _terminal("drain", "metal", edge(100,0,100,10), "voltage", .02)]
        p.notes = "Analytical R = rho L/(w t) = 0.084 ohm at 300 K; L=100 µm, w=10 µm, t=2 µm."
    elif name.startswith("E2_"):
        builder.add(1, [(0,0),(100,0),(100,60),(80,60),(80,12),(0,20)])
        p.terminals = [_terminal("source", "metal", edge(0,0,0,20), "ground"),
                       _terminal("drain", "metal", edge(80,60,100,60), "current", .1)]
        p.notes = "Bent, tapered conductor; current crowding near the reentrant corner is mesh-sensitive."
    elif name.startswith("E3_"):
        for box in [(0,0,10,50),(90,0,100,50),(0,0,100,10),(0,30,100,50)]:
            builder.rect(1,*box)
        p.terminals = [_terminal("source", "metal", edge(0,0,0,50), "ground"),
                       _terminal("drain", "metal", edge(100,0,100,50), "current", .3)]
        p.notes = "Two parallel routes with 10 and 20 µm widths, including distributed access resistance."
    elif name.startswith("E4_"):
        builder.rect(1,0,0,80,20)
        builder.add(2,[(60,0),(80,0),(80,60),(120,60),(120,80),(60,80)])
        builder.rect(10,60,0,80,20)
        p.layers = [_metal("m1", "1/0", 0), _metal("m2", "2/0", 4e-6)]
        p.layers[1].color = "#BB8745"
        p.interfaces = [Interface("via", "m1", "m2", RegionSpec(sources=["10/0"]),
                                  "via", material_id="copper", height=2e-6,
                                  thermal_kind="conductance", thermal_value=2e8)]
        p.terminals = [_terminal("source", "m1", edge(0,0,0,20), "ground"),
                       _terminal("drain", "m2", edge(120,60,120,80), "current", .2)]
        p.notes = "Nonrectangular two-metal structure, finite copper via and separate layer potentials."
    elif name.startswith("E5_"):
        builder.rect(1,0,0,200,200)
        builder.rect(2,0,0,200,200)
        via_cell = builder.layout.create_cell("VIA_UNIT")
        builder.rect(10,0,0,6,6,via_cell)
        builder.cell.insert(kdb.DCellInstArray(via_cell.cell_index(), kdb.DTrans(110., 30.),
                                             kdb.DVector(18, 0), kdb.DVector(0, 35), 4, 5))
        p.layers = [_metal("m1", "1/0", 0,5e-6), _metal("m2", "2/0", 10e-6,5e-6)]
        p.interfaces = [Interface("array", "m1", "m2", RegionSpec(sources=["10/0"]),
                                  "via", material_id="copper", height=5e-6,
                                  thermal_kind="conductance", thermal_value=8e7)]
        p.terminals = [_terminal("source", "m1", edge(0,0,0,200), "ground"),
                       _terminal("drain", "m2", edge(200,0,200,200), "current", 2.)]
        p.mesh.max_size = 20e-6
        p.mesh.contact_size = 2e-6
        p.notes = "Hierarchical 4 × 5 array of finite vias; nonuniform transfer current across a 200 µm pad."
    elif name.startswith("E6_"):
        builder.rect(1,0,0,120,20)
        builder.rect(2,0,0,20,20)
        builder.rect(2,100,0,120,20)
        builder.rect(10,0,0,20,20)
        builder.rect(10,100,0,120,20)
        p.layers = [PhysicalLayer("film", "Resistive film", "silicon", 1e-6, 0,
                                  geometry=RegionSpec(sources=["1/0"]),
                                  electrical=ElectricalModel("sheet", PropertyModel(value=100.))),
                    _metal("pads", "2/0", 1e-6)]
        p.interfaces = [Interface("ohmic", "film", "pads", RegionSpec(sources=["10/0"]), "specific", 1e-9,
                                  thermal_kind="conductance", thermal_value=1e7)]
        p.terminals = [_terminal("source", "pads", rectangle(0,0,20,20), "ground"),
                       _terminal("drain", "pads", rectangle(100,0,120,20), "current", .001),
                       _terminal("sense_a", "film", edge(30,0,30,20), "floating"),
                       _terminal("sense_b", "film", edge(90,0,90,20), "floating")]
        p.notes = "Four-terminal sheet/contact test; floating sense terminals draw exactly zero net current."
    elif name.startswith(("E7_", "E8_")):
        routing = name.startswith("E8_")
        builder.rect(1,0,0,100,100)  # channel
        # Interdigitated fingers alternate source/drain; buses lie beyond the channel.
        for y in (10, 50, 90):
            builder.rect(2,-10,y-3,85,y+3)
            builder.rect(10,0,y-3,85,y+3)
        for y in (30, 70):
            builder.rect(3,15,y-3,110,y+3)
            builder.rect(11,15,y-3,100,y+3)
        builder.rect(2,-10,7,-4 if routing else 0,93)
        builder.rect(3,104 if routing else 100,27,110,73)
        for y in (20, 40, 60, 80):
            builder.rect(4,5,y-1,95,y+1)
        builder.rect(4,3,19,5,81)
        # Insulated field plate over one access region, explicit separate layer.
        builder.rect(5,10,21,90,24)
        p.layers = [PhysicalLayer("channel", "GaN 2DEG", "2deg", 10e-9, 0,
                                  geometry=RegionSpec(sources=["1/0"]),
                                  electrical=ElectricalModel(kind="hemt", mobility=.12, mobility_exponent=1.5,
                                      gate_voltage=0., threshold_voltage=-3., gate_coupling=1.5e16,
                                      smoothing_voltage=.12, background_sheet_density=1e15,
                                      gate_regions=["gate_control"])),
                    _metal("source_metal", "2/0", .1e-6, .7e-6 if routing else 2e-6),
                    _metal("drain_metal", "3/0", .1e-6, .7e-6 if routing else 2e-6),
                    _metal("gate_metal", "4/0", 2.4e-6, .15e-6),
                    _metal("field_plate", "5/0", 3e-6, .3e-6),
                    PhysicalLayer("substrate", "SiC substrate", "sic", 100e-6, -100e-6, "thermal",
                                  RegionSpec(full_domain=True), opacity=.2)]
        p.layers[0].color = "#6689AC"
        p.interfaces = [Interface("source_contact", "channel", "source_metal", RegionSpec(sources=["10/0"]),
                                  "specific", 1e-10, thermal_kind="conductance", thermal_value=1e7),
                        Interface("drain_contact", "channel", "drain_metal", RegionSpec(sources=["11/0"]),
                                  "specific", 1e-10, thermal_kind="conductance", thermal_value=1e7)]
        for layer in p.layers[:-1]:
            p.interfaces.append(Interface("heat_"+layer.id, layer.id, "substrate", deepcopy(layer.geometry),
                                           thermal_kind="resistance", thermal_value=2e-7))
        p.terminals = [_terminal("source", "source_metal", edge(-10,7,-10,93), "ground"),
                       _terminal("drain", "drain_metal", edge(110,27,110,73), "voltage", .2),
                       _terminal("gate_bias", "gate_metal", rectangle(3,19,5,81), "voltage", 0.),
                       _terminal("field_plate_bias", "field_plate", edge(10,21,10,24), "voltage", 0.),
                       _terminal("gate_control", "channel", RegionSpec(sources=["4/0"]), "gate", 0.)]
        p.thermal_boundaries = [ThermalBoundary("sink", "substrate", kind="coefficient", value=7.4e6)]
        p.mesh.max_size = 8e-6
        p.mesh.min_size = .25e-6
        p.mesh.contact_size = 1.5e-6
        p.sweeps = [SweepSpec("terminals.gate_control.value", [-5.,-3.,-1.,1.], "electrothermal", "Gate control")]
        p.notes = "Illustrative depletion-mode interdigitated HEMT. Local gate control only under gate mask; " \
                  "access channel uses the configured ungated carrier-density cap. Field plate is insulated and " \
                  "has no electrostatic effect. Ungated channel edges permit bypass current. Metal/channel " \
                  "to substrate thermal links use an effective 2e-7 m²K/W for omitted buffer/dielectric paths; " \
                  "these parallel heat paths require process calibration. Not a calibrated transistor model."
    elif name.startswith("E9_"):
        builder.rect(1,0,0,100,30)
        builder.rect(2,0,0,10,30)
        builder.rect(2,90,0,100,30)
        builder.rect(10,0,0,10,30)
        builder.rect(10,90,0,100,30)
        p.layers = [PhysicalLayer("sic", "4H-SiC conducting sheet", "sic", 2e-6, 0,
                                  geometry=RegionSpec(sources=["1/0"]),
                                  electrical=ElectricalModel(kind="semiconductor", carrier_density=1e24,
                                                             mobility=.04, mobility_exponent=2.)),
                    _metal("pads", "2/0", 2e-6)]
        p.interfaces = [Interface("ohmic", "sic", "pads", RegionSpec(sources=["10/0"]), "specific", 1e-9,
                                  thermal_kind="conductance", thermal_value=1e7)]
        p.terminals = [_terminal("source", "pads", rectangle(0,0,10,30), "ground"),
                       _terminal("drain", "pads", rectangle(90,0,100,30), "voltage", 1.)]
        p.notes = "Effective n=10^18 cm^-3, mobility=400 cm²/Vs at 300 K; resistive sheet, no junction physics."
    elif name.startswith("E10_"):
        builder.rect(1,0,45,200,55)
        builder.rect(20,-10,-10,210,110)
        p.layers = [_metal("metal", "1/0", 1e-6),
                    PhysicalLayer("oxide", "SiO₂ dielectric", "sio2", 1e-6, 0, "insulator",
                                  RegionSpec(sources=["20/0"]), opacity=.15),
                    PhysicalLayer("substrate", "Silicon substrate", "silicon", 50e-6, -50e-6, "thermal",
                                  RegionSpec(sources=["20/0"]), opacity=.25)]
        p.interfaces = [Interface("metal_oxide", "metal", "oxide", RegionSpec(sources=["1/0", "20/0"], operation="intersection"),
                                  thermal_kind="stack", thermal_resistance_area=1e-8),
                        Interface("oxide_substrate", "oxide", "substrate", RegionSpec(sources=["20/0"]),
                                  thermal_kind="stack", thermal_resistance_area=1e-8)]
        p.terminals = [_terminal("source", "metal", edge(0,45,0,55), "ground"),
                       _terminal("drain", "metal", edge(200,45,200,55), "current", .4)]
        p.thermal_boundaries = [ThermalBoundary("sink", "substrate", RegionSpec(sources=["20/0"]), kind="coefficient", value=148./25e-6),
                                ThermalBoundary("top_air", "metal", RegionSpec(sources=["1/0"]), kind="coefficient", value=10.)]
        p.mesh.max_size = 12e-6
        p.notes = "Thickness-averaged metal/oxide/silicon model. Bottom boundary includes half substrate thickness."


def generate_all(directory: str | Path) -> list[Project]:
    from etlayout.example_catalog import example_info
    projects = [make_example(name, directory) for name in EXAMPLE_NAMES]
    manifest = [{"name": p.name, "project": p.name + ".etl", "gds": p.name + ".gds",
                 "oasis": p.name + ".oas", "description": p.notes,
                 "title": example_info(p.name).title, "category": example_info(p.name).category,
                 "learning_goal": example_info(p.name).learning_goal,
                 "source_sha256": p.layout.sha256,
                 "level": example_info(p.name).level, "reference_tier": example_info(p.name).reference_tier,
                 "physical_scope": example_info(p.name).physical_scope,
                 "evidence_level": example_info(p.name).evidence_level} for p in projects]
    Path(directory, "index.json").write_text(json.dumps(manifest, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return projects


def large_example(directory: str | Path) -> Project:
    """Millimeter-scale serpentine/parallel structure without globally fine meshing."""
    p = make_example("E3", directory)
    builder = LayoutBuilder("millimeter_parallel")
    builder.rect(1,0,0,30,1000)
    builder.rect(1,970,0,1000,1000)
    for y in range(0,981,70):
        builder.rect(1,0,y,1000,y+20)
    source_path = Path(directory) / "millimeter_parallel.gds"
    builder.write(source_path)
    p.name = "1 mm parallel interconnect"
    p.layout = LayoutSource(str(source_path.resolve()), "millimeter_parallel", file_hash(source_path))
    p.terminals = [_terminal("source", "metal", edge(0,0,0,1000), "ground"),
                   _terminal("drain", "metal", edge(1000,0,1000,1000), "current", 2.)]
    p.mesh.max_size = 80e-6
    p.mesh.min_size = 2e-6
    p.mesh.contact_size = 15e-6
    return p
