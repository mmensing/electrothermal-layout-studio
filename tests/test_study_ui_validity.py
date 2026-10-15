# SPDX-License-Identifier: MPL-2.0
"""Study status and plotted validity agree for failures and pulsed operation."""
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import numpy as np
from PySide6.QtCore import Qt

from etlayout.gui.plots import StudyPanel
from etlayout.models import PlanarMesh, SimulationResult
from etlayout.simulation.sweep import SweepRow


def result(value, converged=True):
    mesh = PlanarMesh(np.zeros((3, 2)), np.array([[0, 1, 2]]), {})
    return SimulationResult(mesh, metrics={"temperature_max": value - 10, "transient_peak_temperature": value,
                                          "transient_steps": 100, "transient_peak_retained": True}, converged=converged)


def test_failed_metrics_are_marked_and_break_accepted_trend(qtbot):
    panel = StudyPanel()
    qtbot.addWidget(panel)
    panel.set_rows([SweepRow(.1, result(302)), SweepRow(.05, result(370, False), "Solver did not converge"),
                    SweepRow(.025, result(304))], "Maximum time step (s)")
    assert panel.table.horizontalHeaderItem(0).text() == "Status"
    assert panel.table.item(1, 0).text() == "Not converged"
    assert panel.table.horizontalHeaderItem(1).text() == "Requested (SI)"
    assert "Maximum time step" in panel.table.horizontalHeaderItem(1).toolTip()
    assert panel.table.columnWidth(1) <= 150
    assert panel.table.item(1, 2).text() == "370"
    assert "1 failed" in panel.summary.text()
    accepted, rejected = panel.plot.listDataItems()
    x, y = accepted.getData()
    np.testing.assert_allclose(x, [.025, .05, .1])
    assert np.isnan(y[1])
    np.testing.assert_allclose(rejected.getData()[1], [370])
    assert accepted.opts["connect"] == "finite"
    assert rejected.opts["symbol"] == "x"


def test_missing_failed_result_keeps_gap_and_pulse_sweep_defaults_to_peak(qtbot):
    panel = StudyPanel()
    qtbot.addWidget(panel)
    panel.set_rows([SweepRow(1., result(320)), SweepRow(2., error="Meshing failed"), SweepRow(3., result(340))], "Pulse high current")
    assert panel.metric_combo.currentData() == "transient_peak_temperature"
    assert panel.table.item(1, 0).text() == "Failed"
    assert np.isnan(panel.plot.listDataItems()[0].getData()[1][1])
    assert panel.metric_combo.findData("transient_steps") < 0
    panel.all_metrics.setChecked(True)
    assert panel.metric_combo.findData("transient_steps") >= 0
    assert panel.metric_combo.findData("transient_peak_retained") < 0


def test_enter_inspects_selected_study_result(qtbot):
    panel = StudyPanel()
    qtbot.addWidget(panel)
    source = result(320)
    panel.set_rows([SweepRow(1., source)])
    panel.show()
    panel.table.setCurrentCell(0, 0)
    with qtbot.waitSignal(panel.result_selected) as signal:
        qtbot.keyClick(panel.table, Qt.Key.Key_Return)
    assert signal.args[0] is source
