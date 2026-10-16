# SPDX-License-Identifier: MPL-2.0
"""Release-only streaming inspection keeps ordinary source gates unchanged."""
from __future__ import annotations

import importlib.util
import io
import json
import struct
import sys
import tarfile
import warnings
import zipfile
from dataclasses import replace
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / 'scripts/audit_publication.py'
SPEC = importlib.util.spec_from_file_location('release_artifact_audit', SCRIPT)
audit = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = audit
SPEC.loader.exec_module(audit)


def limits(**changes):
    baseline = audit.ArtifactLimits(compressed_bytes=65_536, expanded_bytes=65_536,
                                   member_bytes=16_384, members=32, depth=3,
                                   directory_bytes=8192, metadata_bytes=32,
                                   chunk_bytes=31, overlap_chars=256)
    return replace(baseline, **changes)


def inspect(path, *, kind='binary-candidate', **changes):
    result = audit.ArtifactAudit(artifact_kind=kind, limits=limits(**changes))
    result.artifact(path)
    return result


def write_zip(path, entries, *, compression=zipfile.ZIP_DEFLATED):
    with warnings.catch_warnings():
        warnings.simplefilter('ignore', UserWarning)
        with zipfile.ZipFile(path, 'w', compression=compression) as archive:
            for name, data in entries:
                archive.writestr(name, data)
    return path


def tar_bytes(entries, mode='w:gz'):
    output = io.BytesIO()
    with tarfile.open(fileobj=output, mode=mode) as archive:
        for name, data in entries:
            member = tarfile.TarInfo(name)
            member.size = len(data)
            archive.addfile(member, io.BytesIO(data))
    return output.getvalue()


def kinds(result):
    return {item['kind'] for item in result.findings}


def test_large_native_is_streamed_only_in_explicit_artifact_mode(tmp_path, monkeypatch):
    path = tmp_path / 'library.dll'
    path.write_bytes(b'MZ' + b'\0' * 8000)
    monkeypatch.setattr(audit, 'MAX_ITEM_BYTES', 64)
    ordinary = audit.PublicationAudit()
    ordinary.tree(path)
    assert kinds(ordinary) == {'item_size_budget'}
    original = Path.read_bytes

    def reject_whole_read(self):
        assert self != path, 'Large native payload must not be read wholesale'
        return original(self)

    monkeypatch.setattr(Path, 'read_bytes', reject_whole_read)
    result = inspect(path)
    assert result.report()['clean']
    assert result.counts['streamed_bytes'] == path.stat().st_size - 8
    assert result.report()['logical_expanded_bytes'] == path.stat().st_size


@pytest.mark.parametrize('encoding', ['utf8', 'utf-16-le', 'utf-16-be'])
def test_native_secrets_cross_chunk_boundaries_and_remain_redacted(tmp_path, encoding):
    secret = 'gh' + 'p_' + 'A' * 40
    path = tmp_path / 'library.dll'
    path.write_bytes(b'MZ' + b'\0' * 19 + ('prefix ' + secret + '\n').encode(encoding))
    result = inspect(path)
    assert 'github_token' in kinds(result)
    assert secret not in json.dumps(result.report())


def test_large_source_member_in_nested_archive_is_streamed(tmp_path):
    path = write_zip(tmp_path / 'sources.zip', [('source.tar.gz', tar_bytes([('upstream/code.c', b'safe line\n' * 400)]))])
    result = inspect(path, kind='third-party-source')
    assert result.report()['clean']
    assert result.counts['archives'] == 2
    assert result.counts['streamed_bytes'] >= 3900
    assert not (tmp_path / 'upstream').exists()


@pytest.mark.parametrize('entries,expected', [
    ([('../outside', b'safe')], 'unsafe_archive_path'),
    ([('directory/', b'hidden')], 'unsupported_archive_entry'),
    ([('folder/file', b'a'), ('FOLDER/file', b'b')], 'duplicate_archive_member'),
    ([('folder', b'a'), ('folder/file', b'b')], 'duplicate_archive_member'),
    ([('folder/file', b'a'), ('folder', b'b')], 'duplicate_archive_member'),
    ([('safe.dll', b'\0' * 17_000)], 'archive_size_budget'),
])
def test_zip_preflight_rejects_unsafe_ambiguous_or_oversize_members(tmp_path, entries, expected):
    result = inspect(write_zip(tmp_path / 'candidate.zip', entries))
    assert expected in kinds(result)
    assert result.counts['streamed_bytes'] == 0
    assert not (tmp_path / 'outside').exists()


