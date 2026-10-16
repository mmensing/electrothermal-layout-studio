# Gmsh 4.15.2 Windows source build

This recipe builds the audited source subset from the pinned upstream archive, SHA-256
`be3f66f225d27ba9fa014f07e83169285da8a051b0e8ab7103d88066b39bdd3e`.
Use the exact archive listed in `packaging/third-party-sources.json`; retain its
`LICENSE.txt` and all bundled-library source and notices. The Gmsh exception
names Netgen, METIS, OpenCASCADE and ParaView; it is not a general linking exception.
The combined desktop distribution follows the GPLv3 route described in
`docs/compliance-sources.md`.

The validated compiler is portable w64devkit 2.10.1, GCC 16.2.0 and CMake 4.4.4.
The downloaded `.7z.exe` was extracted as an archive rather than executed as an
installer. No compiler is installed globally. Paths in this recipe must not
contain whitespace. Invoke PowerShell with explicit local paths:

```powershell
./packaging/source-recipes/gmsh-4.15.2/build-windows.ps1 `
  -SourceDirectory C:/build/gmsh-4.15.2-source `
  -ToolchainBin C:/build/w64devkit/bin `
  -BuildDirectory C:/build/gmsh-out -Jobs 4
```

The resulting `runtime/` contains the unchanged generated Python API and renamed
`gmsh-4.15.dll`. Keep them together when testing. The recipe preserves the application's supported planar meshing algorithms,
parser, postprocessing and OpenMP;
Post is required by the bundled quadrilateral meshing implementation. The
application uses the built-in `model.geo` kernel and conforming planar triangles.
It does not call the OpenCASCADE kernel or Gmsh GUI, PETSc, MED or CGNS interfaces;
these optional dependencies are disabled. No solver model or acceptance tolerance
is changed. The curated source archive omits unused Untangle, HXT and MPEG-encode code with restricted historical notices. ENABLE_UNTANGLE, ENABLE_HXT and ENABLE_MPEG_ENCODE are explicitly off. The application does not use tetrahedral HXT/Untangle operations. All retained source and build inputs are byte-unchanged; see the curation receipt.

GCC/libstdc++/OpenMP/winpthreads are statically linked. Verify the final PE imports;
the validated DLL imported only Windows system DLLs. Matching MinGW runtime
source and GCC Runtime Library Exception notices are included in the source set.
The general-purpose GCC compiler is an unshipped build prerequisite: its exact
version, source archive hash and upstream reference are recorded in the catalog,
rather than included as an application corresponding-source asset.

The recorded recipe maps local source/build roots to neutral paths. Timestamps
and toolchain environment can still change output hashes; byte-identical
reproduction is not claimed. Validate a newly built DLL's hash and record it in a
reviewed build manifest before packaging it. The catalog's approved hash identifies
the tested release candidate, not every permissible local rebuild.
