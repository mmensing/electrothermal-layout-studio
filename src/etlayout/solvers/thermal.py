# SPDX-License-Identifier: MPL-2.0
"""Thickness-integrated lateral heat flow and conservative vertical exchange."""
from __future__ import annotations

import time
from dataclasses import dataclass

import numpy as np
from scipy.sparse import diags

from etlayout.materials.models import evaluate_property
from etlayout.models import (
    Cancel,
    Diagnostic,
    PlanarMesh,
    Progress,
    Project,
    SimulationError,
    SimulationResult,
    checkpoint,
)
from etlayout.simulation.execution import map_independent, record_phase

from .electrical import _interface_elements
from .fem import Assembly, Space, check_references, layer_matrix, linear_solve, mass, stiffness


@dataclass
class ThermalState:
    result: SimulationResult
    values: np.ndarray
    space: Space


def heat_capacity(project: Project, space: Space):
    """P1 thermal capacitance in J/K; optional row-sum lumping conserves mass."""
    assembly = Assembly(space.size)
    for layer_id, layer_space in space.layers.items():
        layer = project.layer(layer_id)
        material = project.materials[layer.material_id]
        properties = [material.density, material.specific_heat, layer.thickness]
        capacity = material.density * material.specific_heat * layer.thickness
        if not np.all(np.isfinite(properties)) or min(properties) <= 0 or not np.isfinite(capacity):
            raise SimulationError(f"Layer {layer_id}: density, specific heat and thickness must be positive and finite")
        assembly.add(layer_space.dofs, layer_space.dofs,
                     mass(space.areas[layer_space.elements]) * capacity)
    matrix = assembly.matrix()
    if project.transient.capacity_scheme == "lumped":
        return diags(np.asarray(matrix.sum(axis=1)).ravel(), format="csr")
    if project.transient.capacity_scheme != "consistent":
        raise SimulationError("Transient capacity scheme must be consistent or lumped")
    return matrix


def _transient_bounds(project, matrix, values, previous, source, boundaries):
    """Check sufficient matrix signs and applicable physical temperature bounds.

    Conservation and nonlinear convergence do not imply a discrete maximum
    principle. Row-sum capacity lumping removes mass off-diagonal couplings,
    but obtuse/anisotropic elements and exchange terms can still violate the
    sign condition; report both that condition and actual bound excursions.
    """
    coefficients = matrix.tocoo()
    scale = float(np.max(np.abs(coefficients.data), initial=0.))
    positive = int(np.count_nonzero((coefficients.row != coefficients.col)
                                   & (coefficients.data > 128 * np.finfo(float).eps * scale)))
    reservoirs = [boundary.value if boundary.kind == "temperature" else boundary.ambient
                  for boundary in boundaries if boundary.kind != "insulated"]
    lower = min(float(previous.min()), *reservoirs) if reservoirs else float(previous.min())
    upper = max(float(previous.max()), *reservoirs) if reservoirs else float(previous.max())
    lower = lower if np.all(source >= 0.) else None
    upper = upper if np.all(source <= 0.) else None
    undershoot = max(0., lower - float(values.min())) if lower is not None else 0.
    overshoot = max(0., float(values.max()) - upper) if upper is not None else 0.
    tolerance = max(project.solver.absolute_tolerance,
                    project.solver.relative_tolerance * float(np.max(np.abs(values))))
    metrics = {"thermal_capacity_scheme": project.transient.capacity_scheme,
               "thermal_positive_offdiagonal_count": positive,
               "thermal_monotonicity_sign_condition": positive == 0,
               "thermal_expected_min": lower, "thermal_expected_max": upper,
               "thermal_bound_undershoot": undershoot, "thermal_bound_overshoot": overshoot,
               "thermal_bound_tolerance": tolerance,
               "thermal_bound_violation": max(undershoot, overshoot) > tolerance}
    diagnostics = []
    if metrics["thermal_bound_violation"]:
        diagnostics.append(Diagnostic("warning", "Transient temperature violates an applicable physical bound "
            f"(undershoot {undershoot:.6g} K, overshoot {overshoot:.6g} K). Energy conservation does not guarantee "
            "monotonicity; check space/time convergence and the thermal capacity scheme. Fields were not clipped."))
    return metrics, diagnostics


