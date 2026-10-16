# SPDX-License-Identifier: MPL-2.0
"""Bounded editor-runtime verification for source and frozen Windows builds.

Uses real layout preparation/export workers and portable persistence. The
separate native acceptance script exercises mouse dragging and draft dialogs;
this helper deliberately does not depend on QtTest in distribution packages.
"""
from __future__ import annotations

import json
import platform
import sys
import time
from pathlib import Path

from PySide6.QtCore import QEventLoop, QPointF, QTimer
from PySide6.QtWidgets import QApplication

from etlayout.geometry.polygon_edit import editable_polygons
from etlayout.io.project import file_hash, load_project, physics_hash


def acceptance_settings_factory(output):
    """Return explicit local INI settings for QA; never alter Qt global settings.

    QSettings(organization, application) can retain NativeFormat despite global
    defaults. Acceptance callers install this factory only on their application
    module aliases for that process, or use scoped monkeypatches in tests. Normal
    application startup never invokes it. No native preference store is inspected.
    """
    from PySide6.QtCore import QSettings

    root = Path(output).resolve()
    directory = root / "settings"
    directory.mkdir(parents=True, exist_ok=True)
    path = (directory / "application.ini").resolve()
    if not path.is_relative_to(root):
        raise RuntimeError("Desktop acceptance preferences escaped the output directory")

    def local_settings(*_args, **_kwargs):
        settings = QSettings(str(path), QSettings.Format.IniFormat)
        settings.setFallbacksEnabled(False)
        if (settings.format() != QSettings.Format.IniFormat or settings.fallbacksEnabled()
                or Path(settings.fileName()).resolve() != path):
            raise RuntimeError("Desktop acceptance preferences escaped the output directory")
        return settings

    return local_settings


