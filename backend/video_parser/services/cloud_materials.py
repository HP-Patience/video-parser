"""Portable, allowlisted material snapshots and non-destructive library imports."""
import hashlib
import json
import math
from pathlib import Path
import re
import tempfile
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from video_parser.services import library

MAX_TEXT = 4 * 1024 * 1024
MAX_FILE = 32 * 1024 * 1024
TEXT_KINDS = {'subtitle', 'transcription', 'correction', 'translation', 'analysis',
              'analysis-followup', 'conversation', 'conversation-message', 'ai_text', 'ai_result'}
IMAGE_KINDS = {'cover', 'image', 'frame'}
MIMES = {'.srt': 'application/x-subrip', '.vtt': 'text/vtt', '.txt': 'text/plain',
         '.md': 'text/markdown', '.json': 'application/json', '.png': 'image/png',
         '.jpg': 'image/jpeg', '.jpeg': 'image/jpeg', '.webp': 'image/webp'}
SCALARS = {'lang', 'language', 'source_language', 'origin', 'label', 'template_id', 'template_name',
           'model', 'format', 'source_sha256', 'video_part', 'report_type', 'generated_at',
           'created_at', 'record', 'conversation', 'sequence', 'question', 'title', 'source',
           'template', 'context_after', 'origin_record', 'position'}
LINK_FIELDS = {'source_resource', 'transcript_resource'}


def identifier(value):
    if not isinstance(value, str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,128}', value):
        raise ValueError('云端项目或材料标识无效。')
    return value


def sha(data):
    return hashlib.sha256(data).hexdigest()


def clean_scalar(value, limit=4000):
    if isinstance(value, str) and len(value) <= limit and not any(c in value for c in ('\x00', '\\')):
        # Metadata must not transport device filesystem locations or credentials.
        if re.search(r'(?:[A-Za-z]:[/\\]|/(?:home|Users|tmp|var)/|(?:api[_-]?key|access_token|password|cookie)\s*[:=])', value, re.I):
            return None
        return value
    if type(value) in (bool, int):
        return value
    if type(value) is float and math.isfinite(value):
        return value
    return None


def project_metadata(project):
    result = {key: clean_scalar(project[key], 500) for key in
              ('title', 'platform', 'video_id', 'part', 'created_at', 'learned') if project.get(key) is not None}
    result = {k: v for k, v in result.items() if v is not None}
    result['source_key'] = clean_scalar(project.get('key', ''), 300) or ''
    source = urlsplit(project.get('source_url') or '')
    if source.scheme in ('https', 'http') and source.hostname and not source.username and not source.password:
        query = []
        for key, value in parse_qsl(source.query):
            if source.hostname.lower() in {'youtube.com', 'www.youtube.com', 'm.youtube.com'} and key == 'v' and re.fullmatch(r'[A-Za-z0-9_-]{1,128}', value):
                query = [('v', value)]
                break
            if source.hostname.lower() in {'bilibili.com', 'www.bilibili.com', 'm.bilibili.com'} and key == 'p' and value.isascii() and value.isdigit() and 1 <= int(value) <= 100000:
                query = [('p', str(int(value)))]
                break
        result['source_url'] = urlunsplit((source.scheme, source.netloc, source.path, urlencode(query), ''))
    return result


def validate_bytes(data, kind, mime):
    if not 0 < len(data) <= MAX_FILE:
        raise ValueError('云端材料为空或超过 32 MB 限制。')
    if kind in IMAGE_KINDS:
        valid = ((mime == 'image/png' and data.startswith(b'\x89PNG\r\n\x1a\n')) or
                 (mime == 'image/jpeg' and data.startswith(b'\xff\xd8\xff')) or
                 (mime == 'image/webp' and data[:4] == b'RIFF' and data[8:12] == b'WEBP'))
        if not valid:
            raise ValueError('图片格式与内容不符。')
    elif kind in TEXT_KINDS and mime in set(MIMES.values()) - {'image/png', 'image/jpeg', 'image/webp'}:
        if len(data) > MAX_TEXT:
            raise ValueError('文字材料超过云端 4 MB 限制，请拆分后上传。')
        text = data.decode('utf-8')
        if '\x00' in text:
            raise ValueError('文字材料格式无效。')
        if mime == 'application/json':
            json.loads(text)
    else:
        raise ValueError('该资源类型不允许同步。')


