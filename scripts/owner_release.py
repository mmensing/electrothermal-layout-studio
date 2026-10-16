# SPDX-License-Identifier: MPL-2.0
"""Owner-local release preflight; optional draft creation, never final publication."""
from __future__ import annotations

import argparse
import hashlib
import io
import json
import os
import re
import runpy
import stat
import subprocess
import sys
import tarfile
import tomllib
import zipfile
from contextlib import contextmanager
from pathlib import Path, PurePosixPath

REPOSITORY = 'mmensing/electrothermal-layout-studio'
OWNER = 'mmensing'
ROOT = Path(__file__).resolve().parents[1]
REQUIRED_JOBS = {'source-quality', 'windows-tests', 'windows-desktop'}
REQUIRED_NATIVE_CHECKS = {
    'version', 'inspect-gds', 'inspect-oas', 'solve-E1', 'gui-smoke',
    'windowed-smoke', 'worker-smoke', 'solve-E19', 'resume-E19', 'replay-E19',
    'transient-worker-smoke', 'frozen-power', 'frozen-multiport', 'frozen-routing',
    'frozen-optimization', 'frozen-optimization-resume', 'frozen-generate-E21',
    'frozen-solve-E21', 'frozen-reference-E21', 'frozen-generate-E23',
    'frozen-solve-E23', 'frozen-reference-E23', 'frozen-E21-parallel',
    'frozen-editor', 'frozen-editor-solve',
}
WINDOWS_MAX_BYTES = 512 * 1024**2
WINDOWS_MAX_ENTRIES = 20_000
WINDOWS_MAX_DIRECTORY = 8 * 1024**2
WINDOWS_MAX_EXPANDED = 2 * 1024**3
WINDOWS_MAX_MEMBER = 256 * 1024**2
MAX_ASSET = 256 * 1024 * 1024
MAX_EXPANDED = 512 * 1024 * 1024
MAX_MEMBER = 64 * 1024 * 1024
MAX_MEMBERS = 12000
NATIVE_SUFFIXES = {'.exe', '.dll', '.pyd', '.so', '.dylib', '.msi', '.msix', '.appx'}


class ReleaseError(ValueError):
    """A failed publication gate; diagnostics never include captured credentials."""


def require(condition, message):
    if not condition:
        raise ReleaseError(message)


def sha256(path):
    digest = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            digest.update(block)
    return digest.hexdigest()


def stable_version(root):
    version = tomllib.loads((root / 'pyproject.toml').read_text(encoding='utf-8'))['project']['version']
    require(re.fullmatch(r'(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)', version),
            'Choose an unambiguous stable public version first; development/pre-release builds cannot be released here')
    source = (root / 'src/etlayout/__init__.py').read_text(encoding='utf-8')
    match = re.search(r'__version__\s*=\s*[\'"]([^\'"]+)[\'"]', source)
    require(match is not None and match.group(1) == version, 'Package and application versions disagree')
    return version


def reader_for(root, *, credential_source='gh'):
    # Keep an optional owner credential in this closure, and pass it only to gh.
    # In particular Git and the artifact scanner never inherit the GH token.
    environment = os.environ.copy()
    token_keys = ('GH_TOKEN', 'GITHUB_TOKEN', 'GH_ENTERPRISE_TOKEN', 'GITHUB_ENTERPRISE_TOKEN')
    token = None
    if credential_source == 'git':
        require(not any(environment.get(key) for key in token_keys),
                'Environment publishing tokens are not accepted')
        environment.update(GIT_TERMINAL_PROMPT='0', GCM_INTERACTIVE='never', GH_PROMPT_DISABLED='1')
        credential = subprocess.run(['git', 'credential', 'fill'], cwd=root,
                                    input='protocol=https\nhost=github.com\nusername=mmensing\n\n',
                                    text=True, capture_output=True, timeout=30, env=environment)
        fields = dict(line.split('=', 1) for line in credential.stdout.splitlines() if '=' in line)
        require(credential.returncode == 0 and fields.get('password')
                and fields.get('username') == OWNER,
                'The existing owner OS credential-manager session is unavailable')
        token = fields['password']
    else:
        require(credential_source == 'gh', 'Unknown owner credential source')
    for key in token_keys:
        environment.pop(key, None)

    def read(args, *, optional404=False):
        child_environment = environment.copy()
        if Path(args[0]).name.casefold() in {'gh', 'gh.exe'} and token is not None:
            child_environment['GH_TOKEN'] = token
        upload = (Path(args[0]).name.casefold() in {'gh', 'gh.exe'}
                  and args[1:3] == ['release', 'upload'])
        scanner = (len(args) > 1 and Path(args[0]).resolve() == Path(sys.executable).resolve()
                   and Path(args[1]).resolve() == (root / 'scripts/audit_publication.py').resolve())
        try:
            result = subprocess.run(args, cwd=root, capture_output=True, text=True,
                                    timeout=1800 if upload else 900 if scanner else 180, check=False, env=child_environment)
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise ReleaseError('A required local Git/GitHub command could not complete') from exc
        if result.returncode:
            if optional404 and re.search(r'HTTP 404', result.stderr):
                return None
            raise ReleaseError('A required Git/GitHub command failed; inspect it locally without disclosing credentials')
        return result.stdout
    return read


