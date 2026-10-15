# SPDX-License-Identifier: MPL-2.0
"""Workflow decisions use real model contracts, diagnostics and numerical runs."""
from copy import deepcopy

import pytest
from solver_helpers import rectangle

from etlayout.connectivity.analyze import analyze_connectivity
from etlayout.geometry.prepare import prepare_geometry
from etlayout.models import (
    Diagnostic,
    PhysicalPort,
    PortLanding,
    Project,
    RegionSpec,
    ThermalBoundary,
    TransientDrive,
    Waveform,
)
from etlayout.solvers import solve_electrical
from etlayout.workflow import (
    ANALYSES,
    STAGES,
    CommandRegistry,
    CommandUnavailable,
    DiagnosticBatch,
    analysis_key,
    assess,
    diagnostic_issue,
    document_identity,
    set_analysis,
)


@pytest.fixture
def metal():
    project, mesh = rectangle(nx=3, ny=2)
    project.layers[0].geometry = RegionSpec(polygons=[{
        "exterior": [[0., 0.], [10e-6, 0.], [10e-6, 2e-6], [0., 2e-6]], "holes": []}])
    project.terminals[0].geometry = RegionSpec(line=[[0., 0.], [0., 2e-6]])
    project.terminals[1].geometry = RegionSpec(line=[[10e-6, 0.], [10e-6, 2e-6]])
    return project, mesh


@pytest.mark.parametrize("choice", [entry.key for entry in ANALYSES])
def test_analysis_switch_preserves_all_dormant_settings(metal, choice):
    project, _ = metal
    project.transient.stop_time = .03
    project.transient.drives = [TransientDrive("terminals.right.value", Waveform(kind="pulse"))]
    project.thermal_boundaries = [ThermalBoundary("cooling", "metal")]
    project.solver.thermal_include_joule = True
    original = deepcopy(project.to_dict())
    set_analysis(project, choice)
    assert analysis_key(project) == choice
    after = project.to_dict()
    # The complete remaining document, including saved waveforms and source
    # footprints, must survive. This is stronger than checking a few UI fields.
    after["solver"]["mode"] = original["solver"]["mode"]
    after["transient"]["enabled"] = original["transient"]["enabled"]
    assert after == original


def test_analysis_unknown_is_atomic_and_invalid_combination_is_explicit(metal):
    project, _ = metal
    original = project.to_dict()
    with pytest.raises(ValueError, match="Unknown analysis"):
        set_analysis(project, "RF")
    assert project.to_dict() == original
    project.transient.enabled = True
    with pytest.raises(ValueError, match="Unsupported analysis"):
        analysis_key(project)


def test_empty_project_guidance_does_not_claim_validated_ready():
    state = assess(Project())
    assert not state.ready
    assert set(state.stages) == {stage.key for stage in STAGES}
    assert state.stages["layout"].status == "Needs input"
    assert state.stages["layers"].status == "Needs input"
    assert state.stages["run"].status == "Blocked"
    assert not state.checked


def test_dc_does_not_require_thermal_sink_but_coupled_does(metal):
    project, _ = metal
    dc = assess(project)
    assert dc.ready
    assert not dc.checked
    assert dc.stages["run"].status == "Needs review"
    assert "does not require a thermal sink" in dc.stages["conditions"].reason
    set_analysis(project, "electrothermal")
    coupled = assess(project)
    assert [issue.key for issue in coupled.blockers] == ["setup.thermal_sink"]
    project.power.thermal_reference_temperature = 300.
    assert "setup.thermal_sink" in {issue.key for issue in assess(project).blockers}
    project.thermal_boundaries = [ThermalBoundary("sink", "metal", kind="temperature", value=300.)]
    assert assess(project).ready


def test_prescribed_thermal_needs_no_electrical_ports_one_way_joule_does(metal):
    project, _ = metal
    project.terminals.clear()
    project.thermal_boundaries = [ThermalBoundary("sink", "metal", kind="temperature", value=300.)]
    set_analysis(project, "thermal")
    state = assess(project)
    assert state.ready
    assert state.stages["connections"].status == "Not needed"
    project.solver.thermal_include_joule = True
    assert "setup.electrical_input" in {issue.key for issue in assess(project).blockers}


def test_adiabatic_transient_is_allowed_but_steady_initialization_needs_sink(metal):
    project, _ = metal
    project.terminals.clear()
    set_analysis(project, "thermal_transient")
    transient = assess(project)
    assert transient.ready
    assert "adiabatic" in transient.stages["conditions"].reason
    project.transient.initialization = "steady"
    assert "setup.thermal_sink" in {issue.key for issue in assess(project).blockers}


