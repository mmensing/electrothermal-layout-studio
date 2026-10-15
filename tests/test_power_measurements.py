# SPDX-License-Identifier: MPL-2.0
"""Measured-field acceptance with independent geometry and circuit identities."""

from copy import deepcopy

import numpy as np
import pytest
from numerical_fixtures import linear_strip_solution as linear_strip_solution
from solver_helpers import all_region, rectangle

from etlayout.models import (
    DeviceGroup,
    ElectricalModel,
    Interface,
    MeasurementSpec,
    PhysicalPort,
    PlanarMesh,
    PortLanding,
    RegionSpec,
    SupportSelector,
    Terminal,
)
from etlayout.power.analysis import analyze_power, evaluate_measurement, evaluate_measurements
from etlayout.solvers import solve_electrical


def region(x0, x1, y0=0., y1=2e-6):
    return RegionSpec(polygons=[{'exterior': [[x0, y0], [x1, y0], [x1, y1], [x0, y1]], 'holes': []}])


def measure(project, result, quantity='potential', aggregation='mean', selector=None, **kwargs):
    return evaluate_measurement(project, result, MeasurementSpec('metric', quantity,
        selector or SupportSelector(layers=['metal']), aggregation, **kwargs))


@pytest.mark.parametrize('aggregation,expected', [('mean', .5), ('integral', 10e-12),
    ('rms', 1/np.sqrt(3)), ('percentile', .95), ('minimum', 0.), ('maximum', 1.), ('exceedance', .3)])
def test_linear_continuum_measurements_are_weighted_and_subdivision_invariant(linear_strip_solution, aggregation, expected):
    project, result = linear_strip_solution
    report = measure(project, result, aggregation=aggregation, threshold=.7 if aggregation == 'exceedance' else None)
    assert report['status'] == 'available', report
    assert report['value'] == pytest.approx(expected, rel=1e-10, abs=1e-14)
    assert report['support_area_m2'] == pytest.approx(20e-12)


def test_geometric_clipping_and_holes_have_exact_integrals():
    project, mesh = rectangle(nx=3, ny=2)
    result = solve_electrical(project, mesh)
    support = region(2e-6, 8e-6)
    support.polygons[0]['holes'] = [[[3e-6, .5e-6], [4e-6, .5e-6], [4e-6, 1.5e-6], [3e-6, 1.5e-6]]]
    selector = SupportSelector(layers=['metal'], geometry=support)
    report = measure(project, result, aggregation='integral', selector=selector)
    assert report['status'] == 'available', report
    assert report['support_area_m2'] == pytest.approx(11e-12)
    assert report['value'] == pytest.approx(6e-12 - .35e-12)


def test_unrelated_inactive_metal_does_not_relax_selected_neck_stress():
    project, mesh = rectangle()
    report = measure(project, solve_electrical(project, mesh), quantity='metal_current_density', aggregation='maximum')
    project2, mesh2 = rectangle(layers=('metal', 'unused'))
    project2.solver.floating_policy = 'reference'
    project2.layers[1].thickness = 1e-3
    other = measure(project2, solve_electrical(project2, mesh2), quantity='metal_current_density', aggregation='maximum')
    assert other['value'] == pytest.approx(report['value'])


def test_sheet_merit_is_invariant_to_reporting_thickness_and_metal_limits_reject_sheet():
    stresses = []
    for thickness in (1e-9, 1e-6):
        project, mesh = rectangle()
        project.layers[0].electrical = ElectricalModel(kind='sheet')
        project.layers[0].thickness = thickness
        result = solve_electrical(project, mesh)
        stresses.append(measure(project, result, 'sheet_current', 'maximum')['value'])
        incompatible = measure(project, result, 'metal_current_density', 'maximum')
        assert incompatible['status'] == 'unavailable'
        converted = measure(project, result, 'effective_current_density', 'maximum', effective_thickness=thickness)
        assert converted['reporting_only']
        assert converted['value'] == pytest.approx(stresses[-1] / thickness)
    assert stresses[0] == pytest.approx(stresses[1])


