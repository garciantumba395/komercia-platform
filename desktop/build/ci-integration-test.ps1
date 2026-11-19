<#
.SYNOPSIS
  Test d'intégration de l'installateur sur un VRAI Windows (runner GitHub Actions windows-latest).

  Installation silencieuse -> démarrage par la tâche planifiée -> /health/ -> /app/connexion/ -> inscription
  (API) -> vente (API) -> PostgreSQL n'écoute QUE sur 127.0.0.1 -> droits des secrets -> arrêt -> mise à jour
  par-dessus -> désinstallation (données conservées) -> réinstallation (données retrouvées) -> désinstallation
  avec suppression des données. Toute étape qui échoue arrête le script avec un code non nul.
#>
[CmdletBinding()]
param(
    [Parameter(Mandatory)][string]$Setup,
    [int]$Port = 8000,
    [string]$LogDir = (Join-Path $PSScriptRoot 'ci-logs')
)

$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'
$InstallDir = Join-Path $env:ProgramFiles 'Komersia'
$DataDir = Join-Path $env:ProgramData 'Komersia'
$Base = "http://localhost:$Port"
$Phone = '+243990000123'
$Password = 'Motdepasse!2026x'
New-Item -ItemType Directory -Force -Path $LogDir | Out-Null

function Step($msg) { Write-Host "`n=== $msg" -ForegroundColor Cyan }
function Fail($msg) { Write-Host "::error::$msg"; throw $msg }
function Assert($cond, $msg) { if (-not $cond) { Fail $msg } }

function Save-Logs {
    # Journaux de l'application et de PostgreSQL : publiés en artefact même en cas d'échec.
    foreach ($f in 'logs', 'config\runtime.json', 'ADRESSE.txt') {
        $src = Join-Path $DataDir $f
        if (Test-Path $src) { Copy-Item -Recurse -Force $src $LogDir -ErrorAction SilentlyContinue }
    }
}

function Invoke-Installer([string[]]$ExtraArgs, [string]$LogName) {
    $args1 = @('/VERYSILENT', '/SUPPRESSMSGBOXES', '/NORESTART', "/LOG=$LogDir\$LogName") + $ExtraArgs
    $p = Start-Process -FilePath $Setup -ArgumentList $args1 -Wait -PassThru
    Assert ($p.ExitCode -eq 0) "L'installateur a échoué (code $($p.ExitCode)) : voir $LogName"
}

function Invoke-Uninstaller([string[]]$ExtraArgs, [string]$LogName) {
    $un = Join-Path $InstallDir 'unins000.exe'
    Assert (Test-Path $un) "Désinstalleur introuvable : $un"
    $args1 = @('/VERYSILENT', '/SUPPRESSMSGBOXES', '/NORESTART', "/LOG=$LogDir\$LogName") + $ExtraArgs
    $p = Start-Process -FilePath $un -ArgumentList $args1 -Wait -PassThru
    Assert ($p.ExitCode -eq 0) "Le désinstalleur a échoué (code $($p.ExitCode))"
}

function Wait-Healthy([int]$TimeoutSec = 300) {
    $deadline = (Get-Date).AddSeconds($TimeoutSec)
    while ((Get-Date) -lt $deadline) {
        try {
            $r = Invoke-WebRequest -UseBasicParsing -Uri "$Base/health/" -TimeoutSec 5
            if ($r.StatusCode -eq 200) { return }
        }
        catch { Start-Sleep -Seconds 2 }
    }
    Save-Logs
    $tail = if (Test-Path "$LogDir\logs\komersia.log") { (Get-Content "$LogDir\logs\komersia.log" -Tail 40) -join "`n" } else { '(pas de journal)' }
    Fail "Komersia ne répond pas sur $Base/health/ après $TimeoutSec s.`n$tail"
}

