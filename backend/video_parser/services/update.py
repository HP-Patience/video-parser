"""Check and apply Windows releases without exposing a general-purpose downloader."""
from __future__ import annotations

import hashlib
import hmac
import json
import os
from pathlib import Path
import re
import subprocess
import threading
from typing import Any
from uuid import uuid4

import requests

from video_parser.core.paths import ROOT, canonical

REPOSITORY = 'HP-Patience/video-parser'
LATEST_RELEASE_URL = f'https://api.github.com/repos/{REPOSITORY}/releases/latest'
RELEASES_URL = f'https://github.com/{REPOSITORY}/releases'
INSTALLER_PREFIX = 'VideoParser-'
INSTALLER_SUFFIX = '-win64-setup.exe'
CHECKSUM_ASSET = 'SHA256SUMS.txt'
UPDATE_DIR = ROOT / '.local' / 'work' / 'updates'
MAX_JSON_BYTES = 1024 * 1024
MAX_DOWNLOAD_BYTES = 2 * 1024 * 1024 * 1024
INSTALLER_KEY = r'Software\Microsoft\Windows\CurrentVersion\Uninstall\{A156B253-F151-4B69-BABA-58D608B78569}_is1'
_VERSION = re.compile(r'^v?(\d+)\.(\d+)\.(\d+)$')


class UpdateError(RuntimeError):
    """A user-facing update failure."""


class UpdateUnavailable(UpdateError):
    pass


class UpdateNotSupported(UpdateError):
    pass


class UpdateBusy(UpdateError):
    pass


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


def install_supported(root: Path = ROOT) -> bool:
    installed = _installed_root()
    if installed is None:
        return False
    current = canonical(root)
    try:
        return installed.samefile(current)
    except OSError:
        return os.path.normcase(str(installed)) == os.path.normcase(str(current))


def _assets(release: dict[str, Any]) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
    rows = release.get('assets')
    if not isinstance(rows, list):
        return None, None
    assets = {row.get('name'): row for row in rows if isinstance(row, dict) and isinstance(row.get('name'), str)}
    version = _release_version(release.get('tag_name'))
    return assets.get(f'{INSTALLER_PREFIX}{version}{INSTALLER_SUFFIX}'), assets.get(CHECKSUM_ASSET)


def _release_result(current_version: str, release: dict[str, Any] | None) -> dict[str, Any]:
    current = version_tuple(current_version)
    if release is None:
        return {'current_version': current_version, 'latest_version': None, 'release_found': False,
                'available': False, 'can_install': False, 'install_reason': 'no_release', 'release_url': RELEASES_URL}
    latest = _release_version(release.get('tag_name'))
    latest_key = version_tuple(latest)
    available = bool(current and latest_key and latest_key > current)
    installer, checksums = _assets(release)
    supported = install_supported()
    can_install = bool(available and supported and installer and checksums)
    reason = ('installed' if can_install else 'not_installed' if not supported else
              'assets_missing' if not (installer and checksums) else 'up_to_date')
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
    if not url.startswith(f'https://github.com/{REPOSITORY}/releases/download/'):
        raise UpdateError('更新包地址异常，已停止处理。')
    return url


