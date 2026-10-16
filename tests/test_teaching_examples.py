# SPDX-License-Identifier: MPL-2.0
"""Independent limits and branch identities for the extended teaching coupons."""
from copy import deepcopy

import numpy as np
import pytest

from etlayout.solvers import branch_current, solve
from etlayout.teaching_examples import build_teaching_example

pytestmark = pytest.mark.numerical


def test_unknown_teaching_identifier_has_no_side_effects():
    from etlayout.examples import LayoutBuilder
    from etlayout.models import Project

    project = Project()
    before = project.to_dict()
    builder = LayoutBuilder("not_an_example")
    assert build_teaching_example("E999", builder, project) is False
    assert project.to_dict() == before and builder.cell.is_empty()


def test_series_coupon_bounds_and_fixed_temperature_current_scaling(expanded_nominal_factory):
    project, result = expanded_nominal_factory("E25")
    sheet = project.materials["copper"].resistivity.value / project.layer("metal").thickness
    lower_limit = sheet * (290. / 20. + 290. / 40.)
    # The 1D value excludes nonnegative step spreading and finite feed losses.
    assert lower_limit < result.metrics["effective_resistance"] < lower_limit * 1.03
    doubled = deepcopy(project)
    next(port for port in doubled.power.ports if port.id == "drain").value *= 2
    # Linearity compares identical spatial discretization. The session fixture
    # can outlive the runner mesh cache; independently remeshing adds unrelated
    # Gmsh variation to a fixed-temperature algebraic scaling identity.
    scaled = solve(doubled, deepcopy(result.mesh))
    assert scaled.converged
    assert scaled.metrics["effective_resistance"] == pytest.approx(result.metrics["effective_resistance"], rel=1e-9)
    assert scaled.metrics["electrical_power"] == pytest.approx(4 * result.metrics["electrical_power"], rel=1e-9)
    assert scaled.metrics["joule_power"] == pytest.approx(4 * result.metrics["joule_power"], rel=1e-9)


def test_dogbone_neck_measurement_matches_conserved_current_per_cross_section(expanded_nominal_factory):
    project, result = expanded_nominal_factory("E26")
    current = result.analysis["ports"]["drain"]["current"]
    expected = current / (16e-6 * project.layer("metal").thickness)
    measured = result.analysis["measurements"]["neck_loading"]
    assert measured["status"] == "available"
    assert measured["support_area_m2"] == pytest.approx(40e-6 * 16e-6)
    assert measured["value"] == pytest.approx(expected, rel=1e-3)


def test_transfer_length_coupon_kelvin_limit_and_finite_contact_loss(expanded_nominal_factory):
    project, result = expanded_nominal_factory("E27")
    expected = project.layer("film").electrical.sheet_resistance.value * 80. / 40.
    assert result.metrics["effective_resistance"] == pytest.approx(expected, rel=1e-7)
    assert abs(result.terminals["sense_a"]["current"]) < 1e-10
    assert abs(result.terminals["sense_b"]["current"]) < 1e-10
    current = result.analysis["ports"]["drain"]["current"]
    full_voltage = result.analysis["ports"]["drain"]["voltage"] - result.analysis["ports"]["source"]["voltage"]
    assert full_voltage / current > expected
    assert result.metrics["contact_power"] > 0
    assert np.sqrt(project.interfaces[0].electrical_value / 10.) == pytest.approx(10e-6)


def test_three_port_splitter_obeys_requested_withdrawal_and_return(expanded_nominal_factory):
    _, result = expanded_nominal_factory("E28")
    ports = result.analysis["ports"]
    assert ports["drain"]["current"] == pytest.approx(.3, abs=1e-10)
    assert ports["branch"]["current"] == pytest.approx(-.1, abs=1e-10)
    assert ports["source"]["current"] == pytest.approx(-.2, abs=1e-10)
    assert sum(port["current"] for port in ports.values()) == pytest.approx(0., abs=1e-10)


def test_annular_bus_preserves_opening_and_symmetric_arm_transport(expanded_nominal_factory):
    from shapely.geometry import Point

    from etlayout.geometry.prepare import prepare_geometry

    project, result = expanded_nominal_factory("E29")
    geometry = prepare_geometry(project)
    assert not geometry.layers["metal"].covers(Point(200e-6, 150e-6))
    assert geometry.layers["substrate"].covers(Point(200e-6, 150e-6))
    # Cross-sectional integration is a mesh approximation; the nominal solver's
    # independent current and power acceptance remains unchanged.
    currents = [branch_current(result, "metal", [200e-6, y0], [200e-6, y1])
                for y0, y1 in ((0., 80e-6), (220e-6, 300e-6))]
    np.testing.assert_allclose(currents, [.5, .5], rtol=1e-3)
    assert sum(currents) == pytest.approx(1., rel=1e-3)
    measured = result.analysis["measurements"]
    assert measured["upper_loss"]["value"] == pytest.approx(measured["lower_loss"]["value"], rel=1e-3)


def test_each_daisy_chain_bank_carries_all_service_without_equal_via_assumption(expanded_nominal_factory):
    _, result = expanded_nominal_factory("E30")
    contacts = list(result.analysis["contacts"].values())
    assert len(contacts) == 36 and all(contact["status"] == "available" for contact in contacts)
    for start in (70e-6, 160e-6, 250e-6, 340e-6):
        bank = [contact for contact in contacts if start < contact["bounds"][0] < start+30e-6]
        assert len(bank) == 9
        assert abs(sum(contact["current_a_to_b"] for contact in bank)) == pytest.approx(.3, rel=1e-8)
        assert np.ptp([contact["absolute_current"] for contact in bank]) > 1e-4


def test_ladder_passive_taps_follow_independent_four_equal_resistor_division(expanded_nominal_factory):
    _, result = expanded_nominal_factory("E31")
    low = result.terminals["source_landing"]["voltage"]
    span = result.terminals["drain_landing"]["voltage"] - low
    normalized = [(result.terminals[f"tap_{index}"]["voltage"] - low) / span for index in range(1, 4)]
    np.testing.assert_allclose(normalized, [.25, .5, .75], atol=1e-4, rtol=0.)
    assert all(abs(result.terminals[f"tap_{index}"]["current"]) < 1e-9 for index in range(1, 4))


def test_parallel_resistor_groups_match_independent_cuts_and_conserve_current(expanded_nominal_factory):
    _, result = expanded_nominal_factory("E32")
    cuts = np.array([branch_current(result, "film", [200e-6, (20+50*index)*1e-6],
                                  [200e-6, (40+50*index)*1e-6]) for index in range(6)])
    groups = result.analysis["devices"]
    group_currents = []
    for index in range(6):
        group = groups[f"resistor_{index+1}"]
        assert group["status"] == "available"
        group_currents.append(group["current"])
        assert group["current_per_width"] == pytest.approx(group["current"] / 20e-6)
    np.testing.assert_allclose(group_currents, cuts, rtol=1e-7, atol=1e-10)
    assert cuts.sum() == pytest.approx(.3, rel=1e-8)
    assert np.all(np.diff(cuts) < 0.) and cuts[0] / cuts[-1] > 1.1
