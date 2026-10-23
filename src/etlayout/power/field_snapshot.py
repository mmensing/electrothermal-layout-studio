# SPDX-License-Identifier: MPL-2.0
"""Explicit finite-volume inputs shared by optional electrostatic and RL tools.

No layer-overlap formula or electrical ground is used to invent capacitance or
a magnetic return. Geometry is resolved once through the normal project path;
physical vias retain their exact replacement footprint and stack interval.
"""
from __future__ import annotations

import hashlib
import json
import math
from copy import deepcopy
from dataclasses import asdict, dataclass, replace
from itertools import combinations

import numpy as np
from shapely.geometry import GeometryCollection, Polygon, box
from shapely.geometry.polygon import orient
from shapely.ops import unary_union

from etlayout.geometry.prepare import polygon_parts, prepare_geometry
from etlayout.io.project import configuration_hash, physics_hash
from etlayout.materials.models import evaluate_property
from etlayout.models import SimulationError, checkpoint

EPSILON_0 = 8.8541878128e-12  # F/m; declared CODATA 2018 rounded vacuum value.
MU_0 = 4 * math.pi * 1e-7  # H/m; nonmagnetic ideal used by the external RL engine.
_GEOMETRY_TOLERANCE = 1e-15  # Roundoff guard, not a geometry inflation/repair.
_MAX_SOLIDS = 512


def _mesh_divisions(length, mesh_size):
    """Stable ceiling at exact mesh boundaries, allowing machine roundoff only."""
    ratio = length / mesh_size
    if not math.isfinite(ratio):
        raise SimulationError("Requested field surface size exceeds the bounded mesh resolution")
    nearest = round(ratio)
    if nearest >= 1 and math.isclose(ratio, nearest, rel_tol=64*np.finfo(float).eps, abs_tol=0):
        ratio = nearest
    return max(1,math.ceil(ratio))


def _hash(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=True,
                                     allow_nan=False, separators=(",", ":")).encode()).hexdigest()


def _positive(value, name):
    if isinstance(value, (bool, str)) or not np.isscalar(value) or not np.isrealobj(value):
        raise SimulationError(f"{name} must be a finite positive SI number")
    value = float(value)
    if not np.isfinite(value) or value <= 0:
        raise SimulationError(f"{name} must be a finite positive SI number")
    return value


def _label(value, name):
    if not isinstance(value, str) or not value.strip() or len(value) > 256 or any(ord(c) < 32 for c in value):
        raise SimulationError(f"{name} must be a nonempty identifier without control characters")
    return value


def _point(value, name):
    try:
        raw = np.asarray(value)
        if raw.dtype.kind not in "iuf" or raw.shape != (3,):
            raise ValueError
        point = tuple(map(float, raw))
        if not np.isfinite(point).all():
            raise ValueError
        return point
    except (TypeError, ValueError, OverflowError) as error:
        raise SimulationError(f"{name} must contain three finite SI coordinates") from error


@dataclass(frozen=True)
class Solid:
    id: str
    origin_id: str
    layer_id: str
    material_id: str
    role: str
    exterior_m: tuple
    holes_m: tuple
    z_bottom_m: float
    z_top_m: float
    component_id: str = ""

    @property
    def polygon(self):
        return Polygon(self.exterior_m, self.holes_m)


@dataclass(frozen=True)
class EvaluatedMaterial:
    id: str
    permittivity_F_per_m: float | None
    permeability_H_per_m: float | None
    conductivity_S_per_m: float | None
    source: str
    field_source: str
    validity: tuple


@dataclass(frozen=True)
class Segment:
    start_m: tuple
    end_m: tuple
    width_m: float
    height_m: float
    width_direction: tuple
    conductivity_S_per_m: float
    solid_ids: tuple


@dataclass(frozen=True)
class Loop:
    id: str
    segments: tuple[Segment, ...]
    component_id: str


