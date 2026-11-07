"""Regenerate the README screenshots in ``docs/assets/readme/`` from the built React app.

The app is served from ``frontend/dist`` and every workspace API call is mocked with
synthetic demo data, so this never reads the local library, credentials, tasks or logs,
never contacts a platform and never calls a model. All screenshots use the dark theme so
the README image set stays visually consistent. Run it after ``npm --prefix frontend
run build``::

    .\\.venv\\Scripts\\python.exe tests/browser/browser_readme_screenshots.py

Review the produced images before committing them: they are documentation, not proof
that a download, transcription, translation or analysis actually succeeded.
"""
import base64
import io
import os
import threading
import wave
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit

from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[2]
DIST = Path(os.environ.get('VIDEO_PARSER_TEST_DIST', ROOT / 'frontend' / 'dist'))
OUT = ROOT / 'docs' / 'assets' / 'readme'
if (ROOT / 'runtimes' / 'browsers').is_dir():
    os.environ.setdefault('PLAYWRIGHT_BROWSERS_PATH', str(ROOT / 'runtimes' / 'browsers'))

PID = f'{1:032x}'
VIDEO_ID = 'BV1LAdemo01'
COURSE = '线性代数入门'
DURATION = 765
EPISODES = [
    (1, '1.1 矩阵与方程组', 765),
    (2, '1.2 向量与线性组合', 612),
    (3, '1.3 矩阵乘法与复合变换', 689),
    (4, '1.4 逆矩阵与可逆性', 544),
    (5, '1.5 行列式与面积', 498),
    (6, '1.6 特征值与特征向量', 731),
]
part_id = lambda part: f'{part:032x}'

COVER = ('data:image/svg+xml;base64,' + base64.b64encode(('''<svg xmlns="http://www.w3.org/2000/svg" width="1280" height="720" viewBox="0 0 1280 720">
<rect width="1280" height="720" fill="#0d0d0c"/>
<g stroke="#3a3a35" stroke-width="1"><path d="M0 96h1280M0 624h1280"/><path d="M96 0v720M1184 0v720"/></g>
<g fill="#efece2" font-family="'Microsoft YaHei','Noto Sans CJK SC',sans-serif">
<text x="128" y="176" fill="#8d8b80" font-size="24" letter-spacing="8">LINEAR ALGEBRA / 01</text>
<text x="128" y="288" font-size="76" font-weight="600">矩阵与方程组</text>
<text x="128" y="352" fill="#a09d90" font-size="30">从一个方程，到一种观察世界的方式。</text>
<text x="128" y="656" fill="#6f6d64" font-size="20" letter-spacing="4">VIDEO PARSER · 演示素材</text>
</g>
<g stroke="#d9d5c7" stroke-width="2" fill="none"><path d="M836 214h264v286H836z"/><path d="M868 214v286M1100 214v286M836 214h264"/></g>
<g fill="#efece2" font-family="Georgia,'Times New Roman',serif" font-size="64">
<text x="898" y="320">1</text><text x="1034" y="320">2</text><text x="898" y="462">3</text><text x="1034" y="462">4</text>
</g>
<path d="M966 268l112 172" stroke="#8d8b80" stroke-width="2"/>
</svg>''').encode('utf-8')).decode('ascii'))

MEDIA = {'resource': 'video/1080p.mp4', 'quality_id': '1080p', 'preview_url': '/demo-media.wav',
         'download_url': '/demo-media.wav', 'filename': '1.1 矩阵与方程组.mp4', 'size_bytes': 182 * 1024 * 1024,
         'duration': DURATION, 'width': 1920, 'height': 1080}

PROJECT = {'id': PID, 'video_id': VIDEO_ID, 'episode_title': '1.1 矩阵与方程组', 'display_title': COURSE,
           'is_single': False, 'title': COURSE, 'platform': '哔哩哔哩', 'part': 1,
           'location': f'bilibili/{COURSE}__{VIDEO_ID}/1.1 矩阵与方程组', 'identity_status': 'verified',
           'learned': False, 'archive_url': f'/api/workspace/projects/{PID}/archive',
           'resources': [
               {'path': 'video/1080p.mp4', 'kind': 'video', 'size_bytes': MEDIA['size_bytes'],
                'url': MEDIA['preview_url'], 'filename': MEDIA['filename']},
               {'path': 'subtitles/1.1 矩阵与方程组-中文-本地转写.srt', 'kind': 'subtitles', 'size_bytes': 48210,
                'url': '/demo-subtitle.srt', 'filename': '1.1 矩阵与方程组-中文-本地转写.srt'},
           ]}

