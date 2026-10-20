# SPDX-License-Identifier: MPL-2.0
"""Independent local gates and command plans; no test creates a tag/release/upload."""
import importlib.util
import io
import json
import stat
import struct
import sys
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
         'parameters': {'required_status_checks': [{'context': name, 'integration_id': 15368}
                                                    for name in ('source-quality', 'scientific-regression')]}}]


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


@pytest.mark.parametrize('context', ['source-quality', 'scientific-regression'])
@pytest.mark.parametrize('change', ['missing', 'wrong-integration'])
def test_both_required_quality_checks_must_come_from_actions(context, change):
    rules = branch_rules()
    checks = rules[-1]['parameters']['required_status_checks']
    if change == 'missing':
        checks[:] = [item for item in checks if item['context'] != context]
    else:
        next(item for item in checks if item['context'] == context)['integration_id'] = 99999
    with pytest.raises(release.ReleaseError, match='actual GitHub Actions integration'):
        release.protected_rules(rules, lambda _identifier: {'enforcement': 'active', 'bypass_actors': []}, 15368)


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


@pytest.mark.parametrize('route,attestation', [
    ('GPL-3.0-only-with-MPL-3.3', 'gpl_source_access'),
    ('GPL-3.0-only-with-LGPL-3.0', 'lgpl_replacement_and_debugging'),
])
def test_binary_distribution_route_requires_its_actual_evidence(local_review, route, attestation):
    root, assets, manifest, review = local_review
    review.update(kind='binary', license_route=route)
    review['attestations'].update(corresponding_source=True, native_and_font_notices=True,
                                 runtime_prerequisites=True)
    policy = {'publication_allowed': True, 'license_route': route}
    manifest.write_text(json.dumps(review))
    with pytest.raises(release.ReleaseError, match='selected source-access'):
        release.reviewed_inputs(root, assets, manifest, COMMIT, VERSION, committed_policy=policy)
    review['attestations'][attestation] = True
    manifest.write_text(json.dumps(review))
    release.reviewed_inputs(root, assets, manifest, COMMIT, VERSION, committed_policy=policy)
    policy['license_route'] = 'unreviewed route'
    with pytest.raises(release.ReleaseError, match='committed distribution'):
        release.reviewed_inputs(root, assets, manifest, COMMIT, VERSION, committed_policy=policy)


def test_per_artifact_finding_review_must_be_hash_bound_private_evidence(local_review):
    root, assets, manifest, review = local_review
    review.update(kind='binary', license_route='GPL-3.0-only-with-MPL-3.3')
    review['attestations'].update(corresponding_source=True, native_and_font_notices=True,
                                 runtime_prerequisites=True, gpl_source_access=True)
    item = review['assets'][0]
    item['inspection_kind'] = 'binary-candidate'
    item['findings_review'] = dict(review['evidence'][0])
    manifest.write_text(json.dumps(review))
    policy = {'publication_allowed': True, 'license_route': review['license_route']}
    release.reviewed_inputs(root, assets, manifest, COMMIT, VERSION, committed_policy=policy)
    item['findings_review']['sha256'] = 'f' * 64
    manifest.write_text(json.dumps(review))
    with pytest.raises(release.ReleaseError, match='hash-bound private evidence'):
        release.reviewed_inputs(root, assets, manifest, COMMIT, VERSION, committed_policy=policy)


