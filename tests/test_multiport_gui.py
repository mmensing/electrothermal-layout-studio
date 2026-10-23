# SPDX-License-Identifier: MPL-2.0
"""Observable passive-extraction controls use real fields and worker operations."""
import json
from copy import deepcopy

import numpy as np
import pytest
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QPushButton
from test_multiport_study import passive_e1

from etlayout.gui.multiport_workflow import MultiportWorkflow
from etlayout.gui.power_workbench import PowerWorkbench
from etlayout.power.multiport import extract_multiport
from etlayout.simulation.execution import ExecutionSettings, execution_context
from etlayout.simulation.runner import prepare_mesh, run_project


@pytest.fixture(scope="module")
def passive_thermal_reference(tmp_path_factory):
    project = passive_e1(tmp_path_factory.mktemp("passive-thermal"))
    project.solver.mode = "electrothermal"
    with execution_context(ExecutionSettings(cpu_threads=1, study_workers=1)):
        result = run_project(project)
        geometry, mesh = prepare_mesh(deepcopy(project))
        report = extract_multiport(project, mesh)
    assert result.converged
    return project, result, geometry, report


def test_temperature_controls_and_selected_run_retire_all_stale_network_evidence(qtbot, passive_thermal_reference):
    project, result, geometry, report = deepcopy(passive_thermal_reference)
    before = deepcopy(project.to_dict())
    view = MultiportWorkflow()
    qtbot.addWidget(view)
    view.set_project(project)
    view.set_result(result)
    view.show_report(report)
    view.set_geometry(geometry, project.layers)
    view.locate_ports(["positive"])
    assert view.mapping.annotations and view.links.rowCount() == 1
    view.temperature_mode.setCurrentIndex(view.temperature_mode.findData("uniform"))
    view.temperature_value.setText("350 K")
    assert view.temperature_value.isEnabled()
    assert view.extraction_options()["temperature_K"] == 350.
    assert not view.preview.toPlainText() and not view.mapping.annotations and view._geometry is None
    assert view.links.rowCount() == 0
    assert project.to_dict() == before
    view.temperature_mode.setCurrentIndex(view.temperature_mode.findData("retained"))
    assert view.extraction_options()["result"] is result
    assert "exact mesh" in view.temperature_note.text()
    view.show_report(report)
    view.set_geometry(geometry, project.layers)
    view.set_result(deepcopy(result))
    assert not view.preview.toPlainText() and view._geometry is None
    assert project.to_dict() == before


def test_port_and_equivalent_link_crossprobe_only_exact_verified_landing_support(qtbot, passive_thermal_reference):
    project, _result, geometry, report = deepcopy(passive_thermal_reference)
    before = deepcopy(project.to_dict())
    view = MultiportWorkflow()
    qtbot.addWidget(view)
    view.resize(1000, 850)
    view.set_project(project)
    view.show_report(report)
    view.set_geometry(geometry, project.layers)
    view.show()
    view.ports.selectRow(0)
    locate = next(button for button in view.findChildren(QPushButton) if button.text() == "Locate selected port")
    qtbot.mouseClick(locate, Qt.MouseButton.LeftButton)
    assert view.pages.currentIndex() == 1
    assert view.mapping_note.text() == "Highlighted physical landings: positive"
    path = view.mapping.annotations[-1].path().boundingRect()
    x0, y0, x1, y1 = geometry.terminals["drain"].bounds
    assert [path.left(), path.top(), path.right(), path.bottom()] == pytest.approx(
        [x0 * 1e6, -y1 * 1e6, x1 * 1e6, -y0 * 1e6])
    view.links.selectRow(0)
    assert "positive, negative" in view.mapping_note.text()
    assert len(view.mapping.annotations) == 1
    assert "distributed conductive path" in view.pages.widget(1).findChildren(type(view.mapping_note))[0].text()
    assert project.to_dict() == before


def test_real_mesh_worker_bundle_and_retained_mesh_rejection_preserve_the_working_setup(
        qtbot, tmp_path, monkeypatch, passive_thermal_reference):
    project, result, _geometry, _report = deepcopy(passive_thermal_reference)
    before = deepcopy(project.to_dict())
    window = PowerWorkbench(project, result, execution=ExecutionSettings(cpu_threads=1, study_workers=1))
    qtbot.addWidget(window)
    window.show()
    window.open_multiport()
    view = window.multiport_workflow
    view.temperature_mode.setCurrentIndex(view.temperature_mode.findData("uniform"))
    view.temperature_value.setText("350 K")
    view.mesh_sizes.setText("20, 10, 5")
    window.run_multiport_mesh()
    assert window.job is not None and not view.isEnabled()
    qtbot.waitUntil(lambda: window.job is None, timeout=90000)
    assert window.multiport_report is not None, window.status.text()
    study = json.loads(view.mesh_details.toPlainText())
    assert study["status"] == "verified" and study["mesh_verified"]
    assert "Extraction mesh evidence: verified" in view.mesh_evidence.toPlainText()
    assert study["network"]["temperature"]["temperature_K"] == 350.
    assert view.mapping.layer_items and window.network_export.isEnabled()
    fine = window.multiport_report
    assert fine["source_physics_hash"] != fine["physics_hash"]
    assert project.to_dict() == before
    bundle = tmp_path / "actual-validation-bundle"
    # Only the human folder chooser is supplied; the real worker performs the
    # deterministic bundle generation and no simulator is substituted.
    monkeypatch.setattr("etlayout.gui.power_workbench.QFileDialog.getExistingDirectory", lambda *_: str(bundle))
    window.validate_multiport(False)
    qtbot.waitUntil(lambda: window.job is None, timeout=90000)
    validation = json.loads(view.simulator_details.toPlainText())
    assert validation["status"] == "prepared", window.status.text()
    assert "No simulator was run" in view.simulator_evidence.toPlainText()
    assert (bundle / "network.cir").read_text() == view.preview.toPlainText()
    assert len(validation["cases"]) == 2
    assert project.to_dict() == before
    view.temperature_mode.setCurrentIndex(view.temperature_mode.findData("retained"))
    assert window.multiport_report is None and not window.network_export.isEnabled()
    geometry_points = result.mesh.points.copy()
    window.extract_multiport()
    qtbot.waitUntil(lambda: window.job is None, timeout=90000)
    assert window.multiport_report is not None, window.status.text()
    retained = window.multiport_report
    assert retained["temperature"]["kind"] == "fixed_spatial_fields"
    np.testing.assert_array_equal(result.mesh.points, geometry_points)
    window.run_multiport_mesh()
    qtbot.waitUntil(lambda: window.job is None, timeout=90000)
    assert "do not project spatial temperature" in window.status.text()
    assert window.multiport_report is retained  # The still-valid original-mesh extraction remains usable.
    np.testing.assert_array_equal(result.mesh.points, geometry_points)
    assert project.to_dict() == before
