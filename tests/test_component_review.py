# SPDX-License-Identifier: MPL-2.0
"""Independent exact inventory/source/notice closure checks, not legal assertions."""
import hashlib
import importlib.util
import json
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('component_inventory', ROOT / 'scripts/create_sbom.py')
inventory = importlib.util.module_from_spec(spec)
spec.loader.exec_module(inventory)


@pytest.fixture
def reviewed(tmp_path, monkeypatch):
    source = tmp_path / 'source'
    (source / 'packaging').mkdir(parents=True)
    catalog = source / 'packaging/third-party-sources.json'
    catalog.write_text(json.dumps({'sources': [{'id': 'exact-upstream'}]}))
    artifact = tmp_path / 'artifact'
    (artifact / 'licenses').mkdir(parents=True)
    (artifact / 'licenses/notice.txt').write_text('Required verbatim upstream notice')
    native = [{'path': '_internal/library.dll', 'sha256': 'a' * 64}]
    fonts = [{'path': '_internal/font.ttf', 'sha256': 'b' * 64}]
    value = {'schema_version': 1, 'reviewer': 'mmensing',
             'source_catalog_sha256': hashlib.sha256(catalog.read_bytes()).hexdigest(),
             'files': [{**row, 'license_expression': 'MIT', 'source_ids': ['exact-upstream'],
                        'notice_files': ['licenses/notice.txt']} for row in [*native, *fonts]]}
    path = tmp_path / 'private-review.json'
    path.write_text(json.dumps(value))
    monkeypatch.setattr(inventory, 'ROOT', source)
    return path, native, fonts, artifact, value


def test_unreviewed_inventory_retains_pending_status():
    assert inventory.reviewed_native_fonts(None, [], [], Path('.')) is None


def test_complete_matching_component_review_retains_each_source_and_notice(reviewed):
    path, native, fonts, artifact, _value = reviewed
    result = inventory.reviewed_native_fonts(path, native, fonts, artifact)
    assert set(result['files']) == {'_internal/library.dll', '_internal/font.ttf'}
    assert result['manifest_sha256'] == hashlib.sha256(path.read_bytes()).hexdigest()
    assert result['files'][fonts[0]['path']]['source_ids'] == ['exact-upstream']


@pytest.mark.parametrize('change', ['missing', 'duplicate', 'sha', 'source', 'catalog', 'license',
                                   'notice', 'notice_escape', 'reviewer', 'schema'])
def test_stale_incomplete_or_unsupported_component_review_fails(reviewed, change):
    path, native, fonts, artifact, value = reviewed
    row = value['files'][0]
    if change == 'missing':
        value['files'].pop()
    elif change == 'duplicate':
        value['files'][1] = row
    elif change == 'sha':
        row['sha256'] = 'c' * 64
    elif change == 'source':
        row['source_ids'] = ['unreviewed']
    elif change == 'catalog':
        value['source_catalog_sha256'] = 'c' * 64
    elif change == 'license':
        row['license_expression'] = 'NOASSERTION'
    elif change == 'notice':
        row['notice_files'] = ['licenses/missing.txt']
    elif change == 'notice_escape':
        row['notice_files'] = ['../private-review.json']
    else:
        value[change if change == 'reviewer' else 'schema_version'] = 'invalid'
    path.write_text(json.dumps(value))
    with pytest.raises(ValueError):
        inventory.reviewed_native_fonts(path, native, fonts, artifact)
