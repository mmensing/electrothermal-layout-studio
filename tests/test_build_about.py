# SPDX-License-Identifier: MPL-2.0
"""About exposes the current build while retaining third-party grants."""
from gui_test_helpers import guided_window as guided_window
from PySide6.QtGui import QAction
from PySide6.QtWidgets import QMessageBox

from etlayout.provenance import build_info


def test_about_action_shows_current_identity_and_retained_licenses(guided_window, qtbot, monkeypatch):
    captured = []
    monkeypatch.setattr(QMessageBox, "about", lambda _parent, _title, text: captured.append(text))
    action = next(a for a in guided_window.findChildren(QAction) if a.text() == "About Electrothermal Layout Studio")
    action.trigger()
    assert len(captured) == 1
    info = build_info()
    assert info["implementation_sha256"] in captured[0]
    assert "Version " + info["version"] in captured[0]
    assert "MPL-2.0" in captured[0] and "GPL-3.0-only" in captured[0]
    assert "GEOS C API: Copyright" in captured[0] and "LGPL" in captured[0]
