# SPDX-License-Identifier: MPL-2.0
"""DC electrical assembly, exact electrode constraints and local HEMT Newton solve."""
from __future__ import annotations

import time
import warnings
from dataclasses import dataclass

import numpy as np
from scipy.sparse import csr_matrix
from scipy.sparse.csgraph import connected_components
from scipy.sparse.linalg import MatrixRankWarning, spsolve

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
from etlayout.physics.constitutive import ELEMENTARY_CHARGE, mobility, sheet_conductance
from etlayout.simulation.execution import map_independent, record_linear_solve, record_phase

from .fem import (
    Assembly,
    Space,
    UnionFind,
    check_references,
    layer_matrix,
    linear_solve,
    mass,
    reduce_matrix,
    reduce_vector,
    stiffness,
)


@dataclass
class ElectricalState:
    result: SimulationResult
    heat: dict[str, np.ndarray]
    values: np.ndarray
    space: Space
    matrix: csr_matrix | None = None  # final reduced operator, suitable for sparse port extraction
    mapping: np.ndarray | None = None
    fixed: dict[int, float] | None = None
    rhs: np.ndarray | None = None
    port_dofs: dict[str, int] | None = None


def _interface_elements(space: Space, interface) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    if interface.layer_a not in space.layers or interface.layer_b not in space.layers:
        raise SimulationError(f"Interface {interface.id}: selected physics is inactive on one connected layer")
    a, b = space.layers[interface.layer_a], space.layers[interface.layer_b]
    key = (interface.id, interface.layer_a, interface.layer_b)
    if key in space._interfaces:
        return space._interfaces[key]
    elements = np.asarray(space.mesh.regions.get(f"interface:{interface.id}", []), dtype=int)
    if not len(elements):
        raise SimulationError(f"Interface {interface.id}: empty meshed contact geometry")
    if np.any((elements < 0) | (elements >= len(space.mesh.triangles))):
        raise SimulationError(f"Interface {interface.id}: contact does not lie in both layers")
    ai, bi = a.global_to_local[elements], b.global_to_local[elements]
    if np.any(ai < 0) or np.any(bi < 0):
        raise SimulationError(f"Interface {interface.id}: contact does not lie in both layers")
    space._interfaces[key] = elements, a.dofs[ai], b.dofs[bi]
    return space._interfaces[key]


def _conductance(project: Project, interface, area: float, temperature: np.ndarray) -> np.ndarray:
    kind, value = interface.electrical_kind, interface.electrical_value
    if kind == "load":
        resistance = value * (1. + interface.temperature_coefficient * (temperature - interface.reference_temperature))
        if interface.reference_temperature <= 0 or np.any(resistance <= 0):
            raise SimulationError(f"Load {interface.id}: temperature-dependent specific resistance must remain positive")
        conductance = 1. / resistance
    elif kind == "specific":
        conductance = np.full_like(temperature, 1. / value if value > 0 else -1.)
    elif kind == "lumped":
        conductance = np.full_like(temperature, 1. / (value * area) if value > 0 else -1.)
    elif kind == "conductance":
        conductance = np.full_like(temperature, value)
    elif kind == "via":
        if interface.material_id not in project.materials or interface.height <= 0:
            raise SimulationError(f"Via {interface.id}: a material and positive height are required")
        rho = evaluate_property(project.materials[interface.material_id].resistivity, temperature)
        conductance = 1. / (rho * interface.height)
    else:
        raise SimulationError(f"Unknown electrical interface kind {kind!r}")
    if np.any(~np.isfinite(conductance)) or np.any(conductance <= 0):
        raise SimulationError(f"Interface {interface.id}: conductance/resistance must be finite and positive")
    return conductance


