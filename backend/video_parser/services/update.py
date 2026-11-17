"""Check and apply verified Windows releases without exposing a general-purpose downloader."""
from __future__ import annotations

import hashlib
import hmac
import json
import os
from pathlib import Path
import re
import shutil
import stat
import subprocess
import threading
import time
from typing import Any, Callable
from uuid import uuid4
import zipfile

import requests

from video_parser.core.paths import ROOT, canonical

REPOSITORY = 'HP-Patience/video-parser'
LATEST_RELEASE_URL = f'https://api.github.com/repos/{REPOSITORY}/releases/latest'
RELEASES_URL = f'https://github.com/{REPOSITORY}/releases'
INSTALLER_PREFIX = 'VideoParser-'
INSTALLER_SUFFIX = '-win64-setup.exe'
PORTABLE_SUFFIX = '-win64-portable.zip'
CHECKSUM_ASSET = 'SHA256SUMS.txt'
UPDATE_DIR = ROOT / '.local' / 'work' / 'updates'
MAX_JSON_BYTES = 1024 * 1024
MAX_DOWNLOAD_BYTES = 2 * 1024 * 1024 * 1024
RESULT_FILE = UPDATE_DIR / 'update-result.json'
INSTALLER_KEY = r'Software\Microsoft\Windows\CurrentVersion\Uninstall\{A156B253-F151-4B69-BABA-58D608B78569}_is1'
_VERSION = re.compile(r'^v?(\d+)\.(\d+)\.(\d+)$')
_PORTABLE_DIRS = {'backend', 'frontend', 'python', 'configs'}
_PORTABLE_ROOT_FILES = {
    'video parser.cmd', 'pyproject.toml', 'license.txt', 'license',
    'third_party_notices.txt', 'third-party-notices.txt', 'readme.md',
}


class UpdateError(RuntimeError):
    """A user-facing update failure."""


class UpdateUnavailable(UpdateError):
    pass


class UpdateNotSupported(UpdateError):
    pass


class UpdateBusy(UpdateError):
    pass


_update_guard = threading.Lock()
_update_active = False
_update_snapshot: dict[str, Any] = {
    'state': 'idle', 'version': None, 'downloaded_bytes': 0,
    'total_bytes': None, 'error': None,
}


def version_tuple(value: str) -> tuple[int, int, int] | None:
    match = _VERSION.fullmatch(value.strip())
    return tuple(map(int, match.groups())) if match else None


def _release_version(tag: object) -> str:
    value = str(tag or '').strip().lstrip('v')
    if version_tuple(value) is None:
        raise UpdateError('最新发布版本号格式无效。')
    return value


def _get_json(url: str) -> dict[str, Any] | None:
    try:
        response = requests.get(url, timeout=(5, 20), headers={
            'Accept': 'application/vnd.github+json',
            'User-Agent': 'video-parser-updater',
            'X-GitHub-Api-Version': '2022-11-28',
        })
    except requests.RequestException as error:
        raise UpdateError('无法连接更新服务，请检查网络后重试。') from error
    if response.status_code == 404:
        return None
    if response.status_code != 200:
        raise UpdateError(f'更新服务返回 HTTP {response.status_code}，请稍后重试。')
    if len(response.content) > MAX_JSON_BYTES:
        raise UpdateError('更新信息异常，已停止处理。')
    try:
        data = response.json()
    except (ValueError, json.JSONDecodeError) as error:
        raise UpdateError('更新信息格式异常，已停止处理。') from error
    if not isinstance(data, dict):
        raise UpdateError('更新信息格式异常，已停止处理。')
    return data


def _installed_root() -> Path | None:
    """Return the installed copy whose files are running, never a portable or source checkout."""
    if os.name != 'nt':
        return None
    import winreg

    views = [0, getattr(winreg, 'KEY_WOW64_64KEY', 0), getattr(winreg, 'KEY_WOW64_32KEY', 0)]
    for view in dict.fromkeys(views):
        try:
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, INSTALLER_KEY, 0, winreg.KEY_READ | view) as key:
                location = winreg.QueryValueEx(key, 'InstallLocation')[0]
        except OSError:
            continue
        if location:
            try:
                return canonical(Path(location))
            except OSError:
                continue
    return None