def api(read, endpoint, *, optional=False, paginate=False):
    args = ['gh', 'api', '--hostname', 'github.com', endpoint]
    if paginate:
        args += ['--paginate', '--slurp']
    raw = read(args, optional404=optional)
    return None if raw is None else json.loads(raw)


def flatten(pages, key=None):
    if isinstance(pages, dict):
        return pages[key] if key else [pages]
    return [item for page in pages for item in (page[key] if key else page)]


def trusted_ci(runs, jobs_by_run, commit):
    candidates = [run for run in runs if run.get('head_sha') == commit
                  and run.get('head_branch') == 'main'
                  and run.get('event') in {'push', 'workflow_dispatch'}
                  and run.get('path') == '.github/workflows/ci.yml'
                  and run.get('repository', {}).get('full_name') == REPOSITORY
                  and run.get('head_repository', {}).get('full_name') == REPOSITORY
                  and run.get('actor', {}).get('login') == OWNER
                  and run.get('triggering_actor', {}).get('login', OWNER) == OWNER]
    require(candidates, 'No trusted public-main validation run exists for this exact commit')
    run = max(candidates, key=lambda item: (item['id'], item.get('run_attempt', 1)))
    require(run.get('status') == 'completed' and run.get('conclusion') == 'success',
            'The latest trusted validation run has not passed; older successes cannot replace it')
    jobs = jobs_by_run(run['id'])
    passed = {job['name'] for job in jobs if job.get('status') == 'completed' and job.get('conclusion') == 'success'}
    require(REQUIRED_JOBS <= passed, 'Trusted validation must pass source-quality, windows-tests and windows-desktop')
    return run['id']


def protected_rules(rules, ruleset_by_id, integration_id):
    require({'pull_request', 'non_fast_forward', 'deletion'} <= {rule['type'] for rule in rules},
            'Main must require PRs and prevent force pushes and deletion')
    quality = [rule for rule in rules if rule['type'] == 'required_status_checks'
               and any(item['context'] == 'source-quality' and item.get('integration_id') == integration_id
                       for item in rule['parameters']['required_status_checks'])]
    require(quality, 'Main must require source-quality from the actual GitHub Actions integration')
    for rule in quality:
        policy = ruleset_by_id(rule['ruleset_id'])
        require(policy.get('enforcement') == 'active' and not policy.get('bypass_actors'),
                'Quality rules must be active without an ownership/integration bypass')


