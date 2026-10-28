"""Single-frame thumbnails for the player's scrub bar.

One ffmpeg call per requested second of footage, then served from disk. The cache folder is keyed by
the archived file's content hash, so re-downloading a video starts from a clean folder instead of
showing stale frames from the previous copy.
"""
from pathlib import Path
import subprocess

from video_parser.core import paths
from video_parser.services import library

CACHE = paths.CACHE_DIR / 'scrub'
SCALE = 'scale=320:-2'
TIMEOUT = 30


def _folder(project, resource):
    info = project['resources'].get(resource)
    if not isinstance(info, dict) or info.get('kind') != 'video':
        raise ValueError('只能为当前项目内已归档的视频截取预览。')
    digest = str(info.get('sha256') or '')
    return CACHE / (digest[:16] or 'unknown')


def frame(identifier, resource, seconds):
    """Absolute path to a cached JPEG for that second, generating it on first request."""
    project = library.get(identifier)
    video = library.resource_path(project, resource)
    if not video.is_file():
        raise ValueError('归档视频缺失或已变化，请重新校验。')
    second = max(0, int(seconds or 0))
    folder = _folder(project, resource)
    target = folder / f'{second:06d}.jpg'
    if target.is_file() and target.stat().st_size:
        return target
    folder.mkdir(parents=True, exist_ok=True)
    try:
        subprocess.run(['ffmpeg', '-hide_banner', '-loglevel', 'error', '-nostdin', '-y',
                        '-ss', str(second), '-i', str(video), '-frames:v', '1', '-vf', SCALE, '-q:v', '5', str(target)],
                       capture_output=True, timeout=TIMEOUT)
    except (OSError, subprocess.SubprocessError) as error:
        raise ValueError('没能截取预览画面，请检查 ffmpeg。') from error
    if not target.is_file() or not target.stat().st_size:
        raise ValueError('没能截取预览画面，请检查 ffmpeg。')
    return target
