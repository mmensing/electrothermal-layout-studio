# SPDX-License-Identifier: MPL-2.0
"""Native retained-reference regressions run in disposable, ordinary Qt children.

These use supported Qt APIs and Python finalizers, not native memory inspection.
A failed lifetime invariant can terminate the child; pytest must remain alive to
report it. The release runtime source patch fixes the underlying Shiboken map.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import textwrap

import pytest

pytestmark = pytest.mark.desktop

_CHILD = textwrap.dedent("""
    import gc
    import json
    import sys
    import threading
    import weakref

    from PySide6.QtCore import QCoreApplication, QEvent
    from PySide6.QtWidgets import QApplication, QStyledItemDelegate, QTreeView

    case = sys.argv[1]
    before = gc.get_threshold()
    assert gc.isenabled()
    app = QApplication([])
    view = QTreeView()
    finalized, errors, inner = [], [], []
    requested, collected = threading.Event(), threading.Event()
    worker_ids = []

    def collect_in_worker():
        for _ in range(2):
            if not requested.wait(5):
                errors.append("Worker collection was not requested")
                return
            requested.clear()
            worker_ids.append(threading.get_ident())
            gc.collect()
            collected.set()

    worker = threading.Thread(target=collect_in_worker) if case == "clear_worker" else None
    if worker is not None:
        worker.start()

    class Delegate(QStyledItemDelegate):
        def __init__(self, name):
            super().__init__()
            self.name = name

        def __del__(self):
            try:
                finalized.append(self.name)
                if case.startswith("clear") and len(finalized) > 1:
                    # Ordinary allocations make reuse of an already released
                    # wrapper visible. These objects are local, bounded and
                    # discarded when this finalizer returns.
                    allocations = [bytes([i % 255]) * 24 for i in range(1000)]
                    if worker is not None:
                        requested.set()
                        assert collected.wait(5), "Worker collection did not finish"
                        collected.clear()
                    else:
                        gc.collect()
                    assert len(allocations) == 1000
                elif self.name == "old":
                    replacement = Delegate("inner")
                    inner.append(weakref.ref(replacement))
                    view.setItemDelegate(replacement)
            except BaseException as error:
                errors.append(str(error))

    if case.startswith("clear"):
        for column in range(3):
            view.setItemDelegateForColumn(column, Delegate(str(column)))
        view.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        if worker is not None:
            worker.join(5)
            assert not worker.is_alive()
            assert len(worker_ids) == 2 and all(identifier != threading.get_ident() for identifier in worker_ids)
        assert sorted(finalized) == ["0", "1", "2"], finalized
    else:
        view.setItemDelegate(Delegate("old"))
        view.setItemDelegate(None if case == "remove" else Delegate("outer"))
        gc.collect()
        assert len(inner) == 1 and inner[0]() is not None, (finalized, inner)
        assert view.itemDelegate() is inner[0](), "Reentrant setter lost its retained reference"
        assert view.itemDelegate().name == "inner"
        view.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        assert finalized.count("old") == 1 and finalized.count("inner") == 1, finalized
        if case == "replace":
            assert finalized.count("outer") == 1, finalized

    assert not errors, errors
    assert gc.isenabled() and gc.get_threshold() == before
    print(json.dumps({"case": case, "finalized": finalized, "errors": errors,
                      "gc_enabled": gc.isenabled(), "thresholds": before,
                      "worker_collection": bool(worker_ids)}), flush=True)
""")


@pytest.mark.parametrize("case", ["clear_gui", "clear_worker", "remove", "replace"])
def test_retained_qt_references_survive_reentrant_collection_and_setters(case):
    environment = dict(os.environ, QT_QPA_PLATFORM="offscreen")
    process = subprocess.run([sys.executable, "-X", "faulthandler", "-c", _CHILD, case],
                             env=environment, capture_output=True, text=True, timeout=30, check=False)
    assert process.returncode == 0, (
        f"{case}: Qt child exited {process.returncode}\n{process.stdout}\n{process.stderr}")
    report = json.loads(process.stdout)
    assert report["case"] == case and report["gc_enabled"] and not report["errors"]
    assert report["worker_collection"] is (case == "clear_worker")
