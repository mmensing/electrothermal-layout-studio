# SPDX-License-Identifier: MPL-2.0
"""Conservative, explicit localization of resistive interface losses."""
from __future__ import annotations

import math

import numpy as np

from etlayout.models import SimulationError


def deposit_interface_heat(project, mesh, result, heat, interface, elements, powers, areas):
    """Deposit exact element-integrated W without altering electrical loss.

    A named sheet is a calibrated depth approximation on identical planar
    support. It neither creates thermal links nor distributes loss by mesh-node
    count. Missing support is rejected rather than clipped or renormalized.
    """
    fraction = interface.heat_to_layer_a_fraction
    if not math.isfinite(fraction) or not 0 <= fraction <= 1:
        raise SimulationError(f"Interface {interface.id}: heat fraction to layer A must be in [0, 1]")
    if interface.heat_layer_id:
        if fraction != .5:
            raise SimulationError(f"Interface {interface.id}: use a named heat layer or an endpoint split, not both")
        destinations = [(interface.heat_layer_id, 1.)]
    else:
        destinations = [(interface.layer_a, fraction), (interface.layer_b, 1. - fraction)]
    deposited = {}
    for layer_id, share in destinations:
        if not share:
            continue
        if not any(layer.id == layer_id for layer in project.layers):
            raise SimulationError(f"Interface {interface.id}: heat-deposition layer {layer_id!r} is missing")
        support = np.asarray(mesh.elements(layer_id), dtype=int)
        if not np.all(np.isin(elements, support)):
            raise SimulationError(f"Interface {interface.id}: heat-deposition layer {layer_id} must cover the complete interface support")
        heat.setdefault(layer_id, np.zeros(len(mesh.triangles)))
        fields = result.fields.setdefault(layer_id, {})
        if "contact_density" not in fields:
            fields["contact_density"] = np.full(len(mesh.triangles), np.nan)
            fields["contact_density"][support] = 0.
        density = powers * share / areas[elements]
        heat[layer_id][elements] += density
        fields["contact_density"][elements] += density
        deposited[layer_id] = float(np.sum(powers * share))
    result.analysis.setdefault("heat_deposition", {})[interface.id] = {
        "model": "named_sheet" if interface.heat_layer_id else "endpoint_partition",
        "power": float(np.sum(powers)), "deposited_by_layer": deposited,
        "assumption": "Thickness-averaged deposition; no resolved internal 3D heat profile",
    }
