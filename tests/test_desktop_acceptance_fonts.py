# SPDX-License-Identifier: MPL-2.0
"""Real isolated Qt font checks; missing glyphs must fail before screenshot QA."""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
pytestmark = pytest.mark.fast


def run_font_check(tmp_path, operation):
    script = r'''
import json, pathlib, sys
sys.path.insert(0, sys.argv[1])
from PySide6.QtGui import QRawFont
from PySide6.QtWidgets import QApplication, QLabel
from desktop_acceptance_support import REQUIRED_CHARACTERS, prepare_desktop_fonts, verify_glyph_support
app = QApplication([])
report = prepare_desktop_fonts(app, pathlib.Path(sys.argv[2]))
operation = sys.argv[3]
if operation == 'valid':
    label = QLabel(REQUIRED_CHARACTERS)
    label.setStyleSheet("font-family: 'Segoe UI', Arial; font-size: 10pt")
    label.ensurePolished()
    verify_glyph_support(QRawFont.fromFont(label.font()))
    assert label.grab().save(str(pathlib.Path(sys.argv[2]) / 'actual-label.png'))
    assert report['status'] == 'passed'
    print(json.dumps({'qpa': app.platformName(), 'verified': sorted(report['fonts'])}))
elif operation == 'missing':
    font = QRawFont.fromFont(app.font())
    assert font.isValid() and not font.supportsCharacter(0x10FFFF)
    try:
        verify_glyph_support(font, REQUIRED_CHARACTERS + chr(0x10FFFF))
    except RuntimeError as error:
        assert 'U+10FFFF' in str(error)
        print('Missing actual glyph rejected')
    else:
        raise AssertionError('A real missing glyph passed verification')
elif operation == 'invalid':
    try:
        verify_glyph_support(QRawFont())
    except RuntimeError as error:
        assert 'valid application font' in str(error)
        print('Invalid actual raw font rejected')
    else:
        raise AssertionError('An invalid raw font passed verification')
'''
    return subprocess.run(
        [sys.executable, '-c', script, str(ROOT / 'scripts'), str(tmp_path), operation],
        env={**os.environ, 'QT_QPA_PLATFORM': 'offscreen', 'QT_SCALE_FACTOR': '1'},
        capture_output=True, text=True, encoding='utf-8', timeout=30, check=True)


@pytest.mark.parametrize('operation', ['valid', 'missing', 'invalid'])
def test_real_font_verification(tmp_path, operation):
    result = run_font_check(tmp_path, operation)
    receipt = json.loads((tmp_path / 'font-verification.json').read_text(encoding='utf-8'))
    assert receipt['status'] == 'passed'
    assert set(receipt['fonts']) == {'regular', 'bold'}
    assert all(not font['missing_codepoints'] for font in receipt['fonts'].values())
    if operation == 'valid':
        assert json.loads(result.stdout)['verified'] == ['bold', 'regular']
        assert (tmp_path / 'actual-label.png').is_file()
    else:
        assert 'rejected' in result.stdout
