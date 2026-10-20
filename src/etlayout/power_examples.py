# SPDX-License-Identifier: MPL-2.0
"""Deterministic synthetic power-device benchmarks; geometry is in micrometers.

Presets are not device ratings. The existing sheet, local-gate and distributed
load laws do all calculations. Generation never launches a solver.
"""
from __future__ import annotations

import json
import math
from dataclasses import asdict, dataclass

import klayout.db as kdb

from etlayout.examples import UM, _metal, rectangle
from etlayout.models import (
    DesignVariable,
    DeviceGroup,
    ElectricalModel,
    Interface,
    MeasurementSpec,
    ObjectiveSpec,
    OperatingScenario,
    OptimizationExperiment,
    PhysicalLayer,
    PhysicalPort,
    PortLanding,
    RefinementRegion,
    RegionSpec,
    RoutingRecipe,
    RoutingRules,
    SupportSelector,
    SweepSpec,
    Terminal,
    ThermalBoundary,
    TransientDrive,
    Waveform,
)

FAMILIES = {
    "E20_high_current_rdl": "rdl", "E21_long_finger_gan": "gan",
    "E22_multibank_gan": "gan_banks", "E23_sic_stripes": "sic_stripes", "E24_sic_cells": "sic_cells",
}
THERMAL_SCENARIOS = {"attached": 2e-6, "resistive_attach": 1e-5}  # m² K/W, synthetic package interfaces
METADATA_MARKER = "\n\nExample configuration (SI):\n"

# Shared generation choices: builders and library navigation use the same tables.
FAMILY_VARIANTS = {
    "rdl": ("uniform_single", "graded_single", "uniform_distributed", "graded_distributed", "poor_single"),
    "gan": ("single", "dual", "distributed"),
    "gan_banks": ("single", "dual", "distributed", "asymmetric"),
    "sic_stripes": ("narrow", "wide", "cross"),
    "sic_cells": ("bonds", "clip", "bumps"),
}
DEFAULT_VARIANTS = {"rdl": "uniform_single", "gan": "single", "gan_banks": "distributed",
                    "sic_stripes": "cross", "sic_cells": "bonds"}
_SIC_PRESETS = {"compact": (250., .25), "reference": (1000., 10.), "large": (2000., 40.)}
FAMILY_PRESETS = {
    "gan": {"compact": (4, 60., .075), "small": (16, 250., 1.5),
            "reference": (32, 500., 5.), "large": (64, 1000., 20.)},
    "gan_banks": {"compact": (2, 2, 2, 60., .15), "reference": (2, 2, 16, 500., 10.),
                  "large": (2, 4, 16, 500., 20.), "stress": (2, 4, 16, 500., 30.)},
    "rdl": {"compact": (200., 1.), "reference": (1000., 10.), "large": (2000., 20.)},
    "sic_stripes": _SIC_PRESETS,
    "sic_cells": _SIC_PRESETS,
}


def family_choices(name):
    """Enumerate builder-supported options without generating geometry or solving."""
    family = FAMILIES[name]
    return dict(presets=tuple(FAMILY_PRESETS[family]), variants=FAMILY_VARIANTS[family],
                thermal=tuple(THERMAL_SCENARIOS))


@dataclass(frozen=True)
class ExampleSettings:
    family: str
    preset: str
    variant: str
    current: float
    thermal: str
    side_um: float = 1000.
    fingers: int = 0
    gate_width_um: float = 0.
    bank_columns: int = 1
    bank_rows: int = 1
    max_elements: int = 200000
    max_dofs: int = 900000
    memory_budget_bytes: int = 3 * 1024**3
    tier: str = "engineering"


def settings(name, *, preset=None, variant=None, current=None, thermal=None, fingers=None, gate_width_um=None):
    family = FAMILIES[name]
    preset = preset or "reference"
    variant = variant or DEFAULT_VARIANTS[family]
    if variant not in FAMILY_VARIANTS[family]:
        raise ValueError(f"{family} variants: {', '.join(FAMILY_VARIANTS[family])}")
    sizes = FAMILY_PRESETS[family]
    if family == "gan":
        if preset not in sizes:
            raise ValueError(f"gan presets: {', '.join(sizes)}")
        count, width, nominal = sizes[preset]
        options = dict(fingers=count, gate_width_um=width, side_um=0.)
    elif family == "gan_banks":
        if preset not in sizes:
            raise ValueError(f"gan_banks presets: {', '.join(sizes)}")
        columns, rows, count, width, nominal = sizes[preset]
        options = dict(fingers=count, gate_width_um=width, bank_columns=columns, bank_rows=rows,
                       side_um=800. if preset == "compact" else 1200.)
    else:
        if preset not in sizes:
            raise ValueError(f"{family} presets: {', '.join(sizes)}")
        side, nominal = sizes[preset]
        options = dict(side_um=side)
    if fingers is not None or gate_width_um is not None:
        if not family.startswith("gan"):
            raise ValueError("Finger count and conducting gate width apply only to GaN families")
        if fingers is not None:
            if isinstance(fingers, bool) or not isinstance(fingers, int) or not 1 <= fingers <= 512:
                raise ValueError("Gate-finger count must be an integer in [1, 512]")
            options["fingers"] = fingers
        if gate_width_um is not None:
            if isinstance(gate_width_um, bool) or not isinstance(gate_width_um, (float, int)) or not math.isfinite(gate_width_um) or not 40 <= gate_width_um <= 5000:
                raise ValueError("Conducting gate width must be in [40, 5000] µm; this is not conduction distance")
            options["gate_width_um"] = float(gate_width_um)
    current = nominal if current is None else current
    if isinstance(current, bool) or not isinstance(current, (float, int)) or not math.isfinite(current) or current <= 0:
        raise ValueError("Requested current must be finite and positive; no automatic current reduction")
    thermal = thermal or "attached"
    if thermal not in THERMAL_SCENARIOS:
        raise ValueError(f"Thermal scenarios: {', '.join(THERMAL_SCENARIOS)}")
    tier = "compact" if preset == "compact" else "large" if preset in {"large", "stress"} else "engineering"
    budget = dict(max_elements=80000, max_dofs=350000, memory_budget_bytes=1024**3) if tier == "compact" else {}
    if family == "gan_banks" and tier != "compact":
        budget = dict(max_elements=450000, max_dofs=2000000, memory_budget_bytes=6 * 1024**3)
    if family == "gan" and preset in {"reference", "large"}:
        budget = dict(max_elements=650000, max_dofs=1500000, memory_budget_bytes=8 * 1024**3)
    # Opt-in long/large cases deliberately have bounded resources; raising them
    # is an explicit user decision, never substitution of smaller geometry.
    return ExampleSettings(family, preset, variant, float(current), thermal, tier=tier, **options, **budget)


