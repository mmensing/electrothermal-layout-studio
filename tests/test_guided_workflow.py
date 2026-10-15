# SPDX-License-Identifier: MPL-2.0
"""Integrated user-edit regressions for the shared guided PySide6 workspace.

These tests use real geometry preparation and numerical results. Dialog answers
are deliberate QTest button presses; no job or solver completion is mocked.
"""
from __future__ import annotations

import hashlib
import os
from copy import deepcopy
from dataclasses import asdict
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import numpy as np
import pytest
from gui_test_helpers import _answer_dialog, _click, _fill, _idle
from gui_test_helpers import guided_window as guided_window
from PySide6.QtCore import QPointF, Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QPushButton
from shapely.geometry import MultiPolygon, box

from etlayout.examples import make_example
from etlayout.gui.viewer import geometry_region
from etlayout.io.project import (
    configuration_hash,
    load_project,
    load_run_history,
    physics_hash,
    project_from_dict,
    save_project,
)
from etlayout.models import RegionSpec, TransientDrive, Waveform
from etlayout.simulation.runner import run_project


@pytest.mark.parametrize("answer,changed,target", [("Apply", True, "conditions"),
                                                  ("Discard", False, "conditions"),
                                                  ("Stay", False, "layers")])
def test_navigation_resolves_real_inspector_drafts(guided_window, qtbot, answer, changed, target):
    window = guided_window
    guided = window.guided
    _click(window, guided.stage_buttons["layers"])
    old_thickness = window.project.layers[0].thickness
    old_revision = guided.document.revision
    _fill(window, "thickness", "3.5")
    assert guided.editor.has_changes()
    assert window.project.layers[0].thickness == old_thickness
    _answer_dialog(answer)
    _click(window, guided.stage_buttons["conditions"])
    _idle(window, qtbot)
    assert guided.stage == target
    assert window.project.layers[0].thickness == (3.5e-6 if changed else old_thickness)
    assert guided.document.revision == old_revision + int(changed)
    if answer == "Stay":
        assert guided.editor.has_changes()
        _click(window, guided.editor.cancel_button)
        assert window.project.layers[0].thickness == old_thickness


def test_clicking_current_step_preserves_dirty_inspector_and_selection(guided_window):
    window = guided_window
    guided = window.guided
    _click(window, guided.stage_buttons["connections"])
    guided.edit_model("terminals", "source")
    _fill(window, "name", "Unapplied source name")
    guided.begin_selection("editor")
    guided.preview_region("terminal_rect", geometry_region(box(5e-6, 1e-6, 10e-6, 9e-6)))
    editor, pending = guided.editor, deepcopy(guided.pending_region)
    original = window.project.to_dict()
    revision = guided.document.revision
    _click(window, guided.stage_buttons["connections"])
    assert guided.stage_buttons["connections"].isChecked()
    assert guided.editor is editor and editor.has_changes()
    assert editor.form.widgets["name"].text() == "Unapplied source name"
    assert guided.pending_region == pending and guided.selection_banner.isVisible()
    assert window.project.to_dict() == original and guided.document.revision == revision
    _click(window, editor.cancel_button)
    assert guided.pending_region is None and not guided.selection_banner.isVisible()
    assert window.project.to_dict() == original


@pytest.mark.parametrize("answer", ["Apply", "Discard", "Stay"])
def test_navigation_resolves_pending_contact_and_editor_as_one_draft(guided_window, qtbot, answer):
    window = guided_window
    guided = window.guided
    _click(window, guided.stage_buttons["connections"])
    guided.edit_model("terminals", "source")
    original = window.project.to_dict()
    revision = guided.document.revision
    _fill(window, "name", "Source with revised footprint")
    editor = guided.editor
    guided.begin_selection("editor")
    start = window.view.mapFromScene(QPointF(5, -1))
    end = window.view.mapFromScene(QPointF(10, -9))
    QTest.mousePress(window.view.viewport(), Qt.MouseButton.LeftButton, pos=start)
    QTest.mouseMove(window.view.viewport(), end)
    QTest.mouseRelease(window.view.viewport(), Qt.MouseButton.LeftButton, pos=end)
    pending = deepcopy(guided.pending_region)
    assert pending is not None and window.project.to_dict() == original
    _answer_dialog(answer)
    _click(window, guided.stage_buttons["run"])
    _idle(window, qtbot)
    assert window.project.roi is None
    if answer == "Stay":
        assert guided.stage == "connections" and guided.editor is editor
        assert guided.pending_region == pending and editor.has_changes()
        assert window.project.to_dict() == original and guided.document.revision == revision
        _click(window, editor.cancel_button)
        assert guided.pending_region is None
    else:
        assert guided.stage == "run" and guided.editor is None
        assert guided.pending_region is None and not guided.selection_banner.isVisible()
        if answer == "Apply":
            terminal = next(item for item in window.project.terminals if item.id == "source")
            assert terminal.name == "Source with revised footprint"
            assert terminal.geometry == pending
            assert guided.document.revision == revision + 1
        else:
            assert window.project.to_dict() == original and guided.document.revision == revision