EPISODE_ROWS = [{'part': part, 'title': title, 'duration': duration, 'project_id': part_id(part),
                 'archived': part <= 2, 'learned': part == 1} for part, title, duration in EPISODES]

PARSED = {'id': PID, 'title': COURSE, 'platform': '哔哩哔哩',
          'source_url': f'https://www.bilibili.com/video/{VIDEO_ID}?p=1',
          'cover_url': COVER, 'cover_download_url': COVER, 'warning': None, 'project': PROJECT,
          'media': MEDIA,
          'qualities': [{'id': '1080p', 'label': '1080p', 'width': 1920, 'height': 1080, 'media': MEDIA},
                        {'id': '720p', 'label': '720p', 'width': 1280, 'height': 720, 'media': None}],
          'default_quality_id': '1080p', 'bilibili_cookie_status': 'available', 'duration': DURATION,
          'episodes': EPISODE_ROWS, 'analysis': ''}

SUBTITLE_TEXT = '''00:00 这讲从线性方程组出发，先看矩阵怎样把多个方程写在一起。
00:06 每一行是一个约束条件，每一列对应一个未知数。
00:14 把方程组写成 Ax=b，就把问题变成了矩阵与向量的乘法。
00:24 从列视角看，求解就是在寻找三个列向量的线性组合。
00:35 判断解是否存在，要看 b 是否落在列向量张成的空间里。
00:46 消元法不改变解集，只改变我们观察方程组的角度。
00:58 主元的位置告诉我们哪些变量是自由的。
01:10 行最简形把解的结构写得更直接。
01:22 有自由变量时，解集是一条直线或一个平面。
01:34 这一讲只处理存在性与唯一性，下一讲讨论向量空间。'''

SUBTITLES = {'project_id': PID, 'has_subtitles': True, 'text': SUBTITLE_TEXT, 'translations': [],
             'versions': [
                 {'version_id': 'v-platform', 'generated_at': 1762320000, 'format': 'SRT',
                  'resource': 'subtitles/1.1 矩阵与方程组-B站平台字幕.srt',
                  'filename': '1.1 矩阵与方程组-B站平台字幕.srt', 'kind': 'platform', 'origin': 'platform',
                  'url': '/demo-subtitle.srt', 'language': 'zh', 'sha256': 'a' * 64},
                 {'version_id': 'v-local', 'generated_at': 1762330000, 'format': 'SRT',
                  'resource': 'subtitles/1.1 矩阵与方程组-中文-本地转写.srt',
                  'filename': '1.1 矩阵与方程组-中文-本地转写.srt', 'kind': 'transcription', 'origin': 'local',
                  'url': '/demo-subtitle.srt', 'language': 'zh', 'sha256': 'b' * 64,
                  'source_resolution': '1080p'},
                 {'version_id': 'v-translation', 'generated_at': 1762340000, 'format': 'SRT',
                  'resource': 'subtitles/1.1 矩阵与方程组-英文-AI译文.srt',
                  'filename': '1.1 矩阵与方程组-英文-AI译文.srt', 'kind': 'translation', 'origin': 'translation',
                  'url': '/demo-subtitle.srt', 'language': 'en', 'sha256': 'c' * 64,
                  'source_resource': 'subtitles/1.1 矩阵与方程组-中文-本地转写.srt',
                  'source_origin': 'local', 'source_language': 'zh'},
             ]}
SUBTITLES['files'] = SUBTITLES['versions']
SUBTITLES['selected'] = SUBTITLES['versions'][1]

