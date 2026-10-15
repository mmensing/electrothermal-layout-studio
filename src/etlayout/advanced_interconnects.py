# SPDX-License-Identifier: MPL-2.0
"""Synthetic multi-turn, multi-level and perforated layout demonstrations.

Builder coordinates are micrometers. Material and boundary values are teaching
assumptions, not calibrated process models; each project states its limitations.
"""
from __future__ import annotations

import klayout.db as kdb

from etlayout.models import (
    HeatSource,
    Interface,
    PhysicalLayer,
    Project,
    RegionSpec,
    SweepSpec,
    ThermalBoundary,
)


def build_interconnect_example(name: str, builder, p: Project) -> bool:
    """Populate an initialized example project, returning whether the ID matches."""
    # The public examples module dispatches here, so keep shared builder imports
    # local to avoid an import cycle when either module is imported directly.
    from etlayout.examples import _metal, _terminal, edge, rectangle

    if name == "E11_serpentine_heater":
        # Five 8 µm wide runs with alternating square U-turns; the gaps are open.
        for row in range(5):
            y = 24 * row
            builder.rect(1, 0, y, 180, y + 8)
            if row < 4:
                x = 172 if row % 2 == 0 else 0
                builder.rect(1, x, y + 8, x + 8, y + 24)
        builder.rect(1, -20, 0, 0, 8)
        builder.rect(1, 180, 96, 200, 104)
        p.layers = [
            PhysicalLayer("heater", "Platinum serpentine", "platinum", .25e-6, 1e-6,
                          geometry=RegionSpec(sources=["1/0"]), color="#B76E45", mesh_size=5e-6),
            PhysicalLayer("substrate", "Silicon heat spreader", "silicon", 50e-6, -50e-6,
                          "thermal", rectangle(-40, -24, 220, 128), color="#6689AC", opacity=.25),
        ]
        p.interfaces = [Interface("heater_substrate", "heater", "substrate", RegionSpec(sources=["1/0"]),
                                  thermal_kind="resistance", thermal_value=2e-6)]
        p.terminals = [_terminal("source", "heater", edge(-20, 0, -20, 8), "ground"),
                       _terminal("drain", "heater", edge(200, 96, 200, 104), "current", .02)]
        p.thermal_boundaries = [ThermalBoundary("backside", "substrate", kind="coefficient", value=2e6)]
        p.heat_sources = [HeatSource("background_heating", "heater", RegionSpec(sources=["1/0"]), "total", .002)]
        p.mesh.max_size = 14e-6
        p.mesh.contact_size = 3e-6
        p.sweeps = [SweepSpec("terminals.drain.value", [.01, .02, .03], "electrothermal", "Heater current"),
                    SweepSpec("interfaces.heater_substrate.thermal_value", [1e-6, 2e-6, 4e-6],
                              "electrothermal", "Effective dielectric thermal resistance")]
        p.notes = (
            "Five-pass 8 µm wide, 0.25 µm thick platinum serpentine, driven at 20 mA. "
            "Square turns demonstrate current crowding; corner peaks require a mesh study. "
            "The explicit 260 × 152 µm silicon substrate extends beyond the imported conductor. "
            "A 2e-6 m²K/W effective dielectric link and a 2e6 W/m²K backside sink are illustrative; "
            "the omitted dielectric has no resolved lateral heat flow. An independent 2 mW heater load "
            "also makes thermal-only mode meaningful. Steady-state thickness-averaged model; "
            "no transient heat capacity, radiation or calibrated thin-film transport."
        )
    elif name == "E12_via_ladder":
        builder.rect(1, 0, 0, 200, 32)
        builder.rect(2, 168, 0, 200, 144)
        builder.rect(3, 0, 112, 200, 144)
        # Two independent array levels remain navigable in the imported hierarchy.
        for mask, prefix, x, y in ((10, "VIA12", 171, 3), (11, "VIA23", 171, 115)):
            unit = builder.layout.create_cell(prefix + "_UNIT")
            builder.rect(mask, 0, 0, 6, 6, unit)
            bank = builder.layout.create_cell(prefix + "_BANK")
            bank.insert(kdb.DCellInstArray(unit.cell_index(), kdb.DTrans(),
                                          kdb.DVector(9, 0), kdb.DVector(0, 9), 3, 3))
            builder.cell.insert(kdb.DCellInstArray(bank.cell_index(), kdb.DTrans(float(x), float(y))))
        p.layers = [_metal("m1", "1/0", 0., 1e-6, "Lower route"),
                    _metal("m2", "2/0", 3e-6, 1e-6, "Middle riser"),
                    _metal("m3", "3/0", 6e-6, 1e-6, "Upper return"),
                    PhysicalLayer("substrate", "Silicon substrate", "silicon", 40e-6, -40e-6,
                                  "thermal", rectangle(-20, -20, 220, 164), color="#6689AC", opacity=.2)]
        p.layers[1].color = "#C79446"
        p.layers[2].color = "#916B9B"
        p.interfaces = [
            Interface("via12", "m1", "m2", RegionSpec(sources=["10/0"]), "via",
                      material_id="copper", height=2e-6, thermal_kind="conductance", thermal_value=2e8),
            Interface("via23", "m2", "m3", RegionSpec(sources=["11/0"]), "via",
                      material_id="copper", height=2e-6, thermal_kind="conductance", thermal_value=2e8),
        ]
        for layer in p.layers[:3]:
            p.interfaces.append(Interface("heat_" + layer.id, layer.id, "substrate", RegionSpec(sources=layer.geometry.sources.copy()),
                                          thermal_kind="resistance", thermal_value=1e-6))
        p.terminals = [_terminal("source", "m1", edge(0, 0, 0, 32), "ground"),
                       _terminal("drain", "m3", edge(0, 112, 0, 144), "current", .3)]
        p.thermal_boundaries = [ThermalBoundary("backside", "substrate", kind="coefficient", value=3e6)]
        p.heat_sources = [HeatSource("riser_heating", "m2", rectangle(168, 48, 200, 96), "total", .003)]
        p.mesh.max_size = 12e-6
        p.mesh.contact_size = 3e-6
        p.sweeps = [SweepSpec("terminals.drain.value", [.1, .3, .5], "electrothermal", "Route current"),
                    SweepSpec("interfaces.via12.height", [1e-6, 2e-6, 4e-6], "electrical", "Lower via height")]
        p.notes = (
            "Three 1 µm copper levels form a U-shaped series route with two hierarchical 3 × 3 banks "
            "of 6 × 6 µm finite copper vias, 2 µm high. A 0.3 A terminal current must cross both banks; "
            "individual vias can carry different currents. Each metal has a separate potential and temperature. "
            "Effective 1e-6 m²K/W links to a larger silicon substrate approximate omitted dielectric paths; "
            "they are parallel thermal paths requiring process calibration. An independent 3 mW load is "
            "applied to the middle riser. Vias are distributed interlayer conductances, not resolved 3D solids. "
            "The via-height sweep changes electrical resistance only; thermal via conductance is independently fixed."
        )
    elif name == "E13_perforated_spreader":
        holes = [
            [(60, 16), (70, 16), (70, 108), (60, 108)],
            [(115, 32), (125, 32), (125, 124), (115, 124)],
            [(170, 16), (180, 16), (180, 108), (170, 108)],
            [(20, 70), (30, 60), (40, 70), (30, 80)],
            [(200, 70), (210, 60), (220, 70), (210, 80)],
        ]
        builder.add(1, [(0, 0), (240, 0), (240, 140), (0, 140)], holes=holes)
        p.layers = [_metal("spreader", "1/0", 0., 2e-6, "Perforated copper sheet"),
                    PhysicalLayer("substrate", "SiC thermal spreader", "sic", 40e-6, -40e-6,
                                  "thermal", rectangle(-20, -20, 260, 160), color="#6689AC", opacity=.25)]
        p.interfaces = [Interface("sheet_substrate", "spreader", "substrate", RegionSpec(sources=["1/0"]),
                                  thermal_kind="resistance", thermal_value=5e-7)]
        p.terminals = [_terminal("source", "spreader", edge(0, 0, 0, 140), "ground"),
                       _terminal("drain", "spreader", edge(240, 0, 240, 140), "current", .75)]
        p.thermal_boundaries = [ThermalBoundary("backside", "substrate", kind="coefficient", value=2e6)]
        p.heat_sources = [HeatSource("local_load", "spreader", rectangle(90, 8, 145, 24), "total", .006)]
        p.mesh.max_size = 10e-6
        p.mesh.contact_size = 3e-6
        p.sweeps = [SweepSpec("terminals.drain.value", [.25, .5, .75], "electrothermal", "Sheet current"),
                    SweepSpec("heat_sources.local_load.value", [.002, .006, .012], "thermal", "Local heating")]
        p.notes = (
            "A 240 × 140 µm, 2 µm thick copper sheet contains three staggered 10 × 92 µm slots and "
            "two diamond cutouts. These are actual holes in the conductor, not decorative masks. "
            "A 0.75 A edge drive diverts current around slot ends; a separate 6 mW localized load exercises "
            "thermal-only mode. The continuous, larger SiC substrate remains beneath the copper holes. "
            "Its effective interface resistance is 5e-7 m²K/W and backside sink is 2e6 W/m²K. "
            "Sharp-corner current peaks are mesh-dependent; use spatial averages or a refinement study. "
            "This steady-state sheet approximation omits 3D sidewall spreading and mechanical stress."
        )
    else:
        return False
    p.solver.resistance_terminals = ["drain", "source"]
    p.solver.resistance_current_terminal = "drain"
    return True
