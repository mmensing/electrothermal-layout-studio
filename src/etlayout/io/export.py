# SPDX-License-Identifier: MPL-2.0
"""Configuration-linked numerical tables, VTU fields, plots and HTML reports."""
from __future__ import annotations

import hashlib
from pathlib import Path

import numpy as np

from etlayout.io.project import _json_dump, project_from_dict, save_project
from etlayout.io.reporting import (
    LIMITATIONS,
    csv_dict_writer,
    csv_writer,
    prepare_report_context,
    render_report,
    safe_filename,
)
from etlayout.models import SimulationResult
from etlayout.simulation.analysis import FIELD_UNITS
from etlayout.visualization.formatting import distinct_labels
from etlayout.visualization.palettes import get_colormap
from etlayout.visualization.sampling import FIELD_LABELS, metric_unit
from etlayout.visualization.sampling import METRIC_UNITS as METRIC_UNITS
from etlayout.visualization.support import display_limits, field_support, region_elements
from etlayout.visualization.units import display_unit, to_display

# Compatibility aliases retained for existing Python callers.
_safe = safe_filename


def _filenames(specifications):
    """Allocate all artifacts together, including case-insensitive filesystem names."""
    reserved = {"results.json", "metrics.csv", "terminals.csv", "report.html", "manifest.json",
                "simulation.etl", "difference-fields.npz", "transient_history.json", "transient_history.csv",
                "transient_history.svg", "transient_history.png"}
    candidates = {key: _safe(stem) + extension for key, stem, extension in specifications}
    counts = {}
    for name in candidates.values():
        counts[name.casefold()] = counts.get(name.casefold(), 0) + 1
    output, used = {}, set(reserved)
    for key, _, extension in sorted(specifications, key=lambda item: repr(item[0])):
        name = candidates[key]
        if counts[name.casefold()] > 1 or name.casefold() in used:
            digest = hashlib.sha256(repr(key).encode("utf-8")).hexdigest()
            stem = name[:-len(extension)]
            size = 10
            name = stem[:88] + "_" + digest[:size] + extension
            while name.casefold() in used:
                size += 2
                name = stem[:max(1, 98 - size)] + "_" + digest[:size] + extension
        output[key] = name
        used.add(name.casefold())
    return output


def _range(limits, automatic, logarithmic=False):
    selected = automatic if limits is None else tuple(map(float, limits))
    if selected is None:
        return None
    low, high = selected
    if not np.isfinite([low, high]).all() or low > high or (logarithmic and low <= 0):
        raise ValueError("Color limits must be finite, ordered, and positive for a logarithmic scale")
    if limits is not None and low == high:
        raise ValueError("Manual color limits require a maximum greater than the minimum")
    return low, high


def _style():
    import matplotlib
    matplotlib.use("Agg")
    matplotlib.rcParams.update({"font.family": "DejaVu Sans", "font.size": 10,
        "axes.labelsize": 10, "axes.titlesize": 12, "axes.titleweight": "normal",
        "axes.spines.top": False, "axes.spines.right": False, "axes.linewidth": .7,
        "axes.edgecolor": "#6F6F6F", "text.color": "#111111", "axes.labelcolor": "#404040",
        "xtick.color": "#404040", "ytick.color": "#404040", "figure.facecolor": "white",
        "axes.facecolor": "white", "savefig.facecolor": "white", "svg.fonttype": "none", "text.usetex": False})


