# SPDX-License-Identifier: MPL-2.0
"""Real export paths treat project metadata as text and SI arrays as numbers."""
from __future__ import annotations

import ast
import csv
import io
import json
from copy import deepcopy
from dataclasses import replace
from html.parser import HTMLParser
from pathlib import Path

import numpy as np
import pytest

from etlayout.io.export import export_profile, export_results, export_study
from etlayout.io.project import configuration_hash, load_project, physics_hash
from etlayout.io.reporting import (
    CONTENT_SECURITY_POLICY,
    csv_dict_writer,
    csv_writer,
    html_image,
    prepare_report_context,
    spreadsheet_cell,
)
from etlayout.models import Diagnostic, PhysicalLayer, PlanarMesh, Project, SimulationResult, TransientHistory
from etlayout.power.analysis import export_power_report
from etlayout.power.multiport import export_multiport
from etlayout.simulation.sweep import SweepRow

ATTACK = '<script src="https://invalid.example/test">harmless</script><img src=x onerror="harmless()"> & \' Ω µm²'


@pytest.fixture
def report_case():
    project = Project(name=ATTACK + " long identifier " * 80, notes=ATTACK + "\nsecond line")
    project.layers = [PhysicalLayer("metal", ATTACK, "synthetic")]
    mesh = PlanarMesh(np.array([[0., 0.], [1e-6, 0.], [0., 1e-6]]), np.array([[0, 1, 2]]),
                      {"layer:metal": np.array([0])})
    result = SimulationResult(mesh, fields={"metal": {"potential": np.array([-2., -1., 0.])}},
        metrics={"total_current": -1.25e-9, "note": ATTACK, "nonfinite_diagnostic": np.nan},
        terminals={ATTACK: {"voltage": -2.5, "current": -1.25e-9}},
        diagnostics=[Diagnostic(ATTACK, ATTACK, ATTACK)], converged=True,
        configuration=project.to_dict(), configuration_hash=configuration_hash(project),
        physics_hash=physics_hash(project), solver_version=ATTACK,
        execution={"metadata": ATTACK}, iterations=[{"diagnostic": ATTACK, "residual": np.nan}])
    return project, result


class ReportParser(HTMLParser):
    def __init__(self, document):
        super().__init__()
        self.tags, self.text = [], []
        self.feed(document)

    def handle_starttag(self, tag, attrs):
        self.tags.append((tag, dict(attrs)))

    def handle_data(self, data):
        self.text.append(data)


def assert_passive_report(document):
    parsed = ReportParser(document)
    assert not {"script", "iframe", "object", "embed", "base", "link", "form"} & {tag for tag, _ in parsed.tags}
    assert not any(key.lower().startswith("on") for _, attrs in parsed.tags for key in attrs)
    assert any(tag == "meta" and attrs.get("http-equiv") == "Content-Security-Policy" and
               attrs.get("content") == CONTENT_SECURITY_POLICY for tag, attrs in parsed.tags)
    assert all(attrs.get("src", "").startswith("data:image/png;base64,") for tag, attrs in parsed.tags if tag == "img")
    assert ATTACK in "".join(parsed.text)
    return parsed


@pytest.mark.parametrize("exporter", ["headless", "gui"])
def test_html_reports_share_escaping_csp_units_and_identity(report_case, qtbot, tmp_path, exporter):
    project, result = report_case
    snapshot, arrays = deepcopy(result.configuration), result.fields["metal"]["potential"].copy()
    if exporter == "gui":
        from etlayout.gui.export import export_html_report
        from etlayout.gui.viewer import LayoutView
        view = LayoutView()
        qtbot.addWidget(view)
        view.resize(800, 500)
        view.show_field(result, "metal", "potential")
        view.fit_field()
        path = tmp_path / "report.html"
        export_html_report(project, result, view, path, ATTACK)
    else:
        path = export_results(result, tmp_path, plots=True, field="potential")
        _, reopened = load_project(tmp_path / "simulation.etl")
        assert reopened.configuration_hash == result.configuration_hash
        np.testing.assert_array_equal(reopened.fields["metal"]["potential"], arrays)
    parsed = assert_passive_report(path.read_text(encoding="utf-8"))
    assert any(tag == "img" and attrs["src"].startswith("data:image/png;base64,") for tag, attrs in parsed.tags)
    text = "".join(parsed.text)
    assert result.configuration_hash in text and result.physics_hash in text
    assert "unavailable (nonfinite)" in text and "-1.25e-09" in text
    assert "Potential (V)" in text and "Thickness (µm)" in text
    assert result.configuration == snapshot
    np.testing.assert_array_equal(result.fields["metal"]["potential"], arrays)
    assert result.diagnostics == [Diagnostic(ATTACK, ATTACK, ATTACK)]


