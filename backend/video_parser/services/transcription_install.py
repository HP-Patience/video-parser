"""Opt-in, Windows-only installation of the bundled transcription integration."""
import hashlib
import json
import logging
import os
from pathlib import Path
import platform
import shutil
import stat
import subprocess
import tempfile
from threading import Lock, Thread
import zipfile

import requests

from video_parser.core.paths import ROOT
from video_parser.services import library, task_runtime, transcription, whisper_pool

logger = logging.getLogger(__name__)
UV_URL = 'https://github.com/astral-sh/uv/releases/download/0.11.21/uv-x86_64-pc-windows-msvc.zip'
UV_SIZE = 23_452_481
UV_SHA = 'ace861f360c6de2babedc1607d0f454b6b09a820dbc8182dc15af927e4df9589'
PYTHON_VERSION = '3.12.13'
MODEL_FILES = {'config.json', 'model.bin', 'preprocessor_config.json', 'tokenizer.json', 'vocabulary.json'}
ACTIVE = {'preparing', 'installing', 'downloading', 'verifying', 'checking_cuda'}
_guard = Lock()
_progress = {'state': 'idle', 'downloaded_bytes': 0, 'total_bytes': None, 'error': None}


class InstallError(RuntimeError):
    pass


def supported():
    return (os.name == 'nt' and platform.machine().lower() in {'amd64', 'x86_64'}
            and transcription.TOOL_ROOT == transcription.BUNDLED_ROOT)


def status():
    with _guard:
        return _snapshot()


def _snapshot():
    return {**_progress, 'supported': supported(), 'ready': transcription.available()}


def _set(state, **fields):
    with _guard:
        _progress.update(state=state, **fields)


def start():
    if not supported():
        raise ValueError('当前环境不支持安装转录组件。')
    with _guard:
        if _progress['state'] not in ACTIVE and not transcription.available():
            _progress.update(state='preparing', downloaded_bytes=0, total_bytes=None, error=None)
            try:
                Thread(target=_worker, name='whisper-install', daemon=True).start()
            except (RuntimeError, OSError):
                _progress.update(state='failed', error='无法启动转录安装。')
        return _snapshot()


def _safe(path):
    for part in (path, *path.parents):
        if part == ROOT:
            break
        try:
            info = part.lstat()
        except FileNotFoundError:
            continue
        if stat.S_ISLNK(info.st_mode) or getattr(info, 'st_file_attributes', 0) & 0x400:
            raise InstallError('转录安装目录或文件包含链接，已停止安装。')
    else:
        raise InstallError('转录安装目录无效。')
    return path


def _valid(path, size, sha):
    if not path.is_file() or path.stat().st_size != size:
        return False
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest() == sha


def _download(url, destination, size, sha, progress=lambda count: None):
    count = 0
    with requests.get(url, stream=True, timeout=(10, 60), headers={'Accept-Encoding': 'identity'}) as response:
        response.raise_for_status()
        with destination.open('xb') as stream:
            for chunk in response.iter_content(chunk_size=1024 * 1024):
                count += len(chunk)
                if count > size:
                    raise InstallError('下载文件大于预期，已停止安装。')
                stream.write(chunk)
                progress(count)
    if not _valid(destination, size, sha):
        raise InstallError('下载文件校验失败，请重试。')


def _models(directory, stage):
    _set('verifying')
    manifest = json.loads((transcription.BUNDLED_ROOT / 'model-manifest.json').read_text(encoding='utf-8'))
    entries = manifest['files']
    if len(entries) != len(MODEL_FILES) or {entry['path'] for entry in entries} != MODEL_FILES:
        raise InstallError('转录模型清单无效。')
    directory.mkdir(parents=True, exist_ok=True)
    missing = []
    for entry in entries:
        path = _safe(directory / entry['path'])
        if not path.exists():
            missing.append(entry)
        elif not _valid(path, entry['size'], entry['sha256']):
            raise InstallError(f"已保留校验失败的模型文件（{entry['path']}）。请移出该文件后重试。")
    if not missing:
        return
    total = sum(entry['size'] for entry in missing)
    if shutil.disk_usage(directory).free < total + 512 * 1024 * 1024:
        raise InstallError('可用磁盘空间不足，无法下载转录模型。')
    completed = 0
    _set('downloading', downloaded_bytes=0, total_bytes=total)
    base = f"https://huggingface.co/{manifest['repo_id']}/resolve/{manifest['revision']}/"
    for entry in missing:
        filename = entry['path']
        part = stage / filename
        _download(base + filename, part, entry['size'], entry['sha256'],
                  lambda count: _set('downloading', downloaded_bytes=completed + count))
        # Hard link publishes a verified file atomically without overwriting an existing model.
        os.link(part, _safe(directory / filename))
        part.unlink()
        completed += entry['size']
    _set('verifying')


