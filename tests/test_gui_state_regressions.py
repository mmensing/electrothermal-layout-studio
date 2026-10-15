# SPDX-License-Identifier: MPL-2.0
"""Audit regressions: run provenance, editable state, recovery and asynchronous UI."""
from __future__ import annotations

import os
from copy import deepcopy

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import QApplication, QInputDialog

from etlayout.examples import make_example
from etlayout.gui.export import export_html_report
from etlayout.gui.main_window import MainWindow
from etlayout.io.project import load_project, load_run_history, physics_hash
from etlayout.models import LayoutSource, PhysicalLayer, Project, RegionSpec
from etlayout.simulation.runner import run_project


@pytest.fixture
def working(qtbot, tmp_path):
    project = make_example("E1", tmp_path / "examples")
    result = run_project(project)
    window = MainWindow(project, result, database_path=tmp_path / "materials.sqlite3", recover=False)
    window.recovery_path = tmp_path / "recovery.etlayout"
    window.errors = []
    window.error = lambda title, message: window.errors.append((title, str(message)))
    window.show()
    def finish(widget):
        if widget.job:
            widget.cancel_job()
        if widget.inspection_context:
            widget.inspection_context["dirty"] = False
        widget.dirty = False
    qtbot.addWidget(window, before_close_func=finish)
    qtbot.waitUntil(lambda: window.prepare_job is None, timeout=30000)
    assert window.geometry is not None
    yield window


def test_presentation_edits_keep_results_save_reopen_and_recover(working, qtbot, tmp_path):
    result = working.result
    original_physics = physics_hash(working.project)
    working.layer_tree.topLevelItem(0).setCheckState(0, Qt.CheckState.Unchecked)
    def accept_notes():
        dialog = QApplication.activeModalWidget()
        assert isinstance(dialog, QInputDialog)
        dialog.setTextValue("Process notes revised after solve")
        dialog.accept()
    QTimer.singleShot(30, accept_notes)
    working.edit_notes()
    working.on_region_drawn("display_rect", RegionSpec(polygons=[{"exterior": [[0, 0], [40e-6, 0], [40e-6, 10e-6], [0, 10e-6]]}]))
    assert working.project.display_roi is None
    working.guided.commit_selection()
    assert working.result is result and working.mesh is result.mesh
    assert physics_hash(working.project) == original_physics
    working.autosave()
    assert working.recovery_path.is_file()
    assert working.recovery_status.text() == "Recovery saved"
    working.path = tmp_path / "saved.etlayout"
    assert working.save()
    project, loaded = load_project(working.path)
    assert loaded is not None
    assert not project.layers[0].visible
    assert project.notes == "Process notes revised after solve"
    assert project.display_roi is not None
    assert len(load_run_history(working.path)) == 1


def test_inspection_is_readonly_and_export_uses_recorded_design(working, qtbot, tmp_path):
    base_project, base_result = working.project, working.result
    other = make_example("E2", tmp_path / "second")
    other.layers[0].id = "other_metal"
    other.layers[0].name = "Recorded bent metal"
    for item in [*other.terminals, *other.thermal_boundaries]:
        item.layer_id = "other_metal"
    result = run_project(other)
    working.remember_result(result)
    working.inspect_result(result)
    assert working.inspection_context["project"] is base_project
    assert working.project.name == other.name
    assert working.field_layer.currentData() == "other_metal"
    assert working.field_combo.count() > 1
    assert not working.config_dock.isEnabled()
    assert not working.run_action.isEnabled()
    working.relink_layout()  # Guard must return before even opening a file picker.
    assert working.result is result and working.project.name == other.name
    working.run_sweep_dialog()
    assert working.project.sweeps == other.sweeps
    qtbot.waitUntil(lambda: working.prepare_job is None, timeout=30000)
    assert "other_metal" in working.geometry.layers
    report = tmp_path / "inspected.html"
    export_html_report(working.project, working.result, working.view, report, working.legend.text())
    assert "<h1>E2_bent_taper</h1>" in report.read_text(encoding="utf-8")
    assert "Recorded bent metal" in report.read_text(encoding="utf-8")
    working.return_to_project()
    assert working.project is base_project and working.result is base_result
    assert working.config_dock.isEnabled() and working.run_action.isEnabled()
    working.path = tmp_path / "history.etlayout"
    assert working.save()
    loaded_runs = load_run_history(working.path)
    assert {run.configuration["name"] for run in loaded_runs} == {base_project.name, other.name}


