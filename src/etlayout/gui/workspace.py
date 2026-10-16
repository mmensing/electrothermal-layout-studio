# SPDX-License-Identifier: MPL-2.0
"""Compact navigation, engineering summaries and immutable run browsing."""
from __future__ import annotations

import sys
from dataclasses import is_dataclass

import numpy as np
from PySide6.QtCore import Signal
from PySide6.QtWidgets import (
    QComboBox,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)


def engineering(value, unit=""):
    """Readable scalar with an explicit unit; absolute temperatures stay kelvin."""
    if value is None:
        return "Unavailable"
    try:
        value = float(value)
    except (ValueError, TypeError):
        return str(value)
    if not np.isfinite(value):
        return "Unavailable"
    from etlayout.visualization.units import DISPLAY_UNITS, display_unit, to_display
    if unit in DISPLAY_UNITS:
        return f"{to_display(value, unit):.5g} {display_unit(unit)}"
    if unit in {"K", "fraction", "ratio", ""} or value == 0:
        return f"{value:.5g} {unit}".strip()
    exponent = max(-12, min(12, int(np.floor(np.log10(abs(value)) / 3)) * 3))
    prefix = {-12: "p", -9: "n", -6: "µ", -3: "m", 0: "", 3: "k", 6: "M", 9: "G", 12: "T"}[exponent]
    return f"{value / 10 ** exponent:.5g} {prefix}{unit}"


class ConfigurationNavigation(QWidget):
    """All editors remain reachable without clipped horizontal tabs."""
    GROUPS = {"Stack": "Geometry", "Refinement": "Geometry", "Terminals": "Electrical",
              "Interfaces": "Electrical / thermal", "Thermal BC": "Thermal", "Heat sources": "Thermal",
              "Materials": "Technology", "Settings": "Simulation"}

    def __init__(self, parent=None):
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        self.selector = QComboBox()
        self.selector.setAccessibleName("Configuration editor")
        self.selector.setToolTip("Choose a geometry, electrical, thermal, technology or simulation editor.")
        layout.addWidget(self.selector)
        self.pages = QStackedWidget()
        layout.addWidget(self.pages)
        self.selector.currentIndexChanged.connect(self.pages.setCurrentIndex)

    def addTab(self, widget, title):
        index = self.pages.addWidget(widget)
        self.selector.addItem(f"{self.GROUPS.get(title, 'Project')}  /  {title}")
        return index

    def setCurrentWidget(self, widget):
        self.selector.setCurrentIndex(self.pages.indexOf(widget))

    def setCurrentIndex(self, index):
        self.selector.setCurrentIndex(index)

    def currentWidget(self):
        return self.pages.currentWidget()