@pytest.mark.parametrize('change,reason', [('empty', 'Empty'), ('stale_selector', 'Stale layer'),
    ('stale_result', 'Stale result'), ('nonfinite', 'Invalid absolute'), ('invalid_temperature', 'Invalid absolute'),
    ('nonconverged', 'Nonconverged'), ('calibration', 'calibrated')])
def test_invalid_measurements_cannot_be_favorable_zero(change, reason):
    project, mesh = rectangle()
    if change == 'calibration':
        project.solver.initial_temperature = 700.
        with pytest.warns(RuntimeWarning):
            result = solve_electrical(project, mesh)
    else:
        result = solve_electrical(project, mesh)
    selector = SupportSelector(layers=['metal'])
    if change == 'empty':
        selector.geometry = region(20e-6, 30e-6)
    elif change == 'stale_selector':
        selector.layers = ['gone']
    elif change == 'stale_result':
        project.layers[0].thickness *= 2
    elif change == 'nonfinite':
        result.element_fields['metal']['temperature'][0, 0] = np.nan
    elif change == 'invalid_temperature':
        result.element_fields['metal']['temperature'][0, 0] = 0.
    elif change == 'nonconverged':
        result.converged = False
    report = measure(project, result, selector=selector, limit=1e9)
    assert report['status'] == 'unavailable'
    assert report['value'] is None
    assert not report['within_limits']
    assert reason in report['reason']


def test_postprocessing_spec_edits_do_not_make_unchanged_physics_stale():
    project, mesh = rectangle()
    result = solve_electrical(project, mesh)
    project.power.measurements = [MeasurementSpec('one', 'potential', aggregation='maximum', limit=1.1)]
    report = evaluate_measurements(project, result)
    assert report['one']['status'] == 'available'
    assert report['one']['within_limits']
    project.power.measurements[0].limit = .9
    assert not evaluate_measurements(project, result)['one']['within_limits']


@pytest.mark.parametrize('nx', [3, 17])
def test_fixed_physical_hotspot_window_is_mesh_independent_and_labeled(nx):
    project, mesh = rectangle(nx=nx)
    report = measure(project, solve_electrical(project, mesh), aggregation='maximum', averaging_length=2e-6)
    assert report['status'] == 'available', report
    assert report['value'] == pytest.approx(.95)
    assert 'square clipped' in report['window_definition']
    assert not report['mesh_sensitive_raw_maximum']


def test_hotspot_includes_small_disconnected_island_without_grid_centers():
    project, _ = rectangle()
    points = np.array([[0., 0.], [2., 0.], [0., 2.],
                       [9.2, .2], [9.4, .2], [9.2, .4]]) * 1e-6
    mesh = PlanarMesh(points, np.array([[0, 1, 2], [3, 4, 5]]),
                      {'layer:metal': np.array([0, 1]),
                       'terminal:left': np.array([0]), 'terminal:right': np.array([1])})
    result = solve_electrical(project, mesh)
    assert result.converged
    assert result.element_fields['metal']['potential'] == pytest.approx(np.array([[0.] * 3, [1.] * 3]))
    # The 1 um sampling grid has centers on the large zero-volt triangle,
    # but none on the separate 0.2 um triangle at one volt.
    report = measure(project, result, aggregation='maximum', averaging_length=2e-6)
    assert report['status'] == 'available', report
    assert report['value'] == pytest.approx(1.)
    assert report['window_center'][0] > 9e-6


def test_unreasonably_small_hotspot_grid_fails_before_allocation():
    project, mesh = rectangle()
    report = measure(project, solve_electrical(project, mesh), aggregation='maximum', averaging_length=1e-300)
    assert report['status'] == 'unavailable'
    assert '100000 centers' in report['reason']


