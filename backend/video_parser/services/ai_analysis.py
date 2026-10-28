"""视频 AI 分析：字幕和/或画面 → 模型；分析记录、追问和导出。

Material sent to the model: title, duration, the chosen subtitle file (with time stamps,
merged when long) and/or frames picked by scene change or evenly. Results, frames and
follow-ups are archived in the video's project, so history survives restarts.
"""
from datetime import datetime, timezone
import base64
import json
import math
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
import time
from uuid import uuid4

from video_parser.core.paths import ROOT, CACHE_DIR
from video_parser.services import ai_settings, anki_export, library, quiz

PREVIEW_ROOT = CACHE_DIR / 'analysis-preview'
PREVIEW_TTL = 6 * 3600
MAX_TRANSCRIPT_CHARS = 60_000
MAX_FRAMES = 16
FRAME_MAX_SIDE = 1024
OUTPUT_TOKENS_ESTIMATE = 1500
RECORD_ID = re.compile(r'^\d{8}-\d{6}-[0-9a-f]{8}(?:__[0-9a-f]{12})?$')
PREVIEW_NAME = re.compile(r'^frame_\d{3}\.jpg$')
LANGUAGES = {'zh': '中文', 'en': '英文', 'ja': '日文', 'ko': '韩文'}
SOURCES = {'both': '字幕 + 画面', 'transcript': '只用字幕', 'frames': '只用画面'}


class AnalysisError(Exception):
    """A failure whose message is safe and useful to show the user as is."""

    def __init__(self, message, code='invalid_input'):
        super().__init__(message)
        self.user_message = message
        self.code = code

SYSTEM_PROMPT = (
    '你是一个视频内容分析助手。用户会提供视频的标题、时长，以及带时间点的字幕文字和/或按时间抽取的画面。'
    '请只根据提供的材料回答，不要编造材料里没有的内容；材料不足以回答时直接说明。'
    '使用简体中文和 Markdown（标题、列表、表格）输出。'
    '提到具体时间点时写成 [mm:ss]，超过一小时写成 [h:mm:ss]，时间必须来自字幕或画面标注的时间。'
)

