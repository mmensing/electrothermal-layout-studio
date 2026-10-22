# SPDX-License-Identifier: MPL-2.0
"""Observable workflow clarity without replacing numerical or draft contracts."""
from copy import deepcopy
from types import SimpleNamespace

import pytest
from gui_test_helpers import _answer_dialog, _fill, _idle
from gui_test_helpers import guided_window as guided_window
from gui_test_helpers import solved_e1 as solved_e1
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication

from etlayout.gui.forms import ModelDialog
from etlayout.gui.multiport_workflow import MultiportWorkflow, extraction_prerequisites
from etlayout.gui.result_summary import result_headlines
from etlayout.models import Interface, PhysicalLayer, PhysicalPort, PortLanding, Project


def test_result_headlines_use_practical_temperature_precision_without_changing_values():
    metrics = {"temperature_max": 300.014, "total_current": 5., "electrical_power": .1, "voltage_drop": .02}
    before = deepcopy(metrics)
    text = result_headlines(SimpleNamespace(metrics=metrics))
    assert "300.0 K" in text and "Delivered current: 5 A" in text
    assert "Voltage drop: 20 mV" in text
    assert metrics == before
    assert result_headlines(None) == "No selected result"


@pytest.mark.parametrize("temperature", [None, float("nan"), float("inf")])
def test_unavailable_legacy_temperature_remains_readable(temperature):
    assert "Peak temperature: Unavailable" in result_headlines(SimpleNamespace(metrics={"temperature_max": temperature}))


@pytest.mark.parametrize("model, name, expected", [
    (PhysicalLayer("metal", "Metal", "copper"), "thickness", "2000 nm"),
    (PhysicalLayer("metal", "Metal", "copper"), "z", "Bottom elevation"),
    (Interface("via", "a", "b", electrical_kind="specific"), "electrical_value", "Ω m²"),
    (Interface("via", "a", "b", thermal_kind="resistance"), "thermal_value", "m² K/W"),
    (Interface("via", "a", "b"), "layer_a", "layer A to layer B"),
])
def test_entry_and_label_hover_help_explain_model_units(qtbot, model, name, expected):
    form = ModelDialog(model)
    qtbot.addWidget(form)
    assert expected in form.widgets[name].toolTip()
    assert form.labels[name].toolTip() == form.widgets[name].toolTip()
    assert form.widgets[name].accessibleDescription() == form.widgets[name].toolTip()
    if name == "electrical_value":
        form.widgets["electrical_kind"].setCurrentIndex(form.widgets["electrical_kind"].findData("lumped"))
        assert "Input unit: Ω;" in form.widgets[name].toolTip()


def test_results_headlines_precede_controls_and_cameras_restore_by_workflow(guided_window, solved_e1, qtbot):
    window = guided_window
    _, result = solved_e1
    window.show_result(result)
    guided = window.guided
    QApplication.processEvents()
    assert guided.result_metrics.y() < window.result_controls.y()
    assert "Delivered current" in guided.result_metrics.text()
    assert "Selected run" in guided.result_scope.text()
    applied = window.project.to_dict()
    assert not guided.verification_toggle.isChecked()
    assert guided.verify_mesh.isEnabled() and not guided.verify_mesh.isVisible()
    qtbot.mouseClick(guided.verification_toggle, Qt.MouseButton.LeftButton)
    assert guided.verify_mesh.isVisible() and guided.verify_mesh.isEnabled()
    qtbot.mouseClick(guided.verification_toggle, Qt.MouseButton.LeftButton)
    assert not guided.verify_mesh.isVisible() and guided.verify_mesh.isEnabled()
    assert window.project.to_dict() == applied and window.result is result
    center = window.view.mapFromScene(window.view.overlay_item.sceneBoundingRect().center())
    assert window.view.viewport().rect().contains(center)
    window.view.scale(2, 2)
    result_transform = window.view.transform()
    guided.navigate("layout")
    window.view.scale(.6, .6)
    setup_transform = window.view.transform()
    guided.navigate("results")
    QApplication.processEvents()
    assert window.view.transform() == result_transform
    guided.navigate("layers")
    assert window.view.transform() == setup_transform
    _idle(window, qtbot)


def test_stack_edit_stay_preserves_applied_selection_and_draft(guided_window):
    window = guided_window
    guided = window.guided
    guided.navigate("layers")
    selected = guided.selected_layer
    _fill(window, "thickness", "3.7")
    editor = guided.editor
    before = window.project.to_dict()
    _answer_dialog("Stay")
    guided.select_mapping(selected)
    assert guided.editor is editor and editor.has_changes()
    assert guided.selected_layer == selected and window.project.to_dict() == before
    guided.discard_draft()