def _same_path(left: Path, right: Path) -> bool:
    try:
        return left.samefile(right)
    except OSError:
        return os.path.normcase(str(left)) == os.path.normcase(str(right))


def is_packaged_root(root: Path = ROOT, platform: str | None = None) -> bool:
    """Recognize only the release layout; source checkouts and arbitrary folders stay unsupported."""
    if (platform or os.name) != 'nt':
        return False
    root = Path(root)
    return (
        not (root / '.git').exists()
        and (root / 'python' / 'python.exe').is_file()
        and (root / 'backend' / 'video_parser').is_dir()
        and (root / 'pyproject.toml').is_file()
    )


def installation_kind(root: Path = ROOT, platform: str | None = None) -> str:
    """Return installed, portable, source, or unsupported for an injectable root/platform."""
    if (platform or os.name) != 'nt':
        return 'unsupported'
    current = canonical(root)
    if (current / '.git').exists():
        return 'source'
    installed = _installed_root()
    if installed is not None and _same_path(installed, current):
        return 'installed'
    if is_packaged_root(current, platform):
        return 'portable'
    return 'unsupported'


def install_supported(root: Path = ROOT) -> bool:
    """Whether this running copy can be updated in place."""
    return installation_kind(root) in {'installed', 'portable'}


def _assets(release: dict[str, Any]) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
    rows = release.get('assets')
    if not isinstance(rows, list):
        return None, None
    assets = {row.get('name'): row for row in rows if isinstance(row, dict) and isinstance(row.get('name'), str)}
    version = _release_version(release.get('tag_name'))
    return assets.get(f'{INSTALLER_PREFIX}{version}{INSTALLER_SUFFIX}'), assets.get(CHECKSUM_ASSET)


def _portable_asset(release: dict[str, Any]) -> dict[str, Any] | None:
    rows = release.get('assets')
    if not isinstance(rows, list):
        return None
    version = _release_version(release.get('tag_name'))
    name = f'{INSTALLER_PREFIX}{version}{PORTABLE_SUFFIX}'
    return next((row for row in rows if isinstance(row, dict) and row.get('name') == name), None)


def _release_result(current_version: str, release: dict[str, Any] | None, root: Path = ROOT) -> dict[str, Any]:
    current = version_tuple(current_version)
    if release is None:
        return {'current_version': current_version, 'latest_version': None, 'release_found': False,
                'available': False, 'can_install': False, 'install_reason': 'no_release', 'release_url': RELEASES_URL}
    latest = _release_version(release.get('tag_name'))
    latest_key = version_tuple(latest)
    available = bool(current and latest_key and latest_key > current)
    installer, checksums = _assets(release)
    portable = _portable_asset(release)
    mode = installation_kind(root)
    supported = mode in {'installed', 'portable'}
    asset = installer if mode == 'installed' else portable if mode == 'portable' else None
    can_install = bool(available and supported and asset and checksums)
    if not supported:
        reason = mode
    elif not (asset and checksums):
        reason = 'assets_missing'
    elif not available:
        reason = 'up_to_date'
    else:
        reason = mode
    html_url = str(release.get('html_url') or '')
    return {'current_version': current_version, 'latest_version': latest, 'release_found': True,
            'available': available, 'can_install': can_install, 'install_reason': reason,
            'release_url': html_url if html_url.startswith(RELEASES_URL + '/') else RELEASES_URL,
            'published_at': release.get('published_at')}


def check(current_version: str) -> dict[str, Any]:
    if version_tuple(current_version) is None:
        raise UpdateError('当前版本号格式无效，无法检查更新。')
    return _release_result(current_version, _get_json(LATEST_RELEASE_URL))


def _valid_asset_url(value: object) -> str:
    url = str(value or '')
    prefix = f'https://github.com/{REPOSITORY}/releases/download/'
    if not url.startswith(prefix) or '?' in url or '#' in url:
        raise UpdateError('更新包地址异常，已停止处理。')
    return url