def metadata(project):
    return json.loads(project.notes.split(METADATA_MARKER, 1)[1]) if METADATA_MARKER in project.notes else {}


def reference_summary(name):
    config = settings(name)
    if config.family == "gan":
        dimensions = f"{config.fingers} × {config.gate_width_um:g} µm conducting gate width"
    elif config.family == "gan_banks":
        dimensions = f"{config.bank_columns} × {config.bank_rows} banks; {config.fingers} × {config.gate_width_um:g} µm per bank"
    else:
        dimensions = f"{config.side_um:g} × {config.side_um:g} µm die"
    currents = [settings(name, preset=preset).current for preset in ("compact", "reference", "large")]
    return (f"{dimensions}; nominal {config.current:g} A; preset requests {min(currents):g}–{max(currents):g} A "
            f"(review model limits); engineering mesh up to {config.max_dofs:,} DOFs. Synthetic assumptions; see project notes.")


def _physical_via(identifier, a, b, mask, p, *, enclosure=.25):
    lower, upper = sorted((p.layer(a), p.layer(b)), key=lambda layer: layer.z)
    return Interface(identifier, a, b, RegionSpec(sources=[f"{mask}/0"]), "via", material_id="copper",
                     height=upper.z - (lower.z + lower.thickness), thermal_kind="stack", model_kind="physical_via",
                     height_mode="stack", enclosure=enclosure * UM)


def _port(p, identifier, layer, footprints, current, *, feed_resistance=.001):
    landings = []
    for index, bounds in enumerate(footprints):
        terminal_id = f"{identifier}_landing_{index + 1}"
        p.terminals.append(Terminal(terminal_id, layer, rectangle(*bounds), "current" if current else "ground", 0.))
        landings.append(PortLanding(terminal_id, feed_resistance * len(footprints), .25))
    p.power.ports.append(PhysicalPort(identifier, landings, "current" if current else "ground", current,
                                    name=f"{identifier.title()} · localized finite feeds"))


def _thermal_stack(builder, project):
    """Nearest-sheet thermal paths, with existing physical links replacing gaps.

    These examples use one source mask per layer. Endpoint intersections and
    exclusions remain live after routing edits: higher footprints and physical
    vias/contacts remove bypasses without requesting out-of-layer support.
    Gaps are explicitly synthetic SiO₂-equivalent insulation, not ideal sinks.
    """
    ordered = sorted(project.layers, key=lambda layer: layer.z)
    masks = {layer.id: layer.geometry.sources[0] for layer in ordered}
    regions = {key: kdb.Region(builder.cell.begin_shapes_rec(builder.layout.layer(int(mask.split('/')[0]), 0)))
               for key, mask in masks.items()}
    replacements = {}
    for interface in project.interfaces:
        if interface.thermal_kind != "none":
            replacements.setdefault(frozenset((interface.layer_a, interface.layer_b)), []).extend(interface.geometry.sources)
    for index, upper in enumerate(ordered[1:], start=1):
        remaining = regions[upper.id].dup()
        intervening = []
        for lower in reversed(ordered[:index]):
            exclusions = replacements.get(frozenset((upper.id, lower.id)), [])
            support = remaining & regions[lower.id]
            for mask in exclusions:
                excluded = kdb.Region(builder.cell.begin_shapes_rec(builder.layout.layer(int(mask.split('/')[0]), 0)))
                support -= excluded
            if not support.is_empty():
                gap = upper.z - lower.z - lower.thickness
                if gap < -1e-15:
                    raise ValueError("Example layers overlap vertically; cannot construct a credible thermal stack")
                sources = list(dict.fromkeys([masks[upper.id], masks[lower.id]]))
                project.interfaces.append(Interface(f"heat_{upper.id}_{lower.id}", upper.id, lower.id,
                    RegionSpec(sources=sources, operation="intersection",
                               exclude_sources=list(dict.fromkeys([*intervening, *exclusions]))), thermal_kind="stack",
                    material_id="sio2" if gap > 1e-15 else None, height=gap if gap > 1e-15 else 1e-6))
            remaining -= regions[lower.id]
            intervening.append(masks[lower.id])


