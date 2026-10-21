# SPDX-License-Identifier: MPL-2.0
"""Release-only streaming inspection keeps ordinary source gates unchanged."""
from __future__ import annotations

import bz2
import importlib.util
import io
import json
import lzma
import re
import struct
import subprocess
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
    review['license_route'] = 'GPL-3.0-only-with-MPL-3.3'
    review['attestations'].update(corresponding_source=True, gpl_source_access=True,
                                  native_and_font_notices=True, runtime_prerequisites=True)
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
            return '{"publication_allowed":true,"license_route":"GPL-3.0-only-with-MPL-3.3"}'
        return original(args, **kwargs)

    release.preflight(root, assets, manifest, read=read, environ={})
    inspections = [call for call in reader.calls if len(call) > 1 and Path(call[1]).name == 'audit_publication.py']
    assert inspections[0][-2:] == ['--artifact-kind', 'third-party-source']
    assert all('--artifact-kind' not in command for command in inspections[1:])
    policy = json.loads((SCRIPT.parents[1] / 'packaging/binary-publication-policy.json').read_text())
    assert policy['publication_allowed'] is True
    assert (policy['version'], policy['platform'], policy['license_route']) == (
        release.stable_version(SCRIPT.parents[1]), 'windows-x64', 'GPL-3.0-only-with-MPL-3.3')
    assert policy['source_catalog_sha256'] == release.sha256(SCRIPT.parents[1] / 'packaging/third-party-sources.json')
    expected_paths = {
        '_internal/gmsh-4.15.dll', '_internal/python312.dll',
        '_internal/numpy/_core/_multiarray_umath.cp312-win_amd64.pyd',
        '_internal/numpy/fft/_pocketfft_umath.cp312-win_amd64.pyd',
        '_internal/Shapely.libs/geos-bf067cd6ff74ee0ad5f3ff52c7ef08c9.dll',
        '_internal/Shapely.libs/geos_c-6dd9fd915eef8a7928285416bef1e666.dll',
        '_internal/shiboken6/shiboken6.abi3.dll', '_internal/shiboken6/Shiboken.pyd',
        '_internal/shiboken6/ETLAYOUT_RUNTIME_BUILD.json',
    }
    assert set(policy['native_outputs']) == expected_paths
    catalog = json.loads((SCRIPT.parents[1] / 'packaging/third-party-sources.json').read_bytes())
    runtime = next(row for row in catalog['compiled_native'] if row['id'] == 'shiboken6-runtime')
    for name, key in [('shiboken6.abi3.dll', 'sha256'), ('Shiboken.pyd', 'unchanged_module_sha256'),
                      ('ETLAYOUT_RUNTIME_BUILD.json', 'receipt_sha256')]:
        assert policy['native_outputs']['_internal/shiboken6/' + name] == runtime[key]
    assert all(re.fullmatch('[0-9a-f]{64}', value) for value in policy['native_outputs'].values())


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


@pytest.mark.parametrize('payload', [b'ordinary raw data\n', b'\0' * 500])
def test_raw_gzip_is_inspected_as_data_with_crc_validation(tmp_path, payload):
    import gzip
    path = tmp_path / 'pixels.raw.gz'
    path.write_bytes(gzip.compress(payload))
    result = inspect(path, member_bytes=2048, ratio=1000)
    assert result.report()['clean']
    assert result.counts['archives'] == 1 and result.counts['items'] == 2
    assert not (tmp_path / 'pixels.raw').exists()
    content = bytearray(path.read_bytes())
    content[-8] ^= 1
    path.write_bytes(content)
    assert 'artifact_read_error' in kinds(inspect(path, member_bytes=2048, ratio=1000))


def test_raw_gzip_expansion_and_hidden_credentials_are_not_waived(tmp_path):
    import gzip
    path = tmp_path / 'data.gz'
    path.write_bytes(gzip.compress(b'x' * 20_000))
    assert 'archive_size_budget' in kinds(inspect(path, member_bytes=4096, ratio=10000))
    path.write_bytes(gzip.compress(b'gh' + b'p_' + b'Z' * 40))
    assert 'github_token' in kinds(inspect(path))


