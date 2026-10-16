# SPDX-License-Identifier: MPL-2.0
"""Real solved transient fields, compact/maximized workspace and Qt scale checks.

Captures/logs are disposable CI artifacts. Qt scaling is not physical monitor-DPI
or multi-monitor certification. Host font verification precedes every capture.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import platform
import subprocess
import time
from pathlib import Path


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=Path("artifacts/desktop-workspace"))
    parser.add_argument("--scale", type=float, default=1.)
    parser.add_argument("--offscreen", action="store_true")
    args = parser.parse_args(argv)
    os.environ["QT_SCALE_FACTOR"] = str(args.scale)
    if args.offscreen:
        os.environ["QT_QPA_PLATFORM"] = "offscreen"
    else:
        os.environ.pop("QT_QPA_PLATFORM", None)
    from desktop_acceptance_support import prepare_desktop_fonts
    from PySide6.QtCore import QCoreApplication, QEvent, QPointF, QSettings, Qt
    from PySide6.QtTest import QTest
    from PySide6.QtWidgets import QApplication

    from etlayout.examples import make_example
    from etlayout.gui.main_window import MainWindow
    from etlayout.io.project import configuration_hash, physics_hash
    from etlayout.simulation.runner import run_project

    started = time.monotonic()
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=True)
    QSettings.setDefaultFormat(QSettings.Format.IniFormat)
    QSettings.setPath(QSettings.Format.IniFormat, QSettings.Scope.UserScope, str(output / "settings"))
    app = QApplication.instance() or QApplication([])
    app.setStyle("Fusion")
    prepare_desktop_fonts(app, output)
    root = Path(__file__).resolve().parents[1]
    digest = hashlib.sha256()
    for source in sorted((root / "src").rglob("*.py")) + [Path(__file__).resolve(), Path(__file__).with_name("desktop_acceptance_support.py")]:
        digest.update(source.relative_to(root).as_posix().encode() + b"\0" + source.read_bytes() + b"\0")
    receipt = {"revision": subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip(),
        "source_tree_sha256": digest.hexdigest(), "platform": platform.platform(), "qpa": app.platformName(),
        "qt_scale_factor": args.scale, "captures": [], "checks": [],
        "limitations": "QtTest widgets and Qt application scaling; no screen-reader, physical DPI, clean-machine or multi-monitor certification."}
    window = None

    def record(name, **data):
        receipt["checks"].append({"name": name, "elapsed_seconds": time.monotonic() - started, **data})
        (output / "receipt.json").write_text(json.dumps(receipt, indent=2), encoding="utf-8")
        print(name, flush=True)

    def settle():
        QTest.qWait(70)
        app.processEvents()

    def capture(name):
        settle()
        picture = window.grab()
        assert picture.save(str(output / (name + ".png")))
        receipt["captures"].append({"file": name + ".png", "window_logical": [window.width(), window.height()],
            "image_pixels": [picture.width(), picture.height()], "dpr": window.devicePixelRatioF(),
            "canvas_logical": [window.view.width(), window.view.height()],
            "available_screen_logical": [window.screen().availableGeometry().width(), window.screen().availableGeometry().height()]})

    def identity():
        view = window.view
        return (window.result, window.mesh, window.transient_frame, window.time_navigation.slider.value(),
            window.field_layer.currentData(), window.field_combo.currentData(), window.field_region.currentData(),
            view.field_colormap, view.field_log, view.field_limits, window.legend_scale.capture_state(),
            configuration_hash(window.project), physics_hash(window.project),
            window.guided.document.revision, tuple(window.run_history))

    try:
        project = make_example("E19", output / "inputs")
        project.layers[0].name = "Receiver metal — deliberately long physical layer name for compact inspector navigation and material identification"
        project.materials[project.layers[0].material_id].name = "Synthetic reference material — deliberately long material description with engineering unit symbols µm and Ω"
        result = run_project(project)
        assert result.converged and result.transient.completed
        record("Actual configured E19 multilayer transient solved", snapshots=len(result.transient.snapshots))
        window = MainWindow(project, result, database_path=output / "materials.sqlite3", recover=False)
        window.recovery_path = output / "recovery.etlayout"
        window.autosave_timer.stop()
        window.resize(1280, 800)
        window.show()
        window.activateWindow()
        deadline = time.monotonic() + 90
        while window.prepare_job:
            assert time.monotonic() < deadline
            QTest.qWait(30)
        guided = window.guided
        guided.panels.restore_default()
        window.time_navigation.slider.setValue(40)
        window.colormap_combo.setCurrentIndex(window.colormap_combo.findData("viridis"))
        window.legend_scale.minimum.setText("302.2 K")
        window.legend_scale.maximum.setText("302.4 K")
        window.legend_scale.lock.setChecked(True)
        assert window.view.overlay_item is not None and window.transient_frame is result.transient.snapshots[40]
        before = identity()
        for dark in (False, True):
            window.apply_theme(dark)
            for size in ((1280, 800), (1920, 1080)):
                window.resize(*size)
                guided.panels.restore_default()
                settle()
                assert (window.width(), window.height()) == size
                prefix = f"{size[0]}-" + ("dark" if dark else "light")
                capture(prefix + "-panels")
                window.view.scale(4, 4)
                window.view.centerOn(QPointF(30, -4))
                settle()
                transform = window.view.transform()
                center = window.view.mapToScene(window.view.viewport().rect().center())
                old_area = window.view.viewport().width() * window.view.viewport().height()
                guided.actions["view.maximize_canvas"].trigger()
                settle()
                new_area = window.view.viewport().width() * window.view.viewport().height()
                assert new_area > old_area * 1.3
                assert identity() == before and window.view.transform() == transform
                capture(prefix + "-canvas")
                assert all(not guided.panels.visible(name) for name in guided.panels.widgets)
                assert guided.left_toggle.isVisible() and guided.right_toggle.isVisible() and guided.drawer_toggle.isVisible()
                guided.actions["view.maximize_canvas"].trigger()
                settle()
                restored = window.view.mapToScene(window.view.viewport().rect().center())
                assert abs(restored.x() - center.x()) * transform.m11() <= 2
                assert abs(restored.y() - center.y()) * transform.m22() <= 2
                assert identity() == before
                record(prefix + " camera and scientific state preserved", canvas_area_ratio=new_area / old_area)
                window.view.fit_all()
        guided.panels.maximize_canvas()
        window.view.fit_all()
        capture("1920-dark-canvas-explicit-fit")
        guided.panels.maximize_canvas()
        guided.panels.focus_canvas()
        QTest.keyClick(window.view, Qt.Key.Key_R, Qt.KeyboardModifier.ControlModifier | Qt.KeyboardModifier.ShiftModifier)
        settle()
        assert not guided.panels.visible("right") and window.view.hasFocus()
        QTest.mouseClick(guided.right_toggle, Qt.MouseButton.LeftButton)
        assert guided.panels.visible("right")
        for index in (0, 1, 3):
            guided.panels.set_visible("drawer", False)
            guided.open_drawer(index)
            assert guided.drawer.currentIndex() == index and guided.drawer.isVisible()
        assert identity() == before
        record("Keyboard, restore controls, Issues Jobs History and transient identity retained")
        selected = window.field_region.findData("interface:receiver_substrate")
        assert selected > 0
        window.field_region.setCurrentIndex(selected)
        selected_state = identity()
        guided.panels.maximize_canvas()
        guided.panels.maximize_canvas()
        assert identity() == selected_state and window.field_region.currentData() == "interface:receiver_substrate"
        window.field_region.setCurrentIndex(0)
        record("Explicit receiver-interface region retained through panel changes")
        guided.navigate("layers")
        guided.select_mapping("heater")
        settle()
        assert guided.editor.form.widgets["name"].text() == project.layers[0].name
        assert "deliberately long material description" in guided.editor.form.widgets["material_id"].currentText()
        capture("long-layer-and-material-names")
        name_field = guided.editor.form.widgets["name"]
        name_field.setFocus()
        QTest.keyClick(name_field, Qt.Key.Key_F, Qt.KeyboardModifier.ControlModifier | Qt.KeyboardModifier.ShiftModifier)
        assert window.view.hasFocus() and not guided.editor.has_changes()
        record("Focus canvas keyboard shortcut remains available inside a long-name inspector field")
        guided.navigate("results")
        assert window.transient_frame is result.transient.snapshots[40]
        state = guided.panels.capture()
        guided.panels.set_visible("left", False)
        assert guided.panels.restore_state(state)
        assert not guided.panels.restore_state('{"version":999}')
        assert all(guided.panels.visible(name) for name in guided.panels.widgets)
        record("Local settings restored; corrupt preferences recover to default")
        receipt["status"] = "passed"
        receipt["elapsed_seconds"] = time.monotonic() - started
        (output / "receipt.json").write_text(json.dumps(receipt, indent=2), encoding="utf-8")
    finally:
        if window is not None:
            window.cancel_geometry()
            window.guided.discard_draft()
            window.dirty = False
            window.close()
            window.deleteLater()
            QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
            app.processEvents()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
