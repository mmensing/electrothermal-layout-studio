# SPDX-License-Identifier: MPL-2.0
"""Explicit, portable physical mapping proposals; never implicit import defaults."""
from __future__ import annotations

import hashlib
import json
import os
import tempfile
from copy import deepcopy
from dataclasses import asdict, dataclass
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QTextEdit,
    QVBoxLayout,
)

from etlayout import __version__
from etlayout.gui.forms import region_summary
from etlayout.io.project import configuration_hash, from_dict
from etlayout.models import Interface, Material, PhysicalLayer, Project, SimulationError
from etlayout.technology.validation import validate_project

FORMAT = "etlayout-technology-template"
MAX_TEMPLATE_BYTES = 20_000_000


@dataclass(frozen=True)
class TechnologyTemplate:
    name: str
    source_project: str
    application_version: str
    layers: tuple[PhysicalLayer, ...]
    interfaces: tuple[Interface, ...]
    materials: dict[str, Material]
    omitted_gate_bindings: dict[str, list[str]]
    sha256: str = ""
    filename: str = ""


@dataclass(frozen=True)
class TemplateProposal:
    project: Project
    source_identity: str
    summary: str


def _validate_mapping(layers, interfaces, materials):
    """Use the existing parameter validator without requiring an operating point."""
    if not layers:
        raise ValueError("The technology template must contain at least one physical layer.")
    project = Project(layers=list(layers), interfaces=list(interfaces), materials=materials)
    identifiers = {entry.id for entry in (*layers, *interfaces)} | set(materials)
    errors = [item.message for item in validate_project(project, raise_errors=False)
              if item.severity == "error" and item.object_id in identifiers]
    for collection in (layers, interfaces):
        ids = [item.id for item in collection]
        if any(not key.strip() for key in ids) or len(set(ids)) != len(ids):
            errors.append("Template identifiers must be unique and nonempty within each collection.")
    if any(key != material.id for key, material in materials.items()):
        errors.append("Each material key must match its snapshot identifier.")
    if errors:
        raise ValueError("\n".join(dict.fromkeys(errors)))