@dataclass(frozen=True)
class FieldSnapshot:
    kind: str
    solids: tuple[Solid, ...]
    components: tuple[str, ...]
    materials: tuple[EvaluatedMaterial, ...]
    loops: tuple[Loop, ...]
    temperature_K: float
    relative_permittivity: float
    relative_permeability: float
    source_context_json: str
    geometry_sha256: str
    field_model_hash: str
    schema_version: int = 1

    @property
    def source_context(self):
        """Return a copy so callers cannot mutate the hash-bound input record."""
        return json.loads(self.source_context_json)

    def to_dict(self):
        data = asdict(self)
        data["source_context"] = data.pop("source_context_json")
        data["source_context"] = json.loads(data["source_context"])
        return data


@dataclass(frozen=True)
class Surface:
    id: str
    vertices_m: tuple
    triangles: tuple


@dataclass(frozen=True)
class SurfaceMesh:
    surfaces: tuple[Surface, ...]
    mesh_sha256: str
    geometry_sha256: str
    mesh_size_m: float

    def to_dict(self):
        return asdict(self)


def _coefficient(material, name, temperature, *, required):
    model = getattr(material, name, None)
    if model is None:
        if required:
            unit = {"permittivity": "F/m", "permeability": "H/m", "resistivity": "Ω m"}[name]
            raise SimulationError(f"Material {material.id}: specify {name} in {unit} with its validity/source; thermal properties do not determine it")
        return None, ()
    if not model.minimum_temperature <= temperature <= model.maximum_temperature:
        raise SimulationError(f"Material {material.id}: {name} temperature {temperature:g} K is outside its declared validity; supply calibrated data or choose a valid fixed temperature")
    if model.kind == "table" and (not model.temperatures or not model.temperatures[0] <= temperature <= model.temperatures[-1]):
        raise SimulationError(f"Material {material.id}: {name} temperature {temperature:g} K is outside its tabulated validity; endpoint clamping is not an electromagnetic material model")
    value = float(evaluate_property(model, temperature))
    return value, (name, model.minimum_temperature, model.maximum_temperature)


def _parts(origin, layer_id, material, role, geometry, bottom, top):
    parts = sorted(polygon_parts(geometry), key=lambda polygon: polygon.normalize().wkb_hex)
    for index, polygon in enumerate(parts):
        polygon = orient(polygon, sign=1.)
        yield Solid(f"{origin}:{index}", origin, layer_id, material, role,
                    tuple(map(tuple, polygon.exterior.coords)),
                    tuple(tuple(map(tuple, ring.coords)) for ring in polygon.interiors), bottom, top)


def _connected_components(solids, project, geometry, cancel):
    parent = list(range(len(solids)))
    def find(index):
        while parent[index] != index:
            parent[index] = parent[parent[index]]
            index = parent[index]
        return index
    interfaces = {interface.id: interface for interface in project.interfaces if interface.enabled}
    for first, second in combinations(range(len(solids)), 2):
        checkpoint(cancel)
        a, b = solids[first], solids[second]
        vertical = min(a.z_top_m, b.z_top_m) - max(a.z_bottom_m, b.z_bottom_m)
        if vertical < -_GEOMETRY_TOLERANCE:
            continue
        overlap = a.polygon.intersection(b.polygon)
        if overlap.is_empty:
            continue
        if vertical > _GEOMETRY_TOLERANCE and overlap.area > 0:
            raise SimulationError(f"3D field material volumes overlap: {a.id} and {b.id}; repair the literal stack instead of using a sheet/effective overlap")
        if a.role != "conductor" or b.role != "conductor":
            continue
        if vertical > _GEOMETRY_TOLERANCE or overlap.area <= 0:
            raise SimulationError(f"Conductors {a.id} and {b.id} meet at an unsupported side/edge/point contact; define a finite physical connection")
        permitted = False
        for via, layer in ((a, b), (b, a)):
            if via.origin_id.startswith("interface:"):
                interface = interfaces[via.origin_id.removeprefix("interface:")]
                permitted |= layer.layer_id in {interface.layer_a, interface.layer_b}
        if a.layer_id and b.layer_id:
            for key, (footprint, low, high) in geometry.physical_connections.items():
                interface = interfaces[key]
                if (interface.model_kind == "physical_contact" and {a.layer_id, b.layer_id} == {interface.layer_a, interface.layer_b}
                        and abs(low - high) <= _GEOMETRY_TOLERANCE
                        and overlap.difference(footprint).area <= 1e-30):
                    permitted = True
        if not permitted:
            raise SimulationError(f"Conductor volumes {a.id} and {b.id} touch without a complete declared physical contact; projected/effective sheet connections are not a 3D field model")
        parent[find(first)] = find(second)
    groups = {}
    for index, solid in enumerate(solids):
        if solid.role == "conductor":
            groups.setdefault(find(index), []).append(solid.id)
    identifiers = {root: "conductor:" + _hash(sorted(keys))[:16] for root, keys in groups.items()}
    updated = tuple(replace(solid, component_id=identifiers[find(index)]) if solid.role == "conductor" else solid
                    for index, solid in enumerate(solids))
    return updated, tuple(sorted(identifiers.values()))