class RunHistoryPanel(QWidget):
    inspect_requested = Signal(object)
    compare_requested = Signal(object)
    remove_requested = Signal(object)
    cancel_requested = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        layout = QVBoxLayout(self)
        note = QLabel("Runs retain their exact setup and fields. Select a run and press Enter to inspect it; select two runs to compare their maps. Remove selected releases retained results from this history.")
        note.setWordWrap(True)
        layout.addWidget(note)
        self.memory_summary = QLabel()
        self.memory_summary.setAccessibleName("Run history retained memory")
        layout.addWidget(self.memory_summary)
        self.list = QListWidget()
        self.list.setAccessibleName("Retained simulation runs")
        self.list.setSelectionMode(QListWidget.SelectionMode.ExtendedSelection)
        self.list.itemActivated.connect(lambda item: self.inspect_requested.emit(self.results[self.list.row(item)]))
        layout.addWidget(self.list)
        row = QHBoxLayout()
        for label, callback in [("Inspect run", self.inspect), ("Compare selected", self.compare), ("Remove selected", self.remove)]:
            button = QPushButton(label)
            button.clicked.connect(callback)
            row.addWidget(button)
        self.cancel_read = QPushButton("Cancel history read")
        self.cancel_read.setAccessibleName("Cancel loading recorded fields")
        self.cancel_read.clicked.connect(self.cancel_requested)
        self.cancel_read.hide()
        row.addWidget(self.cancel_read)
        layout.addLayout(row)
        self.results = []

    def set_results(self, results):
        selected = {getattr(item, "run_id", id(item)) for item in self.selected()}
        self.results = list(results)
        self.memory_summary.setText(f"{len(self.results)} retained runs · estimated memory {retained_result_bytes(self.results) / 1024**2:.1f} MiB")
        self.list.clear()
        for index, result in enumerate(results):
            from etlayout.gui.history_workflow import is_record
            if is_record(result):
                status = "Unavailable" if not result.available else "Stored converged" if result.converged else "Stored not converged"
                time = f" · {len(result.times)} saved frames" if result.times else ""
                item = QListWidgetItem(f"{index + 1}. {result.name} · {result.mode}{time} · {status}\n"
                    f"{result.estimated_bytes / 1024**2:.1f} MiB logical arrays · {result.elapsed_seconds:.3g} s · {result.configuration_hash[:12]}")
                item.setToolTip(result.error or "Stored metadata only. Numerical arrays are loaded and validated when requested. Convergence is the recorded status, not a new verification.")
                self.list.addItem(item)
                item.setSelected(result.run_id in selected)
                continue
            config = result.configuration
            mode = config.get("solver", {}).get("mode", "analysis")
            status = "Converged" if result.converged else "Not converged"
            if result.transient:
                mode += f" · transient to {engineering(result.transient.times[-1], 's')}"
                status = "Completed" if result.transient.completed else "Incomplete"
            item = QListWidgetItem(f"{index + 1}. {config.get('name', 'Run')} · {mode} · {status}\n"
                                   f"{len(result.mesh.triangles):,} triangles · {retained_result_bytes([result]) / 1024**2:.1f} MiB · {result.elapsed_seconds:.3g} s · {result.configuration_hash[:12]}")
            item.setToolTip("Exact recorded configuration and numerical fields; saved with the project.")
            self.list.addItem(item)
            item.setSelected(id(result) in selected)

    def set_loading(self, busy):
        self.cancel_read.setVisible(busy)

    def set_cache_stats(self, stats):
        self.memory_summary.setText(
            f"{len(self.results)} retained records · cache {stats.resident_bytes / 1024**2:.1f}/{stats.budget_bytes / 1024**2:.0f} MiB · "
            f"pinned {stats.pinned_bytes / 1024**2:.1f} MiB · reads {stats.active_reads} · hits/misses {stats.hits}/{stats.misses}")
        self.memory_summary.setToolTip(
            f"Unique resident array buffers, not process RSS. Next selection estimate: {stats.estimated_next_bytes / 1024**2:.1f} MiB. "
            f"Pinned private archive copy: {stats.private_copy_bytes / 1024**2:.1f} MiB on disk. "
            f"Temporary over-budget use: {stats.over_budget_bytes / 1024**2:.1f} MiB.")

    def selected(self):
        return [self.results[self.list.row(item)] for item in self.list.selectedItems()]

    def inspect(self):
        selected = self.selected()
        if selected:
            self.inspect_requested.emit(selected[0])

    def compare(self):
        self.compare_requested.emit(self.selected())

    def remove(self):
        selected = self.selected()
        if selected:
            self.remove_requested.emit(selected)


def retained_result_bytes(results):
    """Estimate owned Python/NumPy storage once per shared object or array base.

    This describes retained result memory, not process RSS or peak solve memory.
    """
    seen = set()
    def size(value):
        identity = id(value)
        if identity in seen:
            return 0
        seen.add(identity)
        total = sys.getsizeof(value)
        if isinstance(value, np.ndarray):
            return total + (size(value.base) if value.base is not None else 0)
        if isinstance(value, dict):
            return total + sum(size(key) + size(item) for key, item in value.items())
        if isinstance(value, (list, tuple, set)):
            return total + sum(size(item) for item in value)
        if is_dataclass(value):
            return total + size(vars(value))
        return total
    return size(list(results))


def run_caption(result):
    config = result.configuration
    timing = f" · t = {engineering(result.metrics['time_s'], 's')}" if "time_s" in result.metrics else ""
    return f"{config.get('name', 'Run')} · {config.get('solver', {}).get('mode', 'analysis')}{timing} · {result.configuration_hash[:8]}"


def configuration_changes(first, second, prefix=""):
    """Human-readable immutable setup differences, including nested model IDs."""
    if isinstance(first, dict) and isinstance(second, dict):
        output = []
        for key in sorted(set(first) | set(second)):
            if key == "path" and prefix == "layout":
                continue
            output.extend(configuration_changes(first.get(key), second.get(key), f"{prefix}.{key}".strip(".")))
        return output
    if isinstance(first, list) and isinstance(second, list) and all(isinstance(x, dict) and "id" in x for x in [*first, *second]):
        return configuration_changes({x["id"]: x for x in first}, {x["id"]: x for x in second}, prefix)
    return [] if first == second else [(prefix, str(first), str(second))]