def inspect_source_asset(path, version):
    """Bounded inspection without extracting/executing nested layouts or arrays."""
    state = {'bytes': 0, 'members': 0, 'application': False, 'init': False, 'license': False}
    expected_license = (ROOT / 'LICENSE').read_bytes().replace(b'\r\n', b'\n')

    def inspect(name, data, depth=0):
        state['bytes'] += len(data)
        state['members'] += 1
        require(state['bytes'] <= MAX_EXPANDED and state['members'] <= MAX_MEMBERS and depth <= 5,
                'Source asset exceeds bounded nested-archive inspection limits')
        normalized = name.replace('\\', '/')
        parts = PurePosixPath(normalized).parts
        require(not normalized.startswith('/') and '..' not in parts and '.git' not in parts
                and not re.match(r'^[A-Za-z]:', normalized), 'Source asset contains unsafe paths or Git internals')
        require(Path(normalized).suffix.lower() not in NATIVE_SUFFIXES and not data.startswith(
                (b'MZ', b'\x7fELF', b'\xfe\xed\xfa\xce', b'\xfe\xed\xfa\xcf', b'\xcf\xfa\xed\xfe', b'\xce\xfa\xed\xfe')),
                'Source-only assets cannot contain native executables/libraries; use the binary compliance gate')
        if PurePosixPath(normalized).name == 'LICENSE' and data.replace(b'\r\n', b'\n') == expected_license:
            state['license'] = True
        if normalized.endswith('pyproject.toml'):
            project = tomllib.loads(data.decode('utf-8')).get('project', {})
            if project.get('name') == 'etlayout':
                require(project.get('version') == version and project.get('license') == 'MPL-2.0',
                        'Source archive application version/license does not match reviewed public source')
                state['application'] = True
        if normalized.endswith('etlayout/__init__.py'):
            require(re.search(r'__version__\s*=\s*[\'"]' + re.escape(version) + r'[\'"]', data.decode('utf-8')),
                    'Archived application version does not match the reviewed version')
            state['init'] = True
        if normalized.endswith('.dist-info/METADATA'):
            text = data.decode('utf-8')
            if re.search(r'^Name: etlayout$', text, re.MULTILINE):
                require(f'Version: {version}\n' in text and 'License-Expression: MPL-2.0' in text,
                        'Wheel version/license does not match public source')
                state['application'] = True
        if normalized.endswith('.dist-info/WHEEL'):
            require(b'Root-Is-Purelib: true' in data and b'Tag: py3-none-any' in data,
                    'Only the pure Python application wheel belongs to a source-only release')
        if data.startswith(b'PK\x03\x04'):
            with zipfile.ZipFile(io.BytesIO(data)) as archive:
                members = archive.infolist()
                require(len(members) <= MAX_MEMBERS, 'Source ZIP member budget exceeded')
                for member in members:
                    require(not stat.S_ISLNK(member.external_attr >> 16) and not member.flag_bits & 1,
                            'Source ZIP symlinks/encrypted members are unsupported')
                    if not member.is_dir():
                        require(member.file_size <= MAX_MEMBER, 'Source member size budget exceeded')
                        require(state['bytes'] + member.file_size <= MAX_EXPANDED, 'Expanded source budget exceeded')
                        inspect(member.filename, archive.read(member), depth + 1)
        elif normalized.endswith(('.tar.gz', '.tgz', '.tar', '.tar.bz2', '.tar.xz')) or data[257:262] == b'ustar':
            with tarfile.open(fileobj=io.BytesIO(data), mode='r:*') as archive:
                for member in archive:
                    require(member.isfile() or member.isdir(), 'Source TAR links/special members are unsupported')
                    if member.isfile():
                        require(member.size <= MAX_MEMBER and state['bytes'] + member.size <= MAX_EXPANDED,
                                'Expanded source budget exceeded')
                        with archive.extractfile(member) as stream:
                            inspect(member.name, stream.read(MAX_MEMBER + 1), depth + 1)
        else:
            require(not data.startswith((b'\x1f\x8b', b'BZh', b'\xfd7zXZ\x00', b'7z\xbc\xaf\x27\x1c', b'Rar!\x1a\x07', b'MSCF')),
                    'Unsupported compressed source payload; use a fully inspected ZIP/TAR format')
    require(path.stat().st_size <= MAX_ASSET, 'Release asset is too large for this bounded source inspection')
    inspect(path.name, path.read_bytes())
    return state['application'] and state['init'] and state['license']


def source_fingerprints(root):
    """Reuse the maintained archive builder's exact source and code selection."""
    builder = runpy.run_path(str(root / 'scripts/build_windows.py'))
    source = {path.relative_to(root).as_posix(): sha256(path)
              for path in builder['source_files']()}
    code = {name: digest for name, digest in source.items()
            if Path(name).suffix in {'.py', '.spec', '.lock', '.toml'}}
    return source, code


