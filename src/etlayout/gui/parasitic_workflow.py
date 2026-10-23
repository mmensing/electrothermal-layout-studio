# SPDX-License-Identifier: MPL-2.0
"""Explicit field setup and results, separate from physical-port DC reduction."""
from __future__ import annotations

from copy import deepcopy

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPlainTextEdit,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from etlayout.gui.inputs import parse_number
from etlayout.models import PropertyModel
from etlayout.power.field_snapshot import EPSILON_0, MU_0


def _note(text):
    label = QLabel(text)
    label.setWordWrap(True)
    return label


def _entry(text, help_text):
    entry = QLineEdit(str(text))
    entry.setToolTip(help_text)
    entry.setAccessibleDescription(help_text)
    return entry


def _item(text):
    return QTableWidgetItem(str(text))


class FieldSetupDialog(QDialog):
    """Atomic project edit: Cancel leaves neither material nor loop drafts behind."""
    def __init__(self, project, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Field inputs · materials and complete return paths")
        self.project = deepcopy(project)
        self.candidate = None
        layout = QVBoxLayout(self)
        layout.addWidget(_note("Field settings are independent of DC drive and thermal boundaries. Apply saves the complete valid form; Cancel preserves the working project."))
        pages = QTabWidget()
        layout.addWidget(pages)
        media = QWidget()
        pages.addTab(media, "Environment & materials")
        media_layout = QVBoxLayout(media)
        form = QFormLayout()
        self.epsilon = _entry(project.parasitics.get("background_relative_permittivity", 1.),
            "Dimensionless relative permittivity εr. Capacitance currently requires this same real homogeneous dielectric everywhere outside the metal; vacuum is 1.")
        self.mu = _entry(project.parasitics.get("background_relative_permeability", 1.),
            "Dimensionless relative permeability μr. The first inductance engine supports the explicit nonmagnetic value 1 only.")
        form.addRow("Environment εr", self.epsilon)
        form.addRow("Environment μr", self.mu)
        media_layout.addLayout(form)
        media_layout.addWidget(_note("Enter relative coefficients below; storage uses absolute F/m and H/m. Blank means unspecified. Existing temperature-dependent models are retained as ‘model’; edit them in Materials. Record the source or synthetic assumption separately from resistivity citations. New constant models have an explicit 200–600 K validity range; existing ranges are preserved."))
        self.materials = QTableWidget(len(project.materials), 4)
        self.materials.setAccessibleName("Optional electromagnetic material coefficients")
        self.materials.setHorizontalHeaderLabels(["Material ID", "εr", "μr", "Field source / assumption"])
        self.materials.horizontalHeader().setStretchLastSection(True)
        sources = project.parasitics.get("material_sources", {})
        self.material_ids = list(project.materials)
        for row, identifier in enumerate(self.material_ids):
            material = project.materials[identifier]
            identity = _item(identifier)
            identity.setFlags(identity.flags() & ~Qt.ItemFlag.ItemIsEditable)
            self.materials.setItem(row, 0, identity)
            for column, (name, scale) in enumerate((("permittivity", EPSILON_0), ("permeability", MU_0)), 1):
                model = getattr(material, name)
                text = "" if model is None else (f"{model.value / scale:.12g}" if model.kind == "constant" else "model")
                self.materials.setItem(row, column, _item(text))
            self.materials.setItem(row, 3, _item(sources.get(identifier, "")))
        media_layout.addWidget(self.materials)
        declare = QPushButton("Declare all listed materials nonmagnetic (μr = 1)")
        declare.setToolTip("An explicit modeling assumption, recorded separately for each material. Do not use for magnetic materials.")
        declare.clicked.connect(self.declare_nonmagnetic)
        media_layout.addWidget(declare)
        paths = QWidget()
        pages.addTab(paths, "Closed-loop paths")
        path_layout = QVBoxLayout(paths)
        path_layout.addWidget(_note("Each loop needs its actual forward AND return bars, in order, closing at the excitation cut. Coordinates, widths and heights are in µm. The complete bar volumes must match a connected conductor in the layout; a narrow invented path inside a wide plane is rejected. Separate loops cannot share a conductor."))
        self.segments = QTableWidget(0, 13)
        self.segments.setAccessibleName("Oriented finite forward and return segments")
        self.segments.setHorizontalHeaderLabels(["Loop ID", "Start X", "Start Y", "Start Z", "End X", "End Y", "End Z", "Width", "Height", "Width X", "Width Y", "Width Z", "Support IDs"])
        self.segments.setToolTip("Coordinates/width/height: µm, or an explicit length suffix. Width X/Y/Z: unit coordinate axis perpendicular to the path. Supports: comma-separated layer:ID or interface:ID.")
        for loop in project.parasitics.get("loops", []):
            for segment in loop["segments"]:
                self.add_segment(loop["id"], segment)
        path_layout.addWidget(self.segments)
        row = QHBoxLayout()
        add = QPushButton("Add rectangular loop…")
        add.clicked.connect(self.add_rectangle)
        remove = QPushButton("Remove selected segment")
        remove.clicked.connect(lambda: self.segments.removeRow(self.segments.currentRow()) if self.segments.currentRow() >= 0 else None)
        row.addWidget(add)
        row.addWidget(remove)
        path_layout.addLayout(row)
        self.errors = _note("")
        layout.addWidget(self.errors)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Apply | QDialogButtonBox.StandardButton.Cancel)
        buttons.button(QDialogButtonBox.StandardButton.Apply).clicked.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
        self.resize(900, 610)

    def declare_nonmagnetic(self):
        for row in range(self.materials.rowCount()):
            self.materials.item(row, 2).setText("1")
            source = self.materials.item(row, 3)
            source.setText((source.text().rstrip() + "; " if source.text().strip() else "") + "User-declared nonmagnetic approximation μr=1")

    def add_segment(self, identifier, segment):
        row = self.segments.rowCount()
        self.segments.insertRow(row)
        values = [identifier, *[value*1e6 for value in segment["start_m"]],
                  *[value*1e6 for value in segment["end_m"]], segment["width_m"]*1e6,
                  segment["height_m"]*1e6, *segment["width_direction"], ", ".join(segment["support_ids"])]
        for column, value in enumerate(values):
            self.segments.setItem(row, column, _item(f"{value:.12g}" if isinstance(value, (float, int)) else value))

    def add_rectangle(self):
        from etlayout.parasitic_examples import rectangular_loop
        dialog = QDialog(self)
        dialog.setWindowTitle("Explicit rectangular return · layout is unchanged")
        layout = QVBoxLayout(dialog)
        form = QFormLayout()
        fields = {}
        for name, value in (("Loop ID", "loop"), ("X0 (µm)", 0), ("Y0 (µm)", 0),
                            ("X1 (µm)", 1000), ("Y1 (µm)", 500), ("Center Z (µm)", 5),
                            ("Bar width (µm)", 50), ("Bar height (µm)", 10)):
            fields[name] = _entry(value, "Explicit centerline rectangle; values must match the actual layout volume.")
            form.addRow(name, fields[name])
        support = QComboBox()
        for layer in self.project.layers:
            if layer.role == "conductor":
                support.addItem(layer.name, "layer:" + layer.id)
        form.addRow("Actual conductor", support)
        layout.addLayout(form)
        error = _note("")
        layout.addWidget(error)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        layout.addWidget(buttons)
        buttons.rejected.connect(dialog.reject)
        def accept():
            try:
                numbers = [parse_number(field.text(), "µm") for name, field in fields.items() if name != "Loop ID"]
                loop = rectangular_loop(fields["Loop ID"].text().strip(), *numbers, support.currentData())
                if not loop["id"] or not support.currentData():
                    raise ValueError("Choose a loop ID and an existing conductor")
                for segment in loop["segments"]:
                    self.add_segment(loop["id"], segment)
                dialog.accept()
            except ValueError as exc:
                error.setText(str(exc))
        buttons.accepted.connect(accept)
        dialog.exec()

    def collect(self):
        candidate = deepcopy(self.project)
        epsilon, mu = parse_number(self.epsilon.text()), parse_number(self.mu.text())
        if min(epsilon, mu) <= 0:
            raise ValueError("Environment coefficients must be positive")
        sources = {}
        for row, identifier in enumerate(self.material_ids):
            material = candidate.materials[identifier]
            for column, (name, scale) in enumerate((("permittivity", EPSILON_0), ("permeability", MU_0)), 1):
                text = self.materials.item(row, column).text().strip()
                if text == "model" and getattr(material, name) is not None:
                    continue
                value = parse_number(text) if text else None
                if value is not None and value <= 0:
                    raise ValueError(f"{identifier}: electromagnetic coefficients must be positive")
                previous = getattr(material, name)
                if value is None:
                    setattr(material, name, None)
                elif previous is not None:
                    if previous.kind != "constant":
                        raise ValueError(f"{identifier}: retain ‘model’ or edit the temperature law in Materials")
                    if abs(value*scale - previous.value) > abs(previous.value)*1e-11:
                        previous.value = value*scale
                else:
                    setattr(material, name, PropertyModel(value=value*scale))
            source = self.materials.item(row, 3).text().strip()
            if source:
                sources[identifier] = source
            if any(getattr(material, name) is not None for name in ("permittivity", "permeability")) and not source:
                raise ValueError(f"{identifier}: record a field source or explicit modeling assumption")
        loops = {}
        for row in range(self.segments.rowCount()):
            values = [self.segments.item(row, column).text().strip() if self.segments.item(row, column) else "" for column in range(13)]
            identifier = values[0]
            if not identifier:
                raise ValueError(f"Segment {row+1}: enter a loop ID")
            numbers = [parse_number(text, "µm") for text in values[1:9]]
            direction = [parse_number(text) for text in values[9:12]]
            loops.setdefault(identifier, []).append({"start_m": numbers[:3], "end_m": numbers[3:6],
                "width_m": numbers[6], "height_m": numbers[7], "width_direction": direction,
                "support_ids": [text.strip() for text in values[12].split(",") if text.strip()]})
        from etlayout.parasitic_examples import validate_loop_entries
        canonical_loops = validate_loop_entries([{"id": key, "segments": value} for key, value in loops.items()])
        candidate.parasitics = {"schema_version": 1, "background_relative_permittivity": epsilon,
            "background_relative_permeability": mu, "material_sources": sources,
            "loops": canonical_loops}
        # Geometry checks remain in the cancellable worker; the complete form is
        # still validated syntactically before its atomic document transaction.
        return candidate

    def accept(self):
        try:
            self.candidate = self.collect()
        except ValueError as error:
            self.errors.setText(str(error))
            return
        super().accept()