def _run(command, env, timeout, log):
    with log.open('ab') as output:
        result = subprocess.run([str(arg) for arg in command], cwd=ROOT, env=env,
                                stdout=output, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL,
                                creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0), timeout=timeout)
    if result.returncode:
        raise InstallError('安装命令未完成，请查看转录安装日志后重试。')


def _runtime(work, stage, log):
    env = {key: value for key, value in os.environ.items()
           if not key.upper().startswith(('UV_', 'PYTHON', 'PIP_'))}
    env.update(UV_CACHE_DIR=str(_safe(work / 'uv-cache')),
               UV_PYTHON_INSTALL_DIR=str(_safe(transcription.MANAGED_WHISPER / 'python')))
    _safe(transcription.MANAGED_WHISPER).mkdir(parents=True, exist_ok=True)
    executable = _safe(transcription.MANAGED_PYTHON)
    lock = ROOT / 'configs/requirements/windows-whisper.lock.txt'
    uv = stage / 'uv.exe'
    archive = _safe(work / 'uv-0.11.21.zip')
    if not _valid(archive, UV_SIZE, UV_SHA):
        candidate = stage / 'uv.zip'
        _download(UV_URL, candidate, UV_SIZE, UV_SHA)
        candidate.replace(archive)
    with zipfile.ZipFile(archive) as package, package.open('uv.exe') as source, uv.open('xb') as target:
        shutil.copyfileobj(source, target)
    common = [uv, '--no-config', '--no-progress']
    if not executable.is_file():
        _run([*common, 'python', 'install', PYTHON_VERSION, '--no-bin', '--no-registry'], env, 600, log)
        _run([*common, 'venv', '--managed-python', '--python', PYTHON_VERSION, '--no-python-downloads',
              transcription.MANAGED_WHISPER / 'venv'], env, 60, log)
    _run([executable, '-I', '-c', 'import sys; assert sys.version_info[:3] == (3,12,13)'], env, 30, log)
    _run([*common, 'pip', 'install', '--python', executable, '--only-binary', ':all:', '--no-deps',
          '--index-url', 'https://pypi.org/simple', '-r', lock], env, 1200, log)
    _run([*common, 'pip', 'check', '--python', executable], env, 30, log)
    return executable, env


def _probe(executable, env, log):
    # Iterating the generator forces real model inference on a synthetic sample.
    code = ('import sys; sys.path.insert(0, sys.argv[1]); from runtime import load_model; '
            'import numpy as np; model = load_model(); '
            'audio = (np.sin(np.arange(32000) * 0.17) * 0.08).astype(np.float32); '
            'segments, _ = model.transcribe(audio, language="en", beam_size=1, vad_filter=False); list(segments)')
    try:
        _run([executable, '-I', '-c', code, transcription.BUNDLED_ROOT], env, 180, log)
    except (InstallError, subprocess.TimeoutExpired):
        raise InstallError('CUDA 转录检查失败。请确认 NVIDIA 驱动可用、显存充足，并查看转录安装日志后重试。') from None


def _worker():
    try:
        try:
            with task_runtime.exclusive_operation():
                work = _safe(ROOT / '.local/work/whisper-install')
                work.mkdir(parents=True, exist_ok=True)
                with library.file_lock(_safe(transcription.TRANSCRIPTION_LOCK), timeout=0):
                    log = _safe(work / 'install.log')
                    with log.open('wb'):
                        pass
                    marker = _safe(transcription.WHISPER_READY)
                    marker.unlink(missing_ok=True)
                    whisper_pool.close()
                    # Staging shares the install volume, but is never considered a model or runtime.
                    with tempfile.TemporaryDirectory(prefix='stage-', dir=work) as name:
                        stage = Path(name)
                        _set('installing')
                        executable, env = _runtime(work, stage, log)
                        _models(_safe(transcription.model_directory()), stage)
                        _set('checking_cuda')
                        _probe(executable, env, log)
                    marker.touch(exist_ok=False)
        except (BlockingIOError, TimeoutError):
            raise InstallError('有其他任务正在运行，请等任务完成后重试安装。') from None
        _set('ready', error=None)
    except (InstallError, requests.RequestException, OSError, subprocess.TimeoutExpired, ValueError, KeyError, zipfile.BadZipFile) as exc:
        logger.warning('Whisper installation stopped: %s', type(exc).__name__)
        _set('failed', error=str(exc) if isinstance(exc, InstallError) else '无法完成转录安装，请检查网络、磁盘空间和目录权限后重试。')
    finally:
        with _guard:
            if _progress['state'] in ACTIVE:
                _progress.update(state='failed', error='安装转录组件失败。')