def test_os_credential_bridge_never_stores_or_prints_credentials(tmp_path, monkeypatch, capsys):
    from types import SimpleNamespace
    calls = []
    sentinel = 'synthetic-owner-session-not-a-real-token'

    def run(args, **kwargs):
        calls.append((args, {**kwargs, 'env': dict(kwargs['env'])}))
        if args[:3] == ['git', 'credential', 'fill']:
            return SimpleNamespace(returncode=0, stdout='username=mmensing\npassword=' + sentinel + '\n')
        if Path(args[0]).name.casefold() in {'gh', 'gh.exe'}:
            assert kwargs['env']['GH_TOKEN'] == sentinel
        else:
            assert not any(key in kwargs['env'] for key in ('GH_TOKEN', 'GITHUB_TOKEN',
                                                            'GH_ENTERPRISE_TOKEN', 'GITHUB_ENTERPRISE_TOKEN'))
        return SimpleNamespace(returncode=0, stdout='owner-response', stderr='')

    monkeypatch.setattr(release.subprocess, 'run', run)
    for key in ('GH_TOKEN', 'GITHUB_TOKEN', 'GH_ENTERPRISE_TOKEN', 'GITHUB_ENTERPRISE_TOKEN'):
        monkeypatch.delenv(key, raising=False)
    reader = release.reader_for(tmp_path, credential_source='git')
    assert reader(['gh', 'api', 'user']) == 'owner-response'
    assert reader(['gh', 'release', 'upload', 'v0.4.0', 'bounded-source.zip']) == 'owner-response'
    assert calls[-1][1]['timeout'] == 1800
    assert reader(['git', 'status', '--porcelain']) == 'owner-response'
    scanner = [sys.executable, str(tmp_path / 'scripts/audit_publication.py'), '--root', 'bounded.zip']
    assert reader(scanner) == 'owner-response'
    assert calls[0][1]['env'].get('GH_TOKEN') is None
    assert calls[-1][1]['timeout'] == 900
    assert calls[-2][1]['timeout'] == 180
    assert not list(tmp_path.iterdir())
    assert sentinel not in capsys.readouterr().out


@pytest.fixture
def windows_profile(tmp_path, monkeypatch):
    import hashlib
    source = tmp_path / 'source'
    (source / 'packaging').mkdir(parents=True)
    (source / 'scripts').mkdir()
    (source / 'docs').mkdir()
    # Execute the maintained builder's real source-selection function, with a
    # small ordinary checkout. No source-map behavior is mocked here.
    for name in ('scripts/build_windows.py', 'packaging/resources.py'):
        (source / name).write_bytes((ROOT / name).read_bytes())
    (source / 'docs/purpose.md').write_text('An independently fingerprinted user document.\n')
    gmsh = b'exact source-built test DLL'
    from test_windows_runtime import pe_fixture
    loader_bytes = pe_fixture('KERNEL32.dll')
    builder = release.runpy.run_path(str(ROOT / 'scripts/build_windows.py'))
    loaders = [{'filename': name, **builder['bootloader_properties'](loader_bytes)}
               for name in ('run.exe', 'runw.exe')]
    catalog = {'compiled_native': [{'id': 'gmsh', 'sha256': hashlib.sha256(gmsh).hexdigest()}],
               'compiled_bootloaders': loaders,
               'runtime_python': {'version': '3.12.14'}}
    catalog_path = source / 'packaging/third-party-sources.json'
    catalog_path.write_text(json.dumps(catalog))
    catalog_hash = release.sha256(catalog_path)
    profile = {'audited_source_profile': True, 'source_catalog_sha256': catalog_hash, **catalog}
    policy = {'version': VERSION, 'platform': 'windows-x64', 'source_catalog_sha256': catalog_hash,
              'native_outputs': {'_internal/gmsh-4.15.dll': hashlib.sha256(gmsh).hexdigest()}}
    path = tmp_path / 'ElectrothermalLayoutStudio-0.4.0-windows-x64.zip'
    source_hashes, code_hashes = release.source_fingerprints(source)
    manifest = {'version': VERSION, 'git_head': COMMIT, 'source_sha256': source_hashes,
                'code_sha256': code_hashes,
                'verification': {'performed': True, 'checks': [
                    {'name': name, 'exit_code': 0} for name in sorted(release.REQUIRED_NATIVE_CHECKS)]}}
    files = {'SOURCE_BUILD_PROVENANCE.json': json.dumps(profile).encode(),
             'BUILD_MANIFEST.json': json.dumps(manifest).encode(),
             'WINDOWS_RUNTIME_MODIFICATIONS.json': b'{"microsoft_runtime_files_bundled":false,"installer_bundled_or_executed":false}',
             'compliance/artifact-manifest.json': json.dumps({'kind': 'frozen',
                 'unresolved_review_items': [], 'python_source_owners': [{'root': 'etlayout'}],
                 'component_review': {'files': {'_internal/gmsh-4.15.dll': {}}}}).encode(),
             '_internal/gmsh-4.15.dll': gmsh,
             'etlayout.exe': loader_bytes, 'ElectrothermalLayoutStudio.exe': loader_bytes}
    monkeypatch.setattr(release, 'ROOT', source)
    return path, policy, files, source, profile, manifest


