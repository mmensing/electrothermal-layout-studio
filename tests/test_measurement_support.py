# SPDX-License-Identifier: MPL-2.0
"""Exact nested geometry and fraction constraints, with independent integrals."""
from copy import deepcopy

import numpy as np
import pytest
from numerical_fixtures import linear_strip_solution as linear_strip_solution

from etlayout.io.project import project_from_dict
from etlayout.models import DeviceGroup, MeasurementSpec, RegionSpec, SupportSelector
from etlayout.power.analysis import evaluate_measurement
from etlayout.technology.power_validation import validate_power_settings


def clip(x0, x1):
    return RegionSpec(polygons=[{'bounds': [x0 * 1e-6, 0, x1 * 1e-6, 2e-6]}])


@pytest.mark.parametrize('path', ['direct', 'device', 'nested', 'union', 'intersection', 'hole'])
def test_exact_nested_support_matches_continuum_and_direct_selection(linear_strip_solution, path):
    source, result = linear_strip_solution
    project = deepcopy(source)
    project.power.devices = [DeviceGroup('a', selector=SupportSelector(layers=['metal'], geometry=clip(0, 2.5))),
                             DeviceGroup('b', selector=SupportSelector(layers=['metal'], geometry=clip(1, 4))),
                             DeviceGroup('nested', selector=SupportSelector(devices=['a']))]
    selector = SupportSelector(layers=['metal'], geometry=clip(0, 2.5))
    area, integral = 5e-12, .625e-12
    if path == 'device':
        selector = SupportSelector(devices=['a'])
    elif path == 'nested':
        selector = SupportSelector(devices=['nested'])
    elif path == 'union':
        selector = SupportSelector(devices=['a', 'b'])
        area, integral = 8e-12, 1.6e-12
    elif path == 'intersection':
        selector = SupportSelector(devices=['nested'], geometry=clip(1, 4))
        area, integral = 3e-12, .525e-12
    elif path == 'hole':
        spec = clip(0, 2.5)
        spec.polygons = [{'exterior': [[0, 0], [2.5e-6, 0], [2.5e-6, 2e-6], [0, 2e-6]],
                          'holes': [[[.5e-6, .5e-6], [1.5e-6, .5e-6], [1.5e-6, 1.5e-6], [.5e-6, 1.5e-6]]]}]
        project.power.devices[0].selector.geometry = spec
        selector = SupportSelector(devices=['nested'])
        area, integral = 4e-12, .525e-12
    spec = MeasurementSpec('cut', 'potential', selector, 'integral')
    report = evaluate_measurement(project, result, spec)
    assert report['status'] == 'available', report
    assert report['support_area_m2'] == pytest.approx(area, rel=1e-12)
    assert report['value'] == pytest.approx(integral, rel=1e-10, abs=1e-24)
    mean = evaluate_measurement(project, result, MeasurementSpec('mean', 'potential', selector, 'mean'))
    assert mean['value'] == pytest.approx(integral / area, rel=1e-10)
    assert result.configuration == source.to_dict()


@pytest.mark.parametrize('threshold,fraction,allowed,feasible', [(-1, 1., .9, False), (.1, .9, .8, False),
                                                              (.7, .3, .4, True), (1., 0., 0., True)])
def test_exceedance_threshold_and_fraction_are_independent(linear_strip_solution, threshold, fraction, allowed, feasible):
    project, result = linear_strip_solution
    spec = MeasurementSpec('fraction', 'potential', aggregation='exceedance', threshold=threshold,
                           threshold_units='V', units='fraction', max_exceedance_fraction=allowed)
    report = evaluate_measurement(project, result, spec)
    assert report['status'] == 'available', report
    assert report['value'] == pytest.approx(fraction, abs=1e-12)
    assert report['within_limits'] is feasible
    clone = deepcopy(project)
    clone.power.measurements = [spec]
    assert not validate_power_settings(clone)
    loaded = project_from_dict(clone.to_dict())
    assert loaded.power.measurements[0] == spec


@pytest.mark.parametrize('settings,reason', [({'limit': .1}, 'Legacy/ambiguous'),
    ({'threshold': .1, 'limit': .2}, 'Legacy/ambiguous'), ({'threshold': .1, 'threshold_units': 'K'}, 'requires V'),
    ({'threshold': .1, 'units': 'V'}, 'fraction'), ({'threshold': .1, 'max_exceedance_fraction': 1.1}, '[0, 1]'),
    ({'threshold': .1, 'max_exceedance_fraction': -.1}, '[0, 1]'),
    ({'threshold': np.inf}, 'finite'), ({}, 'requires a dimensional')])
def test_ambiguous_or_invalid_exceedance_is_unavailable_and_actionable(linear_strip_solution, settings, reason):
    project, result = linear_strip_solution
    spec = MeasurementSpec('bad', 'potential', aggregation='exceedance', **settings)
    report = evaluate_measurement(project, result, spec)
    assert report['status'] == 'unavailable' and report['value'] is None
    assert not report['within_limits'] and reason in report['reason']
    clone = deepcopy(project)
    clone.power.measurements = [spec]
    assert any(reason in issue.message for issue in validate_power_settings(clone))
