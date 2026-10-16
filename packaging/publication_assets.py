# SPDX-License-Identifier: MPL-2.0
"""Generate a reviewed metadata-only copy of one upstream runtime SVG.

The pyqtgraph artwork and its MIT notice remain unchanged. Only three Inkscape
export filenames are removed from the generated build input; installed packages
and imported layouts are never modified. Hash changes require another review.
"""
from __future__ import annotations

import hashlib
import re
from pathlib import Path

UPSTREAM_SVG_SHA256 = "06764b4f4fcd5b4d4c1e0f30ae06949935ab71a346e3b2a4bcf5992d1f3dd02f"
PUBLIC_SVG_SHA256 = "87344cb30a3fa125a979e5394e92db72e29ef0f88d0176d41066f17cbd3749b2"
SVG_DESTINATION = "pyqtgraph/icons/icons.svg"
MAX_SVG_BYTES = 1024 * 1024


_EXCLUDED_DATA_PREFIXES = {
    "matplotlib/mpl-data/sample_data/": "Unused upstream example datasets; ETLayout never calls get_sample_data",
    "matplotlib/mpl-data/fonts/afm/": "Optional legacy AFM metrics; application exports use retained TrueType fonts",
    "matplotlib/mpl-data/fonts/pdfcorefonts/": "Optional PDF core-font metrics; application never enables pdf.use14corefonts",
}


def keep_publication_data(destination):
    """Keep runtime fonts/styles/code; omit only explicitly unused data trees."""
    normalized = str(destination).replace("\\", "/").casefold()
    if normalized.startswith("_internal/"):
        normalized = normalized[len("_internal/"):]
    return not any(normalized.startswith(prefix) for prefix in _EXCLUDED_DATA_PREFIXES)


def filter_publication_data(entries):
    """Return kept data entries and exact hash-bound exclusions without deleting."""
    kept, excluded = [], []
    for entry in entries:
        if keep_publication_data(entry[0]):
            kept.append(entry)
            continue
        destination, source, *_ = entry
        path = Path(source)
        if path.is_symlink() or path.stat().st_size > 32 * 1024**2:
            raise ValueError("Unsafe or oversized optional Matplotlib data")
        normalized = str(destination).replace("\\", "/").casefold()
        if normalized.startswith("_internal/"):
            normalized = normalized[len("_internal/"):]
        reason = next(reason for prefix, reason in _EXCLUDED_DATA_PREFIXES.items()
                      if normalized.startswith(prefix))
        excluded.append({"member": str(destination).replace("\\", "/"),
                         "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
                         "reason": reason})
    return kept, excluded


def sanitize_pyqtgraph_svg(content):
    """Accept exactly the reviewed upstream bytes or their idempotent result."""
    if len(content) > MAX_SVG_BYTES:
        raise ValueError("pyqtgraph SVG exceeds the reviewed byte budget")
    fingerprint = hashlib.sha256(content).hexdigest()
    if fingerprint == PUBLIC_SVG_SHA256:
        return content
    if fingerprint != UPSTREAM_SVG_SHA256:
        raise ValueError("pyqtgraph SVG differs from the reviewed upstream asset")
    sanitized, removed = re.subn(rb'\s+inkscape:export-filename="[^"]*"', b"", content)
    if removed != 3 or hashlib.sha256(sanitized).hexdigest() != PUBLIC_SVG_SHA256:
        raise ValueError("pyqtgraph SVG authoring-metadata contract changed")
    return sanitized


def prepare_publication_assets(entries, build_directory):
    """Return PyInstaller data entries and an auditable modification record.

    Only the exact icons.svg entry is redirected to a generated build copy.
    Other data entries retain their existing objects, destination and ordering.
    """
    directory = Path(build_directory).resolve()
    result, modifications = [], []
    for entry in entries:
        destination, source, *rest = entry
        if str(destination).replace("\\", "/") != SVG_DESTINATION:
            result.append(entry)
            continue
        if modifications:
            raise ValueError("Duplicate pyqtgraph SVG build entry")
        source = Path(source)
        if source.is_symlink() or source.stat().st_size > MAX_SVG_BYTES:
            raise ValueError("Unsafe or oversized pyqtgraph SVG input")
        content = source.read_bytes()
        sanitized = sanitize_pyqtgraph_svg(content)
        target = directory / SVG_DESTINATION
        if not target.resolve().is_relative_to(directory) or target.is_symlink():
            raise ValueError("Generated pyqtgraph SVG escapes its build directory")
        # Never overwrite the installed package, an arbitrary asset or a changed
        # prior build product. A repeated preparation is a byte-identical no-op.
        if target.resolve() == source.resolve() and content != sanitized:
            raise ValueError("Generated SVG must not overwrite the upstream input")
        if target.exists():
            if target.stat().st_size > MAX_SVG_BYTES or target.read_bytes() != sanitized:
                raise ValueError("Existing generated SVG differs from the reviewed output")
        else:
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(sanitized)
        result.append((destination, str(target), *rest))
        modifications.append({
            "component": "pyqtgraph", "version": "0.14.0", "license": "MIT",
            "member": SVG_DESTINATION, "upstream_sha256": UPSTREAM_SVG_SHA256,
            "generated_sha256": PUBLIC_SVG_SHA256, "removed_attributes": 3,
            "change": "Remove Inkscape export-filename metadata; retain all artwork and notices",
        })
    if not modifications:
        raise ValueError("Expected reviewed pyqtgraph SVG build entry is absent")
    return result, modifications
