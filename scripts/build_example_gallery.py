# SPDX-License-Identifier: MPL-2.0
"""Build a repository gallery from real prepared geometry and computed FE fields.

Run from the repository root. PNGs are 2× the intended 960 px GitHub display
width; SVGs preserve editable vector geometry and text. No network is needed.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import textwrap
from copy import deepcopy
from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path

import numpy as np

from etlayout import __version__
from etlayout.example_catalog import example_info
from etlayout.examples import EXAMPLE_NAMES, ROUTINE_EXAMPLES, make_example
from etlayout.geometry.prepare import prepare_geometry
from etlayout.io.export import METRIC_UNITS
from etlayout.io.project import configuration_hash, load_project, physics_hash, save_project
from etlayout.models import SimulationError
from etlayout.simulation.analysis import FIELD_UNITS
from etlayout.simulation.runner import run_project
from etlayout.visualization.units import display_factor, display_unit

ROOT = Path(__file__).resolve().parents[1]
INK = "#171717"
TEAL = "#1F6F5F"
GRAY = "#6F6F6F"
PALETTE = ["#DDE9E5", "#D4D7D5", "#AEB7B3", "#E9EBE9", "#899B93", "#F2F3F1"]
GALLERY_SCHEMA = 1
VISUAL_SPEC = {
    "claim": "Prepared device geometry alongside one representative computed field at its stated operating point.",
    "visual_form": "equal-aspect spatial geometry and finite-element scalar map",
    "metric": "selected scalar field or vector magnitude on its active physical layer",
    "unit": "shown beside each scale; layout axes in micrometers",
    "direction": "spatial field, not an optimization ranking",
    "focal_series": "selected layer highlighted in teal; other layers in neutral gray",
    "baselines": "none; no experimental reference is supplied",
    "ordering": "stable numerical example ID; physical layers ordered by z",
    "width_px": 960, "height_px": 500, "raster_scale": 2,
    "uncertainty": "deterministic discretized result; no experimental uncertainty estimate",
    "annotation_plan": "up to four drive/sense terminal IDs; full setup retained in manifest and ETL",
    "tick_policy": "3–5 major ticks, engineering-scaled field unit, exact range in manifest",
    "formats": ["png", "svg (vector geometry/text; 2× raster FE field)"],
    "style_conventions": ["Equal-aspect geometry and fields", "Restrained neutral/teal palette",
                          "Ordered scalar scales with explicit units"],
}


def _jsonable(value):
    if isinstance(value, dict):
        return {str(k): _jsonable(v) for k, v in value.items()}
    if isinstance(value, (list, tuple, np.ndarray)):
        return [_jsonable(v) for v in value]
    if isinstance(value, (float, np.floating)):
        return float(value) if np.isfinite(value) else None
    if isinstance(value, np.integer):
        return int(value)
    return value


def source_fingerprint() -> str:
    """Invalidate caches when implementation changes even without a version bump."""
    digest = hashlib.sha256()
    for path in sorted((ROOT / "src/etlayout").rglob("*.py")):
        digest.update(path.relative_to(ROOT).as_posix().encode())
        digest.update(path.read_bytes())
    return digest.hexdigest()


def _style():
    import matplotlib
    matplotlib.use("Agg")
    matplotlib.rcParams.update({
        "font.family": "DejaVu Sans", "font.size": 11, "axes.titlesize": 12,
        "axes.labelsize": 11, "axes.edgecolor": GRAY, "axes.linewidth": .65,
        "axes.spines.top": False, "axes.spines.right": False,
        "text.color": INK, "axes.labelcolor": INK, "xtick.color": GRAY, "ytick.color": GRAY,
        "figure.facecolor": "white", "axes.facecolor": "white", "savefig.facecolor": "white",
        "svg.fonttype": "none", "svg.hashsalt": "etlayout-example-gallery",
    })


def _clean_svg(path):
    # Matplotlib emits trailing spaces in path data; keep repository diffs clean.
    path.write_text("\n".join(line.rstrip() for line in path.read_text(encoding="utf-8").splitlines()) + "\n",
                    encoding="utf-8")


def _polygon_path(polygon):
    from matplotlib.path import Path as PlotPath
    from shapely.geometry.polygon import orient
    polygon = orient(polygon, sign=1.)
    points, codes = [], []
    for ring in [polygon.exterior, *polygon.interiors]:
        coordinates = np.asarray(ring.coords) * 1e6
        points.extend(coordinates)
        codes.extend([PlotPath.MOVETO, *([PlotPath.LINETO] * (len(coordinates) - 2)), PlotPath.CLOSEPOLY])
    return PlotPath(points, codes)


def _shapes(ax, shape, **style):
    from matplotlib.patches import PathPatch
    if shape.is_empty:
        return
    if shape.geom_type == "Polygon":
        ax.add_patch(PathPatch(_polygon_path(shape), **style))
    elif hasattr(shape, "geoms"):
        for part in shape.geoms:
            _shapes(ax, part, **style)
    elif shape.geom_type in {"LineString", "LinearRing"}:
        xy = np.asarray(shape.coords) * 1e6
        ax.plot(xy[:, 0], xy[:, 1], color=style.get("edgecolor", INK), lw=1.6)


def _axes(ax, bounds):
    from matplotlib.ticker import MaxNLocator
    x0, y0, x1, y1 = np.asarray(bounds) * 1e6
    margin = max(x1 - x0, y1 - y0) * .065
    ax.set(xlim=(x0 - margin, x1 + margin), ylim=(y0 - margin, y1 + margin),
           xlabel="x (µm)", ylabel="y (µm)", aspect="equal")
    ax.xaxis.set_major_locator(MaxNLocator(4))
    ax.yaxis.set_major_locator(MaxNLocator(4))
    ax.tick_params(length=3, width=.65)


def draw_geometry(ax, project, geometry, info, labels=True):
    """Draw prepared physical polygons, including voids; never layout thumbnails."""
    layers = sorted(project.layers, key=lambda layer: layer.z)
    for index, layer in enumerate(layers):
        selected = layer.id == info.layer
        _shapes(ax, geometry.layers[layer.id], facecolor=PALETTE[index % len(PALETTE)],
                edgecolor=TEAL if selected else GRAY, lw=1.05 if selected else .65,
                alpha=1. if selected else .72, zorder=2 + index)
    for shape in geometry.interfaces.values():
        _shapes(ax, shape, facecolor="none", edgecolor=INK, lw=.65, linestyle="--", zorder=20)
    annotations = []
    if labels:
        terminals = [t for t in project.terminals if t.kind in {"ground", "current", "voltage", "floating"}]
        # Keep the diagram legible; all terminals and biases are listed in the catalogue record.
        terminals = sorted(terminals, key=lambda t: (t.kind == "voltage" and "bias" in t.id,
                                                    t.kind == "floating"))[:4]
        for terminal in terminals:
            shape = geometry.terminals.get(terminal.id)
            if shape is None or shape.is_empty:
                continue
            point = np.asarray(shape.centroid.coords[0]) * 1e6
            ax.plot(*point, marker="o", markersize=4.5, color=INK, markerfacecolor="white", zorder=30)
            annotations.append(ax.annotate(terminal.id, point, xytext=(4, 6), textcoords="offset points",
                               fontsize=10, color=INK, zorder=31,
                               bbox={"facecolor": "white", "edgecolor": "none", "pad": 1, "alpha": .85}))
    _axes(ax, geometry.domain.bounds)
    if annotations:
        _separate_labels(ax, annotations)


def _separate_labels(ax, annotations):
    """Keep nearby terminal names distinct without moving their physical markers."""
    ax.figure.canvas.draw()
    renderer = ax.figure.canvas.get_renderer()
    occupied = []
    for label in annotations:
        for x, y, alignment in [(4, 6, "left"), (4, -18, "left"), (-4, 6, "right"),
                                (-4, -18, "right"), (4, 23, "left"), (4, -35, "left")]:
            label.set_position((x, y))
            label.set_ha(alignment)
            box = label.get_window_extent(renderer).expanded(1.06, 1.16)
            if not any(box.overlaps(other) for other in occupied):
                occupied.append(box)
                break


def _fit_ticks(ax):
    """Choose tick density after equal-aspect layout has determined the actual panel width."""
    from matplotlib.ticker import MaxNLocator
    ax.apply_aspect()
    width_inches = ax.get_position().width * ax.figure.get_figwidth()
    if width_inches < 1.3:
        ax.xaxis.set_major_locator(MaxNLocator(2, min_n_ticks=2))


def field_data(result, layer, field):
    """Select only valid active triangles; never color inactive nodes or hole interiors."""
    values = np.asarray(result.fields[layer][field], dtype=float)
    if values.ndim > 1:
        values = np.linalg.norm(values, axis=1)
    ids = result.mesh.elements(layer)
    nodal = field in {"potential", "temperature"}
    active = values[np.unique(result.mesh.triangles[ids])] if nodal else values[ids]
    finite = active[np.isfinite(active)]
    if not len(finite):
        raise SimulationError(f"No finite {field} field on {layer}")
    valid_triangles = (np.all(np.isfinite(values[result.mesh.triangles[ids]]), axis=1)
                       if nodal else np.isfinite(values[ids]))
    return values, ids[valid_triangles], nodal, float(finite.min()), float(finite.max())


def _field_scale(field, maximum):
    if field == "potential" and abs(maximum) < 1:
        return 1e3, "mV"
    unit = FIELD_UNITS.get(field, "")
    return display_factor(unit), display_unit(unit)


def draw_field(ax, result, info):
    from matplotlib.colors import LinearSegmentedColormap, Normalize
    from matplotlib.tri import Triangulation
    values, ids, nodal, low, high = field_data(result, info.layer, info.field)
    scale, unit = _field_scale(info.field, max(abs(low), abs(high)))
    cmap = LinearSegmentedColormap.from_list("gallery_teal", ["#F2F5F3", "#BDD5CC", "#55927F", "#174D40"])
    triangulation = Triangulation(result.mesh.points[:, 0] * 1e6, result.mesh.points[:, 1] * 1e6,
                                 result.mesh.triangles[ids])
    lower, upper = low * scale, high * scale
    # A numerically uniform field must not turn rounding noise into a full-color
    # gradient or unreadable 16-digit tick labels. Disclose the actual value.
    uniform = upper - lower <= max(abs(lower), abs(upper), 1e-30) * 1e-9
    if uniform:
        midpoint = (lower + upper) / 2
        half_range = max(abs(midpoint) * .001, 1e-9)
        lower, upper = midpoint - half_range, midpoint + half_range
    norm = Normalize(lower, upper)
    if nodal:
        plotted = ax.tripcolor(triangulation, np.nan_to_num(values) * scale,
                               shading="gouraud", cmap=cmap, norm=norm, rasterized=True)
    else:
        plotted = ax.tripcolor(triangulation, facecolors=values[ids] * scale,
                               shading="flat", cmap=cmap, norm=norm, rasterized=True)
    active_points = result.mesh.points[np.unique(result.mesh.triangles[ids])]
    _axes(ax, (*active_points.min(axis=0), *active_points.max(axis=0)))
    _fit_ticks(ax)
    return plotted, {"layer": info.layer, "field": info.field, "unit": FIELD_UNITS[info.field],
                     "minimum": low, "maximum": high, "display_unit": unit,
                     "display_scale": scale, "normalization": "linear", "uniform": uniform,
                     "display_minimum": lower, "display_maximum": upper}


def render_example(project, geometry, result, info, output):
    _style()
    import matplotlib.pyplot as plt
    from matplotlib.ticker import FuncFormatter
    figure = plt.figure(figsize=(10, 5.2))
    figure.text(.075, .935, f"{info.id}  {info.title}", fontsize=16, weight="semibold", ha="left")
    operation = "transient " if project.transient.enabled and info.category != "Transient" else ""
    figure.text(.075, .88, f"{info.category} · {operation}{project.solver.mode} · dimensions in µm", color=GRAY)
    left = figure.add_axes((.075, .245, .365, .51))
    right = figure.add_axes((.565, .245, .365, .51))
    draw_geometry(left, project, geometry, info)
    left.set_title("Prepared geometry", loc="left", pad=10)
    details = None
    if result:
        plotted, details = draw_field(right, result, info)
        title = info.field.replace("_", " ").capitalize()
        if info.field in {"current_density", "sheet_current"}:
            title += " magnitude"
        if result.transient is not None:
            time_s = result.metrics["transient_time"]
            title += f" at {time_s * 1e6:g} µs"
            details["time_s"] = time_s
            details["time_selection"] = "last accepted state"
        title = f"{title} · {info.layer}"
        if details["uniform"]:
            title += f"\nUniform: {details['minimum'] * details['display_scale']:.6g} {details['display_unit']}"
        right.set_title(title, loc="left", pad=10)
        bar_ax = figure.add_axes((.585, .10, .325, .022))
        low, high = plotted.norm.vmin, plotted.norm.vmax
        span = high - low
        magnitude = max(abs(low), abs(high))
        precision = max(3, int(np.ceil(np.log10(magnitude / span))) + 1) if magnitude else 3
        bar = figure.colorbar(plotted, cax=bar_ax, orientation="horizontal",
                              ticks=np.linspace(low, high, 3),
                              format=FuncFormatter(lambda value, _: f"{value:.{precision}g}"))
        bar.set_label(details["display_unit"], labelpad=2)
        bar.outline.set_linewidth(.5)
    else:
        right.axis("off")
        right.text(.5, .5, "Geometry only\nNo solve requested", ha="center", va="center", transform=right.transAxes,
                   color=GRAY, linespacing=1.8)
    count = len(project.layers)
    figure.text(.075, .10, f"{count} physical {'layer' if count == 1 else 'layers'} · dashed outlines: interfaces\n"
                "Field panel uses its active-layer extent; scales vary by example.", fontsize=9.7, color=GRAY)
    for extension in ("png", "svg"):
        target = output.with_suffix("." + extension)
        figure.savefig(target, dpi=192,
                       metadata={"Date": None} if extension == "svg" else None)
        if extension == "svg":
            _clean_svg(target)
    plt.close(figure)
    return details


def render_overview(items, output):
    """Compact six-panel geometric index for the repository README."""
    if not items:
        return
    _style()
    import matplotlib.pyplot as plt
    count = len(items)
    rows = (count + 2) // 3
    height = 3.1 * rows + 1.2
    figure = plt.figure(figsize=(10, height))
    figure.text(.075, 1 - .3 / height, "Complex layouts, ready to explore", fontsize=16,
                weight="semibold", va="top")
    for index, (project, geometry, info) in enumerate(items):
        # Reserve titles, axes and labels in physical inches. Fractional title
        # offsets grew with row count and collided with the preceding x label.
        column, row = index % 3, index // 3
        left, top = .075 + column * .31, height - .9 - row * 3.1
        ax = figure.add_axes((left, (top - 2.35) / height, .235, 1.85 / height))
        draw_geometry(ax, project, geometry, info, labels=False)
        ax.set_anchor("N")
        title = textwrap.wrap(f"{info.id} · {info.title}", width=32, break_long_words=False)
        figure.text(left, top / height, "\n".join(title), fontsize=10.5,
                    va="top", linespacing=1.25)
        ax.tick_params(labelsize=9)
        ax.set_xlabel("x (µm)", fontsize=9)
        ax.set_ylabel("y (µm)", fontsize=9)
    figure.text(.075, .2 / height, "Actual prepared geometry · independent panel scales · dashed outlines show interfaces",
                fontsize=10, color=GRAY)
    for extension in ("png", "svg"):
        target = output.with_suffix("." + extension)
        figure.savefig(target, dpi=192,
                       metadata={"Date": None} if extension == "svg" else None)
        if extension == "svg":
            _clean_svg(target)
    plt.close(figure)


def cached_result(project, cache, fingerprint):
    receipt = cache.with_suffix(".json")
    if not cache.is_file() or not receipt.is_file():
        return None
    try:
        metadata = json.loads(receipt.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if (metadata.get("physics_hash") != physics_hash(project)
            or metadata.get("source_fingerprint") != fingerprint or metadata.get("solver_version") != __version__):
        return None
    try:
        _, result = load_project(cache)
    except (OSError, ValueError, SimulationError):
        return None
    if result is None or not result.converged or result.physics_hash != physics_hash(project):
        return None
    return result


def _relative(path, parent):
    return Path(os.path.relpath(path, parent)).as_posix()


def _number(value, unit=""):
    return f"{value:.4g}{' ' + unit if unit else ''}"


def write_markdown(records, output, markdown, examples):
    asset = lambda name: _relative(output / name, markdown.parent)  # noqa: E731
    lines = ["# Example gallery", "", f"{len(records)} runnable examples, from analytical checks to multilayer devices. "
             "Each preview pairs actual prepared geometry with a computed field. Open an ETL project to retain "
             "materials, boundary conditions and saved sweeps; GDSII and OASIS contain geometry only.", "",
             "All devices are synthetic teaching configurations. Results are deterministic for the recorded mesh; "
             "no experimental uncertainty or process calibration is claimed. Current peaks at ideal corners need "
             "a mesh-convergence study. Field scales and panel extents vary and are explicitly labeled. "
             "SVG previews retain vector geometry, axes and text; only the finite-element field is rasterized "
             "at twice the intended display resolution to keep downloads compact.", "",
             "## Choose an example", "", "| Example | Focus | Level | Preview mode |",
             "|---|---|---|---|"]
    for record in records:
        info = record["catalog"]
        operation = "transient " if record.get("transient") else ""
        lines.append(f"| [{info['id']} · {info['title']}](#{info['id'].lower()}) | {info['category']} | "
                     f"{info['level']} | {operation}{record['mode']} |")
    from etlayout.power_examples import FAMILIES
    if {record["name"] for record in records} == set(ROUTINE_EXAMPLES):
        lines[2] = ("24 runnable reference examples. The first 19 previews show computed fields; "
                    "five larger [power families](power-examples.md) have separate measured benchmark "
                    "receipts and actual-application views. Generation does not launch simulations. "
                    "ETL projects retain physical setup; GDSII/OASIS contain geometry only.")
        for name in FAMILIES:
            info = example_info(name)
            lines.append(f"| [{info.id} · {info.title}](power-examples.md#reference-projects) | "
                         f"{info.category} | {info.level} | electrothermal |")
    lines += ["", "## Run locally", "", "Run these commands from the repository root after installation. "
              "The reference environment is Windows 11 with Python 3.12. Other platforms are unverified.", "", "```powershell",
              r".\.venv\Scripts\python.exe -m etlayout gui examples/E11_serpentine_heater.etl",
              r".\.venv\Scripts\python.exe -m etlayout solve examples/E11_serpentine_heater.etl --mode electrothermal --output results/E11",
              r".\.venv\Scripts\python.exe -m etlayout sweep examples/E11_serpentine_heater.etl --output results/E11-sweep",
              "```", "", "The desktop **File → Ready-to-run examples** menu opens the same configurations. "
              "The per-example solve command below reproduces the preview mode; the saved sweep is a separate experiment.", ""]
    for record in records:
        info, name = record["catalog"], record["name"]
        field_alt = (f" and {info['field'].replace('_', ' ')} on {info['layer']}"
                     if record.get("result") else " only (no solve)")
        lines += [f"<a id=\"{info['id'].lower()}\"></a>", f"## {info['id']} · {info['title']}", "",
                  f"**{info['level']} · {info['category']}.** {info['learning_goal']}", "",
                  f"[![{info['title']}: prepared geometry{field_alt}]({asset(name + '.png')})]"
                  f"({asset(name + '.svg')})", ""]
        downloads = [f"[{label}]({_relative(examples / (name + ext), markdown.parent)})"
                     for label, ext in [("Open project (.etl)", ".etl"), ("GDSII", ".gds"), ("OASIS", ".oas")]]
        layers = "; ".join(f"`{layer['id']}` ({layer['material']}, {_number(layer['thickness_m'] * 1e6, 'µm')})"
                           for layer in record["physical_layers"])
        lines += [" · ".join([*downloads, f"[SVG preview]({asset(name + '.svg')})"]), "",
                  f"**Operating point:** {record['bias_summary']}. Preview mode: **{record['mode']}**.", "",
                  f"**Physical layers:** {layers}.", "",
                  f"**Saved sweep:** {record['sweep_summary']}", "",
                  f"**Physical scope:** {info['physical_scope']} {record['notes']}", ""]
        if record.get("transient"):
            transient = record["transient"]
            lines += [f"**Transient setup:** stop {_number(transient['stop_time'], 's')}; "
                      f"maximum step {_number(transient['time_step'], 's')}; saved output interval "
                      f"{_number(transient['output_interval'], 's')}. {record['waveform_summary']} "
                      "The field preview shows the final accepted time; the saved project opens the full time workflow.", ""]
        if record.get("result"):
            result = record["result"]
            metrics = result["metrics"]
            temperature_label = "maximum temperature at final time" if record.get("transient") else "overall maximum temperature"
            summaries = [_number(metrics[key], METRIC_UNITS.get(key, "")) + " " + label
                         for key, label in [("effective_resistance", "effective resistance"),
                                            ("electrical_power", "electrical power"),
                                            ("temperature_max", temperature_label)]
                         if isinstance(metrics.get(key), (int, float))]
            if record.get("transient"):
                summaries.extend([_number(metrics["transient_peak_temperature"], "K") + " peak over all time steps",
                                  _number(metrics["transient_input_energy"], "J") + " integrated input heat"])
            lines += [f"**Computed preview:** {'; '.join(summaries)}. "
                      f"{result['mesh_nodes']:,} nodes / {result['mesh_triangles']:,} triangles; converged. "
                      f"[Full metrics and hashes]({asset('manifest.json')}).", ""]
            diagnostics = result["diagnostics"]
            warnings = [d["message"] for d in diagnostics if d["severity"] in {"warning", "error"}]
            if warnings:
                lines += ["**Diagnostics:** " + " ".join(dict.fromkeys(warnings)), ""]
        else:
            lines += ["**Preview:** geometry only; no numerical result was generated.", ""]
        lines += ["```powershell", rf".\.venv\Scripts\python.exe -m etlayout solve examples/{name}.etl "
                  f"--mode {record['mode']} --output results/{info['id']}", "```", ""]
    lines += ["## Rebuild this gallery", "", "```powershell",
              r".\.venv\Scripts\python.exe scripts/build_example_gallery.py --use-cache",
              r".\.venv\Scripts\python.exe scripts/build_example_gallery.py --examples E11 E16 --output .artifacts/gallery-preview --markdown .artifacts/gallery-preview.md",
              "```", "", "The generator reads existing projects from `examples/` and creates missing ones. "
              "Its cache is reused only when the physics hash, solver version and source-code fingerprint match. "
              "Use `--no-solve` for explicitly labeled geometry-only previews. "
              "The manifest records the plotted range, units, mesh, convergence, diagnostics, operating point and configuration hashes.", ""]
    markdown.parent.mkdir(parents=True, exist_ok=True)
    markdown.write_text("\n".join(lines), encoding="utf-8")


def build_gallery(names, output, markdown, examples, cache_dir, *, use_cache=False, no_solve=False,
                  overview=True):
    output, markdown, examples, cache_dir = map(Path, (output, markdown, examples, cache_dir))
    output.mkdir(parents=True, exist_ok=True)
    cache_dir.mkdir(parents=True, exist_ok=True)
    fingerprint, records, overview_items = source_fingerprint(), [], []
    for name in names:
        info = example_info(name)
        if info.name not in EXAMPLE_NAMES:
            raise ValueError(f"{info.name} is catalogued but not implemented")
        project_path = examples / (info.name + ".etl")
        project = load_project(project_path)[0] if project_path.is_file() else make_example(info.name, examples)
        project = deepcopy(project)
        project.solver.mode = info.recommended_mode
        geometry = prepare_geometry(project)
        failures = [d.message for d in geometry.diagnostics if d.severity == "error"]
        if failures:
            raise SimulationError("; ".join(failures))
        result = None
        cache = cache_dir / (info.name + ".etl")
        if not no_solve:
            result = cached_result(project, cache, fingerprint) if use_cache else None
            if result is None:
                print(f"Solving {info.name} ({project.solver.mode})", flush=True)
                result = run_project(project)
                if not result.converged:
                    raise SimulationError(f"{info.name} did not converge; gallery not published")
                save_project(project, cache, result)
                cache.with_suffix(".json").write_text(json.dumps({"physics_hash": physics_hash(project),
                    "source_fingerprint": fingerprint, "solver_version": __version__}, indent=2), encoding="utf-8")
        details = render_example(project, geometry, result, info, output / info.name)
        terminals = [{"id": t.id, "kind": t.kind, "value": t.value, "layer": t.layer_id,
                      "unit": "V" if t.kind in {"voltage", "ground", "gate"} else "A" if t.kind == "current" else ""}
                     for t in project.terminals]
        bias = "; ".join(f"{t['id']} {_number(t['value'], t['unit'])} ({t['kind']})" for t in terminals)
        sources = [{"id": h.id, "value": h.value, "kind": h.kind} for h in project.heat_sources]
        if sources:
            bias += "; prescribed heat: " + ", ".join(f"{h['id']} {_number(h['value'], 'W' if h['kind'] == 'total' else 'W/m²')}"
                                                       for h in sources)
        sweeps = [asdict(sweep) for sweep in project.sweeps]
        sweep_summary = "; ".join(f"`{s['parameter']}` = `{s['values']}` (SI values, {s['mode']})" for s in sweeps)
        waveform_summary = []
        if project.transient.enabled:
            for drive in project.transient.drives:
                wave = drive.waveform
                if wave.kind == "pulse":
                    summary = (f"`{drive.target}`: {wave.low:g} → {wave.high:g}, delay {wave.delay:g} s, "
                        f"rise {wave.rise_time:g} s, plateau {wave.width:g} s, fall {wave.fall_time:g} s, "
                        f"period {wave.period:g} s")
                elif wave.kind == "piecewise_linear":
                    summary = f"`{drive.target}`: piecewise linear, times {wave.times} s, values {wave.values}"
                else:
                    summary = f"`{drive.target}`: constant {wave.value:g}"
                waveform_summary.append(summary + " (target SI units).")
        record = {"name": info.name, "catalog": info.to_dict(), "mode": project.solver.mode,
                  "configuration_hash": configuration_hash(project), "physics_hash": physics_hash(project),
                  "layout_sha256": project.layout.sha256, "bias_summary": bias, "terminals": terminals,
                  "heat_sources": sources, "sweeps": sweeps, "sweep_summary": sweep_summary or "None.",
                  "transient": asdict(project.transient) if project.transient.enabled else None,
                  "waveform_summary": " ".join(waveform_summary),
                  "notes": project.notes, "field": details,
                  "physical_layers": [{"id": layer.id, "material": layer.material_id, "z_m": layer.z,
                                       "thickness_m": layer.thickness} for layer in project.layers],
                  "downloads": {ext: _relative(examples / (info.name + "." + ext), output) for ext in ("etl", "gds", "oas")}}
        if result:
            record["result"] = {"configuration_hash": result.configuration_hash, "physics_hash": result.physics_hash,
                "solver_version": result.solver_version, "converged": result.converged,
                "mesh_nodes": len(result.mesh.points), "mesh_triangles": len(result.mesh.triangles),
                "metrics": _jsonable(result.metrics), "terminals": result.terminals,
                "diagnostics": [asdict(d) for d in result.diagnostics]}
        records.append(record)
        if info.level == "Advanced":
            overview_items.append((project, geometry, info))
        print(f"Rendered {info.name}", flush=True)
    if overview:
        render_overview(overview_items, output / "complex-overview")
    manifest = {"schema_version": GALLERY_SCHEMA, "generated_utc": datetime.now(UTC).isoformat(),
                "source_fingerprint": fingerprint, "solver_version": __version__, "visual_spec": VISUAL_SPEC,
                "examples": records}
    (output / "manifest.json").write_text(json.dumps(_jsonable(manifest), indent=2, ensure_ascii=False,
                                                    allow_nan=False) + "\n", encoding="utf-8")
    write_markdown(records, output, markdown, examples)
    return manifest


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=ROOT / "docs/example-gallery")
    parser.add_argument("--markdown", type=Path, default=ROOT / "docs/examples.md")
    parser.add_argument("--examples", nargs="+", default=list(ROUTINE_EXAMPLES),
                        help="Explicit IDs; engineering power benchmarks are opt-in, never implicitly solved")
    parser.add_argument("--example-dir", type=Path, default=ROOT / "examples")
    parser.add_argument("--cache", type=Path, default=ROOT / ".artifacts/example-gallery-cache")
    parser.add_argument("--use-cache", action="store_true")
    parser.add_argument("--no-solve", action="store_true")
    parser.add_argument("--no-overview", action="store_true")
    args = parser.parse_args(argv)
    names = list(dict.fromkeys(example_info(name).name for name in args.examples))
    build_gallery(names, args.output, args.markdown, args.example_dir, args.cache,
                  use_cache=args.use_cache, no_solve=args.no_solve, overview=not args.no_overview)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
