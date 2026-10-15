# Third-party notices

Original application source: MPL-2.0; see `LICENSE`. Dependencies retain their own
licenses and copyright holders. They are not relicensed as MPL. This source edition
does not include dependency binaries. Public binary publication remains blocked by
`packaging/binary-publication-policy.json` until exact-source, notice and replacement
obligations are met. See [the licensing guide](docs/licensing.md).

The versions below are the locked Windows Python 3.12 environment. Exact candidate
contents are determined separately by the generated SBOM and file manifest.
`DEPENDENCIES.json` includes every locked package, role and upstream metadata.
Verbatim available notices are collected by `scripts/collect_licenses.py`; those
notices, rather than this summary, retain the detailed copyright statements.
No project patches to dependency source are maintained here. Upstream wheel build
modifications and static linkage still need exact-source provenance review.

| Component | Version | Applicable license / scope | Upstream |
|---|---|---|---|
| Gmsh | 4.15.2 | GPL-2.0-or-later with limited named-library exception; GPL still applies to Gmsh | [Gmsh](https://gmsh.info/doc/texinfo/#License) |
| KLayout | 0.30.12 | Wheel declares GPL-3.0-or-later; project GPL-2.0-or-later and published Python-import exception. C++ code remains GPL | [License and exception](https://www.klayout.de/license.html) |
| PySide6, Essentials, Addons, Shiboken6 | 6.12.0 | LGPL-3.0-only OR GPL-2.0-only OR GPL-3.0-only; each bundled Qt module and third-party part must be checked | [Qt for Python](https://doc.qt.io/qtforpython-6/licenses.html) |
| PySide6 Pdf / WebEngine environment packages | 6.12.0.140 | Installed by PySide6 metadata; not used by ETLayout. Their absence from frozen contents must be checked, not inferred | [Qt licensing](https://doc.qt.io/qt-6/licensing.html) |
| NumPy | 2.5.4 | BSD-3-Clause AND 0BSD AND MIT AND Zlib AND CC0-1.0; native libraries separate | [NumPy](https://numpy.org/doc/stable/license) |
| SciPy | 1.18.1 | BSD-3-Clause; bundled components retain their notices | [SciPy source](https://github.com/scipy/scipy) |
| OpenBLAS / LAPACK | NumPy: 0.3.34.106.0 ILP64; SciPy: 0.3.31.dev LP64 | BSD-3-Clause / BSD-3-Clause-Open-MPI, plus GCC runtime GPL-3.0-or-later WITH GCC-exception-3.1 where applicable. Exact native revisions unresolved | [OpenBLAS](https://github.com/OpenMathLib/OpenBLAS) |
| Shapely / GEOS | 2.2.0 / 3.14.1 | BSD-3-Clause / LGPL-2.1-or-later | [Shapely](https://github.com/shapely/shapely), [GEOS](https://libgeos.org/) |
| Matplotlib | 3.11.2 | Matplotlib's PSF-based license; retain its full license and embedded-code notices | [License](https://matplotlib.org/stable/project/license.html) |
| Matplotlib fonts | Assets in 3.11.2 wheel | DejaVu: Bitstream Vera permission plus public-domain changes; STIX: OFL-1.1; Computer Modern and PDF fonts have individual notices. Not proprietary system fonts | [Font notices](https://matplotlib.org/stable/project/license.html) |
| Pillow | 12.3.0 | MIT-CMU; bundled codecs, FreeType, libraqm and other native components have additional notices | [Pillow](https://github.com/python-pillow/Pillow) |
| pyqtgraph / meshio / platformdirs | 0.14.0 / 5.3.5 / 4.12.4 | MIT | [pyqtgraph](https://github.com/pyqtgraph/pyqtgraph), [meshio](https://github.com/nschloe/meshio), [platformdirs](https://github.com/tox-dev/platformdirs) |
| threadpoolctl / psutil | 3.7.0 / 7.2.2 | BSD-3-Clause | [threadpoolctl](https://github.com/joblib/threadpoolctl), [psutil](https://github.com/giampaolo/psutil) |
| CPython | 3.12.14 | Python-2.0 / PSF license history and separate bundled component notices | [CPython](https://www.python.org/downloads/source/) |
| PyInstaller bootloader | 6.22.3 | GPL-2.0-or-later WITH Bootloader-exception; some tool source Apache-2.0 | [License](https://pyinstaller.org/en/stable/license.html) |
| Microsoft runtime | Per candidate DLL version | Microsoft redistributable terms; not open-source/MPL | [Redistribution](https://learn.microsoft.com/visualstudio/releases/2022/redistribution) |

## Native and Qt inspection

Do not apply the license of a Python wrapper to every DLL it carries. Inspect
Gmsh's native build (including any Netgen, METIS, OpenCASCADE and FLTK code),
KLayout's expat/curl/png/zlib/pthreads DLLs, OpenBLAS/LAPACK/runtime code, GEOS,
Matplotlib FreeType/Qhull, Pillow codecs, Python SSL/compression/database libraries,
and Qt's embedded third-party code independently. Missing notices or exact source
are blockers, not `MPL-2.0` entries.

The desktop uses Qt Core, Gui, Widgets, Network, OpenGL, OpenGLWidgets, Svg and
SvgWidgets. Qt Test may be used by validation. Their open-source alternatives and
embedded licenses differ; follow the exact module notices. The build excludes
unused QML/Quick, Pdf, WebEngine and virtual-keyboard plugins/libraries, then checks
actual contents. Qt Virtual Keyboard's open-source route is GPL-3.0-only; it must
never be mislabeled LGPL merely because PySide6 metadata lists LGPL.

Qt copyright includes The Qt Company Ltd. and the individual contributors named
in its source. Gmsh is copyright C. Geuzaine and J.-F. Remacle and contributors;
KLayout is copyright Matthias Koefferlein and contributors. NumPy/SciPy and
Matplotlib retain their respective developer/contributor and bundled-code notices.
The collector preserves their full wheel notices without replacing authorship.

## Academic acknowledgment

C. Geuzaine and J.-F. Remacle, *Gmsh: a three-dimensional finite element mesh
generator with built-in pre- and post-processing facilities*, International
Journal for Numerical Methods in Engineering 79(11), 1309–1331 (2009).
[Upstream publication and software](https://gmsh.info/).
