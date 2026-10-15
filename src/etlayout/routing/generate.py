# SPDX-License-Identifier: MPL-2.0
"""Generate isolated layout edits and verify the actual exported file."""
from __future__ import annotations

import copy
import hashlib
import json
from dataclasses import dataclass, field
from pathlib import Path

import klayout.db as kdb
from shapely.geometry import Point, box

from etlayout.geometry.layout import LayoutDocument
from etlayout.geometry.prepare import prepare_geometry, resolve_region
from etlayout.models import Project, SimulationError, checkpoint

from .geometry import canonical_geometry_id, from_region, to_region
from .recipes import recipe_geometry
from .rules import resolved_protected, validate_rules, violation


@dataclass
class GenerationResult:
    project: Project
    violations: list[dict] = field(default_factory=list)
    provenance: dict = field(default_factory=dict)

    @property
    def valid(self):
        return not any(item["severity"] == "error" for item in self.violations)


def _replace_instances(cell, entries):
    cell.clear_insts()
    for array, properties in entries:
        instance = cell.insert(array)
        if properties:
            instance.set_properties(properties)


def _isolate(document, root, path, *, all_instances=False):
    """Clone each selected ancestor; split just the addressed array if necessary."""
    cell, transform = root, kdb.ICplxTrans()
    for index in path:
        offset, selected = 0, None
        entries = []
        for instance in cell.each_inst():
            array = instance.cell_inst.dup()
            properties = instance.properties()
            size = array.size()
            if offset <= index < offset + size:
                if size > 100000:
                    raise SimulationError("Selected array exceeds the safe explicit-edit budget of 100000 placements")
                for j, placed in enumerate(array.each_cplx_trans()):
                    child = document._layout.cell(array.cell_index)
                    if offset + j == index:
                        if not all_instances:
                            child = child.dup()
                        selected = (child, placed)
                    entries.append((kdb.CellInstArray(child.cell_index(), placed), properties))
            else:
                entries.append((array, properties))
            offset += size
        if selected is None:
            raise SimulationError(f"Invalid routing instance path {path}")
        _replace_instances(cell, entries)
        cell, placed = selected
        transform = transform * placed
    return cell, transform


def _cut_recursive(layout, cell, layer_index, cut, transform, cancel, *, clone_children=True):
    """Subtract root-coordinate edit support without flattening unrelated geometry."""
    checkpoint(cancel)
    local = cut.transformed(transform.inverted())
    shapes = cell.shapes(layer_index)
    for shape in list(shapes.each()):
        if not (shape.is_polygon() or shape.is_box() or shape.is_path() or shape.is_simple_polygon()):
            continue
        original = kdb.Region(shape.polygon)
        if (original & local).is_empty():
            continue
        properties = shape.properties()
        remainder = original - local
        shape.delete()
        for polygon in remainder.each():
            replacement = shapes.insert(polygon)
            if properties:
                replacement.set_properties(properties)
    entries = []
    for instance in cell.each_inst():
        array, properties = instance.cell_inst.dup(), instance.properties()
        child = layout.cell(array.cell_index)
        # Bbox-only pruning is conservative; exact subtraction below is safe.
        placed_bounds = child.bbox(layer_index).transformed(array.cplx_trans)
        if array.size() == 1 and (kdb.Region(placed_bounds) & local).is_empty():
            entries.append((array, properties))
            continue
        if array.size() > 100000:
            raise SimulationError("Routing hierarchy traversal exceeds array edit budget")
        for placed in array.each_cplx_trans():
            composed = transform * placed
            if (kdb.Region(child.bbox().transformed(composed)) & cut).is_empty():
                edited = child
            else:
                edited = child.dup() if clone_children else child
                _cut_recursive(layout, edited, layer_index, cut, composed, cancel, clone_children=clone_children)
            entries.append((kdb.CellInstArray(edited.cell_index(), placed), properties))
    _replace_instances(cell, entries)


