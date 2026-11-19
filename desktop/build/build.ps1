<#
.SYNOPSIS
  Construit l'installateur Windows de Komersia : desktop\build\output\Komersia-Setup-<version>.exe

.DESCRIPTION
  1. télécharge les binaires PostgreSQL portables (zip EDB, version et somme SHA-256 figées dans postgres.json)
     et en garde bin, lib, share ;
  2. prépare l'environnement Python avec uv (dépendances figées par backend\uv.lock) ;
  3. construit KomersiaServer.exe avec PyInstaller (mode dossier) et le vérifie (--selftest) ;
  4. assemble le tout et compile komersia.iss avec Inno Setup (ISCC).

  La construction ÉCHOUE si la somme SHA-256 de PostgreSQL n'est pas renseignée ou ne correspond pas.

.PARAMETER Version
  Version de l'application (défaut : fichier desktop\VERSION).
.PARAMETER PrintPgHash
  Télécharge le zip PostgreSQL, affiche sa somme SHA-256 puis s'arrête (pour renseigner postgres.json).
.PARAMETER SkipInstaller
  S'arrête après PyInstaller et l'assemblage (sans Inno Setup).
#>
[CmdletBinding()]
param(
    [string]$Version,
    [switch]$PrintPgHash,
    [switch]$SkipInstaller
)

$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'   # Invoke-WebRequest est très lent avec la barre de progression

$BuildDir = $PSScriptRoot
$Desktop = Split-Path $BuildDir -Parent
$Root = Split-Path $Desktop -Parent
$Backend = Join-Path $Root 'backend'
$Cache = Join-Path $BuildDir 'cache'
$Work = Join-Path $BuildDir 'work'
$Dist = Join-Path $BuildDir 'dist'
$Stage = Join-Path $BuildDir 'stage'
$Output = Join-Path $BuildDir 'output'

function Write-Step($msg) { Write-Host "`n=== $msg" -ForegroundColor Cyan }

function Invoke-Native {
    param([Parameter(Mandatory)][string]$File, [string[]]$Arguments = @())
    Write-Host "> $File $($Arguments -join ' ')"
    & $File @Arguments
    if ($LASTEXITCODE -ne 0) { throw "Échec ($LASTEXITCODE) : $File $($Arguments -join ' ')" }
}

# --- Version ---------------------------------------------------------------------------------------------
if (-not $Version) { $Version = (Get-Content -Raw (Join-Path $Desktop 'VERSION')).Trim() }
if ($Version -notmatch '^\d+\.\d+\.\d+([.-][0-9A-Za-z.-]+)?$') { throw "Version invalide : '$Version' (attendu : 1.2.3)" }
Write-Host "Version Komersia : $Version"

# --- 1. PostgreSQL portable -------------------------------------------------------------------------------------
Write-Step 'PostgreSQL portable'
$pg = Get-Content -Raw -Encoding UTF8 (Join-Path $BuildDir 'postgres.json') | ConvertFrom-Json
$pgVersion = $pg.version
$pgUrl = "https://get.enterprisedb.com/postgresql/postgresql-$pgVersion-windows-x64-binaries.zip"
New-Item -ItemType Directory -Force -Path $Cache | Out-Null
$pgZip = Join-Path $Cache "postgresql-$pgVersion-windows-x64-binaries.zip"
if (-not (Test-Path $pgZip)) {
    Write-Host "Téléchargement : $pgUrl"
    $ok = $false
    foreach ($attempt in 1..3) {
        try { Invoke-WebRequest -Uri $pgUrl -OutFile $pgZip -UseBasicParsing; $ok = $true; break }
        catch { Write-Warning "Essai $attempt/3 : $($_.Exception.Message)"; Start-Sleep -Seconds (5 * $attempt) }
    }
    if (-not $ok) { throw "Téléchargement impossible : $pgUrl (vérifier que la version '$pgVersion' existe)." }
}
$actual = (Get-FileHash -Algorithm SHA256 $pgZip).Hash.ToLowerInvariant()
Write-Host "SHA-256 du zip PostgreSQL : $actual"
if ($PrintPgHash) {
    Write-Host "`nCopier cette somme dans desktop\build\postgres.json (champ sha256) : $actual"
    return
}
$expected = "$($pg.sha256)".Trim().ToLowerInvariant()
if (-not $expected) {
    throw "postgres.json : 'sha256' est vide. Renseigner la somme SHA-256 du zip PostgreSQL $pgVersion (calculée ici : $actual). Vérifiez-la sur la page de téléchargement EDB avant de la copier."
}
if ($actual -ne $expected) {
    Remove-Item -Force $pgZip
    throw "Somme SHA-256 de PostgreSQL différente de postgres.json (attendue $expected, obtenue $actual) : fichier supprimé, construction annulée."
}

$pgExtract = Join-Path $Work 'pg-extract'
if (Test-Path $pgExtract) { Remove-Item -Recurse -Force $pgExtract }
New-Item -ItemType Directory -Force -Path $pgExtract | Out-Null
Expand-Archive -Path $pgZip -DestinationPath $pgExtract
$pgRoot = Join-Path $pgExtract 'pgsql'
if (-not (Test-Path (Join-Path $pgRoot 'bin\postgres.exe'))) { throw "Zip PostgreSQL inattendu : pgsql\bin\postgres.exe introuvable." }

