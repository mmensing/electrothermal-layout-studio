# SPDX-License-Identifier: MPL-2.0
"""Run the real desktop acceptance workflow with Qt dialogs and mouse events.

Run natively on Windows for release evidence:
    python scripts/gui_acceptance.py --output .artifacts/gui-acceptance
Use --offscreen only for CI; native screenshot QA is a separate release gate.
No physics, imports, archives, dialogs or jobs are replaced with fake results.
"""
from __future__ import annotations

import argparse
import json
import multiprocessing
import os
import sys
import time
from copy import deepcopy
from pathlib import Path


def main(argv=None):
    multiprocessing.freeze_support()
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", default=".artifacts/gui-acceptance")
    parser.add_argument("--offscreen", action="store_true")
    args = parser.parse_args(argv)
    if args.offscreen:
        os.environ["QT_QPA_PLATFORM"] = "offscreen"
    else:
        os.environ.pop("QT_QPA_PLATFORM", None)
    from desktop_acceptance_support import (
        isolate_acceptance_settings,
        prepare_desktop_fonts,
        wait_desktop_events,
    )
    from PySide6.QtCore import QPointF, Qt, QTimer
    from PySide6.QtTest import QTest
    from PySide6.QtWidgets import QApplication, QCheckBox, QComboBox, QDialogButtonBox, QMessageBox

    from etlayout.examples import make_example
    from etlayout.gui.export import export_html_report, export_view
    from etlayout.gui.forms import ModelDialog
    from etlayout.gui.main_window import ImportWizard, MainWindow, NewProjectWizard
    from etlayout.io.export import export_results, export_study
    from etlayout.models import SweepSpec
    output = Path(args.output).resolve()
    output.mkdir(parents=True, exist_ok=True)
    isolate_acceptance_settings(output)
    baseline = make_example("E4", output / "layouts")
    application = QApplication.instance() or QApplication(["gui_acceptance"])
    application.setStyle("Fusion")
    prepare_desktop_fonts(application, output)
    window = MainWindow(database_path=output / "materials.sqlite3", recover=False)
    window.recovery_path = output / "recovery.etlayout"
    window.resize(1560, 980)
    window.apply_theme(False)
    window.show()
    wait_desktop_events(180)
    checkpoints = []
    failures = []

    def record(step, **details):
        checkpoints.append({"step": step, **details})
        (output / "acceptance.json").write_text(json.dumps({"platform": sys.platform,
            "qt_platform": application.platformName(), "checkpoints": checkpoints,
            "failures": failures}, indent=2, default=str), encoding="utf-8")
        print(step, flush=True)

    def arm_dialog(expected, configure, timeout=30.):
        deadline = time.monotonic() + timeout
        def handle():
            dialog = QApplication.activeModalWidget()
            if dialog is None and time.monotonic() < deadline:
                QTimer.singleShot(40, handle)
                return
            if not isinstance(dialog, expected):
                failures.append(f"Expected {expected.__name__}, got {type(dialog).__name__}")
                if dialog:
                    dialog.reject()
                return
            try:
                configure(dialog)
                button_box = dialog.findChild(QDialogButtonBox)
                if button_box and button_box.button(QDialogButtonBox.StandardButton.Ok):
                    QTest.mouseClick(button_box.button(QDialogButtonBox.StandardButton.Ok), Qt.MouseButton.LeftButton)
                else:
                    dialog.accept()
                def check_submission():
                    if dialog.isVisible():
                        inline = [label.text() for label in getattr(dialog, "errors", {}).values()
                                  if label.isVisible()]
                        failures.append(f"Dialog {expected.__name__} did not accept: {inline}")
                        dialog.reject()
                QTimer.singleShot(100, check_submission)
            except Exception as error:
                failures.append(f"Dialog {expected.__name__}: {error}")
                dialog.reject()
        QTimer.singleShot(50, handle)

    def fill(dialog, values):
        for key, value in values.items():
            widget = dialog.widgets[key]
            if isinstance(widget, QComboBox):
                index = widget.findData(value)
                if index < 0:
                    index = widget.findText(str(value))
                if index >= 0:
                    widget.setCurrentIndex(index)
                elif widget.isEditable():
                    widget.setEditText(str(value))
                else:
                    raise AssertionError(f"No {key} choice for {value!r}")
            elif isinstance(widget, QCheckBox):
                widget.setChecked(bool(value))
            else:
                widget.setText(str(value))

    def wait_idle(timeout=180., require_geometry=False):
        started = time.monotonic()
        while window.save_job or window._save_queue or window.job is not None or window.prepare_job is not None or window.hierarchy_jobs:
            if time.monotonic() - started > timeout:
                window.cancel_job()
                window.cancel_geometry()
                raise TimeoutError(f"Desktop workers exceeded {timeout} seconds")
            wait_desktop_events(40)
        assert not failures, failures
        if require_geometry:
            assert window.geometry is not None, window.console.toPlainText()
        return time.monotonic() - started

    def edit_object(group, identifier, values):
        window.guided.navigate({"layers": "layers", "interfaces": "connections", "terminals": "connections",
                                "thermal_boundaries": "conditions", "heat_sources": "conditions"}[group])
        window.guided.edit_model(group, identifier)
        assert window.guided.editor is not None
        fill(window.guided.editor.form, values)
        assert window.guided.editor.apply()
        wait_idle(require_geometry=True)

    def add_object(group, values, region=None):
        guide = window.guided
        guide.navigate({"layers": "layers", "interfaces": "connections", "terminals": "connections",
                        "thermal_boundaries": "conditions", "heat_sources": "conditions"}[group])
        if group == "layers":
            guide.add_layer()
            if region is not None:
                guide.editor.set_region(region)
        elif group in {"interfaces", "terminals"}:
            guide.new_connection("interface" if group == "interfaces" else "terminal", region)
        else:
            guide.new_model(group, region)
        assert guide.editor is not None
        fill(guide.editor.form, values)
        assert guide.editor.apply()
        wait_idle(require_geometry=True)

    def draw(mode, start, end, configure=None):
        wait_idle(require_geometry=True)
        window.guided.canvas_tabs.setCurrentIndex(0)
        window.set_tool(mode)
        start_pixel = window.view.mapFromScene(QPointF(start[0], -start[1]))
        end_pixel = window.view.mapFromScene(QPointF(end[0], -end[1]))
        QTest.mousePress(window.view.viewport(), Qt.MouseButton.LeftButton, pos=start_pixel)
        QTest.mouseMove(window.view.viewport(), end_pixel, 50)
        QTest.mouseRelease(window.view.viewport(), Qt.MouseButton.LeftButton, pos=end_pixel)
        QApplication.processEvents()
        if configure:
            guide = window.guided
            assert guide.pending_region is not None and guide.editor is not None
            # Drawing previews geometric support. Explicitly commit it to the
            # inspector, then apply the typed object to the shared document.
            guide.commit_selection()
            fill(guide.editor.form, configure)
            assert guide.editor.apply()
        wait_idle(require_geometry=True)
        assert not failures, failures

    def compute(operation="solve", argument=None, timeout=180.):
        wait_idle(require_geometry=True)
        window.start_job(operation, argument)
        assert window.job is not None
        start = time.monotonic()
        while window.job is not None:
            if time.monotonic() - start > timeout:
                window.cancel_job()
                raise TimeoutError(f"{operation} exceeded {timeout} seconds")
            wait_desktop_events(40)
            modal = QApplication.activeModalWidget()
            if isinstance(modal, QMessageBox):
                text = modal.text()
                modal.accept()
                raise RuntimeError(text)
        assert not failures, failures
        wait_idle(require_geometry=True)
        if operation == "solve":
            assert window.result is not None and window.result.converged, window.console.toPlainText()
        return time.monotonic() - start

    def screenshot(name):
        wait_desktop_events(100)
        window.grab().save(str(output / name))

    try:
        arm_dialog(NewProjectWizard, lambda wizard: (wizard.name.setText("GUI acceptance — multilayer bend"), wizard.import_after.setChecked(False)))
        window.new_project()
        record("Create project wizard")
        def configure_import(wizard):
            wizard.thickness.setText("2")
            for row in range(wizard.mapping.rowCount()):
                mask = wizard.mapping.item(row, 1).text()
                wizard.mapping.cellWidget(row, 0).setChecked(mask in {"1/0", "2/0"})
        arm_dialog(ImportWizard, configure_import)
        window.import_layout(baseline.layout.path)
        wait_idle(require_geometry=True)
        assert window.document and len(window.project.layers) == 2
        record("Import nonrectangular GDS through wizard", cells=window.document.cells, masks=window.mask_keys())
        layer_a, layer_b = [layer.id for layer in window.project.layers]
        edit_object("layers", layer_a, {"name": "Lower copper", "thickness": 2., "z": 0., "material_id": "copper"})
        edit_object("layers", layer_b, {"name": "Upper copper", "thickness": 2., "z": 4., "material_id": "copper"})
        window.guided.navigate("layout")
        for key, value in {"x": "0", "y": "0", "width": "110", "height": "80"}.items():
            window.guided.bounds_fields[key].setText(value)
        window.guided.numeric_domain()
        assert window.guided.pending_region is not None and window.project.roi is None
        window.guided.commit_selection()
        assert window.project.roi is not None
        wait_idle()
        assert window.geometry is None and "truncates a conducting path" in window.console.toPlainText()
        # Correct the domain physically; acknowledging a warning does not supply
        # the missing electrical conditions at a truncated conductor.
        assert not window.project.acknowledge_clipped_boundaries
        window.guided.navigate("layout")
        for key, value in {"x": "0", "y": "0", "width": "120", "height": "80"}.items():
            window.guided.bounds_fields[key].setText(value)
        window.guided.numeric_domain()
        window.guided.commit_selection()
        wait_idle(require_geometry=True)
        def catch_unexpected_message():
            modal = QApplication.activeModalWidget()
            if isinstance(modal, QMessageBox):
                failures.append(modal.text())
                modal.reject()
        modal_guard = QTimer(window)
        modal_guard.setInterval(100)
        modal_guard.timeout.connect(catch_unexpected_message)
        modal_guard.start()
        window.view.fit_all()
        record("Preview clipped domain, then correct to include complete conductors", roi_m=window.geometry.domain.bounds)
        draw("interface_rect", (62, 2), (78, 18), {"id": "via", "layer_a": layer_a, "layer_b": layer_b,
             "electrical_kind": "specific", "electrical_value": 1e-12,
             "thermal_kind": "conductance", "thermal_value": 1e7})
        assert len(window.project.interfaces) == 1
        record("Graphically declare physical interlayer contact")
        window.guided.active_layer.setCurrentIndex(window.guided.active_layer.findData(layer_a))
        draw("terminal_rect", (.2, .2), (2.5, 19.8), {"id": "source", "layer_id": layer_a, "kind": "voltage", "value": 0., "name": "Source"})
        window.guided.active_layer.setCurrentIndex(window.guided.active_layer.findData(layer_b))
        draw("terminal_rect", (107.5, 60.2), (109.8, 79.8), {"id": "drain", "layer_id": layer_b, "kind": "current", "value": .2, "name": "Drain"})
        assert len(window.project.terminals) == 2
        record("Graphically place fixed-voltage and total-current terminals")
        compute("validate")
        assert window.net_report
        compute("mesh")
        assert len(window.mesh.triangles) > 0
        screenshot("01-mesh.png")
        record("Generate and inspect conforming mesh", statistics=window.mesh.statistics)
        window.mesh_toggle.setChecked(False)
        seconds = compute()
        window.field_layer.setCurrentIndex(window.field_layer.findData(layer_b))
        window.field_combo.setCurrentIndex(window.field_combo.findData("potential"))
        screenshot("02-potential.png")
        window.field_combo.setCurrentIndex(window.field_combo.findData("current_density"))
        window.vector_toggle.setChecked(True)
        screenshot("03-current-density.png")
        record("DC electrical maps with real field vectors", seconds=seconds, metrics=window.result.metrics)

        from etlayout.models import RegionSpec
        add_object("layers", {"id": "substrate", "name": "SiC thermal substrate", "material_id": "sic", "thickness": 50., "z": -52., "role": "thermal", "opacity": .2}, RegionSpec(full_domain=True))
        for layer in [layer_a, layer_b]:
            add_object("interfaces", {"id": "thermal_" + layer, "layer_a": layer, "layer_b": "substrate", "electrical_kind": "none", "thermal_kind": "resistance", "thermal_value": 2e-7}, RegionSpec(full_domain=True))
        add_object("thermal_boundaries", {"id": "sink", "layer_id": "substrate", "kind": "coefficient", "value": 2e6, "ambient": 300.}, RegionSpec(full_domain=True))
        window.thermal_joule.setChecked(True)
        window.guided.analysis.setCurrentIndex(window.guided.analysis.findData("thermal"))
        compute()
        record("Steady thermal solve with same-mesh DC Joule and contact heating", metrics=window.result.metrics)
        window.guided.analysis.setCurrentIndex(window.guided.analysis.findData("electrothermal"))
        compute()
        window.field_layer.setCurrentIndex(window.field_layer.findData(layer_b))
        window.field_combo.setCurrentIndex(window.field_combo.findData("temperature"))
        window.vector_toggle.setChecked(False)
        screenshot("04-coupled-temperature.png")
        assert window.result.metrics["temperature_max"] > 300.
        record("Self-consistent electrothermal solve", metrics=window.result.metrics)
        window.locate_hotspot()
        draw("profile", (82, 70), (108, 70))
        assert window.profiles.profiles
        screenshot("05-line-profile.png")
        draw("cross_section", (70, 1), (70, 79))
        assert window.cross_section.intervals
        screenshot("06-cross-section.png")
        window.field_combo.setCurrentIndex(window.field_combo.findData("joule_density"))
        record("Hotspot, power map, line profile and geometry-dependent physical cross-section")

        sweep = SweepSpec("terminals.drain.value", [.1, .2, .3], "electrothermal", "Current sweep")
        arm_dialog(ModelDialog, lambda dialog: fill(dialog, {"parameter": sweep.parameter,
                   "values": ".1, .2, .3", "mode": sweep.mode, "name": sweep.name}))
        window.run_sweep_dialog()
        start = time.monotonic()
        while window.job:
            assert time.monotonic() - start < 180
            wait_desktop_events(50)
        assert len(window.studies) == 3 and all(row.result and row.result.converged for row in window.studies)
        export_study(window.studies, output / "sweep", sweep.parameter)
        screenshot("07-sweep.png")
        record("Parameter sweep through GUI with stored fields", currents=[row.result.metrics["total_current"] for row in window.studies])
        variant = deepcopy(window.project)
        variant.name += " — thicker lower copper"
        variant.layers[0].thickness *= 1.5
        compute("compare", [deepcopy(window.project), variant])
        assert len(window.studies) == 2 and all(row.result and row.result.converged for row in window.studies)
        export_study(window.studies, output / "comparison", "Design")
        record("Compare two technology designs", resistance=[row.result.metrics["effective_resistance"] for row in window.studies])
        window.show_result(window.studies[0].result)
        wait_idle(require_geometry=True)
        window.field_layer.setCurrentIndex(window.field_layer.findData(layer_b))
        window.field_combo.setCurrentIndex(window.field_combo.findData("temperature"))
        export_results(window.result, output / "numerical", plots=False)
        export_view(window.view, output / "temperature.svg", window.legend.text())
        export_view(window.view, output / "temperature.png", window.legend.text())
        export_html_report(window.project, window.result, window.view, output / "report.html", window.legend.text())
        window.path = output / "acceptance.etlayout"
        assert window.save()
        wait_idle()
        saved_current = window.result.metrics["total_current"]
        window.open_project(window.path)
        wait_idle(require_geometry=True)
        assert window.result and window.result.metrics["total_current"] == saved_current
        record("Export numerical data / vector map / report; save, close and reopen project", project=str(window.path))
        window.load_example("E7_hemt")
        wait_idle(require_geometry=True)
        window.guided.analysis.setCurrentIndex(window.guided.analysis.findData("electrothermal"))
        compute()
        window.field_layer.setCurrentIndex(window.field_layer.findData("channel"))
        window.field_combo.setCurrentIndex(window.field_combo.findData("temperature"))
        screenshot("08-hemt-temperature.png")
        current_on = window.result.metrics["total_current"]
        peak_on = window.result.metrics["temperature_max"]
        edit_object("terminals", "gate_control", {"value": -5.})
        compute()
        current_off = window.result.metrics["total_current"]
        assert current_off < current_on
        assert peak_on > 300.
        record("GaN HEMT gate-dependent channel conduction and self-heating", current_at_0V_gate_A=current_on,
               current_at_minus5V_gate_A=current_off, on_state_temperature_max_K=peak_on)
        window.path = output / "hemt.etlayout"
        assert window.save()
        wait_idle()
        record("Acceptance complete", completed=True)
        return 0
    except Exception as error:
        failures.append(str(error))
        record("Acceptance failed", completed=False)
        raise
    finally:
        if window.job:
            window.cancel_job()
        window.cancel_geometry()
        window.autosave_timer.stop()
        window.cancel_saving()
        # Cancelling is asynchronous: let the writer close its temporary archive
        # before window destruction or Python's temporary-directory cleanup.
        cleanup_started = time.monotonic()
        while window.save_job or window._save_queue:
            if time.monotonic() - cleanup_started > 30.:
                raise TimeoutError("Cancelled desktop save did not finish within 30 seconds")
            wait_desktop_events(40)
        window.dirty = False
        window.close()
        application.processEvents()


if __name__ == "__main__":
    raise SystemExit(main())