def _apply_parameters(project, parameters, experiment):
    recipes = {recipe.id: recipe for recipe in project.power.recipes}
    variables = {v.id: v for e in ([experiment] if experiment is not None else project.power.experiments) for v in e.variables}
    for key, value in (parameters or {}).items():
        if key in recipes and isinstance(value, dict):
            recipes[key].parameters.update(value)
            continue
        if key in variables:
            variable = variables[key]
            recipe_id, name = variable.recipe_id, variable.parameter
            if variable.kind == "choice":
                if value not in variable.values:
                    raise SimulationError(f"Variable {key} is not an allowed choice")
            elif not variable.minimum <= value <= variable.maximum:
                raise SimulationError(f"Variable {key} is outside its declared bounds")
            if variable.kind == "integer" and value != int(value):
                raise SimulationError(f"Variable {key} must be integral")
        elif "." in key:
            recipe_id, name = key.split(".", 1)
        else:
            raise SimulationError(f"Unknown routing variable {key}")
        if recipe_id not in recipes:
            raise SimulationError(f"Unknown recipe {recipe_id}")
        recipes[recipe_id].parameters[name] = value


def _synthetic_source(project, path):
    """Explicit-geometry projects acquire a real layout source before recipe edits."""
    layout = kdb.Layout()
    layout.dbu = project.power.rules.dbu / 1e-6
    root = layout.create_cell("ETLAYOUT")
    prepared = prepare_geometry(project)
    for i, layer in enumerate(project.layers):
        mask = next((r.mask for r in project.power.recipes if r.layer_id == layer.id and r.kind != "via_array"), f"{100 + i}/0")
        number, datatype = map(int, mask.split("/"))
        if layer.geometry.sources or layer.geometry.exclude_sources:
            raise SimulationError("An explicit-geometry project cannot reference masks without a layout source")
        shape = prepared.layers[layer.id]
        root.shapes(layout.layer(number, datatype)).insert(to_region(shape, project.power.rules.dbu))
        from etlayout.models import RegionSpec
        layer.geometry = RegionSpec(sources=[mask])
    for i, interface in enumerate(project.interfaces):
        mask = next((r.mask for r in project.power.recipes if r.interface_id == interface.id), f"{500 + i}/0")
        if interface.geometry.full_domain:
            continue
        shape = resolve_region(interface.geometry, {}, None, clip=False)
        number, datatype = map(int, mask.split("/"))
        root.shapes(layout.layer(number, datatype)).insert(to_region(shape, project.power.rules.dbu))
        interface.geometry = RegionSpec(sources=[mask])
    layout.write(str(path))
    project.layout.path, project.layout.cell = str(path), root.name
    project.layout.dbu_um = layout.dbu