def contact_fixture(offset=0.):
    project, mesh = rectangle(nx=1, ny=1, layers=('a', 'b'))
    project.terminals = [Terminal('bottom', 'b', kind='ground')]
    all_region(mesh, 'terminal', 'bottom')
    for index, point in enumerate(mesh.points):
        key = f'node{index}'
        project.terminals.append(Terminal(key, 'a', value=float(point[0] / 10e-6 + offset)))
        mesh.terminal_nodes[key] = np.array([index])
    all_region(mesh, 'interface', 'contact')
    project.interfaces = [Interface('contact', 'a', 'b', electrical_kind='conductance', electrical_value=1e10)]
    return project, mesh, solve_electrical(project, mesh)


def test_contact_selection_deduplicates_parent_and_instance_and_preserves_exact_heat():
    project, mesh, result = contact_fixture()
    instance = next(iter(result.analysis['contacts']))
    selector = SupportSelector(contacts=['contact', instance])
    report = measure(project, result, 'contact_heat_density', 'integral', selector)
    assert report['status'] == 'available', report
    assert report['value'] == pytest.approx(mesh.areas.sum() * 1e10 / 3)
    assert report['support_area_m2'] == pytest.approx(mesh.areas.sum())


def test_normal_density_magnitude_and_signed_counterflow_are_distinct_exact_integrals():
    project, mesh, result = contact_fixture(-.5)
    selector = SupportSelector(contacts=['contact'])
    magnitude = measure(project, result, 'contact_normal_density', 'integral', selector)
    signed = measure(project, result, 'contact_normal_density', 'integral', selector, vector_component='signed')
    assert magnitude['status'] == signed['status'] == 'available'
    assert magnitude['value'] == pytest.approx(mesh.areas.sum() * 1e10 / 4)
    assert signed['value'] == pytest.approx(0., abs=1e-14)


def test_device_groups_integrate_individual_loads_without_repeating_shared_port_total():
    project, mesh = rectangle(nx=12, layers=('a', 'b'))
    project.terminals = [Terminal('left', 'a'), Terminal('bottom', 'b')]
    all_region(mesh, 'terminal', 'bottom')
    all_region(mesh, 'interface', 'cells')
    project.interfaces = [Interface('cells', 'a', 'b', electrical_kind='load', electrical_value=2e-12,
                                    purpose='device_load')]
    project.power.ports = [PhysicalPort('supply', [PortLanding('left')], value=1.),
                          PhysicalPort('return', [PortLanding('bottom')], kind='ground')]
    project.power.devices = [DeviceGroup('first', SupportSelector(layers=['a'], geometry=region(0., 5e-6)),
        active_width=2e-6, active_area=10e-12, source_port='return', drain_port='supply'),
        DeviceGroup('second', SupportSelector(layers=['a'], geometry=region(5e-6, 10e-6)),
        active_width=2e-6, active_area=10e-12, source_port='return', drain_port='supply')]
    result = solve_electrical(project, mesh)
    groups = result.analysis['devices']
    assert all(group['status'] == 'available' for group in groups.values()), groups
    assert sum(group['current'] for group in groups.values()) == pytest.approx(result.analysis['ports']['supply']['current'])
    assert groups['first']['current'] > groups['second']['current']
    assert groups['first']['drain_voltage'] > groups['second']['drain_voltage']
    assert groups['first']['source_voltage'] == pytest.approx(0.)
    assert groups['first']['current_per_area'] == pytest.approx(groups['first']['current']/10e-12)
    before = result.analysis['loss_budget']['total_power']
    analyze_power(project, result)
    assert result.analysis['loss_budget']['total_power'] == before
    # Reporting work may be reused within one call, but a later call must
    # inspect the actual mutable physics again rather than trust a stale token.
    project.power.measurements = [MeasurementSpec('voltage', 'potential', aggregation='maximum')]
    assert evaluate_measurements(project, result)['voltage']['status'] == 'available'
    project.layers[0].thickness *= 2
    report = analyze_power(project, result)
    assert report['measurements']['voltage']['status'] == 'unavailable'
    assert 'Stale result' in report['measurements']['voltage']['reason']
    assert all(row['status'] == 'unavailable' and 'Stale result' in row['reason']
               and row['temperature_max'] is None for row in report['devices'].values())
    assert evaluate_measurement(project, result, project.power.measurements[0])['value'] is None