def _gan(builder, p, config):
    count, width = config.fingers, config.gate_width_um
    banks = config.family == "gan_banks"
    columns, rows = config.bank_columns, config.bank_rows
    pitch = max(config.side_um, count * 20. + 500., width + 600.) if banks else 0.
    die_w, die_h = ((columns * pitch, rows * pitch) if banks else (count * 20. + 500., width + 600.))
    builder.rect(20, 0, 0, die_w, die_h)
    finger = builder.layout.create_cell("GATED_FINGER")
    for mask, bounds in ((1, (0, 0, 9, width)), (2, (-4, -150, 2, width + 100)),
                         (3, (7, 0, 13, width + 250)), (10, (0, 0, 2, width)),
                         (11, (7, 0, 9, width)), (40, (4, 0, 5, width)), (4, (4, -175, 5, width))):
        builder.rect(mask, *bounds, cell=finger)
    bank = builder.layout.create_cell("HEMT_BANK")
    bank.insert(kdb.DCellInstArray(finger.cell_index(), kdb.DTrans(), kdb.DVector(20, 0), kdb.DVector(0, 0), count, 1))
    span = (count - 1) * 20. + 13.
    # Opposing coplanar ohmic/collection combs. The source fingers end before
    # the top drain spine, and drain fingers start above the bottom source spine.
    builder.rect(2, -4, -150, span, -50, cell=bank)
    builder.rect(3, -4, width + 150, span + 150, width + 250, cell=bank)
    if not banks and config.variant in {"dual", "distributed"}:
        builder.rect(3, -200, width + 150, -4, width + 250, cell=bank)
    builder.rect(4, 4, -175, span, -174, cell=bank)
    # A localized gate landing and feed neck occupy the inactive bank margin.
    # Gate metal stays insulated; analytical channel control remains imposed DC
    # bias, not an additional gate-conduction or switching model.
    builder.rect(4, -190, -295, -70, -195, cell=bank)
    builder.rect(4, -132.5, -195, -127.5, -172.5, cell=bank)
    builder.rect(4, -132.5, -177.5, span, -172.5, cell=bank)
    source_rows = [-100.]
    if config.variant in {"dual", "distributed"}:
        source_rows.append(width + 60.)
    if config.variant == "distributed":
        source_rows += [width * fraction for fraction in (.25, .5, .75)]
    for y in source_rows:
        half_width = 60 if y < 0 or y > width else 15
        builder.rect(7, -200, y - half_width, span, y + half_width, cell=bank)
        for i in range(count):
            builder.rect(12, i * 20 - 3, y - 4, i * 20 + 1, y + 4, cell=bank)
    builder.rect(7, -200, min(source_rows) - 60, -80, max(source_rows) + 60, cell=bank)
    origins = []
    for row in range(rows):
        for col in range(columns):
            x, y = (col * pitch + 250, row * pitch + 350) if banks else (250., 350.)
            origins.append((x, y))
            builder.cell.insert(kdb.DCellInstArray(bank.cell_index(), kdb.DTrans(x, y)))
    if banks:
        # Row links share an insulated gate trunk through nonactive corridors.
        builder.rect(4, 27.5, 102.5, 32.5, (rows - 1) * pitch + 107.5)
        for row in range(rows):
            y = row * pitch + 350.
            builder.rect(4, 30., y - 247.5, (columns - 1) * pitch + 122.5, y - 242.5)
            builder.rect(7, 50, y - 160, die_w - 50, y - 40)
            builder.rect(8, 50, y + width + 150, die_w - 50, y + width + 250)
        builder.rect(7, 50, 190, 200, (rows - 1) * pitch + 310)
        builder.rect(8, die_w - 200, 350 + width + 150, die_w - 50, (rows - 1) * pitch + 350 + width + 250)
        for x, y in origins:
            for i in range(count):
                builder.rect(13, x + i * 20 + 8, y + width + 196, x + i * 20 + 12, y + width + 204)
    p.layers = [
        PhysicalLayer("channel", "Fully gated GaN 2DEG paths", "2deg", 10e-9, 0., geometry=RegionSpec(sources=["1/0"]),
                      electrical=ElectricalModel(kind="hemt", mobility=.12, mobility_exponent=1.5,
                          threshold_voltage=-3., gate_coupling=1.5e16, smoothing_voltage=.1,
                          maximum_sheet_density=2e17, minimum_sheet_conductance=1e-10, gate_regions=["gate_control"])),
        _metal("source", "2/0", .1e-6, 8e-6, name="Source contact and collection metal"),
        _metal("drain", "3/0", .1e-6, 8e-6, name="Drain contact and collection metal"),
        _metal("source_rdl", "7/0", 24.1e-6, 20e-6, name="Upper source redistribution"),
        PhysicalLayer("gate", "Insulated gate routing (thermal only)", "gold", .05e-6, .024e-6,
                      "thermal", RegionSpec(sources=["4/0"])),
        PhysicalLayer("buffer", "Effective GaN buffer", "gan", 2e-6, -2e-6, "thermal", RegionSpec(sources=["20/0"]), opacity=.15),
        PhysicalLayer("substrate", "150 µm SiC thermal substrate", "sic", 150e-6, -152e-6, "thermal",
                      RegionSpec(sources=["20/0"]), opacity=.15),
    ]
    if banks:
        p.layers.insert(4, _metal("drain_rdl", "8/0", 50.1e-6, 20e-6))
    for layer in p.layers:
        if layer.id.startswith("source") or layer.id.startswith("drain"):
            layer.net_id = "source" if layer.id.startswith("source") else "drain"
            layer.loss_role = "routing_metal"
    p.interfaces = [
        Interface("source_contact", "source", "channel", RegionSpec(sources=["10/0"]), "specific", 1e-10,
                  thermal_kind="conductance", thermal_value=1e7),
        Interface("drain_contact", "drain", "channel", RegionSpec(sources=["11/0"]), "specific", 1e-10,
                  thermal_kind="conductance", thermal_value=1e7),
        _physical_via("source_vias", "source", "source_rdl", 12, p),
    ]
    if banks:
        p.interfaces.append(_physical_via("drain_vias", "drain", "drain_rdl", 13, p))
    p.terminals = [Terminal("gate_control", "channel", RegionSpec(sources=["40/0"]), "gate", 3.)]
    if banks:
        source_positions = [(60., 200., 180., 300.)]
        drain_positions = [(die_w - 190, (rows - 1) * pitch + 350 + width + 160, die_w - 60,
                            (rows - 1) * pitch + 350 + width + 240)]
        if config.variant == "asymmetric":
            drain_positions = [(die_w - 190, 350 + width + 160, die_w - 60, 350 + width + 240)]
        if config.variant == "dual":
            source_positions += [(60., (rows - 1) * pitch + 200, 180., (rows - 1) * pitch + 300)]
            drain_positions += [(die_w - 190, 350 + width + 160, die_w - 60, 350 + width + 240)]
        if config.variant == "distributed":
            source_positions += [(60., (rows - 1) * pitch + 200, 180., (rows - 1) * pitch + 300),
                                 (die_w - 190, 200., die_w - 60, 300.)]
            drain_positions += [(60., 350 + width + 160, 180., 350 + width + 240)]
        _port(p, "source", "source_rdl", source_positions, 0.)
        _port(p, "drain", "drain_rdl", drain_positions, config.current)
    else:
        x, y = origins[0]
        source_positions = [(x - 190, y - 150, x - 80, y - 50)]
        drain_positions = [(x + span + 20, y + width + 160, x + span + 140, y + width + 240)]
        if config.variant in {"dual", "distributed"}:
            source_positions += [(x - 190, y + width + 10, x - 80, y + width + 110)]
            drain_positions += [(x - 190, y + width + 160, x - 80, y + width + 240)]
        _port(p, "source", "source_rdl", source_positions, 0.)
        _port(p, "drain", "drain", drain_positions, config.current)
    for bank_index, (x, y) in enumerate(origins):
        ids = []
        for i in range(count):
            identifier = f"bank_{bank_index + 1}_finger_{i + 1}"
            ids.append(identifier)
            support = SupportSelector(layers=["channel"], geometry=rectangle(x + i * 20, y, x + i * 20 + 9, y + width))
            p.power.devices.append(DeviceGroup(identifier, support, width * UM, 9 * width * UM**2,
                                               "source", "drain", "gate_control"))
        p.power.devices.append(DeviceGroup(f"bank_{bank_index + 1}", SupportSelector(devices=ids),
                                           count * width * UM, count * 9 * width * UM**2, "source", "drain", "gate_control"))
        p.mesh.refinements.append(RefinementRegion(f"long_fingers_{bank_index + 1}",
            rectangle(x - 4, y, x + span, y + width), 20e-6, [.25e-6, 20e-6]))
    p.mesh.max_size, p.mesh.min_size, p.mesh.contact_size = 60e-6, .125e-6, 20e-6
    p.mesh.corner_refinement = False
    p.power.rules = RoutingRules(allowed_layers=["source_rdl"], via_size=3e-6, via_pitch=8e-6,
        via_enclosure=.25e-6, protected_masks=["1/0", "4/0", "10/0", "11/0", "40/0", "20/0"])
    p.sweeps = [SweepSpec("power.ports.drain.value", [config.current * .5, config.current], "electrothermal", "Equal-service current points")]
    return dict(die_um=[die_w, die_h], actual_gate_fingers=count * columns * rows,
                gate_width_per_finger_m=width * UM, total_gate_width_m=count * columns * rows * width * UM,
                gate_length_m=1e-6, source_access_m=2e-6, drain_access_m=2e-6,
                contact_specific_resistance_ohm_m2=1e-10, contact_transfer_length_300K_m=math.sqrt(1e-10 / 260.075),
                contact_normal_mesh_spacing_m=.25e-6, finger_tangent_spacing_m=20e-6,
                maximum_channel_field_V_m=3e5, model_temperature_range_K=[300., 425.],
                gate_bias_V=3., substrate_thickness_m=150e-6,
                gate_pad_size_um=[120., 100.], gate_routing_width_um=5.,
                gate_network_mask="4/0", gate_control_mask="40/0",
                metallization_assumption="Source and drain collection are coplanar opposing combs with no projected overlap. Crossovers use separately elevated source/drain redistribution and physical vias. Specific contacts represent complete local ohmic transfer, not an additional resolved plug.",
                feed_variants="Single source-end/right drain-bus feed; dual adds the opposite source-end and left drain-bus feed; distributed adds interior source-finger taps at fixed active geometry.",
                model="Synthetic analytical local-gate-bias GaN 2DEG; no high-field saturation, breakdown, trapping or switching. Conductance floor is numerical, not leakage.")


