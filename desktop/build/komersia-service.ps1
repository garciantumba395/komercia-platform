<#
.SYNOPSIS
  Intégration Windows de Komersia : tâche planifiée, pare-feu, droits sur le dossier de données.
  Appelé par l'installateur (komersia.iss) ET par la CI : ce qui est testé en CI est ce qui s'exécute chez le client.

.PARAMETER Action
  Install   : crée les dossiers et droits, settings.json (si absent), la tâche « Komersia », la règle de pare-feu.
  Start     : démarre la tâche.
  Stop      : arrêt propre (KomersiaServer --stop), puis arrêt forcé des processus restants de l'installation.
  Uninstall : Stop + supprime la tâche et la règle de pare-feu (les DONNÉES sont conservées).
  Purge     : supprime le dossier de données (ventes, clients, sauvegardes) : irréversible.
#>
[CmdletBinding()]
param(
    [Parameter(Mandatory)][ValidateSet('Install', 'Start', 'Stop', 'Uninstall', 'Purge')][string]$Action,
    [Parameter(Mandatory)][string]$InstallDir,
    [string]$DataDir = (Join-Path $env:ProgramData 'Komersia'),
    [int]$Port = 8000,
    [int]$Lan = 1,          # 1 : les autres appareils du réseau local peuvent se connecter ; 0 : ce PC seulement
    [int]$StartAfter = 0
)

$ErrorActionPreference = 'Stop'
$TaskName = 'Komersia'
$FirewallRule = 'Komersia'
$Exe = Join-Path $InstallDir 'KomersiaServer.exe'
$SidNetworkService = '*S-1-5-20'
$SidSystem = '*S-1-5-18'
$SidAdmins = '*S-1-5-32-544'
$SidUsers = '*S-1-5-32-545'

function Write-Step($msg) { Write-Host "[komersia] $msg" }

function Invoke-Icacls {
    param([string[]]$IcaclsArgs)
    $out = & icacls.exe @IcaclsArgs
    if ($LASTEXITCODE -ne 0) { throw "icacls $($IcaclsArgs -join ' ') a échoué ($LASTEXITCODE) : $out" }
}

function Get-SettingsPath { Join-Path (Join-Path $DataDir 'config') 'settings.json' }

function Read-Settings {
    $p = Get-SettingsPath
    if (Test-Path $p) { return Get-Content -Raw -Encoding UTF8 $p | ConvertFrom-Json }
    return $null
}

