# SPDX-License-Identifier: MPL-2.0
"""Device and package teaching examples using existing thickness-averaged laws.

Coordinates passed to LayoutBuilder are micrometres. All model values and the
machine-readable notes are SI. Parameters are synthetic assumptions, not ratings.
"""
from __future__ import annotations

import json
from copy import deepcopy

import klayout.db as kdb

from etlayout.models import (
    DeviceGroup,
    ElectricalModel,
    Interface,
    MeasurementSpec,
    PhysicalLayer,
    RefinementRegion,
    RegionSpec,
    RoutingRules,
    SupportSelector,
    SweepSpec,
    Terminal,
    ThermalBoundary,
    TransientDrive,
    TransientSettings,
    Waveform,
)

DEVICE_EXAMPLES = (
    "E33_suspended_bridge_heater", "E34_interleaved_heater_sensor",
    "E35_localized_die_attach", "E36_pulsed_hotspot_neck",
    "E37_gated_hemt_coupon", "E38_dual_bank_gan",
    "E39_sic_island_die", "E40_package_interposer",
)
METADATA_MARKER = "\n\nDevice example configuration (SI):\n"


def metadata(project):
    """Read reproducible geometry/model assumptions without a second catalog."""
    return json.loads(project.notes.split(METADATA_MARKER, 1)[1])


def _film(identifier, mask, material="tin", thickness=.5e-6, z=0., role="conductor"):
    layer = PhysicalLayer(identifier, identifier.replace("_", " ").title(), material,
                          thickness, z, role, RegionSpec(sources=[f"{mask}/0"]))
    if material in {"tin", "tan"} and role == "conductor":
        # Film stress is sheet current. This is exactly the reference bulk
        # rho(T)/thickness law, expressed through its supported sheet model.
        from etlayout.materials.database import reference_materials
        resistance = deepcopy(reference_materials()[material].resistivity)
        resistance.value /= thickness
        layer.electrical = ElectricalModel("sheet", resistance)
    return layer


def _contact(identifier, lower, upper, mask, *, rho=1e-10, thermal=1e7):
    return Interface(identifier, lower, upper, RegionSpec(sources=[f"{mask}/0"]),
                     "specific", rho, thermal_kind="conductance", thermal_value=thermal)


def _backside(p, layer, *, resistance=2e-6, support=None):
    p.thermal_boundaries = [ThermalBoundary("backside", layer,
        support or RegionSpec(full_domain=True), "resistance_area", resistance, 300.)]
    p.power.thermal_reference_boundary = "backside"


def _ports(p, source_layer, source, drain_layer, drain, current, *, resistance=.001):
    from etlayout.power_examples import _port
    _port(p, "source", source_layer, [source], 0., feed_resistance=resistance)
    _port(p, "drain", drain_layer, [drain], current, feed_resistance=resistance)
    p.solver.resistance_terminals = ["source", "drain"]
    p.solver.resistance_current_terminal = "drain"


