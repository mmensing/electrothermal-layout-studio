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


def test_guided_apply_checks_heat_target_and_layer_removal_cleans_dependent_interfaces(qtbot, tmp_path, monkeypatch):
    from PySide6.QtWidgets import QMessageBox

    from etlayout.gui.main_window import MainWindow

    project = project_stack()
    link = Interface("device", "bottom", "top", RegionSpec(full_domain=True), electrical_kind="load", heat_layer_id="film")
    project.interfaces.append(link)
    window = MainWindow(project, database_path=tmp_path / "materials.sqlite3", recover=False)
    qtbot.addWidget(window, before_close_func=lambda widget: setattr(widget, "dirty", False))
    window.show()
    qtbot.waitUntil(lambda: window.prepare_job is None, timeout=30000)
    assert window.geometry is not None
    # Apply validates the requested support; it must not silently crop a contact
    # merely to make the separately selected heat-deposition target cover it.
    target = window.geometry.layers["film"]
    from shapely.geometry import box
    window.geometry.layers["film"] = target.intersection(box(0, 0, 5e-6, 10e-6))
    with pytest.raises(ValueError, match="complete interface support"):
        window.guided.validate_support(link, "interfaces")
    window.geometry.layers["film"] = target
    window.guided.validate_support(link, "interfaces")
    monkeypatch.setattr(window.panels["layers"], "selected_id", lambda: "film")
    monkeypatch.setattr(QMessageBox, "question", lambda *args, **kwargs: QMessageBox.StandardButton.Yes)
    window.remove_object("layers")
    qtbot.waitUntil(lambda: window.prepare_job is None, timeout=30000)
    assert "film" not in {layer.id for layer in window.project.layers}
    assert not window.project.interfaces
    window.dirty = False
