# SPDX-License-Identifier: MPL-2.0
"""Catalog reuse is scoped to inspected bytes, policy and verified record identity."""
from __future__ import annotations

import hashlib
import json
import struct
import zipfile
from copy import deepcopy
from pathlib import Path

import numpy as np
import pytest
from test_content_addressed_archives import specimen as specimen

from etlayout.io import project as project_io
from etlayout.io.archive_v3 import ArchiveV3Reader
from etlayout.io.resources import ExternalResourcePolicy, ResourceAccessError, inspect_project

pytestmark = pytest.mark.fast


def counted_readers(monkeypatch):
    calls = []
    original = ArchiveV3Reader.__init__
    def counted(self, *args, **kwargs):
        calls.append(1)
        return original(self, *args, **kwargs)
    monkeypatch.setattr(ArchiveV3Reader, '__init__', counted)
    return calls


def test_eager_operations_validate_one_catalog_and_keep_fresh_mutable_results(specimen, tmp_path, monkeypatch):
    project, result = specimen
    source = tmp_path / 'runs.etlayout'
    project_io.save_project(project, source, result, history=[result], archive_format=3)
    calls = counted_readers(monkeypatch)
    project_io.load_project(source)
    assert len(calls) == 1
    project_io.load_run_history(source)
    assert len(calls) == 2
    inspection = inspect_project(source)
    assert len(calls) == 3
    _, first = project_io.resolve_project(inspection)
    _, second = project_io.resolve_project(inspection)
    assert len(calls) == 3
    assert not np.shares_memory(first.fields['a']['temperature'], second.fields['a']['temperature'])
    first.configuration['name'] = 'isolated edit'
    first.fields['a']['temperature'][0] = 900.
    assert second.configuration['name'] == project.name
    assert second.fields['a']['temperature'][0] == 300.
    # Unresolved document/Undo snapshots can retain the immutable runtime context.
    assert deepcopy(inspection.unresolved_project())._resource_unresolved


def test_reused_inspection_never_retains_external_authority(specimen, tmp_path):
    project, result = specimen
    layout = tmp_path / 'source.gds'
    layout.write_bytes((Path(__file__).parents[1] / 'examples/E1_straight.gds').read_bytes())
    project.layout.path, project.layout.sha256 = str(layout), project_io.file_hash(layout)
    result.configuration = project.to_dict()
    result.configuration_hash, result.physics_hash = project_io.configuration_hash(project), project_io.physics_hash(project)
    folder = tmp_path / 'project'
    folder.mkdir()
    source = folder / 'external.etlayout'
    project_io.save_project(project, source, result, archive_format=3, embed_layout=False)
    inspection = inspect_project(source)
    with pytest.raises(ResourceAccessError, match='outside'):
        project_io.resolve_project(inspection)
    approved = ExternalResourcePolicy(approved_resources=frozenset({'layout'}))
    assert project_io.resolve_project(inspection, external_policy=approved)[1].converged
    with pytest.raises(ResourceAccessError, match='outside'):
        project_io.resolve_project(inspection)


def test_changed_inspection_generation_and_budget_cannot_reuse_catalog(specimen, tmp_path, monkeypatch):
    project, result = specimen
    source = tmp_path / 'generation.etlayout'
    project_io.save_project(project, source, result, archive_format=3)
    inspection = inspect_project(source)
    calls = counted_readers(monkeypatch)
    inspection.archive_data = bytes(bytearray(inspection.archive_data))
    project_io.resolve_project(inspection)
    assert len(calls) == 1
    inspection = inspect_project(source)
    monkeypatch.setattr(project_io, 'MAX_ARCHIVE_BYTES', 1)
    with pytest.raises(project_io.SimulationError, match='budget'):
        project_io.resolve_project(inspection)
    assert len(calls) == 3