def write_windows_profile(fixture):
    path, _policy, files, _source, profile, manifest = fixture
    files['BUILD_MANIFEST.json'] = json.dumps(manifest).encode()
    files['SOURCE_BUILD_PROVENANCE.json'] = json.dumps(profile).encode()
    with zipfile.ZipFile(path, 'w') as archive:
        for name, data in files.items():
            archive.writestr('ElectrothermalLayoutStudio/' + name, data)
    return path


@pytest.mark.parametrize('change', [
    'none', 'stock', 'native', 'runtime_copy', 'legacy_runtime_copy', 'optional_data', 'catalog', 'old_commit',
    'bootloader_provenance', 'bootloader_code', 'bootloader_import', 'missing_bootloader',
    'missing_inventory', 'unresolved_inventory', 'unowned_python', 'unreviewed_native',
    'skip_tests', 'failed_check', 'duplicate_check', 'missing_check', 'source_drift', 'code_drift',
    'verification_type', 'boolean_exit', 'unsafe_path', 'policy_version', 'policy_platform',
])
def test_windows_release_profile_checks_actual_archive_bytes(windows_profile, change):
    path, policy, files, source, profile, manifest = windows_profile
    if change == 'stock':
        profile['audited_source_profile'] = False
    elif change == 'catalog':
        profile['source_catalog_sha256'] = 'f' * 64
    elif change == 'native':
        files['_internal/gmsh-4.15.dll'] = b'changed'
    elif change == 'runtime_copy':
        files['_internal/vcruntime140.dll'] = b'forbidden copy'
    elif change == 'legacy_runtime_copy':
        files['_internal/msvcrt.dll'] = b'Forbidden system runtime copy.'
    elif change == 'missing_inventory':
        del files['compliance/artifact-manifest.json']
    elif change in {'unresolved_inventory', 'unowned_python', 'unreviewed_native'}:
        data = json.loads(files['compliance/artifact-manifest.json'])
        if change == 'unresolved_inventory':
            data['unresolved_review_items'] = [{'component': 'unmapped_package'}]
        elif change == 'unowned_python':
            data['python_source_owners'] = []
        else:
            data['component_review'] = {'files': {}}
        files['compliance/artifact-manifest.json'] = json.dumps(data).encode()
    elif change == 'bootloader_provenance':
        profile['compiled_bootloaders'] = []
    elif change == 'bootloader_code':
        changed = bytearray(files['etlayout.exe'])
        changed[0x200] ^= 1
        files['etlayout.exe'] = bytes(changed)
    elif change == 'bootloader_import':
        from test_windows_runtime import pe_fixture
        files['ElectrothermalLayoutStudio.exe'] = pe_fixture('USER32.dll')
    elif change == 'missing_bootloader':
        del files['ElectrothermalLayoutStudio.exe']
    elif change == 'optional_data':
        files['_internal/matplotlib/mpl-data/fonts/afm/unused.afm'] = b'not used'
    elif change == 'old_commit':
        manifest['git_head'] = 'b' * 40
    elif change == 'skip_tests':
        manifest['verification']['performed'] = False
    elif change == 'failed_check':
        manifest['verification']['checks'][0]['exit_code'] = 1
    elif change == 'duplicate_check':
        manifest['verification']['checks'][-1] = manifest['verification']['checks'][0]
    elif change == 'missing_check':
        manifest['verification']['checks'].pop()
    elif change == 'source_drift':
        (source / 'docs/purpose.md').write_text('Changed after building.\n')
    elif change == 'code_drift':
        manifest['code_sha256'] = {}
    elif change == 'verification_type':
        manifest['verification'] = []
    elif change == 'boolean_exit':
        manifest['verification']['checks'][0]['exit_code'] = False
    elif change == 'unsafe_path':
        files['../outside.txt'] = b'not a contained candidate member'
    elif change == 'policy_version':
        policy['version'] = '0.4.1'
    elif change == 'policy_platform':
        policy['platform'] = 'linux-x64'
    write_windows_profile(windows_profile)
    if change == 'none':
        release.inspect_windows_profile(path, policy, VERSION, COMMIT)
    else:
        with pytest.raises(release.ReleaseError):
            release.inspect_windows_profile(path, policy, VERSION, COMMIT)


