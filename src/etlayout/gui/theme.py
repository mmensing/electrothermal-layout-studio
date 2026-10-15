# SPDX-License-Identifier: MPL-2.0
"""Complete Qt palettes: form bodies must not inherit an unrelated OS theme."""
from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QPalette
from PySide6.QtWidgets import QApplication


def apply_widget_theme(widget, dark: bool | None = False):
    app = QApplication.instance()
    if dark is None:
        dark = app.styleHints().colorScheme() == Qt.ColorScheme.Dark
    colors = {
        "window": "#202936" if dark else "#f3f7fc",
        "base": "#17202d" if dark else "#ffffff",
        "text": "#eaf1fc" if dark else "#1d2d49",
        "panel": "#2a374b" if dark else "#edf3fc",
        "border": "#526782" if dark else "#d4dfed",
        "accent": "#77b5ff" if dark else "#006bff",
        "selected": "#234d7a" if dark else "#dfeeff",
        "disabled": "#9baec9" if dark else "#5c7191",
        "error": "#ffb4a9" if dark else "#a1261b",
    }
    palette = QPalette()
    roles = {
        "Window": "window", "WindowText": "text", "Base": "base", "AlternateBase": "panel",
        "Text": "text", "Button": "panel", "ButtonText": "text", "ToolTipBase": "base",
        "ToolTipText": "text", "Highlight": "selected", "HighlightedText": "text",
        "Link": "accent", "PlaceholderText": "disabled",
    }
    for role, color in roles.items():
        palette.setColor(getattr(QPalette.ColorRole, role), QColor(colors[color]))
    for role in (QPalette.ColorRole.Text, QPalette.ColorRole.WindowText, QPalette.ColorRole.ButtonText):
        palette.setColor(QPalette.ColorGroup.Disabled, role, QColor(colors["disabled"]))
    if app.palette() != palette:
        app.setPalette(palette)
    widget.setPalette(palette)
    # Scope styling to the owning window and its dialogs. Replacing the global
    # application stylesheet repolishes unrelated/deferred-delete plot widgets
    # and can crash Qt on Windows when several windows have been opened/closed.
    style = """
    QWidget { background: %(window)s; color: %(text)s; font-family: 'Segoe UI', Arial; font-size: 10pt; }
    QToolBar { spacing: 6px; padding: 5px; border-bottom: 1px solid %(border)s; }
    QDockWidget::title { background: %(panel)s; padding: 7px; font-weight: 600; }
    QTabWidget::pane { border: 1px solid %(border)s; }
    QTabBar::tab { padding: 7px 10px; background: %(panel)s; }
    QTabBar::tab:selected { background: %(base)s; border-bottom: 2px solid %(accent)s; }
    QTableWidget, QTreeWidget, QListWidget, QTextEdit, QLineEdit, QComboBox {
        background: %(base)s; color: %(text)s; border: 1px solid %(border)s;
        selection-background-color: %(selected)s; selection-color: %(text)s;
    }
    QHeaderView::section, QTableCornerButton::section {
        background: %(panel)s; color: %(text)s; border: 0; padding: 6px; font-weight: 600;
    }
    QPushButton, QToolButton { background: %(base)s; color: %(text)s; padding: 7px 10px;
        border: 1px solid %(border)s; border-radius: 3px; }
    QPushButton:hover, QToolButton:hover { background: %(selected)s; }
    QPushButton:disabled, QToolButton:disabled, QLineEdit:disabled, QComboBox:disabled { color: %(disabled)s; }
    QLineEdit, QComboBox { padding: 5px; }
    QLineEdit:focus, QComboBox:focus, QPushButton:focus, QToolButton:focus,
    QCheckBox:focus, QListWidget:focus, QTreeWidget:focus, QTableWidget:focus {
        border: 2px solid %(accent)s;
    }
    QLineEdit[invalid="true"], QComboBox[invalid="true"] { border: 2px solid %(error)s; }
    QLabel[error="true"] { color: %(error)s; }
    QProgressBar { border: 1px solid %(border)s; text-align: center; background: %(base)s; }
    QProgressBar::chunk { background: %(selected)s; }
    QCheckBox::indicator { width: 15px; height: 15px; border: 1px solid %(border)s; background: %(base)s; }
    QCheckBox::indicator:checked { background: %(accent)s; border: 2px solid %(border)s; }
    QGroupBox { border: 1px solid %(border)s; border-radius: 3px; margin-top: 12px; padding-top: 12px; }
    QGroupBox::title { subcontrol-origin: margin; left: 10px; padding: 0 5px; }
    QScrollBar { background: %(panel)s; }
    QScrollBar::handle { background: %(border)s; min-width: 18px; min-height: 18px; }
    QFrame#projectHeader { background: %(panel)s; border-bottom: 1px solid %(border)s; }
    QWidget#contextInspector { background: %(base)s; border: 1px solid %(border)s; border-radius: 5px; }
    QLabel[brand="true"] { font-size: 19pt; font-weight: 700; color: %(text)s; }
    QLabel[heading="true"] { font-size: 12pt; font-weight: 700; }
    QLabel[section="true"] { font-size: 10pt; font-weight: 600; color: %(disabled)s; padding: 5px 2px; }
    QLabel[subtle="true"] { color: %(disabled)s; }
    QLabel[hint="true"], QLabel[context="true"] { background: %(panel)s; border-radius: 4px; padding: 8px; }
    QLabel[stageTitle="true"] { font-weight: 600; }
    QPushButton[stage="true"] { background: transparent; border: 1px solid transparent; text-align: left; padding: 0; }
    QPushButton[stage="true"] QLabel { background: transparent; }
    QPushButton[stage="true"]:checked { background: %(selected)s; border: 1px solid %(border)s; }
    QLabel[stageActive="true"] { color: %(accent)s; }
    QLabel#stageNumber { border-radius: 15px; background: %(panel)s; color: %(disabled)s; font-size: 13pt; font-weight: 600; }
    QLabel#stageNumber[stageActive="true"] { background: %(accent)s; color: white; }
    QPushButton[primary="true"] { background: #006bff; border: 1px solid #006bff; color: white; font-weight: 600; padding: 9px 12px; }
    QPushButton[primary="true"]:hover { background: #0059d8; }
    QPushButton[primary="true"]:disabled { background: %(panel)s; border-color: %(border)s; color: %(disabled)s; }
    QFrame[selectionBanner="true"] { border: 1px solid %(accent)s; border-radius: 4px; background: %(selected)s; }
    QSplitter::handle { background: %(border)s; }
    """ % colors
    widget.setStyleSheet(style)
