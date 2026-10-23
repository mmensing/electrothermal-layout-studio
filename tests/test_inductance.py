# SPDX-License-Identifier: MPL-2.0
"""Closed-loop geometry, native matrix fidelity and bounded-job contracts.

These tests do not pretend a substituted process establishes solver accuracy.
Independent actual FastHenry benchmarks are run with an explicitly installed
engine; ordinary CI checks generation, analytical limits and failure handling.
"""
import hashlib
import json
import re
import sys
from dataclasses import replace

import numpy as np
import pytest

from etlayout.models import Cancelled, SimulationError
from etlayout.power import inductance
from etlayout.power.field_snapshot import MU_0, FieldSnapshot, Loop, Segment


@pytest.fixture
def loop_snapshot():
    points = ((0., 0., 0.), (.001, 0., 0.), (.001, .0002, 0.), (0., .0002, 0.), (0., 0., 0.))
    segments = tuple(Segment(start, end, 20e-6, 20e-6,
                             (0., 1., 0.) if start[0] != end[0] else (1., 0., 0.),
                             5.8e7, ("layer:copper:0",)) for start, end in zip(points, points[1:]))
    return FieldSnapshot("loop_rl", (), ("component:0",), (), (Loop("loop μ", segments, "component:0"),),
                         300., 1., 1., json.dumps({"source_physics_hash": "abc", "source_configuration_hash": "def"}),
                         "geometry", "field-model")


def test_bundle_deterministic_port_cut_finite_return_units_and_hashes(tmp_path, loop_snapshot):
    first = inductance.prepare_inductance(loop_snapshot, tmp_path / "a")
    second = inductance.prepare_inductance(loop_snapshot, tmp_path / "b")
    assert first == second and first["status"] == "prepared"
    assert len(first["cases"]) == 9
    assert first["series_bar_resistance_ohm"] == pytest.approx([.0024 / (20e-6**2 * 5.8e7)])
    assert first["provenance"]["source_context"]["source_physics_hash"] == "abc"
    assert first["native_relative_precision_floor"] >= 5e-6
    for name, record in first["files"].items():
        data = (tmp_path / "a" / name).read_bytes()
        assert data == (tmp_path / "b" / name).read_bytes()
        assert record == {"sha256": hashlib.sha256(data).hexdigest(), "bytes": len(data)}
    for case in first["cases"]:
        deck = (tmp_path / "a" / case["deck"]).read_text()
        assert ".units m\n" in deck and ".equiv" not in deck
        assert "sigma=58000000" in deck and 'loop "loop \\u03bc"' in deck
        nodes = {match[1]: np.array(list(map(float, match.groups()[1:]))) for match in re.finditer(
            r"^(N\d+_\d+) x=([^ ]+) y=([^ ]+) z=([^\n]+)$", deck, re.MULTILINE)}
        first_node, last_node, port = case["ports"][0]
        assert first_node != last_node and port == "p1"
        np.testing.assert_array_equal(nodes[first_node], nodes[last_node])
        bars = list(re.finditer(r"^E\d+_\d+ (N\d+_\d+) (N\d+_\d+) ", deck, re.MULTILINE))
        assert len(bars) == 4 * case["longitudinal_subdivisions"]
        # Reconstruct physical series resistance from emitted endpoints rather
        # than asking generation to reproduce its own analytical answer.
        resistance = sum(np.linalg.norm(nodes[m[1]] - nodes[m[2]]) / (20e-6**2 * 5.8e7) for m in bars)
        assert resistance == pytest.approx(first["series_bar_resistance_ohm"][0], rel=1e-13)
        assert case["filaments"] == len(bars) * case["cross_section_subdivisions"]**2
    assert json.loads((tmp_path / "a" / "manifest.json").read_bytes()) == first


@pytest.mark.parametrize("options,match", [
    ({"frequencies_Hz": (1., 10.)}, "beginning with DC"),
    ({"frequencies_Hz": (0., 1., 1.)}, "strictly increasing"),
    ({"frequencies_Hz": (0., float("nan"))}, "frequencies"),
    ({"frequencies_Hz": (0., True)}, "frequencies"),
    ({"frequencies_Hz": (0., 1e12)}, "electrically small"),
    ({"refinements": ((1, 1), (2, 2))}, "3 to 5"),
    ({"refinements": ((1, 1), (2, 3), (1, 5))}, "increasing"),
    ({"relative_tolerance": 1e-8}, "precision floor"),
    ({"relative_tolerance": True}, "precision floor"),
    ({"max_filaments": 99}, "filaments"),
])
def test_unsupported_precision_frequency_and_resource_requests_rejected(tmp_path, loop_snapshot, options, match):
    with pytest.raises(SimulationError, match=match):
        inductance.prepare_inductance(loop_snapshot, tmp_path / "bundle", **options)
    assert not (tmp_path / "bundle").exists()


