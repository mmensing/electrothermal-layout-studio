# SPDX-License-Identifier: MPL-2.0
"""Shared example discovery and loading without Qt, meshing or simulation.

The forty historical references retain their exact inputs. Recommended named
scenarios and parameterized family generation are separate, explicit choices.
Portable packaged reference archives supply card inputs in source, wheel and
frozen installations; they are not evidence that a calculation has been run.
"""
from __future__ import annotations

from copy import deepcopy
from dataclasses import asdict, dataclass
from functools import lru_cache
from importlib.resources import as_file, files
from pathlib import Path

from etlayout.example_catalog import EXAMPLE_CATALOG, LEARNING_PATHS, example_info
from etlayout.example_scenarios import ExampleScenario, apply_scenario, scenarios


@dataclass(frozen=True)
class ExampleChoices:
    presets: tuple[str, ...] = ()
    variants: tuple[str, ...] = ()
    thermal: tuple[str, ...] = ()
    scenarios: tuple[ExampleScenario, ...] = ()
    recommended_scenario: str = "reference"
    default_preset: str | None = None
    default_variant: str | None = None
    default_thermal: str | None = None

    def to_dict(self):
        return asdict(self)


def example_choices(identifier: str) -> ExampleChoices:
    """Return supported builder choices; no layouts are generated or solved."""
    if identifier in COMPANION_CATALOG:
        return ExampleChoices(scenarios=(ExampleScenario("reference", "Original companion project",
            "Keep the existing fixed-device routing project and its bounded studies unchanged."),))
    info = example_info(identifier)
    selected = scenarios(info.name)
    if not info.family:
        return ExampleChoices(scenarios=selected, recommended_scenario=info.recommended_scenario)
    from etlayout.power_examples import family_choices, settings

    options, defaults = family_choices(info.name), settings(info.name)
    return ExampleChoices(**options, scenarios=selected, recommended_scenario=info.recommended_scenario,
                          default_preset=defaults.preset, default_variant=defaults.variant,
                          default_thermal=defaults.thermal)


def create_library_example(identifier: str, directory: str | Path, *, preset=None, variant=None,
                           thermal=None, scenario=None, current=None, pulse=False, fingers=None,
                           gate_width_um=None, stress=False, tlm_spacing_um=None):
    """Create an explicit editable selection without silently dropping options.

    A named operating scenario starts from its documented historical reference;
    family geometry/drive overrides cannot be mixed into it. The ordinary
    ``reference`` choice permits the existing family-generation options.
    """
    generation = dict(preset=preset, variant=variant, thermal=thermal, current=current,
                      fingers=fingers, gate_width_um=gate_width_um)
    if identifier in COMPANION_CATALOG:
        if (scenario not in {None, "reference"} or pulse or stress or tlm_spacing_um is not None
                or any(value is not None for value in generation.values())):
            raise ValueError("Companion projects have fixed original inputs; edit the opened project or its saved study")
        return load_companion(identifier, directory)
    info = example_info(identifier)
    choices = example_choices(info.name)
    if scenario is not None and scenario not in {item.id for item in choices.scenarios}:
        raise ValueError(f"{info.id} scenarios: {', '.join(item.id for item in choices.scenarios)}")
    if scenario not in {None, "reference"} and (
            pulse or stress or tlm_spacing_um is not None
            or any(value is not None for value in generation.values())):
        raise ValueError("Named operating scenarios use their original reference; choose reference before changing family options")
    if not info.family and (pulse or stress or any(value is not None for value in generation.values())):
        raise ValueError("Size, routing, thermal and drive overrides require a power-device family (E20–E24)")
    if tlm_spacing_um is not None and info.id != "E27":
        raise ValueError("Contact spacing is supported only for the E27 TLM coupon")
    if info.family:
        from etlayout.power_examples import settings

        settings(info.name, **generation)  # Validate every option before creating files.
    from etlayout.examples import make_example

    return make_example(info.name, directory, **generation, scenario=scenario, pulse=pulse,
                        stress=stress, tlm_spacing_um=tlm_spacing_um)


@dataclass(frozen=True)
class CompanionInfo:
    name: str
    title: str
    filename: str
    learning_goal: str
    physical_scope: str
    layer: str
    category: str = "Multilayer routing and packages"
    level: str = "Advanced"
    workload: str = "bounded optimization study; review candidate and mesh budgets"
    role: str = "companion optimization project"
    applicability: str = "synthetic functional demonstration"
    reference_status: str = "reference"
    recommended_scenario: str = "reference"
    recommended_mode: str = "electrothermal"
    field: str = "current_density"
    family: str = ""
    capabilities: tuple[str, ...] = ("constrained routing", "equal-service optimization", "mesh-verified finalists")

    @property
    def id(self):
        return self.name

    def to_dict(self):
        return {"id": self.id, **asdict(self), "evidence_level": self.applicability}


