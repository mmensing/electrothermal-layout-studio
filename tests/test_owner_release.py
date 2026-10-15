# SPDX-License-Identifier: MPL-2.0
"""Independent local gates and command plans; no test creates a tag/release/upload."""
import importlib.util
import io
import json
import tarfile
import zipfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('owner_release', ROOT / 'scripts/owner_release.py')
release = importlib.util.module_from_spec(spec)
spec.loader.exec_module(release)
COMMIT = 'a' * 40
VERSION = '0.4.0'


def source_zip(path, extra=None):
    with zipfile.ZipFile(path, 'w') as archive:
        archive.writestr('studio/pyproject.toml', f'[project]\nname="etlayout"\nversion="{VERSION}"\nlicense="MPL-2.0"\n')
        archive.writestr('studio/src/etlayout/__init__.py', f'__version__ = "{VERSION}"\n')
        archive.writestr('studio/LICENSE', (ROOT / 'LICENSE').read_bytes())
        for name, data in (extra or {}).items():
            archive.writestr(name, data)


@pytest.fixture
def local_review(tmp_path):
    root = tmp_path / 'checkout'
    (root / 'src/etlayout').mkdir(parents=True)
    (root / 'pyproject.toml').write_text(f'[project]\nversion="{VERSION}"\n')
    (root / 'src/etlayout/__init__.py').write_text(f'__version__ = "{VERSION}"\n')
    assets = tmp_path / 'owner-assets'
    assets.mkdir()
    source_zip(assets / 'studio-source.zip')
    notes = assets / 'release-notes.md'
    notes.write_text('Reviewed source edition. No binary assets.\n')
    checksums = {'studio-source.zip': release.sha256(assets / 'studio-source.zip')}
    (assets / 'SHA256SUMS.json').write_text(json.dumps(checksums))
    private = tmp_path / 'private-review'
    private.mkdir()
    (private / 'assessment.json').write_text('{"reviewed":true}')
    review = dict(schema_version=1, repository=release.REPOSITORY, commit=COMMIT, version=VERSION,
                  reviewer='mmensing', kind='source',
                  attestations=dict.fromkeys(('rights_and_privacy', 'license_notices', 'build_information',
                                              'owner_only_authority', 'source_contents_reviewed'), True),
                  evidence=[{'name': 'assessment.json', 'sha256': release.sha256(private / 'assessment.json')}],
                  assets=[{'name': name, 'sha256': release.sha256(assets / name)} for name in ('studio-source.zip', 'SHA256SUMS.json')],
                  release_notes={'name': notes.name, 'sha256': release.sha256(notes)})
    manifest = private / 'review.json'
    manifest.write_text(json.dumps(review))
    return root, assets, manifest, review


def successful_run():
    return dict(id=7, head_sha=COMMIT, head_branch='main', event='push', path='.github/workflows/ci.yml',
                repository={'full_name': release.REPOSITORY}, head_repository={'full_name': release.REPOSITORY},
                actor={'login': 'mmensing'}, status='completed', conclusion='success')


def passed_jobs(_identifier):
    return [{'name': name, 'status': 'completed', 'conclusion': 'success'} for name in release.REQUIRED_JOBS]


def branch_rules():
    return [{'type': name} for name in ('pull_request', 'non_fast_forward', 'deletion')] + [
        {'type': 'required_status_checks', 'ruleset_id': 12,
         'parameters': {'required_status_checks': [{'context': 'source-quality', 'integration_id': 15368}]}}]


