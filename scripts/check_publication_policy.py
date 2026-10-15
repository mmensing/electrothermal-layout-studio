# SPDX-License-Identifier: MPL-2.0
"""Small, offline source/workflow contracts; not a substitute for security review."""
from __future__ import annotations

import re
import sys
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ACTIONS = frozenset({
    'actions/checkout@3d3c42e5aac5ba805825da76410c181273ba90b1',
    'actions/setup-python@5fda3b95a4ea91299a34e894583c3862153e4b97',
    'actions/upload-artifact@cf430e030ddbb5b0abf93d22962f4752f3646cd9',
})
CI_PYTHON_VERSION = '3.12.10'  # Official actions/python-versions Windows x64 artifact.
FORBIDDEN_TRIGGERS = ('pull_request_target', 'workflow_run', 'issue_comment', 'repository_dispatch')


def workflow_issues(text: str) -> list[str]:
    """Check this repository's deliberately limited workflow vocabulary.

    This is not a general YAML parser or proof about GitHub's runtime. Server
    permissions and action revisions also require maintainer readback/review.
    """
    issues = []
    code = '\n'.join(line.split('#', 1)[0] for line in text.splitlines())
    if not re.search(r'^permissions:\s*\n  contents: read\s*$', code, re.MULTILINE):
        issues.append('Default workflow permissions must be contents: read')
    if re.search(r'\b(?:write-all|[a-z-]+:\s*write)\b', code):
        issues.append('No workflow may request write permissions')
    if any(re.search(rf'\b{trigger}\s*:', code) for trigger in FORBIDDEN_TRIGGERS):
        issues.append('Privileged or comment-driven execution trigger is prohibited')
    if re.search(r'self-hosted|secrets\.|GITHUB_TOKEN|GH_TOKEN|include-hidden-files:\s*true', code):
        issues.append('No self-hosted runners, explicit credentials or hidden artifact dumps')
    uses = re.findall(r'\buses:\s*([^\s]+)', code)
    if not uses or any(action not in ACTIONS for action in uses):
        issues.append('Every action must match the reviewed immutable allowlist')
    python_setups = sum(action.startswith('actions/setup-python@') for action in uses)
    versions = re.findall(r"python-version:\s*['\"]([^'\"]+)['\"]", code)
    if versions != [CI_PYTHON_VERSION] * python_setups:
        issues.append('Hosted Windows CI must use the reviewed available CPython 3.12.10 artifact')
    checkouts = sum(action.startswith('actions/checkout@') for action in uses)
    if len(re.findall(r'persist-credentials:\s*false', code)) != checkouts:
        issues.append('Every checkout must disable persisted credentials')
    if re.findall(r'runs-on:\s*([^\s]+)', code) != ['windows-2025'] * code.count('runs-on:'):
        issues.append('Only isolated GitHub-hosted windows-2025 runners are configured')
    if re.search(r'\bcache:|actions/cache@', code):
        issues.append('Public PR caches must not feed candidate validation')
    if re.search(r'(?:gh release|git push|twine upload|pypi|contents: write)', code, re.IGNORECASE):
        issues.append('CI may validate candidates but may not publish or push')
    if '--require-hashes --only-binary=:all:' not in code or '--no-build-isolation --no-deps -e .' not in code:
        issues.append('Install hash-verified wheels and local source without dependency resolution')
    if re.search(r'(?:path:|^\s+)[^\n]*\.(?:zip|exe|dll)(?:\s|$)', code, re.MULTILINE):
        issues.append('Public CI artifacts must not redistribute blocked binaries')
    run_indent = None
    for line in code.splitlines():
        if not line.strip():
            continue
        indent = len(line) - len(line.lstrip())
        if run_indent is not None and indent <= run_indent:
            run_indent = None
        if re.match(r'\s*(?:- )?run:', line):
            run_indent = indent
        if run_indent is not None and '${{' in line:
            issues.append('Pass expression/input data via env, never interpolate it into shell code')
    return issues


def main() -> int:
    issues = []
    for path in sorted((ROOT / '.github/workflows').glob('*.yml')):
        issues.extend(f'{path.name}: {item}' for item in workflow_issues(path.read_text(encoding='utf-8')))
    metadata = tomllib.loads((ROOT / 'pyproject.toml').read_text(encoding='utf-8'))
    if metadata['project']['license'] != 'MPL-2.0':
        issues.append('Original project metadata must declare MPL-2.0')
    if (ROOT / '.github/CODEOWNERS').read_text(encoding='utf-8').strip() != '* @mmensing':
        issues.append('CODEOWNERS must route all paths to the owner')
    lock = (ROOT / 'requirements-windows-py312.lock').read_text(encoding='utf-8').replace('\\\n', ' ')
    for line in lock.splitlines():
        if line.strip() and not line.lstrip().startswith('#'):
            if not re.match(r'^[A-Za-z0-9_.-]+==[^\s]+\s+--hash=sha256:[a-f0-9]{64}', line):
                issues.append('Every dependency must be version-pinned with an audited SHA256 wheel hash')
                break
    if issues:
        print('Publication contracts failed:\n' + '\n'.join(f'- {item}' for item in issues), file=sys.stderr)
        return 1
    print('Publication source/workflow contracts passed; binary redistribution remains separately gated.')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
