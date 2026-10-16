# Windows runtime prerequisite

The Windows package requires the **current supported Microsoft Visual C++ x64
v14 Redistributable**, obtained directly from [Microsoft's official download
page](https://learn.microsoft.com/en-us/cpp/windows/latest-supported-vc-redist).
Install it if the runtime is missing or outdated, then extract the complete
application folder and launch `ElectrothermalLayoutStudio.exe`.

You do not need the Visual Studio IDE to run this application. The application
neither downloads nor installs the runtime, accepts its terms, nor bundles the
Microsoft installer or runtime DLLs. Do not download individual DLLs from
third-party websites. The standard Microsoft runtime is serviced separately.

The release's native Windows 11 test host has runtime version **14.51.36247.0**.
The omitted wheel copies ranged from 14.40.33810.0 to 14.44.35229.0. These are
observed versions, not a claim to have determined the lowest compatible ABI
version. Microsoft requires a runtime at least as recent as the build tools
used and recommends the latest supported v14 package. A clean-machine test must
be recorded separately from tests on the build host.

## What the packaging process changes

Some upstream wheels rename their bundled `MSVCP140.dll` and rewrite native
consumers to import that private name. Installing Microsoft's standard runtime
alone cannot satisfy those renamed imports. The reviewed
[`packaging/windows_runtime.py`](../packaging/windows_runtime.py) step restores
the standard `msvcp140.dll` import in four unsigned x64 native files from the
locked NumPy 2.5.4 and Shapely 2.2.0 wheels:

- NumPy `_multiarray_umath` and `_pocketfft_umath`.
- Shapely's GEOS and GEOS C interface libraries.

The helper checks exact original and expected output hashes, PE architecture,
signature/bound-import state, import descriptors, code sections and every changed
byte. It changes only existing import-name storage and the PE checksum, preserving
executable code, function/ordinal imports and section layout. It then omits the
12 reviewed app-local runtime copies. Unknown versions, layouts or extra runtime
copies stop preparation before mutation. Existing prepared candidates are checked
again, making a `--skip-build` verification safe without repeating the rewrite.

Every candidate carries `WINDOWS_RUNTIME_MODIFICATIONS.json` and a readable
modification notice with dates, input/output hashes, changed byte spans and pinned
upstream wheel provenance. Original third-party copyright and license notices
remain. The matching source archive contains this helper so the modification can
be reproduced; the exact upstream GEOS sources and build information remain part
of its separate corresponding-source obligations.

Build through `scripts/build_windows.py`; it runs the step before native tests,
SBOM generation and archiving. Do not modify an existing published package or
assume an old SBOM still describes the changed files. For an isolated candidate,
the same transformation can be reproduced with:

```powershell
python packaging/windows_runtime.py path\to\ElectrothermalLayoutStudio
```

This runtime deployment choice does not establish complete binary licensing
clearance. [The licensing guide](licensing.md) covers the separate GPL, LGPL,
MPL and native/source obligations. Required release checks include real GUI,
worker and CLI solves, extracted-folder tests, exact modified-library hashes,
and evidence that runtime DLLs load from Windows' system directory rather than
a Python environment or another app on `PATH`.

Microsoft's [redistribution guidance](https://learn.microsoft.com/en-us/cpp/windows/redistributing-visual-cpp-files)
and [v14 compatibility guidance](https://learn.microsoft.com/en-us/cpp/porting/binary-compat-2015-2017)
describe the official deployment route. Qt also documents
[deployment without copying compiler runtime files](https://doc.qt.io/qt-6/windows-deployment.html).