class FakeReadOnlyReader:
    """Finite synthetic API responses test gating, not remote execution/completion."""
    def __init__(self):
        self.calls = []
        prefix = 'repos/' + release.REPOSITORY
        self.responses = {
            'user': {'login': 'mmensing'},
            'apps/github-actions': {'slug': 'github-actions', 'id': 15368},
            prefix + '/immutable-releases': {'enabled': True},
            prefix: {'full_name': release.REPOSITORY, 'private': False, 'fork': False,
                     'owner': {'login': 'mmensing'}, 'default_branch': 'main'},
            prefix + '/branches/main': {'protected': True, 'commit': {'sha': COMMIT}},
            prefix + '/rules/branches/main': [branch_rules()],
            prefix + '/rulesets/12': {'enforcement': 'active', 'bypass_actors': []},
            prefix + '/collaborators?affiliation=all&per_page=100': [[{'login': 'mmensing', 'permissions': {'admin': True}}]],
            prefix + '/keys?per_page=100': [[]],
            prefix + '/git/ref/tags/v' + VERSION: None,
            prefix + '/releases/tags/v' + VERSION: None,
            prefix + '/actions/runs?head_sha=' + COMMIT + '&per_page=100': [{'workflow_runs': [successful_run()]}],
            prefix + '/actions/runs/7/jobs?per_page=100': [{'jobs': passed_jobs(7)}],
        }

    def __call__(self, args, **_kwargs):
        self.calls.append(args)
        if args[0] == 'git':
            return {('status', '--porcelain=v1', '--untracked-files=all'): '',
                    ('branch', '--show-current'): 'main',
                    ('remote', 'get-url', '--all', 'origin'): 'https://github.com/' + release.REPOSITORY + '.git',
                    ('rev-parse', 'HEAD'): COMMIT,
                    ('show', COMMIT + ':packaging/binary-publication-policy.json'): '{"publication_allowed":false}'}[tuple(args[1:])]
        if args[0] == 'gh':
            assert args[1:4] == ['api', '--hostname', 'github.com']
            value = self.responses[args[4]]
            return None if value is None else json.dumps(value)
        assert Path(args[1]).name == 'audit_publication.py'
        return '{"clean":true}'


def test_default_preflight_is_read_only_and_source_independent_of_binary_gate(local_review):
    root, assets, manifest, _review = local_review
    read = FakeReadOnlyReader()
    plan = release.preflight(root, assets, manifest, read=read, environ={})
    assert (plan['commit'], plan['tag'], plan['trusted_run']) == (COMMIT, 'v0.4.0', 7)
    assert not any(call[:2] == ['gh', 'release'] for call in read.calls)
    commands = release.draft_commands(plan)
    assert commands[0][:3] == ['gh', 'release', 'create'] and '--draft' in commands[0]
    assert commands[0][commands[0].index('--target') + 1] == COMMIT
    assert all('--clobber' not in command and '--publish' not in command for command in commands)
    assert commands[1][:3] == ['gh', 'release', 'upload']


@pytest.mark.parametrize('environment', [{'GITHUB_ACTIONS': 'true'}, {'CI': 'true'}, {'GH_TOKEN': 'sentinel'}])
def test_ci_or_environment_publishing_token_is_rejected_before_commands(local_review, environment):
    read = FakeReadOnlyReader()
    with pytest.raises(release.ReleaseError):
        release.preflight(*local_review[:3], read=read, environ=environment)
    assert read.calls == []


@pytest.mark.parametrize('version', ['0.4.0.dev0', '0.4.0+local', 'v0.4.0', '00.4.0'])
def test_ambiguous_or_development_versions_cannot_be_released(local_review, version):
    root = local_review[0]
    (root / 'pyproject.toml').write_text(f'[project]\nversion="{version}"\n')
    with pytest.raises(release.ReleaseError, match='stable public version'):
        release.stable_version(root)


@pytest.mark.parametrize('change', [
    {'event': 'pull_request'}, {'head_repository': {'full_name': 'contributor/fork'}},
    {'actor': {'login': 'integration'}}, {'conclusion': 'failure'}, {'head_sha': 'b' * 40},
])
def test_untrusted_or_failed_validation_cannot_authorize_release(change):
    run = successful_run()
    run.update(change)
    with pytest.raises(release.ReleaseError):
        release.trusted_ci([run], passed_jobs, COMMIT)


def test_latest_failed_run_cannot_fall_back_to_old_success():
    old, new = successful_run(), successful_run()
    new.update(id=8, conclusion='failure')
    with pytest.raises(release.ReleaseError, match='latest trusted'):
        release.trusted_ci([old, new], passed_jobs, COMMIT)
    with pytest.raises(release.ReleaseError, match='windows-desktop'):
        release.trusted_ci([old], lambda _identifier: passed_jobs(7)[:-1], COMMIT)


def test_owner_bypass_cannot_bypass_quality():
    with pytest.raises(release.ReleaseError, match='without.*bypass'):
        release.protected_rules(branch_rules(), lambda _identifier: {'enforcement': 'active', 'bypass_actors': [{'actor_type': 'RepositoryRole'}]}, 15368)