def _segment_prism(start, end, width, height, direction):
    delta = np.asarray(end) - start
    axes = np.flatnonzero(delta != 0)
    if len(axes) != 1:
        raise SimulationError("RL centerline segments must be nonzero and axis aligned; arbitrary diagonal/curved solids need a different field discretization")
    tangent = delta / np.linalg.norm(delta)
    w = np.asarray(direction)
    if (np.count_nonzero(w) != 1 or not np.isclose(np.linalg.norm(w), 1., rtol=0, atol=1e-14)
            or np.dot(tangent, w) != 0):
        raise SimulationError("RL width direction must be an orthogonal unit coordinate axis")
    h = np.cross(tangent, w)
    vertices = np.asarray([np.asarray(point) + sw * width / 2 * w + sh * height / 2 * h
                           for point in (start, end) for sw in (-1, 1) for sh in (-1, 1)])
    low, high = vertices.min(axis=0), vertices.max(axis=0)
    return box(low[0], low[1], high[0], high[1]), float(low[2]), float(high[2])


def normalize_loop_entries(records, *, allow_empty=False):
    """Validate explicit bar paths and canonicalize only floating-point roundoff.

    Equivalent engineering-unit strings can differ by a few machine bits. Equal
    coordinates are joined within 64 epsilon times the coordinate/extent scale;
    this is never a layout-unit gap tolerance or inferred physical return.
    """
    if not isinstance(records, list) or not (0 if allow_empty else 1) <= len(records) <= 8:
        raise SimulationError("RL extraction needs 1–8 explicit closed forward/return loops, each with finite rectangular segment cross-sections")
    allowed_loop = {"id", "segments"}
    allowed_segment = {"start_m", "end_m", "width_m", "height_m", "width_direction", "support_ids"}
    result = []
    for record in records:
        if not isinstance(record, dict) or set(record) != allowed_loop:
            raise SimulationError("Each RL loop requires only id and an explicit segments list")
        label = _label(record["id"], "Loop ID")
        entries = record["segments"]
        if not isinstance(entries, list) or not 4 <= len(entries) <= 128:
            raise SimulationError(f"Loop {label}: provide 4–128 oriented finite segments including the complete return")
        parsed = []
        for entry in entries:
            if not isinstance(entry, dict) or set(entry) != allowed_segment:
                raise SimulationError(f"Loop {label}: every segment needs start_m/end_m/width_m/height_m/width_direction/support_ids")
            start, end = _point(entry["start_m"], "Start"), _point(entry["end_m"], "End")
            direction = _point(entry["width_direction"], "Width direction")
            width, height = _positive(entry["width_m"], "Width"), _positive(entry["height_m"], "Height")
            supports = entry["support_ids"]
            if not isinstance(supports, list) or not supports or len(supports) > _MAX_SOLIDS:
                raise SimulationError(f"Loop {label}: select actual finite conductor support IDs")
            for support in supports:
                _label(support, f"Loop {label} support ID")
            if len(set(supports)) != len(supports):
                raise SimulationError(f"Loop {label}: support IDs must not repeat")
            parsed.append({"start_m": list(start), "end_m": list(end), "width_m": width, "height_m": height,
                           "width_direction": list(direction), "support_ids": list(supports)})
        points = np.asarray([entry[key] for entry in parsed for key in ("start_m", "end_m")])
        scale = max(float(np.max(abs(points))), float(np.max(np.ptp(points,axis=0))))
        tolerance = 64*np.finfo(float).eps*scale
        canonical = []
        for axis in range(3):
            mapping, anchor = {}, None
            for value in sorted(set(points[:,axis])):
                if anchor is None or value-anchor > tolerance:
                    anchor = value
                mapping[value] = float(anchor)
            canonical.append(mapping)
        for entry in parsed:
            for key in ("start_m", "end_m"):
                entry[key] = [canonical[axis][value] for axis,value in enumerate(entry[key])]
            _segment_prism(entry["start_m"], entry["end_m"], entry["width_m"], entry["height_m"], entry["width_direction"])
        if any(a["end_m"] != b["start_m"] for a,b in zip(parsed, parsed[1:]+parsed[:1])):
            raise SimulationError(f"Loop {label}: endpoints must join in order and explicitly close the complete physical return; no external ground/zero-length ideal bridge is inferred")
        result.append({"id": label, "segments": parsed})
    if len({record["id"] for record in result}) != len(result):
        raise SimulationError("RL loop IDs must be unique")
    return result


