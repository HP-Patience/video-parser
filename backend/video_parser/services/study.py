"""Personal playback and notes sidecars, isolated from AI records and media manifests."""
from datetime import datetime, timezone
import hashlib
import json
import math
import re
from video_parser.services import anki_export, library

MAX_TIME = 31 * 86400
MAX_NOTES = 1000
MAX_ENTRIES = 10000
MAX_ANSWERS = 200
RECORD_ID = re.compile(r'[0-9A-Za-z_-]{1,64}')

class Conflict(ValueError):
    pass


def seconds(value):
    if type(value) not in (int, float) or not 0 <= value <= MAX_TIME or not math.isfinite(value):
        raise ValueError('时间点无效。')
    return float(value)


def context(identifier, resource):
    project = library.get(identifier)
    item = project['resources'].get(resource)
    if not isinstance(item, dict) or item.get('kind') != 'video':
        raise ValueError('只能为当前项目内已归档的视频保存学习数据。')
    media = library.resource_path(project, resource)
    if not media.is_file() or media.stat().st_size != item.get('size_bytes'):
        raise ValueError('归档视频缺失或已变化，请重新校验。')
    # The archive manifest pins content; do not rehash multi-GB media on every playback checkpoint.
    fingerprint = str(item.get('sha256', ''))
    if not re.fullmatch('[a-f0-9]{64}', fingerprint): raise ValueError('视频缺少内容标识。')
    name = hashlib.sha256((resource + ':' + fingerprint).encode()).hexdigest()
    path = library.contained(project['directory'], f'personal-learning/{name}.json')
    try: duration = float(item['media']['format']['duration'])
    except (KeyError, TypeError, ValueError, OverflowError): duration = 0
    if not math.isfinite(duration) or not 0 < duration <= MAX_TIME: duration = 0
    return project, path, fingerprint, duration


def read(path, identifier, resource, fingerprint):
    if not path.exists():
        return {'version': 1, 'project_id': identifier, 'resource': resource, 'fingerprint': fingerprint,
                'progress': {'position': 0, 'duration': 0, 'revision': 0}, 'notes': {}, 'quizzes': {}}
    try:
        if path.stat().st_size > 8 * 1024 * 1024: raise ValueError()
        data = json.loads(path.read_text(encoding='utf-8'))
        assert data['version'] == 1 and data['project_id'] == identifier and data['resource'] == resource and data['fingerprint'] == fingerprint
        assert isinstance(data['notes'], dict) and len(data['notes']) <= MAX_ENTRIES
        progress = data['progress']; seconds(progress['position']); seconds(progress['duration'])
        assert type(progress['revision']) is int and progress['revision'] >= 0
        for key, note in data['notes'].items():
            assert re.fullmatch('[a-f0-9]{32}', key) and note['id'] == key
            assert type(note['revision']) is int and note['revision'] > 0
            if not note.get('deleted'):
                seconds(note['time']); assert isinstance(note['text'], str) and 0 < len(note['text']) <= 5000
        # Sidecars written before quizzes existed have no key; filling it in keeps old files readable.
        quizzes = data.setdefault('quizzes', {})
        assert isinstance(quizzes, dict) and len(quizzes) <= MAX_ENTRIES
        for key, row in quizzes.items():
            assert RECORD_ID.fullmatch(key) and isinstance(row.get('answers'), dict)
            assert len(row['answers']) <= MAX_ANSWERS
            for index, choice in row['answers'].items():
                assert re.fullmatch(r'\d{1,3}', str(index)) and type(choice) is int and 0 <= choice <= 5
        return data
    except (ValueError, KeyError, TypeError, AssertionError, AttributeError):
        raise ValueError('学习数据无法读取，原文件已保留；请备份后检查，不会自动覆盖。') from None


def load(identifier, resource):
    _, path, fingerprint, _ = context(identifier, resource)
    return read(path, identifier, resource, fingerprint)


def notes(identifier, resource):
    data = load(identifier, resource)
    return sorted((note for note in data['notes'].values() if not note.get('deleted')), key=lambda row: (row['time'], row['id']))


def mutate(identifier, resource, operation):
    project, _, _, _ = context(identifier, resource)
    lock = library.contained(project['directory'], '.personal-learning.lock')
    with library.file_lock(lock, timeout=3):
        _, path, fingerprint, duration = context(identifier, resource)
        data = read(path, identifier, resource, fingerprint)
        value = operation(data, duration)
        path.parent.mkdir(exist_ok=True)
        library.atomic_json(path, data)
        return value


def revision(value):
    if type(value) is not int or value < 0: raise ValueError('版本号无效。')


def progress(identifier, resource, position, duration, expected_revision):
    revision(expected_revision)
    position, duration = seconds(position), seconds(duration)
    if duration <= 0: raise ValueError('视频时长尚未就绪。')
    def change(data, actual):
        previous = data['progress']
        if previous['revision'] != expected_revision: raise Conflict('播放位置已被其他窗口更新；本地位置已保留，请重试确认覆盖。')
        length = actual or duration
        row = {'position': round(min(position, max(0, length - .01)), 3), 'duration': length,
               'revision': previous['revision'] + 1, 'updated_at': datetime.now(timezone.utc).isoformat()}
        data['progress'] = row
        return row
    return mutate(identifier, resource, change)