def test_orphaned_contact_selection_cannot_change_simulation_domain(guided_window):
    window = guided_window
    guided = window.guided
    # Defensively reject an orphaned editor session (for example, after an
    # extension retires its inspector). Its purpose must never default to ROI.
    assert guided.editor is None
    original = window.project.to_dict()
    revision = guided.document.revision
    guided.begin_selection("editor")
    guided.preview_region("terminal_rect", geometry_region(box(5e-6, 1e-6, 10e-6, 9e-6)))
    _click(window, guided.use_selection)
    assert "target inspector is no longer open" in guided.next_reason.text()
    assert window.project.to_dict() == original and guided.document.revision == revision
    assert guided.pending_region is not None
    QTest.keyClick(window.view, Qt.Key.Key_Escape)
    assert guided.pending_region is None


def test_analysis_switches_preserve_all_dormant_configuration(guided_window, qtbot):
    window = guided_window
    def configure_dormant_settings(project):
        project.transient.stop_time = .037
        project.transient.time_step = 2e-4
        project.transient.output_interval = 1e-3
        project.transient.drives = [TransientDrive("terminals.drain.value",
                                                  Waveform("pulse", low=.01, high=.04, width=.003, period=.007))]
    window.guided.document.transact("Configure dormant pulse settings", configure_dormant_settings)
    _idle(window, qtbot)
    original = window.project.to_dict()
    for mode in ("thermal", "electrothermal", "thermal_transient", "electrothermal_transient", "electrical"):
        combo = window.guided.analysis
        combo.setCurrentIndex(combo.findData(mode))
        _idle(window, qtbot)
        assert combo.currentData() == mode
        snapshot = window.project.to_dict()
        snapshot["solver"]["mode"] = original["solver"]["mode"]
        snapshot["transient"]["enabled"] = original["transient"]["enabled"]
        assert snapshot == original
    assert window.guided.assessment.stages["conditions"].status


def test_pending_domain_escape_and_view_crop_do_not_change_physics(guided_window, qtbot):
    window = guided_window
    guided = window.guided
    original = physics_hash(window.project)
    guided.begin_selection("domain")
    start = window.view.mapFromScene(QPointF(20, -1))
    end = window.view.mapFromScene(QPointF(80, -9))
    QTest.mousePress(window.view.viewport(), Qt.MouseButton.LeftButton, pos=start)
    QTest.mouseMove(window.view.viewport(), end)
    QTest.mouseRelease(window.view.viewport(), Qt.MouseButton.LeftButton, pos=end)
    assert guided.pending_region is not None and physics_hash(window.project) == original
    QTest.keyClick(window.view, Qt.Key.Key_Escape)
    assert guided.pending_region is None and window.project.roi is None
    assert physics_hash(window.project) == original
    guided.region_kind.setCurrentIndex(guided.region_kind.findData("view_crop"))
    for key, value in {"x": "10", "y": "1", "width": "80", "height": "8"}.items():
        guided.bounds_fields[key].setFocus()
        guided.bounds_fields[key].selectAll()
        QTest.keyClicks(guided.bounds_fields[key], value)
    guided.numeric_domain()
    assert guided.pending_region is not None
    _click(window, guided.use_selection)
    _idle(window, qtbot)
    assert window.project.display_roi is not None
    assert window.project.roi is None and physics_hash(window.project) == original