@pytest.mark.parametrize('target', ['data', './data'])
def test_tar_hardlink_aliases_require_fully_inspected_regular_targets(tmp_path, target):
    output = io.BytesIO()
    with tarfile.open(fileobj=output, mode='w:gz') as archive:
        alias = tarfile.TarInfo('alias')
        alias.type = tarfile.LNKTYPE
        alias.linkname = target
        archive.addfile(alias)
        payload = b'gh' + b'p_' + b'Z' * 40
        data = tarfile.TarInfo('data')
        data.size = len(payload)
        archive.addfile(data, io.BytesIO(payload))
    path = tmp_path / 'timezones.tar.gz'
    path.write_bytes(output.getvalue())
    result = inspect(path)
    assert result.counts['hardlink_aliases'] == 1 and 'github_token' in kinds(result)
    assert not (tmp_path / 'alias').exists()


@pytest.mark.parametrize('case', ['missing', 'cycle', 'parent', 'directory'])
def test_tar_hardlinks_cannot_hide_outside_missing_or_cyclic_data(tmp_path, case):
    output = io.BytesIO()
    with tarfile.open(fileobj=output, mode='w:gz') as archive:
        member = tarfile.TarInfo('alias')
        member.type = tarfile.LNKTYPE
        member.linkname = {'missing': 'absent', 'cycle': 'alias', 'parent': '../outside', 'directory': 'dir'}[case]
        archive.addfile(member)
        directory = tarfile.TarInfo('dir')
        directory.type = tarfile.DIRTYPE
        archive.addfile(directory)
    path = tmp_path / 'bad.tar.gz'
    path.write_bytes(output.getvalue())
    result = inspect(path)
    assert 'artifact_read_error' in kinds(result) and result.counts['streamed_bytes'] == 0
    assert not (tmp_path / 'outside').exists()


@pytest.mark.parametrize('tar_payload', [False, True])
def test_concatenated_gzip_cannot_hide_uninspected_transport_metadata(tmp_path, tar_payload):
    import gzip
    first = gzip.compress(tar_bytes([('safe', b'safe')], 'w') if tar_payload else b'safe\n')
    second = bytearray(gzip.compress(b'\0' * 512 if tar_payload else b'also safe\n'))
    second[3] |= 8
    token = b'gh' + b'p_' + b'Z' * 40
    path = tmp_path / ('source.tar.gz' if tar_payload else 'data.raw.gz')
    path.write_bytes(first + second[:10] + token + b'\0' + second[10:])
    result = inspect(path, member_bytes=32768, expanded_bytes=65536, ratio=10000)
    assert 'unsupported_gzip_concatenation_or_trailing_data' in kinds(result)
    assert not result.report()['clean']
    assert token.decode() not in json.dumps(result.report())


@pytest.mark.parametrize('change', ['truncated', 'trailing_nonzero', 'header_crc'])
def test_gzip_invalid_transport_fails_closed(tmp_path, change):
    import gzip
    data = gzip.compress(b'safe\n')
    if change == 'truncated':
        data = data[:-1]
    elif change == 'trailing_nonzero':
        data += b'uninspected tail'
    else:
        data = bytearray(data)
        data[3] |= 2  # Deliberately wrong FHCRC; zlib validates it.
        data = data[:10] + b'\0\0' + data[10:]
    path = tmp_path / 'data.raw.gz'
    path.write_bytes(data)
    result = inspect(path)
    assert not result.report()['clean']
    assert kinds(result) & {'artifact_read_error', 'unsupported_gzip_concatenation_or_trailing_data'}


def test_single_gzip_accepts_zero_transport_padding(tmp_path):
    import gzip
    path = tmp_path / 'data.raw.gz'
    path.write_bytes(gzip.compress(b'safe\n') + b'\0' * 100)
    assert inspect(path).report()['clean']


