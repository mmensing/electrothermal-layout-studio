# SPDX-License-Identifier: MPL-2.0
"""Sequential mesh sensitivity of the complete fixed-temperature port operator.

This is a discretization check, separate from linear-solve convergence and from
the resistor export's serialization fidelity. No absolute conductance cutoff is
used: every weak transfer term, zero transition and sign transition is retained.
"""
from __future__ import annotations

import time
from copy import deepcopy

import numpy as np

from etlayout.io.project import configuration_hash, physics_hash
from etlayout.models import Cancelled, SimulationError, checkpoint
from etlayout.power.multiport import extract_multiport
from etlayout.simulation.runner import prepare_mesh


def _entrywise_change(coarse, fine, tolerance):
    """Symmetric relative changes without an absolute floor hiding weak ports."""
    coarse, fine = np.asarray(coarse, dtype=float), np.asarray(fine, dtype=float)
    if coarse.shape != fine.shape or coarse.ndim != 2 or not np.isfinite([coarse, fine]).all():
        raise SimulationError("Mesh comparison requires equally sized finite port matrices")
    denominator = np.maximum(np.abs(coarse), np.abs(fine))
    # Scale before subtracting, avoiding overflow even for opposite finite signs.
    relative = np.zeros_like(denominator)
    active = denominator != 0
    relative[active] = np.abs(coarse[active] / denominator[active] - fine[active] / denominator[active])
    zeros = (coarse == 0) != (fine == 0)
    signs = (np.signbit(coarse) != np.signbit(fine)) & (coarse != 0) & (fine != 0)
    return {"relative_changes": relative.tolist(),
            "maximum_relative_change": float(relative.max(initial=0.)),
            "zero_changes": np.argwhere(zeros).tolist(), "sign_changes": np.argwhere(signs).tolist(),
            "within_tolerance": bool(np.all(relative <= tolerance) and not zeros.any() and not signs.any())}


def _scaled_project(project, size):
    candidate = deepcopy(project)
    factor = size / project.mesh.max_size
    candidate.mesh.max_size = size
    candidate.mesh.min_size *= factor
    candidate.mesh.contact_size *= factor
    for region in candidate.mesh.refinements:
        region.size *= factor
        region.axis_sizes = [value * factor for value in region.axis_sizes]
        region.grading_length *= factor
    for layer in candidate.layers:
        if layer.mesh_size is not None:
            layer.mesh_size *= factor
    return candidate