def _rdl(builder, p, config):
    side, scale = config.side_um, config.side_um / 1000.
    def draw(mask, bounds):
        builder.rect(mask, *(value * scale for value in bounds))
    draw(20, (0, 0, 1000, 1000))
    draw(1, (80, 150, 220, 850))
    for y in (250., 750.):
        width = (70. if y == 750 else 130.) if config.variant == "poor_single" else 100.
        builder.add(1, [(x * scale, yy * scale) for x, yy in
                       ((180, y - 50), (400, y - 50), (630, y - width / 2),
                        (700, y - width / 2), (700, y + width / 2), (630, y + width / 2), (400, y + 50), (180, y + 50))])
        draw(2, (600, y - 65, 910, y + 65))
    draw(2, (850, 150, 980, 850))
    draw(3, (80, 150, 180, 850))
    draw(3, (20, 440, 180, 560))
    draw(99, (400, 420, 580, 580))
    sites = [(130., y, 12) for y in (250., 500., 750.)] + [(650., y, 10) for y in (250., 750.)]
    via_cells = {}
    for mask in (10, 12):
        cell = builder.layout.create_cell(f"RDL_VIA_{mask}")
        builder.rect(mask, -6 * scale, -6 * scale, 6 * scale, 6 * scale, cell=cell)
        via_cells[mask] = cell
    via_count = 0
    for x, y, mask in sites:
        for row in range(3):
            for col in range(3):
                offsets = [-24., 0., 24.] if not config.variant.startswith("graded") else [-24., 4., 24.]
                builder.cell.insert(kdb.DCellInstArray(via_cells[mask].cell_index(),
                    kdb.DTrans((x + offsets[col]) * scale, (y + row * 24 - 24) * scale)))
                via_count += 1
    p.layers = [_metal("m1", "1/0", 0., 10e-6), _metal("m2", "2/0", 20e-6, 10e-6),
                _metal("m3", "3/0", 40e-6, 10e-6),
                PhysicalLayer("substrate", "300 µm SiC support", "sic", 300e-6, -301e-6, "thermal", RegionSpec(sources=["20/0"]), opacity=.15)]
    for layer in p.layers[:3]:
        layer.loss_role = "routing_metal"
    p.interfaces = [_physical_via("branch_vias", "m1", "m2", 10, p, enclosure=2 * scale),
                    _physical_via("feed_vias", "m1", "m3", 12, p, enclosure=2 * scale)]
    positions = [(25, 450, 115, 550)]
    if config.variant.endswith("distributed"):
        positions = [(90, 175, 170, 225), (90, 775, 170, 825)]
    _port(p, "drain", "m3", [tuple(v * scale for v in bounds) for bounds in positions], config.current)
    _port(p, "source", "m2", [tuple(v * scale for v in (890, 450, 970, 550))], 0.)
    p.power.rules = RoutingRules(via_size=10 * scale * UM, via_pitch=18 * scale * UM,
        via_enclosure=2 * scale * UM, keepouts=[rectangle(*(v * scale for v in (400, 420, 580, 580)))],
        protected_masks=["99/0", "20/0"])
    for index, y in enumerate((250., 750.)):
        p.power.measurements.append(MeasurementSpec(f"branch_{index + 1}_current", "contact_normal_density",
            SupportSelector(contacts=["branch_vias"], geometry=rectangle(580 * scale, (y - 70) * scale, 720 * scale, (y + 70) * scale)),
            "integral", vector_component="signed"))
    p.mesh.max_size, p.mesh.min_size, p.mesh.contact_size = 80e-6 * scale, 1e-6 * scale, 12e-6 * scale
    p.mesh.refinements = [RefinementRegion("via_transfer", RegionSpec(sources=["10/0", "12/0"]), 3e-6 * scale)]
    p.sweeps = [SweepSpec("power.ports.drain.value", [1., 5., 10., 20.], "electrothermal", "RDL current scenarios (review limits)")]
    return dict(die_um=[side, side], via_count=via_count, metal_levels=3, protected_opening_um=[180 * scale, 160 * scale],
                substrate_thickness_m=300e-6, model_temperature_range_K=[300., 450.],
                model="Synthetic copper DC RDL with geometric vias; no semiconductor, EM or switching prediction.")


