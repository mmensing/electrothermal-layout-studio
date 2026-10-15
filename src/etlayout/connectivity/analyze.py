# SPDX-License-Identifier: MPL-2.0
"""Inspectable connectivity extraction and pre-solve network diagnostics."""
from __future__ import annotations

from collections import deque
from dataclasses import asdict, dataclass, field, replace
from itertools import combinations

import numpy as np
from shapely import STRtree

from etlayout.geometry.prepare import polygon_parts
from etlayout.models import Diagnostic, PreparedGeometry, Project, checkpoint


@dataclass
class Component:
    id: str
    layer_id: str
    geometry: object
    net: str = ""

    def to_dict(self):
        return dict(id=self.id, layer_id=self.layer_id, net=self.net,
                    bounds=list(self.geometry.bounds), area_m2=self.geometry.area)


@dataclass
class ConnectivityReport:
    components: dict[str, Component] = field(default_factory=dict)
    nets: dict[str, list[str]] = field(default_factory=dict)
    terminal_nets: dict[str, list[str]] = field(default_factory=dict)
    interfaces: dict[str, list[tuple[str, str]]] = field(default_factory=dict)
    diagnostics: list[Diagnostic] = field(default_factory=list)
    adjacency: dict[str, set[str]] = field(default_factory=dict)
    terminal_components: dict[str, list[str]] = field(default_factory=dict)

    def to_dict(self):
        return dict(components={k: v.to_dict() for k, v in self.components.items()},
                    nets=self.nets, terminal_nets=self.terminal_nets,
                    interfaces=self.interfaces, diagnostics=[asdict(d) for d in self.diagnostics])

    def trace(self, terminal_a: str, terminal_b: str) -> list[str]:
        """Return a shortest component path between terminals, or [] for an open."""
        targets = set(self.terminal_components.get(terminal_b, []))
        queue = deque((key, [key]) for key in self.terminal_components.get(terminal_a, []))
        visited = set()
        while queue:
            key, path = queue.popleft()
            if key in targets:
                return path
            if key in visited:
                continue
            visited.add(key)
            for other in sorted(self.adjacency[key] - visited):
                queue.append((other, path + [other]))
        return []