def test_active_run_blocks_import_and_discards_stale_attachment(working, qtbot, tmp_path):
    working.start_job("solve")
    assert not working.guided.actions["file.new"].isEnabled()
    assert not working.guided.actions["file.import"].isEnabled()
    assert working.guided.actions["simulation.cancel"].isEnabled()
    current_layout = working.project.layout.path
    working.import_layout("this-must-never-be-opened.gds")
    assert working.project.layout.path == current_layout
    # Even a programmatic edit bypassing disabled controls cannot attach a stale result.
    working.project.terminals[-1].value *= 2
    working.result = None
    qtbot.waitUntil(lambda: working.job is None, timeout=30000)
    assert working.result is None
    assert len(working.run_history) >= 2
    assert "not attached" in working.console.toPlainText()
    assert working.dirty
    assert not working.errors


def test_open_project_resets_convergence_connectivity_and_diagnostics(working, qtbot, tmp_path):
    working.start_job("validate")
    qtbot.waitUntil(lambda: working.job is None, timeout=30000)
    assert working.net_report is not None and working.net_tree.topLevelItemCount()
    working.set_project(make_example("E2", tmp_path / "unsolved"))
    assert working.result is None and working.net_report is None
    assert working.net_tree.topLevelItemCount() == 0
    assert working.metrics_table.rowCount() == 0
    assert "Converged" not in working.result_status.text()
    assert working.key_metrics.text() == ""


def test_autosave_failure_is_visible_and_can_recover(working, tmp_path):
    working.dirty = True
    working.recovery_path = tmp_path  # A directory cannot be replaced by the archive file.
    working.autosave()
    assert "Recovery failed" in working.recovery_status.text()
    assert "Automatic recovery could not be saved" in working.console.toPlainText()
    working.recovery_path = tmp_path / "working-recovery.etlayout"
    working.autosave()
    assert working.recovery_status.text() == "Recovery saved"
    assert load_project(working.recovery_path)[1] is not None


def test_background_preparation_keeps_event_loop_live_and_pages_instances(working, qtbot, tmp_path):
    import klayout.db as db
    layout = db.Layout()
    layout.dbu = .001
    child = layout.create_cell("UNIT")
    child.shapes(layout.layer(1, 0)).insert(db.Box(0, 0, 1000, 1000))
    top = layout.create_cell("ARRAY")
    top.insert(db.CellInstArray(child.cell_index(), db.Trans(), db.Vector(2000, 0), db.Vector(0, 2000), 240, 1))
    path = tmp_path / "array.gds"
    layout.write(str(path))
    project = Project(layout=LayoutSource(str(path), "ARRAY"), materials=deepcopy(working.project.materials),
                      layers=[PhysicalLayer("metal", "Metal", "copper", geometry=RegionSpec(sources=["1/0"]))])
    ticks = []
    timer = QTimer(working)
    timer.timeout.connect(lambda: ticks.append(1))
    timer.start(10)
    working.set_project(project)
    assert working.prepare_job is not None
    qtbot.waitUntil(lambda: working.prepare_job is None, timeout=30000)
    timer.stop()
    assert len(ticks) > 5
    root = next(working.hierarchy.topLevelItem(i) for i in range(working.hierarchy.topLevelItemCount())
                if working.hierarchy.topLevelItem(i).text(0) == "ARRAY")
    root.setExpanded(True)
    qtbot.waitUntil(lambda: not working.hierarchy_jobs, timeout=30000)
    assert root.childCount() == 101
    more = root.child(100)
    assert "100 / 240" in more.text(0)
    working.select_hierarchy(more, 0)
    qtbot.waitUntil(lambda: not working.hierarchy_jobs, timeout=30000)
    assert root.childCount() == 201
    working.select_hierarchy(root.child(200), 0)
    qtbot.waitUntil(lambda: not working.hierarchy_jobs, timeout=30000)
    assert root.childCount() == 240
    assert root.child(239).data(0, Qt.ItemDataRole.UserRole)[1] == [239]


def test_switching_field_clears_locked_range_before_render(working):
    import numpy as np
    result = working.result
    layer = working.project.layers[0].id
    result.fields[layer]["temperature"] = np.full(len(result.mesh.points), 350.)
    result.element_fields[layer]["temperature"] = np.full((len(result.mesh.triangles), 3), 350.)
    working.populate_fields()
    working.field_combo.setCurrentIndex(working.field_combo.findData("potential"))
    working.legend_scale.minimum.setText("0")
    working.legend_scale.maximum.setText("1")
    working.legend_scale.lock.setChecked(True)
    working.field_combo.setCurrentIndex(working.field_combo.findData("temperature"))
    assert not working.legend_scale.lock.isChecked()
    assert working.legend_scale.details["low"] == pytest.approx(350.)
    assert working.legend_scale.details["unit"] == "K"