def _loops(records, solids, materials, cancel):
    records = normalize_loop_entries(records)
    by_material = {material.id: material for material in materials}
    loops, used_components = [], set()
    for record in records:
        checkpoint(cancel)
        label = record["id"]
        segments, components, prisms = [], set(), []
        for entry in record["segments"]:
            checkpoint(cancel)
            start, end = tuple(entry["start_m"]), tuple(entry["end_m"])
            direction = tuple(entry["width_direction"])
            width, height, supports = entry["width_m"], entry["height_m"], entry["support_ids"]
            footprint, bottom, top = _segment_prism(start, end, width, height, direction)
            selected = [solid for solid in solids if solid.role == "conductor"
                        and (solid.id in supports or solid.origin_id in supports)]
            unknown = set(supports) - {key for solid in selected for key in (solid.id, solid.origin_id)}
            if unknown:
                raise SimulationError(f"Loop {label}: unknown/nonconducting supports {sorted(unknown)}")
            planes = sorted({bottom, top, *(z for solid in selected for z in (solid.z_bottom_m, solid.z_top_m) if bottom < z < top)})
            touched = set()
            for low, high in zip(planes, planes[1:]):
                middle = (low + high) / 2
                covering = [solid for solid in selected if solid.z_bottom_m <= middle <= solid.z_top_m]
                union = unary_union([solid.polygon for solid in covering])
                if footprint.difference(union.buffer(_GEOMETRY_TOLERANCE)).area > 1e-30:
                    raise SimulationError(f"Loop {label}: the complete finite segment cross-section leaves its actual source volumes; fix its path/width/height/supports")
                touched.update(solid.id for solid in covering if solid.polygon.intersection(footprint).area > 0)
            source = [solid for solid in selected if solid.id in touched]
            sigma = [by_material[solid.material_id].conductivity_S_per_m for solid in source]
            if not sigma or any(value is None for value in sigma) or not np.allclose(sigma, sigma[0], rtol=64*np.finfo(float).eps, atol=0):
                raise SimulationError(f"Loop {label}: split segments at material boundaries; each segment needs one explicit bulk conductivity")
            components.update(solid.component_id for solid in source)
            segments.append(Segment(start, end, width, height, direction, float(sigma[0]), tuple(sorted(touched))))
            prisms.append((footprint, bottom, top))
        for first, second in combinations(range(len(prisms)), 2):
            if second == first + 1 or (first == 0 and second == len(prisms)-1):
                continue
            a, za, ta = prisms[first]
            b, zb, tb = prisms[second]
            if min(ta, tb) >= max(za, zb) and a.intersects(b):
                raise SimulationError(f"Loop {label}: nonadjacent finite segments intersect/touch and create an ambiguous self-short")
        if len(components) != 1 or components & used_components:
            raise SimulationError("Independent RL loops must use separate connected physical conductor components; a shared plane/body needs a coupled network-current basis")
        component = next(iter(components))
        bodies = [solid for solid in solids if solid.component_id == component]
        planes = sorted({z for solid in bodies for z in (solid.z_bottom_m, solid.z_top_m)}
                        | {z for _, bottom, top in prisms for z in (bottom, top)})
        for low, high in zip(planes, planes[1:]):
            checkpoint(cancel)
            middle = (low + high) / 2
            source = unary_union([solid.polygon for solid in bodies if solid.z_bottom_m < middle < solid.z_top_m])
            modeled = unary_union([footprint for footprint, bottom, top in prisms if bottom < middle < top])
            if source.difference(modeled.buffer(_GEOMETRY_TOLERANCE)).area > 1e-30:
                raise SimulationError(f"Loop {label}: finite segment prisms must cover the whole connected source conductor; omitted metal/parallel paths require a full-volume current discretization, not an invented centerline in a plane")
        used_components.update(components)
        loops.append(Loop(label, tuple(segments), component))
    if used_components != {solid.component_id for solid in solids if solid.role == "conductor"}:
        raise SimulationError("Every conductive component must belong to an explicit RL loop; omitted conductors can carry induced currents and cannot be silently excluded from the field model")
    return tuple(loops)


