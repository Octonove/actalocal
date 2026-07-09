# Genera el instalador unico de ActaLocal con Inno Setup (requiere ISCC.exe).
#
# Por defecto RECONSTRUYE el dist (build.ps1) para que el instalador refleje
# siempre el codigo actual. Usa -SkipBuild para reutilizar un dist ya fresco.
# La version se toma de actalocal\__init__.py (APP_VERSION): fuente unica.
param([switch]$SkipBuild)

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot

$distExe = Join-Path $root "dist\ActaLocal\ActaLocal.exe"
if ($SkipBuild -and (Test-Path $distExe)) {
    Write-Host "Reutilizando dist existente (-SkipBuild)." -ForegroundColor DarkGray
} else {
    Write-Host "== Reconstruyendo dist (build.ps1) ==" -ForegroundColor Cyan
    & (Join-Path $PSScriptRoot "build.ps1")
    if ($LASTEXITCODE -ne 0) { Write-Host "Fallo el build." -ForegroundColor Red; exit 1 }
}
if (-not (Test-Path $distExe)) {
    Write-Host "No existe el dist tras el build; abortando." -ForegroundColor Red; exit 1
}

$initPy = Join-Path $root "actalocal\__init__.py"
$m = Select-String -Path $initPy -Pattern 'APP_VERSION\s*=\s*"([^"]+)"'
if (-not $m) {
    Write-Host "ERROR: no se encontro APP_VERSION en $initPy." -ForegroundColor Red
    exit 1
}
$ver = $m.Matches[0].Groups[1].Value
Write-Host "Version (APP_VERSION): $ver" -ForegroundColor Green

$iscc = (Get-Command ISCC.exe -ErrorAction SilentlyContinue).Source
if (-not $iscc) {
    foreach ($p in @("${env:ProgramFiles(x86)}\Inno Setup 6\ISCC.exe",
                     "$env:ProgramFiles\Inno Setup 6\ISCC.exe",
                     "$env:LOCALAPPDATA\Programs\Inno Setup 6\ISCC.exe")) {
        if (Test-Path $p) { $iscc = $p; break }
    }
}
if (-not $iscc) {
    Write-Host "No se encontro Inno Setup (ISCC.exe). Instalalo con:" -ForegroundColor Red
    Write-Host "  winget install JRSoftware.InnoSetup --source winget"
    exit 1
}

Write-Host "Compilando instalador con: $iscc" -ForegroundColor Cyan
& $iscc "/DMyAppVersion=$ver" (Join-Path $PSScriptRoot "ActaLocal.iss")
if ($LASTEXITCODE -eq 0) {
    Write-Host "`nInstalador: $(Join-Path $root ("installer\ActaLocal-Setup-$ver.exe"))" -ForegroundColor Green
} else {
    Write-Host "Fallo la creacion del instalador (codigo $LASTEXITCODE)." -ForegroundColor Red
    exit $LASTEXITCODE
}
