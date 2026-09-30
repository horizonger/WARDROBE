; WARDROBE Windows installer
; Requires Inno Setup 6+

#define AppName "WARDROBE"
#ifndef AppVersion
#define AppVersion "1.0.0"
#endif
#define AppPublisher "WARDROBE"
#define AppExeName "WARDROBE.exe"
#define SourceDir AddBackslash(SourcePath) + "dist\WARDROBE"
#define IconFile AddBackslash(SourceDir) + "wardrobe.ico"

[Setup]
AppId={{7F5A3D61-0B8A-4B0D-9F26-0D6B19B7D5A1}
AppName={#AppName}
AppVersion={#AppVersion}
AppVerName={#AppName} {#AppVersion}
AppPublisher={#AppPublisher}
DefaultDirName={autopf}\WARDROBE
DefaultGroupName=WARDROBE
DisableProgramGroupPage=yes
OutputDir=installer
OutputBaseFilename=WARDROBE-Setup-{#AppVersion}
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
ArchitecturesInstallIn64BitMode=x64compatible
PrivilegesRequired=admin
SetupIconFile={#IconFile}
CloseApplications=yes
RestartApplications=yes
UninstallDisplayIcon={app}\{#AppExeName}

; Keep user data in %LOCALAPPDATA%\WARDROBE intact when uninstalling/updating.
; The installer only owns the application files under {app}.

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "Create a desktop shortcut"; GroupDescription: "Additional shortcuts:";

[Files]
Source: "{#SourceDir}\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{autoprograms}\WARDROBE"; Filename: "{app}\{#AppExeName}"; WorkingDir: "{app}"; IconFilename: "{app}\wardrobe.ico"; IconIndex: 0
Name: "{autodesktop}\WARDROBE"; Filename: "{app}\{#AppExeName}"; WorkingDir: "{app}"; IconFilename: "{app}\wardrobe.ico"; IconIndex: 0; Tasks: desktopicon

[Run]
Filename: "{app}\{#AppExeName}"; Description: "Launch WARDROBE"; Flags: nowait postinstall skipifsilent