COMPANION_CATALOG = {
    "routing_four_cell_feed": CompanionInfo("routing_four_cell_feed", "Four-cell feed optimization", "four_cell_feed.etl",
        "Compare bounded bus, strap and via choices at equal delivered service.",
        "Fixed synthetic area-resistor loads; resistive feed optimization, not switching-transistor physics.", "feed"),
    "routing_hemt_source_feed": CompanionInfo("routing_hemt_source_feed", "Legacy HEMT source-feed optimization", "hemt_source_feed.etl",
        "Optimize source-bus and upper-feed choices without changing the fixed legacy channel geometry.",
        "Reuses the legacy E8 local-bias HEMT with ungated edge bypass; use E37/E16 for fully gated channel teaching.", "upper_source"),
}


def _reference_resource(identifier):
    if identifier in COMPANION_CATALOG:
        return files("etlayout").joinpath("data", "examples", "power_routing", COMPANION_CATALOG[identifier].filename)
    return files("etlayout").joinpath("data", "examples", example_info(identifier).name + ".etl")


def reference_project(identifier: str):
    """Load an independent embedded reference; ignore untrusted saved external paths."""
    from etlayout.io.project import inspect_project, load_project
    from etlayout.models import SimulationError

    resource = _reference_resource(identifier)
    if not resource.is_file():
        raise FileNotFoundError(f"Packaged reference is unavailable: {identifier}; regenerate from the example library")
    with as_file(resource) as path:
        inspection = inspect_project(path)
        if not inspection.metadata.get("embedded_layout"):
            raise SimulationError("Built-in reference must embed its audited layout; external reference paths are not opened")
        project, _result = load_project(path)
    return project



@lru_cache(maxsize=1)
def _companion_project_names():
    """Cache immutable names only, never a mutable live Project."""
    from etlayout.io.project import inspect_project
    from etlayout.models import SimulationError

    names = []
    for identifier, info in COMPANION_CATALOG.items():
        try:
            resource = _reference_resource(identifier)
            with as_file(resource) as path:
                names.append((inspect_project(path).project.name, info))
        except (OSError, SimulationError):
            continue  # Missing optional resources must not break unrelated project opening.
    return tuple(names)


def library_info_for_project(project):
    """Recognize unchanged reference names, including portable companion copies."""
    info = EXAMPLE_CATALOG.get(project.name)
    if info is not None:
        return info
    return next((info for name, info in _companion_project_names() if name == project.name), None)


def load_companion(identifier: str, directory: str | Path):
    """Copy an existing portable companion to an editable local project."""
    from etlayout.io.project import save_project

    if identifier not in COMPANION_CATALOG:
        raise ValueError(f"Unknown companion {identifier!r}; choose {', '.join(COMPANION_CATALOG)}")
    project = reference_project(identifier)
    destination = Path(directory)
    destination.mkdir(parents=True, exist_ok=True)
    name = COMPANION_CATALOG[identifier].filename
    source = Path(project.layout.path)
    target_layout = destination / (Path(name).stem + source.suffix)
    target_layout.write_bytes(source.read_bytes())
    project.layout.path = str(target_layout.resolve())
    save_project(project, destination / name)
    return project


