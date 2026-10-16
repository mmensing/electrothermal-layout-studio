# SPDX-License-Identifier: MPL-2.0
# Build the unchanged Gmsh source used by the audited Windows application profile.
[CmdletBinding()]
param(
    [Parameter(Mandatory)][string] $SourceDirectory,
    [Parameter(Mandatory)][string] $ToolchainBin,
    [Parameter(Mandatory)][string] $BuildDirectory,
    [ValidateRange(1,64)][int] $Jobs = 4
)
$ErrorActionPreference = 'Stop'
$sourcePath = (Resolve-Path -LiteralPath $SourceDirectory).Path
$compilerPath = (Resolve-Path -LiteralPath $ToolchainBin).Path
$buildPath = [IO.Path]::GetFullPath($BuildDirectory)
if (($sourcePath + $compilerPath + $buildPath) -match '\s') {
    throw 'Use paths without whitespace for this recorded MinGW build recipe.'
}
foreach ($required in @('LICENSE.txt', 'CMakeLists.txt', 'api/gmsh.py')) {
    if (-not (Test-Path -LiteralPath (Join-Path $sourcePath $required))) { throw "Missing Gmsh source input: $required" }
}
foreach ($required in @('gcc.exe', 'g++.exe', 'cmake.exe', 'mingw32-make.exe')) {
    if (-not (Test-Path -LiteralPath (Join-Path $compilerPath $required))) { throw "Missing toolchain program: $required" }
}
if (-not (Select-String -LiteralPath (Join-Path $sourcePath 'api/gmsh.py') -SimpleMatch 'GMSH_API_VERSION = "4.15.2"' -Quiet)) {
    throw 'This recipe is pinned to Gmsh 4.15.2.'
}
$priorPath = $env:PATH
try {
    $env:PATH = "$compilerPath;$priorPath"
    $sourceMap = $sourcePath.Replace('\','/')
    $buildMap = $buildPath.Replace('\','/')
    $mappedFlags = "-ffile-prefix-map=$sourceMap=gmsh-source -fdebug-prefix-map=$sourceMap=gmsh-source -fmacro-prefix-map=$sourceMap=gmsh-source -ffile-prefix-map=$buildMap=gmsh-build -fdebug-prefix-map=$buildMap=gmsh-build -fmacro-prefix-map=$buildMap=gmsh-build"
    $arguments = @('-S', $sourcePath, '-B', $buildPath, '-G', 'MinGW Makefiles',
        '-DCMAKE_BUILD_TYPE=Release', '-DENABLE_BUILD_SHARED=ON', '-DGMSH_RELEASE=ON',
        '-DENABLE_OPENMP=ON', '-DENABLE_UNTANGLE=OFF', '-DENABLE_HXT=OFF', '-DENABLE_OCC=OFF', '-DENABLE_OCC_CAF=OFF',
        '-DENABLE_FLTK=OFF', '-DENABLE_GRAPHICS=OFF', '-DENABLE_POST=ON',
        '-DENABLE_PLUGINS=OFF', '-DENABLE_PETSC=OFF', '-DENABLE_SLEPC=OFF',
        '-DENABLE_MUMPS=OFF', '-DENABLE_MED=OFF', '-DENABLE_CGNS=OFF',
        '-DENABLE_CAIRO=OFF', '-DENABLE_MMG=OFF', '-DENABLE_GMP=OFF',
        '-DENABLE_GETDP=OFF', '-DENABLE_P4EST=OFF', '-DENABLE_POPPLER=OFF',
        '-DENABLE_MPEG_ENCODE=OFF', '-DENABLE_ONELAB=OFF', '-DENABLE_ONELAB_METAMODEL=OFF',
        '-DENABLE_OSMESA=OFF', '-DENABLE_TESTS=OFF', '-DENABLE_BLAS_LAPACK=OFF',
        '-DENABLE_SYSTEM_CONTRIB=OFF', '-DGMSH_HOST=Windows-x86_64', '-DGMSH_PACKAGER=mmensing',
        '-DCMAKE_POLICY_VERSION_MINIMUM=3.5', "-DCMAKE_C_FLAGS=$mappedFlags", "-DCMAKE_CXX_FLAGS=$mappedFlags",
        '-DCMAKE_SHARED_LINKER_FLAGS=-static -static-libgcc -static-libstdc++')
    & (Join-Path $compilerPath 'cmake.exe') @arguments
    if ($LASTEXITCODE -ne 0) { throw "Gmsh configure failed: $LASTEXITCODE" }
    & (Join-Path $compilerPath 'cmake.exe') --build $buildPath --target shared --parallel $Jobs
    if ($LASTEXITCODE -ne 0) { throw "Gmsh build failed: $LASTEXITCODE" }
    $runtimePath = Join-Path $buildPath 'runtime'
    New-Item -ItemType Directory -Force -Path $runtimePath | Out-Null
    Copy-Item -LiteralPath (Join-Path $buildPath 'gmsh.dll') -Destination (Join-Path $runtimePath 'gmsh-4.15.dll')
    Copy-Item -LiteralPath (Join-Path $sourcePath 'api/gmsh.py') -Destination (Join-Path $runtimePath 'gmsh.py')
    $dll = Get-Item -LiteralPath (Join-Path $runtimePath 'gmsh-4.15.dll')
    $receipt = [ordered]@{
        schema_version = 1; component = 'Gmsh'; version = '4.15.2'
        source_sha256 = 'be3f66f225d27ba9fa014f07e83169285da8a051b0e8ab7103d88066b39bdd3e'
        compiler = ((& (Join-Path $compilerPath 'gcc.exe') --version)[0])
        compiler_distribution = 'w64devkit 2.10.1'; source_patches = @()
        filename = $dll.Name; bytes = $dll.Length
        sha256 = (Get-FileHash -LiteralPath $dll.FullName -Algorithm SHA256).Hash.ToLowerInvariant()
        bit_reproducibility_claimed = $false
        verification_required = 'Inspect imports/privacy and run the complete ETLayout source, numerical and packaged-workflow suites before release.'
    }
    $receipt | ConvertTo-Json -Depth 8 | Set-Content -Encoding utf8 -LiteralPath (Join-Path $runtimePath 'build-receipt.json')
    Write-Output "Built runtime directory: $runtimePath"
} finally {
    $env:PATH = $priorPath
}

