# SPDX-License-Identifier: MPL-2.0
"""Human-readable, stable metadata for the executable example library.

Operating points, sweeps, materials, and numerical metrics are deliberately read
from the generated Project/SimulationResult, never duplicated in this catalogue.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass


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

    @property
    def id(self) -> str:
        return self.name.split("_", 1)[0]

    def to_dict(self) -> dict[str, str]:
        return {"id": self.id, **asdict(self)}


_ENTRIES = (
    ExampleInfo("E1_straight", "Straight conductor", "Fundamentals", "Starter",
        "Compare the solved resistance with the uniform-strip analytical limit.",
        "metal", "potential", "electrical",
        "Uniform thickness copper sheet; the analytical comparison applies at fixed 300 K."),
    ExampleInfo("E2_bent_taper", "Bent and tapered conductor", "Fundamentals", "Starter",
        "See how a taper and a reentrant corner redistribute current.",
        "metal", "current_density", "electrical",
        "Ideal sharp corners have mesh-sensitive peak current density; refine before comparing peaks."),
    ExampleInfo("E3_parallel", "Unequal parallel routes", "Fundamentals", "Starter",
        "Compare current sharing through unequal-width branches and common access buses.",
        "metal", "current_density", "electrical",
        "Distributed access resistance is included, so branch sharing is not a simple width-only ratio."),
    ExampleInfo("E4_multilayer", "Two-metal transfer", "Interconnects", "Intermediate",
        "Follow lateral transport through a nonrectangular second layer and a finite via.",
        "m2", "potential", "electrical",
        "Separate sheet potentials couple through a finite copper via; this is a 2.5D representation."),
    ExampleInfo("E5_via_array", "Hierarchical via array", "Interconnects", "Intermediate",
        "Inspect repeated layout instances and unequal transfer through a 4 × 5 via array.",
        "m2", "current_density", "electrical",
        "Individual via footprints are resolved; through-thickness current is represented by interface links."),
    ExampleInfo("E6_kelvin", "Kelvin sheet and contact test", "Measurements", "Intermediate",
        "Separate voltage sensing from current injection using zero-current floating electrodes.",
        "film", "potential", "electrical",
        "Floating sense electrodes are equipotential constraints and can perturb the sheet locally."),
    ExampleInfo("E7_hemt", "Interdigitated HEMT", "Devices", "Intermediate",
        "Explore local gate control, finite source/drain contacts, and thermal coupling.",
        "channel", "sheet_current", "electrical",
        "Illustrative resistive 2DEG law, with ungated edge bypass; the insulated field plate has no electrostatic effect."),
    ExampleInfo("E8_hemt_routing", "HEMT with resistive routing", "Devices", "Intermediate",
        "Compare thin access buses with the thicker routing in E7.",
        "source_metal", "current_density", "electrical",
        "Same effective channel law as E7; metal thickness changes routing resistance, not transistor electrostatics."),
    ExampleInfo("E9_sic", "Conducting SiC sheet", "Devices", "Intermediate",
        "Connect carrier density and mobility to sheet conduction and contact loss.",
        "sic", "potential", "electrical",
        "Effective doped semiconductor resistor; no junction, depletion, or breakdown physics."),
    ExampleInfo("E10_thermal_stack", "Metal–oxide–silicon stack", "Thermal", "Intermediate",
        "Resolve lateral heat spreading and the resistance of a layered thermal path.",
        "substrate", "temperature", "electrothermal",
        "Thickness-averaged layers; the bottom boundary includes half of the substrate thickness."),
    ExampleInfo("E11_serpentine_heater", "Serpentine thin-film heater", "Thermal", "Advanced",
        "Inspect corner crowding, temperature feedback, and heat spreading beyond a five-pass trace.",
        "heater", "temperature", "electrothermal",
        "Illustrative platinum film on silicon, with an effective thermal interface; sharp-corner peaks need mesh refinement."),
    ExampleInfo("E12_via_ladder", "Three-level via ladder", "Interconnects", "Advanced",
        "Follow sequential vertical transfer through two hierarchical 3 × 3 via banks.",
        "m2", "current_density", "electrothermal",
        "Three independent sheet potentials linked by finite copper vias; no full 3D via-sidewall field solution."),
    ExampleInfo("E13_perforated_spreader", "Perforated current spreader", "Interconnects", "Advanced",
        "See current diversion around staggered slots and diamond holes in a conducting sheet.",
        "spreader", "current_density", "electrothermal",
        "True void topology is preserved in the mesh; ideal hole corners produce mesh-sensitive current peaks."),
    ExampleInfo("E14_bridge_sensor", "Four-arm resistive bridge", "Measurements", "Advanced",
        "Read a differential bridge imbalance with separate current drive and floating midpoint probes.",
        "arm_br", "potential", "electrothermal",
        "Four effective sheet resistors and metal pads; resistor mismatch represents a sensor response without a mechanical model."),
    ExampleInfo("E15_thermal_crosstalk", "Dual-heater thermal crosstalk", "Thermal", "Advanced",
        "Inspect overlapping temperature fields from independently driven meanders on a shared substrate.",
        "substrate", "temperature", "electrothermal",
        "Effective sheet-to-substrate heat transfer; prescribed sources also permit a meaningful independent thermal solve."),
    ExampleInfo("E16_full_gate_hemt_array", "Full-width gated HEMT array", "Devices", "Advanced",
        "Contrast E7's edge bypass with gate control that spans every channel in a four-channel array.",
        "channel", "sheet_current", "electrothermal",
        "Resistive 2DEG approximation with finite contacts; no velocity saturation, gate leakage, trapping, or electrostatic field-plate model."),
    ExampleInfo("E17_pulsed_thermal", "Pulsed heating and cooling", "Transient", "Starter",
        "Compare pulse heating and cooling with the uniform lumped thermal RC limit, then refine the time step.",
        "plate", "temperature", "thermal",
        "Prescribed heat drives uniform copper thermal storage; electrical Joule heating is excluded. Pulse width means high plateau."),
    ExampleInfo("E18_pulsed_conductor", "Pulsed electrothermal conductor", "Transient", "Intermediate",
        "Follow current pulses, Joule energy, delayed temperature peaks and cooling with temperature feedback.",
        "metal", "temperature", "electrothermal",
        "Backward Euler thermal storage with quasi-static electrical feedback; no electrical capacitance, inductance or switching losses."),
    ExampleInfo("E19_pulsed_multilayer", "Multilayer pulse and thermal crosstalk", "Transient", "Advanced",
        "Compare heater, substrate and unpowered receiver histories to locate the delayed crosstalk peak.",
        "substrate", "temperature", "thermal",
        "A prescribed single heat pulse transfers through finite interfaces and a shared silicon sheet; no electrical drive or through-thickness diffusion."),
    ExampleInfo("E20_high_current_rdl", "High-current multilayer RDL", "Power benchmarks", "Engineering",
        "Compare branching buses, individual via loading and localized single/distributed feeds.",
        "m1", "current_density", "electrothermal",
        "Synthetic DC copper network with finite feeds and one substrate-to-backside heat path; no inductive or switching model.",
        "rdl", "engineering"),
    ExampleInfo("E21_long_finger_gan", "Long-finger GaN HEMT array", "Power benchmarks", "Engineering",
        "Resolve coplanar opposing source/drain combs and compare equal-current finger feeds through upper redistribution.",
        "channel", "sheet_current", "electrothermal",
        "Synthetic local-bias 2DEG transport; all paths cross a gate. Insulated gate landing/routing and finite ohmic contacts; no saturation, breakdown, trapping or leakage prediction.",
        "gan", "engineering"),
    ExampleInfo("E22_multibank_gan", "Multi-bank GaN power die", "Power benchmarks", "Engineering",
        "Compare bank sharing, source-potential spread and substrate thermal interaction through shared redistribution.",
        "source_rdl", "current_density", "electrothermal",
        "Hierarchical coplanar-comb banks share insulated gate and resistive power redistribution; hierarchy does not reduce finite-element cost.",
        "gan_banks", "engineering"),
    ExampleInfo("E23_sic_stripes", "SiC MOSFET on-state interconnect · stripes", "Power benchmarks", "Engineering",
        "Separate distributed on-state device loss from source metal, vias and external feed losses.",
        "source", "current_density", "electrothermal",
        "Area-specific synthetic on-state load with insulated gate routing and a nonactive termination margin; fixed gate assumption, no body diode, avalanche or switching physics.",
        "sic_stripes", "engineering"),
    ExampleInfo("E24_sic_cells", "SiC MOSFET on-state interconnect · macrocells", "Power benchmarks", "Engineering",
        "Compare bond, clip and bump footprints at fixed active area and delivered current with passive Kelvin sensing.",
        "collector", "current_density", "electrothermal",
        "Each tile homogenizes physical cells; insulated gate runners occupy the gaps and load current follows local sheet voltages, with no prescribed equal sharing.",
        "sic_cells", "engineering"),
)

EXAMPLE_CATALOG = {entry.name: entry for entry in _ENTRIES}


def example_info(name: str) -> ExampleInfo:
    """Resolve a full name or an unambiguous short ID (E1, E2, …)."""
    for entry in _ENTRIES:
        if name.lower() in (entry.name.lower(), entry.id.lower()):
            return entry
    raise ValueError(f"Unknown example {name!r}; choose {', '.join(x.id for x in _ENTRIES)}")
