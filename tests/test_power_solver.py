# SPDX-License-Identifier: MPL-2.0
"""Independent circuits and continuum identities for component and power APIs."""
from copy import deepcopy

import numpy as np
import pytest
from solver_helpers import all_region, rectangle

from etlayout.models import (
    HeatSource,
    Interface,
    PhysicalPort,
    PlanarMesh,
    PortLanding,
    SimulationError,
    Terminal,
    ThermalBoundary,
)
from etlayout.solvers import solve_electrical, solve_thermal
from etlayout.solvers.electrical import solve_state
from etlayout.solvers.fem import Space


def point_touch():
    project, _ = rectangle()
    points = np.array([[0, 0], [1, 0], [0, 1], [-1, 0], [0, -1]], dtype=float) * 1e-6
    mesh = PlanarMesh(points, np.array([[0, 1, 2], [0, 3, 4]]),
                      {'layer:metal': np.array([0, 1]), 'terminal:left': np.array([0]),
                       'terminal:right': np.array([1])})
    return project, mesh


def test_authoritative_p1_fields_preserve_point_touch_states_and_zero_power():
    project, mesh = point_touch()
    result = solve_electrical(project, mesh)
    assert result.converged
    assert result.element_fields['metal']['potential'] == pytest.approx(np.array([[0., 0., 0.], [1., 1., 1.]]))
    assert result.metrics['electrical_power'] == 0
    # Legacy visualization array is deliberately not authoritative.
    assert result.fields['metal']['potential'][0] == .5
    temperatures = {'metal': np.array([[300., 300., 300.], [400., 400., 400.]])}
    warm = solve_electrical(project, mesh, temperatures=temperatures)
    assert warm.element_fields['metal']['temperature'] == pytest.approx(temperatures['metal'])
    with pytest.raises(SimulationError, match='point-touch'):
        solve_electrical(project, mesh, temperatures={'metal': warm.fields['metal']['temperature']})


def test_authoritative_thermal_fields_preserve_point_touch_temperatures():
    project, mesh = point_touch()
    project.thermal_boundaries = [ThermalBoundary('cold', 'metal', kind='temperature', value=300.),
                                  ThermalBoundary('hot', 'metal', kind='temperature', value=400.)]
    mesh.regions['thermal:cold'] = np.array([0])
    mesh.regions['thermal:hot'] = np.array([1])
    result = solve_thermal(project, mesh)
    assert result.converged
    assert result.element_fields['metal']['temperature'] == pytest.approx(np.array([[300.] * 3, [400.] * 3]))
    assert result.metrics['thermal_reference'] is None
    assert result.metrics['thermal_resistance'] is None


def test_two_reservoir_conduction_has_signed_flows_but_no_device_resistance():
    project, mesh = rectangle()
    mesh.boundary_nodes = {'cold': mesh.terminal_nodes['left'], 'hot': mesh.terminal_nodes['right']}
    project.thermal_boundaries = [ThermalBoundary('cold', 'metal', kind='temperature', value=300.),
                                  ThermalBoundary('hot', 'metal', kind='temperature', value=400.)]
    result = solve_thermal(project, mesh)
    exact = 10. * .5e-6 * 2e-6 / 10e-6 * 100.
    assert result.analysis['reservoirs']['cold']['heat_out'] == pytest.approx(exact)
    assert result.analysis['reservoirs']['hot']['heat_out'] == pytest.approx(-exact)
    assert result.metrics['thermal_resistance'] is None
    project.power.thermal_reference_boundary = 'cold'
    selected = solve_thermal(project, mesh)
    assert selected.metrics['thermal_reference'] == 300.
    assert selected.metrics['temperature_rise'] == pytest.approx(100.)
    assert selected.metrics['thermal_resistance'] is None


@pytest.mark.parametrize('nx', [4, 21])
def test_named_thermal_reference_is_independent_of_boundary_node_count(nx):
    project, mesh = rectangle(nx=nx, ny=2)
    project.thermal_boundaries = [ThermalBoundary('cold', 'metal', value=1e6, ambient=280.),
                                  ThermalBoundary('hot', 'metal', value=1e6, ambient=320.)]
    all_region(mesh, 'thermal', 'cold')
    all_region(mesh, 'thermal', 'hot')
    project.heat_sources = [HeatSource('heat', 'metal', kind='total', value=.001)]
    all_region(mesh, 'heat', 'heat')
    project.power.thermal_reference_boundary = 'cold'
    result = solve_thermal(project, mesh)
    assert result.metrics['thermal_reference'] == 280.
    assert result.metrics['temperature_max'] == pytest.approx(325.)
    assert result.metrics['thermal_resistance'] == pytest.approx(45000.)
    assert 'operational' in result.analysis['thermal_reference']['thermal_resistance_kind']


