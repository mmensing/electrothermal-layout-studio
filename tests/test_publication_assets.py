# SPDX-License-Identifier: MPL-2.0
"""A reviewed build copy removes authoring paths without changing runtime artwork."""
from hashlib import sha256
from importlib.resources import files
from pathlib import Path
from runpy import run_path

import pytest
from PySide6.QtCore import Qt
from PySide6.QtGui import QImage, QPainter
from PySide6.QtSvg import QSvgRenderer

ASSETS = run_path(str(Path(__file__).resolve().parents[1] / "packaging/publication_assets.py"))
pytestmark = pytest.mark.packaging


@pytest.fixture(scope="module")
def original_svg():
    content = files("pyqtgraph").joinpath("icons/icons.svg").read_bytes()
    assert sha256(content).hexdigest() == ASSETS["UPSTREAM_SVG_SHA256"]
    return content


def test_svg_copy_removes_only_three_export_attributes(original_svg):
    public = ASSETS["sanitize_pyqtgraph_svg"](original_svg)
    assert sha256(public).hexdigest() == ASSETS["PUBLIC_SVG_SHA256"]
    assert original_svg.count(b"inkscape:export-filename=") == 3
    assert b"inkscape:export-filename=" not in public
    assert ASSETS["sanitize_pyqtgraph_svg"](public) is public


@pytest.mark.parametrize("size", [(320, 450), (744, 1052)])
def test_svg_rendering_is_pixel_identical(qapp, original_svg, size):
    def render(content):
        renderer = QSvgRenderer(content)
        assert renderer.isValid()
        image = QImage(*size, QImage.Format.Format_ARGB32_Premultiplied)
        image.fill(Qt.GlobalColor.transparent)
        painter = QPainter(image)
        renderer.render(painter)
        painter.end()
        return image
    assert render(original_svg) == render(ASSETS["sanitize_pyqtgraph_svg"](original_svg))


def test_build_entry_redirect_preserves_upstream_and_other_entries(tmp_path, original_svg):
    source = tmp_path / "upstream.svg"
    source.write_bytes(original_svg)
    other = ("example.dat", "input.dat", "DATA")
    original = [(ASSETS["SVG_DESTINATION"], str(source), "DATA"), other]
    result, records = ASSETS["prepare_publication_assets"](original, tmp_path / "generated")
    generated = Path(result[0][1])
    assert generated != source and generated.is_relative_to(tmp_path / "generated")
    assert source.read_bytes() == original_svg and result[1] is other
    assert records[0]["removed_attributes"] == 3
    again, again_records = ASSETS["prepare_publication_assets"](result, tmp_path / "generated")
    assert again == result and again_records == records
    generated.write_bytes(b"unreviewed build data")
    with pytest.raises(ValueError, match="reviewed"):
        ASSETS["prepare_publication_assets"](original, tmp_path / "generated")


@pytest.mark.parametrize("mutation", [lambda data: data + b" ", lambda data: data.replace(b"svg2", b"svg3")])
def test_changed_upstream_asset_fails_closed(original_svg, mutation):
    with pytest.raises(ValueError, match="reviewed upstream"):
        ASSETS["sanitize_pyqtgraph_svg"](mutation(original_svg))


def test_missing_or_duplicate_build_entry_is_rejected(tmp_path, original_svg):
    with pytest.raises(ValueError, match="absent"):
        ASSETS["prepare_publication_assets"]([], tmp_path / "generated")
    source = tmp_path / "upstream.svg"
    source.write_bytes(original_svg)
    entry = (ASSETS["SVG_DESTINATION"], str(source), "DATA")
    with pytest.raises(ValueError, match="Duplicate"):
        ASSETS["prepare_publication_assets"]([entry, entry], tmp_path / "generated")


def test_generated_copy_cannot_replace_upstream(tmp_path, original_svg):
    source = tmp_path / ASSETS["SVG_DESTINATION"]
    source.parent.mkdir(parents=True)
    source.write_bytes(original_svg)
    with pytest.raises(ValueError, match="upstream input"):
        ASSETS["prepare_publication_assets"]([(ASSETS["SVG_DESTINATION"], str(source), "DATA")], tmp_path)
    assert source.read_bytes() == original_svg


@pytest.mark.parametrize("destination,keep", [
    ("matplotlib/mpl-data/sample_data/s1045.ima.gz", False),
    ("matplotlib/mpl-data/sample_data/README.txt", False),
    ("matplotlib/mpl-data/fonts/afm/phvr8a.afm", False),
    ("matplotlib/mpl-data/fonts/pdfcorefonts/Helvetica.afm", False),
    ("_internal/Matplotlib/mpl-data/fonts/afm/example.afm", False),
    ("matplotlib/mpl-data/fonts/ttf/DejaVuSans.ttf", True),
    ("matplotlib/mpl-data/fonts/ttf/LICENSE_STIX", True),
    ("matplotlib/mpl-data/stylelib/classic.mplstyle", True),
    ("matplotlib/backends/backend_pdf.py", True),
    ("my_matplotlib/mpl-data/sample_data/data.dat", True),
    ("matplotlib/mpl-data/sample_data_other/data.dat", True),
    ("licenses/matplotlib/00_LICENSE", True),
])
def test_only_documented_optional_matplotlib_data_is_excluded(destination, keep):
    assert ASSETS["keep_publication_data"](destination) is keep


