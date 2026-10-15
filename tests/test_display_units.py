# SPDX-License-Identifier: MPL-2.0
"""Physical unit identities and observable display/SI boundaries."""
from __future__ import annotations

import csv
import json

import numpy as np
import pytest

from etlayout.gui.field_scale import FieldScaleControl
from etlayout.gui.viewer import LayoutView
from etlayout.models import PlanarMesh, SimulationResult
from etlayout.visualization.units import display_unit, to_display, to_si


@pytest.mark.parametrize("unit,value,shown_unit,shown", [
    ("A/m²", 1e10, "mA/µm²", 10.),
    ("A/m", 2000., "mA/µm", 2.),
    ("V/m", 2e5, "V/µm", .2),
    ("W/m²", 3e7, "W/mm²", 30.),
    ("K/m", 4e5, "K/µm", .4),
    ("m", 5e-6, "µm", 5.),
    ("m²", 2e-12, "µm²", 2.),
    ("fraction", .02, "%", 2.),
    ("K", 300., "K", 300.),
    ("K/W", 2., "K/W", 2.),
])
def test_physical_unit_conversion_and_signed_round_trip(unit, value, shown_unit, shown):
    raw = np.array([value, -value, 0., np.nan])
    original = raw.copy()
    assert display_unit(unit) == shown_unit
    np.testing.assert_allclose(to_display(raw, unit), [shown, -shown, 0., np.nan])
    np.testing.assert_allclose(to_si(to_display(raw, unit), unit), raw)
    np.testing.assert_array_equal(raw, original)


def test_metric_units_include_derived_changes_without_relabeling_raw_values():
    from etlayout.gui.workspace import engineering
    from etlayout.visualization.sampling import metric_label, metric_unit

    assert metric_unit("current_density_max_relative_change") == "fraction"
    assert metric_label("current_density_max_relative_change").endswith("(%)")
    assert engineering(.01, metric_unit("current_density_max_relative_change")) == "1 %"
    assert engineering(1e10, metric_unit("current_density_max")) == "10 mA/µm²"
    assert metric_label("current_density_max", display=False).endswith("(A/m²)")
    assert metric_unit("terminal_current:drain") == "A"


def test_saved_range_can_restore_before_result_details_arrive(qtbot):
    scale = FieldScaleControl()
    qtbot.addWidget(scale)
    scale.restore_state({"field": "current_density", "manual": True,
                         "minimum": "5e9", "maximum": "2e10"}, emit=False)
    assert scale.minimum.text() == "5" and scale.unit_label.text() == "mA/µm²"
    assert scale.validated_limits() == (5e9, 2e10)
    scale.set_details({"field": "current_density", "si_unit": "A/m²", "unit": "mA/µm²",
                       "display_factor": 1e-9, "low": 1e10, "high": 1.5e10})
    assert scale.validated_limits() == (5e9, 2e10)


@pytest.fixture
def density_result():
    mesh = PlanarMesh(np.array([[0., 0.], [1e-6, 0.], [0., 1e-6]]),
                      np.array([[0, 1, 2]]), {"layer:metal": np.array([0])})
    return SimulationResult(mesh=mesh, fields={"metal": {"current_density": np.array([[6e9, 8e9]])}},
                            metrics={"current_density_max": 1e10}, converged=True)


