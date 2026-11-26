# Compiles the native parts for 32-bit and 64-bit games into build\x86 and build\x64.
# Needs Visual Studio Build Tools with the C++ workload.
$ErrorActionPreference = 'Stop'
$root = $PSScriptRoot

$vswhere = "${env:ProgramFiles(x86)}\Microsoft Visual Studio\Installer\vswhere.exe"
if (-not (Test-Path $vswhere)) { throw "vswhere.exe not found; install Visual Studio Build Tools." }
$vs = & $vswhere -latest -products * -requires Microsoft.VisualStudio.Component.VC.Tools.x86.x64 -property installationPath
if (-not $vs) { throw "No Visual Studio installation with C++ tools found." }
$vcvars = Join-Path $vs 'VC\Auxiliary\Build\vcvarsall.bat'

foreach ($arch in 'x86', 'x64') {
    $out = Join-Path $root "build\$arch"
    New-Item -ItemType Directory -Force $out | Out-Null
    $script = @"
@echo off
call "$vcvars" $arch >nul || exit /b 1
cd /d "$out" || exit /b 1
cl /nologo /O2 /W3 /MT /D_CRT_SECURE_NO_WARNINGS /LD "$root\native\ffclock.c" /Fe:ffclock.dll /link winmm.lib || exit /b 1
cl /nologo /O2 /W3 /MT /D_CRT_SECURE_NO_WARNINGS "$root\native\ffinject.c" /Fe:ffinject.exe || exit /b 1
cl /nologo /O2 /W3 /MT /DUNICODE /D_UNICODE "$root\native\ticker.c" /Fe:ticker.exe /link user32.lib gdi32.lib winmm.lib shell32.lib || exit /b 1
"@
    $batch = Join-Path $out 'compile.cmd'
    Set-Content -Path $batch -Value $script -Encoding ascii
    & cmd.exe /d /c $batch
    if ($LASTEXITCODE -ne 0) { throw "$arch build failed" }
    Write-Host "built $arch"
}
