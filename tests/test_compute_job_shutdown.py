# SPDX-License-Identifier: MPL-2.0
"""Real spawned-worker messages must survive a timeout immediately before exit."""
from __future__ import annotations

import multiprocessing as mp
import os
import queue
import traceback

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

from etlayout.gui.jobs import ComputeJob
from etlayout.models import PhysicalLayer, Project, RegionSpec


def _finish_geometry_after_release(messages, project, ready, release, outcome):
    """Calculate first, then allow the receiver to observe the shutdown race."""
    from etlayout.geometry.prepare import prepare_geometry

    try:
        geometry = prepare_geometry(project)
        terminal = ("result", geometry)
    except Exception as error:
        terminal = ("error", (str(error), traceback.format_exc()))
    ready.set()
    if not release.wait(30):
        os._exit(9)
    if outcome == "crash":
        os._exit(7)
    messages.put(("progress", (.9, "Geometry preparation finished")))
    messages.put(terminal)
    # Normal process shutdown joins the Queue feeder, so both messages have
    # reached the pipe before the parent observes exit code zero.


@pytest.mark.parametrize("outcome", ["result", "error", "crash"])
def test_final_message_after_queue_timeout_and_worker_exit(qtbot, monkeypatch, outcome):
    project = Project()
    if outcome != "error":
        project.layers = [PhysicalLayer("metal", "Metal", "copper", geometry=RegionSpec(
            polygons=[{"exterior": [[0., 0.], [10e-6, 0.], [10e-6, 2e-6], [0., 2e-6]]}]))]
    job = ComputeJob(project, "prepare")
    context = mp.get_context("spawn")
    ready, release = context.Event(), context.Event()
    job.process = context.Process(target=_finish_geometry_after_release,
                                  args=(job.messages, project, ready, release, outcome))
    finished, errors, progress, transitions = [], [], [], []
    job.finished.connect(finished.append)
    job.failed.connect(lambda *error: errors.append(error))
    job.progress.connect(lambda *value: progress.append(value))
    original_get = job.messages.get

    def yield_between_timeout_and_exit_check(*args, **kwargs):
        try:
            return original_get(*args, **kwargs)
        except queue.Empty:
            if not transitions:
                # The actual Queue has timed out. Hold this receiver thread at
                # that boundary while a real child sends its result and exits;
                # this models a scheduler pause without fabricating messages.
                prepared = ready.wait(30)
                release.set()
                job.process.join(timeout=10)
                transitions.append((prepared, job.process.exitcode))
            raise

    monkeypatch.setattr(job.messages, "get", yield_between_timeout_and_exit_check)
    try:
        job.start()
        qtbot.waitUntil(lambda: job.done, timeout=45000)
        assert transitions == [(True, 7 if outcome == "crash" else 0)]
        if outcome == "result":
            assert not errors, errors
            assert len(finished) == 1
            assert finished[0].layers["metal"].area == pytest.approx(20e-12)
            assert progress == [(.9, "Geometry preparation finished")]
        elif outcome == "error":
            assert not finished
            assert len(errors) == 1
            assert "No simulation extent" in errors[0][0]
            assert "prepare_geometry" in errors[0][1]
            assert progress == [(.9, "Geometry preparation finished")]
        else:
            assert not finished
            assert errors == [("Worker exited unexpectedly (code 7).", "")]
    finally:
        release.set()
        if not job.done:
            job.cancel()
