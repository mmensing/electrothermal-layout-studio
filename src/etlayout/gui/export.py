# SPDX-License-Identifier: MPL-2.0
"""Vector/raster maps and self-contained HTML reports from actual result data."""
from __future__ import annotations

import base64
import math
from dataclasses import replace
from html import escape
from pathlib import Path

from PySide6.QtCore import QBuffer, QIODevice, QRect, QRectF, QSize, Qt
from PySide6.QtGui import QColor, QFont, QImage, QLinearGradient, QPainter, QPen
from PySide6.QtSvg import QSvgGenerator

from etlayout.io.project import (
    _json_dump,
    annotated_result,
    physics_hash,
    project_from_dict,
    refresh_threshold_diagnostics,
)
from etlayout.simulation.analysis import FIELD_UNITS
from etlayout.visualization.palettes import get_colormap
from etlayout.visualization.sampling import DISPLAY_FIELD_LABELS as FIELD_LABELS
from etlayout.visualization.sampling import metric_label, metric_unit
from etlayout.visualization.timing import difference_time_label
from etlayout.visualization.units import to_display


def draw_colorbar(painter, rect, limits, *, colormap="teal", logarithmic=False, label="", clipped=False):
    """Draw the actual map palette, physical-unit ticks and disclosed transform.

    ``rect`` reserves a 110 px high legend block. Coordinates are painter units;
    comparison exports can reuse this without inheriting viewport decorations.
    """
    painter.save()
    painter.setPen(QColor("#17221e"))
    painter.setFont(QFont("Segoe UI", 11))
    if limits is None:
        painter.drawText(rect, Qt.AlignmentFlag.AlignLeft | Qt.TextFlag.TextWordWrap,
                         label + " · no finite field support")
        painter.restore()
        return
    low, high = map(float, limits)
    if not all(map(math.isfinite, (low, high))) or low > high or (logarithmic and low <= 0):
        painter.restore()
        raise ValueError("Invalid colorbar range")
    bar = QRectF(rect.x() + 10, rect.y(), rect.width() - 20, 22)
    gradient = QLinearGradient(bar.left(), 0, bar.right(), 0)
    cmap = get_colormap(colormap)
    for index in range(65):
        red, green, blue, alpha = cmap(index / 64)
        gradient.setColorAt(index / 64, QColor.fromRgbF(red, green, blue, alpha))
    painter.fillRect(bar, QColor.fromRgbF(*map(float, cmap(.5))) if low == high else gradient)
    painter.setPen(QPen(QColor("#6f7b74"), .8))
    painter.drawRect(bar)
    if low == high:
        ticks = [(0.5, low)]
    else:
        middle = math.exp(.5 * math.log(low) + .5 * math.log(high)) if logarithmic else .5 * low + .5 * high
        ticks = [(0., low), (.5, middle), (1., high)]
    for fraction, value in ticks:
        x = bar.left() + fraction * bar.width()
        painter.drawLine(int(x), int(bar.bottom()), int(x), int(bar.bottom() + 7))
        label_rect = QRectF(x - 100, bar.bottom() + 9, 200, 25)
        if fraction == 0:
            label_rect.moveLeft(bar.left())
            align = Qt.AlignmentFlag.AlignLeft
        elif fraction == 1:
            label_rect.moveRight(bar.right())
            align = Qt.AlignmentFlag.AlignRight
        else:
            align = Qt.AlignmentFlag.AlignHCenter
        painter.drawText(label_rect, align, f"{value:.5g}")
    scale = "logarithmic; nonpositive support omitted" if logarithmic else "linear"
    if low == high:
        scale += " · constant field"
    suffix = " · manual color range; out-of-range colors clipped" if clipped else ""
    painter.drawText(QRectF(rect.x(), rect.y() + 62, rect.width(), rect.height() - 62),
                     Qt.AlignmentFlag.AlignLeft | Qt.TextFlag.TextWordWrap, f"{label} · {scale}{suffix}")
    painter.restore()


def _view_metadata(view, provenance=None):
    field = getattr(view, "field_key", "") or ""
    layer = getattr(view, "field_layer", "") or ""
    label, unit = FIELD_LABELS.get(field, (field.replace("_", " "), ""))
    result = getattr(view, "result", None)
    provenance = dict(provenance or (result.configuration.get("visualization", {}) if result else {}))
    limits = getattr(view, "field_limits", None)
    if getattr(view, "overlay_item", None) is None:
        limits = None
    if result and "time_s" in result.metrics:
        provenance.setdefault("time_s", result.metrics["time_s"])
        provenance.setdefault("applied_drives", result.metrics.get("applied_drives", {}))
    return {"layer": layer, "field": field, "region": getattr(view, "field_region", ""),
            "converged": bool(result.converged) if result else None,
            "colormap": getattr(view, "field_colormap", "teal"), "limits": limits,
            "logarithmic": bool(getattr(view, "field_log", False)),
            "manual_limits": bool(getattr(view, "field_manual_limits", False)),
            "limits_unit": FIELD_UNITS.get(field, ""), "display_unit": unit,
            "display_limits": tuple(to_display(value, FIELD_UNITS.get(field, "")) for value in limits) if limits else None,
            "label": (("Δ " if provenance.get("kind") == "difference" else "") + f"{label} ({unit}) · {layer}") if field else "Layout geometry",
            "provenance": provenance}


