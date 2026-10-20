# SPDX-License-Identifier: MPL-2.0
"""A focused chooser for existing examples, scenarios and family configurations."""
from __future__ import annotations

import re
from html import escape

from PySide6.QtCore import QSignalBlocker, Qt
from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QSplitter,
    QTabWidget,
    QTextBrowser,
    QVBoxLayout,
    QWidget,
)

from etlayout.example_catalog import EXAMPLE_CATALOG, LEARNING_PATHS, START_HERE, SUBJECTS
from etlayout.example_library import COMPANION_CATALOG, example_card, example_choices
from etlayout.models import SimulationError


def _text(value):
    return escape(str(value)).replace("\n", "<br>")


class ExampleLibraryDialog(QDialog):
    """Choose inputs without touching the current document or starting a job."""

    def __init__(self, parent=None, identifier=None):
        super().__init__(parent)
        self.setWindowTitle("Browse example library")
        self.setObjectName("example_library_dialog")
        self.resize(980, 700)
        self.setMinimumSize(680, 480)
        self._identifier = None
        self._choices = None
        self._entries = []
        self._selections = {}
        for info in EXAMPLE_CATALOG.values():
            self._entries.append({**info.to_dict(), "identifier": info.name, "collection": "reference"})
        for name in COMPANION_CATALOG:
            card = example_card(name)
            self._entries.append(card)

        outer = QVBoxLayout(self)
        introduction = QLabel("Choose inputs, then open an editable project. No simulation starts automatically.")
        introduction.setWordWrap(True)
        outer.addWidget(introduction)
        split = QSplitter(Qt.Orientation.Horizontal)
        outer.addWidget(split, 1)
        left = QWidget()
        left.setMinimumWidth(220)
        left_layout = QVBoxLayout(left)
        left_layout.setContentsMargins(0, 0, 8, 0)
        filters = QFormLayout()
        self.collection = QComboBox()
        self.collection.setObjectName("example_collection")
        self.collection.setAccessibleName("Example collection")
        for label, key in [("All reference examples", "reference"), ("Recommended starts", "start"),
                           ("Companion routing studies", "companion")]:
            self.collection.addItem(label, key)
        self.subject = QComboBox()
        self.subject.setObjectName("example_subject")
        self.subject.setAccessibleName("Example subject")
        self.subject.addItem("All subjects", None)
        for subject in SUBJECTS:
            self.subject.addItem(subject, subject)
        self.level = QComboBox()
        self.level.setObjectName("example_level")
        self.level.setAccessibleName("Example learning level")
        self.level.addItem("All levels", None)
        for level in ("Starter", "Intermediate", "Advanced", "Engineering"):
            self.level.addItem(level, level)
        self.search = QLineEdit()
        self.search.setObjectName("example_search")
        self.search.setAccessibleName("Search example IDs, titles and questions")
        self.search.setPlaceholderText("ID, title, model or question…")
        self.search.setClearButtonEnabled(True)
        for combo in (self.collection, self.subject, self.level):
            combo.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
            combo.setMinimumContentsLength(12)
        filters.addRow("&Collection", self.collection)
        filters.addRow("&Subject", self.subject)
        filters.addRow("&Level", self.level)
        filters.addRow("&Search", self.search)
        left_layout.addLayout(filters)
        self.examples = QListWidget()
        self.examples.setObjectName("example_list")
        self.examples.setAccessibleName("Matching examples")
        self.examples.setWordWrap(True)
        left_layout.addWidget(self.examples, 1)
        self.count = QLabel()
        self.count.setWordWrap(True)
        left_layout.addWidget(self.count)
        split.addWidget(left)

        right = QWidget()
        right.setMinimumWidth(330)
        right_layout = QVBoxLayout(right)
        right_layout.setContentsMargins(8, 0, 0, 0)
        self.configuration = QGroupBox("Configuration to open")
        self.form = QFormLayout(self.configuration)
        self.form.setRowWrapPolicy(QFormLayout.RowWrapPolicy.WrapLongRows)
        self.scenario = self._combo("example_scenario", "Operating scenario")
        self.preset = self._combo("example_preset", "Size preset")
        self.variant = self._combo("example_variant", "Routing variant")
        self.thermal = self._combo("example_thermal", "Thermal boundary")
        for label, widget in [("Operating &scenario", self.scenario), ("Size &preset", self.preset),
                              ("Routing &variant", self.variant), ("Thermal &boundary", self.thermal)]:
            self.form.addRow(label, widget)
        right_layout.addWidget(self.configuration)
        self.selection_note = QLabel()
        self.selection_note.setObjectName("example_selection_note")
        self.selection_note.setWordWrap(True)
        right_layout.addWidget(self.selection_note)
        self.detail_tabs = QTabWidget()
        self.detail_tabs.setObjectName("example_detail_tabs")
        self.inputs = QTextBrowser()
        self.inputs.setObjectName("example_requested_inputs")
        self.inputs.setAccessibleName("Selected inputs to open")
        self.inputs.setOpenLinks(False)
        self.details = QTextBrowser()
        self.details.setObjectName("example_details")
        self.details.setAccessibleName("Example question, model and limitations")
        self.details.setOpenLinks(False)
        self.detail_tabs.addTab(self.inputs, "Inputs to open")
        self.detail_tabs.addTab(self.details, "Model && use")
        right_layout.addWidget(self.detail_tabs, 1)
        self.resource_note = QLabel()
        self.resource_note.setObjectName("example_resource_note")
        self.resource_note.setWordWrap(True)
        outer.addWidget(self.resource_note)
        split.addWidget(right)
        split.setSizes([340, 620])
        self.buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Open | QDialogButtonBox.StandardButton.Cancel)
        self.open_button = self.buttons.button(QDialogButtonBox.StandardButton.Open)
        self.open_button.setText("Open example")
        self.open_button.setObjectName("example_open")
        self.open_button.setDefault(True)
        self.open_button.setEnabled(False)
        footer = QHBoxLayout()
        footer.addStretch()
        footer.addWidget(self.buttons)
        outer.addLayout(footer)
        self.buttons.accepted.connect(self.accept)
        self.buttons.rejected.connect(self.reject)
        self.collection.currentIndexChanged.connect(self._collection_changed)
        self.subject.currentIndexChanged.connect(self._refresh_list)
        self.level.currentIndexChanged.connect(self._refresh_list)
        self.search.textChanged.connect(self._refresh_list)
        self.examples.currentItemChanged.connect(self._show_entry)
        self.examples.itemDoubleClicked.connect(lambda _: self.accept() if self.open_button.isEnabled() else None)
        self.scenario.currentIndexChanged.connect(self._update_selection)
        for combo in (self.preset, self.variant, self.thermal):
            combo.currentIndexChanged.connect(self._update_selection)
        self._refresh_list()
        if identifier:
            self.select_example(identifier)
        self.search.setFocus()

    @staticmethod
    def _combo(name, label):
        combo = QComboBox()
        combo.setObjectName(name)
        combo.setAccessibleName(label)
        return combo

    def _collection_changed(self, *_):
        companion = self.collection.currentData() == "companion"
        if companion:
            with QSignalBlocker(self.subject), QSignalBlocker(self.level):
                self.subject.setCurrentIndex(0)
                self.level.setCurrentIndex(0)
        self.subject.setEnabled(not companion)
        self.level.setEnabled(not companion)
        self._refresh_list()

    def _refresh_list(self, *_):
        selected = self._identifier
        collection = self.collection.currentData()
        query = self.search.text().strip().casefold()
        starts = set(START_HERE)
        candidates = [row for row in self._entries
                      if (row["collection"] == "companion") == (collection == "companion")]
        exact = any(query in {str(row.get("id", "")).casefold(), row["identifier"].casefold()}
                    for row in candidates) if query else False
        self.examples.clear()
        for row in candidates:
            if collection == "start" and row.get("id") not in starts:
                continue
            if collection != "companion" and self.subject.currentData() not in (None, row["category"]):
                continue
            if self.level.currentData() not in (None, row["level"]):
                continue
            paths = " ".join(path.title for path in LEARNING_PATHS if row.get("id") in path.examples)
            haystack = " ".join(str(row.get(key, "")) for key in
                                ("id", "identifier", "title", "category", "learning_goal", "physical_scope", "capabilities"))
            words = re.findall(r"[a-z0-9]+", (haystack + " " + paths).casefold())
            tokens = re.findall(r"[a-z0-9]+", query.replace("tlm", "transfer length"))
            matches = all(any(word.startswith(token) for word in words) for token in tokens)
            if query and (query not in {str(row.get("id", "")).casefold(), row["identifier"].casefold()} if exact else not matches):
                continue
            label = f"{row.get('id', row['identifier'])} · {row['title']}"
            if row.get("reference_status") == "stress":
                label += " · stress original"
            item = QListWidgetItem(label)
            item.setData(Qt.ItemDataRole.UserRole, row["identifier"])
            item.setToolTip(row["learning_goal"])
            self.examples.addItem(item)
        self.count.setText(f"{self.examples.count()} matching projects · {len(EXAMPLE_CATALOG)} references and {len(COMPANION_CATALOG)} routing companions")
        if self.examples.count():
            match = next((i for i in range(self.examples.count())
                          if self.examples.item(i).data(Qt.ItemDataRole.UserRole) == selected), 0)
            self.examples.setCurrentRow(match)
        else:
            self._identifier = None
            self._choices = None
            self.inputs.clear()
            self.details.setPlainText("No matches. Clear the search or change the collection, subject or level.")
            self.configuration.setEnabled(False)
            self.selection_note.clear()
            self.resource_note.clear()
            self.open_button.setEnabled(False)

    def select_example(self, identifier):
        """Select a stable ID/name without depending on a row number."""
        if identifier not in COMPANION_CATALOG:
            from etlayout.example_catalog import example_info
            identifier = example_info(identifier).name
        collection = "companion" if identifier in COMPANION_CATALOG else "reference"
        with QSignalBlocker(self.collection), QSignalBlocker(self.subject), QSignalBlocker(self.level), QSignalBlocker(self.search):
            self.collection.setCurrentIndex(self.collection.findData(collection))
            self.subject.setCurrentIndex(0)
            self.level.setCurrentIndex(0)
            self.search.clear()
        self._identifier = identifier
        self._collection_changed()

    def _show_entry(self, item, *_):
        if item is None:
            return
        self._identifier = item.data(Qt.ItemDataRole.UserRole)
        self._choices = example_choices(self._identifier)
        choices = self._choices
        self.configuration.setEnabled(True)
        remembered = self._selections.get(self._identifier, {})
        for combo, key, values, default in [
            (self.scenario, "scenario", [(s.title, s.id) for s in choices.scenarios], choices.recommended_scenario or "reference"),
            (self.preset, "preset", [(x, x) for x in choices.presets], choices.default_preset),
            (self.variant, "variant", [(x.replace('_', ' '), x) for x in choices.variants], choices.default_variant),
            (self.thermal, "thermal", [(x.replace('_', ' '), x) for x in choices.thermal], choices.default_thermal),
        ]:
            with QSignalBlocker(combo):
                combo.clear()
                for label, value in values:
                    combo.addItem(label, value)
                index = combo.findData(remembered.get(key, default))
                combo.setCurrentIndex(index if index >= 0 else 0)
        self.scenario.setEnabled(len(choices.scenarios) > 1)
        for combo in (self.preset, self.variant, self.thermal):
            self.form.setRowVisible(combo, bool(combo.count()))
        self.open_button.setEnabled(True)
        self.detail_tabs.setCurrentIndex(0)
        self._update_selection()

    def _update_selection(self, *_):
        if not self._identifier or not self._choices:
            return
        scenario = self.scenario.currentData() or "reference"
        self._selections[self._identifier] = dict(scenario=scenario, preset=self.preset.currentData(),
                                                  variant=self.variant.currentData(), thermal=self.thermal.currentData())
        named = scenario != "reference"
        for combo in (self.preset, self.variant, self.thermal):
            combo.setEnabled(bool(combo.count()) and not named)
        self.selection_note.setText(
            "This named scenario defines its own inputs. Size, routing and thermal choices are available with Original reference."
            if named and self._choices.presets else
            "Original reference uses the requested family inputs below. Default choices preserve the original configuration."
            if self._choices.presets else
            "The original reference remains available. The selected scenario creates an independent editable project.")
        try:
            card = example_card(self._identifier, scenario=scenario)
            selected = next((s for s in self._choices.scenarios if s.id == scenario), None)
            summary = card.get("project_summary") or {}
            reservoirs = "; ".join(
                f"{row['id']}: {row['value']:g} {row['unit']} ({row['kind']}), reservoir {row['ambient_K']:g} K"
                for row in summary.get("thermal_reservoirs", ()))
            field = {"potential": "Potential", "temperature": "Temperature", "sheet_current": "Sheet current",
                     "current_density": "Volumetric current density"}.get(card.get("field"), card.get("field", ""))
            self.details.setHtml(
                f"<h2>{_text(card.get('id', self._identifier))} · {_text(card['title'])}</h2>"
                f"<p><b>Question:</b> {_text(card['learning_goal'])}</p>"
                f"<p>{_text(card['category'])} · {_text(card['level'])} · {_text(card.get('role', ''))}</p>"
                f"<p><b>Reference context:</b> {_text(card.get('status_note', ''))}</p>"
                f"<p><b>First result:</b> layer {_text(card.get('layer', ''))}, {_text(field)}.</p>"
                f"<p><b>Model limits:</b> {_text(card['physical_scope'])}</p>"
                "<p><b>After opening:</b> review Mesh &amp; Checks, inspect inputs, validate (F6), mesh (F7), run (F5), "
                "then read the selected layer and field. A generated project or converged solve does not establish mesh verification or calibration.</p>")
            title = selected.title if selected else "Original reference"
            inputs_html = (f"<h3>Selected scenario inputs · {_text(title)}</h3>"
                           f"<p>{_text(selected.purpose if selected else '')}</p>"
                           f"<p>{_text(summary.get('summary', card.get('availability_note', 'Review inputs after opening.')))}</p>"
                           f"<p><b>Thermal path:</b> {_text(reservoirs or 'No configured thermal reservoir; use the stated electrical scope.')}</p>")
            note = ""
            if self._choices.presets and not named:
                from etlayout.power_examples import settings
                config = settings(self._identifier, preset=self.preset.currentData(), variant=self.variant.currentData(),
                                  thermal=self.thermal.currentData())
                dimensions = (f"{config.fingers} × {config.gate_width_um:g} µm conducting gate width per bank"
                              if config.family.startswith("gan") else f"{config.side_um:g} µm die size setting")
                if config.family == "gan_banks":
                    dimensions += f"; {config.bank_columns} × {config.bank_rows} banks"
                inputs_html = (f"<h3>Requested configuration inputs</h3>"
                               f"<p><b>Operating point:</b> {config.current:g} A</p>"
                               f"<p><b>Geometry setting:</b> {_text(dimensions)}</p>"
                               f"<p><b>Size preset:</b> {_text(config.preset)}<br>"
                               f"<b>Routing variant:</b> {_text(config.variant)}<br>"
                               f"<b>Thermal boundary:</b> {_text(config.thermal)}</p>"
                               "<p>The generated project's actual physical dimensions and thermal parameters are reviewed after opening. "
                               "The requested settings above replace the original reference overview.</p>")
                note = (f"Resource budget: {config.max_elements:,} elements · {config.max_dofs:,} DOFs · "
                        f"{config.memory_budget_bytes / 1024**3:g} GiB memory screen. Not measured performance.")
                if config.preset in {"large", "stress"}:
                    note += " Large/stress cases are opt-in; resource and model limits apply."
            self.inputs.setHtml(inputs_html + f"<p><b>Workload:</b> {_text(card.get('workload', 'Review mesh and job settings.'))}</p>")
            self.resource_note.setText(note)
            self.open_button.setEnabled(True)
        except (ValueError, OSError, KeyError, SimulationError) as error:
            self.inputs.setPlainText(f"Example details could not be read: {error}")
            self.details.clear()
            self.open_button.setEnabled(False)
            self.resource_note.clear()

    def selection(self):
        """Return only valid options; a named scenario never silently loses inputs."""
        if not self._identifier or not self.open_button.isEnabled():
            raise ValueError("Choose an available example first")
        scenario = self.scenario.currentData() or "reference"
        options = {"name": self._identifier, "scenario": scenario}
        if scenario == "reference" and self._choices.presets:
            options.update(preset=self.preset.currentData(), variant=self.variant.currentData(),
                           thermal=self.thermal.currentData())
        return options