def _resistance_metrics(project, result, terminal_dofs, mapping, component_labels, port_dofs=None):
    """Resolve a voltage/current measurement within one actual electrical net."""
    allowed = {t.id for t in project.terminals if t.kind not in {"gate", "insulated"}}
    networks = {key: np.unique(component_labels[mapping[dofs]])
                for key, dofs in terminal_dofs.items() if key in allowed}
    records = {**result.terminals, **result.analysis.get("ports", {})}
    for key, dof in (port_dofs or {}).items():
        if key in networks:
            raise SimulationError(f"Port {key}: ID collides with a terminal ID")
        networks[key] = np.array([component_labels[mapping[dof]]])
    currents = {key: records[key]["current"] for key in networks}
    cutoff = max(max((abs(value) for value in currents.values()), default=0.) * 1e-8, 1e-30)
    active = [key for key, value in currents.items() if abs(value) > cutoff]
    pair = list(project.solver.resistance_terminals)
    current_terminal = project.solver.resistance_current_terminal
    method = "explicit" if pair or current_terminal else "automatic"

    def network(key):
        if key not in networks:
            raise SimulationError(f"Resistance measurement terminal {key!r} is missing or is a gate-control/insulated region")
        if len(networks[key]) != 1:
            raise SimulationError(f"Resistance measurement terminal {key!r} spans multiple independent networks")
        return int(networks[key][0])

    selected_net = network(current_terminal) if current_terminal else None
    if pair:
        if len(pair) != 2 or pair[0] == pair[1]:
            raise SimulationError("Resistance voltage measurement requires two distinct terminal IDs")
        first, second = network(pair[0]), network(pair[1])
        if first != second or (selected_net is not None and selected_net != first):
            raise SimulationError("Resistance voltage/current terminals must belong to the same electrical network")
        selected_net = first
    candidates = [key for key in active if len(networks[key]) == 1 and
                  (selected_net is None or int(networks[key][0]) == selected_net)]
    if not pair and len(candidates) == 2 and network(candidates[0]) == network(candidates[1]):
        pair = sorted(candidates, key=lambda key: records[key]["voltage"])
        selected_net = network(pair[0])
    if pair and not current_terminal and len(candidates) == 2:
        current_terminal = max(candidates, key=lambda key: currents[key])
    reference_current = abs(currents[current_terminal]) if current_terminal else None
    voltage_drop = abs(records[pair[1]]["voltage"] - records[pair[0]]["voltage"]) if pair else None
    resistance = (voltage_drop / reference_current if pair and reference_current is not None
                  and reference_current > cutoff else None)
    if resistance is None and active:
        result.diagnostics.append(Diagnostic("warning", "Effective resistance is undefined for this ambiguous or un-driven measurement. "
            "Select two resistance voltage terminals and, for a multiport/Kelvin setup, a current-reference terminal."))
    return dict(effective_resistance=resistance, resistance=resistance,
                voltage_drop=voltage_drop, resistance_current=reference_current,
                resistance_terminal_pair=pair, resistance_current_terminal=current_terminal,
                resistance_measurement={"voltage_terminals": pair, "current_terminal": current_terminal,
                                        "network": selected_net, "method": method,
                                        "definition": "abs(Vb - Va) / abs(Icurrent_terminal)"})