def save_note(identifier, resource, note_id, at, text, expected_revision):
    revision(expected_revision)
    at = seconds(at)
    if not re.fullmatch('[a-f0-9]{32}', note_id): raise ValueError('笔记编号无效。')
    if not isinstance(text, str) or not text.strip() or len(text) > 5000: raise ValueError('笔记需为 1～5000 字符。')
    def change(data, duration):
        previous = data['notes'].get(note_id)
        if (previous or {}).get('revision', 0) != expected_revision or (previous and previous.get('deleted')):
            raise Conflict('这条笔记已被其他窗口修改或删除。草稿已保留，可另存为新笔记。')
        if duration and at >= duration: raise ValueError('笔记时间超出视频长度。')
        if not previous and (len(data['notes']) >= MAX_ENTRIES or sum(not n.get('deleted') for n in data['notes'].values()) >= MAX_NOTES):
            raise ValueError('此视频笔记数量已达上限，请整理后再添加。')
        now = datetime.now(timezone.utc).isoformat()
        note = {'id': note_id, 'time': at, 'text': text.strip(), 'revision': expected_revision + 1,
                'created_at': previous['created_at'] if previous else now, 'updated_at': now}
        data['notes'][note_id] = note
        return note
    return mutate(identifier, resource, change)


def delete_note(identifier, resource, note_id, expected_revision):
    revision(expected_revision)
    def change(data, _):
        previous = data['notes'].get(note_id)
        if not previous: raise KeyError('笔记不存在。')
        if previous['revision'] != expected_revision or previous.get('deleted'):
            raise Conflict('笔记已变化，请刷新后再删除。')
        # Keep only a revision tombstone, never deleted personal text; stale autosaves cannot resurrect it.
        data['notes'][note_id] = {'id': note_id, 'revision': expected_revision + 1, 'deleted': True}
        return {'deleted': note_id}
    return mutate(identifier, resource, change)


def quiz(identifier, resource, record_id):
    """Answers already given for one analysis record; survives a refresh."""
    if not RECORD_ID.fullmatch(record_id or ''): raise ValueError('分析记录标识无效。')
    row = (load(identifier, resource).get('quizzes') or {}).get(record_id)
    return {'answers': dict(row['answers']) if row else {}, 'updated_at': row.get('updated_at') if row else None}


def save_quiz(identifier, resource, record_id, answers):
    """Whole-map replace: the quiz is small and last write wins, so a stale tab cannot merge silently."""
    if not RECORD_ID.fullmatch(record_id or ''): raise ValueError('分析记录标识无效。')
    if not isinstance(answers, dict) or len(answers) > MAX_ANSWERS: raise ValueError('答案数量无效。')
    clean = {}
    for index, choice in answers.items():
        if not re.fullmatch(r'\d{1,3}', str(index)): raise ValueError('题号无效。')
        if type(choice) is not int or not 0 <= choice <= 5: raise ValueError('选项无效。')
        clean[str(index)] = choice
    def change(data, _):
        row = {'answers': clean, 'updated_at': datetime.now(timezone.utc).isoformat(timespec='seconds')}
        data.setdefault('quizzes', {})[record_id] = row
        return row
    return mutate(identifier, resource, change)

def clock(seconds):
    total = max(0, int(seconds or 0))
    hours, rest = divmod(total, 3600)
    return f'{hours}:{rest // 60:02d}:{rest % 60:02d}' if hours else f'{rest // 60:02d}:{rest % 60:02d}'


def _label(project):
    return library.episode_title(project) or project.get('title') or '视频笔记'


def export_filename(identifier):
    return library.safe_name(f'{_label(library.get(identifier))}_笔记')


def export_markdown(identifier, resource):
    """Timestamped personal notes as a portable Markdown document."""
    project = library.get(identifier)
    rows = notes(identifier, resource)
    title = _label(project)
    lines = [f'# {title}', '', f'- 视频资源：{resource}', f'- 笔记数量：{len(rows)}']
    if project.get('source_url'):
        lines.append(f"- 原始链接：{project['source_url']}")
    lines.append('')
    for note in rows:
        lines += [f"## [{clock(note['time'])}]", '', note['text'], '']
    return '\n'.join(lines).rstrip() + '\n', library.safe_name(f'{title}_笔记')


def note_cards(identifier, resource):
    """Anki Basic cards: the note is the prompt; the video and time stamp are the source."""
    project = library.get(identifier)
    title = _label(project)
    url = project.get('source_url') or ''
    link = f"<div><a href='{anki_export.html(url)}'>{anki_export.html(url)}</a></div>" if re.match(r'^https?://', url) else ''
    rows = []
    for note in notes(identifier, resource):
        back = [f"<div>时间点：{clock(note['time'])}</div>", f'<div>来源：{anki_export.html(title)}</div>']
        if link: back.append(link)
        rows.append({'front': f"<div>{anki_export.html(note['text'])}</div>", 'back': ''.join(back), 'tags': 'video-parser::notes'})
    return rows


def notes_csv(identifier, resource):
    """Spreadsheet export: time, note, created time."""
    rows = [[clock(note['time']), note['text'], note.get('created_at') or ''] for note in notes(identifier, resource)]
    return anki_export.table_csv(['时间点', '笔记', '创建时间'], rows, bom=True)
