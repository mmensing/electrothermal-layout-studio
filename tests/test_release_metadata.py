# SPDX-License-Identifier: MPL-2.0
"""Release identity must agree without relabeling existing calculation evidence."""
import json
import tomllib
import zipfile
from pathlib import Path

import numpy as np
import pytest

from etlayout import __version__
from etlayout.cli.main import main
from etlayout.io.project import configuration_hash, load_project, save_project
from etlayout.models import PlanarMesh, Project, SimulationResult


def test_package_and_cli_version_agree(capsys):
    root = Path(__file__).resolve().parents[1]
    package = tomllib.loads((root / "pyproject.toml").read_text(encoding="utf-8"))
    assert package["project"]["version"] == __version__
    with pytest.raises(SystemExit) as exit_info:
        main(["--version"])
    assert exit_info.value.code == 0
    assert capsys.readouterr().out.strip() == __version__


@pytest.mark.parametrize("historical", [False, True])
def test_result_version_roundtrip_preserves_provenance(tmp_path, historical):
    project = Project()
    mesh = PlanarMesh(np.array([[0., 0.], [1., 0.], [0., 1.]]), np.array([[0, 1, 2]]), {}, {})
    result = SimulationResult(mesh, configuration=project.to_dict(),
                              configuration_hash=configuration_hash(project))
    assert result.solver_version == __version__
    if historical:
        result.solver_version = "0.1.0"
    path = tmp_path / "versioned.etl"
    save_project(project, path, result)
    with zipfile.ZipFile(path) as archive:
        assert json.loads(archive.read("project.json"))["application_version"] == __version__
    _, restored = load_project(path)
    assert restored.solver_version == result.solver_version