def solve_state(project: Project, mesh: PlanarMesh, temperatures: dict[str, np.ndarray] | None = None,
                progress: Progress | None = None, cancel: Cancel | None = None,
                space: Space | None = None, initial: np.ndarray | None = None) -> ElectricalState:
    space = space or Space(project, mesh, electrical=True)
    settings = project.solver
    temperature = space.values_from_fields(temperatures, settings.initial_temperature)
    if np.any(~np.isfinite(temperature)) or np.any(temperature <= 0):
        raise SimulationError("Electrical material temperatures must be finite and above zero kelvin")
    from .power import contact_islands, prepare_ports

    port_setup = prepare_ports(project, space)
    size = space.size + len(port_setup.nodes)
    union = UnionFind(size)
    contact_assembly = Assembly(size)
    contacts = []
    ideal_contacts = []
    for interface in project.interfaces:
        checkpoint(cancel)
        if not interface.enabled or interface.electrical_kind == "none":
            continue
        elements, a, b = _interface_elements(space, interface)
        if interface.electrical_kind == "ideal":
            for da, db in zip(a.ravel(), b.ravel()):
                union.union(int(da), int(db))
            ideal_contacts.append((interface, elements, a, b))
            continue
        areas = np.full(len(elements), float(space.areas[elements].sum()))
        if interface.resistance_semantics == "instance":
            for _, local in contact_islands(mesh, interface.id, elements):
                areas[local] = space.areas[elements[local]].sum()
        elif interface.resistance_semantics != "array":
            raise SimulationError(f"Interface {interface.id}: resistance semantics must be array or instance")
        g = _conductance(project, interface, areas,
                         (temperature[a].mean(axis=1) + temperature[b].mean(axis=1)) / 2.)
        blocks = mass(space.areas[elements]) * g[:, None, None]
        contact_assembly.add(a, a, blocks)
        contact_assembly.add(b, b, blocks)
        contact_assembly.add(a, b, -blocks)
        contact_assembly.add(b, a, -blocks)
        contacts.append((interface, elements, a, b, blocks))
    terminal_dofs: dict[str, np.ndarray] = {}
    density_loads: dict[str, np.ndarray] = {}
    source = np.zeros(size)
    gates: dict[str, np.ndarray] = {}
    gated_masks: dict[str, np.ndarray] = {}
    seen: dict[int, str] = {}
    for terminal in project.terminals:
        if terminal.kind == "insulated":
            continue
        if terminal.layer_id not in space.layers:
            if terminal.kind == "gate":
                continue  # A gate-control mask may live on a thermally-only metal.
            raise SimulationError(f"Terminal {terminal.id} is not on an active conductor")
        dofs = space.nodes_for(terminal.layer_id, "terminal", terminal.id)
        if not len(dofs):
            raise SimulationError(f"Terminal {terminal.id}: geometry selects no conductor nodes")
        if terminal.kind == "gate" and project.layer(terminal.layer_id).electrical.kind == "hemt":
            continue
        terminal_dofs[terminal.id] = dofs
        if terminal.kind == "probe":
            continue
        for d in dofs:
            if int(d) in seen:
                raise SimulationError(f"Terminals {seen[int(d)]} and {terminal.id} overlap on conductor nodes")
            seen[int(d)] = terminal.id
        if terminal.id in port_setup.owned:
            continue
        if terminal.kind in {"current", "floating", "voltage", "ground", "gate"}:
            for dof in dofs[1:]:
                union.union(int(dofs[0]), int(dof))
        elif terminal.kind == "density":
            load = np.zeros(size)
            elements, region_dofs = space.region(terminal.layer_id, "terminal", terminal.id)
            if len(elements):
                np.add.at(load, region_dofs.ravel(), np.repeat(space.areas[elements] * terminal.value / 3., 3))
            else:
                edges, lengths = space.edge_measure(terminal.layer_id, "terminal", terminal.id)
                measure = lengths * project.layer(terminal.layer_id).thickness
                np.add.at(load, edges.ravel(), np.repeat(measure * terminal.value / 2., 2))
            density_loads[terminal.id] = load
            source += load
        else:
            raise SimulationError(f"Unknown terminal kind {terminal.kind!r}")
    port_setup.assemble(contact_assembly, union, source)
    mapping = union.mapping()
    reaction_owners: dict[int, str] = {}
    for terminal in project.terminals:
        if terminal.id not in terminal_dofs or terminal.kind in {"density", "probe"} or terminal.id in port_setup.owned:
            continue
        for dof in np.unique(mapping[terminal_dofs[terminal.id]]):
            if int(dof) in reaction_owners:
                raise SimulationError(f"Terminals {reaction_owners[int(dof)]} and {terminal.id} share an ideal equipotential; terminal currents are ambiguous")
            reaction_owners[int(dof)] = terminal.id
    for port_id, dof in port_setup.port_dofs.items():
        owner = reaction_owners.get(int(mapping[dof]))
        if owner is not None:
            raise SimulationError(f"Port {port_id} and terminal {owner} share an ideal equipotential; currents are ambiguous")
    fixed: dict[int, float] = {}
    fixed_owners: dict[int, str] = {}
    for terminal in project.terminals:
        if terminal.id not in terminal_dofs or terminal.kind == "probe" or terminal.id in port_setup.owned:
            continue
        dof = int(terminal_dofs[terminal.id][0])
        reduced = int(mapping[dof])
        if terminal.kind in {"voltage", "ground", "gate"}:
            value = 0. if terminal.kind == "ground" else terminal.value
            if reduced in fixed and not np.isclose(fixed[reduced], value, rtol=1e-12, atol=1e-14):
                raise SimulationError(f"Conflicting fixed voltages across an ideal contact: {fixed_owners[reduced]}, {terminal.id}")
            fixed[reduced] = value
            fixed_owners[reduced] = terminal.id
        elif terminal.kind == "current":
            source[dof] += terminal.value
    port_setup.constrain(mapping, fixed, fixed_owners, source)
    rhs = reduce_vector(source, mapping)
    contact_matrix = contact_assembly.matrix()
    offset_full = np.zeros(size)
    nonlinear = any(project.layer(i).electrical.kind == "hemt" for i in space.layers)
    for layer_id, layer_space in space.layers.items():
        model = project.layer(layer_id).electrical
        gate = np.full(len(layer_space.elements), model.gate_voltage)
        chosen = [terminal for terminal in project.terminals if terminal.kind == "gate" and
                  ((terminal.id in model.gate_regions) if model.gate_regions else terminal.layer_id == layer_id)]
        missing_gates = set(model.gate_regions) - {terminal.id for terminal in chosen}
        if missing_gates:
            raise SimulationError(f"Layer {layer_id}: unknown gate-control terminals {sorted(missing_gates)}")
        mask = np.zeros(len(layer_space.elements), dtype=bool) if chosen else np.ones(len(layer_space.elements), dtype=bool)
        for terminal in chosen:
            region = set(np.asarray(mesh.regions.get(f"terminal:{terminal.id}", []), dtype=int).tolist())
            if not region:
                raise SimulationError(f"Gate {terminal.id}: charge control requires an area mask")
            selected = np.array([int(e) in region for e in layer_space.elements])
            if np.any(mask & selected & ~np.isclose(gate, terminal.value, rtol=1e-12, atol=1e-14)):
                raise SimulationError(f"Gate {terminal.id}: overlapping control masks impose conflicting voltages")
            gate[selected] = terminal.value
            mask |= selected
        gates[layer_id] = gate
        gated_masks[layer_id] = mask

    linear_layers = {}

    def assemble(v: np.ndarray, jacobian: bool = False):
        assembly_started = time.perf_counter()
        checkpoint(cancel)

        def prepare_layer(item):
            layer_id, layer_space = item
            layer = project.layer(layer_id)
            material = project.materials.get(layer.material_id)
            if material is None:
                raise SimulationError(f"Layer {layer_id}: material {layer.material_id!r} does not exist")
            elements, dofs = layer_space.elements, layer_space.dofs
            g, dg = sheet_conductance(layer, material, temperature[dofs].mean(axis=1),
                                      v[dofs].mean(axis=1), gates[layer_id], derivative=True)
            if layer.electrical.kind == "hemt" and not np.all(gated_masks[layer_id]):
                model = layer.electrical
                access = ~gated_masks[layer_id]
                local_t = temperature[dofs].mean(axis=1)[access]
                if model.maximum_sheet_density > 0:
                    access_g = (model.minimum_sheet_conductance + ELEMENTARY_CHARGE * mobility(model, local_t)
                                * (model.background_sheet_density + model.maximum_sheet_density))
                    g[access] = access_g[:, None] * np.asarray(material.electrical_anisotropy)
                else:
                    g[access] = sheet_conductance(layer, material, local_t, potential=0.)
                dg[access] = 0.
            blocks = stiffness(space.areas[elements], space.gradients[elements], g)
            local_dofs = dofs - layer_space.all_dofs[0]
            ass = Assembly(len(layer_space.all_dofs))
            ass.add(local_dofs, local_dofs, blocks)
            tangent = csr_matrix((ass.size, ass.size))
            if jacobian and layer.electrical.kind == "hemt":
                grad_v = np.einsum("ei,eik->ek", (v - offset_full)[dofs], space.gradients[elements])
                slope = space.areas[elements, None] * np.einsum("eik,ek,ek->ei", space.gradients[elements], dg, grad_v) / 3.
                nonlinear_ass = Assembly(ass.size)
                nonlinear_ass.add(local_dofs, local_dofs, np.repeat(slope[:, :, None], 3, axis=2))
                tangent = nonlinear_ass.matrix()
            return layer_id, ass.matrix(), tangent, g

        jobs = [(key, layer) for key, layer in space.layers.items() if key not in linear_layers]
        prepared = {output[0]: output[1:] for output in map_independent(prepare_layer, jobs,
                    work_units=sum(len(layer.elements) for _, layer in jobs))}
        for key, output in prepared.items():
            if project.layer(key).electrical.kind != "hemt":
                linear_layers[key] = output
        prepared.update(linear_layers)
        checkpoint(cancel)
        matrix = layer_matrix([prepared[key][0] for key in space.layers], size) + contact_matrix
        derivative_matrix = (matrix + layer_matrix([prepared[key][1] for key in space.layers], size)
                             if jacobian else matrix)
        conductances = {key: prepared[key][2] for key in space.layers}
        record_phase("electrical_assembly", time.perf_counter() - assembly_started)
        return matrix, derivative_matrix, conductances

    if initial is not None and len(initial) == size:
        v = initial.copy()
    else:
        v = np.full(size, np.mean(list(fixed.values())) if fixed else 0.)
    matrix, _, _ = assemble(v)
    reduced_matrix = reduce_matrix(matrix, mapping)
    gauge_notes = check_references(reduced_matrix, rhs, fixed, settings.floating_policy)
    _, component_labels = connected_components(reduced_matrix, directed=False)
    offsets = np.zeros(len(rhs))
    for component in np.unique(component_labels):
        reference_node = next(node for node in fixed if component_labels[node] == component)
        offsets[component_labels == component] = fixed[reference_node]
    offset_full = offsets[mapping]
    shifted_fixed = {node: value - offsets[node] for node, value in fixed.items()}
    u = linear_solve(reduced_matrix, rhs, shifted_fixed, settings.linear_solver, settings.linear_tolerance) + offsets
    v = u[mapping]
    free = np.ones(len(u), dtype=bool)
    free[list(fixed)] = False
    iterations = []
    converged = not nonlinear

    def residual_measure(mat: csr_matrix, candidate: np.ndarray) -> tuple[float, np.ndarray]:
        flow = mat @ (candidate - offsets)
        residual = flow - rhs
        scale = max(float(np.max(np.abs(flow), initial=0)), float(np.max(np.abs(rhs), initial=0)), 1e-30)
        return float(np.max(np.abs(residual[free]), initial=0) / scale), residual

    if nonlinear:
        for iteration in range(settings.max_iterations):
            checkpoint(cancel, progress, .1 + .7 * iteration / settings.max_iterations,
                       f"Electrical nonlinear iteration {iteration + 1}")
            matrix, jacobian, _ = assemble(v, jacobian=True)
            reduced_matrix = reduce_matrix(matrix, mapping)
            residual, vector = residual_measure(reduced_matrix, u)
            if residual <= settings.relative_tolerance:
                converged = True
                iterations.append({"iteration": float(iteration + 1), "electrical_residual": residual,
                                   "potential_change": 0., "relaxation": 0.})
                break
            j = reduce_matrix(jacobian, mapping)[free][:, free]
            linear_started = time.perf_counter()
            with warnings.catch_warnings():
                warnings.simplefilter("error", MatrixRankWarning)
                try:
                    direction = spsolve(j, -vector[free])
                except (MatrixRankWarning, RuntimeError) as exc:
                    raise SimulationError(f"HEMT nonlinear Jacobian failed: {exc}") from exc
            if not np.all(np.isfinite(direction)):
                raise SimulationError("HEMT nonlinear Newton step is nonfinite")
            record_linear_solve("cpu", factorized=True, seconds=time.perf_counter() - linear_started)
            damping = 1.
            merit = float(np.linalg.norm(vector[free]))
            accepted = False
            for _ in range(24):
                candidate = u.copy()
                candidate[free] += damping * direction
                trial_matrix, _, _ = assemble(candidate[mapping])
                trial_residual = reduce_matrix(trial_matrix, mapping) @ (candidate - offsets) - rhs
                if np.linalg.norm(trial_residual[free]) <= merit * (1. - 1e-4 * damping):
                    accepted = True
                    break
                damping *= .5
            if not accepted:
                # Positive-conductance Picard fallback provides a safe descent
                # direction when the local Newton linearization is poor.
                target = linear_solve(reduced_matrix, rhs, shifted_fixed, settings.linear_solver, settings.linear_tolerance) + offsets
                damping = settings.relaxation
                candidate = u + damping * (target - u)
            delta = float(np.max(np.abs(candidate - u)))
            u, v = candidate, candidate[mapping]
            iterations.append({"iteration": float(iteration + 1), "electrical_residual": residual,
                               "potential_change": delta, "relaxation": damping})
    matrix, _, conductances = assemble(v)
    reduced_matrix = reduce_matrix(matrix, mapping)
    residual, _ = residual_measure(reduced_matrix, u)
    converged = residual <= settings.relative_tolerance
    result = SimulationResult(mesh=mesh, converged=converged, iterations=iterations)
    result.diagnostics.extend(Diagnostic("warning", note) for note in gauge_notes)
    if not converged:
        result.diagnostics.append(Diagnostic("error", f"Electrical nonlinear solve did not converge: residual {residual:.6g}"))
    for layer_id, data in space.element_node_fields(v).items():
        result.element_fields[layer_id] = {"potential": data}
    for layer_id, data in space.element_node_fields(temperature).items():
        result.element_fields[layer_id]["temperature"] = data
    heat: dict[str, np.ndarray] = {}
    joule_power = 0.
    current_max = 0.
    current_samples = []
    current_weights = []
    for layer_id, layer_space in space.layers.items():
        e, dofs = layer_space.elements, layer_space.dofs
        gradient = np.einsum("ei,eik->ek", (v - offset_full)[dofs], space.gradients[e])
        field = -gradient
        sheet_current = conductances[layer_id] * field
        density = sheet_current / project.layer(layer_id).thickness
        current_samples.append(np.linalg.norm(density, axis=1))
        current_weights.append(space.areas[e])
        q = np.sum(field * sheet_current, axis=1)
        joule_power += float(np.dot(q, space.areas[e]))
        current_max = max(current_max, float(np.linalg.norm(density, axis=1).max(initial=0)))
        result.fields[layer_id] = {"potential": space.nodal_field(layer_id, v),
                                   "temperature": space.nodal_field(layer_id, temperature)}
        for name, values in (("electric_field", field), ("sheet_current", sheet_current),
                             ("current_density", density), ("joule_density", q),
                             ("sheet_conductance", conductances[layer_id])):
            array = np.full((len(mesh.triangles),) + values.shape[1:], np.nan)
            array[e] = values
            result.fields[layer_id][name] = array
        heat[layer_id] = np.zeros(len(mesh.triangles))
        heat[layer_id][e] = q
        result.fields[layer_id]["contact_density"] = np.full(len(mesh.triangles), np.nan)
        result.fields[layer_id]["contact_density"][e] = 0.
    loss_entries = []
    from .power import append_sheet_losses, report_contacts, report_ideal_contacts
    append_sheet_losses(project, space, result, gated_masks, loss_entries)
    contact_power = 0.
    contact_metrics = {}
    for interface, elements, a, b, blocks in contacts:
        difference = (v - offset_full)[a] - (v - offset_full)[b]
        powers = np.einsum("ei,eij,ej->e", difference, blocks, difference)
        power = float(powers.sum())
        contact_power += power
        transfer = np.einsum("eij,ej->ei", blocks, difference).sum()
        contact_metrics[interface.id] = {"power": power, "current_a_to_b": float(transfer)}
        heat[interface.layer_a][elements] += powers / (2. * space.areas[elements])
        heat[interface.layer_b][elements] += powers / (2. * space.areas[elements])
        result.fields[interface.layer_a]["contact_density"][elements] += powers / (2. * space.areas[elements])
        result.fields[interface.layer_b]["contact_density"][elements] += powers / (2. * space.areas[elements])
    report_contacts(project, space, result, contacts, v, loss_entries)
    reaction = matrix @ (v - offset_full)
    for load in density_loads.values():
        reaction -= load
    reduced_reaction = reduce_vector(reaction, mapping)
    for terminal in project.terminals:
        if terminal.id not in terminal_dofs:
            if terminal.kind == "gate":
                result.terminals[terminal.id] = {"voltage": terminal.value, "current": 0., "power": 0.}
            continue
        dofs = terminal_dofs[terminal.id]
        if terminal.kind == "density":
            load = density_loads[terminal.id]
            current = float(load.sum())
            power = float(np.dot(load, v))
            voltage = power / current if current else float(v[dofs].mean())
        elif terminal.kind == "probe":
            current = power = 0.
            elements, region_dofs = space.region(terminal.layer_id, "terminal", terminal.id)
            if len(elements):
                voltage = float(np.average(v[region_dofs].mean(axis=1), weights=space.areas[elements]))
            else:
                try:
                    edges, lengths = space.edge_measure(terminal.layer_id, "terminal", terminal.id)
                except SimulationError:
                    # Isolated point samples have no area or edge measure.
                    voltage = float(v[dofs].mean())
                else:
                    voltage = float(np.average(v[edges].mean(axis=1), weights=lengths))
        elif terminal.id in port_setup.owned:
            current = power = 0.
            voltage = float(v[dofs].mean())
        else:
            current = float(reduced_reaction[np.unique(mapping[dofs])].sum())
            voltage = float(v[dofs].mean())
            power = voltage * current
        result.terminals[terminal.id] = {"voltage": voltage, "current": current, "power": power}
    report_ideal_contacts(space, result, ideal_contacts, reaction, terminal_dofs, project, loss_entries)
    feed_power = port_setup.report(result, v, reaction, mapping, heat, loss_entries)
    from .power import finish_loss_budget
    finish_loss_budget(result, loss_entries)
    currents = np.array([item["current"] for item in result.terminals.values()] +
                        [item["current"] for item in result.analysis.get("ports", {}).values()])
    terminal_power = sum(item["power"] for item in result.terminals.values()) + sum(
        item["power"] for item in result.analysis.get("ports", {}).values())
    total_power = joule_power + contact_power + feed_power
    balance = abs(float(currents.sum())) / max(float(np.abs(currents).sum()) / 2., 1e-30)
    power_balance = abs(terminal_power - total_power) / max(abs(terminal_power), total_power, 1e-30)
    records = [result.terminals[t.id] for t in project.terminals if t.id in result.terminals and t.kind != "gate"]
    positive_current = sum(max(0., item["current"]) for item in records) + sum(
        max(0., item["current"]) for item in result.analysis.get("ports", {}).values())
    measurement = _resistance_metrics(project, result, terminal_dofs, mapping, component_labels, port_setup.port_dofs)
    density_samples = np.concatenate(current_samples)
    area_weights = np.concatenate(current_weights)
    density_average = float(np.average(density_samples, weights=area_weights))
    sort = np.argsort(density_samples)
    quantile_index = int(np.searchsorted(np.cumsum(area_weights[sort]), .95 * area_weights.sum()))
    density_p95 = float(density_samples[sort[min(quantile_index, len(sort) - 1)]])
    result.metrics.update(electrical_power=total_power, joule_power=joule_power, contact_power=contact_power,
                          external_feed_power=feed_power, terminal_power=terminal_power, current_balance_error=balance,
                          power_balance_error=power_balance, electrical_residual=residual,
                          total_current=positive_current,
                          current_density_max=current_max, current_density_average=density_average,
                          current_density_p95=density_p95,
                          peak_to_average=current_max / density_average if density_average > 0 else None,
                          contacts={**contact_metrics, **result.metrics.get("contacts", {})},
                          electrical_dofs=space.size)
    result.metrics.update(measurement)
    if balance > settings.current_balance_tolerance or power_balance > settings.power_balance_tolerance:
        result.converged = False
        result.diagnostics.append(Diagnostic("error", f"Electrical conservation failed: current {balance:.3g}, power {power_balance:.3g}"))
    return ElectricalState(result, heat, v, space, reduced_matrix, mapping, fixed, rhs,
                           {key: int(mapping[value]) for key, value in port_setup.port_dofs.items()})
