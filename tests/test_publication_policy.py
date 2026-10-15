# SPDX-License-Identifier: MPL-2.0
"""Observable trust-boundary contracts for the public source workflows."""
import runpy
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
policy = runpy.run_path(str(ROOT / 'scripts/check_publication_policy.py'))


def test_current_public_workflows_use_reviewed_read_only_contracts():
    workflows = list((ROOT / '.github/workflows').glob('*.yml'))
    assert {item.name for item in workflows} == {'ci.yml', 'windows-package.yml', 'power-benchmarks.yml'}
    for item in workflows:
        assert policy['workflow_issues'](item.read_text(encoding='utf-8')) == []
    source = (ROOT / '.github/workflows/ci.yml').read_text(encoding='utf-8')
    assert 'name: source-quality' in source
    assert 'branches: [main]' in source
    assert "cancel-in-progress: ${{ github.event_name == 'pull_request' }}" in source
    for name in ('windows-package.yml', 'power-benchmarks.yml'):
        workflow = (ROOT / '.github/workflows' / name).read_text(encoding='utf-8')
        assert "github.actor == 'mmensing'" in workflow
        assert "github.ref == 'refs/heads/main'" in workflow
        assert 'pull_request:' not in workflow and '  push:' not in workflow


@pytest.mark.parametrize('before,after,diagnostic', [
    ('contents: read', 'contents: write', 'write permissions'),
    ('pull_request:', 'pull_request_target:', 'Privileged'),
    ('persist-credentials: false', 'persist-credentials: true', 'persisted credentials'),
    ('windows-2025', 'self-hosted', 'self-hosted'),
    ("python-version: '3.12.10'", "python-version: '3.12.14'", 'available CPython'),
    ('path: artifacts/pytest-fast.xml', 'path: dist/candidate.zip', 'blocked binaries'),
    ('actions/checkout@3d3c42e5aac5ba805825da76410c181273ba90b1', 'actions/checkout@main', 'immutable'),
    ('run: python scripts/check_publication_policy.py', 'run: python ${{ github.event.pull_request.title }}', 'shell code'),
])
def test_unsafe_workflow_mutations_are_rejected(before, after, diagnostic):
    source = (ROOT / '.github/workflows/ci.yml').read_text(encoding='utf-8')
    assert before in source
    issues = policy['workflow_issues'](source.replace(before, after))
    assert any(diagnostic in item for item in issues)
