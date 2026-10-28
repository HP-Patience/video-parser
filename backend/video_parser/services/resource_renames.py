"""Previewed, no-clobber physical renames with stable logical resource identities.

Only explicitly confirmed plans run. Hard links retain at least one copy throughout
commit/rollback. A durable journal survives a process crash; recovery is explicit.
"""
from contextlib import ExitStack
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
import unicodedata
from uuid import uuid4
import os

from video_parser.services import library, task_runtime, library_layout, archive_layout

NAME_FIELDS = ('storage_path', 'download_name')
PROJECT_FIELDS = ('episode_title', 'hierarchical_name')
NUMBER = re.compile(r'[1-9][0-9]{0,3}(?:\.[1-9][0-9]{0,3}){0,5}')
MAX_SELECTED = 150


class NamingError(ValueError):
    code = 'naming_validation'

    @property
    def user_message(self):
        return str(self)


def now():
    return datetime.now(timezone.utc).isoformat()


def scope(identifier):
    project = library.get(identifier)
    if project.get('platform') == 'bilibili':
        return [value for _, value in sorted(library._bilibili_projects(project.get('video_id')).items())]
    return [project]


def plan_path(identifier):
    if not isinstance(identifier, str) or not re.fullmatch('[a-f0-9]{32}', identifier):
        raise NamingError('命名预览编号无效。')
    return library.contained(library.LIBRARY_ROOT, f'.naming-plans/{identifier}.json')


def load_plan(identifier):
    path = plan_path(identifier)
    if not path.is_file() or path.stat().st_size > 16 * 1024 * 1024:
        raise KeyError('命名预览不存在。')
    plan = json.loads(path.read_text(encoding='utf-8'))
    if plan.get('version') != 1 or plan.get('id') != identifier:
        raise NamingError('命名预览记录无效。')
    return plan


def save_plan(plan):
    path = plan_path(plan['id']); path.parent.mkdir(parents=True, exist_ok=True)
    library.atomic_json(path, plan)


def plan_lock(identifier):
    return library.file_lock(plan_path(identifier).with_suffix('.lock'), timeout=3)


def history(identifier):
    ids = {item['id'] for item in scope(identifier)}
    root = library.contained(library.LIBRARY_ROOT, '.naming-plans')
    records = []
    if root.is_dir():
        for path in root.glob('*.json'):
            try: plan = load_plan(path.stem)
            except (ValueError, KeyError, OSError): continue
            if plan.get('project_id') in ids and plan.get('scope') != 'project_folder':
                records.append({key: plan.get(key) for key in ('id', 'state', 'created_at', 'job_id', 'active_job_id', 'last_error')})
    ordered = sorted(records, key=lambda item: item.get('created_at') or '', reverse=True)
    pending = [item for item in ordered if item['state'] in ('applying', 'recovery_required')]
    return pending + [item for item in ordered if item not in pending][:max(0, 10 - len(pending))]


def _file_stat(path):
    stat = path.stat()
    return {'size': stat.st_size, 'mtime_ns': stat.st_mtime_ns, 'inode': stat.st_ino, 'device': stat.st_dev}


SUBTITLE_KINDS = {'subtitle', 'transcription', 'translation', 'correction'}
SUBTITLE_EXTENSIONS = {'.srt', '.vtt', '.ass', '.ssa', '.txt', '.md', '.json'}
JOINT_SCOPE = 'video_subtitles'


def _subtitle_suffix(project, name, info):
    language = info.get('language') or ''
    if not re.fullmatch(r'[A-Za-z0-9-]{1,35}', language):
        match = re.search(r'\.(zh(?:-CN|-TW)?|en|ja|ko)(?:[.\-]|$)', name, re.I)
        language = match[1] if match else ''
    kind = info['kind']
    parent = project['resources'].get(info.get('source_resource'), {})
    origin = parent.get('kind') if kind == 'translation' else kind
    if kind == 'translation' and info.get('source_sha256') and info['source_sha256'] != parent.get('sha256'):
        origin = None
    # Missing provenance remains unknown, not a guess based on an old filename.
    from video_parser.services import subtitle_names
    return subtitle_names.suffix(kind, language, origin, project.get('platform'))



