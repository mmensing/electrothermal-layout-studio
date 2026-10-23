# SPDX-License-Identifier: MPL-2.0
"""Independent resistor-network checks of sparse passive port extraction."""
import json
from copy import deepcopy

import numpy as np
import pytest
from solver_helpers import rectangle

from etlayout.models import ElectricalModel, PhysicalPort, PortLanding, SimulationError, Terminal
from etlayout.power.multiport import export_multiport, extract_multiport, spice_netlist
from etlayout.solvers import solve_electrical


def two_ports():
    project, mesh = rectangle(nx=5, ny=2)
    project.power.ports = [PhysicalPort('positive', [PortLanding('right', .1)], value=1.),
                          PhysicalPort('negative', [PortLanding('left', .3)], kind='ground')]
    return project, mesh


def test_two_port_matrix_matches_exact_resistor_and_combined_bias():
    project, mesh = two_ports()
    report = extract_multiport(project, mesh)
    expected = np.array([[1., -1.], [-1., 1.]]) / .6
    matrix = np.asarray(report['conductance_matrix'])
    assert matrix == pytest.approx(expected, rel=1e-12, abs=1e-12)
    assert report['resistance_matrix'][0][0] == pytest.approx(.6)
    assert report['validation']['factorizations'] == 1
    assert report['validation']['minimum_eigenvalue_S'] >= -1e-12
    voltages = np.array([2.3, -.7])
    for port, voltage in zip(project.power.ports, voltages):
        port.kind, port.value = 'voltage', voltage
    solved = solve_electrical(project, mesh)
    currents = [solved.analysis['ports'][key]['current'] for key in report['port_ids']]
    assert matrix @ voltages == pytest.approx(currents, rel=1e-12)
    assert voltages @ matrix @ voltages == pytest.approx(solved.metrics['electrical_power'])
    assert project.power.ports[0].value == 2.3


def star_fixture():
    project, mesh = rectangle(nx=5, ny=2, layers=('a', 'b', 'c'))
    project.terminals = []
    central = []
    for index, layer in enumerate(project.layers):
        layer.thickness /= index + 1
        for side in ('left', 'right'):
            key = f'{layer.id}_{side}'
            mesh.terminal_nodes[key] = mesh.terminal_nodes[side]
            project.terminals.append(Terminal(key, layer.id))
        central.append(PortLanding(f'{layer.id}_left'))
        project.power.ports.append(PhysicalPort(layer.id, [PortLanding(f'{layer.id}_right', .1 * (index + 1))]))
    project.power.ports.append(PhysicalPort('center', central, kind='floating'))
    return project, mesh


def test_three_port_schur_matches_independent_star_reduction_and_reference_change():
    project, mesh = star_fixture()
    g = 1. / np.array([.3, .6, .9])
    exact = np.diag(g) - np.outer(g, g) / g.sum()
    report = extract_multiport(project, mesh, ['a', 'b', 'c'], reference='c')
    matrix = np.asarray(report['conductance_matrix'])
    assert matrix == pytest.approx(exact, rel=1e-12, abs=1e-12)
    alternate = extract_multiport(project, mesh, ['a', 'b', 'c'], reference='a')
    assert alternate['conductance_matrix'] == report['conductance_matrix']
    voltage = np.array([1., .3, -.2])
    for port, value in zip(project.power.ports[:3], voltage):
        port.value = value
    result = solve_electrical(project, mesh)
    expected_current = g * (voltage - np.dot(g, voltage) / g.sum())
    assert matrix @ voltage == pytest.approx(expected_current, rel=1e-12)
    assert [result.analysis['ports'][key]['current'] for key in ('a', 'b', 'c')] == pytest.approx(expected_current)
    assert matrix.sum(axis=1) == pytest.approx(np.zeros(3), abs=1e-12)
    assert np.linalg.eigvalsh(matrix).min() >= -1e-12


