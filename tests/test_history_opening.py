# SPDX-License-Identifier: MPL-2.0
"""Real worker/copy/generation tests; no native user preferences are accessed."""
from __future__ import annotations

import threading
import zipfile
from pathlib import Path

import numpy as np
import pytest
from PySide6.QtCore import QTimer

from etlayout.gui import history_opening
from etlayout.gui.history_opening import HistoryOpener
from etlayout.io import project as project_io
from etlayout.io.resources import ExternalResourcePolicy
from etlayout.models import PlanarMesh, Project, SimulationResult


def archive(tmp_path, name, *, external=False, version=3):
    folder = tmp_path / name
    folder.mkdir()
    project = Project(name=name)
    if external:
        layout = tmp_path / "external.gds"
        layout.write_bytes((Path(__file__).parents[1] / "examples" / "E1_straight.gds").read_bytes())
        project.layout.path, project.layout.sha256 = str(layout), project_io.file_hash(layout)
    mesh = PlanarMesh(np.array([[0., 0.], [1., 0.], [0., 1.]]), np.array([[0, 1, 2]]), {"layer:a": np.array([0])})
    result = SimulationResult(mesh, fields={"a": {"temperature": np.array([300., 301., 302.])}},
        configuration=project.to_dict(), configuration_hash=project_io.configuration_hash(project),
        physics_hash=project_io.physics_hash(project), converged=True)
    target = folder / "project.etl"
    project_io.save_project(project, target, result, archive_format=version, embed_layout=not external)
    return target


def _make_opener(qtbot):
    control = HistoryOpener()
    received, reviews, failures = [], [], []
    control.loaded.connect(lambda *values: received.append(values))
    control.review_required.connect(lambda *values: reviews.append(values))
    control.failed.connect(lambda *values: failures.append(values))
    return control, received, reviews, failures


@pytest.fixture
def opening(qtbot):
    owned = []
    def create():
        item = _make_opener(qtbot)
        owned.append(item)
        return item
    yield create
    for control, received, _, _ in owned:
        assert control.shutdown()
        qtbot.waitUntil(lambda control=control: not control.jobs, timeout=30000)
        for _, _, store in received:
            if store is not None:
                store.close()


def test_worker_opens_catalog_without_array_reads_and_releases_cancelled_generation(qtbot, tmp_path, monkeypatch, opening):
    target = archive(tmp_path, "metadata-first")
    reads = []
    original = zipfile.ZipFile.open
    def observed(self, name, *args, **kwargs):
        text = name.filename if isinstance(name, zipfile.ZipInfo) else name
        if text.endswith(".npy"):
            reads.append(text)
        return original(self, name, *args, **kwargs)
    monkeypatch.setattr(zipfile.ZipFile, "open", observed)
    control, received, reviews, failures = opening()
    request = control.submit(target, "document-a")
    qtbot.waitUntil(lambda: bool(received or failures), timeout=30000)
    assert not failures and not reviews and not reads
    assert received[0][0] == request and received[0][1].name == "metadata-first"
    store = received[0][2]
    assert store.stats.resident_bytes == 0 and store.records[0].available
    store.close()
    assert control.shutdown()
    qtbot.waitUntil(lambda: not control.jobs)


def test_resource_approval_retries_exact_reviewed_generation_before_external_reads(qtbot, tmp_path, monkeypatch, opening):
    target = archive(tmp_path, "requires-review", external=True)
    external_hashes = []
    original = project_io.file_hash
    def observed(path):
        if Path(path).name == "external.gds":
            external_hashes.append(path)
        return original(path)
    monkeypatch.setattr(project_io, "file_hash", observed)
    control, received, reviews, failures = opening()
    control.submit(target, "document-a")
    qtbot.waitUntil(lambda: bool(reviews or failures), timeout=30000)
    assert not failures and not received and not external_hashes
    _, inspection, error = reviews.pop()
    assert error.reason == "outside-root" and inspection.archive_data is None
    assert inspection._archive_sha256 and all(item.location for item in inspection.resources)
    policy = ExternalResourcePolicy(approved_resources=frozenset(item.key for item in inspection.resources))
    control.submit(target, "document-a", policy=policy, inspection=inspection)
    qtbot.waitUntil(lambda: bool(received or failures), timeout=30000)
    assert not failures and not reviews and external_hashes
    store = received.pop()[2]
    with store.acquire(store.current_run_id) as result:
        np.testing.assert_array_equal(result.fields["a"]["temperature"], [300., 301., 302.])
    store.close()
    # A new file at the reviewed path never inherits those resource grants.
    changed = archive(tmp_path, "different-generation")
    target.write_bytes(changed.read_bytes())
    external_hashes.clear()
    control.submit(target, "document-a", policy=policy, inspection=inspection)
    qtbot.waitUntil(lambda: bool(failures), timeout=30000)
    assert "generation differs" in str(failures[0][1]) and not external_hashes
    assert not received and not reviews
    assert control.shutdown()
    qtbot.waitUntil(lambda: not control.jobs)