SUMMARY_TEXT = '''## 从方程组理解矩阵

矩阵把多个线性方程写进同一个结构里。先观察每一行，再从列向量的角度理解未知数的组合。[00:02]

### 本讲重点
- **行视角**：每一行对应一个约束条件。
- **列视角**：求解是在寻找列向量的线性组合。
- **下一步**：用消元法判断解是否存在、是否唯一。

> 演示学习材料，不是真实模型输出。'''

CHAPTERS_TEXT = '''[00:00] 问题与目标 —— 从实际问题引出矩阵和方程组。
[03:10] 矩阵的行与列 —— 行是约束，列是未知数的系数。
[07:45] 消元与解的结构 —— 主元、自由变量和解集。
[11:20] 回扣与练习 —— 用列视角重新看一遍求解过程。'''

SUMMARY_RECORD = {'id': '20261105-101500-a1b2c3d4', 'created_at': '2026-11-05T10:15:00', 'template': 'summary',
                  'template_name': '内容摘要', 'source': 'both', 'source_label': '字幕 + 画面', 'prompt': '',
                  'model': 'demo-tutor', 'provider': '演示 API', 'engine': 'api', 'transcript_label': '本地转写',
                  'transcript_merged': False, 'video_resource': MEDIA['resource'], 'frame_count': 6, 'frames': [],
                  'usage': {'prompt_tokens': 9800, 'completion_tokens': 1200}, 'text': SUMMARY_TEXT, 'followups': [],
                  'legacy': False, 'quiz': None}
CHAPTERS_RECORD = {'id': '20261105-103500-b2c3d4e5', 'created_at': '2026-11-05T10:35:00', 'template': 'chapters',
                   'template_name': '章节时间轴', 'source': 'transcript', 'source_label': '只用字幕', 'prompt': '',
                   'model': 'demo-tutor', 'provider': '演示 API', 'engine': 'api', 'transcript_label': '本地转写',
                   'transcript_merged': False, 'video_resource': MEDIA['resource'], 'frame_count': 0, 'frames': [],
                   'usage': {'prompt_tokens': 4200, 'completion_tokens': 640}, 'text': CHAPTERS_TEXT, 'followups': [],
                   'legacy': False, 'quiz': None}

AI = {'provider': 'openai-compatible', 'provider_name': '演示 API', 'base_url': 'https://api.demo.invalid/v1',
      'model': 'demo-tutor', 'key_saved': True, 'key_hint': '••••••••', 'price_input': None, 'price_output': None,
      'configured': True, 'local': False, 'consented': True, 'source': 'settings', 'storage': '演示配置', 'presets': {}}

CONFIG = {'platforms': ['哔哩哔哩', '抖音', '小红书', 'YouTube'], 'ai_configured': True, 'media_ready': True,
          'subtitles_ready': True, 'transcription_ready': True, 'transcription_upload_limit': 2 * 1024 ** 3,
          'archive_root': 'C:/Users/Public/Documents/VideoParser/Library', 'archive_root_source': 'settings',
          'version': '3.4.1', 'ai': AI}

AGENTS = {'permission': 'restricted-learning', 'engines': [
    {'id': engine, 'name': name, 'installed': True, 'enabled': True, 'discovered': True, 'updated_at': 1762300000,
     'models': [{'id': engine + '-default', 'label': name + ' 演示模型', 'efforts': ['low', 'high'], 'images': True}],
     'commands': [{'name': 'demo-skill', 'prefix': '$' if engine == 'codex' else '/', 'description': '演示命令'}],
     'error': ''}
    for engine, name in [('claude', 'Claude Code'), ('codex', 'Codex'), ('opencode', 'OpenCode'), ('dsh', 'DeepSeek Harness')]]}


def summary(identifier, title, episode, part, created, archived, subtitles, analyses, materials, *,
            learned=False, translations=0, platform='哔哩哔哩'):
    return {'id': identifier, 'title': title, 'platform': platform, 'part': part,
            'episode_title': episode, 'display_title': title, 'is_single': part is None,
            'location': f'mock/{identifier}', 'created_at': created, 'archived': archived, 'learned': learned,
            'has_subtitles': subtitles > 0, 'subtitle_count': subtitles, 'translation_count': translations,
            'analysis_count': analyses, 'materials': [{'id': f'{identifier}-{index}', 'name': name,
                                                       'template': 'summary', 'created_at': created}
                                                      for index, name in enumerate(materials)]}


