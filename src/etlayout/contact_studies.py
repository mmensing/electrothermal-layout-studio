# SPDX-License-Identifier: MPL-2.0
"""Synthetic finite-contact TLM extraction using ordinary generated FEM projects.

The regression residual describes numerical fit scatter. It is not experimental
uncertainty, a process calibration, or an independent mesh-convergence estimate.
"""

from __future__ import annotations

import json
import math
from pathlib import Path

import numpy as np
from scipy.optimize import brentq

from etlayout.models import SimulationError, checkpoint


def finite_contact_reference(
    spacings_m, *, sheet_resistance=10.0, rho_c=1e-9, width=40e-6, contact_length=60e-6
):
    """Independent 1-D contact-front model, including sheet loss under contacts."""
    values = np.asarray([sheet_resistance, rho_c, width, contact_length], dtype=float)
    gaps = np.asarray(spacings_m, dtype=float)
    if (
        not np.isfinite(values).all()
        or (values <= 0).any()
        or not np.isfinite(gaps).all()
        or (gaps <= 0).any()
    ):
        raise SimulationError("Finite-contact reference requires positive finite SI parameters")
    length = math.sqrt(rho_c / sheet_resistance)
    contact = sheet_resistance * length / width / math.tanh(contact_length / length)
    return {
        "Rs_ohm_per_square": sheet_resistance,
        "Rc_ohm": contact,
        "rho_c_ohm_m2": rho_c,
        "LT_m": length,
        "resistance_ohm": (sheet_resistance * gaps / width + 2 * contact).tolist(),
        "assumptions": "Uniform sheet resistance under and between contacts; equipotential metal; finite contact length",
    }


def fit_transfer_length(spacings_m, resistances_ohm, *, width=40e-6, contact_length=60e-6):
    """Fit Rs and two-contact intercept, then invert the finite-length coth law."""
    gaps, resistance = np.asarray(spacings_m, dtype=float), np.asarray(resistances_ohm, dtype=float)
    if (
        gaps.ndim != 1
        or resistance.shape != gaps.shape
        or len(gaps) < 3
        or len(np.unique(gaps)) < 3
        or not np.isfinite(gaps).all()
        or not np.isfinite(resistance).all()
        or (gaps <= 0).any()
        or (resistance <= 0).any()
        or not math.isfinite(width)
        or width <= 0
        or not math.isfinite(contact_length)
        or contact_length <= 0
    ):
        raise SimulationError(
            "TLM fit needs at least three distinct positive SI gaps/resistances and positive contact dimensions"
        )
    design = np.column_stack((gaps / width, np.ones(len(gaps))))
    coefficients, _, rank, _ = np.linalg.lstsq(design, resistance, rcond=None)
    sheet, intercept = coefficients
    if rank != 2 or sheet <= 0 or intercept <= 0:
        raise SimulationError(
            "TLM fit is nonphysical: positive sheet resistance and contact intercept are required"
        )
    contact = intercept / 2
    ratio = contact * width / (sheet * contact_length)
    # Solve in dimensionless LT/Lc, so root tolerance has no hidden length unit.
    scale = brentq(
        lambda x: x / math.tanh(1 / x) - ratio,
        1e-14,
        max(1.0, ratio + math.sqrt(ratio)),
        xtol=1e-14,
        rtol=1e-12,
    )
    length = scale * contact_length
    rho = sheet * length**2
    predictions = design @ coefficients
    residuals = resistance - predictions
    variance = float(residuals @ residuals / (len(gaps) - 2))
    covariance = variance * np.linalg.inv(design.T @ design)
    z = contact_length / length
    coth = 1 / math.tanh(z)
    csch2 = 0.0 if z > 350 else 1 / math.sinh(z) ** 2
    derivative = sheet / width * (coth + z * csch2)
    dlength = np.array([-length * coth / width / derivative, 0.5 / derivative])
    drho = np.array([length**2, 0.0]) + 2 * sheet * length * dlength
    jacobian = np.vstack(([1.0, 0.0], [0.0, 0.5], dlength, drho))
    derived_covariance = jacobian @ covariance @ jacobian.T
    errors = np.sqrt(np.maximum(np.diag(derived_covariance), 0.0))
    keys = ["Rs_ohm_per_square", "Rc_ohm", "LT_m", "rho_c_ohm_m2"]
    return {
        **dict(zip(keys, map(float, (sheet, contact, length, rho)))),
        "intercept_two_contacts_ohm": float(intercept),
        "covariance": covariance.tolist(),
        "covariance_parameters": ["Rs_ohm_per_square", "intercept_two_contacts_ohm"],
        "derived_covariance": derived_covariance.tolist(),
        "derived_parameters": keys,
        "standard_errors": dict(zip(keys, errors.tolist())),
        "residuals_ohm": residuals.tolist(),
        "predictions_ohm": predictions.tolist(),
        "residual_rms_ohm": math.sqrt(variance),
        "residual_degrees_of_freedom": len(gaps) - 2,
        "uncertainty_scope": "OLS numerical residual scatter only; excludes mesh bias, model error and experimental uncertainty",
    }