def _download(url: str, destination: Path, expected_size: int | None = None) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_name('.' + uuid4().hex + '.tmp')
    total = 0
    try:
        with requests.get(url, stream=True, timeout=(10, 60), headers={'User-Agent': 'video-parser-updater'}) as response:
            response.raise_for_status()
            length = response.headers.get('Content-Length')
            if length and length.isdigit() and int(length) > MAX_DOWNLOAD_BYTES:
                raise UpdateError('更新包超过允许大小，已停止下载。')
            with temporary.open('wb') as stream:
                for chunk in response.iter_content(chunk_size=1024 * 1024):
                    if not chunk:
                        continue
                    total += len(chunk)
                    if total > MAX_DOWNLOAD_BYTES:
                        raise UpdateError('更新包超过允许大小，已停止下载。')
                    stream.write(chunk)
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
  $deadline = (Get-Date).AddMinutes(2)
  while ((Get-Process -Id $ParentId -ErrorAction SilentlyContinue) -and ((Get-Date) -lt $deadline)) {
    Start-Sleep -Milliseconds 250
  }
  $arguments = @('/VERYSILENT', '/SUPPRESSMSGBOXES', '/NORESTART', '/CLOSEAPPLICATIONS', '/FORCECLOSEAPPLICATIONS')
  $result = Start-Process -FilePath $Installer -ArgumentList $arguments -PassThru -Wait -WindowStyle Hidden
  if ($result.ExitCode -ne 0) { throw "Installer exited with code $($result.ExitCode)" }
  Start-Sleep -Milliseconds 500
  $executable = Join-Path $InstallRoot 'python\pythonw.exe'
  if (!(Test-Path -LiteralPath $executable)) { throw "Application executable is missing: $executable" }
  Start-Process -FilePath $executable -ArgumentList '-m', 'video_parser.desktop' -WorkingDirectory $InstallRoot -WindowStyle Hidden
} catch {
  Add-Content -LiteralPath $LogPath -Value ((Get-Date).ToString('s') + ' ' + $_.Exception.Message) -Encoding UTF8
  Start-Process -FilePath $Installer -ArgumentList '/CLOSEAPPLICATIONS'
}
''', encoding='utf-8')
    return helper


def _verified_installer(release: dict[str, Any]) -> Path:
    version = _release_version(release.get('tag_name'))
    installer, checksums = _assets(release)
    if not installer or not checksums:
        raise UpdateNotSupported('该版本没有完整的 Windows 安装包。')
    installer_url = _valid_asset_url(installer.get('browser_download_url'))
    checksums_url = _valid_asset_url(checksums.get('browser_download_url'))
    UPDATE_DIR.mkdir(parents=True, exist_ok=True)
    checksums_path = UPDATE_DIR / CHECKSUM_ASSET
    _download(checksums_url, checksums_path)
    expected = _expected_checksum(checksums_path, installer.get('name'))
    installer_path = UPDATE_DIR / f'{INSTALLER_PREFIX}{version}{INSTALLER_SUFFIX}'
    size = installer.get('size')
    _download(installer_url, installer_path, int(size) if isinstance(size, int) and size > 0 else None)
    if not hmac.compare_digest(_sha256(installer_path), expected):
        installer_path.unlink(missing_ok=True)
        raise UpdateError('安装包校验失败，已删除下载文件。')
    return installer_path


def install(current_version: str) -> dict[str, Any]:
    if version_tuple(current_version) is None:
        raise UpdateError('当前版本号格式无效，无法更新。')
    release = _get_json(LATEST_RELEASE_URL)
    status = _release_result(current_version, release)
    if not status['available']:
        raise UpdateUnavailable('当前已是最新版本。')
    if not status['can_install']:
        raise UpdateNotSupported('当前不是可自动更新的安装版，请前往发布页下载。')
    from video_parser.services import task_runtime
    if task_runtime.any_active():
        raise UpdateBusy('仍有任务进行中，请全部完成后再更新。')
    installer = _verified_installer(release)
    root = canonical(ROOT)
    log = UPDATE_DIR / 'update.log'
    helper = _write_helper(installer, root, log)
    flags = getattr(subprocess, 'CREATE_NO_WINDOW', 0) | getattr(subprocess, 'DETACHED_PROCESS', 0)
    try:
        subprocess.Popen(['powershell.exe', '-NoProfile', '-NonInteractive', '-ExecutionPolicy', 'Bypass',
                          '-File', str(helper), '-ParentId', str(os.getpid()), '-Installer', str(installer),
                          '-InstallRoot', str(root), '-LogPath', str(log)],
                         cwd=str(UPDATE_DIR), creationflags=flags, close_fds=True)
    except OSError as error:
        raise UpdateError('无法启动更新程序，请手动下载安装包。') from error
    threading.Timer(1.2, lambda: os._exit(0)).start()
    return {'started': True, 'version': status['latest_version'], 'installer': installer.name}