def _sic_gate_network(builder, side, cells, cellular):
    """Insulated fixed-on gate distribution through nonactive corridors."""
    trace = side * .003
    half = trace / 2
    builder.rect(4, side * .01, side * .01, side * .07, side * .07)
    builder.rect(4, side * .04 - half, side * .04, side * .04 + half, side * .95)
    for fraction in (.075, .95):
        builder.rect(4, side * .04, side * fraction - half, side * .95, side * fraction + half)
    builder.rect(4, side * .95 - half, side * .075, side * .95 + half, side * .95)
    # Gap-center runners never cover the homogenized active-device support.
    x_intervals = sorted({(x0, x1) for x0, _, x1, _ in cells})
    for left, right in zip(x_intervals, x_intervals[1:]):
        center = (left[1] + right[0]) / 2
        builder.rect(4, center - half, side * .075, center + half, side * .95)
    if cellular:
        y_intervals = sorted({(y0, y1) for _, y0, _, y1 in cells})
        for lower, upper in zip(y_intervals, y_intervals[1:]):
            center = (lower[1] + upper[0]) / 2
            builder.rect(4, side * .04, center - half, side * .95, center + half)
    return dict(gate_pad_size_um=[side * .06, side * .06], gate_routing_width_um=trace,
                termination_margin_um=side * .06, gate_network_mask="4/0")