def inspect_windows_profile(path, policy, version, commit):
    """Check exact source/native provenance and smoke results without execution."""
    from etlayout.io.archive_directory import inspect_directory
    from etlayout.models import SimulationError

    require(policy.get('version') == version and policy.get('platform') == 'windows-x64',
            'Committed binary policy is not eligible for this exact version and platform')
    require(path.name == f'ElectrothermalLayoutStudio-{version}-windows-x64.zip',
            'Unexpected Windows release asset filename')
    try:
        inspect_directory(path, max_bytes=WINDOWS_MAX_BYTES, max_entries=WINDOWS_MAX_ENTRIES,
                          max_directory_bytes=WINDOWS_MAX_DIRECTORY)
    except SimulationError as exc:
        raise ReleaseError('Windows release archive failed bounded central-directory preflight') from exc
    prefix = 'ElectrothermalLayoutStudio/'
    with zipfile.ZipFile(path) as archive:
        members = archive.infolist()
        require(len(members) <= WINDOWS_MAX_ENTRIES
                and len({item.filename.casefold() for item in members}) == len(members),
                'Windows archive has duplicate entries or exceeds its member budget')
        expanded = 0
        for member in members:
            name = member.filename.replace('\\', '/')
            parts = name.rstrip('/').split('/')
            require(name == member.filename and name.startswith(prefix) and all(part not in {'.', '..', ''} and ':' not in part for part in parts)
                    and not stat.S_ISLNK(member.external_attr >> 16) and not member.flag_bits & 1,
                    'Windows archive contains unsafe paths, links or encrypted entries')
            expanded += member.file_size
            require(member.file_size <= WINDOWS_MAX_MEMBER and expanded <= WINDOWS_MAX_EXPANDED
                    and member.file_size <= 10_000 * max(member.compress_size, 1),
                    'Windows archive exceeds its finite expanded/member/ratio budget')
        by_name = {item.filename: item for item in members}

        def selected(name, maximum):
            member = by_name.get(prefix + name)
            require(member is not None and not member.is_dir() and 0 < member.file_size <= maximum,
                    'Required bounded Windows provenance/native input is missing')
            return member

        def content(name, maximum):
            member = selected(name, maximum)
            with archive.open(member) as stream:
                value = stream.read(maximum + 1)
            require(len(value) == member.file_size and len(value) <= maximum,
                    'Windows provenance payload exceeds or differs from its declared size')
            return value

        def native_hash(name):
            member = selected(name, WINDOWS_MAX_MEMBER)
            digest, count = hashlib.sha256(), 0
            with archive.open(member) as stream:
                while block := stream.read(1024**2):
                    count += len(block)
                    require(count <= member.file_size and count <= WINDOWS_MAX_MEMBER,
                            'Windows native payload exceeds its declared bounded size')
                    digest.update(block)
            require(count == member.file_size, 'Windows native payload is truncated')
            return digest.hexdigest()

        profile = json.loads(content('SOURCE_BUILD_PROVENANCE.json', 1024**2))
        require(profile.get('audited_source_profile') is True
                and profile.get('source_catalog_sha256') == policy.get('source_catalog_sha256')
                and profile.get('source_catalog_sha256') == sha256(ROOT / 'packaging/third-party-sources.json'),
                'Windows archive does not match the approved source-built profile')
        catalog = json.loads((ROOT / 'packaging/third-party-sources.json').read_text(encoding='utf-8'))
        require(profile.get('compiled_native') == catalog['compiled_native']
                and profile.get('runtime_python') == catalog['runtime_python'],
                'Windows native/source provenance differs from the committed catalog')
        require(type(policy.get('native_outputs')) is dict and policy['native_outputs'],
                'Committed Windows profile must name exact native outputs')
        for name, expected in policy['native_outputs'].items():
            require(native_hash(name) == expected,
                    'Windows native bytes differ from the approved source/runtime profile')
        require(not any(re.match(r'(?i)(?:vcruntime|msvcp|ucrtbase|vc_redist|vcredist|api-ms-win-crt-)',
                                 PurePosixPath(name).name) for name in by_name),
                'Windows archive must not redistribute Microsoft runtime copies/installers')
        require(not any('/matplotlib/mpl-data/' in name and any(part in name for part in
                            ('/sample_data/', '/fonts/afm/', '/fonts/pdfcorefonts/'))
                        or PurePosixPath(name).name.casefold() == 'opengl32sw.dll' for name in by_name),
                'Windows archive includes excluded optional data/native components')
        runtime = json.loads(content('WINDOWS_RUNTIME_MODIFICATIONS.json', 1024**2))
        require(runtime.get('microsoft_runtime_files_bundled') is False
                and runtime.get('installer_bundled_or_executed') is False,
                'Runtime-prerequisite modification record is missing or inconsistent')
        manifest = json.loads(content('BUILD_MANIFEST.json', 4 * 1024**2))
        require(manifest.get('version') == version and manifest.get('git_head') == commit,
                'Windows archive version/commit differs from the tested protected public main')
        source, code = source_fingerprints(ROOT)
        require(manifest.get('source_sha256') == source and manifest.get('code_sha256') == code,
                'Windows archive source/code fingerprints differ from the exact reviewed source')
        verification = manifest.get('verification', {})
        require(type(verification) is dict, 'Windows native verification record must be an object')
        checks = verification.get('checks')
        require(verification.get('performed') is True and type(checks) is list
                and len(checks) == len(REQUIRED_NATIVE_CHECKS)
                and all(type(check) is dict and type(check.get('exit_code')) is int
                        and check['exit_code'] == 0 and isinstance(check.get('name'), str) for check in checks)
                and {check['name'] for check in checks} == REQUIRED_NATIVE_CHECKS,
                'Windows archive must pass all 25 distinct required native checks; skip-tests is ineligible')



@contextmanager
def _corresponding_source_zip(path, *, application=False):
    """Preflight a source envelope before allocating its ZIP directory."""
    from etlayout.io.archive_directory import inspect_directory
    from etlayout.models import SimulationError

    maximum = MAX_ASSET if application else 2 * 1024**3
    count = MAX_MEMBERS if application else 512
    try:
        inspect_directory(path, max_bytes=maximum, max_entries=count,
                          max_directory_bytes=WINDOWS_MAX_DIRECTORY)
    except SimulationError as exc:
        raise ReleaseError('Corresponding-source ZIP failed bounded central-directory preflight') from exc
    with zipfile.ZipFile(path) as archive:
        members = archive.infolist()
        require(len(members) <= count and len({item.filename.casefold() for item in members}) == len(members),
                'Corresponding-source ZIP has duplicate entries or exceeds its member budget')
        expanded = 0
        for item in members:
            parts = item.filename.rstrip('/').split('/')
            require(not item.filename.startswith('/') and '\\' not in item.filename
                    and all(part not in {'.', '..', ''} and ':' not in part for part in parts)
                    and not item.is_dir() and stat.S_IFMT(item.external_attr >> 16) in {0, stat.S_IFREG}
                    and not item.flag_bits & 1,
                    'Corresponding-source ZIP contains unsafe paths, links or encrypted entries')
            expanded += item.file_size
            require(item.file_size <= (MAX_MEMBER if application else 512 * 1024**2)
                    and expanded <= (MAX_EXPANDED if application else 4 * 1024**3)
                    and item.file_size <= 10_000 * max(item.compress_size, 1),
                    'Corresponding-source ZIP exceeds its finite expanded/member/ratio budget')
        yield archive, {item.filename: item for item in members if not item.is_dir()}


