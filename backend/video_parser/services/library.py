"""Persistent projects: one course directory with stable per-episode subdirectories."""
from contextlib import contextmanager
from datetime import datetime, timezone
import hashlib
import json
import math
import os
from pathlib import Path
import re
import shutil
import tempfile
import time
from urllib.parse import parse_qs, urlsplit, urlunsplit
from uuid import uuid4
from zipfile import ZIP_DEFLATED, ZipFile

from video_parser.core.paths import ROOT, canonical

LIBRARY_ROOT = Path(os.getenv('VIDEO_LIBRARY_ROOT', ROOT / 'outputs'))
PLATFORMS = {'哔哩哔哩': 'bilibili', '抖音': 'douyin', '小红书': 'xiaohongshu', 'YouTube': 'youtube'}
# Kinds that carry readable subtitle text: originals, local transcripts and proofread copies.
TRANSCRIPT_KINDS = ('subtitle', 'transcription', 'correction')
MAX_EPISODES = 500
MAX_PART = 100000

def _positive_duration(value):
    if type(value) not in (int, float): return None
    try:
        return value if math.isfinite(value) and value > 0 else None
    except (OverflowError, TypeError, ValueError):
        return None


def normalize_episodes(value):
    if not isinstance(value, list): return []
    episodes = {}
    for item in value[:MAX_EPISODES]:
        if not isinstance(item, dict): continue
        part = item.get('part')
        if type(part) is not int or not 1 <= part <= MAX_PART: continue
        title = item.get('title')
        title = ' '.join(title.split())[:200] if isinstance(title, str) else ''
        duration = _positive_duration(item.get('duration'))
        episodes[part] = {'part': part, 'title': title or f'P{part}', 'duration': duration}
    return [episodes[part] for part in sorted(episodes)]


def digest(path):
    with Path(path).open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def safe_name(text):
    name = re.sub(r'[<>:"/\\|?*\x00-\x1f]', '_', str(text)).strip(' .')[:65].rstrip(' .') or 'video'
    if re.match(r'^(CON|PRN|AUX|NUL|COM[1-9]|LPT[1-9])(?:\.|$)', name, re.I): name = '_' + name
    return name


def _project_lock_path(path):
    """Project locks must not be open children of a directory being renamed on Windows.

    All existing writers share an identity-keyed lock outside movable project trees.
    Callers still reload the project after acquiring their usual manifest/study lock.
    """
    if not path.name.endswith('.lock') or not path.is_relative_to(LIBRARY_ROOT):
        return path
    parent = path.parent
    if not (parent / 'project.json').is_file() and parent != LIBRARY_ROOT:
        from video_parser.services.library_layout import restore_task_paths
        parent = Path(restore_task_paths({'path': str(parent)})['path'])
    if (parent / 'project.json').is_file():
        project = load(contained(parent, 'project.json'))
        if not re.fullmatch('[a-f0-9]{32}', project.get('id', '')):
            raise ValueError('Invalid project lock identity')
        return contained(LIBRARY_ROOT, '.project-locks/' + project['id'] + '/' + path.name)
    return path


@contextmanager
def file_lock(path, timeout=60):
    """OS releases this lock on process exit, including interrupted CLI runs."""
    path = Path(path)
    if path.is_symlink(): raise ValueError('Unsafe lock path')
    path = _project_lock_path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.is_symlink(): raise ValueError('Unsafe lock path')
    deadline = time.monotonic() + timeout
    while True:
        from video_parser.services import task_runtime
        task_runtime.checkpoint()
        # Creating the lock file races between threads: on Windows the losing create
        # reports PermissionError before any byte lock exists, so retry the open too.
        try:
            stream = path.open('a+b')
        except (FileNotFoundError, PermissionError):
            if time.monotonic() >= deadline: raise TimeoutError('视频项目正在处理中，请稍后重试。')
            time.sleep(.03)
            continue
        try:
            stream.seek(0, 2)
            if not stream.tell(): stream.write(b'0'); stream.flush()
            stream.seek(0)
            if os.name == 'nt':
                import msvcrt
                msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
            break
        except OSError:
            stream.close()
            if time.monotonic() >= deadline: raise TimeoutError('视频项目正在处理中，请稍后重试。')
            time.sleep(.03)
    try: yield
    finally:
        stream.seek(0)
        if os.name == 'nt':
            import msvcrt
            msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
        else:
            import fcntl
            fcntl.flock(stream, fcntl.LOCK_UN)
        stream.close()