def test_threshold_annotations_refresh_without_mutating_run_history(working, tmp_path):
    result = working.result
    original_diagnostics = list(result.diagnostics)
    working.before_edit()
    working.project.thresholds["total_current"] = 1e-12
    working.changed()
    assert working.result is result
    assert result.diagnostics == original_diagnostics
    messages = [working.diagnostics.topLevelItem(i).text(2) for i in range(working.diagnostics.topLevelItemCount())]
    assert any("Engineering threshold exceeded: total_current" in message for message in messages)
    report = tmp_path / "threshold-report.html"
    export_html_report(working.project, result, working.view, report, working.legend.text())
    assert "Engineering threshold exceeded: total_current" in report.read_text(encoding="utf-8")


def test_accepted_close_disposes_plot_scenes_menus_and_repeated_cleanup(working):
    from PySide6.QtCore import QCoreApplication, QEvent
    from shiboken6 import isValid
    working.on_line_drawn("profile", [[1e-6, 5e-6], [99e-6, 5e-6]])
    working.on_line_drawn("cross_section", [[0, 5e-6], [100e-6, 5e-6]])
    plots = [working.profiles.plot, working.cross_section.plot, working.study_panel.plot]
    menus = [plot.plotItem.ctrlMenu for plot in plots]
    working.dirty = False
    assert working.close()
    assert all(plot.plotItem is None for plot in plots)
    # A second close/cleanup must not dereference a scene that is already gone.
    working.dispose_plots()
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    assert all(not isValid(plot) for plot in plots)
    working.dispose_plots()
    # The deliberately retained Python menu wrappers must not retain Qt parent
    # ownership of scene/proxy widgets; explicit PlotItem cleanup detaches them.
    assert all(not isValid(menu) or menu.parent() is None for menu in menus)


def test_run_configuration_diff_identifies_layout_content_not_location():
    from etlayout.gui.workspace import configuration_changes

    first = {"layout": {"path": "original.gds", "sha256": "content-a"}}
    relocated = {"layout": {"path": "embedded/layout.gds", "sha256": "content-a"}}
    different = {"layout": {"path": "embedded/layout.gds", "sha256": "content-b"}}
    assert configuration_changes(first, relocated) == []
    assert configuration_changes(first, different) == [
        ("layout.sha256", "content-a", "content-b")
    ]


def test_geometry_workers_reject_replaced_same_cell_source(tmp_path):
    import queue

    import klayout.db as db

    from etlayout.gui.jobs import _worker
    from etlayout.io.project import file_hash, project_from_dict

    original = make_example("E1", tmp_path / "source")
    result = run_project(original)
    recorded = project_from_dict(result.configuration)
    layout = db.Layout()
    layout.read(recorded.layout.path)
    layout.cell(recorded.layout.cell).shapes(layout.layer(1, 0)).insert(
        db.DBox(0, 0, 200, 20).to_itype(layout.dbu)
    )
    layout.write(recorded.layout.path)
    assert file_hash(recorded.layout.path) != recorded.layout.sha256
    for operation in ("prepare", "mesh", "validate", "hierarchy"):
        messages = queue.Queue()
        argument = {"cell": recorded.layout.cell} if operation == "hierarchy" else None
        _worker(messages, deepcopy(recorded), operation, argument)
        output = list(messages.queue)
        assert [kind for kind, _ in output] == ["error"], operation
        assert "Layout content changed" in output[0][1][0]

    # Import deliberately selects new source contents; a new draft with no
    # retained source identity may also prepare that file normally.
    messages = queue.Queue()
    _worker(messages, deepcopy(recorded), "import", recorded.layout.path)
    kind, metadata = messages.get_nowait()
    assert kind == "result"
    assert metadata["source_sha256"] == file_hash(recorded.layout.path)
    draft = deepcopy(recorded)
    draft.layout.sha256 = ""
    messages = queue.Queue()
    _worker(messages, draft, "prepare", None)
    output = list(messages.queue)
    assert output[-1][0] == "result"
    assert output[-1][1]["document"]["source_sha256"] == file_hash(recorded.layout.path)