function Install-Komersia {
    if (-not (Test-Path $Exe)) { throw "KomersiaServer.exe introuvable : $Exe" }
    foreach ($d in @('', 'config', 'logs', 'securite', 'control')) {
        New-Item -ItemType Directory -Force -Path (Join-Path $DataDir $d) | Out-Null
    }
    Write-Step 'Droits sur le dossier de données'
    # Le service tourne sous NETWORK SERVICE (pas administrateur : PostgreSQL le refuse sinon).
    Invoke-Icacls @($DataDir, '/grant', "${SidNetworkService}:(OI)(CI)M")
    # `control` : seul dossier modifiable par les utilisateurs standard (demande d'arrêt, verrou).
    Invoke-Icacls @((Join-Path $DataDir 'control'), '/grant', "${SidUsers}:(OI)(CI)M")
    # Copies de sécurité internes : service, SYSTEM et administrateurs seulement (elles contiennent toutes les données).
    $backups = Join-Path $DataDir 'securite'
    Invoke-Icacls @($backups, '/inheritance:r', '/grant:r', "${SidNetworkService}:(OI)(CI)F",
        '/grant:r', "${SidSystem}:(OI)(CI)F", '/grant:r', "${SidAdmins}:(OI)(CI)F")

    # settings.json : créé une seule fois (une mise à jour ne change ni le port ni l'ouverture au réseau).
    $settingsPath = Get-SettingsPath
    if (-not (Test-Path $settingsPath)) {
        $bind = if ($Lan -eq 1) { '0.0.0.0' } else { '127.0.0.1' }
        $json = [ordered]@{ web_port = $Port; bind = $bind } | ConvertTo-Json
        # UTF-8 SANS BOM
        [System.IO.File]::WriteAllText($settingsPath, $json, (New-Object System.Text.UTF8Encoding($false)))
        Write-Step "settings.json créé (port $Port, bind $bind)"
    }
    $settings = Read-Settings
    $webPort = if ($settings.web_port) { [int]$settings.web_port } else { $Port }
    $lanEnabled = ($settings.bind -ne '127.0.0.1')

    Write-Step 'Tâche planifiée « Komersia » (au démarrage de Windows, sans session ouverte)'
    $taskAction = New-ScheduledTaskAction -Execute $Exe -Argument '--run' -WorkingDirectory $InstallDir
    $taskTrigger = New-ScheduledTaskTrigger -AtStartup
    $taskPrincipal = New-ScheduledTaskPrincipal -UserId 'NT AUTHORITY\NETWORK SERVICE' -LogonType ServiceAccount `
        -RunLevel Limited
    $taskSettings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries `
        -StartWhenAvailable -MultipleInstances IgnoreNew -ExecutionTimeLimit ([TimeSpan]::Zero) `
        -RestartCount 5 -RestartInterval (New-TimeSpan -Minutes 1)
    Register-ScheduledTask -TaskName $TaskName -Action $taskAction -Trigger $taskTrigger -Principal $taskPrincipal `
        -Settings $taskSettings -Description 'Serveur Komersia (base de données et application web).' -Force | Out-Null

    Remove-NetFirewallRule -DisplayName $FirewallRule -ErrorAction SilentlyContinue
    if ($lanEnabled) {
        Write-Step "Pare-feu : entrée autorisée sur le réseau PRIVÉ, ports $webPort-$($webPort + 9)"
        New-NetFirewallRule -DisplayName $FirewallRule -Direction Inbound -Action Allow -Protocol TCP `
            -LocalPort "$webPort-$($webPort + 9)" -Profile Private -Program $Exe `
            -Description 'Komersia : accès des autres appareils du réseau local (profil privé uniquement).' | Out-Null
    }
    else {
        Write-Step 'Accès limité à ce PC : aucune règle de pare-feu créée'
    }
    if ($StartAfter -eq 1) { Start-Komersia }
}

function Start-Komersia {
    Write-Step 'Démarrage de Komersia'
    Start-ScheduledTask -TaskName $TaskName
}

function Stop-Komersia {
    $env:KOMERSIA_DATA_DIR = $DataDir
    if (Test-Path $Exe) {
        Write-Step 'Arrêt propre de Komersia'
        & $Exe --stop | ForEach-Object { Write-Step $_ }
    }
    Stop-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
    # Filet de sécurité : plus aucun processus de l'installation ne doit tourner (sinon fichiers verrouillés).
    $prefix = $InstallDir.TrimEnd('\') + '\'
    for ($i = 0; $i -lt 20; $i++) {
        $left = @(Get-Process -ErrorAction SilentlyContinue | Where-Object {
                $_.Path -and $_.Path.StartsWith($prefix, [StringComparison]::OrdinalIgnoreCase) })
        if ($left.Count -eq 0) { return }
        if ($i -ge 5) { $left | Stop-Process -Force -ErrorAction SilentlyContinue }
        Start-Sleep -Seconds 1
    }
    throw "Des processus de Komersia ne se sont pas arrêtés : $InstallDir"
}

function Uninstall-Komersia {
    Stop-Komersia
    Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false -ErrorAction SilentlyContinue
    Remove-NetFirewallRule -DisplayName $FirewallRule -ErrorAction SilentlyContinue
    Write-Step 'Tâche et règle de pare-feu supprimées ; les données sont conservées.'
}

function Remove-Data {
    if (-not (Test-Path $DataDir)) { return }
    Write-Step "Suppression des données : $DataDir"
    # pgdata et securite sont réservés au service : l'administrateur en reprend d'abord la propriété.
    & takeown.exe /F $DataDir /R /D O | Out-Null
    Invoke-Icacls @($DataDir, '/grant', "${SidAdmins}:(OI)(CI)F", '/T', '/C', '/Q')
    Remove-Item -Recurse -Force $DataDir
}

switch ($Action) {
    'Install' { Install-Komersia }
    'Start' { Start-Komersia }
    'Stop' { Stop-Komersia }
    'Uninstall' { Uninstall-Komersia }
    'Purge' { Remove-Data }
}