def _thermal_interface(project: Project, interface, a_temp: np.ndarray, b_temp: np.ndarray) -> np.ndarray:
    if interface.thermal_kind == "conductance":
        g = np.full_like(a_temp, interface.thermal_value)
    elif interface.thermal_kind == "resistance":
        g = np.full_like(a_temp, 1. / interface.thermal_value if interface.thermal_value > 0 else -1.)
    elif interface.thermal_kind == "stack":
        resistance = np.full_like(a_temp, interface.thermal_resistance_area)
        if interface.thermal_resistance_area < 0:
            raise SimulationError(f"Interface {interface.id}: area resistance cannot be negative")
        for layer_id, temperature in ((interface.layer_a, a_temp), (interface.layer_b, b_temp)):
            layer = project.layer(layer_id)
            material = project.materials[layer.material_id]
            kz = (material.thermal_conductivity_z if material.thermal_conductivity_z is not None
                  else evaluate_property(material.thermal_conductivity, temperature))
            if np.any(np.asarray(kz) <= 0):
                raise SimulationError(f"Material {material.id}: vertical conductivity must be positive")
            resistance += layer.thickness / (2. * kz)
        if interface.material_id:
            if interface.material_id not in project.materials or interface.height < 0:
                raise SimulationError(f"Interface {interface.id}: invalid gap material/height")
            material = project.materials[interface.material_id]
            kz = (material.thermal_conductivity_z if material.thermal_conductivity_z is not None else
                  evaluate_property(material.thermal_conductivity, (a_temp + b_temp) / 2.))
            if np.any(np.asarray(kz) <= 0):
                raise SimulationError(f"Material {material.id}: vertical conductivity must be positive")
            resistance += interface.height / kz
        g = 1. / resistance
    else:
        raise SimulationError(f"Unknown thermal interface kind {interface.thermal_kind!r}")
    if np.any(~np.isfinite(g)) or np.any(g <= 0):
        raise SimulationError(f"Interface {interface.id}: thermal conductance must be finite and positive")
    return g


