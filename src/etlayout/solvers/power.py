# SPDX-License-Identifier: MPL-2.0
"""Physical contact, external-node port and non-overlapping dissipation accounting.

Positive port current enters the sheet domain. Positive contact current flows
from layer_a to layer_b. Finite contacts use the same P1 mass quadrature as the
assembled operator; physical IDs depend on boundary geometry, never mesh indices.
"""
from __future__ import annotations

import weakref
from collections import OrderedDict
from dataclasses import dataclass, field

import numpy as np
from shapely import Polygon, union_all

from etlayout.models import SimulationError

from .fem import UnionFind, mass

_ISLAND_CACHE = OrderedDict()


def contact_islands(mesh, identifier, elements):
    from etlayout.routing.geometry import canonical_geometry_id

    key = (id(mesh), identifier, hash(elements.tobytes()))
    cached = _ISLAND_CACHE.get(key)
    if cached is not None and cached[0]() is mesh:
        _ISLAND_CACHE.move_to_end(key)
        return cached[1]
    components = UnionFind(len(elements))
    edges = {}
    for local, tri in enumerate(mesh.triangles[elements]):
        for a, b in ((0, 1), (1, 2), (2, 0)):
            edge = tuple(sorted((int(tri[a]), int(tri[b]))))
            if edge in edges:
                components.union(local, edges[edge])
            else:
                edges[edge] = local
    labels = components.mapping()
    islands = []
    geometries = {}
    for label in np.unique(labels):
        local = np.flatnonzero(labels == label)
        geometry = union_all([Polygon(mesh.points[t]) for t in mesh.triangles[elements[local]]])
        identity = canonical_geometry_id(geometry, prefix=identifier)
        islands.append((identity, local))
        geometries[identity] = geometry
    islands.sort()
    _ISLAND_CACHE[key] = (weakref.ref(mesh), islands, geometries)
    _ISLAND_CACHE.move_to_end(key)
    while len(_ISLAND_CACHE) > 16:
        _ISLAND_CACHE.popitem(last=False)
    return islands


def append_sheet_losses(project, space, result, masks, entries):
    for layer_id, layer_space in space.layers.items():
        layer = project.layer(layer_id)
        e = layer_space.elements
        q = result.fields[layer_id]['joule_density'][e]
        role = layer.loss_role
        if role == 'auto':
            role = ('channel' if layer.electrical.kind == 'hemt' else
                    'access' if project.materials[layer.material_id].category != 'metal' else
                    'routing_metal' if layer.net_id else 'other_metal')
        if role not in {'routing_metal', 'other_metal', 'access', 'channel'}:
            raise SimulationError(f'Layer {layer_id}: unsupported loss role {role!r}')
        selections = {role: np.ones(len(e), dtype=bool)}
        if layer.electrical.kind == 'hemt' and layer.loss_role == 'auto':
            selections = {'channel': masks[layer_id], 'access': ~masks[layer_id]}
        for category, selected in selections.items():
            power = float(np.dot(q[selected], space.areas[e[selected]]))
            entries.append({'id': f'layer:{layer_id}:{category}', 'category': category,
                            'layer_id': layer_id, 'power': power,
                            'thermal_deposition': power, 'excluded_power': 0.})


def absolute_p1_integrals(nodal, areas):
    """Exact integral of the absolute value of a scalar linear triangle field.

    Sign-changing triangles are clipped on the zero contour in a unit reference
    triangle. This avoids treating opposing transfer currents as zero loading.
    """
    output = np.abs(nodal.mean(axis=1)) * areas
    mixed = np.flatnonzero((nodal.min(axis=1) < 0) & (nodal.max(axis=1) > 0))
    reference = np.array([[0., 0.], [1., 0.], [0., 1.]])
    for index in mixed:
        vertices = list(zip(reference, nodal[index]))
        clipped = []
        for k, (point, value) in enumerate(vertices):
            previous, old_value = vertices[k - 1]
            if (value >= 0) != (old_value >= 0):
                fraction = old_value / (old_value - value)
                clipped.append((previous + fraction * (point - previous), 0.))
            if value >= 0:
                clipped.append((point, value))
        positive = 0.
        for k in range(1, len(clipped) - 1):
            tri = [clipped[0], clipped[k], clipped[k + 1]]
            first, second = tri[1][0] - tri[0][0], tri[2][0] - tri[0][0]
            fraction = abs(first[0] * second[1] - first[1] * second[0])
            positive += fraction * sum(value for _, value in tri) / 3.
        signed = nodal[index].mean()
        output[index] = areas[index] * (2 * positive - signed)
    return output


