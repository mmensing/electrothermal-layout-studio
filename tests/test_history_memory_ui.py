# SPDX-License-Identifier: MPL-2.0
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import numpy as np
from PySide6.QtCore import Qt

from etlayout.gui.workspace import RunHistoryPanel, retained_result_bytes
from etlayout.models import PlanarMesh, SimulationResult, TransientHistory


def test_memory_estimate_counts_shared_frame_arrays_only_once(qtbot):
    mesh = PlanarMesh(np.zeros((1000, 2)), np.zeros((1000, 3), dtype=int), {})
    values = np.arange(1000.)
    frame = SimulationResult(mesh, fields={"m": {"temperature": values}}, converged=True)
    run = SimulationResult(mesh, fields=frame.fields, transient=TransientHistory(np.array([0.]), [frame]))
    one = retained_result_bytes([run])
    two = retained_result_bytes([run, run])
    assert one >= mesh.points.nbytes + mesh.triangles.nbytes + values.nbytes
    assert two < one + 32
    panel = RunHistoryPanel()
    qtbot.addWidget(panel)
    panel.set_results([run])
    assert "1 retained runs" in panel.memory_summary.text()
    assert "MiB" in panel.list.item(0).text()
    panel.show()
    panel.list.setCurrentRow(0)
    with qtbot.waitSignal(panel.inspect_requested) as signal:
        qtbot.keyClick(panel.list, Qt.Key.Key_Return)
    assert signal.args[0] is run
