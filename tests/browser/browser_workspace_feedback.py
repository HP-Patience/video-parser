"""Deterministic end-to-end UI checks. API/model responses are fixtures; no user data or paid requests."""
import io
import json
import os
from pathlib import Path
import threading
import wave
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlsplit
from playwright.sync_api import sync_playwright, expect

ROOT = Path(__file__).resolve().parents[2]
ART = Path(os.environ.get('VIDEO_PARSER_TEST_OUTPUT', ROOT / '.local/work/artifacts/workspace-feedback'))
PID = 'a' * 32
PID2 = 'c' * 32

def main():
    ART.mkdir(parents=True, exist_ok=True)
    os.environ.setdefault('PLAYWRIGHT_BROWSERS_PATH', str(ROOT / 'runtimes/browsers'))
    class Handler(SimpleHTTPRequestHandler):
        def __init__(self, *args, **kwargs): super().__init__(*args, directory=os.environ.get('VIDEO_PARSER_TEST_DIST', str(ROOT / 'frontend/dist')), **kwargs)
        def log_message(self, *_): pass
    server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    audio = io.BytesIO()
    with wave.open(audio, 'wb') as stream:
        stream.setnchannels(1); stream.setsampwidth(2); stream.setframerate(8000); stream.writeframes(b'\0\0' * 8000 * 70)
    ai = {'provider': 'custom', 'provider_name': '测试模型', 'base_url': 'http://127.0.0.1:9999/v1', 'model': 'fixture-model', 'key_saved': False, 'key_hint': '', 'price_input': None, 'price_output': None, 'configured': True, 'local': True, 'consented': True, 'source': 'settings', 'storage': 'fixture', 'presets': {'custom': {'name': '本地测试', 'base_url': '', 'model': '', 'key_required': False, 'models': [], 'hint': ''}}}
    config = {'platforms': ['哔哩哔哩'], 'ai_configured': True, 'ai': ai, 'media_ready': True, 'subtitles_ready': True, 'transcription_ready': True, 'transcription_upload_limit': 1000000, 'archive_root': '测试素材库'}
    media = {'resource': 'video/fixture.wav', 'quality_id': 'q', 'preview_url': '/fixture.wav', 'download_url': '/fixture.wav', 'filename': 'fixture.wav', 'size_bytes': len(audio.getvalue()), 'duration': 70, 'width': 960, 'height': 540}
    project = {'id': PID, 'title': '内容生产与项目管理 · 功能验收', 'episode_title': '第一集：需求与排期', 'platform': '哔哩哔哩', 'part': 1, 'location': '测试素材库/P1', 'identity_status': 'verified', 'learned': False, 'archive_url': '/fixture.zip', 'resources': []}
    episodes = [{'part': 1, 'title': '需求与排期', 'duration': 70, 'project_id': PID, 'archived': True, 'learned': False}, {'part': 2, 'title': '制作与复盘', 'duration': 80, 'project_id': None, 'archived': False, 'learned': False}]
    parsed = {'id': PID, 'title': project['title'], 'platform': '哔哩哔哩', 'source_url': 'https://www.bilibili.com/video/BV1Fixture00?p=1', 'cover_url': '/frame.svg', 'cover_download_url': None, 'warning': None, 'project': project, 'media': media, 'qualities': [{'id': 'q', 'label': '1080p', 'width': 1920, 'height': 1080, 'media': media}], 'default_quality_id': 'q', 'episodes': episodes}
    source = {'resource': 'subtitles/fixture.srt', 'label': '平台字幕', 'kind': 'subtitle', 'language': 'zh', 'cues': 3, 'chars': 100, 'merged': False, 'tokens': 100, 'end': '01:00'}
    cues = [{'time': 2, 'text': '先明确项目需求，拆分任务并确定负责人。'}, {'time': 12, 'text': '根据制作流程安排时间，给审片和反馈预留空间。'}, {'time': 60, 'text': '最后汇总预算、项目资料和复盘结论。'}]
    record = {'id': '20261023-120000-abcdef12', 'created_at': '2026-10-23T12:00:00', 'template': 'summary', 'template_name': '内容摘要', 'source': 'both', 'source_label': '字幕＋画面', 'prompt': '', 'model': 'fixture-model', 'provider': '测试模型', 'transcript_label': '平台字幕', 'transcript_merged': False, 'video_resource': media['resource'], 'frame_count': 2, 'frames': [{'url': '/frame.svg', 'time': 2, 'label': '00:02'}, {'url': '/frame2.svg', 'time': 12, 'label': '00:12'}], 'usage': {}, 'text': '## 核心要点\n\n1. **明确需求与负责人。** 先拆分任务，再安排制作资源。[00:02]\n\n1. **预留审片时间。** 把反馈和修改纳入排期。[00:12]\n\n1. **集中资料与复盘。** 保存预算和交付记录。[01:00]', 'followups': [], 'legacy': False}
    agents = {'permission': 'restricted-learning', 'engines': [{'id': eid, 'name': name, 'enabled': True, 'installed': True, 'discovered': True, 'models': [{'id': eid + '-fixture', 'label': name + ' 测试模型', 'efforts': [], 'images': True}], 'commands': [], 'error': ''} for eid, name in [('claude', 'Claude Code'), ('codex', 'Codex'), ('opencode', 'OpenCode'), ('dsh', 'DeepSeek Harness')]]}
    jobs = [{'id': str(i) * 32, 'kind': 'analysis', 'state': 'completed', 'phase': 'completed', 'message': '已归档', 'title': project['title'], 'project_title': project['title'], 'project_id': PID, 'part': 1, 'created': 1792728000+i, 'elapsed_seconds': 25+i, 'details': {'template': template, 'source': 'both', 'engine': engine, 'model': engine + '-fixture', 'frame_mode': 'ai'}} for i, template, engine in [(1, 'summary', 'codex'), (2, 'chapters', 'claude')]]
    state = {'subtitles': False, 'calls': [], 'indexed': False, 'progress': {'position': 0, 'duration': 70, 'revision': 0}}
    transcription_jobs = {}
    cancel_ids = []
    source_rows = [dict(project_id=pid, resource=media['resource'], title=title, platform='哔哩哔哩', source_url='https://www.bilibili.com/video/BV1Fixture00?p='+str(part), part=part, quality='480p', duration_seconds=70, size_bytes=1000, archived_at='2026-10-23', has_subtitles=False)
                   for pid, title, part in [(PID, '第一集：需求与排期', 1), (PID2, '第二集：制作与复盘', 2)]]
    unexpected, errors = [], []
    def api(route):
        req = route.request; path = urlsplit(req.url).path.removeprefix('/api/workspace'); body = req.post_data_json if req.method == 'POST' and req.post_data else {}
        if path == '/config': data = config
        elif path == '/ai/settings': data = ai
        elif path in ('/ai/agents', '/ai/agents/discover'): data = agents
        elif path == '/ai/agents/enabled':
            next(row for row in agents['engines'] if row['id'] == body['engine'])['enabled'] = body['enabled']; data = agents
        elif path == '/ai/runtime': data = {'state': 'stopped', 'message': '', 'owned': False, 'elapsed_seconds': 0}
        elif path == '/embedding/settings': data = {'base_url': 'http://127.0.0.1:9999/v1', 'model': 'fixture-embedding', 'key_saved': False, 'configured': True}
        elif path.endswith('/embedding/index'): state['indexed'] = True; data = {'count': 3}
        elif path.endswith('/embedding/search'):
            assert state['indexed'] and body['consent']; data = {'results': [{'text': cues[1]['text'], 'time': 12, 'score': .91, 'title': project['title'], 'part': 1, 'resource': source['resource']}]}
        elif path == '/tasks': data = {'jobs': jobs}
        elif path == '/projects': data = {'projects': [{**project, 'archived': True}]}
        elif path == '/folders': data = {'folders': []}
        elif path == '/accounts': data = {'platforms': [], 'storage': 'fixture'}
        elif path == '/subtitles/inventory': data = {'video_id': 'BV1Fixture00', 'total': 2, 'archived': [], 'available': [], 'episodes': []}
        elif path == '/transcriptions/sources': data = {'sources': source_rows}
        elif path == '/transcriptions' and req.method == 'POST':
            row = next(row for row in source_rows if row['project_id'] == body['project_id'])
            identifier = ('d' if row['project_id'] == PID else 'e') * 32
            assert identifier not in transcription_jobs, 'Duplicate task submission'
            data = dict(id=identifier, state='running', phase='transcribing', message='转写进行中：'+row['title'], title=row['title'], text='', language='en', requested_language='auto', files=[], source_project_id=row['project_id'], source_resource=row['resource'], operation='transcription', progress={'done': 30, 'total': 70})
            transcription_jobs[identifier] = data
        elif path.startswith('/transcriptions/') and path.rsplit('/', 1)[-1] in transcription_jobs:
            data = transcription_jobs[path.rsplit('/', 1)[-1]]
        elif path.startswith('/tasks/transcriptions/') and path.endswith('/cancel'):
            identifier = path.split('/')[-2]; cancel_ids.append(identifier)
            transcription_jobs[identifier].update(state='cancelled', message='已取消该任务')
            data = transcription_jobs[identifier]
        elif path.endswith('/resume'):
            # Shared selection now validates the restored project identity.
            # Return the requested episode, not P1 for every fixture URL.
            restored_pid = path.split('/')[2]
            row = next(item for item in source_rows if item['project_id'] == restored_pid)
            data = {**parsed, 'id': restored_pid, 'source_url': row['source_url'],
                    'project': {**project, 'id': restored_pid, 'part': row['part'], 'episode_title': row['title']}}
        elif path.endswith('/episodes/refresh'): data = {'episodes': episodes}
        elif path.endswith('/study/progress'):
            if body: state['progress'] = {'position': body['position'], 'duration': 70, 'revision': state['progress']['revision']+1}
            data = state['progress']
        elif path.endswith('/study/notes'): data = {'notes': []}
        elif path.endswith('/conversations/context'): data = {'available': state['subtitles'], 'missing': [] if state['subtitles'] else ['subtitles', 'chapters', 'summary']}
        elif '/conversations' in path: data = {'conversations': []}
        elif path.endswith('/analysis/context'):
            data = {'transcripts': [source] if state['subtitles'] else [], 'duration': 70, 'auto_frame_count': 5, 'image_tokens': 800, 'prompt_tokens': 700, 'output_tokens': 1500, 'templates': [{'id': 'summary', 'name': '内容摘要', 'description': '提取核心要点', 'source': 'both', 'requires': None}], 'sources': {'both': '字幕 + 画面', 'transcript': '只用字幕', 'frames': '只用画面'}, 'ai': ai}
        elif path.endswith('/subtitle-cues'): data = {'sources': [source] if state['subtitles'] else [], 'resource': source['resource'], 'cues': cues if state['subtitles'] else []}
        elif path.endswith('/analyses'): data = {'records': [record]}
        elif path.endswith('/analyze-task'):
            assert state['subtitles'] and body['source'] == 'both' and body['frame_mode'] == 'ai'; state['calls'].append(body)
            data = {'id': 'b'*32, 'kind': 'analysis', 'state': 'completed', 'message': '完成', 'result': {'record': record, 'text': record['text'], 'mode': 'analyze', 'project': project}}
        else:
            unexpected.append(path); return route.fulfill(status=404, json={'detail': 'fixture missing: ' + path})
        route.fulfill(json=data)
    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True)
        page = browser.new_page(viewport={'width': 1440, 'height': 1000}, device_scale_factor=1)
        page.on('pageerror', lambda error: errors.append(str(error)))
        page.route('**/api/workspace/**', api)
        page.route('**/frame*.svg', lambda route: route.fulfill(content_type='image/svg+xml', body='<svg xmlns="http://www.w3.org/2000/svg" width="960" height="540"><rect width="960" height="540" fill="#dae5df"/><text x="70" y="100" font-size="34" fill="#243e39">项目管理 / WORKFLOW</text><rect x="70" y="155" width="820" height="240" rx="20" fill="#fff"/><text x="110" y="240" font-size="30" fill="#243e39">01 需求 → 02 制作 → 03 审核</text><text x="110" y="320" font-size="24" fill="#57796e">负责人 · 时间安排 · 反馈与复盘</text><text x="70" y="475" font-size="20" fill="#57796e">离线验收测试画面 / 非用户素材</text></svg>'))
        def audio_route(route):
            data = audio.getvalue(); start = 0; end = len(data)-1
            headers = {'Accept-Ranges': 'bytes'}
            if 'range' in route.request.headers:
                a,b = route.request.headers['range'].split('=')[1].split('-'); start = int(a or 0); end = min(int(b) if b else end, end); headers['Content-Range'] = f'bytes {start}-{end}/{len(data)}'
                route.fulfill(status=206, headers=headers, content_type='audio/wav', body=data[start:end+1])
            else: route.fulfill(headers=headers, content_type='audio/wav', body=data)
        page.route('**/fixture.wav', audio_route)
        base = f'http://127.0.0.1:{server.server_port}'
        try:
            page.goto(base + '/?project=' + PID)
            expect(page.locator('.info-title')).to_have_text('第一集：需求与排期')
            expect(page.locator('.inspector-identity')).to_contain_text(project['title'])
            expect(page.locator('#project-context .subtitle-entry')).to_have_count(0)
            expect(page.locator('.episode-refresh > button')).to_have_text('解析视频集数')
            page.screenshot(path=str(ART/'11-episode-title.png'), full_page=True)
            worktabs = page.get_by_role('tablist', name='内容类型')
            expect(worktabs.get_by_role('tab')).to_have_text(['视频', '字幕与转写', 'AI 分析', '文件重命名'])
            expect(page.locator('.preview-panel [aria-label="滚动字幕"]')).to_have_count(0)
            worktabs.get_by_role('tab', name='视频', exact=True).focus()
            page.keyboard.press('ArrowRight')
            expect(worktabs.get_by_role('tab', name='字幕与转写', exact=True)).to_be_focused()
            expect(page.locator('#subtitle-workpanel')).to_be_visible()
            page.keyboard.press('ArrowRight')
            expect(worktabs.get_by_role('tab', name='AI 分析', exact=True)).to_be_focused()
            expect(page.get_by_role('tab', name='AI 分析', exact=True)).to_be_visible()
            page.get_by_role('tab', name='AI 分析', exact=True).click()
            expect(page.locator('#analysis-configuration')).not_to_be_visible()
            page.get_by_role('button', name='生成新材料', exact=True).click()
            expect(page.get_by_role('button', name='开始分析', exact=True)).to_be_disabled()
            expect(page.get_by_text('当前视频暂无可用字幕', exact=False)).to_be_visible()
            page.screenshot(path=str(ART/'01-missing-subtitles.png'), full_page=True)
            expect(page.locator('.independent-conversations')).to_have_count(0)
            expect(page.get_by_role('button', name='返回学习', exact=True)).to_have_count(0)
            page.get_by_role('button', name='学习视图', exact=True).click()
            page.get_by_role('tab', name='AI 对话', exact=True).click()
            expect(page.locator('#study-panel-chat')).to_contain_text('请先准备字幕（中文或英文均可）')
            state['subtitles'] = True
            page.evaluate("window.dispatchEvent(new Event('video-parser:subtitles-changed'))")
            expect(page.locator('.ai-chat .ai-chat-empty')).to_have_count(0)
            page.get_by_role('button', name='视频工作台', exact=True).click()
            page.get_by_role('tab', name='AI 分析', exact=True).click()
            page.get_by_label('选取画面的方式').select_option('ai')
            expect(page.get_by_label('画面数量')).to_be_disabled()
            page.get_by_role('button', name='开始分析', exact=True).click()
            expect(page.get_by_role('button', name='开始分析', exact=True)).to_be_enabled(timeout=10000)
            assert len(state['calls']) == 1
            expect(page.locator('.analysis-markdown ol')).to_have_count(1)
            expect(page.locator('.analysis-markdown ol > li')).to_have_count(3)
            page.locator('.analysis-evidence > summary').click()
            page.locator('.analysis-frames[aria-label="这次分析用到的画面"] button').first.click()
            expect(page.get_by_role('dialog', name='画面大图')).to_be_visible()
            image_box = page.locator('.frame-lightbox > img').bounding_box()
            jump_box = page.locator('.frame-lightbox > button').bounding_box()
            assert abs(jump_box['y'] - image_box['y'] - image_box['height'] - 16) < 1
            page.screenshot(path=str(ART/'02-frame-lightbox.png'))
            page.keyboard.press('Escape'); expect(page.get_by_role('dialog')).not_to_be_visible()
            page.evaluate('window.scrollTo({top:0,behavior:"instant"})'); page.wait_for_function('window.scrollY === 0')
            page.screenshot(path=str(ART/'03-analysis-and-numbering.png'), full_page=True)
            page.locator('.analysis-markdown .md-time').nth(1).click()
            expect(page.get_by_role('heading', name='学习视图', exact=True)).to_be_visible()
            expect(page.locator('.video-context-more')).to_have_count(0)
            video = page.get_by_label('视频播放器'); page.wait_for_function('document.querySelector("video").currentTime >= 12'); video.evaluate('(v) => { v.pause(); v.currentTime = 20; }'); page.wait_for_function('!document.querySelector("video").seeking'); video.focus(); page.keyboard.press('ArrowRight')
            page.wait_for_function('Math.abs(document.querySelector("video").currentTime - 25) < .5')
            page.keyboard.press('ArrowLeft'); assert abs(video.evaluate('(v) => v.currentTime')-20) < .5
            expect(page.locator('.preview-panel [aria-label="滚动字幕"]')).to_have_count(0)
            expect(page.locator('section[aria-label="滚动字幕"]')).to_have_count(1)
            page.get_by_role('tab', name='字幕', exact=True).click()
            expect(page.get_by_role('button', name='在右侧打开', exact=True)).to_have_count(0)
            expect(page.get_by_role('button', name='移到视频下方', exact=True)).to_have_count(0)
            expect(page.get_by_role('tab', name='字幕', exact=True)).to_have_attribute('aria-selected', 'true')
            page.get_by_role('tabpanel', name='字幕', exact=True).get_by_text(cues[1]['text'], exact=True).click()
            page.wait_for_function('Math.abs(document.querySelector("video").currentTime - 12) < 1 && !document.querySelector("video").seeking'); video.evaluate('(v) => v.pause()')
            subtitle_box = page.get_by_role('region', name='滚动字幕', exact=True).bounding_box()
            video_box = video.bounding_box()
            assert subtitle_box['x'] >= video_box['x'] + video_box['width'], (subtitle_box, video_box)
            version_box = page.get_by_role('combobox', name='字幕版本').bounding_box()
            follow_box = page.get_by_role('button', name='自动跟随：开启', exact=True).bounding_box()
            assert abs(follow_box['y'] + follow_box['height']/2 - version_box['y'] - version_box['height']/2) < 1
            assert follow_box['x'] >= version_box['x'] + version_box['width']
            expect(page.get_by_role('button', name='刷新字幕', exact=True)).to_have_count(0)
            page.screenshot(path=str(ART/'04-subtitle-sidebar.png'), full_page=True)
            expect(page.get_by_text('语义检索 · 当前视频字幕', exact=True)).to_have_count(0)
            page.get_by_role('button', name='任务中心', exact=True).click()
            page.get_by_label('检索项目', exact=True).select_option(PID)
            semantic = page.locator('details').filter(has=page.get_by_text('语义检索 · 当前视频字幕', exact=True))
            semantic.locator('summary').click(); semantic.get_by_role('checkbox').check()
            semantic.get_by_role('button', name='建立 / 重建索引').click(); expect(semantic.get_by_text('已索引 3 个字幕片段。')).to_be_visible()
            semantic.get_by_label('语义检索问题').fill('如何安排制作排期？'); semantic.get_by_role('button', name='语义检索', exact=True).click()
            expect(semantic.get_by_text(cues[1]['text'], exact=True)).to_be_visible()
            search_box = page.get_by_role('region', name='字幕语义检索', exact=True).bounding_box()
            filters_box = page.locator('.task-center').bounding_box()
            assert search_box['y'] + search_box['height'] <= filters_box['y']
            page.screenshot(path=str(ART/'05-semantic-search.png'), full_page=True)
            semantic.locator('ol button').first.click()
            expect(page.get_by_role('heading', name='学习视图', exact=True)).to_be_visible()
            page.wait_for_function('Math.abs(document.querySelector("video").currentTime - 12) < 1')
            expect(page.get_by_text('语义检索 · 当前视频字幕', exact=True)).not_to_be_visible()
            page.get_by_role('button', name='任务中心', exact=True).click()
            expect(page.locator('.task-list > li')).to_have_count(2)
            expect(page.get_by_label('执行工具', exact=True)).not_to_be_visible()
            page.locator('.task-filter-details > summary').click()
            page.get_by_label('执行工具', exact=True).select_option('codex'); expect(page.locator('.task-list > li')).to_have_count(1)
            page.get_by_label('分析类型', exact=True).select_option('chapters'); expect(page.locator('.task-list > li')).to_have_count(0)
            page.get_by_role('button', name='重置筛选').click(); expect(page.locator('.task-list > li')).to_have_count(2)
            page.screenshot(path=str(ART/'06-task-center.png'), full_page=True)
            page.set_viewport_size({'width': 390, 'height': 844})
            assert page.evaluate('document.documentElement.scrollWidth <= innerWidth + 1')
            assert page.get_by_role('region', name='字幕语义检索', exact=True).evaluate('e=>e.scrollWidth<=e.clientWidth+1')
            page.screenshot(path=str(ART/'15-task-center-mobile.png'), full_page=True)
            page.set_viewport_size({'width': 1440, 'height': 1000})
            page.get_by_role('button', name='设置', exact=True).click(); page.get_by_role('button', name='AI 模型', exact=True).click()
            switches = page.locator('.model-sources'); switches.get_by_role('switch', name='Codex').uncheck()
            expect(switches.get_by_role('switch', name='Codex')).not_to_be_checked(); assert not agents['engines'][1]['enabled']
            embedding = page.locator('.embedding-settings')
            learning = page.locator('.learning-targets')
            page.wait_for_function("document.querySelector('.embedding-settings').getBoundingClientRect().top > document.querySelector('.learning-targets').getBoundingClientRect().bottom")
            page.get_by_role('button', name='配置检索模型', exact=True).click()
            fields = embedding.locator('.settings-field')
            for i in range(fields.count()):
                field = fields.nth(i)
                label_box, input_box = field.locator('label').bounding_box(), field.locator('input').bounding_box()
                assert input_box['height'] >= 36, input_box
                assert abs(input_box['y'] - label_box['y'] - label_box['height'] - 8) < 1
            save_box = embedding.get_by_role('button', name='保存检索配置').bounding_box()
            assert abs(save_box['height'] - 36) < 1
            page.screenshot(path=str(ART/'07-agent-and-embedding-settings.png'), full_page=True)
            page.get_by_role('button', name='切换到深色主题', exact=True).click()
            page.wait_for_function("getComputedStyle(document.body).color === 'rgb(250, 250, 250)'")
            page.screenshot(path=str(ART/'13-settings-dark.png'), full_page=True, animations='disabled')
            page.set_viewport_size({'width': 390, 'height': 844})
            assert page.evaluate('document.documentElement.scrollWidth <= innerWidth + 1')
            page.wait_for_function("document.querySelector('.embedding-settings').getBoundingClientRect().top > document.querySelector('.learning-targets').getBoundingClientRect().bottom")
            page.screenshot(path=str(ART/'14-settings-mobile.png'), full_page=True, animations='disabled')
            page.set_viewport_size({'width': 1440, 'height': 1000})
            page.get_by_role('button', name='切换到浅色主题', exact=True).click()
            page.wait_for_function("getComputedStyle(document.body).color === 'rgb(10, 10, 10)'")
            page.get_by_role('button', name='视频工作台', exact=True).click(); page.get_by_role('tab', name='字幕与转写', exact=True).click()
            expect(page.locator('#subtitle-workpanel')).to_be_visible()
            assert page.locator('.app-sidebar').get_by_role('button', name='字幕与转写', exact=True).count() == 0
            page.screenshot(path=str(ART/'08-integrated-transcription.png'), full_page=True)
            transcription = page.locator('section.transcription-panel')
            transcription.get_by_role('button', name='已归档视频', exact=False).click()
            radios = transcription.locator('input[name="transcription-video"]')
            radios.nth(0).click(); expect(radios.nth(0)).to_be_checked()
            transcription.get_by_role('button', name='本地转写', exact=True).click()
            transcription.get_by_role('button', name='开始转写', exact=True).click()
            expect(transcription.locator('.subtitle-status:visible')).to_contain_text('转写进行中：第一集')
            expect(radios.nth(1)).to_be_enabled()
            radios.nth(1).click(); expect(radios.nth(1)).to_be_checked()
            expect(transcription.get_by_role('button', name='取消转录任务', exact=True)).to_have_count(0)
            expect(transcription.locator('.subtitle-status:visible').filter(has_text='转写进行中：第一集')).to_have_count(0)
            transcription.get_by_role('button', name='本地转写', exact=True).click()
            transcription.get_by_role('button', name='开始转写', exact=True).click()
            expect(transcription.locator('.subtitle-status:visible')).to_contain_text('转写进行中：第二集')
            assert transcription_jobs['d'*32]['state'] == 'running' and cancel_ids == []
            radios.nth(0).click(); expect(radios.nth(0)).to_be_checked()
            expect(transcription.locator('.subtitle-status:visible')).to_contain_text('转写进行中：第一集')
            page.screenshot(path=str(ART/'12-transcription-switching.png'), full_page=True)
            transcription.get_by_role('button', name='取消转录任务', exact=True).click()
            expect(transcription.locator('.subtitle-status:visible')).to_contain_text('已取消该任务')
            assert cancel_ids == ['d'*32]
            radios.nth(1).click(); expect(radios.nth(1)).to_be_checked()
            expect(transcription.locator('.subtitle-status:visible')).to_contain_text('转写进行中：第二集')
            assert transcription_jobs['e'*32]['state'] == 'running'
            page.set_viewport_size({'width': 390, 'height': 844})
            page.screenshot(path=str(ART/'09-mobile.png'), full_page=True)
            assert page.evaluate('document.documentElement.scrollWidth <= innerWidth + 1')
            page.set_viewport_size({'width': 1440, 'height': 1000})
            page.get_by_role('button', name='任务中心', exact=True).click()
            page.get_by_role('button', name='切换到深色主题', exact=True).click()
            expect(page.locator('html')).to_have_attribute('data-theme', 'dark')
            page.wait_for_function("getComputedStyle(document.body).color === 'rgb(250, 250, 250)' && getComputedStyle(document.body).backgroundColor === 'rgb(10, 10, 10)'")
            page.screenshot(path=str(ART/'10-task-center-dark.png'), full_page=True, animations='disabled')
            assert not errors, errors
            assert not unexpected, unexpected
            (ART/'browser-summary.json').write_text(json.dumps({'passed': True, 'screenshots': 15, 'page_errors': errors, 'unexpected_api': unexpected, 'analysis_calls': len(state['calls']), 'mode': 'offline fixtures, no real model calls, four feedback regressions'}, ensure_ascii=False, indent=2), encoding='utf-8')
            print('PASS: upgrade browser checks and 15 screenshots; no real model calls.')
        except Exception:
            (ART/'subtitle-layout-failure.html').write_text(page.content(), encoding='utf-8')
            print('SUBTITLE_ANCESTORS', page.locator('.subtitle-scroll-panel').evaluate_all("es => es.map(e => {const a=[]; for(let p=e;p;p=p.parentElement) a.push(p.tagName+'.'+p.className); return a})"))
            page.screenshot(path=str(ART/'failure.png'), full_page=True)
            print('UNEXPECTED', unexpected, 'ERRORS', errors)
            raise
        finally:
            browser.close(); server.shutdown()

if __name__ == '__main__': main()
