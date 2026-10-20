# SPDX-License-Identifier: MPL-2.0
"""Example discovery and native transitions share the existing document workflow."""
from collections import deque

import pytest
from gui_test_helpers import _click, _fill, _idle
from gui_test_helpers import guided_window as guided_window
from PySide6.QtCore import QPoint, Qt, QTimer
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QDialogButtonBox, QMessageBox

from etlayout.example_catalog import EXAMPLE_CATALOG, START_HERE, SUBJECTS, example_info
from etlayout.example_library import COMPANION_CATALOG, library_info_for_project
from etlayout.gui.example_library import ExampleLibraryDialog
from etlayout.io.project import load_project
from etlayout.power_examples import metadata

pytestmark = pytest.mark.desktop


@pytest.fixture
def browser(qtbot):
    from etlayout.gui.theme import apply_widget_theme
    dialog = ExampleLibraryDialog()
    apply_widget_theme(dialog, False)
    qtbot.addWidget(dialog)
    dialog.show()
    return dialog


def _choose(combo, value):
    index = combo.findData(value)
    assert index >= 0, (combo.objectName(), value)
    combo.setCurrentIndex(index)
    QApplication.processEvents()


def _listed(dialog):
    return {dialog.examples.item(index).data(Qt.ItemDataRole.UserRole)
            for index in range(dialog.examples.count())}


def _native_browser(window, configure, *, accept=True, answers=()):
    """Operate real modal widgets, including any subsequent draft/save questions."""
    observed = []
    remaining = deque(answers)

    def answer_question():
        dialog = QApplication.activeModalWidget()
        try:
            assert isinstance(dialog, QMessageBox), type(dialog)
            answer = remaining.popleft()
            observed.append((dialog.windowTitle(), answer))
            button = next(control for control in dialog.buttons()
                          if control.text().replace("&", "") == answer)
            if remaining:
                QTimer.singleShot(0, answer_question)
            QTest.mouseClick(button, Qt.MouseButton.LeftButton)
        except BaseException:
            if dialog:
                dialog.reject()
            raise

    def choose_project():
        dialog = QApplication.activeModalWidget()
        try:
            assert isinstance(dialog, ExampleLibraryDialog), type(dialog)
            observed.append((dialog.windowTitle(), "Open" if accept else "Cancel"))
            configure(dialog)
            if remaining:
                QTimer.singleShot(0, answer_question)
            button = dialog.open_button if accept else dialog.buttons.button(QDialogButtonBox.StandardButton.Cancel)
            QTest.mouseClick(button, Qt.MouseButton.LeftButton)
        except BaseException:
            if dialog:
                dialog.reject()
            raise

    QTimer.singleShot(0, choose_project)
    window.guided.execute("file.example_library")
    assert not remaining
    return observed


@pytest.mark.parametrize("query,required,excluded", [
    ("E2", {"E2"}, {"E20", "E21", "E22", "E23", "E24", "E25", "E26", "E27", "E28", "E29"}),
    ("GaN", {"E16", "E37"}, {"E9", "E23", "E24", "E39"}),
    ("TLM", {"E27"}, {"E1", "E21", "E39"}),
    ("SiC", {"E9", "E23", "E24", "E39"}, {"E26", "E30"}),
])
def test_search_exposes_recommended_models_and_exact_ids(browser, qtbot, query, required, excluded):
    browser.search.setFocus()
    qtbot.keyClicks(browser.search, query)
    identifiers = {example_info(name).id for name in _listed(browser)}
    assert required <= identifiers and not excluded.intersection(identifiers)
    assert browser.open_button.isEnabled()


def test_subject_level_and_collection_filters_share_catalog_metadata(browser):
    assert browser.examples.count() == len(EXAMPLE_CATALOG)
    for subject in SUBJECTS:
        _choose(browser.subject, subject)
        assert _listed(browser) == {info.name for info in EXAMPLE_CATALOG.values() if info.category == subject}
    _choose(browser.subject, None)
    _choose(browser.level, "Starter")
    assert _listed(browser) == {info.name for info in EXAMPLE_CATALOG.values() if info.level == "Starter"}
    _choose(browser.level, None)
    _choose(browser.collection, "start")
    assert _listed(browser) == {example_info(identifier).name for identifier in START_HERE}
    _choose(browser.collection, "companion")
    assert _listed(browser) == set(COMPANION_CATALOG)
    assert not browser.subject.isEnabled() and not browser.level.isEnabled()
    browser.search.setText("there is no matching example")
    assert not _listed(browser) and not browser.open_button.isEnabled()
    with pytest.raises(ValueError, match="Choose an available example"):
        browser.selection()


