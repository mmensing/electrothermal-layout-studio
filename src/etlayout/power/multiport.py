# SPDX-License-Identifier: MPL-2.0
"""Fixed-temperature passive DC port reduction with one sparse factorization.

The matrix maps external port potentials to currents entering the domain. It
is a resistive model at a declared fixed temperature, not an EM extraction or
a large-signal/small-signal reduction of a nonlinear transistor.
"""
from __future__ import annotations

import hashlib
from copy import deepcopy
from dataclasses import asdict
from pathlib import Path

import numpy as np
from scipy.sparse.csgraph import connected_components
from scipy.sparse.linalg import splu

from etlayout import __version__
from etlayout.io.project import configuration_hash, physics_hash
from etlayout.io.reporting import csv_writer
from etlayout.models import SimulationError, checkpoint
from etlayout.provenance import build_info
from etlayout.solvers.electrical import solve_state


def extract_multiport(project, mesh, port_ids=None, reference=None, temperatures=None, cancel=None):
    """Eliminate interior DOFs; retain the full ungrounded nodal port matrix.

    ``temperatures`` may be a positive scalar K or authoritative layer fields.
    Absent fields use the explicitly reported initial_temperature as fixed T.
    Unselected physical ports remain floating/open. Hidden legacy driven
    terminals are rejected, so every reservoir belongs to a declared port.
    """
    ports = {port.id: port for port in project.power.ports}
    selected = list(ports) if port_ids is None else list(port_ids)
    if len(selected) < 2 or len(set(selected)) != len(selected) or set(selected) - ports.keys():
        raise SimulationError('Multiport extraction requires at least two distinct existing physical port IDs')
    if len(selected) > 128:
        raise SimulationError('Multiport extraction is bounded to 128 physical ports')
    reference = selected[-1] if reference is None else reference
    if reference not in selected:
        raise SimulationError('Multiport reference must be one of the selected port IDs')
    owned = {landing.terminal_id for port in ports.values() for landing in port.landings}
    hidden = [t.id for t in project.terminals if t.id not in owned and t.kind not in {'probe', 'insulated'}]
    if hidden:
        raise SimulationError(f'Multiport extraction requires all electrodes to belong to physical ports: {hidden}')
    nonlinear = [layer.id for layer in project.layers if layer.role == 'conductor' and layer.electrical.kind == 'hemt']
    if nonlinear:
        raise SimulationError('Passive multiport extraction excludes voltage-dependent HEMT/device laws; use a separately validated small-signal model')
    clone = deepcopy(project)
    clone.transient.enabled = False
    clone.solver.mode = 'electrical'
    clone.solver.floating_policy = 'reference'
    clone.solver.resistance_terminals = []
    clone.solver.resistance_current_terminal = ''
    for port in clone.power.ports:
        port.kind = 'ground' if port.id in selected else 'floating'
        port.value = 0.
    if temperatures is None:
        temperature_info = {'kind': 'uniform_fixed', 'temperature_K': clone.solver.initial_temperature}
    elif np.isscalar(temperatures):
        value = float(temperatures)
        if not np.isfinite(value) or value <= 0:
            raise SimulationError('Multiport fixed temperature must be positive and finite')
        clone.solver.initial_temperature = value
        temperature_info = {'kind': 'uniform_fixed', 'temperature_K': value}
        temperatures = None
    else:
        temperature_info = {'kind': 'fixed_spatial_fields', 'layers': {}}
        for key, array in temperatures.items():
            active = np.asarray(array)[mesh.elements(key)] if np.asarray(array).ndim == 2 else np.asarray(array)[np.unique(mesh.triangles[mesh.elements(key)])]
            if not active.size or not np.all(np.isfinite(active)) or np.any(active <= 0):
                raise SimulationError(f'Multiport temperature field {key} must be positive and finite on its support')
            temperature_info['layers'][key] = {'minimum_K': float(active.min()), 'maximum_K': float(active.max())}
    checkpoint(cancel)
    state = solve_state(clone, mesh, temperatures=temperatures, cancel=cancel)
    if not state.result.converged:
        raise SimulationError('Passive multiport assembly failed its electrical conservation checks')
    matrix = state.matrix
    port_nodes = np.array([state.port_dofs[key] for key in selected], dtype=int)
    if len(np.unique(port_nodes)) != len(port_nodes):
        raise SimulationError('Selected ports share an ideal/external node and are not independent voltage coordinates')
    _, components = connected_components(matrix, directed=False)
    if len(np.unique(components[port_nodes])) != 1:
        raise SimulationError('Selected ports are disconnected; extract each connected passive network separately')
    interior_mask = np.ones(matrix.shape[0], dtype=bool)
    interior_mask[port_nodes] = False
    # Gauge-only inactive islands are excluded, never silently grounded into G.
    interior_mask &= components == components[port_nodes[0]]
    interior = np.flatnonzero(interior_mask)
    conductance = matrix[port_nodes][:, port_nodes].toarray()
    factorization_count = 0
    if len(interior):
        checkpoint(cancel)
        factor = splu(matrix[interior][:, interior].tocsc())
        factorization_count = 1
        coupling = matrix[interior][:, port_nodes]
        conductance -= matrix[port_nodes][:, interior] @ factor.solve(coupling.toarray())
    scale = max(float(np.max(np.abs(conductance))), 1e-30)
    row_error = float(np.max(np.abs(conductance.sum(axis=1))))
    symmetry_error = float(np.max(np.abs(conductance - conductance.T)))
    # Eigenvalues assess the measured symmetric part without altering the exported
    # operator. No projection, clipping or post-hoc row-sum repair is performed.
    eigenvalues = np.linalg.eigvalsh((conductance + conductance.T) / 2)
    tolerance = max(project.solver.linear_tolerance * 100, 1e-8) * scale
    valid = row_error <= tolerance and symmetry_error <= tolerance and eigenvalues.min() >= -tolerance
    if not valid:
        raise SimulationError(f'Multiport validation failed: KCL {row_error:g}, reciprocity {symmetry_error:g}, minimum eigenvalue {eigenvalues.min():g}')
    reference_index = selected.index(reference)
    retained = [index for index in range(len(selected)) if index != reference_index]
    reduced = conductance[np.ix_(retained, retained)]
    try:
        resistance = np.linalg.solve(reduced, np.eye(len(retained)))
    except np.linalg.LinAlgError as error:
        raise SimulationError('Reference-reduced multiport is singular; ports lack independent conductive paths') from error
    mesh_info = {'nodes': len(mesh.points), 'elements': len(mesh.triangles)}
    # Export the exact physical mesh identity when the shared persistence helper
    # exposes it; source configuration and material snapshots remain explicit.
    digest = hashlib.sha256()
    for array in (mesh.points, mesh.triangles):
        digest.update(np.ascontiguousarray(array).tobytes())
    for key, array in sorted(mesh.regions.items()):
        digest.update(key.encode('utf-8'))
        digest.update(np.asarray(array, dtype='<i8').tobytes())
    mesh_info['identity'] = digest.hexdigest()
    return {'version': 1, 'status': 'available', 'model': 'fixed-temperature passive resistive DC',
            'port_ids': selected, 'reference': reference, 'reduced_port_ids': [selected[i] for i in retained],
            'conductance_matrix': conductance.tolist(), 'conductance_units': 'S',
            'reduced_conductance_matrix': reduced.tolist(), 'resistance_matrix': resistance.tolist(),
            'resistance_units': 'ohm', 'current_sign': 'positive entering domain',
            'temperature': temperature_info, 'solver_version': __version__,
            'build': build_info(),
            'configuration_hash': configuration_hash(project), 'physics_hash': physics_hash(project),
            'configuration': project.to_dict(), 'materials': {key: asdict(value) for key, value in project.materials.items()},
            'mesh': mesh_info, 'validation': {'kcl_absolute_error_S': row_error,
                'reciprocity_absolute_error_S': symmetry_error, 'minimum_eigenvalue_S': float(eigenvalues.min()),
                'tolerance_S': tolerance, 'factorizations': factorization_count,
                'eliminated_dofs': len(interior), 'sparse_operator_shape': list(matrix.shape)}}


