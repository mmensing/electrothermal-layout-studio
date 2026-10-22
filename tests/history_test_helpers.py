# SPDX-License-Identifier: MPL-2.0
"""Small real archives for asynchronous ownership tests, without a solver run."""
from pathlib import Path

import numpy as np

from etlayout.io import project as project_io
from etlayout.models import PlanarMesh, Project, SimulationResult


def archive(tmp_path, name, *, external=False, version=3):
    folder = tmp_path / name
    folder.mkdir()
    project = Project(name=name)
    if external:
        layout = tmp_path / "external.gds"
        layout.write_bytes((Path(__file__).parents[1] / "examples" / "E1_straight.gds").read_bytes())
        project.layout.path, project.layout.sha256 = str(layout), project_io.file_hash(layout)
    mesh = PlanarMesh(np.array([[0., 0.], [1., 0.], [0., 1.]]), np.array([[0, 1, 2]]), {"layer:a": np.array([0])})
    result = SimulationResult(mesh, fields={"a": {"temperature": np.array([300., 301., 302.])}},
        configuration=project.to_dict(), configuration_hash=project_io.configuration_hash(project),
        physics_hash=project_io.physics_hash(project), converged=True)
    target = folder / "project.etl"
    project_io.save_project(project, target, result, archive_format=version, embed_layout=not external)
    return target