def test_windows_profile_bounds_directory_before_zipinfo_allocation(windows_profile, monkeypatch):
    path = write_windows_profile(windows_profile)
    data = bytearray(path.read_bytes())
    end = data.rfind(b'PK\x05\x06')
    struct.pack_into('<HH', data, end + 8, 65_535, 65_535)
    path.write_bytes(data)

    def forbidden(*_args, **_kwargs):
        raise AssertionError('Profile attempted ZipInfo allocation before directory preflight')

    monkeypatch.setattr(release.zipfile, 'ZipFile', forbidden)
    with pytest.raises(release.ReleaseError, match='central-directory'):
        release.inspect_windows_profile(path, windows_profile[1], VERSION, COMMIT)


def test_windows_profile_streams_native_payload_without_archive_read(windows_profile, monkeypatch):
    path = write_windows_profile(windows_profile)
    original = zipfile.ZipExtFile.read

    def bounded_read(stream, count=-1):
        assert count >= 0
        if stream.name.endswith('.dll'):
            assert count <= 1024**2
        return original(stream, count)

    def forbidden(*_args, **_kwargs):
        raise AssertionError('Whole native/provenance archive.read allocation is forbidden')

    monkeypatch.setattr(release.zipfile.ZipExtFile, 'read', bounded_read)
    monkeypatch.setattr(release.zipfile.ZipFile, 'read', forbidden)
    release.inspect_windows_profile(path, windows_profile[1], VERSION, COMMIT)