def report_contacts(project, space, result, contacts, values, entries):
    records = result.analysis.setdefault('contacts', {})
    for interface, elements, a, b, blocks in contacts:
        difference = values[a] - values[b]
        # Sum of a P1 mass matrix equals element area.
        g = blocks.sum(axis=(1, 2)) / space.areas[elements]
        normal = g[:, None] * difference
        powers = np.einsum('ei,eij,ej->e', difference, blocks, difference)
        currents = np.einsum('eij,ej->e', blocks, difference)
        absolute_currents = absolute_p1_integrals(normal, space.areas[elements])
        arrays = {}
        for name, data in (('voltage_drop', difference), ('j_normal', normal),
                           ('heat_density', g[:, None] * difference ** 2)):
            array = np.full((len(space.mesh.triangles), 3), np.nan)
            array[elements] = data
            arrays[name] = array
        arrays['power_density'] = np.full(len(space.mesh.triangles), np.nan)
        arrays['power_density'][elements] = powers / space.areas[elements]
        result.interface_fields[interface.id] = arrays
        ids = []
        for identifier, local in contact_islands(space.mesh, interface.id, elements):
            area = float(space.areas[elements[local]].sum())
            current = float(currents[local].sum())
            record = {'id': identifier, 'interface_id': interface.id, 'layer_a': interface.layer_a,
                      'layer_b': interface.layer_b, 'status': 'available',
                      'direction': 'layer_a_to_layer_b', 'area': area,
                      'current_a_to_b': current, 'absolute_current': float(absolute_currents[local].sum()),
                      'power': float(powers[local].sum()),
                      'j_normal_mean': current / area,
                      'j_normal_rms': float(np.sqrt(np.sum(g[local] * powers[local]) / area)),
                      'j_normal_peak': float(np.max(np.abs(normal[local]))),
                      'peak_definition': 'raw P1 vertex maximum; mesh-sensitive',
                      'elements': elements[local].tolist(), 'resistance_semantics': interface.resistance_semantics}
            geometry = _ISLAND_CACHE[(id(space.mesh), interface.id, hash(elements.tobytes()))][2][identifier]
            record.update(bounds=list(geometry.bounds), geometry_wkt=geometry.wkt)
            records[identifier] = record
            ids.append(identifier)
        net = float(currents.sum())
        absolute = float(absolute_currents.sum())
        for identifier in ids:
            record = records[identifier]
            record['absolute_loading_fraction'] = record['absolute_current'] / absolute if absolute > 0 else None
            record['signed_loading_fraction'] = (record['current_a_to_b'] / net
                if abs(net) > max(absolute * 1e-12, 1e-30) else None)
        power = float(powers.sum())
        entries.append({'id': f'interface:{interface.id}',
                        'category': 'device_load' if interface.purpose == 'device_load' or interface.electrical_kind == 'load' else 'contact',
                        'interface_id': interface.id, 'power': power,
                        'thermal_deposition': power, 'excluded_power': 0.})
        result.metrics['contacts'] = result.metrics.get('contacts', {})


def report_ideal_contacts(space, result, contacts, reaction, terminal_dofs, project, entries):
    """Recover an island reaction only when its side is free of other constraints.

    An ideal tie has no constitutive density law. Aggregate reaction is unique
    on an isolated side; coincident electrodes/other ties make that split
    nonidentifiable. Such records deliberately carry None, never an invented
    equal split or a fictitious Joule source.
    """
    records = result.analysis.setdefault('contacts', {})
    external = set()
    for terminal in project.terminals:
        if terminal.kind not in {'probe', 'insulated', 'density'}:
            external.update(terminal_dofs.get(terminal.id, []))
    for interface, elements, a, b in contacts:
        zero = np.full((len(space.mesh.triangles), 3), np.nan)
        zero[elements] = 0.
        result.interface_fields[interface.id] = {'voltage_drop': zero, 'heat_density': zero.copy()}
        other = set()
        for candidate, _, ca, cb in contacts:
            if candidate.id != interface.id:
                other.update(ca.ravel())
                other.update(cb.ravel())
        islands = []
        for identifier, local in contact_islands(space.mesh, interface.id, elements):
            adofs, bdofs = np.unique(a[local]), np.unique(b[local])
            if not (set(adofs) & (external | other)):
                current = -float(reaction[adofs].sum())
            elif not (set(bdofs) & (external | other)):
                current = float(reaction[bdofs].sum())
            else:
                current = None
            records[identifier] = {'id': identifier, 'interface_id': interface.id,
                'layer_a': interface.layer_a, 'layer_b': interface.layer_b,
                'status': 'available' if current is not None else 'nonunique',
                'reason': '' if current is not None else 'Redundant ideal constraints prevent individual reaction attribution',
                'direction': 'layer_a_to_layer_b', 'area': float(space.areas[elements[local]].sum()),
                'current_a_to_b': current, 'absolute_current': None, 'power': 0., 'j_normal_peak': None,
                'j_normal_mean': None, 'elements': elements[local].tolist()}
            geometry = _ISLAND_CACHE[(id(space.mesh), interface.id, hash(elements.tobytes()))][2][identifier]
            records[identifier].update(bounds=list(geometry.bounds), geometry_wkt=geometry.wkt)
            islands.append(identifier)
        currents = [records[key]['current_a_to_b'] for key in islands]
        result.metrics.setdefault('contacts', {})[interface.id] = {'power': 0.,
            'current_a_to_b': sum(currents) if all(c is not None for c in currents) else None,
            'status': 'available' if all(c is not None for c in currents) else 'nonunique'}
        entries.append({'id': f'interface:{interface.id}', 'category': 'contact',
                        'interface_id': interface.id, 'power': 0., 'thermal_deposition': 0., 'excluded_power': 0.})