def test_stack_connection_opens_unapplied_shared_draft(guided_window):
    window = guided_window
    guided = window.guided
    other = deepcopy(window.project.layers[0])
    other.id, other.name, other.z = "upper", "Upper metal", window.project.layers[0].z + window.project.layers[0].thickness
    guided.document.transact("Add upper sheet", lambda project: project.layers.append(other))
    guided.navigate("layers")
    before = window.project.to_dict()
    guided.connect_stack_layers(window.project.layers[0].id, "upper", "contact")
    assert guided.stage == "connections" and guided.editor_key == "interfaces"
    assert guided.editor_is_new and not window.project.interfaces
    assert window.project.to_dict() == before
    assert guided.editor.form.widgets["layer_b"].currentData() == "upper"
    guided.discard_draft()


def test_multiport_selection_and_reference_remain_view_only_and_drop_stale_preview(qtbot):
    project = Project()
    project.power.ports = [PhysicalPort("in", [PortLanding("a", .01)]),
                           PhysicalPort("out", [PortLanding("b")], kind="ground"),
                           PhysicalPort("sense", [PortLanding("c")], kind="floating")]
    before = project.to_dict()
    view = MultiportWorkflow()
    qtbot.addWidget(view)
    view.set_project(project)
    assert view.selected_ports() == ["in", "out", "sense"]
    view.reference.setCurrentIndex(view.reference.findData("out"))
    view.preview.setPlainText("prior extracted network")
    view.ports.item(2, 0).setCheckState(Qt.CheckState.Unchecked)
    assert view.selected_ports() == ["in", "out"]
    assert view.reference.currentData() == "out" and not view.preview.toPlainText()
    assert project.to_dict() == before
    view.ports.item(1, 0).setCheckState(Qt.CheckState.Unchecked)
    assert "at least two" in view.selection_note.text()


def test_multiport_prerequisites_give_actionable_passive_model_guidance():
    project = Project()
    assert "physical ports" in extraction_prerequisites(project)[0]
    layer = PhysicalLayer("channel", "Channel", "copper")
    layer.electrical.kind = "hemt"
    project.layers.append(layer)
    assert "separate passive interconnect project" in " ".join(extraction_prerequisites(project))


def test_first_different_historical_run_does_not_reuse_working_camera(guided_window, solved_e1, qtbot, tmp_path):
    from etlayout.examples import make_example
    from etlayout.simulation.runner import run_project

    window = guided_window
    _, working_result = solved_e1
    window.show_result(working_result)
    window.view.scale(20, 20)
    working_zoom = window.view.transform()
    window.guided.navigate("layout")
    historical = run_project(make_example("E4", tmp_path / "other-layout"))
    assert historical.converged
    window.inspect_result(historical)
    _idle(window, qtbot)
    assert window.inspection_context is not None
    assert window.view.transform() != working_zoom
    assert window.result is historical
    window.return_to_project()
    _idle(window, qtbot)
    window.guided.navigate("results")
    assert window.view.transform() == working_zoom


def test_shared_physical_edit_retires_network_while_view_and_navigation_preserve_it(guided_window, qtbot):
    window = guided_window
    guided = window.guided
    guided.document.transact("Declare physical ports", lambda project: setattr(project.power, "ports", [
        PhysicalPort("in", [PortLanding("drain", .01)], value=.02),
        PhysicalPort("out", [PortLanding("source")], kind="ground")]))
    _idle(window, qtbot)
    guided.open_studies("multiport")
    power = guided.power
    power.edit_multiport_ports()
    assert power.setup_kind.currentData() == "ports"
    power.open_multiport()
    power.extract_multiport()
    qtbot.waitUntil(lambda: power.job is None, timeout=90000)
    assert power.multiport_report is not None, power.status.text()
    report = power.multiport_report
    guided.document.transact("Hide a layer", lambda project: setattr(project.layers[0], "visible", False))
    _idle(window, qtbot)
    guided.open_studies("multiport")
    assert power.multiport_report is report
    assert power.network_export.isEnabled() and power.multiport_workflow.preview.toPlainText()
    guided.document.transact("Change physical thickness", lambda project: setattr(
        project.layers[0], "thickness", project.layers[0].thickness * 1.1))
    _idle(window, qtbot)
    assert power.multiport_report is None
    assert not power.network_export.isEnabled() and not power.multiport_workflow.preview.toPlainText()
    window.undo()
    _idle(window, qtbot)
    assert power.multiport_report is None
