# SPDX-License-Identifier: MPL-2.0
"""Sharing exports never smuggle a full experiment behind a cropped figure."""
from __future__ import annotations

import csv
import json
import re
import xml.etree.ElementTree as ET
import zipfile
import zlib
from dataclasses import replace
from pathlib import Path

import klayout.db as kdb
import meshio
import numpy as np
import pytest
from PIL import Image

from etlayout.io.project import configuration_hash, file_hash, load_project, physics_hash
from etlayout.io.sharing import ExportRequest, FigureOptions, execute_export, preview_export
from etlayout.models import Diagnostic, PhysicalLayer, PlanarMesh, Project, SimulationResult, TransientHistory

SECRET = "UNSELECTED_PROPRIETARY_SENTINEL"


@pytest.fixture
def experiment(tmp_path):
    layout = kdb.Layout()
    top, private = layout.create_cell("VISIBLE"), layout.create_cell(SECRET)
    layer = layout.layer(1, 0)
    top.shapes(layer).insert(kdb.Box(0, 0, 2000, 2000))
    private.shapes(layer).insert(kdb.Text(SECRET, kdb.Trans(0, 0)))
    top.insert(kdb.CellInstArray(private.cell_index(), kdb.Trans(10000, 0)))
    source = tmp_path / "source.gds"
    layout.write(str(source))
    project = Project(name=SECRET, notes=SECRET)
    project.layout.path, project.layout.sha256 = str(source), file_hash(source)
    project.layers = [PhysicalLayer("metal", "Visible", "synthetic"), PhysicalLayer(SECRET, SECRET, SECRET)]
    points = np.array([[0, 0], [2, 0], [0, 2], [10, 0], [12, 0], [10, 2]], dtype=float) * 1e-6
    mesh = PlanarMesh(points, np.array([[0, 1, 2], [3, 4, 5]]),
        {"layer:metal": np.array([0, 1]), "layer:" + SECRET: np.array([1]), "heat:selected": np.array([0])})
    result = SimulationResult(mesh, fields={"metal": {"potential": np.array([0., 2., 4., 50., 60., 70.]),
        "current_density": np.array([[3., -4.], [100., -200.]])}, SECRET: {"potential": np.arange(6.) + 1000}},
        metrics={"secret": SECRET, "total_current": 123.}, terminals={SECRET: {"current": 999.}},
        diagnostics=[Diagnostic("warning", SECRET)], configuration=project.to_dict(), converged=True,
        configuration_hash=configuration_hash(project), physics_hash=physics_hash(project), execution={"host": SECRET},
        analysis={"notes": SECRET})
    return project, result


def request(mode="data", **kwargs):
    return ExportRequest(mode=mode, layers=("metal",), fields=("potential",), region="heat:selected",
        roi=(0., 0., .5e-6, .5e-6), formats=("csv", "npz", "vtu") if mode == "data" else ("png", "pdf", "svg", "html"),
        figure=FigureOptions(width_inches=4., height_inches=3., dpi=180), **kwargs)


def scan_manifest_and_payload(directory):
    for file in directory.iterdir():
        assert SECRET.encode() not in file.read_bytes()
        if file.suffix == ".png":
            with Image.open(file) as image:
                assert SECRET not in str(image.info)
                assert "source.gds" not in str(image.info)
        elif file.suffix == ".svg":
            document = ET.fromstring(file.read_text(encoding="utf-8"))
            metadata = document.find("{http://www.w3.org/2000/svg}metadata")
            assert metadata is not None and SECRET not in ET.tostring(metadata, encoding="unicode")
        elif file.suffix == ".pdf":
            # Raw scanning alone misses compressed PDF content and metadata.
            for stream in re.findall(rb"stream\r?\n(.*?)\r?\nendstream", file.read_bytes(), re.S):
                try:
                    decoded = zlib.decompress(stream)
                except zlib.error:
                    decoded = stream
                assert SECRET.encode() not in decoded and b"source.gds" not in decoded
        elif file.suffix == ".npz":
            with zipfile.ZipFile(file) as archive:
                assert all(SECRET.encode() not in archive.read(name) for name in archive.namelist())
            with np.load(file, allow_pickle=False) as arrays:
                assert set(arrays) == {"points_m", "triangles", "values"}
                assert all(not value.dtype.hasobject for value in arrays.values())
        elif file.suffix == ".vtu":
            grid = meshio.read(file)
            assert set(grid.point_data) == {"potential"}
            assert not grid.cell_data
    text = next(directory.glob("*.manifest.json")).read_text(encoding="utf-8")
    assert "materials" not in json.loads(text) and "configuration" not in json.loads(text)
    assert not any(token in text for token in ("source.gds", "C:\\", "source.gds", "solver_version"))