def test_text_entry_and_function_keys_do_not_trigger_global_commands(guided_window):
    window = guided_window
    _click(window, window.guided.stage_buttons["layers"])
    field = window.guided.editor.form.widgets["name"]
    window.guided.editor.form.scroll.ensureWidgetVisible(field)
    field.setFocus()
    field.selectAll()
    original = physics_hash(window.project)
    undo_count = len(window.undo_history)
    QTest.keyClicks(field, "Long layer name f123")
    QTest.keyClick(field, Qt.Key.Key_Home)
    QTest.keyClick(field, Qt.Key.Key_Delete)
    for key in (Qt.Key.Key_F5, Qt.Key.Key_F6, Qt.Key.Key_F7):
        QTest.keyClick(field, key)
    QApplication.processEvents()
    assert field.text() == "ong layer name f123"
    assert window.job is None and window.prepare_job is None
    assert len(window.undo_history) == undo_count
    assert physics_hash(window.project) == original
    assert len(window.project.layers) == 1
    _click(window, window.guided.editor.cancel_button)


def test_multi_landing_port_applies_and_undoes_atomically(guided_window, qtbot):
    window = guided_window
    guided = window.guided
    original = window.project.to_dict()
    _click(window, guided.stage_buttons["connections"])
    guided.new_connection("port")
    _fill(window, "id", "distributed")
    _fill(window, "name", "Distributed input")
    _fill(window, "kind", "current")
    _fill(window, "value", "0.1")
    footprint = geometry_region(MultiPolygon([box(10e-6, 1e-6, 20e-6, 9e-6),
                                              box(40e-6, 1e-6, 50e-6, 9e-6)]))
    guided.begin_selection("editor")
    guided.preview_region("terminal_polygon", footprint)
    _click(window, guided.use_selection)
    assert window.project.to_dict() == original
    guided.landing_mode.setCurrentIndex(guided.landing_mode.findData("separate"))
    guided.feed_resistance.setText("0.02")
    old_revision = guided.document.revision
    _click(window, guided.editor.apply_button)
    _idle(window, qtbot)
    port, = window.project.power.ports
    assert port.id == "distributed" and port.kind == "current" and port.value == .1
    assert len(port.landings) == 2
    assert len(window.project.terminals) == len(original["terminals"]) + 2
    assert len({landing.terminal_id for landing in port.landings}) == 2
    assert all(landing.resistance == .02 for landing in port.landings)
    assert guided.document.revision == old_revision + 1
    after = window.project.to_dict()
    window.guided.actions["edit.undo"].trigger()
    _idle(window, qtbot)
    assert window.project.to_dict() == original
    window.guided.actions["edit.redo"].trigger()
    _idle(window, qtbot)
    assert window.project.to_dict() == after


def test_invalid_port_feed_has_no_partial_landings_or_undo_entry(guided_window):
    window = guided_window
    guided = window.guided
    _click(window, guided.stage_buttons["connections"])
    guided.new_connection("port", geometry_region(box(10e-6, 1e-6, 20e-6, 9e-6)))
    original = window.project.to_dict()
    undo_count = len(window.undo_history)
    guided.feed_resistance.setText("-1")
    _click(window, guided.editor.apply_button)
    assert "nonnegative" in guided.next_reason.text()
    assert window.project.to_dict() == original and len(window.undo_history) == undo_count
    assert guided.editor.has_changes()
    _click(window, guided.editor.cancel_button)


def test_passive_probe_is_a_separate_non_equipotential_object(guided_window, qtbot):
    window = guided_window
    guided = window.guided
    _click(window, guided.stage_buttons["connections"])
    guided.new_connection("probe", geometry_region(box(49e-6, 4e-6, 51e-6, 6e-6)))
    _fill(window, "id", "monitor")
    _click(window, guided.editor.apply_button)
    _idle(window, qtbot)
    probe = next(terminal for terminal in window.project.terminals if terminal.id == "monitor")
    assert probe.kind == "probe" and not window.project.power.ports
    baseline = deepcopy(window.project)
    baseline.terminals = [terminal for terminal in baseline.terminals if terminal.id != "monitor"]
    result, without = run_project(window.project), run_project(baseline)
    assert result.converged and without.converged
    assert result.terminals["drain"]["current"] == pytest.approx(without.terminals["drain"]["current"], rel=1e-8)