def _source_member_hash(archive, item):
    digest, count = hashlib.sha256(), 0
    with archive.open(item) as stream:
        while block := stream.read(1024**2):
            count += len(block)
            require(count <= item.file_size, 'Corresponding-source member exceeds its declared size')
            digest.update(block)
    require(count == item.file_size, 'Corresponding-source member is truncated')
    return digest.hexdigest()


def _source_metadata(archive, item):
    require(item is not None and 0 < item.file_size <= 4 * 1024**2,
            'Required bounded corresponding-source metadata is missing')
    with archive.open(item) as stream:
        data = stream.read(4 * 1024**2 + 1)
    require(len(data) == item.file_size and len(data) <= 4 * 1024**2,
            'Corresponding-source metadata exceeds its declared bounded size')
    return data


def inspect_corresponding_sources(root, assets, inspections, version, commit):
    """Require exact application and catalog source assets beside a Windows ZIP."""
    by_name = {asset.name: asset for asset in assets}
    app_name = f'etlayout-{version}-source.zip'
    require(app_name in by_name and inspections[app_name].get('inspection_kind', 'source') == 'source',
            'Windows binary requires its exact matching application source ZIP')
    source, code = source_fingerprints(root)
    with _corresponding_source_zip(by_name[app_name], application=True) as (archive, members):
        expected = {'etlayout/' + name: digest for name, digest in source.items()}
        require(set(members) == set(expected) | {'etlayout/SOURCE_MANIFEST.json'},
                'Application source ZIP does not contain the exact reviewed source file set')
        for name, digest in expected.items():
            require(_source_member_hash(archive, members[name]) == digest,
                    'Application source ZIP differs from the exact reviewed source')
        manifest = json.loads(_source_metadata(archive, members['etlayout/SOURCE_MANIFEST.json']))
        require(manifest.get('version') == version and manifest.get('git_head') == commit
                and manifest.get('source_sha256') == source and manifest.get('code_sha256') == code,
                'Application source manifest differs from the tested protected public main')
    with (root / 'packaging/third-party-sources.json').open('rb') as stream:
        catalog_bytes = stream.read(4 * 1024**2 + 1)
    require(0 < len(catalog_bytes) <= 4 * 1024**2, 'Source catalog metadata budget exceeded')
    catalog = json.loads(catalog_bytes)
    sources, parts = catalog.get('sources'), catalog.get('source_bundle_parts')
    require(type(sources) is list and sources and type(parts) is list and parts,
            'Committed catalog must declare exact corresponding-source archives and bundle parts')
    records, grouped = {}, {}
    for entry in sources:
        require(type(entry) is dict and isinstance(entry.get('id'), str) and entry['id'] not in records,
                'Source catalog contains missing or duplicate component identities')
        name, bundle = entry.get('filename', ''), entry.get('source_bundle', '')
        require(isinstance(name, str) and isinstance(bundle, str)
                and re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9._-]{0,159}', name)
                and entry.get('bundle_path') == 'archives/' + name
                and type(entry.get('bytes')) is int and 0 < entry['bytes'] <= 512 * 1024**2
                and isinstance(entry.get('sha256'), str) and re.fullmatch('[0-9a-f]{64}', entry['sha256'])
                and re.fullmatch(r'ETLayout-' + re.escape(version) + r'-corresponding-source-[A-Za-z0-9-]+\.zip', bundle),
                'Source catalog contains an invalid archive path, size, checksum or versioned part')
        records[entry['id']] = entry
        grouped.setdefault(bundle, []).append(entry)
    declared = {}
    for part in parts:
        require(type(part) is dict and isinstance(part.get('filename'), str),
                'Source bundle declaration must name an ordinary source part')
        name, identities = part.get('filename'), part.get('component_ids')
        require(name in grouped and name not in declared and type(identities) is list
                and all(isinstance(identity, str) for identity in identities)
                and len(identities) == len(set(identities))
                and set(identities) == {entry['id'] for entry in grouped[name]},
                'Source bundle declarations disagree with the complete component catalog')
        declared[name] = identities
    require(set(declared) == set(grouped), 'Source catalog omits a corresponding-source bundle part')
    allowed_metadata = {'README.md', 'SOURCE_INDEX.json', 'SOURCE_CATALOG.json', 'licenses/GPL-3.0.txt',
                        'licenses/Pillow-embedded-font-notices.txt'}
    for name, entries in grouped.items():
        require(name in by_name and inspections[name].get('inspection_kind') == 'third-party-source',
                'Windows binary requires every catalog source bundle as a reviewed third-party-source asset')
        with _corresponding_source_zip(by_name[name]) as (archive, members):
            expected = {entry['bundle_path']: entry for entry in entries}
            require(set(members) == set(expected) | allowed_metadata,
                    'Corresponding-source part contains missing, extra or misplaced archives/metadata')
            for member, entry in expected.items():
                require(members[member].file_size == entry['bytes']
                        and _source_member_hash(archive, members[member]) == entry['sha256'],
                        'Corresponding-source archive bytes differ from the committed catalog')
            require(_source_metadata(archive, members['SOURCE_CATALOG.json']) == catalog_bytes,
                    'Source-part catalog differs from the committed source catalog')
            require(_source_metadata(archive, members['licenses/GPL-3.0.txt']) ==
                    (root / 'packaging/license-texts/GPL-3.0.txt').read_bytes(),
                    'Source-part GPL license text differs from the retained license')
            require(_source_metadata(archive, members['licenses/Pillow-embedded-font-notices.txt']) ==
                    (root / 'packaging/license-texts/Pillow-embedded-font-notices.txt').read_bytes(),
                    'Source-part embedded-font notices differ from the retained permissions')
            index = json.loads(_source_metadata(archive, members['SOURCE_INDEX.json']))
            indexed = [{key: entry[key] for key in ('id', 'filename', 'bytes', 'sha256')}
                       for entry in sorted(entries, key=lambda entry: entry['id'])]
            require(index == {'schema_version': 1, 'source_bundle': name,
                              'source_catalog_sha256': hashlib.sha256(catalog_bytes).hexdigest(),
                              'sources': indexed},
                    'Source-part index differs from its exact committed component subset')
            _source_metadata(archive, members['README.md'])


