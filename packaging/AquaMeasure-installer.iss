#define MyAppName "AquaMeasure"
#define MyAppVersion "2026.09.08"
#define MyAppPublisher "AquaMeasure"
#define MyAppExeName "AquaMeasure.exe"
#ifndef MyAppSource
  #define MyAppSource "..\release\AquaMeasure-Windows-x64-20260908"
#endif

[Setup]
AppId={{A1FB5837-A710-4DA4-99B8-073B2B11B05F}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppVerName={#MyAppName} {#MyAppVersion}
AppPublisher={#MyAppPublisher}
DefaultDirName={localappdata}\Programs\{#MyAppName}
DefaultGroupName={#MyAppName}
DisableProgramGroupPage=yes
OutputDir=..\release
OutputBaseFilename=AquaMeasure-Setup-20260908
SetupIconFile=..\aquameasure-pyside\resources\aquameasure.ico
Compression=lzma2/ultra64
SolidCompression=yes
WizardStyle=modern
PrivilegesRequired=lowest
ArchitecturesAllowed=x64
ArchitecturesInstallIn64BitMode=x64
MinVersion=10.0
UninstallDisplayIcon={app}\{#MyAppExeName}
CloseApplications=yes
CloseApplicationsFilter={#MyAppExeName}
RestartApplications=no
SetupLogging=yes

[Languages]
Name: "french"; MessagesFile: "compiler:Languages\French.isl"

[Tasks]
Name: "desktopicon"; Description: "Créer un raccourci sur le Bureau"; GroupDescription: "Raccourcis supplémentaires :"; Flags: unchecked

[Files]
Source: "{#MyAppSource}\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs
Source: "LISEZ-MOI-INSTALLATION.txt"; DestDir: "{app}"; Flags: ignoreversion

[Dirs]
Name: "{userdocs}\AquaMeasure - Donnees"; Flags: uninsneveruninstall; Check: ShouldConfigureDefaultStorage
Name: "{userdocs}\AquaMeasure - Donnees\camera_parameters"; Flags: uninsneveruninstall; Check: ShouldConfigureDefaultStorage
Name: "{userdocs}\AquaMeasure - Donnees\data\exports"; Flags: uninsneveruninstall; Check: ShouldConfigureDefaultStorage
Name: "{userdocs}\AquaMeasure - Donnees\data\media"; Flags: uninsneveruninstall; Check: ShouldConfigureDefaultStorage

[Icons]
Name: "{group}\AquaMeasure"; Filename: "{app}\{#MyAppExeName}"; WorkingDir: "{app}"; AppUserModelID: "IRD.AquaMeasure"
Name: "{group}\Dossier des données AquaMeasure"; Filename: "{userdocs}\AquaMeasure - Donnees"
Name: "{group}\Guide d'installation"; Filename: "{app}\LISEZ-MOI-INSTALLATION.txt"
#if FileExists(MyAppSource + "\Documentation\AquaMeasure_Manuel_Utilisateur.pdf")
Name: "{group}\Manuel utilisateur"; Filename: "{app}\Documentation\AquaMeasure_Manuel_Utilisateur.pdf"
Name: "{group}\Guide des modèles IA"; Filename: "{app}\Documentation\AquaMeasure_Guide_Extension_Modeles_Detection.pdf"
#else
Name: "{group}\Manuel utilisateur"; Filename: "{app}\Documentation\AquaMeasure_Manuel_Utilisateur.docx"
#endif
Name: "{autodesktop}\AquaMeasure"; Filename: "{app}\{#MyAppExeName}"; WorkingDir: "{app}"; AppUserModelID: "IRD.AquaMeasure"; Tasks: desktopicon

[Run]
Filename: "{app}\{#MyAppExeName}"; Description: "Lancer AquaMeasure"; Flags: nowait postinstall skipifsilent
Filename: "{app}\LISEZ-MOI-INSTALLATION.txt"; Description: "Lire les informations sur les imports et les exports"; Flags: postinstall shellexec skipifsilent unchecked

[Code]
function JsonEscape(Value: String): String;
begin
  Result := Value;
  StringChangeEx(Result, '\', '\\', True);
  StringChangeEx(Result, '"', '\"', True);
end;

function ShouldConfigureDefaultStorage: Boolean;
begin
  { Le paramètre est réservé aux tests automatisés de l'installateur. }
  Result := ExpandConstant('{param:SkipStorageSetup|0}') <> '1';
end;

procedure ConfigureDefaultStorage;
var
  ConfigDir: String;
  ConfigPath: String;
  DataRoot: String;
  Json: String;
begin
  if not ShouldConfigureDefaultStorage then
    Exit;

  ConfigDir := ExpandConstant('{userappdata}\AquaMeasure');
  ConfigPath := ConfigDir + '\storage.json';

  { Une préférence existante appartient à l'utilisateur : ne jamais l'écraser. }
  if FileExists(ConfigPath) then
    Exit;

  if not ForceDirectories(ConfigDir) then
    Exit;

  DataRoot := ExpandConstant('{userdocs}\AquaMeasure - Donnees');
  Json := '{' + #13#10 +
    '  "data_root": "' + JsonEscape(DataRoot) + '"' + #13#10 +
    '}' + #13#10;
  SaveStringToFile(ConfigPath, Json, False);
end;

procedure CurStepChanged(CurStep: TSetupStep);
begin
  if CurStep = ssPostInstall then
    ConfigureDefaultStorage;
end;
