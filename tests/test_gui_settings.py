# SPDX-License-Identifier: MPL-2.0
"""Explicit files isolate desktop preferences while preserving within-test reopening."""
import pytest
from gui_test_helpers import isolate_window_settings
from PySide6.QtCore import QSettings


@pytest.mark.fast
def test_window_test_preferences_use_an_explicit_file_and_reopen(tmp_path, monkeypatch):
    from etlayout.gui import layout_editor, main_window

    original_default = QSettings.defaultFormat()
    filename = isolate_window_settings(monkeypatch, tmp_path)
    assert main_window.QSettings is layout_editor.QSettings
    first = main_window.QSettings("etlayout", "Electrothermal Layout Studio")
    assert isinstance(first, QSettings)
    first.setValue("workspace-test/sentinel", "temporary settings only")
    first.sync()
    assert filename.is_file()
    reopened = main_window.QSettings("etlayout", "Electrothermal Layout Studio")
    assert reopened.value("workspace-test/sentinel") == "temporary settings only"
    editor = layout_editor.QSettings()
    editor.setValue("layout_editor/snap_grid_dbu", 7)
    editor.sync()
    assert reopened.value("layout_editor/snap_grid_dbu", type=int) == 7
    assert QSettings.defaultFormat() == original_default