@pytest.mark.parametrize('case', ['ratio', 'aggregate', 'count', 'directory'])
def test_zip_resource_budgets_fail_before_payload_reads(tmp_path, case):
    path = write_zip(tmp_path / 'candidate.zip', [('a.dll', b'\0' * 3000), ('b.dll', b'\0' * 3000)])
    changes = {'ratio': {'ratio': 5}, 'aggregate': {'expanded_bytes': 5000},
               'count': {'members': 1}, 'directory': {'directory_bytes': 20}}[case]
    result = inspect(path, **changes)
    assert result.findings and result.counts['streamed_bytes'] == 0


def test_zip_directory_is_bounded_before_zipinfo_construction(tmp_path, monkeypatch):
    path = write_zip(tmp_path / 'candidate.zip', [('safe', b'a')])
    content = bytearray(path.read_bytes())
    offset = content.rfind(b'PK\x05\x06')
    struct.pack_into('<HH', content, offset + 8, 65_535, 65_535)
    path.write_bytes(content)

    def forbidden(*_args, **_kwargs):
        raise AssertionError('ZipInfo allocation preceded directory validation')

    monkeypatch.setattr(audit.zipfile, 'ZipFile', forbidden)
    result = inspect(path)
    assert kinds(result) == {'artifact_read_error'}


@pytest.mark.parametrize('mode,suffix', [('w', '.tar'), ('w:gz', '.tar.gz'), ('w:bz2', '.tar.bz2'), ('w:xz', '.tar.xz')])
def test_tar_preflight_and_payload_inspection_do_not_extract(tmp_path, mode, suffix):
    path = tmp_path / ('source' + suffix)
    path.write_bytes(tar_bytes([('upstream/code.c', b'int value;\n' * 80)], mode))
    result = inspect(path, kind='third-party-source')
    assert result.report()['clean']
    assert result.counts['archives'] == 1
    assert not (tmp_path / 'upstream').exists()


@pytest.mark.parametrize('entries', [
    [('../outside', b'safe')], [('same', b'a'), ('same', b'b')],
    [('large', b'x' * 17_000)],
])
def test_tar_preflight_rejects_unsafe_duplicate_and_oversize_entries(tmp_path, entries):
    path = tmp_path / 'source.tar.gz'
    path.write_bytes(tar_bytes(entries))
    result = inspect(path, kind='third-party-source')
    assert result.findings and result.counts['streamed_bytes'] == 0
    assert not (tmp_path / 'outside').exists()


def test_pax_metadata_size_is_bounded_before_reading_it(tmp_path):
    output = io.BytesIO()
    with tarfile.open(fileobj=output, mode='w:gz', format=tarfile.PAX_FORMAT) as archive:
        member = tarfile.TarInfo('a' * 180)
        member.size = 4
        archive.addfile(member, io.BytesIO(b'safe'))
    path = tmp_path / 'source.tar.gz'
    path.write_bytes(output.getvalue())
    result = inspect(path, kind='third-party-source')
    assert 'artifact_read_error' in kinds(result)
    assert result.counts['streamed_bytes'] == 0


def test_nested_budget_and_native_source_rejection_are_explicit(tmp_path):
    inner = io.BytesIO()
    with zipfile.ZipFile(inner, 'w') as archive:
        archive.writestr('hidden.dat', b'MZ' + b'\0' * 100)
    path = write_zip(tmp_path / 'sources.zip', [('nested.zip', inner.getvalue())])
    result = inspect(path, kind='third-party-source')
    assert kinds(result) == {'native_payload_in_source_artifact'}
    bounded = inspect(path, depth=1)
    assert 'nesting_budget' in kinds(bounded)


def test_unterminated_long_printable_region_cannot_bypass_stream_window(tmp_path):
    path = tmp_path / 'library.dll'
    path.write_bytes(b'MZ\0' + b'x' * 500)
    result = inspect(path)
    assert kinds(result) == {'artifact_text_window_budget'}


def test_xz_dictionary_memory_and_unknown_zip_compression_fail_closed(tmp_path):
    path = tmp_path / 'source.tar.xz'
    path.write_bytes(tar_bytes([('safe', b'safe')], 'w:xz'))
    result = inspect(path, lzma_memory_bytes=1024)
    assert 'artifact_read_error' in kinds(result)
    zip_path = write_zip(tmp_path / 'candidate.zip', [('safe', b'safe')], compression=zipfile.ZIP_BZIP2)
    assert kinds(inspect(zip_path)) == {'unsupported_archive_compression'}


def test_cli_requires_explicit_artifact_mode_and_does_not_relax_source(tmp_path, monkeypatch, capsys):
    path = tmp_path / 'native.dll'
    path.write_bytes(b'MZ\0' + b'\0' * 200)
    monkeypatch.setattr(audit, 'MAX_ITEM_BYTES', 64)
    assert audit.main(['--root', str(path)]) == 1
    # The finite larger mode is independent of ordinary source exclusions.
    with pytest.raises(SystemExit):
        audit.main(['--root', str(path), '--source', '--artifact-kind', 'binary-candidate'])
    assert 'item_size_budget' in capsys.readouterr().out