if (Test-Path $Stage) { Remove-Item -Recurse -Force $Stage }
$stagePg = Join-Path $Stage 'pgsql'
New-Item -ItemType Directory -Force -Path $stagePg | Out-Null
foreach ($d in 'bin', 'lib', 'share') { Copy-Item -Recurse (Join-Path $pgRoot $d) (Join-Path $stagePg $d) }  # pas pgAdmin, doc, include...
# Runtime Visual C++ « local à l'application » (le PC du client peut ne pas l'avoir installé).
foreach ($dll in 'vcruntime140.dll', 'vcruntime140_1.dll', 'msvcp140.dll') {
    $src = Join-Path $env:SystemRoot "System32\$dll"
    $dst = Join-Path $stagePg "bin\$dll"
    if ((Test-Path $src) -and -not (Test-Path $dst)) { Copy-Item $src $dst }
}
foreach ($exe in 'postgres', 'initdb', 'pg_ctl', 'pg_dump', 'pg_restore', 'psql') {
    if (-not (Test-Path (Join-Path $stagePg "bin\$exe.exe"))) { throw "Binaire PostgreSQL manquant : $exe.exe" }
}
# Fichier d'extension indispensable aux migrations (pg_trgm : recherche de produits)
if (-not (Test-Path (Join-Path $stagePg 'share\extension\pg_trgm.control')) -and
    -not (Test-Path (Join-Path $stagePg 'share\postgresql\extension\pg_trgm.control'))) {
    throw 'Extension pg_trgm absente du zip PostgreSQL.'
}

# --- 2. Python (uv) ------------------------------------------------------------------------------------
Write-Step 'Environnement Python (uv)'
Invoke-Native 'uv' @('sync', '--project', $Backend, '--frozen', '--no-dev')
Set-Content -Encoding UTF8 -Path (Join-Path $Desktop 'komersia_desktop\_build_info.py') -Value "VERSION = '$Version'"

# --- 3. PyInstaller -----------------------------------------------------------------------------------------
Write-Step 'PyInstaller'
$pyiVersion = (Get-Content -Raw (Join-Path $BuildDir 'pyinstaller-version.txt')).Trim()
foreach ($d in $Dist, (Join-Path $Work 'pyinstaller')) { if (Test-Path $d) { Remove-Item -Recurse -Force $d } }
Invoke-Native 'uv' @('run', '--project', $Backend, '--no-dev', '--frozen', '--with', "pyinstaller==$pyiVersion",
    'pyinstaller', (Join-Path $BuildDir 'komersia.spec'), '--noconfirm', '--distpath', $Dist,
    '--workpath', (Join-Path $Work 'pyinstaller'))
$appDir = Join-Path $Dist 'KomersiaServer'
$serverExe = Join-Path $appDir 'KomersiaServer.exe'
if (-not (Test-Path $serverExe)) { throw 'KomersiaServer.exe non produit.' }

Write-Step 'Auto-test de l''exécutable gelé'
$tmpData = Join-Path $Work 'selftest-data'
$env:KOMERSIA_DATA_DIR = $tmpData
$env:KOMERSIA_PG_BIN = Join-Path $stagePg 'bin'
$reported = (& $serverExe --version).Trim()
if ($reported -ne $Version) { throw "Version de l'exécutable ($reported) différente de $Version." }
Invoke-Native $serverExe @('--selftest')
Remove-Item Env:KOMERSIA_DATA_DIR, Env:KOMERSIA_PG_BIN

# --- 4. Assemblage + Inno Setup -------------------------------------------------------------------------
Write-Step 'Assemblage'
Copy-Item -Recurse $appDir (Join-Path $Stage 'app')
New-Item -ItemType Directory -Force -Path (Join-Path $Stage 'scripts') | Out-Null
Copy-Item (Join-Path $BuildDir 'komersia-service.ps1') (Join-Path $Stage 'scripts')
Copy-Item (Join-Path $BuildDir 'LISEZMOI.txt') $Stage
Write-Host ("Taille de l'installation : {0:N0} Mo" -f ((Get-ChildItem -Recurse $Stage | Measure-Object Length -Sum).Sum / 1MB))

if ($SkipInstaller) { Write-Host 'Installateur ignoré (-SkipInstaller).'; return }

Write-Step 'Inno Setup'
$iscc = @(
    "${env:ProgramFiles(x86)}\Inno Setup 6\ISCC.exe",
    "$env:ProgramFiles\Inno Setup 6\ISCC.exe"
) | Where-Object { Test-Path $_ } | Select-Object -First 1
if (-not $iscc) { $cmd = Get-Command ISCC.exe -ErrorAction SilentlyContinue; if ($cmd) { $iscc = $cmd.Source } }
if (-not $iscc) { throw 'Inno Setup (ISCC.exe) introuvable : choco install innosetup -y' }
New-Item -ItemType Directory -Force -Path $Output | Out-Null
Invoke-Native $iscc @('/Qp', "/DAppVersion=$Version", "/DStageDir=$Stage", "/DOutputDir=$Output",
    (Join-Path $BuildDir 'komersia.iss'))
$setup = Join-Path $Output "Komersia-Setup-$Version.exe"
if (-not (Test-Path $setup)) { throw "Installateur non produit : $setup" }
$hash = (Get-FileHash -Algorithm SHA256 $setup).Hash.ToLowerInvariant()
Set-Content -Encoding ASCII -Path "$setup.sha256" -Value "$hash  Komersia-Setup-$Version.exe"
Write-Host ("`nInstallateur : {0} ({1:N0} Mo)`nSHA-256 : {2}" -f $setup, ((Get-Item $setup).Length / 1MB), $hash)
