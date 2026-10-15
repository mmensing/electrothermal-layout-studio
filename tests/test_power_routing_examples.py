# SPDX-License-Identifier: MPL-2.0
"""Executable demonstration layouts retain active masks through real routing."""
import importlib.util
from pathlib import Path

import numpy as np
import pytest

from etlayout.geometry.layout import LayoutDocument
from etlayout.geometry.prepare import prepare_geometry
from etlayout.optimization import _protected, _scenario, _service
from etlayout.routing import export_candidate, generate_candidate, validate_rules
from etlayout.routing.geometry import canonical_geometry_id
from etlayout.simulation.runner import run_project


def _hemt(directory):
    source = Path(__file__).resolve().parents[1] / "examples" / "power_routing" / "generate.py"
    spec = importlib.util.spec_from_file_location("power_routing_example", source)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.build_hemt_project(directory)


@pytest.mark.parametrize("ends,pattern,columns", [("single", "uniform", 3), ("dual", "graded", 7), ("dual", "staggered", 7)])
def test_hemt_segmented_dual_feed_strap_and_vias_preserve_active_masks(tmp_path, ends, pattern, columns):
    project = _hemt(tmp_path)
    assert not validate_rules(project)
    experiment = project.power.experiments[0]
    before = _protected(project, experiment)
    original = LayoutDocument(project.layout.path).extract(project.layout.cell)
    generated = generate_candidate(project, {"source_start_width": 12e-6, "source_end_width": 6e-6,
        "feed_ends": ends, "strap_width": 12e-6, "via_columns": columns, "via_pattern": pattern},
        tmp_path / "candidate.gds", experiment=experiment)
    assert generated.valid, generated.violations
    assert _protected(generated.project, experiment) == before
    physical = prepare_geometry(generated.project)
    assert generated.project.interfaces[-1].height == pytest.approx(1.2e-6)
    assert len(physical.interfaces["source_vias"].geoms) == columns * 3
    exported = export_candidate(generated.project, tmp_path / "candidate.oas")
    assert exported.valid, exported.violations
    restored = LayoutDocument(exported.project.layout.path).extract(exported.project.layout.cell)
    assert all(canonical_geometry_id(original[mask]) == canonical_geometry_id(restored[mask])
               for mask in project.power.rules.protected_masks)
    assert canonical_geometry_id(original["2/0"]) != canonical_geometry_id(restored["2/0"])


def test_hemt_extended_routing_delivers_real_target_current(tmp_path):
    project = _hemt(tmp_path)
    experiment = project.power.experiments[0]
    generated = generate_candidate(project, {"feed_ends": "dual", "via_columns": 5, "via_pattern": "graded"},
                                   tmp_path / "candidate.gds", experiment=experiment)
    assert generated.valid, generated.violations
    setup = _scenario(generated.project, experiment.scenarios[0])
    setup.solver.mode = "electrical"
    result = run_project(setup)
    assert result.converged
    service = _service(result, experiment.scenarios[0])
    assert service["status"] == "satisfied", service
    assert service["delivered_current"] == pytest.approx(.001, rel=1e-5)
    assert result.metrics["current_balance_error"] < setup.solver.current_balance_tolerance
    assert result.metrics["power_balance_error"] < setup.solver.power_balance_tolerance
    assert np.isfinite(result.analysis["measurements"]["source_loss"]["value"])
    contacts = [record for record in result.analysis["contacts"].values() if record["interface_id"] == "source_vias"]
    assert len(contacts) == 15