def _thermal_teaching(name, b, p):
    from etlayout.examples import rectangle
    if name == DEVICE_EXAMPLES[0]:
        # Wide end anchors and a narrow suspended bridge on one continuous film.
        for bounds in ((0, 20, 60, 100), (50, 56, 250, 64), (240, 20, 300, 100)):
            b.rect(1, *bounds)
        b.rect(20, 0, 0, 300, 120)
        for bounds in ((0, 20, 60, 100), (240, 20, 300, 100)):
            b.rect(10, *bounds)
        p.layers = [_film("bridge", 1, thickness=.4e-6),
                    _film("substrate", 20, "silicon", 50e-6, -52e-6, "thermal")]
        p.interfaces = [Interface("anchor_attachment", "bridge", "substrate", RegionSpec(sources=["10/0"]),
                                 thermal_kind="resistance", thermal_value=1e-7),
                        Interface("weak_cavity_path", "bridge", "substrate", rectangle(60, 56, 240, 64),
                                 thermal_kind="conductance", thermal_value=200.)]
        _ports(p, "bridge", (5, 30, 35, 90), "bridge", (265, 30, 295, 90), .0003, resistance=1.)
        _backside(p, "substrate")
        p.mesh.max_size, p.mesh.min_size, p.mesh.contact_size = 7.5e-6, .5e-6, 2e-6
        p.sweeps = [SweepSpec("interfaces.weak_cavity_path.thermal_value", [100., 200., 400.],
                             "electrothermal", "Cavity heat-transfer sensitivity (W/m² K)")]
        text = ("A narrow 200 × 8 × 0.4 µm TiN bridge connects wide end anchors. The 0.3 mA current "
                "creates Joule heat; no prescribed source is added. The cavity path is an explicitly effective "
                "200 W/(m² K) residual gas/radiative approximation; heat primarily flows laterally to finite "
                "anchor attachments and then through the shared silicon support to one backside reservoir. "
                "Compare bridge and anchor temperatures. No mechanics, pressure or radiation law is solved.")
        data = dict(die_um=[300., 120.], requested_current_A=.0003, suspended_span_m=180e-6,
                    bridge_width_m=8e-6, cavity_conductance_W_m2_K=200., hotspot_layer="bridge")
    elif name == DEVICE_EXAMPLES[1]:
        # Insulated interleaved meanders are electrically disjoint; a common
        # substrate, not an invented electrical transduction, couples them.
        paths = {"heater": [(30, 35, 360, 43), (352, 35, 360, 95), (30, 87, 360, 95),
                            (30, 87, 38, 147), (30, 139, 360, 147)],
                 "sensor": [(55, 61, 330, 67), (55, 61, 61, 121), (55, 115, 330, 121)]}
        for mask, boxes in enumerate(paths.values(), start=1):
            for bounds in boxes:
                b.rect(mask, *bounds)
        b.rect(20, 0, 0, 420, 220)
        p.layers = [_film("heater", 1, thickness=.6e-6),
                    _film("sensor", 2, "platinum", .2e-6, 2e-6),
                    _film("substrate", 20, "silicon", 40e-6, -42e-6, "thermal")]
        from etlayout.power_examples import _thermal_stack
        _thermal_stack(b, p)
        _ports(p, "heater", (30, 35, 50, 43), "heater", (340, 139, 360, 147), .002, resistance=1.)
        # Passive Kelvin voltage probes sample the isolated, weakly biased film.
        p.terminals += [Terminal("sensor_return", "sensor", rectangle(310, 61, 330, 67), "ground"),
                        Terminal("sensor_bias", "sensor", rectangle(310, 115, 330, 121), "current", .00001),
                        Terminal("sensor_probe", "sensor", rectangle(70, 115, 80, 121), "probe")]
        _backside(p, "substrate", resistance=1e-5)
        p.mesh.max_size, p.mesh.min_size, p.mesh.contact_size = 20e-6, .5e-6, 4e-6
        p.sweeps = [SweepSpec("power.ports.drain.value", [.001, .002, .003],
                             "electrothermal", "Heater drive; sensor bias fixed (A)")]
        p.power.measurements += [MeasurementSpec("sensor_mean_temperature", "temperature",
                                   SupportSelector(layers=["sensor"]), "mean"),
                                MeasurementSpec("sensor_probe_potential", "potential",
                                   SupportSelector(layers=["sensor"], regions=["terminal:sensor_probe"]), "mean")]
        text = ("Interleaved TiN heater and elevated platinum sensor meanders remain electrically isolated. "
                "A 2 mA heater and 10 µA sensor bias generate their own Joule heat. Nearest-layer SiO₂-equivalent insulation and a shared silicon layer transfer heat; the platinum reference resistivity has its explicit "
                "linear temperature coefficient. Compare sensor temperature and probe voltage during the bounded "
                "heater-current sweep. This is an effective thermoresistive readout, not a calibrated sensor, "
                "electrostatic coupling, or a new transduction model.")
        data = dict(die_um=[420., 220.], requested_current_A=.002, sensor_current_A=.00001,
                    electrically_isolated_masks=["1/0", "2/0"], hotspot_layer="heater")
    elif name == DEVICE_EXAMPLES[2]:
        b.rect(20, 0, 0, 1000, 1000)
        for bounds in ((150, 300, 360, 700), (330, 460, 670, 540), (640, 300, 850, 700)):
            b.rect(1, *bounds)
        b.rect(30, 50, 100, 420, 900)  # physical partial attachment footprint
        p.layers = [_film("heater", 1, "titanium", 2e-6),
                    _film("substrate", 20, "silicon", 200e-6, -202e-6, "thermal")]
        p.interfaces = [Interface("insulation", "heater", "substrate", RegionSpec(sources=["1/0"]),
                                  thermal_kind="resistance", thermal_value=1e-6)]
        _ports(p, "heater", (170, 350, 300, 650), "heater", (700, 350, 830, 650), .1)
        _backside(p, "substrate", resistance=2e-6, support=RegionSpec(sources=["30/0"]))
        p.mesh.max_size, p.mesh.min_size, p.mesh.contact_size = 65e-6, 2e-6, 25e-6
        p.sweeps = [SweepSpec("thermal_boundaries.backside.value", [2e-6, 1e-5], "electrothermal",
                             "Partial die-attach thermal resistance area (m² K/W)")]
        text = ("A titanium dog-bone heater sits on a 1 × 1 mm silicon die. Only the left-side 370 × 800 µm "
                "backside footprint is attached; the remaining backside is insulated. At the unchanged 0.1 A drive, "
                "compare hot-spot position with electrical crowding and lateral substrate spreading. The finite "
                "top insulation and one partial backside path produce the cooling; hot metal is not independently "
                "attached to an ideal sink. No prescribed heat source, bond void mechanics or depth-resolved "
                "temperature solution is implied.")
        data = dict(die_um=[1000., 1000.], requested_current_A=.1, attached_area_m2=370e-6 * 800e-6,
                    attach_mask="30/0", hotspot_layer="heater")
    else:
        b.rect(20, 0, 0, 600, 240)
        b.add(1, [(30, 50), (180, 50), (260, 100), (340, 100), (420, 50), (570, 50),
                  (570, 190), (420, 190), (340, 140), (260, 140), (180, 190), (30, 190)])
        p.layers = [_film("neck", 1, "copper", 5e-6),
                    _film("substrate", 20, "alumina", 100e-6, -102e-6, "thermal")]
        p.interfaces = [Interface("insulation", "neck", "substrate", RegionSpec(sources=["1/0"]),
                                  thermal_kind="resistance", thermal_value=2e-6)]
        _ports(p, "neck", (45, 70, 135, 170), "neck", (465, 70, 555, 170), 1.)
        _backside(p, "substrate", resistance=5e-6)
        p.transient = TransientSettings(enabled=True, stop_time=.0004, time_step=1e-5, output_interval=2e-5,
            drives=[TransientDrive("power.ports.drain.value", Waveform(kind="pulse", low=0., high=1.,
                delay=20e-6, rise_time=20e-6, width=80e-6, fall_time=20e-6, period=200e-6))])
        p.mesh.max_size, p.mesh.min_size, p.mesh.contact_size = 30e-6, 2e-6, 15e-6
        p.mesh.refinements = [RefinementRegion("neck_transfer", rectangle(230, 85, 370, 155), 10e-6)]
        p.sweeps = [SweepSpec("transient.drives.0.waveform.high", [.5, .75, 1.], "electrothermal",
                             "Pulse high-plateau current (A)")]
        text = ("A tapered copper neck concentrates current between wide finite landings on a 600 × 240 µm "
                "alumina support. Two 1 A pulses have 20 µs rise/fall, an 80 µs plateau and a 200 µs period. "
                "The existing quasi-static electrical and thermal-storage solve gives Joule heating and delayed "
                "temperature response. Inspect the neck temperature and retained frames; refine time and mesh "
                "before comparing peaks. No inductance, switching losses, electromigration lifetime or prescribed "
                "heating is included.")
        data = dict(die_um=[600., 240.], requested_current_A=1., pulse_high_A=1.,
                    neck_width_m=40e-6, hotspot_layer="neck")
    return text, data