def project_summary(project) -> dict:
    """Describe configured inputs cheaply; no geometry preparation or result claims."""
    from etlayout.geometry.layout import LayoutDocument
    from etlayout.power_examples import metadata
    from etlayout.workflow import ANALYSES, analysis_key

    selected_analysis_key = analysis_key(project)
    analysis_label = next(item.label for item in ANALYSES if item.key == selected_analysis_key)
    dimensions = None
    source = Path(project.layout.path)
    if project.layout.path and source.is_file():
        document = LayoutDocument(source)
        bounds = document.cell_bounds(project.layout.cell, project.layout.instance_path)
        dimensions = [(bounds[2] - bounds[0]) * 1e6, (bounds[3] - bounds[1]) * 1e6]
    owned = {landing.terminal_id for port in project.power.ports for landing in port.landings}
    service = [dict(id=port.id, kind=port.kind, value=port.value,
                    unit="A" if port.kind == "current" else "V", source="physical port")
               for port in project.power.ports if port.kind in {"current", "voltage", "ground"}]
    service += [dict(id=terminal.id, kind=terminal.kind, value=terminal.value,
                     unit="A" if terminal.kind == "current" else "A/m²" if terminal.kind == "density" else "V",
                     source="terminal")
                for terminal in project.terminals if terminal.id not in owned
                and terminal.kind in {"current", "voltage", "ground", "density", "gate"}]
    heating = [dict(id=item.id, kind=item.kind, value=item.value, unit="W" if item.kind == "total" else "W/m²")
               for item in project.heat_sources]
    reservoirs = [dict(id=item.id, layer=item.layer_id, kind=item.kind, value=item.value,
                       ambient_K=item.ambient, unit={"temperature": "K", "coefficient": "W/(m² K)",
                       "resistance_area": "m² K/W", "resistance": "K/W"}.get(item.kind, ""))
                  for item in project.thermal_boundaries if item.kind != "insulated"]
    config = metadata(project)
    budgets = dict(max_elements=project.mesh.max_elements, max_dofs=project.mesh.max_dofs,
                   memory_budget_bytes=config.get("memory_budget_bytes"))
    operation = {"electrical": "Electrical", "thermal": "Thermal", "electrothermal": "Electrothermal"}[project.solver.mode]
    operation += " transient" if project.transient.enabled else " steady"
    dimensions_text = (f"{dimensions[0]:g} × {dimensions[1]:g} µm layout bounding box; " if dimensions else "")
    excitation = "; ".join(f"{item['id']} {item['value']:g} {item['unit']} ({item['kind']})" for item in service)
    if heating:
        excitation += ("; " if excitation else "") + "; ".join(
            f"{item['id']} {item['value']:g} {item['unit']} prescribed heat" for item in heating)
    features = dict(layers=len(project.layers), interfaces=len(project.interfaces),
                    physical_ports=len(project.power.ports), measurements=len(project.power.measurements),
                    sweeps=len(project.sweeps), optimization_studies=len(project.power.experiments),
                    device_groups=len(project.power.devices))
    return dict(dimensions_um=dimensions, dimensions_basis="Layout cell bounding box, including labels; not active area",
                operation=operation, analysis_key=selected_analysis_key, analysis_label=analysis_label,
                service=service, heat_sources=heating, thermal_reservoirs=reservoirs,
                budgets=budgets, features=features, transient_drives=[asdict(item) for item in project.transient.drives],
                summary=f"{analysis_label}; {dimensions_text}{excitation}. Limits: {budgets['max_elements']:,} elements, {budgets['max_dofs']:,} DOFs. Configured inputs; not a simulated result.")


def example_card(identifier: str, project=None, *, scenario=None) -> dict:
    """Return common GUI/documentation metadata and independent reference inputs."""
    companion = identifier in COMPANION_CATALOG
    info = COMPANION_CATALOG[identifier] if companion else example_info(identifier)
    choices = example_choices(info.name)
    if scenario is not None and scenario not in {item.id for item in choices.scenarios} | {"reference"}:
        raise ValueError(f"Unknown operating scenario {scenario!r} for {info.id}")
    note = ("Original reference is a stress experiment; start with the recommended lower-current scenario."
            if info.reference_status == "stress" else "Original reference inputs are retained.")
    if info.role == "legacy comparison" or identifier == "routing_hemt_source_feed":
        note += " Legacy ungated edge bypass is intentional; this is not the fully gated teaching reference."
    card = {**info.to_dict(), "identifier": info.name, "collection": "companion" if companion else "reference",
            "learning_paths": tuple(path.id for path in LEARNING_PATHS if info.id in path.examples),
            "status_note": note, "choices": choices.to_dict(), "project_summary": None, "availability_note": ""}
    try:
        selected = reference_project(info.name) if project is None else deepcopy(project)
        if not companion and scenario not in {None, "reference"}:
            apply_scenario(selected, scenario)
        summary = project_summary(selected)
        summary["scenario_id"] = scenario or "reference"
        selected_scenario = next((item for item in choices.scenarios if item.id == summary["scenario_id"]), None)
        summary["scenario_title"] = selected_scenario.title if selected_scenario else "Original reference"
        if scenario not in {None, "reference"}:
            summary["summary"] = summary["scenario_title"] + ". " + summary["summary"]
        elif info.reference_status == "stress":
            summary["summary"] = "Original stress reference. " + summary["summary"]
        card["project_summary"] = summary
    except (FileNotFoundError, OSError) as error:
        card["availability_note"] = str(error)
    return card