def build_field_snapshot(project, settings=None, *, kind, temperature_K=None, cancel=None, progress=None):
    """Resolve and validate a literal 3D field problem without modifying a project.

    First scope: homogeneous electrostatic PEC conductors relative to infinity,
    or nonmagnetic rectilinear finite bulk-conductor closed-loop RL. Unsupported
    heterogeneous media, sheet channels and effective bridges fail explicitly.
    """
    checkpoint(cancel, progress, 0., "Validating explicit 3D field inputs")
    if kind not in {"capacitance", "loop_rl"}:
        raise SimulationError("Field kind must be capacitance or loop_rl")
    settings = deepcopy(getattr(project, "parasitics", {}) if settings is None else settings)
    if not isinstance(settings, dict) or settings.get("schema_version") != 1 or isinstance(settings.get("schema_version"), bool):
        raise SimulationError("Configure explicit parasitic settings with schema_version 1 before field extraction")
    if set(settings) - {"schema_version", "background_relative_permittivity", "background_relative_permeability", "loops", "material_sources"}:
        raise SimulationError("Unknown parasitic settings; supported keys are schema_version, background_relative_permittivity, background_relative_permeability, loops and material_sources")
    field_sources = settings.get("material_sources", {})
    if (not isinstance(field_sources, dict) or any(key not in project.materials for key in field_sources)
            or any(not isinstance(value, str) or not value.strip() or len(value) > 4096 for value in field_sources.values())):
        raise SimulationError("Material field sources must map existing material IDs to nonempty source/assumption text")
    epsilon = _positive(settings.get("background_relative_permittivity") if kind == "capacitance"
                        else settings.get("background_relative_permittivity", 1.), "Explicit background relative permittivity")
    mu = _positive(settings.get("background_relative_permeability"), "Explicit background relative permeability") if kind == "loop_rl" else 1.
    if kind == "loop_rl" and not math.isclose(mu, 1., rel_tol=64*np.finfo(float).eps, abs_tol=0):
        raise SimulationError("The first RL engine supports only an explicitly nonmagnetic μr=1 environment; magnetic media need another field model")
    temperature = _positive(project.solver.initial_temperature if temperature_K is None else temperature_K, "Fixed field temperature in K")
    from etlayout.simulation.runner import _verify_layout
    clone = deepcopy(project)
    _verify_layout(clone)
    source_context = {"configuration_hash": configuration_hash(clone), "physics_hash": physics_hash(clone),
                      "layout_sha256": clone.layout.sha256, "temperature_K": temperature,
                      "reference": {"id": "environment", "kind": "infinity"} if kind == "capacitance" else {"kind": "explicit_closed_loop_cut"},
                      "assumptions": ["Whole connected metal volumes are ideal equipotentials for electrostatics; no semiconductor depletion/quantum capacitance."]
                        if kind == "capacitance" else ["Explicit finite rectangular bulk-conductor paths with complete returns in a nonmagnetic environment; no inferred external wiring."]}
    if kind == "loop_rl" and "background_relative_permittivity" not in settings:
        source_context["assumptions"].append("Background relative permittivity defaults explicitly to vacuum εr=1 for the electrical-size validity check; no dielectric loss is modeled.")
    geometry = prepare_geometry(clone, progress=lambda value, message: checkpoint(cancel, progress, .1 + .25*value, message), cancel=cancel)
    _verify_layout(clone)
    if any(item.severity == "error" for item in geometry.diagnostics):
        raise SimulationError("Repair the project geometry before literal 3D field extraction")
    if any(interface.enabled and interface.electrical_kind != "none" and interface.model_kind == "effective" for interface in clone.interfaces):
        raise SimulationError("Effective electrical interfaces are sheet laws, not finite 3D bodies; define the actual physical contact/via geometry for field extraction")
    solids = []
    for layer in clone.layers:
        checkpoint(cancel)
        if layer.role == "conductor" and layer.electrical.kind != "bulk":
            raise SimulationError(f"Layer {layer.id}: sheet/HEMT/semiconductor laws do not define a bulk PEC/RL volume; use a separate explicitly modeled metal interconnect geometry")
        material = clone.materials[layer.material_id]
        if layer.role == "conductor" and any(value != 1. for value in material.electrical_anisotropy):
            raise SimulationError(f"Layer {layer.id}: anisotropic conductor field extraction is unsupported; no isotropic average is substituted")
        solids.extend(_parts("layer:" + layer.id, layer.id, layer.material_id, layer.role, geometry.layers[layer.id], layer.z, layer.z + layer.thickness))
    for key, (footprint, bottom, top) in geometry.physical_connections.items():
        interface = next(item for item in clone.interfaces if item.id == key)
        if interface.model_kind == "physical_via":
            if not interface.material_id:
                raise SimulationError(f"Physical via {key}: assign an explicit bulk material before field extraction")
            solids.extend(_parts("interface:" + key, "", interface.material_id, "conductor", footprint, bottom, top))
        elif kind == "loop_rl" and interface.electrical_kind not in {"ideal", "none"}:
            raise SimulationError(f"Physical contact {key}: zero-thickness finite contact resistance is not a bulk RL segment; model its physical finite material or keep it separate")
    if len(solids) > _MAX_SOLIDS or sum(len(s.exterior_m)+sum(map(len, s.holes_m)) for s in solids) > 100000:
        raise SimulationError("Field snapshot exceeds 512 solids or 100,000 boundary vertices; select a smaller complete field geometry")
    solids, components = _connected_components(solids, clone, geometry, cancel)
    if not components or len(components) > 128:
        raise SimulationError("Field extraction requires 1–128 complete connected conductor components")
    materials = []
    for key in sorted({solid.material_id for solid in solids}):
        checkpoint(cancel)
        material = clone.materials[key]
        used_as_dielectric = any(s.material_id == key and s.role != "conductor" for s in solids)
        used_as_conductor = any(s.material_id == key and s.role == "conductor" for s in solids)
        ep, ev = (_coefficient(material, "permittivity", temperature, required=True)
                  if kind == "capacitance" and used_as_dielectric else (None, ()))
        pm, mv = _coefficient(material, "permeability", temperature, required=True) if kind == "loop_rl" else (None, ())
        rho, rv = (_coefficient(material, "resistivity", temperature, required=True)
                   if kind == "loop_rl" and used_as_conductor else (None, ()))
        if (ev or mv) and not field_sources.get(key):
            raise SimulationError(f"Material {key}: provide a separate material_sources reference/assumption for ε/μ; existing transport references do not establish electromagnetic properties")
        if kind == "capacitance" and used_as_dielectric and not math.isclose(ep, epsilon * EPSILON_0, rel_tol=64*np.finfo(float).eps, abs_tol=0):
            raise SimulationError(f"Material {key}: this electrostatic backend supports only the explicitly homogeneous background ε; heterogeneous dielectric interfaces are unsupported and are not averaged")
        if kind == "loop_rl" and not math.isclose(pm, MU_0, rel_tol=64*np.finfo(float).eps, abs_tol=0):
            raise SimulationError(f"Material {key}: explicitly nonmagnetic μ=4π×10⁻⁷ H/m is required; magnetic media are unsupported")
        materials.append(EvaluatedMaterial(key, ep, pm, 1/rho if kind == "loop_rl" and used_as_conductor else None,
                                          material.source, field_sources.get(key, ""), tuple(value for value in (ev, mv, rv) if value)))
    loops = _loops(settings.get("loops"), solids, materials, cancel) if kind == "loop_rl" else ()
    geometry_hash = _hash([asdict(solid) for solid in solids])
    model_hash = _hash({"kind": kind, "geometry": geometry_hash, "settings": settings,
                        "materials": [asdict(material) for material in materials], "temperature_K": temperature})
    result = FieldSnapshot(kind, solids, components, tuple(materials), loops, temperature, epsilon, mu,
                           json.dumps(source_context, sort_keys=True, allow_nan=False), geometry_hash, model_hash)
    checkpoint(cancel, progress, 1., "Literal 3D field snapshot verified")
    return result


