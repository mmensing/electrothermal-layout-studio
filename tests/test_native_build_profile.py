# SPDX-License-Identifier: MPL-2.0
"""Exact native profiles, not build success, establish release-input provenance."""
import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from test_windows_runtime import pe_fixture

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
    bootloaders = tmp_path / 'bootloaders'
    bootloaders.mkdir()
    recipe = tmp_path / 'packaging/build-bootloader.ps1'
    recipe.write_text('# Synthetic build recipe.\n')
    catalog['sources'] = [{'id': 'pyinstaller-6.22.3', 'sha256': 'a' * 64}]
    catalog['compiled_bootloaders'] = []
    for name in ('run.exe', 'runw.exe'):
        path = bootloaders / name
        data = pe_fixture('KERNEL32.dll')
        path.write_bytes(data)
        catalog['compiled_bootloaders'].append({'id': name, 'version': build.metadata.version('pyinstaller'),
            'filename': name, 'bytes': len(data), 'sha256': build.sha256(path),
            'source_id': 'pyinstaller-6.22.3', 'source_sha256': 'a' * 64,
            'recipe': 'packaging/build-bootloader.ps1', 'recipe_sha256': build.sha256(recipe),
            **build.bootloader_properties(data)})
    (tmp_path / 'packaging/third-party-sources.json').write_text(json.dumps(catalog))
    descriptor = tmp_path / 'private-build.json'
    proof = {'schema_version': 1, 'runtime_python': catalog['runtime_python'],
             'gmsh': {key: value for key, value in catalog['compiled_native'][0].items() if key != 'id'},
             'private_details': 'C:/private/build-host-location',
             'compiled_bootloaders': catalog['compiled_bootloaders']}
    descriptor.write_text(json.dumps(proof))
    monkeypatch.setattr(build, 'ROOT', tmp_path)
    return library, descriptor, python, proof, bootloaders


def test_stock_candidate_does_not_claim_audited_source_profile():
    assert build.native_build_profile(None, None)['audited_source_profile'] is False


@pytest.mark.parametrize('library,descriptor', [(None, 'descriptor'), ('library', None)])
def test_native_override_requires_paired_evidence(library, descriptor):
    with pytest.raises(ValueError, match='supplied together'):
        build.native_build_profile(library, descriptor)


def test_only_public_catalog_fields_enter_bundle_provenance(profile):
    library, descriptor, python, _proof, bootloaders = profile
    result = build.native_build_profile(library, descriptor, python_library=python, bootloader_directory=bootloaders)
    assert result['audited_source_profile'] is True
    assert result['build_descriptor_sha256'] == build.sha256(descriptor)
    assert 'private_details' not in result and 'private/build-host' not in json.dumps(result)


@pytest.mark.parametrize('changed', ['library', 'python', 'descriptor', 'runtime', 'schema'])
def test_changed_native_inputs_or_evidence_are_rejected(profile, changed):
    library, descriptor, python, proof, bootloaders = profile
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
        build.native_build_profile(library, descriptor, python_library=python, bootloader_directory=bootloaders)


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


def test_installed_bootloader_default_is_the_exact_pinned_tool_distribution(profile, monkeypatch):
    library, descriptor, python, proof, bootloaders = profile
    monkeypatch.setattr(build.metadata, 'distribution',
                        lambda name: SimpleNamespace(version=proof['compiled_bootloaders'][0]['version'],
                                     locate_file=lambda member: bootloaders))
    result = build.native_build_profile(library, descriptor, python_library=python)
    assert result['compiled_bootloaders'] == proof['compiled_bootloaders']


@pytest.mark.parametrize('change', ['file', 'missing', 'recipe', 'source', 'descriptor', 'pair'])
def test_changed_bootloader_source_recipe_or_installed_code_is_rejected(profile, change):
    library, descriptor, python, proof, bootloaders = profile
    catalog_path = build.ROOT / 'packaging/third-party-sources.json'
    catalog = json.loads(catalog_path.read_text())
    if change == 'file':
        (bootloaders / 'run.exe').write_bytes(b'Unreviewed stock bootloader')
    elif change == 'missing':
        (bootloaders / 'runw.exe').unlink()
    elif change == 'recipe':
        (build.ROOT / 'packaging/build-bootloader.ps1').write_text('# Changed recipe')
    elif change == 'source':
        catalog['sources'][0]['sha256'] = 'b' * 64
        catalog_path.write_text(json.dumps(catalog))
    elif change == 'descriptor':
        proof['compiled_bootloaders'][0]['sha256'] = 'c' * 64
        descriptor.write_text(json.dumps(proof))
    else:
        catalog['compiled_bootloaders'] = catalog['compiled_bootloaders'][:1]
        catalog_path.write_text(json.dumps(catalog))
    with pytest.raises(ValueError, match='bootloader'):
        build.native_build_profile(library, descriptor, python_library=python, bootloader_directory=bootloaders)


def test_frozen_resources_and_overlay_do_not_masquerade_as_code_changes():
    original = pe_fixture('KERNEL32.dll')
    record = build.bootloader_properties(original)
    changed = bytearray(original)
    changed[0xD8] ^= 1  # PE checksum/header metadata is rewritten during freezing.
    assert build.verify_frozen_bootloader(bytes(changed) + b'Appended synthetic PYZ bytes', record) == record


@pytest.mark.parametrize('change', ['code', 'dll', 'symbol', 'size', 'malformed'])
def test_frozen_bootloader_rejects_unreviewed_code_imports_and_input_budgets(change):
    original = pe_fixture('KERNEL32.dll')
    record = build.bootloader_properties(original)
    changed = bytearray(original)
    if change == 'code':
        changed[0x200] ^= 1
    elif change == 'dll':
        changed = pe_fixture('USER32.dll')
    elif change == 'symbol':
        changed[0x4C2:0x4CD] = b'other_name_'
    elif change == 'size':
        record['text_section_bytes'] += 1
    else:
        changed = b'Not a PE'
    with pytest.raises(ValueError):
        build.verify_frozen_bootloader(bytes(changed), record)


def test_frozen_bootloader_budget_precedes_pe_parser(monkeypatch):
    import pefile

    monkeypatch.setattr(build, 'BOOTLOADER_MAX_BYTES', 8)
    monkeypatch.setattr(pefile, 'PE', lambda **kwargs: pytest.fail('Oversized PE reached parser'))
    with pytest.raises(ValueError, match='budget'):
        build.bootloader_properties(b'X' * 9)
