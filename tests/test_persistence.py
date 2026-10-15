# SPDX-License-Identifier: MPL-2.0
import io
import json
import zipfile
from copy import deepcopy

import numpy as np
import pytest

from etlayout.examples import make_example
from etlayout.io.project import (
    configuration_hash,
    load_project,
    physics_hash,
    project_from_dict,
    save_project,
)
from etlayout.models import Diagnostic, PlanarMesh, Project, SimulationError, SimulationResult


def test_typed_json_roundtrip():
    project = Project(name="Unicode µm")
    loaded = project_from_dict(json.loads(json.dumps(project.to_dict())))
    assert loaded == project
    with pytest.raises(SimulationError, match="schema"):
        project_from_dict({"schema_version": 999})
    with pytest.raises(SimulationError, match="Unknown"):
        project_from_dict({"unrecognized_option": True})


def test_repeated_decoding_keeps_values_isolated_and_checks_nested_types():
    from etlayout.models import PhysicalLayer, RegionSpec

    project = Project(layers=[PhysicalLayer('metal', 'Metal', 'copper', geometry=RegionSpec(sources=['1/0']))])
    data = project.to_dict()
    first, second = project_from_dict(data), project_from_dict(data)
    first.layers[0].geometry.sources.append('2/0')
    assert second.layers[0].geometry.sources == ['1/0']
    assert data['layers'][0]['geometry']['sources'] == ['1/0']
    data['layers'][0]['geometry']['sources'] = [False]
    with pytest.raises(SimulationError, match='geometry.*sources'):
        project_from_dict(data)


def test_empty_region_exclusions_preserve_legacy_identity_and_nonempty_are_physics(tmp_path):
    project = make_example('E1', tmp_path)
    original = physics_hash(project)
    legacy = project.to_dict()
    def remove_exclusions(value):
        if isinstance(value, dict):
            value.pop('exclude_sources', None)
            for child in value.values():
                remove_exclusions(child)
        elif isinstance(value, list):
            for child in value:
                remove_exclusions(child)
    remove_exclusions(legacy)
    assert physics_hash(legacy) == original
    project.layers[0].geometry.exclude_sources = ['2/0']
    assert physics_hash(project) != original
    target = tmp_path / 'exclusions.etl'
    save_project(project, target)
    reopened, _ = load_project(target)
    assert reopened.layers[0].geometry.exclude_sources == ['2/0']
    assert physics_hash(reopened) == physics_hash(project)


def test_embedded_layout_survives_source_removed(tmp_path):
    project = make_example("E1", tmp_path / "source")
    target = tmp_path / "portable.etl"
    save_project(project, target)
    from pathlib import Path
    Path(project.layout.path).unlink()
    loaded, result = load_project(target)
    assert Path(loaded.layout.path).is_file()
    assert configuration_hash(loaded) == configuration_hash(project)
    assert result is None


def test_result_arrays_edges_and_provenance(tmp_path):
    project = Project()
    mesh = PlanarMesh(np.array([[0.,0.],[1.,0.],[0.,1.]]), np.array([[0,1,2]]),
                      {"layer:a": np.array([0])}, {"ground": np.array([0,2])},
                      boundary_edges={"terminal:ground": np.array([[0,2]])})
    result = SimulationResult(mesh, fields={"a":{"potential":np.array([0.,1.,0.]),
                                                "current_density":np.array([[1.,0.]])}},
                              metrics={"power": .5}, diagnostics=[Diagnostic("warning","example")],
                              converged=True, configuration=project.to_dict(),
                              configuration_hash=configuration_hash(project))
    save_project(project, tmp_path / "result.etl", result)
    restored, loaded = load_project(tmp_path / "result.etl")
    assert restored == project and loaded.converged
    np.testing.assert_array_equal(loaded.mesh.boundary_edges["terminal:ground"], [[0,2]])
    np.testing.assert_array_equal(loaded.fields["a"]["potential"], result.fields["a"]["potential"])
    edited = deepcopy(project)
    edited.name = "changed"
    save_project(edited, tmp_path / "renamed.etl", result)
    assert load_project(tmp_path / "renamed.etl")[0].name == "changed"
    edited.solver.mode = "thermal"
    with pytest.raises(SimulationError, match="different physical setup"):
        save_project(edited, tmp_path / "stale.etl", result)


def test_corrupt_archive_and_layout_hash(tmp_path):
    make_example("E1", tmp_path)
    good = tmp_path / "E1_straight.etl"
    bad = tmp_path / "bad.etl"
    with zipfile.ZipFile(good) as source, zipfile.ZipFile(bad, "w") as dest:
        for name in source.namelist():
            dest.writestr(name, b"changed bytes" if name.startswith("layout/") else source.read(name))
    with pytest.raises(SimulationError, match="hash"):
        load_project(bad)