def run_multiport_mesh_study(project, mesh_sizes=None, port_ids=None, reference=None, temperatures=None,
                             relative_tolerance=.02, *, progress=None, cancel=None):
    """Extract 3–5 successively finer meshes without changing the working project.

    Sizes are maximum element lengths in meters. Local/contact/layer sizes scale
    by the same factor. The project's element and combined DOF budgets remain in
    force on every level; levels execute sequentially. A spatial temperature
    field cannot be transferred between meshes by this workflow and is rejected
    explicitly. A positive scalar temperature is held identical on every mesh.

    ``network`` is the actual finest successful extraction. ``converged`` means
    every requested extraction passed its numerical checks; ``mesh_verified``
    additionally requires two successive distinct refinements to pass *every*
    conductance and driving/transfer-resistance entry. It is not an error bound,
    material calibration or an electromagnetic/switching-model validation.
    """
    started = time.perf_counter()
    checkpoint(cancel)
    if temperatures is not None and not np.isscalar(temperatures):
        raise SimulationError("Passive network mesh studies do not project spatial temperature fields between meshes. "
                              "Choose an explicit uniform fixed temperature, or extract the retained field on its original mesh.")
    fixed = project.solver.initial_temperature if temperatures is None else temperatures
    try:
        fixed = float(fixed)
        tolerance = float(relative_tolerance)
        base_size = float(project.mesh.max_size)
        sizes = [2 * base_size, base_size, base_size / 2] if mesh_sizes is None else [float(value) for value in mesh_sizes]
    except (TypeError, ValueError, OverflowError) as error:
        raise SimulationError("Mesh study requires 3–5 finite positive sizes in meters and a finite relative tolerance") from error
    if not np.isfinite(fixed) or fixed <= 0:
        raise SimulationError("Mesh study fixed temperature must be positive and finite in K")
    if (not 3 <= len(sizes) <= 5 or not np.isfinite(sizes).all() or min(sizes) <= 0
            or any(fine >= coarse for coarse, fine in zip(sizes, sizes[1:]))):
        raise SimulationError("Passive network mesh study needs 3–5 strictly decreasing finite positive sizes in meters")
    if not np.isfinite(base_size) or base_size <= 0 or not np.isfinite(tolerance) or not 0 < tolerance < 1:
        raise SimulationError("Mesh study needs a positive source mesh size and a relative tolerance between 0 and 1")
    selected = list(port_ids) if port_ids is not None else [port.id for port in project.power.ports]
    existing_ports = {port.id for port in project.power.ports}
    if not 2 <= len(selected) <= 128 or len(set(selected)) != len(selected) or set(selected) - existing_ports:
        raise SimulationError("Passive network mesh study requires 2–128 distinct existing physical port IDs")
    chosen_reference = reference if reference is not None else (selected[-1] if selected else None)
    if chosen_reference not in selected:
        raise SimulationError("Passive network mesh study reference must be a selected physical port")
    study = {"schema_version": 1, "model": "fixed-temperature passive resistive DC mesh study",
             "status": "not-established", "converged": False, "mesh_verified": False,
             "source_configuration_hash": configuration_hash(project), "source_physics_hash": physics_hash(project),
             "port_ids": selected, "reference": chosen_reference,
             "temperature": {"kind": "uniform_fixed", "temperature_K": fixed},
             "requested_sizes_m": sizes, "relative_tolerance": tolerance,
             "relative_change_definition": "abs(coarse/max(abs(coarse),abs(fine)) - fine/max(abs(coarse),abs(fine))); both zero gives zero; no absolute floor",
             "resource_budgets": {"max_elements_per_level": project.mesh.max_elements,
                                  "max_combined_dofs_per_level": project.mesh.max_dofs,
                                  "maximum_levels": 5, "simultaneous_levels": 1},
             "levels": [], "comparisons": [], "network": None,
             "limitations": "Two successive mesh differences are sensitivity evidence, not an asymptotic error bound. "
                            "No spatial-temperature projection, material calibration, switching, RLC or EM claim."}
    networks = []
    for index, size in enumerate(sizes):
        checkpoint(cancel, progress, index / len(sizes), f"Passive network mesh level {index + 1}/{len(sizes)}")
        level_started = time.perf_counter()
        candidate = _scaled_project(project, size)
        candidate.solver.initial_temperature = fixed
        try:
            def level_progress(fraction, message, index=index):
                checkpoint(cancel, progress, (index + min(max(fraction, 0.), 1.) * .7) / len(sizes),
                           f"Mesh level {index + 1}/{len(sizes)}: {message}")
            _, mesh = prepare_mesh(candidate, level_progress, cancel=cancel)
            combined_dofs = sum(len(np.unique(mesh.triangles[mesh.elements(layer.id)]))
                                * (2 if layer.role == "conductor" else 1) for layer in candidate.layers)
            if len(mesh.triangles) > candidate.mesh.max_elements or combined_dofs > candidate.mesh.max_dofs:
                raise SimulationError("Prepared mesh exceeds this study's element or combined DOF budget; increase mesh sizes or review budgets")
            checkpoint(cancel, progress, (index + .75) / len(sizes), f"Extracting network on mesh level {index + 1}")
            network = extract_multiport(candidate, mesh, selected, chosen_reference, temperatures=fixed, cancel=cancel)
        except Cancelled:
            raise
        except SimulationError as error:
            study["failure"] = {"level": index, "mesh_max_size_m": size, "message": str(error)}
            study["reason"] = "A requested level could not be established; inspect the recorded failure and resource budgets."
            break
        # The fine mesh keeps its own calculation identity. These separate
        # source bindings let a GUI retire it when the working source changes.
        network["source_physics_hash"] = study["source_physics_hash"]
        network["source_configuration_hash"] = study["source_configuration_hash"]
        networks.append(network)
        study["levels"].append({"index": index, "mesh_max_size_m": size, "mesh": network["mesh"],
                                "combined_dofs": combined_dofs, "configuration_hash": network["configuration_hash"],
                                "conductance_matrix": network["conductance_matrix"],
                                "validation": network["validation"], "elapsed_seconds": time.perf_counter() - level_started})
        study["network"] = network
        # Do not retain geometry/mesh/field arrays for all levels simultaneously.
        del mesh
    study["converged"] = len(networks) == len(sizes)
    if len(networks) >= 2:
        matrices = [np.asarray(network["conductance_matrix"]) for network in networks]
        # One strong reference shared across every comparison avoids grounding a
        # weak port and ill-conditioning an otherwise strong subnetwork.
        stable = int(np.argmax(np.diag(matrices[-1])))
        retained = [index for index in range(len(selected)) if index != stable]
        study["comparison_reference"] = selected[stable]
        study["comparison_port_ids"] = [selected[index] for index in retained]
        try:
            resistances = [np.linalg.solve(matrix[np.ix_(retained, retained)], np.eye(len(retained))) for matrix in matrices]
            if not np.isfinite(resistances).all():
                raise np.linalg.LinAlgError("Nonfinite comparison resistance")
            for level, resistance in zip(study["levels"], resistances):
                level["comparison_resistance_matrix_ohm"] = resistance.tolist()
            for index in range(1, len(networks)):
                checkpoint(cancel)
                conductance = _entrywise_change(matrices[index - 1], matrices[index], tolerance)
                resistance = _entrywise_change(resistances[index - 1], resistances[index], tolerance)
                previous, current = study["levels"][index - 1]["mesh"], study["levels"][index]["mesh"]
                refined = current["elements"] > previous["elements"] and current["identity"] != previous["identity"]
                study["comparisons"].append({"coarse_level": index - 1, "fine_level": index,
                    "conductance": conductance, "resistance": resistance, "distinct_refinement": refined,
                    "within_tolerance": conductance["within_tolerance"] and resistance["within_tolerance"] and refined})
        except np.linalg.LinAlgError as error:
            study["comparison_error"] = "Stable-reference resistance comparison unavailable: " + str(error)
    if study["converged"] and len(study["comparisons"]) >= 2:
        last = study["comparisons"][-2:]
        study["mesh_verified"] = all(item["within_tolerance"] for item in last)
        study["status"] = ("verified" if study["mesh_verified"] else
                           "mesh-sensitive" if all(item["distinct_refinement"] for item in last) else "not-established")
        study["reason"] = ("Every port-operator and driving/transfer-resistance entry passed two successive distinct refinements."
                           if study["mesh_verified"] else
                           "The last two comparisons did not both pass every entry and distinct-refinement check; inspect weak terms, zero/sign changes and refine further.")
    elif "reason" not in study:
        study["reason"] = "Two successive comparable refinements were not established; extraction convergence alone is not mesh verification."
    study["elapsed_seconds"] = time.perf_counter() - started
    checkpoint(cancel, progress, 1., "Passive network mesh study " + study["status"])
    return study
