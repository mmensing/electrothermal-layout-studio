# SPDX-License-Identifier: MPL-2.0
"""Apply independently specified Windows SDK compatibility to LGPL pthreads 2.9.1.

The original files retain their upstream license. This generator does not import
or redistribute an external vendor helper or claim a vendor build reproduction.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

INPUTS = {
    "implement.h": "8959d57ce8de20f347baf3ecdb32320147f1749068a01e6b65f5671e5a8c155e",
    "Makefile": "d188d466de1cce7ace45081c3e8e8f47619c030bf34c6705409a9e8b35854378",
}
OUTPUTS = {
    "implement.h": "08164ec7ac81834284fc2bfd5b516bcc64753447aecb7927ddbf233741ea4248",
    "Makefile": "59c4738917c2b015beaf926be8fa82f94fcae89c4d845e3e8d2fe8200b47193f",
}
ABI_OLD = b"#define PTW32_INTERLOCKED_SIZEPTR PTW32_INTERLOCKED_VOLATILE size_t*"
ABI_NEW = b"\n".join([
    b"/* ETLayout compatibility change (2026-10-16): Windows 64-bit Interlocked ABI. */",
    b"#if defined(_WIN64)",
    b"#define PTW32_INTERLOCKED_SIZEPTR PTW32_INTERLOCKED_VOLATILE LONG64*",
    b"#else",
    ABI_OLD,
    b"#endif",
])
FLAGS_OLD = b"XCFLAGS = /W3 /MD /nologo"
FLAGS_NEW = (b"# ETLayout compatibility change (2026-10-16): modern Windows SDK timespec guard.\n"
             b"XCFLAGS = /W3 /MD /nologo /D_TIMESPEC_DEFINED=1")


def transform(name: str, data: bytes) -> bytes:
    """Change exactly one known configuration/ABI definition, preserving other bytes."""
    old, new = (ABI_OLD, ABI_NEW) if name == "implement.h" else (FLAGS_OLD, FLAGS_NEW)
    if name not in INPUTS or data.count(old) != 1:
        raise ValueError("expected exactly one original pthread definition")
    newline = b"\r\n" if b"\r\n" in data else b"\n"
    return data.replace(old, new.replace(b"\n", newline), 1)


def configure(source: Path) -> dict:
    receipt_path = source / "ETLAYOUT_PTHREAD_COMPATIBILITY.json"
    if receipt_path.exists():
        raise FileExistsError(receipt_path)
    original = {name: (source / name).read_bytes() for name in INPUTS}
    for name, data in original.items():
        if hashlib.sha256(data).hexdigest() != INPUTS[name]:
            raise ValueError(f"not the pinned original pthreads 2.9.1 file: {name}")
    changed = {name: transform(name, data) for name, data in original.items()}
    if any(hashlib.sha256(data).hexdigest() != OUTPUTS[name] for name, data in changed.items()):
        raise ValueError("Compatibility output differs from the reviewed source change")
    receipt = {
        "schema": 1,
        "source": "pthreads-w32-2-9-1-release",
        "upstream_license": "LGPL-2.1-or-later",
        "modified": "2026-10-16",
        "basis": "independent Windows SDK configuration and signed LONG64 Interlocked ABI",
        "vendor_binary_reproduced": False,
        "files": {name: {"before_sha256": INPUTS[name],
                         "after_sha256": hashlib.sha256(data).hexdigest()}
                  for name, data in changed.items()},
    }
    for name, data in changed.items():
        (source / name).write_bytes(data)
    receipt_path.write_text(json.dumps(receipt, indent=2) + "\n", encoding="utf-8")
    return receipt


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path, help="fresh extracted pinned pthreads 2.9.1 directory")
    args = parser.parse_args()
    print(json.dumps(configure(args.source), indent=2))


if __name__ == "__main__":
    main()
