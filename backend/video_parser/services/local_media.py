"""Local video association: verified, cancellable import into a local project."""
from __future__ import annotations

from dataclasses import dataclass, field
from hashlib import sha256
import os
from pathlib import Path
import re
import tempfile
import time
from threading import RLock
from uuid import uuid4

from video_parser.services import library, task_runtime, videos

VIDEO_SUFFIXES = {'.mp4', '.mkv', '.webm', '.mov', '.avi', '.m4v', '.flv', '.ts', '.mts', '.m2ts'}
CHUNK_SIZE = 1024 * 1024
JOURNAL_ROOT = Path(library.LIBRARY_ROOT) / '.local' / 'tasks' / 'local-media'
_INITIAL_JOURNAL_ROOT = JOURNAL_ROOT


class LocalMediaError(ValueError):
    """An expected local association validation failure."""


@dataclass
class LocalMediaJob:
    id: str = field(default_factory=lambda: uuid4().hex)
    project_id: str = ''
    root: str = ''
    account_scope: str = ''
    source: str = ''
    state: str = 'queued'
    phase: str = 'queued'
    progress: dict = field(default_factory=dict)
    message: str = ''
    error_code: str = ''
    result: dict = field(default_factory=dict)
    kind: str = 'local-media'
    created: float = field(default_factory=time.time)
    elapsed_seconds: float = 0
    stage_timings: dict = field(default_factory=dict)
    payload: dict = field(default_factory=dict)


_jobs = {}
_jobs_lock = RLock()


def _root():
    return Path(library.LIBRARY_ROOT).resolve()


def _account_scope():
    from video_parser.services import cloud_sync
    client = getattr(cloud_sync, '_client', None)
    if client is None:
        return ''
    with client.lock:
        return (client.auth or {}).get('scope', '')


def _checkpoint(job):
    task_runtime.checkpoint()
    if str(_root()) != job.root or _account_scope() != job.account_scope:
        raise LocalMediaError('视频库或账号已切换，请重新提交任务。')


def _project(project_id):
    if not isinstance(project_id, str) or not project_id:
        raise LocalMediaError('项目标识无效。')
    project = library.get(project_id)
    if project.get('platform') != 'local' or project.get('source_url'):
        raise LocalMediaError('只允许为没有来源链接的本地项目关联视频。')
    if not re.fullmatch(r'[a-f0-9]{64}', str(project.get('video_id', ''))):
        raise LocalMediaError('本地项目缺少有效的视频 SHA256 标识。')
    scope = _account_scope()
    if project.get('cloud_scope') not in (None, scope) or any(
            item.get('cloud_scope') not in (None, scope) for item in project.get('resources', {}).values()):
        raise LocalMediaError('项目属于另一账号，请先切换账号。')
    return project


def _source(path):
    if not isinstance(path, str) or not path or '\x00' in path or path.startswith(('\\\\', '//')):
        raise LocalMediaError('请选择本机普通视频文件，不能使用网络或设备路径。')
    if os.name == 'nt' and ':' in path[2:]:
        raise LocalMediaError('视频路径不能包含备用数据流。')
    candidate = Path(path)
    if not candidate.is_absolute():
        raise LocalMediaError('视频路径必须是本机绝对路径。')
    if candidate.is_symlink():
        raise LocalMediaError('不允许选择符号链接视频。')
    source = candidate.resolve(strict=True)
    if not source.is_file():
        raise LocalMediaError('所选路径不是普通文件。')
    if source.suffix.lower() not in VIDEO_SUFFIXES:
        raise LocalMediaError('所选文件不是支持的视频格式。')
    if source.stat().st_size <= 0:
        raise LocalMediaError('视频文件为空。')
    return source


def _progress(phase, message, done, total):
    task_runtime.progress(phase, message, bytes=done, total_bytes=total, force=done == total)


def _copy_and_hash(source, staged, job):
    total = source.stat().st_size
    digest = sha256()
    done = 0
    with source.open('rb') as reader, staged.open('xb') as writer:
        while True:
            _checkpoint(job)
            chunk = reader.read(CHUNK_SIZE)
            if not chunk:
                break
            writer.write(chunk)
            digest.update(chunk)
            done += len(chunk)
            _progress('hashing', '正在校验本机视频…', done, total)
        writer.flush()
        os.fsync(writer.fileno())
    return digest.hexdigest(), done