def render_map(view, target, legend, width=1800, height=1100, *, provenance=None):
    metadata = _view_metadata(view, provenance)
    painter = QPainter(target)
    painter.fillRect(QRectF(0, 0, width, height), QColor("white"))
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    painter.setPen(QColor("#17221e"))
    painter.setFont(QFont("Segoe UI", 16))
    time_s = metadata["provenance"].get("time_s")
    if time_s is not None:
        legend += f" · t = {time_s:.9g} s"
    elif metadata["provenance"].get("kind") == "difference":
        times = difference_time_label(metadata["provenance"])
        if times:
            legend += " · " + times
    painter.drawText(QRectF(28, 20, width - 56, 75), 0x1000, legend)
    # Render the current visible viewport in vector coordinates, including scale bar.
    source = view.viewport().rect()
    available = QRectF(28, 100, width - 56, height - (305 if metadata["field"] else 145))
    factor = min(available.width() / source.width(), available.height() / source.height())
    destination = QRectF(0, 0, source.width() * factor, source.height() * factor)
    destination.moveCenter(available.center())
    view.render(painter, destination, source)
    label = metadata["label"] + (f" · {metadata['region']}" if metadata["region"] else "")
    if metadata["field"]:
        draw_colorbar(painter, QRectF(80, height - 188, width - 160, 110), metadata["display_limits"],
                      colormap=metadata["colormap"], logarithmic=metadata["logarithmic"],
                      label=label, clipped=metadata["manual_limits"])
    painter.setFont(QFont("Segoe UI", 11))
    caption = "Electrothermal Layout Studio · physical coordinates in µm · "
    if metadata["provenance"].get("kind") == "difference":
        caption += "derived difference: current − comparison on common finite support"
    elif metadata["field"]:
        caption += "computed finite-element field"
    else:
        caption += "layout geometry"
    if metadata["converged"] is False:
        caption += " · NOT CONVERGED source; diagnostic output only"
    if metadata["provenance"].get("transient_completed") is False:
        caption += " · snapshot from INCOMPLETE transient run"
    painter.drawText(28, height - 24, caption)
    painter.end()
    return metadata


def export_view(view, path, legend, *, provenance=None):
    path = Path(path)
    if path.suffix.lower() == ".svg":
        target = QSvgGenerator()
        target.setFileName(str(path))
        target.setSize(QSize(1800, 1100))
        target.setViewBox(QRect(0, 0, 1800, 1100))
        target.setTitle(legend)
        target.setDescription("Electrothermal Layout Studio vector map generated from the displayed layout and numerical result.")
        metadata = render_map(view, target, legend, provenance=provenance)
    else:
        image = QImage(1800, 1100, QImage.Format.Format_ARGB32)
        metadata = render_map(view, image, legend, provenance=provenance)
        if not image.save(str(path), "PNG"):
            raise OSError(f"Unable to save image {path}")
    path.with_suffix(path.suffix + ".json").write_text(_json_dump(metadata), encoding="utf-8")


