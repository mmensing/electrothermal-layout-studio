# SPDX-License-Identifier: MPL-2.0
"""Distinct example contracts; compact exact nominal solves are reused immutably."""
from copy import deepcopy
from pathlib import Path

import numpy as np
import pytest
from shapely import union_all

from etlayout.examples import EXAMPLE_BUILDERS, EXAMPLE_NAMES, ROUTINE_EXAMPLES, make_example
from etlayout.examples import rectangle as layout_rectangle
from etlayout.geometry.layout import LayoutDocument
from etlayout.geometry.prepare import polygon_parts, prepare_geometry, resolve_region
from etlayout.io.project import file_hash, load_project, physics_hash, save_project
from etlayout.models import MeasurementSpec, RefinementRegion, SimulationError, SupportSelector
from etlayout.optimization import _refined_project
from etlayout.power.analysis import evaluate_measurement, selected_support
from etlayout.power_examples import FAMILIES, metadata, settings
from etlayout.routing import export_candidate, generate_candidate, validate_rules
from etlayout.routing.geometry import canonical_geometry_id
from etlayout.simulation.runner import run_project
from etlayout.solvers import solve_electrical

pytestmark = pytest.mark.numerical


@pytest.fixture(scope="module")
def nominal_solutions(tmp_path_factory):
    solutions = {}
    for name in FAMILIES:
        project = make_example(name, tmp_path_factory.mktemp(name), preset="compact")
        solutions[name] = project, run_project(project)
    return solutions


@pytest.fixture(params=list(FAMILIES))
def nominal(request, nominal_solutions):
    return nominal_solutions[request.param]


def test_explicit_registry_keeps_small_benchmarks_out_of_engineering_three_mode_runs():
    assert set(EXAMPLE_BUILDERS) == set(EXAMPLE_NAMES)
    assert len(ROUTINE_EXAMPLES) == 19
    assert not set(FAMILIES) & set(ROUTINE_EXAMPLES)
    assert EXAMPLE_BUILDERS["E19_pulsed_multilayer"][0] == "etlayout.pulsed_examples"
    assert EXAMPLE_BUILDERS["E21_long_finger_gan"][0] == "etlayout.power_examples"


def test_gate_count_and_conducting_width_are_independent_generator_inputs(tmp_path):
    project = make_example("e21_long_finger_gan", tmp_path, preset="compact", fingers=6, gate_width_um=100.)
    dimensions = metadata(project)
    assert dimensions["actual_gate_fingers"] == 6
    assert dimensions["total_gate_width_m"] == pytest.approx(600e-6)
    assert dimensions["gate_length_m"] == 1e-6 and dimensions["requested_current_A"] == .075


def test_exact_configured_nominal_preserves_service_limits_and_conservation(nominal):
    project, result = nominal
    config = metadata(project)
    assert result.converged, result.diagnostics
    assert result.analysis["ports"]["drain"]["current"] == pytest.approx(config["requested_current_A"], rel=1e-8)
    assert result.metrics["current_balance_error"] < project.solver.current_balance_tolerance
    assert result.metrics["power_balance_error"] < project.solver.power_balance_tolerance
    assert result.metrics["thermal_balance_error"] < project.solver.thermal_balance_tolerance
    assert result.metrics["temperature_max"] <= config["model_temperature_range_K"][1]
    assert not project.heat_sources
    assert len(project.thermal_boundaries) == 1 and project.thermal_boundaries[0].id == "backside"
    assert all(row["status"] == "available" and row["within_limits"] for row in result.analysis["measurements"].values())
    ledger = result.analysis["loss_budget"]
    assert sum(ledger["categories"].values()) == pytest.approx(result.metrics["electrical_power"], rel=1e-8)
    assert ledger["thermal_deposition"] + ledger["excluded_power"] == pytest.approx(ledger["total_power"], rel=1e-10)
    groups = result.analysis["devices"]
    assert all(row["status"] == "available" for row in groups.values()), groups
    leaves = [row for name, row in groups.items() if "finger" in name or name.startswith("macrocell")]
    if leaves:
        assert sum(row["current"] for row in leaves) == pytest.approx(config["requested_current_A"], rel=1e-8)


