; Installateur Windows de Komersia (Inno Setup 6.3 ou plus). Compilation : voir build.ps1.
;   ISCC /DAppVersion=1.0.0 /DStageDir=<dossier assemblé> /DOutputDir=<sortie> komersia.iss
;
; Installation en Program Files\Komersia ; DONNÉES dans %PROGRAMDATA%\Komersia (jamais touchées par une mise
; à jour, conservées par défaut à la désinstallation). Tout ce qui touche à Windows (tâche planifiée, pare-feu,
; droits) est fait par scripts\komersia-service.ps1, le même script que la CI exécute.
;
; Installation silencieuse (CI, déploiement) :
;   Komersia-Setup-1.0.0.exe /VERYSILENT /SUPPRESSMSGBOXES /NORESTART /LOG=setup.log [/PORT=8000] [/LAN=1]
; Désinstallation silencieuse (données CONSERVÉES) : unins000.exe /VERYSILENT ; avec /PURGEDATA=1 : supprimées.

#ifndef AppVersion
  #define AppVersion "0.0.0"
#endif
#ifndef StageDir
  #error StageDir doit être défini (/DStageDir=...)
#endif
#ifndef OutputDir
  #define OutputDir "output"
#endif

[Setup]
; AppId : NE JAMAIS CHANGER (identifie l'installation pour les mises à jour par-dessus).
AppId={{6F2B8D1E-4A7C-4E39-9B52-C0D8A1F37E64}
AppName=Komersia
AppVersion={#AppVersion}
AppVerName=Komersia {#AppVersion}
AppPublisher=Komersia
DefaultDirName={autopf}\Komersia
DefaultGroupName=Komersia
DisableProgramGroupPage=yes
PrivilegesRequired=admin
; Windows 10 et 11, 64 bits uniquement
MinVersion=10.0
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
OutputDir={#OutputDir}
OutputBaseFilename=Komersia-Setup-{#AppVersion}
Compression=lzma2/max
SolidCompression=yes
WizardStyle=modern
InfoBeforeFile={#StageDir}\LISEZMOI.txt
UninstallDisplayName=Komersia
UninstallDisplayIcon={app}\KomersiaServer.exe
; Le serveur est lancé par une tâche planifiée : il est arrêté par notre script, pas par le gestionnaire de redémarrage.
CloseApplications=no
RestartApplications=no
SetupLogging=yes
VersionInfoVersion={#AppVersion}

[Languages]
Name: "french"; MessagesFile: "compiler:Languages\French.isl"

[Messages]
FinishedLabel=Komersia est installé et démarre en arrière-plan (la toute première fois, comptez environ une minute). Ouvrez-le avec le raccourci « Komersia ». Il redémarrera automatiquement avec Windows.

[Tasks]
Name: "desktopicon"; Description: "Créer un raccourci « Komersia » sur le Bureau"; Flags: checkedonce

[Files]
Source: "{#StageDir}\app\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs
Source: "{#StageDir}\pgsql\*"; DestDir: "{app}\pgsql"; Flags: ignoreversion recursesubdirs createallsubdirs
Source: "{#StageDir}\scripts\*"; DestDir: "{app}\scripts"; Flags: ignoreversion
Source: "{#StageDir}\LISEZMOI.txt"; DestDir: "{app}"; Flags: ignoreversion

[InstallDelete]
; Mise à jour : on remplace les programmes (pas les données, qui sont ailleurs).
Type: filesandordirs; Name: "{app}\_internal"
Type: filesandordirs; Name: "{app}\pgsql"
Type: filesandordirs; Name: "{app}\scripts"

[Icons]
Name: "{group}\Komersia"; Filename: "{app}\KomersiaServer.exe"; Parameters: "--open"; Comment: "Ouvrir Komersia dans le navigateur"
Name: "{group}\Arrêter Komersia"; Filename: "{app}\KomersiaServer.exe"; Parameters: "--stop"; Comment: "Arrêter proprement Komersia"
Name: "{group}\Démarrer Komersia (administrateur)"; Filename: "{sys}\WindowsPowerShell\v1.0\powershell.exe"; Parameters: "-NoProfile -WindowStyle Hidden -Command ""Start-Process -Verb RunAs -WindowStyle Hidden -FilePath schtasks.exe -ArgumentList '/Run /TN Komersia'"""; Comment: "Redémarrer Komersia après un arrêt"
Name: "{group}\Restaurer une sauvegarde"; Filename: "{sys}\WindowsPowerShell\v1.0\powershell.exe"; Parameters: "-NoProfile -ExecutionPolicy Bypass -STA -WindowStyle Hidden -File ""{app}\scripts\restaurer-sauvegarde.ps1"""; Comment: "Remplacer les données par celles d'une sauvegarde"
Name: "{group}\Adresse pour les autres appareils"; Filename: "{win}\notepad.exe"; Parameters: """{commonappdata}\Komersia\ADRESSE.txt"""; Comment: "Adresse à taper sur les autres PC et téléphones"
Name: "{group}\Désinstaller Komersia"; Filename: "{uninstallexe}"
Name: "{autodesktop}\Komersia"; Filename: "{app}\KomersiaServer.exe"; Parameters: "--open"; Tasks: desktopicon; Comment: "Ouvrir Komersia dans le navigateur"

[UninstallDelete]
Type: filesandordirs; Name: "{app}"

[Code]
var
  PortPage: TInputQueryWizardPage;
  LanPage: TInputOptionWizardPage;

function DataDir: String;
begin
  Result := ExpandConstant('{commonappdata}\Komersia');
end;

function IsUpgrade: Boolean;
begin
  // Réglages déjà présents : le port et l'ouverture au réseau ne sont pas redemandés.
  Result := FileExists(DataDir + '\config\settings.json');
end;

function RunService(const Action, Extra: String): Integer;
var
  Script, Params: String;
  ResultCode: Integer;
begin
  Script := ExpandConstant('{app}\scripts\komersia-service.ps1');
  Params := '-NoProfile -ExecutionPolicy Bypass -File "' + Script + '" -Action ' + Action +
    ' -InstallDir "' + ExpandConstant('{app}') + '" -DataDir "' + DataDir + '"' + Extra;
  Log('Exécution : powershell ' + Params);
  if not Exec(ExpandConstant('{sys}\WindowsPowerShell\v1.0\powershell.exe'), Params, '', SW_HIDE,
              ewWaitUntilTerminated, ResultCode) then
    ResultCode := -1;
  Log('Code de retour : ' + IntToStr(ResultCode));
  Result := ResultCode;
end;

procedure InitializeWizard;
begin
  PortPage := CreateInputQueryPage(wpSelectDir, 'Port du serveur',
    'Sur quel port Komersia doit-il écouter ?',
    'Laissez 8000 sauf si un autre programme de ce PC utilise déjà ce port.');
  PortPage.Add('Port (entre 1024 et 65000) :', False);
  PortPage.Values[0] := ExpandConstant('{param:PORT|8000}');

  LanPage := CreateInputOptionPage(PortPage.ID, 'Autres appareils',
    'Autoriser les autres appareils de la boutique ?',
    'Si vous décochez, seul ce PC pourra utiliser Komersia.', False, False);
  LanPage.Add('Autoriser les autres PC et téléphones du réseau local (réseau privé uniquement) - recommandé');
  LanPage.Values[0] := (ExpandConstant('{param:LAN|1}') = '1');
end;

function ShouldSkipPage(PageID: Integer): Boolean;
begin
  Result := False;
  if (PageID = PortPage.ID) or (PageID = LanPage.ID) then
    Result := IsUpgrade;
end;

function NextButtonClick(CurPageID: Integer): Boolean;
var
  Port: Integer;
begin
  Result := True;
  if CurPageID = PortPage.ID then
  begin
    Port := StrToIntDef(Trim(PortPage.Values[0]), 0);
    if (Port < 1024) or (Port > 65000) then
    begin
      MsgBox('Le port doit être un nombre entre 1024 et 65000.', mbError, MB_OK);
      Result := False;
    end;
  end;
end;

function PrepareToInstall(var NeedsRestart: Boolean): String;
begin
  Result := '';
  // Mise à jour par-dessus une version existante : arrêter Komersia (les DONNÉES ne sont pas touchées).
  if FileExists(ExpandConstant('{app}\scripts\komersia-service.ps1')) then
  begin
    if RunService('Stop', '') <> 0 then
      Result := 'Impossible d''arrêter la version installée de Komersia. Fermez-la avec « Arrêter Komersia », puis recommencez.';
  end;
end;

procedure CurStepChanged(CurStep: TSetupStep);
var
  Lan: String;
begin
  if CurStep = ssPostInstall then
  begin
    if LanPage.Values[0] then Lan := '1' else Lan := '0';
    if RunService('Install', ' -Port ' + IntToStr(StrToIntDef(Trim(PortPage.Values[0]), 8000)) +
                  ' -Lan ' + Lan + ' -StartAfter 1') <> 0 then
    begin
      MsgBox('L''installation de Komersia est incomplète (tâche planifiée ou pare-feu). Consultez le journal d''installation.',
             mbError, MB_OK);
      Abort;
    end;
  end;
end;

procedure CurUninstallStepChanged(CurUninstallStep: TUninstallStep);
var
  Purge: Boolean;
begin
  if CurUninstallStep = usUninstall then
  begin
    // Les fichiers (dont le script) existent encore à ce stade.
    RunService('Uninstall', '');
    Purge := (ExpandConstant('{param:PURGEDATA|0}') = '1');
    if (not Purge) and (not UninstallSilent) then
    begin
      // Question explicite : les données (ventes, clients, sauvegardes) sont CONSERVÉES par défaut.
      Purge := (MsgBox('Voulez-vous aussi supprimer toutes les données de Komersia (ventes, clients, stock, sauvegardes) ?'#13#10#13#10 +
                       'Choisissez « Non » pour les conserver : elles seront retrouvées si vous réinstallez Komersia.',
                       mbConfirmation, MB_YESNO or MB_DEFBUTTON2) = IDYES);
      if Purge then
        Purge := (MsgBox('ATTENTION : cette suppression est DÉFINITIVE et irréversible. Confirmer la suppression de toutes les données ?',
                         mbError, MB_YESNO or MB_DEFBUTTON2) = IDYES);
    end;
    if Purge then
      RunService('Purge', '');
  end;
end;
