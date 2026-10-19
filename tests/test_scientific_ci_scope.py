# SPDX-License-Identifier: MPL-2.0
"""Numerical merge gates derive from actual paths and fail closed."""
import runpy
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCOPE = runpy.run_path(str(ROOT / "scripts/ci_scientific_scope.py"))


@pytest.mark.parametrize("paths,required", [
    (["README.md", "docs/examples.md", "docs/images/result.png"], False),
    (["src/etlayout/data/user-guide.html"], False),
    (["src/etlayout/solvers/thermal.py"], True),
    (["src/etlayout/models.py"], True),
    (["src/etlayout/gui/main_window.py"], True),
    (["src/etlayout/io/project.py"], True),
    (["src/etlayout/meshing/refinement.py"], True),
    (["examples/E1_straight.etl"], True),
    (["tests/test_thermal.py"], True),
    (["docs/handbook/verify_walkthroughs.py"], True),
    (["requirements-windows-py312.lock"], True),
    (["pyproject.toml"], True),
    ([".github/workflows/ci.yml"], True),
    (["unknown-data.json"], True),
    (["../docs/escape.md"], True),
    (["docs\\escape.md"], True),
    (["docs/new.md", "src/etlayout/solvers/deleted.py"], True),
])
def test_scope_covers_physics_persistence_examples_and_unknown_changes(paths, required):
    assert SCOPE["scientific_scope"](paths)["required"] is required


def test_manual_full_validation_does_not_skip_for_docs_only():
    assert SCOPE["scientific_scope"](["README.md"], "workflow_dispatch")["required"]


def test_missing_comparison_fails_closed_and_output_is_data(tmp_path, monkeypatch):
    output = tmp_path / "scope.json"
    github_output = tmp_path / "github-output"
    monkeypatch.setenv("GITHUB_OUTPUT", str(github_output))
    assert SCOPE["main"](["--event", "pull_request", "--base", "invalid; command", "--output", str(output)]) == 0
    import json
    assert json.loads(output.read_text())["required"]
    assert github_output.read_text().strip() == "required=true"


def test_stable_required_job_is_present_and_no_doc_path_filter_can_leave_it_pending():
    workflow = (ROOT / ".github/workflows/ci.yml").read_text(encoding="utf-8")
    assert "name: scientific-regression" in workflow
    quality = workflow.split("  scientific-regression:", 1)[0]
    assert ("python -m pytest tests/test_transient_ui.py::"
            "test_time_selection_updates_map_sampler_metrics_and_exports_without_mutating_run "
            "--basetemp=.pytest_compact --junitxml=artifacts/pytest-compact.xml") in quality
    assert "paths-ignore:" not in workflow and "  paths:" not in workflow
    assert "python -m pytest -m numerical" in workflow
    assert "needs: [source-quality, scientific-regression]" in workflow
    assert "needs.scientific-regression.outputs.required == 'true'" in workflow