@pytest.mark.parametrize("data", [
    {"acknowledge_clipped_boundaries": "false"},
    {"solver": {"thermal_include_joule": 1}},
    {"solver": {"max_iterations": 2.5}},
    {"solver": {"initial_temperature": "300"}},
    {"solver": {"initial_temperature": True}},
    {"solver": {"initial_temperature": float("nan")}},
    {"solver": {"initial_temperature": None}},
    {"name": 12},
    {"layers": {}},
    {"materials": []},
    {"schema_version": True},
])
def test_json_scalar_and_container_types_are_checked(data):
    with pytest.raises(SimulationError):
        project_from_dict(data)


def test_json_accepts_integer_for_float_and_optional_null_without_hash_changes():
    data = Project().to_dict()
    data["solver"]["initial_temperature"] = 300
    data["roi"] = None
    decoded = project_from_dict(data)
    assert decoded.solver.initial_temperature == 300
    assert decoded.roi is None
    assert configuration_hash(decoded) == configuration_hash(data)


def _stored_test_result(tmp_path):
    project = Project()
    mesh = PlanarMesh(np.array([[0., 0.], [1., 0.], [0., 1.], [1., 1.]]),
                      np.array([[0, 1, 2], [1, 3, 2]]), {"layer:a": np.array([0, 1])},
                      {"ground": np.array([0, 2])}, boundary_nodes={"sink": np.array([1, 3])},
                      boundary_edges={"terminal:ground": np.array([[0, 2]])})
    result = SimulationResult(mesh, fields={"a": {"potential": np.array([0., 1., 0., 1.]),
                                                  "current_density": np.array([[1., 0.], [1., 0.]])}},
                              converged=True, configuration=project.to_dict(),
                              configuration_hash=configuration_hash(project))
    path = tmp_path / "original.etl"
    save_project(project, path, result)
    return path


def _change_array_archive(source_path, target_path, mutate_arrays=None, mutate_metadata=None):
    with zipfile.ZipFile(source_path) as source:
        members = {name: source.read(name) for name in source.namelist()}
    metadata = json.loads(members["results/metadata.json"])
    with np.load(io.BytesIO(members["results/arrays.npz"]), allow_pickle=False) as archive:
        arrays = {key: np.array(archive[key]) for key in archive.files}
    if mutate_arrays:
        mutate_arrays(arrays, metadata)
    if mutate_metadata:
        mutate_metadata(metadata)
    buffer = io.BytesIO()
    np.savez_compressed(buffer, **arrays)
    members["results/arrays.npz"] = buffer.getvalue()
    members["results/metadata.json"] = json.dumps(metadata).encode()
    with zipfile.ZipFile(target_path, "w") as target:
        for name, content in members.items():
            target.writestr(name, content)


@pytest.mark.parametrize("key,replacement,error", [
    ("triangles", np.array([[0., 1., 2.], [1., 3., 2.]]), "integer connectivity"),
    ("triangles", np.array([[0, 1, 4], [1, 3, 2]]), "out of range"),
    ("triangles", np.array([[0, 0, 2], [1, 3, 2]]), "degenerate"),
    ("points", np.array([[0., 0.], [float("nan"), 0.], [0., 1.], [1., 1.]]), "finite real"),
    ("regions_0", np.array([0, 2]), "region.*out of range"),
    ("regions_0", np.array([0, 0]), "duplicate"),
    ("regions_0", np.array([0., 1.]), "integer indices"),
    ("terminal_nodes_0", np.array([0, 7]), "out of range"),
    ("boundary_nodes_0", np.array([1.5, 3.]), "integer indices"),
    ("boundary_edges_0", np.array([[0, 3]]), "not a mesh edge"),
    ("boundary_edges_0", np.array([[0, 2, 1]]), "distinct node pairs"),
    ("field_0_0", np.array([0., 1.]), "shape contract"),
    ("field_0_1", np.array([1., 1.]), "shape contract"),
    ("field_0_0", np.array([0., float("nan"), 0., 1.]), "nonfinite"),
    ("field_0_1", np.array([[1., 0.], [float("inf"), 0.]]), "infinities"),
])
def test_loaded_array_contract_rejects_malformed_mesh_and_fields(tmp_path, key, replacement, error):
    source = _stored_test_result(tmp_path)
    target = tmp_path / "modified.etl"
    _change_array_archive(source, target, lambda arrays, _: arrays.__setitem__(key, replacement))
    with pytest.raises(SimulationError, match=error):
        load_project(target)


def test_saved_convergence_boolean_and_configuration_content_are_checked(tmp_path):
    source = _stored_test_result(tmp_path)
    target = tmp_path / "modified.etl"
    _change_array_archive(source, target, mutate_metadata=lambda meta: meta.__setitem__("converged", "false"))
    with pytest.raises(SimulationError, match="true or false"):
        load_project(target)
    _change_array_archive(source, target, mutate_metadata=lambda meta: meta["configuration"].__setitem__("name", "stale"))
    with pytest.raises(SimulationError, match="content does not match"):
        load_project(target)


@pytest.mark.parametrize("content", ["[]", '"project"', "{broken"])
def test_invalid_json_document_has_user_actionable_error(tmp_path, content):
    path = tmp_path / "invalid.json"
    path.write_text(content, encoding="utf-8")
    with pytest.raises(SimulationError):
        load_project(path)