def transfer_length_study(
    output_directory,
    spacings_um=(40.0, 80.0, 120.0, 160.0),
    *,
    execution=None,
    mesh_factor=1.0,
    progress=None,
    cancel=None,
):
    """Generate, actually solve and archive editable E27 coupons; never change main reference."""
    from etlayout.examples import make_example
    from etlayout.io.project import load_project, physics_hash, save_project
    from etlayout.simulation.runner import run_project

    gaps = np.asarray(spacings_um, dtype=float)
    if (
        gaps.ndim != 1
        or len(gaps) < 3
        or len(np.unique(gaps)) != len(gaps)
        or not np.isfinite(gaps).all()
        or (gaps <= 0).any()
    ):
        raise SimulationError(
            "TLM study needs at least three distinct positive contact spacings in micrometres"
        )
    if not math.isfinite(mesh_factor) or not 0 < mesh_factor <= 1:
        raise SimulationError("TLM mesh factor must be positive and no greater than one")
    root = Path(output_directory)
    if root.exists() and any(root.iterdir()):
        raise SimulationError("Choose an empty output directory for the TLM study; existing projects are preserved")
    root.mkdir(parents=True, exist_ok=True)
    cases, observations = [], []
    for index, spacing in enumerate(gaps):
        checkpoint(cancel, progress, index / len(gaps), f"TLM gap {spacing:g} micrometres")
        folder = root / ("gap-" + format(float(spacing), ".12g"))
        project = make_example("E27", folder, tlm_spacing_um=float(spacing))
        project.mesh.max_size *= mesh_factor
        project.mesh.min_size *= mesh_factor
        project.mesh.contact_size *= mesh_factor

        def point_progress(fraction, message, index=index):
            checkpoint(cancel, progress, (index + fraction) / len(gaps), message)

        result = run_project(project, point_progress, cancel, execution=execution)
        if not result.converged:
            raise SimulationError(
                f"TLM gap {spacing:g}: ordinary configured electrical solve did not converge"
            )
        ports = result.analysis["ports"]
        current = abs(ports["drain"]["current"])
        if current <= 0 or not math.isclose(current, 0.001, rel_tol=project.solver.current_balance_tolerance):
            raise SimulationError("TLM extraction requires the declared 1 mA delivered service")
        total_resistance = abs(ports["drain"]["voltage"] - ports["source"]["voltage"]) / current
        ledger = result.analysis["loss_budget"]
        metal = sum(entry["power"] for entry in ledger["entries"] if entry.get("layer_id") == "pads")
        feed = ledger["categories"]["external_feed"]
        film = sum(entry["power"] for entry in ledger["entries"] if entry.get("layer_id") == "film")
        interface = ledger["categories"]["contact"]
        deembedded = total_resistance - (metal + feed) / current**2
        if deembedded <= 0:
            raise SimulationError("TLM metal/feed deembedding left a nonpositive resistance")
        path = folder / (project.name + ".etl")
        save_project(project, path, result)
        restored, saved = load_project(path)
        if saved is None or physics_hash(restored) != result.physics_hash:
            raise SimulationError("TLM solved project failed physical-identity reopening")
        observations.append(
            {
                "gap_m": float(spacing * 1e-6),
                "delivered_current_A": current,
                "terminal_resistance_ohm": total_resistance,
                "deembedded_resistance_ohm": deembedded,
                "metal_loss_W": metal,
                "external_feed_loss_W": feed,
                "film_loss_W": film,
                "interface_only_loss_W": interface,
                "terminal_power_W": float(result.metrics["terminal_power"]),
                "ledger_power_W": ledger["total_power"],
                "contact_interface_only_equivalent_ohm": interface / current**2,
                "kelvin_gap_resistance_ohm": result.metrics["effective_resistance"],
                "elements": len(result.mesh.triangles),
                "elapsed_seconds": result.elapsed_seconds,
                "execution": result.execution,
                "project_file": path.relative_to(root).as_posix(),
                "physics_hash": result.physics_hash,
                "source_hash": project.layout.sha256,
            }
        )
        cases.append({"spacing_um": float(spacing), "project": project, "result": result})
    reference = finite_contact_reference(gaps * 1e-6)
    fit = fit_transfer_length(gaps * 1e-6, [o["deembedded_resistance_ohm"] for o in observations])
    for observation, analytical in zip(observations, reference["resistance_ohm"]):
        observation["analytical_resistance_ohm"] = analytical
        observation["analytical_relative_error"] = (
            observation["deembedded_resistance_ohm"] - analytical
        ) / analytical
    report = {
        "version": 1,
        "kind": "synthetic_finite_contact_TLM",
        "temperature_K": 300.0,
        "mesh_factor": mesh_factor,
        "width_m": 40e-6,
        "contact_length_m": 60e-6,
        "fit": fit,
        "reference": reference,
        "observations": observations,
        "limitations": [
            "Fixed-temperature linear effective TaN sheet, not calibrated semiconductor contacts.",
            "Uniform sheet resistance under and between contacts is an explicit assumption.",
            "Fitted Rc includes film spreading under the contact and interface loss; interface-only P/I^2 is different.",
            "Actual finite copper and feed losses are subtracted using the disjoint ledger; residual finite-metal field effects remain.",
            "Fit standard errors reflect numerical scatter only; three mesh levels are separate verification.",
            "No CTLM, contact-end extraction, process calibration or experimental uncertainty is claimed.",
        ],
    }
    checkpoint(cancel, progress, 1.0, "Finite-contact TLM fit complete")
    (root / "tlm-report.json").write_bytes(
        (json.dumps(report, indent=2, allow_nan=False) + "\n").encode("utf-8")
    )
    return {"report": report, "cases": cases}