def export_html_report(project, result, view, path, legend, *, provenance=None, run=None):
    provenance = dict(provenance or result.configuration.get("visualization", {}))
    difference = provenance.get("kind") == "difference"
    source_configuration = {key: value for key, value in result.configuration.items() if key != "visualization"}
    if physics_hash(project) != physics_hash(source_configuration):
        # Never label foreign results using the currently edited design.
        project = project_from_dict(source_configuration)
    result = replace(result, diagnostics=list(result.diagnostics))
    if not difference:
        if run is not None:
            result = annotated_result(result, project, run=run)
        elif provenance.get("whole_run_metrics"):
            result = replace(result, metrics={**result.metrics, "whole_run_metrics": provenance["whole_run_metrics"]})
        refresh_threshold_diagnostics(result, project)
    image = QImage(1800, 1100, QImage.Format.Format_ARGB32)
    map_metadata = render_map(view, image, legend, provenance=provenance)
    buffer = QBuffer()
    buffer.open(QIODevice.OpenModeFlag.WriteOnly)
    image.save(buffer, "PNG")
    encoded = base64.b64encode(bytes(buffer.data())).decode("ascii")
    def table(rows, headings):
        return "<table><thead><tr>" + "".join(f"<th>{escape(str(x))}</th>" for x in headings) + "</tr></thead><tbody>" + "".join(
            "<tr>" + "".join(f"<td>{escape(str(value))}</td>" for value in row) + "</tr>" for row in rows) + "</tbody></table>"
    report_metrics = {**result.metrics, **result.metrics.get("whole_run_metrics", {})}
    metrics = table([[metric_label(key), to_display(value, metric_unit(key))] for key, value in ({} if difference else report_metrics).items()
                     if isinstance(value, (int, float, str))], ["Metric", "Value"])
    layers = table([[x.name, x.material_id, x.role, f"{x.z * 1e6:g}", f"{x.thickness * 1e6:g}"] for x in project.layers],
                   ["Layer", "Material", "Role", "Bottom z (µm)", "Thickness (µm)"])
    terminals = table([[name, data.get("voltage", "—"), data.get("current", "—")] for name, data in ({} if difference else result.terminals).items()],
                      ["Terminal", "Potential (V)", "Current into device (A)"])
    diagnostics = table([[d.severity, d.object_id, d.message] for d in result.diagnostics], ["Level", "Object", "Diagnostic"])
    details = escape(_json_dump({"configuration": result.configuration, "report_annotations": {"name": project.name, "notes": project.notes, "thresholds": project.thresholds}, "iterations": result.iterations,
                                "mesh": result.mesh.statistics, "metrics": {} if difference else result.metrics,
                                "converged": result.converged,
                                "displayed_map": map_metadata, "visualization": provenance}))
    if difference:
        results_html = ("<h2>Derived difference</h2><p>Current − comparison on common finite support. "
                        "Original solver metrics and terminal quantities do not describe the difference map and are omitted.</p>"
                        "<h2>Paired-run provenance</h2><pre>" + escape(_json_dump(provenance)) + "</pre>")
    else:
        results_html = f"<h2>Computed metrics</h2>{metrics}<h2>Terminal quantities</h2>{terminals}"
    status = "Derived difference" if difference else "Converged" if result.converged else "<strong>NOT CONVERGED</strong>"
    if "time_s" in result.metrics:
        status += f" · selected snapshot at t = {result.metrics['time_s']:.9g} s"
    if provenance.get("transient_completed") is False:
        status += " · snapshot from <strong>INCOMPLETE transient run</strong>"
    if difference and not result.converged:
        status += " · <strong>NOT CONVERGED source — diagnostic output only</strong>"
    html = f"""<!doctype html><html lang='en'><meta charset='utf-8'><title>{escape(project.name)} — Analysis</title>
<style>body{{font:16px 'Segoe UI',Arial,sans-serif;color:#17221e;max-width:1100px;margin:48px auto;padding:0 24px}}h1{{font-size:30px}}h2{{font-size:21px;margin-top:36px}}p{{line-height:1.6}}table{{width:100%;border-collapse:collapse;margin:20px 0}}th,td{{padding:9px;text-align:left;border-bottom:1px solid #d9dfda}}th{{border-top:2px solid #1f6f5f}}img{{width:100%;height:auto}}.metadata{{color:#59675e;font-size:14px}}@media print{{body{{margin:15px}}}}</style>
<h1>{escape(project.name)}</h1><p>Analysis: <strong>{escape(project.solver.mode)}</strong> · {status} · {result.elapsed_seconds:.3f} s</p>
<p class='metadata'>Configuration hash: {escape(result.configuration_hash)}<br>Solver version: {escape(result.solver_version)} · {len(result.mesh.triangles):,} triangles</p>
<img alt='{escape(legend)}' src='data:image/png;base64,{encoded}'>{results_html}<h2>Physical stack</h2>{layers}<h2>Diagnostics</h2>{diagnostics}
<h2>Assumptions and notes</h2><p>{escape(project.notes).replace(chr(10), '<br>')}</p><p>2.5D resistive sheets with explicitly declared interlayer coupling. The analytical HEMT model excludes drift-diffusion, velocity saturation, trapping, breakdown and field-plate electrostatics. Representative material data require process calibration. Sharp-corner current maxima depend on mesh size; use convergence studies and local averages.</p>
<h2>Reproducible configuration and convergence</h2><p>The recorded setup contains layout identity and ROI, material snapshots, layer dimensions, electrical conditions, thermal boundaries, mesh settings and solver tolerances. Values use SI units. Save the project archive to retain numerical arrays and embedded layout as well.</p>
<details><summary>Inspect setup, iterations and complete statistics</summary><pre style='white-space:pre-wrap;overflow-wrap:anywhere;font-size:12px'>{details}</pre></details></html>"""
    Path(path).write_text(html, encoding="utf-8")
