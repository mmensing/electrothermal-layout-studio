# SPDX-License-Identifier: MPL-2.0
"""Native example browser, real worker solve and portable reference-field captures.

Use --results to display optional benchmark-produced results. Opening references
does not run them; --solve invokes the standard worker on the exact compact input.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import multiprocessing
import os
import platform
import subprocess
import time
from pathlib import Path


def verify_scientific_acceptance(project, result):
    """Check actual stored calculations against their unchanged input limits."""
    from etlayout.power_examples import metadata

    assert result.converged
    checks = []
    frames = result.transient.snapshots if result.transient else [result]
    for index, frame in enumerate(frames):
        for metric, limit in (("current_balance_error", project.solver.current_balance_tolerance),
                              ("power_balance_error", project.solver.power_balance_tolerance),
                              ("thermal_balance_error", project.solver.thermal_balance_tolerance)):
            if metric not in frame.metrics:
                continue
            if (frame.metrics[metric] is None and result.transient and index == 0
                    and metric == "thermal_balance_error" and frame.metrics.get("time_s") == 0):
                checks.append(dict(snapshot=index, metric=metric, status="not_applicable",
                                   reason="Initial state has no elapsed thermal storage interval"))
                continue
            value = float(frame.metrics[metric])
            assert math.isfinite(value) and value <= limit, (project.name, index, metric, value, limit)
            checks.append(dict(snapshot=index, metric=metric, value=value, limit=limit))
        loss_budget = frame.analysis.get("loss_budget")
        if loss_budget and "terminal_power" in frame.metrics:
            terminal = float(frame.metrics["terminal_power"])
            integrated = sum(float(row["power"]) for row in loss_budget["entries"])
            error = abs(terminal - integrated) / max(abs(terminal), abs(integrated), 1e-30)
            assert math.isfinite(error) and error <= project.solver.power_balance_tolerance, (
                project.name, index, "terminal_vs_disjoint_losses", terminal, integrated, error)
            checks.append(dict(snapshot=index, metric="terminal_vs_disjoint_losses", value=error,
                               limit=project.solver.power_balance_tolerance))
    config = metadata(project)
    if "requested_current_A" in config:
        requested = float(config["requested_current_A"])
        delivered = float(result.analysis["ports"]["drain"]["current"])
        error = abs(delivered / requested - 1)
        assert math.isfinite(error) and error <= project.solver.current_balance_tolerance, (
            project.name, "delivered_service", requested, delivered, error)
        checks.append(dict(metric="delivered_service", requested_A=requested, delivered_A=delivered,
                           value=error, limit=project.solver.current_balance_tolerance))
        from benchmark_power_examples import summarize
        science = summarize(project, result)
        assert not science["model_failures"] and not science["unavailable"], (
            project.name, science["model_failures"], science["unavailable"])
    return checks


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=Path(".artifacts/power-desktop"))
    parser.add_argument("--results", type=Path)
    parser.add_argument("--additional-results", nargs="*", type=Path, default=[],
                        help="Fallback directories of previously solved references; current physical identity must match")
    parser.add_argument("--solve", nargs="*", default=["E21", "E23"])
    parser.add_argument("--workflow", nargs="*", default=["E1", "E17"],
                        help="Exact default projects to validate, mesh and run; use all for the complete catalog")
    parser.add_argument("--catalog", action="store_true", help="Open every built-in example and check all seven stages without solving")
    parser.add_argument("--scale", type=float, default=1.)
    parser.add_argument("--offscreen", action="store_true", help="Use offscreen Qt for CI; native Windows is the default")
    args = parser.parse_args()
    if args.offscreen:
        os.environ["QT_QPA_PLATFORM"] = "offscreen"
    else:
        os.environ.pop("QT_QPA_PLATFORM", None)
    os.environ["QT_SCALE_FACTOR"] = str(args.scale)
    import psutil
    from desktop_acceptance_support import isolate_acceptance_settings, prepare_desktop_fonts
    from PySide6.QtCore import QCoreApplication, QEvent, Qt, QTimer
    from PySide6.QtTest import QTest
    from PySide6.QtWidgets import QApplication, QMenu, QPushButton

    from etlayout.example_catalog import EXAMPLE_CATALOG, example_info
    from etlayout.examples import make_example
    from etlayout.gui.main_window import MainWindow
    from etlayout.io.project import (
        estimate_project_archive,
        load_project,
        load_run_history,
        physics_hash,
        save_project,
    )
    from etlayout.power_examples import FAMILIES, metadata
    from etlayout.workflow import STAGES

    out = args.output.resolve()
    out.mkdir(parents=True, exist_ok=True)
    isolate_acceptance_settings(out)
    app = QApplication.instance() or QApplication([])
    app.setStyle("Fusion")
    prepare_desktop_fonts(app, out)
    window = MainWindow(database_path=out / "materials.sqlite3", recover=False)
    window.autosave_timer.stop()
    window.recovery_path = out / "recovery.etl"
    window.apply_theme(False)
    window.show()
    started = time.monotonic()
    source_files = sorted((Path(__file__).resolve().parents[1] / "src").rglob("*.py")) + [Path(__file__).resolve(), Path(__file__).with_name("desktop_acceptance_support.py").resolve()]
    def source_hash():
        digest = hashlib.sha256()
        for path in source_files:
            digest.update(path.relative_to(Path(__file__).resolve().parents[1]).as_posix().encode() + b"\0")
            digest.update(path.read_text(encoding="utf-8").encode() + b"\0")
        return digest.hexdigest()
    receipt = dict(platform=platform.platform(), qpa=app.platformName(), offscreen=args.offscreen,
                   scale=args.scale, checks=[], captures=[], stages=[], runs=[],
                   revision=subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip(),
                   source_tree_sha256=source_hash(),
                   failed_workflows=[], missing_results=[], rendered_results=[], phases=[], hardware=dict(logical_cpus=psutil.cpu_count(),
                       physical_cpus=psutil.cpu_count(logical=False), physical_memory_bytes=psutil.virtual_memory().total),
                   memory_sample_interval_seconds=.2,
                   limits=("Offscreen Qt CI checks; native Windows verification is separate." if args.offscreen else
                           "Native Qt widget automation on build host; not clean-machine or physical-DPI certification."))
    memory = {"phase": None, "peak_gui_rss_bytes": 0, "peak_worker_rss_bytes": 0, "sampling_errors": []}
    def persist():
        (out / "receipt.json").write_text(json.dumps(receipt, indent=2, ensure_ascii=False), encoding="utf-8")

    def sample_memory():
        try:
            parent = psutil.Process()
            memory["peak_gui_rss_bytes"] = max(memory["peak_gui_rss_bytes"], parent.memory_info().rss)
            children = parent.children(recursive=True)
            worker_rss = 0
            for child in children:
                try:
                    worker_rss += child.memory_info().rss
                except psutil.NoSuchProcess:
                    continue
            memory["peak_worker_rss_bytes"] = max(memory["peak_worker_rss_bytes"], worker_rss)
        except (psutil.AccessDenied, psutil.NoSuchProcess) as error:
            if str(error) not in memory["sampling_errors"]:
                memory["sampling_errors"].append(str(error))
    memory_timer = QTimer(window)
    memory_timer.setInterval(200)
    memory_timer.timeout.connect(sample_memory)
    memory_timer.start()

    def idle(timeout=1500, check_error=True):
        deadline = time.monotonic() + timeout
        while window.job or window.prepare_job or window.hierarchy_jobs:
            assert time.monotonic() < deadline, window.console.toPlainText()
            QTest.qWait(40)
        QTest.qWait(100)
        if check_error:
            assert not window.guided.last_error, window.guided.last_error

    def capture(name, compact=False):
        window.resize(1280 if compact else 1586, 800 if compact else 992)
        QTest.qWait(120)
        if compact:
            assert (window.width(), window.height()) == (1280, 800), (
                "Compact viewport was resized", [window.width(), window.height()],
                [window.screen().availableGeometry().width(), window.screen().availableGeometry().height()])
        window.view.fit_all()
        QTest.qWait(150)
        path = out / (name + ".png")
        assert window.grab().save(str(path))
        receipt["captures"].append(dict(file=path.name, project=window.project.name,
            size=[window.width(), window.height()], solved=window.result is not None,
            available_screen_logical=[window.screen().availableGeometry().width(), window.screen().availableGeometry().height()],
            device_pixel_ratio=window.devicePixelRatioF(),
            exceeds_available_screen=not window.screen().availableGeometry().size().expandedTo(window.size()) == window.screen().availableGeometry().size(),
            configuration=metadata(window.project), field=window.field_combo.currentData(),
            layer=window.field_layer.currentData()))
        persist()

    def click(control):
        if window.guided.page.isAncestorOf(control):
            window.guided.page_scroll.ensureWidgetVisible(control)
        QTest.mouseClick(control, Qt.MouseButton.LeftButton)
        QTest.qWait(40)

    def shortcut(key):
        window.raise_()
        window.activateWindow()
        window.view.setFocus(Qt.FocusReason.OtherFocusReason)
        QTest.qWait(100)
        QTest.keyClick(window, key)

    def stage_snapshot():
        guided = window.guided
        receipt["stages"].append(dict(project=window.project.name, stage=guided.stage,
            analysis=guided.analysis.currentData(), primary=guided.primary.text(), enabled=guided.primary.isEnabled(),
            reason=guided.next_reason.text(), status=guided.assessment.stages[guided.stage].status,
            applied_hash=physics_hash(window.project), checked=guided.assessment.checked,
            elements=len(window.mesh.triangles) if window.mesh else None))
        persist()

    def measure_phase(label, launch):
        memory.update(phase=label, peak_gui_rss_bytes=0, peak_worker_rss_bytes=0, sampling_errors=[])
        phase_started = time.monotonic()
        print(f"{window.project.name}: starting {label}", flush=True)
        launch()
        events = []
        if window.job:
            window.job.progress.connect(lambda fraction, message: events.append(dict(
                elapsed_seconds=time.monotonic() - phase_started, message=message)))
        try:
            idle()
        finally:
            sample_memory()
            receipt["phases"].append(dict(project=window.project.name, phase=label,
                wall_seconds=time.monotonic() - phase_started, cpu_threads=window.execution_settings.cpu_threads,
                peak_gui_rss_bytes=memory["peak_gui_rss_bytes"], peak_worker_rss_bytes=memory["peak_worker_rss_bytes"],
                sampling_errors=memory["sampling_errors"], progress=events))
            print(f"{window.project.name}: {label} ended after {time.monotonic()-phase_started:.2f}s", flush=True)
            persist()

    def navigate_all(captures=False):
        applied = window.project.to_dict()
        for number, stage in enumerate(STAGES, 1):
            click(window.guided.stage_buttons[stage.key])
            assert window.guided.stage == stage.key
            assert window.project.to_dict() == applied and not window.job
            assert window.guided.primary.isEnabled() == (stage.key != "results" or window.result is not None)
            stage_snapshot()
            if captures:
                capture(f"{window.project.name}-{number:02d}-{stage.key}", True)

    def worker_journey(identifier):
        window.dirty = False
        project = make_example(identifier, out / "workflow-inputs")
        measure_phase("preparation", lambda: window.set_project(project))
        navigate_all()
        window.guided.navigate("mesh")
        measure_phase("validation", lambda: shortcut(Qt.Key.Key_F6))
        assert window.guided.assessment.checked and window.guided.assessment.ready
        stage_snapshot()
        measure_phase("meshing", lambda: shortcut(Qt.Key.Key_F7))
        assert window.mesh is not None and window.guided.assessment.stages["mesh"].status == "Ready"
        preview = next(b for b in window.guided.page.findChildren(QPushButton) if b.text() == "Preview existing mesh")
        click(preview)
        assert window.mesh_toggle.isChecked()
        capture(window.project.name + "-workflow-mesh", True)
        stage_snapshot()
        click(window.guided.stage_buttons["run"])
        measure_phase("solve_and_postprocessing", lambda: shortcut(Qt.Key.Key_F5))
        assert window.result and window.result.converged and window.run_history
        assert window.guided.stage == "results" and window.guided.primary.isEnabled()
        assert physics_hash(window.result.configuration) == physics_hash(window.project)
        info = example_info(identifier)
        assert (window.field_layer.currentData(), window.field_combo.currentData()) == (info.layer, info.field)
        assert (window.view.field_layer, window.view.field_key) == (info.layer, info.field)
        science_checks = verify_scientific_acceptance(window.project, window.result)
        if window.project.transient.enabled:
            assert window.result.transient is not None
            assert abs(window.result.transient.times[-1] - window.project.transient.stop_time) < 1e-12
        stage_snapshot()
        capture(window.project.name + "-workflow-result", True)
        destination = out / "solved-workflows" / window.project.name / "simulation.etl"
        destination.parent.mkdir(parents=True, exist_ok=True)
        estimate = estimate_project_archive(window.project, window.result, window.run_history)
        assert estimate["fits_budget"], estimate["issues"]
        window.path = destination
        assert window.save(), "Save action did not complete"
        receipt["runs"].append(dict(project=window.project.name, archive=str(destination.relative_to(out)),
            configuration_hash=window.result.configuration_hash, operating_point=metadata(window.project),
            delivered_current_A=window.result.analysis.get("ports", {}).get("drain", {}).get("current"),
            solver_seconds=window.result.elapsed_seconds, elements=len(window.result.mesh.triangles),
            diagnostics=[dict(severity=item.severity, message=item.message, object_id=item.object_id)
                         for item in window.result.diagnostics],
            execution=window.result.execution))
        receipt["runs"][-1]["scientific_acceptance"] = science_checks
        reopened, result = load_project(destination)
        history = load_run_history(destination)
        assert result is not None and history and result.converged
        assert physics_hash(reopened) == physics_hash(project)
        window.dirty = False
        window.set_project(reopened, result, destination, history)
        idle()
        original_project, original_result = window.project.to_dict(), window.result
        window.inspect_result(window.run_history[0])
        idle()
        assert window.inspection_context and not window.guided.analysis.isEnabled()
        assert not window.undo_action.isEnabled()
        window.return_to_project()
        idle()
        assert window.project.to_dict() == original_project and window.result is original_result
        receipt["checks"].append("Actual F6 validation, F7 mesh, preview and F5 Run on exact input: " + identifier)
        receipt["checks"].append("Saved/embedded reopening, read-only History and restored working context: " + identifier)
        persist()

    try:
        idle()
        menu = next(menu for menu in window.findChildren(QMenu) if menu.title() == "Ready-to-run examples")
        for name in FAMILIES:
            window.dirty = False
            action = next(action for action in menu.actions() if action.text().startswith(example_info(name).id + " ·"))
            action.trigger()
            idle()
            assert window.project.name == name and window.geometry is not None and window.mesh is None
            assert window.guided.active_layer.currentData() == example_info(name).layer
            assert window.project.power.ports and window.project.power.measurements
            window.guided.navigate("layout")
            assert window.guided.stage == "layout"
            capture(name + "-layout")
            capture(name + "-compact", True)
            navigate_all(captures=example_info(name).id == "E21")
            receipt["checks"].append("Browser opens complete reference without running: " + name)
        if args.catalog:
            for name in EXAMPLE_CATALOG:
                if name in FAMILIES:
                    continue
                window.dirty = False
                window.load_example(name)
                idle()
                navigate_all()
                assert window.mesh is None and not window.run_history
            receipt["checks"].append("Every catalog reference opens through all seven stages without changing applied inputs")
        workflows = list(EXAMPLE_CATALOG) if "all" in args.workflow else args.workflow
        for identifier in workflows:
            try:
                worker_journey(identifier)
            except Exception as error:
                receipt["failed_workflows"].append(dict(identifier=identifier, project=window.project.name,
                    stage=window.guided.stage, mode=window.guided.analysis.currentData(),
                    configured_hash=physics_hash(window.project), error=str(error),
                    diagnostics=window.guided.last_error, log=window.console.toPlainText()))
                capture(window.project.name + "-workflow-failure", True)
                try:
                    failed_archive = out / "failed-workflows" / window.project.name / "simulation.etl"
                    failed_result = window.result if window.result and physics_hash(window.result.configuration) == physics_hash(window.project) else None
                    save_project(window.project, failed_archive, failed_result, history=window.run_history)
                    receipt["failed_workflows"][-1]["archive"] = str(failed_archive.relative_to(out))
                except Exception as archive_error:
                    receipt["failed_workflows"][-1]["archive_error"] = str(archive_error)
                if window.job:
                    window.cancel_job()
                window.cancel_geometry()
                idle(timeout=30, check_error=False)
                window.guided.discard_draft()
                persist()
        for identifier in args.solve:
            name = example_info(identifier).name
            project = make_example(name, out / "compact-inputs", preset="compact")
            window.dirty = False
            window.set_project(project)
            idle()
            window.raise_()
            window.activateWindow()
            QTest.qWait(100)
            QTest.keyClick(window, Qt.Key.Key_F5)
            idle()
            assert window.result and window.result.converged, window.console.toPlainText()
            assert abs(window.result.analysis["ports"]["drain"]["current"] / metadata(project)["requested_current_A"] - 1) < 1e-6
            assert window.run_history
            window.guided.navigate("results")
            capture(name + "-worker-result", True)
            receipt["checks"].append("F5 exact compact worker solve, current and history: " + name)
        if args.results:
            for name in (EXAMPLE_CATALOG if args.catalog else FAMILIES):
                paths = [directory / name / "simulation.etl" for directory in
                         [out / "solved-workflows", args.results, *args.additional_results]]
                path = next((candidate for candidate in paths if candidate.exists()), None)
                if path is None:
                    receipt["missing_results"].append(name)
                    continue
                project, result = load_project(path)
                assert result is not None, f"{path} has no numerical result"
                reference = make_example(name, out / "reference-identity-inputs")
                assert physics_hash(project) == physics_hash(reference), (
                    name, "Saved result does not match the currently generated nominal reference", str(path))
                science_checks = verify_scientific_acceptance(project, result)
                window.dirty = False
                window.set_project(project, result)
                idle()
                navigate_all()
                window.guided.navigate("results")
                info = example_info(name)
                field_index, layer_index = window.field_combo.findData(info.field), window.field_layer.findData(info.layer)
                assert field_index >= 0 and layer_index >= 0, (name, info.field, info.layer)
                assert (window.field_layer.currentData(), window.field_combo.currentData()) == (info.layer, info.field)
                assert (window.view.field_layer, window.view.field_key) == (info.layer, info.field)
                window.mesh_toggle.setChecked(False)
                window.colormap_combo.setCurrentIndex(window.colormap_combo.findData("viridis"))
                capture(name + "-reference-field", True)
                snapshot = window.project.to_dict()
                window.inspect_result(result)
                idle()
                assert window.inspection_context and not window.guided.analysis.isEnabled()
                if info.id in {"E1", "E17", "E21"}:
                    capture(name + "-loaded-readonly-history", True)
                window.return_to_project()
                idle()
                assert window.project.to_dict() == snapshot and window.result is result
                assert (window.field_layer.currentData(), window.field_combo.currentData()) == (info.layer, info.field)
                receipt["rendered_results"].append(dict(project=name, archive=str(path.resolve()),
                    configured_hash=physics_hash(reference), scientific_acceptance=science_checks))
                receipt["checks"].append("Rendered actual saved reference fields: " + name)
        receipt["source_tree_final_sha256"] = source_hash()
        assert receipt["source_tree_final_sha256"] == receipt["source_tree_sha256"], "Source changed during acceptance; repeat on a frozen revision"
        assert not receipt["failed_workflows"], f"{len(receipt['failed_workflows'])} configured example workflows failed; inspect receipt.json"
        if args.catalog and args.results:
            assert not receipt["missing_results"], "Catalog result archives missing: " + ", ".join(receipt["missing_results"])
        receipt["status"] = "passed"
    except BaseException as error:
        receipt.update(status="failed", error=f"{type(error).__name__}: {error}", log=window.console.toPlainText())
        window.grab().save(str(out / "failure.png"))
        raise
    finally:
        receipt["runtime_seconds"] = time.monotonic() - started
        (out / "receipt.json").write_text(json.dumps(receipt, indent=2, ensure_ascii=False), encoding="utf-8")
        window.cancel_geometry()
        if window.job:
            window.cancel_job()
            idle(check_error=False)
        memory_timer.stop()
        window.guided.discard_draft()
        window.dirty = False
        window.close()
        window.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        app.processEvents()
    print(f"Power-example acceptance passed ({app.platformName()})")


if __name__ == "__main__":
    multiprocessing.freeze_support()
    main()
