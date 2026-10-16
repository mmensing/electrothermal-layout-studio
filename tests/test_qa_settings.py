# SPDX-License-Identifier: MPL-2.0
"""QA preferences and smoke ownership without opening a GUI or native settings."""
from __future__ import annotations

import ast
import tempfile
from pathlib import Path
from types import SimpleNamespace

import pytest
from PySide6 import QtCore, QtWidgets

from etlayout.app import main as desktop
from etlayout.gui import editor_smoke, layout_editor, main_window
from etlayout.models import Project


def test_explicit_qa_factory_never_changes_qt_class_or_defaults(tmp_path):
    original, default = QtCore.QSettings, QtCore.QSettings.defaultFormat()
    factory = editor_smoke.acceptance_settings_factory(tmp_path / "first")
    settings = factory("etlayout", "Electrothermal Layout Studio")
    assert type(settings) is original and settings.format() == original.Format.IniFormat
    assert not settings.fallbacksEnabled()
    assert Path(settings.fileName()).resolve() == tmp_path / "first/settings/application.ini"
    settings.setValue("snap-grid", 13)
    settings.sync()
    assert factory().value("snap-grid", type=int) == 13
    other = editor_smoke.acceptance_settings_factory(tmp_path / "second")()
    assert not other.contains("snap-grid")
    assert QtCore.QSettings is original and original.defaultFormat() == default


def test_every_standalone_window_runner_uses_explicit_settings():
    root = Path(__file__).resolve().parents[1]
    runners = []
    for path in (root / "scripts").glob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        window_lines = [node.lineno for node in ast.walk(tree) if isinstance(node, ast.Call)
                        and isinstance(node.func, ast.Name) and node.func.id == "MainWindow"]
        if not window_lines:
            continue
        isolated = [node.lineno for node in ast.walk(tree) if isinstance(node, ast.Call)
                    and isinstance(node.func, ast.Name) and node.func.id == "isolate_acceptance_settings"]
        assert isolated and min(isolated) < min(window_lines), path.name
        assert not any(isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                       and node.func.attr in {"setDefaultFormat", "setPath"} for node in ast.walk(tree)), path.name
        runners.append(path.name)
    assert {"gui_acceptance.py", "gui_comparison_smoke.py", "reporting_performance_acceptance.py"} <= set(runners)


@pytest.fixture
def simulated_desktop(monkeypatch, tmp_path):
    """Exercise entry-point ownership only; no calculated/GUI result is fabricated."""
    from etlayout.io import project as archive

    state = {"callbacks": [], "windows": [], "disposed": False}
    actual_tempdir = tempfile.TemporaryDirectory
    monkeypatch.setattr(tempfile, "TemporaryDirectory", lambda **kwargs: actual_tempdir(dir=tmp_path, **kwargs))
    monkeypatch.setattr(QtCore.QTimer, "singleShot", lambda delay, callback: state["callbacks"].append(callback))

    class Application:
        @staticmethod
        def instance():
            return None
        def __init__(self, args):
            self.code = 37
        def setApplicationName(self, value):
            pass
        def setOrganizationName(self, value):
            pass
        def setStyle(self, value):
            pass
        def exec(self):
            while state["callbacks"]:
                state["callbacks"].pop(0)()
            return self.code
        def exit(self, code):
            self.code = code

    class Window:
        def __init__(self, project, result, *, database_path, recover):
            self.root = Path(database_path).parent if database_path is not None else None
            self.settings = (main_window.QSettings("etlayout", "Electrothermal Layout Studio") if self.root
                             else SimpleNamespace(value=lambda key: None))
            if self.root:
                assert Path(self.settings.fileName()).resolve().is_relative_to(self.root)
                Path(database_path).write_bytes(b"owned QA database placeholder")
                data = Path(main_window.QStandardPaths.writableLocation(
                    main_window.QStandardPaths.StandardLocation.AppLocalDataLocation))
                assert data.is_relative_to(self.root)
                data.mkdir(parents=True, exist_ok=True)
                (data / "owned-recovery-placeholder").write_text("owned QA recovery", encoding="utf-8")
            self.guided = SimpleNamespace(layout_editor=SimpleNamespace(cache_dir=None))
            self.autosave_timer = SimpleNamespace(stop=lambda: None)
            self.prepare_job = self.job = None
            self.hierarchy_jobs = []
            self.history_panel = SimpleNamespace(set_results=lambda values: None)
            state["windows"].append(self)
        def show(self):
            if self.root:
                assert self.recovery_path.parent == self.root
                assert self.guided.layout_editor.cache_dir.parent == self.root
                assert Path(tempfile.gettempdir()).is_relative_to(self.root)

    def dispose(window, application):
        assert window.root.exists()
        window.settings.sync()
        state["disposed"] = True

    monkeypatch.setattr(QtWidgets, "QApplication", Application)
    monkeypatch.setattr(main_window, "MainWindow", Window)
    monkeypatch.setattr(desktop, "_close_smoke_window", dispose)
    monkeypatch.setattr(archive, "load_project", lambda *args, **kwargs: (Project(), None))
    monkeypatch.setattr(archive, "load_run_history", lambda *args, **kwargs: [])
    monkeypatch.setattr(editor_smoke, "run_editor_smoke", lambda *args, **kwargs: True)
    return state


