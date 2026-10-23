# SPDX-License-Identifier: MPL-2.0
"""Independent energy/geometry and negative checks for C/L interchange only.

No fixture is a field-solver result or an extracted ETLayout project.
"""
import json
from dataclasses import replace
from hashlib import sha256

import numpy as np
import pytest
from scipy.constants import epsilon_0, mu_0

from etlayout.power.parasitic_models import (
    ClosedLoop,
    FieldMaterial,
    FieldProvenance,
    ParasiticMatrix,
    PotentialBoundary,
)


def provenance(approximation):
    return FieldProvenance(
        *(sha256(value.encode()).hexdigest() for value in (
            "analytical fixture geometry", "analytical fixture: no mesh", approximation)),
        solver_name="independent analytical benchmark", solver_version="1", temperature_K=300,
        approximation=approximation)


def capacitance():
    # Infinite-plate limit, not a finite-layout capacitance prediction.
    area, gap, epsilon = 2e-6, 10e-6, 3.9 * epsilon_0
    return ParasiticMatrix("capacitance", ((epsilon * area / gap,),),
        (FieldMaterial("dielectric", ("gap_volume",), epsilon, "F/m"),),
        provenance("uniform parallel plates; fringing excluded"),
        boundaries=(PotentialBoundary("plate", ("upper_plate",)),),
        reference=PotentialBoundary("reference", ("lower_plate",)))


def loop(identifier="loop", offset=0):
    # Oriented circuit contour through coax inner/outer paths and end closures;
    # conductor volumes and the surface-current distribution are separate data.
    a, b, length = .25e-3, 1e-3, .02
    return ClosedLoop(identifier, ((a + offset, 0, 0), (a + offset, 0, length),
        (b + offset, 0, length), (b + offset, 0, 0), (a + offset, 0, 0)),
        (identifier + "/inner",), (identifier + "/outer",))


def inductance():
    value = mu_0 * .02 / (2 * np.pi) * np.log(4)
    return ParasiticMatrix("loop_inductance", ((value,),),
        (FieldMaterial("vacuum", ("coax_gap",), mu_0, "H/m"),),
        provenance("long coax; shell currents; external inductance only; end fields excluded"),
        loops=(loop(),))


def test_parallel_plate_energy_matches_independent_field_volume_and_reference():
    model = capacitance()
    voltage = 7.0
    field_energy = .5 * (3.9 * epsilon_0) * (voltage / 10e-6) ** 2 * (2e-6 * 10e-6)
    assert model.energy_J([voltage]) == pytest.approx(field_energy, rel=1e-14)
    assert model.energy_J([2 * voltage]) == pytest.approx(4 * field_energy, rel=1e-14)
    assert model.energy_J([0]) == 0
    # Potentials are relative to the explicit reference, not an implicit ground.
    assert model.energy_J([107 - 100]) == model.energy_J([voltage])
    assert model.axis_ids == ("plate",) and model.unit == "F"


def test_maxwell_mutual_sign_and_energy_match_three_independent_capacitor_branches():
    first_to_ref, second_to_ref, coupling = 2e-12, 3e-12, 4e-12
    base = capacitance()
    model = replace(base, matrix_SI=((first_to_ref + coupling, -coupling),
        (-coupling, second_to_ref + coupling)), boundaries=(
            PotentialBoundary("first", ("first_plate",)), PotentialBoundary("second", ("second_plate",))))
    state = [3., -2.]
    expected = .5 * (first_to_ref * state[0] ** 2 + second_to_ref * state[1] ** 2
        + coupling * (state[0] - state[1]) ** 2)
    assert model.energy_J(state) == pytest.approx(expected, rel=1e-14)