def test_shared_port_with_no_resolvable_finger_branch_is_unavailable():
    project, mesh = rectangle(nx=10)
    project.power.ports = [PhysicalPort('drive', [PortLanding('right')], value=1.)]
    project.power.devices = [DeviceGroup('middle', SupportSelector(layers=['metal'], geometry=region(3e-6, 7e-6)), drain_port='drive')]
    result = solve_electrical(project, mesh)
    record = result.analysis['devices']['middle']
    assert record['current'] is None
    assert record['status'] == 'unavailable'
    assert 'shared port total' in record['reason']


def test_hemt_gate_bias_uses_local_channel_and_source_feed_changes_loading():
    project, mesh = rectangle(nx=20)
    project.layers[0].electrical = ElectricalModel(kind='hemt', gate_voltage=0.)
    project.power.ports = [PhysicalPort('drain', [PortLanding('right')], value=1.),
                          PhysicalPort('source', [PortLanding('left')], kind='ground')]
    project.power.devices = [DeviceGroup('finger', SupportSelector(layers=['metal']), active_width=2e-6,
                                         source_port='source', drain_port='drain')]
    ideal = solve_electrical(project, mesh)
    first = ideal.analysis['devices']['finger']
    assert first['status'] == 'available', first
    assert first['effective_gate_bias'] < -.3
    project.power.ports[1].landings[0].resistance = 1000.
    fed = solve_electrical(project, mesh)
    second = fed.analysis['devices']['finger']
    assert second['status'] == 'available', second
    assert second['source_voltage'] > .01
    assert second['current'] < first['current']
    assert second['effective_gate_bias'] != pytest.approx(first['effective_gate_bias'])
    assert fed.analysis['loss_budget']['categories']['external_feed'] > 0



@pytest.mark.parametrize('aggregation,expected', [('mean', 1/3), ('rms', 1/np.sqrt(5)),
    ('percentile', .95**2), ('maximum', 1.), ('minimum', 0.), ('exceedance', .5)])
def test_contact_heat_uses_exact_quadratic_distribution_not_nodal_average(aggregation, expected):
    project, mesh, result = contact_fixture()
    report = measure(project, result, 'contact_heat_density', aggregation,
                     SupportSelector(contacts=['contact']), threshold=.25e10 if aggregation == 'exceedance' else None)
    assert report['status'] == 'available', report
    assert report['value'] == pytest.approx(expected if aggregation == 'exceedance' else expected*1e10)


def test_contact_heat_clipping_and_physical_hotspot_use_exact_quadratic_integrals():
    project, mesh, result = contact_fixture()
    selector = SupportSelector(contacts=['contact'], geometry=region(5e-6, 10e-6))
    clipped = measure(project, result, 'contact_heat_density', 'integral', selector)
    assert clipped['value'] == pytest.approx(mesh.areas.sum()*1e10*7/24)
    hotspot = measure(project, result, 'contact_heat_density', 'maximum',
                      SupportSelector(contacts=['contact']), averaging_length=2e-6)
    assert hotspot['value'] == pytest.approx(1e10 * (1-.9**3)/.3)


@pytest.mark.parametrize('kwargs,reason', [({'weighting': 'nodes'}, 'area weighting'),
    ({'aggregation': 'garbage'}, 'Unknown measurement aggregation'), ({'units': 'ohm'}, 'requires V'),
    ({'percentile': 101., 'aggregation': 'percentile'}, 'Percentile'),
    ({'averaging_length': -1.}, 'nonnegative'), ({'limit': np.inf}, 'finite'),
    ({'limit': .5, 'lower_limit': 1.}, 'Lower'),
    ({'quantity': 'no_field'}, 'Unknown scoped quantity')])