def version_id(material_id, blob_sha256, details):
    canonical = json.dumps(details, ensure_ascii=True, sort_keys=True, separators=(',', ':')).encode('ascii')
    return sha((material_id + ':' + blob_sha256 + ':').encode() + canonical)[:40]


def material_id(relative, info):
    return info.get('cloud_material_id') or sha((info['kind'] + ':' + relative).encode())[:32]


def snapshot(project_id):
    """Read verified bytes under the same manifest lock as native writers; never upload paths."""
    project = library.get(project_id)
    with library.file_lock(library.contained(project['directory'], '.manifest.lock')):
        project = library.get(project_id)
        rows, refs, attachment_names = [], {}, set()
        for relative, info in sorted(project['resources'].items()):
            kind = info.get('kind')
            if kind not in TEXT_KINDS | IMAGE_KINDS or info.get('cloud_missing'):
                continue
            mime = MIMES.get(Path(relative).suffix.lower())
            if not mime:
                continue
            path = library.resource_path(project, relative)
            if not path.is_file() or path.stat().st_size > MAX_FILE:
                raise ValueError('已登记材料缺失或过大，请先校验本机项目。')
            data = path.read_bytes()
            if sha(data) != info.get('sha256') or len(data) != info.get('size_bytes'):
                raise ValueError('材料在读取时发生变化，请稍后重试。')
            validate_bytes(data, kind, mime)
            if kind == 'conversation' and mime == 'application/json':
                conversation = json.loads(data)
                if not isinstance(conversation, dict) or not isinstance(conversation.get('messages', []), list):
                    raise ValueError('独立对话格式无效。')
                messages = []
                for message in conversation.get('messages', []):
                    if not isinstance(message, dict):
                        continue
                    safe = {k: message[k] for k in ('id', 'question', 'answer', 'created_at', 'position', 'model')
                            if k in message and type(message[k]) in (str, int, float, bool)}
                    safe['images'] = []
                    for item in message.get('images', []):
                        name = item.get('name') if isinstance(item, dict) else item
                        if isinstance(name, str) and re.fullmatch(r'[a-f0-9]{64}\.(?:png|jpg|webp|gif)', name):
                            safe['images'].append(name)
                            attachment_names.add(name)
                    messages.append(safe)
                sanitized = {'messages': messages}
                if conversation != sanitized:
                    data = json.dumps(sanitized, ensure_ascii=False, separators=(',', ':')).encode('utf-8')
            mid = material_id(relative, info)
            canonical = info.get('cloud_portable')
            vid = canonical['version_id'] if canonical and canonical.get('sha256') == sha(data) else sha((mid + ':' + sha(data)).encode())[:40]
            refs[relative] = {'material_id': mid, 'version_id': vid}
            details = {k: clean_scalar(info[k], 4000 if k in {'question', 'context_after'} else 500) for k in SCALARS if k in info}
            details = {k: v for k, v in details.items() if v is not None}
            if isinstance(info.get('template'), dict):
                details['template'] = {k: clean_scalar(info['template'][k], 300) for k in ('id', 'name') if k in info['template'] and clean_scalar(info['template'][k], 300) is not None}
            details['filename'] = Path(relative).name
            canonical = info.get('cloud_portable')
            if canonical and canonical.get('sha256') == sha(data):
                rows.append({**canonical, '_data': data, '_relative': relative})
            else:
                if canonical:
                    details['filename'] = canonical.get('details', {}).get('filename', details['filename'])
                rows.append({'material_id': mid, 'version_id': vid, 'kind': kind, 'sha256': sha(data),
                             'size': len(data), 'mime': mime, 'details': details, '_data': data, '_relative': relative})
        for relative, info in project['resources'].items():
            if info.get('cloud_missing') and info.get('cloud_material_id'):
                refs[relative] = {'material_id': info['cloud_material_id'], 'version_id': info['cloud_version_id']}
        # Pasted attachments are explicitly referenced, never found by scanning private folders.
        attachment_sources = list(project['resources'].values()) + [{'images': sorted(attachment_names)}]
        for info in attachment_sources:
            for item in info.get('images', []):
                name = item.get('name') if isinstance(item, dict) else item
                if not isinstance(name, str) or not re.fullmatch(r'[a-f0-9]{64}\.(?:png|jpg|webp|gif)', name):
                    raise ValueError('引用的对话图片标识无效。')
                relative = 'attachments/' + name
                if relative in refs:
                    continue
                if name.endswith('.gif'):
                    raise ValueError('云端暂不支持 GIF 对话图片，请先转换为 PNG、JPEG 或 WebP。')
                path = library.contained(project['directory'], relative)
                if not path.is_file() or path.stat().st_size > MAX_FILE:
                    raise ValueError('引用的对话图片缺失或过大。')
                data = path.read_bytes()
                mime = MIMES[Path(name).suffix]
                validate_bytes(data, 'image', mime)
                if sha(data) != Path(name).stem:
                    raise ValueError('引用的对话图片校验失败。')
                mid = sha(('image:' + relative).encode())[:32]
                vid = sha((mid + ':' + sha(data)).encode())[:40]
                refs[relative] = {'material_id': mid, 'version_id': vid}
                rows.append({'material_id': mid, 'version_id': vid, 'kind': 'image', 'sha256': sha(data),
                             'size': len(data), 'mime': mime, 'details': {'filename': name, 'origin': 'attachment'},
                             '_data': data, '_relative': relative})
        for row in rows:
            info = project['resources'].get(row['_relative'], {})
            if info.get('cloud_portable', {}).get('sha256') == row['sha256']:
                continue
            row['details']['images'] = [refs['attachments/' + (item.get('name') if isinstance(item, dict) else item)]
                                       for item in info.get('images', [])
                                       if 'attachments/' + (item.get('name') if isinstance(item, dict) else item) in refs]
            row['details']['links'] = [{'role': field, **refs[info[field]]} for field in LINK_FIELDS
                                       if info.get(field) in refs]
            row['details']['frames'] = [{**refs[frame['path']], 'time': frame.get('time', 0)}
                                        for frame in info.get('frames', []) if isinstance(frame, dict) and frame.get('path') in refs]
        # Resolve referenced versions before hashing their consumers. Existing cloud
        # descriptors remain immutable; only new local versions use this identity.
        indexed = {(row['material_id'], row['version_id']): row for row in rows}
        fixed = {key for key, row in indexed.items()
                 if project['resources'].get(row['_relative'], {}).get('cloud_portable', {}).get('sha256') == row['sha256']}
        resolved, visiting = set(), set()

        def finalize(key):
            row = indexed[key]
            if key in resolved or key in fixed:
                return row['version_id']
            if key in visiting:
                raise ValueError('材料引用存在循环，请先解除循环引用后上传。')
            visiting.add(key)
            for field in ('images', 'links', 'frames'):
                for ref in row['details'].get(field, []):
                    dependency = (ref['material_id'], ref['version_id'])
                    if dependency in indexed:
                        ref['version_id'] = finalize(dependency)
            row['version_id'] = version_id(row['material_id'], row['sha256'], row['details'])
            visiting.remove(key)
            resolved.add(key)
            return row['version_id']

        try:
            for key in indexed:
                finalize(key)
        except RecursionError:
            raise ValueError('材料引用层级过深，请拆分后上传。') from None
        unique = {(row['material_id'], row['version_id']): row for row in rows}
        for row in unique.values():
            details = row.get('details', {})
            if any(len(details.get(field, [])) > 200 for field in ('frames', 'links', 'images')) or len(json.dumps(details, ensure_ascii=True, sort_keys=True, separators=(',', ':')).encode('ascii')) > 65536:
                raise ValueError('材料的图片引用或说明超过云端限制，请拆分后上传。')
        return {'metadata': project_metadata(project), 'versions': list(unique.values())}


