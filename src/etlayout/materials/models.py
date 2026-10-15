# SPDX-License-Identifier: MPL-2.0
"""Scalar constitutive laws. Values always carry the owning property's SI unit."""
from __future__ import annotations

import warnings

import numpy as np

from etlayout.models import PropertyModel, SimulationError


def validate_property(model: PropertyModel) -> None:
    if model.kind not in {"constant", "linear", "polynomial", "table"}:
        raise SimulationError(f"Unknown property law: {model.kind}")
    if not 0 < model.minimum_temperature < model.maximum_temperature:
        raise SimulationError("Material validity requires 0 < minimum temperature < maximum temperature")
    if not np.isfinite(model.value) or model.value <= 0:
        raise SimulationError("Reference material property must be finite and positive")
    if model.kind == "polynomial" and not model.coefficients:
        raise SimulationError("Polynomial needs coefficients in increasing power of T − Tref")
    if model.kind == "table":
        x, y = np.asarray(model.temperatures), np.asarray(model.values)
        if len(x) < 2 or len(x) != len(y) or not np.all(np.diff(x) > 0):
            raise SimulationError("Property table needs at least two strictly increasing temperatures")
        if not np.all(np.isfinite(x)) or not np.all(np.isfinite(y)) or np.any(y <= 0):
            raise SimulationError("Property table values must be finite and positive")


def evaluate_property(model: PropertyModel, temperature) -> np.ndarray:
    validate_property(model)
    t = np.asarray(temperature, dtype=float)
    if not np.all(np.isfinite(t)) or np.any(t <= 0):
        raise SimulationError("Temperature must be finite and positive in kelvin")
    lo, hi = model.minimum_temperature, model.maximum_temperature
    if np.any((t < lo) | (t > hi)):
        warnings.warn(f"Material evaluated outside validity range [{lo:g}, {hi:g}] K", RuntimeWarning,
                      stacklevel=2)
    d = t - model.reference_temperature
    if model.kind == "constant":
        value = np.full_like(t, model.value)
    elif model.kind == "linear":
        value = model.value * (1 + model.alpha * d)
    elif model.kind == "polynomial":
        value = np.polynomial.polynomial.polyval(d, model.coefficients)
    else:
        if np.any((t < model.temperatures[0]) | (t > model.temperatures[-1])):
            warnings.warn("Tabulated material evaluated outside measured range; endpoint values used",
                          RuntimeWarning, stacklevel=2)
        value = np.interp(t, model.temperatures, model.values)
    if not np.all(np.isfinite(value)) or np.any(value <= 0):
        raise SimulationError("Material law produced nonpositive or nonfinite values; check coefficients/range")
    return np.asarray(value)
