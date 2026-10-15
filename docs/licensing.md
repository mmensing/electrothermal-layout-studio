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

## Binary publication remains blocked

`packaging/binary-publication-policy.json` is intentionally false. Local candidate
builds and verification are allowed. No current public binary release is promised.
The release preflight must reject publication until an owner-reviewed compliance
record establishes, for the exact candidate:

- Complete corresponding source and build/install information for bundled GPL
  and LGPL components, including wheel patches, native subcomponents and exact
  revisions. A metadata license string, download URL, source snapshot of only this
  application, or passing test is insufficient.
- Actual module/plugin/native/font contents, verbatim copyright notices, license
  texts and applicable exceptions. All unresolved inventory entries must be
  reviewed. Qt modules have different licenses; installed packages are not proof
  of what a frozen artifact contains.
- A suitable LGPL library replacement mechanism and the required debugging,
  modification and relinking information. An onedir layout alone does not prove
  compliance. Do not prohibit reverse engineering for debugging modifications.
- The selected MPL/GPL larger-work distribution route and any Microsoft runtime
  redistribution conditions.

The present gaps concern distribution evidence, not removing the application's
meshing, geometry, plotting or desktop functionality.

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