def test_saved_historical_result_preserves_working_document_and_export_identity(guided_window, qtbot, tmp_path):
    window = guided_window
    result = run_project(window.project)
    assert result.converged
    window.show_result(result)
    _click(window, window.guided.stage_buttons["layers"])
    _fill(window, "thickness", "3.5")
    _click(window, window.guided.editor.apply_button)
    _idle(window, qtbot)
    working = window.project.to_dict()
    assert physics_hash(working) != physics_hash(result.configuration)
    assert result in window.run_history
    window.guided.open_drawer(3)
    window.history_panel.list.setCurrentRow(0)
    QTest.keyClick(window.history_panel.list, Qt.Key.Key_Return)
    _idle(window, qtbot)
    assert window.inspection_context is not None
    assert window.guided.document.project.to_dict() == working
    assert window.result.configuration_hash == result.configuration_hash
    assert configuration_hash(window.project) == configuration_hash(result.configuration)
    assert window.view_source_result().configuration_hash == result.configuration_hash
    assert "Out of date" in window.guided.result_evidence.text()
    window.guided.return_to_setup()
    _idle(window, qtbot)
    assert window.project.to_dict() == working and window.inspection_context is None
    window.path = tmp_path / "retained.etlayout"
    assert window.save()
    reopened, current = load_project(window.path)
    history = load_run_history(window.path)
    # Archive extraction relocates the embedded source; its byte identity and
    # complete scientific configuration, rather than machine path, must agree.
    assert configuration_hash(reopened) == configuration_hash(working)
    assert current is None
    assert any(run.configuration_hash == result.configuration_hash for run in history)


def test_results_have_one_layer_context_and_accessible_selector_mnemonics(guided_window, qtbot):
    window = guided_window
    result = run_project(window.project)
    assert result.converged
    window.show_result(result)
    guided = window.guided
    assert guided.stage == "results"
    assert not guided.active_layer.isVisible()
    assert window.field_layer.isVisible()
    window.display_tabs.setCurrentIndex(0)
    window.activateWindow()
    qtbot.wait(50)
    for key, control in ((Qt.Key.Key_L, window.field_layer),
                         (Qt.Key.Key_R, window.field_region),
                         (Qt.Key.Key_C, window.colormap_combo)):
        window.view.setFocus()
        QTest.keyClick(window, key, Qt.KeyboardModifier.AltModifier)
        qtbot.wait(50)
        assert control.hasFocus(), control.accessibleName()
    _click(window, guided.stage_buttons["layout"])
    assert guided.active_layer.isVisible()


def test_reporting_revision_labels_reusable_fields_but_boundary_edit_is_out_of_date(guided_window, qtbot):
    from etlayout.power.analysis import _validate_result_context

    window = guided_window
    guided = window.guided
    result = run_project(window.project)
    assert result.converged
    recorded = deepcopy(result)
    window.show_result(result)
    assert "Current" in guided.result_evidence.text()
    guided.document.transact("Change reporting reference only",
                             lambda project: setattr(project.power, "thermal_reference_temperature", 310.))
    _idle(window, qtbot)
    assert physics_hash(window.project) != physics_hash(result.configuration)
    _validate_result_context(window.project, result)

    def inspect_retained_run():
        guided.open_drawer(3)
        window.history_panel.list.setCurrentRow(0)
        QTest.keyClick(window.history_panel.list, Qt.Key.Key_Return)
        _idle(window, qtbot)
        assert window.inspection_context is not None
        assert window.result is result
        assert window.view_source_result().configuration_hash == recorded.configuration_hash

    inspect_retained_run()
    assert "Fields reusable · reporting/study setup changed" in guided.result_evidence.text()
    assert "Out of date" not in guided.result_evidence.text()
    assert guided.stage_status["results"].text() == "Historical · fields reusable"
    assert guided.document.project.power.thermal_reference_temperature == 310.
    assert window.project.power.thermal_reference_temperature == recorded.configuration["power"]["thermal_reference_temperature"]

    guided.return_to_setup()
    _idle(window, qtbot)
    _click(window, guided.stage_buttons["connections"])
    guided.edit_model("terminals", "drain")
    old_value = next(terminal.value for terminal in window.project.terminals if terminal.id == "drain")
    _fill(window, "value", str(old_value + .125))
    _click(window, guided.editor.apply_button)
    _idle(window, qtbot)
    inspect_retained_run()
    assert "Out of date for working setup" in guided.result_evidence.text()
    assert "Fields reusable" not in guided.result_evidence.text()
    assert guided.stage_status["results"].text() == "Historical · out of date"
    assert result.configuration_hash == recorded.configuration_hash
    assert result.configuration == recorded.configuration
    assert result.metrics == recorded.metrics and result.analysis == recorded.analysis
    for layer, fields in recorded.fields.items():
        for name, values in fields.items():
            np.testing.assert_array_equal(result.fields[layer][name], values)


