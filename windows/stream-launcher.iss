; Installer Windows di Stream Launcher (Inno Setup 6). Lo compila la CI:
;   iscc /DAppVersion=1.0.0 windows\stream-launcher.iss
; Installazione per utente (niente permessi di amministratore).

#ifndef AppVersion
  #define AppVersion "0.0.0"
#endif

[Setup]
AppId={{6B7E2F4A-3C1D-4E8B-9A57-5D2C0F1E8B34}
AppName=Stream Launcher
AppVersion={#AppVersion}
AppPublisher=Stream Launcher
DefaultDirName={localappdata}\Programs\Stream Launcher
DisableProgramGroupPage=yes
DisableDirPage=yes
PrivilegesRequired=lowest
OutputDir=..\dist
OutputBaseFilename=StreamLauncher-Setup-{#AppVersion}
SetupIconFile=icon.ico
UninstallDisplayIcon={app}\StreamLauncher.exe
Compression=lzma2/max
SolidCompression=yes
WizardStyle=modern
CloseApplications=force
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible

[Languages]
Name: "it"; MessagesFile: "compiler:Languages\Italian.isl"

[Files]
Source: "..\dist\StreamLauncher\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
; Le voci del menu Start (impostazioni e un servizio ciascuna) le crea l'app stessa.
Name: "{userstartup}\Stream Launcher"; Filename: "{app}\StreamLauncher.exe"; Parameters: "tray"

[Run]
Filename: "{app}\StreamLauncher.exe"; Parameters: "init"; StatusMsg: "Preparo Stream Launcher…"; Flags: runhidden waituntilterminated
Filename: "{app}\StreamLauncher.exe"; Parameters: "settings"; Description: "Apri Stream Launcher"; Flags: nowait postinstall skipifsilent

[UninstallRun]
Filename: "{sys}\taskkill.exe"; Parameters: "/F /IM StreamLauncher.exe"; Flags: runhidden; RunOnceId: "StopTray"

[UninstallDelete]
; Come su Linux: via voci di menu, runtime e profili; la configurazione (%APPDATA%) resta.
Type: filesandordirs; Name: "{userprograms}\Stream Launcher"
Type: filesandordirs; Name: "{localappdata}\stream-launcher"
