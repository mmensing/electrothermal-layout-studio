# SPDX-License-Identifier: MPL-2.0
"""Observable selection/draft/history contracts through the actual desktop."""
from copy import deepcopy

import pytest
from gui_test_helpers import _answer_dialog, _click, _fill, _idle
from gui_test_helpers import guided_window as guided_window
from gui_test_helpers import solved_e1 as solved_e1
from PySide6.QtCore import QPointF, Qt, QTimer
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QDialogButtonBox, QPushButton, QTableWidgetItem
from shapely.geometry import box

from etlayout.geometry.prepare import geometry_region, resolve_region
from etlayout.gui.drafts import resolve_pending
from etlayout.gui.forms import RegionDialog
from etlayout.gui.selection import PendingSelection
from etlayout.io.project import load_project, physics_hash
from etlayout.workflow import WorkflowIssue


@pytest.mark.parametrize('decision,valid,expected,events', [
    ('apply', True, True, ['apply']), ('apply', False, False, ['apply']),
    ('discard', True, True, ['discard']), ('stay', True, False, [])])
def test_shared_draft_policy_has_one_explicit_decision(decision, valid, expected, events):
    calls = []
    def apply():
        calls.append('apply')
        return valid
    accepted = resolve_pending(changed=True, choose=lambda: decision, apply=apply,
                               discard=lambda: calls.append('discard'))
    assert accepted is expected and calls == events


@pytest.mark.parametrize('purpose', ['domain', 'view_crop', 'measurement', 'keepout', 'editor'])
@pytest.mark.parametrize('route', ['draw', 'numeric', 'existing', 'resize'])
def test_selection_intent_preview_and_commit_share_exact_geometry(guided_window, qtbot, purpose, route):
    window, guided = guided_window, guided_window.guided
    original_roi, original_hash = deepcopy(window.project.roi), physics_hash(window.project)
    if purpose == 'editor':
        guided.navigate('connections')
        guided.edit_model('terminals', 'source')
    else:
        guided.region_kind.setCurrentIndex(guided.region_kind.findData(purpose))
    requested = box(5e-6, 1e-6, 15e-6, 9e-6)
    if route == 'numeric' and purpose == 'editor':
        def enter_region():
            dialog = QApplication.activeModalWidget()
            assert isinstance(dialog, RegionDialog)
            dialog.full.setChecked(False)
            dialog.add_polygon()
            dialog.points.setRowCount(4)
            for row, (x, y) in enumerate(list(requested.exterior.coords)[:4]):
                dialog.points.setItem(row, 0, QTableWidgetItem(str(x * 1e6)))
                dialog.points.setItem(row, 1, QTableWidgetItem(str(y * 1e6)))
            QTest.mouseClick(dialog.findChild(QDialogButtonBox).button(QDialogButtonBox.StandardButton.Ok), Qt.MouseButton.LeftButton)
        QTimer.singleShot(0, enter_region)
        guided.numeric_editor_region()
    elif route == 'numeric':
        for key, value in {'x': '5', 'y': '1', 'width': '10', 'height': '8'}.items():
            guided.bounds_fields[key].setText(value)
        guided.numeric_domain()
    elif route == 'existing':
        guided.begin_selection(purpose)
        item = window.view.layer_items['metal'][0]
        item.setSelected(True)
        guided.selection_from_canvas()
    else:
        guided.begin_selection(purpose)
        start, end = window.view.mapFromScene(QPointF(5, -1)), window.view.mapFromScene(QPointF(15, -9))
        QTest.mousePress(window.view.viewport(), Qt.MouseButton.LeftButton, pos=start)
        QTest.mouseMove(window.view.viewport(), end)
        QTest.mouseRelease(window.view.viewport(), Qt.MouseButton.LeftButton, pos=end)
        if route == 'resize':
            assert window.view.pending_region_handles
            window.view._resize_pending_region(2, QPointF(18, -1))
            window.view._emit_pending_region()
    assert guided.pending_purpose == purpose
    assert guided.pending_region is not None
    preview = guided.selection.effective
    assert preview.equals(window.view._pending_geometry)
    assert resolve_region(guided.pending_region, {}, clip=False).equals(preview)
    assert guided.commit_selection()
    if purpose == 'editor':
        assert guided.editor.apply()
        committed = next(t for t in window.project.terminals if t.id == 'source').geometry
    else:
        committed = (window.project.roi if purpose == 'domain' else window.project.display_roi if purpose == 'view_crop'
                     else window.project.power.measurements[-1].selector.geometry if purpose == 'measurement'
                     else window.project.power.rules.keepouts[-1])
    actual = resolve_region(committed, {}, clip=False)
    assert actual.hausdorff_distance(preview) <= window.project.layout.dbu_um * 1e-6
    if purpose != 'domain':
        assert window.project.roi == original_roi
    if purpose == 'view_crop':
        assert physics_hash(window.project) == original_hash
    _idle(window, qtbot)


