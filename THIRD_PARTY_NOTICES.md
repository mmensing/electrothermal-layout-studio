# Third-party notices

Electrothermal Layout Studio's original source is MPL-2.0; see `LICENSE`.
Dependencies retain their copyright, license and patent notices. They are not
relicensed as MPL. A combined Windows distribution containing Gmsh uses the
GPL-3.0-only route described in [the licensing guide](docs/licensing.md), including
MPL Section 3.3 for eligible application files. The source edition remains MPL;
the complete frozen application must not be described as MPL-only.

`DEPENDENCIES.json` describes the locked Python 3.12 environment, including
build/test packages that are not necessarily shipped. The release SBOM, native
inventory and dependency/source manifest determine exact runtime contents.
`scripts/collect_licenses.py` retains installed notices verbatim; supplemental
texts in `packaging/license-texts/` cover components whose wheel notices alone
are insufficient. These summaries do not replace those texts or the obligation
to provide matching corresponding source and build information. Publication is
gated per exact artifact by `packaging/binary-publication-policy.json`.

| Component | Version | License and distribution scope | Upstream |
|---|---|---|---|
| Gmsh | 4.15.2 | GPL-2.0-or-later; original limited named-library exception retained. The Windows combination selects GPL-3.0-only. Bundled components have their own notices | [License](https://gmsh.info/doc/texinfo/#License) |
| KLayout | 0.30.12 | Wheel declares GPL-3.0-or-later; upstream also documents GPL-2.0-or-later and a Python-import exception. Preserve that exception; it does not relicense the C++ library | [License and exception](https://www.klayout.de/license.html) |
| PySide6 / bundled Qt modules; Shiboken6 runtime | Qt/PySide 6.12.0; Shiboken 6.12.0+etlayout.1 | Qualifying Qt/PySide source offers GPL-3.0-only, among its alternatives; the Windows combination selects that route. Embedded third-party code retains separate terms | [Qt for Python](https://doc.qt.io/qtforpython-6/licenses.html), [Qt GPL](https://doc.qt.io/qt-6.12/gpl.html) |
| NumPy | 2.5.4 | BSD-3-Clause, with 0BSD, MIT, Zlib, CC0 and native component notices | [License](https://numpy.org/doc/stable/license) |
| SciPy | 1.18.1 | BSD-3-Clause and its bundled-code licenses | [Source](https://github.com/scipy/scipy) |
| OpenBLAS / LAPACK | NumPy: 0.3.34.106.0 ILP64; SciPy: 0.3.31.dev LP64 | BSD-3-Clause / BSD-3-Clause-Open-MPI; build revisions and static runtimes belong in the exact native/source manifest | [OpenBLAS](https://github.com/OpenMathLib/OpenBLAS) |
| Shapely / GEOS | 2.2.0 / 3.14.1 | BSD-3-Clause / LGPL-2.1-only for the selected GEOS compliance route; source and modification/relinking terms remain applicable | [Shapely](https://github.com/shapely/shapely), [GEOS](https://libgeos.org/) |
| Matplotlib | 3.11.2 | Matplotlib's PSF-based license plus embedded-code notices, including FreeType, Qhull, HarfBuzz and libraqm | [License](https://matplotlib.org/stable/project/license.html) |
| Matplotlib TrueType fonts | Assets in 3.11.2 | Per-family terms below; retain the full Matplotlib, DejaVu and STIX notices | [Font licenses](https://matplotlib.org/stable/project/license.html) |
| Pillow | 12.3.0 | MIT-CMU plus native codec/font libraries below | [Source and licenses](https://github.com/python-pillow/Pillow/tree/12.3.0) |
| pyqtgraph | 0.14.0 | MIT; bundled CET colormap data is CC-BY-4.0, with attribution below | [Source](https://github.com/pyqtgraph/pyqtgraph) |
| Rich | 15.0.0 | MIT, BSD-3-Clause and Unicode-3.0 for the collected generated data; matching wcwidth and emoji preferred sources and original grants are retained | [Source](https://github.com/Textualize/rich) |
| FontTools | 4.66.1 | MIT, Adobe BSD-3-Clause and Unicode-3.0 for the frozen subset; full source also includes Apache-2.0 cu2qu | [Source and licenses](https://github.com/fonttools/fonttools) |
| meshio / platformdirs | 5.3.5 / 4.12.4 | MIT | [meshio](https://github.com/nschloe/meshio), [platformdirs](https://github.com/tox-dev/platformdirs) |
| threadpoolctl / psutil | 3.7.0 / 7.2.2 | BSD-3-Clause | [threadpoolctl](https://github.com/joblib/threadpoolctl), [psutil](https://github.com/giampaolo/psutil) |
| CPython | 3.12.14 | Python-2.0 / PSF license history plus the exact build's SSL, compression, FFI, database and other component notices | [Source](https://www.python.org/downloads/source/) |
| PyInstaller bootloader | 6.22.3 | GPL-2.0-or-later WITH Bootloader-exception; bundled zlib is Zlib. Build-tool source also includes Apache-2.0 parts | [License](https://pyinstaller.org/en/stable/license.html) |
| GCC runtime / MinGW-w64 runtime | GCC 16.2.0 / MinGW-w64 14.0.0 for source-built Gmsh and PyInstaller bootloaders | GPL-3.0-or-later WITH GCC-exception-3.1 for applicable GCC runtime files; MinGW runtime has its own permissive notices | [GCC exception](https://gcc.gnu.org/onlinedocs/libstdc++/manual/license.html), [MinGW-w64](https://www.mingw-w64.org/) |
| Microsoft Visual C++ runtime | System prerequisite for the Windows package | Microsoft terms apply to the separately installed runtime. The publication build removes app-local redistributable copies; this is not an MPL component | [Runtime installation](https://learn.microsoft.com/cpp/windows/latest-supported-vc-redist) |

## Native components and build modifications

The NumPy OpenBLAS source revision is `446c436e10450c348169808f1a6b3fae0925c9f7`
(ILP64), built with openblas-libs recipe `78fc0eaf6de71d92fd98f74f7e5bfa356a2f83e3`.
SciPy uses `5ffbf38b41a1fe494d038efcb09cf22f4d527c22` (LP64), with recipe
`db637abddcd6abb7fcb63be7fbe418deeb4729db`. Retain both exact sources and
recipes; one build cannot stand in for the other.

The publication Gmsh DLL is built from the matching 4.15.2 source with a recorded
configuration and GCC-only toolchain. The original SDK's unused external static
libraries are not silently attributed to the new build. Preserve the actual
configured bundled-code notices in `Gmsh-bundled-notices.txt`, full GPL texts,
GCC Runtime Library Exception 3.1 and `GCC-runtime-notices.txt`, and the complete
MinGW-w64 runtime notice. The compiler itself is a build tool, not an application
runtime. The exception does not remove Gmsh's own GPL obligations.

The Windows publication bootloaders are also built from the matching
PyInstaller 6.22.3 C source with the recorded GNU toolchain. Their bundled
zlib 1.3.2 inflate code retains Jean-loup Gailly and Mark Adler's complete
grant in `PyInstaller-zlib-notice.txt`. Preserve that notice, the bootloader
exception, GCC runtime exception and MinGW runtime notices together.

Gmsh's general exception names Netgen, METIS, OpenCASCADE and ParaView; it is not
a blanket permission to link arbitrary proprietary software. TetGen/BR has a
Gmsh-specific relicensing notice. Blossom/Concorde has a separate permission for
academic and non-academic use **within the Gmsh system**; preserve its exact scope
if included. The unused Untangle/HLBFGS/ICFS feature has restrictive source
headers and is excluded from the publication build and corresponding-source
selection; no new permission is inferred from the Gmsh wrapper license.

The Windows publication build redirects four NumPy/GEOS consumers' private,
hashed MSVC import names to their canonical system-runtime names and updates PE
checksums. It preserves their code, import symbols and section layout; generated
before/after hashes and patch instructions accompany the exact-source evidence.
No Microsoft runtime DLLs or installers are redistributed in that package. Source-build
and frozen-runtime checks remain separate from clean-machine validation.

For pyqtgraph, `packaging/publication_assets.py` creates a hash-checked copy of
`icons/icons.svg` with only three Inkscape export-filename attributes removed.
Artwork and the MIT notice remain unchanged; the modification is recorded in the
build manifest. The installed package is not overwritten.

The binary-supporting `KLayout-native-notices.txt` retains the exact curl 7.61.0,
Expat 2.2.6, libpng 1.6.37, pthreads-w32 2.9.1 and zlib 1.2.11 permission and
copyright texts. `CPython-native-notices.txt` similarly retains matching
OpenSSL 3.5.8, libffi 3.4.8, bzip2 1.0.8, Expat 2.8.3, XZ/liblzma 5.8.3 and
zlib 1.3.2 notices for the recorded Python 3.12.14 build, in addition to CPython's
full historical license. The retained zlib-ng alternative-recipe notice does not
imply that zlib-ng is linked into this CPython build; Pillow uses zlib-ng 2.3.3.
Dynamic library source and build instructions accompany
the release rather than being inferred from the wrapper's license.

For GEOS, ANN and pthreads-win32, this distribution supplies the complete
application/library source and build/relink material required by LGPL 2.1
section 6(a), alongside the GPL-3.0-only combined-work terms. Original library
grants and contributor notices remain intact; no later-version GEOS grant or
conversion of its source notices is assumed. Gmsh's Gmm component retains its
LGPL-3.0-or-later grant with the original GCC Runtime Library Exception. Its
MathEx component retains its LGPL 2.1 grant and static-link permissions: Gmsh
is based in part on the work of the SSCILIB Library, copyright (C) 2000-2003
Sadao Massago. The exact grants and the Eigen/TensorFlow Apache 2.0 attribution
are preserved in `Gmsh-bundled-notices.txt`.

Pillow also embeds a limited-character Aileron Regular font in `PIL.ImageFont`;
this font is additional to the 40 standalone Matplotlib TTF files. Its designer
is Sora Sagano. The author permits modification and redistribution without
warranty; the exact original permission and subset hash are retained in
`Pillow-embedded-font-notices.txt` as `LicenseRef-DotColon-Aileron`. No OFL or
CC0 license is inferred for this embedded subset.
The separate legacy `courB08` bitmap fallback derives from Adobe/DEC Courier;
its complete permissive grant and copyright are retained in the same notice
file as `LicenseRef-Adobe-DEC-Courier`. `packaging/embedded-fonts.json` records
both exact embedded payload sets. These add two font resources to the 40
standalone TTF files; the bitmap descriptor/atlas are preserved in Pillow source.

The frozen Python module inventory also includes Setuptools 84.0.0, wheel 0.48.0
and typing_extensions 4.16.0; they are runtime contents even when also used as
build tools. `Python-packaging-notices.txt` retains their grants and Setuptools
vendor notices: backports.tarfile 1.2.0, importlib_metadata 8.7.1, jaraco.context
6.1.0, jaraco.functools 4.4.0, jaraco.text 4.0.0, more_itertools 10.8.0, packaging
26.0, tomli 2.4.0 and zipp 3.23.0. These differ from separately installed
versions where recorded in the module/source inventory. The collected subset
uses MIT, Apache 2.0, BSD 2-Clause and inherited Python terms; typing_extensions
uses PSF-2.0. Distutils' CPython provenance and original Python notices remain.
The supplied full Setuptools source additionally includes uncollected autocommand
2.2.2 under LGPL-3.0-or-later; a package-level MIT declaration does not replace
that grant. Complete preferred source and unmodified vendor license texts are
supplied separately from binary module-presence evidence.

The collected `pyi_rth_pyqtgraph_multiprocess` runtime hook comes from
pyinstaller-hooks-contrib 2026.8 and is Apache-2.0, copyright (c) 2022
PyInstaller Development Team. Standard build hooks in that separate source
package are GPL-2.0-or-later; the grants apply by file type. Its complete
license and this exact runtime-hook copyright/grant are retained in
`Python-packaging-notices.txt`; PyInstaller core source is not substituted
for this distinct component.

## Generated data and adapted-code attribution

`Generated-data-notices.txt` retains the full Jeff Quast/Markus Kuhn, emoji
(Taehoon Kim and Kevin Wurster), Adobe and Unicode grants used by actual
frozen modules. Rich's 21 Unicode width/VS16 tables match wcwidth 0.3.0, and
its emoji-name table matches emoji 0.5.4 through the supplied upstream Rich
generators. These are copied/generated-data sources, not additional imported
runtime packages; the matching data does not establish a historical generator
environment. FontTools' Adobe glyph lists and Unicode character-property
tables retain their own grants. Its full source's optional cu2qu Apache-2.0
code is not part of the verified frozen subset.

The same notice preserves Mathias Bynens and Taneli Hukkinen's MIT attribution
for markdown-it-py's punycode code, and the MIT copyright/permission notices
from the exact Neuroglancer source adapted by meshio: CEA, Forschungszentrum
Juelich GmbH and Yann Leprince. Required institutional and contributor
attribution is retained alongside each installed package's license.

## Qt modules and fonts

The desktop uses Qt Core, Gui, Widgets, Network, OpenGL, OpenGLWidgets, Svg and
SvgWidgets. Qt Test is retained for validation. Inspect actual DLLs and plugins,
not just PySide6 metadata: unused QML/Quick, Pdf, WebEngine and virtual-keyboard
components are excluded by the build and checked in the candidate inventory.
An installed Pdf/WebEngine environment package does not establish that its code
is shipped. Qt copyright includes The Qt Company Ltd. and its contributors;
Qt's embedded FreeType, HarfBuzz, image codecs, fonts and other parts retain the
full module/source notices. A GPL offer for Qt does not change permissive
embedded-code attribution requirements. `Qt-6.12.0-notices.txt` retains matching
QtBase/Svg/ImageFormats attribution records and exact embedded license texts,
including Windows tablet/OpenGL interfaces, Unicode data, text shaping, fonts,
image codecs and compression. The source records describe optional configurations;
a notice's inclusion alone does not establish that its optional code is linked.

The Matplotlib publication data keeps all **40 TrueType fonts**:

| Family | Files | Required notice |
|---|---:|---|
| DejaVu | 14 | Bitstream Vera and Arev permission notices; DejaVu changes are public domain |
| STIX | 17 | OFL-1.1; retain `LICENSE_STIX`, including the upstream OTF-to-TTF conversion notice and reserved names |
| BaKoMa Computer Modern | 8 | Basil Malyshev's BaKoMa permission in Matplotlib's full license; retain its copyright and contact notice |
| Last Resort High-Efficiency | 1 | OFL-1.1, with its notice in Matplotlib's full license |

The optional legacy AFM and PDF core-font metric directories are excluded, as
are unused Matplotlib demonstration datasets. All TrueType assets, their license
files, style data and library code remain. Application exports do not enable
`ps.useafm` or `pdf.use14corefonts`; PDF/SVG math-font export is tested with the
retained DejaVu, STIX and Computer Modern files. This does not assert a new grant
for legacy Adobe AFM material. Host-system UI fonts are not copied into the
package.

## Pillow codec notices

The Windows Pillow wheel's native reports and exact release recipes identify
FreeType 2.14.3, HarfBuzz 14.2.1, littlecms2 2.19.1 (runtime reports 2.19),
libjpeg-turbo 3.1.4.1, libpng 1.6.58, WebP 1.6.0 including libsharpyuv,
OpenJPEG 2.5.4, TIFF 4.7.1, Brotli 1.2.0, XZ 5.8.3 and zlib-ng 2.3.3.
The AVIF extension includes libavif 1.4.2, AOM 3.14.1 encoding, dav1d 1.5.3
decoding and libyuv revision `644251f252a84bf8ce91ff0aca86a9b16b069ab8`.
The exact recipes and source manifest record the library configuration.

Retain Pillow's combined license, including codec-specific IJG/BSD/Zlib, FTL,
MIT, Apache and other notices. `Pillow-AVIF-notices.txt` additionally preserves
AOM's BSD copyright and Patent License 1.0, the exact dav1d copyright notice,
and libyuv's BSD notice and patent grant. Their presence is not a claim that
third-party patent rights are universally cleared. The inspected Pillow wheel
does not enable Raqm; Matplotlib's own libraqm notice is still required. Portions
of this software use the FreeType Project (https://freetype.org/); the complete
FreeType notice and applicable permission accompany the distribution.

## Colormap attribution and academic acknowledgment

The bundled CET colormap data is by **Peter Kovesi / ColorCET**, licensed under
[CC-BY-4.0](https://creativecommons.org/licenses/by/4.0/). The collected pyqtgraph
CET license is retained verbatim. Data is supplied without changes by ETLayout;
selecting a map does not confer endorsement. See [ColorCET](https://colorcet.com/)
and P. Kovesi, *Good Colour Maps: How to Design Them* (2015),
[arXiv:1509.03700](https://arxiv.org/abs/1509.03700).

C. Geuzaine and J.-F. Remacle, *Gmsh: a three-dimensional finite element mesh
generator with built-in pre- and post-processing facilities*, International
Journal for Numerical Methods in Engineering 79(11), 1309–1331 (2009).
[Upstream software and publication](https://gmsh.info/).

KLayout is copyright Matthias Koefferlein and contributors. NumPy, SciPy,
Matplotlib and every embedded component retain their genuine contributor
notices in the supplied license texts and matching source. Do not erase those
notices to satisfy a personal-data scanner or substitute the application owner's
name for upstream authorship.

## Modified Shiboken runtime

The original bootstrap wheel is Shiboken 6.12.0. The supported desktop runtime
is source-built 6.12.0+etlayout.1 with a dated retained-reference lifetime patch.
Original copyright and offered LGPL/GPL grants are retained; the combined Windows
profile selects GPL-3.0-only. See [the maintained patch and build recipe](packaging/source-recipes/shiboken6-6.12.0/README.md)
and [runtime provenance](docs/qt-runtime.md). The exact source archive, patch,
modified source, recipe, compiler/ABI evidence and notices accompany a distributed
candidate. Microsoft runtime DLLs are excluded; use the documented system prerequisite.