@pytest.mark.parametrize("editor", [False, True], ids=["generic-temporary", "editor-retained"])
def test_smoke_local_data_outlives_shutdown_and_returns_actual_code(simulated_desktop, tmp_path, editor):
    before = (main_window.QSettings, layout_editor.QSettings, main_window.QStandardPaths,
              layout_editor.QStandardPaths, tempfile.tempdir, QtCore.QSettings, QtCore.QStandardPaths)
    args = ["--smoke-test"]
    if editor:
        args += ["fixture.etl", "--editor-smoke-output", str(tmp_path / "explicit")]
    assert desktop.main(args) == 0
    state = simulated_desktop
    assert state["disposed"]
    root = state["windows"][0].root
    assert root.exists() == editor
    assert (main_window.QSettings, layout_editor.QSettings, main_window.QStandardPaths,
            layout_editor.QStandardPaths, tempfile.tempdir, QtCore.QSettings, QtCore.QStandardPaths) == before


def test_ordinary_startup_keeps_original_preferences_and_exit_status(simulated_desktop):
    before = main_window.QSettings, layout_editor.QSettings, tempfile.tempdir
    assert desktop.main([]) == 37
    assert not simulated_desktop["disposed"] and simulated_desktop["windows"][0].root is None
    assert (main_window.QSettings, layout_editor.QSettings, tempfile.tempdir) == before


def test_failed_owned_shutdown_preserves_temporary_files(simulated_desktop, monkeypatch):
    before = main_window.QSettings, layout_editor.QSettings, tempfile.tempdir
    def failed(window, application):
        raise RuntimeError("Owned worker still active")
    monkeypatch.setattr(desktop, "_close_smoke_window", failed)
    with pytest.raises(RuntimeError, match="Owned worker"):
        desktop.main(["--smoke-test"])
    assert simulated_desktop["windows"][0].root.is_dir()
    assert (main_window.QSettings, layout_editor.QSettings, tempfile.tempdir) == before


def test_owned_smoke_worker_shutdown_precedes_window_deletion(monkeypatch):
    events = []
    process = SimpleNamespace(pid=42, join=lambda **kwargs: events.append("joined"), is_alive=lambda: False)
    receiver = SimpleNamespace(join=lambda **kwargs: events.append("receiver joined"), is_alive=lambda: False)
    job = SimpleNamespace(process=process, _receiver=receiver, cancel=lambda: events.append("cancelled"),
                          cleanup=lambda: events.append("cleaned"))
    window = SimpleNamespace(autosave_timer=SimpleNamespace(stop=lambda: None), cancel_job=lambda: None,
        cancel_geometry=lambda: None, findChildren=lambda kind: [job], guided=SimpleNamespace(discard_draft=lambda: None),
        inspection_context=None, close=lambda: events.append("closed") or True,
        settings=SimpleNamespace(sync=lambda: events.append("synced")), deleteLater=lambda: events.append("deleted"))
    monkeypatch.setattr(QtCore.QCoreApplication, "sendPostedEvents", lambda *args: events.append("deferred deletion"))
    desktop._close_smoke_window(window, SimpleNamespace(processEvents=lambda: events.append("events")))
    assert events.index("joined") < events.index("closed") < events.index("deleted")
    assert events.index("receiver joined") < events.index("closed")


def test_qa_standard_paths_are_local_without_changing_qt(tmp_path):
    original = QtCore.QStandardPaths
    paths = editor_smoke.acceptance_standard_paths(tmp_path)
    for location in (original.StandardLocation.AppLocalDataLocation, original.StandardLocation.AppDataLocation,
                     original.StandardLocation.CacheLocation):
        assert Path(paths.writableLocation(location)).is_relative_to(tmp_path)
    with pytest.raises(RuntimeError, match="Unexpected"):
        paths.writableLocation(original.StandardLocation.HomeLocation)
    assert QtCore.QStandardPaths is original