def test_saved_state_readiness_requires_validated_matching_available_source(guided_window, qtbot, tmp_path):
    window = guided_window
    guided = window.guided
    project = make_example("E17", tmp_path / "pulse-inputs")
    source = run_project(project)
    warm = run_project(project, initial_result=source)
    assert source.converged and warm.converged
    archive = tmp_path / "warm.etlayout"
    save_project(project_from_dict(warm.configuration), archive, warm)
    loaded_project, loaded_result = load_project(archive)
    window.set_project(loaded_project, loaded_result, archive)
    _idle(window, qtbot)

    def needs_source():
        return any(issue.key == "setup.initial_state" for issue in guided.assessment.blockers)

    # Merely retaining a digest does not prove its source can be replayed.
    assert window.project.transient.initial_state_digest
    assert needs_source()
    _click(window, guided.stage_buttons["conditions"])
    resolve = next(control for control in guided.page.findChildren(QPushButton)
                   if control.text() == "Resolve and validate saved initial state")
    _click(window, resolve)
    _idle(window, qtbot)
    assert not needs_source()
    assert guided.initial_state_evidence == guided.initial_state_key()

    # Run through the real worker: completion adds a new result identity and
    # must carry validated evidence forward to that newly retained result.
    guided.run()
    _idle(window, qtbot, timeout=60000)
    assert window.result.converged
    assert window.result.configuration_hash == warm.configuration_hash
    assert not needs_source()
    assert guided.initial_state_evidence == guided.initial_state_key()

    guided.document.transact("Bind an unavailable initial-state digest",
                             lambda candidate: setattr(candidate.transient, "initial_state_digest", "0" * 64))
    _idle(window, qtbot)
    assert needs_source()
    with pytest.raises(ValueError, match="saved initial thermal field"):
        window.initial_state_argument()
    assert guided.initial_state_evidence != guided.initial_state_key()

    # Reopening the genuine archive and validating it restores readiness, but
    # removing all retained source results invalidates that evidence again.
    window.set_project(loaded_project, loaded_result, archive)
    _idle(window, qtbot)
    assert window.initial_state_argument()["initial_result"] is not None
    guided.refresh()
    assert not needs_source()
    window.run_history.clear()
    window.result = None
    guided.refresh()
    assert needs_source()
    with pytest.raises(ValueError, match="saved initial thermal field"):
        window.initial_state_argument()


def test_failed_support_keeps_draft_and_routes_to_existing_object(guided_window, qtbot):
    window = guided_window
    guided = window.guided
    _click(window, guided.stage_buttons["connections"])
    guided.edit_model("terminals", "source")
    original = window.project.to_dict()
    guided.editor.set_region(RegionSpec())
    _click(window, guided.editor.apply_button)
    assert "nonempty geometric support" in guided.next_reason.text()
    assert window.project.to_dict() == original
    assert guided.editor_key == "terminals" and guided.editor_id == "source"
    assert guided.editor.has_changes()
    _click(window, guided.editor.cancel_button)
    _idle(window, qtbot)
    assert asdict(window.project.terminals[0]) == original["terminals"][0]


def test_run_cannot_silently_consume_unapplied_inspector_values(guided_window):
    window = guided_window
    _click(window, window.guided.stage_buttons["layers"])
    original = physics_hash(window.project)
    _fill(window, "thickness", "4")
    _answer_dialog("Stay")
    window.guided.run()
    assert window.job is None and physics_hash(window.project) == original
    assert window.guided.editor.has_changes()
    _click(window, window.guided.editor.cancel_button)