def test_selected_data_clips_partial_elements_and_drops_source_history_and_unused_fields(experiment, tmp_path):
    _, result = experiment
    destination = tmp_path / "shared"
    before = result.fields["metal"]["potential"].copy()
    plan = preview_export(result, request())
    assert not destination.exists() and plan["datasets"][0]["triangle_count"] == 2
    manifest = execute_export(result, destination, request())
    assert plan == manifest
    assert set(file.name for file in destination.iterdir()) == set(manifest["files"])
    scan_manifest_and_payload(destination)
    with np.load(destination / "selection.npz", allow_pickle=False) as data:
        assert np.all(data["points_m"][:, :2] >= 0.) and np.all(data["points_m"][:, :2] <= .5e-6)
        np.testing.assert_allclose(data["values"], (data["points_m"][:, 0] + 2 * data["points_m"][:, 1]) * 1e6)
        mesh = PlanarMesh(data["points_m"][:, :2], data["triangles"], {})
        assert mesh.areas.sum() == pytest.approx(.25e-12, rel=1e-12)
        assert len(data["points_m"]) == 6
    with (destination / "selection.csv").open(encoding="utf-8-sig", newline="") as stream:
        rows = list(csv.reader(stream))
    assert rows[0][-1] == "potential [V]" and len(rows) == 7
    np.testing.assert_array_equal(result.fields["metal"]["potential"], before)
    assert result.configuration_hash == manifest["configuration_hash"]


def test_png_pdf_svg_and_sanitized_html_share_real_field_rendering(experiment, tmp_path, qapp):
    from PySide6.QtPdf import QPdfDocument
    _, result = experiment
    destination = tmp_path / "figures"
    manifest = execute_export(result, destination, request("figure", limits=(.25, 1.)))
    scan_manifest_and_payload(destination)
    with Image.open(destination / "selection.png") as image:
        assert image.size == (720, 540)
        assert image.info["dpi"] == pytest.approx((180., 180.), abs=.02)
    pdf = QPdfDocument()
    assert pdf.load(str(destination / "selection.pdf")) == QPdfDocument.Error.None_
    assert pdf.pageCount() == 1
    metadata = {field.name: str(pdf.metaData(field)) for field in QPdfDocument.MetaDataField}
    assert SECRET not in str(metadata) and "source.gds" not in str(metadata)
    assert metadata["Creator"] == "Electrothermal Layout Studio"
    assert not metadata["Author"]
    size = pdf.pagePointSize(0)
    assert size.width() == pytest.approx(297 / 25.4 * 72, abs=.01)
    assert size.height() == pytest.approx(210 / 25.4 * 72, abs=.01)
    rendered = pdf.render(0, __import__("PySide6.QtCore", fromlist=["QSize"]).QSize(1000, 707))
    assert not rendered.isNull()
    assert rendered.save(str(tmp_path / "pdf-render.png"))
    assert b"/Subtype /Image" not in (destination / "selection.pdf").read_bytes()
    svg = (destination / "selection.svg").read_text(encoding="utf-8")
    assert "Potential (V)" in svg and "Manual color range" in svg and "x (µm)" in svg
    assert '<image ' not in svg and 'viewBox="0 0 288 216"' in svg
    html = (destination / "selection.html").read_text(encoding="utf-8")
    assert "Content-Security-Policy" in html and "data:image/png;base64," in html
    assert "Selected quantities" in html and "source.gds" not in html
    assert manifest["display"]["manual_clipping"] is True