def test_deterministic_layout_roundtrip_and_portable_embedded_reopen(nominal, tmp_path):
    project, _ = nominal
    config = metadata(project)
    regenerated = make_example(project.name, tmp_path / "copy", preset=config["preset"], variant=config["variant"])
    assert regenerated.layout.sha256 == project.layout.sha256
    first = LayoutDocument(project.layout.path).extract(project.layout.cell)
    oasis = LayoutDocument(Path(project.layout.path).with_suffix(".oas")).extract(project.layout.cell)
    assert first.keys() == oasis.keys()
    assert all(canonical_geometry_id(first[key]) == canonical_geometry_id(oasis[key]) for key in first)
    saved = tmp_path / "embedded.etl"
    save_project(regenerated, saved)
    Path(regenerated.layout.path).rename(tmp_path / "temporarily-unavailable.gds")
    reopened, result = load_project(saved)
    assert result is None
    assert Path(reopened.layout.path).is_file()
    assert file_hash(reopened.layout.path) == project.layout.sha256
    assert physics_hash(reopened) == physics_hash(project)
    assert reopened.power.devices == project.power.devices


def test_dimensions_active_support_gate_paths_and_protected_openings(nominal):
    project, result = nominal
    config, geometry = metadata(project), prepare_geometry(project)
    assert not [item for item in geometry.diagnostics if item.severity == "error"]
    assert not [item for item in geometry.diagnostics if "clipped to their common area" in item.message]
    assert not validate_rules(project, geometry)
    width, height = config["die_um"]
    assert geometry.domain.bounds == pytest.approx((0., 0., width * 1e-6, height * 1e-6), abs=1e-15)
    if config["family"].startswith("gan"):
        gates = geometry.terminals["gate_control"]
        channel = geometry.layers["channel"]
        assert len(list(polygon_parts(channel))) == config["actual_gate_fingers"]
        assert gates.area / config["gate_length_m"] == pytest.approx(config["total_gate_width_m"], rel=1e-10)
        ungated = channel.difference(gates)
        assert len(list(polygon_parts(ungated))) == 2 * config["actual_gate_fingers"]
        assert not any(part.intersects(geometry.interfaces["source_contact"]) and
                       part.intersects(geometry.interfaces["drain_contact"]) for part in polygon_parts(ungated))
        assert result.mesh.statistics["directional_partitions"] > 0
        assert project.layer("gate").role == "thermal"
        masks = LayoutDocument(project.layout.path).extract(project.layout.cell)
        assert project.layer("source").z == project.layer("drain").z
        assert masks["2/0"].intersection(masks["3/0"]).area < 1e-24
        assert len(list(polygon_parts(masks["4/0"]))) == 1
        assert masks["40/0"].difference(masks["4/0"]).area < 1e-24
        assert config["gate_pad_size_um"] == [120., 100.]
    elif config["family"].startswith("sic"):
        active = geometry.interfaces["on_state"]
        assert active.area == pytest.approx(config["active_area_m2"], rel=1e-10)
        assert len(list(polygon_parts(active))) == config["macrocell_count"]
        assert project.layer("gate").role == "thermal"
        assert all(layer.electrical.kind != "hemt" for layer in project.layers)
        interface = next(item for item in project.interfaces if item.id == "on_state")
        assert interface.electrical_value == 1e-7  # 1 mΩ cm² in Ω m²
        assert interface.temperature_coefficient == .003
        probe = next(item for item in project.terminals if item.id == "kelvin_sense")
        assert probe.kind == "probe" and result.terminals[probe.id]["current"] == 0.
        assert all(landing.terminal_id != probe.id for port in project.power.ports for landing in port.landings)
        masks = LayoutDocument(project.layout.path).extract(project.layout.cell)
        assert len(list(polygon_parts(masks["4/0"]))) == 1
        assert masks["4/0"].intersection(masks["90/0"]).area < 1e-24
        collar = masks["99/0"]
        assert len(list(polygon_parts(collar))) == 1 and len(collar.interiors) == 1
        assert collar.area == pytest.approx((width * height - .88**2 * width * height) * 1e-12, rel=1e-10)
        assert collar.intersection(active).area < 1e-24
    else:
        opening = resolve_region(project.power.rules.keepouts[0], {}, clip=False)
        assert union_all([geometry.layers[key] for key in ("m1", "m2", "m3")]).intersection(opening).area == 0.


