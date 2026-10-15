# SPDX-License-Identifier: MPL-2.0
"""Exercise actual process isolation, cancellation and CPU-budget propagation."""
from __future__ import annotations

import json
import multiprocessing as mp
import os
import time

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import psutil
from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QFileDialog, QMessageBox

from etlayout.examples import make_example
from etlayout.gui.jobs import ComputeJob
from etlayout.io.project import load_project, project_from_dict, save_project
from etlayout.models import SweepSpec
from etlayout.simulation.execution import ExecutionSettings
from etlayout.simulation.runner import run_project


def _sleeping_child():
    time.sleep(60)


def _coordinator(messages):
    context = mp.get_context("spawn")
    children = [context.Process(target=_sleeping_child) for _ in range(2)]
    for child in children:
        child.start()
    messages.put(("progress", (.5, "Children started")))
    for child in children:
        child.join()


def test_compute_job_parallel_points_keep_gui_live(qtbot, tmp_path):
    project = make_example("E1", tmp_path / "inputs")
    budget = min(4, os.cpu_count() or 1)
    job = ComputeJob(project, "sweep", SweepSpec("terminals.drain.value", [.1, .2], "electrical"),
                     execution=ExecutionSettings(cpu_threads=budget, study_workers=min(2, budget)))
    finished, errors, ticks = [], [], []
    job.finished.connect(finished.append)
    job.failed.connect(lambda *error: errors.append(error))
    timer = QTimer()
    timer.setInterval(10)
    timer.timeout.connect(lambda: ticks.append(time.monotonic()))
    timer.start()
    try:
        job.start()
        qtbot.waitUntil(lambda: job.done, timeout=90000)
        assert not errors, errors
        assert len(ticks) >= 5
        assert len(finished[0]) == 2
        assert all(row.result and row.result.converged for row in finished[0])
        assert [row.value for row in finished[0]] == [.1, .2]
        assert all(row.result.execution["cpu_threads"] <= budget for row in finished[0])
    finally:
        timer.stop()
        if not job.done:
            job.cancel()


def test_cancel_terminates_owned_descendants(qtbot, tmp_path):
    job = ComputeJob(make_example("E1", tmp_path / "inputs"))
    job.process = mp.get_context("spawn").Process(target=_coordinator, args=(job.messages,))
    started = []
    job.progress.connect(lambda *_: started.append(True))
    try:
        job.start()
        qtbot.waitUntil(lambda: bool(started), timeout=30000)
        descendants = psutil.Process(job.process.pid).children(recursive=True)
        assert len(descendants) >= 2
        job.cancel()
        assert not job.process.is_alive()
        qtbot.waitUntil(lambda: all(not child.is_running() for child in descendants), timeout=5000)
    finally:
        if not job.done:
            job.cancel()


def test_execution_provenance_is_independent_and_portable(tmp_path):
    project = make_example("E1", tmp_path / "inputs")
    one = run_project(project, execution=ExecutionSettings(cpu_threads=1, study_workers=1))
    two = run_project(project, execution=ExecutionSettings(cpu_threads=min(2, os.cpu_count() or 1), study_workers=1))
    assert one.physics_hash == two.physics_hash
    assert one.execution["cpu_threads"] == 1
    assert two.execution["cpu_threads"] == min(2, os.cpu_count() or 1)
    path = tmp_path / "result.etl"
    save_project(project, path, two)
    _, loaded = load_project(path)
    assert loaded.execution == two.execution


def test_source_bound_preset_requires_explicit_choice(qtbot, tmp_path, monkeypatch):
    from etlayout.gui.main_window import MainWindow
    project = make_example("E17", tmp_path / "inputs")
    config = project.to_dict()
    config["transient"].update(initialization="saved", initial_state_digest="a" * 64)
    path = tmp_path / "preset.json"
    path.write_text(json.dumps({key: config[key] for key in ("mesh", "solver", "transient")}), encoding="utf-8")
    window = MainWindow(project, database_path=tmp_path / "materials.sqlite3", recover=False)
    window.recovery_path = tmp_path / "recovery.etl"
    qtbot.addWidget(window, before_close_func=lambda widget: setattr(widget, "dirty", False))
    qtbot.waitUntil(lambda: window.prepare_job is None, timeout=30000)
    errors, prompts = [], []
    window.error = lambda *error: errors.append(error)
    monkeypatch.setattr(QFileDialog, "getOpenFileName", lambda *_args, **_kwargs: (str(path), ""))
    monkeypatch.setattr(QMessageBox, "exec", lambda dialog: prompts.append(dialog.text()))
    monkeypatch.setattr(QMessageBox, "clickedButton", lambda _dialog: None)
    window.load_preset()
    assert prompts and not errors
    assert window.project.transient.initialization == "uniform"
    assert not window.dirty
    monkeypatch.setattr(QMessageBox, "clickedButton", lambda dialog: next(
        button for button in dialog.buttons() if button.text() == "Use steady initialization"))
    window.load_preset()
    assert not errors
    assert window.project.transient.initialization == "steady"
    assert not window.project.transient.initial_state_digest


def test_preset_rebinds_matching_archived_initial_field(qtbot, tmp_path, monkeypatch):
    from etlayout.gui.main_window import MainWindow
    project = make_example("E17", tmp_path / "inputs")
    project.transient.stop_time = 300e-6
    source = run_project(project)
    warm = run_project(project, initial_result=source)
    recorded = project_from_dict(warm.configuration)
    path = tmp_path / "warm.etl"
    save_project(recorded, path, warm)
    preset = tmp_path / "preset.json"
    preset.write_text(json.dumps({key: warm.configuration[key] for key in ("mesh", "solver", "transient")}), encoding="utf-8")
    window = MainWindow(project, database_path=tmp_path / "materials.sqlite3", recover=False)
    window.recovery_path = tmp_path / "recovery.etl"
    qtbot.addWidget(window, before_close_func=lambda widget: setattr(widget, "dirty", False))
    qtbot.waitUntil(lambda: window.prepare_job is None, timeout=30000)
    errors = []
    window.error = lambda *error: errors.append(error)
    paths = iter([str(preset), str(path)])
    monkeypatch.setattr(QFileDialog, "getOpenFileName", lambda *_args, **_kwargs: (next(paths), ""))
    monkeypatch.setattr(QMessageBox, "exec", lambda _dialog: None)
    monkeypatch.setattr(QMessageBox, "clickedButton", lambda dialog: next(
        button for button in dialog.buttons() if button.text() == "Choose source archive…"))
    window.load_preset()
    assert not errors
    assert window.project.transient.initial_state_digest == recorded.transient.initial_state_digest
    assert window.initial_state_argument()["initial_result"] is not None


def test_solve_waits_for_preparation_and_source_identity(qtbot, tmp_path):
    from etlayout.gui.main_window import MainWindow
    project = make_example("E1", tmp_path / "inputs")
    project.layout.sha256 = ""
    window = MainWindow(project, database_path=tmp_path / "materials.sqlite3", recover=False)
    window.recovery_path = tmp_path / "recovery.etl"
    qtbot.addWidget(window, before_close_func=lambda widget: setattr(widget, "dirty", False))
    errors = []
    window.error = lambda *error: errors.append(error)
    assert window.prepare_job is not None
    window.run()
    assert window.job is None and window._pending_compute is not None
    qtbot.waitUntil(lambda: window.prepare_job is None and window.job is None, timeout=60000)
    assert not errors
    assert window.result is not None and window.result.converged
    assert window.inspection_context is None
    assert window.project.layout.sha256 == window.result.configuration["layout"]["sha256"]
