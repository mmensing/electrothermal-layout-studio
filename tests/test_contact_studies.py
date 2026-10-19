# SPDX-License-Identifier: MPL-2.0
"""Finite-contact inversion, real multi-gap solves and portable reference identity."""

from __future__ import annotations

import json
import math
from pathlib import Path

import numpy as np
import pytest

from etlayout.contact_studies import finite_contact_reference, fit_transfer_length, transfer_length_study
from etlayout.examples import make_example
from etlayout.io.project import configuration_hash, load_project, physics_hash
from etlayout.models import SimulationError


@pytest.mark.fast
@pytest.mark.parametrize("sheet,rho,contact_length", [(10.0, 1e-9, 60e-6), (20.0, 8e-9, 5e-6)])
def test_finite_length_fit_recovers_known_parameters_and_residual_covariance(sheet, rho, contact_length):
    gaps, width = np.array([40.0, 80.0, 120.0, 160.0]) * 1e-6, 40e-6
    length = math.sqrt(rho / sheet)
    contact = math.sqrt(sheet * rho) / width / math.tanh(contact_length / length)
    resistance = sheet * gaps / width + 2 * contact
    independent = finite_contact_reference(gaps, sheet_resistance=sheet, rho_c=rho,
                                           width=width, contact_length=contact_length)
    np.testing.assert_allclose(independent["resistance_ohm"], resistance, rtol=1e-13)

    fit = fit_transfer_length(gaps, resistance, width=width, contact_length=contact_length)
    assert fit["Rs_ohm_per_square"] == pytest.approx(sheet, rel=1e-11)
    assert fit["Rc_ohm"] == pytest.approx(contact, rel=1e-11)
    assert fit["rho_c_ohm_m2"] == pytest.approx(rho, rel=1e-10)
    assert fit["LT_m"] == pytest.approx(length, rel=1e-10)
    assert np.linalg.eigvalsh(fit["covariance"]).min() >= -1e-25
    assert fit["residual_degrees_of_freedom"] == 2
    assert "excludes mesh bias" in fit["uncertainty_scope"]


@pytest.mark.fast
@pytest.mark.parametrize(
    "gaps,resistance",
    [
        ([1.0, 1.0, 1.0], [1.0, 2.0, 3.0]),
        ([1.0, 2.0, 3.0], [3.0, 2.0, 1.0]),
        ([1.0, 2.0, np.nan], [1.0, 2.0, 3.0]),
    ],
)
def test_ambiguous_or_nonphysical_extractions_are_rejected(gaps, resistance):
    with pytest.raises(SimulationError):
        fit_transfer_length(gaps, resistance)


@pytest.mark.fast
def test_parameterized_coupon_keeps_exact_default_and_rejects_invalid_geometry(tmp_path):
    project = make_example("E27", tmp_path / "reference")
    explicit = make_example("E27", tmp_path / "explicit", tlm_spacing_um=120.0)
    baseline = json.loads(
        (Path(__file__).resolve().parents[1] / "docs/example-science/nominal-reference.json").read_text(
            encoding="utf-8"
        )
    )
    expected = next(row["physics_hash"] for row in baseline["examples"] if row["id"] == "E27")
    assert configuration_hash(project) == configuration_hash(explicit)
    assert physics_hash(explicit) == expected
    assert project.layout.sha256 == explicit.layout.sha256
    with pytest.raises(ValueError, match="only for the E27"):
        make_example("E1", tmp_path / "bad", tlm_spacing_um=80.0)
    for gap in (0.0, -10.0, np.nan):
        with pytest.raises(ValueError, match="finite and positive"):
            make_example("E27", tmp_path / "bad-gap", tlm_spacing_um=gap)
    occupied = tmp_path / "occupied"
    occupied.mkdir()
    sentinel = occupied / "existing.etl"
    sentinel.write_bytes(b"preserve user project")
    with pytest.raises(SimulationError, match="empty output directory"):
        transfer_length_study(occupied)
    assert sentinel.read_bytes() == b"preserve user project"
    with pytest.raises(SimulationError, match="distinct positive"):
        transfer_length_study(tmp_path / "bad-study", [40.0, 40.0, 80.0])


@pytest.fixture(scope="module")
def solved_tlm(tmp_path_factory):
    return transfer_length_study(tmp_path_factory.mktemp("finite-contact-TLM"))


@pytest.mark.numerical
def test_exact_nominal_multi_gap_conservation_fit_and_deembedding(solved_tlm):
    report = solved_tlm["report"]
    reference, fit = report["reference"], report["fit"]
    # Analytical verification budgets include the finite Cu sheet, while the
    # independent reference assumes ideal metal. These are not solver tolerances.
    assert fit["Rs_ohm_per_square"] == pytest.approx(10.0, rel=0.001)
    assert fit["Rc_ohm"] == pytest.approx(reference["Rc_ohm"], rel=0.005)
    assert fit["rho_c_ohm_m2"] == pytest.approx(1e-9, rel=0.01)
    for case, observation in zip(solved_tlm["cases"], report["observations"]):
        project, result = case["project"], case["result"]
        assert project.solver.mode == "electrical" and not project.transient.enabled
        assert observation["delivered_current_A"] == pytest.approx(0.001, rel=1e-10)
        assert observation["ledger_power_W"] == pytest.approx(observation["terminal_power_W"], rel=1e-9)
        assert result.metrics["current_balance_error"] < project.solver.current_balance_tolerance
        assert result.metrics["power_balance_error"] < project.solver.power_balance_tolerance
        ports = result.analysis["ports"]
        feed_integral = sum(port["feed_power"] for port in ports.values())
        uniform_lower_bound = sum(
            landing["resistance"] * landing["current"] ** 2
            for port in ports.values()
            for landing in port["landings"]
        )
        assert observation["external_feed_loss_W"] == pytest.approx(feed_integral, rel=1e-12)
        # Distributed footprints have nonuniform normal current: Cauchy gives
        # P >= R_total I^2, with strict excess in this finite-metal geometry.
        assert observation["external_feed_loss_W"] > uniform_lower_bound
        assert uniform_lower_bound == pytest.approx(0.004 * 0.001**2, rel=1e-9)
        assert observation["metal_loss_W"] > 0
        assert observation["film_loss_W"] > 0 and observation["interface_only_loss_W"] > 0
        assert observation["contact_interface_only_equivalent_ohm"] < 2 * fit["Rc_ohm"]
        assert abs(observation["analytical_relative_error"]) < 0.001
        assert observation["kelvin_gap_resistance_ohm"] == pytest.approx(
            10 * (observation["gap_m"] * 2 / 3) / 40e-6, rel=1e-5
        )


@pytest.mark.numerical
def test_generated_layout_formats_and_solved_projects_reopen(solved_tlm):
    from etlayout.geometry.layout import LayoutDocument
    from etlayout.routing.geometry import canonical_geometry_id

    for case in solved_tlm["cases"]:
        project = case["project"]
        source = Path(project.layout.path)
        gds = LayoutDocument(source).extract(project.layout.cell)
        oasis = LayoutDocument(source.with_suffix(".oas")).extract(project.layout.cell)
        assert set(gds) == set(oasis)
        assert all(canonical_geometry_id(gds[key]) == canonical_geometry_id(oasis[key]) for key in gds)
        restored, result = load_project(source.with_suffix(".etl"))
        assert physics_hash(restored) == case["result"].physics_hash
        assert result.analysis == case["result"].analysis
        assert restored.power == project.power