def test_empty_contact_layers_remain_integer_indices_in_mixed_thermal_projects(nominal):
    project, result = nominal
    via = next(interface for interface in project.interfaces if interface.electrical_kind == "via")
    selected, _ = selected_support(project, result, SupportSelector(contacts=[via.id]))
    assert all(np.issubdtype(ids.dtype, np.integer) for ids in selected.values())
    record = evaluate_measurement(project, result, MeasurementSpec("via_current", "contact_normal_density",
        SupportSelector(contacts=[via.id]), "integral", vector_component="signed"))
    assert record["status"] == "available"


def test_thermal_gap_paths_are_disjoint_and_do_not_duplicate_physical_links(nominal):
    project, _ = nominal
    geometry = prepare_geometry(project)
    for upper in project.layers:
        gaps = [interface for interface in project.interfaces
                if interface.id.startswith("heat_") and interface.layer_a == upper.id]
        supports = [geometry.interfaces[interface.id] for interface in gaps]
        for index, support in enumerate(supports):
            assert all(support.intersection(other).area < 1e-24 for other in supports[index + 1:])
        for gap in gaps:
            for physical in project.interfaces:
                if (not physical.id.startswith("heat_") and physical.thermal_kind != "none" and
                        {gap.layer_a, gap.layer_b} == {physical.layer_a, physical.layer_b}):
                    assert geometry.interfaces[gap.id].intersection(geometry.interfaces[physical.id]).area < 1e-24
            lower = project.layer(gap.layer_b)
            common = geometry.layers[gap.layer_a].intersection(geometry.layers[gap.layer_b])
            assert geometry.interfaces[gap.id].difference(common).area < 1e-24
            assert gap.geometry.operation == "intersection"
            distance = upper.z - lower.z - lower.thickness
            if gap.material_id:
                assert gap.material_id == "sio2" and gap.height == pytest.approx(distance, abs=1e-15)
    assert len(project.thermal_boundaries) == 1


@pytest.mark.parametrize("name,variants", [("E21", ("single", "dual", "distributed")),
    ("E24", ("bonds", "clip", "bumps")), ("E20", ("uniform_single", "graded_single", "poor_single"))])
def test_equal_service_variants_preserve_active_geometry_and_reimport(name, variants, tmp_path):
    protected = []
    groups = []
    for variant in variants:
        project = make_example(name, tmp_path / variant, preset="compact", variant=variant)
        geometry = prepare_geometry(project)
        assert not validate_rules(project, geometry)
        masks = LayoutDocument(project.layout.path).extract(project.layout.cell)
        protected.append({key: canonical_geometry_id(masks[key]) for key in project.power.rules.protected_masks})
        groups.append(project.power.devices)
        exported = export_candidate(project, tmp_path / variant / "exported.oas")
        assert exported.valid, exported.violations
        reimported = LayoutDocument(exported.project.layout.path).extract(exported.project.layout.cell)
        assert all(canonical_geometry_id(value) == canonical_geometry_id(reimported[key]) for key, value in masks.items())
        assert next(port for port in project.power.ports if port.id == "drain").value == metadata(project)["requested_current_A"]
    assert all(row == protected[0] for row in protected)
    assert all(row == groups[0] for row in groups)


