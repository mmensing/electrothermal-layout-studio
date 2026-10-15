# SPDX-License-Identifier: MPL-2.0
"""Create a deterministic, deliberately improvable four-cell power-feed example.

Run from the checkout: python examples/power_routing/generate.py --output DIR
The fixture uses analytical areal resistors, not a switching transistor model.
"""
from __future__ import annotations

import argparse
from pathlib import Path

import klayout.db as kdb

from etlayout.io.project import file_hash, save_project
from etlayout.materials.database import reference_materials
from etlayout.models import (
    DesignVariable,
    DeviceGroup,
    Interface,
    LayoutSource,
    MeasurementSpec,
    ObjectiveSpec,
    OperatingScenario,
    OptimizationExperiment,
    PhysicalLayer,
    PhysicalPort,
    PortLanding,
    Project,
    RegionSpec,
    RoutingRecipe,
    RoutingRules,
    SupportSelector,
    Terminal,
    ThermalBoundary,
)


def rectangle(x0, y0, x1, y1):
    return RegionSpec(polygons=[dict(bounds=[x0 * 1e-6, y0 * 1e-6, x1 * 1e-6, y1 * 1e-6])])


def build_project(directory):
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    layout = kdb.Layout()
    layout.dbu = .001
    top = layout.create_cell("FOUR_CELL_FEED")

    def draw(mask, coordinates):
        top.shapes(layout.layer(mask, 0)).insert(kdb.Box(*(round(c * 1000) for c in coordinates)))

    draw(1, [0, 1.5, 40, 4.5])
    draw(2, [0, 1, 40, 5])
    draw(4, [0, 0, 40, 32])
    for x in (5, 15, 25, 35):
        draw(10, [x - 1.5, 3, x + 1.5, 30])
        draw(90, [x - 1.5, 28, x + 1.5, 30])
    for x in (18, 22):
        draw(3, [x - .5, 2.5, x + .5, 3.5])
    top.shapes(layout.layer(99, 0)).insert(kdb.Text("FIXED FOUR ACTIVE CELLS", kdb.Trans(0, 34000)))
    source = directory / "four_cell_feed.gds"
    layout.write(str(source))
    project = Project(name="Four fixed power cells: bounded feed optimization", materials=reference_materials(),
                      layout=LayoutSource(str(source.resolve()), top.name, file_hash(source)))
    project.layers = [
        PhysicalLayer("feed", "Frontside feed and fingers", "copper", thickness=1e-6, z=0.,
                      geometry=RegionSpec(sources=["1/0", "10/0"]), net_id="supply", loss_role="routing_metal"),
        PhysicalLayer("strap", "Upper feed strap", "copper", thickness=1e-6, z=2e-6,
                      geometry=RegionSpec(sources=["2/0"]), net_id="supply", loss_role="routing_metal"),
        PhysicalLayer("return", "Backside return", "copper", thickness=1e-6, z=-2e-6,
                      geometry=RegionSpec(sources=["4/0"]), net_id="return", loss_role="other_metal")]
    project.interfaces = [
        Interface("vias", "feed", "strap", RegionSpec(sources=["3/0"]), electrical_kind="via", material_id="copper",
                  thermal_kind="stack", height=1e-6, model_kind="physical_via", height_mode="stack", enclosure=.25e-6),
        Interface("cells", "feed", "return", RegionSpec(sources=["90/0"]), electrical_kind="load", electrical_value=1e-12,
                  purpose="device_load", temperature_coefficient=.003, model_kind="effective"),
        Interface("buffer_thermal", "feed", "return", RegionSpec(full_domain=True),
                  thermal_kind="conductance", thermal_value=1e7, model_kind="effective")]
    project.terminals = [Terminal("feed_landing", "strap", rectangle(0, 1, 2, 5), kind="current", value=.05),
                         Terminal("return_landing", "return", RegionSpec(full_domain=True), kind="ground")]
    project.power.ports = [PhysicalPort("supply", [PortLanding("feed_landing", .01, 1.)], kind="current", value=.05),
                           PhysicalPort("return_port", [PortLanding("return_landing")], kind="ground")]
    project.thermal_boundaries = [ThermalBoundary("backside", "return", kind="temperature", value=300.)]
    project.power.thermal_reference_boundary = "backside"
    project.power.devices = [DeviceGroup(f"cell-{index + 1}", SupportSelector(layers=["feed"], geometry=rectangle(x - 1.5, 28, x + 1.5, 30)),
                                          active_width=3e-6, active_area=6e-12, source_port="return_port", drain_port="supply")
                              for index, x in enumerate((5, 15, 25, 35))]
    project.power.rules = RoutingRules(allowed_layers=["feed", "strap"], minimum_width={"feed": .5e-6, "strap": .5e-6},
                                       via_size=1e-6, via_pitch=2e-6, via_enclosure=.25e-6,
                                       transitions=[["feed", "strap"]], protected_masks=["10/0", "90/0", "99/0"])
    project.power.recipes = [
        RoutingRecipe("bus", "bus", layer_id="feed", mask="1/0", edit_region=rectangle(0, 0, 40, 6),
                      parameters={"width": 3e-6, "center": 3e-6, "bounds": {"width": [3e-6, 6e-6]}}, source_hash=project.layout.sha256),
        RoutingRecipe("strap", "strap", layer_id="strap", mask="2/0", edit_region=rectangle(0, 0, 40, 6),
                      parameters={"width": 4e-6, "center": 3e-6, "bounds": {"width": [4e-6, 6e-6]}}, source_hash=project.layout.sha256),
        RoutingRecipe("vias", "via_array", layer_id="strap", mask="3/0", interface_id="vias", edit_region=rectangle(0, 2, 40, 4),
                      parameters={"columns": 2, "rows": 1, "pitch": 4e-6, "size": 1e-6, "bounds": {"columns": [2, 8]}}, source_hash=project.layout.sha256)]
    routing = SupportSelector(layers=["feed", "strap"])
    project.power.measurements = [MeasurementSpec("routing_j", "metal_current_density", routing, aggregation="rms", limit=1e11),
                                  MeasurementSpec("routing_loss", "joule_density", routing, aggregation="integral"),
                                  MeasurementSpec("peak_temperature", "temperature", routing, aggregation="maximum", limit=450.)]
    project.power.experiments = [OptimizationExperiment("four-cell-feed", recipe_ids=["bus", "strap", "vias"], variables=[
        DesignVariable("bus_width", "bus", "width", minimum=3e-6, maximum=6e-6),
        DesignVariable("strap_width", "strap", "width", minimum=4e-6, maximum=6e-6),
        DesignVariable("via_count", "vias", "columns", kind="integer", minimum=2, maximum=8)], scenarios=[
            OperatingScenario("nominal", service_port="supply", reference_port="return_port", required_current=.05, compliance_voltage=.5),
            OperatingScenario("high_current", service_port="supply", reference_port="return_port", required_current=.1, compliance_voltage=.5)],
        objectives=[ObjectiveSpec("routing_j"), ObjectiveSpec("routing_loss")], constraints=["routing_j", "peak_temperature"],
        protected_devices=[d.id for d in project.power.devices], max_evaluations=8, finalists=2, seed=7,
        refinement_factors=[1., .5], maximum_mesh_change=.1)]
    project.mesh.max_size, project.mesh.min_size, project.mesh.contact_size = 3e-6, .2e-6, .6e-6
    project.solver.mode = "electrothermal"
    project.notes = "Synthetic areal-resistor power cells. Fixed finger and active masks. No transistor switching or breakdown prediction."
    save_project(project, directory / "four_cell_feed.etl")
    return project