def finish_loss_budget(result, entries):
    categories = ('routing_metal', 'other_metal', 'access', 'channel', 'contact', 'device_load', 'external_feed')
    totals = {key: sum(row['power'] for row in entries if row['category'] == key) for key in categories}
    result.analysis['loss_budget'] = {'version': 1, 'entries': entries, 'categories': totals,
        'total_power': sum(row['power'] for row in entries),
        'thermal_deposition': sum(row['thermal_deposition'] for row in entries),
        'excluded_power': sum(row['excluded_power'] for row in entries),
        'definition': 'Disjoint layer-role, interface and external-feed integrals; groups are views, not additional losses'}


@dataclass
class PortSetup:
    project: object
    space: object
    nodes: dict = field(default_factory=dict)
    port_dofs: dict = field(default_factory=dict)
    owned: dict = field(default_factory=dict)
    landings: list = field(default_factory=list)

    def assemble(self, assembly, union, source):
        for port, landing, _terminal, dofs, blocks, _elements in self.landings:
            node = self.port_dofs[port.id]
            if landing.resistance == 0:
                for dof in np.unique(dofs):
                    union.union(node, int(dof))
            else:
                conductance = blocks / (landing.resistance * blocks.sum())
                external = np.full_like(dofs, node)
                assembly.add(dofs, dofs, conductance)
                assembly.add(dofs, external, -conductance)
                assembly.add(external, dofs, -conductance)
                assembly.add(external, external, conductance)

    def constrain(self, mapping, fixed, owners, source):
        for port in self.project.power.ports:
            node = self.port_dofs[port.id]
            reduced = int(mapping[node])
            if port.kind in {'voltage', 'ground'}:
                value = 0. if port.kind == 'ground' else port.value
                if reduced in fixed and not np.isclose(fixed[reduced], value, atol=1e-14, rtol=1e-12):
                    raise SimulationError(f'Port {port.id}: conflicting voltages on shared external node')
                fixed[reduced] = value
                owners[reduced] = port.id
            elif port.kind == 'current':
                source[node] += port.value
            elif port.kind != 'floating':
                raise SimulationError(f'Port {port.id}: unsupported kind {port.kind!r}')

    def report(self, result, values, reaction, mapping, heat, entries):
        ports = {port.id: {'voltage': float(values[self.port_dofs[port.id]]), 'current': 0.,
                          'power': 0., 'feed_power': 0., 'landings': [],
                          'external_node': port.external_node or port.id,
                          'specified_current': port.value if port.kind == 'current' else None,
                          'constraint_scope': 'shared_external_node' if sum((other.external_node or other.id) == (port.external_node or port.id) for other in self.project.power.ports) > 1 else 'port'}
                 for port in self.project.power.ports}
        total = 0.
        for port, landing, terminal, dofs, blocks, elements in self.landings:
            node = self.port_dofs[port.id]
            difference = values[node] - values[dofs]
            if landing.resistance:
                conductance = blocks / (landing.resistance * blocks.sum())
                currents = np.einsum('eij,ej->ei', conductance, difference)
                current = float(currents.sum())
                powers = np.einsum('ei,eij,ej->e', difference, conductance, difference)
                power = float(powers.sum())
            else:
                current = float(reaction[np.unique(dofs)].sum())
                power = 0.
                powers = np.zeros(len(dofs))
            deposition = power * landing.thermal_fraction
            if deposition:
                if elements is not None:
                    density = powers * landing.thermal_fraction / self.space.areas[elements]
                    heat[terminal.layer_id][elements] += density
                    result.fields[terminal.layer_id]['contact_density'][elements] += density
                else:
                    # Edge feed heat is conservatively assigned to adjacent physical
                    # triangles, partitioned by edge length. No artificial new layer.
                    layer = self.space.layers[terminal.layer_id]
                    allocation = np.zeros(len(self.space.mesh.triangles))
                    for edge, edge_power in zip(dofs, powers):
                        adjacent = np.flatnonzero(np.sum(np.isin(layer.dofs, edge), axis=1) == 2)
                        allocation[layer.elements[adjacent]] += edge_power * landing.thermal_fraction / len(adjacent)
                    heat[terminal.layer_id] += allocation / self.space.areas
                    result.fields[terminal.layer_id]['contact_density'][layer.elements] += allocation[layer.elements] / self.space.areas[layer.elements]
            row = {'terminal_id': terminal.id, 'resistance': landing.resistance,
                   'current': current, 'power': power, 'thermal_fraction': landing.thermal_fraction,
                   'voltage': float(np.einsum('eij,ej->', blocks, values[dofs]) / blocks.sum()),
                   'voltage_min': float(values[dofs].min()), 'voltage_max': float(values[dofs].max()),
                   'ideal': landing.resistance == 0,
                   'resistance_model': 'ideal_equipotential' if landing.resistance == 0 else 'distributed_total_contact_resistance'}
            ports[port.id]['landings'].append(row)
            ports[port.id]['current'] += current
            ports[port.id]['feed_power'] += power
            entries.append({'id': f'feed:{port.id}:{terminal.id}', 'category': 'external_feed',
                            'port_id': port.id, 'terminal_id': terminal.id, 'power': power,
                            'thermal_deposition': deposition, 'excluded_power': power - deposition})
            total += power
        for record in ports.values():
            record['power'] = record['voltage'] * record['current']
        nodes = {}
        for record in ports.values():
            node = nodes.setdefault(record['external_node'], {'voltage': record['voltage'], 'current': 0.})
            node['current'] += record['current']
        result.analysis['external_nodes'] = nodes
        result.analysis['ports'] = ports
        return total


