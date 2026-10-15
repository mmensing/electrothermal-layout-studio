# SPDX-License-Identifier: MPL-2.0
"""SI measurement and acceptance contracts shared by validation and reporting."""
import math

from etlayout.models import SimulationError

QUANTITIES = {
    "electric_field": ("electric_field", "V/m"),
    "metal_current_density": ("current_density", "A/m²"),
    "sheet_current": ("sheet_current", "A/m"),
    "effective_current_density": ("sheet_current", "A/m²"),
    "potential": ("potential", "V"), "temperature": ("temperature", "K"),
    "temperature_rise": ("temperature", "K"), "joule_density": ("joule_density", "W/m²"),
    "heat_density": ("heat_density", "W/m²"), "contact_density": ("contact_density", "W/m²"),
    "contact_normal_density": ("j_normal", "A/m²"), "contact_voltage_drop": ("voltage_drop", "V"),
    "contact_heat_density": ("heat_density", "W/m²"), "port_current": ("current", "A"),
    "port_voltage": ("voltage", "V"), "port_power": ("power", "W"),
    "terminal_current": ("current", "A"), "loss_power": ("power", "W"),
}


def validate_limits(spec):
    """Reject ambiguous old exceedance contracts; never infer a fraction bound."""
    for value in (spec.limit, spec.lower_limit, spec.threshold, spec.max_exceedance_fraction):
        if value is not None and not math.isfinite(value):
            raise SimulationError("Measurement limits and thresholds must be finite")
    if spec.aggregation == "exceedance":
        if spec.limit is not None or spec.lower_limit is not None:
            raise SimulationError(
                "Legacy/ambiguous exceedance limits: move the dimensional sample limit to threshold, "
                "declare threshold_units, clear limit/lower_limit, and set max_exceedance_fraction "
                "explicitly for a constraint. No automatic reinterpretation is safe.")
        if spec.threshold is None:
            raise SimulationError("Exceedance requires a dimensional threshold and optional max_exceedance_fraction")
        unit = QUANTITIES.get(spec.quantity, (None, ""))[1]
        if spec.threshold_units and spec.threshold_units != unit:
            raise SimulationError(f"Exceedance threshold requires {unit}, received {spec.threshold_units}")
        if spec.units and spec.units != "fraction":
            raise SimulationError("Exceedance result units must be fraction; threshold_units describe the samples")
        if spec.max_exceedance_fraction is not None and not 0 <= spec.max_exceedance_fraction <= 1:
            raise SimulationError("max_exceedance_fraction must lie in [0, 1]")
    else:
        if spec.threshold is not None or spec.threshold_units or spec.max_exceedance_fraction is not None:
            raise SimulationError("Threshold and exceedance-fraction settings require exceedance aggregation")
        if spec.limit is not None and spec.lower_limit is not None and spec.lower_limit > spec.limit:
            raise SimulationError("Lower measurement limit cannot exceed upper limit")


def has_acceptance_limit(spec):
    return (spec.max_exceedance_fraction is not None if spec.aggregation == "exceedance"
            else spec.limit is not None or spec.lower_limit is not None)