def _sic(builder, p, config):
    side, cellular = config.side_um, config.family == "sic_cells"
    builder.rect(20, 0, 0, side, side)
    builder.rect(2, 0, 0, side, side)
    # Four ordinary rectangles encode the collar without GDS hole cutlines.
    margin = side * .06
    for bounds in ((0, 0, side, margin), (0, side - margin, side, side),
                   (0, margin, margin, side - margin), (side - margin, margin, side, side - margin)):
        builder.rect(99, *bounds)
    cells = []
    if cellular:
        n = 3 if config.preset == "compact" else 8
        gap = side * .012
        tile = (side * .84 - gap * (n - 1)) / n
        for row in range(n):
            for col in range(n):
                x, y = side * .08 + col * (tile + gap), side * .08 + row * (tile + gap)
                cells.append((x, y, x + tile, y + tile))
        builder.rect(3, side * .08, side * .08, side * .92, side * .92)
    else:
        n = 4 if config.preset == "compact" else 20
        pitch = side * .84 / n
        for i in range(n):
            x = side * .08 + i * pitch
            cells.append((x, side * .10, x + .72 * pitch, side * .90))
        strap = side * (.05 if config.variant == "narrow" else .12)
        positions = [.25, .75] + ([.5] if config.variant == "cross" else [])
        for fraction in positions:
            builder.rect(3, side * .07, side * fraction - strap / 2, side * .93, side * fraction + strap / 2)
        builder.rect(3, side * .08, side * .25 - strap / 2, side * .14, side * .75 + strap / 2)
        builder.rect(3, side * .86, side * .25 - strap / 2, side * .92, side * .75 + strap / 2)
    gate_details = _sic_gate_network(builder, side, cells, cellular)
    via_count, active_area = 0, 0.
    macrocell = builder.layout.create_cell("ACTIVE_MACROCELL")
    for index, bounds in enumerate(cells):
        x0, y0, x1, y1 = bounds
        # Repeated active regions use a real hierarchy cell, not image assets.
        if index == 0:
            builder.rect(1, 0, 0, x1 - x0, y1 - y0, cell=macrocell)
            builder.rect(90, 0, 0, x1 - x0, y1 - y0, cell=macrocell)
        builder.cell.insert(kdb.DCellInstArray(macrocell.cell_index(), kdb.DTrans(x0, y0)))
        active_area += (x1 - x0) * (y1 - y0) * UM**2
        via_size = side * .012
        via_ys = [(y0 + y1) / 2] if cellular else [side * .25, side * .75]
        for vy in via_ys:
            cx = (x0 + x1) / 2
            builder.rect(12, cx - via_size / 2, vy - via_size / 2, cx + via_size / 2, vy + via_size / 2)
            via_count += 1
        p.power.devices.append(DeviceGroup(f"macrocell_{index + 1}", SupportSelector(layers=["source"], geometry=rectangle(*bounds)),
            active_area=(x1 - x0) * (y1 - y0) * UM**2, source_port="source", drain_port="drain"))
    p.layers = [_metal("source", "1/0", 0., 10e-6, name="Active macrocell source metal"),
                _metal("drain", "2/0", -220e-6, 20e-6, name="Backside drain metal"),
                _metal("collector", "3/0", 14e-6, 20e-6, name="Frontside source collection"),
                PhysicalLayer("gate", "Insulated gate drawing; fixed on-state assumption", "gold", .3e-6, 10.2e-6,
                              "thermal", RegionSpec(sources=["4/0"])),
                PhysicalLayer("substrate", "200 µm SiC thermal substrate; electrical drift included in Ron,sp", "sic",
                              200e-6, -200e-6, "thermal", RegionSpec(sources=["20/0"]), opacity=.15)]
    for layer in p.layers[:3]:
        layer.net_id = "drain" if layer.id == "drain" else "source"
        layer.loss_role = "routing_metal"
    p.interfaces = [Interface("on_state", "drain", "source", RegionSpec(sources=["90/0"]), "load", 1e-7,
                              purpose="device_load", reference_temperature=300., temperature_coefficient=.003),
                    _physical_via("collection_vias", "source", "collector", 12, p, enclosure=side * .001)]
    if cellular:
        if config.variant == "clip":
            footprints = [(side * .32, side * .25, side * .68, side * .75)]
        elif config.variant == "bumps":
            footprints = [(side * x - side * .035, side * y - side * .035,
                           side * x + side * .035, side * y + side * .035) for x in (.25, .5, .75) for y in (.25, .5, .75)]
        else:
            footprints = [(side * x - side * .05, side * y - side * .05,
                           side * x + side * .05, side * y + side * .05) for x, y in ((.25, .25), (.75, .25), (.25, .75), (.75, .75))]
    else:
        footprints = [(side * .08, side * .25 - side * .02, side * .28, side * .25 + side * .02)]
    _port(p, "source", "collector", footprints, 0., feed_resistance=.002)
    _port(p, "drain", "drain", [(side * .4, side * .4, side * .6, side * .6)], config.current, feed_resistance=.001)
    p.terminals.append(Terminal("kelvin_sense", "collector", rectangle(side * .86, side * .73, side * .88, side * .75), "probe"))
    p.power.measurements += [MeasurementSpec("kelvin_potential", "potential", SupportSelector(regions=["terminal:kelvin_sense"], layers=["collector"]), "mean"),
                            MeasurementSpec("device_loss", "contact_heat_density", SupportSelector(contacts=["on_state"]), "integral")]
    p.power.rules = RoutingRules(via_size=side * .010 * UM, via_pitch=side * .020 * UM,
        via_enclosure=side * .001 * UM, protected_masks=["1/0", "90/0", "4/0", "20/0", "99/0"],
        keepouts=[RegionSpec(sources=["99/0"]), rectangle(side * .01, side * .01, side * .07, side * .07)])
    p.mesh.max_size, p.mesh.min_size, p.mesh.contact_size = side * .05 * UM, side * .0005 * UM, side * .02 * UM
    p.mesh.refinements = [RefinementRegion("via_transfer", RegionSpec(sources=["12/0"]), side * .003 * UM)]
    p.sweeps = [SweepSpec("power.ports.drain.value", [5., 10., 20., 40.], "electrothermal", "On-state current scenarios (review limits)")]
    return dict(die_um=[side, side], macrocell_count=len(cells), active_area_m2=active_area, via_count=via_count,
                on_state_specific_resistance_ohm_m2=1e-7, reference_temperature_K=300., temperature_coefficient_K_inverse=.003,
                analytical_uniform_device_resistance_ohm=1e-7 / active_area, substrate_thickness_m=200e-6,
                **gate_details,
                model_temperature_range_K=[300., 450.], fixed_gate_assumption="On state; drawn insulated gate does not control the load",
                model="SiC MOSFET on-state interconnect model. Ron,sp includes channel/drift/intrinsic device contacts, excluding modeled metal/vias/external feeds. No separate electrical substrate loss, body diode, avalanche, switching or trench/planar distinction.")