@pytest.fixture
def corresponding_sources(windows_profile):
    import hashlib
    _path, _policy, _files, root, _profile, _manifest = windows_profile
    (root / 'packaging/license-texts').mkdir(parents=True)
    for notice in ('GPL-3.0.txt', 'Pillow-embedded-font-notices.txt', 'Generated-data-notices.txt'):
        (root / 'packaging/license-texts' / notice).write_bytes(
            (ROOT / 'packaging/license-texts' / notice).read_bytes())
    assets = root.parent / 'complete-source'
    assets.mkdir()
    sources, contents, grouped = [], {}, {}
    for index in range(3):
        filename = f'component-{index}-source.tar.gz'
        name = f'ETLayout-{VERSION}-corresponding-source-group-{index % 2}.zip'
        data = f'independently hashed component {index}'.encode()
        entry = {'id': f'component-{index}', 'filename': filename, 'bytes': len(data),
                 'sha256': hashlib.sha256(data).hexdigest(), 'bundle_path': 'archives/' + filename,
                 'source_bundle': name}
        sources.append(entry)
        contents[entry['bundle_path']] = data
        grouped.setdefault(name, []).append(entry)
    catalog = {'sources': sources, 'source_bundle_parts': [
        {'filename': name, 'component_ids': [entry['id'] for entry in entries]}
        for name, entries in grouped.items()]}
    catalog_bytes = json.dumps(catalog).encode()
    (root / 'packaging/third-party-sources.json').write_bytes(catalog_bytes)
    source, code = release.source_fingerprints(root)
    manifest = {'version': VERSION, 'git_head': COMMIT, 'source_sha256': source, 'code_sha256': code}
    application = {'etlayout/' + name: (root / name).read_bytes() for name in source}
    application['etlayout/SOURCE_MANIFEST.json'] = json.dumps(manifest).encode()
    envelopes = {f'etlayout-{VERSION}-source.zip': application}
    for name, entries in grouped.items():
        index = {'schema_version': 1, 'source_bundle': name,
                 'source_catalog_sha256': hashlib.sha256(catalog_bytes).hexdigest(),
                 'sources': [{key: entry[key] for key in ('id', 'filename', 'bytes', 'sha256')}
                             for entry in sorted(entries, key=lambda entry: entry['id'])]}
        envelopes[name] = {entry['bundle_path']: contents[entry['bundle_path']] for entry in entries}
        envelopes[name].update({'README.md': b'Complete component source beside the binary.\n',
            'SOURCE_CATALOG.json': catalog_bytes, 'SOURCE_INDEX.json': json.dumps(index).encode(),
            'licenses/GPL-3.0.txt': (root / 'packaging/license-texts/GPL-3.0.txt').read_bytes(),
            'licenses/Pillow-embedded-font-notices.txt':
                (root / 'packaging/license-texts/Pillow-embedded-font-notices.txt').read_bytes(),
            'licenses/Generated-data-notices.txt':
                (root / 'packaging/license-texts/Generated-data-notices.txt').read_bytes()})
    inspections = {name: {'inspection_kind': 'source' if name.startswith('etlayout-') else 'third-party-source'}
                   for name in envelopes}
    return root, assets, envelopes, inspections


def write_corresponding_sources(fixture):
    _root, assets, envelopes, _inspections = fixture
    paths = []
    for name, entries in envelopes.items():
        path = assets / name
        with zipfile.ZipFile(path, 'w') as archive:
            for member, data in entries.items():
                archive.writestr(member, data)
        paths.append(path)
    return paths


@pytest.mark.parametrize('change', ['none', 'missing_application', 'application_kind', 'application_bytes',
    'application_commit', 'missing_part', 'part_kind', 'missing_archive', 'archive_hash', 'archive_size',
    'extra_archive', 'part_catalog', 'part_index', 'gpl_text', 'font_notice', 'missing_font_notice', 'generated_notice', 'missing_generated_notice', 'unsafe_member'])