def _gan(name, b, p):
    from etlayout.examples import _metal, rectangle
    from etlayout.power_examples import _port, _thermal_stack
    dual = name == DEVICE_EXAMPLES[5]
    count, width = (8, 250.) if dual else (1, 250.)
    die = (1100., 650.) if dual else (350., 500.)
    origins = [(200., 200.), (650., 200.)] if dual else [(150., 120.)]
    b.rect(90, 0, 0, *die)
    p.layers = []
    p.terminals = []
    p.interfaces = []
    source_mask, gate_mask = 10, 20
    for bank_index, (x, y) in enumerate(origins):
        suffix = chr(ord("a") + bank_index)
        channel, drain = "channel_" + suffix, "drain_" + suffix
        channel_mask, drain_mask, source_contact_mask, drain_contact_mask, control = (
            bank_index + 1, 11 + bank_index, 30 + 2 * bank_index, 31 + 2 * bank_index, 40 + bank_index)
        finger = b.layout.create_cell("GATED_COUPON_" + suffix.upper())
        for mask, bounds in ((channel_mask, (0, 0, 9, width)),
                             (source_mask, (-4, -50, 2, width)), (drain_mask, (7, 0, 13, width + 70)),
                             (source_contact_mask, (0, 0, 2, width)), (drain_contact_mask, (7, 0, 9, width)),
                             (gate_mask, (4, -10, 5, width)), (control, (4, 0, 5, width))):
            b.rect(mask, *bounds, cell=finger)
        b.cell.insert(kdb.DCellInstArray(finger.cell_index(), kdb.DTrans(x, y),
                                       kdb.DVector(20, 0), kdb.DVector(0, 0), count, 1))
        span = (count - 1) * 20 + 13
        b.rect(source_mask, x - 60, y - 60, x + span, y - 20)
        b.rect(drain_mask, x - 4, y + width + 30, x + span + 70, y + width + 70)
        b.rect(gate_mask, x + 4, y - 10, x + span, y - 9)
        b.rect(gate_mask, x + 4, y - 70, x + 5, y - 9)
        b.rect(gate_mask, x - 15, y - 110, x + 25, y - 65)
        gate_terminal = "gate_" + suffix
        p.layers.append(PhysicalLayer(channel, "Fully gated GaN bank " + suffix.upper(), "2deg", 10e-9, 0.,
            geometry=RegionSpec(sources=[f"{channel_mask}/0"]),
            electrical=ElectricalModel(kind="hemt", mobility=.12, mobility_exponent=1.5,
                threshold_voltage=-3., gate_coupling=1.5e16, smoothing_voltage=.1,
                maximum_sheet_density=2e17, minimum_sheet_conductance=1e-10, gate_regions=[gate_terminal])))
        p.layers.append(_metal(drain, f"{drain_mask}/0", .1e-6, 8e-6))
        p.interfaces += [_contact("source_contact_" + suffix, "source", channel, source_contact_mask),
                         _contact("drain_contact_" + suffix, drain, channel, drain_contact_mask)]
        p.terminals.append(Terminal(gate_terminal, channel, RegionSpec(sources=[f"{control}/0"]), "gate", 3.))
        port = "drain_" + suffix
        current = .25 if dual else .02
        _port(p, port, drain, [(x + span + 10, y + width + 36, x + span + 60, y + width + 64)], current)
        p.power.measurements += [MeasurementSpec("delivered_" + suffix, "port_current", SupportSelector(ports=[port]), "integral"),
            MeasurementSpec("field_" + suffix, "electric_field", SupportSelector(layers=[channel]), "maximum", limit=3e5),
            MeasurementSpec("sheet_loading_" + suffix, "sheet_current", SupportSelector(layers=[channel]), "percentile", percentile=95.)]
        fingers = []
        for index in range(count):
            identifier = f"bank_{suffix}_finger_{index + 1}"
            fingers.append(identifier)
            p.power.devices.append(DeviceGroup(identifier, SupportSelector(layers=[channel],
                geometry=rectangle(x + index * 20, y, x + index * 20 + 9, y + width)),
                width * 1e-6, 9 * width * 1e-12, "source", port, gate_terminal))
        p.power.devices.append(DeviceGroup("bank_" + suffix, SupportSelector(devices=fingers),
            count * width * 1e-6, count * 9 * width * 1e-12, "source", port, gate_terminal))
        p.mesh.refinements.append(RefinementRegion("finger_resolution_" + suffix,
            rectangle(x - 4, y, x + span, y + width), 20e-6, [.25e-6, 25e-6]))
        p.sweeps.append(SweepSpec("terminals." + gate_terminal + ".value", [1., 2., 3.], "electrothermal",
                                 "Bank " + suffix.upper() + " gate bias at fixed delivered current"))
    if dual:
        b.rect(source_mask, 140, 140, 1010, 180)
        source_landing = (470, 145, 550, 175)
    else:
        source_landing = (92, 65, 125, 95)
    p.layers += [_metal("source", "10/0", .1e-6, 8e-6),
                 _film("gate", 20, "gold", .05e-6, .024e-6, "thermal"),
                 _film("buffer", 90, "gan", 2e-6, -2e-6, "thermal"),
                 _film("substrate", 90, "sic", 150e-6, -152e-6, "thermal")]
    for layer in p.layers:
        if layer.id.startswith("drain") or layer.id == "source":
            layer.loss_role = "routing_metal"
    _port(p, "source", "source", [source_landing], 0.)
    _thermal_stack(b, p)
    _backside(p, "substrate", resistance=2e-6 + 150e-6 / (2 * 370.))
    p.solver.resistance_terminals = ["source", "drain_a"]
    p.solver.resistance_current_terminal = "drain_a"
    p.mesh.max_size, p.mesh.min_size, p.mesh.contact_size = 70e-6, .125e-6, 20e-6
    p.mesh.corner_refinement = False
    p.power.rules = RoutingRules(protected_masks=["1/0", "10/0", "20/0", "40/0", "90/0"] + (["2/0", "41/0"] if dual else []))
    text = ("Two independently current-driven eight-finger GaN banks share finite source collection and a "
            "thermal substrate. Compare bank/finger currents, local source bias and temperatures while varying "
            "one gate at fixed 0.25 A per bank. " if dual else
            "A single 250 µm-wide GaN coupon resolves a 1 µm gate, 2 µm source/drain access and finite "
            "2 µm ohmic overlaps. The localized finite feeds deliver 20 mA. ")
    text += ("Every source-to-drain channel path crosses the complete gate; there is no ungated edge bypass. "
             "The drawn gate is insulated and its explicit gate terminal controls the existing analytical local-bias law. "
             "The same synthetic mobility, density bounds and finite 1e-10 Ω m² contacts as the larger power examples "
             "are retained. The single backside sink follows buffer/interface paths. No high-field saturation, "
             "breakdown, switching or calibrated rating is established; numerical conductance-floor current is not leakage. "
             "The supplied fixed-current gate study is bounded to 1–3 V. Deep-off, floor-dominated solves can "
             "become ill-conditioned and are outside this example's validated operating scope.")
    return text, dict(die_um=list(die), requested_current_A=.5 if dual else .02, bank_count=len(origins),
        actual_gate_fingers=count * len(origins), total_gate_width_m=count * width * len(origins) * 1e-6,
        gate_length_m=1e-6, contact_specific_resistance_ohm_m2=1e-10, hotspot_layer="channel_a",
        maximum_channel_field_V_m=3e5, supplied_gate_sweep_range_V=[1., 3.], model_temperature_range_K=[300., 425.])