@pytest.mark.parametrize('bounds,valid', [((-20e-6, -10e-6, 120e-6, 20e-6), True),
    ((-5e-6, -2e-6, 105e-6, 12e-6), True), ((0, -4e-6, 120e-6, 16e-6), True),
    ((20e-6, 2e-6, 80e-6, 8e-6), False), ((10e-6, 0, 110e-6, 10e-6), False)])
def test_domain_expansion_reduction_translation_are_not_clipped(guided_window, qtbot, bounds, valid):
    window, guided = guided_window, guided_window.guided
    # A padded starting domain permits reduction/translation without silently
    # waiving the backend's separate cut-conductor acceptance requirements.
    initial = box(-10e-6, -5e-6, 110e-6, 15e-6)
    guided.preview_region('numeric', geometry_region(initial), purpose='domain')
    assert guided.commit_selection()
    _idle(window, qtbot)
    assert window.geometry.domain.equals(initial)
    requested = box(*bounds)
    guided.preview_region('numeric', geometry_region(requested), purpose='domain')
    assert guided.selection.effective.equals(requested)
    assert window.view._pending_geometry.bounds == pytest.approx(bounds)
    assert guided.commit_selection()
    _idle(window, qtbot)
    assert resolve_region(window.project.roi, {}, clip=False).equals(requested)
    if valid:
        assert window.geometry.domain.bounds == pytest.approx(bounds)
    else:
        # E1 uses end-edge terminals. An x crop that removes one requires actual
        # support correction, but must still commit precisely the previewed ROI.
        assert window.geometry is None and guided.last_error
        assert guided.last_error_stage == 'layout' and guided.drawer.currentIndex() == 0


def test_physical_clip_exposes_requested_and_effective_support(guided_window, qtbot):
    guided = guided_window.guided
    guided.navigate('connections')
    guided.edit_model('terminals', 'source')
    requested = box(-5e-6, -5e-6, 20e-6, 20e-6)
    guided.preview_region('numeric', geometry_region(requested), purpose='editor')
    assert guided.selection.requested.equals(requested)
    assert guided.selection.effective.area < requested.area
    assert guided_window.view.requested_region_item is not None
    assert 'Requested' in guided.selection_text.text() and 'effective' in guided.selection_text.text()
    assert guided.apply_draft()
    _idle(guided_window, qtbot)
    actual = resolve_region(next(t for t in guided_window.project.terminals if t.id == 'source').geometry, {}, clip=False)
    assert actual.equals(requested.intersection(guided_window.geometry.layers['metal']))


@pytest.mark.parametrize('transition', ['save', 'history'])
@pytest.mark.parametrize('answer', ['Apply', 'Discard', 'Stay'])
def test_draft_resolution_precedes_save_or_historical_context(guided_window, qtbot, tmp_path, transition, answer, solved_e1):
    window, guided = guided_window, guided_window.guided
    result = deepcopy(solved_e1[1])
    assert physics_hash(result.configuration) == physics_hash(window.project)
    window.show_result(result)
    _idle(window, qtbot)
    window.path = tmp_path / 'working.etl'
    guided.navigate('connections')
    guided.edit_model('terminals', 'source')
    editor = guided.editor
    _fill(window, 'name', 'Applied draft name')
    guided.begin_selection('editor')
    guided.preview_region('terminal_rect', geometry_region(box(5e-6, 1e-6, 10e-6, 9e-6)))
    context = (window.project, window.result, window.mesh, guided.stage, window.view.transform())
    original = window.project.to_dict()
    pending = deepcopy(guided.pending_region)
    _answer_dialog(answer)
    if transition == 'save':
        window.save()
    else:
        window.inspect_result(result)
    if answer == 'Stay':
        assert (window.project, window.result, window.mesh, guided.stage, window.view.transform()) == context
        assert window.inspection_context is None and guided.editor is editor and editor.has_changes()
        assert guided.pending_region == pending and window.project.to_dict() == original
        assert not window.path.exists()
        guided.discard_draft()
        return
    _idle(window, qtbot)
    if transition == 'history':
        assert window.inspection_context and window.model_locked()
        window.return_to_project()
        _idle(window, qtbot)
        assert guided.stage == 'connections'
    else:
        saved, _ = load_project(window.path)
        # Embedded layouts are extracted to a new path on load. Every other
        # model field and its physical identity must survive unchanged.
        saved.layout.path = window.project.layout.path
        assert saved.to_dict() == window.project.to_dict()
    terminal = next(t for t in window.project.terminals if t.id == 'source')
    assert terminal.name == ('Applied draft name' if answer == 'Apply' else original['terminals'][0]['name'])
    assert guided.pending_region is None
    if guided.editor:
        assert not guided.editor.has_changes()
    # A later Save/History transition cannot revive a discarded edit.
    assert guided.resolve_draft()