def export_field_plot(result: SimulationResult, layer: str, field: str, path: str | Path,
                      logarithmic: bool = False, *, region: str = "", colormap: str = "teal",
                      limits=None, provenance=None, figure_options=None, resolution_scale=None):
    _style()
    import matplotlib.pyplot as plt
    from matplotlib.colors import ListedColormap, LogNorm, Normalize
    from matplotlib.tri import Triangulation
    support = field_support(result, layer, field, region=region, logarithmic=logarithmic)
    precision_scale = support.resolution_scale if resolution_scale is None else float(resolution_scale)
    if not np.isfinite(precision_scale) or precision_scale < 0:
        raise ValueError("Display resolution scale must be finite and nonnegative")
    cmap = get_colormap(colormap)
    provenance = provenance or result.configuration.get("visualization", {})
    selected = display_limits(support.limits, limits=limits, logarithmic=logarithmic,
                              colormap=colormap, provenance=provenance, resolution_scale=precision_scale)
    if not len(support.indices) or selected is None:
        return None
    # Never retain unused global points: matplotlib otherwise includes unrelated
    # geometry in the plot extent and can interpolate across unavailable regions.
    # Local vertices preserve independent values at point-touch components.
    xy = result.mesh.points[result.mesh.triangles[support.indices]].reshape(-1, 2) * 1e6
    triangles = np.arange(len(xy)).reshape(-1, 3)
    tri = Triangulation(xy[:, 0], xy[:, 1], triangles)
    low, high = selected
    constant = low == high
    if constant:
        padding = max(abs(low) * 1e-6, 1e-12)
        low, high = (low / 1.001, high * 1.001) if logarithmic else (low - padding, high + padding)
        cmap = ListedColormap([cmap(.5)], name=colormap + "_constant")
    # Distinct IEEE values can have identical logarithms. Normalize their
    # differences first, rather than handing LogNorm a singular log interval.
    # Across this roundoff-sized span the logarithm is linear to float precision.
    log_roundoff = logarithmic and np.log10(low) == np.log10(high)
    norm = Normalize(0., 1., clip=True) if log_roundoff else (LogNorm if logarithmic else Normalize)(vmin=low, vmax=high, clip=True)
    aspect = float(np.ptp(xy[:, 0]) / max(np.ptp(xy[:, 1]), 1e-30))
    target = Path(path)
    if figure_options is not None:
        figure_options.validate()
    size = figure_options.size_inches(target.suffix.lower()) if figure_options else ((5.2, 6.2) if aspect < .6 else (7.1, 4.8))
    fig, ax = plt.subplots(figsize=size, layout="constrained")
    automatic = support.limits
    below, above = automatic[0] < low, automatic[1] > high
    extend = "both" if below and above else "min" if below else "max" if above else "neither"
    if support.nodal and provenance.get("smooth", True):
        fractions = np.linspace(0., 1., 257)
        levels = np.exp((1 - fractions) * np.log(low) + fractions * np.log(high)) if logarithmic else (1 - fractions) * low + fractions * high
        # A near-isothermal field can span a single floating-point step: 257
        # nominal levels then collapse to duplicates. Preserve finite endpoints.
        levels = np.clip(levels, low, high)
        levels[0], levels[-1] = low, high
        levels = np.unique(levels[np.isfinite(levels)])
        # Contouring interpolates P1 scalar values before mapping them to color.
        # RGB Gouraud interpolation would be wrong for nonlinear palettes/logs.
        values = support.element_data[support.indices].ravel()
        if log_roundoff:
            values = (values - low) / (high - low)
            levels = fractions
        plotted = ax.tricontourf(tri, values, levels=levels, cmap=cmap, norm=norm,
                                 extend=extend)
        plotted.set_rasterized(not (figure_options is not None and target.suffix.lower() in {".pdf", ".svg"}))
    else:
        values = (support.values - low) / (high - low) if log_roundoff else support.values
        plotted = ax.tripcolor(tri, facecolors=values, shading="flat", cmap=cmap, norm=norm,
                               rasterized=not (figure_options is not None and target.suffix.lower() in {".pdf", ".svg"}))
    if figure_options is not None:
        # Avoid PDF/SVG viewer antialias seams looking like a false mesh overlay.
        plotted.set_edgecolor("face")
        plotted.set_linewidth(.05)
        plotted.set_antialiased(False)
    name, unit = FIELD_LABELS.get(field, (field.replace("_", " ").capitalize(), FIELD_UNITS.get(field, "")))
    si_unit = unit
    unit = display_unit(unit)
    if provenance.get("kind") == "difference":
        name = "Δ " + name
    time_label = f" · t = {result.metrics['time_s']:.9g} s" if "time_s" in result.metrics else ""
    if provenance.get("kind") == "difference":
        from etlayout.visualization.timing import difference_time_label
        timing = difference_time_label(provenance)
        time_label = "\n" + timing if timing else ""
    title = f"{name} ({unit}) · {layer}" + (f" · {region}" if region else "") + time_label
    if figure_options is not None and figure_options.title:
        title = figure_options.title + time_label
    if figure_options is not None:
        import textwrap
        title = textwrap.fill(title, width=max(25, int(size[0] * 11)))
    ax.set(title=title, xlabel="x (µm)", ylabel="y (µm)", aspect="equal")
    if figure_options is None or figure_options.colorbar:
        shrink = min(.85, .85 * size[0] / (aspect * size[1])) if figure_options is not None and aspect > 3 else .85
        bar = fig.colorbar(plotted, ax=ax, shrink=shrink, pad=.04, panchor=False, extend=extend)
        if figure_options is not None and aspect > 3 and not constant and not log_roundoff:
            middle = np.exp((np.log(low) + np.log(high)) / 2) if logarithmic else low / 2 + high / 2
            bar.set_ticks([low, middle, high])
        if figure_options is not None and target.suffix.lower() in {".pdf", ".svg"} and bar.solids is not None:
            bar.solids.set_rasterized(False)
        if constant:
            bar.set_ticks([selected[0]])
        elif log_roundoff:
            bar.set_ticks([0., 1.], labels=[f"{to_display(low, si_unit):.17g}", f"{to_display(high, si_unit):.17g}"])
        if not log_roundoff:
            ticks = bar.get_ticks()
            bar.set_ticks(ticks, labels=distinct_labels(to_display(value, si_unit) for value in ticks))
        bar.set_label(f"{name} ({unit})" + (" · constant" if constant else "") + (" · log scale" if logarithmic else ""))
    elif figure_options is not None:
        # Quantity and units remain visible even if both optional decorations are hidden.
        ax.set_xlabel(f"x (µm) · {name} ({unit})")
    notes = []
    if figure_options is not None:
        notes.append("2.5D effective-sheet result; review model calibration and mesh uncertainty in the manifest")
        if figure_options.legend:
            notes.append(f"{name} ({unit}) · {layer}")
        if figure_options.annotations:
            notes.append(figure_options.annotations)
    if (limits is None and not logarithmic and precision_scale and
            support.limits[1] - support.limits[0] <= 8 * np.finfo(float).eps * precision_scale):
        notes.append("Temperature-rise variation is at floating-point resolution of absolute temperature; automatic scale padded, raw values preserved")
    if limits is not None:
        notes.append("Manual color range; values outside limits are clipped")
    if logarithmic:
        notes.append("Nonpositive support omitted")
    if log_roundoff:
        notes.append("Roundoff-level log range: stable linearized normalization")
    if provenance.get("kind") == "difference":
        notes.append("Current − comparison · " + provenance.get("quantity", "scalar difference") + " on common finite support")
    if provenance.get("transient_completed") is False:
        notes.append("Snapshot from incomplete transient run")
    if notes:
        import textwrap
        wrap = max(25, int(size[0] * 14)) if figure_options is not None else (85 if aspect < .6 else 110)
        note = textwrap.fill(" · ".join(notes), width=wrap)
        lines = note.count("\n") + 1
        if figure_options is not None and lines > max(3, int(size[1] * 72 * .32 / 9)):
            plt.close(fig)
            raise ValueError("Annotations and scientific warnings need more figure height; enlarge the figure or shorten annotations")
        fig.text(.5, .01, note, ha="center", fontsize=8)
        footer = ((lines * 9 + 8) / (size[1] * 72)) if figure_options is not None else (.065 if "\n" in note else .045)
        fig.get_layout_engine().set(rect=(0, footer, 1, 1 - footer))
    if not result.converged:
        ax.text(.02, .98, "NOT CONVERGED", transform=ax.transAxes, va="top", color="#B5473C")
    target.parent.mkdir(parents=True, exist_ok=True)
    metadata = None
    if figure_options is not None:
        if target.suffix.lower() == ".pdf":
            metadata = {"Creator": "Electrothermal Layout Studio", "Producer": "Electrothermal Layout Studio",
                        "CreationDate": None, "ModDate": None, "Title": figure_options.title or "Selected field"}
        elif target.suffix.lower() == ".svg":
            metadata = {"Date": None, "Creator": "Electrothermal Layout Studio", "Title": figure_options.title or "Selected field"}
        else:
            metadata = {"Software": "Electrothermal Layout Studio", "Title": figure_options.title or "Selected field"}
    try:
        if figure_options is not None:
            from matplotlib import rc_context
            with rc_context({"svg.hashsalt": "ElectrothermalLayoutStudio", "pdf.fonttype": 42, "text.parse_math": False}):
                fig.savefig(target, dpi=figure_options.dpi, metadata=metadata)
        else:
            fig.savefig(target, dpi=200, metadata=metadata)
    finally:
        plt.close(fig)
    if target.suffix.lower() == ".svg":
        target.write_text("\n".join(line.rstrip() for line in target.read_text(encoding="utf-8").splitlines()) + "\n", encoding="utf-8")
    return target