def test_complete_archive_explicitly_preserves_source_notes_history_and_identity(experiment, tmp_path):
    project, result = experiment
    archive_request = ExportRequest(mode="archive", formats=("etl",), stem="experiment")
    destination = tmp_path / "complete"
    manifest = execute_export(result, destination, archive_request, project=project, history=[result])
    assert manifest["confidential"] and "cropped view does not crop" in manifest["disclosure"]
    reopened, restored = load_project(destination / "experiment.etl")
    assert reopened.name == SECRET and restored.configuration_hash == result.configuration_hash
    assert SECRET in restored.fields and SECRET in restored.terminals
    with zipfile.ZipFile(destination / "experiment.etl") as archive:
        assert any(SECRET.encode() in archive.read(name) for name in archive.namelist())
    with pytest.raises(ValueError, match="cannot be presented"):
        preview_export(result, replace(archive_request, roi=(0., 0., 1., 1.)), project=project)


@pytest.mark.parametrize("changes", [{"stem": "../escape"}, {"stem": "C:\\escape"}, {"formats": ("etl",)},
                                    {"roi": (1., 0., 0., 1.)}, {"limits": (2., 1.)}])
def test_invalid_scope_and_paths_are_rejected_without_artifacts(experiment, tmp_path, changes):
    _, result = experiment
    selected = replace(request("figure"), **changes)
    destination = tmp_path / "invalid"
    with pytest.raises(ValueError):
        execute_export(result, destination, selected)
    assert not destination.exists()


def test_collisions_require_deliberate_replacement_and_keep_previous_bytes(experiment, tmp_path):
    _, result = experiment
    destination = tmp_path / "existing"
    execute_export(result, destination, request())
    before = {file.name: file.read_bytes() for file in destination.iterdir()}
    with pytest.raises(FileExistsError, match="explicitly"):
        execute_export(result, destination, request())
    assert before == {file.name: file.read_bytes() for file in destination.iterdir()}
    execute_export(result, destination, replace(request(), overwrite=True))


def test_snapshot_selection_exports_only_selected_time_and_discloses_incomplete_run(experiment, tmp_path):
    _, result = experiment
    first = replace(result, metrics={"time_s": 0.})
    last = replace(result, metrics={"time_s": 1.}, fields={"metal": {"potential": result.fields["metal"]["potential"] + 10.}})
    source = replace(last, transient=TransientHistory(np.array([0., 1.]), [first, last], [], False))
    destination = tmp_path / "time"
    manifest = execute_export(source, destination, request(time_s=.1))
    assert manifest["requested_time_s"] == .1 and manifest["timing"]["time_s"] == 0.
    assert manifest["timing"]["transient_completed"] is False
    with np.load(destination / "selection.npz", allow_pickle=False) as arrays:
        assert arrays["values"].max() <= 1.5
    assert "snapshots" not in (destination / "selection.manifest.json").read_text()