def test_duplicate_descriptor_checks_each_summary_once(specimen, tmp_path, monkeypatch):
    project, result = specimen
    source = tmp_path / 'shared.etlayout'
    project_io.save_project(project, source, result, history=[result], archive_format=3)
    calls = []
    original = ArchiveV3Reader._check_summary
    def counted(self, summary, *args):
        calls.append(id(summary))
        return original(self, summary, *args)
    monkeypatch.setattr(ArchiveV3Reader, '_check_summary', counted)
    with ArchiveV3Reader(source) as reader:
        assert len(calls) == 2 and len(set(calls)) == 2
        for _ in range(2):
            reader.required_arrays(reader.current_run_id)
            reader.layout_resource_key(reader.current_run_id)
            reader.materialize(reader.current_run_id)
        assert len(calls) == 2
    target = tmp_path / 'wrong-summary.etlayout'
    with zipfile.ZipFile(source) as archive, zipfile.ZipFile(target, 'w', zipfile.ZIP_DEFLATED) as output:
        for item in archive.infolist():
            raw = archive.read(item)
            if item.filename == 'runs.json':
                value = json.loads(raw)
                value['records'][1]['summary']['converged'] = False
                raw = json.dumps(value).encode()
            output.writestr(item, raw)
    with ArchiveV3Reader(target) as reader:
        assert [row['available'] for row in reader.records] == [True, False]
        assert 'summary' in reader.records[1]['error'].lower()


def replace_typed_object(source, target, digest, changed):
    """Rehash an altered object and every ancestor; integrity stays valid."""
    with zipfile.ZipFile(source) as archive:
        members = {name: archive.read(name) for name in archive.namelist()}
    root, runs = json.loads(members['project.json']), json.loads(members['runs.json'])
    objects = root['objects']
    def substitute(value, old, new):
        if isinstance(value, dict):
            return {key: new if key == '$ref' and item == old else substitute(item, old, new)
                    for key, item in value.items()}
        if isinstance(value, list):
            return [substitute(item, old, new) for item in value]
        return value
    def references(value):
        if isinstance(value, dict):
            if set(value) == {'$ref'}:
                yield value['$ref']
            else:
                for item in value.values():
                    yield from references(item)
        elif isinstance(value, list):
            for item in value:
                yield from references(item)
    pending = [(digest, changed)]
    while pending:
        old, value = pending.pop()
        data = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()
        new = hashlib.sha256(data).hexdigest()
        record = objects.pop(old)
        members.pop('objects/sha256/' + old + '.json')
        objects[new] = {**record, 'bytes': len(data), 'references': sorted(set(references(value)))}
        members['objects/sha256/' + new + '.json'] = data
        runs = substitute(runs, old, new)
        for parent, info in list(objects.items()):
            if old in info['references']:
                info['references'] = [new if ref == old else ref for ref in info['references']]
                parent_data = json.loads(members['objects/sha256/' + parent + '.json'])
                pending.append((parent, substitute(parent_data, old, new)))
    members['project.json'] = json.dumps(root).encode()
    members['runs.json'] = json.dumps(runs).encode()
    with zipfile.ZipFile(target, 'w', zipfile.ZIP_DEFLATED) as archive:
        for name, data in members.items():
            archive.writestr(name, data)