def export_profile(profile: dict, path: str | Path):
    target = Path(path)
    sampled_values = np.asarray(profile["value"], dtype=float)
    with target.open("w", newline="", encoding="utf-8") as stream:
        writer = csv_writer(stream)
        writer.writerow(["distance_m", "x_m", "y_m", f"{profile['field']} [{profile.get('unit','')}]", "configuration_hash"])
        for d, x, y, value in zip(profile["distance_m"], profile["x_m"], profile["y_m"], sampled_values, strict=True):
            writer.writerow([d, x, y, value if np.isfinite(value) else "", profile.get("configuration_hash", "")])
    _style()
    import matplotlib.pyplot as plt
    fig, ax = plt.subplots(figsize=(7.1, 4.3), layout="constrained")
    ax.plot(np.asarray(profile["distance_m"])*1e6, to_display(sampled_values, profile.get("unit", "")), color="#1F6F5F", lw=1.7)
    field_name = "Sheet conductance tensor norm" if profile["field"] == "sheet_conductance" else profile["field"].replace("_", " ")
    ax.set(xlabel="Distance along cut (µm)", ylabel=f"{field_name} ({display_unit(profile.get('unit',''))})",
           title=f"{profile['layer_id']} · line profile")
    for extension in (".svg", ".png"):
        fig.savefig(target.with_suffix(extension), dpi=200)
    plt.close(fig)


