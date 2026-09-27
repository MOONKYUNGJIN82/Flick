#ifndef AppVersion
  #define AppVersion "1.0.0"
#endif
[Setup]
AppId={{CDA5A280-D2D7-4A64-A65B-346254CED847}
AppName=Flick
AppVersion={#AppVersion}
AppPublisher=KALLOS
AppPublisherURL=https://github.com/MOONKYUNGJIN82/Flick
AppUpdatesURL=https://github.com/MOONKYUNGJIN82/Flick/releases/latest
DefaultDirName={localappdata}\Programs\Flick
DefaultGroupName=Flick
PrivilegesRequired=lowest
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
MinVersion=10.0
OutputDir=..\dist
OutputBaseFilename=Flick.Setup
SetupIconFile=..\assets\flick-v2.ico
UninstallDisplayIcon={app}\Flick.exe
Compression=lzma2/fast
SolidCompression=yes
WizardStyle=modern
DisableProgramGroupPage=yes
CloseApplications=yes
RestartApplications=no
VersionInfoVersion={#AppVersion}

[Languages]
Name: "korean"; MessagesFile: "compiler:Languages\Korean.isl"
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "Create a desktop shortcut / 바탕화면 바로가기"; Flags: unchecked

[Files]
Source: "..\dist\Flick-1.0.0\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{group}\Flick"; Filename: "{app}\Flick.exe"
Name: "{group}\Uninstall Flick"; Filename: "{uninstallexe}"
Name: "{autodesktop}\Flick"; Filename: "{app}\Flick.exe"; Tasks: desktopicon

[Run]
Filename: "{app}\Flick.exe"; Description: "Launch Flick / Flick 실행"; Flags: nowait postinstall skipifsilent
