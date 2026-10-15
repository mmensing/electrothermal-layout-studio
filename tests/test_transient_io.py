# SPDX-License-Identifier: MPL-2.0
"""Portable pulse histories retain one mesh, numeric fields, time and provenance."""
from __future__ import annotations

import csv
import io
import json
import zipfile
from copy import deepcopy
from pathlib import Path

import numpy as np
import pytest
from solver_helpers import all_region, rectangle

from etlayout.io.export import export_field_plot, export_results
from etlayout.io.project import configuration_hash, load_project, physics_hash, save_project
from etlayout.io.transient import validate_history
from etlayout.models import HeatSource, SimulationError, TransientDrive, TransientSettings, Waveform
from etlayout.solvers import solve


def test_real_legacy_enabled_transient_identity_survives_load_save_load(tmp_path):
    path = Path(__file__).parent / "fixtures" / "legacy_transient_e18.etl"
    project, result = load_project(path)
    expected = "c2fc5f6fe25ce734c035106a39697573670b11b645434a7d6ff45a726aa17cd9"
    assert "initialization" not in result.configuration["transient"]
    assert result.physics_hash == physics_hash(project) == expected
    assert result.metrics["transient_peak_temperature"] > 300.
    target = tmp_path / "resaved.etl"
    save_project(project, target, result)
    reopened, saved = load_project(target)
    assert saved.physics_hash == physics_hash(reopened) == expected
    assert saved.configuration_hash == result.configuration_hash
    np.testing.assert_array_equal(saved.fields["metal"]["temperature"], result.fields["metal"]["temperature"])
    changed = deepcopy(project)
    changed.transient.initialization = "steady"
    assert physics_hash(changed) != expected


@pytest.fixture
def pulse():
    project, mesh = rectangle(nx=3, ny=2)
    project.solver.mode = "thermal"
    project.materials["copper"].density = 2000.
    project.materials["copper"].specific_heat = 1000.
    project.heat_sources = [HeatSource("heater", "metal", value=10.)]
    all_region(mesh, "heat", "heater")
    project.transient = TransientSettings(enabled=True, stop_time=.7, time_step=.13, output_interval=.2,
        drives=[TransientDrive("heat_sources.heater.value", Waveform(kind="pulse", low=0., high=10.,
                                                                   delay=.05, width=.1, period=.2))])
    result = solve(project, mesh)
    assert result.converged
    return project, result