def test_port_owned_footprint_is_not_an_implicit_voltage_reference(metal):
    project, _ = metal
    project.power.ports = [PhysicalPort("input", [PortLanding("left")], "current", .01),
                           PhysicalPort("return", [PortLanding("right")], "current", -.01)]
    # The placeholder terminal kinds must not override physical port conditions.
    state = assess(project)
    assert "setup.electrical_reference" in {issue.key for issue in state.blockers}
    project.power.ports[1].kind = "ground"
    assert assess(project).ready


def test_explicit_backend_gauge_policy_does_not_invent_reference_blocker(metal):
    project, _ = metal
    project.terminals[0].kind = project.terminals[1].kind = "current"
    project.terminals[0].value = -project.terminals[1].value
    project.solver.floating_policy = "reference"
    state = assess(project)
    assert "setup.electrical_reference" not in {issue.key for issue in state.blockers}
    assert not state.checked


def test_real_connectivity_diagnostic_keeps_target_bounds_and_stable_identity(metal):
    project, _ = metal
    project.terminals[0].kind = project.terminals[1].kind = "current"
    project.terminals[0].value = -project.terminals[1].value
    geometry = prepare_geometry(project)
    diagnostics = analyze_connectivity(project, geometry).diagnostics
    batch = DiagnosticBatch(document_identity(project), tuple(diagnostics))
    state = assess(project, geometry, batch)
    missing = next(issue for issue in state.issues if "no reference potential" in issue.message)
    assert missing.blocking
    assert missing.object_id.startswith("metal")
    assert (missing.stage, missing.field) == ("conditions", "terminals")
    assert missing.bounds == (0., 0., 10e-6, 2e-6)
    assert missing.detail == missing.message
    assert state.checked
    assert missing.key in {issue.key for issue in assess(project, geometry, batch).issues}


def test_async_evidence_is_discarded_after_document_edit(metal):
    project, _ = metal
    old = DiagnosticBatch(document_identity(project), (Diagnostic("error", "Old contact diagnostic", "right"),))
    project.terminals[1].value = 2.
    state = assess(project, diagnostics=old)
    assert not state.checked
    assert all("Old contact" not in issue.message for issue in state.issues)
    assert any(issue.key == "checks.outdated" for issue in state.issues)
    assert state.ready  # obsolete blockers must not block the newly edited model


def test_current_empty_batch_proves_checks_ran_without_claiming_mesh_verification(metal):
    project, _ = metal
    batch = DiagnosticBatch(document_identity(project))
    state = assess(project, diagnostics=batch, mesh_current=True)
    assert state.ready and state.checked
    assert state.stages["run"].status == "Ready"
    assert state.stages["mesh"].status == "Ready"
    assert "separate evidence" in state.stages["mesh"].reason


def test_stale_geometry_diagnostics_do_not_reappear_and_plain_current_diagnostics_work(metal):
    project, _ = metal
    geometry = prepare_geometry(project)
    geometry.diagnostics.append(Diagnostic("error", "Old domain cut", "metal", [0., 0., 1., 1.]))
    identity = document_identity(project)
    project.name += " updated"
    state = assess(project, geometry, geometry_identity=identity)
    assert "Old domain cut" not in [issue.message for issue in state.issues]
    current = Diagnostic("warning", "Review mesh resolution", "metal", [0., 0., 1., 1.])
    current_state = assess(project, diagnostics=[current], diagnostics_identity=document_identity(project))
    assert current_state.checked
    assert next(issue for issue in current_state.issues if issue.message == current.message).bounds == (0., 0., 1., 1.)


def test_backend_diagnostics_deduplicate_and_keep_nonblocking_warnings(metal):
    project, _ = metal
    geometry = prepare_geometry(project)
    warning = Diagnostic("warning", "ROI truncates conducting layer metal", "metal", [0., 0., 1., 1.])
    geometry.diagnostics.append(warning)
    state = assess(project, geometry, [warning, warning])
    issues = [issue for issue in state.issues if issue.message == warning.message]
    assert len(issues) == 1
    assert issues[0].stage == "layout"
    assert not issues[0].blocking
    assert state.stages["layout"].status == "Needs review"


def test_typed_validation_error_targets_correct_inspector_and_nonfinite_is_safe(metal):
    project, _ = metal
    project.layers[0].thickness = float("nan")
    state = assess(project)
    assert not state.ready
    issue = next(issue for issue in state.issues if "thickness" in issue.message)
    assert issue.stage == "layers"
    assert state.source_identity.startswith("invalid:")
    project.layers[0].thickness = .5e-6
    project.power.ports = [PhysicalPort("port", [PortLanding("missing")])]
    state = assess(project)
    assert any(issue.object_id == "port" and issue.field == "landings" for issue in state.blockers)