def _component_faces(solids, mesh_size, max_faces):
    """Boundary polygons of the exact union; internal via/plate faces disappear."""
    source_planes = sorted({z for solid in solids for z in (solid.z_bottom_m, solid.z_top_m)})
    planes = [source_planes[0]]
    for low, high in zip(source_planes, source_planes[1:]):
        divisions = _mesh_divisions(high-low,mesh_size)
        if len(planes) + divisions > max_faces:
            raise SimulationError("Closed field surface exceeds its panel budget before vertical face allocation; increase surface size")
        planes.extend(low+(high-low)*index/divisions for index in range(1, divisions))
        planes.append(high)
    slabs = [unary_union([solid.polygon for solid in solids if solid.z_bottom_m < (low+high)/2 < solid.z_top_m])
             for low, high in zip(planes, planes[1:])]
    faces = []
    for index, (low, high, footprint) in enumerate(zip(planes, planes[1:], slabs)):
        previous = slabs[index-1] if index else GeometryCollection()
        following = slabs[index+1] if index+1 < len(slabs) else GeometryCollection()
        for z, region, sign in ((low, footprint.difference(previous), -1), (high, footprint.difference(following), 1)):
            for part in polygon_parts(region):
                if part.is_empty or part.area <= 0:
                    continue
                part = orient(part, sign=1.)
                faces.append((tuple((x, y, z) for x, y in part.exterior.coords),
                              tuple(tuple((x, y, z) for x, y in ring.coords) for ring in part.interiors), (0., 0., float(sign))))
        for part in polygon_parts(footprint):
            part = orient(part, sign=1.)
            for ring in (part.exterior, *part.interiors):
                for a, b in zip(ring.coords, list(ring.coords)[1:]):
                    dx, dy = b[0]-a[0], b[1]-a[1]
                    divisions = _mesh_divisions(math.hypot(dx,dy),mesh_size)
                    if len(faces) + divisions > max_faces:
                        raise SimulationError("Closed field surface exceeds its panel budget before wall face allocation; increase surface size")
                    for step in range(divisions):
                        x0,y0 = a if step == 0 else (a[0]+dx*step/divisions, a[1]+dy*step/divisions)
                        x1,y1 = b if step+1 == divisions else (a[0]+dx*(step+1)/divisions, a[1]+dy*(step+1)/divisions)
                        faces.append((((x0,y0,low), (x1,y1,low), (x1,y1,high), (x0,y0,high), (x0,y0,low)),
                                      (), (dy, -dx, 0.)))
    return faces, sum((high-low)*footprint.area for low, high, footprint in zip(planes, planes[1:], slabs))