def contained(directory, relative):
    relative = Path(relative)
    if relative.is_absolute() or '..' in relative.parts or not relative.parts:
        raise ValueError('Unsafe relative path')
    target = directory / relative
    resolved = canonical(target)
    if not resolved.is_relative_to(canonical(directory)) or not resolved.is_relative_to(canonical(LIBRARY_ROOT)):
        raise ValueError('File outside project')
    # Junctions and symlinks must never redirect a resource within or outside the project.
    for parent in [target, *target.parents]:
        if parent == LIBRARY_ROOT.parent: break
        if parent.is_symlink() or (hasattr(parent, 'is_junction') and parent.is_junction()):
            raise ValueError('Linked project paths are not allowed')
    return target


def atomic_json(path, data):
    temporary = path.with_name('.manifest-' + uuid4().hex)
    try:
        with temporary.open('x', encoding='utf-8') as stream:
            json.dump(data, stream, ensure_ascii=False, indent=2)
            stream.write('\n'); stream.flush(); os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally: temporary.unlink(missing_ok=True)


def identity(metadata):
    platform = PLATFORMS.get(metadata.get('platform'), metadata.get('platform', 'unknown'))
    if not re.fullmatch(r'[a-z][a-z0-9_-]{0,25}', platform): raise ValueError('Unknown platform identity')
    source = urlsplit(metadata.get('source_url', ''))
    identifier = str(metadata.get('video_id') or '')
    if platform == 'bilibili':
        match = re.search(r'/video/(BV[A-Za-z0-9]{10})(?:/|$)', source.path)
        identifier = match[1] if match else identifier
        if not re.fullmatch(r'BV[A-Za-z0-9]{10}', identifier): raise ValueError('Missing Bilibili BV identity')
    if not re.fullmatch(r'[A-Za-z0-9_-]{1,100}', identifier): raise ValueError('Missing stable video ID')
    part = int(parse_qs(source.query).get('p', ['1'])[0]) if platform == 'bilibili' else None
    if part is not None and not 1 <= part <= 100000: raise ValueError('Invalid episode')
    key = f'{platform}:{identifier}' + (f':p{part}' if part else '')
    if platform == 'bilibili':
        canonical = f'https://www.bilibili.com/video/{identifier}?p={part}'
    elif platform == 'youtube':
        from video_parser.platforms.youtube_links import canonical_url
        canonical = canonical_url(metadata.get('source_url', ''))
        if parse_qs(urlsplit(canonical).query)['v'][0] != identifier:
            raise ValueError('YouTube identity mismatch')
    elif source.scheme in ('https', 'http') and source.hostname:
        canonical = urlunsplit(('https', source.hostname, source.path, '', ''))
    else: canonical = ''
    return {'key': key, 'platform': platform, 'video_id': identifier, 'part': part, 'source_url': canonical}


def manifests():
    from video_parser.services.library_layout import manifest_paths
    return manifest_paths(LIBRARY_ROOT)


def load(path):
    contained(path.parent, 'project.json')
    data = json.loads(path.read_text(encoding='utf-8'))
    if data.get('schema_version') != 1 or not re.fullmatch(r'[0-9a-f]{32}', data.get('id', '')):
        raise ValueError('Invalid project manifest')
    return {**data, 'directory': path.parent}


def get(identifier):
    from video_parser.services import library_index
    return library_index.lookup(LIBRARY_ROOT, identifier, load, atomic_json)