def generate_candidate(project, parameters, output_path, *, cancel=None, experiment=None):
    """Generate, rule-check and reimport a new GDS/OASIS source copy.

    Hard violations return ``valid=False`` and a project which must not be solved
    or ranked. The imported original and any existing destination are immutable.
    Values accept variable IDs, ``recipe.parameter`` or nested recipe dictionaries.
    """
    checkpoint(cancel)
    candidate = copy.deepcopy(project)
    destination = Path(output_path).resolve()
    if destination.suffix.lower() not in {".gds", ".gdsii", ".oas", ".oasis"}:
        raise SimulationError("Candidate export requires a GDSII or OASIS filename")
    if candidate.layout.path and destination == Path(candidate.layout.path).resolve():
        raise SimulationError("Candidate export may not overwrite the imported original")
    if destination.exists():
        raise SimulationError("Candidate destination already exists; choose a new file")
    destination.parent.mkdir(parents=True, exist_ok=True)
    _apply_parameters(candidate, parameters, experiment)
    temporary_source = None
    if not candidate.layout.path:
        temporary_source = destination.with_name(destination.stem + ".explicit-source.gds")
        if temporary_source.exists():
            raise SimulationError("Explicit source staging path already exists")
        _synthetic_source(candidate, temporary_source)
    try:
        document = LayoutDocument(candidate.layout.path)
        source_hash = hashlib.sha256(Path(candidate.layout.path).read_bytes()).hexdigest()
        if candidate.layout.sha256 and candidate.layout.sha256 != source_hash:
            return GenerationResult(candidate, [violation("source_identity", "Imported layout content changed; explicitly reimport/rebind it before generating")])
        root = document._cell(candidate.layout.cell)
        source_masks = document.extract(candidate.layout.cell, candidate.layout.instance_path)
        # A selected simulation instance is not the complete export source.
        # All-instance edits can affect its siblings, so protection and export
        # comparisons must retain the entire root-cell geometry.
        source_export_masks = (document.extract(candidate.layout.cell)
                               if candidate.layout.instance_path else source_masks)
        dbu = document.dbu_um * 1e-6
        rules = candidate.power.rules
        issues, edits = [], []
        if abs(dbu - rules.dbu) > dbu * 1e-9:
            return GenerationResult(candidate, [violation("dbu_mismatch", "Rule DBU must match the imported layout DBU")])
        domain = box(*document.cell_bounds(candidate.layout.cell))
        permissions = resolved_protected(candidate, source_export_masks, domain)
        selected_ids = set(experiment.recipe_ids) if experiment is not None else None
        for recipe in candidate.power.recipes:
            checkpoint(cancel)
            if selected_ids is not None and recipe.id not in selected_ids:
                continue
            if recipe.source_hash and recipe.source_hash != source_hash:
                issues.append(violation("source_identity", "Recipe source identity does not match the immutable source", object_id=recipe.id))
                continue
            recipe.source_hash = source_hash
            try:
                if recipe.mask in rules.protected_masks:
                    raise SimulationError("Recipe mask is protected")
                if rules.allowed_layers and recipe.layer_id not in rules.allowed_layers:
                    raise SimulationError("Recipe physical layer is not allowed for routing")
                if recipe.layer_id not in {layer.id for layer in candidate.layers}:
                    raise SimulationError("Recipe physical layer does not exist")
                number, datatype = map(int, recipe.mask.split("/"))
                if min(number, datatype) < 0 or max(number, datatype) > 65535:
                    raise SimulationError("GDS mask layer/datatype must be in [0, 65535]")
                target = next((i for i in candidate.interfaces if i.id == recipe.interface_id), None) if recipe.kind == "via_array" else candidate.layer(recipe.layer_id)
                if target is None or recipe.mask not in target.geometry.sources:
                    raise SimulationError("Recipe mask must be referenced by its physical layer/interface geometry")
                edit = resolve_region(recipe.edit_region, source_masks, domain, clip=False)
                generated = recipe_geometry(recipe, edit, rules)
                snapped = from_region(to_region(generated, dbu), dbu)
                if snapped.is_empty:
                    raise SimulationError("Generated feature vanished after DBU snapping")
                if snapped.difference(edit.buffer(dbu * .01)).area > 1e-30:
                    raise SimulationError("Generated geometry extends outside its permitted edit region")
                path = recipe.instance_path or candidate.layout.instance_path
                selected_masks = document.extract(candidate.layout.cell, path)
                old = selected_masks.get(recipe.mask)
                if old is None:
                    old = from_region(kdb.Region(), dbu)
                changed = old.intersection(edit).symmetric_difference(snapped)
                for label, protected in permissions:
                    area = (snapped.difference(old) if label == "keepout" else changed).intersection(protected).area
                    if area > dbu ** 2 * .01:
                        issues.append(violation(label, f"Recipe changes {label.replace('_', ' ')} geometry", layer_id=recipe.layer_id,
                                                net_id=recipe.net_id, object_id=recipe.id, geometry=protected.intersection(edit)))
                # Anchors are physical source/load landing points. All declared
                # anchors must belong to one generated/retained connected island.
                complete = old.difference(edit).union(snapped)
                anchors = recipe.parameters.get("anchors", [])
                if anchors:
                    from etlayout.geometry.prepare import polygon_parts
                    if not any(all(part.buffer(dbu * .01).covers(Point(point)) for point in anchors) for part in polygon_parts(complete)):
                        issues.append(violation("connectivity", "Source/load anchors are not connected after routing", layer_id=recipe.layer_id, object_id=recipe.id, geometry=edit))
                selected, transform = _isolate(document, root, path, all_instances=recipe.all_instances)
                permitted = edit
                if recipe.all_instances and path:
                    local_edit = to_region(edit, dbu).transformed(transform.inverted())
                    for placement in document.instances(candidate.layout.cell, recursive=True, max_instances=100000):
                        placed_cell, placed_transform = document._selection(candidate.layout.cell, placement["path"])
                        if placed_cell.cell_index() == selected.cell_index():
                            permitted = permitted.union(from_region(local_edit.transformed(placed_transform), dbu))
                index = document._layout.layer(number, datatype)
                if not any(item["key"] == recipe.mask for item in document.layers):
                    document.layers.append(dict(key=recipe.mask, layer=number, datatype=datatype, name="", index=index))
                cut = to_region(edit, dbu)
                _cut_recursive(document._layout, selected, index, cut, transform, cancel, clone_children=not recipe.all_instances)
                selected.shapes(index).insert(to_region(snapped, dbu).transformed(transform.inverted()))
                edits.append(dict(recipe_id=recipe.id, kind=recipe.kind, version=recipe.version,
                                  layer_id=recipe.layer_id, mask=recipe.mask, instance_path=path,
                                  all_instances=recipe.all_instances, seed=recipe.seed,
                                  parameters=copy.deepcopy(recipe.parameters), edit_region=permitted,
                                  geometry_id=canonical_geometry_id(snapped, recipe.id)))
                document._extraction_cache.clear()
            except (SimulationError, ValueError, TypeError) as exc:
                issues.append(violation("recipe", str(exc), layer_id=recipe.layer_id, net_id=recipe.net_id, object_id=recipe.id))
        if issues:
            return GenerationResult(candidate, issues, {"source_sha256": source_hash})
        document._layout.write(str(destination))
        candidate.layout.path = str(destination)
        candidate.layout.sha256 = hashlib.sha256(destination.read_bytes()).hexdigest()
        reopened = LayoutDocument(destination)
        actual = reopened.extract(candidate.layout.cell, candidate.layout.instance_path)
        export_actual = reopened.extract(candidate.layout.cell) if candidate.layout.instance_path else actual
        expected = document.extract(candidate.layout.cell)
        for mask in set(source_export_masks) | set(export_actual):
            before = source_export_masks.get(mask, from_region(kdb.Region(), dbu))
            after = export_actual.get(mask, from_region(kdb.Region(), dbu))
            if canonical_geometry_id(after) != canonical_geometry_id(expected.get(mask, from_region(kdb.Region(), dbu))):
                issues.append(violation("roundtrip_geometry", "Export/reimport changed canonical mask geometry", object_id=mask))
            permitted = from_region(kdb.Region(), dbu)
            for edit in edits:
                if edit["mask"] == mask:
                    permitted = permitted.union(edit["edit_region"])
            difference = before.symmetric_difference(after).difference(permitted.buffer(dbu * .01))
            if difference.area > dbu ** 2 * .01:
                issues.append(violation("outside_edit", "Export changed geometry outside the approved region/layer", object_id=mask, geometry=difference))
            if mask in rules.protected_masks and before.symmetric_difference(after).area > dbu ** 2 * .01:
                issues.append(violation("protected_mask", "Export changed a protected mask", object_id=mask))
            for label, protected in permissions:
                difference = (after.difference(before) if label == "keepout" else after.symmetric_difference(before)).intersection(protected)
                if difference.area > dbu ** 2 * .01:
                    issues.append(violation(label, "Export changed protected support", object_id=mask, geometry=difference))
        try:
            geometry = prepare_geometry(candidate)
            issues.extend(validate_rules(candidate, geometry))
            for terminal in candidate.terminals:
                if terminal.geometry.full_domain:
                    continue
                raw = resolve_region(terminal.geometry, actual, geometry.domain, clip=False)
                outside = raw.difference(geometry.layers[terminal.layer_id].buffer(dbu * .01))
                if outside.area > dbu ** 2 * .01 or (raw.area == 0 and outside.length > dbu * .01):
                    issues.append(violation("terminal_support", "Generated routing does not preserve the complete terminal/port footprint", layer_id=terminal.layer_id,
                                            object_id=terminal.id, geometry=outside))
            if candidate.terminals:
                from etlayout.connectivity.analyze import analyze_connectivity
                network = analyze_connectivity(candidate, geometry, cancel=cancel)
                issues.extend(violation("connectivity", item.message, object_id=item.object_id)
                              for item in network.diagnostics if item.severity == "error")
        except SimulationError as exc:
            issues.append(violation("physical_geometry", str(exc)))
        provenance = dict(version=1, source_sha256=source_hash, output_sha256=candidate.layout.sha256,
                          dbu_m=dbu, canonical_masks={key: canonical_geometry_id(value) for key, value in sorted(export_actual.items())},
                          recipes=[{key: value for key, value in edit.items() if key != "edit_region"} for edit in edits],
                          export_reimport_verified=not issues, output_path=str(destination))
        destination.with_suffix(destination.suffix + ".provenance.json").write_text(json.dumps(provenance, indent=2), encoding="utf-8")
        return GenerationResult(candidate, issues, provenance)
    finally:
        if temporary_source is not None and temporary_source.exists():
            temporary_source.unlink()