@pytest.mark.parametrize("identifier,scenario,drive,original", [
    ("E37", "voltage_controlled", "drain_a 0.2 V (voltage)", "drain_a 0.02 A (current)"),
    ("E39", "continuous_reference", "drain 2 A (current)", "drain 10 A (current)"),
])
def test_recommended_scenarios_show_selected_inputs_and_preserve_original_choice(browser, identifier, scenario, drive, original):
    browser.select_example(identifier)
    assert browser.scenario.currentData() == scenario
    assert browser.selection() == {"name": example_info(identifier).name, "scenario": scenario}
    assert "Selected scenario inputs" in browser.inputs.toPlainText()
    assert drive in browser.inputs.toPlainText()
    _choose(browser.scenario, "reference")
    assert browser.selection()["scenario"] == "reference"
    assert original in browser.inputs.toPlainText()
    if identifier == "E39":
        assert "stress" in browser.details.toPlainText().lower()


def test_entry_switching_preserves_family_choices_and_named_scenarios(browser):
    browser.select_example("E21")
    _choose(browser.preset, "compact")
    _choose(browser.variant, "dual")
    _choose(browser.thermal, "resistive_attach")
    expected = browser.selection()
    assert "Requested configuration inputs" in browser.inputs.toPlainText()
    assert "Original reference thermal path" not in browser.inputs.toPlainText()
    assert "0.075 A" in browser.inputs.toPlainText() and "resistive_attach" in browser.inputs.toPlainText()
    browser.select_example("E39")
    _choose(browser.scenario, "semiconductor_heat")
    browser.select_example("E21")
    assert browser.selection() == expected
    browser.select_example("E39")
    assert browser.selection()["scenario"] == "semiconductor_heat"
    browser.select_example("E21")
    _choose(browser.preset, "large")
    assert "Large/stress cases are opt-in" in browser.resource_note.text()
    assert browser.selection()["preset"] == "large"  # Choice inspection does not generate the large device.


@pytest.mark.parametrize("identifier", ["E21", *COMPANION_CATALOG])
def test_native_browser_opens_selected_project_with_useful_view_without_autosolve(guided_window, qtbot, identifier):
    window = guided_window

    def configure(dialog):
        dialog.select_example(identifier)
        if identifier == "E21":
            _choose(dialog.preset, "compact")
            _choose(dialog.variant, "dual")
            _choose(dialog.thermal, "resistive_attach")

    observed = _native_browser(window, configure)
    _idle(window, qtbot)
    info = library_info_for_project(window.project)
    assert info.name == (example_info(identifier).name if identifier == "E21" else identifier)
    assert observed == [("Browse example library", "Open")]
    assert window.guided.active_layer.currentData() == window.field_layer.currentData() == info.layer
    assert window._preferred_field == info.field and window.geometry is not None
    assert window.mesh is None and window.result is None and not window.run_history and window.job is None
    assert not window.dirty
    if identifier == "E21":
        config = metadata(window.project)
        assert (config["preset"], config["variant"], config["thermal"]) == ("compact", "dual", "resistive_attach")
        assert config["fingers"] == 4 and config["gate_width_um"] == 60.
    else:
        assert window.project.power.experiments and window.project.power.recipes


def test_canceling_native_browser_preserves_unapplied_editor_and_working_context(guided_window, qtbot):
    window = guided_window
    _click(window, window.guided.stage_buttons["layers"])
    _fill(window, "thickness", "3.5")
    project, geometry, editor = window.project, window.geometry, window.guided.editor
    original = project.to_dict()
    revision = window.guided.document.revision
    observed = _native_browser(window, lambda dialog: dialog.select_example("E39"), accept=False)
    _idle(window, qtbot)
    assert observed == [("Browse example library", "Cancel")]
    assert window.project is project and window.geometry is geometry and project.to_dict() == original
    assert window.guided.stage == "layers" and window.guided.editor is editor and editor.has_changes()
    assert window.guided.document.revision == revision and not window.run_history


@pytest.mark.parametrize("answer", ["Stay", "Discard", "Apply"])
def test_accepting_browser_resolves_draft_before_replacing_project(guided_window, qtbot, tmp_path, answer):
    window = guided_window
    _click(window, window.guided.stage_buttons["layers"])
    _fill(window, "thickness", "3.5")
    project, geometry, editor = window.project, window.geometry, window.guided.editor
    original = project.to_dict()
    window.path = tmp_path / "working-before-example.etl"
    answers = (answer, "Save") if answer == "Apply" else (answer,)
    observed = _native_browser(window, lambda dialog: dialog.select_example("E25"), answers=answers)
    _idle(window, qtbot)
    assert ("Unapplied draft", answer) in observed
    if answer == "Stay":
        assert window.project is project and window.geometry is geometry and project.to_dict() == original
        assert window.guided.editor is editor and editor.has_changes()
        assert editor.form.widgets["thickness"].text() == "3.5"
        assert window.guided.stage == "layers" and not window.run_history
    else:
        assert window.project.name == "E25_stepped_width_series"
        assert window.result is None and window.mesh is None and window.job is None and not window.dirty
        if answer == "Discard":
            assert project.to_dict() == original
        else:
            saved, result = load_project(tmp_path / "working-before-example.etl")
            assert saved.layer("metal").thickness == pytest.approx(3.5e-6)
            assert result is None and ("Unsaved project", "Save") in observed