TEMPLATES = {
    'summary': {
        'name': '内容摘要', 'description': '一句话概括、核心要点和结论', 'source': 'both',
        'prompt': '请为这个视频写一份内容摘要：\n1. 一句话概括视频讲了什么；\n2. 3～6 个核心要点，每个要点后标出对应的时间点；'
                  '\n3. 视频的结论或作者的观点；\n4. 适合什么人看、看完能得到什么。',
    },
    'chapters': {
        'name': '章节时间轴', 'description': '按时间分段，每段一个标题和说明', 'source': 'both',
        'prompt': '请按时间顺序把视频分成若干章节，生成章节时间轴。每个章节一行，格式为：\n'
                  '[mm:ss] 章节标题 —— 一两句话说明这一段讲了什么。\n'
                  '章节数量根据内容决定，通常 5～15 个；起始时间要准确对应字幕或画面的时间。',
    },
    'notes': {
        'name': '学习笔记', 'description': '知识点、公式、易错点和自测题，适合课程', 'source': 'both',
        'prompt': '请把这个视频整理成一份学习笔记，使用以下结构：\n## 主题概览\n'
                  '## 知识点（按讲解顺序，写清定义、推导或例子，并标出时间点）\n## 关键公式或结论（如有）\n'
                  '## 容易混淆或出错的地方\n## 复习自测题（3～5 道，附简短答案）',
    },
    'quiz': {
        'name': '复习自测题', 'description': '结构化选择题，可逐题作答并导出 Anki', 'source': 'transcript', 'requires': 'transcript',
        'prompt': '请根据视频内容出 6～10 道复习自测题，覆盖主要知识点，由易到难。\n'
                  '只输出一个 ```json 代码块，不要输出任何其他文字。JSON 结构：\n'
                  '{"title":"这套题的标题","questions":[{"question":"题干","options":["选项内容","选项内容","选项内容","选项内容"],'
                  '"answer":"A","explanation":"为什么选它，以及常见错误","time":192}]}\n'
                  '要求：每题 4 个选项且只有一个正确答案；answer 只写选项字母 A/B/C/D；'
                  '判断题也写成两个选项（A. 正确、B. 错误）；time 是该知识点在视频中的秒数，无法确定时写 null；'
                  '题干、选项和解析都用简体中文；不要编造材料里没有的内容；不要使用 Markdown 或注释。',
    },
    'exam': {
        'name': '模拟测验', 'description': '先作答、后统一判分，含总分与错题回顾', 'source': 'transcript', 'requires': 'transcript',
        'prompt': '请根据视频内容出一套模拟测验题，用来检验学习成果。\n'
                  '只输出一个 ```json 代码块，不要输出任何其他文字。JSON 结构：\n'
                  '{"title":"这套测验的标题","questions":[{"question":"题干","options":["选项内容","选项内容","选项内容","选项内容"],'
                  '"answer":"A","explanation":"为什么选它，以及常见错误","time":192}]}\n'
                  '要求：每题 4 个选项且只有一个正确答案；answer 只写选项字母 A/B/C/D；'
                  '判断题也写成两个选项（A. 正确、B. 错误）；time 是该知识点在视频中的秒数，无法确定时写 null；'
                  '题干、选项和解析都用简体中文；不要编造材料里没有的内容；不要使用 Markdown 或注释；'
                  '题干和选项都不能暗示正确答案，不要出现“以上都对”“以上都不对”这类选项；'
                  '各题之间不要重复考查同一个知识点。未指定题量和难度时，出 8～12 道中等难度的题。',
    },
    'breakdown': {
        'name': '短视频拆解', 'description': '开头钩子、结构节奏、镜头和文案', 'source': 'both',
        'prompt': '请从内容创作的角度拆解这个视频：\n1. 开头钩子：前 3 秒用了什么画面或台词吸引人；\n'
                  '2. 结构与节奏：分几段、每段做什么、转折点在哪里（标时间点）；\n'
                  '3. 画面与镜头：主要景别、运镜、字幕或贴纸等视觉手段；\n4. 文案与话术：标题、口播里的关键句和说服手法；\n'
                  '5. 互动引导：如何引导点赞、评论、关注或购买；\n6. 可以借鉴的 3 个做法，以及可以改进的地方。',
    },
    'storyboard': {
        'name': '分镜表', 'description': '每张画面一行：时间、内容、景别和台词', 'source': 'both', 'requires': 'frames',
        'prompt': '请根据提供的画面（以及字幕）整理一份分镜表，用 Markdown 表格输出，列为：'
                  '| 时间 | 画面内容 | 景别/运镜 | 台词或字幕 |。每张画面至少对应一行，时间使用画面标注的时间。'
                  '表格后用两三句话总结整体视觉风格。',
    },
    'script': {
        'name': '提取口播文案', 'description': '把字幕整理成通顺、分段的文案', 'source': 'transcript', 'requires': 'transcript',
        'prompt': '请根据字幕整理出完整的口播文案：去掉口头禅、重复和语气词，修正明显的识别错误，按意思分段并加上小标题；'
                  '保留原意，不要添加字幕里没有的内容。每段开头标出起始时间点。',
    },
    'custom': {
        'name': '自定义', 'description': '只按你写的要求分析', 'source': 'both', 'prompt': '',
    },
}


def public_templates():
    return [{'id': key, 'name': item['name'], 'description': item['description'], 'source': item['source'],
             'requires': item.get('requires')} for key, item in TEMPLATES.items()]


def ts(seconds):
    total = max(0, int(seconds or 0))
    hours, rest = divmod(total, 3600)
    return f'{hours}:{rest // 60:02d}:{rest % 60:02d}' if hours else f'{rest // 60:02d}:{rest % 60:02d}'


# ---- Subtitles ------------------------------------------------------------

CUE_TIME = re.compile(r'(?:(\d+):)?(\d{1,2}):(\d{2})[,.](\d{1,3})\s*-->')
CJK = r'\u3000-\u303f\u3400-\u9fff\uff00-\uffef'


def parse_cues(text, preserve_duplicates=False):
    """SRT or WebVTT → [(start_seconds, text)]."""
    cues, current = [], None
    for raw in str(text).splitlines():
        line = raw.strip().lstrip('\ufeff')
        match = CUE_TIME.search(line)
        if match:
            hours, minutes, seconds, millis = match.groups()
            current = [int(hours or 0) * 3600 + int(minutes) * 60 + int(seconds) + int(millis.ljust(3, '0')) / 1000, []]
            cues.append(current)
        elif not line:
            current = None
        elif current is not None:
            current[1].append(re.sub(r'<[^>]+>', '', line).strip())
    result = []
    for start, lines in cues:
        body = join_text(item for item in lines if item)
        if body and (preserve_duplicates or not result or result[-1][1] != body):
            result.append((start, body))
    return result


def join_text(parts):
    text = ' '.join(parts)
    return re.sub(rf'(?<=[{CJK}])\s+(?=[{CJK}])', '', text).strip()