def ensure(metadata):
    info = identity(metadata)
    identifier = hashlib.sha256(info['key'].encode()).hexdigest()[:32]
    episodes = normalize_episodes(metadata.get('episodes')) if info['platform'] == 'bilibili' else []
    with file_lock(LIBRARY_ROOT / '.library.lock'):
        try: project = get(identifier)
        except KeyError: pass
        else:
            if episodes and episodes != normalize_episodes(project.get('episodes')):
                with file_lock(contained(project['directory'], '.manifest.lock')):
                    project = get(identifier)
                    project['episodes'] = episodes
                    atomic_json(project['directory'] / 'project.json', {k: v for k, v in project.items() if k != 'directory'})
                    project = get(identifier)
            return project
        project_title = str(metadata.get('title') or 'video')
        if info['platform'] == 'bilibili':
            from video_parser.services.library_layout import series_directory, single_episode, episode_folder
            siblings = list(_bilibili_projects(info['video_id']).values())
            if siblings:
                project_title = min(siblings, key=lambda item: item['part'])['title']
            parent = series_directory(info, metadata.get('title'), siblings)
            contained(parent, 'project.json')
            known_parent = any(item['directory'].parent == parent for item in siblings)
            if parent.exists() and any(parent.iterdir()) and not known_parent:
                raise FileExistsError('项目父目录包含未知内容，未合并或覆盖。')
            candidate = {**info, 'episodes': episodes}
            title = next((item['title'] for item in episodes if item['part'] == info['part']), project_title)
            directory = parent if single_episode(candidate, siblings) else parent / episode_folder(info['part'], title)
            if directory.exists():
                raise FileExistsError('分集目录已存在但身份无法确认，未覆盖。')
        else:
            directory = LIBRARY_ROOT / info['platform'] / (safe_name(metadata.get('title') or 'video') + '__' + info['video_id'])
            if directory.exists():
                directory = directory.with_name(directory.name + '__' + identifier[:8])
        contained(directory, 'project.json')
        directory.mkdir(parents=True, exist_ok=False)
        data = {'schema_version': 1, 'id': identifier, **info, 'title': project_title,
                'created_at': datetime.now(timezone.utc).isoformat(), 'resources': {},
                'identity_status': 'pending' if info['platform'] in ('unknown', 'local') else 'confirmed',
                'learned': False}
        if episodes: data['episodes'] = episodes
        atomic_json(directory / 'project.json', data)
        return {**data, 'directory': directory}


@contextmanager
def operation(identifier):
    project = get(identifier)
    with file_lock(contained(project['directory'], '.operation.lock'), timeout=1800): yield get(identifier)


def import_file(identifier, source, relative, kind, details=None):
    source = Path(source)
    if not source.is_file() or not source.stat().st_size: raise ValueError('Missing or empty resource')
    project = get(identifier)
    with file_lock(contained(project['directory'], '.manifest.lock')):
        project = get(identifier)
        directory = project['directory']
        if relative not in project['resources']:
            # Re-importing a renamed physical filename must reuse its stable logical key.
            relative = next((key for key, info in project['resources'].items()
                             if info.get('kind') == kind and info.get('storage_path') == relative), relative)
        known = project['resources'].get(relative)
        target = resource_path(project, relative) if known else contained(directory, relative)
        target.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(prefix='.publish-', dir=directory) as temporary:
            staged = Path(temporary) / 'resource'
            shutil.copyfile(source, staged)
            hashed = digest(staged)
            if target.exists() and digest(target) != hashed:
                target = contained(directory, str(target.relative_to(directory).with_name(target.stem + '__' + hashed[:12] + target.suffix)))
            if not target.exists(): os.link(staged, target)
            if digest(target) != hashed: raise ValueError('Published resource mismatch')
            key = relative if known and target == resource_path(project, relative) else target.relative_to(directory).as_posix()
            project['resources'][key] = {**(known if known and key == relative else {}), 'kind': kind, 'size_bytes': target.stat().st_size, 'sha256': hashed,
                                         'validated': True, **(details or {})}
            atomic_json(directory / 'project.json', {k: v for k, v in project.items() if k != 'directory'})
        return key


def update_details(identifier, relative, details):
    """Merge metadata into a registered resource without touching its file."""
    project = get(identifier)
    with file_lock(contained(project['directory'], '.manifest.lock')):
        project = get(identifier)
        info = project['resources'].get(relative)
        if not info: raise KeyError('Unregistered resource')
        info.update(details)
        atomic_json(project['directory'] / 'project.json', {k: v for k, v in project.items() if k != 'directory'})


def resource(identifier, relative):
    return resource_from_project(get(identifier), relative)


