# SPDX-License-Identifier: MPL-2.0
"""Experimental static C/L result interchange, not a layout field solver.

These contracts validate externally supplied operators and their electrical
basis. They neither calculate a field solution nor certify external provenance
or mesh convergence. No existing project material or SPICE model is changed.
"""
from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass

import numpy as np

_MAX_AXES = 128
_MAX_JSON_BYTES = 1_048_576


def _label(value):
    if not isinstance(value, str) or not value.strip() or len(value) > 1024 or any(ord(c) < 32 for c in value):
        raise ValueError("Identifiers must be nonempty strings without control characters")
    return value


def _positive(value, name):
    if isinstance(value, (bool, np.bool_, str)) or not np.isscalar(value) or not np.isrealobj(value):
        raise ValueError(f"{name} must be a positive finite SI number")
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError) as error:
        raise ValueError(f"{name} must be a positive finite SI number") from error
    if not np.isfinite(number) or number <= 0:
        raise ValueError(f"{name} must be a positive finite SI number")
    return number


def _identifiers(values, name):
    if not isinstance(values, (tuple, list)) or not 1 <= len(values) <= 4096:
        raise ValueError(f"{name} must name explicit geometry supports")
    result = tuple(_label(value) for value in values)
    if len(set(result)) != len(result):
        raise ValueError(f"{name} contains duplicate identifiers")
    return result


@dataclass(frozen=True)
class PotentialBoundary:
    """One equipotential conductor boundary or the fixed zero-volt reference.

    Support identifiers resolve against the hash-bound external 3D geometry;
    they are not inferred from an ETLayout layer name or projected overlap.
    """

    id: str
    support_ids: tuple[str, ...]

    def __post_init__(self):
        _label(self.id)
        object.__setattr__(self, "support_ids", _identifiers(self.support_ids, "Boundary supports"))


@dataclass(frozen=True)
class ClosedLoop:
    """An oriented closed 3D circuit contour, including forward and return paths.

    The ordered SI vertices establish loop-current direction, not a thin-wire
    approximation or a current distribution. Conductor cross-sections and
    end connections must remain in the external geometry identified by hash.
    """

    id: str
    vertices_m: tuple[tuple[float, float, float], ...]
    forward_support_ids: tuple[str, ...]
    return_support_ids: tuple[str, ...]

    def __post_init__(self):
        _label(self.id)
        forward = _identifiers(self.forward_support_ids, "Forward path")
        returns = _identifiers(self.return_support_ids, "Return path")
        if set(forward) & set(returns):
            raise ValueError("Forward and return supports must be distinct")
        try:
            if np.asarray(self.vertices_m).dtype.kind not in "iuf":
                raise ValueError("Loop vertices must be real SI numbers")
            vertices = np.asarray(self.vertices_m, dtype=float)
        except (TypeError, ValueError, OverflowError) as error:
            raise ValueError("Loop vertices must be finite 3D coordinates in metres") from error
        if (vertices.ndim != 2 or vertices.shape[1] != 3 or not 4 <= len(vertices) <= 4097
                or not np.isfinite(vertices).all()):
            raise ValueError("Loop needs 4–4097 finite 3D vertices in metres, including closure")
        if not np.array_equal(vertices[0], vertices[-1]):
            raise ValueError("Loop must include its explicit closing vertex; an open return path is unsupported")
        if len(set(map(tuple, vertices[:-1]))) != len(vertices) - 1:
            raise ValueError("Repeated or zero-length loop segments are unsupported")
        offsets = vertices[1:-1] - vertices[0]
        # Reject a contour that only runs out and back along a line. This does
        # not claim to validate conductor volumes or arbitrary self-intersection.
        length = float(np.max(np.abs(offsets)))
        if not np.isfinite(length) or length == 0 or np.linalg.matrix_rank(offsets / length) < 2:
            raise ValueError("A loop contour must span a non-collinear forward/return path")
        object.__setattr__(self, "vertices_m", tuple(map(tuple, vertices.tolist())))
        object.__setattr__(self, "forward_support_ids", forward)
        object.__setattr__(self, "return_support_ids", returns)


@dataclass(frozen=True)
class FieldMaterial:
    """Positive real linear permittivity (F/m) or permeability (H/m)."""

    id: str
    support_ids: tuple[str, ...]
    value_SI: float
    unit: str

    def __post_init__(self):
        _label(self.id)
        object.__setattr__(self, "support_ids", _identifiers(self.support_ids, "Material supports"))
        object.__setattr__(self, "value_SI", _positive(self.value_SI, "Material coefficient"))
        if self.unit not in {"F/m", "H/m"}:
            raise ValueError("Material coefficient unit must be F/m or H/m; relative values need conversion")