def test_fully_gated_compact_hemt_controls_all_paths_and_floor_is_not_leakage(tmp_path):
    project = make_example("E21", tmp_path, preset="compact")
    project.solver.mode = "electrical"
    drain = next(port for port in project.power.ports if port.id == "drain")
    drain.kind, drain.value = "voltage", .1
    on = run_project(project)
    next(terminal for terminal in project.terminals if terminal.id == "gate_control").value = -3.
    off = run_project(project)
    assert on.converged and off.converged
    assert on.analysis["ports"]["drain"]["current"] > 20 * off.analysis["ports"]["drain"]["current"]
    assert "not leakage" in metadata(project)["model"]
    # Deep numerical-floor operation need not be accepted by conservation at
    # the extreme metal/channel conductance ratio. Never call that leakage.
    next(terminal for terminal in project.terminals if terminal.id == "gate_control").value = -5.
    floor = run_project(project)
    if floor.converged:
        bound = project.layer("channel").electrical.minimum_sheet_conductance * metadata(project)["total_gate_width_m"] / 1e-6 * .1
        assert floor.analysis["ports"]["drain"]["current"] <= bound * 1.001
    else:
        assert all(record["status"] == "unavailable" for record in floor.analysis["measurements"].values())


@pytest.mark.parametrize("name", ["E20_high_current_rdl", "E23_sic_stripes", "E24_sic_cells"])
def test_linear_metal_current_scaling_and_conservative_sic_area_loading(name, nominal_solutions):
    project, result = nominal_solutions[name]
    family = metadata(project)["family"]
    if family == "rdl":
        clone = deepcopy(project)
        clone.solver.mode = "electrical"
        one = solve_electrical(clone, result.mesh)
        next(port for port in clone.power.ports if port.id == "drain").value *= 2
        two = solve_electrical(clone, result.mesh)
        assert one.converged and two.converged
        assert two.metrics["effective_resistance"] == pytest.approx(one.metrics["effective_resistance"], rel=1e-8)
        assert two.metrics["joule_power"] == pytest.approx(4 * one.metrics["joule_power"], rel=1e-8)
    elif family.startswith("sic"):
        config = metadata(project)
        current = result.analysis["ports"]["drain"]["current"]
        # Cauchy-Schwarz bound: nonuniform local currents cannot dissipate less
        # than the uniform area-scaled intrinsic load at its 300 K resistance.
        minimum = current**2 * config["on_state_specific_resistance_ohm_m2"] / config["active_area_m2"]
        assert result.analysis["loss_budget"]["categories"]["device_load"] >= minimum
        currents = [row["current"] for row in result.analysis["devices"].values()]
        assert np.ptp(currents) > abs(current) * 1e-6  # sharing is solved, not prescribed equal


def test_asymmetric_bank_feed_changes_solved_sharing_at_equal_service(tmp_path):
    original = make_example("E22", tmp_path / "single", preset="compact", variant="single")
    symmetric = run_project(original)
    asymmetric = make_example("E22", tmp_path / "asymmetric", preset="compact", variant="asymmetric")
    assert original.layout.sha256 == asymmetric.layout.sha256  # only feed placement changes
    assert original.thermal_boundaries == asymmetric.thermal_boundaries
    assert asymmetric.power.devices == original.power.devices
    assert asymmetric.layer("channel").electrical == original.layer("channel").electrical
    solved = run_project(asymmetric)
    assert solved.converged
    assert solved.analysis["ports"]["drain"]["current"] == pytest.approx(symmetric.analysis["ports"]["drain"]["current"], rel=1e-8)
    keys = [f"bank_{index}" for index in range(1, 5)]
    before = np.asarray([symmetric.analysis["devices"][key]["current"] for key in keys])
    after = np.asarray([solved.analysis["devices"][key]["current"] for key in keys])
    assert abs(after.sum() - before.sum()) < 1e-8 * abs(before.sum())
    assert np.max(np.abs(after - before)) > abs(before.sum()) * 1e-5