def resource_path(project, relative):
    """Resolve a stable logical key; physical renames do not change study/chat IDs."""
    info = project['resources'].get(relative)
    if not isinstance(info, dict): raise KeyError('Unregistered resource')
    storage = info.get('storage_path', relative)
    if not isinstance(storage, str) or '\\' in storage or '\0' in storage:
        raise ValueError('Unsafe resource storage path')
    if storage != relative and (info.get('kind') not in ('video', *TRANSCRIPT_KINDS, 'translation') or Path(storage).parent != Path(relative).parent
                                or Path(storage).suffix.lower() != Path(relative).suffix.lower()):
        raise ValueError('Storage aliases may only rename a registered media resource within its original folder')
    return contained(project['directory'], storage)


def logical_resource(project, path):
    """Reverse physical lookup for CLI provenance; retain the legacy fallback for unregistered inputs."""
    path = canonical(path)
    for name in project['resources']:
        if canonical(resource_path(project, name)) == path: return name
    return path.relative_to(canonical(project['directory'])).as_posix()


def resource_from_project(project, relative):
    info = project['resources'].get(relative)
    if not info: raise KeyError('Unregistered resource')
    path = resource_path(project, relative)
    if not path.is_file() or path.stat().st_size != info['size_bytes'] or digest(path) != info['sha256']:
        raise ValueError('项目资源缺失或已变化，请重新校验或获取。')
    return path


def find(identifier, kind):
    project = get(identifier)
    for name, info in project['resources'].items():
        if info['kind'] == kind:
            try: return name, resource_from_project(project, name)
            except (ValueError, OSError): continue
    return None


def save_text(identifier, kind, text, details=None):
    if not text.strip(): raise ValueError('Empty report')
    project = get(identifier)
    with tempfile.TemporaryDirectory(prefix='.report-', dir=project['directory']) as directory:
        source = Path(directory) / 'report.md'
        source.write_text(text, encoding='utf-8')
        batch = datetime.now().strftime('%Y%m%d-%H%M%S') + '-' + uuid4().hex[:8]
        return import_file(identifier, source, f'{kind}/{batch}.md', kind, details)


def local_project(path):
    owner_project = owner(path)
    if owner_project: return owner_project
    return ensure({'platform': 'local', 'video_id': digest(path), 'title': Path(path).stem})


def owner(path):
    path = canonical(path)
    root = canonical(LIBRARY_ROOT)
    if not path.is_relative_to(root): return None
    for parent in path.parents:
        if parent == root: break
        manifest = parent / 'project.json'
        if manifest.is_file(): return load(manifest)
    return None


def episode_title(project):
    """Prefer explicit user naming, otherwise resolve the current part without rewriting data."""
    if project.get('episode_title'):
        return project['episode_title']
    if project.get('platform') != 'bilibili':
        return None
    # A one-part video has no episode to name. The platform's P1 title is frequently the uploader's
    # raw file name ("video_final"), which reads like a bug next to the real video title, so the
    # video title wins there. Multi-part catalogs keep their per-part titles.
    from video_parser.services.library_layout import single_episode
    if single_episode(project):
        return None
    return next((item['title'] for item in normalize_episodes(project.get('episodes'))
                 if item['part'] == project.get('part')), None)


def public(project):
    from video_parser.services.project_folder_naming import display_title
    from video_parser.services.library_layout import single_episode
    return {'id': project['id'], 'title': project['title'], 'display_title': display_title(project), 'platform': project['platform'],
            'video_id': project.get('video_id'), 'episode_title': episode_title(project), 'is_single': single_episode(project),
            'part': project['part'], 'location': project['directory'].relative_to(LIBRARY_ROOT).as_posix(),
            'identity_status': project['identity_status'], 'learned': project.get('learned') is True,
            'resources': [{'path': name, 'kind': item['kind'], 'size_bytes': item['size_bytes'],
                           'storage_path': item.get('storage_path', name), 'filename': item.get('download_name') or Path(item.get('storage_path', name)).name,
                           'url': f"/api/workspace/projects/{project['id']}/files/{name}"}
                          for name, item in project['resources'].items()],
            'archive_url': f"/api/workspace/projects/{project['id']}/archive"}


