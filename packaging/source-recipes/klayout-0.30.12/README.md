# KLayout Windows dependency build inputs

KLayout 0.30.12 and its matching native dependency sources are identified by the
source catalog. Preserve the upstream GPL/Python-import exception and dependency
notices. The unlicensed external convenience-helper repository is not distributed.

The maintained `configure_pthreads.py` independently expresses two compatibility
requirements for the original LGPL pthreads-w32 2.9.1 source:

1. Modern Microsoft SDKs provide `struct timespec`; define `_TIMESPEC_DEFINED=1`
   when compiling this older library to avoid redefining that SDK type.
2. On Windows x64 the Interlocked 64-bit destination parameter is signed
   `LONG64 volatile *`, rather than an unsigned `size_t *`. The generator uses the
   SDK's own `LONG64` type only in the `_WIN64` branch and retains the original
   non-Windows-64 definition. See the [Microsoft ABI reference](https://learn.microsoft.com/en-us/windows/win32/api/winnt/nf-winnt-interlockedcompareexchange64).

Extract the pinned pthreads archive into a fresh directory, then run:

```console
python packaging/source-recipes/klayout-0.30.12/configure_pthreads.py path/to/pthreads-w32-2-9-1-release
```

The generator verifies both complete original file hashes before writing either
file. It retains all other bytes and records its modifications and output hashes
in `ETLAYOUT_PTHREAD_COMPATIBILITY.json`. Original LGPL notices remain in place;
the new compatibility change is identified and dated. No external helper script,
patch text or documentation has been copied.

With a separately installed Microsoft C++ toolchain, the dependency's original
Makefile provides the `VC` target (`nmake clean VC`); configure x64 compilation in
the developer shell. KLayout's own `setup.py`/build scripts define extension
sources and dependency discovery. Supply the matching source/build outputs for
Expat 2.2.6, libpng 1.6.37, zlib 1.2.11, curl 7.61.0 and pthreads 2.9.1.
The standalone Python extension does not require Ruby. In KLayout 0.30.12
`setup.py`, `KLAYOUT_BITS` supplies include/library roots with these exact
subdirectories: `zlib/include` and `zlib/lib`, `ptw/include` and `ptw/libraries`,
`png/include` and `png/libraries`, `expat/include` and `expat/libraries`, and
`curl/include` and `curl/libraries`. Place the matching headers and built libraries
there, set `KLAYOUT_BITS` to their common root and run `python -m pip wheel .
--no-deps --no-build-isolation` from the pinned KLayout source with the chosen
CPython developer headers/import library. `KLAYOUT_SETUP_MULTICORE` controls its
build parallelism. The supplied upstream scripts contain the full extension and
link definitions; do not substitute newer dependencies silently.

The supplied sources, independent compatibility generator and upstream build
scripts describe the required inputs. The exact vendor MSVC wheel has **not**
been rebuilt here, and this document does not claim byte-for-byte reproduction.
The independent x64 signed-pointer ABI check was compiled using the audited
MinGW toolchain; it is not a claim that an MSVC build was executed.
