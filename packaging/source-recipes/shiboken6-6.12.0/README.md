# Shiboken 6.12 reference-lifetime runtime patch

The source patch fixes reference-map mutation during Python finalization in
Shiboken 6.12.0. Referents are detached from the live map before decref can invoke
Python code or garbage collection. The application does not disable GC, alter its
thresholds or retain windows indefinitely. `tests/test_qt_reference_lifetime.py`
exercises actual Qt delegates and reentrant clear/remove/replace behavior in fresh
processes, including collection from a worker thread.

## Build and source installation

Use native Windows x64, CPython 3.12, CMake 3.26 or newer, Git and an x64 MSVC C++17
compiler with the Windows SDK. The MSVC ABI must match the upstream PySide wheels;
MinGW is rejected. The builder does not install a compiler or mutate the active
Python environment. Install the repository's hash-locked development dependencies
first. MSVC is available on the maintained Windows hosted runner.

`libshiboken` has no Qt link dependency. The small CMake entry point reuses its
upstream target, source list, signature embedding generator and Python macros.
It inspects the complete pinned archive, materializes only the upstream runtime
and build-support inputs, and omits the top-level Qt SDK/generator/bindings build.
The complete pristine source archive is retained in the preferred-source output. Python's stable ABI import
library (`python3.lib`) and `Py_LIMITED_API=0x030a0000` preserve the upstream
`cp310-abi3-win_amd64` interface. The application profile is tested on Python 3.12.

Obtain the exact source and stock wheel identified below through the maintained
preparation helper or their upstream/catalogued sources. Normalization of the
pinned official Qt source must reproduce the existing audited archive exactly.
The build helper itself performs no downloads:

```powershell
python packaging/source-recipes/shiboken6-6.12.0/build_runtime.py --source-archive pyside6-6.12.0-etlayout-source.tar.gz --upstream-wheel shiboken6-6.12.0-cp310-abi3-win_amd64.whl --build-dir build/shiboken-runtime --output-dir artifacts/shiboken-runtime --jobs 4
python -m pip install --no-deps --force-reinstall artifacts/shiboken-runtime/shiboken6-6.12.0+etlayout.1-cp310-abi3-win_amd64.whl
python -m pip check
```

The runtime wheel has distribution version `6.12.0+etlayout.1`; the upstream
module/bindings API remains 6.12.0. PEP 440 public-version equality means PySide's
`shiboken6==6.12.0` requirement accepts this explicit local version. Only the runtime
DLL and corresponding import library are rebuilt; the locked upstream
`Shiboken.pyd`, Python API, headers and required notices are retained. Every
packaged byte receives a new truthful wheel `RECORD`. Compiler/source paths are
mapped to neutral prefixes; build logs stay outside the wheel.

The packager excludes Microsoft's runtime DLL copies. Install the supported
Microsoft Visual C++ x64 v14 system prerequisite instead, as for the application
bundle. It preserves all upstream copyright/license material and adds the GPLv3
text for this distribution's selected GPL-3.0-only route. This is a third-party
runtime dependency; the application's original source remains MPL-2.0.

## Exact inputs and gates

| Input | SHA256 |
| --- | --- |
| `pyside6-6.12.0-etlayout-source.tar.gz` (21,104,154 bytes) | `e659fbccafdc0ca7a0235188355837b67274e4e18e893b0b98a5a76c859d13ef` |
| `shiboken6-6.12.0-cp310-abi3-win_amd64.whl` | `a1906cb8116869178c64b6bab52623bbc8b4ceed7b30bfc3c9aa0f6e67f6438e` |

The source is the full maintained PySide/Shiboken archive from the corresponding
source catalog. Only `sources/shiboken6/libshiboken/basewrapper.cpp` is patched.
Unsafe archive members, wrong input hashes, a changed MSVC export set, non-stable
Python linkage or a failed lifetime regression stop the build. There is no skip
option. The output directory receives a wheel only after its actual rebuilt DLL
has imported with stock PySide bindings and passed the native regression gate.
Fresh build/output names avoid replacement or recursive cleanup.

Outputs are the custom wheel, `BUILD_PROVENANCE.json`, `regression.xml` and
`preferred-source.zip`. The source archive includes the exact pristine archive,
patch, modified C++ file, maintained build recipe, notices and build information.
It does not include proprietary compiler/system runtime binaries. The provenance
records source/patch/recipe/compiler versions, native hashes, unchanged module
hash, exports/imports and executed test evidence. Whole-application source CI,
GUI/CLI/native package validation, privacy/license review and exact owner-main
release gates remain separate. A candidate artifact is not a release publication.

The upstream source retains its Qt Company copyright and offered licenses. The
lifetime patch identifies its applicable upstream license; no third-party code is
relicensed as original application material. Retain the source, patch, recipes and
notices beside any distributed patched native dependency, following the project's
maintained corresponding-source distribution procedure.