def reviewed_inputs(root, assets_dir, manifest_path, commit, version, *, committed_policy=None):
    require(not manifest_path.is_symlink() and not manifest_path.resolve().is_relative_to(root.resolve()),
            'Keep the private release-review manifest outside the source checkout')
    require(manifest_path.stat().st_size <= 1024 * 1024, 'Review manifest size budget exceeded')
    review = json.loads(manifest_path.read_text(encoding='utf-8'))
    require(review.get('schema_version') == 1 and review.get('repository') == REPOSITORY
            and review.get('commit') == commit and review.get('version') == version
            and review.get('reviewer') == OWNER, 'Review must bind the exact owner, destination, commit and version')
    needed = {'rights_and_privacy', 'license_notices', 'build_information', 'owner_only_authority', 'source_contents_reviewed'}
    require(all(review.get('attestations', {}).get(key) is True for key in needed),
            'Owner review must explicitly clear rights/privacy, notices, build information, authority and source contents')
    kind = review.get('kind')
    require(kind in {'source', 'binary'}, 'Review kind must be source or binary')
    if kind == 'binary':
        require(committed_policy is not None and committed_policy.get('publication_allowed') is True, 'Binary publication is blocked by the committed compliance policy')
        require(all(review['attestations'].get(key) is True for key in
                    ('corresponding_source', 'native_and_font_notices', 'runtime_prerequisites')),
                'Binary review needs exact corresponding source, native/font notices and runtime prerequisite evidence')
        route = review.get('license_route')
        require(route == committed_policy.get('license_route') and route in
                {'GPL-3.0-only-with-MPL-3.3', 'GPL-3.0-only-with-LGPL-3.0'},
                'Binary review must match the committed distribution license route')
        route_evidence = ('gpl_source_access' if route == 'GPL-3.0-only-with-MPL-3.3'
                          else 'lgpl_replacement_and_debugging')
        require(review['attestations'].get(route_evidence) is True,
                'Binary review lacks evidence for its selected source-access/license route')
    evidence = review.get('evidence', [])
    require(evidence, 'Keep hash-bound owner review evidence outside public source')
    evidence_by_name = {}
    for item in evidence:
        file = (manifest_path.parent / item['name']).resolve()
        require(file.is_relative_to(manifest_path.parent.resolve()) and not file.is_relative_to(root.resolve())
                and file.is_file() and not (manifest_path.parent / item['name']).is_symlink(), 'Unsafe private evidence path')
        require(sha256(file) == item['sha256'], 'Private evidence checksum changed after owner review')
        require(item['name'] not in evidence_by_name, 'Duplicate private evidence')
        evidence_by_name[item['name']] = item['sha256']
    assets = review.get('assets', [])
    require(assets and len(assets) <= 50, 'Review must list 1–50 exact asset names and SHA256 hashes')
    resolved, application = [], False
    for item in [*assets, review['release_notes']]:
        name = item['name']
        require(re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9._-]{0,159}', name),
                'Asset names must be plain filenames')
        file = assets_dir / name
        require(file.is_file() and not file.is_symlink() and file.resolve().parent == assets_dir.resolve(),
                'Release assets must be ordinary local files in the reviewed asset folder')
        require(sha256(file) == item['sha256'], 'Release asset checksum changed after owner review')
        inspection = item.get('inspection_kind', 'source')
        require(isinstance(inspection, str) and inspection in {'source', 'binary-candidate', 'third-party-source'},
                'Every release asset needs a supported explicit inspection kind')
        require(kind == 'binary' or inspection == 'source',
                'Larger release-artifact inspection is unavailable to source-only reviews')
        finding_review = item.get('findings_review')
        if finding_review is not None:
            require(kind == 'binary' and inspection in {'binary-candidate', 'third-party-source'}
                    and type(finding_review) is dict
                    and evidence_by_name.get(finding_review.get('name')) == finding_review.get('sha256')
                    and re.fullmatch('[0-9a-f]{64}', finding_review.get('sha256', '')),
                    'Finding review must be hash-bound private evidence for an explicit release artifact')
        if kind == 'source':
            application = inspect_source_asset(file, version) or application
        resolved.append(file)
    require(len({item['name'] for item in assets}) == len(assets), 'Duplicate release assets are not allowed')
    require(resolved[-1].suffix == '.md' and resolved[-1].stat().st_size <= 1024 * 1024,
            'Release notes must be a reviewed bounded Markdown file')
    require('SHA256SUMS.json' in {item['name'] for item in assets}, 'Include reviewed public SHA256SUMS.json')
    checksums = json.loads((assets_dir / 'SHA256SUMS.json').read_text(encoding='utf-8'))
    require(checksums == {item['name']: item['sha256'] for item in assets if item['name'] != 'SHA256SUMS.json'},
            'Public checksums must match exactly the reviewed uploaded assets')
    require(kind != 'source' or application, 'A source release must include its matching source archive or pure Python wheel')
    return review, resolved[:-1], resolved[-1]


