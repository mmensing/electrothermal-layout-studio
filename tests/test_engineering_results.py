# SPDX-License-Identifier: MPL-2.0
"""Engineering interpretation on actual independently checkable calculations."""
from copy import deepcopy

import numpy as np
import pytest
from solver_helpers import all_region, rectangle

from etlayout.models import RegionSpec, TemperatureLimit, ThermalBoundary
from etlayout.power.engineering import engineering_summary, scoped_engineering
from etlayout.solvers import solve

pytestmark = pytest.mark.numerical


@pytest.fixture
def strip_solution():
    project, mesh = rectangle(nx=7)
    project.solver.mode = "electrothermal"
    project.thermal_boundaries = [ThermalBoundary("sink", "metal", value=1e10)]
    all_region(mesh, "thermal", "sink")
    result = solve(project, mesh)
    assert result.converged
    return project, result


def test_same_partial_support_links_field_heat_and_temperature_integrals(strip_solution):
    _, result = strip_solution
    before = deepcopy(result.configuration)
    report = scoped_engineering(result, "metal", window=RegionSpec(polygons=[dict(bounds=[2e-6, 0., 7e-6, 2e-6])]))
    records = {item["quantity"]: item["measurements"] for item in report["quantities"]}
    assert report["support_area_m2"] == pytest.approx(10e-12, rel=1e-12)
    assert records["potential"]["mean"]["value"] == pytest.approx(.45, rel=1e-12)
    assert records["potential"]["maximum"]["value"] == pytest.approx(.7, rel=1e-12)
    assert records["electric_field"]["mean"]["value"] == pytest.approx(1e5, rel=1e-12)
    assert records["sheet_current"]["mean"]["value"] == pytest.approx(2.5e6, rel=1e-12)
    assert records["metal_current_density"]["mean"]["value"] == pytest.approx(5e12, rel=1e-12)
    assert records["joule_density"]["integral"]["value"] == pytest.approx(2.5, rel=1e-12)
    assert records["joule_density"]["integral"]["unit"] == "W"
    assert records["temperature"]["mean"]["value"] == pytest.approx(325., abs=2e-5)
    assert records["temperature_rise"]["mean"]["value"] == pytest.approx(25., abs=2e-5)
    assert report["crowding"]["raw_peak_status"] == "mesh_unverified"
    assert result.configuration == before


def test_engineering_axes_keep_real_temperature_failure_separate_from_convergence(strip_solution):
    project, _ = strip_solution
    project.temperature_limits = [TemperatureLimit("hard", maximum_temperature=320.)]
    _, mesh = rectangle(nx=7)
    all_region(mesh, "thermal", "sink")
    result = solve(project, mesh)
    assert result.converged
    axes = {row["axis"]: row for row in engineering_summary(result)["axes"]}
    assert axes["Numerical solve"]["status"] == "passed"
    assert axes["Conservation"]["status"] == "passed"
    assert axes["Temperature limits"]["status"] == "failed"
    assert axes["Material/model range"]["status"] == "within_declared_ranges"
    assert axes["Mesh confidence"]["status"] == "not_established"
    assert axes["Calibration"]["status"] == "uncalibrated_seeds"
    assert not axes["Material/model range"]["physical_qualification"]


def test_material_range_warning_is_independent_of_finite_converged_fields(strip_solution):
    project, _ = strip_solution
    project.materials["copper"].thermal_conductivity.maximum_temperature = 310.
    _, mesh = rectangle(nx=7)
    all_region(mesh, "thermal", "sink")
    with pytest.warns(RuntimeWarning, match="validity range"):
        result = solve(project, mesh)
    assert result.converged
    axes = {row["axis"]: row for row in engineering_summary(result)["axes"]}
    assert axes["Numerical solve"]["status"] == "passed"
    assert axes["Material/model range"]["status"] == "outside_declared_range"
    assert axes["Material/model range"]["violations"][0]["maximum_temperature"] > 310.


@pytest.mark.parametrize("identifier,family", [("E37", "GaN HEMT"), ("E39", "SiC on-state interconnect")])
def test_device_dashboard_reuses_actual_nominal_loss_and_group_ledgers(expanded_nominal_factory, identifier, family):
    _, result = expanded_nominal_factory(identifier)
    report = engineering_summary(result)
    assert report["family"] == family
    assert report["loss_budget"] == result.analysis["loss_budget"]
    assert report["devices"] == result.analysis["devices"]
    assert sum(report["loss_budget"]["categories"].values()) == pytest.approx(result.metrics["electrical_power"], rel=1e-10)
    if identifier == "E39":
        assert report["loss_budget"]["categories"]["device_load"] > report["loss_budget"]["categories"]["contact"]
        assert report["metrics"]["total_current"] == pytest.approx(10., rel=1e-9)


def test_scope_does_not_present_missing_thermal_or_electrical_fields_as_zero():
    from etlayout.solvers import solve_electrical
    project, mesh = rectangle()
    result = solve_electrical(project, mesh)
    report = scoped_engineering(result, "metal")
    row = next(item for item in report["quantities"] if item["quantity"] == "heat_density")
    assert row["measurements"]["integral"]["status"] == "unavailable"
    assert row["measurements"]["integral"]["value"] is None
    assert np.isfinite(report["crowding"]["raw_sheet_current_max_A_per_m"])


