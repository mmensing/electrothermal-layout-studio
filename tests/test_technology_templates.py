# SPDX-License-Identifier: MPL-2.0
"""Portable physical mapping proposals, real solves and explicit Qt application."""
from __future__ import annotations

import json
import os
from copy import deepcopy
from dataclasses import asdict

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtCore import QCoreApplication, QEvent, QSettings, Qt, QTimer
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QMessageBox

from etlayout.examples import make_example
from etlayout.gui.main_window import MainWindow
from etlayout.gui.technology_templates import (
    TechnologyTemplateDialog,
    load_template,
    propose_template,
    save_template,
)
from etlayout.io.project import configuration_hash, load_project, save_project
from etlayout.models import Interface, Material, PhysicalLayer, Project, RegionSpec, SimulationError
from etlayout.simulation.runner import run_project


@pytest.fixture
def template_file(tmp_path):
    project = Project(name="Two-level copper technology", materials={"copper": Material("copper", "Copper")},
                      layers=[PhysicalLayer("lower", "Lower route", "copper", 2e-6, 0., geometry=RegionSpec(sources=["1/0"])),
                              PhysicalLayer("upper", "Upper route", "copper", 3e-6, 4e-6, geometry=RegionSpec(sources=["2/0"]))],
                      interfaces=[Interface("via", "lower", "upper", RegionSpec(sources=["3/0"]),
                                            electrical_kind="specific", electrical_value=2e-12)])
    path = tmp_path / "stack.etltech.json"
    save_template(project, path)
    return project, path


def test_portable_mapping_roundtrip_keeps_material_models_contacts_and_provenance(template_file):
    source, path = template_file
    before = source.to_dict()
    source.layers[0].electrical.gate_regions = ["project-specific-gate"]
    save_template(source, path)
    template = load_template(path)
    assert template.layers[0].electrical.gate_regions == []
    assert template.omitted_gate_bindings == {"lower": ["project-specific-gate"]}
    assert source.layers[0].electrical.gate_regions == ["project-specific-gate"]
    assert asdict(template.interfaces[0]) == before["interfaces"][0]
    assert asdict(template.materials["copper"]) == before["materials"]["copper"]
    assert len(template.sha256) == 64
    payload = json.loads(path.read_text())
    assert not {"terminals", "thermal_boundaries", "heat_sources", "solver", "transient"} & payload.keys()


@pytest.mark.parametrize("problem", ["version", "thickness", "missing_material", "role", "unknown", "endpoint", "nonfinite_geometry"])
def test_invalid_template_cannot_be_proposed(template_file, problem):
    _, path = template_file
    data = json.loads(path.read_text())
    if problem == "version":
        data["version"] = True
    elif problem == "thickness":
        data["layers"][0]["thickness"] = -1
    elif problem == "missing_material":
        data["materials"] = {}
    elif problem == "role":
        data["layers"][0]["role"] = "rf_magic"
    elif problem == "unknown":
        data["layers"][0]["unknown"] = True
    elif problem == "nonfinite_geometry":
        data["layers"][0]["geometry"]["polygons"] = [{"exterior": [[float('nan'), 0.]], "holes": []}]
    else:
        data["interfaces"][0]["layer_b"] = "gone"
    path.write_text(json.dumps(data))
    with pytest.raises((ValueError, SimulationError)):
        load_template(path)


def test_proposal_remaps_masks_without_overwriting_layers_materials_or_operating_conditions(template_file):
    source, path = template_file
    source.layers[0].geometry.exclude_sources = ['4/0']
    source.interfaces[0].geometry.exclude_sources = ['4/0']
    save_template(source, path)
    working = deepcopy(source)
    working.materials["copper"].resistivity.value *= 2
    before = working.to_dict()
    mapping = {"1/0": "8/0", "2/0": "9/0", "3/0": "10/0", "4/0": "11/0"}
    with pytest.raises(ValueError, match='4/0'):
        propose_template(working, load_template(path), mapping, ['lower', 'upper'], ['8/0', '9/0', '10/0'])
    proposal = propose_template(working, load_template(path), mapping,
                                ["lower", "upper"], ["8/0", "9/0", "10/0", "11/0", "99/0"])
    assert working.to_dict() == before
    assert [layer.id for layer in proposal.project.layers] == ["lower", "upper", "lower_2", "upper_2"]
    assert proposal.project.layer("lower_2").material_id == "copper_2"
    assert proposal.project.layer("lower_2").geometry.sources == ["8/0"]
    assert proposal.project.layer("lower_2").geometry.exclude_sources == ['11/0']
    assert proposal.project.interfaces[-1].geometry.exclude_sources == ['11/0']
    assert proposal.project.interfaces[-1].id == "via_2"
    assert (proposal.project.interfaces[-1].layer_a, proposal.project.interfaces[-1].layer_b) == ("lower_2", "upper_2")
    assert proposal.project.materials["copper"].resistivity.value == before["materials"]["copper"]["resistivity"]["value"]
    assert proposal.project.materials["copper_2"].resistivity.value == source.materials["copper"].resistivity.value
    assert proposal.project.terminals == working.terminals
    assert "99/0" in proposal.summary
    assert load_template(path).sha256 in proposal.project.notes


def test_partial_selection_omits_dependent_contacts_and_requires_explicit_target_masks(template_file):
    _, path = template_file
    template = load_template(path)
    proposal = propose_template(Project(), template, {"1/0": "7/0"}, ["lower"], ["7/0", "99/0"])
    assert len(proposal.project.layers) == 1
    assert proposal.project.interfaces == []
    assert "Contacts omitted because their layers were not selected: via" in proposal.summary
    for mapping, selected in [({}, ["lower"]), ({"1/0": "gone"}, ["lower"]), ({"1/0": "7/0"}, [])]:
        with pytest.raises(ValueError):
            propose_template(Project(), template, mapping, selected, ["7/0"])