def test_coax_loop_inductance_energy_matches_independent_magnetic_volume_integral():
    model = inductance()
    a, b, length, current = .25e-3, 1e-3, .02, 5.
    # Integrate B(r)^2/(2 mu) over annular shells independently of the log formula.
    roots, weights = np.polynomial.legendre.leggauss(48)
    radius = (roots + 1) * (b - a) / 2 + a
    field = mu_0 * current / (2 * np.pi * radius)
    density = field ** 2 / (2 * mu_0)
    expected = np.sum(weights * density * (2 * np.pi * radius * length)) * (b - a) / 2
    assert model.energy_J([current]) == pytest.approx(expected, rel=1e-13)
    assert model.energy_J([2 * current]) == pytest.approx(4 * expected, rel=1e-13)
    assert model.unit == "H" and model.reference is None


@pytest.mark.parametrize("sign", [-1, 1])
def test_loop_orientation_changes_mutual_sign_without_changing_energy(sign):
    base = inductance()
    self_l = base.matrix_SI[0][0]
    second = loop("second", .01)
    if sign == -1:
        second = replace(second, vertices_m=tuple(reversed(second.vertices_m)),
            forward_support_ids=second.return_support_ids, return_support_ids=second.forward_support_ids)
    mutual = sign * .5 * self_l
    model = replace(base, matrix_SI=((self_l, mutual), (mutual, self_l)), loops=(loop(), second))
    assert model.energy_J([2., sign * 3.]) == pytest.approx(.5 * self_l * (4 + 9 + 6), rel=1e-14)
    assert ParasiticMatrix.from_json(model.to_json()) == model


@pytest.mark.parametrize("builder", [capacitance, inductance])
def test_json_roundtrip_preserves_si_matrix_reference_geometry_and_provenance(builder):
    model = builder()
    encoded = model.to_json()
    assert encoded.isascii()
    restored = ParasiticMatrix.from_json(encoded)
    assert restored == model
    assert restored.energy_J([2.]) == model.energy_J([2.])
    assert json.loads(encoded)["unit"] == model.unit


@pytest.mark.parametrize("matrix,reason", [
    (((0.,),), "self-energy"), (((-1.,),), "self-energy"),
    (((float("nan"),),), "finite"), (((float("inf"),),), "finite"),
    (((1j,),), "real SI"), (((True,),), "real SI"),
    ((("1",),), "real SI"), (((1., 2.),), "dimensions"),
])
def test_invalid_coefficients_are_rejected_without_repair(matrix, reason):
    with pytest.raises(ValueError, match=reason):
        replace(capacitance(), matrix_SI=matrix)


@pytest.mark.parametrize("matrix,reason", [
    (((1., -.2), (-.3, 1.)), "reciprocal"),
    (((1., -2.), (-2., 1.)), "passive"),
    (((1., .1), (.1, 1.)), "nonpositive mutuals"),
    (((1., -2.), (-2., 5.)), "reference coupling"),
])
def test_invalid_maxwell_conventions_and_physical_states_fail(matrix, reason):
    with pytest.raises(ValueError, match=reason):
        replace(capacitance(), matrix_SI=matrix, boundaries=(
            PotentialBoundary("first", ("first",)), PotentialBoundary("second", ("second",))))


def test_invalid_inductance_is_not_accepted_as_capacitance_or_a_grounded_branch():
    model = inductance()
    with pytest.raises(ValueError, match="passive"):
        replace(model, matrix_SI=((1., 1.1), (1.1, 1.)), loops=(loop(), loop("second", .01)))
    with pytest.raises(ValueError, match="return path"):
        replace(model, reference=PotentialBoundary("ground", ("ground",)))
    with pytest.raises(ValueError, match="zero-volt reference"):
        replace(capacitance(), reference=None)
    with pytest.raises(ValueError, match="nonoverlapping"):
        replace(capacitance(), reference=PotentialBoundary("other", ("upper_plate",)))
    with pytest.raises(ValueError, match="material coefficients"):
        replace(model, materials=capacitance().materials)