@pytest.mark.parametrize('resistance', [0., .3])
def test_external_current_port_solves_unknown_voltage_and_exact_series_feed(resistance):
    project, mesh = rectangle()
    project.power.ports = [PhysicalPort('drive', [PortLanding('right', resistance, .25)], kind='current', value=2.)]
    state = solve_state(project, mesh)
    result = state.result
    port = result.analysis['ports']['drive']
    assert result.converged
    assert port['current'] == pytest.approx(2.)
    assert port['voltage'] == pytest.approx(2. * (.2 + resistance))
    assert port['feed_power'] == pytest.approx(4. * resistance)
    assert result.metrics['electrical_power'] == pytest.approx(4. * (.2 + resistance))
    assert result.metrics['power_balance_error'] < 1e-12
    budget = result.analysis['loss_budget']
    assert budget['total_power'] == pytest.approx(result.metrics['electrical_power'])
    assert budget['thermal_deposition'] == pytest.approx(.8 + resistance)
    assert budget['excluded_power'] == pytest.approx(3. * resistance)
    assert sum(np.dot(values, mesh.areas) for values in state.heat.values()) == pytest.approx(budget['thermal_deposition'])
    assert state.matrix.shape[0] == len(state.rhs)
    assert state.port_dofs['drive'] < len(state.rhs)


def test_multiple_landings_share_external_node_but_distribute_by_branch_resistance():
    project, mesh = rectangle(layers=('a', 'b'))
    project.layers[1].thickness /= 2
    mesh.terminal_nodes.update(left_b=mesh.terminal_nodes['left'], right_b=mesh.terminal_nodes['right'])
    project.terminals.extend([Terminal('left_b', 'b', kind='ground'), Terminal('right_b', 'b')])
    project.power.ports = [PhysicalPort('drive', [PortLanding('right', .1), PortLanding('right_b', .2)],
                                       kind='current', value=1.)]
    result = solve_electrical(project, mesh)
    assert result.converged
    port = result.analysis['ports']['drive']
    assert port['voltage'] == pytest.approx(.2)
    assert [x['current'] for x in port['landings']] == pytest.approx([2/3, 1/3])
    assert port['current'] == pytest.approx(1.)
    assert result.metrics['effective_resistance'] is None  # three physical reservoirs


def test_shared_external_node_aliases_and_conflicting_voltage_are_explicit():
    project, mesh = rectangle()
    project.power.ports = [PhysicalPort('supply', [PortLanding('right', .1)], value=1., external_node='vdd'),
                          PhysicalPort('return', [PortLanding('left', .1)], kind='ground')]
    result = solve_electrical(project, mesh)
    assert result.converged
    assert result.analysis['ports']['supply']['current'] == pytest.approx(2.5)
    assert result.metrics['effective_resistance'] == pytest.approx(.4)
    project.power.ports[1].external_node = 'vdd'
    with pytest.raises(SimulationError, match='conflicting voltages'):
        solve_electrical(project, mesh)


def test_passive_probe_does_not_flatten_bus_but_floating_electrode_does():
    project, mesh = rectangle(nx=20)
    baseline = solve_electrical(project, mesh)
    mesh.regions['terminal:sense'] = np.flatnonzero((mesh.centroids[:, 0] > 4e-6) & (mesh.centroids[:, 0] < 6e-6))
    project.terminals.append(Terminal('sense', 'metal', kind='probe'))
    probe = solve_electrical(project, mesh)
    assert probe.fields['metal']['potential'] == pytest.approx(baseline.fields['metal']['potential'])
    assert probe.terminals['sense']['voltage'] == pytest.approx(.5)
    assert probe.terminals['sense']['current'] == 0.
    project.terminals[-1].kind = 'floating'
    floating = solve_electrical(project, mesh)
    nodes = np.unique(mesh.triangles[mesh.regions['terminal:sense']])
    assert np.ptp(floating.fields['metal']['potential'][nodes]) == 0.
    assert floating.metrics['electrical_power'] > probe.metrics['electrical_power']


