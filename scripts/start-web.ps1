param(
    [ValidateRange(1024, 65535)][int]$Port = 15001,
    [switch]$OpenBrowser
)
$ErrorActionPreference = 'Stop'
$root = Split-Path $PSScriptRoot -Parent
$python = Join-Path $root '.venv\Scripts\python.exe'
$url = "http://127.0.0.1:$Port"
if (-not (Test-Path -LiteralPath $python)) {
    throw 'Project .venv not found. Create it and install requirements.txt first.'
}
$expectedInstance = (& $python -X utf8 -c "import sys; from pathlib import Path; sys.path.insert(0, str(Path(sys.argv[1]) / 'backend')); from video_parser.core.paths import instance_id; print(instance_id(Path(sys.argv[1])))" $root | Out-String).Trim()
if ($LASTEXITCODE -ne 0 -or $expectedInstance -notmatch '^[a-f0-9]{12}$') { throw 'Cannot identify this project instance.' }
$connection = New-Object System.Net.Sockets.TcpClient
$occupied = $false
try { $connection.Connect('127.0.0.1', $Port); $occupied = $true }
catch [System.Net.Sockets.SocketException] { }
finally { $connection.Dispose() }
# Local checks must not be diverted by a system proxy: a VPN on this machine would otherwise
# answer for 127.0.0.1 and the launcher would think its own server is unreachable.
function Get-LocalJson([string]$address) {
    $request = [System.Net.WebRequest]::Create($address)
    $request.Proxy = $null
    $request.Timeout = 3000
    $response = $request.GetResponse()
    try {
        $reader = New-Object System.IO.StreamReader($response.GetResponseStream(), [System.Text.Encoding]::UTF8)
        try { return ($reader.ReadToEnd() | ConvertFrom-Json) } finally { $reader.Close() }
    }
    finally { $response.Close() }
}
if ($occupied) {
    try {
        $health = Get-LocalJson "$url/health"
        $config = Get-LocalJson "$url/api/workspace/config"
        if ($health.status -eq 'healthy' -and $health.instance -eq $expectedInstance -and $config.PSObject.Properties.Name -contains 'transcription_ready') {
            Write-Host "Video Parser is already running: $url"
            if ($OpenBrowser) { Start-Process $url }
            return
        }
    }
    catch { }
    throw "Port $Port is occupied by another service. No process was stopped."
}
if (-not (Get-Command npm.cmd -ErrorAction SilentlyContinue)) {
    throw 'Node.js >=22.12 and npm are required to build the React frontend.'
}
Push-Location -LiteralPath $root
try {
    if (-not (Test-Path -LiteralPath (Join-Path $root 'frontend\node_modules\.bin\vite.cmd'))) {
        & npm.cmd --prefix frontend ci --no-fund --no-audit
        if ($LASTEXITCODE -ne 0) { throw 'Frontend dependency installation failed.' }
    }
    & npm.cmd --prefix frontend run build
    if ($LASTEXITCODE -ne 0) { throw 'Frontend typecheck or build failed.' }
    Write-Host "Video Parser: $url"
    Write-Host 'Keep this window open while using the workspace. Ctrl+C stops the server.'
    # The server opens the browser itself (see maybe_open_browser in video_parser.web.app): no second
    # PowerShell has to be spawned, which some security policies block from a hidden process.
    if ($OpenBrowser) {
        $env:VIDEO_PARSER_OPEN_BROWSER = '1'
        $env:VIDEO_PARSER_OPEN_URL = $url
    }
    & $python -X utf8 -m uvicorn video_parser.web.app:app --host 127.0.0.1 --port $Port --no-proxy-headers
    if ($LASTEXITCODE -ne 0) { throw 'Web server failed. Check the port and Python dependencies.' }
}
finally {
    Remove-Item Env:\VIDEO_PARSER_OPEN_BROWSER -ErrorAction SilentlyContinue
    Remove-Item Env:\VIDEO_PARSER_OPEN_URL -ErrorAction SilentlyContinue
    Pop-Location
}