def _spice_lines(report):
    """Keep every finite coupling and establish fidelity per port before writing.

    The extraction's global matrix tolerance is a validation bound, never a
    sparsification threshold. Resistor-only export must reproduce individual
    transfer and driving-point responses even when one port is very weak.
    """
    matrix = np.asarray(report['conductance_matrix'], dtype=float)
    count = len(report['port_ids'])
    if matrix.shape != (count, count) or count < 2 or not np.isfinite(matrix).all():
        raise SimulationError('SPICE export needs a finite square passive port operator')
    names = [f'p{index + 1}' for index in range(count)]
    lines = ['* ETLayout fixed-temperature passive DC extraction; no switching/EM claim',
             '* reference port: ' + report['reference']]
    lines += [f'* {name} = {key}' for name, key in zip(names, report['port_ids'])]
    lines += ['.subckt ETLAYOUT ' + ' '.join(names)]
    for first in range(count):
        for second in range(first + 1, count):
            g = -float((matrix[first, second] + matrix[second, first]) / 2)
            if g < 0:
                raise SimulationError('Positive off-diagonal port terms cannot be represented by positive pairwise resistors; refine the mesh')
            if g == 0:
                continue
            resistance = 1 / g
            if not np.isfinite(resistance) or resistance <= 0:
                raise SimulationError('SPICE resistance is outside finite floating-point range; export the full operator instead')
            lines.append(f'R{first + 1}_{second + 1} {names[first]} {names[second]} {resistance:.17g}')
    lines += ['.ends ETLAYOUT', '']
    # Reconstruct from the actual serialized resistor values, independently of
    # the reduction. Basis excitations check each transfer current, not a single
    # global absolute norm dominated by strong branches.
    reconstructed = np.zeros_like(matrix)
    for line in lines:
        if not line.startswith('R'):
            continue
        _, node_a, node_b, value = line.split()
        a, b = int(node_a[1:]) - 1, int(node_b[1:]) - 1
        conductance = 1 / float(value)
        reconstructed[a, a] += conductance
        reconstructed[b, b] += conductance
        reconstructed[a, b] -= conductance
        reconstructed[b, a] -= conductance
    tolerance = 1e-8
    for port in range(count):
        if not np.allclose(reconstructed[:, port], matrix[:, port], rtol=tolerance, atol=0):
            raise SimulationError(f'SPICE fidelity cannot be established for port {report["port_ids"][port]}: transfer currents disagree; export JSON/CSV or refine the mesh')
    # Ground a strong port for the independent resistance comparison. Grounding
    # the weakest port makes a nearly floating strong subnetwork and amplifies
    # subtraction noise; it is not a change to the exported ungrounded operator.
    reference = int(np.argmax(np.diag(matrix)))
    retained = [i for i in range(count) if i != reference]
    try:
        expected = np.linalg.solve(matrix[np.ix_(retained, retained)], np.eye(count - 1))
        actual = np.linalg.solve(reconstructed[np.ix_(retained, retained)], np.eye(count - 1))
    except np.linalg.LinAlgError as exc:
        raise SimulationError('SPICE port connectivity/fidelity cannot be established') from exc
    if not np.allclose(actual, expected, rtol=tolerance, atol=0):
        raise SimulationError('SPICE driving-point/transfer resistance fidelity cannot be established; export the full operator')
    return lines


def export_multiport(report, path, format='json'):
    """Export a port matrix/metadata, labeled CSV, or passive resistor subcircuit."""
    if format not in {'json', 'csv', 'spice'}:
        raise SimulationError('Multiport export format must be json, csv or spice')
    path = Path(path)
    suffix = {'json': '.json', 'csv': '.csv', 'spice': '.cir'}[format]
    if not path.suffix:
        path = path / ('multiport' + suffix)
    path.parent.mkdir(parents=True, exist_ok=True)
    if format == 'json':
        from etlayout.io.project import _json_dump
        path.write_text(_json_dump(report), encoding='utf-8')
    elif format == 'csv':
        with path.open('w', newline='', encoding='utf-8') as stream:
            writer = csv_writer(stream)
            writer.writerow(['current_port / voltage_port [S]', *report['port_ids']])
            for key, row in zip(report['port_ids'], report['conductance_matrix']):
                writer.writerow([key, *row])
        from etlayout.io.project import _json_dump
        path.with_suffix('.metadata.json').write_text(_json_dump(report), encoding='utf-8')
    else:
        lines = _spice_lines(report)
        path.write_text('\n'.join(lines), encoding='utf-8')
    return path
