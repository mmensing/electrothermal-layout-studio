# SPDX-License-Identifier: MPL-2.0
"""Safe implementation identity, separate from physical configuration identity.

Source and installed packages hash the shipped Python implementation. Frozen
builds use the source stamp created before freezing; compiled loader bytes are
not a substitute for the original source identity. No paths or hostnames leave
this module. Saved results are never relabeled with the current installation.
"""
from __future__ import annotations

import hashlib
import json
import platform
import sys
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path

from etlayout import __version__
from etlayout.models import SimulationError

NUMERICAL_DEPENDENCIES = ("numpy", "scipy", "shapely", "gmsh", "klayout")
RUNTIME_DEPENDENCIES = (*NUMERICAL_DEPENDENCIES, "PySide6", "shiboken6", "pyqtgraph", "matplotlib",
                        "meshio", "platformdirs", "threadpoolctl", "psutil")


def implementation_identity(package=None, *, frozen=None):
    """Canonical exact source digest shared with optimization and frozen builds."""
    package = Path(package) if package is not None else Path(__file__).parent
    frozen = getattr(sys, "frozen", False) if frozen is None else frozen
    if frozen:
        stamp = package / "data" / "implementation.json"
        if not stamp.is_file():
            raise SimulationError("Frozen package lacks implementation identity; rebuild the application")
        try:
            digest = json.loads(stamp.read_text(encoding="utf-8")).get("source_sha256", "")
        except (ValueError, AttributeError, OSError) as error:
            raise SimulationError("Frozen implementation identity is invalid; rebuild the application") from error
        if not isinstance(digest, str) or len(digest) != 64 or any(c not in "0123456789abcdef" for c in digest):
            raise SimulationError("Frozen implementation identity is invalid; rebuild the application")
        return digest
    source = {p.relative_to(package).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
              for p in sorted(package.rglob("*.py"))}
    if not source:
        raise SimulationError("Package lacks Python implementation sources; repair the installation")
    return hashlib.sha256(json.dumps(source, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()).hexdigest()


def dependency_versions(names=RUNTIME_DEPENDENCIES):
    """Read distribution metadata without importing GUI or native libraries."""
    result = {}
    for name in names:
        try:
            result[name] = version(name)
        except PackageNotFoundError:
            result[name] = None
    return result


def build_info():
    """Current implementation receipt; a fingerprint is not a test attestation."""
    frozen = bool(getattr(sys, "frozen", False))
    package = Path(__file__).parent
    digest = implementation_identity(package, frozen=frozen)
    if frozen:
        stamp = json.loads((package / "data/implementation.json").read_text(encoding="utf-8"))
        if stamp.get("version", __version__) != __version__:
            raise SimulationError("Frozen build version differs from its implementation stamp; rebuild the application")
    edition = "frozen" if frozen else ("source" if (package.parent.parent / "pyproject.toml").is_file() else "installed")
    return {"schema_version": 1, "version": __version__, "edition": edition, "identity_status": "available",
            "implementation_sha256": digest,
            "identity_basis": "frozen source stamp" if frozen else "exact shipped Python source bytes",
            "dependencies": dependency_versions(),
            "runtime": {"python": platform.python_version(), "implementation": platform.python_implementation(),
                        "system": platform.system(), "release": platform.release(), "machine": platform.machine()},
            "limitations": "Implementation identity is separate from physics identity; it does not establish validation, calibration or native binary equivalence."}


def result_build_info(result):
    """Only recorded provenance; legacy results retain their original solver label."""
    recorded = getattr(result, "execution", {}).get("build")
    if isinstance(recorded, dict) and recorded:
        from copy import deepcopy
        return deepcopy(recorded)
    return {"schema_version": 1, "identity_status": "unavailable", "solver_version": result.solver_version,
            "reason": "This result predates recorded build provenance; rerun to establish an implementation identity."}


def about_build_text():
    info = build_info()
    return (f"Version {info['version']} ({info['edition']}) · Python {info['runtime']['python']}\n"
            f"Implementation SHA-256: {info['implementation_sha256']}")


def shared_result_identity(result):
    """Small allowlisted identity for explicit sharing, excluding arbitrary metadata."""
    import re
    recorded = result_build_info(result)
    digest = recorded.get("implementation_sha256", "")
    if not isinstance(digest, str) or re.fullmatch("[0-9a-f]{64}", digest) is None:
        return {"identity_status": "unavailable"}
    shared = {"identity_status": "available", "implementation_sha256": digest}
    value = recorded.get("version", "")
    if isinstance(value, str) and re.fullmatch(r"[0-9]+(?:\.[0-9]+){1,3}(?:[a-z0-9.+-]{0,30})", value):
        shared["version"] = value
    return shared
