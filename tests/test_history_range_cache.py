# SPDX-License-Identifier: MPL-2.0
"""Range caching is bounded, scoped, invalidatable and releases source runs."""
import gc
import weakref

import numpy as np

from etlayout.models import PlanarMesh, SimulationResult, TransientHistory
from etlayout.visualization import history


def result():
    mesh = PlanarMesh(np.array([[0., 0.], [1., 0.], [0., 1.]]), np.array([[0, 1, 2]]),
                      {"layer:m": np.array([0]), "heat:a": np.array([0])})
    frames = [SimulationResult(mesh, fields={"m": {"temperature": np.full(3, value)}}) for value in (300., 350.)]
    return SimulationResult(mesh, transient=TransientHistory(np.array([0., 1.]), frames))


def test_repeated_range_is_cached_and_filter_changes_remain_distinct(monkeypatch):
    history.clear_history_range_cache()
    source = result()
    original = history.field_support
    calls = []
    def counted(*args, **kwargs):
        calls.append(kwargs)
        return original(*args, **kwargs)
    monkeypatch.setattr(history, "field_support", counted)
    assert history.history_field_limits(source, "m", "temperature") == (300, 350)
    assert history.history_field_limits(source, "m", "temperature") == (300, 350)
    assert len(calls) == 2
    history.history_field_limits(source, "m", "temperature", region="heat:a")
    history.history_field_limits(source, "m", "temperature", logarithmic=True)
    assert len(calls) == 6
    source.transient.snapshots[1].fields["m"]["temperature"][:] = 400
    history.clear_history_range_cache(source)
    assert history.history_field_limits(source, "m", "temperature") == (300, 400)


def test_cache_is_bounded_and_does_not_keep_results_alive():
    history.clear_history_range_cache()
    sources = [result() for _ in range(history._MAX_RANGE_ENTRIES + 3)]
    for source in sources:
        history.history_field_limits(source, "m", "temperature")
    assert len(history._RANGE_CACHE) == history._MAX_RANGE_ENTRIES
    reference = weakref.ref(sources[-1])
    del source
    sources.clear()
    gc.collect()
    assert reference() is None
    assert not history._RANGE_CACHE
