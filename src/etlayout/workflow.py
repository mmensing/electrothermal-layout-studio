# SPDX-License-Identifier: MPL-2.0
"""Qt-independent workflow presentation over the existing scientific model.

This module does not prepare geometry, infer connectivity, or solve equations.
It adapts the inexpensive typed validator and explicitly supplied backend
diagnostics. A ready configuration is not a convergence or verification claim.
"""
from __future__ import annotations

import hashlib
import math
from collections.abc import Callable, Iterable, Iterator
from dataclasses import dataclass, field
from typing import Any

from etlayout.io.project import configuration_hash, physics_hash
from etlayout.models import Diagnostic, Project
from etlayout.technology.validation import validate_project


@dataclass(frozen=True)
class Stage:
    key: str
    title: str
    subtitle: str


STAGES = (
    Stage("layout", "Layout & region", "Define the simulation domain"),
    Stage("layers", "Layers & materials", "Map masks to physical layers"),
    Stage("connections", "Contacts & connections", "Define ports, contacts and probes"),
    Stage("conditions", "Operating conditions", "Set electrical and thermal inputs"),
    Stage("mesh", "Mesh & checks", "Validate and generate the mesh"),
    Stage("run", "Run", "Solve the applied configuration"),
    Stage("results", "Results", "Inspect fields and recorded runs"),
)


@dataclass(frozen=True)
class Analysis:
    key: str
    label: str
    solver_mode: str
    transient: bool
    summary: str


ANALYSES = (
    Analysis("electrical", "DC electrical", "electrical", False,
             "Fixed-state potential and current; electrical inputs and a reference path are required."),
    Analysis("thermal", "Steady-state thermal", "thermal", False,
             "Prescribed heat and heat-removal paths; optional one-way Joule heating is configured separately."),
    Analysis("electrothermal", "Coupled DC electrothermal", "electrothermal", False,
             "Self-consistent electrical heating and temperature-dependent transport."),
    Analysis("thermal_transient", "Thermal transient", "thermal", True,
             "Heat storage with time-dependent loads; optional one-way Joule heating is configured separately."),
    Analysis("electrothermal_transient", "Electrothermal transient", "electrothermal", True,
             "Thermal dynamics with quasi-static electrical solves; no switching-circuit or electromagnetic model."),
)


def analysis_key(project: Project) -> str:
    """Return the supported combined analysis choice, rejecting impossible modes."""
    for analysis in ANALYSES:
        if (analysis.solver_mode, analysis.transient) == (project.solver.mode, project.transient.enabled):
            return analysis.key
    raise ValueError("Unsupported analysis: " + project.solver.mode
                     + (" transient" if project.transient.enabled else ""))


def set_analysis(project: Project, key: str) -> None:
    """Change only mode/activation; preserve dormant assignments and waveforms.

    The host owns the transaction/undo boundary. In particular, choosing thermal
    must not infer one-way Joule heating from whichever result happens to be open.
    """
    selected = next((analysis for analysis in ANALYSES if analysis.key == key), None)
    if selected is None:
        raise ValueError(f"Unknown analysis choice: {key}")
    project.solver.mode = selected.solver_mode
    project.transient.enabled = selected.transient