def test_matrix_rejects_hidden_reservoirs_nonlinear_laws_and_nonindependent_aliases():
    project, mesh = two_ports()
    project.power.ports.pop()
    with pytest.raises(SimulationError, match='at least two'):
        extract_multiport(project, mesh)
    project, mesh = two_ports()
    project.power.ports[1].landings = []
    with pytest.raises(SimulationError, match='all electrodes'):
        extract_multiport(project, mesh)
    project, mesh = two_ports()
    project.layers[0].electrical = ElectricalModel(kind='hemt')
    with pytest.raises(SimulationError, match='voltage-dependent'):
        extract_multiport(project, mesh)
    project, mesh = two_ports()
    for port in project.power.ports:
        port.external_node = 'common'
    with pytest.raises(SimulationError, match='not independent'):
        extract_multiport(project, mesh)


def test_fixed_temperature_and_original_model_are_reported_without_mutation():
    project, mesh = two_ports()
    source = deepcopy(project.to_dict())
    report = extract_multiport(project, mesh, temperatures=350.)
    assert report['temperature'] == {'kind': 'uniform_fixed', 'temperature_K': 350.}
    assert project.to_dict() == source
    assert report['configuration'] == source
    for invalid in (-1, np.inf):
        with pytest.raises(SimulationError, match='positive and finite'):
            extract_multiport(project, mesh, temperatures=invalid)


@pytest.mark.parametrize('weak_resistance', [0., 1e6, 1e9])
def test_json_csv_and_spice_export_reproduce_port_operator(tmp_path, weak_resistance):
    project, mesh = star_fixture()
    if weak_resistance:
        project.power.ports[2].landings[0].resistance = weak_resistance
    report = extract_multiport(project, mesh, ['a', 'b', 'c'])
    for format in ('json', 'csv', 'spice'):
        destination = export_multiport(report, tmp_path / format, format)
        assert destination.is_file()
    assert (tmp_path / 'csv' / 'multiport.metadata.json').is_file()
    # Independently reconstruct the exported resistor graph, not the matrix
    # implementation, to verify the equivalent circuit's port response.
    circuit = np.zeros((3, 3))
    text = (tmp_path / 'spice' / 'multiport.cir').read_text()
    for line in text.splitlines():
        if not line.startswith('R'):
            continue
        _, node_a, node_b, resistance = line.split()
        a, b = int(node_a[1:]) - 1, int(node_b[1:]) - 1
        g = 1. / float(resistance)
        circuit[a, a] += g
        circuit[b, b] += g
        circuit[a, b] -= g
        circuit[b, a] -= g
    operator = np.asarray(report['conductance_matrix'])
    assert circuit == pytest.approx(operator, rel=1e-12, abs=1e-12)
    assert np.allclose(circuit, operator, rtol=1e-8, atol=0)
    g = 1 / np.array([.3, .6, weak_resistance + .6 if weak_resistance else .9])
    # Basis voltages excite every port, including the weakest. Relative errors
    # are assessed for each current; an absolute strong-port bound cannot hide
    # an isolated weak port.
    for voltages in np.eye(3):
        expected = g * (voltages - np.dot(g, voltages) / g.sum())
        assert np.allclose(circuit @ voltages, expected, rtol=1e-8, atol=0)
    assert circuit[2, 2] > 0 and circuit[0, 2] < 0 and circuit[1, 2] < 0


@pytest.mark.parametrize('damage', ['positive', 'asymmetric', 'row_balance', 'disconnected', 'nonfinite'])
def test_spice_rejects_unestablished_fidelity_without_writing_circuit(tmp_path, damage):
    project, mesh = two_ports()
    report = extract_multiport(project, mesh)
    matrix = np.asarray(report['conductance_matrix'])
    if damage == 'positive':
        matrix[0, 1] = matrix[1, 0] = 1e-14
    elif damage == 'asymmetric':
        matrix[1, 0] *= .999
    elif damage == 'row_balance':
        matrix[0, 0] *= .999
    elif damage == 'disconnected':
        matrix[:] = 0
    else:
        matrix[0, 0] = np.nan
    report['conductance_matrix'] = matrix.tolist()
    path = tmp_path / 'invalid.cir'
    with pytest.raises(SimulationError, match='SPICE|off-diagonal'):
        export_multiport(report, path, 'spice')
    assert not path.exists()



