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


GUI_EXERCISES = {
    "E1": ("Open Metrics & loss budget and compare the resistance with the uniform-strip value in the reference details.",
           "A smooth voltage drop along the straight strip; the fixed-temperature resistance follows its length, width and thickness.",
           "Use Parameter sweep… to vary the drain voltage in DC electrical mode. Does the resistance stay constant while Joule power rises?"),
    "E2": ("Zoom into the inside bend, then enable Show vectors in Appearance to follow current around it.",
           "Current bunches at the reentrant corner and redistributes through the taper.",
           "Save a copy and use Edit layout to widen the neck. Does an averaged loading measure improve when the mesh is refined?"),
    "E3": ("Compare both branch widths and their common access buses before inspecting the current map.",
           "The wider route carries more current, but shared access resistance also affects the division.",
           "Change one branch in a project-owned layout copy. How much does current sharing change without changing total delivered current?"),
    "E4": ("Switch Layer between m1 and m2; use Fit layer after each switch to follow the finite interlayer transfer.",
           "Each sheet has its own potential distribution, joined through the declared via footprint.",
           "Change contact refinement in Mesh & checks. Which voltage drops remain stable as that region is resolved?"),
    "E5": ("Zoom into the repeated via footprints and compare the two sheet layers at the same operating point.",
           "Transfer is distributed across the array; distance from the feed can make individual vias load unequally.",
           "Would moving the physical feed change loading more than adding another row of vias? Keep planar metal density distinct from normal via current density."),
    "E6": ("Inspect the sense_a and sense_b terminal values in Quantitative results as well as the sheet potential.",
           "The floating sense electrodes have zero net current, while their equipotential footprints can locally perturb the film.",
           "How does the sensed voltage differ from a two-terminal voltage that also includes the current contacts?"),
    "E7": ("In Operating conditions, choose Electrical inputs / gate controls and inspect gate_control. Run a second gate bias and use History to compare.",
           "The gated region changes conductance, while the documented ungated edges still provide a bypass path.",
           "Compare the response with E16. Does every source-to-drain path cross a gate-controlled region?"),
    "E8": ("Inspect the source_metal current map, then compare the layer thicknesses with E7 in Layers & materials.",
           "The thinner access metal changes routing resistance and current crowding while retaining the same illustrative channel law.",
           "Which part of the voltage drop comes from routing, and which from the channel? Compare at equal delivered current before calling a layout better."),
    "E9": ("Inspect the sic layer's electrical model in Layers & materials, then run its fixed-temperature electrical calculation.",
           "An effective doped-SiC resistive sheet with finite contacts; this example has no transistor gate or junction model.",
           "How does changing mobility affect sheet resistance? For a distributed MOSFET on-state load, continue with E23 or E24."),
    "E10": ("Switch Layer between the metal and substrate, then compare Temperature with Temperature rise when a reservoir reference is available.",
           "Heat spreads laterally in the substrate while the configured stack and backside boundary set the thermal path.",
           "Change the backside condition in Operating conditions. Which layer changes most, and why is it misleading to inspect only the metal?"),
    "E11": ("Inspect the heater temperature, then switch to the substrate and look beyond the meander footprint.",
           "Bends concentrate electrical loading, while the effective interface and substrate spread the deposited heat.",
           "Vary current with Parameter sweep… and compare temperature and resistance. Keep the configured prescribed heat input when reproducing the nominal project."),
    "E12": ("Switch Layer among m1, m2 and m3, using Fit layer to follow the two via banks and the middle riser.",
           "Current transfers vertically twice; a map of one metal level does not describe loading in the others.",
           "Draw a profile through a transfer region and compare it with a finer mesh. Can you separate a persistent bottleneck from a corner peak?"),
    "E13": ("Inspect the narrow ligaments around slots and diamond holes; preview the mesh to check that voids stay empty.",
           "Current detours around genuine holes, concentrating in the remaining conducting paths.",
           "Compare a physically averaged hotspot with the largest element value while refining near a hole. Which conclusion is stable?"),
    "E14": ("Inspect arm_br and the floating midpoint terminal values in Quantitative results.",
           "A differential bridge signal caused by unequal effective sheet resistances, with no mechanical strain calculation.",
           "Change one arm's thickness in a copied setup. How do bridge imbalance and total power respond?"),
    "E15": ("Choose the shared substrate layer and inspect both heat-source neighborhoods on one temperature scale.",
           "Overlapping heat-spreading fields and warming outside the driven meanders; narrow temperature ranges need distinct tick labels.",
           "Change one configured heat input and compare saved runs at a common manual range. Does the unpowered neighborhood still warm?"),
    "E16": ("Inspect the four channel stripes, then vary gate_control through Operating conditions and compare the channel maps.",
           "Every intended conducting path crosses a gate-controlled region, avoiding E7/E8's edge bypass.",
           "Compare gate-bias response with E7, then move to E21 for long fingers. The numerical conductance floor is not an off-state leakage prediction."),
    "E17": ("Open Time, waveform & initial-state settings… in Operating conditions, inspect Timing and Waveforms, then run and open Time history.",
           "Uniform prescribed heating and cooling with thermal storage; the field can be nearly uniform even while the time history changes strongly.",
           "Use Time-step convergence… to compare peak temperature and pulse energy. Does a shorter integration step resolve the pulse edges?"),
    "E18": ("Inspect the current waveform, run, then select retained times and use Time history to view temperature and Stored energy.",
           "Joule heating during current pulses, thermal delay and cooling between pulses, with temperature-dependent electrical feedback.",
           "Change the pulse plateau or cooling interval in Waveforms, then refine time. This models thermal dynamics, not electromagnetic switching losses."),
    "E19": ("Compare heater, substrate and unpowered receiver layers, then inspect their Time history scopes.",
           "A delayed receiver temperature peak after a prescribed heat pulse transfers through the shared structure.",
           "Change an interface thermal parameter and compare the receiver's peak time as well as its maximum temperature."),
    "E20": ("Inspect all metal levels and the Analysis Contacts and Loss budget tables.",
            "Branching sheet currents and unequal via transfer under localized physical feeds.",
            "Compare routing choices at equal delivered current, then check mesh sensitivity and the configured model limits."),
    "E21": ("Inspect channel sheet current, then the source_rdl metal map and the Devices table.",
            "Finite feed/contact resistance creates finger loading and source-potential differences across fully gated paths.",
            "Compare single, dual or distributed feeds at fixed active width, current and thermal environment."),
    "E22": ("Compare bank groups and shared redistribution, then inspect substrate temperature.",
            "Electrical sharing and thermal interaction across hierarchical HEMT banks; hierarchy does not reduce FEM cost.",
            "Move only the feed landing in a controlled variant and compare bank current per active width."),
    "E23": ("Inspect source/collector current and the Analysis Loss budget table.",
            "A distributed SiC on-state load with local current sharing and separate device/interconnect losses.",
            "Compare source collection choices at fixed active area, delivered current and reservoir conditions."),
    "E24": ("Inspect collection metal, macrocell groups and the passive Kelvin-sense measurement.",
            "Local sheet voltages determine macrocell sharing and sense error under localized feeds.",
            "Compare bond, clip and bump footprints without changing active area or requested service."),
}