@pytest.mark.parametrize("damage", ["open", "zero_length", "bad_width", "bad_vector", "shared_component", "magnetic"])
def test_no_inferred_return_bad_cross_section_or_shared_loop_basis(tmp_path, loop_snapshot, damage):
    segments = list(loop_snapshot.loops[0].segments)
    if damage == "open":
        segments.pop()
    elif damage == "zero_length":
        segments[0] = replace(segments[0], end_m=segments[0].start_m)
    elif damage == "bad_width":
        segments[0] = replace(segments[0], width_m=-1.)
    elif damage == "bad_vector":
        segments[0] = replace(segments[0], width_direction=(1., 0., 0.))
    loops = (replace(loop_snapshot.loops[0], segments=tuple(segments)),)
    if damage == "shared_component":
        loops *= 2
    snapshot = replace(loop_snapshot, loops=loops, relative_permeability=2. if damage == "magnetic" else 1.)
    with pytest.raises(SimulationError):
        inductance.prepare_inductance(snapshot, tmp_path / "bundle")


def _matrix_text(matrix, frequency=1.):
    n = len(matrix)
    header = "".join(f"Row {i + 1}: n{i}_0 to n{i}_4, port name: p{i + 1}\n" for i in range(n))
    rows = "\n".join(" ".join(f"{complex(value).real:.17g} {complex(value).imag:+.17g}j" for value in row) for row in matrix)
    return f"{header}Impedance matrix for frequency = {frequency:g} {n} x {n}\n{rows}\n".encode()


def test_native_operator_preserves_mutual_orientation_weak_entries_and_dc_inductance_absence():
    ports = [("N0_0", "N0_4", "p1"), ("N1_0", "N1_4", "p2")]
    matrix = np.array([[.2 + 1e-6j, -1e-15j], [-1e-15j, .3 + 2e-6j]])
    parsed = inductance._parse_impedance(_matrix_text(matrix), 1., ports)
    np.testing.assert_array_equal(parsed, matrix)
    native_lines = _matrix_text(matrix).splitlines()
    native_lines[:2] = reversed(native_lines[:2])
    np.testing.assert_array_equal(inductance._parse_impedance(b"\n".join(native_lines) + b"\n", 1., ports), matrix)
    assert parsed[0, 1].imag < 0
    assert inductance._physical_checks(parsed, 1., [.2, .3])["passed"]
    dc = inductance._physical_checks(np.diag([.2, .3]), 0., [.2, .3])
    assert dc["passed"] and dc["dc_imaginary_zero"]
    broken = parsed.copy()
    broken[0, 1] = 0
    assert not inductance._physical_checks(broken, 1., [.2, .3])["reciprocal"]
    assert inductance._relative_change([1e-15, 0], [0, 1e-30])[0] == 1.
    assert MU_0 == pytest.approx(4e-7 * np.pi)


def test_weak_negative_energy_and_real_reciprocity_are_not_hidden_by_strong_imaginary_response():
    weak_negative = np.diag([1e6 + 1j, -1e-15 + 1e-6j])
    checks = inductance._physical_checks(weak_negative, 1., [1e6, 1e-15])
    assert not checks["passed"] and not checks["components"]["R_ohm"]["positive_self_entries"]
    nonreciprocal = np.array([[1. + 1e6j, 1e-15 + 1e3j], [2e-15 + 1e3j, 1. + 1e6j]])
    checks = inductance._physical_checks(nonreciprocal, 1., [1., 1.])
    assert not checks["reciprocal"] and checks["components"]["R_ohm"]["reciprocity_relative_error"] == .5