def test_spatial_fixed_temperature_arrays_match_nodal_and_local_representations():
    project, mesh = rectangle(nx=60, ny=1)
    project.materials['copper'].resistivity.kind = 'linear'
    project.materials['copper'].resistivity.alpha = .002
    project.power.ports = [PhysicalPort('positive', [PortLanding('right', .1)]),
                          PhysicalPort('negative', [PortLanding('left', .3)], kind='ground')]
    temperature = 300. + 100.*mesh.points[:, 0]/10e-6
    nodal = extract_multiport(project, mesh, temperatures={'metal': temperature})
    local = extract_multiport(project, mesh, temperatures={'metal': temperature[mesh.triangles]})
    assert nodal['temperature']['layers']['metal'] == {'minimum_K': 300., 'maximum_K': 400.}
    assert local['conductance_matrix'] == pytest.approx(np.asarray(nodal['conductance_matrix']))
    # Integral rho(T(x)) dx/(width*thickness), plus known finite contacts.
    assert local['resistance_matrix'][0][0] == pytest.approx(.62, rel=2e-5)
    with pytest.raises(SimulationError, match='positive and finite'):
        extract_multiport(project, mesh, temperatures={'metal': np.full(len(mesh.points), np.nan)})


def test_unconnected_ports_and_invalid_reference_fail_explicitly():
    project, mesh = rectangle(layers=('a', 'b'))
    project.terminals[1].layer_id = 'b'
    project.power.ports = [PhysicalPort('first', [PortLanding('left')]),
                          PhysicalPort('second', [PortLanding('right')])]
    with pytest.raises(SimulationError, match='disconnected'):
        extract_multiport(project, mesh)
    project, mesh = two_ports()
    with pytest.raises(SimulationError, match='reference'):
        extract_multiport(project, mesh, reference='missing')
    report = extract_multiport(project, mesh)
    with pytest.raises(SimulationError, match='export format'):
        export_multiport(report, 'unused.output', format='binary')


@pytest.mark.parametrize("identifier", [
    "supply",
    "supply\nVUNWANTED p1 p2 99",
    "supply\r.include restricted.cir",
    "supply\r\n.control\nquit\n.endc",
    "supply\vVUNWANTED p1 p2 99",
    "supply\fVUNWANTED p1 p2 99",
    "supply\x85VUNWANTED p1 p2 99",
    "supply\u2028VUNWANTED p1 p2 99",
    "supply\u2029VUNWANTED p1 p2 99",
    'Kelvin \u03a9 / \u00b5m \u6e90 "quoted" \\backslash',
])
def test_spice_port_and_reference_labels_cannot_create_circuit_statements(tmp_path, identifier):
    project, mesh = two_ports()
    project.power.ports[0].id = identifier
    report = extract_multiport(project, mesh, reference=identifier)
    netlist = spice_netlist(report)
    # Validate what the external reader receives, rather than just the list of
    # lines used internally to assemble the netlist. Only the resistor model's
    # declared subcircuit, one resistor and its terminator may be executable.
    statements = [line for line in netlist.splitlines() if line and not line.startswith("*")]
    assert statements[0] == ".subckt ETLAYOUT p1 p2"
    assert len(statements) == 3
    assert statements[1].split()[:3] == ["R1_2", "p1", "p2"]
    assert float(statements[1].split()[3]) == pytest.approx(.6, rel=1e-12)
    assert statements[2] == ".ends ETLAYOUT"
    assert netlist.isascii()
    for prefix in ("* reference port: ", "* p1 = "):
        encoded = next(line.removeprefix(prefix) for line in netlist.splitlines() if line.startswith(prefix))
        assert json.loads('"' + encoded + '"') == identifier
    if identifier == "supply":
        assert "* p1 = supply\n" in netlist
    exported = export_multiport(report, tmp_path / "reviewed.cir", "spice")
    assert exported.read_text(encoding="utf-8") == netlist
