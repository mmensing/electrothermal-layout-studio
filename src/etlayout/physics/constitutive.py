# SPDX-License-Identifier: MPL-2.0
"""SI resistive conduction and smooth local GaN charge control.

The HEMT law is a calibrated resistive charge-control approximation, not a
drift-diffusion or high-field transistor model. See docs/physics.md.
"""
from __future__ import annotations

import numpy as np

from etlayout.materials.models import evaluate_property
from etlayout.models import ElectricalModel, Material, PhysicalLayer, SimulationError

ELEMENTARY_CHARGE = 1.602176634e-19


def mobility(model: ElectricalModel, temperature: np.ndarray) -> np.ndarray:
    temperature = np.asarray(temperature, dtype=float)
    if model.mobility <= 0 or model.reference_temperature <= 0 or np.any(temperature <= 0):
        raise SimulationError("Mobility and absolute temperatures must be positive")
    return model.mobility * (temperature / model.reference_temperature) ** (-model.mobility_exponent)


def sheet_conductance(layer: PhysicalLayer, material: Material, temperature: np.ndarray,
                      potential: np.ndarray | float = 0., gate_voltage: np.ndarray | None = None,
                      derivative: bool = False) -> np.ndarray | tuple[np.ndarray, np.ndarray]:
    """Return (elements, 2) diagonal sheet conductance in siemens.

    The optional derivative is dGs/d(local element-mean potential). It is used
    in the consistent nonlinear FEM Jacobian, including local channel bias.
    """
    model = layer.electrical
    t = np.asarray(temperature, dtype=float)
    if layer.thickness <= 0:
        raise SimulationError(f"Layer {layer.id}: thickness must be positive")
    anisotropy = np.asarray(material.electrical_anisotropy, dtype=float)
    if anisotropy.shape != (2,) or np.any(anisotropy <= 0) or not np.all(np.isfinite(anisotropy)):
        raise SimulationError(f"Material {material.id}: electrical anisotropy must contain two positive factors")
    dg = np.zeros_like(t)
    if model.kind == "bulk":
        rho = np.asarray(evaluate_property(material.resistivity, t))
        g = layer.thickness / rho
    elif model.kind == "sheet":
        g = 1. / np.asarray(evaluate_property(model.sheet_resistance, t))
    elif model.kind == "semiconductor":
        if model.carrier_density <= 0:
            raise SimulationError(f"Layer {layer.id}: carrier density must be positive")
        g = ELEMENTARY_CHARGE * model.carrier_density * mobility(model, t) * layer.thickness
    elif model.kind == "hemt":
        if (model.smoothing_voltage <= 0 or model.gate_coupling < 0
                or model.minimum_sheet_conductance <= 0 or model.background_sheet_density < 0):
            raise SimulationError(f"Layer {layer.id}: invalid charge-control smoothing/density/conductance")
        vg = model.gate_voltage if gate_voltage is None else gate_voltage
        vth = model.threshold_voltage + model.threshold_temperature_coefficient * (t - model.reference_temperature)
        x = (np.asarray(vg) - np.asarray(potential) - vth) / model.smoothing_voltage
        raw = model.gate_coupling * model.smoothing_voltage * np.logaddexp(0., x)
        # Stable logistic, including strongly depleted and accumulated regions.
        sigmoid = np.exp(-np.logaddexp(0., -x))
        draw = -model.gate_coupling * sigmoid
        if model.maximum_sheet_density > 0:
            ratio = raw / model.maximum_sheet_density
            saturation = np.tanh(ratio)
            density = model.background_sheet_density + model.maximum_sheet_density * saturation
            ddensity = draw * (1. - saturation * saturation)
        else:
            density = model.background_sheet_density + raw
            ddensity = draw
        mu = mobility(model, t)
        g = model.minimum_sheet_conductance + ELEMENTARY_CHARGE * mu * density
        dg = ELEMENTARY_CHARGE * mu * ddensity
    else:
        raise SimulationError(f"Unknown electrical model {model.kind!r}")
    if np.any(~np.isfinite(g)) or np.any(g <= 0):
        raise SimulationError(f"Layer {layer.id}: electrical conductance must be finite and positive")
    result = np.asarray(g)[..., None] * anisotropy
    return (result, np.asarray(dg)[..., None] * anisotropy) if derivative else result
