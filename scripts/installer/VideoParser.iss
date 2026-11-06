; Video Parser installer, built with Inno Setup 7 by scripts/build-release.ps1 -Installer, which passes
;   /DAppVersion=3.0.0  /DStageDir=<.local\work\release\VideoParser>  /DOutputDir=<.local\work\release>
; Per-user install (no admin) into %LOCALAPPDATA%\Programs\VideoParser. User data lives inside the install
; folder: outputs\ (videos, subtitles, notes), .local\ (sign-ins, caches, logs) and an optional .env.
; Upgrades replace the program folders and keep that data; uninstall deletes it only when the user agrees.
; This file is UTF-8 with BOM (the Chinese messages need it).

#ifndef AppVersion
  #error Pass /DAppVersion=x.y.z (build-release.ps1 does this)
#endif
#ifndef StageDir
  #error Pass /DStageDir=<staged app folder>
#endif
#ifndef OutputDir
  #define OutputDir "."
#endif

#define AppName "Video Parser"
#define AppIcon "backend\video_parser\assets\app.ico"
; Must match APP_ID and MUTEX in backend/video_parser/desktop.py.
#define AppUserModelID "VideoParser.Desktop"
#define AppMutexName "VideoParser.Desktop.Running"

[Setup]
; Never change AppId: Windows uses it to recognise later versions as upgrades of this one.
AppId={{A156B253-F151-4B69-BABA-58D608B78569}
AppName={#AppName}
AppVersion={#AppVersion}
AppVerName={#AppName} {#AppVersion}
AppPublisher=Video Parser
AppPublisherURL=https://github.com/HP-Patience/video-parser
AppSupportURL=https://github.com/HP-Patience/video-parser/issues
AppUpdatesURL=https://github.com/HP-Patience/video-parser/releases
VersionInfoVersion={#AppVersion}
; Per user: {autopf} is %LOCALAPPDATA%\Programs, which the app can write to (outputs\, .local\).
PrivilegesRequired=lowest
DefaultDirName={autopf}\VideoParser
DisableProgramGroupPage=yes
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
MinVersion=10.0
; The app holds this mutex while open; Setup and Uninstall ask the user to close it first.
AppMutex={#AppMutexName}
CloseApplications=yes
RestartApplications=no
SetupIconFile={#StageDir}\{#AppIcon}
UninstallDisplayIcon={app}\{#AppIcon}
UninstallDisplayName={#AppName}
OutputDir={#OutputDir}
OutputBaseFilename=VideoParser-{#AppVersion}-win64-setup
Compression=lzma2/max
SolidCompression=yes
WizardStyle=modern
ShowLanguageDialog=auto

[Languages]
Name: "chinesesimplified"; MessagesFile: "compiler:Languages\ChineseSimplified.isl"
Name: "english"; MessagesFile: "compiler:Default.isl"

[CustomMessages]
chinesesimplified.DeleteUserData=是否同时删除你的数据？%n%n包括下载的视频、字幕、学习笔记、登录状态和 API 设置，位置：%n%1%n%n选“否”会保留这些文件；以后重新安装到同一位置可以继续使用。
english.DeleteUserData=Also delete your data?%n%nThis includes downloaded videos, subtitles, study notes, sign-ins and API settings, stored in:%n%1%n%nChoose No to keep them; reinstalling to the same folder picks them up again.

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"

[InstallDelete]
; Replace the program folders completely so modules dropped by a new version cannot linger.
; Never listed here: outputs, .local, .env (user data) and other runtimes\ components.
Type: filesandordirs; Name: "{app}\python"
Type: filesandordirs; Name: "{app}\backend"
Type: filesandordirs; Name: "{app}\frontend"
Type: filesandordirs; Name: "{app}\configs"
Type: filesandordirs; Name: "{app}\runtimes\ffmpeg"

[Files]
Source: "{#StageDir}\*"; DestDir: "{app}"; Excludes: "\Video Parser.cmd"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{autoprograms}\{#AppName}"; Filename: "{app}\python\pythonw.exe"; Parameters: "-m video_parser.desktop"; WorkingDir: "{app}"; IconFilename: "{app}\{#AppIcon}"; AppUserModelID: "{#AppUserModelID}"
Name: "{autodesktop}\{#AppName}"; Filename: "{app}\python\pythonw.exe"; Parameters: "-m video_parser.desktop"; WorkingDir: "{app}"; IconFilename: "{app}\{#AppIcon}"; AppUserModelID: "{#AppUserModelID}"; Tasks: desktopicon

[Run]
Filename: "{app}\python\pythonw.exe"; Parameters: "-m video_parser.desktop"; WorkingDir: "{app}"; Description: "{cm:LaunchProgram,{#AppName}}"; Flags: nowait postinstall skipifsilent

[UninstallDelete]
; Python writes __pycache__ next to the code at run time; remove the program folders whole.
Type: filesandordirs; Name: "{app}\python"
Type: filesandordirs; Name: "{app}\backend"
Type: filesandordirs; Name: "{app}\frontend"
Type: filesandordirs; Name: "{app}\configs"
Type: filesandordirs; Name: "{app}\runtimes\ffmpeg"
Type: dirifempty; Name: "{app}\runtimes"

[Code]
function HasUserData(): Boolean;
begin
  Result := DirExists(ExpandConstant('{app}\outputs')) or DirExists(ExpandConstant('{app}\.local'))
    or FileExists(ExpandConstant('{app}\.env'));
end;

procedure CurUninstallStepChanged(CurUninstallStep: TUninstallStep);
begin
  if CurUninstallStep <> usPostUninstall then
    Exit;
  // Silent uninstalls (and upgrades by script) always keep the data.
  if HasUserData() and not UninstallSilent() then
    if MsgBox(FmtMessage(CustomMessage('DeleteUserData'), [ExpandConstant('{app}')]),
              mbConfirmation, MB_YESNO or MB_DEFBUTTON2) = IDYES then
    begin
      DelTree(ExpandConstant('{app}\outputs'), True, True, True);
      DelTree(ExpandConstant('{app}\.local'), True, True, True);
      DeleteFile(ExpandConstant('{app}\.env'));
    end;
  RemoveDir(ExpandConstant('{app}'));  // only succeeds when nothing is left
end;
