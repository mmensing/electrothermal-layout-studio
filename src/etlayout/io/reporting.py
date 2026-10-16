# SPDX-License-Identifier: MPL-2.0
"""Offline report and spreadsheet contracts shared by GUI and headless exports.

Only renderer-owned markup enters ``html_document``. Every metadata value is
text; image payloads are generated PNGs, never metadata-provided URLs or HTML.
CSV policy is type-aware: textual identifiers are protected, numeric objects
retain their SI values (including negative/scientific/nonfinite diagnostics).
"""
from __future__ import annotations

import base64
import csv
import html
import math
import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, replace
from numbers import Real
from typing import Any, TextIO

from etlayout.io.project import (
    _json_dump,
    annotated_result,
    physics_hash,
    project_from_dict,
    refresh_threshold_diagnostics,
)
from etlayout.models import Project, SimulationResult
from etlayout.visualization.sampling import metric_label, metric_unit
from etlayout.visualization.timing import difference_time_label
from etlayout.visualization.units import display_unit, to_display

LIMITATIONS = (
    "Thickness-averaged 2.5D sheets do not resolve full 3D thermal spreading or through-thickness gradients. "
    "Semiconductors are resistive effective-medium models. The analytical HEMT law excludes drift diffusion, "
    "velocity saturation, junctions, breakdown, trapping, gate leakage, and field-plate electrostatics. "
    "Material seeds require process calibration. Peak current at ideal sharp corners is mesh-sensitive."
)
CONTENT_SECURITY_POLICY = (
    "default-src 'none'; script-src 'none'; style-src 'unsafe-inline'; img-src data:; "
    "connect-src 'none'; object-src 'none'; frame-src 'none'; base-uri 'none'; form-action 'none'"
)
REPORT_STYLE = """body{font:15px/1.6 'Segoe UI',Arial,sans-serif;color:#17221e;max-width:1100px;margin:48px auto;padding:0 24px}
h1{font-size:30px}h2{font-size:21px;margin-top:32px}table{width:100%;border-collapse:collapse;margin:20px 0}
th,td{padding:9px;text-align:left;border-bottom:1px solid #d9dfda;overflow-wrap:anywhere}th{border-top:2px solid #1f6f5f}
img{max-width:100%;height:auto}pre{white-space:pre-wrap;overflow-wrap:anywhere;font-size:12px}.metadata,figcaption{color:#59675e}
.warning{color:#a43329;font-weight:600}@media print{body{margin:15px}}"""


def html_text(value: Any) -> str:
    """Escape a text node or quoted attribute; do not treat metadata as markup."""
    return html.escape(str(value), quote=True)


def safe_filename(name: Any) -> str:
    value = re.sub(r"[^A-Za-z0-9_.-]+", "_", str(name))[:100].strip(". ") or "layer"
    reserved = {"CON", "PRN", "AUX", "NUL", *(f"COM{i}" for i in range(1, 10)),
                *(f"LPT{i}" for i in range(1, 10))}
    return "_" + value if value.split(".")[0].upper() in reserved else value


def spreadsheet_cell(value: Any, *, spreadsheet_safe: bool = True) -> Any:
    """Neutralize formula-like *text*, without coercing any numerical column.

    A leading apostrophe is intentionally retained in the CSV text. Quoting alone
    does not stop spreadsheet formula evaluation. ``spreadsheet_safe=False`` is
    an explicit machine-only escape hatch; application writers use the default.
    """
    if not spreadsheet_safe or not isinstance(value, str) or not value:
        return value
    index = 0
    while index < len(value) and (value[index].isspace() or ord(value[index]) < 32 or value[index] in "\ufeff\u200b"):
        index += 1
    candidate = value[index:]
    if (candidate.startswith(("=", "+", "-", "@", "＝", "＋", "－", "＠")) or
            any(ord(char) < 32 or char in "\ufeff\u200b" for char in value[:index])):
        return "'" + value
    return value


class _CsvWriter:
    def __init__(self, stream, *, spreadsheet_safe=True, **kwargs):
        self.writer = csv.writer(stream, **kwargs)
        self.spreadsheet_safe = spreadsheet_safe

    def writerow(self, row):
        return self.writer.writerow(spreadsheet_cell(value, spreadsheet_safe=self.spreadsheet_safe) for value in row)

    def writerows(self, rows):
        for row in rows:
            self.writerow(row)


def csv_writer(stream: TextIO, *, spreadsheet_safe: bool = True, **kwargs) -> _CsvWriter:
    return _CsvWriter(stream, spreadsheet_safe=spreadsheet_safe, **kwargs)