def _worker(job):
    _checkpoint(job)
    root = _root()
    project = _project(job.project_id)
    source = _source(job.source)
    relative = 'videos/' + source.name
    if relative in project.get('resources', {}):
        raise LocalMediaError('项目中已有同名视频资源，拒绝覆盖。')
    target = library.contained(project['directory'], relative)
    if target.exists():
        raise LocalMediaError('项目目标视频已存在，拒绝覆盖。')
    with tempfile.TemporaryDirectory(prefix='.local-video-', dir=project['directory']) as directory:
        staged = Path(directory) / source.name
        digest, _ = _copy_and_hash(source, staged, job)
        task_runtime.checkpoint()
        if digest != str(project['video_id']):
            raise LocalMediaError('视频校验失败：文件 SHA256 与项目 video_id 不一致。')
        _progress('validating', '正在验证视频轨道…', 1, 1)
        media = videos.probe(staged)
        videos.check_video(media)
        _checkpoint(job)
        # import_file publishes from the verified immutable staging copy; no source path is reused.
        publishing = 0
        total = staged.stat().st_size

        def copied(count):
            nonlocal publishing
            publishing += count
            _checkpoint(job)
            _progress('publishing', '正在登记已校验的视频…', publishing, total)

        imported = library.import_file(job.project_id, staged, relative, 'video', {'media': media},
                                       copy_callback=copied, expected_sha256=digest, no_replace=True,
                                       checkpoint=lambda: _checkpoint(job))
        if _root() != root:
            raise LocalMediaError('视频库已切换，任务未登记。')
        saved = library.get(job.project_id)
        item = saved['resources'].get(imported)
        if not item or item.get('sha256') != digest:
            raise LocalMediaError('登记后校验失败，未接受视频。')
        job.result = {'project_id': job.project_id, 'path': imported, 'sha256': digest,
                      'size_bytes': item.get('size_bytes'), 'media': media}
        # Publication has committed: a late cancellation must not report an unregistered video.
        job.phase = 'completed'
        job.progress = {'bytes': 1, 'total_bytes': 1}
        job.state = 'completed'
        job.message = '视频已登记。'


def start(project_id, path):
    root = _root()
    _project(project_id)
    source = _source(path)
    if _root() != root:
        raise LocalMediaError('视频库已切换，请重新选择项目。')
    job = LocalMediaJob(project_id=project_id, root=str(root), account_scope=_account_scope(), source=str(source))
    with _jobs_lock:
        _jobs[job.id] = job
    try:
        journal_root = JOURNAL_ROOT if JOURNAL_ROOT != _INITIAL_JOURNAL_ROOT else root / '.local' / 'tasks' / 'local-media'
        task_runtime.submit('local-media', job, _worker,
                            lambda: task_runtime.save(journal_root, job), capacity=8)
    except Exception:
        with _jobs_lock:
            _jobs.pop(job.id, None)
        raise
    return view(job.id)


def view(identifier):
    if not isinstance(identifier, str) or identifier not in _jobs:
        raise KeyError(identifier)
    with _jobs_lock:
        job = _jobs[identifier]
        if job.root != str(_root()) or job.account_scope != _account_scope():
            raise KeyError(identifier)
        return {'task_id': job.id, 'project_id': job.project_id, 'state': task_runtime.public_state(job),
                'phase': job.phase, 'progress': dict(job.progress), 'message': job.message,
                'error_code': job.error_code, 'result': dict(job.result)}


def cancel(identifier):
    view(identifier)
    task_runtime.cancel(identifier)
    return view(identifier)


def _pick_native():
    import base64
    import json
    import subprocess
    import sys
    from video_parser.services import ai_files, folders
    if sys.platform != 'win32':
        raise LocalMediaError('本机文件选择窗口目前支持 Windows；请直接输入视频绝对路径。')
    if not folders.dialog_lock.acquire(blocking=False):
        raise BlockingIOError('已经打开了文件选择窗口，请先完成选择。')
    try:
        options = {'title': '选择项目对应的本机视频',
                   'filter': '视频文件|' + ';'.join('*' + ext for ext in sorted(VIDEO_SUFFIXES)),
                   'directory': str(_root())}
        encoded = base64.b64encode(json.dumps(options, ensure_ascii=False).encode()).decode('ascii')
        script = base64.b64encode(ai_files.DIALOG.replace('__OPTIONS__', encoded).encode('utf-16-le')).decode('ascii')
        result = subprocess.run(['powershell.exe', '-NoProfile', '-STA', '-EncodedCommand', script],
                                capture_output=True, text=True, encoding='utf-8', timeout=900,
                                creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
        if result.returncode:
            raise LocalMediaError('文件选择窗口失败，请直接输入视频绝对路径。')
        output = result.stdout.strip()
        return base64.b64decode(output, validate=True).decode('utf-8') if output else None
    finally:
        folders.dialog_lock.release()


def picker(project_id, path=None):
    _project(project_id)
    if path is None:
        path = _pick_native()
    if path is None:
        return {'project_id': project_id, 'path': None, 'cancelled': True,
                'allowed_extensions': sorted(VIDEO_SUFFIXES)}
    source = _source(path)
    return {'project_id': project_id, 'path': str(source), 'cancelled': False,
            'allowed_extensions': sorted(VIDEO_SUFFIXES)}


def status(identifier):
    return view(identifier)
