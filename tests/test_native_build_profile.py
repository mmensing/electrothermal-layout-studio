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
    runtime = tmp_path / 'shiboken6'
    runtime.mkdir()
    for name in ('shiboken6.abi3.dll', 'shiboken6.abi3.lib', 'Shiboken.pyd'):
        (runtime / name).write_bytes(('Synthetic native fixture: ' + name).encode())
    runtime_recipe = tmp_path / build.SHIBOKEN_RECIPE
    runtime_recipe.mkdir(parents=True)
    recipe_files = {}
    for name in ('CMakeLists.txt', 'build_runtime.py', 'README.md'):
        (runtime_recipe / name).write_text('Synthetic regression recipe ' + name)
        recipe_files[name] = build.sha256(runtime_recipe / name)
    patch = runtime_recipe / 'referred_objects_lifetime.patch'
    patch.write_text('# Synthetic source patch, not executable code.\n')
    upstream = {'id': 'pyside6-6.12.0', 'filename': 'pyside-source.tar.gz', 'sha256': 'b' * 64, 'bytes': 123}
    catalog['sources'] += [upstream, {'id': 'shiboken-patch-source', 'sha256': 'c' * 64}]
    receipt = {'schema_version': 1, 'distribution': 'shiboken6', 'version': build.SHIBOKEN_RUNTIME_VERSION,
               'upstream_version': '6.12.0', 'abi': 'cp310-abi3-win_amd64',
               'compiler': {'id': 'MSVC', 'version': 'synthetic fixture'},
               'source': {'name': upstream['filename'], 'sha256': upstream['sha256'], 'bytes': upstream['bytes']},
               'recipe': recipe_files, 'patch': {'name': patch.name, 'sha256': build.sha256(patch),
                   'target': 'sources/shiboken6/libshiboken/basewrapper.cpp',
                   'before_sha256': 'd' * 64, 'after_sha256': 'e' * 64},
               'dll_sha256': build.sha256(runtime / 'shiboken6.abi3.dll'),
               'import_library_sha256': build.sha256(runtime / 'shiboken6.abi3.lib'),
               'unchanged_module_sha256': build.sha256(runtime / 'Shiboken.pyd')}
    (runtime / build.SHIBOKEN_RECEIPT).write_text(json.dumps(receipt))
    runtime_row = {'id': build.SHIBOKEN_RUNTIME_ID, 'version': build.SHIBOKEN_RUNTIME_VERSION,
                   'filename': 'shiboken6.abi3.dll', 'abi': receipt['abi'],
                   'bytes': (runtime / 'shiboken6.abi3.dll').stat().st_size, 'sha256': receipt['dll_sha256'],
                   'source_id': 'shiboken-patch-source', 'upstream_source_id': upstream['id'],
                   'source_archive_sha256': upstream['sha256'],
                   'recipe': build.SHIBOKEN_RECIPE + '/build_runtime.py',
                   'recipe_sha256': recipe_files['build_runtime.py'], 'recipe_files': recipe_files,
                   'patch': build.SHIBOKEN_RECIPE + '/' + patch.name, 'patch_sha256': build.sha256(patch),
                   'source_before_sha256': receipt['patch']['before_sha256'],
                   'source_after_sha256': receipt['patch']['after_sha256'],
                   'receipt_sha256': build.sha256(runtime / build.SHIBOKEN_RECEIPT),
                   'import_library_sha256': receipt['import_library_sha256'],
                   'unchanged_module_sha256': receipt['unchanged_module_sha256']}
    catalog['compiled_native'].append(runtime_row)
    (tmp_path / 'packaging/third-party-sources.json').write_text(json.dumps(catalog))
    descriptor = tmp_path / 'private-build.json'
    proof = {'schema_version': 1, 'runtime_python': catalog['runtime_python'],
             'gmsh': {key: value for key, value in catalog['compiled_native'][0].items() if key != 'id'},
             'private_details': 'C:/private/build-host-location',
             'compiled_bootloaders': catalog['compiled_bootloaders'],
             build.SHIBOKEN_RUNTIME_ID: {key: value for key, value in runtime_row.items() if key != 'id'}}
    descriptor.write_text(json.dumps(proof))
    original_version, original_distribution = build.metadata.version, build.metadata.distribution
    monkeypatch.setattr(build.metadata, 'version', lambda name: build.SHIBOKEN_RUNTIME_VERSION
                        if name == 'shiboken6' else original_version(name))
    monkeypatch.setattr(build.metadata, 'distribution', lambda name:
                        SimpleNamespace(locate_file=lambda member: runtime)
                        if name == 'shiboken6' else original_distribution(name))
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
    original_distribution = build.metadata.distribution
    monkeypatch.setattr(build.metadata, 'distribution',
                        lambda name: SimpleNamespace(version=proof['compiled_bootloaders'][0]['version'],
                                     locate_file=lambda member: bootloaders) if name == 'pyinstaller'
                        else original_distribution(name))
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


@pytest.mark.parametrize('change', ['missing', 'descriptor', 'version', 'abi', 'source', 'patch',
                                    'recipe', 'receipt', 'dll', 'import-library', 'module', 'unknown-native',
                                    'missing-source-hash', 'equal-source-hashes'])