def prepare_ports(project, space):
    setup = PortSetup(project, space)
    terminals = {terminal.id: terminal for terminal in project.terminals}
    seen = set()
    for port in project.power.ports:
        if port.id in setup.port_dofs or not port.id or not port.landings:
            raise SimulationError(f'Port {port.id!r}: ID must be unique and at least one landing is required')
        if not np.isfinite(port.value):
            raise SimulationError(f'Port {port.id}: prescribed value must be finite')
        node = port.external_node or port.id
        setup.nodes.setdefault(node, space.size + len(setup.nodes))
        setup.port_dofs[port.id] = setup.nodes[node]
        for landing in port.landings:
            if landing.terminal_id not in terminals:
                raise SimulationError(f'Port {port.id}: unknown landing terminal {landing.terminal_id}')
            if landing.terminal_id in setup.owned:
                raise SimulationError(f'Landing {landing.terminal_id}: assigned to more than one port')
            terminal = terminals[landing.terminal_id]
            if terminal.kind in {'probe', 'gate', 'insulated', 'density'}:
                raise SimulationError(f'Port {port.id}: landing must reference an electrode footprint')
            if not np.isfinite(landing.resistance) or landing.resistance < 0 or not 0 <= landing.thermal_fraction <= 1:
                raise SimulationError(f'Port {port.id}: invalid feed resistance or thermal fraction')
            if terminal.layer_id not in space.layers:
                raise SimulationError(f'Port {port.id}: inactive landing layer')
            elements, dofs = space.region(terminal.layer_id, 'terminal', terminal.id)
            if len(elements):
                blocks = mass(space.areas[elements])
            else:
                dofs, lengths = space.edge_measure(terminal.layer_id, 'terminal', terminal.id)
                blocks = lengths[:, None, None] * (np.ones((2, 2)) + np.eye(2))[None] / 6.
                elements = None
            if seen.intersection(dofs.ravel()):
                raise SimulationError('Port landing footprints overlap on conductor nodes')
            seen.update(dofs.ravel())
            setup.owned[terminal.id] = port.id
            setup.landings.append((port, landing, terminal, dofs, blocks, elements))
    return setup