def _set_status(state: str, *, version: str | None = None, downloaded: int | None = None,
               total: int | None = None, error: str | None = None) -> None:
    with _update_guard:
        _update_snapshot['state'] = state
        if version is not None:
            _update_snapshot['version'] = version
        if downloaded is not None:
            _update_snapshot['downloaded_bytes'] = downloaded
        if total is not None:
            _update_snapshot['total_bytes'] = total
        if error is not None or state in {'checking', 'downloading', 'verifying', 'applying'}:
            _update_snapshot['error'] = error


def status() -> dict[str, Any]:
    with _update_guard:
        snapshot = dict(_update_snapshot)
    if RESULT_FILE.is_file():
        try:
            result = json.loads(RESULT_FILE.read_text(encoding='utf-8-sig'))
            if result.get('state') == 'failed':
                snapshot.update({'state': 'failed', 'error': str(result.get('error') or '更新失败，请查看更新日志。')})
        except (OSError, ValueError, TypeError):
            pass
    return snapshot


def _download(url: str, destination: Path, expected_size: int | None = None,
              progress: Callable[[int, int | None], None] | None = None) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_name('.' + uuid4().hex + '.tmp')
    total = 0
    reported_total = expected_size
    try:
        with requests.get(url, stream=True, timeout=(10, 60), headers={'User-Agent': 'video-parser-updater'}) as response:
            response.raise_for_status()
            length = response.headers.get('Content-Length')
            if length and length.isdigit():
                reported_total = int(length)
                if reported_total > MAX_DOWNLOAD_BYTES:
                    raise UpdateError('更新包超过允许大小，已停止下载。')
            with temporary.open('wb') as stream:
                for chunk in response.iter_content(chunk_size=1024 * 1024):
                    if not chunk:
                        continue
                    total += len(chunk)
                    if total > MAX_DOWNLOAD_BYTES:
                        raise UpdateError('更新包超过允许大小，已停止下载。')
                    stream.write(chunk)
                    if progress:
                        progress(total, reported_total)
                stream.flush()
                os.fsync(stream.fileno())
        if expected_size is not None and total != expected_size:
            raise UpdateError('更新包大小与发布记录不一致，已停止处理。')
        os.replace(temporary, destination)
    except requests.RequestException as error:
        raise UpdateError('下载更新包失败，请检查网络后重试。') from error
    finally:
        temporary.unlink(missing_ok=True)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open('rb') as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b''):
            digest.update(chunk)
    return digest.hexdigest()


def _expected_checksum(path: Path, asset_name: object) -> str:
    try:
        lines = path.read_text(encoding='utf-8').splitlines()
    except OSError as error:
        raise UpdateError('无法读取更新校验文件。') from error
    for line in lines:
        match = re.fullmatch(r'([0-9a-fA-F]{64})\s+\*?(.+)', line.strip())
        if match and match.group(2).strip() == str(asset_name):
            return match.group(1).lower()
    raise UpdateError('更新校验文件中缺少安装包记录。')


def _write_helper(installer: Path, install_root: Path, log: Path) -> Path:
    UPDATE_DIR.mkdir(parents=True, exist_ok=True)
    helper = UPDATE_DIR / 'apply-update.ps1'
    helper.write_text(r'''param(
  [Parameter(Mandatory=$true)][int]$ParentId,
  [Parameter(Mandatory=$true)][string]$Installer,
  [Parameter(Mandatory=$true)][string]$InstallRoot,
  [Parameter(Mandatory=$true)][string]$LogPath
)
$ErrorActionPreference = 'Stop'
try {
  $root = [IO.Path]::GetFullPath($InstallRoot)
  $deadline = (Get-Date).AddMinutes(2)
  function Get-RootProcesses {
    Get-CimInstance Win32_Process -ErrorAction SilentlyContinue | Where-Object {
      $_.ProcessId -ne $PID -and $_.ExecutablePath -and
      ([IO.Path]::GetFullPath($_.ExecutablePath)).StartsWith($root + '\', [StringComparison]::OrdinalIgnoreCase)
    }
  }
  while (((Get-Process -Id $ParentId -ErrorAction SilentlyContinue) -or (Get-RootProcesses)) -and ((Get-Date) -lt $deadline)) {
    Start-Sleep -Milliseconds 250
  }
  if ((Get-Process -Id $ParentId -ErrorAction SilentlyContinue) -or (Get-RootProcesses)) { throw '主程序未在超时时间内退出，未修改安装目录。' }
  $arguments = @('/VERYSILENT', '/SUPPRESSMSGBOXES', '/NORESTART', '/CLOSEAPPLICATIONS', ('/DIR="' + $root + '"'))
  $result = Start-Process -FilePath $Installer -ArgumentList $arguments -PassThru -Wait -WindowStyle Hidden
  if ($result.ExitCode -ne 0) { throw "Installer exited with code $($result.ExitCode)" }
  Start-Sleep -Milliseconds 500
  $executable = Join-Path $root 'python\pythonw.exe'
  if (!(Test-Path -LiteralPath $executable)) { throw "Application executable is missing: $executable" }
  Start-Process -FilePath $executable -ArgumentList '-m', 'video_parser.desktop' -WorkingDirectory $root -WindowStyle Hidden
} catch {
  $resultPath = Join-Path (Split-Path $LogPath) 'update-result.json'
  @{state='failed'; error=$_.Exception.Message} | ConvertTo-Json | Set-Content -LiteralPath $resultPath -Encoding UTF8
  Add-Content -LiteralPath $LogPath -Value ((Get-Date).ToString('s') + ' ' + $_.Exception.Message) -Encoding UTF8
''', encoding='utf-8-sig')
    return helper