@pytest.mark.parametrize('fault', ['configuration', 'configuration_hash', 'physics_hash', 'different_physics'])
def test_shared_transient_configuration_checked_once_but_forged_frame_rejected(tmp_path, monkeypatch, fault):
    legacy = Path(__file__).parent / 'fixtures/legacy_transient_e18.etl'
    project, result = project_io.load_project(legacy)
    source = tmp_path / 'transient.etlayout'
    project_io.save_project(project, source, result, archive_format=3)
    checks = []
    original = project_io.configuration_hash
    def counted(configuration):
        checks.append(id(configuration))
        return original(configuration)
    monkeypatch.setattr(project_io, 'configuration_hash', counted)
    with ArchiveV3Reader(source) as reader:
        assert len(result.transient.snapshots) > 1 and len(checks) == 1
        descriptor = reader._descriptor(reader.current_run_id)
        reference = descriptor['transient']['frames'][1]['metadata']['$ref']
        changed = deepcopy(reader._json_objects[reference])
        if fault in {'configuration', 'different_physics'}:
            configuration = next(pair for pair in changed['value']['items'] if pair[0] == 'configuration')
            original_ref = configuration[1]['object']['$ref']
            configuration[1] = deepcopy(reader._json_objects[original_ref]['value'])
            if fault == 'configuration':
                name = next(pair for pair in configuration[1]['items'] if pair[0] == 'name')
                name[1] = 'different content claiming the original configuration hash'
            else:
                # Self-consistent alternate physics is still not a frame of
                # this run or mesh. Rehashing does not establish provenance.
                alternate = deepcopy(result.transient.snapshots[1].configuration)
                alternate['solver']['initial_temperature'] += 1.
                solver = next(pair for pair in configuration[1]['items'] if pair[0] == 'solver')[1]
                temperature = next(pair for pair in solver['items'] if pair[0] == 'initial_temperature')
                temperature[1] = {'type': 'float64', 'bits': struct.pack('>d', alternate['solver']['initial_temperature']).hex()}
                for key, expected in [('configuration_hash', original(alternate)),
                                      ('physics_hash', project_io.physics_hash(alternate, resolve_layout=False))]:
                    claim = next(pair for pair in changed['value']['items'] if pair[0] == key)
                    claim[1] = expected
        else:
            claim = next(pair for pair in changed['value']['items'] if pair[0] == fault)
            claim[1] = 'f' * 64
    target = tmp_path / 'forged-frame.etlayout'
    replace_typed_object(source, target, reference, changed)
    with ArchiveV3Reader(target) as reader:
        assert not reader.records[0]['available']
        expected = ('Transient frame configuration differs from its run setup' if fault == 'different_physics'
                    else 'physics hash does not match' if fault == 'physics_hash'
                    else 'configuration content does not match its hash')
        assert expected in reader.records[0]['error']


def test_json_reference_walk_is_once_but_logical_cost_counts_every_occurrence(specimen, tmp_path, monkeypatch):
    from collections import Counter

    from etlayout.io import archive_v3

    project, result = specimen
    array = result.fields['a']['temperature']
    result.fields['a'].update({'potential': array, 'temperature_rise': array})
    result.execution['shared_buffers'] = [array] * 8
    source = tmp_path / 'many-bindings.etlayout'
    project_io.save_project(project, source, result, history=[result], archive_format=3)
    calls = Counter()
    original = archive_v3._references
    def counted(value, *args, **kwargs):
        if isinstance(value, dict) and 'typed_codec' in value:
            calls[id(value)] += 1
        yield from original(value, *args, **kwargs)
    monkeypatch.setattr(archive_v3, '_references', counted)
    with ArchiveV3Reader(source) as reader:
        assert calls and set(calls.values()) == {1}
        observed = reader.size['logical_bytes']
        assert observed > reader.size['uncompressed_bytes']
    # Independently reconstruct the ordered reference occurrences from ZIP JSON;
    # counting the manifest's unique edges would miss repeated array bindings.
    with zipfile.ZipFile(source) as archive:
        root, runs = json.loads(archive.read('project.json')), json.loads(archive.read('runs.json'))
        objects = root['objects']
        def references(value):
            if isinstance(value, dict):
                if set(value) == {'$ref'}:
                    yield value['$ref']
                else:
                    for item in value.values():
                        yield from references(item)
            elif isinstance(value, list):
                for item in value:
                    yield from references(item)
        def cost(digest):
            item = objects[digest]
            payload = archive.read('objects/sha256/' + digest + item['suffix'])
            return len(payload) + (sum(cost(ref) for ref in references(json.loads(payload)))
                                   if item['kind'] == 'json' else 0)
        expected = len(archive.read('project.json')) + len(archive.read('runs.json'))
        expected += sum(cost(record['descriptor']['$ref']) for record in runs['records'])
        assert expected == observed
    monkeypatch.setattr(project_io, 'MAX_ARCHIVE_BYTES', expected - 1)
    with pytest.raises(project_io.SimulationError, match='Logical materialization'):
        ArchiveV3Reader(source)