@dataclass(frozen=True)
class FieldProvenance:
    """Declared external evidence identities; hashes are not proof of a solve."""

    geometry_sha256: str
    mesh_sha256: str
    result_sha256: str
    solver_name: str
    solver_version: str
    temperature_K: float
    approximation: str
    frequency_Hz: float = 0.0

    def __post_init__(self):
        for name in ("geometry_sha256", "mesh_sha256", "result_sha256"):
            if not isinstance(getattr(self, name), str) or not re.fullmatch("[0-9a-f]{64}", getattr(self, name)):
                raise ValueError(f"{name} must be a lowercase SHA-256 identity")
        for value in (self.solver_name, self.solver_version, self.approximation):
            _label(value)
        object.__setattr__(self, "temperature_K", _positive(self.temperature_K, "Temperature in K"))
        if (isinstance(self.frequency_Hz, (bool, np.bool_)) or not np.isscalar(self.frequency_Hz)
                or not np.isrealobj(self.frequency_Hz) or self.frequency_Hz != 0.0):
            raise ValueError("Only real static operators at 0 Hz are supported; AC/lossy models need another contract")
        object.__setattr__(self, "frequency_Hz", 0.0)


@dataclass(frozen=True)
class ParasiticMatrix:
    """Static grounded Maxwell C or oriented closed-loop L, entirely in SI.

    C maps conductor potentials relative to the declared reference to charge;
    L maps oriented closed-loop currents to flux linkages. These matrices have
    different bases. A partial-inductance matrix is not a loop-L matrix.
    """

    kind: str
    matrix_SI: tuple[tuple[float, ...], ...]
    materials: tuple[FieldMaterial, ...]
    provenance: FieldProvenance
    boundaries: tuple[PotentialBoundary, ...] = ()
    reference: PotentialBoundary | None = None
    loops: tuple[ClosedLoop, ...] = ()
    schema_version: int = 1

    def __post_init__(self):
        if self.schema_version != 1 or isinstance(self.schema_version, bool):
            raise ValueError("Unsupported parasitic matrix schema version")
        if self.kind not in {"capacitance", "loop_inductance"}:
            raise ValueError("Kind must be capacitance or loop_inductance; no inferred matrix convention")
        for name, cls in (("boundaries", PotentialBoundary), ("loops", ClosedLoop), ("materials", FieldMaterial)):
            values = getattr(self, name)
            if not isinstance(values, (list, tuple)) or not all(isinstance(item, cls) for item in values):
                raise ValueError(f"{name} must contain validated {cls.__name__} records")
            object.__setattr__(self, name, tuple(values))
        if not isinstance(self.provenance, FieldProvenance) or not self.materials:
            raise ValueError("Explicit field materials and validated provenance are required")
        if self.kind == "capacitance":
            if not self.boundaries or not isinstance(self.reference, PotentialBoundary) or self.loops:
                raise ValueError("Capacitance needs conductor boundaries and an explicit zero-volt reference, not loops")
            all_boundaries = (*self.boundaries, self.reference)
            supports = [support for boundary in all_boundaries for support in boundary.support_ids]
            if len(set(boundary.id for boundary in all_boundaries)) != len(all_boundaries) or len(set(supports)) != len(supports):
                raise ValueError("Capacitance boundaries/reference must have distinct IDs and nonoverlapping support IDs")
            material_unit = "F/m"
        else:
            if not self.loops or self.boundaries or self.reference is not None:
                raise ValueError("Loop inductance needs explicit closed loops; an electrical ground cannot define a return path")
            material_unit = "H/m"
        if any(material.unit != material_unit for material in self.materials):
            raise ValueError(f"{self.kind} requires material coefficients in {material_unit}")
        material_supports = [support for material in self.materials for support in material.support_ids]
        if (len({material.id for material in self.materials}) != len(self.materials)
                or len(set(material_supports)) != len(material_supports)):
            raise ValueError("Material IDs and support IDs must be distinct")
        if not 1 <= len(self.axis_ids) <= _MAX_AXES or len(set(self.axis_ids)) != len(self.axis_ids):
            raise ValueError("Operator needs 1–128 distinct electrical axes")
        try:
            # Complex and boolean arrays must not be silently coerced to real.
            original = np.asarray(self.matrix_SI)
            if original.dtype.kind not in "iuf":
                raise ValueError("Matrix coefficients must be real SI numbers")
            matrix = np.asarray(self.matrix_SI, dtype=float)
        except (TypeError, ValueError, OverflowError) as error:
            raise ValueError("Matrix coefficients must be real SI numbers") from error
        if matrix.shape != (len(self.axis_ids), len(self.axis_ids)) or not np.isfinite(matrix).all():
            raise ValueError("Matrix dimensions must match axes and every coefficient must be finite")
        if np.any(np.diag(matrix) <= 0):
            raise ValueError("Each declared electrical axis needs positive self-energy")
        # A machine-roundoff bound, not a user-adjustable physical tolerance.
        # Scale each axis by its own positive self term. A strong axis must
        # not conceal a negative-energy weak subnetwork under a global norm.
        diagonal_root = np.sqrt(np.diag(matrix))
        with np.errstate(over="ignore", invalid="ignore", divide="ignore"):
            normalized = matrix / diagonal_root[:, None] / diagonal_root[None, :]
        if not np.isfinite(normalized).all():
            raise ValueError("Matrix is not passive within finite per-axis scaling")
        tolerance = 64 * np.finfo(float).eps * len(matrix)
        if np.max(np.abs(normalized - normalized.T)) > tolerance:
            raise ValueError("Matrix is not reciprocal; no averaging/repair is performed")
        eigenvalues = np.linalg.eigvalsh((normalized + normalized.T) / 2)
        if eigenvalues[0] < -tolerance:
            raise ValueError("Matrix is not passive: a state has negative stored energy")
        if self.kind == "capacitance":
            off_diagonal = matrix[~np.eye(len(matrix), dtype=bool)]
            row_scaled = matrix / np.max(np.abs(matrix), axis=1)[:, None]
            if np.any(off_diagonal > 0) or np.any(row_scaled.sum(axis=1) < -tolerance):
                raise ValueError("Grounded Maxwell capacitance requires nonpositive mutuals and nonnegative reference coupling")
        object.__setattr__(self, "matrix_SI", tuple(map(tuple, matrix.tolist())))

    @property
    def axis_ids(self):
        return tuple(item.id for item in (self.boundaries if self.kind == "capacitance" else self.loops))

    @property
    def unit(self):
        return "F" if self.kind == "capacitance" else "H"

    def energy_J(self, state):
        """Return 1/2 VᵀCV or 1/2 IᵀLI; C voltages are reference-relative."""
        values = np.asarray(state)
        if values.dtype.kind not in "iuf" or values.shape != (len(self.axis_ids),) or not np.isfinite(values).all():
            raise ValueError("Energy state must have one finite real SI value per electrical axis")
        with np.errstate(over="ignore", invalid="ignore"):
            energy = float(0.5 * values @ np.asarray(self.matrix_SI) @ values)
        if not np.isfinite(energy) or energy < 0:
            raise ValueError("Stored energy is negative/nonfinite; coefficients and state are not repaired")
        return energy

    def to_json(self):
        """Serialize only this versioned contract; no code, paths or execution."""
        text = json.dumps({**asdict(self), "unit": self.unit}, allow_nan=False, ensure_ascii=True, sort_keys=True)
        if len(text.encode("utf-8")) > _MAX_JSON_BYTES:
            raise ValueError("Parasitic interchange JSON exceeds 1 MiB")
        return text

    @classmethod
    def from_json(cls, text):
        """Read a bounded JSON record, rejecting duplicate/unknown keys and NaN."""
        if not isinstance(text, str) or len(text.encode("utf-8")) > _MAX_JSON_BYTES:
            raise ValueError("Parasitic interchange JSON must be text of at most 1 MiB")

        def unique(pairs):
            result = {}
            for key, value in pairs:
                if key in result:
                    raise ValueError(f"Duplicate JSON key: {key}")
                result[key] = value
            return result

        def nonfinite(value):
            raise ValueError(f"Nonfinite JSON number: {value}")

        try:
            data = json.loads(text, object_pairs_hook=unique, parse_constant=nonfinite)
            if not isinstance(data, dict):
                raise ValueError("Interchange record must be an object")
            if data.get("schema_version") != 1 or isinstance(data.get("schema_version"), bool):
                raise ValueError("An explicit supported schema version is required")
            kind = data.get("kind")
            if data.pop("unit") != {"capacitance": "F", "loop_inductance": "H"}.get(kind):
                raise ValueError("Matrix unit must match its explicit SI convention (F or H)")
            data["materials"] = tuple(FieldMaterial(**item) for item in data["materials"])
            data["boundaries"] = tuple(PotentialBoundary(**item) for item in data.get("boundaries", []))
            data["loops"] = tuple(ClosedLoop(**item) for item in data.get("loops", []))
            if data.get("reference") is not None:
                data["reference"] = PotentialBoundary(**data["reference"])
            data["provenance"] = FieldProvenance(**data["provenance"])
            return cls(**data)
        except (KeyError, TypeError, OverflowError, RecursionError) as error:
            raise ValueError("Malformed or unsupported parasitic interchange record") from error
