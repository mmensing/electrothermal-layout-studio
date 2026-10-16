# SPDX-License-Identifier: MPL-2.0
"""Exact native profiles, not build success, establish release-input provenance."""
import importlib.util
import json
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('build_windows', ROOT / 'scripts/build_windows.py')
build = importlib.util.module_from_spec(spec)
spec.loader.exec_module(build)


@pytest.fixture
def profile(tmp_path, monkeypatch):
    library = tmp_path / 'gmsh-4.15.dll'
    library.write_bytes(b'synthetic audited native library')
    python = tmp_path / 'python312.dll'
    python.write_bytes(b'synthetic audited interpreter')
    catalog = {'compiled_native': [{'id': 'gmsh', 'version': '4.15.2', 'filename': library.name,
                                   'sha256': build.sha256(library), 'source_id': 'gmsh-source',
                                   'recipe': 'packaging/source-recipes/build.ps1'}],
               'runtime_python': {'version': '3.12.14', 'python312_dll_sha256': build.sha256(python)}}
    (tmp_path / 'packaging').mkdir()
    (tmp_path / 'packaging/third-party-sources.json').write_text(json.dumps(catalog))
    descriptor = tmp_path / 'private-build.json'
    proof = {'schema_version': 1, 'runtime_python': catalog['runtime_python'],
             'gmsh': {key: value for key, value in catalog['compiled_native'][0].items() if key != 'id'},
             'private_details': 'C:/private/build-host-location'}
    descriptor.write_text(json.dumps(proof))
    monkeypatch.setattr(build, 'ROOT', tmp_path)
    return library, descriptor, python, proof


def test_stock_candidate_does_not_claim_audited_source_profile():
    assert build.native_build_profile(None, None)['audited_source_profile'] is False


@pytest.mark.parametrize('library,descriptor', [(None, 'descriptor'), ('library', None)])
def test_native_override_requires_paired_evidence(library, descriptor):
    with pytest.raises(ValueError, match='supplied together'):
        build.native_build_profile(library, descriptor)


def test_only_public_catalog_fields_enter_bundle_provenance(profile):
    library, descriptor, python, _proof = profile
    result = build.native_build_profile(library, descriptor, python_library=python)
    assert result['audited_source_profile'] is True
    assert result['build_descriptor_sha256'] == build.sha256(descriptor)
    assert 'private_details' not in result and 'private/build-host' not in json.dumps(result)


@pytest.mark.parametrize('changed', ['library', 'python', 'descriptor', 'runtime', 'schema'])
def test_changed_native_inputs_or_evidence_are_rejected(profile, changed):
    library, descriptor, python, proof = profile
    if changed in {'library', 'python'}:
        (library if changed == 'library' else python).write_bytes(b'changed input')
    else:
        if changed == 'descriptor':
            proof['gmsh']['sha256'] = 'f' * 64
        elif changed == 'runtime':
            proof['runtime_python']['version'] = 'wrong version'
        else:
            proof['schema_version'] = 2
        descriptor.write_text(json.dumps(proof))
    with pytest.raises(ValueError, match='differs from the audited'):
        build.native_build_profile(library, descriptor, python_library=python)


@pytest.mark.parametrize('name', ['src/etlayout/data/user-guide.html', 'docs/license-guide.md',
                                  'packaging/third-party-sources.json', 'examples/reference.gds'])
def test_build_snapshot_rejects_non_code_resource_drift(tmp_path, monkeypatch, name):
    monkeypatch.setattr(build, 'ROOT', tmp_path)
    path = tmp_path / name
    path.parent.mkdir(parents=True)
    path.write_bytes(b'Original audited resource.')
    before = build.source_fingerprint()
    code = build.code_fingerprint()
    build.require_source_snapshot(before)
    path.write_bytes(b'Changed after native collection.')
    assert build.code_fingerprint() == code
    with pytest.raises(RuntimeError, match='Source or resources changed'):
        build.require_source_snapshot(before)


@pytest.mark.parametrize('change', ['add', 'remove'])
def test_build_snapshot_rejects_changed_resource_set(tmp_path, monkeypatch, change):
    monkeypatch.setattr(build, 'ROOT', tmp_path)
    path = tmp_path / 'docs/guide.md'
    path.parent.mkdir()
    path.write_bytes(b'Audited guide.')
    before = build.source_fingerprint()
    if change == 'add':
        (path.parent / 'additional.md').write_bytes(b'Not in the initial snapshot.')
    else:
        path.unlink()
    with pytest.raises(RuntimeError, match='Source or resources changed'):
        build.require_source_snapshot(before)