def export_results(result: SimulationResult, directory: str | Path, plots: bool = True, *,
                   layers=None, field: str | None = None, region: str = "", colormap: str = "teal",
                   limits=None, logarithmic: bool = False, provenance=None):
    """Export full results, or deliberately select a displayed layer/field/region.

    Numerical values retain SI units and are never clipped to the color range.
    A filtered normal export includes the full source archive for reproducibility;
    a derived difference instead carries its arrays and paired-run provenance.
    """
    get_colormap(colormap)  # reject an unknown palette before writing artifacts
    _range(limits, None, logarithmic)
    selected_layers = list(result.fields) if layers is None else ([layers] if isinstance(layers, str) else list(layers))
    if any(layer not in result.fields for layer in selected_layers):
        raise ValueError("An export layer is not present in this result")
    selected_layers = list(dict.fromkeys(selected_layers))
    context = prepare_report_context(result, provenance=provenance)
    provenance = context.provenance
    difference = context.difference
    metrics = {} if difference else result.metrics
    terminals = {} if difference else result.terminals
    selection = {"layers": selected_layers, "field": field, "region": region, "colormap": colormap,
                 "limits": list(limits) if limits is not None else None, "logarithmic": logarithmic,
                 "scope": "displayed selection" if layers is not None or field or region else "full result",
                 "numerical_values": "SI values; color clipping does not alter data"}
    plans, specifications = {}, []
    for layer in selected_layers:
        raw = result.fields[layer]
        fields = [field] if field else list(raw)
        supports = {key: field_support(result, layer, key, region=region,
                                       logarithmic=logarithmic if field else False) for key in fields}
        ids = supports[field].indices if field else region_elements(result, layer, region)
        if not len(ids):
            continue
        plot_fields = [key for key in fields if len(supports[key].indices) and
                       (field or key in {"potential", "current_density", "joule_density", "temperature", "temperature_rise"})]
        plans[layer] = (ids, supports, plot_fields)
        specifications.extend([((layer, "vtu"), layer, ".vtu"),
                               ((layer, "nodes"), layer + "_nodes", ".csv"),
                               ((layer, "elements"), layer + "_elements", ".csv")])
        if plots:
            specifications.extend(((layer, key, extension), layer + "_" + key, extension)
                                  for key in plot_fields for extension in (".png", ".svg"))
    filenames = _filenames(specifications)
    root = Path(directory)
    root.mkdir(parents=True, exist_ok=True)
    metadata = {"configuration_hash": result.configuration_hash, "converged": result.converged,
                "execution": result.execution,
                "solver_version": result.solver_version, "elapsed_seconds": result.elapsed_seconds,
                "metrics": metrics, "terminals": terminals, "mesh": result.mesh.statistics,
                "configuration": result.configuration, "iterations": result.iterations,
                "analysis": result.analysis, "field_version": result.field_version,
                "diagnostics": [d.__dict__ for d in result.diagnostics], "limitations": LIMITATIONS,
                "selection": selection, "visualization": provenance,
                "archive_scope": "derived difference arrays only" if difference else "full source result"}
    (root / "results.json").write_text(_json_dump(metadata), encoding="utf-8")
    with (root / "metrics.csv").open("w", newline="", encoding="utf-8-sig") as stream:
        writer = csv_writer(stream)
        writer.writerow(["metric", "value", "unit", "configuration_hash"])
        for name, value in metrics.items():
            if isinstance(value, (float, int, str, bool)):
                writer.writerow([name, value, metric_unit(name), result.configuration_hash])
    with (root / "terminals.csv").open("w", newline="", encoding="utf-8-sig") as stream:
        keys = sorted({k for terminal in terminals.values() for k in terminal})
        writer = csv_writer(stream)
        writer.writerow(["terminal"] + keys)
        for name, values in terminals.items():
            writer.writerow([name] + [values.get(k, "") for k in keys])
    image_files = []
    manifest = {"schema_version": 1, "configuration_hash": result.configuration_hash,
                "selection": selection, "visualization": provenance, "layers": {},
                "shared_files": ["results.json", "metrics.csv", "terminals.csv", "report.html", "manifest.json"]}
    difference_arrays = {}
    centroids, areas = result.mesh.centroids, result.mesh.areas
    import meshio
    for layer, (ids, supports, plot_fields) in plans.items():
        raw = result.fields[layer]
        values = {key: np.asarray(raw[key]) if key in raw else support.data for key, support in supports.items()}
        layer_config = next((x for x in result.configuration.get("layers", []) if x["id"] == layer), {})
        coordinates = np.column_stack((result.mesh.points, np.full(len(result.mesh.points), layer_config.get("z", 0.))))
        node_ids = np.unique(result.mesh.triangles[ids])
        nodes = {key: supports[key].element_data for key in values if supports[key].nodal}
        elements = {key: [value[ids]] for key, value in values.items() if key not in nodes}
        # VTU uses element-local vertices so coincident independent DOFs survive.
        local_node_ids = result.mesh.triangles[ids].ravel()
        meshio.write_points_cells(root / filenames[layer, "vtu"], coordinates[local_node_ids],
                                  [("triangle", np.arange(len(local_node_ids)).reshape(-1, 3))],
                                  point_data={key: value[ids].ravel() for key, value in nodes.items()}, cell_data=elements)
        layer_manifest = {"vtu": filenames[layer, "vtu"], "nodes": filenames[layer, "nodes"],
                          "elements": filenames[layer, "elements"], "plots": {}, "fields": {},
                          "node_count": len(node_ids), "triangle_count": len(ids)}
        manifest["layers"][layer] = layer_manifest
        if difference:
            support_key = f"layer_{len(manifest['layers']) - 1}_elements"
            difference_arrays[support_key] = ids
            layer_manifest["element_indices_key"] = support_key
        for key, value in values.items():
            color_limits = display_limits(supports[key].limits, limits=limits, logarithmic=logarithmic,
                                          colormap=colormap, provenance=provenance,
                                          resolution_scale=supports[key].resolution_scale)
            layer_manifest["fields"][key] = {"location": "element_local_node" if supports[key].nodal else "element",
                "quantity": "scalar" if value.ndim == 1 else "tensor" if key == "sheet_conductance" else "vector",
                "unit": FIELD_UNITS.get(key, ""), "finite_triangle_count": len(supports[key].indices),
                "data_limits": supports[key].limits,
                "plot_unit": display_unit(FIELD_UNITS.get(key, "")),
                "plot_limits": tuple(to_display(v, FIELD_UNITS.get(key, "")) for v in color_limits) if color_limits is not None else None,
                "display_limits": color_limits,
                "plot_sampling": "P1 scalar contours (up to 256 color intervals)" if supports[key].nodal and provenance.get("smooth", True)
                                 else "constant scalar per triangle"}
            if difference:
                array_key = f"layer_{len(manifest['layers']) - 1}_field_{len(difference_arrays)}"
                authoritative = supports[key].element_data if supports[key].nodal else value
                selected_values = np.full(authoritative.shape, np.nan)
                selected_values[ids] = authoritative[ids]
                difference_arrays[array_key] = selected_values
                layer_manifest["fields"][key]["array_key"] = array_key
        with (root / filenames[layer, "nodes"]).open("w", newline="", encoding="utf-8-sig") as stream:
            writer = csv_writer(stream)
            writer.writerow(["node", "x_m", "y_m", "z_m", "element", "local_vertex"] + [f"{k} [{FIELD_UNITS.get(k,'')}]" for k in nodes])
            for element in ids:
                for vertex, node in enumerate(result.mesh.triangles[element]):
                    writer.writerow([node, *coordinates[node], element, vertex] + [values[element, vertex] for values in nodes.values()])
        with (root / filenames[layer, "elements"]).open("w", newline="", encoding="utf-8-sig") as stream:
            writer = csv_writer(stream)
            element_fields, element_units = {}, {}
            for key, value in values.items():
                if key in nodes:
                    continue
                if value.ndim == 1:
                    element_fields[key] = value[ids]
                    element_units[key] = FIELD_UNITS.get(key, "")
                else:
                    components = value.reshape(len(value), -1)
                    suffixes = ("x", "y") if components.shape[1] == 2 else tuple(str(i) for i in range(components.shape[1]))
                    for dimension, suffix in enumerate(suffixes):
                        element_fields[key + "_" + suffix] = components[ids, dimension]
                        element_units[key + "_" + suffix] = FIELD_UNITS.get(key, "")
                    if key != "sheet_conductance":
                        element_fields[key + "_magnitude"] = np.linalg.norm(components[ids], axis=1)
                        element_units[key + "_magnitude"] = FIELD_UNITS.get(key, "")
            writer.writerow(["element", "x_m", "y_m", "area_m2"] +
                            [f"{k} [{element_units[k]}]" for k in element_fields])
            for j, element in enumerate(ids):
                writer.writerow([element, *centroids[element], areas[element]] +
                                [v[j] for v in element_fields.values()])
        if plots:
            for key in plot_fields:
                layer_manifest["plots"][key] = {}
                for extension in (".png", ".svg"):
                    filename = filenames[layer, key, extension]
                    generated = export_field_plot(result, layer, key, root / filename, logarithmic,
                        region=region, colormap=colormap, limits=limits, provenance=provenance)
                    if generated:
                        layer_manifest["plots"][key][extension[1:]] = filename
                if "png" in layer_manifest["plots"][key]:
                    image_files.append(layer_manifest["plots"][key]["png"])
    if difference:
        np.savez_compressed(root / "difference-fields.npz", points=result.mesh.points,
                            triangles=result.mesh.triangles, **difference_arrays)
        manifest["shared_files"].append("difference-fields.npz")
    elif result.configuration:
        save_project(project_from_dict(result.configuration), root / "simulation.etl", result)
        manifest["shared_files"].append("simulation.etl")
    if result.transient is not None:
        from etlayout.io.transient_export import export_transient_history
        manifest["transient"] = {"snapshot_times_s": result.transient.times.tolist(),
                                  "completed": result.transient.completed,
                                  "field_map_time_s": float(result.transient.times[-1])}
        manifest["shared_files"].extend(export_transient_history(result, root, plots=plots))
        if plots:
            image_files.append("transient_history.png")
    (root / "manifest.json").write_text(_json_dump(manifest), encoding="utf-8")
    scope = ("Derived difference arrays are saved in difference-fields.npz. Original solver metrics and terminal quantities do not describe this difference and are omitted."
             if difference else "simulation.etl contains the full source result, including fields outside the exported selection.")
    # Embed generated PNGs so the HTML remains portable and CSP needs no file/network access.
    images = [(root.joinpath(filename).read_bytes(), filename) for filename in image_files]
    document = render_report(context, images=images, selection=selection, metadata=metadata, scope=scope)
    (root / "report.html").write_text(document, encoding="utf-8")
    return root / "report.html"