PROJECTS = [
    summary(PID, COURSE, '1.1 矩阵与方程组', 1, '2026-11-05T09:20:00', True, 2, 2,
            ['内容摘要', '章节时间轴'], learned=True, translations=1),
    summary(part_id(2), COURSE, '1.2 向量与线性组合', 2, '2026-11-05T09:26:00', True, 1, 0, []),
    summary(part_id(3), 'Python 数据处理入门', None, None, '2026-11-03T15:40:00', True, 1, 1,
            ['学习笔记'], learned=True, platform='YouTube'),
    summary(part_id(4), '纪录片 · 城市与记忆', None, None, '2026-10-31T20:10:00', False, 0, 0, []),
    summary(part_id(5), '视频剪辑基础 · 时间线', None, None, '2026-10-29T11:05:00', True, 0, 0, []),
    summary(part_id(6), '微积分 · 极限与连续', None, None, '2026-10-28T08:30:00', True, 3, 2,
            ['内容摘要', '章节时间轴'], learned=True),
    summary(part_id(7), '概率论 · 随机变量', None, None, '2026-10-26T19:15:00', True, 1, 0, []),
]

SOURCES = [{'project_id': part_id(part), 'resource': MEDIA['resource'], 'title': COURSE, 'platform': '哔哩哔哩',
            'source_url': f'https://www.bilibili.com/video/{VIDEO_ID}?p={part}', 'part': part,
            'quality': '1080p', 'duration_seconds': duration, 'size_bytes': 182 * 1024 * 1024,
            'archived_at': '2026-11-05T09:20:00', 'has_subtitles': part <= 2, 'subtitle_count': 3 if part == 1 else 1}
           for part, title, duration in EPISODES[:3]]

ACCOUNTS = {'storage': '.local/credentials', 'platforms': [
    {'id': 'bilibili', 'name': '哔哩哔哩', 'benefit': '解析分集、下载平台字幕和最高可用画质',
     'qr_login': True, 'qr_mode': 'bili', 'manual_finish': False, 'state': 'missing', 'saved_at': None,
     'expires_at': None, 'login': None},
    {'id': 'douyin', 'name': '抖音', 'benefit': '解析并下载公开视频与图集',
     'qr_login': True, 'qr_mode': 'browser', 'manual_finish': False, 'state': 'missing', 'saved_at': None,
     'expires_at': None, 'login': None},
    {'id': 'xiaohongshu', 'name': '小红书', 'benefit': '解析需要登录的公开笔记视频',
     'qr_login': True, 'qr_mode': 'browser', 'manual_finish': True, 'state': 'missing', 'saved_at': None,
     'expires_at': None, 'login': None},
]}

NAMING = {'title': COURSE, 'subtitle_count': 6, 'ready_count': 6, 'max_selected': 8, 'history': [], 'entries': [
    {'single_episode': False, 'project_id': part_id(part), 'part': part, 'title': title,
     'number': f'1.{part}', 'transcript': f'{title} 的演示字幕内容。',
     'transcript_filename': f'{title}-中文-本地转写.srt',
     'subtitle_files': [{'resource': f'subtitles/{title}-中文-本地转写.srt', 'source': 'local',
                         'filename': f'{title}-中文-本地转写.srt'}],
     'files': [{'resource': f'video/1080p-p{part}.mp4', 'source': 'video', 'filename': f'{title}.mp4'},
               {'resource': f'subtitles/{title}-中文-本地转写.srt', 'source': 'subtitles',
                'filename': f'{title}-中文-本地转写.srt'}]}
    for part, title, _ in EPISODES]}

FOLDER_NAMING = {'location': f'bilibili/{COURSE}__{VIDEO_ID}', 'title': COURSE, 'projects': 6, 'latest': None}

