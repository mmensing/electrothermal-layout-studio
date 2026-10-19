# SPDX-License-Identifier: MPL-2.0
"""Bounded operating scenarios; reference projects and material/cooling seeds stay fixed.

Each change is explicit in the generated Project and uses ordinary editable
inputs. Generation never starts a calculation or claims an operating rating.
"""
from __future__ import annotations

import json
from dataclasses import asdict, dataclass

from etlayout.example_catalog import example_info
from etlayout.models import SweepSpec, TemperatureLimit
from etlayout.simulation.sweep import set_parameter


@dataclass(frozen=True)
class ExampleScenario:
    id: str
    title: str
    purpose: str
    changes: tuple[tuple[str, float], ...] = ()
    joule_only: bool = False
    maximum_temperature_K: float | None = None
    evaluation: str = "fail_after"


def scenarios(identifier):
    """Return supported choices, with the original reference first."""
    key = example_info(identifier).id
    reference = ExampleScenario("reference", "Original reference", "Keep the published reference inputs unchanged.")
    demo = {
        "E11": (("terminals.drain.value", .03),),
        "E15": (("terminals.drain_a.value", .8), ("terminals.drain_b.value", .5)),
        "E26": (("power.ports.drain.value", 1.25),),
        "E29": (("power.ports.drain.value", 8.),),
        "E30": (("power.ports.drain.value", .6),),
        "E33": (("power.ports.drain.value", .0009),),
        "E34": (("power.ports.drain.value", .015),),
        "E35": (("power.ports.drain.value", .5),),
        "E36": (("power.ports.drain.value", 2.5), ("transient.drives.0.waveform.high", 2.5)),
    }
    choices = [reference]
    if key in {"E11", "E15", "E16"}:
        choices.append(ExampleScenario("joule_only", "Separate Joule heating", "Remove the explicitly independent prescribed heater; retain the original electrical operating point.", joule_only=True))
    if key in demo:
        choices.append(ExampleScenario("thermal_demo", "Joule thermal demonstration",
            "Raise only the declared electrical excitation within the synthetic screening model; keep geometry, material parameters and thermal boundaries fixed. This is a demonstration, not a current rating.",
            demo[key], key in {"E11", "E15"}, 423.15))
    if key in {"E18", "E26", "E36"}:
        changes = demo.get(key, ())
        choices.append(ExampleScenario("thermal_limit_failure", "Failed engineering limit",
            "Use a deliberately chosen 310 K engineering limit; retain all converged fields and complete pulse cooling where applicable. The threshold is a user criterion, not a material rating.", changes, False, 310.))
        if key in {"E18", "E36"}:
            choices.append(ExampleScenario("thermal_stop", "Stop at accepted hot state",
                "Stop only after an accepted physical state reaches the 310 K engineering limit. The first observed time is not an exactly localized threshold crossing.", changes, False, 310., "stop_transient"))
    return tuple(choices)


def apply_scenario(project, identifier):
    choices = {item.id: item for item in scenarios(project.name)}
    if identifier not in choices:
        raise ValueError(f"{example_info(project.name).id} scenarios: {', '.join(choices)}")
    scenario = choices[identifier]
    if identifier == "reference":
        return project
    for path, value in scenario.changes:
        set_parameter(project, path, value)
    if scenario.joule_only:
        project.heat_sources = []
        project.sweeps = [study for study in project.sweeps if not study.parameter.startswith("heat_sources.")]
    if scenario.maximum_temperature_K is not None:
        project.temperature_limits = [TemperatureLimit("example_maximum", maximum_temperature=scenario.maximum_temperature_K,
                                                       evaluation=scenario.evaluation)]
    if scenario.changes:
        path, maximum = next(((path, value) for path, value in scenario.changes
            if path.startswith("transient.drives.") and path.endswith(".waveform.high")), scenario.changes[0])
        project.sweeps = [SweepSpec(path, [maximum * .5, maximum * .75, maximum], project.solver.mode,
                                   "Bounded electrical drive; fixed geometry, materials and cooling")]
    introduction = (f"Operating scenario: {scenario.title}. {scenario.purpose}\n"
                    f"Explicit input changes (SI): {json.dumps(dict(scenario.changes), sort_keys=True)}. "
                    f"Prescribed heat removed: {scenario.joule_only}.\nOriginal reference geometry/model description:\n")
    markers = ("\n\nExample configuration (SI):\n", "\n\nDevice example configuration (SI):\n")
    for marker in markers:
        if marker in project.notes:
            description, encoded = project.notes.split(marker, 1)
            data = json.loads(encoded)
            data["operating_scenario"] = asdict(scenario)
            drain = next((port for port in project.power.ports if port.id == "drain"), None)
            if drain:
                data["requested_current_A"] = drain.value
            for drive in project.transient.drives:
                if drive.target == "power.ports.drain.value":
                    data["pulse_high_A"] = drive.waveform.high
            data["no_prescribed_heat"] = not project.heat_sources
            project.notes = introduction + description + marker + json.dumps(data, sort_keys=True, indent=2)
            break
    else:
        project.notes = introduction + project.notes
    return project
