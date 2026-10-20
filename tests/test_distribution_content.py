# SPDX-License-Identifier: MPL-2.0
"""Source/frozen resource policy retains engineering assets and drops handoffs."""
import runpy
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
policy = runpy.run_path(str(ROOT / 'packaging/resources.py'))


@pytest.mark.parametrize('path', ['docs/implementation-brief.md', 'docs/ux-guided-workflow/README.md',
    'docs/verification/receipt.json', 'docs/mockups/example.png', 'docs/internal_implementation_prompt.md',
    'docs/handoff-notes.md', 'docs/conversation-export.md', 'docs/scratch-plan.md',
    'tests/__pycache__/test_gui.pyc', 'docs/pytest-results.xml', 'docs/coverage.json', 'AGENTS.md',
    'src/etlayout.egg-info/PKG-INFO', 'src/etlayout.egg-info/SOURCES.txt', 'src/Old.EGG-INFO/requires.txt'])
def test_development_content_is_not_distributable(path):
    assert not policy['distributable'](path)


@pytest.mark.parametrize('path', ['docs/design-guidelines.md', 'docs/images/desktop/results.png',
    'docs/example-provenance/power-routing.json', 'examples/E1_straight.etl', 'examples/power_routing/generate.py',
    'src/etlayout/data/user-guide.html', 'packaging/license-texts/GPL-3.0.txt', 'tests/fixtures/physical.xml',
    '.gitattributes', 'runtime/PySide6-6.12.0.dist-info/METADATA'])
def test_maintained_assets_and_attribution_are_distributable(path):
    assert policy['distributable'](path)


def test_synchronization_removes_stale_bundle_handoffs_without_touching_binaries(tmp_path):
    docs = tmp_path / 'docs'
    docs.mkdir()
    (docs / 'guide.md').write_text('Maintained user guide')
    (docs / 'handoff.md').write_text('Development-only text')
    bundle = tmp_path / 'dist/app'
    (bundle / 'docs/verification').mkdir(parents=True)
    (bundle / 'docs/verification/stale.json').write_text('{}')
    (bundle / 'app.exe').write_bytes(b'untouched binary sentinel')
    policy['synchronize_resources'](tmp_path, bundle, 'docs')
    assert (bundle / 'docs/guide.md').read_text() == 'Maintained user guide'
    assert not (bundle / 'docs/handoff.md').exists()
    assert not (bundle / 'docs/verification/stale.json').exists()
    assert (bundle / 'app.exe').read_bytes() == b'untouched binary sentinel'
    with pytest.raises(ValueError, match='explicit workspace'):
        policy['synchronize_resources'](tmp_path, tmp_path.parent / 'outside-app', 'docs')


def test_actual_resource_and_source_lists_exclude_obsolete_content():
    resources = [str(path.relative_to(ROOT)) for path in policy['resource_files'](ROOT, 'docs')]
    assert resources and all(policy['distributable'](path) for path in resources)
    build = runpy.run_path(str(ROOT / 'scripts/build_windows.py'))
    source = list(build['source_files']())
    assert ROOT / 'LICENSE' in source and ROOT / 'THIRD_PARTY_NOTICES.md' in source
    assert ROOT / 'docs/design-guidelines.md' in source
    assert all(policy['distributable'](path.relative_to(ROOT)) for path in source)


@pytest.mark.parametrize('separate_git_dir', [False, True])
def test_source_export_uses_only_indexed_regular_files(tmp_path, monkeypatch, separate_git_dir):
    root = tmp_path / 'checkout'
    command = ['git', 'init', '--quiet']
    if separate_git_dir:
        command += ['--separate-git-dir', str(tmp_path / 'git-control')]
    subprocess.run([*command, str(root)], check=True, capture_output=True)
    kept = {'.gitattributes', 'README.md', 'src/etlayout/model.py', 'maintained-reference.txt',
            'notes/µ guide.md'}
    generated = 'src/etlayout.egg-info/PKG-INFO'
    for name in kept | {generated, 'docs/untracked-note.md', 'src/ignored.pyc', 'docs/removed.md'}:
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text('Version: 0.4.0.dev0' if name == generated else 'maintained', encoding='utf-8')
    subprocess.run(['git', 'add', '-f', '--', *sorted(kept | {generated, 'docs/removed.md'})],
                   cwd=root, check=True, capture_output=True)
    subprocess.run(['git', 'rm', '--cached', '--', 'docs/removed.md'],
                   cwd=root, check=True, capture_output=True)
    (root / 'src/etlayout/model.py').write_text('modified tracked source', encoding='utf-8')
    build = runpy.run_path(str(ROOT / 'scripts/build_windows.py'))
    monkeypatch.setitem(build['source_files'].__globals__, 'ROOT', root)
    actual = list(build['source_files']())
    assert {path.relative_to(root).as_posix() for path in actual} == kept
    assert (root / 'src/etlayout/model.py').read_text(encoding='utf-8') == 'modified tracked source'
    (root / 'README.md').unlink()
    with pytest.raises(ValueError, match='missing, linked or outside'):
        list(build['source_files']())


def test_standalone_source_export_omits_generated_metadata(tmp_path, monkeypatch):
    # No .git entry: do not let Git discover the containing repository instead.
    subprocess.run(['git', 'init', '--quiet', str(tmp_path)], check=True, capture_output=True)
    (tmp_path / 'parent-only.txt').write_text('outside the source export', encoding='utf-8')
    subprocess.run(['git', 'add', '--', 'parent-only.txt'], cwd=tmp_path, check=True, capture_output=True)
    root = tmp_path / 'source'
    kept = {'.gitattributes', 'pyproject.toml', 'docs/guide.md', 'src/etlayout/model.py'}
    for name in kept | {'src/etlayout.egg-info/PKG-INFO', 'src/etlayout.egg-info/SOURCES.txt'}:
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text('standalone source', encoding='utf-8')
    build = runpy.run_path(str(ROOT / 'scripts/build_windows.py'))
    monkeypatch.setitem(build['source_files'].__globals__, 'ROOT', root)
    assert {path.relative_to(root).as_posix() for path in build['source_files']()} == kept