def _sic(b, p):
    from etlayout.examples import _metal, rectangle
    from etlayout.power_examples import _physical_via, _thermal_stack
    side = 1000.
    b.rect(20, 0, 0, side, side)
    b.rect(2, 0, 0, side, side)
    for bounds in ((0, 0, 1000, 80), (0, 920, 1000, 1000), (0, 80, 80, 920), (920, 80, 1000, 920),
                   (450, 450, 550, 550)):
        b.rect(99, *bounds)
    # Four macrocells are separated by actual nonactive isolation corridors.
    unit = b.layout.create_cell("SIC_ACTIVE_ISLAND")
    b.rect(1, 0, 0, 320, 320, cell=unit)
    b.rect(90, 0, 0, 320, 320, cell=unit)
    for row, y in enumerate((100., 580.)):
        for col, x in enumerate((100., 580.)):
            b.cell.insert(kdb.DCellInstArray(unit.cell_index(), kdb.DTrans(x, y)))
            for vx in (x + 60, x + 260):
                for vy in (y + 160,):
                    b.rect(12, vx - 12, vy - 12, vx + 12, vy + 12)
            identifier = f"island_{row + 1}_{col + 1}"
            p.power.devices.append(DeviceGroup(identifier, SupportSelector(layers=["source"],
                geometry=rectangle(x, y, x + 320, y + 320)), active_area=320e-6**2,
                source_port="source", drain_port="drain"))
    # Two horizontal straps and edge rails leave a visible protected central opening.
    for bounds in ((95, 225, 905, 295), (95, 705, 905, 775), (95, 100, 185, 900), (815, 100, 905, 900)):
        b.rect(3, *bounds)
    for bounds in ((25, 25, 70, 70), (46, 45, 50, 950), (48, 948, 950, 952),
                   (948, 48, 952, 950), (48, 48, 950, 52), (498, 50, 502, 950)):
        b.rect(4, *bounds)
    p.layers = [_metal("source", "1/0", 0., 10e-6), _metal("drain", "2/0", -220e-6, 20e-6),
                _metal("collector", "3/0", 14e-6, 30e-6),
                _film("gate", 4, "gold", .3e-6, 10.2e-6, "thermal"),
                _film("substrate", 20, "sic", 200e-6, -200e-6, "thermal")]
    p.interfaces = [Interface("on_state", "drain", "source", RegionSpec(sources=["90/0"]), "load", 1e-7,
        purpose="device_load", reference_temperature=300., temperature_coefficient=.003),
        _physical_via("collection_vias", "source", "collector", 12, p, enclosure=2.)]
    _ports(p, "collector", (100, 445, 175, 555), "drain", (400, 400, 600, 600), 10., resistance=.001)
    p.terminals.append(Terminal("kelvin", "collector", rectangle(830, 815, 850, 835), "probe"))
    _thermal_stack(b, p)
    _backside(p, "drain", resistance=2e-6 + 20e-6 / (2 * 401.))
    p.mesh.max_size, p.mesh.min_size, p.mesh.contact_size = 60e-6, 1e-6, 30e-6
    p.mesh.refinements = [RefinementRegion("via_transfer", RegionSpec(sources=["12/0"]), 8e-6)]
    p.power.rules = RoutingRules(via_size=20e-6, via_pitch=40e-6, via_enclosure=2e-6,
        protected_masks=["1/0", "90/0", "4/0", "99/0", "20/0"])
    p.power.measurements += [MeasurementSpec("device_loss", "contact_heat_density", SupportSelector(contacts=["on_state"]), "integral"),
        MeasurementSpec("kelvin_potential", "potential", SupportSelector(layers=["collector"], regions=["terminal:kelvin"]), "mean")]
    p.sweeps = [SweepSpec("layers.collector.thickness", [30e-6, 40e-6], "electrothermal", "Fixed-service source strap thickness"),
                SweepSpec("thermal_boundaries.backside.value", [2.025e-6, 1.0025e-5], "electrothermal", "Die-attach thermal sensitivity")]
    return ("SiC MOSFET on-state interconnect model with four 320 × 320 µm active islands on a 1 mm die. "
            "Separated active zones, nonactive termination margins, insulated gate corridors and two elevated "
            "source straps make source collection visible. Eight geometric copper vias join the active source "
            "sheets to the straps; a passive Kelvin location samples their potential. Current sharing emerges "
            "from local j_z=(V_d−V_s)/Ron,sp(T), with Ron,sp=1e-7 Ω m² (1 mΩ cm²) at 300 K and alpha=0.003/K. "
            "Ron,sp includes intrinsic channel/drift/contact contributions, so substrate electrical resistance "
            "is not counted again. A fixed-on gate assumption, not the insulated drawing, controls this load. "
            "The 10 A service includes finite localized feeds and one backside thermal path. No body diode, "
            "avalanche, switching or planar/trench distinction is modeled."), dict(die_um=[1000., 1000.],
                requested_current_A=10., active_area_m2=4 * 320e-6**2, macrocell_count=4, via_count=8,
                on_state_specific_resistance_ohm_m2=1e-7, analytical_uniform_device_resistance_ohm=1e-7 / (4 * 320e-6**2),
                hotspot_layer="source", model_temperature_range_K=[300., 450.])