class _CsvDictWriter(csv.DictWriter):
    def __init__(self, stream, fieldnames, *, spreadsheet_safe=True, **kwargs):
        super().__init__(stream, fieldnames, **kwargs)
        self.spreadsheet_safe = spreadsheet_safe

    def writerow(self, rowdict):
        return self.writer.writerow(spreadsheet_cell(value, spreadsheet_safe=self.spreadsheet_safe)
                                   for value in self._dict_to_list(rowdict))

    def writerows(self, rowdicts):
        for row in rowdicts:
            self.writerow(row)


def csv_dict_writer(stream: TextIO, fieldnames: Iterable[str], *, spreadsheet_safe: bool = True, **kwargs) -> _CsvDictWriter:
    return _CsvDictWriter(stream, fieldnames, spreadsheet_safe=spreadsheet_safe, **kwargs)


def format_value(value: Any, unit: str = "") -> str:
    """Engineering presentation only; numerical artifacts always retain SI."""
    shown = to_display(value, unit)
    if isinstance(shown, Real) and not isinstance(shown, bool):
        return f"{shown:.9g}" if math.isfinite(shown) else "unavailable (nonfinite)"
    return "—" if shown is None else str(shown)


def html_table(rows: Iterable[Iterable[Any]], headings: Iterable[Any]) -> str:
    return ("<table><thead><tr>" + "".join(f"<th>{html_text(value)}</th>" for value in headings) +
            "</tr></thead><tbody>" + "".join("<tr>" + "".join(f"<td>{html_text(value)}</td>" for value in row) +
                                               "</tr>" for row in rows) + "</tbody></table>")


def html_image(png: bytes, caption: Any) -> str:
    if not isinstance(png, bytes) or not png.startswith(b"\x89PNG\r\n\x1a\n"):
        raise ValueError("Report images must be generated PNG payloads")
    source = "data:image/png;base64," + base64.b64encode(png).decode("ascii")
    return f'<figure><img src="{source}" alt="{html_text(caption)}"><figcaption>{html_text(caption)}</figcaption></figure>'


def html_document(title: Any, body: str) -> str:
    """Wrap trusted renderer markup; raw metadata belongs in ``html_text``."""
    return (f'<!doctype html><html lang="en"><head><meta charset="utf-8">'
            f'<meta http-equiv="Content-Security-Policy" content="{html_text(CONTENT_SECURITY_POLICY)}">'
            '<meta name="referrer" content="no-referrer">'
            f'<title>{html_text(title)}</title><style>{REPORT_STYLE}</style></head><body>{body}</body></html>')


@dataclass(frozen=True)
class ReportContext:
    project: Project
    result: SimulationResult
    provenance: dict[str, Any]

    @property
    def difference(self) -> bool:
        return self.provenance.get("kind") == "difference"


def prepare_report_context(result: SimulationResult, project: Project | None = None, *,
                           provenance=None, run: SimulationResult | None = None) -> ReportContext:
    """Use recorded physics for foreign/stale results and never mutate a source.

    Editable notes/thresholds may annotate compatible physics, as before. A report
    is not a new solve and cannot rewrite result/configuration/physics identity.
    """
    if not isinstance(result.configuration, Mapping):
        raise ValueError("Report configuration must be a metadata mapping")
    visualization = provenance if provenance is not None else result.configuration.get("visualization", {})
    if not isinstance(visualization, Mapping):
        raise ValueError("Report visualization metadata must be a mapping")
    visualization = dict(visualization)
    for key in ("time_s", "current_time_s", "comparison_time_s"):
        value = visualization.get(key)
        if value is not None and (not isinstance(value, Real) or isinstance(value, bool) or not math.isfinite(value)):
            raise ValueError(f"Report {key} must be a finite time in seconds or null")
    if "whole_run_metrics" in visualization and not isinstance(visualization["whole_run_metrics"], Mapping):
        raise ValueError("Report whole_run_metrics must be a metadata mapping")
    source = {key: value for key, value in result.configuration.items() if key != "visualization"}
    if project is None:
        project = project_from_dict(source) if source else Project(name="Electrothermal simulation")
    elif physics_hash(project) != physics_hash(source):
        project = project_from_dict(source)
    rendered = replace(result, diagnostics=list(result.diagnostics))
    if visualization.get("kind") != "difference":
        if run is not None:
            rendered = annotated_result(rendered, project, run=run)
        elif visualization.get("whole_run_metrics"):
            rendered = replace(rendered, metrics={**rendered.metrics, "whole_run_metrics": visualization["whole_run_metrics"]})
        refresh_threshold_diagnostics(rendered, project)
    return ReportContext(project, rendered, visualization)


