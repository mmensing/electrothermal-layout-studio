# SPDX-License-Identifier: MPL-2.0
"""Synthetic electrical coupons and small networks with explicit thermal paths.

Layout-builder dimensions are micrometres; model parameters are SI. No builder
runs a calculation. Finite contacts and localized external feeds remain part of
the solved network, rather than prescribing current sharing inside a device.
"""
from __future__ import annotations

from etlayout.models import (
    DeviceGroup,
    ElectricalModel,
    Interface,
    MeasurementSpec,
    PhysicalLayer,
    PhysicalPort,
    PortLanding,
    PropertyModel,
    RegionSpec,
    SupportSelector,
    SweepSpec,
    Terminal,
    ThermalBoundary,
)

NAMES = (
    "E25_stepped_width_series", "E26_dogbone_coupon", "E27_transfer_length_coupon",
    "E28_t_junction_splitter", "E29_annular_bus", "E30_via_daisy_chain",
    "E31_resistor_ladder", "E32_parallel_resistor_array",
)


def _feed(project, identifier, layer, geometry, value=0., *, kind=None, resistance=.002):
    kind = kind or ("current" if value else "ground")
    terminal_id = identifier + "_landing"
    project.terminals.append(Terminal(terminal_id, layer, geometry, kind, 0., identifier.title() + " landing"))
    project.power.ports.append(PhysicalPort(identifier, [PortLanding(terminal_id, resistance, .25)],
                                            kind, value, name=identifier.title() + " finite feed"))


def _film(identifier, mask, resistance, *, z=1e-6, thickness=.2e-6):
    return PhysicalLayer(identifier, "TaN resistive film", "tan", thickness, z,
                         geometry=RegionSpec(sources=[f"{mask}/0"]), color="#8B739D",
                         electrical=ElectricalModel("sheet", PropertyModel(value=resistance,
                             minimum_temperature=250., maximum_temperature=500.)), loss_role="access")


def _finish(builder, project, bounds, *, hotspot_layer, hotspot_size=20., nominal=""):
    from etlayout.examples import UM
    from etlayout.power_examples import _thermal_stack

    x0, y0, x1, y1 = bounds
    builder.rect(90, *bounds)
    project.layers.append(PhysicalLayer("substrate", "Silicon thermal support", "silicon", 100e-6,
        -100e-6, "thermal", RegionSpec(sources=["90/0"]), color="#ADB9C7", opacity=.2))
    _thermal_stack(builder, project)
    # One reservoir. The lower half of the thickness-averaged substrate and a
    # finite die-attach resistance are both included; upper sheets have no sink.
    project.thermal_boundaries = [ThermalBoundary("backside", "substrate", kind="resistance_area",
        value=2e-6 + 50e-6 / 148., ambient=300.)]
    project.power.thermal_reference_boundary = "backside"
    project.solver.resistance_terminals = project.solver.resistance_terminals or ["drain", "source"]
    project.solver.resistance_current_terminal = "drain"
    project.mesh.max_elements = 120000
    project.mesh.max_dofs = 250000
    metals = [layer.id for layer in project.layers if layer.role == "conductor" and layer.electrical.kind == "bulk"]
    conducting = [layer.id for layer in project.layers if layer.role == "conductor"]
    contacts = [interface.id for interface in project.interfaces if interface.electrical_kind != "none"]
    # Electrical reference calculations hold the layers at the model's 300 K;
    # they do not solve a thermal reservoir. Coupled studies follow the named
    # boundary so changing that reservoir also updates the reported rise.
    from etlayout.example_catalog import example_info
    fixed_temperature = example_info(project.name).recommended_mode == "electrical"
    project.power.measurements += [
        MeasurementSpec("delivered_current", "port_current", SupportSelector(ports=["drain"]), "integral"),
        MeasurementSpec("terminal_voltage", "port_voltage", SupportSelector(ports=["drain"]), "mean"),
        MeasurementSpec("terminal_power", "port_power", SupportSelector(ports=["drain"]), "integral"),
        MeasurementSpec("conductor_loss", "joule_density", SupportSelector(layers=conducting), "integral"),
        MeasurementSpec("maximum_temperature", "temperature", limit=500.),
        MeasurementSpec("temperature_rise", "temperature_rise",
                        reference_boundary="" if fixed_temperature else "backside",
                        reference_temperature=300. if fixed_temperature else None),
        MeasurementSpec("averaged_hotspot", "temperature", SupportSelector(layers=[hotspot_layer]),
                        "maximum", averaging_length=hotspot_size * UM),
    ]
    if metals:
        project.power.measurements.append(MeasurementSpec("metal_loading", "metal_current_density",
                                                         SupportSelector(layers=metals), "percentile", percentile=95.))
    if contacts:
        project.power.measurements.append(MeasurementSpec("contact_loss", "contact_heat_density",
                                                         SupportSelector(contacts=contacts), "integral"))
    project.notes += (
        f" Nominal setup: {nominal} Layout support {x1-x0:g} × {y1-y0:g} µm including margins. "
        "Synthetic 300 K engineering assumptions, not process calibration or device ratings. "
        "Nearest-sheet SiO₂-equivalent gaps connect to a 100 µm silicon support; finite physical "
        "contacts/vias replace gap areas. One backside 300 K reservoir includes 2e-6 m²K/W die "
        "attachment plus half-substrate conduction. These thickness-averaged paths omit depth-resolved "
        "gradients. No prescribed heating is added: coupled mode uses conductor, contact and finite-feed "
        "losses only; 25% of external-feed loss enters the local landing and 75% remains external. "
        "Constant TaN sheet values, where present, are synthetic effective values without electrical "
        "temperature feedback; bulk copper retains its reference temperature dependence. Review "
        "250–500 K material validity and mesh refinement before interpreting peaks. No electromigration, "
        "damage, reactive circuit or mechanical model is implied."
    )


