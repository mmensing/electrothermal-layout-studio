# SPDX-License-Identifier: MPL-2.0
"""Real three-layer pulse fields keep their layer, region, time and export scope."""
import json
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import numpy as np
import pytest

from etlayout.examples import make_example
from etlayout.gui.export import _view_metadata
from etlayout.gui.viewer import LayoutView
from etlayout.io.export import export_results
from etlayout.io.project import load_project
from etlayout.models import TransientDrive, TransientSettings, Waveform
from etlayout.simulation.runner import run_project
from etlayout.visualization.sampling import FieldSampler
from etlayout.visualization.support import field_support, region_elements, region_options


def test_e10_pulses_render_and_export_selected_layer_region_and_time(tmp_path, qtbot):
    project = make_example("E10", tmp_path / "source")
    project.solver.mode = "electrothermal"
    project.transient = TransientSettings(enabled=True, stop_time=40e-6, time_step=5e-6,
        output_interval=10e-6, drives=[TransientDrive("terminals.drain.value",
            Waveform(kind="pulse", low=0., high=.2, width=15e-6, period=30e-6))])
    result = run_project(project)
    assert result.converged and result.transient.completed
    assert all(snapshot.elapsed_seconds == result.elapsed_seconds for snapshot in result.transient.snapshots)
    view = LayoutView()
    view.resize(700, 500)
    qtbot.addWidget(view)
    # Exact peak retention can insert a frame between scheduled outputs.
    selected_frames = [result.transient.snapshots[int(np.argmin(abs(result.transient.times - time)))]
                       for time in (10e-6, 20e-6)]
    assert [frame.metrics["time_s"] for frame in selected_frames] == pytest.approx([10e-6, 20e-6])
    for frame in selected_frames:
        sampler = FieldSampler(frame)
        peaks = {}
        for layer in ("metal", "oxide", "substrate"):
            assert "temperature" in frame.fields[layer]
            support = field_support(frame, layer, "temperature")
            assert np.array_equal(support.indices, np.sort(frame.mesh.elements(layer)))
            assert np.isfinite(frame.fields[layer]["temperature"][support.nodes]).all()
            peaks[layer] = support.limits[1]
            view.show_field(frame, layer, "temperature", colormap="magma")
            assert view.result is frame and view.field_layer == layer
            assert view.overlay_item is not None
            metadata = _view_metadata(view)
            assert metadata["provenance"]["time_s"] == frame.metrics["time_s"]
            assert metadata["provenance"]["applied_drives"] == frame.metrics["applied_drives"]
            for region, _ in region_options(frame, layer):
                filtered = field_support(frame, layer, "temperature", region=region)
                assert np.array_equal(filtered.indices, region_elements(frame, layer, region))
                if len(filtered.indices):
                    points = frame.mesh.centroids[filtered.indices[:3]]
                    assert np.isfinite(sampler.sample(layer, "temperature", points, region=region)).all()
        assert peaks["metal"] > peaks["substrate"] > 300.
    assert selected_frames[0].metrics["electrical_power"] > 0
    assert selected_frames[1].metrics["electrical_power"] == 0
    frame = selected_frames[0]
    output = tmp_path / "selection"
    export_results(frame, output, plots=False, layers=["oxide"], field="temperature",
                   region="interface:metal_oxide", colormap="magma",
                   provenance={"time_s": frame.metrics["time_s"], "transient_completed": True})
    manifest = json.loads((output / "manifest.json").read_text(encoding="utf-8"))
    assert list(manifest["layers"]) == ["oxide"]
    assert manifest["visualization"]["time_s"] == frame.metrics["time_s"]
    assert manifest["layers"]["oxide"]["triangle_count"] == len(region_elements(frame, "oxide", "interface:metal_oxide"))
    _, reopened = load_project(output / "simulation.etl")
    assert reopened.metrics["time_s"] == frame.metrics["time_s"]
    assert reopened.fields["oxide"]["temperature"] == pytest.approx(frame.fields["oxide"]["temperature"], nan_ok=True)