def test_map_manual_range_and_saved_legacy_state_use_correct_units(qtbot, density_result):
    view, scale = LayoutView(), FieldScaleControl()
    qtbot.addWidget(view)
    qtbot.addWidget(scale)
    view.legend_details.connect(scale.set_details)
    view.show_field(density_result, "metal", "current_density")
    assert "mA/µm²" in view.legend and "10 … 10" in view.legend
    assert view.field_limits == (1e10, 1e10)  # Map/API range remains SI.
    assert scale.minimum.text() == "10" and scale.unit_label.text() == "mA/µm²"
    scale.restore_state({"field": "current_density", "manual": True,
                         "minimum": "5e9", "maximum": "20 GA/m²"}, emit=False)
    assert scale.validated_limits() == pytest.approx((5e9, 2e10))
    assert float(scale.minimum.text()) == pytest.approx(5.)
    state = scale.capture_state()
    assert state["unit"] == "A/m²" and state["display_unit"] == "mA/µm²"
    assert float(state["minimum"]) == pytest.approx(5e9)  # Also safe for the older SI-only reader.
    scale.reset()
    scale.restore_state(state, emit=False)
    assert scale.validated_limits(logarithmic=True) == pytest.approx((5e9, 2e10))
    view.show_field(density_result, "metal", "current_density", limits=scale.validated_limits())
    assert "5 … 20" in view.legend
    np.testing.assert_array_equal(density_result.fields["metal"]["current_density"], [[6e9, 8e9]])
    scale.restore_state({"field": "current_density", "manual": True,
                         "minimum": "1 mA/µm²", "maximum": "20 GA/m²"}, emit=False)
    with pytest.raises(ValueError, match="Manual range not applied"):
        scale.validated_limits()
    scale.set_details(None)
    assert scale.unit_label.text() == "mA/µm²"


def test_plot_and_gui_exports_disclose_display_units_but_keep_numerical_si(qtbot, density_result, tmp_path):
    from etlayout.gui.export import export_view
    from etlayout.io.export import export_results

    view = LayoutView()
    qtbot.addWidget(view)
    view.show_field(density_result, "metal", "current_density")
    path = tmp_path / "map.svg"
    export_view(view, path, view.legend)
    metadata = json.loads(path.with_suffix(".svg.json").read_text(encoding="utf-8"))
    assert metadata["display_unit"] == "mA/µm²"
    assert metadata["limits_unit"] == "A/m²"
    assert metadata["limits"] == [1e10, 1e10] and metadata["display_limits"] == [10., 10.]
    export_results(density_result, tmp_path / "numeric", plots=True)
    manifest = json.loads((tmp_path / "numeric/manifest.json").read_text(encoding="utf-8"))
    layer = manifest["layers"]["metal"]
    field = layer["fields"]["current_density"]
    assert field["unit"] == "A/m²" and field["plot_unit"] == "mA/µm²"
    assert field["data_limits"] == [1e10, 1e10] and field["plot_limits"] == [10., 10.]
    with (tmp_path / "numeric" / layer["elements"]).open(encoding="utf-8-sig", newline="") as stream:
        row = next(csv.DictReader(stream))
    assert float(row["current_density_x [A/m²]"]) == 6e9
    svg = (tmp_path / "numeric" / layer["plots"]["current_density"]["svg"]).read_text(encoding="utf-8")
    assert "mA/µm²" in svg


def test_profiles_and_measurements_convert_values_without_mutating_sources(qtbot, density_result, tmp_path, monkeypatch):
    from PySide6.QtWidgets import QFileDialog

    from etlayout.gui.plots import ProfilePanel
    from etlayout.gui.power_workbench import RecordTable

    panel = ProfilePanel()
    qtbot.addWidget(panel)
    values = np.array([1e10, np.nan, 2e10])
    panel.add_profile("Cut", "metal", "current_density", np.array([0., 1e-6, 2e-6]),
                      values, np.array([[0., 0.], [1e-6, 0.], [2e-6, 0.]]))
    np.testing.assert_allclose(panel.plot.listDataItems()[0].getData()[1], [10., np.nan, 20.])
    path = tmp_path / "profile.csv"
    monkeypatch.setattr(QFileDialog, "getSaveFileName", lambda *args: (str(path), "CSV"))
    panel.export_csv()
    with path.open(encoding="utf-8", newline="") as stream:
        row = next(csv.DictReader(stream))
    assert row["unit"] == "A/m²" and float(row["value"]) == 1e10
    table = RecordTable([("value", "Value"), ("unit", "Unit")], units={"value": "record"})
    qtbot.addWidget(table)
    record = {"value": 1e10, "unit": "A/m²"}
    table.set_records([record])
    assert table.item(0, 0).text() == "10" and table.item(0, 1).text() == "mA/µm²"
    assert table.records[0] == record == {"value": 1e10, "unit": "A/m²"}
