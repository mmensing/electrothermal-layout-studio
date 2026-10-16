# SPDX-License-Identifier: MPL-2.0
# Build upstream PyInstaller C bootloaders using the reviewed GNU toolchain.
[CmdletBinding()]
param(
    [Parameter(Mandatory)][string] $SourceDirectory,
    [Parameter(Mandatory)][string] $ToolchainBin,
    [Parameter(Mandatory)][string] $PythonInterpreter,
    [ValidateRange(1,64)][int] $Jobs = 4
)
$ErrorActionPreference = 'Stop'
$sourcePath = (Resolve-Path -LiteralPath $SourceDirectory).Path
$compilerPath = (Resolve-Path -LiteralPath $ToolchainBin).Path
$pythonPath = (Resolve-Path -LiteralPath $PythonInterpreter).Path
if (($sourcePath + $compilerPath) -match '\s') {
    throw 'Use source and compiler paths without whitespace for this recorded Waf recipe.'
}
foreach ($required in @('bootloader/waf', 'bootloader/wscript', 'bootloader/src/pyi_main.c', 'bootloader/zlib/zlib.h', 'PyInstaller/__init__.py')) {
    if (-not (Test-Path -LiteralPath (Join-Path $sourcePath $required))) { throw "Missing preferred source input: $required" }
}
if (-not (Select-String -LiteralPath (Join-Path $sourcePath 'PyInstaller/__init__.py') -SimpleMatch "__version__ = '6.22.3'" -Quiet)) {
    throw 'This recipe requires PyInstaller 6.22.3 preferred source.'
}
$gcc = Join-Path $compilerPath 'gcc.exe'
if (-not (Test-Path -LiteralPath $gcc)) { throw 'GCC toolchain is missing.' }
$compilerVersion = (& $gcc --version)[0]
if ($LASTEXITCODE -ne 0 -or $compilerVersion -ne 'gcc.exe (GCC) 16.2.0') { throw 'This recorded profile requires GCC 16.2.0.' }
$previous = @{}
foreach ($name in @('PATH','CC','CFLAGS','LINKFLAGS')) { $previous[$name] = [Environment]::GetEnvironmentVariable($name, 'Process') }
try {
    $env:PATH = "$compilerPath;$env:SystemRoot\System32;$env:SystemRoot"
    $env:CC = $gcc
    $sourceMap = $sourcePath.Replace('\','/')
    $env:CFLAGS = "-std=gnu17 -ffile-prefix-map=$sourceMap=pyinstaller-source -fdebug-prefix-map=$sourceMap=pyinstaller-source -fmacro-prefix-map=$sourceMap=pyinstaller-source"
    $env:LINKFLAGS = '-static-libgcc -Wl,--no-insert-timestamp'
    Push-Location (Join-Path $sourcePath 'bootloader')
    try {
        & $pythonPath waf --gcc --target-arch=64bit --jobs=$Jobs all
        if ($LASTEXITCODE -ne 0) { throw "PyInstaller bootloader build failed: $LASTEXITCODE" }
    } finally { Pop-Location }
    foreach ($filename in @('run.exe','runw.exe')) {
        $path = Join-Path $sourcePath "PyInstaller/bootloader/Windows-64bit-intel/$filename"
        if (-not (Test-Path -LiteralPath $path)) { throw "Missing output: $filename" }
        Write-Output "$filename $((Get-FileHash -LiteralPath $path -Algorithm SHA256).Hash.ToLowerInvariant())"
    }
} finally {
    foreach ($name in $previous.Keys) { [Environment]::SetEnvironmentVariable($name, $previous[$name], 'Process') }
}