def compact(cues, limit=MAX_TRANSCRIPT_CHARS):
    """Time-stamped lines; merges cues into longer windows until the text fits."""
    lines = []
    for window in (0, 15, 30, 60, 120, 300):
        lines, bucket = [], None
        for start, body in cues:
            key = start if window == 0 else int(start // window)
            if bucket is None or key != bucket[0]:
                if bucket:
                    lines.append(f'[{ts(bucket[1])}] ' + join_text(bucket[2]))
                bucket = [key, start, [body]]
            else:
                bucket[2].append(body)
        if bucket:
            lines.append(f'[{ts(bucket[1])}] ' + join_text(bucket[2]))
        text = '\n'.join(lines)
        if len(text) <= limit:
            return text, window > 0
    step = math.ceil(len('\n'.join(lines)) / limit)
    return '\n'.join(lines[::step])[:limit], True


def estimate_text_tokens(text):
    cjk = len(re.findall(rf'[{CJK}]', text))
    return int(cjk * 0.7 + (len(text) - cjk) / 3.5) + 1


def transcript_label(item):
    if item['kind'] == 'subtitle':
        return '平台字幕'
    language = item.get('language') or ''
    name = {'translation': 'AI 翻译', 'correction': '人工校对'}.get(item['kind'], '本地转写')
    return name + (f'（{LANGUAGES.get(language, language)}）' if language else '')


def transcript_sources(project, include_translations=False):
    rows = []
    for name, item in project['resources'].items():
        if item['kind'] not in ((*library.TRANSCRIPT_KINDS, 'translation') if include_translations else library.TRANSCRIPT_KINDS) or Path(name).suffix.lower() not in ('.srt', '.vtt'):
            continue
        try:
            cues = parse_cues(library.resource_from_project(project, name).read_text(encoding='utf-8', errors='replace'))
        except (OSError, ValueError, KeyError, AnalysisError):
            continue
        if not cues:
            continue
        body, merged = compact(cues)
        rows.append({'resource': name, 'label': transcript_label(item), 'kind': item['kind'], 'language': item.get('language') or '',
                     'cues': len(cues), 'chars': len(body), 'merged': merged, 'tokens': estimate_text_tokens(body), 'end': ts(cues[-1][0])})
    rows.sort(key=lambda row: (row['kind'] != 'subtitle', row['resource']))
    return rows


def load_transcript(project, resource):
    item = project['resources'].get(resource)
    if not item or item['kind'] not in library.TRANSCRIPT_KINDS:
        raise AnalysisError('所选字幕不属于这个视频，请重新选择。')
    cues = parse_cues(library.resource_from_project(project, resource).read_text(encoding='utf-8', errors='replace'))
    if not cues:
        raise AnalysisError('所选字幕是空的，请换一个字幕或重新生成。')
    body, merged = compact(cues)
    return {'resource': resource, 'label': transcript_label(item), 'body': body, 'merged': merged}


# ---- Frames ---------------------------------------------------------------

SCALE = f"scale=w='min({FRAME_MAX_SIDE},iw)':h='min({FRAME_MAX_SIDE},ih)':force_original_aspect_ratio=decrease"


def video_info(project, resource):
    item = project['resources'].get(resource)
    if not item or item['kind'] != 'video':
        raise AnalysisError('所选视频不属于这个项目，请重新选择。')
    media = item.get('media')
    if not media:
        from video_parser.services import videos
        media = videos.probe(video_path(project, resource))
    track = next((stream for stream in media.get('streams', []) if stream.get('codec_type') == 'video'), {})
    return {'duration': float(media.get('format', {}).get('duration') or 0), 'width': track.get('width'), 'height': track.get('height')}


def video_path(project, resource):
    """Read-only path of an archived video (skips re-hashing a large file on every request)."""
    item = project['resources'].get(resource)
    if not item or item['kind'] != 'video':
        raise AnalysisError('所选视频不属于这个项目，请重新选择。')
    path = library.resource_path(project, resource)
    if path.is_symlink() or not path.is_file() or path.stat().st_size != item['size_bytes']:
        raise AnalysisError('归档的视频文件缺失或已变化，请重新下载。')
    return path


def auto_count(duration):
    return max(4, min(MAX_FRAMES, round(4 + duration / 60)))


def image_tokens(width, height):
    if not width or not height:
        return 800
    scale = min(1, FRAME_MAX_SIDE / max(width, height))
    return math.ceil(width * scale / 28) * math.ceil(height * scale / 28) + 2


def scene_changes(video, timeout=150):
    """(time, score) of key frames where the picture changes a lot."""
    command = ['ffmpeg', '-hide_banner', '-nostdin', '-skip_frame', 'nokey', '-i', str(video), '-an', '-sn',
               '-vf', "scale=320:-2,select='gt(scene,0.3)',metadata=print", '-f', 'null', '-']
    result = subprocess.run(command, capture_output=True, text=True, encoding='utf-8', errors='replace', timeout=timeout)
    found, moment = [], None
    for line in result.stderr.splitlines():
        match = re.search(r'pts_time:([0-9.]+)', line)
        if match:
            moment = float(match[1])
            continue
        match = re.search(r'lavfi\.scene_score=([0-9.]+)', line)
        if match and moment is not None:
            found.append((moment, float(match[1])))
            moment = None
    return found


def plan_times(video, duration, mode='scene', count=0):
    count = max(1, min(MAX_FRAMES, int(count or 0) or auto_count(duration)))
    duration = max(duration, 0.1)
    uniform = [duration * (index + 1) / (count + 1) for index in range(count)]
    if mode != 'scene':
        return uniform
    try:
        found = scene_changes(video)
    except (OSError, subprocess.SubprocessError):
        found = []
    gap = duration / (count * 2.5)
    opening = min(1.0, duration * 0.05)
    picked = [opening] if count >= 3 else []  # The first seconds matter for most videos.
    for moment, _ in sorted(found, key=lambda item: -item[1]):
        if len(picked) >= count:
            break
        if 0.3 <= moment <= duration - 0.3 and all(abs(moment - other) >= gap for other in picked):
            picked.append(moment)
    for moment in uniform:  # Fill quiet stretches evenly.
        if len(picked) >= count:
            break
        if all(abs(moment - other) >= gap for other in picked):
            picked.append(moment)
    for moment in uniform:
        if len(picked) >= count:
            break
        if moment not in picked:
            picked.append(moment)
    return sorted(picked)[:count]


def extract_frames(video, times, directory):
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    frames = []
    for moment in times:
        target = directory / f'frame_{len(frames):03d}.jpg'
        try:
            subprocess.run(['ffmpeg', '-hide_banner', '-loglevel', 'error', '-nostdin', '-y', '-ss', f'{moment:.3f}', '-i', str(video),
                            '-frames:v', '1', '-vf', SCALE, '-q:v', '3', str(target)], capture_output=True, timeout=90)
        except (OSError, subprocess.SubprocessError):
            continue
        if target.is_file() and target.stat().st_size:
            frames.append({'name': target.name, 'time': round(moment, 2)})
    return frames


def cleanup_previews():
    if not PREVIEW_ROOT.is_dir():
        return
    folders = sorted((item for item in PREVIEW_ROOT.iterdir() if item.is_dir() and not item.is_symlink()), key=lambda item: item.stat().st_mtime)
    for index, folder in enumerate(folders):
        if time.time() - folder.stat().st_mtime > PREVIEW_TTL or index < len(folders) - 20:
            shutil.rmtree(folder, ignore_errors=True)


def create_preview(project_id, resource, mode, count):
    cleanup_previews()
    project = library.get(project_id)
    info = video_info(project, resource)
    video = video_path(project, resource)
    token = uuid4().hex
    directory = PREVIEW_ROOT / token
    times = plan_times(video, info['duration'], mode, count)
    frames = extract_frames(video, times, directory)
    if not frames:
        shutil.rmtree(directory, ignore_errors=True)
        raise AnalysisError('没能从视频里截取画面，请检查 ffmpeg。', 'operation_failed')
    (directory / 'meta.json').write_text(json.dumps({'project_id': project_id, 'resource': resource, 'frames': frames}), encoding='utf-8')
    return {'token': token, 'frames': [{**frame, 'label': ts(frame['time']), 'url': f'/api/workspace/analysis/previews/{token}/{frame["name"]}'}
                                       for frame in frames]}


def preview_file(token, name):
    if not re.fullmatch(r'[0-9a-f]{32}', token or '') or not PREVIEW_NAME.match(name or ''):
        raise KeyError(name)
    path = PREVIEW_ROOT / token / name
    if path.is_symlink() or not path.is_file() or not path.resolve().is_relative_to(PREVIEW_ROOT.resolve()):
        raise KeyError(name)
    return path


def use_preview(token, selected, project_id, resource, target):
    """Copy the frames the user kept from a preview; [] when the preview no longer matches."""
    if not re.fullmatch(r'[0-9a-f]{32}', token or ''):
        return []
    try:
        meta = json.loads((PREVIEW_ROOT / token / 'meta.json').read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return []
    if meta.get('project_id') != project_id or meta.get('resource') != resource:
        return []
    chosen = [frame for frame in meta['frames'] if selected is None or frame['name'] in selected]
    target = Path(target)
    target.mkdir(parents=True, exist_ok=True)
    frames = []
    for frame in chosen:
        try:
            source = preview_file(token, frame['name'])
        except KeyError:
            continue
        name = f'frame_{len(frames):03d}.jpg'
        shutil.copyfile(source, target / name)
        frames.append({'name': name, 'time': frame['time']})
    return frames


# ---- Model call ---------------------------------------------------------------

def task_text(template, extra):
    base = TEMPLATES.get(template, TEMPLATES['summary'])['prompt']
    extra = (extra or '').strip()
    if base and extra:
        return base + '\n\n补充要求：' + extra
    return base or extra or TEMPLATES['summary']['prompt']


def build_messages(meta, template, extra, transcript, frames):
    """frames: [{'path': Path, 'time': seconds}]."""
    header = f"视频标题：{meta.get('title') or '未命名'}\n平台：{meta.get('platform') or '未知'}\n时长：{ts(meta.get('duration'))}"
    parts = [{'type': 'text', 'text': f'{header}\n\n分析任务：\n{task_text(template, extra)}'}]
    if transcript:
        note = '，内容较长，已按时间段合并' if transcript.get('merged') else ''
        parts.append({'type': 'text', 'text': f"以下是带时间点的字幕（{transcript['label']}{note}）：\n{transcript['body']}"})
    if frames:
        parts.append({'type': 'text', 'text': f'以下是按时间抽取的 {len(frames)} 张画面，每张前面标注了它在视频中的时间。'})
        for index, frame in enumerate(frames, 1):
            data = base64.b64encode(Path(frame['path']).read_bytes()).decode('ascii')
            parts.append({'type': 'text', 'text': f"画面 {index}（{ts(frame['time'])}）"})
            parts.append({'type': 'image_url', 'image_url': {'url': f'data:image/jpeg;base64,{data}'}})
    content = parts if frames else '\n\n'.join(part['text'] for part in parts)
    return [{'role': 'system', 'content': SYSTEM_PROMPT}, {'role': 'user', 'content': content}]


def call_model(messages, settings=None, max_tokens=None):
    settings = settings or ai_settings.current()
    if not ai_settings.configured(settings):
        raise AnalysisError('尚未配置 AI 模型，请在“设置 → AI 模型”里填写。', 'ai_not_configured')
    extra = {'max_tokens': max_tokens} if max_tokens else {}
    try:
        response = ai_settings.client(settings).chat.completions.create(model=settings['model'], messages=messages, temperature=0.3, **extra)
    except Exception as error:
        raise AnalysisError(ai_settings.explain(error), 'network_error') from None
    # With an explicit budget, a length stop means the reply is incomplete; never publish it as a result.
    if max_tokens and response.choices and getattr(response.choices[0], 'finish_reason', None) == 'length':
        raise AnalysisError('模型输出达到单次上限被截断，没有发布不完整结果；请缩小批次或提高输出上限。', 'operation_failed')
    text = (response.choices[0].message.content or '').strip() if response.choices else ''
    if not text:
        raise AnalysisError('模型没有返回内容，请换个模型或稍后重试。', 'operation_failed')
    usage = getattr(response, 'usage', None)
    tokens = {'prompt_tokens': getattr(usage, 'prompt_tokens', None), 'completion_tokens': getattr(usage, 'completion_tokens', None)} if usage else {}
    return text, tokens


def _progress(phase, message):
    from video_parser.services import task_runtime
    task_runtime.progress(phase, message)


def now():
    return datetime.now(timezone.utc).isoformat(timespec='seconds')


def validate_material(project_id, source, transcript_resource=None, frame_mode='scene'):
    if source in ('both', 'transcript') or frame_mode == 'ai':
        project = library.get(project_id)
        chosen = transcript_resource or next((row['resource'] for row in transcript_sources(project)), None)
        if not chosen:
            raise AnalysisError('当前视频暂无可用字幕，请先获取平台字幕或生成转写字幕。')
        load_transcript(project, chosen)


def run(project_id, payload, directory):
    """One analysis: gather material, call the model, archive frames and the report."""
    from video_parser.services import model_calls
    selected = model_calls.target(payload.get('agent'))
    settings = ai_settings.current() if selected['engine'] == 'api' else {'model': selected['model'], 'provider': 'custom'}
    project = library.get(project_id)
    resource = payload['resource']
    info = video_info(project, resource)
    template = payload.get('template') if payload.get('template') in TEMPLATES else 'summary'
    spec = TEMPLATES[template]
    source = payload.get('source') if payload.get('source') in SOURCES else spec['source']
    extra = (payload.get('prompt') or '').strip()
    if template == 'custom' and not extra:
        raise AnalysisError('自定义分析需要写下分析要求。')
    transcript = None
    if source in ('both', 'transcript'):
        chosen = payload.get('transcript_resource') or next((row['resource'] for row in transcript_sources(project)), None)
        if chosen:
            transcript = load_transcript(project, chosen)
        else:
            raise AnalysisError('当前视频暂无可用字幕，请先获取平台字幕或生成转写字幕。不会自动切换为只用画面。')
    if spec.get('requires') == 'transcript' and not transcript:
        raise AnalysisError('提取口播文案需要字幕，请先生成字幕。')
    planning_usage = {}
    frame_mode = payload.get('frame_mode') or 'scene'
    if frame_mode == 'ai' and transcript is None:
        validate_material(project_id, source, payload.get('transcript_resource'), frame_mode)
        chosen = payload.get('transcript_resource') or transcript_sources(project)[0]['resource']
        planning_transcript = load_transcript(project, chosen)
    else:
        planning_transcript = transcript
    frames = []
    frames_dir = Path(directory) / 'frames'
    shutil.rmtree(frames_dir, ignore_errors=True)
    if source in ('both', 'frames'):
        video = video_path(project, resource)
        if payload.get('preview_token') and frame_mode != 'ai':
            frames = use_preview(payload['preview_token'], payload.get('frames'), project_id, resource, frames_dir)
        if not frames:
            _progress('extracting', '正在挑选并截取画面…')
            if frame_mode == 'ai':
                from video_parser.services.frame_planning import plan
                _progress('analyzing', '正在根据字幕规划画面时间点与数量（额外一次模型调用）…')
                try:
                    times, planning_usage = plan(planning_transcript, info['duration'], settings, selected, directory)
                except ValueError as error:
                    raise AnalysisError(str(error)) from None
            else:
                times = plan_times(video, info['duration'], frame_mode, payload.get('count') or 0)
            frames = extract_frames(video, times, frames_dir)
        if not frames:
            raise AnalysisError('没能从视频里截取画面，请检查 ffmpeg。', 'operation_failed')
    elif spec.get('requires') == 'frames':
        raise AnalysisError('分镜表需要画面，请把分析依据改为包含画面。')
    meta = {'title': project['title'], 'platform': project['platform'], 'duration': info['duration']}
    messages = build_messages(meta, template, extra, transcript, [{'path': frames_dir / frame['name'], 'time': frame['time']} for frame in frames])
    _progress('analyzing', f"正在等待模型（{settings['model']}）返回结果；不会自动重试…")
    reply = model_calls.call(messages, settings, selected, directory, purpose='material', question=task_text(template, extra))
    text, usage = reply.text, reply.usage
    _progress('archiving', '正在归档分析结果与画面…')
    paths = library.import_frames(project_id, frames_dir) if frames else []
    details = {'template': template, 'source': source, 'prompt': extra, 'video_resource': resource,
               'quality_id': payload.get('quality_id'), 'transcript_resource': transcript and transcript['resource'],
               'transcript_label': transcript and transcript['label'], 'transcript_merged': bool(transcript and transcript['merged']),
               'frames': [{'path': path, 'time': frame['time']} for path, frame in zip(paths, frames)],
               'frame_count': len(frames), 'frame_mode': frame_mode, 'planning_usage': planning_usage, 'model': reply.model or settings['model'],
               'provider': model_calls.provider(selected, settings), 'agent': selected,
               'usage': usage, 'created_at': now()}
    report = library.save_text(project_id, 'analysis', text, details)
    return record(project_id, Path(report).stem)


def _report(project, record_id):
    if not RECORD_ID.match(record_id or ''):
        raise KeyError(record_id)
    name = f'analysis/{record_id}.md'
    item = project['resources'].get(name)
    if not item or item['kind'] != 'analysis':
        raise KeyError(record_id)
    return name, item


def followups(project, record_id):
    rows = [(name, item) for name, item in project['resources'].items()
            if item['kind'] == 'analysis-followup' and item.get('record') == record_id]
    result = []
    for name, item in sorted(rows):
        try:
            answer = library.resource_from_project(project, name).read_text(encoding='utf-8')
        except (OSError, ValueError, KeyError, AnalysisError):
            continue
        result.append({'id': Path(name).stem, 'question': item.get('question', ''), 'answer': answer, 'created_at': item.get('created_at')})
    return result


def since_reset(item, history):
    """Follow-ups after the user's last "清空上下文"; earlier ones stay archived but are not resent."""
    after = item.get('context_after')
    if not after:
        return history
    ids = [entry['id'] for entry in history]
    return history[ids.index(after) + 1:] if after in ids else [entry for entry in history if entry['id'] > after]


def clear_context(project_id, record_id):
    project = library.get(project_id)
    name, item = _report(project, record_id)
    history = followups(project, record_id)
    if history and item.get('context_after') != history[-1]['id']:
        library.update_details(project_id, name, {'context_after': history[-1]['id']})


def ask(project_id, record_id, question, directory, agent=None):
    question = (question or '').strip()
    if not question:
        raise AnalysisError('请输入要追问的问题。')
    from video_parser.services import model_calls
    selected = model_calls.target(agent)
    settings = ai_settings.current() if selected['engine'] == 'api' else {'model': selected['model'], 'provider': 'custom'}
    project = library.get(project_id)
    name, item = _report(project, record_id)
    original = library.resource_from_project(project, name).read_text(encoding='utf-8')
    transcript = None
    if item.get('transcript_resource'):
        try:
            transcript = load_transcript(project, item['transcript_resource'])
        except (OSError, ValueError, KeyError, AnalysisError):
            transcript = None
    frames = []
    for frame in item.get('frames') or []:
        try:
            frames.append({'path': library.resource_from_project(project, frame['path']), 'time': frame['time']})
        except (OSError, ValueError, KeyError, AnalysisError):
            continue
    duration = 0
    try:
        duration = video_info(project, item.get('video_resource'))['duration']
    except (OSError, ValueError, KeyError, AnalysisError, TypeError):
        pass
    meta = {'title': project['title'], 'platform': project['platform'], 'duration': duration}
    messages = build_messages(meta, item.get('template') or 'custom', item.get('prompt') or '描述视频内容', transcript, frames)
    messages.append({'role': 'assistant', 'content': original})
    for previous in since_reset(item, followups(project, record_id)):
        messages += [{'role': 'user', 'content': previous['question']}, {'role': 'assistant', 'content': previous['answer']}]
    messages.append({'role': 'user', 'content': question})
    _progress('analyzing', f"正在等待模型（{settings['model']}）回答追问…")
    reply = model_calls.call(messages, settings, selected, directory, purpose='followup', question=question)
    answer, usage = reply.text, reply.usage
    _progress('archiving', '正在保存追问结果…')
    with tempfile.TemporaryDirectory(prefix='.followup-', dir=directory) as temporary:
        source = Path(temporary) / 'answer.md'
        source.write_text(answer, encoding='utf-8')
        batch = f"{len(followups(project, record_id)) + 1:03d}-" + datetime.now().strftime('%Y%m%d-%H%M%S') + '-' + uuid4().hex[:8]
        library.import_file(project_id, source, f'analysis/{record_id}/followup-{batch}.md', 'analysis-followup',
                            {'record': record_id, 'question': question[:4000], 'model': reply.model or settings['model'], 'agent': selected, 'usage': usage, 'created_at': now()})
    return record(project_id, record_id)


# ---- Records ------------------------------------------------------------------

def _record(project, name, item):
    record_id = Path(name).stem
    try:
        text = library.resource_from_project(project, name).read_text(encoding='utf-8')
    except (OSError, ValueError, KeyError, AnalysisError):
        text = ''
    template = item.get('template')
    legacy = template not in TEMPLATES
    base = f"/api/workspace/projects/{project['id']}/files/"
    frames = [{'url': base + frame['path'], 'time': frame['time'], 'label': ts(frame['time'])}
              for frame in item.get('frames') or [] if frame.get('path') in project['resources']]
    source = item.get('source') or 'frames'
    agent = item.get('agent') if isinstance(item.get('agent'), dict) else {}
    engine = agent.get('engine') if agent.get('engine') in ('api', 'claude', 'codex', 'opencode') else 'api'
    return {'id': record_id, 'created_at': item.get('created_at') or _batch_time(record_id), 'template': template if not legacy else 'legacy',
            'template_name': TEMPLATES[template]['name'] if not legacy else '画面分析（旧版）', 'source': source,
            'source_label': SOURCES.get(source, '只用画面'), 'prompt': item.get('prompt') or '', 'model': item.get('model') or '',
            'provider': item.get('provider') or '', 'engine': engine, 'transcript_label': item.get('transcript_label') or '',
            'transcript_merged': bool(item.get('transcript_merged')), 'video_resource': item.get('video_resource'),
            'frame_count': item.get('frame_count') or len(frames), 'frames': frames, 'usage': item.get('usage') or {},
            'quiz': quiz.parse(text) if template in ('quiz', 'exam') and text else None,
            'text': text, 'followups': followups(project, record_id), 'context_after': item.get('context_after'), 'legacy': legacy}


def _batch_time(record_id):
    try:
        return datetime.strptime(record_id[:15], '%Y%m%d-%H%M%S').astimezone(timezone.utc).isoformat(timespec='seconds')
    except ValueError:
        return None


def records(project_id):
    project = library.get(project_id)
    rows = [(name, item) for name, item in project['resources'].items() if item['kind'] == 'analysis']
    return [_record(project, name, item) for name, item in sorted(rows, key=lambda row: Path(row[0]).stem, reverse=True)]


def record(project_id, record_id):
    project = library.get(project_id)
    name, item = _report(project, record_id)
    return _record(project, name, item)


def delete(project_id, record_id):
    # Detach legacy Q&A before deleting its source material. Copies survive deletion.
    from video_parser.services import conversations
    conversations.migrate(project_id)
    project = library.get(project_id)
    name, item = _report(project, record_id)
    names = [name, *[frame['path'] for frame in item.get('frames') or []],
             *[path for path, info in project['resources'].items() if info['kind'] == 'analysis-followup' and info.get('record') == record_id]]
    library.remove(project_id, names)


def context(project_id, resource):
    project = library.get(project_id)
    # Before archiving there is no video yet: subtitles can still be listed.
    info = video_info(project, resource) if resource else {'duration': 0, 'width': None, 'height': None}
    return {'transcripts': transcript_sources(project), 'duration': info['duration'], 'auto_frame_count': auto_count(info['duration']),
            'image_tokens': image_tokens(info['width'], info['height']), 'prompt_tokens': 700,
            'output_tokens': OUTPUT_TOKENS_ESTIMATE, 'templates': public_templates(), 'sources': SOURCES}


# ---- Export -------------------------------------------------------------------

def export_markdown(project_id, record_id):
    project = library.get(project_id)
    data = record(project_id, record_id)
    created = (data['created_at'] or '')[:16].replace('T', ' ')
    lines = [f"# {project['title']}", '', f"- 分析模板：{data['template_name']}", f"- 分析依据：{data['source_label']}"
             + (f"（{data['transcript_label']}）" if data['transcript_label'] else ''),
             f"- 模型：{data['model'] or '未记录'}", f"- 时间：{created or '未记录'}"]
    if project.get('source_url'):
        lines.append(f"- 原始链接：{project['source_url']}")
    if data['prompt']:
        lines.append(f"- 补充要求：{data['prompt']}")
    body = quiz.markdown(data['quiz']) if data.get('quiz') else data['text'].strip()
    lines += ['', body]
    if data['followups']:
        lines += ['', '## 追问']
        for item in data['followups']:
            lines += ['', f"### 问：{item['question']}", '', item['answer'].strip()]
    filename = library.safe_name(f"{project['title']}_{data['template_name']}")
    return '\n'.join(lines) + '\n', filename


def export_anki(project_id, record_id):
    """Structured quiz → Anki Basic CSV. Refuses records without a usable quiz."""
    project = library.get(project_id)
    data = record(project_id, record_id)
    if not data.get('quiz'):
        raise ValueError('这条记录没有可导出的结构化自测题，请先用“复习自测题”模板重新生成。')
    cards = quiz.cards(data['quiz'], project['title'], project.get('source_url') or '')
    return anki_export.cards_csv(cards), library.safe_name(f"{project['title']}_{data['template_name']}_Anki")


def export_quiz_csv(project_id, record_id):
    """Structured quiz → spreadsheet CSV (question, options, answer, explanation, time)."""
    project = library.get(project_id)
    data = record(project_id, record_id)
    if not data.get('quiz'):
        raise ValueError('这条记录没有可导出的结构化自测题，请先用“复习自测题”模板重新生成。')
    rows = quiz.csv_rows(data['quiz'])
    return (anki_export.table_csv(['题号', '题干', '选项', '答案', '解析', '时间点'], rows, bom=True),
            library.safe_name(f"{project['title']}_{data['template_name']}"))