def write_markdown(records, output, markdown, examples):
    from etlayout.power_examples import FAMILIES
    from etlayout.visualization.sampling import DISPLAY_FIELD_LABELS
    from etlayout.workflow import ANALYSES

    asset = lambda name: _relative(output / name, markdown.parent)  # noqa: E731
    full_catalog = {record["name"] for record in records} == set(ROUTINE_EXAMPLES)
    power_guide = _relative(ROOT / "docs/power-examples.md", markdown.parent)
    labels = {(analysis.solver_mode, analysis.transient): analysis.label for analysis in ANALYSES}
    count = len(records) + (len(FAMILIES) if full_catalog else 0)
    lines = ["# Explore the examples", "",
             "See current crowd around a bend, follow heat into a substrate, or watch a device cool between pulses. "
             f"The library contains {count} runnable {'reference ' if full_catalog else ''}examples with configured "
             "geometry, materials, connections and operating conditions. These walkthroughs use the desktop controls.", "",
             "## Open your first example", "",
             "1. In **File → Ready-to-run examples**, choose an example by its stable ID below. "
             "Opening prepares the project; it does not start a simulation.",
             "2. Use **Save project as…** to keep your own project. Review the visible **Analysis type**; "
             "the gallery's analysis column states the mode used for its recorded picture.",
             "3. Follow **Layout & region → Layers & materials → Contacts & connections → Operating conditions**. "
             "The example already supplies these inputs; inspect them before changing anything.",
             "4. In **Mesh & checks**, select **Run validation**, inspect the Issues drawer, then "
             "**Generate / update mesh**. **Run → Run analysis** starts the background calculation. "
             "F6, F7 and F5 provide the corresponding shortcuts.",
             "5. In **Results**, select **Layer** and **Quantity**, then **Fit layer**. "
             "Use **Appearance** for colors and range, **Metrics & loss budget** for numbers, and **History** for earlier runs.",
             "6. Try the follow-up question for your example. **Sweeps & convergence** opens parameter, mesh and "
             "time-step studies; **Compare saved runs…** helps inspect alternatives.", ""]
    if full_catalog:
        showcase = _relative(ROOT / "docs/images/showcase/workspace-overview.png", markdown.parent)
        lines += [f"![Actual application: E12 layout and mesh in the numbered workflow]({showcase})", "",
                  "*A real E12 workflow view. The large canvas, contextual inspector and Issues/Jobs/History drawer "
                  "stay together while you move through the numbered stages.*", "",
                  "## Choose a question", "", "| What do you want to understand? | Start here | Then explore |",
                  "| --- | --- | --- |",
                  "| How do geometry and current paths set resistance? | [E1: straight strip](#e1) | [E2: bend and taper](#e2), [E3: parallel paths](#e3) |",
                  "| Where do multilayer routes become bottlenecks? | [E4: two metals](#e4) | [E5: via array](#e5), [E12: via ladder](#e12), [E13: perforated spreader](#e13) |",
                  "| What does the voltage probe actually measure? | [E6: Kelvin sensing](#e6) | [E14: resistive bridge](#e14) |",
                  "| How does a gate change the conducting channel? | [E7: illustrative HEMT](#e7) | [E8: routing resistance](#e8), [E16: fully gated stripes](#e16) |",
                  "| How far does heat spread? | [E10: layered thermal path](#e10) | [E11: meander heater](#e11), [E15: thermal crosstalk](#e15) |",
                  "| What happens between pulses? | [E17: heating and cooling](#e17) | [E18: pulsed conductor](#e18), [E19: delayed receiver heating](#e19) |",
                  f"| How do larger power structures share current? | [E20–E24: power guide]({power_guide}) | [GaN fingers and banks]({power_guide}#e21), [SiC source collection]({power_guide}#e24) |", ""]
    lines += ["## All examples", "", "| Example | Focus | Level | Gallery analysis |", "| --- | --- | --- | --- |"]
    for record in records:
        info = record["catalog"]
        label = labels[(record["mode"], bool(record.get("transient")))]
        lines.append(f"| [{info['id']} · {info['title']}](#{info['id'].lower()}) | {info['category']} | {info['level']} | {label} |")
    if full_catalog:
        for name in FAMILIES:
            info = example_info(name)
            lines.append(f"| [{info.id} · {info.title}]({power_guide}#{info.id.lower()}) | "
                         f"{info.category} | {info.level} | Coupled DC electrothermal |")
    lines += ["", "## Read the pictures, then run your own project", "",
              "Each scientific preview pairs actual prepared geometry with a recorded computed field. "
              "All devices are synthetic teaching configurations; deterministic calculations do not supply "
              "experimental uncertainty or process calibration. A converged solve still needs a mesh study "
              "before comparing sensitive peaks. Field scales and panel extents differ between pictures.", "",
              "The recorded preview details below retain their original operating points, diagnostics and "
              "numerical provenance. Some were calculated with earlier solver versions; the manifest records "
              "those identities. They are illustrations, not fresh validation of a newly opened project. "
              "Current application screenshots are labeled separately.", "",
              "ETL projects retain the physical setup and saved sweeps. GDSII/OASIS downloads contain geometry only. "
              "SVG previews retain vector geometry, axes and text; only the finite-element field is rasterized "
              "at twice the intended display resolution.", ""]
    for record in records:
        info, name = record["catalog"], record["name"]
        action, observation, question = GUI_EXERCISES[info["id"]]
        field_label, field_unit = DISPLAY_FIELD_LABELS[info["field"]]
        analysis = labels[(record["mode"], bool(record.get("transient")))]
        field_alt = (f" and {info['field'].replace('_', ' ')} on {info['layer']}"
                     if record.get("result") else " only (no solve)")
        lines += [f"<a id=\"{info['id'].lower()}\"></a>", f"## {info['id']} · {info['title']}", "",
                  f"**{info['level']} · {info['category']}.** {info['learning_goal']}", "",
                  f"[![{info['title']}: prepared geometry{field_alt}]({asset(name + '.png')})]({asset(name + '.svg')})", "",
                  f"**In the GUI:** choose **{analysis}**, then in Results select layer `{info['layer']}` "
                  f"and **{field_label} ({field_unit})**. {action}", "",
                  f"**Look for:** {observation}", "", f"**Try next:** {question}", ""]
        downloads = [f"[{label}]({_relative(examples / (name + ext), markdown.parent)})"
                     for label, ext in [("Project (.etl)", ".etl"), ("GDSII", ".gds"), ("OASIS", ".oas")]]
        lines += [" · ".join([*downloads, f"[Vector preview]({asset(name + '.svg')})"]), "",
                  "<details>", "<summary>Recorded inputs, model limits and numerical details</summary>", ""]
        layers = "; ".join(f"`{layer['id']}` ({layer['material']}, {_number(layer['thickness_m'] * 1e6, 'µm')})"
                           for layer in record["physical_layers"])
        mode = ("transient " if record.get("transient") else "") + record["mode"]
        lines += [f"**Recorded operating point:** {record['bias_summary']}. Preview mode: **{mode}**.", "",
                  f"**Physical layers:** {layers}.", "", f"**Saved sweep:** {record['sweep_summary']}", "",
                  "The saved sweep is a separate experiment; review the selected parameter, values and analysis "
                  "in **Parameter sweep…** before starting it.", "",
                  f"**Physical scope:** {info['physical_scope']} {record['notes']}", ""]
        if record.get("transient"):
            transient = record["transient"]
            lines += [f"**Transient setup:** stop {_number(transient['stop_time'], 's')}; "
                      f"maximum step {_number(transient['time_step'], 's')}; saved output interval "
                      f"{_number(transient['output_interval'], 's')}. {record['waveform_summary']} "
                      "The field preview shows the final accepted time; use Time history to inspect the full run.", ""]
        if record.get("result"):
            result, metrics = record["result"], record["result"]["metrics"]
            temperature_label = "maximum temperature at final time" if record.get("transient") else "overall maximum temperature"
            summaries = [_number(metrics[key], METRIC_UNITS.get(key, "")) + " " + label
                         for key, label in [("effective_resistance", "effective resistance"),
                                            ("electrical_power", "electrical power"),
                                            ("temperature_max", temperature_label)]
                         if isinstance(metrics.get(key), (int, float))]
            if record.get("transient"):
                summaries.extend([_number(metrics["transient_peak_temperature"], "K") + " peak over all time steps",
                                  _number(metrics["transient_input_energy"], "J") + " integrated input heat"])
            lines += [f"**Recorded computed preview:** {'; '.join(summaries)}. "
                      f"{result['mesh_nodes']:,} nodes / {result['mesh_triangles']:,} triangles; converged. "
                      f"[Full metrics and hashes]({asset('manifest.json')}).", ""]
            warnings = [d["message"] for d in result["diagnostics"] if d["severity"] in {"warning", "error"}]
            if warnings:
                lines += ["**Recorded diagnostics:** " + " ".join(dict.fromkeys(warnings)), "",
                          "Review current Issues after validation; a recorded warning is not permission to ignore a current blocker.", ""]
        else:
            lines += ["**Preview:** geometry only; no numerical result was generated.", ""]
        lines += ["Optional command-line run of the current project:", "", "```powershell",
                  rf".\.venv\Scripts\python.exe -m etlayout solve examples/{name}.etl --mode {record['mode']} --output results/{info['id']}",
                  "```", "", "</details>", ""]
    lines += ["## Maintaining the gallery", "", "<details>", "<summary>Advanced: deterministic generation and reproduction</summary>", "",
              "The desktop walkthroughs above work with the existing projects. Maintainers can regenerate the "
              "scientific previews separately:", "", "```powershell",
              r".\.venv\Scripts\python.exe scripts/build_example_gallery.py --use-cache",
              r".\.venv\Scripts\python.exe scripts/build_example_gallery.py --examples E11 E16 --output .artifacts/gallery-preview --markdown .artifacts/gallery-preview.md",
              "```", "", "The generator reads existing projects from `examples/` and creates missing ones. "
              "Its cache is reused only when the physics hash, solver version and source-code fingerprint match. "
              "Use `--no-solve` for explicitly labeled geometry-only previews. "
              "The manifest records plotted range, units, mesh, convergence, diagnostics, operating point and configuration hashes.",
              "", "</details>", ""]
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
