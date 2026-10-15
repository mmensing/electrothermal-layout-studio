# SPDX-License-Identifier: MPL-2.0
"""Bounded publication checks preserve science and required upstream notices."""
from __future__ import annotations

import importlib.util
import io
import json
import struct
import sys
import tarfile
import zipfile
from pathlib import Path

import pytest

_SCRIPT = Path(__file__).resolve().parents[1] / 'scripts' / 'audit_publication.py'
_SPEC = importlib.util.spec_from_file_location('publication_audit', _SCRIPT)
audit_module = importlib.util.module_from_spec(_SPEC)
sys.modules[_SPEC.name] = audit_module
_SPEC.loader.exec_module(audit_module)


def local_path():
    return 'C:' + '/' + 'Users' + '/' + 'private-fixture' + '/source.gds'


def zip_bytes(entries):
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, 'w', compression=zipfile.ZIP_DEFLATED) as archive:
        for name, content in entries.items():
            archive.writestr(name, content)
    return stream.getvalue()


@pytest.mark.parametrize('payload,kind', [
    ('gh' + 'p_' + 'A' * 40, 'github_token'),
    ('github_' + 'pat_' + 'B' * 60, 'github_token'),
    ('AK' + 'IA' + 'Z' * 16, 'cloud_access_key'),
    ('-----BEGIN ' + 'RSA PRIVATE KEY-----', 'private_key'),
    ('https' + '://' + 'user:' + 'synthetic-password' + '@' + 'example.invalid', 'authenticated_url'),
    (local_path(), 'personal_path'),
    ('/' + 'home' + '/private-fixture/source.gds', 'personal_path'),
    ('10' + '.1.2.3', 'private_address'),
    ('192' + '.168.1.2', 'private_address'),
    ('172' + '.31.1.2', 'private_address'),
    ('unnecessary' + '@' + 'mail.example-owner.org', 'email'),
    ('api_' + 'key = "' + 'synthetic-key-value' + '"', 'secret_assignment'),
])
def test_findings_are_specific_and_never_echo_sensitive_values(payload, kind):
    audit = audit_module.PublicationAudit()
    audit.bytes('payload.txt', payload.encode())
    assert kind in {item['kind'] for item in audit.findings}
    assert payload not in json.dumps(audit.report())


def test_upstream_attribution_and_scientific_values_are_retained():
    address = 'maintainer' + '@' + 'upstream.example-owner.org'
    audit = audit_module.PublicationAudit()
    audit.bytes('packaging/license-texts/upstream.txt', ('Copyright owner ' + address).encode())
    audit.bytes('physics.txt', b'prompt = input(); j = 1e10; hash = ' + b'a' * 64)
    audit.bytes('README.md', b'GitHub owner: contributor@users.noreply.github.com; example@example.org')
    assert not audit.findings


def test_individual_required_public_email_can_be_reviewed_without_waiving_secrets():
    address = 'contributor' + '@' + 'upstream.example-owner.org'
    audit = audit_module.PublicationAudit(allowed_emails=[address])
    audit.bytes('source.py', address.encode())
    audit.bytes('source.py', ('gh' + 'p_' + 'A' * 40).encode())
    assert {finding['kind'] for finding in audit.findings} == {'github_token'}


def test_decoded_nested_project_metadata_and_npz_strings_are_inspected():
    project = zip_bytes({'project.json': json.dumps({'layout': {'path': local_path()}}),
                         'results/arrays.npz': zip_bytes({'description.npy': local_path()})})
    audit = audit_module.PublicationAudit()
    audit.bytes('portable.etl', project)
    assert audit.counts['archives'] == 2
    assert any(finding['kind'] == 'personal_path' and '!json-string' in finding['path'] for finding in audit.findings)
    assert any('description.npy' in finding['path'] for finding in audit.findings)


@pytest.mark.parametrize('case', ['traversal', 'depth', 'size', 'ratio', 'corrupt'])
def test_unsafe_or_uninspected_archives_fail_closed(case, monkeypatch, tmp_path):
    audit = audit_module.PublicationAudit()
    if case == 'traversal':
        content = zip_bytes({'../escape.txt': 'safe'})
        expected = 'unsafe_archive_path'
    elif case == 'depth':
        content = b'plain'
        for _ in range(audit_module.MAX_DEPTH + 1):
            content = zip_bytes({'nested.zip': content})
        expected = 'nesting_budget'
    elif case == 'size':
        monkeypatch.setattr(audit_module, 'MAX_ARCHIVE_BYTES', 10)
        content = zip_bytes({'safe.txt': 'safe' * 10})
        expected = 'archive_size_budget'
    elif case == 'ratio':
        stream = io.BytesIO()
        with zipfile.ZipFile(stream, 'w', compression=zipfile.ZIP_BZIP2) as archive:
            archive.writestr('safe.bin', bytes(1_000_000))
        content = stream.getvalue()
        expected = 'archive_ratio_budget'
    else:
        content = b'not an archive'
        expected = 'archive_read_error'
    audit.bytes('input.etl', content)
    assert expected in {item['kind'] for item in audit.findings}
    assert not (tmp_path / 'escape.txt').exists()


