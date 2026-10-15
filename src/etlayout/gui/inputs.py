# SPDX-License-Identifier: MPL-2.0
"""Unit-aware parsing for forms, independent of the serialized model value type."""
from __future__ import annotations

import math
import re

PREFIXES = {"p": 1e-12, "n": 1e-9, "u": 1e-6, "µ": 1e-6, "μ": 1e-6,
            "m": 1e-3, "k": 1e3, "M": 1e6, "G": 1e9, "T": 1e12}


def parse_number(raw: str, unit: str = "", *, integer=False) -> float | int:
    """Unqualified input uses the displayed unit; an explicit suffix overrides it.

    E.g. a µm field accepts ``2``, ``2000 nm`` and ``2e-6 m`` identically.
    A V field accepts ``20 mV``, ``20m`` and ``0.02`` identically.
    """
    match = re.fullmatch(r"\s*([+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?)\s*(.*?)\s*", raw)
    if not match:
        raise ValueError("Enter a finite number, for example 2.5 or 1e-6.")
    value = float(match[1])
    suffix = match[2].replace(" ", "")
    expected = unit.replace(" ", "")
    base = "m" if expected == "µm" else expected
    scale = 1e-6 if expected == "µm" else 1.
    if suffix:
        if suffix == base:
            scale = 1.
        elif suffix == expected:
            pass
        elif suffix in PREFIXES and base != suffix:
            scale = PREFIXES[suffix]
        elif base and suffix.endswith(base) and suffix[:-len(base)] in PREFIXES:
            # A prefix on a powered leading unit is powered too: mm² = 1e-6 m².
            power = re.match(r"^[A-Za-zµμΩ]+([²³])", base)
            exponent = {"²": 2, "³": 3}[power[1]] if power else 1
            scale = PREFIXES[suffix[:-len(base)]] ** exponent
        else:
            raise ValueError(f"Use {unit or 'a number without a unit'} or a compatible engineering prefix.")
    value *= scale
    if not math.isfinite(value):
        raise ValueError("The value must be finite.")
    if integer:
        if not value.is_integer():
            raise ValueError("Enter a whole number.")
        return int(value)
    return value