@pytest.mark.parametrize('metadata', ['overridden_linkname', 'overridden_gnu_longname', 'uname', 'gname', 'raw_padding'])
def test_all_raw_tar_metadata_is_scanned_before_overrides(tmp_path, metadata):
    import gzip
    token = b'gh' + b'p_' + b'Z' * 40
    short_key = b'AK' + b'IA' + b'Z' * 16
    output = io.BytesIO()
    with tarfile.open(fileobj=output, mode='w', format=tarfile.PAX_FORMAT) as archive:
        if metadata == 'overridden_linkname':
            alias = tarfile.TarInfo('alias')
            alias.type = tarfile.LNKTYPE
            alias.linkname = token.decode()
            alias.pax_headers = {'linkpath': 'data'}
            archive.addfile(alias)
        elif metadata == 'overridden_gnu_longname':
            longname = tarfile.TarInfo('././@LongLink')
            longname.type = tarfile.GNUTYPE_LONGNAME
            longname.size = len(token) + 1
            archive.addfile(longname, io.BytesIO(token + b'\0'))
        data = tarfile.TarInfo('data')
        data.size = 4
        if metadata == 'overridden_gnu_longname':
            data.pax_headers = {'path': 'data'}
        if metadata in {'uname', 'gname'}:
            setattr(data, metadata, short_key.decode())
        archive.addfile(data, io.BytesIO(b'safe'))
    raw = output.getvalue()
    if metadata == 'raw_padding':
        header = bytearray(raw[:512])
        header[5:5 + len(token)] = token  # After the name's first NUL.
        header[148:156] = b' ' * 8
        header[148:156] = ('%06o\0 ' % sum(header)).encode('ascii')
        raw = bytes(header) + raw[512:]
    path = tmp_path / 'data.tar.gz'
    path.write_bytes(gzip.compress(raw))
    result = inspect(path, member_bytes=32768, expanded_bytes=65536, metadata_bytes=512, ratio=10000)
    assert ('cloud_access_key' if metadata in {'uname', 'gname'} else 'github_token') in kinds(result)
    assert not result.report()['clean']
    assert token.decode() not in json.dumps(result.report())
    assert short_key.decode() not in json.dumps(result.report())


def test_reviewed_artifact_size_is_bounded_before_any_content_read(tmp_path, monkeypatch):
    path = tmp_path / 'large.dll'
    path.write_bytes(b'MZ' + b'x' * 100)
    small_limits = limits(compressed_bytes=32)
    monkeypatch.setattr(audit, 'ArtifactLimits', lambda: small_limits)
    original_open = Path.open

    def guarded_open(current, *args, **kwargs):
        if current == path:
            pytest.fail('Oversized reviewed artifact must not be opened for hashing')
        return original_open(current, *args, **kwargs)

    monkeypatch.setattr(Path, 'open', guarded_open)
    with pytest.raises(SystemExit) as stopped:
        audit.main(['--root', str(path), '--artifact-kind', 'binary-candidate',
                    '--reviewed-findings', str(tmp_path / 'review.json')])
    assert stopped.value.code == 2


def test_reviewed_artifact_growing_during_prehash_is_rejected(tmp_path, monkeypatch):
    path = tmp_path / 'changing.dll'
    path.write_bytes(b'MZ00')
    original_open = Path.open

    def growing_open(current, *args, **kwargs):
        return io.BytesIO(b'MZ000') if current == path else original_open(current, *args, **kwargs)

    monkeypatch.setattr(Path, 'open', growing_open)
    with pytest.raises(SystemExit) as stopped:
        audit.main(['--root', str(path), '--artifact-kind', 'binary-candidate',
                    '--reviewed-findings', str(tmp_path / 'review.json')])
    assert stopped.value.code == 2


@pytest.mark.parametrize('secret', [False, True])
def test_conventional_lgpl_copying_lib_is_retained_and_fully_scanned(tmp_path, secret):
    license_text = (SCRIPT.parents[1] / 'packaging/license-texts/LGPL-2.1.txt').read_bytes()
    token = 'gh' + 'p_' + 'A' * 40
    path = tmp_path / 'COPYING.LIB'
    path.write_bytes(license_text + (b'\n' + token.encode() if secret else b''))
    result = inspect(path, kind='third-party-source', member_bytes=128 * 1024,
                     expanded_bytes=128 * 1024, compressed_bytes=128 * 1024)
    assert 'native_payload_in_source_artifact' not in kinds(result)
    assert result.report()['clean'] is not secret
    assert ('github_token' in kinds(result)) is secret
    assert token not in json.dumps(result.report())


