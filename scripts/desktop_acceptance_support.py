# SPDX-License-Identifier: MPL-2.0
"""Shared local settings isolation and fonts for desktop acceptance captures.

Windows' offscreen Qt plugin does not discover installed fonts. Register the
host's existing fonts for that test plugin only; never copy or bundle them.
Native runs keep their font database and application font unchanged.
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

REQUIRED_CHARACTERS = "ETLayout0123456789µΩ²Δ°C×−→≤≥"
WINDOWS_FONT_FILES = ("segoeui.ttf", "segoeuib.ttf", "segoeuisl.ttf")


def verify_glyph_support(raw_font, characters=REQUIRED_CHARACTERS):
    """Reject invalid fonts and missing glyphs, rather than accepting tofu images."""
    if not raw_font.isValid():
        raise RuntimeError("Desktop capture requires a valid application font.")
    missing = sorted({f"U+{ord(char):04X}" for char in characters
                      if not raw_font.supportsCharacter(ord(char))})
    if missing:
        raise RuntimeError("Desktop capture font lacks required glyphs: " + ", ".join(missing))
    return {"family": raw_font.familyName(), "style": raw_font.styleName(),
            "verified_characters": characters, "missing_codepoints": []}


def prepare_desktop_fonts(app, output):
    """Verify regular/bold fonts before rendering, and write an explicit receipt."""
    from PySide6.QtCore import qVersion
    from PySide6.QtGui import QFont, QFontDatabase, QRawFont

    output = Path(output)
    output.mkdir(parents=True, exist_ok=True)
    receipt = {"qpa": app.platformName(), "qt_version": qVersion(),
               "loaded_font_families": [], "native_font_database_unchanged": True,
               "fonts": {}, "status": "failed"}
    try:
        if sys.platform == "win32" and app.platformName() == "offscreen":
            font_directory = Path(os.environ.get("WINDIR", "C:/Windows")) / "Fonts"
            for filename in WINDOWS_FONT_FILES:
                path = font_directory / filename
                if not path.is_file():
                    if filename == "segoeuisl.ttf":
                        continue  # Optional light face; regular/bold glyph checks remain mandatory.
                    raise RuntimeError("Offscreen Windows capture requires installed host font " + filename)
                font_id = QFontDatabase.addApplicationFont(str(path))
                families = QFontDatabase.applicationFontFamilies(font_id)
                if font_id < 0 or not families:
                    raise RuntimeError("Could not register host font " + filename)
                receipt["loaded_font_families"].extend(families)
            receipt["native_font_database_unchanged"] = False
            app.setFont(QFont("Segoe UI", 9))
        for name, bold in (("regular", False), ("bold", True)):
            font = QFont(app.font())
            font.setBold(bold)
            receipt["fonts"][name] = verify_glyph_support(QRawFont.fromFont(font))
        receipt["loaded_font_families"] = sorted(set(receipt["loaded_font_families"]))
        receipt["status"] = "passed"
    except RuntimeError as error:
        receipt["error"] = str(error)
        raise
    finally:
        (output / "font-verification.json").write_text(
            json.dumps(receipt, indent=2, ensure_ascii=False), encoding="utf-8")
    return receipt


def isolate_acceptance_settings(output):
    """Install file-backed preferences for this standalone acceptance process.

    Only application-module constructor aliases are replaced; QtCore.QSettings
    and global defaults/paths remain untouched. Both windows and vertex-editor
    snap-grid controls must use the same store. Eager recovery initialization and
    cache fallbacks receive owned module-local standard paths too. Return QSettings for
    runners that need explicit initial preferences before constructing windows.
    """
    import etlayout.gui.layout_editor as layout_editor
    import etlayout.gui.main_window as main_window
    from etlayout.gui.editor_smoke import acceptance_settings_factory, acceptance_standard_paths

    factory = acceptance_settings_factory(output)
    main_window.QSettings = factory
    layout_editor.QSettings = factory
    paths = acceptance_standard_paths(output)
    main_window.QStandardPaths = paths
    layout_editor.QStandardPaths = paths
    return factory()


def wait_desktop_events(milliseconds=40):
    """Pump a real Qt event loop without starving Python worker threads.

    Some PySide6 QTest.qWait builds hold the GIL while waiting in native code.
    An event loop matches QApplication.exec and allows archive compression,
    cancellation and queued Qt signals to make progress during acceptance.
    """
    from PySide6.QtCore import QEventLoop, QTimer

    if milliseconds < 0:
        raise ValueError("Event-loop wait duration must be nonnegative")
    loop = QEventLoop()
    timer = QTimer(loop)
    timer.setSingleShot(True)
    timer.timeout.connect(loop.quit)
    timer.start(milliseconds)
    loop.exec()


def desktop_worker_activity(window):
    """Report every operation owned by this acceptance window, including readers.

    An existing geometry or result can belong to the previous project while the
    asynchronous project reader is still running. Retired ComputeJobs also own
    process/receiver cleanup after their public window slot has been cleared.
    """
    from etlayout.gui.jobs import ComputeJob

    active = []
    for label, busy in (("calculation", window.job), ("preparation", window.prepare_job),
                        ("hierarchy", window.hierarchy_jobs), ("save", window.save_job),
                        ("queued saves", window._save_queue),
                        ("project reader", window.history_opener.jobs),
                        ("history reader", window.history_workflow.loader.jobs),
                        ("pending project", window.project_opening.pending)):
        if busy:
            active.append(label)
    for name in ("power", "layout_editor"):
        controller = getattr(window.guided, name, None)
        if controller and controller.job:
            active.append(name)
    if any(not job._cleanup_complete for job in window.findChildren(ComputeJob)):
        active.append("process cleanup")
    return active


def wait_desktop_workers(window, *, timeout=180., process_events=wait_desktop_events, check=None, allow_modal=None):
    """Wait for accepted work and queued application callbacks, with a deadline.

    A timer rejects unexpected nested modal dialogs so event delivery cannot
    strand a noninteractive journey inside QDialog.exec(). Expected dialogs are
    allowed explicitly by a journey while its bounded dialog driver is active.
    """
    import time

    from PySide6.QtCore import QTimer
    from PySide6.QtWidgets import QApplication

    deadline = time.monotonic() + timeout
    errors = []

    def monitor_modal():
        modal = QApplication.activeModalWidget()
        if modal is not None:
            if allow_modal and allow_modal(modal) and time.monotonic() < deadline:
                return
            text = modal.text() if hasattr(modal, "text") else modal.windowTitle()
            errors.append("Unexpected desktop dialog: " + text)
            modal.reject()

    timer = QTimer(window)
    timer.setInterval(25)
    timer.timeout.connect(monitor_modal)
    timer.start()
    try:
        # Even an apparently idle window can have a queued reader-completion or
        # pending-project application callback waiting in the GUI event queue.
        while True:
            if check:
                check()
            monitor_modal()
            if errors:
                raise RuntimeError(errors[0])
            if time.monotonic() >= deadline:
                raise TimeoutError("Desktop workers did not finish: " + ", ".join(desktop_worker_activity(window)))
            process_events(40)
            if errors:
                raise RuntimeError(errors[0])
            if not desktop_worker_activity(window) and QApplication.activeModalWidget() is None:
                if check:
                    check()
                return
    finally:
        timer.stop()
        timer.deleteLater()


def assert_desktop_project(window, path, expected_project):
    """Reject false readiness on the old project, even if it has valid geometry."""
    from etlayout.io.identity import document_id
    from etlayout.io.project import physics_hash

    expected_path = Path(path).resolve()
    assert window.path and Path(window.path).resolve() == expected_path, (window.path, expected_path)
    assert window.project.name == expected_project.name, (window.project.name, expected_project.name)
    assert document_id(window.project) == document_id(expected_project), "Opened project identity differs from the requested archive"
    assert physics_hash(window.project, resolve_layout=False) == physics_hash(expected_project, resolve_layout=False), "Opened physical inputs differ from the requested archive"
    assert window.geometry is not None, window.console.toPlainText()


def close_desktop_window(window, *, timeout=30.):
    """Cancel and join this journey's workers before closing without draft dialogs.

    This is acceptance cleanup, not a production Close policy. It discards only
    the temporary journey's unsaved editor state and never marks a failed step
    successful. A cancelled reader must actually stop before QObject teardown.
    """
    from PySide6.QtCore import QCoreApplication, QEvent
    from PySide6.QtWidgets import QApplication

    from etlayout.gui.jobs import ComputeJob

    window.autosave_timer.stop()
    window.cancel_catalog()
    window.history_workflow.cancel()
    window.cancel_saving()
    window.cancel_job()
    window.cancel_geometry()
    for job in window.findChildren(ComputeJob):
        if not job._cleanup_complete:
            job.cancel()
    wait_desktop_workers(window, timeout=timeout)
    if window.inspection_context:
        window.inspection_context["dirty"] = False
    window.guided.cancel_selection()
    window.guided.clear_editor()
    window.guided.layout_editor.discard()
    window.dirty = False
    assert window.close(), "Acceptance window refused to close after its workers stopped"
    window.deleteLater()
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    QApplication.processEvents()
