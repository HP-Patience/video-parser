"""Content-addressed images pasted into personal notes or conversations.

Bytes live beside the project so they travel with the archive; note and message
records only keep a file name, which keeps the 4 MB worker payload and the
personal-learning JSON small.
"""
import base64
import hashlib
import os
import re
from pathlib import Path
from uuid import uuid4

from video_parser.services import library

MAX_BYTES = 4 * 1024 * 1024
MAX_COUNT = 4
ALLOWED = {'image/jpeg': '.jpg', 'image/png': '.png', 'image/webp': '.webp', 'image/gif': '.gif'}
DATA_URL = re.compile(r'data:(image/(?:jpeg|png|webp|gif));base64,([A-Za-z0-9+/=]+)')
NAME = re.compile(r'[a-f0-9]{64}\.(?:jpg|png|webp|gif)')
FOLDER = 'attachments'


def _atomic_bytes(path, data):
    temporary = path.with_name('.image-' + uuid4().hex)
    try:
        with temporary.open('xb') as stream:
            stream.write(data); stream.flush(); os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def save(identifier, data_url):
    """Store one pasted image; identical bytes share one file."""
    project = library.get(identifier)
    match = DATA_URL.fullmatch((data_url or '').strip())
    if not match:
        raise ValueError('只支持 JPEG、PNG、WebP 或 GIF 图片。')
    try:
        raw = base64.b64decode(match[2], validate=True)
    except ValueError:
        raise ValueError('图片数据无效，请重新粘贴。') from None
    if not raw:
        raise ValueError('图片内容为空。')
    if len(raw) > MAX_BYTES:
        raise ValueError('单张图片不能超过 4 MB，请压缩后重试。')
    mime = match[1]
    name = hashlib.sha256(raw).hexdigest() + ALLOWED[mime]
    target = library.contained(project['directory'], f'{FOLDER}/{name}')
    if not target.is_file():
        target.parent.mkdir(exist_ok=True)
        _atomic_bytes(target, raw)
    return {'name': name, 'mime': mime, 'size': len(raw)}


def resolve(identifier, references):
    """Validate stored references so a hand-edited note cannot point at any file."""
    if references is None:
        return []
    if not isinstance(references, list) or len(references) > MAX_COUNT:
        raise ValueError(f'每条内容最多 {MAX_COUNT} 张图片。')
    project = library.get(identifier)
    result = []
    for item in references:
        name = item.get('name') if isinstance(item, dict) else item
        if not isinstance(name, str) or not NAME.fullmatch(name):
            raise ValueError('图片标识无效。')
        target = library.contained(project['directory'], f'{FOLDER}/{name}')
        if not target.is_file() and project['resources'].get(f'{FOLDER}/{name}', {}).get('cloud_missing'):
            from video_parser.services.cloud_sync import ensure_resource
            target = ensure_resource(project, f'{FOLDER}/{name}')
        if not target.is_file():
            raise ValueError('引用的图片已不存在，请重新粘贴。')
        result.append({'name': name, 'mime': next((mime for mime, ext in ALLOWED.items() if name.endswith(ext)), 'image/jpeg'),
                       'size': target.stat().st_size})
    return result


def path(identifier, name):
    project = library.get(identifier)
    if not isinstance(name, str) or not NAME.fullmatch(name):
        raise ValueError('图片标识无效。')
    target = library.contained(project['directory'], f'{FOLDER}/{name}')
    if not target.is_file() and project['resources'].get(f'{FOLDER}/{name}', {}).get('cloud_missing'):
        from video_parser.services.cloud_sync import ensure_resource
        target = ensure_resource(project, f'{FOLDER}/{name}')
    if not target.is_file():
        raise KeyError('图片不存在。')
    return target


def media_type(name):
    return next((mime for mime, ext in ALLOWED.items() if name.endswith(ext)), 'application/octet-stream')
