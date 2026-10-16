# Windows corresponding source

The original Electrothermal Layout Studio source remains **MPL-2.0**. A Windows
bundle combines it with Gmsh and GPL-offered Qt components; that combined binary
uses the **GPL-3.0-only** route permitted by MPL 2.0 section 3.3. Keep the MPL
notices on original source files and the original terms on every dependency.
This is not an MPL-only binary, and it does not revoke earlier recipients' rights.
See [licensing](licensing.md) and [third-party notices](../THIRD_PARTY_NOTICES.md).

## What accompanies a binary

Each approved download must have equivalent access to its complete corresponding
source, notices, exact build information, patches and hashes beside it. The
machine-readable [source catalog](../packaging/third-party-sources.json) identifies
the preferred source archives and the native/Python build profile. The per-bundle
SBOM and inventory identify what actually shipped; an installed-package list does
not establish a frozen bundle's contents. Archive hashes identify the reviewed
inputs. Upstream links alone do not satisfy a publisher's source responsibility.

The application source asset and four corresponding-source parts include the tested
application tree, every catalogued archive,
source-curation/build recipes, dependency build patches, native import-rewrite
script and the exact manifest used to package the binary. Preserve each upstream
license, attribution and patent notice. Build-only sources are distinguished from
runtime sources in the catalog. General-purpose compilers and operating-system
libraries are prerequisites; they are not silently treated as application source.
The shipped MinGW runtime source and preserved GCC runtime exception/notices
identify the native runtime provenance. The full GCC compiler is an unshipped
general-purpose prerequisite, recorded by version/archive hash and upstream
reference rather than included as an application source asset.