def portable_version(row):
    return {key: row[key] for key in ('material_id', 'version_id', 'kind', 'sha256', 'size', 'mime', 'details') if key in row}


def resource_name(material, version):
    kind = material['kind']
    if kind not in TEXT_KINDS | IMAGE_KINDS:
        raise ValueError('云端材料类型不受支持。')
    details = version.get('details') or {}
    filename = details.get('filename', '')
    suffix = next((ext for ext, mime in MIMES.items() if mime == version['mime']), None)
    if not suffix:
        raise ValueError('云端材料格式不受支持。')
    if not isinstance(filename, str) or filename != Path(filename).name or '/' in filename or '\\' in filename or filename.startswith('.'):
        filename = identifier(material['material_id']) + suffix
    if MIMES.get(Path(filename).suffix.lower()) != version['mime']:
        raise ValueError('云端材料扩展名无效。')
    folder = {'frame': 'frames/' + identifier(material['material_id']), 'conversation': 'conversations',
              'conversation-message': 'conversation-message'}.get(kind, kind)
    if kind == 'image' and details.get('origin') == 'attachment':
        if not re.fullmatch(r'[a-f0-9]{64}\.(?:png|jpg|webp)', filename):
            raise ValueError('云端附件图片名称无效。')
        folder = 'attachments'
    # Versions live beside, never over, existing material files.
    return folder + '/' + filename