CONTEXT = {'transcripts': [{'resource': 'subtitles/1.1 矩阵与方程组-中文-本地转写.srt', 'label': '本地转写',
                            'kind': 'transcription', 'language': 'zh', 'cues': 36, 'chars': 920,
                            'merged': False, 'tokens': 640, 'end': '12:45'}],
           'duration': DURATION, 'auto_frame_count': 8, 'image_tokens': 800, 'prompt_tokens': 700,
           'output_tokens': 1500,
           'templates': [
               {'id': 'summary', 'name': '内容摘要', 'description': '提炼这节课的主要概念与结论', 'source': 'both',
                'requires': None},
               {'id': 'chapters', 'name': '章节时间轴', 'description': '按时间点划分章节', 'source': 'transcript',
                'requires': 'transcript'},
               {'id': 'notes', 'name': '学习笔记', 'description': '整理成便于复习的结构化笔记', 'source': 'both',
                'requires': None},
               {'id': 'quiz', 'name': '复习自测', 'description': '生成带答案与时间点的选择题', 'source': 'both',
                'requires': None},
           ],
           'sources': {'both': '字幕 + 画面', 'transcript': '只用字幕', 'frames': '只用画面'}, 'ai': AI}


def media_audio():
    buffer = io.BytesIO()
    with wave.open(buffer, 'wb') as clip:
        clip.setnchannels(1)
        clip.setsampwidth(2)
        clip.setframerate(8000)
        clip.writeframes(b'\0\0' * 8000 * DURATION)
    return buffer.getvalue()


class StaticHandler(SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=str(DIST), **kwargs)

    def log_message(self, *_args):
        pass