@pytest.mark.parametrize('name,data', [
    ('COPYING.LIB', b'MZ' + b'X' * 40),
    ('COPYING.LIB', b'\x7fELF' + b'X' * 40),
    ('COPYING.LIB', b'!<arch>\n' + b'X' * 40),
    ('COPYING.LIB', b'not a text license\x00binary bytes'),
    ('licenses/runtime.lib', b'ordinary suffix remains native'),
    ('COPYING.dll', b'other native suffix remains native'),
])
def test_license_filenames_never_exempt_native_or_binary_payload(tmp_path, name, data):
    path = tmp_path / 'sources.zip'
    write_zip(path, [(name, data)])
    result = inspect(path, kind='third-party-source')
    assert kinds(result) & {'native_payload_in_source_artifact', 'binary_payload_in_license_notice'}


@pytest.mark.parametrize('payload', ['ordinary', 'token', 'authenticated_url'])
def test_long_source_line_is_scanned_without_a_window_waiver(tmp_path, payload):
    token = 'gh' + 'p_' + 'A' * 40
    value = 'symbols = ' + 'A' * 70_000
    if payload == 'token':
        value += ' ' + token
    elif payload == 'authenticated_url':
        value = 'url = "https://public-user:' + 'A' * 70_000 + '@example.test/path"'
    path = tmp_path / 'generated.py'
    path.write_text(value + '\n', encoding='utf-8')
    result = inspect(path, kind='third-party-source', member_bytes=128 * 1024,
                     expanded_bytes=128 * 1024, compressed_bytes=128 * 1024)
    assert 'artifact_text_window_budget' not in kinds(result)
    assert result.report()['clean'] is (payload == 'ordinary')
    assert result.counts['bounded_text_bytes'] == path.stat().st_size
    if payload != 'ordinary':
        assert ('github_token' if payload == 'token' else 'authenticated_url') in kinds(result)
        assert value not in json.dumps(result.report())


def test_ordinary_source_text_ceiling_is_not_relaxed(tmp_path, monkeypatch):
    assert audit.ArtifactLimits().overlap_chars == 65_536
    path = tmp_path / 'generated.py'
    path.write_bytes(b'A' * 65)
    monkeypatch.setattr(audit, 'MAX_ITEM_BYTES', 64)
    result = inspect(path, kind='third-party-source')
    assert kinds(result) == {'text_item_budget'}
    assert result.counts['streamed_bytes'] == 0


@pytest.mark.parametrize('kind,custom,ceiling', [
    ('binary-candidate', False, 2000),
    ('third-party-source', False, 20_000),
    ('third-party-source', True, 2),
])
def test_finding_budgets_remain_finite_and_scope_specific(kind, custom, ceiling):
    result = audit.ArtifactAudit(artifact_kind=kind,
                                 limits=limits(findings=ceiling) if custom else None)
    assert result.limits.findings == ceiling
    for index in range(ceiling):
        result.finding('upstream/source.c', 'email', f'upstream-{index}@example.test')
    assert len(result.findings) == ceiling
    assert all(row['kind'] == 'email' for row in result.findings)
    with pytest.raises(audit._ArtifactStop, match='finding budget'):
        result.finding('upstream/source.c', 'email', 'one-extra@example.test')
    assert len(result.findings) == ceiling + 1
    assert result.findings[-1]['kind'] == 'finding_budget'
    assert not result.report()['clean']
    assert audit.MAX_ITEM_BYTES == 32 * 1024**2
    assert audit.MAX_ARCHIVE_BYTES == 256 * 1024**2


@pytest.mark.parametrize('suffix', ['.ttf', '.otf'])
@pytest.mark.parametrize('encoding', ['utf-16-le', 'utf-16-be'])
def test_font_strings_are_scanned_in_both_unicode_orders_across_chunks(tmp_path, suffix, encoding):
    token = 'gh' + 'p_' + 'A' * 40
    path = tmp_path / ('synthetic-font' + suffix)
    # The SFNT prefix is data, not executable magic. No font parser or font code
    # is executed; this tests raw metadata discovery independently of fontTools.
    path.write_bytes(b'\x00\x01\x00\x00' + b'\0' * 17 + ('prefix ' + token + '\n').encode(encoding))
    result = inspect(path, kind='third-party-source')
    assert 'native_payload_in_source_artifact' not in kinds(result)
    assert 'github_token' in kinds(result)
    assert all('!native-string' not in finding['path'] for finding in result.findings)
    assert any('!font-string' in finding['path'] for finding in result.findings)
    assert token not in json.dumps(result.report())