def export_candidate(project, output_path, parameters=None, *, cancel=None, experiment=None):
    """Export current candidate unchanged; explicit parameters request regeneration.

    Finalist export must preserve the exact chosen geometry, not rerun recipes
    against the generated layout or mistake its hash for the original source.
    """
    if parameters is not None:
        return generate_candidate(project, parameters, output_path, cancel=cancel, experiment=experiment)
    checkpoint(cancel)
    destination = Path(output_path).resolve()
    if destination.suffix.lower() not in {".gds", ".gdsii", ".oas", ".oasis"}:
        raise SimulationError("Candidate export requires a GDSII or OASIS filename")
    if not project.layout.path:
        raise SimulationError("Generate a candidate before exporting an explicit-geometry project")
    if destination == Path(project.layout.path).resolve() or destination.exists():
        raise SimulationError("Candidate export requires a new path and may not overwrite its source")
    source_hash = hashlib.sha256(Path(project.layout.path).read_bytes()).hexdigest()
    if project.layout.sha256 and project.layout.sha256 != source_hash:
        return GenerationResult(copy.deepcopy(project), [violation(
            "source_identity", "Candidate layout content changed; explicitly reimport/rebind it before exporting")])
    document = LayoutDocument(project.layout.path, cancel=cancel)
    expected = document.extract(project.layout.cell, cancel=cancel)
    destination.parent.mkdir(parents=True, exist_ok=True)
    document._layout.write(str(destination))
    candidate = copy.deepcopy(project)
    candidate.layout.path = str(destination)
    candidate.layout.sha256 = hashlib.sha256(destination.read_bytes()).hexdigest()
    actual = LayoutDocument(destination).extract(candidate.layout.cell, cancel=cancel)
    issues = []
    if {key: canonical_geometry_id(value) for key, value in expected.items()} != {
            key: canonical_geometry_id(value) for key, value in actual.items()}:
        issues.append(violation("roundtrip_geometry", "Candidate export changed canonical mask geometry"))
    issues.extend(validate_rules(candidate))
    provenance = dict(version=1, source_sha256=source_hash,
                      output_sha256=candidate.layout.sha256, dbu_m=document.dbu_um * 1e-6,
                      canonical_masks={key: canonical_geometry_id(value) for key, value in sorted(actual.items())},
                      export_reimport_verified=not issues, output_path=str(destination), operation="copy_finalist")
    destination.with_suffix(destination.suffix + ".provenance.json").write_text(json.dumps(provenance, indent=2), encoding="utf-8")
    return GenerationResult(candidate, issues, provenance)