def preflight(root, assets_dir, manifest_path, *, read=None, environ=None, credential_source='gh'):
    environ = os.environ if environ is None else environ
    require(not environ.get('GITHUB_ACTIONS') and not environ.get('CI'), 'Release preparation runs locally, never in CI')
    require(not any(environ.get(key) for key in ('GH_TOKEN', 'GITHUB_TOKEN', 'GH_ENTERPRISE_TOKEN', 'GITHUB_ENTERPRISE_TOKEN')),
            'Use the existing owner GitHub CLI session, not an environment publishing token')
    version = stable_version(root)
    read = reader_for(root, credential_source=credential_source) if read is None else read
    require(not read(['git', 'status', '--porcelain=v1', '--untracked-files=all']).strip(), 'Source checkout must be clean')
    require(read(['git', 'branch', '--show-current']).strip() == 'main', 'Release from local main only')
    origin = read(['git', 'remote', 'get-url', '--all', 'origin']).strip()
    require(origin in {f'https://github.com/{REPOSITORY}.git', f'https://github.com/{REPOSITORY}', f'git@github.com:{REPOSITORY}.git'},
            'Origin must be the exact independent public destination')
    commit = read(['git', 'rev-parse', 'HEAD']).strip()
    require(re.fullmatch('[0-9a-f]{40}', commit), 'Cannot establish the local source commit')
    require(api(read, 'user').get('login') == OWNER, 'The active GitHub CLI account must be mmensing')
    repo = api(read, f'repos/{REPOSITORY}')
    require(repo.get('full_name') == REPOSITORY and not repo.get('private') and not repo.get('fork')
            and repo.get('owner', {}).get('login') == OWNER and repo.get('default_branch') == 'main', 'Unexpected destination repository')
    branch = api(read, f'repos/{REPOSITORY}/branches/main')
    require(branch.get('protected') is True and branch['commit']['sha'] == commit,
            'Local HEAD must equal current protected public main')
    rules = flatten(api(read, f'repos/{REPOSITORY}/rules/branches/main', paginate=True))
    actions_app = api(read, 'apps/github-actions')
    require(actions_app.get('slug') == 'github-actions' and isinstance(actions_app.get('id'), int),
            'Cannot establish the GitHub Actions integration identity')
    protected_rules(rules, lambda identifier: api(read, f'repos/{REPOSITORY}/rulesets/{identifier}'), actions_app['id'])
    immutable = api(read, f'repos/{REPOSITORY}/immutable-releases', optional=True)
    require(immutable is not None and immutable.get('enabled') is True,
            'Enable immutable releases, or document unavailable/equivalent controls in an owner-reviewed helper change')
    collaborators = flatten(api(read, f'repos/{REPOSITORY}/collaborators?affiliation=all&per_page=100', paginate=True))
    writers = [item for item in collaborators if any(item.get('permissions', {}).get(key) for key in ('push', 'maintain', 'admin'))]
    require(len(writers) == 1 and writers[0].get('login') == OWNER and writers[0]['permissions'].get('admin'),
            'Only mmensing may hold repository writer/admin membership')
    keys = flatten(api(read, f'repos/{REPOSITORY}/keys?per_page=100', paginate=True))
    require(not any(not item.get('read_only', False) for item in keys), 'Writable deploy keys are not permitted')
    tag = 'v' + version
    require(api(read, f'repos/{REPOSITORY}/git/ref/tags/{tag}', optional=True) is None, 'Tag already exists; never replace it')
    require(api(read, f'repos/{REPOSITORY}/releases/tags/{tag}', optional=True) is None, 'Release/draft already exists; never replace it')
    runs = flatten(api(read, f'repos/{REPOSITORY}/actions/runs?head_sha={commit}&per_page=100', paginate=True), 'workflow_runs')
    run = trusted_ci(runs, lambda identifier: flatten(api(read, f'repos/{REPOSITORY}/actions/runs/{identifier}/jobs?per_page=100', paginate=True), 'jobs'), commit)
    require(assets_dir.is_dir() and not assets_dir.is_symlink(), 'Use an ordinary local asset directory')
    committed_policy = json.loads(read(['git', 'show', f'{commit}:packaging/binary-publication-policy.json']))
    review, assets, notes = reviewed_inputs(root, assets_dir.resolve(), manifest_path, commit, version,
                                           committed_policy=committed_policy)
    inspections = {item['name']: item for item in [*review['assets'], review['release_notes']]}
    if any(item.get('inspection_kind') == 'binary-candidate' for item in review['assets']):
        inspect_corresponding_sources(root, assets, inspections, version, commit)
    for asset in [*assets, notes]:
        command = [sys.executable, str(root / 'scripts/audit_publication.py'), '--root', str(asset)]
        asset_review = inspections[asset.name]
        inspection = asset_review.get('inspection_kind', 'source')
        if inspection == 'binary-candidate':
            source, _code = source_fingerprints(root)
            tracked = set(read(['git', 'ls-files', '-z']).split('\0'))
            require(set(source) <= tracked, 'Windows source fingerprints include uncommitted/untracked input')
            inspect_windows_profile(asset, committed_policy, version, commit)
        if review['kind'] == 'binary' and inspection != 'source':
            # reviewed_inputs already enforced the unchanged committed compliance
            # gate and binary attestations before selecting this larger inspector.
            command += ['--artifact-kind', inspection]
        if asset_review.get('findings_review') is not None:
            command += ['--reviewed-findings', str(manifest_path.parent / asset_review['findings_review']['name'])]
        read(command)
    return dict(repository=REPOSITORY, commit=commit, version=version, tag=tag, trusted_run=run,
                assets=assets, notes=notes, review=review)


