# SPDX-License-Identifier: MPL-2.0
"""Owned completed workers release their native Qt graph after real shutdown."""
from PySide6.QtCore import QCoreApplication, QEvent, QObject

from etlayout.examples import make_example
from etlayout.gui.jobs import ComputeJob


def test_completed_parented_compute_jobs_do_not_accumulate_native_children(qtbot, tmp_path):
    project = make_example("E1", tmp_path / "layout")
    parent = QObject()
    jobs = []
    for _ in range(3):
        job = ComputeJob(project, "prepare", parent=parent)
        errors = []
        job.failed.connect(lambda message, detail, output=errors: output.append((message, detail)))
        jobs.append(job)
        job.start()
        qtbot.waitUntil(lambda current=job: current.done, timeout=30000)
        qtbot.waitUntil(lambda current=job: not current.process.is_alive() and not current._receiver.is_alive(), timeout=5000)
        assert not errors
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    qtbot.waitUntil(lambda: not parent.findChildren(ComputeJob), timeout=5000)
    assert not parent.findChildren(ComputeJob)
    assert all(not job.process.is_alive() and not job._receiver.is_alive() for job in jobs)