def test_report_local_support_memo_preserves_exact_partial_group_values(nominal_solutions):
    project, result = nominal_solutions["E21_long_finger_gan"]
    selector = SupportSelector(devices=["bank_1"], geometry=layout_rectangle(254.5, 355., 254.9, 383.))
    cached = {}
    specification = MeasurementSpec("partial", "potential", selector, "mean")
    direct = evaluate_measurement(project, result, specification)
    first = evaluate_measurement(project, result, specification, _support_cache=cached)
    again = evaluate_measurement(project, result, specification, _support_cache=cached)
    assert direct["status"] == first["status"] == again["status"] == "available"
    assert first["value"] == pytest.approx(direct["value"], rel=1e-12)
    assert again["support_area_m2"] == pytest.approx(direct["support_area_m2"], rel=1e-12)


@pytest.mark.parametrize("name,variant", [("E20_high_current_rdl", variant) for variant in
    ("uniform_single", "graded_single", "uniform_distributed", "graded_distributed", "poor_single")] +
    [("E21_long_finger_gan", variant) for variant in ("single", "dual", "distributed")] +
    [("E22_multibank_gan", variant) for variant in ("single", "dual", "distributed", "asymmetric")] +
    [("E23_sic_stripes", variant) for variant in ("narrow", "wide", "cross")] +
    [("E24_sic_cells", variant) for variant in ("bonds", "clip", "bumps")])
def test_bounded_recipe_replay_keeps_active_geometry_and_stress_has_no_ranking(name, variant, tmp_path):
    project = make_example(name, tmp_path / "input", preset="compact", variant=variant)
    experiment = project.power.experiments[0]
    variable = experiment.variables[0]
    replayed = generate_candidate(project, {variable.id: variable.values[0]}, tmp_path / "nominal.gds", experiment=experiment)
    assert replayed.valid, replayed.violations
    original_masks = LayoutDocument(project.layout.path).extract(project.layout.cell)
    nominal_masks = LayoutDocument(replayed.project.layout.path).extract(replayed.project.layout.cell)
    assert original_masks.keys() == nominal_masks.keys()
    assert all(canonical_geometry_id(value) == canonical_geometry_id(nominal_masks[key]) for key, value in original_masks.items())
    generated = generate_candidate(project, {variable.id: variable.values[-1]}, tmp_path / "candidate.gds", experiment=experiment)
    assert generated.valid, generated.violations
    assert generated.project.power.devices == project.power.devices
    assert len(experiment.variables) == 1 and experiment.max_evaluations == 2
    if name == "E22_multibank_gan":
        stress = make_example(name, tmp_path / "stress", preset="stress")
        assert metadata(stress)["stress"] and not stress.power.experiments


def test_directional_refinement_scales_and_does_not_ignore_scalar_transfer_checks(tmp_path):
    from etlayout.meshing.generate import directional_faces
    project = make_example("E21", tmp_path, preset="compact")
    refined, _ = _refined_project(project, .5)
    assert refined.mesh.refinements[0].axis_sizes == pytest.approx([.125e-6, 10e-6])
    geometry = prepare_geometry(project)
    region = geometry.refinements[project.mesh.refinements[0].id]
    faces = list(polygon_parts(region))
    selected, intervals = directional_faces(project, geometry, faces)
    ordinary = intervals(*faces[0].bounds[1::2], 1)
    project.mesh.refinements.append(RefinementRegion("extra", deepcopy(project.mesh.refinements[0].geometry), .25e-6))
    geometry = prepare_geometry(project)
    _, smaller = directional_faces(project, geometry, faces)
    assert selected and smaller(*faces[0].bounds[1::2], 1) > ordinary
    project.mesh.refinements[0].axis_sizes = [float("nan"), 1.]
    with pytest.raises(SimulationError, match="axis_sizes"):
        directional_faces(project, geometry, faces)


@pytest.mark.parametrize("options", [{"current": -1}, {"variant": "unsupported"}, {"preset": "missing"}])
def test_bad_requests_are_rejected_explicitly_without_substitution(options):
    with pytest.raises(ValueError):
        settings("E21_long_finger_gan", **options)