def test_saved_initial_state_requirement_is_not_hidden(metal):
    project, _ = metal
    set_analysis(project, "thermal_transient")
    project.transient.initialization = "saved"
    state = assess(project)
    assert any(issue.field == "transient" and issue.blocking for issue in state.issues)
    assert not any(issue.field == "transient" and issue.blocking for issue in
                   assess(project, initial_state_supplied=True).issues)


def test_real_solved_result_freshness_is_independent_of_mesh_evidence(metal):
    project, mesh = metal
    result = solve_electrical(project, mesh)
    assert result.converged
    assert result.metrics["effective_resistance"] == pytest.approx(.2, rel=1e-10)
    state = assess(project, result=result)
    assert state.result_fresh
    assert state.stages["results"].status == "Ready"
    assert "separate study evidence" in state.stages["results"].reason
    assert state.stages["mesh"].status == "Needs input"
    project.layers[0].visible = False
    project.display_roi = RegionSpec(polygons=[{"exterior": [[0., 0.], [1e-6, 0.], [1e-6, 1e-6]], "holes": []}])
    assert assess(project, result=result).result_fresh
    project.materials["copper"].resistivity.value *= 2
    stale = assess(project, result=result)
    assert not stale.result_fresh
    assert stale.stages["results"].status == "Out of date"
    # The workflow is read-only; stale inspection must retain its original fields.
    assert result.metrics["effective_resistance"] == pytest.approx(.2, rel=1e-10)


def test_diagnostic_routing_preserves_original_object_and_original_detail(metal):
    project, _ = metal
    diagnostic = Diagnostic("error", "Transient time-step budget invalid", "transient")
    issue = diagnostic_issue(project, diagnostic)
    assert issue.stage == "conditions" and issue.field == "transient"
    assert issue.detail == diagnostic.message
    assert issue.object_id == diagnostic.object_id


def test_readiness_never_reads_unbound_source_bytes(metal, tmp_path, monkeypatch):
    project, mesh = metal
    result = solve_electrical(project, mesh)
    source = tmp_path / "unbound.gds"
    source.write_bytes(b"source identity belongs to the background worker")
    project.layout.path = str(source)

    def forbidden_hash(*_):
        raise AssertionError("Readiness must not read source-file bytes")

    monkeypatch.setattr("etlayout.io.project.file_hash", forbidden_hash)
    state = assess(project, result=result)
    assert state.result_fresh is None
    assert state.stages["results"].status == "Needs review"
    assert "source-identity" in state.stages["results"].reason


def test_registry_aliases_use_same_guard_and_handler_with_current_enablement():
    registry = CommandRegistry()
    calls = []
    state = {"reason": "Apply or discard the pending inspector edits."}
    registry.register("simulation.run", "Run analysis", lambda revision: calls.append(revision),
                      enabled=lambda: state["reason"], shortcut="F5", stage="run")
    menu_alias = keyboard_alias = issue_alias = lambda: registry.execute("simulation.run", "revision-7")
    for alias in (menu_alias, keyboard_alias, issue_alias):
        with pytest.raises(CommandUnavailable, match="pending inspector"):
            alias()
    assert not calls and not registry.available("simulation.run")
    state["reason"] = ""
    keyboard_alias()
    assert calls == ["revision-7"]
    assert registry.get("simulation.run").shortcut == "F5"
    assert [command.key for command in registry] == ["simulation.run"]


def test_registry_rejects_duplicates_invalid_enablement_and_unknown_commands():
    registry = CommandRegistry()
    registry.register("view.fit", "Fit layout", lambda: 12, enabled=False)
    assert registry.reason("view.fit")
    with pytest.raises(CommandUnavailable):
        registry.execute("view.fit")
    with pytest.raises(ValueError):
        registry.register("view.fit", "Second fit", lambda: None)
    with pytest.raises(TypeError):
        registry.register("bad", "No handler", None)
    with pytest.raises(TypeError):
        registry.register("bad", "Bad guard", lambda: None, enabled="Disabled")
    with pytest.raises(KeyError):
        registry.execute("not.registered")
    registry.register("bad.return", "Bad return", lambda: None, enabled=lambda: None)
    with pytest.raises(TypeError):
        registry.reason("bad.return")
    registry.register("ok", "Enabled", lambda value=2: value * 3)
    assert registry.execute("ok", value=4) == 12