@pytest.mark.parametrize('unsafe', ['link', 'owner', 'path', 'clean'])
def test_tar_sdist_inspection_does_not_extract_or_ignore_owner_metadata(unsafe):
    stream = io.BytesIO()
    with tarfile.open(fileobj=stream, mode='w:gz') as archive:
        item = tarfile.TarInfo('../payload.txt' if unsafe == 'path' else 'package/payload.txt')
        item.size = 4
        if unsafe == 'link':
            item.type = tarfile.SYMTYPE
            item.linkname = '../outside'
        if unsafe == 'owner':
            item.uname = 'private-fixture'
        archive.addfile(item, io.BytesIO(b'safe'))
    audit = audit_module.PublicationAudit()
    audit.bytes('source.tar.gz', stream.getvalue())
    expected = {'link': 'unsupported_archive_entry', 'owner': 'archive_owner_metadata', 'path': 'unsafe_archive_path'}
    if unsafe == 'clean':
        assert not audit.findings
    else:
        assert expected[unsafe] in {item['kind'] for item in audit.findings}


@pytest.mark.parametrize('suffix', ['.gds', '.oas'])
def test_actual_layout_text_and_properties_are_inspected(suffix, tmp_path):
    import klayout.db as kdb
    layout = kdb.Layout()
    cell = layout.create_cell('Synthetic')
    cell.shapes(layout.layer(1, 0)).insert(kdb.Text(local_path(), kdb.Trans()))
    cell.set_property('source', local_path())
    path = tmp_path / ('input' + suffix)
    layout.write(str(path))
    audit = audit_module.PublicationAudit()
    audit.bytes('input' + suffix, path.read_bytes())
    assert audit.counts['layouts'] == 1
    assert 'personal_path' in {item['kind'] for item in audit.findings}
    assert local_path() not in json.dumps(audit.report())


def test_truncated_gds_rejects_uninspected_payload():
    audit = audit_module.PublicationAudit()
    audit.bytes('input.gds', struct.pack('>HBB', 20, 0x19, 6) + b'short')
    assert audit.findings[0]['kind'] == 'gds_invalid_record'


def test_image_and_embedded_svg_metadata_are_inspected_without_rendering_code():
    from PIL import Image, PngImagePlugin
    image = Image.new('RGB', (2, 2))
    metadata = PngImagePlugin.PngInfo()
    metadata.add_text('Source', local_path())
    stream = io.BytesIO()
    image.save(stream, format='PNG', pnginfo=metadata)
    audit = audit_module.PublicationAudit()
    audit.bytes('image.png', stream.getvalue())
    assert {finding['kind'] for finding in audit.findings} == {'personal_path'}
    import base64
    encoded = '\n' + base64.b64encode(stream.getvalue()).decode() + '\n'
    svg = '<svg xmlns="http://www.w3.org/2000/svg"><image href="data:image/png;base64,' + encoded + '"/></svg>'
    embedded = audit_module.PublicationAudit()
    embedded.bytes('image.svg', svg.encode())
    assert embedded.counts['images'] == 1
    assert any('!embedded-image' in finding['path'] for finding in embedded.findings)
    assert {finding['kind'] for finding in embedded.findings} == {'personal_path'}


def test_confidential_policy_literals_and_filenames_are_redacted():
    literal = 'restricted-' + 'dataset'
    audit = audit_module.PublicationAudit(forbidden_text=[literal])
    audit.bytes(literal + '.txt', literal.encode())
    assert audit.findings
    assert literal not in json.dumps(audit.report())


def test_complete_tree_and_source_mode_have_explicit_exclusion_semantics(tmp_path, capsys):
    (tmp_path / 'README.md').write_text('safe', encoding='utf8')
    cache = tmp_path / 'build'
    cache.mkdir()
    (cache / 'private.txt').write_text(local_path(), encoding='utf8')
    output = tmp_path.parent / (tmp_path.name + '-report.json')
    assert audit_module.main(['--root', str(tmp_path), '--output', str(output), '--source']) == 0
    source = json.loads(output.read_text())
    assert source['mode'] == 'source' and source['counts']['generated_files_excluded'] == 1
    assert audit_module.main(['--root', str(tmp_path), '--output', str(output)]) == 1
    complete = json.loads(output.read_text())
    assert complete['mode'] == 'complete'
    assert complete['findings'][0]['kind'] == 'personal_path'
    assert str(tmp_path) not in capsys.readouterr().out


def test_normal_image_dpi_metadata_does_not_make_verification_fail():
    from PIL import Image
    stream = io.BytesIO()
    Image.new('RGB', (2, 2)).save(stream, format='PNG', dpi=(96., 96.))
    audit = audit_module.PublicationAudit()
    audit.bytes('screenshot.png', stream.getvalue())
    assert not audit.findings


def test_zip_links_are_rejected_without_following_their_target():
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, 'w') as archive:
        item = zipfile.ZipInfo('linked-layout')
        item.create_system = 3
        item.external_attr = 0o120777 << 16
        archive.writestr(item, '../outside.gds')
    audit = audit_module.PublicationAudit()
    audit.bytes('source.zip', stream.getvalue())
    assert {finding['kind'] for finding in audit.findings} == {'unsupported_archive_entry'}


def test_license_check_source_filename_does_not_exempt_unnecessary_personal_email():
    audit = audit_module.PublicationAudit()
    audit.bytes('tests/test_license_workflow.py', ('individual' + '@' + 'mail.example-owner.org').encode())
    assert {finding['kind'] for finding in audit.findings} == {'email'}


@pytest.mark.parametrize('service_host', ['github.com', 'gitlab.com'])
def test_git_ssh_transport_does_not_exempt_other_email_addresses(service_host):
    audit = audit_module.PublicationAudit()
    audit.bytes('release.py', ('git@' + service_host + ':owner/project.git').encode())
    assert not audit.findings
    address = 'individual' + '@' + service_host
    audit.bytes('notes.md', (address + ': contact').encode())
    assert {finding['kind'] for finding in audit.findings} == {'email'}
