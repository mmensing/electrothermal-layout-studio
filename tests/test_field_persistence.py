# SPDX-License-Identifier: MPL-2.0
"""Optional EM inputs retain legacy DC identities and portable source support."""
import hashlib
import json
from copy import deepcopy

import pytest

from etlayout.io.project import (
    configuration_hash,
    load_project,
    physics_hash,
    project_from_dict,
    save_project,
)
from etlayout.models import Project, PropertyModel
from etlayout.parasitic_examples import make_field_reference
from etlayout.power.field_snapshot import EPSILON_0, MU_0, build_field_snapshot


def test_absent_optional_field_inputs_preserve_legacy_serialization_and_hashes():
    project = Project()
    legacy = project.to_dict()
    assert "parasitics" not in legacy
    assert all("permittivity" not in material and "permeability" not in material
               for material in legacy["materials"].values())
    expected = hashlib.sha256(json.dumps(legacy, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()).hexdigest()
    assert configuration_hash(project) == expected
    explicit_defaults = deepcopy(legacy)
    explicit_defaults["parasitics"] = {}
    for material in explicit_defaults["materials"].values():
        material.update(permittivity=None, permeability=None)
    restored = project_from_dict(explicit_defaults)
    assert restored.to_dict() == legacy
    assert configuration_hash(explicit_defaults) == expected == configuration_hash(restored)
    assert physics_hash(explicit_defaults) == physics_hash(legacy)


@pytest.mark.parametrize("kind", ["capacitance", "loop_rl"])
def test_configured_field_reference_reopens_embedded_layout_with_exact_geometry_and_identity(tmp_path, kind):
    project = make_field_reference(kind, tmp_path / kind)
    original = build_field_snapshot(project, kind=kind)
    loaded, result = load_project(next((tmp_path / kind).glob("*.etl")))
    reopened = build_field_snapshot(loaded, kind=kind)
    assert result is None and loaded.layout.sha256 == project.layout.sha256
    assert configuration_hash(loaded) == configuration_hash(project)
    assert original.geometry_sha256 == reopened.geometry_sha256
    assert original.field_model_hash == reopened.field_model_hash
    assert original.source_context["physics_hash"] == reopened.source_context["physics_hash"]
    assert loaded.parasitics == project.parasitics
    without_em = deepcopy(project)
    without_em.parasitics.clear()
    for material in without_em.materials.values():
        material.permittivity = material.permeability = None
    assert physics_hash(without_em) == physics_hash(project)
    assert configuration_hash(without_em) != configuration_hash(project)


def test_configuring_em_fields_does_not_relabel_or_invalidate_a_real_retained_dc_result(tmp_path, e1_reference_solution):
    project, result = deepcopy(e1_reference_solution)
    original_physics, original_configuration = physics_hash(project), configuration_hash(project)
    original_result = (result.physics_hash, result.configuration_hash, deepcopy(result.configuration))
    material = next(iter(project.materials.values()))
    material.permittivity = PropertyModel(value=4*EPSILON_0, minimum_temperature=250., maximum_temperature=450.)
    material.permeability = PropertyModel(value=MU_0, minimum_temperature=250., maximum_temperature=450.)
    project.parasitics = {"schema_version": 1, "background_relative_permittivity": 4.,
        "background_relative_permeability": 1., "material_sources": {material.id: "Explicit synthetic test coefficients"}}
    assert physics_hash(project) == original_physics
    assert configuration_hash(project) != original_configuration
    target = tmp_path / "retained-with-optional-fields.etl"
    save_project(project, target, result)
    loaded, retained = load_project(target)
    assert physics_hash(loaded) == original_physics == retained.physics_hash
    assert (retained.physics_hash, retained.configuration_hash) == original_result[:2]
    saved_configuration = deepcopy(retained.configuration)
    # Embedded layouts reopen at a portable cache path, preserving their hash.
    saved_configuration["layout"]["path"] = original_result[2]["layout"]["path"]
    assert saved_configuration == original_result[2]
    restored = loaded.materials[material.id]
    assert restored.permittivity == material.permittivity and restored.permeability == material.permeability
    assert loaded.parasitics == project.parasitics