def _package(b, p):
    from etlayout.examples import _metal, rectangle
    from etlayout.power_examples import _physical_via, _thermal_stack
    b.rect(20, 0, 0, 1800, 1600)
    for bounds in ((100, 150, 320, 1450), (250, 250, 1350, 450), (250, 1150, 1350, 1350)):
        b.rect(1, *bounds)
    b.rect(2, 1180, 200, 1450, 1400)
    for bounds in ((1280, 700, 1650, 900), (1500, 600, 1750, 1000)):
        b.rect(3, *bounds)
    b.rect(99, 650, 600, 1000, 1000)
    for mask, centers in ((10, ((1250, 350), (1250, 1250))), (11, ((1350, 800),))):
        via = b.layout.create_cell("INTERPOSER_VIA_" + str(mask))
        b.rect(mask, -9, -9, 9, 9, cell=via)
        for x, y in centers:
            b.cell.insert(kdb.DCellInstArray(via.cell_index(), kdb.DTrans(x - 52.5, y - 52.5),
                kdb.DVector(35, 0), kdb.DVector(0, 35), 4, 4))
    p.layers = [_metal("m1", "1/0", 0., 20e-6), _metal("m2", "2/0", 45e-6, 20e-6),
                _metal("m3", "3/0", 90e-6, 20e-6),
                _film("substrate", 20, "alumina", 300e-6, -302e-6, "thermal")]
    p.interfaces = [_physical_via("branch_vias", "m1", "m2", 10, p, enclosure=4.),
                    _physical_via("output_vias", "m2", "m3", 11, p, enclosure=4.)]
    for interface in p.interfaces:
        lower, upper = sorted((p.layer(interface.layer_a), p.layer(interface.layer_b)), key=lambda layer: layer.z)
        # Match preparation's physical upper/lower-surface expression exactly;
        # regrouping float subtraction must not mutate a configured example.
        interface.height = upper.z - (lower.z + lower.thickness)
    _ports(p, "m3", (1575, 725, 1725, 875), "m1", (130, 700, 270, 900), 5.)
    _thermal_stack(b, p)
    _backside(p, "substrate", resistance=2e-5 + 300e-6 / (2 * 30.))
    p.mesh.max_size, p.mesh.min_size, p.mesh.contact_size = 100e-6, 2e-6, 35e-6
    p.mesh.refinements = [RefinementRegion("via_transfer", RegionSpec(sources=["10/0", "11/0"]), 6e-6)]
    p.power.rules = RoutingRules(via_size=16e-6, via_pitch=30e-6, via_enclosure=4e-6,
        keepouts=[rectangle(650, 600, 1000, 1000)], protected_masks=["99/0", "20/0"])
    for identifier, bounds in (("lower_branch", (1150, 250, 1350, 450)), ("upper_branch", (1150, 1150, 1350, 1350))):
        p.power.measurements.append(MeasurementSpec(identifier + "_current", "contact_normal_density",
            SupportSelector(contacts=["branch_vias"], geometry=rectangle(*bounds)), "integral", vector_component="signed"))
    p.sweeps = [SweepSpec("power.ports.drain.value", [2.5, 5., 7.5], "electrothermal", "Interposer service current (A)"),
                SweepSpec("layers.m1.thickness", [20e-6, 30e-6], "electrothermal", "Parallel-branch copper thickness")]
    return ("A 1.8 × 1.6 mm three-metal alumina interposer routes 5 A around a protected central opening. "
            "Two parallel lower-metal arms transfer through hierarchical 4 × 4 via arrays into a middle-metal "
            "collector, then through a third array into the upper output landing. Local finite input/output "
            "feeds leave sheet resistance and via sharing observable. Compare the two branch transfers and "
            "individual via currents; bends and access resistance create local crowding. Synthetic SiO₂-equivalent "
            "intermetal gaps and a single alumina backside path represent package heat transfer. This is a DC "
            "interconnect example, not RF impedance, inductance, switching or a package qualification."), dict(
                die_um=[1800., 1600.], requested_current_A=5., metal_levels=3, via_count=48,
                protected_opening_um=[350., 400.], hotspot_layer="m1")