def _verified_asset(release: dict[str, Any], asset: dict[str, Any], checksums: dict[str, Any], suffix: str,
                    progress: Callable[[int, int | None], None] | None = None) -> Path:
    version = _release_version(release.get('tag_name'))
    asset_url = _valid_asset_url(asset.get('browser_download_url'))
    checksums_url = _valid_asset_url(checksums.get('browser_download_url'))
    UPDATE_DIR.mkdir(parents=True, exist_ok=True)
    checksums_path = UPDATE_DIR / CHECKSUM_ASSET
    _download(checksums_url, checksums_path)
    expected = _expected_checksum(checksums_path, asset.get('name'))
    name = str(asset.get('name') or '')
    if name != f'{INSTALLER_PREFIX}{version}{suffix}':
        raise UpdateError('更新包名称与版本不一致，已停止处理。')
    destination = UPDATE_DIR / name
    size = asset.get('size')
    if isinstance(size, int) and size > MAX_DOWNLOAD_BYTES:
        raise UpdateError('更新包超过允许大小，已停止下载。')
    _download(asset_url, destination, int(size) if isinstance(size, int) and size > 0 else None, progress)
    if not hmac.compare_digest(_sha256(destination), expected):
        destination.unlink(missing_ok=True)
        raise UpdateError('更新包校验失败，已删除下载文件。')
    return destination


def _verified_installer(release: dict[str, Any], progress=None) -> Path:
    installer, checksums = _assets(release)
    if not installer or not checksums:
        raise UpdateNotSupported('该版本没有完整的 Windows 安装包。')
    return _verified_asset(release, installer, checksums, INSTALLER_SUFFIX, progress)


def _verified_portable(release: dict[str, Any], progress=None) -> Path:
    portable, checksums = _portable_asset(release), _assets(release)[1]
    if not portable or not checksums:
        raise UpdateNotSupported('该版本没有完整的 Windows 便携包。')
    return _verified_asset(release, portable, checksums, PORTABLE_SUFFIX, progress)