def _bounded_study(p, config, geometry):
    width, height = geometry["die_um"]
    parameter, extras = "width", {}
    if config.family.startswith("gan"):
        route_layer, mask, axis = "source_rdl", "7/0", "x"
        # Edit a horizontal section between spines, retaining their full height.
        if config.family == "gan_banks":
            x0 = 250. + (config.fingers - 1) * 20. + 13. + 20.
            x1 = width / config.bank_columns + 30.
        else:
            x0, x1 = 170., width - 257.
        center = 250.
        edit, nominal, alternative = rectangle(x0, center - 75, x1, center + 75), 120e-6, 140e-6
    elif config.family == "rdl":
        route_layer, mask, axis = "m1", "1/0", "x"
        scale = width / 1000.
        edit, center = rectangle(400 * scale, 175 * scale, 630 * scale, 325 * scale), 250 * scale
        nominal = (130 if config.variant == "poor_single" else 100) * scale * UM
        alternative = nominal + 20 * scale * UM
        parameter, extras = "end_width", {"start_width": 100 * scale * UM}
    elif config.family == "sic_stripes":
        route_layer, mask, axis = "collector", "3/0", "x"
        center = height * .25
        edit = rectangle(width * .14, center - height * .08, width * .86, center + height * .08)
        nominal = width * (.05 if config.variant == "narrow" else .12) * UM
        alternative = nominal + width * .02 * UM
    else:
        route_layer, mask, axis = "collector", "3/0", "y"
        center = width * .905
        # A narrower edge neck remains attached through both ends. The nominal
        # width restores the full existing plate within this bounded edit.
        edit, nominal, alternative = rectangle(width * .89, height * .30, width * .94, height * .70), width * .03 * UM, width * .02 * UM
    recipe = RoutingRecipe("collection_bus", "bus", layer_id=route_layer, mask=mask, edit_region=edit,
        parameters={"axis": axis, "center": center * UM, parameter: nominal, **extras,
                    "bounds": {parameter: [min(nominal, alternative), max(nominal, alternative)]}})
    p.power.recipes = [recipe]
    p.power.experiments = [OptimizationExperiment("bounded_collection", recipe_ids=[recipe.id],
        variables=[DesignVariable("collection_width", recipe.id, parameter, kind="choice", values=[nominal, alternative])],
        scenarios=[OperatingScenario("nominal", service_port="drain", reference_port="source",
                   required_current=config.current, compliance_voltage=10.)],
        objectives=[ObjectiveSpec("routing_loss"), ObjectiveSpec("regularized_hotspot")],
        constraints=["maximum_temperature"] + (["transport_field"] if config.family.startswith("gan") else []),
        protected_devices=[device.id for device in p.power.devices],
        max_evaluations=2, finalists=2, refinement_factors=[1., .75], maximum_mesh_change=.03,
        improvement_margin=.01, max_wall_seconds=1800.,
        assumptions=["Synthetic screening study, not a device rating.", "Fixed active masks, current, parameters and reservoir.",
                     "Stress or transport-invalid results must not enter verified rankings."])]