def analyze_connectivity(project: Project, geometry: PreparedGeometry, progress=None, cancel=None,
                         *, start_time: float = 0.) -> ConnectivityReport:
    """Extract robust same-layer components and declared interlayer electrical nets.

    Edge sharing and overlap are merged by geometry preparation. Point-touching
    polygons remain distinct components and produce an actionable warning.
    An equipotential terminal explicitly joins the components its geometry spans.
    Interface ``enabled=False`` is the manual disconnection override; adding a
    declared interface is the manual connection override.
    ``start_time`` limits driven constraints to a continuation interval. Only
    the outgoing drive is checked at its start and the incoming drive at its end.
    """
    result = ConnectivityReport()
    landing_ports = {landing.terminal_id: port for port in project.power.ports for landing in port.landings}
    effective_terminals = [replace(t, kind="floating", value=0.) if t.id in landing_ports else t
                           for t in project.terminals]
    # Each logical source constrains one external node. Landings are connected
    # through that node even when their feed resistance is finite.
    for port in project.power.ports:
        if port.landings:
            effective_terminals = [replace(t, kind=port.kind, value=port.value)
                                   if t.id == port.landings[0].terminal_id else t for t in effective_terminals]
    electrical_required = project.solver.mode != "thermal" or project.solver.thermal_include_joule
    # Current balance and conflicting voltage constraints concern the applied
    # excitation, not a static value superseded by a waveform. The union grid
    # includes every pulse edge and PWL knot. On each interval the constraints
    # are linear, so both endpoint limits bound the complete interval, including
    # pulses shorter than the nominal integration step.
    drives = ({drive.target: drive.waveform for drive in project.transient.drives}
              if project.transient.enabled and electrical_required else {})
    sampled_values = {}
    sample_times = None
    sample_starts = None

    def values(terminal):
        nonlocal sample_times, sample_starts
        if terminal.kind == "ground":
            return 0.
        target = f"terminals.{terminal.id}.value"
        port = landing_ports.get(terminal.id)
        if port and port.landings[0].terminal_id == terminal.id:
            target = f"power.ports.{port.id}.value"
        if target not in drives:
            return terminal.value
        if target not in sampled_values:
            from etlayout.solvers.transient import time_schedule, waveform_value
            if sample_times is None:
                grid = time_schedule(project, start_time=start_time)[0]
                # Each interval contributes its right-hand start and left-hand
                # end. Pre-resume history and post-stop events are outside scope.
                sample_times = np.repeat(grid, 2)[1:-1]
                sample_starts = np.repeat(grid[:-1], 2)
            wave = drives[target]
            samples = np.empty(len(sample_times))
            for index, time_s in enumerate(sample_times):
                if index % 1024 == 0:
                    checkpoint(cancel)
                samples[index] = waveform_value(wave, float(time_s), left_limit=bool(index % 2),
                    interval_start=float(sample_starts[index]) if index % 2 else None)
            sampled_values[target] = samples
        return sampled_values[target]

    def maximum_constraint(value):
        if np.ndim(value) == 0:
            return float(value), ""
        index = int(np.argmax(np.abs(value)))
        side = "left" if index % 2 else "right"
        return float(value[index]), f" at t={sample_times[index]:.9g} s ({side}-hand drive limit)"

    by_layer = {}
    indexes = {}
    for layer in project.layers:
        checkpoint(cancel)
        if layer.role != "conductor":
            continue
        components = []
        for polygon in polygon_parts(geometry.layers[layer.id]):
            from etlayout.routing.geometry import canonical_geometry_id
            key = canonical_geometry_id(polygon, layer.id)
            component = Component(key, layer.id, polygon)
            result.components[key] = component
            result.adjacency[key] = set()
            components.append(component)
        by_layer[layer.id] = components
        tree = indexes[layer.id] = STRtree([component.geometry for component in components])
        for index, first in enumerate(components):
            if index % 128 == 0:
                checkpoint(cancel)
            for other in tree.query(first.geometry, predicate="intersects"):
                if other <= index:
                    continue
                second = components[other]
                crossing = first.geometry.intersection(second.geometry)
                if not crossing.is_empty and crossing.area == 0 and crossing.length == 0:
                    result.diagnostics.append(Diagnostic("warning", f"Point-only contact between {first.id} and {second.id} remains electrically disconnected. Add finite overlap or a physical bridge if intended.", layer.id, list(crossing.bounds)))

    def candidates(layer_id, region):
        if layer_id not in indexes:
            return []
        return [by_layer[layer_id][index] for index in sorted(indexes[layer_id].query(region, predicate="intersects"))]

    def connect(first, second):
        result.adjacency[first].add(second)
        result.adjacency[second].add(first)

    for interface in project.interfaces:
        checkpoint(cancel)
        if not interface.enabled or interface.electrical_kind == "none":
            continue
        region = geometry.interfaces.get(interface.id)
        connections = []
        if region is not None:
            for first in candidates(interface.layer_a, region):
                overlap = region.intersection(first.geometry)
                for second in candidates(interface.layer_b, overlap):
                    if overlap.intersection(second.geometry).area > 0:
                        connect(first.id, second.id)
                        connections.append((first.id, second.id))
        result.interfaces[interface.id] = connections
        if not connections:
            result.diagnostics.append(Diagnostic("error", f"Electrical interface {interface.id} makes no finite-area connection. Check its masks and both layer assignments.", interface.id))
    for terminal in project.terminals:
        checkpoint(cancel)
        if terminal.kind in {"gate", "insulated"}:
            continue
        region = geometry.terminals.get(terminal.id)
        selected = []
        if region is not None:
            for component in candidates(terminal.layer_id, region):
                intersection = region.intersection(component.geometry)
                if intersection.area > 0 or (region.area == 0 and intersection.length > 0):
                    selected.append(component.id)
        result.terminal_components[terminal.id] = selected
        if not selected:
            result.diagnostics.append(Diagnostic("error", f"Terminal {terminal.id} does not contact a conducting component", terminal.id))
        if terminal.kind in {"voltage", "ground", "current", "floating"}:
            for first, second in zip(selected, selected[1:]):
                connect(first, second)
    external_components = {}
    for port in project.power.ports:
        components = [c for landing in port.landings for c in result.terminal_components.get(landing.terminal_id, [])]
        external_components.setdefault(port.external_node or port.id, []).extend(components)
        result.terminal_components[port.id] = components
    for components in external_components.values():
        for first, second in zip(components, components[1:]):
            connect(first, second)
    unseen = set(result.components)
    for first in sorted(result.components):
        if first not in unseen:
            continue
        net = f"net-{len(result.nets) + 1}"
        members, stack = [], [first]
        while stack:
            current = stack.pop()
            if current not in unseen:
                continue
            unseen.remove(current)
            members.append(current)
            result.components[current].net = net
            stack.extend(sorted(result.adjacency[current] & unseen))
        result.nets[net] = sorted(members)
    for key, components in result.terminal_components.items():
        result.terminal_nets[key] = sorted({result.components[c].net for c in components})
    terminals_by_net = {net: [] for net in result.nets}
    for terminal in effective_terminals:
        for net in result.terminal_nets.get(terminal.id, []):
            terminals_by_net[net].append(terminal)
    for net, members in result.nets.items():
        checkpoint(cancel)
        terminals = terminals_by_net[net]
        fixed = [t for t in terminals if t.kind in {"voltage", "ground"}]
        if not fixed:
            if not electrical_required:
                result.diagnostics.append(Diagnostic("info", f"{net} has no electrical reference; electrical conduction is not used in this prescribed-source thermal analysis.", members[0], list(result.components[members[0]].geometry.bounds)))
                continue
            severity = "error" if project.solver.floating_policy == "error" else "warning"
            result.diagnostics.append(Diagnostic(severity, f"{net} has no reference potential ({', '.join(members)}). Assign ground/voltage or explicitly enable per-component gauge references.", members[0], list(result.components[members[0]].geometry.bounds)))
            total = 0.
            for terminal in terminals:
                if terminal.kind == "current":
                    total = total + values(terminal)
                elif terminal.kind == "density":
                    region = geometry.terminals[terminal.id]
                    selected = set(result.terminal_components[terminal.id]) & set(members)
                    parts = [region.intersection(result.components[key].geometry) for key in selected]
                    measure = (sum(part.area for part in parts) if region.area
                               else sum(part.length for part in parts) * project.layer(terminal.layer_id).thickness)
                    total = total + values(terminal) * measure
            total, time_context = maximum_constraint(total)
            if abs(total) > 1e-12:
                result.diagnostics.append(Diagnostic("error", f"Unreferenced {net} has unbalanced imposed total current {total:.6g} A{time_context}", net))
        elif len(terminals) == 1:
            result.diagnostics.append(Diagnostic("warning", f"{net} has only one electrical terminal; it has no driven source-to-drain path", net))
    fixed = [t for t in project.terminals if t.kind in {"voltage", "ground"} and t.id not in landing_ports]
    for first, second in combinations(fixed, 2):
        if first.layer_id != second.layer_id:
            continue
        difference, time_context = maximum_constraint(values(first) - values(second))
        if abs(difference) <= 1e-12:
            continue
        a, b = geometry.terminals.get(first.id), geometry.terminals.get(second.id)
        shared_components = set(result.terminal_components.get(first.id, [])) & set(result.terminal_components.get(second.id, []))
        if shared_components and a is not None and b is not None and a.intersects(b):
            crossing = a.intersection(b)
            result.diagnostics.append(Diagnostic("error" if electrical_required else "warning", f"Conflicting potentials on touching terminals {first.id} and {second.id}{time_context}; move or resize the terminal regions", first.id, list(crossing.bounds)))
    # Explicitly surface projected overlaps with no contact rule, without creating
    # connections: an intended via might have been omitted; insulated gates are valid.
    for first, second in combinations([layer for layer in project.layers if layer.role == "conductor"], 2):
        overlap = geometry.layers[first.id].intersection(geometry.layers[second.id])
        declared = any(i.enabled and i.electrical_kind != "none" and
                       {i.layer_a, i.layer_b} == {first.id, second.id} for i in project.interfaces)
        if overlap.area > 0 and not declared:
            result.diagnostics.append(Diagnostic("info", f"{first.name} and {second.name} overlap in projection but have no declared electrical contact; their potentials are independent. Add a via/contact rule only if intended.", first.id, list(overlap.bounds)))
    checkpoint(cancel, progress, 1., "Connectivity verified")
    return result


# A short discoverable alias for headless consumers.
analyze = analyze_connectivity