def test_rapid_open_and_cancel_keep_gui_heartbeat_and_close_stale_private_copy(qtbot, tmp_path, monkeypatch, opening):
    first, second = archive(tmp_path, "slow"), archive(tmp_path, "latest")
    entered, release = threading.Event(), threading.Event()
    copied = []
    original = history_opening.open_history_catalog
    def delayed(path, **options):
        store = original(path, **options)
        if Path(path) == first:
            copied.append(store)
            entered.set()
            release.wait(10)
        return store
    monkeypatch.setattr(history_opening, "open_history_catalog", delayed)
    control, received, reviews, failures = opening()
    ticks = []
    timer = QTimer()
    timer.setInterval(5)
    timer.timeout.connect(lambda: ticks.append(1))
    timer.start()
    try:
        control.submit(first, "document-a")
        qtbot.waitUntil(entered.is_set, timeout=30000)
        qtbot.wait(60)
        assert len(ticks) >= 2  # The blocking operation is on the worker.
        latest = control.submit(second, "document-a")
        qtbot.waitUntil(lambda: bool(received or failures), timeout=30000)
        assert not failures and received[0][0] == latest and received[0][1].name == "latest"
        release.set()
        qtbot.waitUntil(lambda: not control.jobs, timeout=30000)
        assert len(received) == 1 and copied[0].stats.closed
        assert not copied[0]._snapshot_path.exists()
        received[0][2].close()
        assert not reviews
    finally:
        timer.stop()
        release.set()
        assert control.shutdown()
        qtbot.waitUntil(lambda: not control.jobs, timeout=30000)


@pytest.mark.parametrize("version,lazy", [(2, False), (3, False), (2, True)])
def test_eager_and_legacy_decode_actual_fields_in_worker(qtbot, tmp_path, monkeypatch, opening, version, lazy):
    target = archive(tmp_path, f"schema-{version}-{lazy}", version=version)
    main_thread = threading.get_ident()
    decoded_on = []
    original = history_opening.resolve_project
    def observed(*args, **options):
        decoded_on.append(threading.get_ident())
        return original(*args, **options)
    monkeypatch.setattr(history_opening, "resolve_project", observed)
    control, received, reviews, failures = opening()
    request = control.submit(target, "document-a", lazy=lazy)
    qtbot.waitUntil(lambda: bool(received or failures), timeout=30000)
    assert not failures and not reviews
    assert decoded_on and all(item != main_thread for item in decoded_on)
    assert received[0][0] == request and received[0][2] is None
    result = received[0][1]._opening_result
    np.testing.assert_array_equal(result.fields["a"]["temperature"], [300., 301., 302.])
    assert control.shutdown()
    qtbot.waitUntil(lambda: not control.jobs)


def test_metadata_only_review_can_resume_eager_without_granting_another_generation(
        qtbot, tmp_path, opening):
    from etlayout.io.history_store import open_history_catalog

    target = archive(tmp_path, "reviewed-eager", external=True)
    policy = ExternalResourcePolicy(allow_outside_root=True)
    store = open_history_catalog(target, external_policy=policy)
    try:
        inspection = history_opening.catalog_inspection(store)
    finally:
        store.close()
    control, received, reviews, failures = opening()
    control.submit(target, "document-a", policy=policy, inspection=inspection, lazy=False)
    qtbot.waitUntil(lambda: bool(received or failures), timeout=30000)
    assert not reviews and not failures and received[0][2] is None
    np.testing.assert_array_equal(received[0][1]._opening_result.fields["a"]["temperature"], [300., 301., 302.])
    received.clear()
    target.write_bytes(archive(tmp_path, "replacement-generation").read_bytes())
    control.submit(target, "document-a", policy=policy, inspection=inspection, lazy=False)
    qtbot.waitUntil(lambda: bool(failures), timeout=30000)
    assert "generation differs" in str(failures[0][1]) and not received
    assert control.shutdown()
    qtbot.waitUntil(lambda: not control.jobs)
