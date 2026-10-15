# SPDX-License-Identifier: MPL-2.0
"""Shared distribution policy for maintained resources and source archives."""
from pathlib import Path, PurePosixPath

EXCLUDED_DIRECTORIES = frozenset({
    '__pycache__', '.pytest_cache', '.artifacts', 'verification', 'mockups',
    'ux-guided-workflow', 'scratch', 'conversations',
})
EXCLUDED_SUFFIXES = frozenset({'.pyc', '.pyo', '.log', '.tmp'})
HANDOFF_WORDS = ('implementation_prompt', 'ux_prompt', 'handoff', 'mockup', 'conversation', 'scratch')


def distributable(relative):
    """Reject development-only content; retain normal scientific JSON/assets."""
    path = Path(relative)
    names = [part.lower() for part in path.parts]
    if any(part in EXCLUDED_DIRECTORIES for part in names):
        return False
    name = path.name.lower()
    if path.suffix.lower() in EXCLUDED_SUFFIXES or name.startswith('.coverage') or name in {'coverage.json', 'coverage.xml', 'pytest-results.xml'}:
        return False
    if name in {'agents.md', 'skill.md', 'start_here.md', 'implementation-brief.md', 'design-qa.md'}:
        return False
    return not any(word in name for word in HANDOFF_WORDS)


def resource_files(root, directory):
    root = Path(root)
    return [path for path in sorted((root / directory).rglob('*'))
            if path.is_file() and distributable(path.relative_to(root))]


def synchronize_resources(root, bundle, directory):
    """Copy a filtered set and remove only obsolete resource files in that set.

    No recursive directory deletion: existing binaries and licenses are outside
    the explicitly named docs/examples resource root and cannot be removed.
    """
    root, bundle = Path(root).resolve(), Path(bundle).resolve()
    target = (bundle / directory).resolve()
    if directory not in {'docs', 'examples'} or not bundle.is_relative_to(root) or not target.is_relative_to(bundle):
        raise ValueError('Resource synchronization requires an explicit workspace bundle docs/examples directory')
    import shutil
    allowed = {path.relative_to(root / directory): path for path in resource_files(root, directory)}
    target.mkdir(parents=True, exist_ok=True)
    for existing in target.rglob('*'):
        if existing.is_file() and existing.relative_to(target) not in allowed:
            resolved = existing.resolve()
            if not resolved.is_relative_to(target):
                raise ValueError('Resource file resolves outside the intended bundle')
            existing.unlink()
    for relative, path in allowed.items():
        destination = target / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(path, destination)
    return allowed


def distributable_metadata(destination):
    """Omit installed PEP 610 origins; preserve application resources and notices.

    PyInstaller expands copied distribution metadata to top-level .dist-info
    destinations before adding its _internal runtime directory.
    """
    parts = PurePosixPath(str(destination).replace("\\", "/")).parts
    return not (len(parts) == 2 and parts[0].lower().endswith(".dist-info")
                and parts[1].lower() == "direct_url.json")