class ParasiticWorkflow(QWidget):
    configure_requested = Signal()
    run_requested = Signal(bool)
    reference_requested = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.report = None
        self._component_labels = {}
        layout = QVBoxLayout(self)
        form = QFormLayout()
        self.kind = QComboBox()
        self.kind.addItem("Capacitance · homogeneous electrostatic field", "capacitance")
        self.kind.addItem("Loop resistance & inductance · explicit return", "loop_rl")
        self.kind.setToolTip("Capacitance is not determined by DC port landings. Inductance always depends on a complete current return.")
        form.addRow("Field model", self.kind)
        layout.addLayout(form)
        self.setup_group = QGroupBox("Model assumptions & setup")
        self.setup_group.setCheckable(True)
        self.setup_group.setChecked(True)
        group_layout = QVBoxLayout(self.setup_group)
        self.setup_body = QWidget()
        setup = QVBoxLayout(self.setup_body)
        setup.setContentsMargins(0, 0, 0, 0)
        setup.addWidget(_note("Experimental 3D fields, independent of DC Run prerequisites. C uses complete equipotential bodies relative to infinity; RL needs finite forward and return bars. These results remain separate from the physical-port DC network and are not automatically combined into an RLC circuit."))
        form = QFormLayout()
        self.temperature = _entry("300", "Uniform absolute temperature in K. Material laws are evaluated within their declared validity ranges; no self-heating or retained nonuniform thermal state is inferred.")
        form.addRow("Fixed temperature (K)", self.temperature)
        setup.addLayout(form)
        row = QHBoxLayout()
        configure = QPushButton("Configure field inputs…")
        configure.clicked.connect(self.configure_requested)
        reference = QPushButton("Create ready-to-open reference…")
        reference.clicked.connect(self.reference_requested)
        row.addWidget(configure)
        row.addWidget(reference)
        setup.addLayout(row)
        self.setup_note = _note("Configure explicit environment/material assumptions and, for RL, complete return paths.")
        setup.addWidget(self.setup_note)
        group_layout.addWidget(self.setup_body)
        self.setup_group.toggled.connect(self.setup_body.setVisible)
        layout.addWidget(self.setup_group)
        self.pages = QTabWidget()
        layout.addWidget(self.pages, 1)
        controls = QWidget()
        self.pages.addTab(controls, "Calculate")
        controls_layout = QVBoxLayout(controls)
        controls_layout.addWidget(_note("Select a trusted console executable installed separately. ETLayout generates its own bounded inputs, isolates the process, and records hashes and resource use. FastCap2 must expose unaveraged SI matrix output; a stock averaged display cannot establish acceptance. External engines are not bundled."))
        form = QFormLayout()
        row = QHBoxLayout()
        self.executable = _entry("", "Explicit trusted local field engine. No executable is downloaded or found through PATH. FastCap2 for C; FastHenry for RL.")
        choose = QPushButton("Browse…")
        choose.clicked.connect(self.choose_executable)
        row.addWidget(self.executable, 1)
        row.addWidget(choose)
        form.addRow("External engine", row)
        self.engine_form = form
        self.mesh_sizes = _entry("100, 50, 25", "Three to five strictly decreasing surface panel sizes in µm. Every C coefficient is checked over the final two refinement steps; changing a length alone does not prove distinct meshes.")
        form.addRow("C panel sizes (µm)", self.mesh_sizes)
        self.direct = QCheckBox("Direct interactions (slower; at most 2000 panels)")
        self.direct.setToolTip("Use direct interactions for small capacitance problems. For the plate reference, try panel sizes 200, 100, 50 µm; verify refinement rather than assuming accuracy. No matrix repair is applied.")
        form.addRow("C algorithm", self.direct)
        self.frequencies = _entry("0, 1, 10", "Two to eight increasing frequencies in Hz, starting at DC. L(f)=Im Z/(2πf) requires positive frequency; DC alone gives resistance. The lowest two positive points check observed low-frequency sensitivity.")
        form.addRow("RL frequencies (Hz)", self.frequencies)
        self.refinements = _entry("1:1, 2:3, 4:5", "Three to five increasing longitudinal:cross-section subdivision pairs. Cross-section n means n×n volume filaments; geometry is unchanged.")
        form.addRow("RL refinement levels", self.refinements)
        self.budget = _entry("20000", "Maximum C surface panels, or maximum RL filaments. Reducing a budget causes an explicit resource-limited failure; geometry and requested physics are not silently simplified.")
        form.addRow("Panel / filament budget", self.budget)
        self.timeout = _entry("180", "Total wall-clock field-engine time budget in seconds; C divides this across mesh levels. Preparation and postprocessing are separate. Hard process memory cap is 1 GiB.")
        form.addRow("Engine time budget (s)", self.timeout)
        self.tolerance = _entry("2", "Dimensionless percent change limit per matrix entry, over two successive refinements. This is sensitivity evidence, not calibration or a rigorous error bound. Native output precision is checked separately.")
        form.addRow("Mesh sensitivity limit (%)", self.tolerance)
        controls_layout.addLayout(form)
        row = QHBoxLayout()
        prepare = QPushButton("Prepare inputs only…")
        prepare.clicked.connect(lambda: self.run_requested.emit(True))
        calculate = QPushButton("Calculate & check refinement…")
        calculate.clicked.connect(lambda: self.run_requested.emit(False))
        row.addWidget(prepare)
        row.addWidget(calculate)
        controls_layout.addLayout(row)
        controls_layout.addStretch()
        evidence = QWidget()
        self.pages.addTab(evidence, "Results & evidence")
        evidence_layout = QVBoxLayout(evidence)
        self.summary = _note("No field calculation. Prepared inputs are not numerical validation.")
        self.summary.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        evidence_layout.addWidget(self.summary)
        self.matrix = QTableWidget()
        self.matrix.setMinimumHeight(120)
        self.matrix.setAccessibleName("Raw field coefficients in engineering units")
        self.matrix.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        evidence_layout.addWidget(self.matrix)
        self.details = QPlainTextEdit()
        self.details.setMaximumHeight(150)
        self.details.setReadOnly(True)
        self.details.setAccessibleName("Field provenance and verification evidence in SI")
        evidence_layout.addWidget(self.details)
        self._previous_kind = None
        self._engine_paths = {}
        self._budgets = {"capacitance": "20000", "loop_rl": "1024"}
        self.kind.currentIndexChanged.connect(self.update_kind)
        self.update_kind()
        self.direct.toggled.connect(self.clear_report)
        self.pages.currentChanged.connect(lambda index: self.setup_group.setChecked(index == 0))
        for entry in (self.temperature, self.executable, self.mesh_sizes, self.frequencies,
                      self.refinements, self.budget, self.timeout, self.tolerance):
            entry.textChanged.connect(self.clear_report)

    def choose_executable(self):
        path, _ = QFileDialog.getOpenFileName(self, "Choose trusted external console field engine", "", "Executable (*.exe);;All files (*)")
        if path:
            self.executable.setText(path)

    def update_kind(self):
        kind = self.kind.currentData()
        if self._previous_kind is not None:
            self._engine_paths[self._previous_kind] = self.executable.text()
            self._budgets[self._previous_kind] = self.budget.text()
        capacitance = kind == "capacitance"
        self.mesh_sizes.setEnabled(capacitance)
        self.direct.setEnabled(capacitance)
        self.frequencies.setEnabled(not capacitance)
        self.refinements.setEnabled(not capacitance)
        for widget in (self.mesh_sizes, self.direct):
            self.engine_form.setRowVisible(widget, capacitance)
        for widget in (self.frequencies, self.refinements):
            self.engine_form.setRowVisible(widget, not capacitance)
        self.budget.setText(self._budgets[kind])
        self.executable.setText(self._engine_paths.get(kind, ""))
        self._previous_kind = kind
        self.clear_report()

    def clear_report(self):
        self.report = None
        self.summary.setText("No current field calculation. Run the field model after reviewing its explicit inputs.")
        self.matrix.clear()
        self.matrix.setRowCount(0)
        self.matrix.setColumnCount(0)
        self.details.clear()

    def set_project(self, project):
        from etlayout.io.project import configuration_hash
        identity = configuration_hash(project)
        if getattr(self, "_source_identity", identity) != identity:
            self.clear_report()
        self._source_identity = identity
        settings = project.parasitics
        self.setup_note.setText(f"{len(project.layers)} physical layers · {len(settings.get('loops', []))} declared loops · "
            + ("explicit field settings saved in project" if settings else "field inputs are not configured"))

    def set_snapshot(self, snapshot):
        self._component_labels = {}
        if not snapshot:
            return
        groups = {}
        for solid in snapshot["solids"]:
            if solid["role"] == "conductor":
                groups.setdefault(solid["component_id"], set()).add(solid["layer_id"] or solid["origin_id"])
        bases = {identifier: " + ".join(sorted(keys)) for identifier, keys in groups.items()}
        for identifier in sorted(bases):
            base = bases[identifier]
            peers = sorted(key for key, value in bases.items() if value == base)
            self._component_labels[identifier] = base + (f" · body {peers.index(identifier)+1}" if len(peers) > 1 else "")

    def options(self, prepare_only):
        options = {"kind": self.kind.currentData(), "prepare_only": prepare_only,
            "executable": self.executable.text().strip(), "temperature_K": parse_number(self.temperature.text(), "K"),
            "timeout": parse_number(self.timeout.text(), "s"), "relative_tolerance": parse_number(self.tolerance.text()) / 100}
        if options["kind"] == "capacitance":
            options["mesh_sizes_m"] = [parse_number(value, "µm") for value in self.mesh_sizes.text().split(",")]
            options["max_panels"] = parse_number(self.budget.text(), integer=True)
            options["direct"] = self.direct.isChecked()
        else:
            options["frequencies_Hz"] = [parse_number(value, "Hz") for value in self.frequencies.text().split(",")]
            options["refinements"] = [tuple(parse_number(value, integer=True) for value in pair.split(":")) for pair in self.refinements.text().split(",")]
            options["max_filaments"] = parse_number(self.budget.text(), integer=True)
        return options

    def show_report(self, report):
        from etlayout.io.project import _json_dump
        self.matrix.clear()
        self.matrix.setRowCount(0)
        self.matrix.setColumnCount(0)
        self.report = report
        self.details.setPlainText(_json_dump(report))
        self.summary.setText("Field result: " + report.get("status", "unknown") + ". " + report.get("diagnostic", "Inspect per-entry refinement, physical assumptions and source identity below. Prepared inputs are not a solve."))
        if report.get("kind", "").startswith("capacitance") and report.get("converged"):
            self.summary.setText("Capacitance calculated · mesh sensitivity: "
                + ("passed" if report.get("mesh_verified") else "not established")
                + " · strict static operator: " + ("accepted" if report.get("accepted") else "not accepted")
                + ". Original coefficients are preserved; no averaging or automatic RLC export.")
        network = report.get("network") or report
        values = network.get("raw_matrix_F")
        labels = network.get("axis_ids", [])
        unit, scale = "pF", 1e12
        if values is not None:
            self.matrix.setColumnCount(len(labels))
            self.matrix.setRowCount(len(labels))
            titles = [self._component_labels.get(identifier, identifier) for identifier in labels]
            self.matrix.setHorizontalHeaderLabels([title + " (" + unit + ")" for title in titles])
            self.matrix.setVerticalHeaderLabels(titles)
            for index, identifier in enumerate(labels):
                self.matrix.horizontalHeaderItem(index).setToolTip(identifier)
                self.matrix.verticalHeaderItem(index).setToolTip(identifier)
            for row, entries in enumerate(values):
                for column, value in enumerate(entries):
                    self.matrix.setItem(row, column, _item(f"{value*scale:.7g}"))
        elif report.get("frequency_results"):
            self.matrix.setColumnCount(5)
            self.matrix.setHorizontalHeaderLabels(["Frequency (Hz)", "Response loop", "Excited loop", "R (mΩ)", "L (nH)"])
            rows = []
            labels = report["loop_ids"]
            for case in report["frequency_results"]:
                for row, identifier in enumerate(labels):
                    for column, excitation in enumerate(labels):
                        value = case["L_H"][row][column] if case.get("L_H") is not None else None
                        rows.append([f"{case['frequency_Hz']:.7g}", identifier, excitation,
                                     f"{case['R_ohm'][row][column]*1e3:.7g}",
                                     f"{value*1e9:.7g}" if value is not None else "Requires f > 0"])
            self.matrix.setRowCount(len(rows))
            for row, entries in enumerate(rows):
                for column, value in enumerate(entries):
                    self.matrix.setItem(row, column, _item(value))
        self.matrix.resizeColumnsToContents()
        self.pages.setCurrentIndex(1)