def inventory(identifier):
    entries, signatures = [], []
    projects = scope(identifier)
    for project in projects:
        files, subtitles = [], []
        for name, info in project.get('resources', {}).items():
            if info.get('kind') not in {'video', *SUBTITLE_KINDS}: continue
            try:
                path = library.resource_path(project, name)
                stat = _file_stat(path)
                if not path.is_file() or stat['size'] != info.get('size_bytes'): continue
            except (ValueError, OSError, KeyError, TypeError): continue
            item = {'resource': name, 'source': path.relative_to(project['directory']).as_posix(),
                    'filename': path.name, 'sha256': info['sha256'], 'stat': stat, 'kind': info['kind']}
            if info['kind'] == 'video':
                files.append({**item, 'quality': (info.get('selected_quality') or {}).get('label') or ''})
            elif path.suffix.lower() in SUBTITLE_EXTENSIONS:
                subtitles.append({**item, 'video_resource': info.get('video_resource'),
                                  'download_name': info.get('download_name') or path.name,
                                  'suffix': _subtitle_suffix(project, info.get('download_name') or path.name, info)})
        video_keys = {file['resource'] for file in files}
        subtitles = [item for item in subtitles if not item['video_resource'] or item['video_resource'] in video_keys]
        # Keep languages, sources, formats and duplicate tracks distinct, with deterministic names.
        for item in subtitles:
            peers = [other for other in subtitles if (Path(other['source']).parent, other['suffix'].casefold(), Path(other['source']).suffix.lower()) ==
                     (Path(item['source']).parent, item['suffix'].casefold(), Path(item['source']).suffix.lower())]
            item['disambiguator'] = ' [' + hashlib.sha256(item['resource'].encode()).hexdigest()[:6] + ']' if len(peers) > 1 else ''
        transcripts = [item for item in subtitles if Path(item['source']).suffix.lower() in ('.srt', '.vtt')]
        transcripts.sort(key=lambda item: (item['kind'] == 'translation', item['kind'] != 'subtitle', item['resource']))
        entries.append({'project_id': project['id'], 'part': project.get('part'),
                        # A one-part video has no episode name: its platform P1 title is the uploader's
                        # raw file name ("video_final"), so the video title is the only usable label here.
                        'title': library.episode_title(project) or project.get('title', '视频'),
                        'number': (project.get('hierarchical_name') or {}).get('number', ''),
                        'single_episode': library_layout.single_episode(project, projects),
                        'files': files, 'subtitle_files': subtitles,
                        'transcript': transcripts[0]['resource'] if transcripts else None,
                        'transcript_filename': transcripts[0]['download_name'] if transcripts else None})
        signatures.append({'id': project['id'], 'platform': project.get('platform'), 'video_id': project.get('video_id'),
                           'part': project.get('part'), 'title': project.get('title'), 'episode_title': project.get('episode_title'),
                           'hierarchical_name': project.get('hierarchical_name'), 'files': files, 'subtitles': subtitles})
    fingerprint = hashlib.sha256(json.dumps(signatures, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
    return {'project_id': identifier, 'title': library.get(identifier)['title'], 'entries': entries,
            'fingerprint': fingerprint, 'subtitle_count': sum(bool(item['transcript']) for item in entries),
            'ready_count': sum(bool(item['transcript'] and item['files']) for item in entries), 'max_selected': MAX_SELECTED}


def plan_files(plan, entry):
    return entry['files'] + (entry.get('subtitle_files', []) if plan.get('rename_scope') == JOINT_SCOPE else [])


def clean_name(number, title, allow_empty_number=False):
    if not isinstance(number, str) or not (NUMBER.fullmatch(number) or (allow_empty_number and number == '')):
        raise NamingError('层级序号需为 1、1.1、2.2.1 等正整数层级，最多六层，不使用前导零。')
    if not isinstance(title, str) or any(unicodedata.category(char).startswith('C') for char in title):
        raise NamingError('标题不能含控制、隐藏或方向控制字符。')
    if title != title.strip() or title.endswith('.') or re.search(r'[<>:"/\\|?*]', title):
        raise NamingError('标题含 Windows 不安全字符、末尾句点或首尾空格，请编辑后重新预览。')
    title = unicodedata.normalize('NFC', title)
    if not title or len(title) > 100:
        raise NamingError('标题需要 1–100 个字符。')
    if not number:
        subtitle_stem(title + '.srt')
    return number, title


def subtitle_stem(filename):
    """Reuse the published subtitle filename verbatim, never a course title or model guess."""
    if not isinstance(filename, str) or '/' in filename or '\\' in filename or Path(filename).suffix.lower() not in ('.srt', '.vtt'):
        raise NamingError('字幕文件名无效，请先核对字幕名称。')
    stem = Path(filename).stem
    if (not stem or stem != stem.strip() or stem.endswith('.') or
            re.search(r'[<>:"/\\|?*]', stem) or
            any(unicodedata.category(char).startswith('C') for char in stem) or
            re.fullmatch(r'(?:CON|PRN|AUX|NUL|COM[1-9]|LPT[1-9])(?:\..*)?', stem, re.I)):
        raise NamingError('字幕名称不能安全地用作 Windows 视频文件名，请先修改字幕名称。')
    return stem


def reuse_subtitles(identifier, selected_ids=None):
    """Create a local preview from subtitle names. No model selection, calls or file moves."""
    current = inventory(identifier)
    eligible = {item['project_id']: item for item in current['entries'] if item['files'] and item['transcript']}
    selected_ids = list(eligible) if selected_ids is None else selected_ids
    if (not isinstance(selected_ids, list) or not 1 <= len(selected_ids) <= MAX_SELECTED or
            len(set(selected_ids)) != len(selected_ids) or any(value not in eligible for value in selected_ids)):
        raise NamingError('请选择 1–150 个具有本地字幕和归档视频的分集。')
    if any(item['state'] in ('applying', 'recovery_required') for item in history(identifier)):
        raise NamingError('本课程有未恢复的文件事务，请先恢复原命名。')
    entries = [item for item in current['entries'] if item['project_id'] in selected_ids]
    rows = [{'project_id': item['project_id'], 'number': '', 'title': subtitle_stem(item['transcript_filename'])} for item in entries]
    plan = {'version': 1, 'id': uuid4().hex, 'project_id': identifier, 'title': current['title'],
            'created_at': now(), 'state': 'proposed', 'source': 'subtitle', 'rename_scope': JOINT_SCOPE,
            'fingerprint': current['fingerprint'], 'entries': entries, 'rows': rows, 'last_error': ''}
    save_plan(plan)
    return {'plan_id': plan['id'], 'state': plan['state']}


def adjust_plan(project_id, identifier):
    """Fork only the applied subset into a fresh local draft, never reuse its transaction."""
    with task_runtime.exclusive_operation(), plan_lock(identifier), ExitStack() as stack:
        previous = load_plan(identifier)
        if previous.get('project_id') not in {project['id'] for project in scope(project_id)}:
            raise NamingError('这个命名结果不属于当前课程，未创建调整草稿。')
        if previous.get('state') != 'applied' or not previous.get('transaction'):
            raise NamingError('只能调整已完成的命名；未完成的事务请先恢复。')
        if any(item['state'] in ('applying', 'recovery_required') for item in history(project_id)):
            raise NamingError('本课程有未恢复的文件事务，请先恢复原命名。')
        _locked(previous, stack)
        current = inventory(project_id)
        rows = (previous.get('preview') or {}).get('rows') or []
        selected = {row['project_id'] for row in rows}
        entries = [item for item in current['entries'] if item['project_id'] in selected and item['files'] and item['transcript']]
        transaction = previous['transaction']
        if (not 1 <= len(rows) <= MAX_SELECTED or len(selected) != len(rows) or
                len(entries) != len(rows) or set(transaction['manifests']) != selected):
            raise NamingError('原命名结果或当前分集不完整，请重新读取课程清单。')
        files = {(entry['project_id'], file['resource']): file for entry in entries for file in plan_files(previous, entry)}
        for entry in entries:
            project = library.get(entry['project_id'])
            saved = transaction['manifests'][entry['project_id']]
            if _fields(project, PROJECT_FIELDS) != saved['after']:
                raise NamingError('这份命名结果已被之后的修改替代，请打开最近的结果再调整。')
            for resource, fields in saved['resources'].items():
                info = project['resources'].get(resource) or {}
                if _fields(info, NAME_FIELDS) != fields['after'] or info.get('sha256') != fields['sha256']:
                    raise NamingError('原视频或命名信息已变化，未创建调整草稿。')
        for move in transaction['moves']:
            file = files.get((move['project_id'], move['resource']))
            if not file or file['source'] != move['target'] or file['stat'] != move['stat']:
                raise NamingError('原视频位置或内容已变化，请重新核对后再调整。')
        plan = {'version': 1, 'id': uuid4().hex, 'project_id': project_id, 'title': current['title'],
                'created_at': now(), 'state': 'proposed', 'source': 'manual', 'adjusted_from': identifier, 'rename_scope': JOINT_SCOPE,
                'fingerprint': current['fingerprint'], 'entries': entries,
                'rows': [{key: row[key] for key in ('project_id', 'number', 'title')} for row in rows], 'last_error': ''}
        save_plan(plan)
        return {'plan_id': plan['id'], 'state': plan['state']}


def _filename(project, file, number, title, multiple):
    suffix = ''
    if file.get('kind') in SUBTITLE_KINDS:
        suffix = file.get('disambiguator', '') + file.get('suffix', '')
    elif multiple:
        quality = library.safe_name(file.get('quality') or '画质')[:22]
        quality = ''.join('_' if unicodedata.category(char).startswith('C') else char for char in quality)
        suffix = f" [{quality}-{hashlib.sha256(file['resource'].encode()).hexdigest()[:6]}]"
    base = f'{number} {title}' if number else title
    if file.get('kind') in SUBTITLE_KINDS and suffix and base.endswith(suffix): suffix = ''
    name = f'{base}{suffix}{Path(file["source"]).suffix}'
    path = library.contained(project['directory'], (Path(file['source']).parent / name).as_posix())
    if len(name.encode('utf-16-le')) // 2 > 180 or len(str(path).encode('utf-16-le')) // 2 > 240:
        raise NamingError('新文件名或完整路径过长，请缩短标题；不会自动移动项目目录。')
    return path


def preview(plan, rows):
    current = inventory(plan['project_id'])
    if current['fingerprint'] != plan['fingerprint']:
        raise NamingError('项目、字幕或视频自生成建议后已变化，请重新生成命名建议，未改动文件。')
    allowed = {item['project_id']: item for item in plan['entries']}
    if not isinstance(rows, list) or not 1 <= len(rows) <= MAX_SELECTED:
        raise NamingError('请选择 1–150 个有字幕的已归档视频。')
    if len({row.get('project_id') for row in rows}) != len(rows):
        raise NamingError('命名列表包含重复项目。')
    numbers, targets, normalized = set(), set(), []
    for row in rows:
        identifier = row.get('project_id')
        if identifier not in allowed: raise NamingError('命名项目不属于本次已选范围。')
        if plan.get('source') == 'subtitle':
            number, title = '', subtitle_stem(allowed[identifier]['transcript_filename'])
            if row.get('number') != number or row.get('title') != title:
                raise NamingError('复用字幕名时不能另拟视频标题，请重新读取对应字幕名称。')
        else:
            single = next(item for item in current['entries'] if item['project_id'] == identifier)['single_episode']
            number, title = clean_name('' if single else row.get('number'), row.get('title'),
                                       allow_empty_number=single or plan.get('source') == 'manual')
        if number:
            if number in numbers: raise NamingError('层级序号重复，请编辑后重新预览。')
            numbers.add(number)
        entry = allowed[identifier]; project = library.get(identifier)
        for file in plan_files(plan, entry):
            source = library.contained(project['directory'], file['source'])
            target = _filename(project, file, number, title, len(entry['files']) > 1)
            key = (identifier, unicodedata.normalize('NFC', str(target.relative_to(project['directory']))).casefold())
            if key in targets: raise NamingError('新文件名重复，不会覆盖文件。')
            targets.add(key)
            if source.name != target.name and target.exists():
                raise NamingError(f'目标文件已存在：{target.name}；不会覆盖。')
            if source.name != target.name and source.name.casefold() == target.name.casefold():
                raise NamingError('仅大小写不同的改名暂不执行，请使用不同名称。')
        normalized.append({'project_id': identifier, 'number': number, 'title': title})
    selected_ids = {row['project_id'] for row in rows}
    for entry in current['entries']:
        if entry['project_id'] not in selected_ids and entry.get('number') in numbers:
            raise NamingError('序号与未选择的已命名分集重复，请调整。')
    moves = []
    for row in normalized:
        entry = allowed[row['project_id']]; project = library.get(row['project_id'])
        for file in plan_files(plan, entry):
            target = _filename(project, file, row['number'], row['title'], len(entry['files']) > 1)
            moves.append({**file, 'project_id': row['project_id'], 'target': target.relative_to(project['directory']).as_posix()})
    directory_moves = archive_layout.naming_moves({row['project_id']: library.get(row['project_id']) for row in normalized}, normalized)
    for directory in directory_moves:
        for move in moves:
            if move['project_id'] == directory['project_id'] and len(str(library.LIBRARY_ROOT / directory['new'] / move['target']).encode('utf-16-le')) // 2 > 240:
                raise NamingError('新文件与目录组合路径过长，请缩短名称。')
    return {'rows': normalized, 'moves': moves, 'directory_moves': directory_moves, 'fingerprint': current['fingerprint']}


def validate(identifier, rows):
    with plan_lock(identifier):
        plan = load_plan(identifier)
        if plan['state'] not in ('proposed', 'validated'):
            raise NamingError('当前预览不能执行；已执行计划需撤销，未完成事务需先恢复。')
        result = preview(plan, rows)
        plan.update(state='validated', preview=result, preview_token=uuid4().hex, last_error='')
        save_plan(plan)
        return {**result, 'preview_token': plan['preview_token'], 'plan_id': identifier}


def _fields(data, fields):
    return {key: data[key] for key in fields if key in data}


def _set_fields(data, fields, values):
    for key in fields:
        if key in values: data[key] = values[key]
        else: data.pop(key, None)


def _check_file(path, move):
    if _file_stat(path) != move['stat'] or library.digest(path) != move['sha256'] or _file_stat(path) != move['stat']:
        raise NamingError('文件内容、位置或时间戳已变化，未覆盖任何文件，请重新核对。')


def _checked_link(source, target, move):
    # Cooperative locks cannot stop external programs replacing a path after preflight.
    if _file_stat(source) != move['stat']:
        raise NamingError('提交前发现视频已被外部修改，保留文件等待恢复。')
    os.link(source, target)  # Atomic no-clobber; unsupported filesystems fail without moving the original.
    if not source.samefile(target) or _file_stat(target) != move['stat']:
        raise NamingError('创建文件名时视频被外部替换，保留两份文件等待核对。')


def _restore(plan):
    """Preflight every source/target and merge ONLY naming-owned metadata fields."""
    transaction = plan['transaction']; replacements = {}; writes = set(); links = []
    for move in transaction['moves']:
        project = library.get(move['project_id'])
        source = library.contained(project['directory'], move['source'])
        target = library.contained(project['directory'], move['target'])
        if source.exists(): _check_file(source, move)
        elif target.exists(): _check_file(target, move); links.append((target, source, move))
        else: raise NamingError('原文件与改名文件均不存在，需手动恢复备份；未覆盖其他文件。')
        if target.exists():
            _check_file(target, move)
            if source.exists() and not source.samefile(target): raise NamingError('恢复位置已被其他文件占用，未覆盖。')
    for identifier, saved in transaction['manifests'].items():
        current = library.get(identifier)
        for resource, fields in saved['resources'].items():
            info = current['resources'].get(resource)
            if not info or info.get('sha256') != fields['sha256']: raise NamingError('视频资源已更换，不能覆盖新的资源关联。')
            if _fields(info, NAME_FIELDS) not in (fields['before'], fields['after']):
                raise NamingError('文件命名信息已被另一次修改，请先核对，不会覆盖。')
            if _fields(info, NAME_FIELDS) != fields['before']: writes.add(identifier)
            _set_fields(info, NAME_FIELDS, fields['before'])
        if _fields(current, PROJECT_FIELDS) not in (saved['before'], saved['after']):
            raise NamingError('视频界面标题已被另一次修改，未覆盖。')
        if _fields(current, PROJECT_FIELDS) != saved['before']: writes.add(identifier)
        _set_fields(current, PROJECT_FIELDS, saved['before'])
        replacements[identifier] = current
    for row in transaction.get('directory_moves', []): archive_layout.check_simple(row)
    for source, target, move in links: _checked_link(source, target, move)
    for identifier in writes:
        project = replacements[identifier]
        library.atomic_json(project['directory'] / 'project.json', {key: value for key, value in project.items() if key != 'directory'})
    for move in transaction['moves']:
        if move['source'] == move['target']: continue
        project = replacements[move['project_id']]
        source = library.contained(project['directory'], move['source'])
        target = library.contained(project['directory'], move['target'])
        if target.exists():
            if not source.samefile(target) or _file_stat(target) != move['stat']: raise NamingError('恢复时发现目标已被替换或修改，保留两份文件等待核对。')
            target.unlink()
    archive_layout.rename_directories(transaction.get('directory_moves', []), undo=True)


def _locked(plan, stack):
    # Sorted lock acquisition avoids multi-project deadlocks with another naming operation.
    for identifier in sorted({entry['project_id'] for entry in plan['entries']}):
        project = library.get(identifier)
        stack.enter_context(library.file_lock(library.contained(project['directory'], '.operation.lock'), timeout=3))
        stack.enter_context(library.file_lock(library.contained(project['directory'], '.manifest.lock'), timeout=3))


def apply(identifier, token):
    with task_runtime.exclusive_operation(), plan_lock(identifier), ExitStack() as stack:
        plan = load_plan(identifier)
        if plan.get('state') == 'applied' and token == plan.get('preview_token'):
            return {'plan_id': identifier, 'state': 'applied', 'changed': len(plan['transaction']['moves'])}
        if plan.get('state') != 'validated' or not token or token != plan.get('preview_token'):
            raise NamingError('需要先验证最终名称并明确确认；过期预览不能执行。')
        stack.enter_context(library.file_lock(library.LIBRARY_ROOT / '.library.lock', timeout=3))
        _locked(plan, stack)
        checked = preview(plan, plan['preview']['rows'])
        for index, move in enumerate(checked['moves']):
            task_runtime.checkpoint()
            project = library.get(move['project_id'])
            _check_file(library.contained(project['directory'], move['source']), move)
            task_runtime.progress('validating', '正在逐个校验视频与字幕；校验完成前不会改名。', done=index + 1, total=len(checked['moves']))
        transaction = {'moves': checked['moves'], 'directory_moves': checked['directory_moves'], 'manifests': {}}
        projects = {row['project_id']: library.get(row['project_id']) for row in checked['rows']}
        for row in checked['rows']:
            project = projects[row['project_id']]
            saved = {'before': _fields(project, PROJECT_FIELDS), 'resources': {}}
            project['episode_title'] = f'{row["number"]} {row["title"]}' if row['number'] else row['title']
            if not row['number']:
                project.pop('hierarchical_name', None)
            else:
                project['hierarchical_name'] = {'number': row['number'], 'title': row['title']}
            saved['after'] = _fields(project, PROJECT_FIELDS)
            for move in (item for item in checked['moves'] if item['project_id'] == row['project_id']):
                info = project['resources'][move['resource']]
                old = _fields(info, NAME_FIELDS)
                info.update(storage_path=move['target'], download_name=Path(move['target']).name)
                saved['resources'][move['resource']] = {'sha256': info['sha256'], 'before': old, 'after': _fields(info, NAME_FIELDS)}
            transaction['manifests'][row['project_id']] = saved
        task_runtime.progress('archiving', '正在提交真实文件名与界面标题；本阶段不可中断，完成后可撤销。')
        task_runtime.checkpoint()
        plan.update(state='applying', transaction=transaction); save_plan(plan)
        try:
            # Critical commit: no cancellation checkpoints; afterwards an explicit Undo remains available.
            for move in checked['moves']:
                if move['source'] == move['target']: continue
                project = projects[move['project_id']]
                source = library.contained(project['directory'], move['source'])
                target = library.contained(project['directory'], move['target'])
                _checked_link(source, target, move)
            for project in projects.values():
                library.atomic_json(project['directory'] / 'project.json', {key: value for key, value in project.items() if key != 'directory'})
            for move in checked['moves']:
                if move['source'] != move['target']:
                    project = projects[move['project_id']]
                    source = library.contained(project['directory'], move['source'])
                    target = library.contained(project['directory'], move['target'])
                    if not source.samefile(target) or _file_stat(source) != move['stat']:
                        raise NamingError('删除旧文件名前检测到外部修改，保留两份文件等待恢复。')
                    source.unlink()
            archive_layout.rename_directories(transaction['directory_moves'])
            plan.update(state='applied', applied_at=now(), last_error=''); save_plan(plan)
        except BaseException as error:
            try:
                _restore(plan)
                plan.update(state='proposed', preview_token='', last_error='执行失败，已恢复原文件名和原界面标题；请重新验证。')
            except BaseException:
                plan.update(state='recovery_required', last_error='事务未完成。文件副本与恢复日志保留；请关闭占用程序后使用“恢复原命名”，不要手动删除副本。')
            save_plan(plan)
            raise NamingError(plan['last_error']) from error
        return {'plan_id': identifier, 'state': plan['state'], 'changed': len(checked['moves'])}


def restore(identifier):
    with task_runtime.exclusive_operation(), plan_lock(identifier), ExitStack() as stack:
        plan = load_plan(identifier)
        if plan['state'] == 'rolled_back': return {'plan_id': identifier, 'state': 'rolled_back'}
        if plan['state'] not in ('applied', 'applying', 'recovery_required') or not plan.get('transaction'):
            raise NamingError('这个计划没有可撤销或恢复的文件事务。')
        stack.enter_context(library.file_lock(library.LIBRARY_ROOT / '.library.lock', timeout=3))
        _locked(plan, stack)
        task_runtime.progress('archiving', '正在恢复文件名与界面标题；本阶段不可中断，之后新增的学习内容保留。')
        task_runtime.checkpoint()
        plan.update(state='recovery_required', last_error='恢复事务尚未完成；如服务中断，请继续恢复原命名。'); save_plan(plan)
        try:
            _restore(plan)
            plan.update(state='rolled_back', restored_at=now(), last_error=''); save_plan(plan)
        except BaseException as error:
            plan.update(state='recovery_required', last_error='恢复未完成，保留全部可用文件与日志。请检查占用、同名文件或外部修改后再次恢复。')
            save_plan(plan)
            raise NamingError(plan['last_error']) from error
        return {'plan_id': identifier, 'state': 'rolled_back'}
