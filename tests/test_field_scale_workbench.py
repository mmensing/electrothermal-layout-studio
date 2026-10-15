# SPDX-License-Identifier: MPL-2.0
"""Manual range validation and exact legend state for result maps."""
from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

from etlayout.gui.field_scale import FieldScaleControl


@pytest.fixture
def scale(qtbot):
    control = FieldScaleControl()
    qtbot.addWidget(control)
    control.set_details({"field": "potential", "unit": "V", "low": .001, "high": 1., "colormap": "viridis"})
    return control


def test_manual_engineering_units_and_auto_restore(scale, qtbot):
    scale.lock.setChecked(True)
    scale.minimum.setText("20 mV")
    scale.maximum.setText("800 mV")
    assert scale.validated_limits() == (.02, .8)
    assert scale.current_colormap == "viridis"
    with qtbot.waitSignal(scale.range_changed):
        scale.auto_button.click()
    assert not scale.lock.isChecked()
    assert scale.validated_limits() is None
    assert not scale.minimum.isEnabled()


@pytest.mark.parametrize("low,high,logarithmic", [
    ("", "1", False), ("NaN", "1", False), ("0", "Inf", False),
    ("1", "1", False), ("2", "1", False), ("0", "1", True),
])
def test_invalid_manual_values_remain_visible_and_never_validate_as_auto(scale, low, high, logarithmic):
    scale.lock.setChecked(True)
    scale.minimum.setText(low)
    scale.maximum.setText(high)
    with pytest.raises(ValueError, match="Manual range not applied"):
        scale.validated_limits(logarithmic)
    assert scale.lock.isChecked()
    assert not scale.valid
    assert not scale.error.isHidden()
    # A cleared display retains the unit context and the invalid manual choice.
    scale.set_details(None)
    with pytest.raises(ValueError, match="Manual range not applied"):
        scale.validated_limits(logarithmic)
    assert (scale.minimum.text(), scale.maximum.text()) == (low, high)


def test_range_state_round_trip_and_quantity_change(scale):
    scale.lock.setChecked(True)
    scale.minimum.setText("10 mV")
    scale.maximum.setText("500 mV")
    saved = scale.capture_state()
    scale.reset()
    scale.restore_state(saved, emit=False)
    assert scale.validated_limits() == (.01, .5)
    scale.set_colormap("magma")
    assert scale.current_colormap == "magma"
    assert scale.validated_limits() == (.01, .5)
    scale.set_details({"field": "temperature", "unit": "K", "low": 300., "high": 320., "colormap": "magma"})
    scale.restore_state(saved, emit=False)
    assert not scale.lock.isChecked()
    assert scale.minimum.text() == "300"
    assert scale.unit_label.text() == "K"


def test_compact_range_inputs_retain_sign_and_full_value_tooltips(scale, qtbot):
    scale.set_details({"field": "potential", "unit": "V", "low": -4.133999e9, "high": 4.133999e9})
    scale.resize(250, 110)
    scale.show()
    qtbot.waitUntil(scale.isVisible)
    for entry in (scale.minimum, scale.maximum):
        assert 100 <= entry.width() <= 110
        assert entry.text() in entry.toolTip()
        assert entry.cursorPosition() == 0
    assert scale.minimum.text() == "-4.133999e+09"
    scale.lock.setChecked(True)
    scale.maximum.setText("10000000000.0")
    assert "10000000000.0" in scale.maximum.toolTip()
    assert scale.validated_limits() == (-4.133999e9, 1e10)


def test_constant_legend_uses_one_solid_midpoint_color(scale):
    import numpy as np

    from etlayout.visualization.palettes import rgba

    scale.set_details({"field": "potential", "unit": "V", "low": 2.5, "high": 2.5, "colormap": "viridis"})
    scale.color_bar.resize(320, 45)
    image = scale.color_bar.grab().toImage()
    midpoint = rgba(np.array([.5]), "viridis")[0]
    expected = tuple(round(float(channel) * 255) for channel in midpoint)
    for x in (15, image.width() // 2, image.width() - 15):
        assert image.pixelColor(x, 8).getRgb() == pytest.approx(expected, abs=1)
