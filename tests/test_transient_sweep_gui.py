# SPDX-License-Identifier: MPL-2.0
"""The native sweep dialog must retain the actual waveform parameter target."""
from types import MethodType, SimpleNamespace

import pytest

from etlayout.examples import make_example
from etlayout.gui.main_window import MainWindow
from etlayout.models import SweepSpec


def sweep_window(project):
    """Keep the focused dialog-contract adapter on the real shared lock policy."""
    window = SimpleNamespace(project=project, job=None, inspection_context=None)
    window.model_locked = MethodType(MainWindow.model_locked, window)
    return window


@pytest.mark.parametrize("example,unit,hidden", [
    ("E17", "W/m²", "heat_sources.heater.value"),
    ("E18", "A", "terminals.drain.value"),
])
def test_native_sweep_keeps_saved_pulse_target_and_hides_overridden_base(example, unit, hidden, tmp_path, monkeypatch):
    project = make_example(example, tmp_path)
    captured = {}

    class Dialog:
        def __init__(self, spec, title, parent, **kwargs):
            captured.update(spec=spec, **kwargs)

        def exec(self):
            return False

    monkeypatch.setattr("etlayout.gui.main_window.ModelDialog", Dialog)
    window = sweep_window(project)
    MainWindow.run_sweep_dialog(window)
    path = "transient.drives.0.waveform.high"
    assert captured["spec"].parameter == path
    assert hidden not in captured["choices"]["parameter"]
    assert captured["parameter_units"][path] == unit
    assert captured["parameter_units"]["transient.drives.0.waveform.width"] == "s"


def test_native_sweep_preserves_valid_custom_scalar_setting(tmp_path, monkeypatch):
    project = make_example("E18", tmp_path)
    project.sweeps = [SweepSpec("materials.copper.specific_heat", [300., 385.], "electrothermal")]
    captured = {}

    class Dialog:
        def __init__(self, spec, title, parent, **kwargs):
            captured.update(spec=spec, **kwargs)

        def exec(self):
            return False

    monkeypatch.setattr("etlayout.gui.main_window.ModelDialog", Dialog)
    MainWindow.run_sweep_dialog(sweep_window(project))
    assert captured["spec"].parameter == "materials.copper.specific_heat"
    assert captured["spec"].parameter in captured["choices"]["parameter"]
