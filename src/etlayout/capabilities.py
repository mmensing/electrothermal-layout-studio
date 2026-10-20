# SPDX-License-Identifier: MPL-2.0
"""Qt-free feature identity for clients and generated documentation.

Introduced versions establish source availability, not publication of a binary,
experimental calibration or verification of every supported combination.
"""
from __future__ import annotations

import re
from dataclasses import asdict, dataclass

from etlayout import __version__

ANALYSES = ("electrical", "thermal", "electrothermal", "thermal-transient", "electrothermal-transient")


@dataclass(frozen=True)
class Capability:
    id: str
    title: str
    group: str
    gui_route: str
    analyses: tuple[str, ...]
    limitations: str
    introduced_in: str
    test_references: tuple[str, ...]

    def available_in(self, version: str) -> bool:
        return _version(version) >= _version(self.introduced_in)


def _version(value: str) -> tuple[int, int, int]:
    match = re.fullmatch(r"(\d+)\.(\d+)\.(\d+)(?:[+.-][a-zA-Z0-9.-]+)?", value)
    if not match:
        raise ValueError("Use a semantic version such as 0.4.0 or 0.4.1")
    return tuple(map(int, match.groups()))


# IDs remain stable when titles/menu labels change. References identify relevant
# behavior suites; they are navigation aids, not coverage or CI pass claims.
CAPABILITIES = (
    Capability("layout.import", "GDSII/OASIS hierarchy", "Experiment",
               "File → Import layout…; Layout & Region", ANALYSES,
               "Layout hierarchy does not reduce finite-element cost.", "0.4.0", ("tests/test_geometry.py",)),
    Capability("layout.regions", "Distinct domain, crop and reporting support", "Experiment",
               "Layout & Region; Model; View", ANALYSES,
               "View-only changes preserve physical identity.", "0.4.0", ("tests/test_workflow_maintenance.py",)),
    Capability("layout.vertices", "Project-owned polygon editing", "Experiment",
               "Edit → Edit layout polygon vertices", ANALYSES,
               "Imported source is preserved; polygon editing is not general CAD construction.", "0.4.0", ("tests/test_layout_editor.py",)),
    Capability("stack.materials", "Materials and technology templates", "Experiment",
               "Layers & Materials", ANALYSES,
               "Thickness-averaged 2.5D sheets require explicit interfaces.", "0.4.0", ("tests/test_technology_templates.py",)),
    Capability("stack.thermal-films", "Thermal films and explicit heat placement", "Experiment",
               "Layers & Materials; Contacts & Connections", ANALYSES,
               "Avoid counting effective thermal paths twice.", "0.4.1", ("tests/test_thermal_stacks.py",)),
    Capability("analysis.dc", "Fixed-temperature DC electrical", "Analysis",
               "Header → Analysis type → DC electrical", ("electrical",),
               "Conduction model; no charge storage or switching parasitics.", "0.4.0", ("tests/test_electrical.py",)),
    Capability("analysis.thermal", "Steady thermal and coupled electrothermal", "Analysis",
               "Header → Analysis type", ("thermal", "electrothermal"),
               "Configured material laws and reservoirs determine physical validity.", "0.4.0", ("tests/test_thermal.py",)),
    Capability("analysis.transient", "Thermal/electrothermal transients", "Analysis",
               "Header → Analysis type; Simulation → Time and load settings…", ("thermal-transient", "electrothermal-transient"),
               "Quasi-static electrical loss and thermal storage; no switching physics.", "0.4.0", ("tests/test_transient.py",)),
    Capability("analysis.temperature-limits", "Maximum-temperature engineering acceptance", "Analysis",
               "Operating conditions → Maximum operating temperature…", ANALYSES,
               "Acceptance is distinct from numerical convergence.", "0.4.1", ("tests/test_temperature_limits.py",)),
    Capability("power.ports", "Finite ports, vias and distributed loads", "Power",
               "Contacts & Connections; Studies → Routing & optimization", ("electrical", "electrothermal", "electrothermal-transient"),
               "Projected overlap is not connectivity; device parameters need calibration.", "0.4.0", ("tests/test_power_solver_multiport.py",)),
    Capability("power.routing", "Constrained routing and equal-service optimization", "Power",
               "Studies → Routing & optimization", ("electrical", "thermal", "electrothermal"),
               "Steady operation only; compare delivered service and verified finalists.", "0.4.0", ("tests/test_power_routing.py",)),
    Capability("power.multiport", "Passive multiport export", "Power",
               "Studies → Routing & optimization → Analysis", ("electrical",),
               "Supported passive linear networks only; per-port fidelity is required.", "0.4.0", ("tests/test_power_solver_multiport.py",)),
    Capability("results.maps", "Layer, region, quantity and time maps", "Results",
               "Results → Layer / Quantity / Region", ANALYSES,
               "Display scales preserve saved fields; raw extrema need physical qualification.", "0.4.0", ("tests/test_result_layer_selection.py",)),
    Capability("results.engineering", "Independent evidence and same-support quantities", "Results",
               "Results → Metrics & loss budget → Engineering", ANALYSES,
               "Convergence, conservation, mesh confidence, model range and calibration are separate.", "0.4.1", ("tests/test_engineering_workflow.py",)),
    Capability("results.transient-peaks", "Peak and final transient frames", "Results",
               "Results → Time navigation", ("thermal-transient", "electrothermal-transient"),
               "A cool selected frame does not erase whole-run acceptance failure.", "0.4.1", ("tests/test_engineering_workflow.py",)),
    Capability("studies.convergence", "Mesh and time-step sensitivity studies", "Studies",
               "Studies → Mesh convergence… / Time-step convergence…", ANALYSES,
               "Compare integrals and regularized hotspots; sensitivity is not a rigorous error bound.", "0.4.0", ("tests/test_study_execution.py",)),
    Capability("studies.local-mesh", "Physical local refinement and per-metric evidence", "Studies",
               "Mesh & Checks → Suggest local refinement…", ANALYSES,
               "A suggestion or generated mesh is not proof; retain support and resource limits.", "0.4.1", ("tests/test_local_refinement_gui.py",)),
    Capability("studies.devices", "Configured GaN, SiC and finite-contact studies", "Studies",
               "Studies → Run configured study… / Finite-contact TLM study (E27)…", ("electrical", "electrothermal", "electrothermal-transient"),
               "Synthetic effective models do not establish breakdown or switching performance.", "0.4.1", ("tests/test_device_scenarios.py",)),
    Capability("projects.history", "Embedded projects and read-only run history", "Persistence",
               "File → Save project as…; History", ANALYSES,
               "Apply/Discard/Stay resolves drafts; historical physical settings remain read-only.", "0.4.0", ("tests/test_lazy_history_workflows.py",)),
    Capability("projects.sharing", "Scientific sharing and complete archives", "Persistence",
               "Results → Export task", ANALYSES,
               "Complete archives contain confidential source/configuration; review output scope.", "0.4.0", ("tests/test_explicit_exports.py",)),
    Capability("execution.cpu", "Cancellable background work and shared CPU budget", "Execution",
               "Edit → Execution settings…", ANALYSES,
               "Assembly/studies can parallelize; sparse factorization is serial. No GPU backend.", "0.4.0", ("tests/test_execution.py",)),
    Capability("examples.references", "Forty reference examples", "Learning",
               "File → Ready-to-run examples", ANALYSES,
               "Each example has its own supported modes, validity and resource limits.", "0.4.0", ("tests/test_example_science.py",)),
    Capability("examples.browser", "Searchable examples, family presets and scenarios", "Learning",
               "File → Browse example library…", ANALYSES,
               "Opening does not solve; named scenarios define inputs; large presets are opt-in.", "0.4.1", ("tests/test_example_library_browser.py",)),
    Capability("workflow.verification", "Direct result-to-verification guidance", "Learning",
               "Results → Check mesh / time-step sensitivity…", ANALYSES,
               "Current applied physics required; historical inspection cannot edit or run.", "0.4.1", ("tests/test_workflow_next_steps.py",)),
    Capability("identity.build", "Machine-readable build provenance", "Execution",
               "CLI: etlayout build-info", ANALYSES,
               "Source/build identity does not establish publication or calibration.", "0.4.1", ("tests/test_build_provenance.py",)),
)


def capability(identifier: str) -> Capability:
    try:
        return next(row for row in CAPABILITIES if row.id == identifier)
    except StopIteration as error:
        raise KeyError(identifier) from error


def capability_manifest(*, installed_version: str | None = None) -> dict:
    """Optionally compare an explicitly supplied version, without claiming release."""
    if installed_version is not None:
        _version(installed_version)
    entries = []
    for row in CAPABILITIES:
        record = asdict(row)
        if installed_version is not None:
            record["available_in_version"] = row.available_in(installed_version)
        entries.append(record)
    return {"schema_version": 1, "source_version": __version__,
            "compared_version": installed_version, "entries": entries}
