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
; La cartella dell'app va nel PATH dell'utente: stream-launcher-cli da qualunque prompt.
ChangesEnvironment=yes

[Languages]
Name: "it"; MessagesFile: "compiler:Languages\Italian.isl"

[Files]
Source: "..\dist\StreamLauncher\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Run]
Filename: "{app}\StreamLauncher.exe"; Parameters: "init"; StatusMsg: "Preparo Stream Launcher…"; Flags: runhidden waituntilterminated
Filename: "{app}\StreamLauncher.exe"; Parameters: "settings"; Description: "Apri Stream Launcher"; Flags: nowait postinstall skipifsilent

[Registry]
Root: HKCU; Subkey: "Environment"; ValueType: expandsz; ValueName: "Path"; ValueData: "{olddata};{app}"; Check: NeedsAddPath(ExpandConstant('{app}'))

[UninstallRun]
Filename: "{sys}\taskkill.exe"; Parameters: "/F /IM StreamLauncher.exe"; Flags: runhidden; RunOnceId: "StopTray"
Filename: "{sys}\taskkill.exe"; Parameters: "/F /IM stream-launcher-cli.exe"; Flags: runhidden; RunOnceId: "StopCli"

[UninstallDelete]
; Come su Linux: via voci di menu, runtime e profili; la configurazione (%APPDATA%) resta.
Type: filesandordirs; Name: "{userprograms}\Stream Launcher"
Type: files; Name: "{userstartup}\Stream Launcher.lnk"
Type: filesandordirs; Name: "{localappdata}\stream-launcher"

[Code]
function UserPath(): string;
begin
  if not RegQueryStringValue(HKCU, 'Environment', 'Path', Result) then
    Result := '';
end;

function NeedsAddPath(Dir: string): Boolean;
begin
  Result := Pos(';' + Uppercase(Dir) + ';', ';' + Uppercase(UserPath()) + ';') = 0;
end;

procedure CurUninstallStepChanged(CurUninstallStep: TUninstallStep);
var
  Path, Dir: string;
  P: Integer;
begin
  if CurUninstallStep <> usPostUninstall then
    exit;
  Dir := ExpandConstant('{app}');
  Path := ';' + UserPath() + ';';
  P := Pos(';' + Uppercase(Dir) + ';', Uppercase(Path));
  if P = 0 then
    exit;
  Delete(Path, P, Length(Dir) + 1);
  Path := Copy(Path, 2, Length(Path) - 2);
  RegWriteExpandStringValue(HKCU, 'Environment', 'Path', Path);
end;
