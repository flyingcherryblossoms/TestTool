; 固定 AppId 和安装目录，让新版本覆盖升级；用户数据存放于用户主目录。
#ifndef MyAppVersion
  #error MyAppVersion is required
#endif

[Setup]
AppId=TestTool.Desktop.x64
AppName=TestTool
AppVersion={#MyAppVersion}
AppPublisher=Quasimodo
AppPublisherURL=https://github.com/flyingcherryblossoms/TestTool
DefaultDirName={autopf}\TestTool
DefaultGroupName=TestTool
DisableProgramGroupPage=yes
UsePreviousAppDir=yes
ArchitecturesAllowed=x64os
ArchitecturesInstallIn64BitMode=x64os
PrivilegesRequired=admin
OutputDir=..\dist
OutputBaseFilename=TestTool-{#MyAppVersion}-Windows-x64-Setup
SetupIconFile=..\resources\icon.ico
UninstallDisplayIcon={app}\TestTool.exe
VersionInfoVersion={#MyAppVersion}
Compression=lzma2/fast
SolidCompression=yes
WizardStyle=modern
CloseApplications=yes
CloseApplicationsFilter=*.exe,*.dll,*.pyd
RestartApplications=no

[Tasks]
Name: "desktopicon"; Description: "Create a desktop shortcut"; Flags: unchecked

[InstallDelete]
; 清理旧依赖，避免库升级后遗留文件；不会触及 ~/.config/TestTool。
Type: filesandordirs; Name: "{app}\_internal"

[Files]
Source: "..\dist\TestTool\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs; Excludes: "*.db,*.db-wal,*.db-shm"

[Icons]
Name: "{autoprograms}\TestTool"; Filename: "{app}\TestTool.exe"
Name: "{autodesktop}\TestTool"; Filename: "{app}\TestTool.exe"; Tasks: desktopicon

[Run]
Filename: "{app}\TestTool.exe"; Description: "Launch TestTool"; Flags: nowait postinstall skipifsilent
