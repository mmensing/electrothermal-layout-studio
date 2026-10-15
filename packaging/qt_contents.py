# SPDX-License-Identifier: MPL-2.0
"""Keep the desktop's Qt modules; reject unexpected modules after collection."""
from pathlib import PurePosixPath

QT_MODULES = {"qt6core.dll", "qt6gui.dll", "qt6network.dll", "qt6opengl.dll",
              "qt6openglwidgets.dll", "qt6svg.dll", "qt6svgwidgets.dll",
              "qt6widgets.dll", "qt6test.dll"}
UNUSED_MODULES = {"qt6pdf.dll", "qt6qml.dll", "qt6qmlmeta.dll", "qt6qmlmodels.dll",
                  "qt6qmlworkerscript.dll", "qt6quick.dll", "qt6virtualkeyboard.dll"}


def keep_qt_entry(destination):
    name = destination.replace("\\", "/").lower()
    base = PurePosixPath(name).name
    if "pyside6/plugins/" in name:
        if "/platforminputcontexts/" in name or "/qmltooling/" in name or base == "qpdf.dll":
            return False
    if base in UNUSED_MODULES:
        return False
    if base.startswith("qt6") and base.endswith(".dll") and base not in QT_MODULES:
        raise ValueError(f"Unexpected Qt module requires explicit licensing/functional review: {base}")
    return True
