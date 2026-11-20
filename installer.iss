; AuroraList Windows 安装包（Inno Setup 6）
; 构建：先 pyinstaller --noconfirm --clean app.spec，再 ISCC.exe installer.iss

#define MyAppName "AuroraList"
#define MyAppVersion "1.0.2"
#define MyAppPublisher "AuroraList"
#define MyAppExeName "AuroraList.exe"

[Setup]
AppId={{7E2B2F1A-6C1D-4C8E-9A55-A1B2C3D4E5F6}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppPublisher={#MyAppPublisher}
DefaultDirName={localappdata}\Programs\{#MyAppName}
DefaultGroupName={#MyAppName}
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
OutputDir=dist
OutputBaseFilename=AuroraList-Setup-{#MyAppVersion}
SetupIconFile=assets\icon.ico
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
UninstallDisplayIcon={app}\{#MyAppExeName}
CloseApplications=yes
CloseApplicationsFilter={#MyAppExeName}

[Tasks]
Name: "desktopicon"; Description: "创建桌面快捷方式（默认勾选）"; GroupDescription: "附加任务:"

[Files]
Source: "dist\{#MyAppName}\*"; DestDir: "{app}"; Flags: recursesubdirs createallsubdirs ignoreversion

[Icons]
; IconFilename 显式指向 exe，避免 Windows 用到缓存的旧图标
Name: "{autoprograms}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; IconFilename: "{app}\{#MyAppExeName}"
Name: "{autodesktop}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; Tasks: desktopicon; IconFilename: "{app}\{#MyAppExeName}"

[Run]
; 安装完刷新 Windows 图标缓存，避免覆盖安装后仍显示旧图标
Filename: "{sys}\ie4uinit.exe"; Parameters: "-show"; Flags: runhidden nowait skipifsilent
Filename: "{app}\{#MyAppExeName}"; Description: "启动 {#MyAppName}"; Flags: nowait postinstall skipifsilent