def test_exclusions_record_exact_members_and_do_not_remove_files(tmp_path):
    sample = tmp_path / "sample.dat"
    sample.write_bytes(b"unused upstream sample")
    entry = ("matplotlib/mpl-data/sample_data/sample.dat", str(sample), "DATA")
    font = ("matplotlib/mpl-data/fonts/ttf/font.ttf", "unchanged.ttf", "DATA")
    kept, excluded = ASSETS["filter_publication_data"]([entry, font])
    assert kept == [font] and kept[0] is font
    assert sample.read_bytes() == b"unused upstream sample"
    assert excluded[0]["member"] == entry[0]
    assert excluded[0]["sha256"] == sha256(sample.read_bytes()).hexdigest()
    assert "Unused upstream" in excluded[0]["reason"]


@pytest.mark.parametrize("math_font", ["dejavusans", "stix", "cm"])
def test_pdf_svg_math_export_uses_retained_ttf_without_afm(qapp, tmp_path, monkeypatch, math_font):
    from io import BytesIO
    from shutil import copy2

    import matplotlib as mpl
    from matplotlib import _mathtext, font_manager, mathtext, text, textpath
    from matplotlib.backends import backend_agg, backend_pdf
    from matplotlib.figure import Figure
    from PySide6.QtCore import QBuffer, QByteArray, QIODevice, QSize
    from PySide6.QtPdf import QPdfDocument

    original_data = Path(mpl.get_data_path())
    filtered = tmp_path / "mpl-data"
    for source in original_data.rglob("*"):
        if source.is_file():
            destination = "matplotlib/mpl-data/" + source.relative_to(original_data).as_posix()
            if ASSETS["keep_publication_data"](destination):
                target = filtered / source.relative_to(original_data)
                target.parent.mkdir(parents=True, exist_ok=True)
                copy2(source, target)
    assert not (filtered / "fonts/afm").exists() and not (filtered / "fonts/pdfcorefonts").exists()
    assert not (filtered / "sample_data").exists() and (filtered / "fonts/ttf/DejaVuSans.ttf").is_file()

    def export():
        figure = Figure(figsize=(4, 3))
        axes = figure.subplots()
        axes.plot([0, 1, 2], [0, 1, 4])
        axes.set(xlabel="Position (µm)", ylabel="Temperature (K)",
                 title=r"$I=\sigma w t\nabla V$ · Ω / ΔT")
        outputs = {}
        for extension in ("pdf", "svg"):
            stream = BytesIO()
            figure.savefig(stream, format=extension, metadata={"Creator": "ETLayout export check"})
            outputs[extension] = stream.getvalue()
        return outputs

    def read_pdf(content):
        buffer = QBuffer()
        buffer.setData(QByteArray(content))
        assert buffer.open(QIODevice.OpenModeFlag.ReadOnly)
        document = QPdfDocument()
        document.load(buffer)
        assert document.pageCount() == 1
        image = document.render(0, QSize(800, 600))
        assert not image.isNull() and len(set(bytes(image.constBits()))) > 8
        document.close()
        return image

    settings = {"font.family": "DejaVu Sans", "mathtext.fontset": math_font,
                "pdf.use14corefonts": False, "ps.useafm": False, "pdf.fonttype": 42, "svg.fonttype": "path"}
    with mpl.rc_context(settings):
        before = export()
        monkeypatch.setattr(mpl, "get_data_path", lambda: str(filtered))
        # Use only the copied TTF files, including in modules that imported the
        # manager or findfont earlier. Clear parser caches so the second export
        # cannot reuse a font loaded from the unfiltered installation.
        manager = font_manager.FontManager()
        manager.ttflist = [entry for entry in manager.ttflist
                           if Path(entry.fname).is_relative_to(filtered)]
        manager.afmlist = []
        assert len({entry.fname for entry in manager.ttflist}) == 40
        monkeypatch.setattr(font_manager, "fontManager", manager)
        monkeypatch.setattr(font_manager, "findfont", manager.findfont)
        monkeypatch.setattr(_mathtext, "findfont", manager.findfont)
        for module in (backend_pdf, backend_agg, textpath):
            monkeypatch.setattr(module, "_fontManager", manager)
        monkeypatch.setattr(text, "fontManager", manager)
        monkeypatch.setattr(backend_pdf.RendererPdf, "_afm_font_dir", filtered / "fonts/pdfcorefonts")
        mathtext.MathTextParser._parse_cached.cache_clear()
        original_get_font = font_manager._get_font
        original_get_font.cache_clear()
        loaded = set()

        def load_retained_fonts(filenames, **kwargs):
            paths = [filename[0] for filename in filenames]
            assert all(Path(filename).is_relative_to(filtered) for filename in paths)
            loaded.update(paths)
            return original_get_font(filenames, **kwargs)

        monkeypatch.setattr(font_manager, "_get_font", load_retained_fonts)
        after = export()
        assert len(loaded) >= 2
    assert read_pdf(before["pdf"]) == read_pdf(after["pdf"])
    assert b"<svg" in after["svg"] and b"<path" in after["svg"]
    assert b"FontFile2" in after["pdf"]
