; Inno Setup script for NousHalo-Setup.exe (https://jrsoftware.org/isinfo.php).
; build.ps1 runs it after building dist\NousHalo; GitHub does the same on version tags.
;
; Installs for the current user only (no admin prompt) into
; %LOCALAPPDATA%\Programs\Nous Halo, with a Start menu entry, an optional desktop
; shortcut and an uninstaller in Settings > Apps. .halo files (Nous Halo's own)
; open in it; it's also offered in "Open with" and Settings > Default apps for
; .ai and .svg, but never takes those over from Illustrator or a browser.

#define AppName "Nous Halo"
#define AppExe "NousHalo.exe"
#define AppVersion Trim(FileRead(FileOpen(AddBackslash(SourcePath) + "VERSION")))
#define ProgId "NousHalo.Document"
#define ArtProgId "NousHalo.Artwork"

[Setup]
; Never change AppId: it's how a newer installer finds and replaces an older install.
AppId={{5C398EB6-B755-4D7D-8E43-1B470CFDC207}
AppName={#AppName}
AppVersion={#AppVersion}
AppVerName={#AppName} {#AppVersion}
AppPublisher=lionthroat
AppPublisherURL=https://noushalo.lionthroat.com
AppSupportURL=https://github.com/lionthroat/NousHalo
DefaultDirName={autopf}\{#AppName}
DisableProgramGroupPage=yes
DisableDirPage=yes
PrivilegesRequired=lowest
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
ChangesAssociations=yes
CloseApplications=yes
OutputDir=installer-output
OutputBaseFilename=NousHalo-Setup
SetupIconFile=icon.ico
UninstallDisplayIcon={app}\{#AppExe}
UninstallDisplayName={#AppName}
WizardStyle=modern
Compression=lzma2/max
SolidCompression=yes
VersionInfoVersion={#AppVersion}
VersionInfoDescription={#AppName} Setup

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "Put a {#AppName} shortcut on the desktop"; GroupDescription: "Shortcuts:"

[Files]
Source: "dist\NousHalo\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[InstallDelete]
; An update replaces the whole program folder, so files a newer build dropped don't linger
Type: filesandordirs; Name: "{app}\_internal"

[Icons]
Name: "{autoprograms}\{#AppName}"; Filename: "{app}\{#AppExe}"
Name: "{autodesktop}\{#AppName}"; Filename: "{app}\{#AppExe}"; Tasks: desktopicon

[Registry]
; HKA = the current user's registry hive here, since the install is per-user.
; .halo documents belong to Nous Halo
Root: HKA; Subkey: "Software\Classes\{#ProgId}"; ValueType: string; ValueData: "Nous Halo document"; Flags: uninsdeletekey
Root: HKA; Subkey: "Software\Classes\{#ProgId}\DefaultIcon"; ValueType: string; ValueData: "{app}\{#AppExe},0"
Root: HKA; Subkey: "Software\Classes\{#ProgId}\shell\open\command"; ValueType: string; ValueData: """{app}\{#AppExe}"" ""%1"""
Root: HKA; Subkey: "Software\Classes\.halo"; ValueType: string; ValueData: "{#ProgId}"; Flags: uninsdeletevalue
Root: HKA; Subkey: "Software\Classes\.halo\OpenWithProgids"; ValueType: string; ValueName: "{#ProgId}"; ValueData: ""; Flags: uninsdeletevalue
; Artwork (.ai, .svg) it can open, offered in "Open with" only
Root: HKA; Subkey: "Software\Classes\{#ArtProgId}"; ValueType: string; ValueData: "Artwork"; Flags: uninsdeletekey
Root: HKA; Subkey: "Software\Classes\{#ArtProgId}\DefaultIcon"; ValueType: string; ValueData: "{app}\{#AppExe},0"
Root: HKA; Subkey: "Software\Classes\{#ArtProgId}\shell\open\command"; ValueType: string; ValueData: """{app}\{#AppExe}"" ""%1"""
Root: HKA; Subkey: "Software\Classes\.ai\OpenWithProgids"; ValueType: string; ValueName: "{#ArtProgId}"; ValueData: ""; Flags: uninsdeletevalue
Root: HKA; Subkey: "Software\Classes\.svg\OpenWithProgids"; ValueType: string; ValueName: "{#ArtProgId}"; ValueData: ""; Flags: uninsdeletevalue
Root: HKA; Subkey: "Software\Classes\Applications\{#AppExe}"; ValueType: string; ValueName: "FriendlyAppName"; ValueData: "{#AppName}"; Flags: uninsdeletekey
Root: HKA; Subkey: "Software\Classes\Applications\{#AppExe}\SupportedTypes"; ValueType: string; ValueName: ".halo"; ValueData: ""
Root: HKA; Subkey: "Software\Classes\Applications\{#AppExe}\SupportedTypes"; ValueType: string; ValueName: ".ai"; ValueData: ""
Root: HKA; Subkey: "Software\Classes\Applications\{#AppExe}\SupportedTypes"; ValueType: string; ValueName: ".svg"; ValueData: ""
Root: HKA; Subkey: "Software\Classes\Applications\{#AppExe}\shell\open\command"; ValueType: string; ValueData: """{app}\{#AppExe}"" ""%1"""
; ...and listed in Settings > Apps > Default apps
Root: HKA; Subkey: "Software\{#AppName}"; Flags: uninsdeletekey
Root: HKA; Subkey: "Software\{#AppName}\Capabilities"; ValueType: string; ValueName: "ApplicationName"; ValueData: "{#AppName}"
Root: HKA; Subkey: "Software\{#AppName}\Capabilities"; ValueType: string; ValueName: "ApplicationDescription"; ValueData: "Vector drawing with a hue-shifting colour wheel, palettes and frame animation."
Root: HKA; Subkey: "Software\{#AppName}\Capabilities\FileAssociations"; ValueType: string; ValueName: ".halo"; ValueData: "{#ProgId}"
Root: HKA; Subkey: "Software\{#AppName}\Capabilities\FileAssociations"; ValueType: string; ValueName: ".ai"; ValueData: "{#ArtProgId}"
Root: HKA; Subkey: "Software\{#AppName}\Capabilities\FileAssociations"; ValueType: string; ValueName: ".svg"; ValueData: "{#ArtProgId}"
Root: HKA; Subkey: "Software\RegisteredApplications"; ValueType: string; ValueName: "{#AppName}"; ValueData: "Software\{#AppName}\Capabilities"; Flags: uninsdeletevalue

[Run]
Filename: "{app}\{#AppExe}"; Description: "Open {#AppName} now"; Flags: nowait postinstall skipifsilent
