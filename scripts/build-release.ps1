<#
Build the portable Windows package (stage 1 of docs: desktop release plan).

Output (all under .local/work/release, nothing else is touched):
  VideoParser\                               staged app, runnable in place
  VideoParser-<version>-win64-portable.zip   zipped stage
  VideoParser-<version>-win64-setup.exe      installer (-Installer, Inno Setup 7)
  SHA256SUMS.txt
Downloads are pinned by URL and SHA-256 and cached in .local/work/release/cache. Inno Setup is unpacked in
portable mode into .local/work/release/tools (no uninstaller, no registry entries); set VIDEO_PARSER_ISCC
to use an existing ISCC.exe instead.
Only git-tracked or new non-ignored files are copied, so .env, .local, outputs and credentials never ship.
#>
param([switch]$SkipFrontend, [switch]$NoZip, [switch]$Installer)
$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'
$Root = Split-Path $PSScriptRoot -Parent
$Release = Join-Path $Root '.local\work\release'
$Cache = Join-Path $Release 'cache'
$Stage = Join-Path $Release 'VideoParser'

$PythonVersion = '3.13.12'   # must match the interpreter the main lock was tested with
$PythonZip = "python-$PythonVersion-embed-amd64.zip"
$PythonUrl = "https://www.python.org/ftp/python/$PythonVersion/$PythonZip"
$PythonSha = '76f238f606250c87c6beac75dccd35ee99070a13490555936abb6cb64ecce3d0'
# BtbN keeps month-end builds for two years; daily builds expire after 14 days.
$FfmpegZip = 'ffmpeg-n8.1.3-9-g29e619e767-win64-lgpl-shared-8.1.zip'
$FfmpegUrl = "https://github.com/BtbN/FFmpeg-Builds/releases/download/autobuild-2026-09-30-13-08/$FfmpegZip"
$FfmpegSha = '3e47bda1607740550141e37c0e49d1e5182b34699f15adfd137ee266d346811a'
$InnoVersion = '7.1.0'
$InnoExe = "innosetup-$InnoVersion-x64.exe"
$InnoUrl = "https://github.com/jrsoftware/issrc/releases/download/is-7_1_0/$InnoExe"
$InnoSha = '0362a383ed217d4c4239b5933866dd96d3eb2102737da92f80f6057a4b40df2f'

function Checked {
    param([string]$Program, [string[]]$Arguments)
    & $Program @Arguments
    if ($LASTEXITCODE -ne 0) { throw "Command failed: $Program (exit $LASTEXITCODE)" }
}

function Get-Pinned {
    param([string]$Url, [string]$Name, [string]$Sha)
    $Path = Join-Path $Cache $Name
    if (!(Test-Path -LiteralPath $Path)) {
        Write-Host "Downloading $Name"
        Invoke-WebRequest -Uri $Url -OutFile "$Path.part" -UseBasicParsing
        Move-Item -LiteralPath "$Path.part" -Destination $Path
    }
    $Actual = (Get-FileHash -LiteralPath $Path -Algorithm SHA256).Hash.ToLowerInvariant()
    if ($Actual -ne $Sha) { Remove-Item -LiteralPath $Path; throw "SHA-256 mismatch for $Name; the cached file was removed." }
    return $Path
}

function Copy-Listed {
    # Copy the given repository paths, limited to tracked + untracked-not-ignored files.
    param([string[]]$Paths)
    # git prints UTF-8; without this PowerShell decodes with the ANSI code page and mangles names like b站.py.
    $Previous = [Console]::OutputEncoding
    [Console]::OutputEncoding = [Text.UTF8Encoding]::new($false)
    try { $Files = & git -C $Root -c core.quotepath=false ls-files --cached --others --exclude-standard -- @Paths }
    finally { [Console]::OutputEncoding = $Previous }
    if ($LASTEXITCODE -ne 0) { throw 'git ls-files failed' }
    foreach ($File in $Files) {
        $Source = Join-Path $Root $File
        if (!(Test-Path -LiteralPath $Source -PathType Leaf)) { throw "Listed file not found (commit or restore it first): $File" }
        $Target = Join-Path $Stage $File
        New-Item -ItemType Directory -Force -Path (Split-Path $Target -Parent) | Out-Null
        Copy-Item -LiteralPath $Source -Destination $Target
    }
    return @($Files).Count
}

