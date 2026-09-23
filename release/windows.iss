#ifndef Payload
  #error Payload is required
#endif
#ifndef Destination
  #error Destination is required
#endif
#ifndef Version
  #error Version is required
#endif
[Setup]
AppId={{6F827CF7-D853-461F-B757-A06ED92C5D55}
AppName=Stimulus Harvester
AppVersion={#Version}
AppVerName=Stimulus Harvester {#Version}
AppPublisher=Stimulus Harvester contributors
AppComments=Research stimulus discovery & adjudication
VersionInfoVersion=0.2.0.6
VersionInfoProductVersion=0.2.0.6
VersionInfoProductTextVersion={#Version}
DefaultDirName={localappdata}\Programs\Stimulus Harvester RC
DefaultGroupName=Stimulus Harvester RC
PrivilegesRequired=lowest
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
MinVersion=10.0
OutputDir={#Destination}
OutputBaseFilename=Stimulus-Harvester-{#Version}-windows-x64-setup
SetupIconFile={#Payload}\harvester.ico
UninstallDisplayIcon={app}\Stimulus Harvester.exe
LicenseFile={#Payload}\LICENSE
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
CloseApplications=yes
RestartApplications=no
DisableProgramGroupPage=yes
; Uninstall only files listed by the installer. Never recursively delete projects or caches.
[Files]
Source: "{#Payload}\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs
[Icons]
Name: "{group}\Stimulus Harvester"; Filename: "{app}\Stimulus Harvester.exe"; WorkingDir: "{userdocs}"
Name: "{group}\Uninstall Stimulus Harvester"; Filename: "{uninstallexe}"
[Run]
Filename: "{app}\Stimulus Harvester.exe"; Description: "Launch Stimulus Harvester"; Flags: nowait postinstall skipifsilent