def test_recorded_real_engine_sample_agrees_with_independent_volume_current_energy_and_dc_limit():
    # Native FastHenry 3.0wr mod031424, upstream c1244c23, unmodified %lg
    # output. Generated/reimported GDS: 1mm x 0.2mm centerline rectangle,
    # 20um-square copper bars, sigma5.8e7S/m, uniform1Hz excitation, 400
    # filaments. This is a recorded numerical regression, not a CI tool run.
    raw = (b"Row 1:  n0_0  to  n0_16, port name: p1\n"
           b"Impedance matrix for frequency = 1 1 x 1\n"
           b"      0.103448  +8.74212e-09j \n")
    measured = inductance._parse_impedance(raw, 1., [("N0_0", "N0_16", "p1")])[0, 0]
    assert measured.real == pytest.approx(.0024 / (20e-6**2 * 5.8e7), rel=5e-6)
    length, spacing, width, height = .001, .0002, 20e-6, 20e-6
    # Independently integrate the Neumann volume-current energy. Perpendicular
    # bars have zero dot product. For parallel bars, first integrate the two
    # longitudinal coordinates analytically, then use Gauss quadrature over
    # the triangular cross-section difference distribution (including self).
    def partial(bar_length, separation, order):
        nodes, weights = np.polynomial.legendre.leggauss(order)
        y, z = np.meshgrid((nodes + 1) * width / 2, (nodes + 1) * height / 2, indexing="ij")
        weight = np.outer(weights, weights) * width * height / 4 * (width-y) * (height-z) / (width*height)**2
        integral = 0.
        for sy in (-1, 1):
            for sz in (-1, 1):
                radius = np.hypot(separation + sy*y, sz*z)
                kernel = bar_length * np.arcsinh(bar_length/radius) - np.hypot(bar_length, radius) + radius
                integral += float(np.sum(weight * kernel))
        return MU_0 / (2*np.pi) * integral
    energies = [2 * (partial(length, 0., order) + partial(spacing, 0., order)
                     - partial(length, spacing, order) - partial(spacing, length, order)) for order in (32, 64)]
    assert energies[0] == pytest.approx(energies[1], rel=1e-6)
    assert measured.imag / (2*np.pi) == pytest.approx(energies[-1], rel=5e-6)


@pytest.mark.parametrize("damage", ["frequency", "cut", "dimension", "missing", "nan", "injected", "duplicate"])
def test_native_output_is_complete_and_cannot_silently_change_frequency_or_port_basis(damage):
    raw = _matrix_text([[.1 + 1e-8j]])
    changes = {"frequency": (b"frequency = 1 ", b"frequency = 0 "),
               "cut": (b"n0_4", b"n0_5"), "dimension": (b"1 x 1", b"2 x 2"),
               "missing": (b"0.10000000000000001 +1e-08j", b""),
               "nan": (b"0.10000000000000001", b"1e999"),
               "injected": (b"+1e-08j", b"+1e-08j .external bad nodes")}
    raw = raw + raw if damage == "duplicate" else raw.replace(*changes[damage])
    with pytest.raises(SimulationError):
        inductance._parse_impedance(raw, 1., [("N0_0", "N0_4", "p1")])


def test_disposable_bundle_cancellation_and_existing_decks_cannot_execute(tmp_path, loop_snapshot, monkeypatch):
    directory = tmp_path / "cancel"
    with pytest.raises(Cancelled):
        inductance.prepare_inductance(loop_snapshot, directory, cancel=lambda: True)
    assert not directory.exists()
    directory.mkdir()
    (directory / "model.inp").write_text("untrusted external input")
    monkeypatch.setattr(inductance, "run_external_tool", lambda *_a, **_k: pytest.fail("Must not execute existing input"))
    with pytest.raises(SimulationError, match="new or empty"):
        inductance.extract_inductance(loop_snapshot, directory, executable=sys.executable)


@pytest.mark.parametrize("status", ["execution_failed", "timeout", "output_limit", "memory_limit"])
def test_external_execution_failures_cannot_claim_extraction_completion(tmp_path, loop_snapshot, monkeypatch, status):
    calls = []
    def failed(executable, args, working, **settings):
        calls.append(settings)
        return {"status": status, "output": b"diagnostic", "returncode": 2, "seconds": .01,
                "peak_rss_sampled_bytes": 12345, "peak_job_commit_bytes": 23456}
    monkeypatch.setattr(inductance, "run_external_tool", failed)
    report = inductance.extract_inductance(loop_snapshot, tmp_path / "failure", executable=sys.executable)
    assert len(calls) == 1 and calls[0]["memory_limit_bytes"] == 1024**3 and calls[0]["native_threads"] == 1
    assert report["status"] == status and report["frequency_results"] == []
    assert report["mesh_verification"]["status"] == "not_verified"
    assert report["low_frequency_limit"]["status"] == "not_established"
    assert report["resources"]["peak_job_commit_bytes"] == 23456
    assert json.loads((tmp_path / "failure" / "report.json").read_bytes()) == report
