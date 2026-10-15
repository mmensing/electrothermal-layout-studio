# SPDX-License-Identifier: MPL-2.0
"""View-only selection descriptions; no project mutation or solver assumptions."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from etlayout.geometry.prepare import geometry_region, resolve_region
from etlayout.models import SimulationError


@dataclass
class PendingSelection:
    """One requested/effective geometry and explicit intent across entry routes.

    Full-domain construction and view framing are never clipped to the old ROI.
    Physical supports can supply their actual admissible geometry for clipping.
    """
    purpose: str = ""
    region: Any = None
    requested: Any = None
    effective: Any = None
    explicit: bool = False

    def begin(self, purpose):
        if purpose not in {"domain", "view_crop", "measurement", "keepout", "editor"}:
            raise ValueError("Unknown selection purpose")
        self.clear()
        self.purpose = purpose
        self.explicit = True

    def preview(self, region, masks, domain=None, support=None):
        requested = resolve_region(region, masks, domain, clip=False)
        effective = requested if support is None else requested.intersection(support)
        if requested.is_empty or effective.is_empty:
            raise SimulationError("Empty requested or effective geometric support")
        self.requested, self.effective = requested, effective
        self.region = geometry_region(effective)
        return effective

    def clear(self):
        self.region = self.requested = self.effective = None


@dataclass(frozen=True)
class SelectionCandidate:
    """An explicit footprint in the currently displayed geometry revision.

    Component index identifies a displayed polygon, not an electrical net.  The
    caller supplies source identity because flattened geometry cannot recover an
    original hierarchy path or establish electrical connectivity on its own.
    """

    layer_id: str
    layer_name: str
    component_index: int
    geometry: Any = field(repr=False, compare=False)
    z: float | None = None
    role: str = "source mask"
    material: str = ""
    source: str = ""
    instance_path: str = ""
    provenance: dict = field(default_factory=dict, compare=False)

    @property
    def label(self) -> str:
        parts = [self.layer_name, self.role]
        if self.z is not None:
            parts.append(f"z {self.z * 1e6:g} µm")
        if self.material:
            parts.append(self.material)
        parts.append(f"footprint {self.component_index + 1}")
        if self.instance_path:
            parts.append(self.instance_path)
        if self.source:
            parts.append(self.source)
        return " · ".join(parts)