@pytest.mark.parametrize("prefix", ["=", "+", "-", "@", " =", "\t", "\r", "\n", " \x00=", "\ufeff=", "\u200b=", "＝"])
def test_spreadsheet_policy_neutralizes_text_and_preserves_machine_numeric_types(prefix):
    value = prefix + "harmless(1)"
    numeric = [-2.5, +1.25e-9, np.float64(-3e12), np.int64(-4), False, None]
    for factory, rows in ((csv_writer, [[value, *numeric]]),
                          (lambda stream: csv_dict_writer(stream, ["name", *map(str, range(len(numeric)))]),
                           [dict(zip(["name", *map(str, range(len(numeric)))], [value, *numeric]))])):
        stream = io.StringIO(newline="")
        factory(stream).writerows(rows)
        written = next(csv.reader(io.StringIO(stream.getvalue())))
        assert written[0] == "'" + value
        assert [float(x) for x in written[1:5]] == [-2.5, 1.25e-9, -3e12, -4.]
        assert written[5:] == ["False", ""]
    assert spreadsheet_cell(value, spreadsheet_safe=False) == value


def test_textual_numeric_identifier_is_not_coerced_and_plain_unicode_is_unchanged():
    assert spreadsheet_cell("-2.5") == "'-2.5"
    assert spreadsheet_cell("Ω µm² / 1e-9") == "Ω µm² / 1e-9"
    assert spreadsheet_cell("") == ""


@pytest.mark.parametrize("provenance", [[], {"time_s": ATTACK}, {"current_time_s": np.inf},
                                        {"comparison_time_s": True}, {"whole_run_metrics": []}])
def test_invalid_display_metadata_rejected_before_rendering(report_case, provenance):
    _, result = report_case
    with pytest.raises(ValueError, match="Report"):
        prepare_report_context(result, provenance=provenance)


def test_reports_bind_foreign_editor_to_recorded_physics_without_mutating_sources(report_case):
    project, result = report_case
    changed = deepcopy(project)
    changed.name, changed.notes = "CURRENT EDITOR SENTINEL", "Do not present this edited design"
    changed.layers[0].thickness *= 2
    context = prepare_report_context(result, changed)
    assert context.project.to_dict() == project.to_dict()
    assert context.result.configuration_hash == result.configuration_hash
    assert context.result.physics_hash == result.physics_hash
    assert changed.layers[0].thickness == 2 * project.layers[0].thickness
    assert context.result is not result and context.result.diagnostics is not result.diagnostics


def test_generated_png_contract_rejects_metadata_urls_and_svg():
    for value in (b"<svg onload='harmless()'/>", b"https://invalid.example/a.png", "data:image/png;base64,"):
        with pytest.raises(ValueError, match="generated PNG"):
            html_image(value, ATTACK)


def read_csv(path):
    with path.open(encoding="utf-8-sig", newline="") as stream:
        return list(csv.reader(stream))


def test_all_headless_csv_paths_share_text_policy_and_signed_si_values(report_case, tmp_path):
    _, result = report_case
    key = " \t=harmless(1)"
    result.terminals = {key: {key: -2.5, "current": -1.25e-9}}
    result.metrics[key] = -2.5
    export_results(result, tmp_path / "results", plots=False)
    terminal = read_csv(tmp_path / "results" / "terminals.csv")
    assert terminal[0][1] == "'" + key and terminal[1][0] == "'" + key
    assert float(terminal[1][1]) == -2.5 and float(terminal[1][2]) == -1.25e-9
    metric = next(row for row in read_csv(tmp_path / "results" / "metrics.csv") if row[0] == "'" + key)
    assert float(metric[1]) == -2.5
    # Numeric field rows contain real signed quantities, without apostrophes.
    field = read_csv(tmp_path / "results" / "metal_nodes.csv")
    assert [float(row[-1]) for row in field[1:]] == [-2., -1., 0.]
    profile = dict(field="=profile", unit="V", layer_id="=layer", value=[-2.5], distance_m=[0.], x_m=[0.], y_m=[0.], configuration_hash=key)
    export_profile(profile, tmp_path / "profile.csv")
    profile_rows = read_csv(tmp_path / "profile.csv")
    assert profile_rows[0][3].startswith("'") and float(profile_rows[1][3]) == -2.5
    assert profile_rows[1][-1] == "'" + key
    parameter = "@parameter"
    export_study([SweepRow(key, replace(result, configuration={}, converged=False), key, -2.5)], tmp_path / "study", parameter=parameter)
    study = read_csv(tmp_path / "study" / "study.csv")
    assert study[0][0] == "'" + parameter and study[1][0] == "'" + key
    assert float(study[1][2]) == -2.5
    result.analysis = {"measurements": {key: {key: -2.5, "reason": key}}}
    export_power_report(result, tmp_path / "power")
    power = read_csv(tmp_path / "power" / "measurements.csv")
    assert power[0][1] == "'" + key and power[1][0] == "'" + key
    assert float(power[1][1]) == -2.5 and power[1][-1] == "'" + key
    export_multiport({"port_ids": [key, "return"], "conductance_matrix": [[2., -2.], [-2., 2.]]}, tmp_path / "multiport.csv", "csv")
    multiport = read_csv(tmp_path / "multiport.csv")
    assert multiport[0][1] == "'" + key and multiport[1][0] == "'" + key
    assert float(multiport[1][-1]) == -2.
    result.configuration_hash = key
    result.transient = TransientHistory(np.array([0.]), [], [{"time_s": 0., "thermal_power": -2.5}], True)
    from etlayout.io.transient_export import export_transient_history
    export_transient_history(result, tmp_path / "transient", plots=False)
    transient = read_csv(tmp_path / "transient" / "transient_history.csv")
    assert transient[1][-1] == "'" + key
    assert float(transient[1][transient[0].index("thermal_power_W")]) == -2.5
    assert json.loads((tmp_path / "transient" / "transient_history.json").read_text())["configuration_hash"] == key