def _archive_members(path: Path) -> list[tuple[zipfile.ZipInfo, str]]:
    try:
        archive = zipfile.ZipFile(path)
    except (OSError, zipfile.BadZipFile) as error:
        raise UpdateError('更新压缩包损坏，已停止处理。') from error
    with archive:
        infos = archive.infolist()
        if not infos:
            raise UpdateError('更新压缩包为空，已停止处理。')
        raw = []
        total = 0
        for info in infos:
            normalized = info.filename.replace('\\', '/')
            directory_name = normalized.endswith('/')
            clean_name = normalized.rstrip('/')
            parts = clean_name.split('/') if clean_name else []
            device_names = {'con', 'prn', 'aux', 'nul', *(f'com{i}' for i in range(1, 10)), *(f'lpt{i}' for i in range(1, 10))}
            if normalized.startswith('/') or not parts or any(part in ('', '.', '..') for part in parts):
                raise UpdateError('更新压缩包包含非法路径，已停止处理。')
            if len(normalized) > 500 or any(':' in part or part.endswith((' ', '.')) or part.split('.')[0].casefold() in device_names for part in parts):
                raise UpdateError('更新压缩包包含非法路径，已停止处理。')
            attrs = info.external_attr
            if stat.S_IFMT(attrs >> 16) == stat.S_IFLNK or attrs & 0x400 or info.flag_bits & 1:
                raise UpdateError('更新压缩包包含不安全文件，已停止处理。')
            total += info.file_size
            if total > MAX_DOWNLOAD_BYTES or info.file_size > MAX_DOWNLOAD_BYTES:
                raise UpdateError('更新压缩包超过允许大小，已停止处理。')
            raw.append((info, parts))
        first = {parts[0].casefold() for _, parts in raw}
        strip = len(first) == 1 and next(iter(first)) not in _PORTABLE_DIRS and next(iter(first)) not in {
            item.split('.')[0] for item in _PORTABLE_ROOT_FILES
        }
        result = []
        seen: set[str] = set()
        kinds: dict[str, bool] = {}
        for info, parts in raw:
            if strip:
                parts = parts[1:]
            if not parts:
                continue
            relative = '/'.join(parts).rstrip('/')
            top = parts[0].casefold()
            allowed = (top in _PORTABLE_DIRS
                       or (top == 'runtimes' and (len(parts) == 1 or (len(parts) > 1 and parts[1].casefold() == 'ffmpeg')))
                       or (len(parts) == 1 and relative.casefold() in _PORTABLE_ROOT_FILES))
            if not allowed:
                raise UpdateError('更新压缩包包含未允许的文件，已停止处理。')
            key = relative.casefold()
            directory = info.is_dir() or info.filename.endswith(('/', '\\'))
            if key in seen or (key in kinds and kinds[key] != directory):
                raise UpdateError('更新压缩包包含重复文件，已停止处理。')
            for index in range(1, len(parts)):
                parent_key = '/'.join(parts[:index]).casefold()
                if parent_key in kinds and not kinds[parent_key]:
                    raise UpdateError('更新压缩包包含路径冲突，已停止处理。')
            seen.add(key)
            kinds[key] = directory
            result.append((info, relative))
        required = {'python/python.exe', 'backend/video_parser', 'pyproject.toml'}
        names = {name.casefold() for _, name in result}
        if not {'python/python.exe', 'pyproject.toml'} <= names or not any(
                name.startswith('backend/video_parser/') for name in names):
            raise UpdateError('更新压缩包缺少应用文件，已停止处理。')
        return result


def _extract_portable(path: Path, stage: Path) -> list[str]:
    members = _archive_members(path)
    files = []
    with zipfile.ZipFile(path) as archive:
        for info, relative in members:
            target = stage / Path(relative)
            if info.is_dir() or info.filename.endswith(('/', '\\')):
                target.mkdir(parents=True, exist_ok=True)
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            with archive.open(info) as source, target.open('xb') as destination:
                shutil.copyfileobj(source, destination, length=1024 * 1024)
            files.append(relative)
    return files


def _prepare_portable(archive: Path, root: Path, version: str) -> Path:
    UPDATE_DIR.mkdir(parents=True, exist_ok=True)
    stage = UPDATE_DIR / f'.stage-{uuid4().hex}'
    try:
        _extract_portable(archive, stage)
        _healthcheck(stage, version, smoke=True)
        return stage
    except Exception:
        shutil.rmtree(stage, ignore_errors=True)
        raise


