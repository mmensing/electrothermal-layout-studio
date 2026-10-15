# SPDX-License-Identifier: MPL-2.0
"""Time-series reductions with the same layer/region support as field maps."""
from __future__ import annotations

import weakref
from collections import OrderedDict

import numpy as np

from etlayout.visualization.sampling import FieldSampler
from etlayout.visualization.support import field_support

_RANGE_CACHE = OrderedDict()
_MAX_RANGE_ENTRIES = 128


def clear_history_range_cache(result=None):
    """Invalidate extrema after intentional edits to otherwise immutable results."""
    for key in list(_RANGE_CACHE):
        if result is None or _RANGE_CACHE[key][0]() is result:
            _RANGE_CACHE.pop(key, None)


def _release_range(key, reference):
    entry = _RANGE_CACHE.get(key)
    if entry is not None and entry[0] is reference:
        _RANGE_CACHE.pop(key, None)


def history_field_limits(result, layer, field, *, region="", logarithmic=False):
    """Cached extrema across immutable retained fields, without retaining arrays.

    Weak source references and checked identity prevent object-ID reuse errors.
    Entries are bounded independently of the application's retained run budget.
    """
    history = result.transient
    frames = history.snapshots if history else [result]
    key = (id(result), layer, field, region, bool(logarithmic)) if history else None
    signature = (id(history), id(history.times), len(frames), id(result.mesh)) if history else None
    entry = _RANGE_CACHE.get(key)
    if entry is not None and entry[0]() is result and entry[1] == signature:
        _RANGE_CACHE.move_to_end(key)
        return entry[2]
    limits = []
    for frame in frames:
        try:
            support = field_support(frame, layer, field, region=region, logarithmic=logarithmic)
        except KeyError:
            continue
        if support.limits is not None:
            limits.append(support.limits)
    limits = (min(pair[0] for pair in limits), max(pair[1] for pair in limits)) if limits else None
    if key is not None:
        reference = weakref.ref(result, lambda ref, key=key: _release_range(key, ref))
        _RANGE_CACHE[key] = reference, signature, limits
        _RANGE_CACHE.move_to_end(key)
        while len(_RANGE_CACHE) > _MAX_RANGE_ENTRIES:
            _RANGE_CACHE.popitem(last=False)
    return limits


def local_history(result, layer, field="temperature", *, region="", reduction="maximum", probe=None):
    """Local traces use retained fields; means are area-weighted triangle means.

    Local curves deliberately do not interpolate unsaved integration states.
    Probes outside the selected support remain NaN rather than crossing a hole.
    """
    if not result.transient:
        return np.array([]), np.array([])
    values = []
    sampler = None
    for frame in result.transient.snapshots:
        try:
            if probe is not None:
                # Frames share an immutable mesh: reuse spatial indexing.
                if sampler is None:
                    sampler = FieldSampler(frame)
                else:
                    sampler.result = frame
                value = sampler.sample(layer, field, np.asarray([probe]), region=region)[0]
            else:
                support = field_support(frame, layer, field, region=region)
                if support.limits is None:
                    value = np.nan
                elif reduction == "maximum":
                    value = support.limits[1]
                elif reduction == "mean":
                    value = np.average(support.values, weights=frame.mesh.areas[support.indices])
                else:
                    raise ValueError(f"Unknown history reduction {reduction!r}")
        except KeyError:
            value = np.nan
        values.append(value)
    return np.asarray(result.transient.times), np.asarray(values)
