# SPDX-License-Identifier: MPL-2.0
"""Source/frozen resource policy retains engineering assets and drops handoffs."""
import runpy
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
policy = runpy.run_path(str(ROOT / 'packaging/resources.py'))


@pytest.mark.parametrize('path', ['docs/implementation-brief.md', 'docs/ux-guided-workflow/README.md',
    'docs/verification/receipt.json', 'docs/mockups/example.png', 'docs/internal_implementation_prompt.md',
    'docs/handoff-notes.md', 'docs/conversation-export.md', 'docs/scratch-plan.md',
    'tests/__pycache__/test_gui.pyc', 'docs/pytest-results.xml', 'docs/coverage.json', 'AGENTS.md'])
def test_development_content_is_not_distributable(path):
    assert not policy['distributable'](path)


@pytest.mark.parametrize('path', ['docs/design-guidelines.md', 'docs/images/desktop/results.png',
    'docs/example-provenance/power-routing.json', 'examples/E1_straight.etl', 'examples/power_routing/generate.py',
    'src/etlayout/data/user-guide.html', 'packaging/license-texts/GPL-3.0.txt', 'tests/fixtures/physical.xml'])
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