def _write_portable_helper(archive: Path, install_root: Path, stage: Path, log: Path) -> tuple[Path, Path]:
    UPDATE_DIR.mkdir(parents=True, exist_ok=True)
    runner_root = UPDATE_DIR / 'update-runner'
    package = runner_root / 'video_parser'
    (package / 'services').mkdir(parents=True, exist_ok=True)
    (package / 'core').mkdir(parents=True, exist_ok=True)
    for init in (package / '__init__.py', package / 'services' / '__init__.py', package / 'core' / '__init__.py'):
        init.write_text('', encoding='utf-8')
    (package / 'core' / 'paths.py').write_text('\n'.join([
        'import os', 'from pathlib import Path',
        'ROOT = Path(os.environ["VIDEO_PARSER_UPDATE_ROOT"])',
        'def canonical(path): return Path(path).resolve()', '',
    ]), encoding='utf-8')
    (package / 'services' / 'update.py').write_text(Path(__file__).read_text(encoding='utf-8'), encoding='utf-8')
    runner = runner_root / 'run-portable.py'
    runner.write_text('''from pathlib import Path\nimport os\nimport sys\nrunner_root, archive, root, version, stage_root = Path(sys.argv[1]), Path(sys.argv[2]), Path(sys.argv[3]), sys.argv[4], Path(sys.argv[5])\nos.environ["VIDEO_PARSER_UPDATE_ROOT"] = str(root)\nos.environ["VIDEO_PARSER_UPDATE_WORK"] = str(runner_root.parent)\nsys.path.insert(0, str(runner_root))\nfrom video_parser.services import update\nupdate.UPDATE_DIR = runner_root.parent\nupdate.RESULT_FILE = update.UPDATE_DIR / "update-result.json"\nupdate._apply_portable(archive, root, version)\n''', encoding='utf-8-sig')
    helper = UPDATE_DIR / 'apply-portable.ps1'
    helper.write_text(r'''param(
  [Parameter(Mandatory=$true)][int]$ParentId,
  [Parameter(Mandatory=$true)][string]$Python,
  [Parameter(Mandatory=$true)][string]$Runner,
  [Parameter(Mandatory=$true)][string]$Archive,
  [Parameter(Mandatory=$true)][string]$InstallRoot,
  [Parameter(Mandatory=$true)][string]$Version,
  [Parameter(Mandatory=$true)][string]$StageRoot,
  [Parameter(Mandatory=$true)][string]$LogPath
)
$ErrorActionPreference = 'Stop'
$canRestart = $false
try {
  $root = [IO.Path]::GetFullPath($InstallRoot)
  $deadline = (Get-Date).AddMinutes(2)
  function Get-RootProcesses {
    Get-CimInstance Win32_Process -ErrorAction SilentlyContinue | Where-Object {
      $_.ProcessId -ne $PID -and $_.ExecutablePath -and
      ([IO.Path]::GetFullPath($_.ExecutablePath)).StartsWith($root + '\', [StringComparison]::OrdinalIgnoreCase)
    }
  }
  while (((Get-Process -Id $ParentId -ErrorAction SilentlyContinue) -or (Get-RootProcesses)) -and ((Get-Date) -lt $deadline)) { Start-Sleep -Milliseconds 250 }
  if ((Get-Process -Id $ParentId -ErrorAction SilentlyContinue) -or (Get-RootProcesses)) { throw '主程序未在超时时间内退出，未修改安装目录。' }
  $canRestart = $true
  $runnerRoot = Split-Path -Parent $Runner
  $arguments = @(('"' + $Runner + '"'), ('"' + $runnerRoot + '"'), ('"' + $Archive + '"'), ('"' + $root + '"'), ('"' + $Version + '"'), ('"' + $StageRoot + '"'))
  $result = Start-Process -FilePath $Python -ArgumentList $arguments -PassThru -Wait -WindowStyle Hidden -RedirectStandardError (Join-Path (Split-Path $LogPath) 'update-helper-error.log')
  if ($result.ExitCode -ne 0) { throw "Portable transaction exited with code $($result.ExitCode)" }
} catch {
  $resultPath = Join-Path (Split-Path $LogPath) 'update-result.json'
  @{state='failed'; error=$_.Exception.Message} | ConvertTo-Json | Set-Content -LiteralPath $resultPath -Encoding UTF8
  Add-Content -LiteralPath $LogPath -Value ((Get-Date).ToString('s') + ' ' + $_.Exception.Message) -Encoding UTF8
} finally {
  if ($canRestart) {
    $executable = Join-Path $root 'python\pythonw.exe'
    if (Test-Path -LiteralPath $executable) { Start-Process -FilePath $executable -ArgumentList '-m', 'video_parser.desktop' -WorkingDirectory $root -WindowStyle Hidden }
  }
  if (([IO.Path]::GetFileName($StageRoot) -match '^\.stage-[0-9a-fA-F]{32}$') -and
      ([IO.Path]::GetFullPath((Split-Path -Parent $StageRoot)) -eq [IO.Path]::GetFullPath((Split-Path -Parent $LogPath)))) {
    Remove-Item -LiteralPath $StageRoot -Recurse -Force -ErrorAction SilentlyContinue
  }
}
''', encoding='utf-8-sig')
    return helper, runner