def draft_commands(plan):
    return [
        ['gh', 'release', 'create', plan['tag'], '--repo', REPOSITORY, '--target', plan['commit'], '--draft',
         '--title', f"Electrothermal Layout Studio {plan['version']}", '--notes-file', str(plan['notes'])],
        ['gh', 'release', 'upload', plan['tag'], *map(str, plan['assets']), '--repo', REPOSITORY],
    ]


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--assets-dir', required=True, type=Path)
    parser.add_argument('--review-manifest', required=True, type=Path)
    parser.add_argument('--credential-source', choices=('gh', 'git'), default='gh',
                        help='Existing owner CLI session, or existing OS Git credential-manager session')
    parser.add_argument('--prepare-draft', action='store_true', help='Explicitly create/upload an owner-reviewed draft; never publish')
    args = parser.parse_args(argv)
    try:
        # Re-read all live gates for each invocation, including explicit draft preparation.
        plan = preflight(ROOT, args.assets_dir, args.review_manifest, credential_source=args.credential_source)
        if not args.prepare_draft:
            print(f"Preflight passed for {REPOSITORY} {plan['tag']} at {plan['commit']}; no remote state changed.")
            return 0
        read = reader_for(ROOT, credential_source=args.credential_source)
        require(api(read, 'user').get('login') == OWNER, 'The owner session changed after preflight')
        # No automatic retry, deletion or replacement after uncertain network outcomes.
        for command in draft_commands(plan):
            read(command)
        draft = api(read, f"repos/{REPOSITORY}/releases/tags/{plan['tag']}")
        require(draft.get('draft') is True and draft.get('target_commitish') == plan['commit'],
                'Draft target/state could not be verified; stop and inspect it manually')
        remote = {item['name']: item.get('digest') for item in draft.get('assets', [])}
        require(remote == {item['name']: 'sha256:' + item['sha256'] for item in plan['review']['assets']},
                'Uploaded asset checksums could not be verified; keep the draft unpublished and inspect locally')
        print(f"Draft prepared: {draft['html_url']}. Inspect it manually; this helper never publishes.")
        return 0
    except (ReleaseError, OSError, KeyError, ValueError, zipfile.BadZipFile, tarfile.TarError) as exc:
        # Known gate errors are intentionally safe; unexpected parser exceptions get only their type.
        message = str(exc) if isinstance(exc, ReleaseError) else type(exc).__name__
        print('Release preparation stopped: ' + message, file=sys.stderr)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