def test_invalid_measurement_settings_are_explicit(kwargs, reason):
    project, mesh = rectangle()
    record = measure(project, solve_electrical(project, mesh), **kwargs)
    assert record['status'] == 'unavailable'
    assert reason in record['reason']


def test_branch_measurements_require_explicit_matching_support_and_export(tmp_path):
    from etlayout.power.analysis import export_power_report

    project, mesh = rectangle()
    project.power.ports = [PhysicalPort('drive', [PortLanding('right')], value=1.)]
    result = solve_electrical(project, mesh)
    spec = MeasurementSpec('current', 'port_current', SupportSelector(ports=['drive', 'drive']), 'integral')
    record = evaluate_measurement(project, result, spec)
    assert record['value'] == pytest.approx(5.)
    spec.selector.regions = ['missing_terminal']
    mixed = evaluate_measurement(project, result, spec)
    assert mixed['status'] == 'unavailable'
    assert mixed['value'] is None
    assert 'cannot mix' in mixed['reason']
    spec.selector.regions = []
    spec.selector.layers = ['metal']
    assert evaluate_measurement(project, result, spec)['status'] == 'unavailable'
    project.power.measurements = [MeasurementSpec('power', 'loss_power', aggregation='integral')]
    analyze_power(project, result)
    output = export_power_report(result, tmp_path)
    assert output.is_file()
    assert (tmp_path/'measurements.csv').is_file()
    assert result.analysis['measurements']['power']['value'] == pytest.approx(result.metrics['electrical_power'])


def test_ideal_contact_has_zero_heat_but_unavailable_normal_density():
    project, mesh = rectangle(layers=('a', 'b'))
    project.interfaces = [Interface('ideal', 'a', 'b', electrical_kind='ideal')]
    all_region(mesh, 'interface', 'ideal')
    result = solve_electrical(project, mesh)
    selector = SupportSelector(contacts=['ideal'])
    heat = measure(project, result, 'contact_heat_density', 'integral', selector)
    assert heat['status'] == 'available'
    assert heat['value'] == 0.
    assert measure(project, result, 'contact_normal_density', 'maximum', selector)['status'] == 'unavailable'


def test_temperature_rise_requires_valid_explicit_reservoir_reference():
    project, mesh = rectangle()
    result = solve_electrical(project, mesh)
    assert measure(project, result, 'temperature_rise')['status'] == 'unavailable'
    # Area-weighted quadrature can differ by a few ULPs across numerical builds.
    rise = measure(project, result, 'temperature_rise', reference_temperature=280.)
    assert rise['value'] == pytest.approx(20., rel=0., abs=1e-12)
    assert measure(project, result, 'temperature_rise', reference_temperature=-1.)['status'] == 'unavailable'
    assert measure(project, result, 'temperature_rise', reference_boundary='gone')['status'] == 'unavailable'


def thermal_reference_fixture(two_reservoirs=False):
    from etlayout.models import HeatSource, ThermalBoundary
    from etlayout.solvers import solve_thermal

    project, mesh = rectangle(nx=3, ny=2)
    project.solver.mode = 'thermal'
    project.power.thermal_reference_temperature = 300.
    project.thermal_boundaries = [ThermalBoundary('sink', 'metal', kind='coefficient', value=1e5, ambient=300.)]
    if two_reservoirs:
        project.thermal_boundaries.append(ThermalBoundary('warm', 'metal', kind='coefficient', value=1e5, ambient=310.))
        all_region(mesh, 'thermal', 'warm')
    project.heat_sources = [HeatSource('heater', 'metal', kind='total', value=4e-5)]
    all_region(mesh, 'thermal', 'sink')
    all_region(mesh, 'heat', 'heater')
    result = solve_thermal(project, mesh)
    assert result.converged
    # Uniform film: Q = area * sum(h_i * (T - T_i)).
    expected = 315. if two_reservoirs else 320.
    assert result.metrics['temperature_average'] == pytest.approx(expected)
    return project, result, expected