def test_applied_template_reproduces_real_strip_resistance_and_archive_preserves_provenance(tmp_path):
    source = make_example("E1", tmp_path / "source")
    path = tmp_path / "strip.etltech.json"
    save_template(source, path)
    template = load_template(path)
    target = Project(name="Explicitly mapped strip", layout=deepcopy(source.layout), mesh=deepcopy(source.mesh))
    masks = {key for layer in template.layers for key in layer.geometry.sources}
    proposal = propose_template(target, template, {key: key for key in masks}, [layer.id for layer in template.layers], masks)
    assert not proposal.project.terminals  # A technology does not invent electrical inputs.
    proposal.project.terminals = deepcopy(source.terminals)
    result = run_project(proposal.project)
    assert result.converged
    # E1: rho=1.68e-8 ohm m, length=100 um, width=10 um, thickness=2 um.
    assert result.metrics["resistance"] == pytest.approx(1.68e-8 * 100e-6 / (10e-6 * 2e-6), rel=1e-7)
    archive = tmp_path / "mapped.etlayout"
    save_project(proposal.project, archive, result)
    reopened, stored = load_project(archive)
    assert template.sha256 in reopened.notes
    assert stored.configuration_hash == result.configuration_hash
    assert reopened.schema_version == source.schema_version


@pytest.fixture
def window(qtbot, tmp_path):
    QSettings.setDefaultFormat(QSettings.Format.IniFormat)
    QSettings.setPath(QSettings.Format.IniFormat, QSettings.Scope.UserScope, str(tmp_path / "settings"))
    project = make_example("E1", tmp_path / "example")
    w = MainWindow(project, database_path=tmp_path / "materials.sqlite3", recover=False)
    w.autosave_timer.stop()
    w.recovery_path = tmp_path / "recovery.etlayout"
    w.show()
    qtbot.waitUntil(lambda: not w.prepare_job, timeout=30000)
    yield w
    w.cancel_geometry()
    w.dirty = False
    w.close()
    w.deleteLater()
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    QApplication.processEvents()


def _answer(standard_button):
    def respond():
        prompt = QApplication.activeModalWidget()
        assert isinstance(prompt, QMessageBox)
        QTest.mouseClick(prompt.button(standard_button), Qt.MouseButton.LeftButton)
    QTimer.singleShot(0, respond)


def test_qt_proposal_needs_preview_and_confirmation_then_commits_once_with_undo(window, qtbot, tmp_path):
    path = tmp_path / "mapping.etltech.json"
    save_template(window.project, path)
    before = window.project.to_dict()
    undo_count = len(window.undo_history)
    dialog = TechnologyTemplateDialog(window.guided)
    qtbot.addWidget(dialog)
    dialog.show()
    dialog.set_template(load_template(path))
    assert not dialog.apply_button.isEnabled()
    assert window.project.to_dict() == before
    dialog.preview()
    assert dialog.apply_button.isEnabled()
    _answer(QMessageBox.StandardButton.Cancel)
    QTest.mouseClick(dialog.apply_button, Qt.MouseButton.LeftButton)
    assert window.project.to_dict() == before
    _answer(QMessageBox.StandardButton.Apply)
    QTest.mouseClick(dialog.apply_button, Qt.MouseButton.LeftButton)
    qtbot.waitUntil(lambda: not window.prepare_job, timeout=30000)
    assert len(window.project.layers) == 2 * len(before["layers"])
    assert len(window.undo_history) == undo_count + 1
    window.undo()
    qtbot.waitUntil(lambda: not window.prepare_job, timeout=30000)
    assert window.project.to_dict() == before


def test_qt_changed_selection_invalidates_preview_and_new_working_edit_prevents_apply(window, qtbot, tmp_path):
    path = tmp_path / "mapping.etltech.json"
    save_template(window.project, path)
    dialog = TechnologyTemplateDialog(window.guided)
    qtbot.addWidget(dialog)
    dialog.set_template(load_template(path))
    dialog.preview()
    dialog.layers.item(0, 0).setCheckState(Qt.CheckState.Unchecked)
    assert not dialog.apply_button.isEnabled()
    dialog.layers.item(0, 0).setCheckState(Qt.CheckState.Checked)
    dialog.preview()
    window.project.name = "A newer working setup"
    before = configuration_hash(window.project)
    dialog.apply_proposal()
    assert not dialog.apply_button.isEnabled()
    assert "changed" in dialog.summary.toPlainText()
    assert configuration_hash(window.project) == before


def test_qt_template_button_is_reachable_and_cancel_does_not_change_project(window, qtbot):
    window.guided.navigate("layers")
    from PySide6.QtWidgets import QPushButton
    action = next(control for control in window.guided.page.findChildren(QPushButton)
                  if control.text() == "Technology templates…")
    original = configuration_hash(window.project)

    def cancel():
        dialog = QApplication.activeModalWidget()
        assert isinstance(dialog, TechnologyTemplateDialog)
        QTest.keyClick(dialog, Qt.Key.Key_Escape)

    QTimer.singleShot(0, cancel)
    window.guided.page_scroll.ensureWidgetVisible(action)
    QTest.mouseClick(action, Qt.MouseButton.LeftButton)
    assert configuration_hash(window.project) == original