def test_gui_table_exports_apply_shared_policy_on_real_widgets(report_case, qtbot, tmp_path, monkeypatch):
    from PySide6.QtWidgets import QFileDialog

    from etlayout.gui.plots import ProfilePanel, StudyPanel
    from etlayout.gui.transient import TimeHistoryPanel
    _, result = report_case
    key = "\n=harmless(1)"
    selected = []
    monkeypatch.setattr(QFileDialog, "getSaveFileName", lambda *args, **kwargs: (str(selected[-1]), "CSV (*.csv)"))
    profiles = ProfilePanel()
    studies = StudyPanel()
    history = TimeHistoryPanel()
    for widget in (profiles, studies, history):
        qtbot.addWidget(widget)
    profiles.profiles = [dict(name=key, layer=key, field="potential", distance=[0.], values=[-2.5], coordinates=[(0., 0.)])]
    selected.append(tmp_path / "profiles.csv")
    profiles.export_csv()
    assert read_csv(selected[-1])[1][:2] == ["'" + key, "'" + key]
    assert float(read_csv(selected[-1])[1][7]) == -2.5
    studies.rows, studies.parameter = [SweepRow(key, result, key, -2.5)], key
    selected.append(tmp_path / "study.csv")
    studies.export_csv()
    study = read_csv(selected[-1])
    assert study[0][0] == "'" + key and study[1][0] == "'" + key
    assert float(study[1][1]) == -2.5 and study[1][-1] == "'" + key
    history.result, history.layer, history.region = result, key, key
    history.scope_selector.blockSignals(True)
    history.scope_selector.setCurrentIndex(history.scope_selector.findData("selection"))
    history.trace_times, history.trace_values = np.array([0.]), np.array([300.])
    history.export_local_history_to(tmp_path / "local.csv")
    local = read_csv(tmp_path / "local.csv")
    assert local[1][2:4] == ["'" + key, "'" + key]
    assert float(local[1][1]) == 300.


def test_application_csv_writers_cannot_bypass_the_shared_policy():
    # Future output sites must deliberately adopt the centralized policy.
    root = Path(__file__).parents[1] / "src" / "etlayout"
    for path in root.rglob("*.py"):
        if path.name == "reporting.py":
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
                assert not (isinstance(node.func.value, ast.Name) and node.func.value.id == "csv" and
                            node.func.attr in {"writer", "DictWriter"}), str(path)


@pytest.mark.fast
def test_report_provenance_never_probes_metadata_resource_locations(report_case, monkeypatch):
    project, result = report_case
    project.layout.path = r"\\example.invalid\private\layout.gds"
    project.layout.sha256 = ""
    result.configuration = project.to_dict()
    result.physics_hash = physics_hash(project, resolve_layout=False)
    calls = []
    for name in ("stat", "is_file", "exists", "open", "read_bytes"):
        original = getattr(Path, name)
        def probe(self, *args, _name=name, _original=original, **kwargs):
            if "example.invalid" in str(self):
                calls.append(_name)
                raise AssertionError("Report metadata caused filesystem access")
            return _original(self, *args, **kwargs)
        monkeypatch.setattr(Path, name, probe)
    context = prepare_report_context(result, deepcopy(project))
    assert context.result.physics_hash == result.physics_hash
    assert calls == []