function Get-InnoCompiler {
    if ($env:VIDEO_PARSER_ISCC) { return $env:VIDEO_PARSER_ISCC }
    $Dir = Join-Path $Release "tools\innosetup-$InnoVersion"
    $Iscc = Join-Path $Dir 'ISCC.exe'
    if (!(Test-Path -LiteralPath $Iscc)) {
        $Setup = Get-Pinned $InnoUrl $InnoExe $InnoSha
        Write-Host "Unpacking Inno Setup $InnoVersion (portable mode)"
        $Arguments = @('/VERYSILENT', '/SUPPRESSMSGBOXES', '/NORESTART', '/CURRENTUSER', '/PORTABLE=1', '/NOICONS', '/TASKS=""', "/DIR=`"$Dir`"")
        $Process = Start-Process -FilePath $Setup -ArgumentList $Arguments -WindowStyle Hidden -Wait -PassThru
        if ($Process.ExitCode -ne 0 -or !(Test-Path -LiteralPath $Iscc)) { throw "Inno Setup unpack failed (exit $($Process.ExitCode))" }
    }
    return $Iscc
}

$Version = ([regex]::Match((Get-Content -Raw (Join-Path $Root 'pyproject.toml')), '(?m)^version\s*=\s*"([^"]+)"')).Groups[1].Value
if (!$Version) { throw 'Version not found in pyproject.toml' }
Write-Host "Video Parser $Version portable build"
New-Item -ItemType Directory -Force -Path $Cache | Out-Null

# 1. Frontend
if (!$SkipFrontend) {
    Push-Location (Join-Path $Root 'frontend')
    try {
        if (!(Test-Path 'node_modules\.bin\vite.cmd')) { Checked 'npm.cmd' @('ci', '--no-fund', '--no-audit') }
        Checked 'npm.cmd' @('run', 'build')
    } finally { Pop-Location }
}
if (!(Test-Path (Join-Path $Root 'frontend\dist\index.html'))) { throw 'frontend/dist is missing; build the frontend first.' }

# 2. Fresh stage (only ever inside .local\work\release)
$StageFull = [IO.Path]::GetFullPath($Stage)
if (!$StageFull.StartsWith([IO.Path]::GetFullPath($Release) + '\')) { throw 'Refusing to clean a stage outside .local\work\release' }
if (Test-Path -LiteralPath $Stage) { Remove-Item -LiteralPath $Stage -Recurse -Force }
New-Item -ItemType Directory -Force -Path $Stage | Out-Null

# 3. Embedded Python; ._pth makes the layout relocatable (no venv)
$PythonDir = Join-Path $Stage 'python'
Expand-Archive -LiteralPath (Get-Pinned $PythonUrl $PythonZip $PythonSha) -DestinationPath $PythonDir
$Pth = Get-ChildItem -LiteralPath $PythonDir -Filter 'python*._pth' | Select-Object -First 1
$ZipName = (Get-ChildItem -LiteralPath $PythonDir -Filter 'python3*.zip' | Select-Object -First 1).Name
Set-Content -LiteralPath $Pth.FullName -Encoding ascii -Value @($ZipName, '.', 'Lib\site-packages', '..\backend', 'import site')
$Python = Join-Path $PythonDir 'python.exe'
$SitePackages = Join-Path $PythonDir 'Lib\site-packages'
New-Item -ItemType Directory -Force -Path $SitePackages | Out-Null

# 4. Locked dependencies straight into site-packages
# A mirror can be injected for networks where pypi.org is unreachable; CI keeps the default.
$IndexUrl = if ($env:VIDEO_PARSER_PYPI_INDEX) { $env:VIDEO_PARSER_PYPI_INDEX } else { 'https://pypi.org/simple' }
Checked 'uv' @('pip', 'install', '--python', $Python, '--target', $SitePackages, '--no-deps', '--compile-bytecode',
    '-r', (Join-Path $Root 'configs\requirements\windows-main.lock.txt'),
    '-r', (Join-Path $Root 'configs\requirements\windows-desktop.lock.txt'),
    '--index-url', $IndexUrl)

# 5. Application files
$Count = Copy-Listed @('backend', 'configs', 'pyproject.toml', 'LICENSE', 'README.md')
Copy-Item -Recurse -LiteralPath (Join-Path $Root 'frontend\dist') -Destination (Join-Path $Stage 'frontend\dist')
Write-Host "Copied $Count repository files and frontend/dist"

# 6. LGPL ffmpeg / ffprobe
$FfmpegTemp = Join-Path $Cache 'ffmpeg-extract'
if (Test-Path -LiteralPath $FfmpegTemp) { Remove-Item -LiteralPath $FfmpegTemp -Recurse -Force }
Expand-Archive -LiteralPath (Get-Pinned $FfmpegUrl $FfmpegZip $FfmpegSha) -DestinationPath $FfmpegTemp
$FfmpegRoot = (Get-ChildItem -LiteralPath $FfmpegTemp -Directory | Select-Object -First 1).FullName
$Bin = Join-Path $Stage 'runtimes\ffmpeg\bin'
New-Item -ItemType Directory -Force -Path $Bin | Out-Null
Copy-Item -Path (Join-Path $FfmpegRoot 'bin\ffmpeg.exe'), (Join-Path $FfmpegRoot 'bin\ffprobe.exe') -Destination $Bin
Copy-Item -Path (Join-Path $FfmpegRoot 'bin\*.dll') -Destination $Bin
Copy-Item -LiteralPath (Join-Path $FfmpegRoot 'LICENSE.txt') -Destination (Join-Path $Stage 'runtimes\ffmpeg\LICENSE.txt')
Remove-Item -LiteralPath $FfmpegTemp -Recurse -Force

# 7. Start file and third-party notices
Set-Content -LiteralPath (Join-Path $Stage 'Video Parser.cmd') -Encoding ascii -Value @(
    '@echo off', 'start "" "%~dp0python\pythonw.exe" -m video_parser.desktop')
Checked $Python @('-X', 'utf8', (Join-Path $Root 'scripts\write-third-party-notices.py'), $Stage, $FfmpegZip, $FfmpegUrl)

# 8. Smoke test in place, then remove everything the test created
Push-Location $Stage
try { Checked $Python @('-m', 'video_parser.desktop', '--check') } finally { Pop-Location }
foreach ($Created in @('.local', 'outputs', 'cache', 'logs', 'var')) {
    $Path = Join-Path $Stage $Created
    if (Test-Path -LiteralPath $Path) { Remove-Item -LiteralPath $Path -Recurse -Force }
}

$Size = (Get-ChildItem -LiteralPath $Stage -Recurse -File | Measure-Object Length -Sum).Sum / 1MB
Write-Host ('Stage: {0} ({1:N0} MB)' -f $Stage, $Size)

# 9. Zip
$Outputs = @()
if (!$NoZip) {
    $ZipPath = Join-Path $Release "VideoParser-$Version-win64-portable.zip"
    if (Test-Path -LiteralPath $ZipPath) { Remove-Item -LiteralPath $ZipPath }
    # Use the bundled standard library; Windows tar has crashed while creating ZIPs on hosted runners.
    Checked $Python @('-m', 'zipfile', '-c', $ZipPath, $Stage)
    Write-Host ('Zip: {0} ({1:N0} MB)' -f $ZipPath, ((Get-Item -LiteralPath $ZipPath).Length / 1MB))
    $Outputs += $ZipPath
}

# 10. Installer (Inno Setup 7, per-user, scripts\installer\VideoParser.iss)
if ($Installer) {
    $SetupPath = Join-Path $Release "VideoParser-$Version-win64-setup.exe"
    if (Test-Path -LiteralPath $SetupPath) { Remove-Item -LiteralPath $SetupPath }
    Checked (Get-InnoCompiler) @('/Q', "/DAppVersion=$Version", "/DStageDir=$Stage", "/DOutputDir=$Release",
        (Join-Path $Root 'scripts\installer\VideoParser.iss'))
    if (!(Test-Path -LiteralPath $SetupPath)) { throw "Installer not produced: $SetupPath" }
    Write-Host ('Installer: {0} ({1:N0} MB)' -f $SetupPath, ((Get-Item -LiteralPath $SetupPath).Length / 1MB))
    $Outputs += $SetupPath
}

# 11. Checksums for whatever was produced in this run
if ($Outputs) {
    $Lines = foreach ($Output in $Outputs) {
        "$((Get-FileHash -LiteralPath $Output -Algorithm SHA256).Hash.ToLowerInvariant())  $(Split-Path $Output -Leaf)"
    }
    Set-Content -LiteralPath (Join-Path $Release 'SHA256SUMS.txt') -Encoding ascii -Value $Lines
}