@pytest.mark.parametrize("vertices,reason", [
    (((0., 0, 0), (1., 0, 0), (1., 1., 0), (0., 1., 0)), "closing vertex"),
    (((0., 0, 0), (1., 0, 0), (1., 0, 0), (0., 0, 0)), "Repeated"),
    (((0., 0, 0), (1., 0, 0), (2., 0, 0), (0., 0, 0)), "non-collinear"),
    (((0., 0, 0), (1., 0, 0), (1., float("nan"), 0), (0., 0, 0)), "finite"),
])
def test_loops_need_actual_closed_nondegenerate_paths(vertices, reason):
    with pytest.raises(ValueError, match=reason):
        replace(loop(), vertices_m=vertices)
    with pytest.raises(ValueError, match="distinct"):
        replace(loop(), return_support_ids=loop().forward_support_ids)


@pytest.mark.parametrize("state", [[np.inf], [np.nan], [1j], [True], [1, 2]])
def test_invalid_energy_states_are_rejected(state):
    with pytest.raises(ValueError, match="finite real SI"):
        capacitance().energy_J(state)


@pytest.mark.parametrize("change", [
    {"unit": "pF"}, {"schema_version": 2}, {"unknown": "ignored?"},
    {"provenance": {"solver_name": "missing geometry and mesh"}},
    {"kind": "partial_inductance"},
])
def test_json_rejects_ambiguous_units_versions_and_missing_provenance(change):
    record = json.loads(capacitance().to_json())
    record.update(change)
    with pytest.raises(ValueError):
        ParasiticMatrix.from_json(json.dumps(record))


def test_json_and_provenance_fail_closed_for_duplicates_limits_nonfinite_and_ac():
    encoded = capacitance().to_json()
    for malformed in (encoded.replace('"unit": "F"', '"unit": "F", "unit": "F"'),
                      encoded.replace('300.0', 'NaN'), '[]', ' ' * 1_048_577):
        with pytest.raises(ValueError):
            ParasiticMatrix.from_json(malformed)
    for changes in ({"geometry_sha256": "unidentified"}, {"frequency_Hz": 1e6}, {"temperature_K": 0}):
        with pytest.raises(ValueError):
            replace(capacitance().provenance, **changes)
    with pytest.raises(ValueError, match="relative values"):
        replace(capacitance().materials[0], value_SI=3.9, unit="relative")


def test_extreme_finite_coefficients_do_not_overflow_validation_or_clip_energy():
    model = replace(capacitance(), matrix_SI=((1e308,),))
    assert model.energy_J([1.]) == 5e307
    with pytest.raises(ValueError, match="nonfinite"):
        model.energy_J([1e308])
    # Preserve tiny positive couplings and do not sparsify a weak axis.
    small = replace(capacitance(), matrix_SI=((1e-300,),))
    assert small.matrix_SI == ((1e-300,),)
    assert small.energy_J([1.]) == 5e-301


def test_weak_axis_negative_energy_is_not_hidden_by_a_strong_axis():
    model = inductance()
    with pytest.raises(ValueError, match="passive"):
        replace(model, matrix_SI=((1., 1e-10), (1e-10, 1e-24)),
                loops=(loop(), loop("weak", .01)))
    # Independent valid axes can span the floating-point dynamic range.
    valid = replace(model, matrix_SI=((1e300, 0.), (0., 1e-300)),
                    loops=(loop(), loop("weak", .01)))
    assert valid.energy_J([0., 1.]) == 5e-301


def test_unversioned_json_is_not_silently_adopted():
    record = json.loads(capacitance().to_json())
    record.pop("schema_version")
    with pytest.raises(ValueError, match="explicit supported schema"):
        ParasiticMatrix.from_json(json.dumps(record))


@pytest.mark.parametrize("boolean", [True, np.bool_(True)])
def test_boolean_scalar_coefficients_temperatures_and_frequencies_are_not_si_numbers(boolean):
    model = capacitance()
    with pytest.raises(ValueError, match="positive finite SI"):
        replace(model.materials[0], value_SI=boolean)
    with pytest.raises(ValueError, match="positive finite SI"):
        replace(model.provenance, temperature_K=boolean)
    with pytest.raises(ValueError, match="real static operators"):
        replace(model.provenance, frequency_Hz=type(boolean)(False))