def test_browser_command_is_guarded_during_real_job_and_restored(guided_window, qtbot):
    window = guided_window
    key = "file.example_library"
    project = window.project
    window.guided.execute("simulation.mesh")
    assert window.job is not None
    assert not window.guided.actions[key].isEnabled() and not window.guided.registry.available(key)
    window.guided.execute(key)
    window.browse_example_library()  # Direct invocation has the same job safeguard.
    assert window.project is project and QApplication.activeModalWidget() is None
    assert "Finish or cancel" in window.statusBar().currentMessage()
    _idle(window, qtbot)
    assert window.mesh is not None and window.guided.registry.available(key)
    assert window.guided.actions[key].isEnabled()


def test_compact_browser_retains_keyboard_open_and_cancel_controls(browser, qtbot):
    browser.resize(680, 480)
    QApplication.processEvents()
    assert browser.size().width() == 680 and browser.size().height() == 480
    cancel = browser.buttons.button(QDialogButtonBox.StandardButton.Cancel)
    for button in (browser.open_button, cancel):
        assert button.isVisible() and button.isEnabled()
        assert browser.rect().contains(button.mapTo(browser, QPoint(0, 0)))
        assert browser.rect().contains(button.mapTo(browser, button.rect().bottomRight()))
    browser.search.setFocus()
    qtbot.keyClicks(browser.search, "E2")
    assert _listed(browser) == {example_info("E2").name}
    for _ in range(20):
        if browser.open_button.hasFocus():
            break
        qtbot.keyClick(QApplication.focusWidget(), Qt.Key.Key_Tab)
    assert browser.open_button.hasFocus()
    with qtbot.waitSignal(browser.accepted):
        qtbot.keyClick(browser.open_button, Qt.Key.Key_Return)
    assert browser.selection()["name"] == example_info("E2").name
    browser.show()
    cancel.setFocus()
    with qtbot.waitSignal(browser.rejected):
        qtbot.keyClick(cancel, Qt.Key.Key_Space)


def test_compact_browser_prioritizes_requested_inputs_without_nested_scroll(browser):
    from PySide6.QtWidgets import QScrollArea
    browser.resize(680, 480)
    browser.select_example("E21")
    _choose(browser.preset, "large")
    QApplication.processEvents()
    assert browser.detail_tabs.currentWidget() is browser.inputs
    assert "64 × 1000" in browser.inputs.toPlainText()
    assert "Requested configuration inputs" in browser.inputs.toPlainText()
    assert "Large/stress cases are opt-in" in browser.resource_note.text()
    assert not browser.findChildren(QScrollArea)
    assert browser.configuration.isVisible()
    browser.detail_tabs.setCurrentWidget(browser.details)
    assert "Model limits" in browser.details.toPlainText()
    assert browser.configuration.isVisible() and browser.open_button.isVisible()


@pytest.mark.parametrize("settle_before_resize", [False, True])
def test_programmatic_selection_stays_visible_after_compact_resize(browser, qtbot, settle_before_resize):
    browser.select_example("E21")
    if settle_before_resize:
        qtbot.waitUntil(lambda: not browser._selection_visibility_timer.isActive())
        QApplication.processEvents()
    browser.resize(680, 480)
    qtbot.waitUntil(lambda: not browser._selection_visibility_timer.isActive())
    QApplication.processEvents()
    item = browser.examples.currentItem()
    assert item.data(Qt.ItemDataRole.UserRole) == example_info("E21").name
    assert browser.examples.viewport().rect().contains(browser.examples.visualItemRect(item))
    assert "Requested configuration inputs" in browser.inputs.toPlainText()

    # A user can read the start of the list without losing the selected inputs.
    scrollbar = browser.examples.verticalScrollBar()
    qtbot.keyClick(scrollbar, Qt.Key.Key_Home)
    QApplication.processEvents()
    assert not browser.examples.viewport().rect().intersects(browser.examples.visualItemRect(item))
    browser.resize(700, 500)
    QApplication.processEvents()
    qtbot.waitUntil(lambda: not browser._selection_visibility_timer.isActive())
    assert browser.examples.verticalScrollBar().value() == 0
    assert browser.selection()["name"] == example_info("E21").name