def export_study(rows, directory: str | Path, parameter: str = "parameter"):
    root = Path(directory)
    root.mkdir(parents=True, exist_ok=True)
    metrics = ["effective_resistance", "electrical_power", "temperature_max", "temperature_rise",
               "current_density_max", "thermal_resistance", "voltage_drop", "total_current"]
    metrics += sorted({name for row in rows for name, value in row.metrics.items()
                       if isinstance(value, (int, float)) and name not in metrics})
    records = []
    for i, row in enumerate(rows):
        value = getattr(row, "value", getattr(row, "name", i))
        record = {parameter: value, "error": row.error, "converged": bool(row.result and row.result.converged)}
        record["requested_value"] = value
        record["applied_value"] = getattr(row, "applied_value", value)
        record.update({m: row.metrics.get(m) for m in metrics})
        if row.result:
            record["configuration_hash"] = row.result.configuration_hash
            if row.result.configuration:
                save_project(project_from_dict(row.result.configuration), root / f"run_{i:03d}.etl", row.result)
            for j, profile in enumerate(row.result.metrics.get("mesh_profiles", [])):
                export_profile(profile, root / f"profile_run_{i:03d}_cut_{j:02d}.csv")
        records.append(record)
    (root / "study.json").write_text(_json_dump(records), encoding="utf-8")
    keys = list(dict.fromkeys([parameter, "requested_value", "applied_value", "converged", "error"] + metrics + ["configuration_hash"]))
    with (root / "study.csv").open("w", newline="", encoding="utf-8-sig") as stream:
        writer = csv_dict_writer(stream, keys)
        writer.writeheader()
        writer.writerows(records)
    _style()
    import matplotlib.pyplot as plt
    numeric = all(isinstance(r[parameter], (float, int)) for r in records)
    for metric in metrics:
        present = [r for r in records if r.get(metric) is not None and r["converged"]]
        if not present:
            continue
        fig, ax = plt.subplots(figsize=(7.1, 4.4), layout="constrained")
        if numeric:
            present.sort(key=lambda r: r[parameter])
            ax.plot([r[parameter] for r in present], [to_display(r[metric], metric_unit(metric)) for r in present], "o-", color="#1F6F5F", lw=1.6, ms=4)
            ax.set_xlabel(parameter)
            ax.set_ylabel(f"{metric.replace('_',' ')} ({display_unit(metric_unit(metric))})")
        else:
            ax.scatter([to_display(r[metric], metric_unit(metric)) for r in present], np.arange(len(present)), color="#1F6F5F", s=30)
            ax.set_yticks(np.arange(len(present)), [str(r[parameter]) for r in present])
            ax.set_xlabel(f"{metric.replace('_',' ')} ({display_unit(metric_unit(metric))})")
            ax.invert_yaxis()
        ax.set_title(metric.replace("_", " ").capitalize())
        for suffix in (".svg", ".png"):
            fig.savefig(root / (_safe(metric) + suffix), dpi=200)
        plt.close(fig)
    return root / "study.csv"