def _healthcheck(root: Path, version: str, *, smoke: bool = False) -> None:
    if not is_packaged_root(root, 'nt'):
        raise UpdateError('更新后应用目录校验失败，已回滚。')
    try:
        import tomllib
        actual = tomllib.loads((root / 'pyproject.toml').read_text(encoding='utf-8'))['project']['version']
    except (OSError, KeyError, ValueError, TypeError) as error:
        raise UpdateError('更新后版本文件校验失败，已回滚。') from error
    if str(actual) != version:
        raise UpdateError('更新后版本号校验失败，已回滚。')
    if smoke:
        environment = os.environ.copy()
        environment['PYTHONPATH'] = str(root / 'backend') + os.pathsep + environment.get('PYTHONPATH', '')
        try:
            subprocess.run([str(root / 'python' / 'python.exe'), '-m', 'video_parser.desktop', '--check'],
                           cwd=str(root), env=environment, timeout=45,
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=True)
        except (OSError, subprocess.SubprocessError) as error:
            raise UpdateError('新版本启动检查失败，已停止更新。') from error


def _apply_portable(archive: Path, root: Path, version: str) -> None:
    root = canonical(root)
    if not is_packaged_root(root, 'nt'):
        raise UpdateNotSupported('当前不是可自动更新的便携版。')
    UPDATE_DIR.mkdir(parents=True, exist_ok=True)
    stage = UPDATE_DIR / f'.stage-{uuid4().hex}'
    backup = UPDATE_DIR / f'.backup-{uuid4().hex}'
    changed: list[tuple[Path, Path | None]] = []
    rollback_error = None
    try:
        files = _extract_portable(archive, stage)
        for relative in files:
            target = root / relative
            cursor = root
            for part in Path(relative).parts[:-1]:
                cursor = cursor / part
                if cursor.is_symlink() or (hasattr(cursor, 'is_junction') and cursor.is_junction()):
                    raise UpdateError('应用目录包含不安全链接，已停止处理。')
            if target.is_dir() or target.is_symlink() and not target.is_file():
                raise UpdateError('更新目标与现有目录冲突，已停止处理。')
            previous = None
            if target.exists() or target.is_symlink():
                previous = backup / relative
                previous.parent.mkdir(parents=True, exist_ok=True)
                os.replace(target, previous)
            changed.append((target, previous))
            target.parent.mkdir(parents=True, exist_ok=True)
            os.replace(stage / relative, target)
        _healthcheck(root, version, smoke=True)
    except Exception as error:
        for target, previous in reversed(changed):
            try:
                if target.is_dir() and not target.is_symlink():
                    shutil.rmtree(target)
                else:
                    target.unlink(missing_ok=True)
                if previous is not None and previous.exists():
                    previous.parent.mkdir(parents=True, exist_ok=True)
                    os.replace(previous, target)
            except OSError as restore_error:
                rollback_error = restore_error
        if rollback_error is not None:
            recovery = UPDATE_DIR / f'recovery-{uuid4().hex}'
            try:
                os.replace(backup, recovery)
            except OSError:
                recovery = backup
            message = f'更新回滚未完成，请保留备份目录并联系支持：{recovery}'
            _set_status('failed', error=message)
            raise UpdateError(message) from rollback_error
        raise error
    finally:
        shutil.rmtree(stage, ignore_errors=True)
        if rollback_error is None:
            shutil.rmtree(backup, ignore_errors=True)


def _task_active() -> bool:
    from video_parser.services import task_runtime
    return task_runtime.any_active()


