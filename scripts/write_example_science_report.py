# SPDX-License-Identifier: MPL-2.0
"""Build a concise source-linked forty-example matrix from actual audit receipts."""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from dataclasses import asdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))


def compact(record):
    """Retain engineering quantities and provenance, excluding bulky field data."""
    metrics = record["electrical"]
    return dict(id=record["id"], name=record["name"], evidence_level=record["evidence_level"],
        purpose=record["purpose"], supported_conclusions=record["supported_conclusions"],
        unsupported_claims=record["unsupported_claims"], recommended_action=record["recommended_action"],
        layout_sha256=record["layout_sha256"], physics_hash=record["physics_hash"],
        configuration_hash=record["configuration_hash"], analysis_mode=record["analysis_mode"],
        electrical=metrics, loss_categories_W=record["loss_budget"].get("categories", {}),
        electrical_thermal_deposition_W=record["loss_budget"].get("thermal_deposition"),
        excluded_fixture_power_W=record["loss_budget"].get("excluded_power"),
        thermal=record["thermal"], assumptions=record["assumptions"], status=record["status"],
        geometry=record["geometry"], contact_resolution=record["contact_resolution"],
        layers=record["layers"], interfaces=record["interfaces"], boundaries=record["boundaries"],
        prescribed_sources=record["prescribed_sources"], measurements=record["measurements"],
        mesh=dict(nodes=record["mesh"]["nodes"], elements=record["mesh"]["elements"], settings=record["mesh"]["settings"]),
        diagnostics=record["diagnostics"], units=record["units"],
        resource_observation=record.get("resource_observation", {}), portability=record.get("portability", {}),
        performance=dict(cpu_threads=record["performance"].get("cpu_threads"),
            phase_seconds=record["performance"].get("timings_seconds", {})))


def number(value):
    return "—" if value is None else f"{value:.6g}"


def write_report(root, output, revision):
    from etlayout.example_catalog import example_info
    from etlayout.examples import EXAMPLE_NAMES

    records = [json.loads((root / example_info(name).id / "receipt.json").read_text(encoding="utf-8")) for name in EXAMPLE_NAMES]
    if any(record.get("name") != name for record, name in zip(records, EXAMPLE_NAMES, strict=True)):
        raise ValueError("All forty exact named scientific receipts are required")
    from etlayout.models import ElectricalModel, Interface, MeasurementSpec

    def without_defaults(value, defaults):
        if not isinstance(value, dict) or not isinstance(defaults, dict):
            return value
        return {key: without_defaults(item, defaults.get(key)) for key, item in value.items()
                if key not in defaults or item != defaults[key]}

    rows = [compact(record) for record in records]
    materials = {}
    for row in rows:
        for layer in row["layers"]:
            material = {key: layer.pop(key) for key in ("material_source", "material_notes", "properties")}
            material["id"] = layer["material"]
            digest = hashlib.sha256(json.dumps(material, sort_keys=True).encode()).hexdigest()[:12]
            reference = layer["material"] + ":" + digest
            materials[reference] = material
            layer["material_reference"] = reference
            layer["electrical"] = without_defaults(layer["electrical"], asdict(ElectricalModel()))
        row["interfaces"] = [without_defaults(item, asdict(Interface("", "", ""))) for item in row["interfaces"]]
        for measurement in row["measurements"].values():
            measurement.pop("configuration_hash", None)
            measurement.pop("mesh_triangles", None)
            measurement["specification"] = without_defaults(measurement["specification"], asdict(MeasurementSpec("", "")))
        row.pop("units")
    output.mkdir(parents=True, exist_ok=True)
    manifest = dict(schema_version=1, source_revision=revision, units=records[0]["units"], materials=materials,
        implicit_defaults=dict(electrical=asdict(ElectricalModel()), interface=asdict(Interface("", "", "")),
            measurement=asdict(MeasurementSpec("", ""))),
        observation="Original configured nominals, actual source calculations; one mesh/time grid per project",
        calibration="None of these synthetic examples establishes an experimentally calibrated device rating",
        examples=rows)
    (output / "nominal-reference.json").write_text(json.dumps(manifest, ensure_ascii=False, allow_nan=False, indent=2) + "\n", encoding="utf-8")
    lines = ["# Exact nominal observations: E1–E40", "", f"Source revision: `{revision}`.", "",
        "All forty configured nominals were calculated without substituting drives, loads, analysis mode or tolerances. These single-grid observations verify function and conservation; mesh/time verification and physical calibration remain unestablished. Values are rounded for reading; the [machine-readable matrix](nominal-reference.json) retains SI values, support, sources, model diagnostics and loss categories.", "",
        "## Electrical service and disjoint losses", "",
        "`I` and `V` use the result's declared total-current and resistance-terminal definitions; multiport examples require inspecting individual ports before treating this as a two-terminal device. Device loss is separate from conventional contact/via loss. Thermal-only or cooled pulse-end electrical quantities may be absent or zero.", "",
        "| Example | I (A) | V (V) | Electrical (W) | Sheet Joule (W) | Device load (W) | Contact/via (W) | Feed (W) |",
        "| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |"]
    for row in rows:
        m, c = row["electrical"], row["loss_categories_W"]
        vals = [m.get("total_current"), m.get("voltage_drop"), m.get("electrical_power"), m.get("joule_power"),
                c.get("device_load"), c.get("contact"), m.get("external_feed_power")]
        lines.append("| " + row["id"] + " | " + " | ".join(map(number, vals)) + " |")
    lines += ["", "## Thermal response and numerical scope", "",
        "`Tfinal` is the displayed last frame. `Trun peak` uses all accepted transient steps; it is not inferred from a cooled final field. Rise uses the named/declared reservoir. Every entry remains mesh unverified; transient entries also remain time unverified. A dash is unavailable, not zero. Legacy nominals have no typed hard-temperature limit configured.", "",
        "| Example | Tfinal (K) | Trun peak (K) | Peak rise (K) | Peak time (s) | Elements | Converged | Model observations |",
        "| --- | ---: | ---: | ---: | ---: | ---: | --- | --- |"]
    for row in rows:
        t, s = row["thermal"], row["status"]
        peak = t.get("transient_peak_temperature") or t.get("temperature_max")
        reference = t.get("thermal_reference")
        rise = peak - reference if peak is not None and reference is not None else None
        issues = s["declared_model_violations"] + [item["layer"] + ":property range" for item in s["property_range_violations"]] + s["measurement_failures"]
        values = [t.get("temperature_max"), peak, rise, t.get("transient_peak_time"), row["mesh"]["elements"]]
        lines.append("| " + row["id"] + " | " + " | ".join(map(number, values)) + " | " + str(s["numerically_converged"]) + " | " + (", ".join(issues) or "No observed declared-range violation; uncalibrated") + " |")
    lines += ["", "The JSON records include ambient/initial temperatures, hotspot coordinates, thermal input/removal and transient energy; fixed support measurements, layer-resolved K/J/E ranges, geometry diagnostics, contact-transfer resolution screens and measured phases. Review those records before comparing raw maxima. Detailed archives and process-memory receipts are CI/local artifacts, not source data.", "", "[Interpretation and reproduction](../example-science.md).", ""]
    (output / "nominal-reference.md").write_text("\n".join(lines), encoding="utf-8")
    return manifest


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("receipts", type=Path)
    parser.add_argument("--output", type=Path, default=Path("docs/example-science"))
    parser.add_argument("--revision", required=True, help="Actual source revision of the retained calculations")
    args = parser.parse_args()
    write_report(args.receipts, args.output, args.revision)