def import_version(local_id, material, version, data, scope, names=None, target=None):
    validate_bytes(data, material['kind'], version['mime'])
    if sha(data) != version['sha256'] or len(data) != version['size']:
        raise ValueError('下载材料校验失败，未写入本机。')
    details = {k: clean_scalar(v) for k, v in (version.get('details') or {}).items() if k in SCALARS}
    details = {k: v for k, v in details.items() if v is not None}
    native_template = (version.get('details') or {}).get('template')
    if isinstance(native_template, dict):
        details['template'] = clean_scalar(native_template.get('name') or native_template.get('id'), 500) or ''
    for link in (version.get('details') or {}).get('links', []):
        key = (link.get('material_id'), link.get('version_id'))
        if link.get('role') in LINK_FIELDS and names and key in names:
            details[link['role']] = names[key]
    details['frames'] = [{'path': names[(f['material_id'], f['version_id'])], 'time': f.get('time', 0)}
                         for f in (version.get('details') or {}).get('frames', [])
                         if names and (f.get('material_id'), f.get('version_id')) in names]
    details['images'] = [Path(names[(f['material_id'], f['version_id'])]).name
                         for f in (version.get('details') or {}).get('images', [])
                         if names and (f.get('material_id'), f.get('version_id')) in names]
    details.update(cloud_scope=scope, cloud_material_id=material['material_id'],
                   cloud_portable={'material_id': material['material_id'], 'kind': material['kind'], **version},
                   cloud_version_id=version['version_id'], cloud_missing=False)
    relative = target or resource_name(material, version)
    old = library.get(local_id)['resources'].get(relative, {})
    if old.get('sha256') == version['sha256']:
        details = {**old, **details}
    with tempfile.TemporaryDirectory(prefix='cloud-import-') as temporary:
        source = Path(temporary) / 'material'
        source.write_bytes(data)
        return library.import_file(local_id, source, relative, material['kind'], details)
