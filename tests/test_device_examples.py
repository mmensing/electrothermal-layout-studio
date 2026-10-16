# SPDX-License-Identifier: MPL-2.0
"""Independent geometric and physical contracts for the new device references."""
from copy import deepcopy

import numpy as np
import pytest

from etlayout.device_examples import metadata
from etlayout.examples import make_example
from etlayout.geometry.prepare import polygon_parts, prepare_geometry
from etlayout.simulation.runner import run_project
from etlayout.visualization.sampling import element_nodal_values

pytestmark = pytest.mark.numerical


def test_suspended_bridge_heat_escapes_through_real_anchors(expanded_nominal_factory):
    project, result = expanded_nominal_factory("E33")
    geometry = prepare_geometry(project)
    anchors = geometry.interfaces["anchor_attachment"]
    cavity = geometry.interfaces["weak_cavity_path"]
    assert anchors.intersection(cavity).area < 1e-24
    assert cavity.bounds == pytest.approx((60e-6, 56e-6, 240e-6, 64e-6))
    assert np.nanmax(result.fields["bridge"]["temperature"]) > np.nanmax(result.fields["substrate"]["temperature"]) + 1.
    assert result.analysis["measurements"]["film_sheet_loading"]["status"] == "available"
    assert len(project.thermal_boundaries) == 1 and not project.heat_sources
    assert result.metrics["temperature_min"] >= project.thermal_boundaries[0].ambient


def test_heater_sensor_isolation_and_finite_readout_bias(expanded_nominal_factory):
    project, result = expanded_nominal_factory("E34")
    assert project.layer("sensor").z > project.layer("heater").z + project.layer("heater").thickness
    assert result.terminals["sensor_bias"]["current"] == pytest.approx(1e-5, rel=1e-8)
    assert result.terminals["sensor_return"]["current"] == pytest.approx(-1e-5, rel=1e-8)
    assert result.terminals["sensor_probe"]["current"] == 0.
    assert result.analysis["measurements"]["sensor_mean_temperature"]["value"] > 300.
    assert project.materials["platinum"].resistivity.alpha == .00392
    assert all(interface.electrical_kind == "none" for interface in project.interfaces)


def test_die_attach_support_is_partial_and_preserves_hot_side(expanded_nominal_factory):
    project, result = expanded_nominal_factory("E35")
    geometry = prepare_geometry(project)
    attached = geometry.thermal_boundaries["backside"]
    assert attached.area == pytest.approx(metadata(project)["attached_area_m2"], rel=1e-12)
    assert attached.bounds[2] == pytest.approx(420e-6)
    assert result.metrics["hotspot"]["x"] > attached.bounds[2]
    assert not project.heat_sources and len(project.thermal_boundaries) == 1


def test_neck_pulse_delivers_service_and_cools_without_invented_heat(expanded_nominal_factory):
    project, result = expanded_nominal_factory("E36")
    assert result.transient.completed and not project.heat_sources
    delivered = []
    for frame in result.transient.snapshots:
        current = frame.analysis["ports"]["drain"]["current"]
        prescribed = frame.metrics["applied_drives"]["power.ports.drain.value"]
        assert current == pytest.approx(prescribed, abs=1e-9)
        delivered.append(current)
    assert max(delivered) == pytest.approx(1., rel=1e-8)
    assert delivered[-1] == pytest.approx(0., abs=1e-12)
    assert 300 < result.metrics["temperature_max"] < result.metrics["transient_peak_temperature"] < 450.
    assert result.metrics["transient_energy_balance_error"] < 1e-9
    assert result.metrics["transient_bound_violation_steps"] == 0


@pytest.mark.parametrize("identifier", ["E37", "E38"])
def test_short_gates_cut_every_conducting_path_and_width_is_not_double_counted(identifier, tmp_path):
    project = make_example(identifier, tmp_path)
    geometry = prepare_geometry(project)
    info = metadata(project)
    total_width = 0.
    for channel in [layer for layer in project.layers if layer.id.startswith("channel")]:
        suffix = channel.id.removeprefix("channel_")
        gate = geometry.terminals["gate_" + suffix]
        support = geometry.layers[channel.id]
        assert gate.area / info["gate_length_m"] > 0
        total_width += gate.area / info["gate_length_m"]
        count = len(list(polygon_parts(support)))
        assert len(list(polygon_parts(support.difference(gate)))) == 2 * count
        for part in polygon_parts(support.difference(gate)):
            assert not (part.intersects(geometry.interfaces["source_contact_" + suffix]) and
                        part.intersects(geometry.interfaces["drain_contact_" + suffix]))
        assert channel.electrical.gate_regions == ["gate_" + suffix]
        assert geometry.layers["source"].intersection(geometry.layers["drain_" + suffix]).area < 1e-24
    assert total_width == pytest.approx(info["total_gate_width_m"], rel=1e-12)
    assert info["actual_gate_fingers"] == sum(len(list(polygon_parts(geometry.layers[layer.id])))
        for layer in project.layers if layer.id.startswith("channel"))


