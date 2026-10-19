# SPDX-License-Identifier: MPL-2.0
"""Observable thermal-stack roles, deposition editing and template identity."""
from copy import deepcopy

import pytest

from etlayout.geometry.prepare import prepare_geometry
from etlayout.gui.forms import ModelDialog
from etlayout.gui.plots import CrossSectionPanel
from etlayout.gui.technology_templates import load_template, propose_template, save_template
from etlayout.materials.database import reference_materials
from etlayout.models import Interface, PhysicalLayer, Project, RegionSpec, ThermalBoundary


def project_stack():
    domain = RegionSpec(polygons=[dict(bounds=[0, 0, 10e-6, 10e-6])])
    project = Project(materials=reference_materials(), layers=[
        PhysicalLayer("bottom", "Lower copper", "copper", 1e-6, 0., geometry=deepcopy(domain)),
        PhysicalLayer("film", "SiN passivation", "sin", 1e-6, 1e-6, "insulator", deepcopy(domain)),
        PhysicalLayer("top", "Upper copper", "copper", 1e-6, 2e-6, geometry=deepcopy(domain))],
        interfaces=[Interface("lower", "bottom", "film", deepcopy(domain), thermal_kind="stack"),
                    Interface("upper", "film", "top", deepcopy(domain), thermal_kind="stack")],
        thermal_boundaries=[ThermalBoundary("backside", "bottom", deepcopy(domain), kind="temperature", value=300.)])
    return project


def test_cross_section_explains_thermal_only_roles_properties_and_boundary(qtbot):
    project = project_stack()
    panel = CrossSectionPanel()
    qtbot.addWidget(panel)
    panel.resize(1100, 700)
    panel.show_cut(project, prepare_geometry(project), [0, 5e-6], [10e-6, 5e-6])
    panel.show()
    assert panel.layer_table.rowCount() == 3
    assert panel.layer_table.item(1, 1).text() == "insulator (thermal-only)"
    assert "3 / 3 / 3" in panel.layer_table.item(1, 5).text()
    assert "backside" in panel.stack_summary.text() and "300 K" in panel.stack_summary.text()
    assert "Thickness-averaged 2.5D" in panel.stack_summary.text()
    assert panel.intervals["film"] == pytest.approx([(0., 10e-6)])


def test_interface_form_edits_heat_depth_and_rejects_ambiguous_split(qtbot):
    model = Interface("device", "a", "b", electrical_kind="load")
    dialog = ModelDialog(model, choices={"heat_layer_id": {"": "Endpoint partition", "semiconductor": "Semiconductor thermal sheet"}})
    qtbot.addWidget(dialog)
    dialog.widgets["heat_layer_id"].setCurrentIndex(1)
    dialog.widgets["heat_to_layer_a_fraction"].setText("0.25")
    assert dialog.parse_candidate() is None
    assert "Reset to 0.5" in dialog.errors["heat_to_layer_a_fraction"].text()
    dialog.widgets["heat_to_layer_a_fraction"].setText("0.5")
    dialog.accept()
    assert dialog.model.heat_layer_id == "semiconductor"
    assert model.heat_layer_id == ""


def test_template_remaps_heat_depth_and_omits_link_when_deposition_layer_not_selected(tmp_path):
    source = project_stack()
    source.interfaces = [Interface("device", "bottom", "top", electrical_kind="load", heat_layer_id="film")]
    filename = tmp_path / "thermal-template.json"
    save_template(source, filename)
    template = load_template(filename)
    existing = project_stack()
    proposal = propose_template(existing, template, {}, ["bottom", "top", "film"], [])
    link = proposal.project.interfaces[-1]
    assert (link.layer_a, link.layer_b, link.heat_layer_id) == ("bottom_2", "top_2", "film_2")
    limited = propose_template(existing, template, {}, ["bottom", "top"], [])
    assert len(limited.project.interfaces) == len(existing.interfaces)
