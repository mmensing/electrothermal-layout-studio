# SPDX-License-Identifier: MPL-2.0
"""Purpose-first export review; one backend enforces both GUI and CLI scope."""
from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QDoubleSpinBox,
    QFileDialog,
    QFormLayout,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPlainTextEdit,
    QProgressBar,
    QPushButton,
    QScrollArea,
    QSpinBox,
    QTabWidget,
    QTextBrowser,
    QVBoxLayout,
    QWidget,
)

from etlayout.gui.inputs import parse_number
from etlayout.io.sharing import (
    MODE_FORMATS,
    MODES,
    ExportRequest,
    FigureOptions,
    execute_export,
    preview_export,
)
from etlayout.models import SimulationError
from etlayout.simulation.analysis import FIELD_UNITS
from etlayout.visualization.palettes import COLORMAPS
from etlayout.visualization.support import region_options
from etlayout.visualization.units import display_unit, to_display, to_si


class ExportDialog(QDialog):
    def __init__(self, project, result, parent=None, *, history=None, displayed=None, initial=None,
                 archive_result=None, archive_provider=None):
        super().__init__(parent)
        self.project, self.run_result = project, result
        self.archive_result = archive_result if archive_result is not None else result
        self.displayed = displayed or result
        self.history = list(history or [])
        self.archive_history = self.history
        self._archive_provider = archive_provider
        self._archive_owner = None
        self._archive_ready = archive_provider is None
        self.archive_job = None
        self._archive_outcome = None
        self._close_after_export = False
        self.reviewed = None
        self.exported = None
        self.setWindowTitle("Export / share results")
        available = self.screen().availableGeometry()
        self.resize(min(850, max(320, available.width() - 40)), min(760, max(300, available.height() - 60)))
        layout = QVBoxLayout(self)
        self.mode_combo = QComboBox()
        self.mode_combo.setAccessibleName("Export purpose")
        self.mode_combo.setMinimumContentsLength(20)
        self.mode_combo.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
        for key, label in MODES.items():
            self.mode_combo.addItem(label, key)
        layout.addWidget(self.mode_combo)
        self.disclosure = QLabel()
        self.disclosure.setWordWrap(True)
        self.disclosure.setTextFormat(Qt.TextFormat.PlainText)
        layout.addWidget(self.disclosure)
        self.tabs = QTabWidget()
        layout.addWidget(self.tabs, 1)
        selection = QWidget()
        form = QFormLayout(selection)
        self.layer_combo, self.field_combo, self.region_combo, self.time_combo = [QComboBox() for _ in range(4)]
        for label, widget in (("Layer", self.layer_combo), ("Field quantity", self.field_combo),
                              ("Solved region", self.region_combo), ("Retained snapshot", self.time_combo)):
            widget.setAccessibleName("Export " + label.lower())
            form.addRow(label, widget)
        self.roi_enabled = QCheckBox("Clip to an explicit physical rectangle")
        form.addRow(self.roi_enabled)
        self.roi_edits = []
        row = QGridLayout()
        for index, label in enumerate(("X min", "Y min", "X max", "Y max")):
            entry = QLineEdit("0")
            entry.setAccessibleName("Export ROI " + label + " in micrometers")
            row.addWidget(QLabel(label + " (µm)"), index // 2, 2 * (index % 2))
            row.addWidget(entry, index // 2, 2 * (index % 2) + 1)
            self.roi_edits.append(entry)
        form.addRow(row)
        self.colormap = QComboBox()
        for key in COLORMAPS:
            self.colormap.addItem(key.title(), key)
        self.logarithmic = QCheckBox("Logarithmic color scale (positive figure support only)")
        self.manual = QCheckBox("Manual range in displayed quantity units")
        self.low, self.high = QLineEdit(), QLineEdit()
        self.low.setAccessibleName("Export color minimum")
        self.high.setAccessibleName("Export color maximum")
        self.range_unit = QLabel()
        range_row = QHBoxLayout()
        range_row.addWidget(self.low)
        range_row.addWidget(QLabel("to"))
        range_row.addWidget(self.high)
        range_row.addWidget(self.range_unit)
        form.addRow("Colormap", self.colormap)
        form.addRow(self.logarithmic)
        form.addRow(self.manual)
        form.addRow("Color limits", range_row)
        selection_scroll = QScrollArea()
        selection_scroll.setWidgetResizable(True)
        selection_scroll.setWidget(selection)
        self.tabs.addTab(selection_scroll, "Selection && scale")
        figure_page = QWidget()
        figure_form = QFormLayout(figure_page)
        self.figure_width, self.figure_height = QDoubleSpinBox(), QDoubleSpinBox()
        for widget, value in ((self.figure_width, 7.1), (self.figure_height, 4.8)):
            widget.setRange(2., 30.)
            widget.setDecimals(2)
            widget.setValue(value)
        self.dpi = QSpinBox()
        self.dpi.setRange(72, 1200)
        self.dpi.setValue(300)
        self.page, self.orientation = QComboBox(), QComboBox()
        self.page.addItems(["A4", "A3", "Letter", "Custom"])
        self.orientation.addItems(["landscape", "portrait"])
        self.title, self.annotations = QLineEdit(), QPlainTextEdit()
        self.title.setPlaceholderText("Optional title explicitly included in shared output")
        self.annotations.setPlaceholderText("Only these annotations are included; project notes are excluded")
        self.annotations.setMaximumHeight(110)
        self.smooth = QCheckBox("Smooth nodal field values (element quantities stay flat)")
        self.smooth.setChecked(True)
        self.colorbar, self.legend = QCheckBox("Include physical colorbar"), QCheckBox("Include quantity legend")
        self.colorbar.setChecked(True)
        self.legend.setChecked(True)
        for label, widget in (("PNG/SVG/custom width (in)", self.figure_width), ("Height (in)", self.figure_height),
                              ("PNG resolution (DPI)", self.dpi), ("PDF page", self.page), ("PDF orientation", self.orientation),
                              ("Title", self.title), ("Annotations", self.annotations)):
            figure_form.addRow(label, widget)
        figure_form.addRow(self.colorbar)
        figure_form.addRow(self.legend)
        figure_form.addRow(self.smooth)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setWidget(figure_page)
        self.tabs.addTab(scroll, "Figure && report")
        review_page = QWidget()
        review_layout = QVBoxLayout(review_page)
        self.formats = {}
        formats = QHBoxLayout()
        for key in ("png", "pdf", "svg", "html", "csv", "npz", "vtu", "etl"):
            box = QCheckBox(key.upper())
            box.setAccessibleName("Export " + key.upper() + " format")
            self.formats[key] = box
            formats.addWidget(box)
        review_layout.addLayout(formats)
        row = QHBoxLayout()
        self.directory = QLineEdit()
        self.directory.setPlaceholderText("Choose the destination folder")
        self.directory.setAccessibleName("Export destination directory")
        row.addWidget(self.directory, 1)
        browse = QPushButton("Browse…")
        browse.clicked.connect(self.browse)
        row.addWidget(browse)
        review_layout.addLayout(row)
        self.stem = QLineEdit("selection")
        self.stem.setAccessibleName("Export filename basename")
        review_layout.addWidget(self.stem)
        self.overwrite = QCheckBox("Explicitly replace listed existing artifacts")
        review_layout.addWidget(self.overwrite)
        review = QPushButton("Review exact contents")
        review.clicked.connect(self.review)
        review_layout.addWidget(review)
        self.preview = QTextBrowser()
        self.preview.setAccessibleName("Export information and files to include")
        self.preview.setOpenExternalLinks(False)
        review_layout.addWidget(self.preview, 1)
        self.tabs.addTab(review_page, "Files && review")
        self.export_status = QLabel()
        self.export_status.setTextFormat(Qt.TextFormat.PlainText)
        self.export_status.setWordWrap(True)
        layout.addWidget(self.export_status)
        progress_row = QHBoxLayout()
        self.export_progress = QProgressBar()
        self.export_progress.setRange(0, 1000)
        self.export_progress.setAccessibleName("Complete archive export progress")
        self.export_progress.hide()
        progress_row.addWidget(self.export_progress, 1)
        self.cancel_export = QPushButton("Cancel export")
        self.cancel_export.setAccessibleName("Cancel complete archive export")
        self.cancel_export.clicked.connect(lambda checked=False: self.cancel_archive_export())
        self.cancel_export.hide()
        progress_row.addWidget(self.cancel_export)
        layout.addLayout(progress_row)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Cancel)
        self.export_button = buttons.addButton("Export reviewed files", QDialogButtonBox.ButtonRole.AcceptRole)
        self.export_button.setEnabled(False)
        self.export_button.clicked.connect(self.perform_export)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
        self.layer_combo.currentIndexChanged.connect(self.populate_fields)
        self.field_combo.currentIndexChanged.connect(self.field_changed)
        self.mode_combo.currentIndexChanged.connect(self.mode_changed)
        for key in self.displayed.fields:
            self.layer_combo.addItem(key, key)
        if self.displayed.configuration.get("visualization", {}).get("kind") == "difference":
            self.time_combo.addItem("Paired snapshots preserved", None)
            self.time_combo.setEnabled(False)
        elif self.run_result.transient is not None:
            for time in self.run_result.transient.times:
                self.time_combo.addItem(f"t = {time:.9g} s", float(time))
        else:
            self.time_combo.addItem("Displayed solution", None)
        initial = initial or ExportRequest()
        self.mode_combo.setCurrentIndex(self.mode_combo.findData(initial.mode))
        if initial.layers:
            self.layer_combo.setCurrentIndex(self.layer_combo.findData(initial.layers[0]))
        self.populate_fields()
        if initial.fields:
            self.field_combo.setCurrentIndex(self.field_combo.findData(initial.fields[0]))
        self.region_combo.setCurrentIndex(max(0, self.region_combo.findData(initial.region)))
        self.colormap.setCurrentIndex(max(0, self.colormap.findData(initial.colormap)))
        self.logarithmic.setChecked(initial.logarithmic)
        if initial.time_s is not None and self.run_result.transient is not None:
            self.time_combo.setCurrentIndex(int(__import__("numpy").argmin(abs(self.run_result.transient.times - initial.time_s))))
        elif self.run_result.transient is not None:
            displayed_time = self.displayed.metrics.get("time_s", float(self.run_result.transient.times[-1]))
            self.time_combo.setCurrentIndex(max(0, self.time_combo.findData(displayed_time)))
        bounds = self.displayed.mesh.points
        if len(bounds):
            values = initial.roi if initial.roi is not None else [*bounds.min(axis=0), *bounds.max(axis=0)]
            for entry, value in zip(self.roi_edits, values, strict=True):
                entry.setText(f"{value * 1e6:.9g}")
        self.roi_enabled.setChecked(initial.roi is not None)
        for widget, value in ((self.figure_width, initial.figure.width_inches), (self.figure_height, initial.figure.height_inches), (self.dpi, initial.figure.dpi)):
            widget.setValue(value)
        self.page.setCurrentText(initial.figure.page_size)
        self.orientation.setCurrentText(initial.figure.orientation)
        self.title.setText(initial.figure.title)
        self.annotations.setPlainText(initial.figure.annotations)
        self.colorbar.setChecked(initial.figure.colorbar)
        self.legend.setChecked(initial.figure.legend)
        self.smooth.setChecked(initial.figure.smooth)
        if initial.limits is not None:
            unit = FIELD_UNITS.get(self.field_combo.currentData(), "")
            self.low.setText(str(to_display(initial.limits[0], unit)))
            self.high.setText(str(to_display(initial.limits[1], unit)))
            self.manual.setChecked(True)
        self.mode_changed()
        for format, widget in self.formats.items():
            widget.setChecked(format in initial.formats and format in MODE_FORMATS[initial.mode])
        for widget in self.findChildren(QComboBox):
            widget.currentIndexChanged.connect(self.invalidate_review)
        for widget in self.findChildren(QLineEdit):
            widget.textChanged.connect(self.invalidate_review)
        for widget in self.findChildren(QCheckBox):
            widget.toggled.connect(self.invalidate_review)
        for widget in self.findChildren(QDoubleSpinBox) + self.findChildren(QSpinBox):
            widget.valueChanged.connect(self.invalidate_review)
        self.annotations.textChanged.connect(self.invalidate_review)

    def populate_fields(self):
        layer = self.layer_combo.currentData()
        previous = self.field_combo.currentData()
        self.field_combo.blockSignals(True)
        self.field_combo.clear()
        quantities = list(self.displayed.fields.get(layer, {}))
        if "temperature" in quantities:
            quantities.append("temperature_rise")
        for quantity in quantities:
            self.field_combo.addItem(quantity.replace("_", " ").capitalize(), quantity)
        self.field_combo.setCurrentIndex(max(0, self.field_combo.findData(previous)))
        self.field_combo.blockSignals(False)
        self.region_combo.clear()
        if layer:
            for key, label in region_options(self.displayed, layer):
                self.region_combo.addItem(label, key)
        self.field_changed()

    def field_changed(self):
        unit = FIELD_UNITS.get(self.field_combo.currentData(), "")
        self.range_unit.setText(display_unit(unit))
        self.manual.setChecked(False)
        self.invalidate_review()

    def mode_changed(self):
        mode = self.mode_combo.currentData()
        sharing = mode != "archive"
        self.tabs.setTabEnabled(0, sharing)
        self.tabs.setTabEnabled(1, mode == "figure")
        self.disclosure.setText("CONFIDENTIAL complete experiment: original layout, all fields, configuration, materials, notes and history. A cropped map does not crop this archive."
            if not sharing else "Sharing exports include only selected FE support and interpretation metadata. Source layout, full configuration, project notes and history are excluded. Selected geometry can still reveal your design.")
        defaults = {"figure": {"png", "html"}, "data": {"csv", "npz", "vtu"}, "archive": {"etl"}}[mode]
        for key, widget in self.formats.items():
            widget.setVisible(key in MODE_FORMATS[mode])
            widget.setChecked(key in defaults)
        self.invalidate_review()

    def invalidate_review(self, *_args):
        self.reviewed = None
        self.export_button.setEnabled(False)

    def browse(self):
        path = QFileDialog.getExistingDirectory(self, "Export destination")
        if path:
            self.directory.setText(path)

    def request(self):
        mode = self.mode_combo.currentData()
        quantity = self.field_combo.currentData()
        unit = FIELD_UNITS.get(quantity, "")
        limits = tuple(to_si(parse_number(entry.text(), display_unit(unit)), unit) for entry in (self.low, self.high)) if mode == "figure" and self.manual.isChecked() else None
        roi = tuple(parse_number(entry.text(), "µm") for entry in self.roi_edits) if mode != "archive" and self.roi_enabled.isChecked() else None
        return ExportRequest(mode=mode, layers=(self.layer_combo.currentData(),) if mode != "archive" else (),
            fields=(quantity,) if mode != "archive" else (), region=(self.region_combo.currentData() or "") if mode != "archive" else "",
            roi=roi if mode != "archive" else None, time_s=self.time_combo.currentData() if mode != "archive" and self.displayed.configuration.get("visualization", {}).get("kind") != "difference" else None,
            formats=tuple(key for key, widget in self.formats.items() if widget.isChecked() and key in MODE_FORMATS[mode]),
            colormap=self.colormap.currentData(), limits=limits, logarithmic=self.logarithmic.isChecked(),
            figure=FigureOptions(self.figure_width.value(), self.figure_height.value(), self.dpi.value(), self.page.currentText(),
                self.orientation.currentText(), self.title.text(), self.annotations.toPlainText(), self.colorbar.isChecked(), self.legend.isChecked(), self.smooth.isChecked()),
            stem=self.stem.text(), overwrite=self.overwrite.isChecked())

    def _prepare_archive_sources(self):
        if self._archive_ready:
            return
        archived, retained, owner = self._archive_provider()
        try:
            self.archive_history = list(retained)
            self.archive_result = archived
            self._archive_owner = owner
            self._archive_ready = True
        except BaseException:
            if owner is not None:
                owner.close()
            raise

    def release_archive_sources(self):
        """Release only the archive generation acquired by this export dialog."""
        owner, self._archive_owner = self._archive_owner, None
        if self._archive_provider is not None:
            self._archive_ready = False
            self.archive_history = []
            self.archive_result = None
        if owner is not None:
            owner.close()

    def history_for(self, mode):
        if mode == "archive":
            self._prepare_archive_sources()
            return self.archive_history
        return []

    def source(self, mode):
        if mode == "archive":
            self._prepare_archive_sources()
            return self.archive_result
        return self.run_result if self.displayed.configuration.get("visualization", {}).get("kind") != "difference" else self.displayed

    def review(self):
        try:
            request = self.request()
            manifest = preview_export(self.source(request.mode), request, project=self.project,
                                      history=self.history_for(request.mode))
            lines = [manifest["scope"], "", manifest["disclosure"], "", "Files to include:"]
            lines.extend("  • " + name for name in manifest["files"])
            if request.mode == "archive":
                lines.extend(["", f"Retained historical runs: {manifest['history_runs']}", "Original layout, complete setup, notes and numerical fields are included."])
            else:
                lines.extend(["", "Selected physical support:"])
                for data in manifest["datasets"]:
                    bounds = ", ".join(f"{value * 1e6:.9g}" for value in data["bounds_m"])
                    lines.append(f"  • {data['layer']} · {data['field']} ({data['unit']}) · {data['triangle_count']:,} triangles")
                    lines.append(f"    Effective bounds (xmin, ymin, xmax, ymax), µm: {bounds}")
                lines.append("Time: " + (f"{manifest['timing']['time_s']:.9g} s" if "time_s" in manifest["timing"] else "steady / paired comparison"))
                if request.roi is not None:
                    lines.append("Requested ROI (µm): " + ", ".join(f"{value * 1e6:.9g}" for value in request.roi))
                lines.extend(["", "Excluded: " + ", ".join(manifest["excluded"]) + "."])
                if request.mode == "figure":
                    lines.append(f"Display: {request.colormap}, {'logarithmic' if request.logarithmic else 'linear'} scale; {'manual clipping disclosed' if request.limits else 'automatic range'}.")
                    lines.append(f"PNG/SVG: {request.figure.width_inches:g} × {request.figure.height_inches:g} in; PNG {request.figure.dpi} DPI. PDF: {request.figure.page_size}, {request.figure.orientation} ({request.figure.size_inches('.pdf')[0]:g} × {request.figure.size_inches('.pdf')[1]:g} in).")
                if not manifest["converged"]:
                    lines.append("WARNING: source did not converge; diagnostic output only.")
                if manifest["timing"].get("transient_completed") is False:
                    lines.append("WARNING: snapshot comes from an incomplete transient run.")
            self.preview.setPlainText("\n".join(lines))
            self.reviewed = (request, self.directory.text())
            self.export_button.setEnabled(bool(self.directory.text().strip()))
            self.tabs.setCurrentIndex(2)
        except (ValueError, OSError, SimulationError) as error:
            self.preview.setPlainText(str(error))
            self.invalidate_review()

    def perform_export(self):
        if self.archive_job is not None:
            return
        try:
            request = self.request()
            if self.reviewed != (request, self.directory.text()):
                raise ValueError("Review the current export contents before writing files")
            if request.mode == "archive":
                self.start_archive_export(request)
                return
            self.exported = execute_export(self.source(request.mode), self.directory.text(), request,
                                           project=self.project, history=self.history_for(request.mode))
            self.accept()
        except (ValueError, OSError, SimulationError) as error:
            QMessageBox.warning(self, "Export failed", str(error))

    def start_archive_export(self, request):
        from etlayout.gui.archive_export import ArchiveExportJob
        from etlayout.io.saving import SaveSnapshot

        result = self.source("archive")
        history = self.history_for("archive")
        owner = self._archive_owner
        snapshot = SaveSnapshot.capture(self.project, result, history,
            archive_format=getattr(self.project, "_archive_format", 2), source_lease=owner)
        job = ArchiveExportJob(snapshot, self.directory.text(), request, self)
        self.archive_job = job
        # Ownership now belongs to the worker's immutable snapshot, including
        # cancellation/failure paths. Closing the view cannot unpin its source.
        self._archive_owner = None
        if self._archive_provider is not None:
            self._archive_ready = False
            self.archive_result = None
            self.archive_history = []
        self._archive_outcome = None
        self._close_after_export = False
        self.mode_combo.setEnabled(False)
        self.tabs.setEnabled(False)
        self.export_button.setEnabled(False)
        self.export_progress.setValue(0)
        self.export_progress.show()
        self.cancel_export.show()
        self.cancel_export.setEnabled(True)
        self.export_status.setText("Exporting the complete archive in a background worker…")
        job.progress.connect(self.archive_progress)
        job.exported.connect(lambda manifest: self.archive_outcome("success", manifest))
        job.failed.connect(lambda message: self.archive_outcome("failed", message))
        job.cancelled.connect(lambda message: self.archive_outcome("cancelled", message))
        job.finished.connect(self.archive_finished)
        try:
            job.start()
        except BaseException:
            self.archive_job = None
            snapshot.release_sources()
            self.mode_combo.setEnabled(True)
            self.tabs.setEnabled(True)
            self.export_progress.hide()
            self.cancel_export.hide()
            self.invalidate_review()
            job.deleteLater()
            raise

    def archive_progress(self, fraction, message):
        self.export_progress.setValue(round(max(0., min(1., fraction)) * 1000))
        self.export_status.setText(message)

    def archive_outcome(self, kind, value):
        self._archive_outcome = kind, value

    def archive_finished(self):
        job, self.archive_job = self.archive_job, None
        job.deleteLater()
        self.mode_combo.setEnabled(True)
        self.tabs.setEnabled(True)
        self.export_progress.hide()
        self.cancel_export.hide()
        kind, value = self._archive_outcome or ("failed", "Archive worker exited without a result")
        self._archive_outcome = None
        if kind == "success":
            self.exported = value
            self.export_status.setText("Complete archive exported and verified.")
            self.accept()
            return
        self.invalidate_review()
        self.export_status.setText(value)
        self.preview.setPlainText(("Archive export cancelled: " if kind == "cancelled" else "Archive export failed: ") + value)
        self.tabs.setCurrentIndex(2)
        if self._close_after_export:
            self.reject()

    def cancel_archive_export(self, *, close=False):
        if self.archive_job is not None:
            self._close_after_export = self._close_after_export or close
            self.archive_job.cancel()
            self.cancel_export.setEnabled(False)
            self.export_status.setText("Cancellation requested; waiting for the archive worker to stop safely…")

    def reject(self):
        if self.archive_job is not None:
            self.cancel_archive_export(close=True)
            return
        super().reject()


    def done(self, result):
        if self.archive_job is not None:
            self.cancel_archive_export(close=True)
            return
        self.release_archive_sources()
        super().done(result)

    def closeEvent(self, event):
        if self.archive_job is not None:
            self.cancel_archive_export(close=True)
            event.ignore()
            return
        self.release_archive_sources()
        super().closeEvent(event)
