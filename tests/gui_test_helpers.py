# SPDX-License-Identifier: MPL-2.0
"""Shared native-widget fixtures; each test owns its project and worker lifecycle."""
import os
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtCore import QCoreApplication, QEvent, QSettings, Qt, QTimer
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QComboBox, QLineEdit, QMessageBox

from etlayout.examples import make_example
from etlayout.gui.main_window import MainWindow
from etlayout.simulation.runner import run_project


def isolate_window_settings(monkeypatch, tmp_path):
    """Patch application aliases only; Qt's class and native stores stay untouched."""
    from etlayout.gui import layout_editor, main_window
    from etlayout.gui.editor_smoke import acceptance_settings_factory, acceptance_standard_paths

    factory = acceptance_settings_factory(tmp_path)
    monkeypatch.setattr(main_window, "QSettings", factory)
    monkeypatch.setattr(layout_editor, "QSettings", factory)
    paths = acceptance_standard_paths(tmp_path)
    monkeypatch.setattr(main_window, "QStandardPaths", paths)
    monkeypatch.setattr(layout_editor, "QStandardPaths", paths)
    settings = factory()
    filename = Path(settings.fileName()).resolve()
    assert settings.format() == QSettings.Format.IniFormat
    assert not settings.fallbacksEnabled()
    assert filename.is_relative_to(tmp_path.resolve())
    return filename


@pytest.fixture(scope='module')
def solved_e1(tmp_path_factory):
    project = make_example('E1', tmp_path_factory.mktemp('immutable-e1'))
    result = run_project(project)
    assert result.converged
    return project, result


@pytest.fixture
def guided_window(qtbot, tmp_path):
    project = make_example("E1", tmp_path / "inputs")
    window = MainWindow(project, database_path=tmp_path / "materials.sqlite3", recover=False)
    window.recovery_path = tmp_path / "recovery.etlayout"
    window.autosave_timer.stop()
    window.resize(1280, 800)
    window.show()
    window.activateWindow()
    _idle(window, qtbot)
    assert window.geometry is not None
    yield window
    window.cancel_geometry()
    if window.job:
        window.cancel_job()
    if window.guided.power and window.guided.power.job:
        window.guided.power.cancel_job()
    if window.inspection_context:
        window.inspection_context["dirty"] = False
    _idle(window, qtbot)
    window.guided.discard_draft()
    window.dirty = False
    window.close()
    window.deleteLater()
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    QApplication.processEvents()


def _idle(window, qtbot, timeout=30000):
    qtbot.waitUntil(lambda: not window.prepare_job and not window.job and not window.hierarchy_jobs,
                    timeout=timeout)
    QApplication.processEvents()


def _click(window, button):
    guided = window.guided
    if guided.page.isAncestorOf(button):
        guided.page_scroll.ensureWidgetVisible(button)
    QTest.mouseClick(button, Qt.MouseButton.LeftButton)
    QApplication.processEvents()


def _fill(window, name, value):
    editor = window.guided.editor
    field = editor.form.widgets[name]
    editor.form.scroll.ensureWidgetVisible(field)
    window.guided.page_scroll.ensureWidgetVisible(editor)
    if isinstance(field, QLineEdit):
        field.setFocus()
        field.selectAll()
        QTest.keyClicks(field, str(value))
    elif isinstance(field, QComboBox):
        index = field.findData(value)
        assert index >= 0, (name, value)
        field.setCurrentIndex(index)
    else:
        raise TypeError(name)


def _answer_dialog(answer):
    """Answer only the expected modal, without replacing its execution path."""
    def choose():
        dialog = QApplication.activeModalWidget()
        assert isinstance(dialog, QMessageBox), type(dialog)
        button = next((control for control in dialog.buttons() if control.text().replace("&", "") == answer), None)
        assert button is not None, [control.text() for control in dialog.buttons()]
        QTest.mouseClick(button, Qt.MouseButton.LeftButton)
    QTimer.singleShot(0, choose)