def build_hemt_project(directory):
    """Fixed HEMT fingers with a segmented source bus, dual upper feeds and vias."""
    from etlayout.examples import make_example
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    project = make_example("E8", directory)
    layout = kdb.Layout()
    layout.read(project.layout.path)
    top = layout.cell(project.layout.cell)
    source_mask = layout.layer(2, 0)
    top.shapes(source_mask).clear()
    # Split the editable bus from immutable source fingers. At baseline the
    # union is identical to the original E8 source-metal footprint.
    top.shapes(source_mask).insert(kdb.Box(-10000, 7000, -4000, 93000))
    for y in (10000, 50000, 90000):
        top.shapes(layout.layer(20, 0)).insert(kdb.Box(-4000, y - 3000, 85000, y + 3000))
        top.shapes(layout.layer(31, 0)).insert(kdb.Box(-13000, y - 3000, 85000, y + 3000))
        for x in (37000, 45000, 53000):
            top.shapes(layout.layer(32, 0)).insert(kdb.Box(x - 500, y - 500, x + 500, y + 500))
    top.shapes(layout.layer(31, 0)).insert(kdb.Box(-13000, 7000, -11000, 93000))
    top.shapes(layout.layer(30, 0)).insert(kdb.Box(-10000, 7000, -4000, 93000))
    source = directory / "hemt_source_feed.gds"
    layout.write(str(source))
    project.layout = LayoutSource(str(source.resolve()), project.layout.cell, file_hash(source), .001)
    project.name = "Fixed HEMT fingers: bus, dual feeds, upper strap and via optimization"
    project.layer("source_metal").geometry.sources = ["2/0", "20/0"]
    # E8's effective gate-metal placement crosses source metal in z. This
    # demonstration gives it a non-overlapping physical height before adding
    # literal fabricated vias; its control mask/channel law remain unchanged.
    project.layer("gate_metal").z = .85e-6
    project.layers.append(PhysicalLayer("upper_source", "Upper source feed", "copper", thickness=.7e-6, z=2e-6,
        geometry=RegionSpec(sources=["30/0", "31/0"]), net_id="source", loss_role="routing_metal"))
    project.interfaces.append(Interface("source_vias", "source_metal", "upper_source", RegionSpec(sources=["32/0"]),
        electrical_kind="via", material_id="copper", thermal_kind="stack", height=1.2e-6,
        model_kind="physical_via", height_mode="stack", enclosure=.25e-6))
    for layer in project.layers:
        if layer.id in {"source_metal", "drain_metal"}:
            layer.loss_role = "routing_metal"
    next(t for t in project.terminals if t.id == "source").geometry = rectangle(-10, 87, -4, 93)
    project.power.ports = [PhysicalPort("source_port", [PortLanding("source")], kind="ground"),
                           PhysicalPort("drain_port", [PortLanding("drain")], value=.2)]
    project.power.devices = [DeviceGroup(f"finger-{i + 1}", SupportSelector(layers=["channel"], geometry=rectangle(0, y - 10, 100, y + 10)),
                                          active_width=90e-6, active_area=90e-6 * 16e-6,
                                          source_port="source_port", drain_port="drain_port", gate_terminal="gate_control")
                              for i, y in enumerate((20, 40, 60, 80))]
    project.power.rules = RoutingRules(allowed_layers=["source_metal", "upper_source"],
                                       minimum_width={"source_metal": .5e-6, "upper_source": .5e-6},
                                       via_size=1e-6, via_pitch=2e-6, via_enclosure=.25e-6,
                                       transitions=[["source_metal", "upper_source"]],
                                       protected_masks=["1/0", "3/0", "4/0", "5/0", "10/0", "11/0", "20/0"])
    project.power.recipes = [RoutingRecipe("source_bus_lower", "bus", layer_id="source_metal", mask="2/0", source_hash=project.layout.sha256,
            edit_region=rectangle(-13, 7, -1, 50), parameters={"axis": "y", "center": -7e-6, "width": 6e-6,
                "bounds": {"width": [6e-6, 12e-6]}}),
        RoutingRecipe("source_bus_upper", "bus", layer_id="source_metal", mask="2/0", source_hash=project.layout.sha256,
            edit_region=rectangle(-13, 50, -1, 93), parameters={"axis": "y", "center": -7e-6, "width": 6e-6,
                "bounds": {"width": [6e-6, 12e-6]}}),
        RoutingRecipe("upper_feed", "finger_feed", layer_id="upper_source", mask="31/0", source_hash=project.layout.sha256,
            edit_region=rectangle(-13, 7, 85, 93), parameters={"axis": "y", "count": 3, "pitch": 40e-6,
                "finger_width": 6e-6, "bus_width": 2e-6, "end": "single"}),
        RoutingRecipe("upper_strap", "strap", layer_id="upper_source", mask="30/0", source_hash=project.layout.sha256,
            edit_region=rectangle(-13, 7, -1, 93), parameters={"axis": "y", "center": -7e-6, "width": 6e-6,
                "bounds": {"width": [6e-6, 12e-6]}}),
        RoutingRecipe("source_vias", "via_array", layer_id="upper_source", mask="32/0", interface_id="source_vias",
            source_hash=project.layout.sha256, edit_region=rectangle(0, 7, 85, 93), parameters={"columns": 3, "rows": 3,
                "pitch_x": 8e-6, "pitch_y": 40e-6, "center_x": 45e-6, "center_y": 50e-6,
                "size": 1e-6, "pattern": "uniform", "grade": .5, "bounds": {"columns": [3, 7]}})]
    selection = SupportSelector(layers=["source_metal", "upper_source"])
    project.power.measurements = [MeasurementSpec("source_j", "metal_current_density", selection, aggregation="rms", limit=1e11),
                                  MeasurementSpec("source_loss", "joule_density", selection, aggregation="integral"),
                                  MeasurementSpec("channel_temperature", "temperature", SupportSelector(layers=["channel"]), aggregation="maximum", limit=450.)]
    project.power.experiments = [OptimizationExperiment("hemt-source-feed", recipe_ids=[recipe.id for recipe in project.power.recipes], variables=[
        DesignVariable("source_start_width", "source_bus_lower", "width", minimum=6e-6, maximum=12e-6, values=[6e-6, 12e-6]),
        DesignVariable("source_end_width", "source_bus_upper", "width", minimum=6e-6, maximum=12e-6, values=[6e-6, 12e-6]),
        DesignVariable("feed_ends", "upper_feed", "end", kind="choice", values=["single", "dual"]),
        DesignVariable("strap_width", "upper_strap", "width", minimum=6e-6, maximum=12e-6, values=[6e-6, 12e-6]),
        DesignVariable("via_columns", "source_vias", "columns", kind="integer", minimum=3, maximum=7, values=[3, 5, 7]),
        DesignVariable("via_pattern", "source_vias", "pattern", kind="choice", values=["uniform", "graded", "staggered"])], scenarios=[
            OperatingScenario("gate_zero", settings={"terminals.gate_control.value": 0.}, service_port="drain_port", reference_port="source_port", required_current=.001, compliance_voltage=2.),
            OperatingScenario("gate_negative", settings={"terminals.gate_control.value": -1.}, service_port="drain_port", reference_port="source_port", required_current=.002, compliance_voltage=2.)],
        objectives=[ObjectiveSpec("source_j"), ObjectiveSpec("source_loss")], constraints=["source_j", "channel_temperature"],
        protected_devices=[group.id for group in project.power.devices], max_evaluations=6, finalists=2, seed=7,
        refinement_factors=[1., .5], maximum_mesh_change=.1)]
    project.solver.mode = "electrothermal"
    project.power.thermal_reference_boundary = "sink"
    project.notes += " Source landing is localized at one end. Two source-bus segment widths, single/dual upper feed, upper strap and source-via distributions are editable; HEMT/channel/gate/contact/field-plate/finger masks remain fixed. Gate metal is placed above source/drain metal for a physically non-overlapping stack."
    save_project(project, directory / "hemt_source_feed.etl")
    return project


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=Path(".artifacts/power-routing-example"))
    args = parser.parse_args()
    created = build_project(args.output)
    print(args.output / "four_cell_feed.etl")
    build_hemt_project(args.output)
    print(args.output / "hemt_source_feed.etl")