def test_binary_helper_selects_large_modes_only_after_unchanged_compliance_gate(tmp_path):
    # Reuse the existing owner-gate fixture/finite API reader rather than repeat
    # its authority/CI simulations in this focused streaming suite.
    from test_owner_release import COMMIT, VERSION, FakeReadOnlyReader, local_review, release
    root, assets, manifest, review = local_review.__wrapped__(tmp_path)
    review['assets'][0]['inspection_kind'] = 'third-party-source'
    manifest.write_text(json.dumps(review))
    with pytest.raises(release.ReleaseError, match='source-only'):
        release.reviewed_inputs(root, assets, manifest, COMMIT, VERSION)
    review['kind'] = 'binary'
    review['attestations'].update(corresponding_source=True,
                                 lgpl_replacement_and_debugging=True,
                                 native_and_font_notices=True)
    manifest.write_text(json.dumps(review))
    with pytest.raises(release.ReleaseError, match='committed compliance'):
        release.reviewed_inputs(root, assets, manifest, COMMIT, VERSION,
                                committed_policy={'publication_allowed': False})
    reader = FakeReadOnlyReader()
    original = reader.__call__

    def read(args, **kwargs):
        if args[:2] == ['git', 'show']:
            return '{"publication_allowed":true}'
        return original(args, **kwargs)

    release.preflight(root, assets, manifest, read=read, environ={})
    inspections = [call for call in reader.calls if len(call) > 1 and Path(call[1]).name == 'audit_publication.py']
    assert inspections[0][-2:] == ['--artifact-kind', 'third-party-source']
    assert all('--artifact-kind' not in command for command in inspections[1:])
    policy = json.loads((SCRIPT.parents[1] / 'packaging/binary-publication-policy.json').read_text())
    assert policy['publication_allowed'] is False


def test_gzip_transport_filename_metadata_is_inspected(tmp_path):
    import gzip
    private = 'C:/Users/' + 'synthetic-owner/source.tar'
    compressed = bytearray(gzip.compress(tar_bytes([('safe', b'safe')], 'w')))
    compressed[3] |= 8  # Valid FNAME transport metadata independent of the TAR.
    path = tmp_path / 'source.tar.gz'
    path.write_bytes(compressed[:10] + private.encode() + b'\0' + compressed[10:])
    result = inspect(path, metadata_bytes=512)
    assert 'personal_path' in kinds(result)
    assert private not in json.dumps(result.report())


def test_valid_pax_long_names_and_explicit_parent_directory_are_supported(tmp_path):
    output = io.BytesIO()
    with tarfile.open(fileobj=output, mode='w:gz', format=tarfile.PAX_FORMAT) as archive:
        member = tarfile.TarInfo('upstream/' + 'a' * 140 + '.c')
        member.size = 4
        archive.addfile(member, io.BytesIO(b'safe'))
    path = tmp_path / 'source.tar.gz'
    path.write_bytes(output.getvalue())
    assert inspect(path, metadata_bytes=1024).report()['clean']
    zipped = write_zip(tmp_path / 'candidate.zip', [('parent/child', b'safe'), ('parent/', b'')])
    assert inspect(zipped).report()['clean']


@pytest.mark.parametrize('entry_type', [tarfile.SYMTYPE, tarfile.LNKTYPE, tarfile.CHRTYPE])
def test_tar_links_and_special_entries_fail_preflight(tmp_path, entry_type):
    output = io.BytesIO()
    with tarfile.open(fileobj=output, mode='w:gz') as archive:
        member = tarfile.TarInfo('linked')
        member.type = entry_type
        member.linkname = '../outside'
        archive.addfile(member)
    path = tmp_path / 'source.tar.gz'
    path.write_bytes(output.getvalue())
    result = inspect(path)
    assert 'artifact_read_error' in kinds(result)
    assert not (tmp_path / 'outside').exists()


def test_streamed_artifact_cli_reports_its_explicit_scope(tmp_path, capsys):
    path = tmp_path / 'library.dll'
    path.write_bytes(b'MZ' + b'\0' * 4096)
    assert audit.main(['--root', str(path), '--artifact-kind', 'binary-candidate']) == 0
    result = json.loads(capsys.readouterr().out)
    assert result['mode'] == 'binary-candidate' and result['clean']


@pytest.mark.parametrize('change', [{'depth': 0}, {'expanded_bytes': float('inf')}, {'chunk_bytes': 2 * 1024**2}])
def test_artifact_limits_cannot_be_unbounded_or_disable_checks(change):
    with pytest.raises(ValueError):
        limits(**change)
