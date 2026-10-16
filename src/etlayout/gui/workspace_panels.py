# SPDX-License-Identifier: MPL-2.0
"""Application-local panel layout; never part of a simulation document."""
from __future__ import annotations

import json

from PySide6.QtCore import QEvent, QObject, Qt
from PySide6.QtWidgets import QApplication

SETTINGS_KEY = "workspace/panels_v1"
DEFAULT_HORIZONTAL = [250, 890, 400]
DEFAULT_VERTICAL = [650, 190]


def validated_panel_state(value: object) -> dict | None:
    """Read only bounded, versioned primitives; reject corrupt/older preferences."""
    try:
        state = json.loads(value) if isinstance(value, str) else value
        if not isinstance(state, dict) or type(state.get("version")) is not int or state["version"] != 1:
            return None
        if not isinstance(state.get("visible"), dict) or set(state["visible"]) != {"left", "right", "drawer"}:
            return None
        if any(type(flag) is not bool for flag in state["visible"].values()):
            return None
        for key, count in (("horizontal", 3), ("vertical", 2)):
            sizes = state[key]
            if not isinstance(sizes, list) or len(sizes) != count:
                return None
            if any(type(size) is not int or not 1 <= size <= 10000 for size in sizes):
                return None
        return state
    except (KeyError, TypeError, ValueError):
        return None


class WorkspacePanels(QObject):
    """Own visibility, remembered splitter sizes and action synchronization only."""

    def __init__(self, workspace):
        super().__init__(workspace.window)
        self.workspace = workspace
        self.window = workspace.window
        self.widgets = {"left": workspace.navigation, "right": workspace.inspector,
                        "drawer": workspace.drawer}
        self.horizontal = list(DEFAULT_HORIZONTAL)
        self.vertical = list(DEFAULT_VERTICAL)
        self.actions = {}
        self.maximize_action = None
        self._before_maximize = None
        self._applying = False
        self._ready = False
        for widget in self.widgets.values():
            widget.installEventFilter(self)
        workspace.splitter.splitterMoved.connect(self.remember)
        workspace.center_splitter.splitterMoved.connect(self.remember)

    def bind_actions(self, actions, maximize):
        self.actions = actions
        self.maximize_action = maximize
        for action in (*actions.values(), maximize):
            action.setCheckable(True)
        self.sync_actions()

    def visible(self, name: str) -> bool:
        return not self.widgets[name].isHidden()

    def capture(self) -> dict:
        self._remember_sizes()
        return {"version": 1, "visible": {name: self.visible(name) for name in self.widgets},
                "horizontal": list(self.horizontal), "vertical": list(self.vertical)}

    def _remember_sizes(self):
        for current, saved in ((self.workspace.splitter.sizes(), self.horizontal),
                               (self.workspace.center_splitter.sizes(), self.vertical)):
            for index, size in enumerate(current):
                if size > 0:
                    saved[index] = min(size, 10000)

    def persist(self):
        if self._ready and not self._applying:
            self.window.settings.setValue(SETTINGS_KEY, json.dumps(self.capture()))

    def remember(self, *_):
        self._before_maximize = None
        self.sync_actions()
        self.persist()

    def sync_actions(self):
        for name, action in self.actions.items():
            action.setChecked(self.visible(name))
        if self.maximize_action:
            self.maximize_action.setChecked(not any(self.visible(name) for name in self.widgets))

    def eventFilter(self, watched, event):
        if watched in self.widgets.values() and event.type() in (QEvent.Type.Show, QEvent.Type.Hide):
            self.sync_actions()
            self.persist()
        return super().eventFilter(watched, event)

    def focus_canvas(self):
        self.workspace.canvas_tabs.setCurrentIndex(0)
        self.window.view.setFocus(Qt.FocusReason.ShortcutFocusReason)

    def set_visible(self, name: str, visible: bool):
        self._remember_sizes()
        widget = self.widgets[name]
        focused = QApplication.focusWidget()
        hiding_focus = not visible and focused is not None and (focused is widget or widget.isAncestorOf(focused))
        self._applying = True
        try:
            widget.setVisible(visible)
            if visible:
                splitter = self.workspace.center_splitter if name == "drawer" else self.workspace.splitter
                splitter.setSizes(self.vertical if name == "drawer" else self.horizontal)
        finally:
            self._applying = False
        if hiding_focus:
            self.focus_canvas()
        self.sync_actions()
        self.persist()

    def toggle(self, name: str):
        self._before_maximize = None
        self.set_visible(name, not self.visible(name))

    def open_drawer(self, index: int):
        self._before_maximize = None
        self.set_visible("drawer", True)
        self.workspace.drawer.setCurrentIndex(index)

    def restore_state(self, value: object) -> bool:
        state = validated_panel_state(value)
        if state is None:
            self.restore_default()
            return False
        self._before_maximize = None
        self.horizontal, self.vertical = list(state["horizontal"]), list(state["vertical"])
        self._applying = True
        try:
            for name, visible in state["visible"].items():
                self.widgets[name].setVisible(visible)
            self.workspace.splitter.setSizes(self.horizontal)
            self.workspace.center_splitter.setSizes(self.vertical)
        finally:
            self._applying = False
        self.sync_actions()
        self.persist()
        return True

    def restore_preferences(self):
        stored = self.window.settings.value(SETTINGS_KEY)
        if stored is not None:
            self.restore_state(stored)
        self._ready = True
        self.sync_actions()
        self.persist()

    def restore_default(self):
        return self.restore_state({"version": 1,
            "visible": {"left": True, "right": True, "drawer": True},
            "horizontal": list(DEFAULT_HORIZONTAL), "vertical": list(DEFAULT_VERTICAL)})

    def maximize_canvas(self):
        if self._before_maximize is None and not any(self.visible(name) for name in self.widgets):
            self.restore_default()
            self.focus_canvas()
            return
        if self._before_maximize is not None:
            state, self._before_maximize = self._before_maximize, None
            self.restore_state(state)
            self.focus_canvas()
            return
        previous = self.capture()
        for name in self.widgets:
            self.set_visible(name, False)
        self._before_maximize = previous
        self.sync_actions()
        self.persist()
        self.focus_canvas()
