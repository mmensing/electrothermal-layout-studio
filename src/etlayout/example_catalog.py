# SPDX-License-Identifier: MPL-2.0
"""Human-readable, stable metadata for the executable example library.

Operating points, sweeps, materials, and numerical metrics are deliberately read
from the generated Project/SimulationResult, never duplicated in this catalogue.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, replace


@dataclass(frozen=True)
class ExampleInfo:
    name: str
    title: str
    category: str
    level: str
    learning_goal: str
    layer: str
    field: str
    recommended_mode: str
    physical_scope: str
    family: str = ""
    reference_tier: str = "introductory"
    applicability: str = "synthetic functional demonstration"
    role: str = "tutorial"
    reference_status: str = "reference"
    recommended_scenario: str = "reference"
    workload: str = "compact steady calculation"
    capabilities: tuple[str, ...] = ()

    @property
    def id(self) -> str:
        return self.name.split("_", 1)[0]

    @property
    def evidence_level(self) -> str:
        """Compatibility alias for applicability, never a convergence claim."""
        return self.applicability

    def to_dict(self) -> dict[str, object]:
        return {"id": self.id, **asdict(self), "evidence_level": self.evidence_level}



_ENTRIES = (
    ExampleInfo("E1_straight", "Straight conductor", "Conductors and current sharing", "Starter",
        "Compare the solved resistance with the uniform-strip analytical limit.",
        "metal", "potential", "electrical",
        "Uniform thickness copper sheet; the analytical comparison applies at fixed 300 K."),
    ExampleInfo("E2_bent_taper", "Bent and tapered conductor", "Conductors and current sharing", "Starter",
        "See how a taper and a reentrant corner redistribute current.",
        "metal", "current_density", "electrical",
        "Ideal sharp corners have mesh-sensitive peak current density; refine before comparing peaks."),
    ExampleInfo("E3_parallel", "Unequal parallel routes", "Conductors and current sharing", "Starter",
        "Compare current sharing through unequal-width branches and common access buses.",
        "metal", "current_density", "electrical",
        "Distributed access resistance is included, so branch sharing is not a simple width-only ratio."),
    ExampleInfo("E4_multilayer", "Two-metal transfer", "Multilayer routing and packages", "Intermediate",
        "Follow lateral transport through a nonrectangular second layer and a finite via.",
        "m2", "potential", "electrical",
        "Separate sheet potentials couple through a finite copper via; this is a 2.5D representation."),
    ExampleInfo("E5_via_array", "Hierarchical via array", "Multilayer routing and packages", "Intermediate",
        "Inspect repeated layout instances and unequal transfer through a 4 × 5 via array.",
        "m2", "current_density", "electrical",
        "Individual via footprints are resolved; through-thickness current is represented by interface links."),
    ExampleInfo("E6_kelvin", "Kelvin sheet and contact test", "Contacts, sensing and extraction", "Intermediate",
        "Separate voltage sensing from current injection using zero-current floating electrodes.",
        "film", "potential", "electrical",
        "Floating sense electrodes are equipotential constraints and can perturb the sheet locally."),
    ExampleInfo("E7_hemt", "Interdigitated HEMT", "Semiconductor interconnects", "Intermediate",
        "Explore local gate control, finite source/drain contacts, and thermal coupling.",
        "channel", "sheet_current", "electrical",
        "Illustrative resistive 2DEG law, with ungated edge bypass; the insulated field plate has no electrostatic effect."),
    ExampleInfo("E8_hemt_routing", "HEMT with resistive routing", "Semiconductor interconnects", "Intermediate",
        "Compare thin access buses with the thicker routing in E7.",
        "source_metal", "current_density", "electrical",
        "Same effective channel law as E7; metal thickness changes routing resistance, not transistor electrostatics."),
    ExampleInfo("E9_sic", "Conducting SiC sheet", "Semiconductor interconnects", "Intermediate",
        "Connect carrier density and mobility to sheet conduction and contact loss.",
        "sic", "potential", "electrical",
        "Effective doped semiconductor resistor; no junction, depletion, or breakdown physics."),
    ExampleInfo("E10_thermal_stack", "Metal–oxide–silicon stack", "Heat flow and thermal interaction", "Intermediate",
        "Resolve lateral heat spreading and the resistance of a layered thermal path.",
        "substrate", "temperature", "electrothermal",
        "Thickness-averaged layers; the bottom boundary includes half of the substrate thickness."),
    ExampleInfo("E11_serpentine_heater", "Serpentine thin-film heater", "Heat flow and thermal interaction", "Advanced",
        "Inspect corner crowding, temperature feedback, and heat spreading beyond a five-pass trace.",
        "heater", "temperature", "electrothermal",
        "Illustrative platinum film on silicon, with an effective thermal interface; sharp-corner peaks need mesh refinement."),
    ExampleInfo("E12_via_ladder", "Three-level via ladder", "Multilayer routing and packages", "Advanced",
        "Follow sequential vertical transfer through two hierarchical 3 × 3 via banks.",
        "m2", "current_density", "electrothermal",
        "Three independent sheet potentials linked by finite copper vias; no full 3D via-sidewall field solution."),
    ExampleInfo("E13_perforated_spreader", "Perforated current spreader", "Conductors and current sharing", "Advanced",
        "See current diversion around staggered slots and diamond holes in a conducting sheet.",
        "spreader", "current_density", "electrothermal",
        "True void topology is preserved in the mesh; ideal hole corners produce mesh-sensitive current peaks."),
    ExampleInfo("E14_bridge_sensor", "Four-arm resistive bridge", "Contacts, sensing and extraction", "Advanced",
        "Read a differential bridge imbalance with separate current drive and floating midpoint probes.",
        "arm_br", "potential", "electrothermal",
        "Four effective sheet resistors and metal pads; resistor mismatch represents a sensor response without a mechanical model."),
    ExampleInfo("E15_thermal_crosstalk", "Dual-heater thermal crosstalk", "Heat flow and thermal interaction", "Advanced",
        "Inspect overlapping temperature fields from independently driven meanders on a shared substrate.",
        "substrate", "temperature", "electrothermal",
        "Effective sheet-to-substrate heat transfer; prescribed sources also permit a meaningful independent thermal solve."),
    ExampleInfo("E16_full_gate_hemt_array", "Full-width gated HEMT array", "Semiconductor interconnects", "Advanced",
        "Contrast E7's edge bypass with gate control that spans every channel in a four-channel array.",
        "channel", "sheet_current", "electrothermal",
        "Resistive 2DEG approximation with finite contacts; no velocity saturation, gate leakage, trapping, or electrostatic field-plate model."),
    ExampleInfo("E17_pulsed_thermal", "Pulsed heating and cooling", "Pulsed operation", "Starter",
        "Compare pulse heating and cooling with the uniform lumped thermal RC limit, then refine the time step.",
        "plate", "temperature", "thermal",
        "Prescribed heat drives uniform copper thermal storage; electrical Joule heating is excluded. Pulse width means high plateau."),
    ExampleInfo("E18_pulsed_conductor", "Pulsed electrothermal conductor", "Pulsed operation", "Intermediate",
        "Follow current pulses, Joule energy, delayed temperature peaks and cooling with temperature feedback.",
        "metal", "temperature", "electrothermal",
        "Backward Euler thermal storage with quasi-static electrical feedback; no electrical capacitance, inductance or switching losses."),
    ExampleInfo("E19_pulsed_multilayer", "Multilayer pulse and thermal crosstalk", "Pulsed operation", "Advanced",
        "Compare heater, substrate and unpowered receiver histories to locate the delayed crosstalk peak.",
        "substrate", "temperature", "thermal",
        "A prescribed single heat pulse transfers through finite interfaces and a shared silicon sheet; no electrical drive or through-thickness diffusion."),
    ExampleInfo("E20_high_current_rdl", "High-current multilayer RDL", "Multilayer routing and packages", "Engineering",
        "Compare branching buses, individual via loading and localized single/distributed feeds.",
        "m1", "current_density", "electrothermal",
        "Synthetic DC copper network with finite feeds and one substrate-to-backside heat path; no inductive or switching model.",
        "rdl", "engineering"),
    ExampleInfo("E21_long_finger_gan", "Long-finger GaN HEMT array", "Semiconductor interconnects", "Engineering",
        "Resolve coplanar opposing source/drain combs and compare equal-current finger feeds through upper redistribution.",
        "channel", "sheet_current", "electrothermal",
        "Synthetic local-bias 2DEG transport; all paths cross a gate. Insulated gate landing/routing and finite ohmic contacts; no saturation, breakdown, trapping or leakage prediction.",
        "gan", "engineering"),
    ExampleInfo("E22_multibank_gan", "Multi-bank GaN power die", "Semiconductor interconnects", "Engineering",
        "Compare bank sharing, source-potential spread and substrate thermal interaction through shared redistribution.",
        "source_rdl", "current_density", "electrothermal",
        "Hierarchical coplanar-comb banks share insulated gate and resistive power redistribution; hierarchy does not reduce finite-element cost.",
        "gan_banks", "engineering"),
    ExampleInfo("E23_sic_stripes", "SiC MOSFET on-state interconnect · stripes", "Semiconductor interconnects", "Engineering",
        "Separate distributed on-state device loss from source metal, vias and external feed losses.",
        "source", "current_density", "electrothermal",
        "Area-specific synthetic on-state load with insulated gate routing and a nonactive termination margin; fixed gate assumption, no body diode, avalanche or switching physics.",
        "sic_stripes", "engineering"),
    ExampleInfo("E24_sic_cells", "SiC MOSFET on-state interconnect · macrocells", "Semiconductor interconnects", "Engineering",
        "Compare bond, clip and bump footprints at fixed active area and delivered current with passive Kelvin sensing.",
        "collector", "current_density", "electrothermal",
        "Each tile homogenizes physical cells; insulated gate runners occupy the gaps and load current follows local sheet voltages, with no prescribed equal sharing.",
        "sic_cells", "engineering"),
    ExampleInfo("E25_stepped_width_series", "Stepped-width series reference", "Conductors and current sharing", "Starter",
        "Compare series resistance with access spreading at width changes.",
        "metal", "potential", "electrical",
        "Synthetic copper coupon at fixed reference temperature; abrupt width transitions add spreading resistance.", reference_tier="extended"),
    ExampleInfo("E26_dogbone_coupon", "Dog-bone current-crowding coupon", "Conductors and current sharing", "Intermediate",
        "Separate a physically averaged neck loading from a mesh-sensitive corner maximum.",
        "metal", "current_density", "electrothermal",
        "Joule-heated resistive metal; current crowding does not predict melting, electromigration lifetime or fracture.", reference_tier="extended"),
    ExampleInfo("E27_transfer_length_coupon", "Finite-contact transfer-length coupon", "Contacts, sensing and extraction", "Intermediate",
        "Compare contact drop and passive Kelvin voltage with the contact transfer length.",
        "film", "potential", "electrical",
        "Finite area-specific ohmic contacts to a resistive sheet; no Schottky, tunneling or calibrated contact chemistry.", reference_tier="extended"),
    ExampleInfo("E28_t_junction_splitter", "Three-terminal T-junction splitter", "Conductors and current sharing", "Starter",
        "Track signed terminal currents through a branched route with two return landings.",
        "metal", "current_density", "electrical",
        "Prescribed branch withdrawal and a balancing reference return; no passive-divider ratio, microwave S-parameters, inductance or capacitance.", reference_tier="extended"),
    ExampleInfo("E29_annular_bus", "Annular current-spreading bus", "Conductors and current sharing", "Intermediate",
        "Follow current around a protected central opening and compare the two annular paths.",
        "metal", "current_density", "electrothermal",
        "Polygonal approximation to a curved copper bus; compare integral or averaged quantities through mesh refinement.", reference_tier="extended"),
    ExampleInfo("E30_via_daisy_chain", "Multilevel via daisy chain", "Multilayer routing and packages", "Advanced",
        "Follow repeated serial transfers through physical via banks and inspect their loss contributions.",
        "m2", "current_density", "electrothermal",
        "Separate sheet potentials and finite-area vertical conductances; via interiors are not resolved in 3D.", reference_tier="extended"),
    ExampleInfo("E31_resistor_ladder", "Tapped resistive ladder", "Contacts, sensing and extraction", "Starter",
        "Read passive midpoint voltages and compare the ladder with its analytical resistance network.",
        "film", "potential", "electrical",
        "Effective resistive film with zero-current floating sense footprints; no mechanical sensing model.", reference_tier="extended"),
    ExampleInfo("E32_parallel_resistor_array", "Parallel resistor array with resistive buses", "Conductors and current sharing", "Advanced",
        "Measure how finite access buses change sharing among repeated resistive strips.",
        "film", "sheet_current", "electrothermal",
        "Distributed film/contact/bus resistance sets current sharing; per-strip currents are not prescribed.", reference_tier="extended"),
    ExampleInfo("E33_suspended_bridge_heater", "Thermal bridge heater", "Heat flow and thermal interaction", "Intermediate",
        "Compare lateral heat escape through anchors with weak distributed thermal attachment.",
        "bridge", "temperature", "electrothermal",
        "Effective suspended-film thermal path; no mechanical suspension, gas-flow or radiation model.", reference_tier="extended"),
    ExampleInfo("E34_interleaved_heater_sensor", "Interleaved heater and sensor", "Heat flow and thermal interaction", "Advanced",
        "Separate the driven heater, weakly biased resistive sensor and common substrate temperatures.",
        "heater", "temperature", "electrothermal",
        "Electrical isolation and thermal crosstalk use existing sheet laws; no invented transduction or measurement electronics.", reference_tier="extended"),
    ExampleInfo("E35_localized_die_attach", "Localized backside die attachment", "Heat flow and thermal interaction", "Advanced",
        "Observe lateral spreading when the thermal reservoir touches only a selected backside patch.",
        "heater", "temperature", "electrothermal",
        "Thickness-averaged substrate and finite attachment area; no resolved through-thickness transient or package stress.", reference_tier="extended"),
    ExampleInfo("E36_pulsed_hotspot_neck", "Pulsed constriction and cooling", "Pulsed operation", "Advanced",
        "Follow transient neck heating and temperature-dependent resistance over a current pulse train.",
        "neck", "temperature", "electrothermal",
        "Quasi-static electrical/thermal storage approximation; no fusing, electrical switching loss or inductive overshoot.", reference_tier="extended"),
    ExampleInfo("E37_gated_hemt_coupon", "Fully gated HEMT contact coupon", "Semiconductor interconnects", "Intermediate",
        "Inspect a single fully controlled channel with finite source and drain contacts.",
        "channel_a", "sheet_current", "electrothermal",
        "Local-bias 2DEG resistance law; no ungated bypass, velocity saturation, trapping, breakdown or predicted off-state leakage.", reference_tier="extended"),
    ExampleInfo("E38_dual_bank_gan", "Independently driven GaN banks", "Semiconductor interconnects", "Advanced",
        "Compare separately driven gated banks through their shared source and thermal environment.",
        "channel_a", "sheet_current", "electrothermal",
        "Synthetic fully gated conducting paths; no switching, gate leakage, high-field saturation or calibrated transistor rating.", reference_tier="extended"),
    ExampleInfo("E39_sic_island_die", "SiC on-state die with active islands", "Semiconductor interconnects", "Engineering",
        "Inspect area-normalized vertical current sharing between protected active islands and finite collection straps.",
        "source", "current_density", "electrothermal",
        "SiC MOSFET on-state interconnect model with fixed-gate area-specific resistance; no body diode, avalanche or switching physics.", reference_tier="extended"),
    ExampleInfo("E40_package_interposer", "Three-level package interposer", "Multilayer routing and packages", "Engineering",
        "Trace localized package landings through millimeter buses and hierarchical via banks.",
        "m1", "current_density", "electrothermal",
        "Resistive/thermal 2.5D package approximation; hierarchy does not reduce FEM cost and no signal-integrity model is included.", reference_tier="extended"),
)

# Subject order and learning order are independent of the historical E-number.
SUBJECTS = (
    "Conductors and current sharing",
    "Contacts, sensing and extraction",
    "Multilayer routing and packages",
    "Heat flow and thermal interaction",
    "Pulsed operation",
    "Semiconductor interconnects",
)


@dataclass(frozen=True)
class LearningPath:
    id: str
    title: str
    purpose: str
    examples: tuple[str, ...]


START_HERE = ("E1", "E6", "E4", "E10", "E17", "E37")
LEARNING_PATHS = (
    LearningPath("conductors", "Conductors and current sharing",
        "Start from a strip, then compare branches, spreading and averaged crowding.",
        ("E1", "E25", "E3", "E28", "E2", "E26", "E29", "E13", "E32")),
    LearningPath("contacts", "Contacts, sensing and extraction",
        "Separate current drive, passive sensing and finite-contact extraction.", ("E6", "E27", "E31", "E14")),
    LearningPath("routing", "Multilayer routing and packages",
        "Follow one vertical transfer before studying arrays and package-scale feeds.", ("E4", "E5", "E12", "E30", "E20", "E40")),
    LearningPath("heat", "Heat flow and thermal interaction",
        "Inspect a layered heat path, then lateral escape, sensing and localized attachment.",
        ("E10", "E11", "E33", "E15", "E34", "E35")),
    LearningPath("pulses", "Pulsed operation",
        "Begin with the thermal RC reference before adding Joule feedback and spatial crosstalk.", ("E17", "E18", "E19", "E36")),
    LearningPath("gan", "Fully gated GaN: coupon to power die",
        "Use the recommended voltage-controlled coupon, then increase active width and bank complexity.",
        ("E37", "E16", "E21", "E38", "E22")),
    LearningPath("sic", "SiC: resistor to on-state collection",
        "E9 is an optional semiconductor-material resistor reference; start on-state collection with the recommended E39 scenario, then compare stripes and macrocells.",
        ("E9", "E39", "E23", "E24")),
    LearningPath("legacy-hemt", "Legacy HEMT routing comparison",
        "Retained E7/E8 comparisons include ungated edge bypass; use E37/E16 for fully gated teaching.", ("E7", "E8")),
)

# Public learning metadata is separate from reference_tier, which retains the
# established numerical-test scheduling policy. No operating values live here.
_GUIDANCE = {
    "E1": ("benchmark", "compact steady calculation", ("sheet conduction", "analytical resistance")),
    "E2": ("tutorial", "compact steady calculation", ("current crowding", "averaged hotspots")),
    "E3": ("comparison", "compact steady calculation", ("parallel sharing", "resistive buses")),
    "E4": ("tutorial", "multilayer steady calculation", ("vertical transfer", "finite via")),
    "E5": ("comparison", "multilayer steady calculation", ("via arrays", "hierarchy")),
    "E6": ("tutorial", "compact steady calculation", ("passive sensing", "finite contacts")),
    "E7": ("legacy comparison", "multilayer steady calculation", ("local gate bias", "ungated bypass")),
    "E8": ("legacy comparison", "multilayer steady calculation", ("local gate bias", "routing resistance", "ungated bypass")),
    "E9": ("tutorial", "compact steady calculation", ("semiconductor resistor", "finite contacts")),
    "E10": ("tutorial", "multilayer steady calculation", ("thermal stack", "backside reservoir")),
    "E11": ("comparison", "multilayer steady calculation", ("Joule heating", "prescribed heating", "current crowding")),
    "E12": ("comparison", "multilayer steady calculation", ("vertical transfer", "via arrays", "hierarchy")),
    "E13": ("comparison", "compact steady calculation", ("protected openings", "current spreading")),
    "E14": ("comparison", "multilayer steady calculation", ("passive sensing", "resistor bridge")),
    "E15": ("comparison", "multilayer steady calculation", ("thermal crosstalk", "independent heaters")),
    "E16": ("comparison", "multilayer steady calculation", ("fully gated channels", "finite contacts")),
    "E17": ("benchmark", "transient calculation", ("thermal storage", "prescribed pulse", "analytical thermal RC")),
    "E18": ("tutorial", "transient calculation", ("current pulse", "Joule heating", "thermal storage")),
    "E19": ("comparison", "multilayer transient calculation", ("prescribed pulse", "thermal crosstalk", "thermal storage")),
    "E20": ("engineering study", "engineering benchmark; review resource limits", ("routing variants", "physical ports", "via arrays", "optimization")),
    "E21": ("engineering study", "engineering benchmark; review resource limits", ("fully gated channels", "routing variants", "finite contacts", "optimization")),
    "E22": ("engineering study", "engineering benchmark; review resource limits", ("bank sharing", "routing variants", "hierarchy", "optimization")),
    "E23": ("engineering study", "engineering benchmark; review resource limits", ("area-specific on-state load", "routing variants", "optimization")),
    "E24": ("engineering study", "engineering benchmark; review resource limits", ("area-specific on-state load", "routing variants", "passive sensing", "optimization")),
    "E25": ("comparison", "compact steady calculation", ("series resistance", "access spreading")),
    "E26": ("comparison", "compact steady calculation", ("current crowding", "averaged hotspots")),
    "E27": ("comparison", "compact steady calculation", ("finite contacts", "passive sensing", "transfer-length extraction")),
    "E28": ("tutorial", "compact steady calculation", ("multiple power ports", "signed current balance")),
    "E29": ("comparison", "compact steady calculation", ("current spreading", "protected opening")),
    "E30": ("comparison", "multilayer steady calculation", ("vertical transfer", "via arrays", "hierarchy")),
    "E31": ("tutorial", "compact steady calculation", ("passive sensing", "resistor network")),
    "E32": ("comparison", "multilayer steady calculation", ("parallel sharing", "finite contacts", "resistive buses")),
    "E33": ("comparison", "multilayer steady calculation", ("thermal anchors", "effective suspended film")),
    "E34": ("comparison", "multilayer steady calculation", ("thermal crosstalk", "resistive sensing")),
    "E35": ("comparison", "multilayer steady calculation", ("localized attachment", "thermal spreading")),
    "E36": ("comparison", "transient calculation", ("current pulse", "current crowding", "thermal storage")),
    "E37": ("tutorial", "multilayer steady calculation", ("fully gated channel", "finite contacts", "voltage-controlled scenario")),
    "E38": ("comparison", "multilayer steady calculation", ("bank sharing", "gate mismatch", "voltage-controlled scenarios")),
    "E39": ("engineering study", "engineering benchmark; review resource limits", ("area-specific on-state load", "active islands", "lower-current scenario")),
    "E40": ("engineering study", "engineering benchmark; review resource limits", ("physical ports", "via arrays", "hierarchy")),
}
_ENTRIES = tuple(replace(entry,
    role=_GUIDANCE[entry.id][0], workload=_GUIDANCE[entry.id][1], capabilities=_GUIDANCE[entry.id][2],
    applicability=("analytical reference" if entry.id in {"E1", "E17"} else
                   "synthetic engineering study" if _GUIDANCE[entry.id][0] == "engineering study" else
                   "synthetic functional demonstration"),
    reference_status="stress" if entry.id == "E39" else "reference",
    recommended_scenario={"E37": "voltage_controlled", "E39": "continuous_reference"}.get(entry.id, "reference"))
    for entry in _ENTRIES)
EXAMPLE_CATALOG = {entry.name: entry for entry in _ENTRIES}


def example_info(name: str) -> ExampleInfo:
    """Resolve a full name or an unambiguous short ID (E1, E2, …)."""
    for entry in _ENTRIES:
        if name.lower() in (entry.name.lower(), entry.id.lower()):
            return entry
    raise ValueError(f"Unknown example {name!r}; choose {', '.join(x.id for x in _ENTRIES)}")