def triangulate_conductors(snapshot, mesh_size_m, *, max_panels=100000, cancel=None, progress=None):
    """Closed outward triangular surfaces, preserving holes and physical via joins.

    Collinear shared edge vertices are retained before a common dyadic triangle
    refinement. Manifold edge incidence and per-body volume are independently
    checked. A target below the panel budget fails before refinement allocation.
    """
    mesh_size = _positive(mesh_size_m, "Surface mesh size in m")
    if type(max_panels) is not int or not 1 <= max_panels <= 1000000:
        raise SimulationError("Surface panel budget must be an integer from 1 to 1,000,000")
    try:
        from shapely import constrained_delaunay_triangles
    except ImportError as error:
        raise SimulationError("Closed field surface triangulation requires Shapely 2.1 or newer") from error
    surfaces, used = [], 0
    for component_index, component in enumerate(snapshot.components):
        checkpoint(cancel, progress, component_index / len(snapshot.components), "Triangulating complete conductor " + component)
        solids = [solid for solid in snapshot.solids if solid.component_id == component]
        faces, expected_volume = _component_faces(solids, mesh_size, max_panels-used)
        boundary_points = np.asarray(sorted({point for exterior, holes, _ in faces for ring in (exterior, *holes) for point in ring[:-1]}))
        triangles = []
        for exterior, holes, normal in faces:
            checkpoint(cancel)
            def expanded(ring, boundary_points=boundary_points):
                result = []
                for a, b in zip(ring, ring[1:]):
                    start, delta = np.asarray(a), np.asarray(b)-a
                    length2 = float(delta @ delta)
                    if not length2:
                        raise SimulationError("Degenerate field boundary edge")
                    t = (boundary_points-start) @ delta / length2
                    error = np.linalg.norm(boundary_points - (start+t[:,None]*delta), axis=1)
                    selected = boundary_points[(t >= 0) & (t < 1) & (error <= _GEOMETRY_TOLERANCE)]
                    result.extend(map(tuple, sorted(selected, key=lambda p: np.dot(np.asarray(p)-start, delta))))
                return tuple((*result, result[0]))
            exterior = expanded(exterior)
            holes = tuple(expanded(ring) for ring in holes)
            origin = np.asarray(exterior[0])
            u = np.asarray(exterior[1])-origin
            u /= np.linalg.norm(u)
            n = np.asarray(normal, dtype=float)
            n /= np.linalg.norm(n)
            v = np.cross(n, u)
            def local(ring, origin=origin, u=u, v=v):
                return [(float(np.dot(np.asarray(p)-origin, u)), float(np.dot(np.asarray(p)-origin, v))) for p in ring]
            polygon = Polygon(local(exterior), [local(ring) for ring in holes])
            for triangle in polygon_parts(constrained_delaunay_triangles(polygon)):
                points = np.asarray([origin+x*u+y*v for x,y in list(triangle.exterior.coords)[:3]])
                if np.dot(np.cross(points[1]-points[0], points[2]-points[0]), n) < 0:
                    points = points[[0,2,1]]
                triangles.append(points)
        longest = max(np.linalg.norm(triangle[i]-triangle[(i+1)%3]) for triangle in triangles for i in range(3))
        refinement = (_mesh_divisions(longest,mesh_size)-1).bit_length()
        required = len(triangles) * 4**refinement
        if required + used > max_panels:
            raise SimulationError(f"Closed field surface needs {required+used:,} triangular panels, exceeding budget {max_panels:,}; increase surface size or review the explicit budget")
        for _ in range(refinement):
            checkpoint(cancel)
            refined = []
            for a,b,c in triangles:
                ab,bc,ca = (a+b)/2, (b+c)/2, (c+a)/2
                refined.extend(((a,ab,ca), (ab,b,bc), (ca,bc,c), (ab,bc,ca)))
            triangles = refined
        points, identifiers, connectivity = [], {}, []
        for triangle in triangles:
            row = []
            for point in triangle:
                key = tuple(np.round(point, 15))
                if key not in identifiers:
                    identifiers[key] = len(points)
                    points.append(tuple(map(float, point)))
                row.append(identifiers[key])
            connectivity.append(tuple(row))
        edges = {}
        for row in connectivity:
            for a,b in zip(row, (*row[1:], row[0])):
                key = (min(a,b),max(a,b))
                edges.setdefault(key, []).append(1 if a < b else -1)
        if any(len(directions) != 2 or sum(directions) != 0 for directions in edges.values()):
            raise SimulationError("Closed field surface is not a conforming outward manifold; this geometry needs another supported discretization")
        xyz = np.asarray(points)
        xyz -= xyz.min(axis=0)
        xyz = xyz[np.asarray(connectivity)]
        volume = float(np.einsum("ij,ij->i", xyz[:,0], np.cross(xyz[:,1], xyz[:,2])).sum()/6)
        if not math.isclose(volume, expected_volume, rel_tol=1e-8, abs_tol=0):
            raise SimulationError("Closed field surface does not reproduce its source solid volume; no repaired/approximate surface is exported")
        used += len(connectivity)
        surfaces.append(Surface(component, tuple(points), tuple(connectivity)))
    data = [asdict(surface) for surface in surfaces]
    result = SurfaceMesh(tuple(surfaces), _hash(data), snapshot.geometry_sha256, mesh_size)
    checkpoint(cancel, progress, 1., "Closed field surface verified")
    return result
