# SPDX-License-Identifier: MPL-2.0
"""Identical quantitative colormaps for Qt maps and exported figures."""
from __future__ import annotations

from functools import lru_cache

import numpy as np
from matplotlib import colormaps
from matplotlib.colors import LinearSegmentedColormap

COLORMAPS = {
    "teal": "Teal", "viridis": "Viridis", "magma": "Magma", "cividis": "Cividis",
    "coolwarm": "Coolwarm (diverging)", "grayscale": "Grayscale",
}


@lru_cache(maxsize=len(COLORMAPS))
def get_colormap(key: str = "teal"):
    if key not in COLORMAPS:
        raise ValueError(f"Unknown colormap {key!r}")
    if key == "teal":
        return LinearSegmentedColormap.from_list("etlayout_teal", ["#EDF5F1", "#0E5245"], N=256)
    return colormaps["Greys" if key == "grayscale" else key]


def rgba(values, key: str = "teal") -> np.ndarray:
    """Map normalized scalar values to RGBA floats; undefined values are clear."""
    return np.asarray(get_colormap(key)(np.ma.masked_invalid(np.clip(values, 0., 1.))))