For online distribution use [GPLv3 section 6(d)](https://doc.qt.io/qt-6/gpl.html),
with publisher-maintained equivalent source access. Section 6(b)'s written-offer
route concerns physical products/media; it is not a substitute for missing online
source. Source must include the scripts and interface inputs needed to build,
install and run a modified version. The license does not require a byte-identical
rebuild; we do not claim one. No signature or application hash lock prevents users
from running modified source. The maintainer's release-integrity checks remain a
separate requirement for publishing an official artifact.

## Native build choices

- **Gmsh 4.15.2:** built with the maintained
  [Windows recipe](../packaging/source-recipes/gmsh-4.15.2/README.md), rather than
  relying on undocumented static dependencies in the upstream SDK binary. The
  audited source set excludes unused `contrib/untangle`, `contrib/hxt` and
  `contrib/mpeg_encode` components with restricted historical notices. Their CMake
  options are explicitly off. Planar `model.geo` geometry and algorithm 6 meshing,
  local refinement and application physics are retained. The upstream named-library
  exception is preserved with its actual limited scope. The catalog records the
  actual compiled input separately from the delivered archive: normalization and
  six unused Eigen benchmark HTML/JavaScript omissions leave all retained compiled
  source, build and license payloads unchanged.
- **PyInstaller 6.22.3:** the console and windowed bootloaders are built from the
  delivered C/Waf/zlib sources using the tested
  [GCC Windows recipe](../packaging/source-recipes/pyinstaller-6.22.3/README.md).
  Unshipped precompiled upstream loaders are omitted. The native profile records
  both selected loader hashes, their raw code-section hashes and complete direct
  import signatures. Freezing may change resources and append the Python archive;
  the final executables must retain the reviewed code and imports. GNU runtime and
  MinGW notices remain; the Microsoft runtime is not statically linked into these
  two loaders. This is a build-provenance change, not a change to application physics.
- **Qt/PySide/Shiboken 6.12.0:** use the upstream GPLv3 offer for the shipped modules.
  Full QtBase, QtSvg, QtImageFormats and PySide source archives retain their bundled
  sources and build inputs. The LGPLv3 section 4 combined-work route is not the
  selected offer for these modules; this does not remove GPL source or notice
  obligations. The optional unused Mesa `opengl32sw.dll` is excluded after native
  raster/cross-section/profile/comparison/export checks, including
  `QT_OPENGL=software` and actual loaded-module observation. ETLayout does not
  implement an OpenGL/3D canvas. Supported plots use Qt raster widgets and Agg.
- **CPython 3.12.14:** the release interpreter comes from the pinned public
  python-build-standalone 20260825 distribution, with its exact recipe commit,
  source patches and native dependency sources. It is not the development tool's
  managed interpreter. Python's Microsoft runtime dependency is an external
  official Redistributable prerequisite, not an undocumented bundled runtime.
  The omitted CPython `ensurepip` pip 25.0.1 wheel and `Lib/test/wheeldata`
  archives are unshipped installer/test data. The pinned PBS Windows build
  independently downloads and bootstraps pip 26.2.1 through its hash-pinned
  `pythonbuild/downloads.py` entry and `cpython-windows/build.py`; that separate
  general-purpose build prerequisite is recorded in the catalog. The frozen
  application does not ship pip. Preserve the default PGO training sources and
  required compression/XML/pickle data when reproducing the interpreter build.
- **Scientific/geometry/image libraries:** retain the exact OpenBLAS revisions and
  Windows patches, the [independent KLayout dependency configuration](../packaging/source-recipes/klayout-0.30.12/README.md), GEOS, and the actual native
  Pillow/Matplotlib codec/font sources. Pillow's AVIF path includes AOM, dav1d,
  libyuv and sharpyuv; package-level metadata alone is insufficient. Matplotlib's
  unused AFM/core-font and sample data are omitted; supported TTF/math/PDF/SVG
  rendering remains available. The exact exclusions and normalized archive-header
  policy are reproducible with
  [curate_sources.py](../packaging/source-recipes/curate_sources.py).

The frozen Python module archive and outer runtime hooks are audited separately
from installed-package metadata. Collected Setuptools, Wheel and typing_extensions
code has its own exact preferred sources and notices. Setuptools retains the
vendored packaging 26.0 sources separately from the application's packaging 26.3
installation, all other collected vendor code, and historical CPython/distutils
notices. Its uncollected source-only autocommand grant is preserved without
claiming that LGPL component ships in the executable. The collected
`pyi_rth_pyqtgraph_multiprocess` hook belongs to pyinstaller-hooks-contrib 2026.8;
its Apache 2.0 source and notices are supplied separately from PyInstaller's
bootloader source. Standard build collection hooks in that same source archive
retain their GPL 2.0-or-later terms. The catalog maps each non-standard-library module root and
outer hook to its actual source owner; standard-library roots use the recorded
CPython source. Imported module names alone do not establish copyright ownership.

Rich's generated Unicode widths and emoji aliases have explicit additional
preferred data sources: wcwidth 0.3.0 and emoji 0.5.4. All 21 width tables/VS16
sets and 3,608 name/code pairs match those sources under Rich's original
generators. This establishes data correspondence, not the original developer's
generator environment. Both source archives, the omitted-from-sdist Rich
upstream generators and their original grants are supplied; see
[the generation inputs](../packaging/source-recipes/rich-15.0.0/README.md).
FontTools' supplied source retains its Adobe glyph lists, editable Unicode
tables and MetaTools generator. Runtime metadata distinguishes these data
licenses from source-only cu2qu and omitted optional font media.

The NumPy/SciPy OpenBLAS DLLs embed libgfortran paths identifying GCC 10.3.0;
these are separate from the rebuilt Gmsh GCC 16.2 runtime. Their exact published
Windows link recipes use gfortran/static runtimes and check that libquadmath is
not linked. Redistribution of eligible target code with independent modules uses
the [GCC Runtime Library Exception 3.1](https://gcc.gnu.org/onlinedocs/libstdc++/manual/license.html)
additional permission, preserving its actual notices. We do not claim that the
GCC 16.2 archive is corresponding source for the older vendor runtime, or that we
reproduced that vendor runtime byte for byte. OpenBLAS source, build recipes and
patches remain supplied independently of this exception.

For GEOS, pthreads-w32 and the LGPL-covered ANN/MathEx portions in Gmsh, retain
original LGPL grants and satisfy the **LGPL 2.1 section 6(a)** source/relink option
alongside the GPLv3 combined application. This requires the complete library
preferred source and modifications, plus application source/object code and
build inputs in a form suitable for modification and relinking. The supplied
application, GEOS/Shapely, KLayout/dependency sources, upstream build definitions,
independent pthread compatibility generator and native import-rewrite script
provide those inputs. Preserve all copyrights, license texts, exceptions and
MathEx's SSCILIB attribution. GEOS uses conservative selected LGPL 2.1 terms;
its generic source grant is not silently interpreted as an explicit later-version
offer. ANN/pthreads' original later-version statements are preserved without
relying on them for this route. No LGPL 2.1 section 3 notice conversion is claimed.
See the [LGPL 2.1 text](https://www.gnu.org/licenses/old-licenses/lgpl-2.1.html).

The distribution permits recipient modifications and reverse engineering for
debugging them. A separate DLL alone does not satisfy these obligations: final
native validation must also confirm GEOS replacement remains possible. Publication
hash checks identify an official candidate and do not restrict running a modified
application/library. The original MSVC vendor wheels were not rebuilt here; their
preferred sources, functional patches and build inputs are supplied without a
byte-identical vendor-rebuild claim. Gmm retains its LGPLv3/GCC exception terms;
its full source and the tested Gmsh rebuild recipe are included. Qt's separately
selected GPLv3 offer remains as described above.

No dependency source is relabeled as the application's own work. Retained source
members and build patches are unmodified unless the catalog explicitly records a
change. Curated archives preserve required source/build/license bytes and include
`SOURCE_SET_CHANGES.json` and/or `SOURCE_ARCHIVE_CHANGES.json`. These records name
the exact unshipped malformed fixtures, remote-lab automation and optional data
omitted, normalized owner/time headers, materialized internal file links, and
metadata-only changes. Qt attribution JSON is re-encoded without changing any
parsed key/value or copyright. Required configure/build inputs and Python PGO
training sources remain. Optional font/media corpora in dependency tests, fuzzing,
benchmarks and sample applications are omitted where redistribution permission was
not established; this includes HarfBuzz, FontTools, Raqm, Pillow, Matplotlib and Qt
test assets. Source, build/install inputs, notices and training inputs required for
the shipped configurations remain. Explicitly listed optional language bindings,
test assets and benchmark tooling may be absent; upstream tests/configurations
relying on them are not claimed usable. The 40 shipped
Matplotlib TTF fonts retain their grants; the two retained Qt WebAssembly source
sample fonts have explicit DejaVu/Bitstream/Arev notices. Pillow's embedded default
font data remain in its source with the separately preserved upstream permission
notices. The excluded optional components are not needed for the shipped
configuration; omitted upstream test fixtures mean that some upstream media tests
are unavailable from this curated source set.

## Revalidation before owner publication

1. Check every local source archive against the catalog and inspect bounded nested
   contents with the publication scanner. Review upstream test keys and copyright
   material by provenance; do not waive parser failures or genuine credentials.
2. Build the documented native/Python profile. Record actual DLL hashes, compiler
   flags, direct imports and all modifications in `SOURCE_BUILD_PROVENANCE.json`.
3. Run the full source/numerical suite, exact configured examples, native GUI
   journeys and final frozen CLI/editor/export checks. Do not substitute a source
   test for a packaged-build test or claim clean-machine validation without it.
4. Verify the frozen inventory has no excluded source/data/runtime files, all
   native/font entries have source and notice mappings, and all source assets are
   actually available with the intended binary download.
5. Follow the owner-only draft/review/publication procedure. Any missing right,
   notice, preferred source, build input or failing gate blocks binary publication.

The source manifest's candidate status is deliberate until final artifact and
source receipts have passed. It is not itself a legal clearance or a release.