def _bilibili_projects(video_id):
    projects = {}
    for manifest in manifests():
        try: item = load(manifest)
        except (OSError, ValueError, KeyError, TypeError, AttributeError): continue
        part = item.get('part')
        if item.get('platform') == 'bilibili' and item.get('video_id') == video_id and type(part) is int and 1 <= part <= MAX_PART:
            projects[part] = item
    return projects


def _learning_catalog(project, projects):
    """Old archived parts may lack a catalog; borrow the fullest same-video catalog without writes."""
    episodes = normalize_episodes(project.get('episodes'))
    for sibling in projects.values():
        candidate = normalize_episodes(sibling.get('episodes'))
        if len(candidate) > len(episodes):
            episodes = candidate
    return episodes


def learning_episodes(identifier):
    project = get(identifier)
    if project.get('platform') != 'bilibili':
        return []
    projects = _bilibili_projects(project.get('video_id'))
    episodes = _learning_catalog(project, projects)
    if len(episodes) <= 1:
        return []
    rows = []
    for episode in episodes:
        item = projects.get(episode['part'])
        archived = False
        if item:
            resources = item.get('resources')
            if not isinstance(resources, dict): resources = {}
            for relative, resource in resources.items():
                if not isinstance(resource, dict) or resource.get('kind') != 'video': continue
                try:
                    path = resource_path(item, relative)
                    archived = path.is_file() and type(resource.get('size_bytes')) is int and path.stat().st_size == resource['size_bytes']
                except (OSError, ValueError, TypeError):
                    archived = False
                if archived: break
        rows.append({**episode, 'title': item.get('episode_title') or episode['title'] if item else episode['title'],
                     'project_id': item['id'] if item else None,
                     'archived': archived, 'learned': item.get('learned') is True if item else False})
    return rows


def set_learning_state(identifier, part, learned):
    if type(part) is not int or not 1 <= part <= MAX_PART or type(learned) is not bool:
        raise ValueError('学习状态无效。')
    project = get(identifier)
    projects = _bilibili_projects(project.get('video_id')) if project.get('platform') == 'bilibili' else {}
    episodes = _learning_catalog(project, projects)
    if project.get('platform') != 'bilibili' or len(episodes) <= 1:
        # No catalog is not evidence of a single-part course. Only mark this existing project.
        if part != (project.get('part') or 1):
            raise ValueError('所选分集不属于当前视频。')
        target = project
    else:
        episode = next((item for item in episodes if item['part'] == part), None)
        if episode is None:
            raise ValueError('所选分集不属于当前视频。')
        target = projects.get(part)
        if target is None and not learned:
            return learning_episodes(identifier)
        if target is None:
            source_url = f"https://www.bilibili.com/video/{project['video_id']}?p={part}"
            target = ensure({'platform': '哔哩哔哩', 'video_id': project['video_id'], 'source_url': source_url,
                             'title': project.get('title') or episode['title'], 'episodes': episodes})
    with file_lock(contained(target['directory'], '.manifest.lock')):
        target = get(target['id'])
        target['learned'] = learned
        if project.get('platform') == 'bilibili' and len(episodes) > 1 and episodes != normalize_episodes(target.get('episodes')): target['episodes'] = episodes
        atomic_json(target['directory'] / 'project.json', {k: v for k, v in target.items() if k != 'directory'})
    return learning_episodes(identifier)


def clean_title(value):
    if not isinstance(value, str): raise ValueError('标题无效。')
    title = ' '.join(value.split())
    if not title or len(title) > 200 or any(ord(char) < 32 or 0x7f <= ord(char) < 0xa0 for char in title):
        raise ValueError('标题需为 1–200 个字符，且不含控制字符。')
    return title


def rename(identifier, title):
    """Display metadata only: the stable id, archive directory and resource paths never change.

    Bilibili episodes are separate projects of one course, so every archived part shares the new course title."""
    title = clean_title(title)
    project = get(identifier)
    targets = {identifier: project}
    if project.get('platform') == 'bilibili':
        targets.update({item['id']: item for item in _bilibili_projects(project.get('video_id')).values()})
    for target in targets.values():
        with file_lock(contained(target['directory'], '.manifest.lock')):
            current = get(target['id'])
            from video_parser.services.project_folder_naming import root_location
            location = root_location(current)
            if current.get('title') != title or current.get('title_override_location') != location:
                current['title'] = title
                current['title_override_location'] = location
                atomic_json(current['directory'] / 'project.json', {k: v for k, v in current.items() if k != 'directory'})
    return get(identifier), sorted(targets)