def run_editor_smoke(window, output, *, timeout=90.):
    """Edit the E1 line-contact fixture and leave a portable input for CLI solve."""
    output = Path(output).resolve()
    output.mkdir(parents=True, exist_ok=True)
    editor = window.guided.layout_editor
    editor.cache_dir = output / "owned-layouts"
    window.autosave_timer.stop()
    window.recovery_path = output / "recovery.etl"
    started = time.monotonic()
    imported = Path(window.project.layout.path)
    imported_hash = file_hash(imported)
    receipt = dict(status="failed", frozen=bool(getattr(sys, "frozen", False)),
        executable=sys.executable, platform=platform.platform(), qpa=QApplication.platformName(),
        imported_sha256=imported_hash, checks=[], captures=[],
        limits="Programmatic runtime verification on the build host; mouse/draft acceptance is separate and no clean-machine certification is implied.")
    ticks = []
    timed_out = []
    heartbeat = QTimer(window)
    heartbeat.setInterval(20)

    def pulse():
        ticks.append(time.monotonic())
        if time.monotonic() - started > timeout and not timed_out:
            timed_out.append(True)
            if editor.job:
                editor.job.cancel()
            window.cancel_geometry()
            if window.job:
                window.cancel_job()
    heartbeat.timeout.connect(pulse)
    heartbeat.start()

    def settle():
        while window.prepare_job or window.job or window.hierarchy_jobs or editor.job:
            QApplication.processEvents(QEventLoop.ProcessEventsFlag.AllEvents, 50)
            if timed_out or time.monotonic() - started > timeout:
                raise TimeoutError(f"Editor runtime verification exceeded {timeout:g} seconds")
            time.sleep(.01)
        # Deliver posted scene, layout and redraw events before inspecting state.
        QApplication.processEvents(QEventLoop.ProcessEventsFlag.AllEvents, 50)
        if window.guided.last_error:
            raise RuntimeError(window.guided.last_error)

    def check(name, condition):
        if not condition:
            raise AssertionError(name)
        receipt["checks"].append(name)

    def capture(name):
        window.resize(1280, 800)
        QApplication.processEvents(QEventLoop.ProcessEventsFlag.AllEvents, 50)
        window.view.fit_all()
        QApplication.processEvents(QEventLoop.ProcessEventsFlag.AllEvents, 50)
        picture = window.grab()
        check("Captured " + name, picture.save(str(output / (name + ".png"))))
        receipt["captures"].append(dict(file=name + ".png", logical_size=[window.width(), window.height()],
                                        physical_size=[picture.width(), picture.height()]))

    try:
        settle()
        before = window.project.to_dict()
        check("Editable imported layout is ready", bool(window.geometry and window.project.layout.path))
        window.guided.navigate("layout")
        check("Real preparation worker enters layout editor", editor.enter())
        key = next(iter(editor.targets))
        item = window.view.layer_items[key][0]
        window.view.choose_candidate(window.view._candidate(item))
        check("Original polygon has editable handles", bool(editor.target and editor.handles))
        original_rings = [list(ring) for ring in editor.rings]
        check("Insert midpoint creates a valid vertex draft", editor.insert_selected() and editor.valid
              and len(editor.rings[0]) == len(original_rings[0]) + 1)
        check("Delete inserted vertex restores the applied ring", editor.delete_selected() and editor.rings == original_rings and not editor.dirty)
        check("Whole-polygon move previews affected fixed contacts", editor.translate(1e-6, 0.) and editor.dirty
              and any(impact.category == "Contact" and "lost" in impact.message for impact in editor.impacts))
        editor.discard()
        editor.snap_grid.setValue(100)
        check("Snap preference leaves physical identity unchanged", not editor.dirty and window.project.to_dict() == before)
        editor.snap_grid.setValue(1)
        handle = editor.handles[0]
        # E1's source lies on x=0. Extending that edge preserves its support.
        editor.move_vertex(handle.ring, handle.index, handle.pos() + QPointF(0., .25))
        expected = editor.effective
        check("Vertex preview is valid and pending", editor.dirty and editor.valid)
        check("Preview preserves project and imported source", window.project.to_dict() == before and file_hash(imported) == imported_hash)
        capture("frozen-editor-pending")
        check("Real export worker applies project-owned layout", editor.apply())
        settle()
        edited = window.project.to_dict()
        check("Applied layout uses a new immutable file", edited["layout"]["path"] != str(imported)
              and file_hash(imported) == imported_hash)
        check("Applied geometry equals preview", any(target.geometry.equals(expected)
              for target in editable_polygons(window.project)[0]))
        window.undo()
        settle()
        check("Undo restores the imported revision", window.project.to_dict() == before)
        window.redo()
        settle()
        check("Redo restores the edited revision", window.project.to_dict() == edited)
        window.path = output / "edited.etl"
        check("Portable project saves through the standard action", window.save())
        restored, _ = load_project(window.path)
        check("Embedded edited layout reopens with matching physics", physics_hash(restored) == physics_hash(window.project))
        check("Embedded polygon equals the committed preview", any(target.geometry.equals(expected)
              for target in editable_polygons(restored)[0]))
        check("Imported file remained unchanged", file_hash(imported) == imported_hash)
        check("Qt remained responsive during the actual workers", len(ticks) >= 5)
        capture("frozen-editor-committed")
        check("Runtime verification completed within its deadline", not timed_out and time.monotonic() - started < timeout)
        receipt.update(status="passed", edited_project=str(window.path),
                       edited_layout_sha256=window.project.layout.sha256,
                       edited_physics_hash=physics_hash(window.project))
    except Exception as error:
        receipt.update(error=f"{type(error).__name__}: {error}", worker_detail=window.guided.last_error_detail,
                       log=window.console.toPlainText())
        window.grab().save(str(output / "failure.png"))
    finally:
        heartbeat.stop()
        heartbeat.deleteLater()
        receipt.update(elapsed_seconds=time.monotonic() - started, heartbeat_ticks=len(ticks))
        (output / "receipt.json").write_text(json.dumps(receipt, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        editor.discard()
        editor.leave(restore=False)
        window.dirty = False
    return receipt["status"] == "passed"