def test_issue_fix_opens_the_affected_material_field_and_clears_blocker(guided_window, qtbot):
    window = guided_window
    guided = window.guided
    guided.document.transact("Load an unresolved material assignment",
                             lambda project: setattr(project.layers[0], "material_id", "missing_material"))
    _idle(window, qtbot)
    identifier = window.project.layers[0].id
    matching = [issue for issue in guided.assessment.blockers if issue.object_id == identifier
                and "material" in issue.message.lower()]
    assert matching and matching[0].stage == "layers"
    guided.open_drawer(0)
    row = next(index for index in range(guided.issue_table.rowCount())
               if guided.issue_table.item(index, 1).text() == matching[0].message)
    fix = next(control for control in guided.issue_table.cellWidget(row, 3).findChildren(QPushButton)
               if control.text().startswith("Fix"))
    _click(window, fix)
    assert guided.stage == "layers" and guided.editor_id == identifier
    _fill(window, "material_id", "copper")
    _click(window, guided.editor.apply_button)
    _idle(window, qtbot)
    assert window.project.layers[0].material_id == "copper"
    assert not any(issue.object_id == identifier and "material" in issue.message.lower()
                   for issue in guided.assessment.blockers)


def test_duplicate_port_id_is_rejected_without_orphaned_footprints(guided_window, qtbot):
    window = guided_window
    guided = window.guided
    _click(window, guided.stage_buttons["connections"])
    support = geometry_region(box(10e-6, 1e-6, 20e-6, 9e-6))
    guided.new_connection("port", support)
    _fill(window, "id", "external")
    _click(window, guided.editor.apply_button)
    _idle(window, qtbot)
    original = window.project.to_dict()
    undo_count = len(window.undo_history)
    guided.new_connection("port", support)
    _fill(window, "id", "external")
    _click(window, guided.editor.apply_button)
    assert "already used" in guided.next_reason.text()
    assert window.project.to_dict() == original and len(window.undo_history) == undo_count
    _click(window, guided.editor.cancel_button)


def test_repeated_mirrored_hierarchy_browsing_requires_explicit_scope(guided_window, qtbot):
    import klayout.db as db

    window = guided_window
    source = window.project.layout.path
    layout = db.Layout()
    layout.read(source)
    child = layout.cell(window.project.layout.cell)
    assembly = layout.create_cell("ASSEMBLY")
    assembly.insert(db.CellInstArray(child.cell_index(), db.Trans(0, False, 0, 0)))
    assembly.insert(db.CellInstArray(child.cell_index(), db.Trans(0, True, 200000, 20000)))
    layout.write(source)

    def relink(project):
        project.layout.cell = "ASSEMBLY"
        project.layout.sha256 = hashlib.sha256(Path(source).read_bytes()).hexdigest()

    window.guided.document.transact("Choose repeated-instance test input", relink)
    _idle(window, qtbot)
    original = configuration_hash(window.project)
    undo_count = len(window.undo_history)
    guided = window.guided
    guided.show_page(window.design_dock.widget(), "Model browser")
    tree = window.hierarchy
    parent = next(tree.topLevelItem(index) for index in range(tree.topLevelItemCount())
                  if tree.topLevelItem(index).data(0, Qt.ItemDataRole.UserRole)[0] == "ASSEMBLY")
    tree.setCurrentItem(parent)
    tree.setFocus()
    QTest.keyClick(tree, Qt.Key.Key_Right)
    _idle(window, qtbot)
    assert parent.childCount() == 2
    mirrored = parent.child(1)
    tree.scrollToItem(mirrored)
    QTest.mouseDClick(tree.viewport(), Qt.MouseButton.LeftButton, pos=tree.visualItemRect(mirrored).center())
    _idle(window, qtbot)
    assert configuration_hash(window.project) == original
    assert len(window.undo_history) == undo_count
    assert window.project.layout.instance_path == []
    tree.setCurrentItem(mirrored)
    expected_path = mirrored.data(0, Qt.ItemDataRole.UserRole)[1]
    _answer_dialog("Yes")
    guided.apply_scope()
    _idle(window, qtbot)
    assert window.project.layout.instance_path == expected_path
    assert window.project.layout.instance_path
    assert len(window.undo_history) == undo_count + 1
