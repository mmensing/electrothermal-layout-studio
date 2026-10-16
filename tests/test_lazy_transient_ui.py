# SPDX-License-Identifier: MPL-2.0
"""Stored transient metadata stays explicit until a user requests field loading."""
from pathlib import Path

import numpy as np
import pytest

from etlayout.gui.transient import TimeHistoryPanel, TimeNavigation
from etlayout.io.history_store import open_history_catalog
from etlayout.io.project import load_project, save_project


@pytest.fixture
def transient_record(tmp_path):
    project, result = load_project(Path(__file__).parent / "fixtures" / "legacy_transient_e18.etl")
    path = tmp_path / "transient.etlayout"
    save_project(project, path, result, archive_format=3)
    with open_history_catalog(path) as store:
        yield store, store.records[0], result


def test_metadata_time_navigation_preserves_exact_indices_without_loading(qtbot, transient_record):
    store, record, _ = transient_record
    navigation = TimeNavigation()
    qtbot.addWidget(navigation)
    changed = []
    navigation.snapshot_changed.connect(changed.append)
    navigation.set_record(record)
    assert navigation.slider.maximum() == len(record.times) - 1
    assert navigation.slider.value() == len(record.times) - 1
    assert changed == [] and store.stats.resident_bytes == 0
    navigation.set_record(record, index=0)
    assert navigation.slider.value() == 0 and changed == []
    navigation.select_time(record.times[1])
    assert changed == [1] and navigation.slider.value() == 1
    assert store.stats.resident_bytes == store.stats.misses == 0
    navigation.set_result(None)
    assert len(navigation.times) == 0 and navigation.isHidden()


def test_stored_step_trace_never_implicitly_scans_local_fields(qtbot, transient_record):
    store, record, _ = transient_record
    panel = TimeHistoryPanel()
    qtbot.addWidget(panel)
    requested = []
    panel.scan_requested.connect(requested.append)
    panel.set_record(record, store.transient_metadata(record.run_id))
    rows = store.transient_metadata(record.run_id)["rows"]
    np.testing.assert_array_equal(panel.trace_times, [row["time_s"] for row in rows])
    assert "Stored completed" in panel.description.text()
    assert "numerical fields not loaded" in panel.description.text()
    assert store.stats.resident_bytes == 0
    panel.set_context("metal")
    panel.scope_selector.setCurrentIndex(panel.scope_selector.findData("selection"))
    assert len(panel.trace_times) == 0 and not panel.load_history.isHidden()
    assert requested == [] and store.stats.resident_bytes == 0
    panel.load_history.click()
    assert requested == ["trace"]
    panel.export_history()
    assert requested == ["trace", "export"]
    assert store.stats.resident_bytes == 0
    panel.plot.close()


def test_explicit_full_load_restores_spatial_scope_quantity_and_cursor(qtbot, transient_record):
    store, record, result = transient_record
    panel = TimeHistoryPanel()
    qtbot.addWidget(panel)
    panel.set_record(record, store.transient_metadata(record.run_id))
    layer = next(owner for owner, fields in result.fields.items() if "temperature" in fields)
    panel.set_context(layer)
    panel.scope_selector.setCurrentIndex(panel.scope_selector.findData("selection"))
    panel.metric_selector.setCurrentIndex(1)
    panel.select_time(record.times[0])
    context = panel.context()
    with store.acquire(record.run_id) as loaded:
        panel.set_result(loaded)
        panel.restore_context(context)
        assert panel.layer == layer and panel.scope_selector.currentData() == "selection"
        assert panel.metric_selector.currentIndex() == 1 and panel.selected_time == record.times[0]
        assert len(panel.trace_times) == len(record.times)
        assert not panel.load_history.isVisible()
        assert "Stored" not in panel.description.text()
    panel.plot.close()