def subtitle_records(project, video_resource=None):
    """Verified original captions plus proofread copies, shared by list badges, previews and inventory."""
    records = []
    for name, info in project.get('resources', {}).items():
        if not isinstance(info, dict) or info.get('kind') not in TRANSCRIPT_KINDS: continue
        if Path(name).suffix.lower() not in ('.srt', '.vtt', '.txt'): continue
        if video_resource and info.get('video_resource') not in (None, '', video_resource): continue
        try:
            path = resource_from_project(project, name)
            if not path.stat().st_size or path.stat().st_size > 2 * 1024 * 1024: continue
        except (OSError, ValueError, KeyError, TypeError): continue
        records.append({'resource': name, 'kind': info['kind'], 'filename': info.get('download_name') or path.name,
                        'language': info.get('language') or '', 'origin': {'transcription': 'local', 'correction': 'corrected'}.get(
                            info['kind'], 'platform' if project.get('platform') == 'bilibili' else 'unknown'), 'sha256': info.get('sha256'), 'url': f"/api/workspace/projects/{project['id']}/files/{name}"})
    return sorted(records, key=lambda row: (row['kind'] != 'subtitle', Path(row['resource']).suffix.lower() == '.txt', row['resource']))


def translation_records(project, video_resource=None, source_resource=None):
    """Verified published translations. Never infer association from a filename/title."""
    source = project.get('resources', {}).get(source_resource, {}) if source_resource else None
    if source is not None and source.get('kind') not in TRANSCRIPT_KINDS: return []
    if source is not None:
        try: source_hash = digest(resource_from_project(project, source_resource))
        except (OSError, ValueError, KeyError): return []
    else: source_hash = None
    rows = []
    for name, info in project.get('resources', {}).items():
        if info.get('kind') != 'translation' or Path(name).suffix.lower() not in ('.srt', '.vtt', '.txt'): continue
        if video_resource and info.get('video_resource') not in (None, '', video_resource): continue
        if source_hash and info.get('source_sha256') != source_hash: continue
        if source_resource and info.get('source_resource') not in (None, '', source_resource): continue
        try:
            path = resource_from_project(project, name)
            if not 0 < path.stat().st_size <= 2 * 1024 * 1024: continue
        except (OSError, ValueError, KeyError): continue
        parent_key = info.get('source_resource')
        resolution = 'recorded' if parent_key else 'unknown'
        # Recover missing legacy references only from one hash-verified, video-compatible original.
        if not parent_key and info.get('source_sha256'):
            candidates = []
            for key, candidate in project.get('resources', {}).items():
                if candidate.get('kind') not in TRANSCRIPT_KINDS or candidate.get('sha256') != info['source_sha256']: continue
                if info.get('video_resource') and candidate.get('video_resource') not in (None, '', info['video_resource']): continue
                try:
                    if digest(resource_from_project(project, key)) == info['source_sha256']: candidates.append(key)
                except (OSError, ValueError, KeyError): pass
            if len(candidates) == 1: parent_key, resolution = candidates[0], 'verified_hash'
        parent = project.get('resources', {}).get(parent_key, {})
        # A legacy/missing/stale parent is explicitly unknown, not guessed from
        # the language or filename. Validate the recorded hash if present.
        parent_kind = parent.get('kind')
        if parent_kind in TRANSCRIPT_KINDS:
            try:
                parent_path = resource_from_project(project, parent_key)
                if info.get('source_sha256') and digest(parent_path) != info['source_sha256']:
                    parent_kind = None
            except (OSError, ValueError, KeyError): parent_kind = None
        parent_origin = {'transcription': 'local', 'correction': 'corrected'}.get(parent_kind, 'platform' if parent_kind == 'subtitle' and project.get('platform') == 'bilibili' else 'unknown')
        rows.append({'resource': name, 'filename': info.get('download_name') or path.name, 'kind': 'translation', 'origin': 'translation',
                     'source_resource': parent_key, 'source_origin': parent_origin,
                     'source_resolution': resolution if parent_origin != 'unknown' else 'unknown',
                     'source_language': (parent.get('language') or '') if parent_kind in TRANSCRIPT_KINDS else '', 'sha256': info.get('sha256'), 'language': info.get('language') or '',
                     'url': f"/api/workspace/projects/{project['id']}/files/{name}",
                     'location': 'outputs/' + path.relative_to(LIBRARY_ROOT).as_posix(), 'modified': path.stat().st_mtime})
    return sorted(rows, key=lambda row: (-row['modified'], Path(row['resource']).suffix.lower() == '.txt', row['resource']))


