# SPDX-License-Identifier: MPL-2.0
"""Owner-local release preflight; optional draft creation, never final publication."""
from __future__ import annotations

import argparse
import hashlib
import io
import json
import os
import re
import stat
import subprocess
import sys
import tarfile
import tomllib
import zipfile
from pathlib import Path, PurePosixPath

REPOSITORY = 'mmensing/electrothermal-layout-studio'
OWNER = 'mmensing'
ROOT = Path(__file__).resolve().parents[1]
REQUIRED_JOBS = {'source-quality', 'windows-tests', 'windows-desktop'}
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


def reader_for(root):
    def read(args, *, optional404=False):
        try:
            result = subprocess.run(args, cwd=root, capture_output=True, text=True, timeout=180, check=False)
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
                    ('corresponding_source', 'lgpl_replacement_and_debugging', 'native_and_font_notices')),
                'Binary review needs exact corresponding source, LGPL replacement/debugging and native/font evidence')
    evidence = review.get('evidence', [])
    require(evidence, 'Keep hash-bound owner review evidence outside public source')
    for item in evidence:
        file = (manifest_path.parent / item['name']).resolve()
        require(file.is_relative_to(manifest_path.parent.resolve()) and not file.is_relative_to(root.resolve())
                and file.is_file() and not (manifest_path.parent / item['name']).is_symlink(), 'Unsafe private evidence path')
        require(sha256(file) == item['sha256'], 'Private evidence checksum changed after owner review')
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


def preflight(root, assets_dir, manifest_path, *, read=None, environ=None):
    environ = os.environ if environ is None else environ
    require(not environ.get('GITHUB_ACTIONS') and not environ.get('CI'), 'Release preparation runs locally, never in CI')
    require(not any(environ.get(key) for key in ('GH_TOKEN', 'GITHUB_TOKEN', 'GH_ENTERPRISE_TOKEN', 'GITHUB_ENTERPRISE_TOKEN')),
            'Use the existing owner GitHub CLI session, not an environment publishing token')
    version = stable_version(root)
    read = reader_for(root) if read is None else read
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
    for asset in [*assets, notes]:
        read([sys.executable, str(root / 'scripts/audit_publication.py'), '--root', str(asset)])
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
    parser.add_argument('--prepare-draft', action='store_true', help='Explicitly create/upload an owner-reviewed draft; never publish')
    args = parser.parse_args(argv)
    try:
        # Re-read all live gates for each invocation, including explicit draft preparation.
        plan = preflight(ROOT, args.assets_dir, args.review_manifest)
        if not args.prepare_draft:
            print(f"Preflight passed for {REPOSITORY} {plan['tag']} at {plan['commit']}; no remote state changed.")
            return 0
        read = reader_for(ROOT)
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