def run():
    assert (DIST / 'index.html').is_file(), 'Build the React frontend first: npm --prefix frontend run build'
    OUT.mkdir(parents=True, exist_ok=True)
    audio = media_audio()
    unexpected = []

    server = ThreadingHTTPServer(('127.0.0.1', 0), StaticHandler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    url = f'http://127.0.0.1:{server.server_port}'

    def api(route):
        request = route.request
        path = urlsplit(request.url).path.removeprefix('/api/workspace')
        if request.method == 'GET' and path == '/config':
            return route.fulfill(json=CONFIG)
        if path == '/tasks':
            return route.fulfill(json={'jobs': []})
        if path == '/projects':
            return route.fulfill(json={'projects': PROJECTS})
        if path == '/accounts':
            return route.fulfill(json=ACCOUNTS)
        if path == '/ai/agents':
            return route.fulfill(json=AGENTS)
        if path == '/ai/settings':
            return route.fulfill(json=AI)
        if path == '/embedding/settings':
            return route.fulfill(json={'base_url': '', 'model': ''})
        if path == '/transcriptions/sources':
            return route.fulfill(json={'sources': SOURCES})
        if path == '/folders':
            return route.fulfill(json={'folders': []})
        if path.startswith('/projects/') and path.endswith('/resume'):
            identifier = path.split('/')[2]
            return route.fulfill(json={**PARSED, 'id': identifier, 'project': {**PROJECT, 'id': identifier,
                                                                                'part': int(identifier, 16)},
                                       'episodes': EPISODE_ROWS})
        if path.startswith('/projects/') and path.endswith('/subtitles'):
            return route.fulfill(json=SUBTITLES)
        if path.startswith('/projects/') and path.endswith('/subtitle-cues'):
            return route.fulfill(json={'resource': 'subtitles/1.1 矩阵与方程组-中文-本地转写.srt',
                                       'sources': [], 'cues': []})
        if path.startswith('/projects/') and path.endswith('/analysis/context'):
            return route.fulfill(json=CONTEXT)
        if path.startswith('/projects/') and path.endswith('/analyses'):
            return route.fulfill(json={'records': [SUMMARY_RECORD, CHAPTERS_RECORD]})
        if path.startswith('/projects/') and '/study/progress' in path:
            return route.fulfill(json={'position': 0, 'duration': DURATION, 'revision': 1})
        if path.startswith('/projects/') and '/study/notes' in path:
            return route.fulfill(json={'notes': []})
        if path.startswith('/projects/') and '/conversations/context' in path:
            return route.fulfill(json={'available': True, 'missing': []})
        if path.startswith('/projects/') and '/conversations' in path:
            return route.fulfill(json={'conversations': []})
        if path.startswith('/projects/') and path.endswith('/naming'):
            return route.fulfill(json=NAMING)
        if path.startswith('/projects/') and path.endswith('/folder-naming'):
            return route.fulfill(json=FOLDER_NAMING)
        unexpected.append((request.method, path))
        return route.fulfill(status=404, json={'detail': f'Unexpected fixture request: {path}'})

    def shot(page, name, height):
        page.set_viewport_size({'width': 1440, 'height': height})
        page.wait_for_function("document.fonts.status === 'loaded'")
        page.evaluate('document.activeElement instanceof HTMLElement && document.activeElement.blur()')
        page.mouse.move(2, 2)
        page.wait_for_timeout(350)
        page.screenshot(path=str(OUT / name), full_page=False, animations='disabled')
        print('wrote', OUT / name)

    def context_for(scheme):
        context = browser.new_context(viewport={'width': 1440, 'height': 1040}, color_scheme=scheme,
                                      locale='zh-CN', timezone_id='Asia/Shanghai')
        context.route('**/api/workspace/**', api)
        context.route('**/demo-media.wav', lambda route: route.fulfill(content_type='audio/wav', body=audio))
        context.route('**/demo-subtitle.srt', lambda route: route.fulfill(content_type='text/plain', body=SUBTITLE_TEXT))
        return context

    try:
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=True)
            session = context_for('dark')
            page = session.new_page()
            page.goto(url + f'/?page=workspace&project={PID}', wait_until='networkidle')
            page.wait_for_selector('.info-title', state='visible')
            page.wait_for_function("document.querySelector('video')?.readyState >= 1")
            shot(page, 'workspace.png', 1040)

            page.locator('.app-sidebar').get_by_role('button', name='视频总览', exact=True).click()
            page.wait_for_selector('.overview-table tbody tr')
            shot(page, 'overview.png', 1200)

            page.locator('.app-sidebar').get_by_role('button', name='视频工作台', exact=True).click()
            page.get_by_role('tab', name='文件重命名', exact=True).click()
            page.wait_for_selector('.subtitle-naming-panel .naming-scope input[type=checkbox]')
            page.set_viewport_size({'width': 1440, 'height': 1600})
            page.wait_for_function("document.fonts.status === 'loaded'")
            page.evaluate('window.scrollTo(0, 0)')
            page.wait_for_timeout(500)
            box = page.locator('.subtitle-naming-panel').bounding_box()
            page.evaluate('document.activeElement instanceof HTMLElement && document.activeElement.blur()')
            page.mouse.move(2, 2)
            page.screenshot(path=str(OUT / 'naming.png'), animations='disabled',
                            clip={'x': box['x'] - 14, 'y': box['y'] - 14, 'width': box['width'] + 28, 'height': box['height'] + 28})
            print('wrote', OUT / 'naming.png')

            page.locator('.app-sidebar').get_by_role('button', name='账号与登录', exact=True).click()
            page.wait_for_selector('.account-list li')
            shot(page, 'accounts.png', 1040)

            page.locator('.app-sidebar').get_by_role('button', name='视频工作台', exact=True).click()
            page.get_by_role('tab', name='字幕与转写', exact=True).click()
            page.get_by_role('button', name='已归档视频').click()
            page.wait_for_selector('.subtitle-viewer')
            page.wait_for_selector('.source-list .source-option.checked')
            shot(page, 'transcription.png', 1040)

            page.goto(url + f'/?page=learning&project={PID}', wait_until='networkidle')
            page.wait_for_selector('.learning-materials')
            page.wait_for_selector('.chapter-timeline')
            shot(page, 'learning.png', 1200)
            session.close()

            # A fresh context has no remembered project, so the workbench shows its initial state.
            empty = context_for('dark')
            page = empty.new_page()
            page.goto(url + '/?page=workspace', wait_until='networkidle')
            page.wait_for_selector('.workspace-start')
            page.wait_for_selector('#source')
            shot(page, 'workspace-empty.png', 1040)
            empty.close()
            browser.close()
    finally:
        server.shutdown()
    assert not unexpected, f'Unmocked requests: {unexpected}'
    print('done')


if __name__ == '__main__':
    run()