def test_modified_shiboken_requires_exact_installed_source_and_bytes(profile, monkeypatch, change):
    library, descriptor, python, proof, bootloaders = profile
    catalog_path = build.ROOT / 'packaging/third-party-sources.json'
    catalog = json.loads(catalog_path.read_text())
    runtime = build.ROOT / 'shiboken6'
    if change == 'missing':
        catalog['compiled_native'] = catalog['compiled_native'][:1]
    elif change == 'descriptor':
        proof[build.SHIBOKEN_RUNTIME_ID]['sha256'] = 'f' * 64
    elif change == 'version':
        original_version = build.metadata.version
        monkeypatch.setattr(build.metadata, 'version', lambda name: '6.12.0' if name == 'shiboken6'
                            else original_version(name))
    elif change == 'abi':
        catalog['compiled_native'][1]['abi'] = 'wrong-ABI'
        proof[build.SHIBOKEN_RUNTIME_ID]['abi'] = 'wrong-ABI'
    elif change == 'source':
        catalog['sources'][1]['sha256'] = 'f' * 64
    elif change == 'patch':
        (build.ROOT / build.SHIBOKEN_RECIPE / 'referred_objects_lifetime.patch').write_text('Changed patch')
    elif change == 'recipe':
        (build.ROOT / build.SHIBOKEN_RECIPE / 'CMakeLists.txt').write_text('Changed recipe')
    elif change == 'receipt':
        (runtime / build.SHIBOKEN_RECEIPT).write_text('{}')
    elif change in {'missing-source-hash', 'equal-source-hashes'}:
        row = catalog['compiled_native'][1]
        receipt_path = runtime / build.SHIBOKEN_RECEIPT
        receipt = json.loads(receipt_path.read_text())
        row['source_after_sha256'] = None if change == 'missing-source-hash' else row['source_before_sha256']
        receipt['patch']['after_sha256'] = row['source_after_sha256']
        receipt_path.write_text(json.dumps(receipt))
        row['receipt_sha256'] = build.sha256(receipt_path)
        proof[build.SHIBOKEN_RUNTIME_ID] = {key: value for key, value in row.items() if key != 'id'}
    elif change in {'dll', 'import-library', 'module'}:
        name = {'dll': 'shiboken6.abi3.dll', 'import-library': 'shiboken6.abi3.lib', 'module': 'Shiboken.pyd'}[change]
        (runtime / name).write_bytes(b'Changed native input')
    else:
        catalog['compiled_native'].append({'id': 'unreviewed-other-library'})
    catalog_path.write_text(json.dumps(catalog))
    descriptor.write_text(json.dumps(proof))
    with pytest.raises(ValueError, match='Shiboken|explicit source-build verifier'):
        build.native_build_profile(library, descriptor, python_library=python, bootloader_directory=bootloaders)


@pytest.mark.parametrize('change', ['none', 'dll', 'module', 'receipt', 'missing-receipt'])
def test_frozen_modified_shiboken_is_the_reviewed_installed_payload(profile, tmp_path, change):
    import shutil

    library, descriptor, python, _proof, bootloaders = profile
    native = build.native_build_profile(library, descriptor, python_library=python, bootloader_directory=bootloaders)
    bundle = tmp_path / 'bundle'
    destination = bundle / '_internal/shiboken6'
    destination.mkdir(parents=True)
    for name in ('shiboken6.abi3.dll', 'Shiboken.pyd', build.SHIBOKEN_RECEIPT):
        shutil.copyfile(build.ROOT / 'shiboken6' / name, destination / name)
    if change == 'missing-receipt':
        (destination / build.SHIBOKEN_RECEIPT).unlink()
    elif change != 'none':
        name = {'dll': 'shiboken6.abi3.dll', 'module': 'Shiboken.pyd', 'receipt': build.SHIBOKEN_RECEIPT}[change]
        (destination / name).write_bytes(b'Unreviewed frozen input')
    if change == 'none':
        assert build.verify_frozen_shiboken(bundle, native) is None
    else:
        with pytest.raises(ValueError, match='Shiboken'):
            build.verify_frozen_shiboken(bundle, native)


@pytest.mark.parametrize('changed', [False, True])
def test_collected_qt_identity_precedes_runtime_transformation(profile, monkeypatch, changed):
    library, descriptor, python, _, bootloaders = profile
    audited = build.native_build_profile(library, descriptor, python_library=python,
                                        bootloader_directory=bootloaders)
    bundle = library.parent / 'candidate'
    (bundle / '_internal/shiboken6').mkdir(parents=True)
    (bundle / '_internal' / library.name).write_bytes(library.read_bytes())
    (bundle / '_internal/python312.dll').write_bytes(python.read_bytes())
    runtime = library.parent / 'shiboken6'
    for name in ('shiboken6.abi3.dll', 'Shiboken.pyd'):
        (bundle / '_internal/shiboken6' / name).write_bytes((runtime / name).read_bytes())
    if changed:
        (bundle / '_internal/shiboken6/shiboken6.abi3.dll').write_bytes(b'unreviewed')
    calls = []

    def prepare(candidate):
        assert candidate == bundle
        build.verify_frozen_shiboken(candidate, audited)
        assert (candidate / '_internal/shiboken6' / build.SHIBOKEN_RECEIPT).read_bytes() == (
            runtime / build.SHIBOKEN_RECEIPT).read_bytes()
        calls.append('runtime transformation')
        return {'synthetic': 'runtime receipt'}

    monkeypatch.setattr(build.runpy, 'run_path', lambda path: {'prepare_runtime_prerequisite': prepare})
    if changed:
        with pytest.raises(ValueError, match='Frozen modified Shiboken'):
            build.prepare_collected_runtime(bundle, audited)
        assert calls == []
    else:
        assert build.prepare_collected_runtime(bundle, audited) == {'synthetic': 'runtime receipt'}
        assert calls == ['runtime transformation']