@pytest.mark.parametrize('value', [
    'prefix.user+tag@example.test other@example.test',
    'abc%def---hello@domain.example (outside@another.example)',
    'a@b.example@d.example', 'bad-prefix@a..b.example suffix',
    'not-an-address just-token AAAAAA', 'user@notld user@localhost',
    '.a@domain.example trailing', 'before\nnext@example.test\nafter',
    'copyright contact@example.test\n' + 'A' * 150,
])
def test_email_boundary_optimization_preserves_maximal_matches(value):
    original = re.compile(r'[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}')
    expected = [(item.span(), item.group()) for item in original.finditer(value)]
    assert [(item.span(), item.group()) for item in audit._PATTERNS['email'].finditer(value)] == expected


def test_long_source_token_after_real_email_finishes_in_bounded_process(tmp_path):
    source = tmp_path / 'long-runtime-source.xml'
    source.write_text('Copyright public-contact@upstream.test\n' + 'A' * 2_000_000 + '\n', encoding='utf8')
    program = (
        "import runpy,sys;from pathlib import Path;"
        "m=runpy.run_path(sys.argv[1]);a=m['ArtifactAudit'](artifact_kind='third-party-source');"
        "a.artifact(Path(sys.argv[2]));r=a.report();"
        "assert not any(f['kind'] in {'artifact_text_window_budget','artifact_read_error'} for f in r['findings']);"
        "assert r['counts']['bounded_text_bytes']==Path(sys.argv[2]).stat().st_size;"
        "print('completed')"
    )
    completed = subprocess.run([sys.executable, '-c', program, str(SCRIPT), str(source)],
                               check=True, capture_output=True, text=True, timeout=10)
    assert completed.stdout.strip() == 'completed'


def test_incremental_finding_line_numbers_match_direct_counts():
    value = ('one[at]fixture.example.edu\nignored@example.test\npassword = "long-enough-value"'
             '\nnext[at]fixture.example.edu').replace('[at]', '@')
    result = audit.PublicationAudit()
    result.text('source.txt', value, line_offset=19)
    assert len(result.findings) == 3
    for finding in result.findings:
        pattern = audit._PATTERNS[finding['kind']]
        expected = {audit._fingerprint(match.group()): 20 + value.count('\n', 0, match.start())
                    for match in pattern.finditer(value)}
        assert finding['line'] == expected[finding['fingerprint']]


@pytest.mark.parametrize('payload', ['ordinary', 'token', 'encoded_token', 'entity', 'wrong_root', 'native'])
def test_exact_qmake_xml_plist_lib_is_validated_and_scanned(tmp_path, payload):
    token = 'gh' + 'p_' + 'A' * 40
    value = token if payload == 'token' else ''.join(f'&#{ord(char)};' for char in token) if payload == 'encoded_token' else '$(PRODUCT_NAME)'
    data = ('<?xml version="1.0"?><plist version="1.0"><dict><key>Name</key><string>'
            + value + '</string></dict></plist>').encode()
    if payload == 'entity':
        data = b'<?xml version="1.0"?><!DOCTYPE plist [<!ENTITY hidden "data">]><plist>&hidden;</plist>'
    elif payload == 'wrong_root':
        data = b'<?xml version="1.0"?><other>Not a plist</other>'
    elif payload == 'native':
        data = b'MZ' + b'X' * 40
    path = tmp_path / 'Info.plist.lib'
    path.write_bytes(data)
    result = inspect(path, kind='third-party-source')
    assert result.report()['clean'] is (payload == 'ordinary')
    if payload in {'token', 'encoded_token'}:
        assert 'github_token' in kinds(result)
    elif payload == 'native':
        assert 'native_payload_in_source_artifact' in kinds(result)
    elif payload == 'entity':
        assert 'plist_entity_declaration' in kinds(result)
    elif payload == 'wrong_root':
        assert 'invalid_plist_template' in kinds(result)
    assert token not in json.dumps(result.report())


