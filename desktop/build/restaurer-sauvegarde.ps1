<#
  Raccourci « Restaurer une sauvegarde » : choisit un fichier .dump, demande une double confirmation, puis demande
  à l'instance Komersia en cours de restaurer (KomersiaServer --restore). L'instance arrête le serveur web,
  fait d'abord une copie de sécurité de l'état actuel, restaure, migre et relance. Le fichier doit être lisible par
  tous les comptes (disque ou clé USB), pas dans un dossier personnel privé.
#>
Add-Type -AssemblyName System.Windows.Forms
$exe = Join-Path (Split-Path $PSScriptRoot -Parent) 'KomersiaServer.exe'
$dlg = New-Object System.Windows.Forms.OpenFileDialog
$dlg.Title = 'Choisir la sauvegarde Komersia à restaurer'
$dlg.Filter = 'Sauvegardes Komersia (*.dump)|*.dump'
if ($dlg.ShowDialog() -ne 'OK') { exit 0 }
$file = $dlg.FileName
$yn = [System.Windows.Forms.MessageBox]
$check = & $exe --restore $file --check-only 2>&1 | Out-String
if ($LASTEXITCODE -ne 0) {
    [void]$yn::Show("Ce fichier ne peut pas être restauré :`n`n$check", 'Komersia', 'OK', 'Error'); exit 1
}
$q1 = $yn::Show("Restaurer cette sauvegarde remplace TOUTES les données actuelles (ventes, clients, stock).`n`nUne copie de sécurité de l'état actuel sera faite d'abord.`n`nFichier : $file`n`nContinuer ?", 'Komersia - Restaurer', 'YesNo', 'Warning', 'Button2')
if ($q1 -ne 'Yes') { exit 0 }
$out = & $exe --restore $file 2>&1 | Out-String
if ($LASTEXITCODE -eq 0) { [void]$yn::Show("Restauration terminée.`n`n$out", 'Komersia', 'OK', 'Information') }
else { [void]$yn::Show("La restauration a échoué. Vos données actuelles n'ont pas été modifiées si l'erreur est survenue avant la restauration.`n`n$out", 'Komersia', 'OK', 'Error'); exit 1 }