def test_early_thermal_stop_keeps_numerical_and_requested_duration_distinct(strip_solution):
    from etlayout.models import TransientSettings
    project, _ = strip_solution
    project.temperature_limits = [TemperatureLimit("stop", maximum_temperature=320., evaluation="stop_transient")]
    project.transient = TransientSettings(enabled=True, time_step=1e-10, stop_time=1e-9, output_interval=1e-10)
    _, mesh = rectangle(nx=7)
    all_region(mesh, "thermal", "sink")
    result = solve(project, mesh)
    assert result.converged and not result.transient.completed
    axes = {row["axis"]: row for row in engineering_summary(result)["axes"]}
    assert axes["Numerical solve"]["status"] == "passed"
    assert axes["Temperature limits"]["status"] == "failed"
    assert axes["Requested duration"]["status"] == "stopped_early"
    assert axes["Requested duration"]["actual_stop_s"] < axes["Requested duration"]["requested_stop_s"]
    assert axes["Time confidence"]["status"] == "not_established"
    assert axes["Material/model range"]["status"] == "partially_checked"
    assert any("density/specific-heat" in row["reason"] for row in axes["Material/model range"]["unverified"])


def test_contact_transfer_uses_existing_recorded_state_estimate(tmp_path):
    from etlayout.examples import make_example
    from etlayout.meshing.refinement import contact_transfer_estimate
    from etlayout.simulation.runner import run_project
    project = make_example("E27", tmp_path)
    result = run_project(project)
    report = engineering_summary(result)
    interface = next(item for item in project.interfaces if item.electrical_kind == "specific")
    for layer in (interface.layer_a, interface.layer_b):
        expected, definition = contact_transfer_estimate(project, interface, layer, [result])
        assert report["contact_transfer"][interface.id + "/" + layer] == dict(value_m=expected, definition=definition)
    layer = interface.layer_a
    linked = scoped_engineering(result, layer, region="interface:" + interface.id)
    generated = next(row for row in linked["quantities"] if row["quantity"] == "contact_heat_density")
    expected_power = sum(row["power"] for row in result.analysis["contacts"].values() if row["interface_id"] == interface.id)
    assert generated["measurements"]["integral"]["value"] == pytest.approx(expected_power, rel=1e-10)
    assert linked["crowding"]["nearby_boundaries"] == []  # Geometry intentionally absent, never invented.


def test_nonconverged_scoped_readouts_do_not_bypass_existing_acceptance(strip_solution):
    _, result = strip_solution
    result.converged = False
    report = scoped_engineering(result, "metal")
    for row in report["quantities"]:
        assert all(measured["status"] == "unavailable" for measured in row["measurements"].values())


def test_tiny_partial_support_peak_marker_remains_inside_selection(strip_solution):
    from shapely import from_wkt
    from shapely.geometry import Point
    _, result = strip_solution
    window = RegionSpec(polygons=[dict(bounds=[.02e-6, .01e-6, .04e-6, .02e-6])])
    report = scoped_engineering(result, "metal", window=window)
    support = from_wkt(report["support_wkt"])
    assert isinstance(report["crowding"]["peak_xy"], list)
    assert support.covers(Point(report["crowding"]["peak_xy"]))
    assert report["support_area_m2"] == pytest.approx(2e-16, rel=1e-12)
    assert report["crowding"]["sheet_current_average_A_per_m"] == pytest.approx(2.5e6, rel=1e-12)


def test_bulk_semiconductor_readout_uses_declared_thickness_without_changing_stress_contract(tmp_path):
    from etlayout.examples import make_example
    from etlayout.simulation.runner import run_project
    project = make_example("E9", tmp_path)
    result = run_project(project)
    layer = next(item for item in project.layers if item.electrical.kind == "semiconductor")
    report = scoped_engineering(result, layer.id)
    rows = {item["quantity"]: item["measurements"] for item in report["quantities"]}
    measured = rows["effective_current_density"]["mean"]
    assert measured["status"] == "available" and measured["reporting_only"]
    assert measured["value"] == pytest.approx(rows["sheet_current"]["mean"]["value"] / layer.thickness, rel=1e-12)
    assert "metal_current_density" not in rows


def test_legacy_point_touch_temperature_cannot_claim_property_range_validity():
    from test_power_local_fields import triangles

    from etlayout.solvers import solve_thermal
    project, mesh = triangles()
    project.solver.mode = "thermal"
    result = solve_thermal(project, mesh)
    first = next(row for row in engineering_summary(result)["axes"] if row["axis"] == "Material/model range")
    assert first["status"] == "within_declared_ranges"
    result.element_fields.clear()
    result.fields["metal"]["temperature"] = np.nan_to_num(result.fields["metal"]["temperature"], nan=350.)
    legacy = next(row for row in engineering_summary(result)["axes"] if row["axis"] == "Material/model range")
    assert legacy["status"] == "unavailable"
    assert not legacy["checked_properties"]
    assert any("Ambiguous legacy" in row["reason"] for row in legacy["unverified"])


def test_used_via_material_law_scope_remains_explicitly_partial(expanded_nominal_factory):
    _, result = expanded_nominal_factory("E39")
    axis = next(row for row in engineering_summary(result)["axes"] if row["axis"] == "Material/model range")
    assert axis["status"] == "partially_checked"
    assert {row["property"] for row in axis["checked_properties"]} == {"bulk resistivity", "thermal conductivity"}
    assert any("Via/gap/through-plane" in row["reason"] for row in axis["unverified"])
    assert not axis["physical_qualification"]