def build_power_example(name, builder, p, *, pulse=False, stress=False, **options):
    config = settings(name, **options)
    geometry = (_gan if config.family.startswith("gan") else _rdl if config.family == "rdl" else _sic)(builder, p, config)
    _thermal_stack(builder, p)
    width, height = geometry["die_um"]
    p.roi = rectangle(0, 0, width, height)
    reservoir_layer = "drain" if config.family.startswith("sic") else "substrate"
    reservoir = p.layer(reservoir_layer)
    conductivity = p.materials[reservoir.material_id].thermal_conductivity.value
    p.thermal_boundaries = [ThermalBoundary("backside", reservoir_layer, kind="resistance_area",
        value=THERMAL_SCENARIOS[config.thermal] + reservoir.thickness / (2 * conductivity), ambient=300.)]
    p.power.thermal_reference_boundary = "backside"
    p.solver.mode = "electrothermal"
    p.solver.relative_tolerance, p.solver.absolute_tolerance = 1e-6, 1e-6
    p.solver.resistance_terminals, p.solver.resistance_current_terminal = ["source", "drain"], "drain"
    p.mesh.max_elements, p.mesh.max_dofs = config.max_elements, config.max_dofs
    hotspot_layer = "channel" if config.family.startswith("gan") else "source" if config.family.startswith("sic") else "m1"
    metals = [layer.id for layer in p.layers if layer.role == "conductor" and layer.id != "channel"]
    contacts = [interface.id for interface in p.interfaces if interface.electrical_kind in {"specific", "via"}]
    vias = [interface.id for interface in p.interfaces if interface.electrical_kind == "via"]
    p.power.measurements += [
        MeasurementSpec("delivered_current", "port_current", SupportSelector(ports=["drain"]), "integral"),
        MeasurementSpec("terminal_voltage", "port_voltage", SupportSelector(ports=["drain"]), "mean"),
        MeasurementSpec("terminal_power", "port_power", SupportSelector(ports=["drain"]), "integral"),
        MeasurementSpec("routing_loss", "joule_density", SupportSelector(layers=metals), "integral"),
        MeasurementSpec("contact_via_loss", "contact_heat_density", SupportSelector(contacts=contacts), "integral"),
        MeasurementSpec("maximum_temperature", "temperature", limit=geometry["model_temperature_range_K"][1]),
        MeasurementSpec("temperature_rise", "temperature_rise", reference_boundary="backside"),
        MeasurementSpec("regularized_hotspot", "temperature", SupportSelector(layers=[hotspot_layer]), "maximum",
                        averaging_length=min(width, height) * .1 * UM),
        MeasurementSpec("via_normal_loading", "contact_normal_density", SupportSelector(contacts=vias), "rms"),
        MeasurementSpec("metal_current_density", "metal_current_density", SupportSelector(layers=metals), "percentile", percentile=95.),
    ]
    if config.family.startswith("gan"):
        p.power.measurements += [MeasurementSpec("channel_sheet_loading", "sheet_current", SupportSelector(layers=["channel"]), "percentile", percentile=95.),
                                 MeasurementSpec("transport_field", "electric_field", SupportSelector(layers=["channel"]), "maximum", limit=3e5)]
    # One bounded source-route thickness study uses existing sweep machinery.
    # Geometry variants are generated at identical active support and current.
    route_layer = "source_rdl" if config.family.startswith("gan") else "collector" if config.family.startswith("sic") else "m1"
    layer = p.layer(route_layer)
    p.sweeps.append(SweepSpec(f"layers.{route_layer}.thickness", [layer.thickness, layer.thickness * 1.25], "electrothermal", "Collection metal thickness"))
    p.sweeps.append(SweepSpec("thermal_boundaries.backside.value",
        [attach + reservoir.thickness / (2 * conductivity) for attach in THERMAL_SCENARIOS.values()],
        "electrothermal", "Package thermal resistance area (m² K/W); fixed current"))
    _bounded_study(p, config, geometry)
    if config.preset == "stress" or stress:
        p.power.experiments = []  # Explicit stress projects have no verified-ranking study.
    if pulse:
        p.transient.enabled = True
        # Retain one field per high-plateau duration, plus the solver's peak
        # snapshot. Electrical/time integration still resolves the 20 µs edges.
        # Large pulse cases can still reject their explicit retention budget.
        p.transient.stop_time, p.transient.time_step, p.transient.output_interval = .006, .0001, .0005
        p.transient.drives = [TransientDrive("power.ports.drain.value", Waveform(kind="pulse", low=0., high=config.current,
            width=.0005, period=.002, rise_time=.00002, fall_time=.00002))]
        p.sweeps[0] = SweepSpec("transient.drives.0.waveform.high", [config.current * .5, config.current],
                               "electrothermal", "Pulse high-plateau current (A)")
        p.power.experiments = []  # Steady rankings do not certify a pulsed duty cycle.
    palette = {"channel": "#398B79", "source": "#376F9B", "drain": "#A87942",
               "source_rdl": "#5591C3", "drain_rdl": "#C79866", "collector": "#447FA5",
               "gate": "#8E6AAA", "buffer": "#9BA7B3", "substrate": "#C7CDD3",
               "m1": "#387D8B", "m2": "#9568AA", "m3": "#C49450"}
    for layer in p.layers:
        layer.color = palette[layer.id]
    data = {"generator_version": 2, **asdict(config), **geometry,
            "requested_current_A": config.current, "thermal_reservoir": "backside", "reservoir_temperature_K": 300.,
            "attach_resistance_area_m2_K_W": THERMAL_SCENARIOS[config.thermal],
            "heat_source_policy": "Joule/contact/device/feed losses only; no prescribed heating",
            "thermal_path_policy": "Nearest underlying modeled sheet; SiO₂-equivalent geometric gaps. Physical contacts/vias replace gap areas; one backside reservoir.",
            "validation": "Generated, not simulated. Convergence and model/mesh acceptance must be established separately.",
            "stress": config.preset == "stress" or stress}
    if pulse:
        data["pulse"] = dict(period_s=.002, high_plateau_s=.0005, rise_s=.00002, fall_s=.00002,
                            high_current_A=config.current, low_current_A=0.,
                            maximum_time_step_s=.0001, field_output_interval_s=.0005,
                            history_budget_bytes=p.transient.max_history_bytes,
                            model="Quasi-static electrical and thermal storage only; no switching losses")
    p.notes = (f"{geometry['model']}\n{width:g} × {height:g} µm; requested {config.current:g} A; {config.preset}/{config.variant}/{config.thermal}.\n"
               "Inputs are synthetic engineering assumptions at 300 K, not calibrated ratings. "
               "One backside reservoir follows layer/interface thermal paths; effective thickness averaging omits depth-resolved gradients. "
               "External feed loss is deposited 25% in the landing and 75% outside the model. "
               "Detailed individual via/load currents, group currents/active width or area, local source potentials and the disjoint loss ledger are available in Results. "
               "Review both thermal scenarios and mesh refinement before ranking; stress cases are not verified candidates."
               + METADATA_MARKER + json.dumps(data, sort_keys=True, indent=2))
    from etlayout.example_catalog import example_info
    preferred = example_info(name).layer
    p.layers.sort(key=lambda layer: layer.id != preferred)
    return True