@pytest.mark.parametrize('name,data', [('layout/payload.dll', b'data'), ('layout/not-a-layout.gds', b'MZ' + b'0' * 100),
                                      ('../outside.txt', b'data')])
def test_source_archive_rejects_native_or_unsafe_members(tmp_path, name, data):
    path = tmp_path / 'source.zip'
    source_zip(path, {name: data})
    with pytest.raises(release.ReleaseError):
        release.inspect_source_asset(path, VERSION)


def test_source_archive_accepts_nested_project_data_without_executing_it(tmp_path):
    project = io.BytesIO()
    with zipfile.ZipFile(project, 'w') as archive:
        archive.writestr('project.json', '{"schema_version":2}')
        archive.writestr('layout/source.gds', b'\x00\x06\x00\x02\x02X')
    path = tmp_path / 'source.zip'
    source_zip(path, {'examples/reference.etl': project.getvalue()})
    assert release.inspect_source_asset(path, VERSION)


def test_source_tar_link_is_rejected_without_extraction(tmp_path):
    path = tmp_path / 'source.tar.gz'
    with tarfile.open(path, 'w:gz') as archive:
        member = tarfile.TarInfo('unsafe-link')
        member.type = tarfile.SYMTYPE
        member.linkname = '../outside'
        archive.addfile(member)
    with pytest.raises(release.ReleaseError, match='links/special'):
        release.inspect_source_asset(path, VERSION)
    assert not (tmp_path / 'unsafe-link').exists()


def test_reviewed_hash_and_committed_binary_policy_are_real_gates(local_review):
    root, assets, manifest, review = local_review
    review['kind'] = 'binary'
    manifest.write_text(json.dumps(review))
    with pytest.raises(release.ReleaseError, match='committed compliance'):
        release.reviewed_inputs(root, assets, manifest, COMMIT, VERSION, committed_policy={'publication_allowed': False})
    review['kind'] = 'source'
    manifest.write_text(json.dumps(review))
    (assets / 'release-notes.md').write_text('Changed after approval')
    with pytest.raises(release.ReleaseError, match='checksum changed'):
        release.reviewed_inputs(root, assets, manifest, COMMIT, VERSION)


def test_existing_tag_or_wrong_account_stops_preparation(local_review):
    root, assets, manifest, _review = local_review
    for endpoint, value in [('user', {'login': 'another-account'}),
                            ('repos/' + release.REPOSITORY + '/git/ref/tags/v' + VERSION, {'object': {'sha': COMMIT}})]:
        read = FakeReadOnlyReader()
        read.responses[endpoint] = value
        with pytest.raises(release.ReleaseError):
            release.preflight(root, assets, manifest, read=read, environ={})


def test_missing_immutability_or_other_check_integration_is_rejected(local_review):
    read = FakeReadOnlyReader()
    read.responses['repos/' + release.REPOSITORY + '/immutable-releases'] = None
    with pytest.raises(release.ReleaseError, match='immutable releases'):
        release.preflight(*local_review[:3], read=read, environ={})
    with pytest.raises(release.ReleaseError, match='actual GitHub Actions integration'):
        release.protected_rules(branch_rules(), lambda _identifier: {'enforcement': 'active', 'bypass_actors': []}, 99999)


@pytest.mark.parametrize('suffix,mode', [('.tar.bz2', 'w:bz2'), ('.tar.xz', 'w:xz')])
def test_compressed_tar_cannot_hide_native_payload(tmp_path, suffix, mode):
    path = tmp_path / ('source' + suffix)
    with tarfile.open(path, mode) as archive:
        member = tarfile.TarInfo('hidden/library.dll')
        member.size = 4
        archive.addfile(member, io.BytesIO(b'data'))
    with pytest.raises(release.ReleaseError, match='native executables'):
        release.inspect_source_asset(path, VERSION)


def test_unsupported_compression_is_rejected_instead_of_assumed_source(tmp_path):
    path = tmp_path / 'opaque-layout.gds'
    path.write_bytes(bytes.fromhex('1f8b') + b'opaque-compressed-content')
    with pytest.raises(release.ReleaseError, match='Unsupported compressed'):
        release.inspect_source_asset(path, VERSION)