def report_status(context: ReportContext) -> str:
    result, provenance = context.result, context.provenance
    if context.difference:
        status = "Derived difference: current − comparison on common finite support"
        timing = difference_time_label(provenance)
        if timing:
            status += " · " + timing
        if not result.converged:
            status += " · NOT CONVERGED source — diagnostic output only"
    else:
        status = "Converged" if result.converged else "NOT CONVERGED — diagnostic output only"
        if result.transient is not None:
            status += f" · transient fields at t = {format_value(result.transient.times[-1])} s; complete history in simulation.etl"
        elif "time_s" in result.metrics:
            status += " · selected snapshot at t = " + format_value(result.metrics["time_s"]) + " s"
    if provenance.get("transient_completed") is False or (result.transient is not None and not result.transient.completed):
        status += " · snapshot from incomplete transient run"
    return status


def render_report(context: ReportContext, *, images=(), selection=None, metadata=None, scope="") -> str:
    """Render the same security/provenance contract for every application report."""
    project, result = context.project, context.result
    metrics = {**result.metrics, **result.metrics.get("whole_run_metrics", {})}
    status_class = ' class="warning"' if (not result.converged or context.provenance.get("transient_completed") is False or
                                          result.transient is not None and not result.transient.completed) else ""
    content = [f"<h1>{html_text(project.name)}</h1><p{status_class}>{html_text(report_status(context))}</p>",
               f"<p>Analysis: {html_text(project.solver.mode)} · Solver {html_text(result.solver_version)} · "
               f"elapsed {html_text(format_value(result.elapsed_seconds))} s · {len(result.mesh.triangles):,} triangles</p>",
               f"<p>Configuration SHA-256: <code>{html_text(result.configuration_hash)}</code><br>"
               f"Physics SHA-256: <code>{html_text(result.physics_hash)}</code></p>",
               f"<p>Export scope: {html_text(scope)}</p>"]
    if selection:
        content.append("<h2>Displayed selection</h2><pre>" + html_text(_json_dump(selection)) + "</pre>")
    if context.provenance:
        content.append("<h2>Paired-run provenance</h2><pre>" + html_text(_json_dump(context.provenance)) + "</pre>")
    content.append("<h2>Field maps</h2>" + "".join(html_image(png, caption) for png, caption in images))
    if context.difference:
        content.append("<h2>Derived difference</h2><p>Original solver metrics and terminal quantities do not describe this difference and are omitted.</p>")
    else:
        content.append("<h2>Computed metrics</h2>" + html_table(
            [[metric_label(key), format_value(value, metric_unit(key)), display_unit(metric_unit(key))]
             for key, value in metrics.items() if isinstance(value, (Real, str))], ["Metric", "Value", "Unit"]))
        content.append("<h2>Terminal quantities</h2>" + html_table(
            [[name, format_value(data.get("voltage")), format_value(data.get("current"))]
             for name, data in result.terminals.items()], ["Terminal", "Potential (V)", "Current into device (A)"]))
    content.append("<h2>Physical stack</h2>" + html_table(
        [[layer.name, layer.material_id, layer.role, format_value(layer.z, "m"), format_value(layer.thickness, "m")]
         for layer in project.layers], ["Layer", "Material", "Role", "Bottom z (µm)", "Thickness (µm)"]))
    content.append("<h2>Diagnostics</h2>" + html_table(
        [[item.severity, item.object_id, item.message] for item in result.diagnostics], ["Level", "Object", "Diagnostic"]))
    content.extend(["<h2>Assumptions and notes</h2><p>" + html_text(project.notes).replace("\n", "<br>") + "</p>",
                    "<h2>Model limitations</h2><p>" + html_text(LIMITATIONS) + "</p>"])
    details = {"configuration": result.configuration, "report_annotations": {"name": project.name, "notes": project.notes,
               "thresholds": project.thresholds}, "iterations": result.iterations, "mesh": result.mesh.statistics,
               "converged": result.converged, "selection": selection, "visualization": context.provenance,
               "configuration_hash": result.configuration_hash, "physics_hash": result.physics_hash,
               "metrics": {} if context.difference else result.metrics,
               "execution": result.execution, "analysis": result.analysis, "export_metadata": metadata}
    content.append("<h2>Reproducible configuration and convergence</h2><p>Numerical arrays use SI units. "
                   "Display conversions and color clipping do not modify scientific values. "
                   "Field values describe this discretization; experimental uncertainty has not been estimated.</p>"
                   "<details><summary>Configuration and convergence history</summary><pre>" + html_text(_json_dump(details)) + "</pre></details>")
    return html_document(project.name, "".join(content))
