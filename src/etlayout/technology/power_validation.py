# SPDX-License-Identifier: MPL-2.0
"""Validate declarative power contracts before allocation or solving."""
from __future__ import annotations

import math

from etlayout.models import Diagnostic, SimulationError
from etlayout.power.measurement_contract import validate_limits


def validate_power_settings(project):
    messages = []
    power = project.power

    def error(message, obj=""):
        messages.append(Diagnostic("error", message, obj))

    for name in ("measurements", "ports", "devices", "recipes", "experiments"):
        ids = [item.id for item in getattr(power, name)]
        if any(not key for key in ids) or len(ids) != len(set(ids)):
            error(f"Power {name} require unique nonempty IDs")
    terminal_ids = {t.id for t in project.terminals}
    layer_ids = {layer.id for layer in project.layers}
    landings = set()
    for port in power.ports:
        if port.kind not in {"voltage", "ground", "current", "floating"} or not math.isfinite(port.value):
            error("Port kind/value is invalid", port.id)
        if not port.landings:
            error("Port requires at least one physical landing", port.id)
        for landing in port.landings:
            if landing.terminal_id not in terminal_ids:
                error("Port landing references a missing terminal footprint", port.id)
            if landing.terminal_id in landings:
                error("A terminal footprint cannot belong to more than one port landing", port.id)
            landings.add(landing.terminal_id)
            if not math.isfinite(landing.resistance) or landing.resistance < 0:
                error("Port landing resistance must be finite and nonnegative", port.id)
            if not math.isfinite(landing.thermal_fraction) or not 0 <= landing.thermal_fraction <= 1:
                error("Feed-loss thermal fraction must lie in [0, 1]", port.id)
    for device in power.devices:
        if any(not math.isfinite(v) or v < 0 for v in (device.active_width, device.active_area)):
            error("Device intended active width/area must be finite and nonnegative", device.id)
    for measurement in power.measurements:
        try:
            validate_limits(measurement)
        except SimulationError as exc:
            error(str(exc), measurement.id)
        if measurement.aggregation not in {"integral", "mean", "rms", "percentile", "maximum", "minimum", "exceedance"}:
            error("Unknown measurement aggregation", measurement.id)
        components = {"magnitude", "signed"} if measurement.quantity == "contact_normal_density" else {"magnitude", "x", "y"}
        if measurement.vector_component not in components:
            error("Measurement vector component must be " + ", ".join(sorted(components)), measurement.id)
        if measurement.weighting != "area":
            error("Only physical-area measurement weighting is implemented", measurement.id)
        if not math.isfinite(measurement.averaging_length) or measurement.averaging_length < 0:
            error("Hotspot averaging length must be finite and nonnegative", measurement.id)
        if not 0 <= measurement.percentile <= 100:
            error("Measurement percentile must lie in [0, 100]", measurement.id)
        for field in ("limit", "lower_limit", "reference_temperature", "effective_thickness"):
            value = getattr(measurement, field)
            if value is not None and (not math.isfinite(value) or (field in {"reference_temperature", "effective_thickness"} and value <= 0)):
                error(f"Measurement {field} is invalid", measurement.id)
    rules = power.rules
    if not math.isfinite(rules.dbu) or rules.dbu <= 0:
        error("Routing rule DBU must be positive")
    if any(key not in layer_ids for key in rules.allowed_layers):
        error("Routing allowed layers contain an unknown physical layer")
    for transition in rules.transitions:
        if len(transition) != 2 or len(set(transition)) != 2 or any(key not in layer_ids for key in transition):
            error("Routing transitions require two distinct existing layer IDs")
    for recipe in power.recipes:
        if recipe.layer_id not in layer_ids or recipe.kind not in {"bus", "finger_feed", "strap", "via_array"} or recipe.version != 1:
            error("Recipe requires an existing layer, supported kind and version 1", recipe.id)
    for experiment in power.experiments:
        if not 1 <= experiment.max_evaluations <= 10000 or experiment.finalists < 1:
            error("Optimization evaluation/finalist budgets are invalid", experiment.id)
        if len(experiment.refinement_factors) < 2 or any(not math.isfinite(v) or v <= 0 for v in experiment.refinement_factors):
            error("Optimization requires at least two positive mesh refinement factors", experiment.id)
        for variable in experiment.variables:
            if variable.kind not in {"continuous", "integer", "choice"} or variable.recipe_id not in {r.id for r in power.recipes}:
                error("Design variable has unknown kind or recipe", variable.id)
            if variable.kind == "choice" and not variable.values:
                error("Choice design variable requires values", variable.id)
            if variable.kind != "choice" and (not math.isfinite(variable.minimum) or not math.isfinite(variable.maximum) or variable.minimum > variable.maximum):
                error("Design variable bounds must be finite and ordered", variable.id)
        for scenario in experiment.scenarios:
            if scenario.service_mode not in {"target_current", "fixed_voltage"}:
                error("Scenario service mode must be target_current or fixed_voltage", scenario.id)
            if not math.isfinite(scenario.required_current) or scenario.required_current <= 0:
                error("Equal-service scenario requires positive current", scenario.id)
            if not math.isfinite(scenario.compliance_voltage) or scenario.compliance_voltage <= 0:
                error("Scenario compliance voltage must be positive", scenario.id)
            if not 0 < scenario.service_tolerance < 1 or not math.isfinite(scenario.weight) or scenario.weight <= 0:
                error("Scenario tolerance/weight is invalid", scenario.id)
    return messages
