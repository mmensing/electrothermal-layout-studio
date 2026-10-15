# SPDX-License-Identifier: MPL-2.0
"""Readable labels that retain the distinctions in an actual color scale."""
from __future__ import annotations

import math
import sys


def distinct_labels(values, *, minimum_precision=4):
    """Use one significant-digit precision, increasing until distinct values differ.

    IEEE floats need at most 17 significant digits for a round trip. Exact repeats
    intentionally keep the same label; numeric data and scale endpoints are never
    rounded or changed. Normal-sized ranges retain short familiar labels.
    """
    values = tuple(float(value) for value in values)
    distinct = len(set(values))
    for precision in range(minimum_precision, 18):
        labels = tuple(f"{0. if value == 0. else value:.{precision}g}" for value in values)
        if len(set(labels)) == distinct:
            return labels
    return labels


def scale_ticks(low, high, *, logarithmic=False, factor=1.):
    """Return endpoint/midpoint samples and their mutually distinct display labels."""
    fractions = (.5,) if low == high else (0., .5, 1.)
    values = tuple(low if fraction == 0. else high if fraction == 1. else
                   max(low, min(high, math.exp(math.log(low) / 2 + math.log(high) / 2)))
                   if logarithmic else low * (1 - fraction) + high * fraction for fraction in fractions)
    if len(values) == 3 and values[1] in (values[0], values[2]):
        # A one-ULP range has no representable midpoint. Endpoints still retain
        # their exact distinct values, including when their logarithms coincide.
        fractions, values = (0., 1.), (low, high)
    return fractions, distinct_labels(value * factor for value in values)


def series_context_range(values):
    """Give roundoff-level/constant trends a readable contextual viewport.

    The 4096-ULP threshold recognizes accumulated float summation variation;
    it is a display choice, not an error estimate or scientific acceptance.
    There is no absolute floor: tiny, signed and resolved signals retain Auto.
    Curves, scalar values, rankings and exported numeric data stay unchanged.
    """
    finite = [float(value) for value in values if math.isfinite(value)]
    if not finite:
        return None
    low, high = min(finite), max(finite)
    scale = max(abs(low), abs(high))
    if not scale or high - low > min(4096 * math.ulp(scale), scale * 1e-10):
        return None
    center = low / 2 + high / 2
    padding = max(scale * .001, 4 * math.ulp(scale))
    return max(-sys.float_info.max, center - padding), min(sys.float_info.max, center + padding)
