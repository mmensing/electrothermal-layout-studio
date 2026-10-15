# SPDX-License-Identifier: MPL-2.0
import pytest

from etlayout.connectivity import analyze_connectivity
from etlayout.geometry.prepare import prepare_geometry
from etlayout.models import Interface, PhysicalLayer, Project, RegionSpec, Terminal


def rect(x0, y0, x1, y1):
    return {"exterior": [[v * 1e-6 for v in p] for p in [[x0,y0],[x1,y0],[x1,y1],[x0,y1]]]}


def test_point_contact_disconnected_edge_contact_connected():
    geometry = RegionSpec(polygons=[rect(0,0,10,5), rect(10,5,15,10), rect(-5,0,0,5)])
    project = Project(layers=[PhysicalLayer("m", "Metal", "cu", geometry=geometry)])
    report = analyze_connectivity(project, prepare_geometry(project))
    assert len(report.components) == 2
    assert len(report.nets) == 2
    assert any("Point-only" in d.message for d in report.diagnostics)
    assert any("reference potential" in d.message for d in report.diagnostics)


def test_overlapping_layers_need_declared_contact_and_trace():
    shape = RegionSpec(polygons=[rect(0,0,20,10)])
    project = Project(layers=[PhysicalLayer("a", "A", "cu", geometry=shape),
                              PhysicalLayer("b", "B", "cu", z=2e-6, geometry=shape)],
                      terminals=[Terminal("source", "a", RegionSpec(polygons=[rect(0,0,2,10)]), "ground"),
                                 Terminal("drain", "b", RegionSpec(polygons=[rect(18,0,20,10)]), "voltage", 1)])
    report = analyze_connectivity(project, prepare_geometry(project))
    assert len(report.nets) == 2
    assert report.trace("source", "drain") == []
    project.interfaces = [Interface("via", "a", "b", RegionSpec(polygons=[rect(8,4,12,6)]), "specific", 1e-10)]
    report = analyze_connectivity(project, prepare_geometry(project))
    assert len(report.nets) == 1
    assert [report.components[key].layer_id for key in report.trace("source", "drain")] == ["a", "b"]
    project.interfaces[0].enabled = False
    assert len(analyze_connectivity(project, prepare_geometry(project)).nets) == 2


def test_conflicting_terminals_detected():
    shape = RegionSpec(polygons=[rect(0,0,10,10)])
    project = Project(layers=[PhysicalLayer("m", "Metal", "cu", geometry=shape)],
                      terminals=[Terminal("a", "m", shape, "ground"), Terminal("b", "m", shape, "voltage", 1.)])
    report = analyze_connectivity(project, prepare_geometry(project))
    assert any("Conflicting" in d.message for d in report.diagnostics)
    components = list(report.to_dict()["components"].values())
    assert len(components) == 1
    assert components[0]["area_m2"] == pytest.approx(100e-12)


def transient_current_project():
    from etlayout.models import TransientDrive, TransientSettings, Waveform
    shape = RegionSpec(polygons=[rect(0, 0, 20, 10)])
    project = Project(layers=[PhysicalLayer("m", "Metal", "cu", geometry=shape)],
        terminals=[Terminal("a", "m", RegionSpec(polygons=[rect(0, 0, 2, 10)]), "current", 1.),
                   Terminal("b", "m", RegionSpec(polygons=[rect(18, 0, 20, 10)]), "current", -2.)])
    project.solver.mode = "electrothermal"
    project.solver.floating_policy = "reference"
    project.transient = TransientSettings(enabled=True, stop_time=1., time_step=1., output_interval=1.,
        drives=[TransientDrive("terminals.a.value", Waveform(kind="pulse", low=0., high=.25,
                    delay=.2, width=.01, period=0.)),
                TransientDrive("terminals.b.value", Waveform(kind="pulse", low=0., high=-.25,
                    delay=.2, width=.01, period=0.))])
    return project


def test_transient_connectivity_uses_effective_currents_without_mutating_static_setup():
    project = transient_current_project()
    report = analyze_connectivity(project, prepare_geometry(project))
    assert not any(d.severity == "error" for d in report.diagnostics)
    assert [terminal.value for terminal in project.terminals] == [1., -2.]


def test_transient_connectivity_detects_imbalance_inside_short_pulse():
    project = transient_current_project()
    project.transient.drives[1].waveform.delay = .205
    report = analyze_connectivity(project, prepare_geometry(project))
    errors = [d.message for d in report.diagnostics if d.severity == "error"]
    assert len(errors) == 1 and "unbalanced imposed total current" in errors[0]
    assert "at t=" in errors[0] and "drive limit" in errors[0]


def test_resumed_connectivity_excludes_consumed_left_limit_but_checks_future_intervals():
    from etlayout.models import Waveform
    project = transient_current_project()
    project.transient.drives[1].waveform = Waveform(kind="constant", value=0.)
    geometry = prepare_geometry(project)
    full = analyze_connectivity(project, geometry)
    assert any("unbalanced imposed" in d.message for d in full.diagnostics)
    # Resume exactly at the first pulse's falling edge: its left-hand drive was
    # consumed by the source history, while the new segment has balanced zero A.
    resumed = analyze_connectivity(project, geometry, start_time=.21)
    assert not any(d.severity == "error" for d in resumed.diagnostics)
    project.transient.drives[0].waveform.delay = .4
    resumed = analyze_connectivity(project, geometry, start_time=.21)
    assert any("unbalanced imposed" in d.message and "t=0.4" in d.message for d in resumed.diagnostics)


def test_connectivity_does_not_check_drive_after_stop_boundary():
    from etlayout.models import Waveform
    project = transient_current_project()
    project.transient.drives[0].waveform.delay = project.transient.stop_time
    project.transient.drives[1].waveform = Waveform(kind="constant", value=0.)
    report = analyze_connectivity(project, prepare_geometry(project), start_time=.5)
    assert not any(d.severity == "error" for d in report.diagnostics)


def test_transient_voltage_conflict_checks_piecewise_linear_interior_knots():
    from etlayout.models import TransientDrive, TransientSettings, Waveform
    shape = RegionSpec(polygons=[rect(0, 0, 10, 10)])
    project = Project(layers=[PhysicalLayer("m", "Metal", "cu", geometry=shape)],
        terminals=[Terminal("a", "m", shape, "ground"), Terminal("b", "m", shape, "voltage", 0.)])
    project.solver.mode = "electrothermal"
    project.transient = TransientSettings(enabled=True, stop_time=1., time_step=1., output_interval=1.,
        drives=[TransientDrive("terminals.b.value", Waveform(kind="piecewise_linear",
            times=[0., .3, 1.], values=[0., .5, 0.]))])
    report = analyze_connectivity(project, prepare_geometry(project))
    assert any("Conflicting potentials" in d.message and "t=0.3" in d.message for d in report.diagnostics)
    project.transient.drives[0].waveform.values = [0., 0., 0.]
    project.terminals[1].value = 2.
    report = analyze_connectivity(project, prepare_geometry(project))
    assert not any("Conflicting potentials" in d.message for d in report.diagnostics)
