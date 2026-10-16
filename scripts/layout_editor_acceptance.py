# SPDX-License-Identifier: MPL-2.0
"""Native vertex-editing, draft, history and portable-save acceptance.

Every displayed field comes from the standard E1 worker solve. Captures and
receipts belong in artifacts; this does not certify a clean Windows machine,
physical monitor DPI, screen readers, or human interaction timings.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import multiprocessing
import os
import platform
import subprocess
import time
from copy import deepcopy
from dataclasses import replace
from pathlib import Path


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=Path(".artifacts/layout-editor"))
    parser.add_argument("--scale", type=float, default=1.)
    parser.add_argument("--offscreen", action="store_true")
    args = parser.parse_args(argv)
    os.environ["QT_SCALE_FACTOR"] = str(args.scale)
    if args.offscreen:
        os.environ["QT_QPA_PLATFORM"] = "offscreen"
    else:
        os.environ.pop("QT_QPA_PLATFORM", None)
    from desktop_acceptance_support import isolate_acceptance_settings, prepare_desktop_fonts
    from PySide6.QtCore import QCoreApplication, QEvent, QPointF, Qt, QTimer
    from PySide6.QtTest import QTest
    from PySide6.QtWidgets import QApplication, QMessageBox, QProgressDialog, QPushButton

    from etlayout.examples import make_example
    from etlayout.geometry.polygon_edit import editable_polygons
    from etlayout.gui.main_window import MainWindow
    from etlayout.io.project import file_hash, load_project, physics_hash

    out = args.output.resolve()
    out.mkdir(parents=True, exist_ok=True)
    isolate_acceptance_settings(out)
    app = QApplication.instance() or QApplication([])
    app.setStyle("Fusion")
    prepare_desktop_fonts(app, out)
    project = make_example("E1", out / "inputs")
    original = deepcopy(project)
    imported = Path(project.layout.path)
    original_hash = file_hash(imported)
    window = MainWindow(project, database_path=out / "materials.sqlite3", recover=False)
    window.recovery_path = out / "recovery.etl"
    window.autosave_timer.stop()
    window.guided.layout_editor.cache_dir = out / "owned-layouts"
    window.resize(1586, 992)
    window.apply_theme(False)
    window.show()
    window.activateWindow()
    repository = Path(__file__).resolve().parents[1]
    def source_digest():
        digest = hashlib.sha256()
        for source in sorted((repository / "src").rglob("*.py")) + [Path(__file__).resolve(), Path(__file__).with_name("desktop_acceptance_support.py").resolve()]:
            digest.update(source.relative_to(repository).as_posix().encode() + b"\0")
            digest.update(source.read_text(encoding="utf-8").encode() + b"\0")
        return digest.hexdigest()

    receipt = dict(platform=platform.platform(), qpa=app.platformName(), scale=args.scale,
        revision=subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip(),
        source_tree_sha256=source_digest(), imported_sha256=original_hash, checks=[], captures=[],
        limits="Native Qt automation on the build host; no clean-machine, physical-DPI or screen-reader certification.")
    started = time.monotonic()

    def record(name, **values):
        receipt["checks"].append(dict(name=name, elapsed_seconds=time.monotonic() - started, **values))
        (out / "receipt.json").write_text(json.dumps(receipt, indent=2, ensure_ascii=False), encoding="utf-8")
        print(name, flush=True)

    def idle(timeout=180, *, expected_error=False):
        deadline = time.monotonic() + timeout
        while window.save_job or window._save_queue or window.job or window.prepare_job or window.hierarchy_jobs or window.guided.layout_editor.job:
            assert time.monotonic() < deadline, window.console.toPlainText()
            QTest.qWait(40)
        QTest.qWait(100)
        if not expected_error:
            assert not window.guided.last_error, window.guided.last_error

    def capture(name, compact=False):
        window.resize(1280 if compact else 1586, 800 if compact else 992)
        QTest.qWait(150)
        window.view.fit_all()
        QTest.qWait(80)
        picture = window.grab()
        assert picture.save(str(out / (name + ".png")))
        screen = window.screen().availableGeometry()
        receipt["captures"].append(dict(file=name + ".png", logical_size=[window.width(), window.height()],
            requested_logical_size=[1280, 800] if compact else [1586, 992],
            physical_size=[picture.width(), picture.height()], dpr=window.devicePixelRatioF(),
            available_screen_logical=[screen.width(), screen.height()],
            exceeds_available_screen=window.width() > screen.width() or window.height() > screen.height(),
            historical=bool(window.inspection_context), editing=window.guided.layout_editor.active,
            pending=window.guided.layout_editor.dirty))
        if compact:
            assert (window.width(), window.height()) == (1280, 800), "Compact workflow must retain the requested 1280 × 800 window."
        record("Captured " + name)

    def select_polygon():
        editor = window.guided.layout_editor
        target = next(iter(editor.targets.values()))
        point = target.geometry.representative_point()
        click = window.view.mapFromScene(QPointF(point.x * 1e6, -point.y * 1e6))
        QTest.mouseClick(window.view.viewport(), Qt.MouseButton.LeftButton, pos=click)
        QTest.qWait(40)
        assert editor.target is target and editor.handles
        return editor

    def run_from_keyboard():
        window.raise_()
        window.activateWindow()
        QTest.qWait(100)
        QTest.keyClick(window, Qt.Key.Key_F5)
        idle()

    def drag(editor, *, invalid=False):
        handle = editor.handles[0]
        start = window.view.mapFromScene(handle.pos())
        # Extend the x=0 edge downward while retaining E1's line contact.
        # Moving its corner inward would deliberately remove terminal support.
        destination = editor.handles[1].pos() if invalid else handle.pos() + QPointF(0., .25)
        end = window.view.mapFromScene(destination)
        QTest.mousePress(window.view.viewport(), Qt.MouseButton.LeftButton, pos=start)
        QTest.mouseMove(window.view.viewport(), end, delay=40)
        QTest.mouseRelease(window.view.viewport(), Qt.MouseButton.LeftButton, pos=end)
        QTest.qWait(50)
        assert editor.dirty and editor.valid != invalid

    def answer(decision):
        answered = []
        deadline = time.monotonic() + 15

        def choose():
            dialog = QApplication.activeModalWidget()
            if not isinstance(dialog, QMessageBox):
                if time.monotonic() < deadline:
                    QTimer.singleShot(20, choose)
                return
            button = next(button for button in dialog.buttons() if button.text().replace("&", "") == decision)
            QTest.mouseClick(button, Qt.MouseButton.LeftButton)
            answered.append(decision)
        QTimer.singleShot(0, choose)
        return answered

    def reset(result):
        editor = window.guided.layout_editor
        editor.discard()
        editor.leave()
        window.dirty = False
        window.set_project(deepcopy(original), result, history=[result])
        idle()
        window.guided.navigate("layout")
        window.view.fit_all()

    try:
        idle()
        assert window.geometry is not None
        run_from_keyboard()
        result = window.result
        assert result and result.converged and window.run_history
        record("E1 standard F5 worker solve", configuration_hash=result.configuration_hash,
               solve_seconds=result.elapsed_seconds)
        window.guided.navigate("layout")
        window.view.fit_all()
        editor = window.guided.layout_editor
        before = window.project.to_dict()
        QTest.mouseClick(editor.toggle, Qt.MouseButton.LeftButton)
        assert editor.active
        editor = select_polygon()
        capture("selected-original-polygon")
        rings = deepcopy(editor.rings)
        QTest.mouseClick(editor.insert_button, Qt.MouseButton.LeftButton)
        assert editor.valid and editor.dirty and len(editor.rings[0]) == len(rings[0]) + 1
        QTest.mouseClick(editor.delete_button, Qt.MouseButton.LeftButton)
        assert editor.rings == rings and not editor.dirty
        record("Insert and Delete controls preserve the original exact polygon support")
        editor.snap_grid.setValue(100)
        assert not editor.dirty and window.project.to_dict() == before
        editor.vertex_choice.setCurrentIndex(0)
        x, y = editor.rings[0][0]
        editor.y_field.setFocus()
        editor.y_field.selectAll()
        QTest.keyClicks(editor.y_field, f"{y * 1e6 - .24:g}")
        QTest.mouseClick(editor.coordinate_button, Qt.MouseButton.LeftButton)
        assert editor.valid and editor.dirty and abs(editor.rings[0][0][1] - y + .2e-6) < 1e-15
        assert window.project.to_dict() == before
        capture("numeric-vertex-grid-compact", True)
        editor.discard()
        handle = editor.move_handle
        start = window.view.mapFromScene(handle.pos())
        end = window.view.mapFromScene(handle.pos() + QPointF(2., 0.))
        QTest.mousePress(window.view.viewport(), Qt.MouseButton.LeftButton, pos=start)
        QTest.mouseMove(window.view.viewport(), end, delay=40)
        QTest.mouseRelease(window.view.viewport(), Qt.MouseButton.LeftButton, pos=end)
        assert editor.dirty and editor.valid and any(impact.category == "Contact" and "lost" in impact.message for impact in editor.impacts)
        editor.tabs.setCurrentIndex(2)
        editor.impact_list.setCurrentRow(next(i for i, impact in enumerate(editor.impacts) if impact.object_id == "source"))
        assert editor.impact_outline and "lost" in editor.impact_summary.text()
        capture("whole-polygon-affected-supports-compact", True)
        editor.discard()
        editor.tabs.setCurrentIndex(0)
        editor.snap_grid.setValue(1)
        record("Numeric DBU-aligned grid editing and whole-polygon mouse drag preserve project identity until Apply")
        drag(editor)
        expected = editor.effective
        assert window.project.to_dict() == before and window.result is result
        assert file_hash(imported) == original_hash
        capture("pending-vertices-compact", True)
        QTest.mouseClick(editor.apply_button, Qt.MouseButton.LeftButton)
        idle()
        edited = window.project.to_dict()
        assert not editor.dirty and edited["layout"]["path"] != str(imported)
        assert window.result is None and window.mesh is None
        assert any(target.geometry.equals(expected) for target in editable_polygons(window.project)[0])
        assert file_hash(imported) == original_hash
        record("Mouse drag is pending; Apply creates verified project-owned layout and invalidates analysis")
        window.undo()
        idle()
        assert window.project.to_dict() == before
        window.redo()
        idle()
        assert window.project.to_dict() == edited
        record("Undo and Redo restore immutable layout revisions")
        window.path = out / "edited.etl"
        assert window.save()
        idle()
        reopened, _ = load_project(window.path)
        assert physics_hash(reopened) == physics_hash(window.project)
        assert any(target.geometry.equals(expected) for target in editable_polygons(reopened)[0])
        record("Save and embedded-layout reopening preserve the exact committed polygon")
        run_from_keyboard()
        assert window.result and window.result.converged
        assert window.result.configuration_hash != result.configuration_hash
        assert any(run is result for run in window.run_history)
        window.guided.navigate("results")
        capture("edited-layout-real-result", True)
        record("Edited layout runs through the standard solver and retains the imported-layout historical run",
               configuration_hash=window.result.configuration_hash, solve_seconds=window.result.elapsed_seconds)

        for destination in ("save", "history"):
            for decision in ("Apply", "Discard", "Stay"):
                reset(result)
                window.path = out / f"{destination}-{decision.lower()}.etl"
                assert editor.enter()
                select_polygon()
                drag(editor)
                old = window.project.to_dict()
                old_view = window.capture_view_state()
                old_target = editor.target
                old_history = list(window.run_history)
                clicked = answer(decision)
                window.save() if destination == "save" else window.inspect_result(result)
                idle()
                assert clicked == [decision]
                if decision == "Stay":
                    assert editor.active and editor.dirty and editor.target is old_target
                    assert window.project.to_dict() == old and window.result is result
                    assert window.capture_view_state() == old_view
                    assert not window.inspection_context and not window.path.exists()
                else:
                    assert not editor.active and not editor.dirty
                    working = window.inspection_context["project"] if window.inspection_context else window.project
                    assert (working.to_dict() == old) == (decision == "Discard")
                    if destination == "history":
                        assert window.inspection_context and window.result is result
                        assert not editor.enter()
                        capture("historical-readonly-" + decision.lower(), True)
                        window.return_to_project()
                        idle()
                        assert physics_hash(window.project) == physics_hash(working)
                    else:
                        restored, _ = load_project(window.path)
                        assert physics_hash(restored) == physics_hash(working)
                assert window.run_history == old_history and file_hash(imported) == original_hash
                record(f"{decision} before {destination} preserves draft, project and history policy")

        reset(result)
        assert editor.enter()
        select_polygon()
        drag(editor, invalid=True)
        assert not editor.apply_button.isEnabled() and not editor.apply()
        capture("invalid-vertex-draft", True)
        QTest.keyClick(window.view, Qt.Key.Key_Escape)
        assert editor.active and editor.valid and not editor.dirty
        handle = editor.handles[0]
        prior = editor.rings[handle.ring][handle.index][0]
        handle.setFocus()
        QTest.keyClick(window.view, Qt.Key.Key_Right, Qt.KeyboardModifier.ShiftModifier)
        assert abs(editor.rings[handle.ring][handle.index][0] - prior - 10 * editor.dbu) < 1e-15
        QTest.mouseClick(editor.discard_button, Qt.MouseButton.LeftButton)
        assert not editor.dirty
        record("Invalid drag cannot Apply; Escape and Discard clear it; keyboard nudge follows database grid")

        # A deliberately stale selection exercises the actual export worker's
        # concurrency guard without changing the user's imported source.
        drag(editor)
        unchanged = window.project.to_dict()
        editor.target = replace(editor.target, source_hash="0" * 64)
        assert not editor.apply()
        idle(expected_error=True)
        assert window.project.to_dict() == unchanged and file_hash(imported) == original_hash
        assert "revision changed" in window.guided.last_error.lower()
        assert window.guided.last_error_stage == "layout"
        window.guided.open_drawer(0)
        messages = [window.guided.issue_table.item(row, 1).text() for row in range(window.guided.issue_table.rowCount())]
        assert any("Layout edit failed" in message for message in messages)
        capture("export-failure-visible-issue", True)
        controls = window.guided.issue_table.cellWidget(0, 3)
        open_setup = next(button for button in controls.findChildren(QPushButton) if button.text() == "Open setup")
        viewport = window.guided.issue_table.viewport()
        action_rect = open_setup.rect().translated(open_setup.mapTo(viewport, open_setup.rect().topLeft()))
        assert viewport.rect().contains(action_rect), "Open setup must be fully visible in the compact issue drawer."
        editor.discard()
        QTest.mouseClick(open_setup, Qt.MouseButton.LeftButton)
        assert window.guided.stage == "layout" and not editor.active
        record("Actual stale-target export worker rejects mutation and exposes an actionable layout issue")
        window.guided.last_error = ""
        window.guided.refresh()

        cancelled = []
        cancellation_deadline = time.monotonic() + 15

        def cancel_preparation():
            dialog = QApplication.activeModalWidget()
            if not isinstance(dialog, QProgressDialog):
                if time.monotonic() < cancellation_deadline:
                    QTimer.singleShot(10, cancel_preparation)
                return
            button = next(button for button in dialog.findChildren(QPushButton) if button.text() == "Cancel")
            QTest.mouseClick(button, Qt.MouseButton.LeftButton)
            cancelled.append(True)
        QTimer.singleShot(10, cancel_preparation)
        unchanged = window.project.to_dict()
        assert not editor.enter()
        idle()
        assert cancelled == [True] and not editor.active and window.project.to_dict() == unchanged
        assert file_hash(imported) == original_hash
        record("Actual editable-layout preparation worker cancels without mutation")

        # A real imported hole exercises the inspector's alternate ring route.
        import klayout.db as kdb
        reset(result)
        holed = deepcopy(original)
        layout = kdb.Layout()
        layout.read(holed.layout.path)
        cell, layer = layout.cell(holed.layout.cell), layout.find_layer(1, 0)
        shape = next(cell.shapes(layer).each())
        polygon = shape.polygon
        polygon.insert_hole([kdb.Point(round(x / layout.dbu), round(y / layout.dbu))
                             for x, y in [(40, 2), (60, 2), (60, 8), (40, 8)]])
        shape.delete()
        cell.shapes(layer).insert(polygon)
        holed.layout.path = str(out / "holed-import.gds")
        layout.write(holed.layout.path)
        holed.layout.sha256 = file_hash(holed.layout.path)
        window.set_project(holed)
        idle()
        assert editor.enter()
        select_polygon()
        assert len(editor.rings) == 2
        editor.ring_choice.setCurrentIndex(1)
        editor.vertex_choice.setCurrentIndex(0)
        x, y = editor.rings[1][0]
        editor.y_field.setFocus()
        editor.y_field.selectAll()
        QTest.keyClicks(editor.y_field, f"{y * 1e6 + .25:g}")
        QTest.mouseClick(editor.coordinate_button, Qt.MouseButton.LeftButton)
        assert editor.valid and editor.dirty and abs(editor.rings[1][0][1] - y - .25e-6) < 1e-15
        capture("numeric-hole-vertex-compact", True)
        expected = editor.effective
        assert editor.apply()
        idle()
        assert any(target.geometry.equals(expected) for target in editable_polygons(window.project)[0])
        assert file_hash(holed.layout.path) == holed.layout.sha256
        assert editor.finish()
        run_from_keyboard()
        assert window.result and window.result.converged
        record("Hole-ring numeric edit commits, independently reimports and solves through the real worker")
        receipt["source_tree_final_sha256"] = source_digest()
        assert receipt["source_tree_final_sha256"] == receipt["source_tree_sha256"], "Source changed during acceptance; rerun on a stable tree."
        receipt["status"] = "passed"
        receipt["runtime_seconds"] = time.monotonic() - started
    except BaseException as error:
        receipt.update(status="failed", error=f"{type(error).__name__}: {error}", log=window.console.toPlainText(),
                       worker_detail=window.guided.last_error_detail)
        window.grab().save(str(out / "failure.png"))
        raise
    finally:
        (out / "receipt.json").write_text(json.dumps(receipt, indent=2, ensure_ascii=False), encoding="utf-8")
        editor = window.guided.layout_editor
        editor.discard()
        editor.leave(restore=False)
        window.cancel_geometry()
        if window.job:
            window.cancel_job()
        if window.inspection_context:
            window.inspection_context["dirty"] = False
        window.guided.discard_draft()
        window.dirty = False
        window.close()
        window.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        app.processEvents()
    print("Layout-editor acceptance passed", flush=True)


if __name__ == "__main__":
    multiprocessing.freeze_support()
    main()
