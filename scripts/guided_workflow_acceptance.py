# SPDX-License-Identifier: MPL-2.0
"""Native public-action guided workflow and reference capture acceptance.

Fixtures contain input geometry only. Every displayed field is an actual solve.
Qt scaling is application scaling, not physical monitor-DPI certification.
"""
from __future__ import annotations

import argparse
import faulthandler
import hashlib
import json
import multiprocessing
import os
import platform
import subprocess
import time
from dataclasses import asdict
from pathlib import Path


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=Path(".artifacts/guided-workflow"))
    parser.add_argument("--scale", type=float, default=1.)
    parser.add_argument("--offscreen", action="store_true")
    parser.add_argument("--gallery-only", action="store_true")
    parser.add_argument("--cache", type=Path, default=Path(".artifacts/guided-gallery.etlayout"))
    parser.add_argument("--timeout", type=float, default=900., help="Overall Qt workflow deadline in seconds")
    args = parser.parse_args(argv)
    if args.timeout <= 0:
        parser.error("--timeout must be positive")
    os.environ["QT_SCALE_FACTOR"] = str(args.scale)
    if args.offscreen:
        os.environ["QT_QPA_PLATFORM"] = "offscreen"
    else:
        os.environ.pop("QT_QPA_PLATFORM", None)
    import klayout.db as db
    from desktop_acceptance_support import prepare_desktop_fonts
    from PySide6.QtCore import QCoreApplication, QEvent, QPointF, QSettings, Qt, QTimer
    from PySide6.QtTest import QTest
    from PySide6.QtWidgets import QApplication, QComboBox, QLineEdit, QMessageBox

    from etlayout.examples import make_example
    from etlayout.gui.export import export_html_report, export_view
    from etlayout.gui.main_window import ImportWizard, MainWindow
    from etlayout.io.project import load_project, load_run_history, physics_hash, save_project
    from etlayout.simulation.runner import run_project

    out = args.output.resolve()
    out.mkdir(parents=True, exist_ok=True)
    QSettings.setDefaultFormat(QSettings.Format.IniFormat)
    QSettings.setPath(QSettings.Format.IniFormat, QSettings.Scope.UserScope, str(out / "settings"))
    app = QApplication.instance() or QApplication([])
    app.setStyle("Fusion")
    prepare_desktop_fonts(app, out)
    revision = subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip()
    repository = Path(__file__).resolve().parents[1]
    source_digest = hashlib.sha256()
    source_files = sorted((repository / "src").rglob("*.py")) + [Path(__file__).resolve(), Path(__file__).with_name("desktop_acceptance_support.py").resolve()]
    for source in source_files:
        source_digest.update(source.relative_to(repository).as_posix().encode("utf-8") + b"\0")
        source_digest.update(source.read_text(encoding="utf-8").encode("utf-8") + b"\0")
    receipt = {"revision": revision, "working_tree": "UX implementation under validation", "platform": platform.platform(),
               "source_tree_sha256": source_digest.hexdigest(),
               "source_digest_scope": "Sorted src/**/*.py followed by this acceptance script and shared font support; relative POSIX path NUL UTF-8 text with normalized newlines NUL",
               "qpa": app.platformName(), "qt_scale_factor": args.scale, "automated": True,
               "limits": "QTest native widget interaction; no human task timings, screen-reader or physical DPI certification.",
               "captures": [], "checks": []}
    started = time.monotonic()
    window = None
    phase = "Initializing the guided workflow"
    expected_modal = None
    monitor_error = None

    def check_monitor():
        if monitor_error:
            raise AssertionError(monitor_error)

    def record(name, **values):
        nonlocal phase
        check_monitor()
        phase = name
        receipt["checks"].append({"name": name, "elapsed_seconds": time.monotonic() - started, **values})
        (out / "receipt.json").write_text(json.dumps(receipt, indent=2, default=str), encoding="utf-8")
        print(name, flush=True)

    def idle(timeout=180):
        deadline = time.monotonic() + timeout
        while window.job or window.prepare_job or window.hierarchy_jobs:
            check_monitor()
            assert time.monotonic() < deadline, window.console.toPlainText()
            QTest.qWait(40)
        QTest.qWait(100)
        check_monitor()
        assert not window.guided.last_error, window.guided.last_error

    def capture(name, reference=None):
        QTest.qWait(120)
        picture = window.grab()
        path = out / (name + ".png")
        assert picture.save(str(path))
        receipt["captures"].append({"name": name, "reference": reference, "file": path.name,
            "logical_size": [window.width(), window.height()], "physical_size": [picture.width(), picture.height()],
            "available_screen_logical": [window.screen().availableGeometry().width(), window.screen().availableGeometry().height()],
            "capture_exceeds_available_screen": window.width() > window.screen().availableGeometry().width() or window.height() > window.screen().availableGeometry().height(),
            "dpr": window.devicePixelRatioF(), "theme": "dark" if window.settings.value("dark", False, type=bool) else "light",
            "project": window.project.name, "analysis": window.guided.analysis.currentData(), "stage": window.guided.stage,
            "layer": window.field_layer.currentData(), "support": window.field_region.currentData(),
            "run": window.result.configuration_hash if window.result else None,
            "time": window.view_source_result().metrics.get("time_s") if window.result else None})
        record("Captured " + name)

    def click(control):
        check_monitor()
        if window.guided.page.isAncestorOf(control):
            window.guided.page_scroll.ensureWidgetVisible(control)
        QTest.mouseClick(control, Qt.MouseButton.LeftButton)
        QTest.qWait(30)
        check_monitor()

    def fill(key, value):
        field = window.guided.editor.form.widgets[key]
        window.guided.editor.form.scroll.ensureWidgetVisible(field)
        if isinstance(field, QComboBox):
            index = field.findData(value)
            assert index >= 0, (key, value)
            field.setCurrentIndex(index)
        elif isinstance(field, QLineEdit):
            field.setFocus()
            field.selectAll()
            QTest.keyClicks(field, str(value))
        else:
            raise TypeError(key)

    def draw(start, end):
        view = window.view
        view.fit_all()
        a = view.mapFromScene(QPointF(start[0], -start[1]))
        b = view.mapFromScene(QPointF(end[0], -end[1]))
        QTest.mousePress(view.viewport(), Qt.MouseButton.LeftButton, pos=a)
        QTest.mouseMove(view.viewport(), b, 30)
        QTest.mouseRelease(view.viewport(), Qt.MouseButton.LeftButton, pos=b)
        QTest.qWait(40)
        assert window.guided.pending_region is not None

    def dispose():
        if window:
            window.cancel_geometry()
            if window.job:
                window.cancel_job()
            if window.inspection_context:
                window.inspection_context["dirty"] = False
            # Cleanup must not enter another draft dialog after a failed check.
            window.guided.cancel_selection()
            window.guided.clear_editor()
            window.dirty = False
            window.close()
            window.deleteLater()
            QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
            app.processEvents()

    def monitor():
        nonlocal monitor_error
        modal = QApplication.activeModalWidget()
        expired = time.monotonic() - started > args.timeout
        unexpected = modal is not None and not (expected_modal and isinstance(modal, expected_modal))
        if monitor_error:
            if unexpected:
                modal.reject()
            return
        if not (expired or unexpected):
            return
        diagnostic = {"phase": phase, "elapsed_seconds": time.monotonic() - started,
                      "reason": "workflow deadline" if expired else "unexpected modal dialog"}
        if modal:
            diagnostic.update(dialog=type(modal).__name__, title=modal.windowTitle(),
                              text=modal.text() if isinstance(modal, QMessageBox) else "",
                              buttons=[button.text() for button in modal.buttons()] if isinstance(modal, QMessageBox) else [])
            modal.grab().save(str(out / "unexpected-modal.png"))
        if window:
            diagnostic["log"] = window.console.toPlainText()
            window.grab().save(str(out / "watchdog-failure.png"))
        receipt["watchdog_failure"] = diagnostic
        monitor_error = "Guided workflow stopped: " + json.dumps(diagnostic, default=str)
        (out / "receipt.json").write_text(json.dumps(receipt, indent=2, default=str), encoding="utf-8")
        print(monitor_error, flush=True)
        if modal:
            # Fail the journey; never approve an unexpected transition to make
            # automation appear successful. This releases the nested Qt loop.
            modal.reject()
        if expired and window:
            window.cancel_geometry()
            if window.job:
                window.cancel_job()

    def perform(name, callback):
        record(name + " starting")
        value = callback()
        check_monitor()
        record(name + " completed")
        return value

    watchdog = QTimer(app)
    watchdog.setInterval(100)
    watchdog.timeout.connect(monitor)
    watchdog.start()
    # A traceback remains available if a non-Qt operation blocks event delivery.
    faulthandler.dump_traceback_later(min(args.timeout, 180.), repeat=True)

    try:
        if not args.gallery_only:
            layout = db.Layout()
            layout.dbu = .001
            cell = layout.create_cell("METAL_DEMO")
            cell.shapes(layout.layer(1, 0)).insert(db.Box(0, 0, 100000, 10000))
            cell.shapes(layout.layer(99, 0)).insert(db.Box(-10000, -10000, 110000, 20000))
            oasis = out / "fresh-metal.oas"
            layout.write(str(oasis))
            window = MainWindow(database_path=out / "materials.sqlite3", recover=False)
            window.autosave_timer.stop()
            window.recovery_path = out / "recovery.etlayout"
            window.resize(1586, 992)
            window.apply_theme(False)
            window.show()
            capture("analysis-first-entry")

            def accept_import():
                nonlocal monitor_error
                dialog = QApplication.activeModalWidget()
                if not isinstance(dialog, ImportWizard):
                    QTimer.singleShot(30, accept_import)
                    return
                try:
                    assert all(not dialog.mapping.cellWidget(i, 0).isChecked() for i in range(dialog.mapping.rowCount()))
                    dialog.accept()
                except BaseException as error:
                    monitor_error = f"Import wizard acceptance failed: {type(error).__name__}: {error}"
                    dialog.reject()
            expected_modal = ImportWizard
            QTimer.singleShot(30, accept_import)
            window.import_layout(oasis)
            idle()
            expected_modal = None
            assert not window.project.layers and window.masks
            record("UX-01 fresh OASIS import has explicit display-only masks")
            click(window.guided.stage_buttons["layers"])
            window.guided.add_layer("1/0")
            fill("id", "metal")
            fill("name", "Copper routing")
            fill("material_id", "copper")
            fill("thickness", "2")
            click(window.guided.editor.apply_button)
            idle()
            assert len(window.project.layers) == 1, window.guided.next_reason.text()
            assert window.project.layers[0].geometry.sources == ["1/0"]
            record("Mapped physical copper explicitly; mask 99/0 retained as display-only")

            for identifier, kind, value, corners in (("input", "voltage", .02, ((98.5, 1), (99.5, 9))),
                                                     ("return", "ground", 0., ((.5, 1), (1.5, 9)))):
                click(window.guided.stage_buttons["connections"])
                window.guided.new_connection("port")
                fill("id", identifier)
                fill("name", identifier)
                fill("kind", kind)
                if kind != "ground":
                    fill("value", value)
                window.guided.begin_selection("editor")
                draw(*corners)
                before = physics_hash(window.project)
                assert window.guided.pending_region is not None
                click(window.guided.use_selection)
                assert physics_hash(window.project) == before
                click(window.guided.editor.apply_button)
                idle()
                assert any(port.id == identifier for port in window.project.power.ports), window.guided.next_reason.text()
            record("UX-06 physical port input and reference with explicit footprint commits")
            assert window.guided.assessment.ready
            window.guided.validate()
            idle()
            assert not window.guided.assessment.blockers
            window.guided.mesh()
            idle()
            assert window.mesh is not None
            capture("fresh-mesh", "M04")
            window.guided.run()
            capture("run-progress")
            idle()
            assert window.result and window.result.converged, window.console.toPlainText()
            first = window.result
            record("Actual DC solve", configuration_hash=first.configuration_hash, metrics=first.metrics)
            before = physics_hash(window.project)
            mesh = window.mesh
            window.guided.navigate("layout")
            window.guided.begin_selection("view_crop")
            draw((20, -2), (80, 12))
            click(window.guided.use_selection)
            assert physics_hash(window.project) == before and window.mesh is mesh and window.result is first
            window.clear_display_roi()
            record("UX-03 view crop preserves physics, mesh and result identity")
            window.guided.begin_selection("domain")
            draw((20, -2), (80, 12))
            QTest.keyClick(window.view, Qt.Key.Key_Escape)
            assert window.project.roi is None and window.guided.pending_region is None
            record("UX-05 Escape cancels pending domain without mutation")
            window.guided.analysis.setCurrentIndex(window.guided.analysis.findData("electrothermal"))
            assert window.project.power.ports
            assert window.guided.assessment.blockers
            capture("blocking-thermal-issue")
            issue = next(i for i in window.guided.assessment.issues if i.blocking and i.stage == "conditions")
            window.guided.fix_issue(issue)
            assert window.guided.stage == "conditions"
            window.guided.new_model("thermal_boundaries")
            fill("kind", "coefficient")
            fill("value", "2000000")
            fill("ambient", "300")
            click(window.guided.editor.apply_button)
            idle()
            assert window.project.thermal_boundaries
            capture("operating-conditions")
            window.guided.run()
            idle()
            assert window.result.converged and "temperature" in window.result.fields["metal"]
            record("UX-07 converted fresh DC setup to real coupled electrothermal solve", metrics=window.result.metrics)
            second = window.result
            window.guided.navigate("layers")
            fill("thickness", "3")
            click(window.guided.editor.apply_button)
            idle()
            assert window.result is None and any(x is first for x in window.run_history) and any(x is second for x in window.run_history)
            window.inspect_result(second)
            idle()
            capture("historical-stale-result")
            assert window.inspection_context["project"].layers[0].thickness == 3e-6
            perform("Historical field export", lambda: export_view(window.view, out / "historical-map.png", window.legend.text()))
            perform("Historical report export", lambda: export_html_report(window.project, window.result, window.view, out / "historical-report.html", window.legend.text()))
            perform("Return from History to working setup", window.return_to_project)
            window.path = out / "fresh-project.etlayout"
            assert perform("Save working project after History", window.save)
            assert len(perform("Load archived run history", lambda: load_run_history(window.path))) == 2
            perform("Reopen saved working project", lambda: window.open_project(window.path))
            idle()
            assert window.project.layers[0].thickness == 3e-6
            record("UX-11/13/17 stale history, immutable export and archive reopen")
            window.guided.run()
            assert window.job
            window.cancel_job()
            idle()
            assert window.job is None and "Cancelled" in window.guided.execution_state
            record("UX-12 actual worker cancellation retains earlier history")
            # Start a separate, known HEMT example through the same command used
            # by File / Examples. Change only the declared gate-control value.
            window.dirty = False
            window.guided.execute("example.E7")
            idle()
            geometry_before = [asdict(layer.geometry) for layer in window.project.layers]
            window.guided.run()
            idle()
            gate_baseline = window.result
            assert gate_baseline and gate_baseline.converged
            click(window.guided.stage_buttons["connections"])
            window.guided.edit_model("terminals", "gate_control")
            fill("value", -1)
            click(window.guided.editor.apply_button)
            idle()
            assert [asdict(layer.geometry) for layer in window.project.layers] == geometry_before
            window.guided.run()
            idle()
            assert window.result and window.result.converged
            assert window.result.metrics["total_current"] != gate_baseline.metrics["total_current"]
            window.field_layer.setCurrentIndex(window.field_layer.findData("channel"))
            capture("hemt-gate-result")
            record("HEMT gate-control change preserves active geometry and changes actual current",
                   before_current_A=gate_baseline.metrics["total_current"],
                   after_current_A=window.result.metrics["total_current"])
            history_count = len(window.run_history)
            click(window.guided.stage_buttons["mesh"])
            fill("max_elements", 1)
            click(window.guided.editor.apply_button)
            idle()
            window.guided.mesh()
            deadline = time.monotonic() + 180
            while window.job:
                assert time.monotonic() < deadline
                QTest.qWait(40)
            assert window.guided.last_error, "The real mesh resource limit must fail"
            assert len(window.run_history) == history_count
            window.guided.open_drawer(0)
            capture("worker-failure")
            record("Real worker resource-limit failure retains history", error=window.guided.last_error)
            dispose()
            window = None

        # One coherent multilayer example supplies all M01–M05 captures.
        if args.cache.is_file():
            project, result = load_project(args.cache)
        else:
            project = make_example("E12", out / "gallery-inputs")
            project.solver.mode = "electrothermal"
            result = run_project(project)
            assert result.converged
            args.cache.parent.mkdir(parents=True, exist_ok=True)
            save_project(project, args.cache, result)
        window = MainWindow(project, result, database_path=out / "gallery-materials.sqlite3", recover=False)
        window.autosave_timer.stop()
        window.recovery_path = out / "gallery-recovery.etlayout"
        window.resize(1586, 992)
        window.setMinimumSize(1586, 992)
        window.apply_theme(False)
        window.show()
        window.resize(1586, 992)
        idle()
        for stage, reference in (("layout", "M01"), ("layers", "M02"), ("connections", "M03"), ("mesh", "M04"), ("results", "M05")):
            click(window.guided.stage_buttons[stage])
            window.guided.open_drawer(3 if stage == "results" else 0)
            QTest.qWait(60)
            window.view.fit_all()
            if stage == "layout":
                window.guided.numeric_domain()
            elif stage == "connections":
                window.guided.edit_model("terminals", project.terminals[0].id)
            elif stage == "mesh":
                window.guided.preview_mesh()
            elif stage == "results":
                window.field_layer.setCurrentIndex(window.field_layer.findData("substrate"))
                window.field_combo.setCurrentIndex(window.field_combo.findData("temperature"))
                window.colormap_combo.setCurrentIndex(window.colormap_combo.findData("viridis"))
                window.mesh_toggle.setChecked(False)
                point = result.mesh.centroids[result.mesh.elements(window.field_layer.currentData())[0]]
                window.probe(*point)
                window.guided.record_probe(*point)
                window.fit_result_layer()
            capture(reference + "-" + stage, reference)
            window.guided.cancel_selection()
        click(window.guided.stage_buttons["layers"])
        window.set_tool("select")
        point = window.geometry.layers["m1"].representative_point()
        scene_point = QPointF(point.x * 1e6, -point.y * 1e6)
        QTest.mouseClick(window.view.viewport(), Qt.MouseButton.LeftButton,
                         pos=window.view.mapFromScene(scene_point))
        QTest.qWait(100)
        assert window.view.candidate_menu and window.view.candidate_menu.isVisible()
        assert window.view.candidate_menu.grab().save(str(out / "overlapping-object-pick-list.png"))
        window.view.candidate_menu.close()
        record("Actual overlapping-object candidate menu captured")
        for stage in ("conditions", "run", "studies"):
            if stage == "studies":
                window.guided.navigate(stage)
            else:
                click(window.guided.stage_buttons[stage])
            capture(stage + "-derived")
        window.setMinimumSize(1280, 800)
        window.resize(1280, 800)
        click(window.guided.stage_buttons["results"])
        window.guided.drawer.hide()
        QTest.qWait(80)
        window.fit_result_layer()
        capture("compact-results")
        assert window.guided.primary.isVisible() and window.guided.analysis.isVisible()
        assert window.guided.primary.rect().height() > 20
        window.apply_theme(True)
        capture("compact-dark")
        record("Reference and compact capture series completed")
        receipt["result"] = "passed"
    except BaseException as error:
        receipt["result"] = "failed"
        receipt["error"] = f"{type(error).__name__}: {error}"
        if window:
            window.grab().save(str(out / "failure.png"))
            receipt["log"] = window.console.toPlainText()
        raise
    finally:
        expected_modal = None
        try:
            dispose()
        finally:
            watchdog.stop()
            faulthandler.cancel_dump_traceback_later()
            if monitor_error:
                receipt["result"] = "failed"
                receipt["error"] = receipt.get("error", monitor_error)
            (out / "receipt.json").write_text(json.dumps(receipt, indent=2, default=str), encoding="utf-8")
    check_monitor()
    print("Guided workflow acceptance passed", flush=True)


if __name__ == "__main__":
    multiprocessing.freeze_support()
    main()
