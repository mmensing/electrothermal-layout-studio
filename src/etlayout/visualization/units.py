# SPDX-License-Identifier: MPL-2.0
"""Presentation units only; solver arrays, configuration and numerical exports use SI."""
from __future__ import annotations

import numpy as np

# Multipliers convert canonical SI values to the displayed unit. Keep fixed
# conventions across layers, runs and time frames so comparisons never rescale.
DISPLAY_UNITS = {
    "A/m²": ("mA/µm²", 1e-9, 1e9),
    "A/m": ("mA/µm", 1e-3, 1e3),
    "V/m": ("V/µm", 1e-6, 1e6),
    "W/m²": ("W/mm²", 1e-6, 1e6),
    "K/m": ("K/µm", 1e-6, 1e6),
    "m": ("µm", 1e6, 1e-6),
    "m²": ("µm²", 1e12, 1e-12),
    "fraction": ("%", 100., .01),
}


def display_unit(si_unit):
    return DISPLAY_UNITS.get(si_unit, (si_unit, 1.))[0]


def display_factor(si_unit):
    return DISPLAY_UNITS.get(si_unit, (si_unit, 1.))[1]


def to_display(value, si_unit):
    if value is None or isinstance(value, str):
        return value
    if isinstance(value, (list, tuple)):
        value = np.asarray(value, dtype=float)
    return value * display_factor(si_unit)


def to_si(value, si_unit):
    # Explicit reciprocal multipliers avoid division by an inexact 1e-9
    # introducing unnecessary ULP drift in common manual density ranges.
    if isinstance(value, (list, tuple)):
        value = np.asarray(value, dtype=float)
    return value * DISPLAY_UNITS.get(si_unit, (si_unit, 1., 1.))[2]
