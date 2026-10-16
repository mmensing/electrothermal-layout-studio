# SPDX-License-Identifier: MPL-2.0
"""Vector/raster maps and self-contained HTML reports from actual result data."""
from __future__ import annotations

import math
from pathlib import Path

from PySide6.QtCore import QBuffer, QIODevice, QRect, QRectF, QSize, Qt
from PySide6.QtGui import QColor, QFont, QImage, QLinearGradient, QPainter, QPen
from PySide6.QtSvg import QSvgGenerator

from etlayout.io.project import _json_dump
from etlayout.io.reporting import prepare_report_context, render_report
from etlayout.simulation.analysis import FIELD_UNITS
from etlayout.visualization.palettes import get_colormap
from etlayout.visualization.sampling import DISPLAY_FIELD_LABELS as FIELD_LABELS
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
    context = prepare_report_context(result, project, provenance=provenance, run=run)
    image = QImage(1800, 1100, QImage.Format.Format_ARGB32)
    map_metadata = render_map(view, image, legend, provenance=context.provenance)
    buffer = QBuffer()
    buffer.open(QIODevice.OpenModeFlag.WriteOnly)
    if not image.save(buffer, "PNG"):
        raise OSError("Unable to encode the displayed report image")
    document = render_report(context, images=[(bytes(buffer.data()), legend)], selection=map_metadata,
                             scope="Displayed map and recorded physical configuration; this report may contain design metadata.")
    Path(path).write_text(document, encoding="utf-8")