def build_teaching_example(name, builder, p):
    """Build an explicitly registered coupon; unknown IDs leave the project alone."""
    from etlayout.examples import UM, _metal, rectangle

    if name not in NAMES:
        return False
    p.layers, p.interfaces, p.terminals, p.sweeps = [], [], [], []
    p.mesh.max_size, p.mesh.min_size, p.mesh.contact_size = 12e-6, .5e-6, 3e-6
    if name == "E25_stepped_width_series":
        builder.add(1, [(0, 0), (300, 0), (300, -10), (600, -10),
                        (600, 30), (300, 30), (300, 20), (0, 20)])
        p.layers = [_metal("metal", "1/0", 1e-6, 2e-6, "Stepped copper strip")]
        _feed(p, "source", "metal", rectangle(0, 0, 10, 20), resistance=.0001)
        _feed(p, "drain", "metal", rectangle(590, -10, 600, 30), .05, resistance=.0001)
        p.sweeps = [SweepSpec("power.ports.drain.value", [.025, .05, .1], "electrical", "Fixed-temperature strip current")]
        p.notes = ("Two 300 µm series segments have widths 20 and 40 µm and copper thickness 2 µm. "
                   "Ten-micrometre full-width end electrodes leave 290 µm per segment outside the electrodes. "
                   "The 1D lower limit at 300 K is 0.1827 Ω inside the strip, plus two 0.1 mΩ feeds; "
                   "the width step adds spreading resistance. Localized finite feeds leave the entire "
                   "route resistive, including the metal beneath their footprints.")
        _finish(builder, p, (-20, -30, 620, 50), hotspot_layer="metal", nominal="50 mA DC electrical reference.")
    elif name == "E26_dogbone_coupon":
        builder.add(1, [(0, 0), (80, 0), (130, 52), (190, 52), (240, 0), (320, 0),
                        (320, 120), (240, 120), (190, 68), (130, 68), (80, 120), (0, 120)])
        p.layers = [_metal("metal", "1/0", 1e-6, 4e-6, "Tapered copper neck coupon")]
        _feed(p, "source", "metal", rectangle(5, 40, 25, 80))
        _feed(p, "drain", "metal", rectangle(295, 40, 315, 80), .5)
        p.power.measurements.append(MeasurementSpec("neck_loading", "metal_current_density",
            SupportSelector(layers=["metal"], geometry=rectangle(140, 52, 180, 68)), "mean"))
        p.sweeps = [SweepSpec("power.ports.drain.value", [.25, .5, .75], "electrothermal", "Neck current")]
        p.notes = ("A 320 × 120 µm dog-bone coupon narrows through two 50 µm tapers into a 60 × 16 µm neck. "
                   "Localized 20 × 40 µm feed footprints and 2 mΩ feeds retain pad spreading. "
                   "The neck_loading measurement averages a fixed 40 × 16 µm physical window; "
                   "sharp taper-end point maxima remain mesh-sensitive. This is a crowding coupon, not a fuse model.")
        _finish(builder, p, (-20, -20, 340, 140), hotspot_layer="metal", nominal="0.5 A coupled DC.")
    elif name == "E27_transfer_length_coupon":
        builder.rect(1, 0, 0, 240, 40)
        for bounds in ((0, 0, 60, 40), (180, 0, 240, 40)):
            builder.rect(2, *bounds)
            builder.rect(10, *bounds)
        p.layers = [_film("film", 1, 10.), _metal("pads", "2/0", 1.2e-6, 2e-6, "Copper contact electrodes")]
        p.interfaces = [Interface("film_contacts", "film", "pads", RegionSpec(sources=["10/0"]),
            "specific", 1e-9, thermal_kind="conductance", thermal_value=1e7, model_kind="physical_contact", height_mode="stack", height=0.)]
        _feed(p, "source", "pads", rectangle(0, 0, 10, 40))
        _feed(p, "drain", "pads", rectangle(230, 0, 240, 40), .001)
        from etlayout.examples import edge
        p.terminals += [Terminal("sense_a", "film", edge(80, 0, 80, 40), "floating"),
                        Terminal("sense_b", "film", edge(160, 0, 160, 40), "floating")]
        p.solver.resistance_terminals = ["sense_b", "sense_a"]
        p.power.measurements.append(MeasurementSpec("film_sheet_loading", "sheet_current",
                                                   SupportSelector(layers=["film"]), "percentile", percentile=95.))
        p.mesh.contact_size = 2e-6
        p.sweeps = [SweepSpec("interfaces.film_contacts.electrical_value", [2.5e-10, 1e-9, 4e-9],
                             "electrical", "Specific contact resistivity (Ω m²)")]
        p.notes = ("A 240 × 40 µm, 10 Ω/□ effective TaN strip has two 60 µm long copper contacts with "
                   "specific contact resistivity 1e-9 Ω m². The nominal contact transfer length is "
                   "sqrt(rho_c/R_sheet) = 10 µm, resolved with 2 µm contact sizing. Zero-current floating "
                   "cross-section probes at x=80 and 160 µm are equipotential and can perturb nonuniform "
                   "fields; here the intervening uniform strip gives a 20 Ω Kelvin reference. Terminal "
                   "two-terminal resistance additionally includes finite contact, pad and external-feed losses.")
        _finish(builder, p, (-20, -20, 260, 60), hotspot_layer="film", nominal="1 mA DC electrical Kelvin reference.")
    elif name == "E28_t_junction_splitter":
        builder.add(1, [(0, 80), (140, 80), (140, 0), (180, 0), (180, 80), (320, 80),
                        (320, 120), (0, 120)])
        p.layers = [_metal("metal", "1/0", 1e-6, 5e-6, "Copper T-junction")]
        _feed(p, "source", "metal", rectangle(0, 85, 20, 115))
        _feed(p, "drain", "metal", rectangle(145, 0, 175, 20), .3)
        _feed(p, "branch", "metal", rectangle(300, 85, 320, 115), -.1)
        p.power.measurements += [
            MeasurementSpec("branch_current", "port_current", SupportSelector(ports=["branch"]), "integral"),
            MeasurementSpec("return_current", "port_current", SupportSelector(ports=["source"]), "integral"),
        ]
        p.sweeps = [SweepSpec("power.ports.branch.value", [-.05, -.1, -.15], "electrical", "Right branch withdrawal at fixed input")]
        p.notes = ("A 320 µm copper T-bus with 40 µm arms receives 0.3 A through its bottom landing. "
                   "A separate right port withdraws 0.1 A; the left ground/reference port must return 0.2 A. "
                   "Negative port current explicitly leaves the model. The external currents are controlled; "
                   "the local junction fields and required port voltages emerge from the sheet solution. "
                   "Every localized landing has 2 mΩ feed resistance. The effective resistance metric uses "
                   "the bottom input against the left reference and is not a two-terminal network reduction.")
        _finish(builder, p, (-20, -20, 340, 140), hotspot_layer="metal", nominal="0.3 A input and 0.1 A right withdrawal.")
    elif name == "E29_annular_bus":
        outer = [(0, 60), (60, 0), (340, 0), (400, 60), (400, 240), (340, 300), (60, 300), (0, 240)]
        hole = [(120, 100), (140, 80), (260, 80), (280, 100), (280, 200), (260, 220), (140, 220), (120, 200)]
        builder.add(1, outer, holes=[hole])
        p.layers = [_metal("metal", "1/0", 1e-6, 8e-6, "Octagonal windowed copper bus")]
        _feed(p, "source", "metal", rectangle(5, 120, 35, 180), resistance=.0005)
        _feed(p, "drain", "metal", rectangle(365, 120, 395, 180), 1., resistance=.0005)
        for suffix, bounds in (("upper", (140, 220, 260, 300)), ("lower", (140, 0, 260, 80))):
            # These windows do not contain complete port landings. They are
            # scoped loss supports, not device groups with a terminal current.
            p.power.measurements.append(MeasurementSpec(suffix + "_loss", "joule_density",
                SupportSelector(layers=["metal"], geometry=rectangle(*bounds)), "integral"))
        p.mesh.max_size = 18e-6
        p.mesh.contact_size = 5e-6
        p.sweeps = [SweepSpec("power.ports.drain.value", [.5, 1., 1.5], "electrothermal", "Windowed bus current")]
        p.notes = ("A 400 × 300 µm octagonal 8 µm copper bus surrounds a genuine octagonal 160 × 140 µm "
                   "opening. The larger continuous substrate remains below the opening. Opposed centered "
                   "30 × 60 µm landings and 0.5 mΩ feeds preserve top/bottom symmetry: each arm should "
                   "carry half the delivered current. Upper/lower loss measurements use matching physical "
                   "support windows. No conductor exists in the protected opening; corner point peaks need refinement.")
        _finish(builder, p, (-20, -20, 420, 320), hotspot_layer="metal", hotspot_size=40., nominal="1 A coupled DC.")
    elif name == "E30_via_daisy_chain":
        for bounds in ((0, 0, 100, 30), (160, 0, 280, 30)):
            builder.rect(1, *bounds)
        for bounds in ((70, 0, 190, 30), (250, 0, 370, 30)):
            builder.rect(2, *bounds)
        builder.rect(3, 340, 0, 460, 30)
        for x, mask in ((70, 10), (160, 10), (250, 10), (340, 11)):
            for column in range(3):
                for row in range(3):
                    builder.rect(mask, x+4+8*column, 4+8*row, x+7+8*column, 7+8*row)
        p.layers = [_metal("m1", "1/0", 1e-6, 2e-6, "Lower alternating islands"),
                    _metal("m2", "2/0", 5e-6, 2e-6, "Middle chain links"),
                    _metal("m3", "3/0", 9e-6, 2e-6, "Upper output link")]
        p.layers[1].color, p.layers[2].color = "#8C73A6", "#BB914C"
        p.interfaces = [Interface(identifier, a, b, RegionSpec(sources=[mask]), "via", material_id="copper",
            height=p.layer(b).z - (p.layer(a).z + p.layer(a).thickness), thermal_kind="stack",
            model_kind="physical_via", height_mode="stack", enclosure=1e-6)
            for identifier, a, b, mask in (("via12", "m1", "m2", "10/0"), ("via23", "m2", "m3", "11/0"))]
        _feed(p, "source", "m1", rectangle(0, 0, 12, 30), resistance=.0005)
        _feed(p, "drain", "m3", rectangle(448, 0, 460, 30), .3, resistance=.0005)
        p.mesh.contact_size = 1.5e-6
        p.power.measurements.append(MeasurementSpec("via_loading", "contact_normal_density",
            SupportSelector(contacts=["via12", "via23"]), "rms"))
        p.sweeps = [SweepSpec("power.ports.drain.value", [.1, .3, .5], "electrothermal", "Daisy-chain current")]
        p.notes = ("A 460 × 30 µm three-level copper chain alternates between separated M1 and M2 islands, "
                   "then transfers to M3. Four independent 3 × 3 arrays contain 36 actual 3 × 3 µm vias, "
                   "2 µm high on 8 µm pitch. All 0.3 A must cross each bank; vias within a bank need not share "
                   "equally. The three M1–M2 banks are grouped by fabrication transition, not silently merged "
                   "electrically into a single crossing. Through-via fields are distributed interface links, "
                   "not a resolved 3D sidewall solution. Two localized 12 × 30 µm landings each add 0.5 mΩ.")
        _finish(builder, p, (-20, -20, 480, 50), hotspot_layer="m2", nominal="0.3 A coupled DC through all four via banks.")
    elif name == "E31_resistor_ladder":
        for index in range(4):
            builder.rect(1, 15+80*index, 30, 85+80*index, 50)
            builder.rect(10, 15+80*index, 30, 20+80*index, 50)
            builder.rect(10, 80+80*index, 30, 85+80*index, 50)
        for index in range(5):
            builder.rect(2, 80*index, 20, 20+80*index, 60)
        p.layers = [_film("film", 1, 10.), _metal("pads", "2/0", 1.2e-6, 2e-6, "Five ladder electrodes")]
        p.interfaces = [Interface("ladder_contacts", "film", "pads", RegionSpec(sources=["10/0"]),
            "specific", 1e-9, thermal_kind="conductance", thermal_value=1e7, model_kind="physical_contact", height_mode="stack", height=0.)]
        _feed(p, "source", "pads", rectangle(0, 20, 20, 60), resistance=.001)
        _feed(p, "drain", "pads", rectangle(320, 20, 340, 60), .4, kind="voltage", resistance=.001)
        for index in range(1, 4):
            p.terminals.append(Terminal(f"tap_{index}", "pads", rectangle(80*index, 20, 20+80*index, 60),
                                        "floating", name=f"Passive tap {index}"))
        p.mesh.contact_size = 2e-6
        p.sweeps = [SweepSpec("power.ports.drain.value", [.2, .4, .6], "electrical", "Ladder bias")]
        p.notes = ("Four identical 10 Ω/□ TaN film resistors join five copper electrode nodes on 80 µm pitch. "
                   "Each resistor is 70 × 20 µm, including two 5 µm contact overlaps with 1e-9 Ω m² specific "
                   "resistivity (10 µm transfer length); the free resistor length is 60 µm. Three complete "
                   "middle electrode nodes are intentionally ideal zero-current floating probes. Equal "
                   "resistor/contact geometry predicts quarter-step node voltages; this is a DC tapped "
                   "divider, without capacitance or a switching-ladder model. End electrodes have 1 mΩ feeds.")
        _finish(builder, p, (-20, 0, 360, 80), hotspot_layer="film", nominal="0.4 V DC electrical ladder bias.")
    else:
        for index in range(6):
            y = 20+50*index
            builder.rect(1, 50, y, 350, y+20)
            builder.rect(10, 50, y, 70, y+20)
            builder.rect(11, 330, y, 350, y+20)
            p.power.devices.append(DeviceGroup(f"resistor_{index+1}",
                SupportSelector(layers=["film"], geometry=rectangle(50, y, 350, y+20)), active_width=20*UM,
                source_port="source", drain_port="drain", name=f"Resistor row {index+1}"))
        builder.rect(2, 30, 0, 70, 310)
        builder.rect(3, 330, 0, 370, 310)
        p.layers = [_film("film", 1, 1.),
                    _metal("source_bus", "2/0", 1.2e-6, .4e-6, "Resistive copper source bus"),
                    _metal("drain_bus", "3/0", 1.2e-6, .4e-6, "Resistive copper drain bus")]
        p.interfaces = [Interface(identifier, "film", layer, RegionSpec(sources=[mask]),
            "specific", 1e-10, thermal_kind="conductance", thermal_value=1e7,
            model_kind="physical_contact", height_mode="stack", height=0.)
            for identifier, layer, mask in (("source_contacts", "source_bus", "10/0"),
                                             ("drain_contacts", "drain_bus", "11/0"))]
        _feed(p, "source", "source_bus", rectangle(30, 0, 70, 15), resistance=.001)
        _feed(p, "drain", "drain_bus", rectangle(330, 0, 370, 15), .3, resistance=.001)
        p.power.measurements.append(MeasurementSpec("film_sheet_loading", "sheet_current",
                                                   SupportSelector(layers=["film"]), "percentile", percentile=95.))
        p.mesh.max_size, p.mesh.contact_size = 15e-6, 3e-6
        p.sweeps = [SweepSpec("layers.source_bus.thickness", [.2e-6, .4e-6, .8e-6],
                             "electrothermal", "Source-bus thickness at fixed delivered current")]
        p.notes = ("Six equal 300 × 20 µm effective 1 Ω/□ TaN resistors connect 0.4 µm copper buses, "
                   "40 µm wide and 310 µm long. Each film overlaps its bus by 20 µm with 1e-10 Ω m² "
                   "specific contact resistivity; nominal transfer length is 10 µm. Both localized "
                   "40 × 15 µm feed footprints are at the same end, so remote rows carry less current "
                   "because of resistive buses. Current sharing is solved, not imposed. Each row group "
                   "reports its supported current, loss and current per 20 µm active width. Vary source-bus "
                   "thickness while preserving all active film geometry and the 0.3 A delivered service.")
        _finish(builder, p, (0, -20, 400, 330), hotspot_layer="film", hotspot_size=30., nominal="0.3 A coupled DC across six rows.")
    return True