def test_binary_corresponding_source_gate_requires_complete_exact_assets(corresponding_sources, change):
    root, _assets, envelopes, inspections = corresponding_sources
    app = f'etlayout-{VERSION}-source.zip'
    part = next(name for name in envelopes if name != app)
    inner = next(name for name in envelopes[part] if name.startswith('archives/'))
    if change == 'missing_application':
        del envelopes[app]
    elif change == 'application_kind':
        inspections[app]['inspection_kind'] = 'third-party-source'
    elif change == 'application_bytes':
        envelopes[app]['etlayout/docs/purpose.md'] = b'Wrong application source.'
    elif change == 'application_commit':
        data = json.loads(envelopes[app]['etlayout/SOURCE_MANIFEST.json'])
        data['git_head'] = 'b' * 40
        envelopes[app]['etlayout/SOURCE_MANIFEST.json'] = json.dumps(data).encode()
    elif change == 'missing_part':
        del envelopes[part]
    elif change == 'part_kind':
        inspections[part]['inspection_kind'] = 'source'
    elif change == 'missing_archive':
        del envelopes[part][inner]
    elif change == 'archive_hash':
        envelopes[part][inner] = b'x' * len(envelopes[part][inner])
    elif change == 'archive_size':
        envelopes[part][inner] += b'x'
    elif change == 'extra_archive':
        envelopes[part]['archives/uncatalogued.tar.gz'] = b'Not catalogued.'
    elif change == 'part_catalog':
        envelopes[part]['SOURCE_CATALOG.json'] = b'{}'
    elif change == 'part_index':
        envelopes[part]['SOURCE_INDEX.json'] = b'{}'
    elif change == 'gpl_text':
        envelopes[part]['licenses/GPL-3.0.txt'] = b'Not the required license text.'
    elif change == 'font_notice':
        envelopes[part]['licenses/Pillow-embedded-font-notices.txt'] = b'Incomplete permission.'
    elif change == 'missing_font_notice':
        del envelopes[part]['licenses/Pillow-embedded-font-notices.txt']
    elif change == 'generated_notice':
        envelopes[part]['licenses/Generated-data-notices.txt'] = b'Incomplete copied-data permission.'
    elif change == 'missing_generated_notice':
        del envelopes[part]['licenses/Generated-data-notices.txt']
    elif change == 'unsafe_member':
        envelopes[part]['../outside.tar.gz'] = b'Unsafe.'
    paths = write_corresponding_sources(corresponding_sources)
    if change == 'none':
        assert release.inspect_corresponding_sources(root, paths, inspections, VERSION, COMMIT) is None
    else:
        with pytest.raises(release.ReleaseError):
            release.inspect_corresponding_sources(root, paths, inspections, VERSION, COMMIT)


def test_corresponding_source_bounds_directory_before_zipinfo_allocation(corresponding_sources, monkeypatch):
    paths = write_corresponding_sources(corresponding_sources)
    path = next(path for path in paths if path.name.startswith('ETLayout-'))
    data = bytearray(path.read_bytes())
    end = data.rfind(b'PK\x05\x06')
    struct.pack_into('<HH', data, end + 8, 65_535, 65_535)
    path.write_bytes(data)

    def forbidden(*_args, **_kwargs):
        raise AssertionError('Source gate allocated ZipInfo before bounded directory preflight')

    monkeypatch.setattr(release.zipfile, 'ZipFile', forbidden)
    with pytest.raises(release.ReleaseError, match='central-directory'):
        with release._corresponding_source_zip(path):
            pass


def test_corresponding_source_streams_archives_without_whole_member_read(corresponding_sources, monkeypatch):
    paths = write_corresponding_sources(corresponding_sources)
    original = zipfile.ZipExtFile.read

    def bounded_read(stream, count=-1):
        assert count >= 0
        if stream.name.startswith('archives/'):
            assert count <= 1024**2
        return original(stream, count)

    def forbidden(*_args, **_kwargs):
        raise AssertionError('Whole archive.read allocation is forbidden')

    monkeypatch.setattr(release.zipfile.ZipExtFile, 'read', bounded_read)
    monkeypatch.setattr(release.zipfile.ZipFile, 'read', forbidden)
    release.inspect_corresponding_sources(corresponding_sources[0], paths, corresponding_sources[3], VERSION, COMMIT)


@pytest.mark.parametrize('mode', [stat.S_IFIFO, stat.S_IFCHR, stat.S_IFSOCK])
def test_corresponding_source_envelope_rejects_special_unix_file_modes(tmp_path, mode):
    path = tmp_path / 'corresponding-source.zip'
    member = zipfile.ZipInfo('archives/component-source.tar.gz')
    member.create_system = 3
    member.external_attr = (mode | 0o644) << 16
    with zipfile.ZipFile(path, 'w') as archive:
        archive.writestr(member, b'Exact bytes cannot legitimize a special file.')
    with pytest.raises(release.ReleaseError):
        with release._corresponding_source_zip(path):
            pass