def document_identity(project: Project) -> str:
    """Cheap revision token for diagnostic messages; does not read layout bytes.

    Presentation edits may conservatively expire checks. An invalid draft can
    still be assessed: the fallback is an identity only, never a persisted hash
    or permission to run a nonfinite scientific configuration.
    """
    try:
        return configuration_hash(project)
    except (ValueError, TypeError):
        return "invalid:" + hashlib.sha256(repr(project.to_dict()).encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class DiagnosticBatch:
    """Backend evidence bound to the document that produced it.

    Construct the identity before launching the worker, not after it completes.
    Even an empty batch represents completed checks at that revision.
    """
    identity: str
    diagnostics: tuple[Diagnostic, ...] = ()


@dataclass(frozen=True)
class WorkflowIssue:
    key: str
    severity: str
    message: str
    detail: str = ""
    object_id: str = ""
    bounds: tuple[float, float, float, float] | None = None
    stage: str = "mesh"
    field: str = ""
    blocking: bool = False


@dataclass(frozen=True)
class StageReadiness:
    status: str
    reason: str
    applicable: bool = True


@dataclass(frozen=True)
class Assessment:
    issues: tuple[WorkflowIssue, ...]
    stages: dict[str, StageReadiness]
    source_identity: str
    checked: bool = False
    result_fresh: bool | None = None

    @property
    def blockers(self) -> tuple[WorkflowIssue, ...]:
        return tuple(issue for issue in self.issues if issue.blocking)

    @property
    def ready(self) -> bool:
        """No known blocker; execution still performs authoritative backend checks."""
        return not self.blockers

    def issues_for(self, stage: str) -> tuple[WorkflowIssue, ...]:
        return tuple(issue for issue in self.issues if issue.stage == stage)


def _destination(project: Project, diagnostic: Diagnostic) -> tuple[str, str]:
    """Route existing diagnostics by object identity before using message hints."""
    key, message = diagnostic.object_id, diagnostic.message.lower()
    if "truncat" in message or "clipped boundar" in message:
        return "layout", "roi"
    if key == "transient":
        return "conditions", "transient"
    if any(item.id == key for item in project.layers) or key in project.materials:
        # A component's missing reference belongs with its inputs, not material properties.
        if "reference potential" in message or "terminal" in message or "current" in message and "unbalanced" in message:
            return "conditions", "terminals"
        return "layers", "material_id" if "material" in message else "layers"
    if any(item.id == key for item in project.interfaces):
        return "connections", "interfaces"
    if any(item.id == key for item in project.terminals):
        return ("conditions", "value") if "value" in message or "potential" in message else ("connections", "geometry")
    if any(item.id == key for item in project.power.ports):
        return "connections", "landings"
    if any(item.id == key for item in (*project.thermal_boundaries, *project.heat_sources)):
        return "conditions", "thermal_boundaries" if "thermal" in message else "heat_sources"
    if any(item.id == key for item in project.mesh.refinements):
        return "mesh", "refinements"
    if "layout" in message or "simulation extent" in message or "domain" in message or "roi" in message:
        return "layout", "roi" if "roi" in message or "domain" in message else "layout"
    if "physical layer" in message or "thickness" in message or "material" in message or "layer z" in message:
        return "layers", "layers"
    if "reference potential" in message or "unbalanced imposed" in message:
        return "conditions", "terminals"
    if any(word in message for word in ("reference potential", "imposed", "thermal", "temperature", "transient")):
        return "conditions", "solver"
    if "terminal" in message or "landing" in message or "contact" in message or "interface" in message:
        return "connections", "terminals"
    return "mesh", "mesh"


def diagnostic_issue(project: Project, diagnostic: Diagnostic) -> WorkflowIssue:
    """Keep the original backend explanation and geometric support in a UI issue."""
    stage, target = _destination(project, diagnostic)
    payload = f"{diagnostic.severity}\0{diagnostic.object_id}\0{diagnostic.message}"
    key = "backend:" + hashlib.sha256(payload.encode("utf-8")).hexdigest()[:20]
    bounds = tuple(diagnostic.bounds) if diagnostic.bounds is not None else None
    return WorkflowIssue(key, diagnostic.severity, diagnostic.message, diagnostic.message,
                         diagnostic.object_id, bounds, stage, target, diagnostic.severity == "error")


def assess(project: Project, geometry=None, diagnostics: Iterable[Diagnostic] | DiagnosticBatch = (),
           mesh_current: bool = False, result=None, *, diagnostics_identity: str | None = None,
           geometry_identity: str | None = None, initial_state_supplied: bool = False) -> Assessment:
    """Assess setup without meshing, source-file I/O or alternative validation.

    Pass a DiagnosticBatch for asynchronous evidence. A plain diagnostic iterable
    is reserved for synchronous/current evidence; callers can bind it using the
    ``diagnostics_identity`` keyword. The host must similarly bind cached geometry
    using ``geometry_identity``. Results freshness uses the established backend
    identity, while convergence remains a separate status.
    """
    identity = document_identity(project)
    issues = [diagnostic_issue(project, diagnostic) for diagnostic in
              validate_project(project, raise_errors=False, initial_state_supplied=initial_state_supplied)]
    stale_checks = False
    if isinstance(diagnostics, DiagnosticBatch):
        diagnostics_identity = diagnostics.identity
        diagnostics = diagnostics.diagnostics
        checked = diagnostics_identity == identity
    else:
        diagnostics = tuple(diagnostics)
        checked = bool(diagnostics) or diagnostics_identity == identity
    if diagnostics_identity is not None and diagnostics_identity != identity:
        stale_checks = True
        checked = False
        diagnostics = ()
    if geometry_identity is not None and geometry_identity != identity:
        geometry = None
        stale_checks = True
    if geometry is not None:
        issues.extend(diagnostic_issue(project, item) for item in geometry.diagnostics)
    issues.extend(diagnostic_issue(project, item) for item in diagnostics)

    def hint(key, message, stage, target, *, blocking=True, severity="error"):
        issues.append(WorkflowIssue(key, severity, message, message, stage=stage, field=target,
                                    blocking=blocking))

    has_extent = bool(project.layout.path or (project.roi and project.roi.polygons)
                      or any(layer.geometry.polygons for layer in project.layers))
    if not has_extent:
        hint("setup.layout", "Import a layout or define layer/domain polygons to establish the simulation extent.",
             "layout", "layout")
    electrical_required = project.solver.mode != "thermal" or project.solver.thermal_include_joule
    thermal_required = project.solver.mode != "electrical"
    owned = {landing.terminal_id for port in project.power.ports for landing in port.landings}
    electrical_objects = [terminal for terminal in project.terminals if terminal.id not in owned]
    electrical_objects.extend(project.power.ports)
    drives = [item for item in electrical_objects if item.kind in {"voltage", "ground", "current", "density"}]
    if electrical_required:
        if not drives:
            hint("setup.electrical_input", "Define an electrical input and its return/reference for this analysis.",
                 "conditions", "terminals")
        elif not any(item.kind in {"voltage", "ground"} for item in drives) and project.solver.floating_policy == "error":
            hint("setup.electrical_reference", "No voltage reference is defined. Assign a return/voltage input, then check connectivity.",
                 "conditions", "terminals")
    sink_required = thermal_required and (not project.transient.enabled or project.transient.initialization == "steady")
    has_sink = any(boundary.kind in {"temperature", "coefficient", "resistance_area", "resistance"}
                   and math.isfinite(boundary.value) and boundary.value > 0 for boundary in project.thermal_boundaries)
    if sink_required and not has_sink:
        hint("setup.thermal_sink", "Define a heat-removal boundary for the steady thermal solve. A reporting reference is not a sink.",
             "conditions", "thermal_boundaries")
    if project.transient.enabled and project.transient.initialization in {"saved", "resume"} and not initial_state_supplied:
        hint("setup.initial_state", "Bind the saved thermal field before starting or resuming this transient run.",
             "conditions", "transient")
    if stale_checks:
        hint("checks.outdated", "Previous checks belong to an older setup. Validate the applied configuration again.",
             "mesh", "validate", blocking=False, severity="info")

    # Multiple evidence sources may report the same diagnostic. One issue has
    # one stable identity regardless of row ordering or repeated validation.
    unique = {issue.key: issue for issue in issues}
    ordered = tuple(sorted(unique.values(), key=lambda issue:
                           ({"error": 0, "warning": 1, "info": 2}.get(issue.severity, 3), issue.stage, issue.key)))
    stages = {}
    for stage in STAGES:
        own = [issue for issue in ordered if issue.stage == stage.key]
        blockers = [issue for issue in own if issue.blocking]
        if blockers:
            stages[stage.key] = StageReadiness("Blocked", blockers[0].message)
        elif any(issue.severity == "warning" for issue in own):
            stages[stage.key] = StageReadiness("Needs review", own[0].message)
        else:
            stages[stage.key] = StageReadiness("Ready", "Applied settings are available; backend checks still govern execution.")
    if not has_extent:
        stages["layout"] = StageReadiness("Needs input", "Import a layout or define the physical domain.")
    elif stages["layout"].status == "Ready":
        stages["layout"] = StageReadiness("Ready", "Simulation scope is configured; browsing and view crop do not change it.")
    if not project.layers:
        stages["layers"] = StageReadiness("Needs input", "Assign intended physical layers. Display-only masks may remain unused.")
    if not electrical_required and not project.interfaces and not any(
            issue.stage == "connections" and issue.blocking for issue in ordered):
        stages["connections"] = StageReadiness("Not needed", "Prescribed-source thermal analysis does not require electrical ports.", False)
    elif stages["connections"].status == "Ready" and not checked:
        stages["connections"] = StageReadiness("Needs review", "Connectivity has not been checked for this setup.")
    if stages["conditions"].status == "Ready":
        reason = ("Electrical inputs are configured; this analysis does not require a thermal sink."
                  if not thermal_required else
                  "Thermal conditions are configured; component heat-removal paths are checked by the backend."
                  if sink_required else
                  "Transient heat storage permits adiabatic operation; review load, initial state and time settings.")
        stages["conditions"] = StageReadiness("Ready", reason)
    if stages["mesh"].status == "Ready":
        stages["mesh"] = StageReadiness("Ready" if mesh_current else "Out of date" if stale_checks else "Needs input",
                                        "Current generated mesh; mesh convergence is separate evidence."
                                        if mesh_current else "Generate or update the mesh; Run can perform backend preparation.")
    blockers = [issue for issue in ordered if issue.blocking]
    stages["run"] = StageReadiness("Blocked" if blockers else "Ready" if checked else "Needs review",
                                    blockers[0].message if blockers else
                                    "Applied setup is ready for execution; convergence is not yet established."
                                    if checked else "Run will perform authoritative checks on an immutable applied setup.")
    fresh = None
    if result is None:
        stages["results"] = StageReadiness("Needs input", "No run selected. Solve or open a recorded result.")
    else:
        recorded_source = result.configuration.get("layout", {})
        source_unbound = (project.layout.path and not project.layout.sha256
                          or recorded_source.get("path") and not recorded_source.get("sha256"))
        if source_unbound:
            # Source preparation verifies bytes in the worker and supplies a
            # digest. A GUI readiness refresh must never hash a large layout.
            fresh = None
        else:
            try:
                fresh = bool(result.configuration) and physics_hash(result.configuration) == physics_hash(project)
            except (ValueError, TypeError):
                fresh = False
        stages["results"] = StageReadiness(
            "Needs review" if fresh is None else "Out of date" if not fresh else "Ready" if result.converged else "Needs review",
            "Result applicability awaits source-identity validation in the background worker." if fresh is None else
            "Recorded run differs from the working setup and remains available for inspection." if not fresh else
            "Selected run converged. Mesh verification requires separate study evidence." if result.converged else
            "Selected run did not converge; inspect its diagnostics before using numerical conclusions.")
    return Assessment(ordered, stages, identity, checked, fresh)


class CommandUnavailable(RuntimeError):
    """The command is known but cannot run in the current application context."""


@dataclass(frozen=True)
class Command:
    key: str
    label: str
    handler: Callable[..., Any] = field(repr=False)
    enabled: Callable[[], bool | str] | bool = field(default=True, repr=False)
    shortcut: str = ""
    stage: str = ""


class CommandRegistry:
    """One guarded callback for every menu/button/keyboard/issue-link alias."""
    def __init__(self):
        self._commands: dict[str, Command] = {}

    def register(self, key: str, label: str, handler: Callable[..., Any], *,
                 enabled: Callable[[], bool | str] | bool = True,
                 shortcut: str = "", stage: str = "") -> Command:
        if not key or key in self._commands:
            raise ValueError(f"Command identifier must be nonempty and unique: {key!r}")
        if not callable(handler):
            raise TypeError("A command requires a callable handler")
        if not isinstance(enabled, bool) and not callable(enabled):
            raise TypeError("Command enablement must be boolean or a callable returning boolean/reason")
        command = Command(key, label, handler, enabled, shortcut, stage)
        self._commands[key] = command
        return command

    def get(self, key: str) -> Command:
        return self._commands[key]

    def __iter__(self) -> Iterator[Command]:
        return iter(self._commands.values())

    def reason(self, key: str) -> str:
        command = self.get(key)
        state = command.enabled() if callable(command.enabled) else command.enabled
        if isinstance(state, str):
            return state
        if not isinstance(state, bool):
            raise TypeError("Command enablement must return a boolean or an unavailable reason")
        return "" if state else f"{command.label} is unavailable in the current context."

    def available(self, key: str) -> bool:
        return not self.reason(key)

    def execute(self, key: str, *args, **kwargs):
        reason = self.reason(key)
        if reason:
            raise CommandUnavailable(reason)
        return self.get(key).handler(*args, **kwargs)