@pytest.mark.parametrize('nx', [4, 12])
@pytest.mark.parametrize('support', ['area', 'edge', 'point'])
def test_passive_probe_uses_physical_measure_on_nonuniform_mesh(nx, support):
    project, mesh = rectangle(nx=nx, ny=2)
    # Strongly nonuniform refinement preserves the analytic solution V=x/L.
    mesh.points[:, 0] = 10e-6 * (mesh.points[:, 0] / 10e-6)**2
    project.terminals.append(Terminal('sense', 'metal', kind='probe'))
    if support == 'area':
        all_region(mesh, 'terminal', 'sense')
    elif support == 'edge':
        mesh.terminal_nodes['sense'] = np.flatnonzero(mesh.points[:, 1] == 0.)
    else:
        mesh.terminal_nodes['sense'] = np.array([nx // 2])
    result = solve_electrical(project, mesh)
    assert result.converged
    assert result.fields['metal']['potential'] == pytest.approx(mesh.points[:, 0] / 10e-6)
    assert result.terminals['sense']['voltage'] == pytest.approx(.25 if support == 'point' else .5)
    assert result.terminals['sense']['current'] == result.terminals['sense']['power'] == 0.
    assert result.metrics['electrical_power'] == pytest.approx(5.)
    if support != 'point':
        assert abs(result.fields['metal']['potential'].mean() - .5) > .1


def contact_array(nx=10, semantics='instance'):
    project, mesh = rectangle(nx=nx, layers=('a', 'b'))
    project.terminals = [Terminal('top', 'a', value=1.), Terminal('bottom', 'b', kind='ground')]
    all_region(mesh, 'terminal', 'top')
    all_region(mesh, 'terminal', 'bottom')
    mesh.regions['interface:array'] = np.flatnonzero((mesh.centroids[:, 0] < 2e-6) | (mesh.centroids[:, 0] > 8e-6))
    project.interfaces = [Interface('array', 'a', 'b', electrical_kind='lumped', electrical_value=2.,
                                    resistance_semantics=semantics)]
    return project, mesh


@pytest.mark.parametrize('semantics,current', [('array', .5), ('instance', 1.)])
def test_per_instance_and_array_resistance_semantics_and_exact_fields(semantics, current):
    project, mesh = contact_array(semantics=semantics)
    result = solve_electrical(project, mesh)
    assert result.converged
    contacts = list(result.analysis['contacts'].values())
    assert len(contacts) == 2
    assert sum(x['current_a_to_b'] for x in contacts) == pytest.approx(current)
    assert sum(x['power'] for x in contacts) == pytest.approx(result.metrics['contact_power'])
    assert [x['absolute_loading_fraction'] for x in contacts] == pytest.approx([.5, .5])
    e = mesh.regions['interface:array']
    assert result.interface_fields['array']['voltage_drop'][e] == pytest.approx(np.ones((len(e), 3)))
    assert result.analysis['loss_budget']['categories']['contact'] == pytest.approx(current)


def test_contact_identity_survives_refinement_and_triangle_order():
    ids = []
    for nx in (10, 20):
        project, mesh = contact_array(nx)
        result = solve_electrical(project, mesh)
        ids.append(set(result.analysis['contacts']))
    assert ids[0] == ids[1]
    project, mesh = contact_array(10)
    mesh.regions['interface:array'] = mesh.regions['interface:array'][::-1]
    assert set(solve_electrical(project, mesh).analysis['contacts']) == ids[0]


def test_one_sided_feed_produces_nonuniform_via_loading_and_missing_via_changes_solution():
    project, mesh = contact_array(20)
    project.terminals = [Terminal('left', 'a', value=1.), Terminal('bottom', 'b', kind='ground')]
    result = solve_electrical(project, mesh)
    values = sorted(result.analysis['contacts'].values(), key=lambda x: x['current_a_to_b'])
    assert values[0]['current_a_to_b'] < values[1]['current_a_to_b']
    assert result.converged
    assert sum(x['current_a_to_b'] for x in values) == pytest.approx(result.terminals['left']['current'])
    removed = deepcopy(mesh)
    removed.regions['interface:array'] = mesh.regions['interface:array'][mesh.centroids[mesh.regions['interface:array'], 0] < 2e-6]
    missing = solve_electrical(project, removed)
    assert len(missing.analysis['contacts']) == 1
    assert missing.terminals['left']['current'] < result.terminals['left']['current']


def test_distributed_areal_load_uses_local_drop_and_temperature_not_uniform_current():
    project, mesh = rectangle(nx=20, layers=('a', 'b'))
    project.terminals = [Terminal('left', 'a', value=1.), Terminal('bottom', 'b', kind='ground')]
    all_region(mesh, 'terminal', 'bottom')
    all_region(mesh, 'interface', 'cells')
    project.interfaces = [Interface('cells', 'a', 'b', electrical_kind='load', electrical_value=2e-12,
                                    purpose='device_load', temperature_coefficient=.01)]
    result = solve_electrical(project, mesh)
    assert result.converged
    field = result.interface_fields['cells']['j_normal']
    drop = result.interface_fields['cells']['voltage_drop']
    assert field == pytest.approx(drop / 2e-12)
    assert np.ptp(field) > 1e10
    assert result.analysis['loss_budget']['categories']['device_load'] == pytest.approx(result.metrics['contact_power'])
    warm = solve_electrical(project, mesh, temperatures={key: np.full(len(mesh.points), 400.) for key in ('a', 'b')})
    assert warm.interface_fields['cells']['j_normal'] == pytest.approx(warm.interface_fields['cells']['voltage_drop'] / 4e-12)
    assert warm.terminals['left']['current'] < result.terminals['left']['current']


def test_ideal_contact_reaction_reports_uniqueness_and_zero_loss():
    project, mesh = rectangle(layers=('a', 'b'))
    project.interfaces = [Interface('tie', 'a', 'b', electrical_kind='ideal')]
    all_region(mesh, 'interface', 'tie')
    result = solve_electrical(project, mesh)
    record = next(iter(result.analysis['contacts'].values()))
    assert record['status'] == 'available'
    assert record['power'] == 0.
    assert record['current_a_to_b'] == pytest.approx(0., abs=1e-12)  # lateral parallel path net transfer cancels
    project.interfaces.append(Interface('redundant', 'a', 'b', electrical_kind='ideal'))
    all_region(mesh, 'interface', 'redundant')
    result = solve_electrical(project, mesh)
    assert all(x['status'] == 'nonunique' and x['current_a_to_b'] is None for x in result.analysis['contacts'].values())
    assert result.metrics['contact_power'] == 0.


def test_invalid_active_temperature_cannot_silently_fall_back_to_initial():
    project, mesh = rectangle()
    values = np.full((len(mesh.triangles), 3), 300.)
    values[0, 0] = np.nan
    with pytest.raises(SimulationError, match='nonfinite active'):
        Space(project, mesh, electrical=True).values_from_fields({'metal': values}, 300.)


@pytest.mark.parametrize('offset,expected_current,expected_absolute,expected_power', [
    (0., .5, .5, 1/3), (-.5, 0., .25, 1/12)])
def test_normal_field_mass_quadrature_and_counterflow_absolute_loading(offset, expected_current, expected_absolute, expected_power):
    project, mesh = rectangle(nx=1, ny=1, layers=('a', 'b'))
    project.terminals = [Terminal('bottom', 'b', kind='ground')]
    all_region(mesh, 'terminal', 'bottom')
    for index, point in enumerate(mesh.points):
        key = f'node{index}'
        project.terminals.append(Terminal(key, 'a', value=float(point[0] / 10e-6 + offset)))
        mesh.terminal_nodes[key] = np.array([index])
    all_region(mesh, 'interface', 'contact')
    project.interfaces = [Interface('contact', 'a', 'b', electrical_kind='conductance', electrical_value=1e10)]
    result = solve_electrical(project, mesh)
    assert result.converged
    scale = mesh.areas.sum() * 1e10
    record = next(iter(result.analysis['contacts'].values()))
    assert record['current_a_to_b'] == pytest.approx(scale * expected_current, abs=1e-14)
    assert record['absolute_current'] == pytest.approx(scale * expected_absolute)
    assert record['power'] == pytest.approx(scale * expected_power)
    assert record['absolute_loading_fraction'] == pytest.approx(1.)
    if offset:
        assert record['signed_loading_fraction'] is None


def test_overlapping_ideal_reservoirs_do_not_invent_individual_heat_split():
    project, mesh = rectangle()
    project.thermal_boundaries = [ThermalBoundary(key, 'metal', kind='temperature', value=300.)
                                  for key in ('first', 'second')]
    for key in ('first', 'second'):
        all_region(mesh, 'thermal', key)
    project.heat_sources = [HeatSource('heat', 'metal', kind='total', value=.01)]
    all_region(mesh, 'heat', 'heat')
    result = solve_thermal(project, mesh)
    assert result.converged
    assert result.metrics['heat_out'] == pytest.approx(.01)
    assert all(record['status'] == 'nonunique' and record['heat_out'] is None
               for record in result.analysis['reservoirs'].values())


def test_point_touch_thermal_restart_keeps_authoritative_component_states():
    from etlayout.solvers import solve

    project, mesh = point_touch()
    project.solver.mode = 'thermal'
    project.thermal_boundaries = [ThermalBoundary('cold', 'metal', kind='temperature', value=300.),
                                  ThermalBoundary('hot', 'metal', kind='temperature', value=400.)]
    mesh.regions['thermal:cold'] = np.array([0])
    mesh.regions['thermal:hot'] = np.array([1])
    source = solve(project, mesh)
    project.transient.enabled = True
    project.transient.stop_time = .2
    project.transient.time_step = .1
    project.transient.output_interval = .1
    resumed = solve(project, mesh, initial_result=source)
    assert resumed.converged
    for snapshot in resumed.transient.snapshots:
        assert snapshot.element_fields['metal']['temperature'] == pytest.approx(np.array([[300.] * 3, [400.] * 3]))
    legacy = deepcopy(source)
    legacy.element_fields = {}
    with pytest.raises(SimulationError, match='Legacy nodal'):
        solve(project, mesh, initial_result=legacy)



def test_unique_ideal_contact_reaction_transfers_nonzero_current_between_layers():
    project, mesh = rectangle(nx=10, layers=('a', 'b'))
    project.terminals = [Terminal('left', 'a', value=1.), Terminal('right', 'b', kind='ground')]
    project.interfaces = [Interface('tie', 'a', 'b', electrical_kind='ideal')]
    mesh.regions['interface:tie'] = np.flatnonzero(mesh.centroids[:, 0] < 2e-6)
    result = solve_electrical(project, mesh)
    record = next(iter(result.analysis['contacts'].values()))
    assert result.converged
    assert record['status'] == 'available'
    assert record['current_a_to_b'] > 0
    assert record['current_a_to_b'] == pytest.approx(result.terminals['left']['current'])
    assert record['power'] == 0.


def test_declared_external_node_shares_voltage_across_distinct_physical_ports():
    project, mesh = rectangle(layers=('a', 'b'))
    project.layers[1].thickness /= 2
    mesh.terminal_nodes.update(left_b=mesh.terminal_nodes['left'], right_b=mesh.terminal_nodes['right'])
    project.terminals.extend([Terminal('left_b', 'b', kind='ground'), Terminal('right_b', 'b')])
    project.power.ports = [PhysicalPort('feed_a', [PortLanding('right', .1)], value=1., external_node='supply'),
                          PhysicalPort('feed_b', [PortLanding('right_b', .2)], value=1., external_node='supply')]
    result = solve_electrical(project, mesh)
    assert result.converged
    a, b = result.analysis['ports']['feed_a'], result.analysis['ports']['feed_b']
    assert a['voltage'] == b['voltage'] == pytest.approx(1.)
    assert a['current'] == pytest.approx(1/.3)
    assert b['current'] == pytest.approx(1/.6)
    assert result.metrics['current_balance_error'] < 1e-12
    assert result.metrics['power_balance_error'] < 1e-12


def test_finite_landing_preserves_resistive_bus_gradient_and_exact_footprint_loss():
    project, mesh = rectangle(nx=20)
    mesh.regions['terminal:right'] = np.flatnonzero(mesh.centroids[:, 0] > 8e-6)
    project.power.ports = [PhysicalPort('drive', [PortLanding('right', .2)], value=1.)]
    finite = solve_electrical(project, mesh)
    nodes = np.unique(mesh.triangles[mesh.regions['terminal:right']])
    assert np.ptp(finite.fields['metal']['potential'][nodes]) > 1e-3
    assert finite.analysis['ports']['drive']['landings'][0]['resistance_model'] == 'distributed_total_contact_resistance'
    assert finite.metrics['power_balance_error'] < 1e-12
    project.power.ports[0].landings[0].resistance = 0.
    ideal = solve_electrical(project, mesh)
    assert np.ptp(ideal.fields['metal']['potential'][nodes]) == 0.
    assert ideal.analysis['ports']['drive']['feed_power'] == 0.