def _worker(current_version: str, root: Path) -> None:
    exclusive = None
    stage = None
    handed_off = False
    try:
        _set_status('checking')
        release = _get_json(LATEST_RELEASE_URL)
        snapshot = _release_result(current_version, release, root)
        latest = snapshot.get('latest_version')
        _set_status('checking', version=latest)
        if not snapshot['available']:
            raise UpdateUnavailable('当前已是最新版本。')
        if not snapshot['can_install']:
            reason = snapshot['install_reason']
            messages = {'source': '当前是源码目录，不能覆盖更新，请前往发布页下载。',
                        'unsupported': '当前目录不是可自动更新的安装版，请前往发布页下载。'}
            raise UpdateNotSupported(messages.get(reason, '当前版本缺少可验证的更新包，请前往发布页下载。'))
        if _task_active():
            raise UpdateBusy('仍有任务进行中，请全部完成后再更新。')
        mode = installation_kind(root)
        _set_status('downloading', version=latest)
        progress = lambda done, total: _set_status('downloading', downloaded=done, total=total)
        package = _verified_installer(release, progress) if mode == 'installed' else _verified_portable(release, progress)
        _set_status('verifying', version=latest)
        if mode == 'portable':
            stage = _prepare_portable(package, root, latest)
        else:
            stage = None
        from video_parser.services import task_runtime
        try:
            exclusive = task_runtime.exclusive_operation()
            exclusive.__enter__()
        except BlockingIOError as error:
            raise UpdateBusy(str(error)) from error
        _set_status('applying', version=latest)
        log = UPDATE_DIR / 'update.log'
        flags = getattr(subprocess, 'CREATE_NO_WINDOW', 0) | getattr(subprocess, 'DETACHED_PROCESS', 0)
        if mode == 'portable':
            helper, runner = _write_portable_helper(package, canonical(root), stage, log)
            command = ['powershell.exe', '-NoProfile', '-NonInteractive', '-ExecutionPolicy', 'Bypass', '-File', str(helper),
                       '-ParentId', str(os.getpid()), '-Python', str(stage / 'python' / 'python.exe'), '-Runner', str(runner),
                       '-Archive', str(package), '-InstallRoot', str(canonical(root)), '-Version', latest,
                       '-StageRoot', str(stage), '-LogPath', str(log)]
        else:
            helper = _write_helper(package, canonical(root), log)
            command = ['powershell.exe', '-NoProfile', '-NonInteractive', '-ExecutionPolicy', 'Bypass', '-File', str(helper),
                       '-ParentId', str(os.getpid()), '-Installer', str(package), '-InstallRoot', str(canonical(root)), '-LogPath', str(log)]
        try:
            subprocess.Popen(command, cwd=str(UPDATE_DIR), creationflags=flags, close_fds=True)
        except OSError as error:
            raise UpdateError('无法启动更新程序，请手动下载安装包。') from error
        handed_off = True

        def finish():
            global _update_active
            try:
                os._exit(0)
            finally:
                if exclusive is not None:
                    exclusive.__exit__(None, None, None)
                with _update_guard:
                    _update_active = False

        threading.Timer(1.2, finish).start()
    except UpdateError as error:
        _set_status('failed', error=str(error))
    except Exception:
        _set_status('failed', error='更新失败，请检查网络、磁盘空间与更新日志后重试。')
    finally:
        if not handed_off:
            if stage is not None:
                shutil.rmtree(stage, ignore_errors=True)
            if exclusive is not None:
                exclusive.__exit__(None, None, None)
            global _update_active
            with _update_guard:
                _update_active = False


def install(current_version: str) -> dict[str, Any]:
    """Start the update worker and return immediately; application mutation stays off the request thread."""
    if version_tuple(current_version) is None:
        raise UpdateError('当前版本号格式无效，无法更新。')
    global _update_active
    with _update_guard:
        if _update_active:
            raise UpdateBusy('更新已在进行中，请等待当前更新完成。')
        _update_active = True
        _update_snapshot.update({'state': 'checking', 'version': None, 'downloaded_bytes': 0,
                                 'total_bytes': None, 'error': None})
    try:
        RESULT_FILE.unlink(missing_ok=True)
    except OSError:
        pass
    thread = threading.Thread(target=_worker, args=(current_version, canonical(ROOT)), name='video-parser-update', daemon=True)
    thread.start()
    return {'started': True, 'version': None, 'installer': None, 'state': 'checking'}