def test_single_gate_changes_current_at_fixed_voltage_without_edge_bypass(tmp_path):
    project = make_example("E37", tmp_path)
    project.solver.mode = "electrical"
    drain = next(port for port in project.power.ports if port.id == "drain_a")
    drain.kind, drain.value = "voltage", .1
    on = run_project(project)
    closed = deepcopy(project)
    next(terminal for terminal in closed.terminals if terminal.id == "gate_a").value = -3.3
    off = run_project(closed)
    assert on.converged and off.converged
    on_current = on.analysis["ports"]["drain_a"]["current"]
    off_current = off.analysis["ports"]["drain_a"]["current"]
    assert on_current > .01
    model = closed.layer("channel_a").electrical
    # Independent upper bound: even if the full 0.1 V dropped across the
    # 1 µm gate, its maximum charge-control conductance cannot carry more.
    # Access and finite contacts only lower this value. This tests the complete
    # gate cut without entering the floor-dominated, ill-conditioned deep off state.
    density_bound = model.gate_coupling * model.smoothing_voltage * np.logaddexp(
        0., (-3.3 - model.threshold_voltage) / model.smoothing_voltage)
    conductance_bound = model.minimum_sheet_conductance + 1.602176634e-19 * model.mobility * density_bound
    current_bound = conductance_bound * metadata(closed)["total_gate_width_m"] / 1e-6 * .1
    assert 0 < off_current <= current_bound
    assert model.minimum_sheet_conductance == 1e-10


def test_dual_bank_current_is_measured_locally_not_assigned_per_finger(expanded_nominal_factory):
    project, result = expanded_nominal_factory("E38")
    groups = result.analysis["devices"]
    for suffix in ("a", "b"):
        leaves = [value for key, value in groups.items() if key.startswith("bank_" + suffix + "_finger_")]
        assert len(leaves) == 8 and all(value["status"] == "available" for value in leaves)
        measured = sum(value["current"] for value in leaves)
        assert measured == pytest.approx(.25, rel=1e-7)
        assert groups["bank_" + suffix]["current"] == pytest.approx(measured, rel=1e-10)
        assert groups["bank_" + suffix]["active_width"] == pytest.approx(.002)
        assert result.analysis["measurements"]["field_" + suffix]["within_limits"]
    assert next(item for item in project.terminals if item.id == "gate_a").geometry != next(
        item for item in project.terminals if item.id == "gate_b").geometry


def test_sic_island_area_normalization_and_coupled_load_law(expanded_nominal_factory):
    project, result = expanded_nominal_factory("E39")
    geometry = prepare_geometry(project)
    info = metadata(project)
    active = geometry.interfaces["on_state"]
    assert len(list(polygon_parts(active))) == 4
    assert active.area == pytest.approx(4 * (320e-6)**2, rel=1e-12)
    assert active.area == pytest.approx(info["active_area_m2"], rel=1e-12)
    assert geometry.layers["gate"].intersection(active).area == 0.
    assert all(layer.electrical.kind != "hemt" for layer in project.layers)
    assert result.terminals["kelvin"]["current"] == 0.
    assert all(group["status"] == "available" for group in result.analysis["devices"].values())
    assert sum(group["current"] for group in result.analysis["devices"].values()) == pytest.approx(10., rel=1e-8)
    interface = next(item for item in project.interfaces if item.id == "on_state")
    ids = result.mesh.regions["interface:on_state"]
    local_temperature = (element_nodal_values(result, "source", "temperature")[ids].mean(axis=1)
                         + element_nodal_values(result, "drain", "temperature")[ids].mean(axis=1)) / 2
    expected_rsp = interface.electrical_value * (1 + interface.temperature_coefficient * (local_temperature - 300.))
    fields = result.interface_fields["on_state"]
    np.testing.assert_allclose(fields["j_normal"][ids], fields["voltage_drop"][ids] / expected_rsp[:, None], rtol=2e-6)
    assert result.metrics["effective_resistance"] > info["analytical_uniform_device_resistance_ohm"]


def test_interposer_branch_transfers_and_protected_opening(expanded_nominal_factory):
    project, result = expanded_nominal_factory("E40")
    geometry = prepare_geometry(project)
    from etlayout.geometry.prepare import resolve_region
    opening = resolve_region(project.power.rules.keepouts[0], {}, clip=False)
    for key in ("m1", "m2", "m3"):
        assert geometry.layers[key].intersection(opening).area == 0.
    assert len(list(polygon_parts(geometry.interfaces["branch_vias"]))) == 32
    assert len(list(polygon_parts(geometry.interfaces["output_vias"]))) == 16
    currents = [result.analysis["measurements"][key + "_branch_current"]["value"] for key in ("lower", "upper")]
    assert all(current > 0 for current in currents)
    assert sum(currents) == pytest.approx(5., rel=1e-8)
    # Symmetry is physical, not a constraint distributing prescribed branch loads.
    assert currents[0] == pytest.approx(currents[1], rel=.01)