def save_template(project: Project, filename) -> None:
    """Save sheets/materials/declared contacts, excluding project operating inputs."""
    layers = deepcopy(project.layers)
    omitted = {layer.id: list(layer.electrical.gate_regions) for layer in layers if layer.electrical.gate_regions}
    for layer in layers:
        layer.electrical.gate_regions = []  # IDs of project terminals are not technology assignments.
    material_ids = {layer.material_id for layer in layers}
    material_ids.update(link.material_id for link in project.interfaces if link.material_id)
    materials = {key: deepcopy(project.materials[key]) for key in material_ids if key in project.materials}
    _validate_mapping(layers, project.interfaces, materials)
    payload = {"format": FORMAT, "version": 1, "name": project.name + " mapping",
               "source_project": project.name, "application_version": __version__,
               "layers": [asdict(layer) for layer in layers],
               "interfaces": [asdict(link) for link in project.interfaces],
               "materials": {key: asdict(value) for key, value in sorted(materials.items())},
               "omitted_gate_bindings": omitted}
    encoded = (json.dumps(payload, indent=2, allow_nan=False) + "\n").encode("utf-8")
    if len(encoded) > MAX_TEMPLATE_BYTES:
        raise ValueError("Technology template exceeds the 20 MB metadata limit.")
    path = Path(filename)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(dir=path.parent, prefix=path.name + ".", suffix=".tmp", delete=False) as handle:
            temporary = Path(handle.name)
            handle.write(encoded)
        os.replace(temporary, path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def load_template(filename) -> TechnologyTemplate:
    path = Path(filename)
    if path.stat().st_size > MAX_TEMPLATE_BYTES:
        raise ValueError("Technology template exceeds the 20 MB metadata limit.")
    content = path.read_bytes()
    data = json.loads(content)
    json.dumps(data, allow_nan=False)  # Also reject nonfinite values inside explicit polygon dictionaries.
    allowed = {"format", "version", "name", "source_project", "application_version", "layers", "interfaces",
               "materials", "omitted_gate_bindings"}
    if not isinstance(data, dict) or set(data) != allowed or data.get("format") != FORMAT:
        raise ValueError("Choose an ETLayout technology template, not a project or solver preset.")
    if type(data["version"]) is not int or data["version"] != 1:
        raise ValueError("Unsupported technology template version.")
    for key in ("name", "source_project", "application_version"):
        if not isinstance(data[key], str):
            raise ValueError(f"Template {key} must be text.")
    if not isinstance(data["layers"], list) or not isinstance(data["interfaces"], list) or not isinstance(data["materials"], dict):
        raise ValueError("Template layer, contact and material collections have invalid types.")
    omitted = data["omitted_gate_bindings"]
    if not isinstance(omitted, dict) or any(not isinstance(k, str) or not isinstance(v, list)
                                         or any(not isinstance(item, str) for item in v) for k, v in omitted.items()):
        raise ValueError("Invalid omitted gate-association provenance.")
    layers = tuple(from_dict(PhysicalLayer, item) for item in data["layers"])
    interfaces = tuple(from_dict(Interface, item) for item in data["interfaces"])
    materials = {key: from_dict(Material, item) for key, item in data["materials"].items()}
    if any(layer.electrical.gate_regions for layer in layers):
        raise ValueError("Templates cannot carry project-specific gate-terminal associations.")
    _validate_mapping(layers, interfaces, materials)
    return TechnologyTemplate(data["name"], data["source_project"], data["application_version"], layers,
                              interfaces, materials, omitted, hashlib.sha256(content).hexdigest(), path.name)


def _unique(identifier, used):
    candidate, index = identifier, 2
    while candidate in used:
        candidate = f"{identifier}_{index}"
        index += 1
    used.add(candidate)
    return candidate


def propose_template(project, template, mask_mapping, selected_layers, available_masks) -> TemplateProposal:
    """Build an independent additive proposal. Existing objects are never replaced."""
    selected = set(selected_layers)
    known = {layer.id for layer in template.layers}
    if not selected or not selected <= known:
        raise ValueError("Select at least one known template physical layer.")
    candidate = deepcopy(project)
    layer_ids = {layer.id for layer in candidate.layers}
    layer_map = {layer.id: _unique(layer.id, layer_ids) for layer in template.layers if layer.id in selected}
    links = [link for link in template.interfaces
             if {link.layer_a, link.layer_b, *link.replaces_layers, *([link.heat_layer_id] if link.heat_layer_id else [])} <= selected]
    regions = [layer.geometry for layer in template.layers if layer.id in selected] + [link.geometry for link in links]
    required_masks = {key for region in regions for key in (*region.sources, *region.exclude_sources)}
    available = set(available_masks)
    if any(not mask_mapping.get(key) or mask_mapping[key] not in available for key in required_masks):
        missing = sorted(key for key in required_masks if not mask_mapping.get(key) or mask_mapping[key] not in available)
        raise ValueError("Choose a target mask for: " + ", ".join(missing))
    materials = {layer.material_id for layer in template.layers if layer.id in selected}
    materials.update(link.material_id for link in links if link.material_id)
    material_map, used_materials = {}, set(candidate.materials)
    for key in sorted(materials):
        material = deepcopy(template.materials[key])
        if key in candidate.materials and asdict(candidate.materials[key]) == asdict(material):
            material_map[key] = key
        else:
            material.id = _unique(key, used_materials)
            material.reference = False  # Imported snapshots belong to this project, not the shared library.
            candidate.materials[material.id] = material
            material_map[key] = material.id
    added_layers, added_links = [], []
    for original in template.layers:
        if original.id not in selected:
            continue
        layer = deepcopy(original)
        layer.id, layer.material_id = layer_map[layer.id], material_map[layer.material_id]
        layer.geometry.sources = [mask_mapping[key] for key in layer.geometry.sources]
        layer.geometry.exclude_sources = [mask_mapping[key] for key in layer.geometry.exclude_sources]
        candidate.layers.append(layer)
        added_layers.append(layer.id)
    interface_ids = {link.id for link in candidate.interfaces}
    for original in links:
        link = deepcopy(original)
        link.id = _unique(link.id, interface_ids)
        link.layer_a, link.layer_b = layer_map[link.layer_a], layer_map[link.layer_b]
        link.replaces_layers = [layer_map[key] for key in link.replaces_layers]
        if link.heat_layer_id:
            link.heat_layer_id = layer_map[link.heat_layer_id]
        if link.material_id:
            link.material_id = material_map[link.material_id]
        link.geometry.sources = [mask_mapping[key] for key in link.geometry.sources]
        link.geometry.exclude_sources = [mask_mapping[key] for key in link.geometry.exclude_sources]
        candidate.interfaces.append(link)
        added_links.append(link.id)
    ignored = available - set(mask_mapping[key] for key in required_masks)
    omitted_links = [link.id for link in template.interfaces if link not in links]
    explicit = [layer.id for layer in template.layers if layer.id in selected and layer.geometry.polygons]
    material_details = "; ".join(f"{key} → {material_map[key]} ({template.materials[key].source}; version {template.materials[key].version})"
                                 for key in sorted(materials))
    summary = (f"Template: {template.name}\nFile: {template.filename}\nSHA-256: {template.sha256}\n"
               f"Source project: {template.source_project} · ETLayout {template.application_version}\n"
               f"Add layers: {', '.join(added_layers)}\nAdd contacts: {', '.join(added_links) or 'none'}\n"
               f"Material snapshots: {material_details}\n"
               "Existing layers, materials and operating inputs are preserved.\n"
               f"Masks not assigned by this proposal: {', '.join(sorted(ignored)) or 'none'}\n"
               f"Contacts omitted because their layers were not selected: {', '.join(omitted_links) or 'none'}\n"
               f"Explicit SI-coordinate geometry retained: {', '.join(explicit) or 'none'}\n"
               f"Project gate associations omitted: {', '.join(template.omitted_gate_bindings) or 'none'}\n"
               "Validate geometry, connectivity and stack after applying; a template does not prove readiness.")
    provenance = f"Technology template {template.name!r} ({template.filename}), SHA-256 {template.sha256}; source {template.source_project!r}. Added layers: {', '.join(added_layers)}."
    candidate.notes = (candidate.notes.rstrip() + "\n" + provenance).lstrip()
    return TemplateProposal(candidate, configuration_hash(project), summary)


class TechnologyTemplateDialog(QDialog):
    """One optional task dialog: choose, map, preview, confirm, then transact."""

    def __init__(self, workspace):
        super().__init__(workspace.window)
        self.workspace = workspace
        self.template = None
        self.proposal = None
        self.setWindowTitle("Technology mapping templates")
        self.resize(820, 650)
        layout = QVBoxLayout(self)
        note = QLabel("Save physical layers, material snapshots and declared contacts for reuse. Load a template, select layers and map its masks, then preview and confirm. Terminal drives, waveforms and thermal boundaries are not imported; existing objects stay unchanged.")
        note.setWordWrap(True)
        layout.addWidget(note)
        actions = QHBoxLayout()
        for label, callback in (("Load template…", self.load_file), ("Save current mapping…", self.save_file)):
            control = QPushButton(label)
            control.clicked.connect(callback)
            actions.addWidget(control)
        layout.addLayout(actions)
        self.layers = QTableWidget(0, 5)
        self.layers.setHorizontalHeaderLabels(["Include / layer", "Role / model / material", "Geometry", "Thickness (µm)", "Z (µm)"])
        self.layers.setAccessibleName("Proposed template physical layers")
        self.layers.itemChanged.connect(self.invalidate_preview)
        layout.addWidget(self.layers, 2)
        self.masks = QTableWidget(0, 2)
        self.masks.setHorizontalHeaderLabels(["Template mask", "Target source mask"])
        self.masks.setAccessibleName("Explicit source-mask mapping")
        layout.addWidget(self.masks, 1)
        self.summary = QTextEdit()
        self.summary.setReadOnly(True)
        self.summary.setAccessibleName("Technology proposal provenance and changes")
        layout.addWidget(self.summary, 2)
        preview = QPushButton("Preview selected mapping")
        preview.clicked.connect(self.preview)
        layout.addWidget(preview)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Apply | QDialogButtonBox.StandardButton.Cancel)
        self.apply_button = buttons.button(QDialogButtonBox.StandardButton.Apply)
        self.apply_button.setText("Apply confirmed proposal")
        self.apply_button.setEnabled(False)
        self.apply_button.clicked.connect(self.apply_proposal)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def invalidate_preview(self, *_):
        self.proposal = None
        if hasattr(self, "apply_button"):
            self.apply_button.setEnabled(False)

    def load_file(self):
        path, _ = QFileDialog.getOpenFileName(self, "Load technology mapping template", "", "Technology template (*.etltech.json);;JSON (*.json)")
        if path:
            self.invalidate_preview()
            try:
                self.set_template(load_template(path))
            except (OSError, ValueError, SimulationError) as error:
                self.summary.setPlainText(str(error))

    def set_template(self, template):
        self.template = template
        self.invalidate_preview()
        self.layers.setRowCount(len(template.layers))
        for row, layer in enumerate(template.layers):
            values = [f"{layer.id} · {layer.name}", f"{layer.role} / {layer.electrical.kind} / {layer.material_id}", region_summary(layer.geometry),
                      f"{layer.thickness * 1e6:g}", f"{layer.z * 1e6:g}"]
            for column, value in enumerate(values):
                item = QTableWidgetItem(value)
                item.setFlags(Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable)
                if column == 0:
                    item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
                    item.setCheckState(Qt.CheckState.Checked)
                    item.setData(Qt.ItemDataRole.UserRole, layer.id)
                self.layers.setItem(row, column, item)
        keys = sorted({key for entry in (*template.layers, *template.interfaces)
                       for key in (*entry.geometry.sources, *entry.geometry.exclude_sources)})
        self.masks.setRowCount(len(keys))
        available = self.workspace.window.mask_keys()
        for row, key in enumerate(keys):
            item = QTableWidgetItem(key)
            item.setFlags(Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable)
            self.masks.setItem(row, 0, item)
            combo = QComboBox()
            combo.setAccessibleName("Target mask for " + key)
            combo.addItem("Choose target mask…", "")
            for target in available:
                combo.addItem(target, target)
            combo.setCurrentIndex(max(0, combo.findData(key)))
            combo.currentIndexChanged.connect(self.invalidate_preview)
            self.masks.setCellWidget(row, 1, combo)
        self.layers.resizeColumnsToContents()
        self.masks.horizontalHeader().setStretchLastSection(True)
        self.summary.setPlainText(f"Loaded {template.name}\nSource project: {template.source_project}\nFile: {template.filename}\nSHA-256: {template.sha256}\nReview layer inclusion and target masks, then Preview.")

    def save_file(self):
        path, _ = QFileDialog.getSaveFileName(self, "Save technology mapping template", "technology.etltech.json", "Technology template (*.etltech.json)")
        if path:
            try:
                save_template(self.workspace.document.project, path)
                self.summary.setPlainText("Saved physical mapping to " + path + ". Project terminal/gate associations and operating inputs are not exported.")
            except (OSError, ValueError, SimulationError) as error:
                self.summary.setPlainText(str(error))

    def preview(self):
        try:
            if self.template is None:
                raise ValueError("Load a technology mapping template first.")
            selected = [self.layers.item(row, 0).data(Qt.ItemDataRole.UserRole) for row in range(self.layers.rowCount())
                        if self.layers.item(row, 0).checkState() == Qt.CheckState.Checked]
            mapping = {self.masks.item(row, 0).text(): self.masks.cellWidget(row, 1).currentData()
                       for row in range(self.masks.rowCount())}
            self.proposal = propose_template(self.workspace.document.project, self.template, mapping, selected,
                                             self.workspace.window.mask_keys())
            self.summary.setPlainText(self.proposal.summary)
            self.apply_button.setEnabled(True)
        except (ValueError, SimulationError) as error:
            self.invalidate_preview()
            self.summary.setPlainText(str(error))

    def apply_proposal(self):
        if self.proposal is None:
            return
        if configuration_hash(self.workspace.document.project) != self.proposal.source_identity:
            self.invalidate_preview()
            self.summary.setPlainText("The working setup changed. Preview again before applying this template.")
            return
        if QMessageBox.question(self, "Apply technology mapping proposal", self.proposal.summary,
                                QMessageBox.StandardButton.Apply | QMessageBox.StandardButton.Cancel,
                                QMessageBox.StandardButton.Cancel) != QMessageBox.StandardButton.Apply:
            return
        try:
            proposal = self.proposal
            def apply(project):
                if configuration_hash(project) != proposal.source_identity:
                    raise ValueError("The working setup changed during confirmation. Preview again before applying.")
                return deepcopy(proposal.project)
            self.workspace.document.transact("Applied technology mapping template", apply)
            self.accept()
        except ValueError as error:
            self.summary.setPlainText(str(error))


def open_technology_templates(workspace):
    if workspace.window.model_locked() or not workspace.resolve_draft():
        return
    dialog = TechnologyTemplateDialog(workspace)
    if dialog.exec():
        workspace.navigate("layers", force=True)
