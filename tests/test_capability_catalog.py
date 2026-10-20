# SPDX-License-Identifier: MPL-2.0
"""Stable source capabilities and generated documentation stay consistent."""
import importlib.util
import json
import subprocess
import sys
from pathlib import Path

import pytest

from etlayout.capabilities import ANALYSES, CAPABILITIES, capability, capability_manifest

pytestmark = pytest.mark.fast
ROOT = Path(__file__).resolve().parents[1]


def test_registry_has_stable_unique_ids_honest_limits_and_real_references():
    assert len({row.id for row in CAPABILITIES}) == len(CAPABILITIES) >= 20
    for row in CAPABILITIES:
        assert row.gui_route and row.limitations and row.title and row.group
        assert row.analyses and set(row.analyses) <= set(ANALYSES)
        assert row.available_in(row.introduced_in)
        for reference in row.test_references:
            assert (ROOT / reference).is_file(), (row.id, reference)
    with pytest.raises(KeyError):
        capability("unknown.feature")


def test_availability_compares_explicit_versions_without_claiming_a_binary_exists():
    manifest = capability_manifest(installed_version="0.4.0")
    records = {row["id"]: row for row in manifest["entries"]}
    assert manifest["compared_version"] == "0.4.0" and json.dumps(manifest)
    assert records["analysis.dc"]["available_in_version"]
    assert not records["examples.browser"]["available_in_version"]
    assert not records["workflow.verification"]["available_in_version"]
    assert capability("examples.browser").available_in("0.4.1")
    assert capability("examples.browser").available_in("0.5.0")
    assert "available_in_version" not in capability_manifest()["entries"][0]
    with pytest.raises(ValueError, match="semantic version"):
        capability_manifest(installed_version="latest published")


def test_capability_registry_imports_without_qt_or_numerical_solver():
    code = "import sys; import etlayout.capabilities; assert not any(k.startswith(('PySide6', 'scipy', 'etlayout.simulation')) for k in sys.modules)"
    subprocess.run([sys.executable, "-c", code], check=True, cwd=ROOT)


def test_online_offline_generated_blocks_are_current_and_preserve_prose():
    spec = importlib.util.spec_from_file_location("capability_docs", ROOT / "scripts/generate_capability_docs.py")
    generator = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(generator)
    assert not generator.generate(check=True)
    original = "maintained introduction\n" + generator.START + "\nstale\n" + generator.END + "\nmaintained tail\n"
    updated = generator.replace_block(original, generator.markdown_table(), anchor="unused")
    assert updated.startswith("maintained introduction\n") and updated.endswith("\nmaintained tail\n")
    for row in CAPABILITIES:
        assert row.id in generator.markdown_table() and row.id in generator.offline_table()
    with pytest.raises(ValueError, match="marker pair"):
        generator.replace_block(generator.START, "", anchor="unused")


def test_cli_catalog_is_additive_and_preserves_cpu_capabilities(capsys):
    from etlayout.cli.main import main
    from etlayout.simulation.execution import detect_capabilities
    assert main(["capabilities"]) == 0
    result = json.loads(capsys.readouterr().out)
    backend = detect_capabilities()
    assert {key: result[key] for key in backend} == backend
    assert result["feature_catalog"] == json.loads(json.dumps(capability_manifest()))
    assert set(backend) == {"cpu_count", "threadpoolctl_available"}
