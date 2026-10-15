# SPDX-License-Identifier: MPL-2.0
"""Shared font verification for real desktop acceptance captures.

Windows' offscreen Qt plugin does not discover installed fonts. Register the
host's existing fonts for that test plugin only; never copy or bundle them.
Native runs keep their font database and application font unchanged.
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

REQUIRED_CHARACTERS = "ETLayout0123456789µΩ²Δ°C×−→≤≥"
WINDOWS_FONT_FILES = ("segoeui.ttf", "segoeuib.ttf", "segoeuisl.ttf")


def verify_glyph_support(raw_font, characters=REQUIRED_CHARACTERS):
    """Reject invalid fonts and missing glyphs, rather than accepting tofu images."""
    if not raw_font.isValid():
        raise RuntimeError("Desktop capture requires a valid application font.")
    missing = sorted({f"U+{ord(char):04X}" for char in characters
                      if not raw_font.supportsCharacter(ord(char))})
    if missing:
        raise RuntimeError("Desktop capture font lacks required glyphs: " + ", ".join(missing))
    return {"family": raw_font.familyName(), "style": raw_font.styleName(),
            "verified_characters": characters, "missing_codepoints": []}


def prepare_desktop_fonts(app, output):
    """Verify regular/bold fonts before rendering, and write an explicit receipt."""
    from PySide6.QtCore import qVersion
    from PySide6.QtGui import QFont, QFontDatabase, QRawFont

    output = Path(output)
    output.mkdir(parents=True, exist_ok=True)
    receipt = {"qpa": app.platformName(), "qt_version": qVersion(),
               "loaded_font_families": [], "native_font_database_unchanged": True,
               "fonts": {}, "status": "failed"}
    try:
        if sys.platform == "win32" and app.platformName() == "offscreen":
            font_directory = Path(os.environ.get("WINDIR", "C:/Windows")) / "Fonts"
            for filename in WINDOWS_FONT_FILES:
                path = font_directory / filename
                if not path.is_file():
                    if filename == "segoeuisl.ttf":
                        continue  # Optional light face; regular/bold glyph checks remain mandatory.
                    raise RuntimeError("Offscreen Windows capture requires installed host font " + filename)
                font_id = QFontDatabase.addApplicationFont(str(path))
                families = QFontDatabase.applicationFontFamilies(font_id)
                if font_id < 0 or not families:
                    raise RuntimeError("Could not register host font " + filename)
                receipt["loaded_font_families"].extend(families)
            receipt["native_font_database_unchanged"] = False
            app.setFont(QFont("Segoe UI", 9))
        for name, bold in (("regular", False), ("bold", True)):
            font = QFont(app.font())
            font.setBold(bold)
            receipt["fonts"][name] = verify_glyph_support(QRawFont.fromFont(font))
        receipt["loaded_font_families"] = sorted(set(receipt["loaded_font_families"]))
        receipt["status"] = "passed"
    except RuntimeError as error:
        receipt["error"] = str(error)
        raise
    finally:
        (output / "font-verification.json").write_text(
            json.dumps(receipt, indent=2, ensure_ascii=False), encoding="utf-8")
    return receipt
