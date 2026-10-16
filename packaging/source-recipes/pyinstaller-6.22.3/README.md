# PyInstaller 6.22.3 Windows bootloaders

The Windows profile builds `run.exe` and `runw.exe` from the exact PyInstaller
preferred source delivered in the corresponding-source bundle. The unshipped
precompiled upstream bootloaders are omitted. No PyInstaller C source is patched.
The console and windowed loaders use w64devkit 2.10.1 / GCC 16.2.0 rather than
the upstream wheel's static Microsoft CRT build.

Extract the catalogued source archive into a new directory, then run:

```powershell
./build-windows.ps1 -SourceDirectory C:/build/pyinstaller-6.22.3 `
  -ToolchainBin C:/tools/w64devkit/bin -PythonInterpreter C:/python/python.exe
```

The recipe uses upstream Waf, x64 Unicode entry points, four build jobs by
default, GCC's normal optimization and warning checks, explicit GNU C17, source
prefix maps and `-static-libgcc`. The C runtime is the Windows-supplied
`msvcrt.dll`; no Microsoft runtime library is copied or linked statically.
Bundled zlib, GNU runtime code and MinGW-w64 runtime retain their separate
licenses, notices and source provenance. The GCC runtime exception applies to
eligible compiler-generated runtime combinations; it does not relicense other
components. The compiler itself is a build prerequisite and is not shipped.

Outputs remain under the extracted source's `PyInstaller/bootloader` directory.
The recipe never installs or replaces environment bootloaders. A maintainer
must back up the build environment's existing loaders, review the exact output
hashes/imports, and use the corresponding-source/native profile before freezing.
The exact destination in the selected isolated Windows build environment is
`Lib/site-packages/PyInstaller/bootloader/Windows-64bit-intel/`. Locate it using
that environment's interpreter:

```powershell
python -c "from importlib.metadata import distribution; print(distribution('pyinstaller').locate_file('PyInstaller/bootloader/Windows-64bit-intel'))"
```

After separately backing up that directory, copy only the rebuilt `run.exe` and
`runw.exe` from the same relative directory under the extracted source into that
reported destination. Leave the debug loaders and unrelated environments intact.
The release builder independently rejects loader bytes that disagree with the
reviewed catalog; a new independent build needs its own reviewed output hashes.

Build timestamps and toolchain details can change output hashes; bit-for-bit
reproducibility is not claimed. The published profile binds the actual selected
output hashes, raw `.text` size/hash and complete direct-import signature.
Freezing may replace PE resources and append the Python archive; code/import
correspondence and both native entry points must pass validation afterwards.

See the complete [licensing guide](../../../docs/licensing.md), retained
[PyInstaller license](../../license-texts/PyInstaller-COPYING.txt),
[GNU runtime notices](../../license-texts/GCC-runtime-notices.txt) and
[MinGW-w64 notice](../../license-texts/MinGW-w64-runtime.txt).