def build_device_example(name, builder, p) -> bool:
    """Populate an E33–E40 physical project; generation never starts a solve."""
    if name not in DEVICE_EXAMPLES:
        return False
    if name in DEVICE_EXAMPLES[:4]:
        text, data = _thermal_teaching(name, builder, p)
    elif name in DEVICE_EXAMPLES[4:6]:
        text, data = _gan(name, builder, p)
    elif name == DEVICE_EXAMPLES[6]:
        text, data = _sic(builder, p)
    else:
        text, data = _package(builder, p)
    from etlayout.examples import rectangle
    width, height = data["die_um"]
    p.roi = rectangle(0, 0, width, height)
    p.solver.mode = "electrothermal"
    p.solver.relative_tolerance = p.solver.absolute_tolerance = 1e-6
    p.mesh.max_elements, p.mesh.max_dofs = 150000, 600000
    for layer in p.layers:
        if layer.id not in {"gate", "buffer", "substrate"}:
            layer.loss_role = "channel" if layer.id.startswith("channel") else "routing_metal"
    conductor_layers = [layer for layer in p.layers if layer.role == "conductor" and not layer.id.startswith("channel")]
    metal_layers = [layer.id for layer in conductor_layers if p.materials[layer.material_id].category == "metal"]
    sheet_layers = [layer.id for layer in conductor_layers if p.materials[layer.material_id].category != "metal"]
    contacts = [item.id for item in p.interfaces if item.electrical_kind in {"specific", "via"}]
    vias = [item.id for item in p.interfaces if item.electrical_kind == "via"]
    p.power.measurements += [
        MeasurementSpec("maximum_temperature", "temperature", limit=data.get("model_temperature_range_K", [300., 450.])[1]),
        MeasurementSpec("temperature_rise", "temperature_rise", reference_boundary="backside"),
        MeasurementSpec("regularized_hotspot", "temperature", SupportSelector(layers=[data["hotspot_layer"]]),
                        "maximum", averaging_length=min(width, height) * .05e-6),
        MeasurementSpec("routing_loss", "joule_density", SupportSelector(layers=[layer.id for layer in conductor_layers]), "integral"),
    ]
    if metal_layers:
        p.power.measurements.append(MeasurementSpec("metal_loading", "metal_current_density",
            SupportSelector(layers=metal_layers), "percentile", percentile=95.))
    if sheet_layers:
        p.power.measurements.append(MeasurementSpec("film_sheet_loading", "sheet_current",
            SupportSelector(layers=sheet_layers), "percentile", percentile=95.))
    if "drain" in {port.id for port in p.power.ports}:
        p.power.measurements += [MeasurementSpec("delivered_current", "port_current", SupportSelector(ports=["drain"]), "integral"),
                                MeasurementSpec("terminal_voltage", "port_voltage", SupportSelector(ports=["drain"]), "mean")]
    if contacts:
        p.power.measurements.append(MeasurementSpec("contact_via_loss", "contact_heat_density", SupportSelector(contacts=contacts), "integral"))
    if vias:
        p.power.measurements.append(MeasurementSpec("via_loading", "contact_normal_density", SupportSelector(contacts=vias), "rms"))
    palette = ["#477FA8", "#AD834E", "#668F83", "#9A74AF", "#88A6B7", "#BBC4CC"]
    for index, layer in enumerate(p.layers):
        layer.color = palette[index % len(palette)]
        if layer.role == "thermal":
            layer.opacity = .2 if layer.id in {"substrate", "buffer"} else .65
    data.update(generator_version=1, reference_temperature_K=300., thermal_reservoir="backside",
                no_prescribed_heat=True, max_elements=p.mesh.max_elements, max_dofs=p.mesh.max_dofs,
                validation="Generated, not simulated; assess convergence and mesh sensitivity separately.")
    p.notes = text + " All inputs are synthetic engineering assumptions at 300 K; thickness-averaged layers " \
        "omit depth-resolved gradients. External feed loss is deposited 25% locally and 75% outside the model." \
        + METADATA_MARKER + json.dumps(data, sort_keys=True, indent=2)
    return True