@pytest.mark.parametrize("mode", ["figure", "data", "archive"])
def test_real_gui_review_then_export_preserves_working_physics(experiment, qtbot, tmp_path, mode):
    from etlayout.gui.export_dialog import ExportDialog
    project, result = experiment
    initial = request(mode) if mode != "archive" else ExportRequest(mode="archive", formats=("etl",))
    dialog = ExportDialog(project, result, history=[result], initial=initial)
    qtbot.addWidget(dialog)
    dialog.show()
    identity = physics_hash(project)
    dialog.directory.setText(str(tmp_path / ("gui-" + mode)))
    assert not dialog.export_button.isEnabled()
    dialog.review()
    assert dialog.reviewed is not None and dialog.export_button.isEnabled()
    assert MODES_FOR_TEST[mode] in dialog.preview.toPlainText()
    dialog.stem.setText("reviewed-output")
    assert not dialog.export_button.isEnabled()
    dialog.review()
    dialog.perform_export()
    qtbot.waitUntil(lambda: dialog.archive_job is None, timeout=30000)
    assert dialog.exported["mode"] == mode and dialog.result() == dialog.DialogCode.Accepted
    assert physics_hash(project) == identity
    assert result.configuration_hash == configuration_hash(project)
    if mode != "archive":
        scan_manifest_and_payload(tmp_path / ("gui-" + mode))


MODES_FOR_TEST = {"figure": "Share figure or report", "data": "Share selected numerical data", "archive": "Archive complete experiment"}


def test_cli_preview_and_export_are_real_saved_field_workflows(experiment, tmp_path, capsys):
    from etlayout.cli.main import main
    from etlayout.io.project import save_project
    project, result = experiment
    source = tmp_path / "input.etl"
    save_project(project, source, result, history=[result])
    output = tmp_path / "cli-data"
    args = ["export", str(source), "--mode", "data", "--layer", "metal", "--field", "potential", "--region", "heat:selected",
            "--roi", "0", "0", "5e-7", "5e-7", "--output", str(output)]
    assert main([*args, "--preview"]) == 0 and not output.exists()
    preview = json.loads(capsys.readouterr().out)
    assert main(args) == 0
    assert json.loads(capsys.readouterr().out) == preview
    scan_manifest_and_payload(output)
    assert main(args) == 2 and "already exist" in capsys.readouterr().err
    archive = tmp_path / "cli-archive"
    assert main(["export", str(source), "--mode", "archive", "--output", str(archive)]) == 0
    capsys.readouterr()
    assert load_project(archive / "selection.etl")[0].name == SECRET


def test_authoritative_point_touch_values_and_signed_vectors_survive_selection(experiment, tmp_path):
    _, result = experiment
    # Coincident geometry is intentionally represented by distinct local P1 DOFs.
    result.mesh.points[3:] = result.mesh.points[:3]
    result.element_fields = {"metal": {"potential": np.array([[0., 2., 4.], [10., 12., 14.]])}}
    selected = replace(request(), region="", roi=None)
    execute_export(result, tmp_path / "local", selected)
    with np.load(tmp_path / "local" / "selection.npz", allow_pickle=False) as arrays:
        np.testing.assert_array_equal(arrays["values"], [0., 2., 4., 10., 12., 14.])
    vector = replace(request(), fields=("current_density",), logarithmic=True)
    execute_export(result, tmp_path / "vectors", vector)
    with np.load(tmp_path / "vectors" / "selection.npz", allow_pickle=False) as arrays:
        np.testing.assert_array_equal(arrays["values"], [[3., -4.], [3., -4.]])


def test_optional_decorations_keep_quantity_units_and_portrait_page(experiment, tmp_path, qapp):
    from PySide6.QtPdf import QPdfDocument
    _, result = experiment
    options = FigureOptions(width_inches=4., height_inches=3., dpi=180, orientation="portrait",
                            title="Literal <script> & Ω", annotations="Explicitly included µm² note", colorbar=False, legend=False)
    selected = replace(request("figure"), formats=("svg", "pdf"), figure=options)
    execute_export(result, tmp_path / "plain", selected)
    svg = (tmp_path / "plain" / "selection.svg").read_text(encoding="utf-8")
    assert "Potential (V)" in svg and "Literal &lt;script&gt; &amp; Ω" in svg
    visible = " ".join(" ".join(ET.fromstring(svg).itertext()).split())
    assert "Explicitly included µm² note" in visible
    pdf = QPdfDocument()
    assert pdf.load(str(tmp_path / "plain" / "selection.pdf")) == QPdfDocument.Error.None_
    assert pdf.pagePointSize(0).height() > pdf.pagePointSize(0).width()


