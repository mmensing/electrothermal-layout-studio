# Qt retained-reference lifetime fix

The Windows desktop uses PySide6 / Qt 6.12.0 with a source-built
**Shiboken 6.12.0+etlayout.1** runtime. The original Qt bindings and Shiboken.pyd
are retained. The small patch changes retained-reference ownership in
libshiboken; it does not disable garbage collection, change its thresholds,
retain every widget indefinitely or change simulation threads.

## What the patch changes

Upstream basewrapper.cpp releases Python objects before removing their entries
from the map traversed by Python garbage collection. A Python finalizer can
re-enter Qt or permit another thread to collect while an already freed object
remains in that map. The dated patch detaches entries before releasing them and
completes a replacement before invoking the previous object's finalizer.
Finalizer-driven changes therefore survive the outer setter.

Four regressions run ordinary supported Qt operations in disposable child
processes: deferred deletion with GUI or worker-thread collection, reference
removal, and reference replacement. They check finalization and the retained
replacement as well as process survival. All four fail on the unmodified locked
runtime. A broad passing workflow alone did not expose this defect.

## Install from source on Windows

Published application folders include their reviewed runtime. Source development
also needs **CMake 3.26 or newer and the x64 MSVC C++ build tools / Windows SDK**
(for example the Visual Studio 2022 C++ build tools). MinGW cannot replace this
MSVC-ABI library.

After installing the repository's hash-locked bootstrap dependencies and local
application, run:

~~~powershell
python scripts/prepare_qt_runtime.py
python -m pytest tests/test_qt_reference_lifetime.py
python -m etlayout gui
~~~

The bootstrap downloads only bounded, hash-pinned upstream inputs, reproduces
the maintained sanitized source archive, builds the upstream Qt-independent
runtime target, checks all public export names and runs actual Qt regressions
before installation. It creates a local-version wheel with truthful metadata
and RECORD, then tests the installed runtime too. The original Qt module and
unrelated wheel payloads/notices are retained. Build and output directories must
be fresh: an uncertain or failed build never overwrites earlier evidence.
The [recipe](../packaging/source-recipes/shiboken6-6.12.0/README.md) documents
explicit local-input commands, ABI checks and complete preferred-source output.

The lock describes the original upstream bootstrap wheels. The supported runtime
then uses the explicitly built local patch; dependency provenance records this
rather than presenting it as an unchanged upstream wheel. These build steps need
internet access; ordinary application operation remains offline.
The [Microsoft system runtime prerequisite](windows-runtime.md) is installed
separately; its DLLs and installer are not redistributed.

## Rebuild, CI and distribution

Windows CI builds and tests the runtime from the same pinned sources. Only a
successful owner-main run may retain the explicit candidate wheel with its
complete preferred source, notices, recipe and provenance. PR artifacts never
authorize release publication. Normal owner review, protected-main validation,
native/extracted application checks and exact artifact privacy/licensing gates
still apply. CI has read-only contents permissions and cannot publish.

For the release source set, the Qt part contains the original
`archives/pyside6-6.12.0-etlayout-source.tar.gz` and
`archives/shiboken6-6.12.0-etlayout.1-source-supplement.zip` as separate siblings.
The supplement preserves every modified source, patch, recipe, license and build
proof byte from the complete CI archive. It omits only the duplicate original tar;
**both archives are required together** for complete corresponding source. Its
README records the original tar's exact hash/size and the complete CI archive hash.
The stable catalog source ID is retained; its filename and role identify the
release supplement. The native runtime and the complete CI source artifact do not
change during this packaging step.

The release-only [source supplement builder](../packaging/source-recipes/shiboken6-6.12.0/source_supplement.py)
requires explicit reviewed hashes, rejects missing/extra members and writes a new
ZIP without overwriting either input or existing output. It checks that all eight
retained files remain byte-identical. For the reviewed 0.4.2 CI source artifact:

~~~powershell
python packaging/source-recipes/shiboken6-6.12.0/source_supplement.py `
  --preferred-source preferred-source.zip `
  --preferred-sha256 ebeea77ca4ac6eee73a8c25b5a8260c31df6fabb831fa732df0a2b266d0d409f `
  --upstream-sha256 e659fbccafdc0ca7a0235188355837b67274e4e18e893b0b98a5a76c859d13ef `
  --upstream-bytes 21104154 `
  --output shiboken6-6.12.0-etlayout.1-source-supplement.zip
~~~

This pairing avoids redundant recursive archives while retaining the full source
and the unchanged finite publication-inspection budgets. Distribute the entire
corresponding-source Qt part rather than the supplement alone.

The C++ patch preserves its upstream LGPL/GPL alternatives and original notices;
the modified source has a dated change notice. The selected Windows distribution
route is GPL-3.0-only, retaining the offered library grants. Original application
source remains MPL-2.0. Matching source and rebuild information must accompany
any distributed modified runtime; a wheel hash or upstream link alone does not
satisfy that obligation. See [licensing](licensing.md) and
[maintainer releases](maintainer-guide.md).

Finite regression and application journeys establish their tested scopes. They
do not certify every Qt lifetime, clean-machine deployment, physical monitor-DPI,
screen-reader behavior or code signing. Release-specific measured results belong
in [validation](validation.md) and the actual release notes.

