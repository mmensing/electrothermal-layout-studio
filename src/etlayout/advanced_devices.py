# SPDX-License-Identifier: MPL-2.0
"""Synthetic device examples with explicit electrical and thermal assumptions.

Builder coordinates are micrometres; physical project values use SI. These
examples illustrate the thickness-averaged solver, not calibrated devices.
"""
from __future__ import annotations

from etlayout.models import (
    ElectricalModel,
    HeatSource,
    Interface,
    PhysicalLayer,
    Project,
    PropertyModel,
    RegionSpec,
    SweepSpec,
    ThermalBoundary,
)


def build_device_example(name: str, builder, p: Project) -> bool:
    """Populate a known advanced device using the common example builder."""
    # Local import lets examples.make_example dispatch here without a cycle.
    from etlayout.examples import _metal, _terminal, edge, rectangle

    if name == "E14_bridge_sensor":
        arms = [
            ("arm_tl", "Upper left arm", 1, (0, 40, 60, 48), 100.),
            ("arm_tr", "Upper right arm", 2, (60, 40, 120, 48), 100.),
            ("arm_bl", "Lower left arm", 3, (0, 0, 60, 8), 100.),
            ("arm_br", "Lower right sensing arm", 4, (60, 0, 120, 8), 110.),
        ]
        p.layers = []
        p.interfaces = []
        for identifier, label, mask, box, resistance in arms:
            builder.rect(mask, *box)
            layer = PhysicalLayer(
                identifier, label, "tan", .2e-6, 0., geometry=RegionSpec(sources=[f"{mask}/0"]),
                electrical=ElectricalModel("sheet", PropertyModel(value=resistance)))
            layer.color = "#6D72AE" if identifier == "arm_br" else "#658F8B"
            p.layers.append(layer)
            p.interfaces.extend([
                Interface("contact_" + identifier, identifier, "pads", RegionSpec(sources=["10/0", f"{mask}/0"], operation="intersection"),
                          "ideal", thermal_kind="conductance", thermal_value=2e7),
                Interface("heat_" + identifier, identifier, "substrate", RegionSpec(sources=[f"{mask}/0"]),
                          thermal_kind="resistance", thermal_value=1e-6),
            ])
        # Shared drive buses plus two separate high-impedance midpoint electrodes.
        for box in [(-8, 0, 5, 48), (115, 0, 128, 48), (55, 0, 65, 8), (55, 40, 65, 48)]:
            builder.rect(10, *box)
        p.layers.extend([
            _metal("pads", "10/0", .2e-6, 2e-6, "Bridge electrodes"),
            PhysicalLayer("substrate", "Shared silicon support", "silicon", 30e-6, -30e-6,
                          "thermal", rectangle(-18, -12, 138, 60), opacity=.2),
        ])
        p.interfaces.append(Interface("heat_pads", "pads", "substrate", RegionSpec(sources=["10/0"]),
                                      thermal_kind="resistance", thermal_value=1e-6))
        p.terminals = [
            _terminal("source", "pads", edge(-8, 0, -8, 48), "ground"),
            _terminal("drain", "pads", edge(128, 0, 128, 48), "voltage", .1),
            _terminal("sense_top", "pads", rectangle(55, 40, 65, 48), "floating"),
            _terminal("sense_bottom", "pads", rectangle(55, 0, 65, 8), "floating"),
        ]
        p.solver.resistance_terminals = ["source", "drain"]
        p.solver.resistance_current_terminal = "drain"
        p.thermal_boundaries = [ThermalBoundary("backside", "substrate", value=2e6)]
        p.heat_sources = [HeatSource("sensor_load", "arm_br", RegionSpec(sources=["4/0"]), kind="total", value=.0002)]
        p.mesh.max_size, p.mesh.contact_size = 9e-6, 3e-6
        p.sweeps = [SweepSpec("layers.arm_br.electrical.sheet_resistance.value", [80., 100., 120.],
                             "electrical", "Bridge imbalance (lower-right Ω/□)")]
        p.notes = (
            "Four-arm Wheatstone bridge: three 100 Ω/□ TaN sheets and one 110 Ω/□ sensing arm. "
            "The 0.1 V drive uses finite-conductivity metal buses and ideal arm/pad interfaces. "
            "Floating midpoint electrodes draw zero net current. Read V(sense_top) − V(sense_bottom); "
            "effective resistance instead reports the source/drain drive resistance. Sweep the lower-right "
            "sheet resistance through 100 Ω/□ to reverse the differential output. A separate 0.2 mW "
            "prescribed sensor load also makes thermal-only operation meaningful; fixed sheet resistance "
            "does not model thermoresistive transduction. Synthetic 2.5D demonstration, not a calibrated sensor."
        )
    elif name == "E15_thermal_crosstalk":
        p.layers = []
        p.terminals = []
        p.interfaces = []
        for suffix, mask, y, voltage in [("a", 1, 0, .4), ("b", 2, 42, .25)]:
            # Each three-pass meander is a single connected trace, with separate terminals.
            for box in [(0, y, 100, y+4), (96, y, 100, y+16), (0, y+12, 100, y+16),
                        (0, y+12, 4, y+28), (0, y+24, 100, y+28)]:
                builder.rect(mask, *box)
            identifier = "heater_" + suffix
            layer = PhysicalLayer(identifier, "TiN meander " + suffix.upper(), "tin", .5e-6, 0.,
                                  geometry=RegionSpec(sources=[f"{mask}/0"]))
            layer.color = "#B27B42" if suffix == "a" else "#527FA5"
            p.layers.append(layer)
            p.terminals.extend([
                _terminal("source_" + suffix, identifier, edge(0, y, 0, y+4), "ground"),
                _terminal("drain_" + suffix, identifier, edge(100, y+24, 100, y+28), "voltage", voltage),
            ])
            p.interfaces.append(Interface("heat_" + suffix, identifier, "substrate", RegionSpec(sources=[f"{mask}/0"]),
                                          thermal_kind="resistance", thermal_value=2e-6))
        p.layers.append(PhysicalLayer("substrate", "Shared silicon heat spreader", "silicon", 30e-6, -30e-6,
                                      "thermal", rectangle(-20, -20, 120, 90), opacity=.2))
        p.thermal_boundaries = [ThermalBoundary("backside", "substrate", value=5e4)]
        p.heat_sources = [HeatSource("load_a", "heater_a", RegionSpec(sources=["1/0"]), kind="total", value=.0005),
                          HeatSource("load_b", "heater_b", RegionSpec(sources=["2/0"]), kind="total", value=.00015)]
        p.solver.resistance_terminals = ["source_a", "drain_a"]
        p.solver.resistance_current_terminal = "drain_a"
        p.mesh.max_size, p.mesh.contact_size = 10e-6, 2e-6
        p.sweeps = [SweepSpec("heat_sources.load_a.value", [0., .0005, .002], "thermal",
                             "Heater A thermal crosstalk (W)"),
                    SweepSpec("terminals.drain_a.value", [.2, .4, .6], "electrothermal",
                              "Heater A electrical bias (V)")]
        p.notes = (
            "Two electrically independent three-pass TiN heaters share a larger silicon thermal layer. "
            "A and B are biased at 0.4 V and 0.25 V; prescribed loads add 0.5 mW and 0.15 mW respectively. "
            "Electrical mode computes each powered net; reported resistance uses A only. Thermal-only mode "
            "uses prescribed loads without Joule heating, whereas electrothermal mode adds both Joule loads. "
            "Sweep load_a with B held fixed and inspect the rise of B temperature to observe thermal crosstalk. "
            "Effective 2e-6 m²K/W interfaces represent omitted isolation stacks; the 5e4 W/m²K backside sink "
            "and representative film properties need calibration. Synthetic steady-state 2.5D example."
        )
    elif name == "E16_full_gate_hemt_array":
        for y in (10, 35, 60, 85):
            builder.rect(1, 0, y, 100, y+10)
            builder.rect(2, -12, y, 12, y+10)
            builder.rect(3, 88, y, 112, y+10)
            builder.rect(10, 0, y, 12, y+10)
            builder.rect(11, 88, y, 100, y+10)
        builder.rect(2, -12, 8, -4, 97)
        builder.rect(3, 104, 8, 112, 97)
        # This gate spans the entire width of every disconnected channel stripe.
        builder.rect(4, 46, 8, 54, 97)
        p.layers = [
            PhysicalLayer("channel", "Four GaN 2DEG channels", "2deg", 10e-9, 0.,
                          geometry=RegionSpec(sources=["1/0"]), color="#6689AC",
                          electrical=ElectricalModel(kind="hemt", mobility=.12, mobility_exponent=1.5,
                              threshold_voltage=-3., gate_coupling=1.5e16, smoothing_voltage=.12,
                              background_sheet_density=0., minimum_sheet_conductance=1e-7,
                              gate_regions=["gate_control"])),
            _metal("source_metal", "2/0", .1e-6, 1e-6, "Source bus and fingers"),
            _metal("drain_metal", "3/0", .1e-6, 1e-6, "Drain bus and fingers"),
            PhysicalLayer("gate_metal", "Insulated gate electrode (thermal only)", "gold", .2e-6, .5e-6,
                          "thermal", RegionSpec(sources=["4/0"]), color="#A979B5"),
            PhysicalLayer("substrate", "Shared SiC substrate", "sic", 75e-6, -75e-6,
                          "thermal", rectangle(-22, -2, 122, 107), opacity=.2),
        ]
        p.interfaces = [
            Interface("source_contact", "channel", "source_metal", RegionSpec(sources=["10/0"]),
                      "specific", 1e-10, thermal_kind="conductance", thermal_value=1e7),
            Interface("drain_contact", "channel", "drain_metal", RegionSpec(sources=["11/0"]),
                      "specific", 1e-10, thermal_kind="conductance", thermal_value=1e7),
        ]
        for layer in p.layers[:-1]:
            p.interfaces.append(Interface("heat_" + layer.id, layer.id, "substrate", RegionSpec(sources=layer.geometry.sources.copy()),
                                          thermal_kind="resistance", thermal_value=2e-7))
        p.terminals = [
            _terminal("source", "source_metal", edge(-12, 8, -12, 97), "ground"),
            _terminal("drain", "drain_metal", edge(112, 8, 112, 97), "voltage", .1),
            _terminal("gate_control", "channel", RegionSpec(sources=["4/0", "1/0"], operation="intersection"), "gate", 0.),
        ]
        p.solver.resistance_terminals = ["source", "drain"]
        p.solver.resistance_current_terminal = "drain"
        p.thermal_boundaries = [ThermalBoundary("backside", "substrate", value=4e6)]
        p.heat_sources = [HeatSource("channel_load", "channel", RegionSpec(sources=["1/0"]), kind="total", value=.0002)]
        p.mesh.max_size, p.mesh.min_size, p.mesh.contact_size = 9e-6, .4e-6, 2e-6
        p.sweeps = [SweepSpec("terminals.gate_control.value", [-5., -3., -1., 1.], "electrothermal",
                             "Full-width gate transfer (V)")]
        p.notes = (
            "Four separated parallel GaN 2DEG stripes join shared source/drain metal buses through finite "
            "1e-10 Ωm² contacts, with 0.1 V drain bias. One gate bar spans each complete channel width, "
            "so every source-to-drain channel path crosses a controlled region; there is no ungated edge "
            "bypass. The gold gate is electrically insulated and modeled as a thermal-only layer; its "
            "voltage enters the local analytical charge-control model through gate_control. No gate leakage, "
            "capacitance, electrostatic field solution, velocity saturation or calibrated transistor prediction "
            "is provided. A 1e-7 S sheet-conductance floor limits deep-off current and maintains numerical "
            "conditioning; the off-state current is not a leakage prediction. A 0.2 mW prescribed channel "
            "load supports thermal-only operation and adds to Joule "
            "heating in coupled mode. Effective thermal interfaces represent omitted buffer/dielectric paths."
        )
    else:
        return False
    return True