@pytest.mark.parametrize('case', ['valid', 'secret', 'truncated', 'crc', 'trailing', 'concatenated', 'ratio'])
def test_standalone_bzip2_build_fixture_is_decoded_and_bounded(tmp_path, case):
    token = 'gh' + 'p_' + 'A' * 40
    plain = (token + '\n').encode() if case == 'secret' else b'Required make-test input\n' * 20
    if case == 'ratio':
        plain = b'A' * 8000
    data = bz2.compress(plain)
    if case == 'truncated':
        data = data[:-5]
    elif case == 'crc':
        data = data[:-5] + bytes([data[-5] ^ 1]) + data[-4:]
    elif case == 'trailing':
        data += b'hidden uninspected nonzero tail'
    elif case == 'concatenated':
        data += bz2.compress(token.encode())
    path = tmp_path / 'sample1.bz2'
    path.write_bytes(data)
    result = inspect(path, kind='third-party-source', **({'ratio': 2} if case == 'ratio' else {}))
    assert result.report()['clean'] is (case == 'valid')
    if case == 'secret':
        assert 'github_token' in kinds(result)
    elif case in {'trailing', 'concatenated'}:
        assert 'unsupported_bzip2_concatenation_or_trailing_data' in kinds(result)
    elif case in {'truncated', 'crc'}:
        assert 'artifact_read_error' in kinds(result)
    elif case == 'ratio':
        assert 'archive_size_budget' in kinds(result)
    assert token not in json.dumps(result.report())


def test_bzip2_tar_rejects_hidden_compressed_tail(tmp_path):
    path = tmp_path / 'source.tar.bz2'
    path.write_bytes(tar_bytes([('source.c', b'int retained;')], 'w:bz2') + b'hidden tail')
    result = inspect(path, kind='third-party-source')
    assert 'unsupported_bzip2_concatenation_or_trailing_data' in kinds(result)


@pytest.mark.parametrize('case', ['valid', 'empty', 'secret', 'truncated', 'crc', 'trailing',
                                  'concatenated', 'ratio', 'dictionary'])
def test_standalone_xz_build_fixture_is_decoded_and_bounded(tmp_path, case):
    token = 'gh' + 'p_' + 'A' * 40
    plain = (token + '\n').encode() if case == 'secret' else b'Required XZ build-test input\n' * 20
    if case == 'empty':
        plain = b''
    elif case == 'ratio':
        plain = b'A' * 8000
    data = lzma.compress(plain, format=lzma.FORMAT_XZ, check=lzma.CHECK_CRC64)
    if case == 'truncated':
        data = data[:-5]
    elif case == 'crc':
        data = data[:-20] + bytes([data[-20] ^ 1]) + data[-19:]
    elif case == 'trailing':
        data += b'hidden uninspected nonzero tail'
    elif case == 'concatenated':
        data += lzma.compress(token.encode(), format=lzma.FORMAT_XZ)
    path = tmp_path / 'good-data.xz'
    path.write_bytes(data)
    settings = {'ratio': 2} if case == 'ratio' else {'lzma_memory_bytes': 1024} if case == 'dictionary' else {}
    result = inspect(path, kind='third-party-source', **settings)
    assert result.report()['clean'] is (case in {'valid', 'empty'})
    if case == 'secret':
        assert 'github_token' in kinds(result)
        assert any(row['path'] == 'good-data' for row in result.findings)
    elif case in {'trailing', 'concatenated'}:
        assert 'unsupported_xz_concatenation_or_trailing_data' in kinds(result)
    elif case in {'truncated', 'crc', 'dictionary'}:
        assert 'artifact_read_error' in kinds(result)
    elif case == 'ratio':
        assert 'archive_size_budget' in kinds(result)
    assert token not in json.dumps(result.report())


@pytest.mark.parametrize('suffix', ['.tar.xz', '.txz'])
def test_xz_tar_keeps_strict_member_and_compressed_tail_checks(tmp_path, suffix):
    path = tmp_path / ('source' + suffix)
    path.write_bytes(tar_bytes([('source.c', b'int retained;')], 'w:xz') + b'hidden tail')
    result = inspect(path, kind='third-party-source')
    assert 'artifact_read_error' in kinds(result)
    assert result.counts['items'] == 1  # Never reinterpret a named TAR as standalone text.