def solve_state(project: Project, mesh: PlanarMesh, heat: dict[str, np.ndarray] | None = None,
                temperatures: dict[str, np.ndarray] | None = None,
                progress: Progress | None = None, cancel: Cancel | None = None,
                space: Space | None = None, evaluate_only: bool = False, *,
                previous_values: np.ndarray | None = None, time_step: float | None = None,
                capacity_matrix=None) -> ThermalState:
    space = space or Space(project, mesh, electrical=False)
    settings = project.solver
    reference = settings.initial_temperature
    values = space.values_from_fields(temperatures, reference)
    if np.any(values <= 0):
        raise SimulationError("Absolute temperatures must be positive")
    transient = previous_values is not None
    if transient:
        if time_step is None or not np.isfinite(time_step) or time_step <= 0:
            raise SimulationError("Transient thermal step must be finite and positive")
        previous_values = np.asarray(previous_values)
        if previous_values.shape != (space.size,) or np.any(~np.isfinite(previous_values)) or np.any(previous_values <= 0):
            raise SimulationError("Transient previous temperatures must match thermal degrees of freedom")
        capacity_matrix = heat_capacity(project, space) if capacity_matrix is None else capacity_matrix
        capacity_rate = capacity_matrix / time_step
    elif time_step is not None:
        raise SimulationError("A transient time step requires previous temperatures")
    source = np.zeros(space.size)
    layer_heat = {key: np.zeros(len(mesh.triangles)) for key in space.layers}
    if heat:
        for layer_id, q in heat.items():
            if layer_id not in space.layers:
                raise SimulationError(f"Heating layer {layer_id} has no active thermal mesh")
            e = space.layers[layer_id].elements
            layer_heat[layer_id][e] += np.nan_to_num(np.asarray(q)[e], nan=0.)
    for item in project.heat_sources:
        if item.layer_id not in space.layers:
            raise SimulationError(f"Heat source {item.id}: inactive thermal layer")
        elements, _ = space.region(item.layer_id, "heat", item.id)
        area = float(space.areas[elements].sum())
        if area <= 0:
            raise SimulationError(f"Heat source {item.id}: empty area")
        if item.kind == "density":
            density = item.value
        elif item.kind == "total":
            density = item.value / area
        else:
            raise SimulationError(f"Unknown heat source kind {item.kind!r}")
        layer_heat[item.layer_id][elements] += density
    for layer_id, layer_space in space.layers.items():
        e, dofs = layer_space.elements, layer_space.dofs
        np.add.at(source, dofs.ravel(), np.repeat(layer_heat[layer_id][e] * space.areas[e] / 3., 3))
    boundary_ass = Assembly(space.size)
    rhs = source.copy()
    sinks = np.zeros(space.size)
    fixed: dict[int, float] = {}
    fixed_by_boundary: dict[str, np.ndarray] = {}
    exchange_boundaries = []
    for boundary in project.thermal_boundaries:
        if boundary.kind == "insulated":
            continue
        if boundary.layer_id not in space.layers:
            raise SimulationError(f"Thermal boundary {boundary.id}: inactive layer")
        if boundary.kind == "temperature":
            nodes = space.nodes_for(boundary.layer_id, "thermal", boundary.id)
            if not len(nodes):
                raise SimulationError(f"Thermal boundary {boundary.id}: empty temperature selection")
            if boundary.value <= 0:
                raise SimulationError(f"Thermal boundary {boundary.id}: temperature must be above zero kelvin")
            for node in nodes:
                if int(node) in fixed and not np.isclose(fixed[int(node)], boundary.value - reference, atol=1e-10):
                    raise SimulationError(f"Conflicting temperatures at boundary {boundary.id}")
                fixed[int(node)] = boundary.value - reference
            fixed_by_boundary[boundary.id] = nodes
            continue
        if boundary.value <= 0 or boundary.ambient <= 0:
            raise SimulationError(f"Thermal boundary {boundary.id}: coefficient/resistance and ambient must be positive")
        elements, dofs = space.region(boundary.layer_id, "thermal", boundary.id)
        if len(elements):
            measures = space.areas[elements]
            blocks = mass(measures)
        else:
            dofs, lengths = space.edge_measure(boundary.layer_id, "thermal", boundary.id)
            measures = lengths * project.layer(boundary.layer_id).thickness
            blocks = measures[:, None, None] * (np.ones((2, 2)) + np.eye(2))[None] / 6.
        area = float(measures.sum())
        if boundary.kind == "coefficient":
            g = boundary.value
        elif boundary.kind == "resistance_area":
            g = 1. / boundary.value
        elif boundary.kind == "resistance":
            g = 1. / (boundary.value * area)
        else:
            raise SimulationError(f"Unknown thermal boundary kind {boundary.kind!r}")
        blocks = blocks * g
        loads = blocks.sum(axis=2)
        boundary_ass.add(dofs, dofs, blocks)
        np.add.at(rhs, dofs.ravel(), (loads * (boundary.ambient - reference)).ravel())
        np.add.at(sinks, dofs.ravel(), loads.ravel())
        exchange_boundaries.append((boundary, dofs, blocks))
    boundary_matrix = boundary_ass.matrix()
    if transient:
        rhs += capacity_rate @ (previous_values - reference)
    nonlinear = any(project.materials[project.layer(layer_id).material_id].thermal_conductivity.kind != "constant"
                    for layer_id in space.layers)
    nonlinear |= any(i.enabled and i.thermal_kind == "stack" and i.material_id and
                     project.materials[i.material_id].thermal_conductivity.kind != "constant" for i in project.interfaces)

    def operator_key():
        # This space already owns an immutable mesh. Include every numerical
        # diffusion/interface input so supported changes invalidate the cache.
        layers = [(key, project.layer(key).thickness, project.layer(key).material_id,
                   repr(project.materials[project.layer(key).material_id].thermal_conductivity),
                   tuple(project.materials[project.layer(key).material_id].thermal_anisotropy),
                   project.materials[project.layer(key).material_id].thermal_conductivity_z)
                  for key in space.layers]
        interfaces = [(i.id, i.layer_a, i.layer_b, i.thermal_kind, i.thermal_value,
                       i.material_id, i.height, i.thermal_resistance_area,
                       repr(project.materials[i.material_id].thermal_conductivity)
                       if i.thermal_kind == "stack" and i.material_id else None,
                       project.materials[i.material_id].thermal_conductivity_z
                       if i.thermal_kind == "stack" and i.material_id else None)
                      for i in project.interfaces if i.enabled and i.thermal_kind != "none"]
        return repr((layers, interfaces))

    def assemble(temperature: np.ndarray):
        assembly_started = time.perf_counter()
        checkpoint(cancel)
        key = operator_key() if not nonlinear else None
        cached = getattr(space, "_thermal_operator", None)
        reuse = cached is not None and cached[0] == key and key is not None

        def prepare_layer(item):
            layer_id, layer_space = item
            layer = project.layer(layer_id)
            material = project.materials.get(layer.material_id)
            if material is None:
                raise SimulationError(f"Layer {layer_id}: missing material {layer.material_id}")
            if layer.thickness <= 0:
                raise SimulationError(f"Layer {layer_id}: thickness must be positive")
            e, dofs = layer_space.elements, layer_space.dofs
            anisotropy = np.asarray(material.thermal_anisotropy)
            if anisotropy.shape != (2,) or np.any(anisotropy <= 0):
                raise SimulationError(f"Material {material.id}: thermal anisotropy must contain two positive factors")
            k = evaluate_property(material.thermal_conductivity, temperature[dofs].mean(axis=1))
            if np.any(k <= 0) or np.any(~np.isfinite(k)):
                raise SimulationError(f"Material {material.id}: thermal conductivity must be finite and positive")
            if reuse:
                return None  # Still validate temperature/material ranges above.
            conductivity = np.asarray(k)[:, None] * layer.thickness * anisotropy
            ass = Assembly(len(layer_space.all_dofs))
            local_dofs = dofs - layer_space.all_dofs[0]
            ass.add(local_dofs, local_dofs, stiffness(space.areas[e], space.gradients[e], conductivity))
            return ass.matrix()

        jobs = list(space.layers.items())
        layers = map_independent(prepare_layer, jobs, work_units=sum(len(s.elements) for _, s in jobs))
        interfaces = []
        for interface in project.interfaces:
            if not interface.enabled or interface.thermal_kind == "none":
                continue
            e, a, b = _interface_elements(space, interface)
            interfaces.append((interface, e, a, b))

        def prepare_interface(item):
            interface, e, a, b = item
            g = _thermal_interface(project, interface, temperature[a].mean(axis=1), temperature[b].mean(axis=1))
            if reuse:
                return None
            ass = Assembly(space.size)
            blocks = mass(space.areas[e]) * g[:, None, None]
            ass.add(a, a, blocks)
            ass.add(b, b, blocks)
            ass.add(a, b, -blocks)
            ass.add(b, a, -blocks)
            return ass.matrix()

        transfers = map_independent(prepare_interface, interfaces, work_units=sum(len(e) for _, e, _, _ in interfaces))
        checkpoint(cancel)
        if reuse:
            matrix = cached[1]
        else:
            matrix = layer_matrix(layers, space.size)
            for transfer in transfers:
                matrix += transfer
            if key is not None:
                space._thermal_operator = key, matrix
        matrix = matrix + boundary_matrix
        assembled = matrix + capacity_rate if transient else matrix
        record_phase("thermal_assembly", time.perf_counter() - assembly_started)
        return assembled

    for node, value in fixed.items():
        values[node] = value + reference
    matrix = assemble(values)
    if not transient:
        check_references(matrix, rhs, fixed, thermal=True, sinks=sinks)
    free = np.ones(space.size, dtype=bool)
    free[list(fixed)] = False

    def residual_measure(temperature, mat):
        flow = mat @ (temperature - reference)
        r = flow - rhs
        scale = max(float(np.max(np.abs(rhs), initial=0)), float(np.max(np.abs(flow), initial=0)), 1e-30)
        return float(np.max(np.abs(r[free]), initial=0) / scale)

    converged = False
    iterations = []
    # Retaining a large sparse LU can overlap the next electrical factorization.
    # Keep one thermal factor only for bounded spaces; larger jobs retain the
    # existing solve-and-release behavior rather than increasing their peak RSS.
    factor_cache = None
    if space.size <= 250_000:
        if not hasattr(space, "_thermal_factor"):
            space._thermal_factor = {}
        factor_cache = space._thermal_factor
    for iteration in range(0 if evaluate_only else (settings.max_iterations if nonlinear else 1)):
        checkpoint(cancel, progress, .1 + .8 * iteration / settings.max_iterations,
                   f"Thermal iteration {iteration + 1}")
        target = linear_solve(matrix, rhs, fixed, settings.linear_solver, settings.linear_tolerance,
                              factor_cache=factor_cache) + reference
        candidate = values + (settings.relaxation if nonlinear else 1.) * (target - values)
        for node, value in fixed.items():
            candidate[node] = value + reference
        if np.any(candidate <= 0) or np.any(~np.isfinite(candidate)):
            raise SimulationError("Thermal solution left the positive absolute-temperature range")
        delta = float(np.max(np.abs(candidate - values)))
        values = candidate
        # Constant operators reuse their buffers, while final-temperature
        # validity checks remain identical to nonlinear assembly.
        matrix = assemble(values)
        residual = residual_measure(values, matrix)
        iterations.append({"iteration": float(iteration + 1), "temperature_change": delta,
                           "thermal_residual": residual})
        temperature_tolerance = settings.absolute_tolerance + settings.relative_tolerance * float(np.max(np.abs(values)))
        if residual <= settings.relative_tolerance and (not nonlinear or delta <= temperature_tolerance):
            converged = True
            break
    residual = residual_measure(values, matrix)
    if evaluate_only:
        converged = residual <= settings.relative_tolerance
    result = SimulationResult(mesh=mesh, converged=converged, iterations=iterations)
    heat_out = 0.
    fluxes = {}
    reservoirs = {}
    for boundary, dofs, blocks in exchange_boundaries:
        flux = float(np.einsum("eij,ej->ei", blocks, values[dofs] - boundary.ambient).sum())
        fluxes[boundary.id] = flux
        heat_out += flux
        reservoirs[boundary.id] = {"temperature": boundary.ambient, "heat_out": flux,
                                   "kind": boundary.kind, "status": "available"}
    reaction = matrix @ (values - reference) - rhs
    claimed = set()
    for boundary_id, nodes in fixed_by_boundary.items():
        unique = [int(node) for node in nodes if int(node) not in claimed]
        claimed.update(unique)
        flux = -float(reaction[unique].sum())
        heat_out += flux
        fluxes[boundary_id] = flux
        boundary = next(item for item in project.thermal_boundaries if item.id == boundary_id)
        reservoirs[boundary_id] = {"temperature": boundary.value, "heat_out": flux,
                                  "kind": boundary.kind, "status": "available"}
    ambiguous_boundaries = set()
    fixed_counts = np.zeros(space.size, dtype=int)
    for nodes in fixed_by_boundary.values():
        fixed_counts[nodes] += 1
    for boundary_id, nodes in fixed_by_boundary.items():
        if np.any(fixed_counts[nodes] > 1):
            ambiguous_boundaries.add(boundary_id)
            reservoirs[boundary_id].update(heat_out=None, status="nonunique",
                reason="Coincident fixed-temperature constraints do not identify an individual reservoir reaction")
    thermal_power = float(source.sum())
    storage_rate = float(np.sum(capacity_rate @ (values - previous_values))) if transient else 0.
    # With zero internal generation, opposing thermal reservoirs can exchange
    # heat; their gross boundary flows provide the conservation denominator.
    scale = max(abs(thermal_power), abs(storage_rate), sum(abs(value) for value in fluxes.values()), 1e-30)
    imbalance = abs(thermal_power - heat_out - storage_rate)
    roundoff = 0.
    if transient:
        # During an adiabatic off interval all physical powers can be zero.
        # Sparse accumulation roundoff must not become a relative error of one.
        # Keep the measured imbalance, with an explicit arithmetic error floor.
        roundoff = float(128 * np.finfo(float).eps * (
            np.sum(np.abs(matrix.data)) * np.max(np.abs(values - reference)) + np.sum(np.abs(rhs))))
        scale = max(scale, roundoff / settings.thermal_balance_tolerance)
    balance = imbalance / scale
    for boundary_id in ambiguous_boundaries:
        fluxes[boundary_id] = None
    if ambiguous_boundaries:
        result.diagnostics.append(Diagnostic("warning", "Overlapping fixed-temperature reservoirs have nonunique individual heat flows; aggregate heat remains conserved"))
    maximum = float(values.max())
    minimum = float(values.min())
    hot_dof = int(np.argmax(values))
    hotspot = {}
    average_sum = 0.
    area_sum = 0.
    for layer_id, layer_space in space.layers.items():
        e, dofs = layer_space.elements, layer_space.dofs
        gradient = np.einsum("ei,eik->ek", values[dofs] - reference, space.gradients[e])
        gradients = np.full((len(mesh.triangles), 2), np.nan)
        gradients[e] = gradient
        density = np.full(len(mesh.triangles), np.nan)
        density[e] = layer_heat[layer_id][e]
        result.fields[layer_id] = {"temperature": space.nodal_field(layer_id, values),
                                   "thermal_gradient": gradients, "heat_density": density}
        average_sum += float(np.dot(values[dofs].mean(axis=1), space.areas[e]))
        area_sum += float(space.areas[e].sum())
        where = np.flatnonzero(layer_space.all_dofs == hot_dof)
        if len(where):
            point = mesh.points[layer_space.nodes[where[0]]]
            hotspot = {"layer": layer_id, "x": float(point[0]), "y": float(point[1]),
                       "z": project.layer(layer_id).z, "temperature": maximum}
    result.element_fields = {key: {"temperature": field} for key, field in space.element_node_fields(values).items()}
    result.analysis["reservoirs"] = reservoirs
    named = project.power.thermal_reference_boundary
    explicit = project.power.thermal_reference_temperature
    reference_method = "single_temperature_reservoir"
    if named:
        if named not in reservoirs:
            raise SimulationError(f"Thermal reference boundary {named!r} is not an active reservoir")
        ambient = float(reservoirs[named]["temperature"])
        reference_method = "named_boundary:" + named
    elif explicit is not None:
        if not np.isfinite(explicit) or explicit <= 0:
            raise SimulationError("Explicit thermal reference must be finite and positive")
        ambient = float(explicit)
        reference_method = "explicit_temperature"
    else:
        physical_temperatures = {float(item["temperature"]) for item in reservoirs.values()}
        ambient = next(iter(physical_temperatures)) if len(physical_temperatures) == 1 else None
        if ambient is None:
            reference_method = "unavailable: select a named reservoir or explicit reference"
    rise = maximum - ambient if ambient is not None else None
    unequal = len({float(item["temperature"]) for item in reservoirs.values()}) > 1
    operational = bool(named or explicit is not None)
    cooling = any(np.any(layer_heat[key][layer.elements] < 0) for key, layer in space.layers.items())
    resistance = (rise / thermal_power if rise is not None and rise >= 0 and thermal_power > 0 and not cooling
                  and (not unequal or operational) else None)
    result.analysis["thermal_reference"] = {"temperature": ambient, "method": reference_method,
        "thermal_resistance_kind": "operational rise / positive internal power" if unequal and operational else "single-reservoir rise / positive internal power",
        "power_denominator": thermal_power, "cooling_sources_present": cooling,
        "heat_sign": "positive out of modeled domain"}
    result.metrics.update(temperature_max=maximum, temperature_min=minimum,
                          temperature_average=average_sum / area_sum, temperature_rise=rise,
                          thermal_reference=ambient, thermal_power=thermal_power, heat_out=heat_out,
                          thermal_resistance=resistance,
                          thermal_balance_error=balance, thermal_residual=residual,
                          boundary_heat_flows=fluxes, hotspot=hotspot, thermal_dofs=space.size)
    if transient:
        result.metrics.update(thermal_storage_rate=storage_rate,
                              thermal_stored_energy=float(np.sum(capacity_matrix @ (values - reference))),
                              thermal_balance_absolute_error=imbalance,
                              thermal_balance_roundoff_allowance=roundoff,
                              thermal_resistance=None)
        # Bounds follow the elementwise physical source sign. Nodal load
        # averaging can conceal a locally negative source among positive ones.
        density = np.concatenate([layer_heat[key][layer.elements] for key, layer in space.layers.items()])
        bounds, diagnostics = _transient_bounds(project, matrix, values, previous_values, density,
                                                project.thermal_boundaries)
        result.metrics.update(bounds)
        result.diagnostics.extend(diagnostics)
    if not converged:
        result.diagnostics.append(Diagnostic("error", f"Thermal nonlinear solve did not converge: residual {residual:.6g}"))
    if balance > settings.thermal_balance_tolerance:
        result.converged = False
        result.diagnostics.append(Diagnostic("error", f"Thermal energy balance failed: relative error {balance:.6g}"))
    return ThermalState(result, values, space)
