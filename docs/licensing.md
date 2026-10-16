# Licensing and redistribution

The original Electrothermal Layout Studio source edition is **MPL-2.0**. The
Python namespace and CLI remain `etlayout`. `LICENSE` is Mozilla's unmodified
license; source SPDX notices and `REUSE.toml` identify the covered material.
No Exhibit B incompatibility notice is attached. Existing copyright notices,
scientific citations and separately licensed material remain applicable.
The maintainer has authorized this license for the original project material.
Earlier recipients retain any GPL rights they already received.

When distributing modified covered source, retain notices, use MPL-2.0 for those
files and make their source available as the license requires. Contributions must
be eligible for the same grants; see [CONTRIBUTING](../CONTRIBUTING.md).
[MPL text](https://www.mozilla.org/en-US/MPL/2.0/) and
[Mozilla's FAQ](https://www.mozilla.org/en-US/MPL/2.0/FAQ/) explain the source and
executable obligations.

## Source edition and combined executables

The source repository and ordinary application wheel do not include third-party
Python wheels or native dependency libraries. Installing them obtains separately
licensed software. The source's MPL declaration does not make a complete frozen
application MPL-only.

In particular, Gmsh is GPL-2.0-or-later. Its exception concerns combinations with
the named Netgen, METIS, OpenCASCADE and ParaView code; it is **not** a general
permission to link arbitrary applications under any license. Before distributing
a qualifying GPL combined work, use the applicable MPL section 3.3 route: keep the
covered source available under MPL and additionally under the applicable secondary
GPL license, while satisfying that license for the larger work. Section 3.3 is
conditional, not a blanket dual-license declaration. Record the selected route and
exact covered versions with the candidate.

KLayout publishes a Python-script/import exception covering its Python packages;
that does not remove the GPL requirements on redistribution of KLayout itself or
reuse of its C++ code. Keep its exception with its GPL text.

## Windows distribution route

The reviewed Windows profile distributes the combined application under
**GPL-3.0-only**, using MPL section 3.3 for the original covered files. Those
files remain available under MPL-2.0 as well. Gmsh's named-library exception and
KLayout's Python-import exception are retained; neither is used as a general
exemption from distributing their corresponding source.

For actually bundled Qt/PySide/Shiboken modules, the distribution selects their
offered GPL-3.0 option. This is different from claiming LGPL shared-library
replacement compliance. For GEOS, the distribution retains the documented
LGPL 2.1 grant and uses the complete-source/relink option in section 6(a); no
later-version conversion is assumed. Matching library and application preferred
source, modifications, build/relink inputs and notices accompany the release.
Recipients may replace or modify the library and reverse engineer the combined
application to debug those modifications. GPL status alone does not discharge
these obligations. Permissive dependency and font notices remain intact.
See [the source manifest and recipes](compliance-sources.md)
and [THIRD_PARTY_NOTICES](../THIRD_PARTY_NOTICES.md).

A release must provide equivalent download access to exact corresponding source,
patches and build/install information with the object code (GPL section 6(d)). An
upstream homepage, this application's source alone, or an unfulfilled written
offer is insufficient. Release assets include the source catalog, archives and
build information, with exact hashes. This is a selected license route, not a
claim of bit-identical reproducibility or an MPL-only executable.

Microsoft's Visual C++ runtime is a **system prerequisite**. No Microsoft runtime
DLL or redistributable installer is included or executed by the package. Four
reviewed NumPy/GEOS import aliases are restored to the standard system runtime
name; the package includes exact before/after hashes and modification notices.
See [runtime installation and modifications](windows-runtime.md).

The application uses raster desktop/plotting views. Unused optional Mesa software
OpenGL, Matplotlib demonstration data and legacy AFM/PDF core-font metrics are
excluded after functional checks. All supported exports retain TrueType fonts;
these exclusions do not remove a supported visualization or calculation.

[`binary-publication-policy.json`](../packaging/binary-publication-policy.json)
records the approved profile and remaining blockers. A policy, build success or
SBOM alone never authorizes publication. Every release additionally needs exact
asset/privacy/native/font review, a clean tested public commit and the local
owner preflight. Future versions or different native inputs need a fresh review.

## Reproduce the inventory

`DEPENDENCIES.json` records exact locked Windows package versions and separates
runtime-environment dependencies from build/test tools. A package being installed
does not imply it is shipped. After building, generate artifact-specific evidence:

```powershell
python scripts/collect_licenses.py artifacts/licenses
python scripts/create_sbom.py --kind wheel --artifact dist/etlayout-VERSION-py3-none-any.whl --output artifacts/wheel-inventory
python scripts/create_sbom.py --kind frozen --artifact dist/ElectrothermalLayoutStudio --output artifacts/frozen-inventory
```

The scripts inspect files and archives without running candidate code. They record
file hashes, frozen Python module membership, native PE import/version information,
fonts, component evidence and unresolved entries. CycloneDX JSON and the detailed
manifest are complementary; neither asserts legal clearance. The native build
produces them automatically. Keep full evidence with candidate artifacts; retain
small maintained source notices here.

## Primary references

- [Gmsh license appendix](https://gmsh.info/doc/texinfo/#License)
- [KLayout license and Python exception](https://www.klayout.de/license.html)
- [Qt module licensing](https://doc.qt.io/qt-6/licensing.html),
  [Qt third-party components](https://doc.qt.io/qt-6/licenses-used-in-qt.html),
  [Qt for Python notices](https://doc.qt.io/qtforpython-6/licenses.html)
- [PyInstaller license and bootloader exception](https://pyinstaller.org/en/stable/license.html)
- [Matplotlib license, fonts and native dependencies](https://matplotlib.org/stable/project/license.html)