def rewrite_archive(source, target, callback):
    with zipfile.ZipFile(source) as archive:
        files = {item.filename: archive.read(item.filename) for item in archive.infolist()}
    callback(files)
    with zipfile.ZipFile(target, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for name, content in files.items():
            archive.writestr(name, content)


def test_real_pulse_archive_preserves_fields_rows_drives_and_shares_one_mesh(pulse, tmp_path):
    project, result = pulse
    path = tmp_path / "pulse.etl"
    save_project(project, path, result)
    reopened, saved = load_project(path)
    assert reopened.to_dict() == project.to_dict()
    assert saved.configuration_hash == result.configuration_hash
    assert saved.physics_hash == result.physics_hash
    assert saved.transient.completed
    assert saved.transient.times == pytest.approx([0., .2, .4, .6, .7])
    assert saved.transient.rows == result.transient.rows
    assert saved.metrics == result.metrics
    assert saved.metrics["temperature_max"] == pytest.approx(303.5, abs=1e-6)
    for expected, snapshot in zip(result.transient.snapshots, saved.transient.snapshots, strict=True):
        assert snapshot.mesh is saved.mesh and snapshot.transient is None
        assert snapshot.configuration is saved.configuration
        assert snapshot.elapsed_seconds == saved.elapsed_seconds == result.elapsed_seconds
        assert snapshot.metrics == expected.metrics
        assert snapshot.terminals == expected.terminals
        for layer, fields in expected.fields.items():
            for name, values in fields.items():
                np.testing.assert_array_equal(snapshot.fields[layer][name], values)
    with zipfile.ZipFile(path) as archive:
        assert "transient/metadata.json" in archive.namelist()
        assert len([name for name in archive.namelist() if name.startswith("transient/frame_")]) == 5


@pytest.mark.parametrize("case", ["duplicate", "nan", "late", "missing_step", "snapshot_time", "missing_time", "result_time", "row_time", "duration", "final_field", "nested", "mesh_boundary", "completion"])
def test_rejects_inconsistent_transient_histories(pulse, case):
    project, original = pulse
    result = deepcopy(original)
    history = result.transient
    if case == "duplicate":
        history.times[1] = 0.
    elif case == "nan":
        history.times[1] = np.nan
    elif case == "late":
        history.times[-1] = 1.
    elif case == "missing_step":
        history.times[1] = .211
        history.snapshots[1].metrics["time_s"] = .211
    elif case == "snapshot_time":
        history.snapshots[1].metrics["time_s"] = np.nan
    elif case == "missing_time":
        history.snapshots[1].metrics.pop("time_s")
    elif case == "result_time":
        result.metrics["time_s"] = .3
    elif case == "row_time":
        history.rows[1]["time_s"] = "0.05"
    elif case == "duration":
        history.rows[1]["time_step"] = .2
    elif case == "final_field":
        result.fields = deepcopy(result.fields)
        result.fields["metal"]["temperature"][0] += 1.
    elif case == "nested":
        history.snapshots[1].transient = deepcopy(history)
    elif case == "mesh_boundary":
        history.snapshots[1].mesh = deepcopy(history.snapshots[1].mesh)
        history.snapshots[1].mesh.terminal_nodes["left"] = np.array([0])
    elif case == "completion":
        history.completed = False
    with pytest.raises(SimulationError):
        validate_history(result, project)


@pytest.mark.parametrize("setting,value", [("max_snapshots", 2), ("max_steps", 1), ("max_history_bytes", 64)])
def test_history_budgets_are_enforced_before_save(pulse, setting, value):
    project, result = pulse
    setattr(project.transient, setting, value)
    with pytest.raises(SimulationError, match="budget"):
        validate_history(result, project)


def test_archive_rejects_bad_time_metadata_before_loading_frames(pulse, tmp_path):
    project, result = pulse
    source, target = tmp_path / "valid.etl", tmp_path / "broken.etl"
    save_project(project, source, result)
    def corrupt(files):
        data = json.loads(files["transient/metadata.json"])
        data["times_s"][1] = 0.
        files["transient/metadata.json"] = json.dumps(data)
        files.pop("transient/frame_00000.npz")
    rewrite_archive(source, target, corrupt)
    with pytest.raises(SimulationError, match="increase strictly"):
        load_project(target)


def test_archive_array_expansion_respects_history_budget(pulse, tmp_path, monkeypatch):
    project, result = pulse
    path = tmp_path / "pulse.etl"
    save_project(project, path, result)
    with zipfile.ZipFile(path) as archive:
        metadata_bytes = archive.getinfo("transient/metadata.json").file_size
    monkeypatch.setattr("etlayout.io.transient.MAX_HISTORY_BYTES", metadata_bytes)
    with pytest.raises(SimulationError, match="byte budget"):
        load_project(path)


@pytest.mark.parametrize("limit", ["configured", "safety_cap"])
def test_history_encoded_metadata_and_arrays_share_exact_save_load_budget(pulse, tmp_path, monkeypatch, limit):
    project, result = pulse
    # Snapshot metadata was omitted from the old save estimate. Use a small
    # rich payload to test this independently of mesh size or compression.
    result.transient.snapshots[0].metrics["annotation"] = "Pulse µs — test " * 1024
    path = tmp_path / "pulse.etl"
    save_project(project, path, result)
    with zipfile.ZipFile(path) as archive:
        metadata_bytes = archive.getinfo("transient/metadata.json").file_size
        array_bytes = 0
        for name in archive.namelist():
            if name.startswith("transient/frame_"):
                with zipfile.ZipFile(io.BytesIO(archive.read(name))) as frame:
                    array_bytes += sum(item.file_size for item in frame.infolist())
    total_bytes = metadata_bytes + array_bytes

    def set_budget(size):
        if limit == "safety_cap":
            monkeypatch.setattr("etlayout.io.transient.MAX_HISTORY_BYTES", size)
        else:
            project.transient.max_history_bytes = size
            result.configuration = project.to_dict()
            result.configuration_hash = configuration_hash(project)
            result.physics_hash = physics_hash(project)

    set_budget(total_bytes)
    save_project(project, path, result)
    _, loaded = load_project(path)
    assert loaded.transient.rows == result.transient.rows
    assert loaded.transient.snapshots[0].metrics == result.transient.snapshots[0].metrics

    set_budget(total_bytes - 1)
    rejected = tmp_path / "over-budget.etl"
    with pytest.raises(SimulationError, match="byte budget"):
        save_project(project, rejected, result)
    assert not rejected.exists()


def test_archive_metadata_budget_is_checked_before_reading_payload(pulse, tmp_path, monkeypatch):
    project, result = pulse
    path = tmp_path / "pulse.etl"
    save_project(project, path, result)
    monkeypatch.setattr("etlayout.io.transient.MAX_HISTORY_BYTES", 64)
    read = zipfile.ZipFile.read

    def guarded_read(archive, name, *args, **kwargs):
        assert name != "transient/metadata.json", "oversized metadata must not be expanded"
        return read(archive, name, *args, **kwargs)

    monkeypatch.setattr(zipfile.ZipFile, "read", guarded_read)
    with pytest.raises(SimulationError, match="metadata exceeds its byte budget"):
        load_project(path)


def test_incomplete_history_preserves_last_accepted_frame_without_claiming_completion(pulse, tmp_path):
    project, result = pulse
    result.transient.snapshots = result.transient.snapshots[:-1]
    result.transient.times = result.transient.times[:-1]
    stop = result.transient.times[-1]
    result.transient.rows = [row for row in result.transient.rows if row["time_s"] <= stop]
    result.fields = result.transient.snapshots[-1].fields
    result.element_fields = result.transient.snapshots[-1].element_fields
    result.interface_fields = result.transient.snapshots[-1].interface_fields
    result.analysis = result.transient.snapshots[-1].analysis
    result.converged = result.transient.completed = False
    result.metrics["time_s"] = stop
    path = tmp_path / "partial.etl"
    save_project(project, path, result)
    _, loaded = load_project(path)
    assert not loaded.converged and not loaded.transient.completed
    assert loaded.transient.times[-1] == pytest.approx(.6)
    assert loaded.transient.snapshots[-1].converged


def test_disabled_transient_defaults_preserve_legacy_physics_and_archive(pulse, tmp_path):
    project, transient = pulse
    project.transient.enabled = False
    project.solver.mode = "electrical"
    # The old steady archive lacks the newly introduced settings altogether.
    legacy = deepcopy(project.to_dict())
    legacy.pop("transient")
    assert physics_hash(project) == physics_hash(legacy)
    project.transient.time_step = .000123
    assert physics_hash(project) == physics_hash(legacy)
    result = solve(project, transient.mesh)
    source, target = tmp_path / "new.etl", tmp_path / "legacy.etl"
    save_project(project, source, result)
    def remove_new_settings(files):
        metadata = json.loads(files["project.json"])
        metadata["project"].pop("transient")
        files["project.json"] = json.dumps(metadata)
        metadata = json.loads(files["results/metadata.json"])
        metadata["configuration"].pop("transient")
        metadata["configuration_hash"] = configuration_hash(metadata["configuration"])
        files["results/metadata.json"] = json.dumps(metadata)
    rewrite_archive(source, target, remove_new_settings)
    reopened, saved = load_project(target)
    assert not reopened.transient.enabled and saved.transient is None
    assert saved.physics_hash == physics_hash(legacy)


def test_full_export_has_si_history_units_complete_archive_plots_and_time_label(pulse, tmp_path):
    _, result = pulse
    export_results(result, tmp_path, plots=True)
    manifest = json.loads((tmp_path / "manifest.json").read_text())
    assert manifest["transient"]["field_map_time_s"] == .7
    assert {"transient_history.csv", "transient_history.json", "transient_history.png", "transient_history.svg", "simulation.etl"} <= set(manifest["shared_files"])
    with (tmp_path / "transient_history.csv").open(encoding="utf-8-sig", newline="") as stream:
        rows = list(csv.DictReader(stream))
    assert len(rows) == len(result.transient.rows)
    assert float(rows[-1]["time_s"]) == .7
    assert float(rows[-1]["temperature_max_K"]) == pytest.approx(303.5, abs=1e-6)
    assert float(rows[-1]["input_energy_J"]) == pytest.approx(result.metrics["transient_input_energy"])
    assert rows[0]["thermal_storage_rate_W"] == ""
    assert json.loads(rows[-1]["drives_json"])["heat_sources.heater.value"] == 10.
    assert rows[-1]["configuration_hash"] == result.configuration_hash
    assert (tmp_path / "transient_history.png").stat().st_size > 5000
    assert "Temperature (K)" in (tmp_path / "transient_history.svg").read_text()
    assert "transient fields at t = 0.7 s" in (tmp_path / "report.html").read_text()
    _, reopened = load_project(tmp_path / "simulation.etl")
    assert reopened.transient.rows == result.transient.rows
    export_field_plot(result.transient.snapshots[1], "metal", "temperature", tmp_path / "snapshot.svg")
    assert "t = 0.2 s" in (tmp_path / "snapshot.svg").read_text()


def test_selected_snapshot_export_discloses_incomplete_parent_run(pulse, tmp_path):
    _, result = pulse
    frame = result.transient.snapshots[1]
    assert frame.converged
    provenance = {"time_s": frame.metrics["time_s"], "transient_completed": False}
    export_results(frame, tmp_path, plots=True, field="temperature", provenance=provenance)
    data = json.loads((tmp_path / "results.json").read_text())
    assert data["converged"] and data["visualization"]["transient_completed"] is False
    assert "snapshot from incomplete transient run" in (tmp_path / "report.html").read_text()
    assert "Snapshot from incomplete transient run" in (tmp_path / "metal_temperature.svg").read_text()


def test_difference_export_records_both_snapshot_times_and_applied_drives(pulse, tmp_path):
    from etlayout.visualization.difference import make_field_difference
    from etlayout.visualization.timing import difference_time_label
    _, result = pulse
    first, second = result.transient.snapshots[1], result.transient.snapshots[-1]
    difference = make_field_difference(first, second, "metal", "temperature")
    metadata = difference.configuration["visualization"]
    assert metadata["current_time_s"] == .2 and metadata["comparison_time_s"] == .7
    assert [source["time_s"] for source in metadata["sources"]] == [.2, .7]
    assert metadata["sources"][0]["applied_drives"] == first.metrics["applied_drives"]
    assert metadata["sources"][1]["drive_time_convention"] == "left_limit_at_step_end"
    assert difference_time_label(metadata) == "Current t = 0.2 s; comparison t = 0.7 s"
    export_results(difference, tmp_path, plots=True, field="temperature")
    assert "Current t = 0.2 s; comparison t = 0.7 s" in (tmp_path / "report.html").read_text()
    assert "Current t = 0.2 s; comparison t = 0.7 s" in (tmp_path / "metal_temperature.svg").read_text()


def test_full_transient_archive_requires_history_but_standalone_snapshot_is_valid(pulse, tmp_path):
    project, result = pulse
    source, target = tmp_path / "full.etl", tmp_path / "missing-history.etl"
    save_project(project, source, result)
    def remove_history(files):
        for name in list(files):
            if name.startswith("transient/"):
                files.pop(name)
    rewrite_archive(source, target, remove_history)
    with pytest.raises(SimulationError, match="missing its time-history payload"):
        load_project(target)
    snapshot_path = tmp_path / "snapshot.etl"
    save_project(project, snapshot_path, result.transient.snapshots[1])
    _, frame = load_project(snapshot_path)
    assert frame.transient is None and frame.metrics["time_s"] == .2


@pytest.mark.parametrize("values,unit,expected", [([0, .0004], "s", (1e-6, "µs")),
    ([0, .02], "s", (.001, "ms")), ([0, 3.], "s", (1., "s")),
    ([0, .012], "W", (.001, "mW")), ([0, 2e-6], "J", (1e-6, "µJ")),
    ([None, np.nan, 0], "J", (1., "J")), ([-.003, .001], "W", (.001, "mW"))])
def test_transient_figure_axes_choose_explicit_engineering_units(values, unit, expected):
    from etlayout.io.transient_export import _axis_scale
    assert _axis_scale(values, unit) == expected