def subtitle_inventory(video_id):
    """Bilibili episodes whose platform subtitles are registered AND verified in the local library. No network access."""
    if not isinstance(video_id, str) or not re.fullmatch(r'BV[A-Za-z0-9]{10}', video_id): raise ValueError('需要 B站 BV 号。')
    projects = _bilibili_projects(video_id)
    episodes = next((found for item in projects.values() if (found := normalize_episodes(item.get('episodes')))), [])
    archived, available = [], []
    for part, item in sorted(projects.items()):
        records = subtitle_records(item)
        if records: available.append(part)
        if any(row['kind'] == 'subtitle' for row in records): archived.append(part)
    # A one-part video's P1 title is the uploader's raw file name, so the video title is the only
    # meaningful label there. Multi-part catalogs keep their per-part titles.
    single = len(episodes) <= 1
    def label(episode):
        owner = projects.get(episode['part'])
        return owner.get('title') if single and owner else episode['title']
    return {'video_id': video_id, 'total': len(episodes) or None, 'archived': archived, 'available': available,
            'episodes': [{'part': episode['part'], 'title': label(episode), 'subtitles': episode['part'] in available,
                          'platform_subtitles': episode['part'] in archived} for episode in episodes]}



def archive(identifier):
    project = get(identifier)
    with file_lock(contained(project['directory'], '.manifest.lock')):
        project = get(identifier)
        export = LIBRARY_ROOT / '.exports'
        export.mkdir(exist_ok=True)
        target = export / (identifier + '-' + uuid4().hex + '.zip')
        try:
            with ZipFile(target, 'x', compression=ZIP_DEFLATED) as zipped:
                for name in project['resources']:
                    path = resource(identifier, name)
                    zipped.write(path, path.relative_to(project['directory']).as_posix())
                zipped.write(project['directory'] / 'project.json', 'project.json')
                personal = contained(project['directory'], 'personal-learning')
                if personal.is_dir():
                    with file_lock(contained(project['directory'], '.personal-learning.lock'), timeout=3):
                        for sidecar in sorted(personal.glob('*.json')):
                            if re.fullmatch(r'[a-f0-9]{64}\.json', sidecar.name):
                                relative = 'personal-learning/' + sidecar.name
                                zipped.write(contained(project['directory'], relative), relative)
        except Exception:
            target.unlink(missing_ok=True); raise
        return target


def remove(identifier, names):
    """Unregister and delete resources, e.g. when the user deletes an analysis record."""
    project = get(identifier)
    with file_lock(contained(project['directory'], '.manifest.lock')):
        project = get(identifier)
        directory = project['directory']
        for name in dict.fromkeys(names):
            if name not in project['resources']: continue
            path = resource_path(project, name)
            if path.is_file() and not path.is_symlink(): path.unlink()
            del project['resources'][name]
            parent = path.parent
            while parent != directory and parent.is_dir() and not any(parent.iterdir()):
                parent.rmdir(); parent = parent.parent
        atomic_json(directory / 'project.json', {k: v for k, v in project.items() if k != 'directory'})


def import_frames(identifier, directory):
    files = sorted(Path(directory).glob('*.jpg'))
    if not files: return []
    batch = datetime.now().strftime('%Y%m%d-%H%M%S') + '-' + uuid4().hex[:8]
    return [import_file(identifier, path, f'frames/{batch}/{path.name}', 'frame') for path in files]