def test_path_like_selected_labels_get_local_aliases_and_malformed_hashes_fail_closed(experiment, tmp_path):
    _, result = experiment
    private_layer = "C:/private/" + SECRET
    result.fields[private_layer] = result.fields.pop("metal")
    result.mesh.regions["layer:" + private_layer] = result.mesh.regions.pop("layer:metal")
    selected = replace(request(), layers=(private_layer,))
    manifest = execute_export(result, tmp_path / "aliases", selected)
    assert manifest["datasets"][0]["layer"] == "Layer 1"
    scan_manifest_and_payload(tmp_path / "aliases")
    result.configuration_hash = "C:/private/" + SECRET
    with pytest.raises(ValueError, match="SHA-256"):
        preview_export(result, selected)


def test_output_link_guard_rejects_before_writing_and_hardlink_replacement_preserves_original(experiment, tmp_path, monkeypatch):
    import os
    _, result = experiment
    destination = tmp_path / "links"
    destination.mkdir()
    outside = tmp_path / "outside.npz"
    outside.write_bytes(b"preserve outside destination")
    link = destination / "selection.npz"
    os.link(outside, link)
    actual = Path.is_symlink
    # Windows file-symlink creation needs a privilege this source-test host does
    # not grant. Instrument its classification boundary, then independently test
    # an actual NTFS hardlink: replacement must sever it without editing origin.
    monkeypatch.setattr(Path, "is_symlink", lambda path: path == link or actual(path))
    with pytest.raises(ValueError, match="symlinks"):
        execute_export(result, destination, replace(request(), overwrite=True))
    assert list(destination.iterdir()) == [link]
    monkeypatch.setattr(Path, "is_symlink", actual)
    execute_export(result, destination, replace(request(), overwrite=True))
    assert outside.read_bytes() == b"preserve outside destination"


def test_temperature_rise_retains_sub_ulp_interpolation_and_original_precision_context(experiment, tmp_path):
    _, result = experiment
    result.fields["metal"]["temperature"] = np.array([300., np.nextafter(300., 301.), 300., 350., 350., 350.])
    result.metrics["thermal_reference"] = 300.
    selected = replace(request(), fields=("temperature_rise",))
    data_manifest = execute_export(result, tmp_path / "rise-data", selected)
    assert data_manifest["datasets"][0]["reference_temperature_K"] == 300.
    with np.load(tmp_path / "rise-data" / "selection.npz", allow_pickle=False) as arrays:
        expected = arrays["points_m"][:, 0] / 2e-6 * (np.nextafter(300., 301.) - 300.)
        np.testing.assert_array_equal(arrays["values"], expected)
        assert arrays["values"].max() > 0.
    figure = replace(request("figure"), fields=("temperature_rise",), formats=("svg",))
    manifest = execute_export(result, tmp_path / "rise-figure", figure)
    limits = manifest["datasets"][0]["effective_limits_SI"]
    assert limits[1] - limits[0] > 1e-12
    svg = (tmp_path / "rise-figure" / "selection.svg").read_text(encoding="utf-8")
    visible = " ".join(" ".join(ET.fromstring(svg).itertext()).split()).replace("- ", "-")
    assert "floating-point resolution" in visible



def test_explicit_overwrite_rolls_back_previous_bundle_after_real_publication_failure(experiment, tmp_path, monkeypatch):
    import os
    _, result = experiment
    output = tmp_path / "rollback"
    execute_export(result, output, request())
    original = {path.name: path.read_bytes() for path in output.iterdir()}
    actual = os.replace
    triggered = []
    def fail_once(source, target):
        if Path(target) == output / "selection.npz" and "previous" not in Path(source).parts and not triggered:
            triggered.append(True)
            raise OSError("Synthetic disk failure after the first artifact replacement")
        return actual(source, target)
    monkeypatch.setattr(os, "replace", fail_once)
    with pytest.raises(OSError, match="Synthetic disk failure"):
        execute_export(result, output, replace(request(), overwrite=True))
    assert triggered
    assert original == {path.name: path.read_bytes() for path in output.iterdir()}