def test_background_geometry_failure_is_visible_and_opens_layout(guided_window, qtbot):
    window, guided = guided_window, guided_window.guided
    def relink(project):
        project.layout.path, project.layout.sha256 = 'missing-maintenance.gds', ''
    guided.document.transact('Broken layout path', relink)
    _idle(window, qtbot)
    assert window.geometry is None and 'Geometry preparation failed' in guided.last_error
    assert guided.drawer.currentIndex() == 0 and not guided.drawer.isHidden()
    messages = [guided.issue_table.item(row, 1).text() for row in range(guided.issue_table.rowCount())]
    row = next(index for index, text in enumerate(messages) if 'Layout is missing.' in text)
    assert 'Relink the source in the project editor.' in messages[row]
    assert all('missing-maintenance.gds' not in text for text in messages)
    assert 'missing-maintenance.gds' not in window.console.toPlainText()
    assert guided.last_error_stage == 'layout'
    action = next(control for control in guided.issue_table.cellWidget(row, 3).findChildren(QPushButton)
                  if control.text() == 'Open setup')
    assert action.isEnabled()
    guided.navigate('conditions')
    _click(window, action)
    assert guided.stage == 'layout'


def test_pending_geometry_policy_is_independent_of_qt():
    selection = PendingSelection()
    selection.begin('domain')
    expanded = box(-1, -1, 2, 2)
    selection.preview(geometry_region(expanded), {}, box(0, 0, 1, 1))
    assert selection.effective.equals(expanded) and selection.purpose == 'domain'


def test_stay_before_issue_fix_preserves_canvas_editor_and_selection(guided_window):
    window, guided = guided_window, guided_window.guided
    guided.navigate('connections')
    guided.edit_model('terminals', 'source')
    _fill(window, 'name', 'Unapplied')
    guided.begin_selection('editor')
    guided.preview_region('terminal_rect', geometry_region(box(5e-6, 1e-6, 10e-6, 9e-6)))
    editor, state, pending = guided.editor, window.capture_view_state(), deepcopy(guided.pending_region)
    _answer_dialog('Stay')
    guided.fix_issue(WorkflowIssue('layer', 'error', 'Review layer', object_id='metal',
                                 bounds=(0, 0, 100e-6, 10e-6), stage='layers'))
    assert guided.editor is editor and guided.pending_region == pending
    assert window.capture_view_state() == state and guided.stage == 'connections'
    assert guided.stage_buttons['connections'].isChecked()


def test_discard_clears_existing_geometry_selection_and_does_not_revive_it(guided_window):
    window, guided = guided_window, guided_window.guided
    guided.begin_selection('measurement')
    window.view.layer_items['metal'][0].setSelected(True)
    assert guided.pending_region is not None
    _answer_dialog('Discard')
    assert guided.resolve_draft()
    assert not window.view.selected_geometries() and guided.pending_region is None
    guided.begin_selection('measurement')
    assert not guided.commit_selection()
    assert not window.project.power.measurements


def test_invalid_editor_cannot_partially_commit_an_unrelated_region(guided_window):
    window, guided = guided_window, guided_window.guided
    guided.navigate('layers')
    _fill(window, 'thickness', '-1')
    guided.begin_selection('keepout')
    guided.preview_region('roi_rect', geometry_region(box(5e-6, 1e-6, 10e-6, 9e-6)))
    original = window.project.to_dict()
    assert not guided.apply_draft()
    assert window.project.to_dict() == original and guided.pending_region is not None
    assert guided.editor.has_changes()


@pytest.mark.parametrize('purpose', ['view_crop', 'measurement', 'keepout'])
def test_explicit_purpose_edit_keeps_requested_geometry_and_updates_existing_pick(guided_window, purpose):
    window, guided = guided_window, guided_window.guided
    guided.begin_selection('domain')
    requested = box(5e-6, 1e-6, 10e-6, 9e-6)
    guided.preview_region('roi_rect', geometry_region(requested))
    guided.region_kind.setCurrentIndex(guided.region_kind.findData(purpose))
    assert guided.pending_purpose == purpose and guided.selection.requested.equals(requested)
    window.view.layer_items['metal'][0].setSelected(True)
    assert guided.pending_purpose == purpose
    assert guided.commit_selection()
    assert window.project.roi is None
    assert (window.project.display_roi is not None if purpose == 'view_crop' else
            bool(window.project.power.measurements) if purpose == 'measurement' else bool(window.project.power.rules.keepouts))


@pytest.mark.parametrize('clear', ['clear_roi', 'clear_display_roi'])
def test_stay_before_scope_clear_preserves_complete_selection(guided_window, clear):
    window, guided = guided_window, guided_window.guided
    guided.begin_selection('domain')
    guided.preview_region('roi_rect', geometry_region(box(5e-6, 1e-6, 10e-6, 9e-6)))
    original, pending = window.project.to_dict(), deepcopy(guided.pending_region)
    _answer_dialog('Stay')
    getattr(window, clear)()
    assert window.project.to_dict() == original and guided.pending_region == pending
    assert guided.pending_purpose == 'domain'