def test_reanalysis_uses_current_reporting_reference_without_mutating_solved_fields():
    project, result, temperature = thermal_reference_fixture()
    configuration = deepcopy(result.configuration)
    metrics = deepcopy(result.metrics)
    fields = {name: values.copy() for name, values in result.element_fields['metal'].items()}
    project.power.thermal_reference_temperature = 310.
    report = measure(project, result, 'temperature_rise')
    assert report['status'] == 'available', report
    assert report['reference_temperature'] == 310.
    assert report['value'] == pytest.approx(temperature - 310.)
    override = measure(project, result, 'temperature_rise', reference_temperature=305.)
    assert override['value'] == pytest.approx(temperature - 305.)
    assert result.configuration == configuration
    assert result.metrics == metrics
    for name, values in fields.items():
        np.testing.assert_array_equal(result.element_fields['metal'][name], values)


def test_clearing_reporting_reference_uses_actual_reservoir_and_named_reference_precedence():
    project, result, temperature = thermal_reference_fixture(two_reservoirs=True)
    project.power.thermal_reference_temperature = None
    assert measure(project, result, 'temperature_rise')['status'] == 'unavailable'
    project.power.thermal_reference_boundary = 'warm'
    project.power.thermal_reference_temperature = 290.  # Named project reference wins, as in the thermal solver.
    assert measure(project, result, 'temperature_rise')['value'] == pytest.approx(temperature - 310.)
    override = measure(project, result, 'temperature_rise', reference_boundary='sink')
    assert override['value'] == pytest.approx(temperature - 300.)
    assert measure(project, result, 'temperature_rise', reference_temperature=305.)['value'] == pytest.approx(temperature - 305.)
    project.power.thermal_reference_boundary = 'gone'
    assert measure(project, result, 'temperature_rise')['status'] == 'unavailable'

    project, result, temperature = thermal_reference_fixture()
    project.power.thermal_reference_temperature = None
    assert measure(project, result, 'temperature_rise')['value'] == pytest.approx(temperature - 300.)


@pytest.mark.parametrize('reference', [-1., 0., float('nan'), float('inf')])
def test_reanalysis_rejects_invalid_project_reporting_temperature(reference):
    project, result, _ = thermal_reference_fixture()
    project.power.thermal_reference_temperature = reference
    report = measure(project, result, 'temperature_rise')
    assert report['status'] == 'unavailable'
    assert 'finite and positive' in report['reason']


def test_feed_resistance_changes_coupled_device_self_heating_without_geometry_change():
    from etlayout.models import ThermalBoundary
    from etlayout.solvers import solve

    project, mesh = rectangle(nx=5, ny=2, layers=('a', 'b'))
    project.solver.mode = 'electrothermal'
    project.terminals = [Terminal('left', 'a'), Terminal('bottom', 'b')]
    all_region(mesh, 'terminal', 'bottom')
    all_region(mesh, 'interface', 'cells')
    project.interfaces = [Interface('cells', 'a', 'b', electrical_kind='load', electrical_value=2e-12,
                                    purpose='device_load', temperature_coefficient=.002)]
    project.power.ports = [PhysicalPort('supply', [PortLanding('left')], value=1.),
                          PhysicalPort('return', [PortLanding('bottom')], kind='ground')]
    project.power.devices = [DeviceGroup('cell', SupportSelector(layers=['a']),
                                         source_port='return', drain_port='supply')]
    project.thermal_boundaries = [ThermalBoundary(layer, layer, value=1e11) for layer in ('a', 'b')]
    for layer in ('a', 'b'):
        all_region(mesh, 'thermal', layer)
    first = solve(project, mesh)
    assert first.converged
    original_geometry = deepcopy(project.layers)
    project.power.ports[0].landings[0].resistance = .2
    second = solve(project, mesh)
    assert second.converged
    assert project.layers == original_geometry
    assert second.analysis['devices']['cell']['current'] < first.analysis['devices']['cell']['current']
    assert second.analysis['devices']['cell']['temperature_max'] < first.analysis['devices']['cell']['temperature_max']