function Wait-Stopped([int]$TimeoutSec = 120) {
    $deadline = (Get-Date).AddSeconds($TimeoutSec)
    while ((Get-Date) -lt $deadline) {
        try { Invoke-WebRequest -UseBasicParsing -Uri "$Base/health/" -TimeoutSec 2 | Out-Null; Start-Sleep -Seconds 2 }
        catch {
            $left = @(Get-Process -ErrorAction SilentlyContinue | Where-Object {
                    $_.Path -and $_.Path.StartsWith("$InstallDir\", [StringComparison]::OrdinalIgnoreCase) })
            if ($left.Count -eq 0) { return }
            Start-Sleep -Seconds 1
        }
    }
    Fail 'Komersia ne s''est pas arrêté (ou des processus de l''installation tournent encore).'
}

function Invoke-Api([string]$Method, [string]$Path, $Body = $null, [string]$Token = $null) {
    $h = @{}
    if ($Token) { $h['Authorization'] = "Token $Token" }
    $params = @{ Method = $Method; Uri = "$Base$Path"; Headers = $h; UseBasicParsing = $true }
    if ($null -ne $Body) { $params['Body'] = ($Body | ConvertTo-Json -Depth 6); $params['ContentType'] = 'application/json' }
    try {
        $r = Invoke-WebRequest @params
        return [pscustomobject]@{ Status = [int]$r.StatusCode; Json = ($r.Content | ConvertFrom-Json) }
    }
    catch {
        if (-not $_.Exception.Response) { throw }
        $code = [int]$_.Exception.Response.StatusCode
        return [pscustomobject]@{ Status = $code; Json = $null }
    }
}

function Test-Login {
    $r = Invoke-Api 'POST' '/api/v1/auth/login/' @{ phone = $Phone; password = $Password }
    Assert ($r.Status -eq 200) "Connexion impossible après redémarrage/mise à jour (HTTP $($r.Status)) : les données ont-elles été conservées ?"
}

function Stop-Komersia {
    $exe = Join-Path $InstallDir 'KomersiaServer.exe'
    & $exe --stop
    Assert ($LASTEXITCODE -eq 0) "KomersiaServer --stop a échoué (code $LASTEXITCODE)"
    Wait-Stopped
}

try {
    Assert (Test-Path $Setup) "Installateur introuvable : $Setup"
    $exe = Join-Path $InstallDir 'KomersiaServer.exe'

    Step '1. Installation silencieuse'
    Invoke-Installer @("/PORT=$Port") 'setup.log'
    Assert (Test-Path $exe) "KomersiaServer.exe absent de $InstallDir"
    Assert (Test-Path (Join-Path $InstallDir 'pgsql\bin\postgres.exe')) 'PostgreSQL embarqué absent'
    Assert ((& $exe --version) -match '^\d+\.\d+\.\d+') 'KomersiaServer --version inattendu'

    Step '2. Tâche planifiée « au démarrage » (sans session ouverte) et pare-feu'
    $task = Get-ScheduledTask -TaskName 'Komersia' -ErrorAction SilentlyContinue
    Assert ($null -ne $task) 'Tâche planifiée « Komersia » absente'
    Assert ($task.Triggers[0].CimClass.CimClassName -eq 'MSFT_TaskBootTrigger') 'Le déclencheur doit être « au démarrage »'
    Assert ($task.Principal.UserId -match 'NETWORK SERVICE|S-1-5-20') "Compte de la tâche inattendu : $($task.Principal.UserId)"
    $rule = Get-NetFirewallRule -DisplayName 'Komersia' -ErrorAction SilentlyContinue
    Assert ($null -ne $rule) 'Règle de pare-feu « Komersia » absente'
    Assert (("$($rule.Profile)" -eq 'Private') -and ("$($rule.Direction)" -eq 'Inbound') -and ("$($rule.Action)" -eq 'Allow')) `
        "Règle de pare-feu inattendue : profil=$($rule.Profile) sens=$($rule.Direction) action=$($rule.Action)"
    $portFilter = ($rule | Get-NetFirewallPortFilter).LocalPort
    Assert ($portFilter -contains "$Port-$($Port + 9)") "Ports du pare-feu inattendus : $portFilter"

    Step '3. Démarrage par la tâche planifiée : /health/ puis /app/connexion/'
    Wait-Healthy
    $page = Invoke-WebRequest -UseBasicParsing -Uri "$Base/app/connexion/"
    Assert ($page.StatusCode -eq 200) "/app/connexion/ a répondu $($page.StatusCode)"
    $ops = Invoke-Api 'GET' '/ops/'
    Assert ($ops.Status -eq 404) "/ops/ ne doit pas exister en édition locale (HTTP $($ops.Status))"

    Step '4. Inscription (API) puis vente : base + RLS + application de bout en bout'
    $reg = Invoke-Api 'POST' '/api/v1/auth/register/' @{
        full_name = 'Marie Test'; phone = $Phone; password = $Password; business_name = 'Boutique CI'; base_currency = 'CDF' }
    Assert ($reg.Status -eq 201) "Inscription : HTTP $($reg.Status)"
    $token = $reg.Json.token
    $storeId = $reg.Json.store.id
    Assert ($token -and $storeId) 'Réponse d''inscription incomplète'
    $second = Invoke-Api 'POST' '/api/v1/auth/register/' @{
        full_name = 'Autre'; phone = '+243990000999'; password = $Password; business_name = 'Autre' }
    Assert ($second.Status -eq 403) "Une 2e entreprise doit être refusée (HTTP $($second.Status))"
    $prod = Invoke-Api 'POST' '/api/v1/catalog/products/' @{
        name = 'Savon'; sku = 'SAV-001'; price_minor = 1500; currency = 'CDF'; track_stock = $false } $token
    Assert ($prod.Status -eq 201) "Création du produit : HTTP $($prod.Status)"
    $sale = Invoke-Api 'POST' '/api/v1/sales/' @{
        client_id = [guid]::NewGuid().ToString(); store = $storeId
        items = @(@{ product = $prod.Json.id; quantity = '2' })
        payments = @(@{ method = 'cash'; amount_minor = 3000; currency = 'CDF' }) } $token
    Assert ($sale.Status -eq 201) "Vente : HTTP $($sale.Status)"
    $list = Invoke-Api 'GET' '/api/v1/sales/' $null $token
    Assert (($list.Status -eq 200) -and ($list.Json.count -eq 1)) 'La vente n''apparaît pas dans la liste'

    Step '5. PostgreSQL n''écoute QUE sur 127.0.0.1'
    $pgProcs = @(Get-Process -Name postgres -ErrorAction SilentlyContinue |
            Where-Object { $_.Path -and $_.Path.StartsWith("$InstallDir\", [StringComparison]::OrdinalIgnoreCase) })
    Assert ($pgProcs.Count -gt 0) 'Aucun processus postgres.exe de l''installation'
    $pgIds = $pgProcs.Id
    $listeners = @(Get-NetTCPConnection -State Listen -ErrorAction SilentlyContinue | Where-Object { $pgIds -contains $_.OwningProcess })
    Assert ($listeners.Count -gt 0) 'PostgreSQL n''écoute sur aucun port ?'
    foreach ($l in $listeners) {
        Write-Host "  postgres écoute sur $($l.LocalAddress):$($l.LocalPort)"
        Assert ($l.LocalAddress -eq '127.0.0.1') "PostgreSQL écoute sur $($l.LocalAddress):$($l.LocalPort) : interdit (127.0.0.1 uniquement)"
    }
    $web = @(Get-NetTCPConnection -State Listen -LocalPort $Port -ErrorAction SilentlyContinue)
    Assert ($web.Count -gt 0) "Rien n'écoute sur le port web $Port"
    Write-Host "  serveur web sur $($web[0].LocalAddress):$Port"
    $owners = Get-CimInstance Win32_Process -Filter "Name='postgres.exe'" | ForEach-Object { (Invoke-CimMethod -InputObject $_ -MethodName GetOwner).User } | Sort-Object -Unique
    Assert ($owners -contains 'NETWORK SERVICE') "PostgreSQL doit tourner sous NETWORK SERVICE (propriétaires : $owners)"

    Step '6. Secrets illisibles par les autres comptes'
    $acl = (& icacls (Join-Path $DataDir 'config\secrets.json')) -join "`n"
    Write-Host $acl
    Assert ($acl -notmatch 'Users|Everyone|Authenticated Users|BUILTIN\\Users') 'secrets.json est lisible par des comptes non autorisés'
    $aclPg = (& icacls (Join-Path $DataDir 'pgdata')) -join "`n"
    Assert ($aclPg -notmatch 'BUILTIN\\Users|Everyone|Authenticated Users') 'pgdata est lisible par tous les utilisateurs'
    Assert (Test-Path (Join-Path $DataDir 'ADRESSE.txt')) 'ADRESSE.txt absent'
    & $exe --status
    Assert ($LASTEXITCODE -eq 0) "--status : code $LASTEXITCODE"

    Step '7. Arrêt propre'
    Stop-Komersia
    & $exe --status | Out-Null
    Assert ($LASTEXITCODE -eq 3) "--status après arrêt : attendu 3, obtenu $LASTEXITCODE"

    Step '8. Redémarrage par la tâche : les données sont retrouvées'
    Start-ScheduledTask -TaskName 'Komersia'
    Wait-Healthy 120
    Test-Login
    Stop-Komersia

    Step '9. Mise à jour par-dessus (même installateur) : données intactes'
    Invoke-Installer @() 'setup-upgrade.log'
    Wait-Healthy 180
    Test-Login
    $list = Invoke-Api 'GET' '/api/v1/sales/' $null $token
    Assert ($list.Json.count -eq 1) 'La vente a disparu après la mise à jour'
    Stop-Komersia

    Step '10. Désinstallation (les données sont CONSERVÉES)'
    Invoke-Uninstaller @() 'uninstall.log'
    Assert (-not (Test-Path $exe)) 'KomersiaServer.exe est encore installé'
    Assert ($null -eq (Get-ScheduledTask -TaskName 'Komersia' -ErrorAction SilentlyContinue)) 'La tâche planifiée existe encore'
    Assert ($null -eq (Get-NetFirewallRule -DisplayName 'Komersia' -ErrorAction SilentlyContinue)) 'La règle de pare-feu existe encore'
    Assert (Test-Path (Join-Path $DataDir 'pgdata\PG_VERSION')) 'Les données ont été supprimées alors qu''elles devaient être conservées'

    Step '11. Réinstallation : les données de la boutique sont retrouvées'
    Invoke-Installer @("/PORT=$Port") 'setup-reinstall.log'
    Wait-Healthy 240
    Test-Login
    Stop-Komersia

    Step '12. Désinstallation avec suppression des données'
    Invoke-Uninstaller @('/PURGEDATA=1') 'uninstall-purge.log'
    Assert (-not (Test-Path $DataDir)) "Le dossier de données existe encore : $DataDir"
    Write-Host "`nTOUS LES TESTS D'INTÉGRATION WINDOWS ONT RÉUSSI" -ForegroundColor Green
}
catch {
    Write-Host "::error::ÉCHEC : $($_.Exception.Message)"
    Save-Logs
    throw
}
finally {
    Save-Logs
}