def test_custom_pdf_orientation_and_unavailable_quantities_are_validated(experiment):
    _, result = experiment
    options = FigureOptions(width_inches=4., height_inches=3., page_size="Custom", orientation="portrait")
    assert options.size_inches(".pdf") == (3., 4.) and options.size_inches(".png") == (4., 3.)
    with pytest.raises(ValueError, match="not available"):
        preview_export(result, replace(request(), fields=("temperature",)))


def test_windows_casefold_and_selected_label_collisions_never_overwrite(experiment, tmp_path):
    _, result = experiment
    for layer in ("M1", "m1", "A/B", "A\\B"):
        result.fields[layer] = {"potential": result.fields["metal"]["potential"].copy()}
        result.mesh.regions["layer:" + layer] = result.mesh.regions["layer:metal"].copy()
    layers = ("M1", "m1", "A/B", "A\\B")
    selected = replace(request(), layers=layers)
    manifest = execute_export(result, tmp_path / "labels", selected)
    assert len(manifest["datasets"]) == 4
    assert len({name.casefold() for name in manifest["files"]}) == len(manifest["files"])
    for dataset in manifest["datasets"]:
        with np.load(tmp_path / "labels" / dataset["files"]["npz"], allow_pickle=False) as arrays:
            assert arrays["values"].size == 6
    output = tmp_path / "casefold"
    execute_export(result, output, replace(request(), stem="M1"))
    before = {file.name: file.read_bytes() for file in output.iterdir()}
    for overwrite in (False, True):
        with pytest.raises(FileExistsError, match="case-insensitive"):
            execute_export(result, output, replace(request(), stem="m1", overwrite=overwrite))
    assert before == {file.name: file.read_bytes() for file in output.iterdir()}


@pytest.mark.parametrize("network", [False, True])
def test_cli_export_enforces_independent_external_and_network_grants(experiment, tmp_path, capsys, monkeypatch, network):
    from etlayout.cli.main import main
    from etlayout.io.project import save_project
    project, result = experiment
    folder = tmp_path / "external-project"
    folder.mkdir()
    source = folder / "project.etl"
    outside = Path(project.layout.path)
    save_project(project, source, result, embed_layout=False)
    accesses = []
    actual = Path.open
    def observed(path, *args, **kwargs):
        if path == outside:
            accesses.append(True)
        return actual(path, *args, **kwargs)
    monkeypatch.setattr(Path, "open", observed)
    if network:
        monkeypatch.setattr("etlayout.io.resources._network_drive", lambda path: path == outside)
    args = ["export", str(source), "--mode", "data", "--layer", "metal", "--field", "potential", "--region", "heat:selected",
            "--output", str(tmp_path / "explicit")]
    assert main(args) == 2 and accesses == []
    assert "authorization" in capsys.readouterr().err or network
    if network:
        assert main([*args, "--allow-outside-root"]) == 2 and accesses == []
        assert "network" in capsys.readouterr().err
    assert main([*args, "--allow-network"]) == 2 and accesses == []
    assert "outside-root" in capsys.readouterr().err
    flags = ["--allow-outside-root", "--allow-network"] if network else ["--allow-outside-root"]
    assert main([*args, *flags]) == 0 and accesses
    capsys.readouterr()
    scan_manifest_and_payload(tmp_path / "explicit")
    assert main(["export", str(source), "--mode", "archive", "--output", str(tmp_path / "approved-archive"), *flags]) == 0
    capsys.readouterr()
    assert load_project(tmp_path / "approved-archive" / "selection.etl")[0].name == SECRET