@pytest.mark.parametrize("length_scale", [1., 2.])
def test_sic_specific_resistance_uniform_limit_and_symmetric_area_sharing(length_scale):
    from solver_helpers import all_region, rectangle

    from etlayout.models import Interface, RegionSpec, Terminal
    project, mesh = rectangle(nx=4, ny=2, layers=("a", "b"))
    mesh.points *= length_scale
    project.terminals = [Terminal("uniform_source", "a", kind="ground"),
                         Terminal("uniform_drain", "b", kind="current", value=1e-6)]
    all_region(mesh, "terminal", "uniform_source")
    all_region(mesh, "terminal", "uniform_drain")
    all_region(mesh, "interface", "on_state")
    project.interfaces = [Interface("on_state", "b", "a", electrical_kind="load", electrical_value=1e-7,
                                   purpose="device_load", temperature_coefficient=.003)]
    result = solve_electrical(project, mesh)
    assert result.converged
    area = mesh.areas.sum()
    assert result.metrics["effective_resistance"] == pytest.approx(1e-7 / area, rel=1e-9)
    x0, y0 = mesh.points.min(axis=0)
    x1, y1 = mesh.points.max(axis=0)
    midpoint = (x0 + x1) / 2
    for low, high in ((x0, midpoint), (midpoint, x1)):
        selector = SupportSelector(contacts=["on_state"], geometry=RegionSpec(polygons=[{"bounds": [low, y0, high, y1]}]))
        record = evaluate_measurement(project, result, MeasurementSpec("half", "contact_normal_density", selector,
                                                                     "integral", vector_component="signed"))
        assert record["status"] == "available"
        assert record["value"] == pytest.approx(.5e-6, rel=1e-9)


def test_optional_port_pulse_generation_uses_unshadowed_drive_and_rejects_landing_override(tmp_path, nominal_solutions):
    from etlayout.gui.transient import drive_targets
    from etlayout.models import TransientDrive
    from etlayout.solvers.transient import estimate_transient_resources, validate_transient
    project = make_example("E23", tmp_path, preset="compact", pulse=True)
    assert project.transient.enabled and not project.power.experiments
    assert project.sweeps[0].parameter == "transient.drives.0.waveform.high"
    validate_transient(project)
    estimate = estimate_transient_resources(project, nominal_solutions["E23_sic_stripes"][1].mesh)
    assert estimate["output_snapshots"] == 13 and estimate["fits_history_budget"]
    assert project.transient.time_step == .0001 and project.transient.drives[0].waveform.high == .25
    choices = dict(drive_targets(project))
    assert choices["power.ports.drain.value"] == "A"
    assert not any("landing" in key for key in choices)
    project.transient.drives = [TransientDrive("terminals.drain_landing_1.value")]
    with pytest.raises(SimulationError, match="physical-port landing"):
        validate_transient(project)


def test_power_port_pulse_uses_actual_delivered_current_and_thermal_storage():
    from solver_helpers import all_region, rectangle

    from etlayout.models import PhysicalPort, PortLanding, ThermalBoundary, TransientDrive, Waveform
    from etlayout.solvers import solve_transient
    project, mesh = rectangle(nx=4, ny=2)
    project.solver.mode = "electrothermal"
    project.power.ports = [PhysicalPort("drain", [PortLanding("right", .01, .25)], "current", 1e-3)]
    project.thermal_boundaries = [ThermalBoundary("backside", "metal", value=1e6)]
    all_region(mesh, "thermal", "backside")
    project.transient.enabled = True
    project.transient.stop_time, project.transient.time_step, project.transient.output_interval = .0003, .00005, .00005
    project.transient.drives = [TransientDrive("power.ports.drain.value", Waveform(kind="pulse", high=1e-3, low=0.,
        width=.0001, period=.0002))]
    result = solve_transient(project, mesh)
    assert result.converged
    assert result.transient.snapshots
    positive = [snapshot for snapshot in result.transient.snapshots if snapshot.analysis["ports"]["drain"]["current"] > 1e-8]
    assert positive
    assert all(snapshot.analysis["ports"]["drain"]["current"] == pytest.approx(1e-3, rel=1e-8) for snapshot in positive)
    assert result.metrics["temperature_max"] > 300.
