"""Offline browser regressions for the 2026-10-17 product improvements.

Build first. VIDEO_PARSER_TEST_DIST selects an isolated build; every workspace API
request is mocked and all non-loopback requests are blocked. No real model, agent,
platform download, credential write, or user-library mutation is permitted here.
"""
import io
import json
import os
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import re
import threading
import unittest
from tests.browser.project_picker import select_project
import wave
from urllib.parse import urlsplit

from playwright.sync_api import expect, sync_playwright

ROOT = Path(__file__).resolve().parents[2]
os.environ.setdefault('PLAYWRIGHT_BROWSERS_PATH', str(ROOT / 'runtimes/browsers'))
VIDEO_ID = 'BV1TaqYBcEJc'
PROJECT_ID = f'{3:032x}'
PREFERENCES = 'video-parser:agent-preferences'


class ProductImprovementsBrowserTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        dist = Path(os.environ.get('VIDEO_PARSER_TEST_DIST', ROOT / 'frontend/dist'))
        if not (dist / 'index.html').is_file():
            raise RuntimeError('Build the React frontend first')
        audio = io.BytesIO()
        with wave.open(audio, 'wb') as clip:
            clip.setnchannels(1)
            clip.setsampwidth(2)
            clip.setframerate(8000)
            clip.writeframes(b'\0\0' * 8000)
        cls.audio = audio.getvalue()
        cls.output = Path(os.environ.get('VIDEO_PARSER_TEST_OUTPUT', ROOT / '.local/product-improvements-browser'))
        cls.output.mkdir(parents=True, exist_ok=True)

        class StaticHandler(SimpleHTTPRequestHandler):
            def __init__(self, *args, **kwargs):
                super().__init__(*args, directory=str(dist), **kwargs)

            def log_message(self, *_args):
                pass

        cls.server = ThreadingHTTPServer(('127.0.0.1', 0), StaticHandler)
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()
        cls.url = f'http://127.0.0.1:{cls.server.server_port}'
        cls.playwright = sync_playwright().start()
        cls.browser = cls.playwright.chromium.launch(headless=True, channel=os.environ.get('VIDEO_PARSER_BROWSER_CHANNEL'))

    @classmethod
    def tearDownClass(cls):
        cls.browser.close()
        cls.playwright.stop()
        cls.server.shutdown()
        cls.thread.join(timeout=5)
        cls.server.server_close()

    def setUp(self):
        self.title = '原课程标题'
        self.archived = {1, 3}
        self.parts = [1, 2, 3]
        self.requests = []
        self.unexpected = []
        self.errors = []
        self.rename_failure = False
        self.with_media = False
        self.ai = {
            'provider': 'custom', 'provider_name': 'llama.cpp fixture',
            'base_url': 'http://127.0.0.1:19999/v1', 'model': 'llama-fixture',
            'key_saved': False, 'key_hint': '', 'price_input': None, 'price_output': None,
            'configured': True, 'local': True, 'consented': True, 'source': 'none', 'storage': 'fixture',
            'presets': {'custom': {'name': '自定义', 'base_url': '', 'model': '', 'models': [], 'key_required': False, 'hint': ''}},
        }
        self.catalog = {'permission': 'restricted-learning', 'engines': [
            {'id': engine, 'name': name, 'installed': True, 'discovered': True, 'error': '',
             'models': [{'id': engine + '-fixture', 'label': name + ' fixture', 'efforts': ['low', 'high'], 'images': True}],
             'commands': []}
            for engine, name in [('claude', 'Claude Code'), ('codex', 'Codex'), ('opencode', 'OpenCode')]
        ]}
        self.jobs = [
            {'id': 'a' * 32, 'kind': 'video', 'state': 'completed', 'part': 3, 'project_id': PROJECT_ID},
            {'id': 'b' * 32, 'kind': 'analysis', 'state': 'queued', 'part': 1, 'project_id': f'{1:032x}'},
            {'id': 'c' * 32, 'kind': 'subtitles', 'state': 'failed', 'part': 2, 'project_id': f'{2:032x}'},
            {'id': 'd' * 32, 'kind': 'transcriptions', 'state': 'empty', 'part': None, 'project_id': None},
        ]
        self.context = self.browser.new_context(viewport={'width': 1440, 'height': 1000}, service_workers='block')
        self.context.route('**/*', self.route)
        self.context.on('page', lambda page: page.on('pageerror', lambda error: self.errors.append(str(error))))
        self.page = self.context.new_page()

    def tearDown(self):
        self.context.close()
        self.assertEqual(self.unexpected, [], 'Unexpected network/API calls must not be silently mocked')
        self.assertEqual(self.errors, [])

    def media(self):
        return {'resource': 'video/fixture.wav', 'quality_id': '1080p',
                'preview_url': '/product-fixture.wav', 'download_url': '/product-fixture.wav',
                'filename': 'fixture.wav', 'size_bytes': len(self.audio), 'duration': 1,
                'width': None, 'height': None} if self.with_media else None

    def project(self):
        return {'id': PROJECT_ID, 'title': self.title, 'platform': 'bilibili', 'part': 3,
                'location': f'bilibili/{VIDEO_ID}/P003', 'identity_status': 'verified', 'resources': [],
                'archive_url': f'/api/workspace/projects/{PROJECT_ID}/archive'}

    def parsed(self):
        media = self.media()
        return {'id': PROJECT_ID, 'title': self.title, 'platform': '哔哩哔哩',
                'source_url': f'https://www.bilibili.com/video/{VIDEO_ID}?p=3',
                'cover_url': None, 'cover_download_url': None, 'warning': None, 'project': self.project(),
                'media': media, 'qualities': [{'id': '1080p', 'label': '1080p', 'width': 1920, 'height': 1080, 'media': media}],
                'default_quality_id': '1080p', 'duration': 180, 'episodes': []}

    def route(self, route):
        url = urlsplit(route.request.url)
        if url.hostname != '127.0.0.1' or url.port != self.server.server_port:
            self.unexpected.append(('external', url.hostname))
            return route.abort()
        if url.path == '/product-fixture.wav':
            # Real servers answer range requests; without 206 support Chromium treats the clip as
            # non-seekable and `currentTime = x` is silently dropped.
            data = self.audio
            span = route.request.headers.get('range', '')
            if span.startswith('bytes='):
                first, _, last = span[6:].partition('-')
                start = int(first or 0)
                end = min(int(last) if last else len(data) - 1, len(data) - 1)
                return route.fulfill(status=206, content_type='audio/wav', body=data[start:end + 1],
                                     headers={'Content-Range': f'bytes {start}-{end}/{len(data)}', 'Accept-Ranges': 'bytes'})
            return route.fulfill(content_type='audio/wav', body=data, headers={'Accept-Ranges': 'bytes'})
        if not url.path.startswith('/api/workspace'):
            return route.continue_()
        # Scrub-bar thumbnails are static imagery like the media fixture: mocked and never recorded,
        # so request assertions keep covering real API traffic only.
        if url.path.endswith('/preview-frame'):
            return route.fulfill(status=200, content_type='image/svg+xml',
                                 body=b'<svg xmlns="http://www.w3.org/2000/svg" width="160" height="90"/>')
        path = url.path.removeprefix('/api/workspace')
        method = route.request.method
        body = route.request.post_data_json if route.request.post_data else None
        self.requests.append((method, path, body))
        if method == 'GET':
            if path == '/config':
                return route.fulfill(json={'platforms': ['哔哩哔哩'], 'ai_configured': True, 'media_ready': True,
                    'subtitles_ready': True, 'transcription_ready': True, 'transcription_upload_limit': 1000000,
                    'archive_root': 'fixture/library', 'ai': self.ai})
            if path == '/embedding/settings':
                return route.fulfill(json={'base_url': '', 'model': '', 'key_saved': False, 'configured': False})
            if path.endswith('/subtitle-cues'):
                return route.fulfill(json={'sources': [], 'resource': None, 'cues': []})
            if path == '/ai/settings':
                return route.fulfill(json=self.ai)
            if path == '/ai/agents':
                return route.fulfill(json=self.catalog)
            if path == '/tasks':
                return route.fulfill(json={'jobs': [{**job, 'file_names': job.get('file_names', ['fixture.wav']), 'title': '旧平台标题',
                    'project_title': self.title if job['project_id'] else None,
                    'phase': job['state'], 'message': '', 'created': 1, 'progress': {}} for job in self.jobs]})
            if path == '/accounts':
                return route.fulfill(json={'platforms': [], 'storage': 'fixture'})
            if path == '/transcriptions/sources':
                sources = [{'project_id': PROJECT_ID, 'resource': 'video/fixture.wav', 'title': self.title,
                    'platform': '哔哩哔哩', 'part': 3, 'source_url': self.parsed()['source_url'], 'quality': '1080p',
                    'duration_seconds': 1, 'size_bytes': len(self.audio), 'archived_at': ''}] if self.with_media else []
                return route.fulfill(json={'sources': sources})
            if path == '/projects':
                return route.fulfill(json={'projects': [{key: value for key, value in {
                    **self.project(), 'archived': self.with_media, 'created_at': '',
                }.items() if key in ('id', 'title', 'platform', 'part', 'location', 'created_at', 'video_id', 'episode_title', 'archived')}]})
            if path == '/folders':
                return route.fulfill(json={'folders': []})
            if path == '/bili-login/status':
                return route.fulfill(json={'authenticated': True})
            if path.endswith('/resume'):
                return route.fulfill(json=self.parsed())
            if '/study/' in path and path.endswith('/progress'):
                return route.fulfill(json={'position': 0, 'duration': 1, 'revision': 0})
            if '/study/' in path and path.endswith('/notes'):
                return route.fulfill(json={'notes': []})
            if path.endswith('/analysis/context'):
                return route.fulfill(json={'transcripts': [], 'duration': 180, 'auto_frame_count': 8, 'image_tokens': 800,
                    'prompt_tokens': 700, 'output_tokens': 1500, 'templates': [
                        {'id': 'summary', 'name': '内容摘要', 'description': 'fixture', 'source': 'both', 'requires': None}],
                    'sources': {'both': '字幕 + 画面', 'transcript': '只用字幕', 'frames': '只用画面'}, 'ai': self.ai})
            if path.endswith('/analyses'):
                return route.fulfill(json={'records': []})
            if path.endswith('/conversations/context'):
                return route.fulfill(json={'available': False, 'missing': ['subtitles', 'chapters', 'summary']})
            if path.endswith('/conversations'):
                return route.fulfill(json={'conversations': []})
        if method == 'POST':
            if path.endswith('/conversations/migrate'):
                self.assertEqual(body, {})
                return route.fulfill(json={'conversations': []})
            if path == '/ai/agents/discover':
                self.assertEqual(set(body), {'confirm', 'engine', 'model'})
                self.assertIs(body['confirm'], True); self.assertEqual(body['model'], '')
                self.assertIn(body['engine'], {'all', 'claude', 'codex', 'opencode'})
                return route.fulfill(json=self.catalog)
            if path == '/parse':
                return route.fulfill(json=self.parsed())
            if path == f'/projects/{PROJECT_ID}/title':
                if self.rename_failure:
                    return route.fulfill(status=503, json={'detail': '模拟保存失败，原标题保留。'})
                self.title = body['title']
                return route.fulfill(json={'project': self.project(), 'renamed': [PROJECT_ID, f'{1:032x}']})
            if path == '/tasks/clear':
                self.assertEqual(body, {'confirm': True})
                previous = len(self.jobs)
                self.jobs = [job for job in self.jobs if job['state'] not in {'failed', 'cancelled', 'interrupted'}]
                return route.fulfill(json={'removed': previous - len(self.jobs)})
            if path == '/subtitles/inventory':
                return route.fulfill(json={'video_id': VIDEO_ID, 'total': len(self.parts), 'archived': sorted(self.archived),
                    'episodes': [{'part': part, 'title': f'第{part}讲', 'subtitles': part in self.archived} for part in self.parts]})
            if path == '/subtitles':
                self.assertEqual(body['scope'], 'custom')
                self.assertEqual(body['episodes'], '1-3')
                self.archived.add(2)
                return route.fulfill(status=202, json={'id': 'e' * 32, 'state': 'completed', 'title': self.title,
                    'message': '已下载缺失的 P2；已跳过本地已有字幕 2 集。',
                    'results': [], 'skipped': [1, 3], 'downloaded': 1, 'existing': 0, 'failed': 0})
        self.unexpected.append((method, path))
        return route.fulfill(status=500, json={'detail': 'Unexpected fixture request'})

    def settings(self, page=None):
        page = page or self.page
        page.goto(self.url + '?page=settings&settings_section=ai')
        targets = page.locator('.learning-targets')
        expect(targets).to_be_visible()
        for engine in ('claude', 'codex', 'opencode'):
            targets.locator(f'button[aria-controls="models-{engine}"]').click()
        expect(targets.locator('.learning-target-group')).to_have_count(3)
        expect(targets.get_by_role('switch')).to_have_count(3)
        return targets

    @staticmethod
    def model_row(targets, name):
        return targets.locator('li').filter(has_text=name)

    def fit(self, name):
        for width in (1440, 1024, 760, 390, 320, 280):
            self.page.set_viewport_size({'width': width, 'height': 1000})
            self.assertTrue(self.page.evaluate('document.documentElement.scrollWidth <= innerWidth'), f'{name} overflow at {width}px')
        self.page.set_viewport_size({'width': 390, 'height': 900})
        self.page.screenshot(path=str(self.output / (name + '-mobile.png')), full_page=True)
        self.page.set_viewport_size({'width': 1440, 'height': 1000})
        self.page.screenshot(path=str(self.output / (name + '-desktop.png')), full_page=True)

    def parse(self):
        self.page.goto(self.url)
        self.page.get_by_role('textbox', name='视频链接或分享文本').fill(self.parsed()['source_url'])
        self.page.get_by_role('button', name='解析视频', exact=True).click()
        expect(self.page.get_by_role('button', name='重命名', exact=True)).to_be_visible()

    def subtitle_panel(self):
        self.parse()
        self.page.get_by_role('tab', name='AI 分析', exact=True).click()
        self.page.get_by_role('button', name='先生成字幕', exact=True).click()
        self.page.wait_for_load_state('networkidle')
        self.page.get_by_role('radiogroup', name='选择视频', exact=True).get_by_role('radio').check()
        panel = self.page.get_by_role('group', name='平台字幕获取确认', exact=True)
        expect(panel).to_be_visible()
        panel.get_by_role('button', name='选择批量范围（可选）', exact=True).click()
        return panel

    def test_default_model_is_primary_and_visibility_management_is_optional(self):
        self.page.goto(self.url + '?page=settings&settings_section=ai')
        targets = self.page.locator('.learning-targets')
        default = targets.get_by_role('combobox', name='默认模型', exact=True)
        expect(default).to_be_visible(); expect(default).to_have_value('api|')
        expect(targets.get_by_text('跟随工具默认不代表离线或免费，联网与费用由对应工具配置决定。', exact=True)).to_have_count(0)
        expect(targets.locator('#models-codex')).to_be_hidden()
        expect(targets.get_by_role('radio')).to_have_count(0)
        self.assertFalse(any(method == 'POST' for method, _, _ in self.requests))
        default.select_option('codex|codex-fixture')
        saved = self.page.evaluate('(key)=>JSON.parse(localStorage.getItem(key))', PREFERENCES)
        self.assertEqual(saved['target'], {'engine': 'codex', 'model': 'codex-fixture', 'effort': ''})
        self.assertIn('codex|codex-fixture', saved['enabled'])
        targets.locator('button[aria-controls="models-codex"]').click()
        search = targets.get_by_role('searchbox', name='搜索 Codex 模型', exact=True)
        search.fill('codex-fixture')
        expect(targets.locator('#models-codex li')).to_have_count(1)
        row = self.model_row(targets, 'Codex fixture')
        expect(row.get_by_role('checkbox')).to_be_checked()
        expect(row.locator('code')).to_have_count(0)
        targets.locator('#models-codex').get_by_label('显示调用 ID', exact=True).check()
        expect(row.locator('code')).to_have_text('codex-fixture')
        self.assertEqual(self.page.evaluate('(key)=>JSON.parse(localStorage.getItem(key))', PREFERENCES), saved)
        self.assertFalse(any(method == 'POST' for method, _, _ in self.requests))
        self.fit('separated-model-controls')

    def test_workspace_empty_state_offers_import_or_existing_learning(self):
        self.page.goto(self.url)
        expect(self.page.get_by_role('heading', name='从一个链接开始', exact=True)).to_be_visible()
        expect(self.page.get_by_role('textbox', name='视频链接或分享文本')).to_be_visible()
        expect(self.page.get_by_role('button', name='解析视频', exact=True)).to_be_disabled()
        expect(self.page.locator('#workpanel')).to_be_hidden()
        expect(self.page.locator('#project-context')).to_be_hidden()
        self.assertFalse(any(method == 'POST' for method, _, _ in self.requests))
        self.fit('workspace-empty')
        self.with_media = True
        self.page.get_by_role('button', name='继续已有学习', exact=True).click()
        picker = self.page.get_by_role('combobox', name='学习项目', exact=True)
        expect(picker).to_be_enabled(); select_project(picker, PROJECT_ID)
        expect(self.page.locator('video')).to_be_visible()
        self.assertFalse(any(method == 'POST' and path in {'/parse', '/ai/agents/discover'} for method, path, _ in self.requests))

    def test_archived_workspace_keeps_learning_primary_and_source_edit_explicit(self):
        self.with_media = True; self.parse()
        expect(self.page.locator('.study-start-bar')).to_have_count(0)
        expect(self.page.get_by_role('button', name='开始学习', exact=True)).to_have_count(0)
        expect(self.page.get_by_text('视频已归档，可以开始学习', exact=True)).to_have_count(0)
        expect(self.page.get_by_role('button', name='学习视图', exact=True)).to_be_visible()
        expect(self.page.locator('.workspace-start')).to_have_count(0)
        expect(self.page.locator('.workspace-source-editor')).to_have_count(0)
        expect(self.page.locator('.workspace-source-url')).to_have_count(0)
        count = sum(method == 'POST' and path == '/parse' for method, path, _ in self.requests)
        self.page.get_by_role('button', name='清空链接', exact=True).click()
        expect(self.page.get_by_role('textbox', name='视频链接或分享文本')).to_have_value('')
        self.assertEqual(sum(method == 'POST' and path == '/parse' for method, path, _ in self.requests), count)

    def test_tool_default_wording_preserves_native_target_and_visibility(self):
        seed = {'enabled': ['api|', 'codex|', 'codex|codex-fixture'],
                'target': {'engine': 'codex', 'model': '', 'effort': ''}}
        self.context.add_init_script(f"localStorage.setItem('{PREFERENCES}',JSON.stringify({json.dumps(seed)})); window.preferenceWrites=[]; const original=Storage.prototype.setItem; Storage.prototype.setItem=function(key,value){{if(key==='{PREFERENCES}') window.preferenceWrites.push(value); return original.call(this,key,value)}}")
        targets = self.settings()
        expect(targets.get_by_text('跟随工具默认模型', exact=True)).to_have_count(3)
        expect(targets).not_to_contain_text('本机默认')
        expect(targets).to_contain_text('不代表离线或免费')
        expect(targets.locator('#learning-default-model option:checked')).to_have_text('Codex · 跟随工具默认模型')
        group = targets.locator('fieldset[aria-label="Codex"]')
        row = self.model_row(group, '跟随工具默认模型')
        expect(row.get_by_role('checkbox')).to_be_checked()
        expect(row.get_by_text('当前默认', exact=True)).to_be_visible()
        expect(targets.get_by_role('radio')).to_have_count(0)
        self.assertEqual(self.page.evaluate('(key)=>JSON.parse(localStorage.getItem(key))', PREFERENCES), seed)
        self.assertEqual(self.page.evaluate('window.preferenceWrites'), [])
        self.assertFalse(any(method == 'POST' for method, _, _ in self.requests))
        self.fit('tool-default-wording')
        self.page.reload()
        expect(self.page.locator('#learning-default-model option:checked')).to_have_text('Codex · 跟随工具默认模型')
        self.assertEqual(self.page.evaluate('(key)=>JSON.parse(localStorage.getItem(key))', PREFERENCES), seed)
        self.with_media = True
        self.parse()
        self.page.get_by_role('tab', name='AI 分析', exact=True).click()
        picker = self.page.locator('.analysis-form').get_by_role('combobox', name='引擎与模型', exact=True)
        expect(picker).to_have_value('codex|')
        expect(picker.locator('option:checked')).to_have_text('Codex · 跟随工具默认模型')
        self.assertEqual(self.page.evaluate('(key)=>JSON.parse(localStorage.getItem(key))', PREFERENCES), seed)
        self.assertEqual(self.page.evaluate('window.preferenceWrites'), [])
        self.assertFalse(any(method == 'POST' and path not in {'/parse', f'/projects/{PROJECT_ID}/conversations/migrate'}
                             for method, path, _ in self.requests))

    def test_tool_default_wording_is_translated_without_changing_the_target(self):
        seed = {'enabled': None, 'target': {'engine': 'opencode', 'model': '', 'effort': ''}}
        self.context.add_init_script(f"localStorage.setItem('vp-language','en');localStorage.setItem('{PREFERENCES}',JSON.stringify({json.dumps(seed)}))")
        targets = self.settings()
        expect(targets.get_by_text('Use tool default model', exact=True)).to_have_count(3)
        expect(targets.get_by_role('combobox', name='Default model', exact=True)).to_have_value('opencode|')
        expect(targets.locator('#learning-default-model option:checked')).to_have_text('OpenCode · Use tool default model')
        expect(targets).to_contain_text('does not mean offline or free')
        for width in (390, 280):
            self.page.set_viewport_size({'width': width, 'height': 900})
            self.assertTrue(self.page.evaluate('document.documentElement.scrollWidth<=innerWidth'), width)
        expect(targets).not_to_contain_text('Local default model')
        self.assertEqual(self.page.evaluate('(key)=>JSON.parse(localStorage.getItem(key))', PREFERENCES), seed)
        self.assertFalse(any(method == 'POST' for method, _, _ in self.requests))

    def test_settings_default_visibility_persistence_and_explicit_switch(self):
        targets = self.settings()
        default = targets.get_by_role('combobox', name='默认模型', exact=True)
        expect(default).to_have_value('api|')
        expect(default.locator('option[value="api|"]')).to_be_enabled()
        expect(targets.locator('.settings-ai')).to_contain_text('API 连接（含本地模型）')
        self.assertFalse(any(method == 'POST' for method, _, _ in self.requests), 'Opening Settings must not discover models or save config')
        codex = self.model_row(targets, 'Codex fixture')
        default.select_option('codex|codex-fixture')
        self.model_row(targets, 'Claude Code fixture').get_by_role('checkbox').uncheck()
        target = self.page.evaluate('(key) => JSON.parse(localStorage.getItem(key))', PREFERENCES)
        self.assertEqual(target['target'], {'engine': 'codex', 'model': 'codex-fixture', 'effort': ''})
        self.assertNotIn('claude|claude-fixture', target['enabled'])
        targets.locator('#models-codex').get_by_role('button', name='读取本机模型', exact=True).click()
        expect(targets.get_by_role('status')).to_contain_text('已读取本机模型列表')
        expect(targets.get_by_role('radio')).to_have_count(0)
        self.fit('settings')
        self.page.reload()
        self.page.locator('button[aria-controls="models-codex"]').click()
        expect(self.page.locator('#learning-default-model')).to_have_value('codex|codex-fixture')
        self.with_media = True
        self.parse()
        self.page.get_by_role('tab', name='AI 分析', exact=True).click()
        picker = self.page.locator('.analysis-form').get_by_role('combobox', name='引擎与模型', exact=True)
        expect(picker).to_have_value('codex|codex-fixture')
        expect(picker.locator('option[value="claude|claude-fixture"]')).to_have_count(0)
        expect(picker.locator('option[value="api|"]')).to_contain_text('llama-fixture')
        targets = self.settings()
        expect(self.model_row(targets, 'Codex fixture').get_by_role('checkbox')).to_be_disabled()
        expect(targets.locator('#learning-default-model')).to_have_value('codex|codex-fixture')
        targets.locator('#learning-default-model').select_option('api|')
        self.model_row(targets, 'Codex fixture').get_by_role('checkbox').uncheck()
        expect(targets.locator('#learning-default-model')).to_have_value('api|')
        self.assertFalse(any(path in {'/ai/settings', '/ai/runtime/start', '/ai/settings/test'} and method == 'POST' for method, path, _ in self.requests))

    def test_settings_cross_tab_updates(self):
        targets = self.settings()
        other = self.context.new_page()
        other_targets = self.settings(other)
        other_targets.locator('#learning-default-model').select_option('codex|codex-fixture')
        expect(targets.locator('#learning-default-model')).to_have_value('codex|codex-fixture')
        other.close()

    def test_malformed_preferences_fall_back_without_writing_credentials(self):
        self.context.add_init_script("localStorage.setItem('video-parser:agent-preferences','{broken json')")
        targets = self.settings()
        expect(targets.locator('#learning-default-model')).to_have_value('api|')

    def test_legacy_target_migrates_without_discovery(self):
        self.context.add_init_script("localStorage.setItem('video-parser:agent-target',JSON.stringify({engine:'codex',model:'codex-fixture',effort:'high'}))")
        targets = self.settings()
        expect(targets.locator('#learning-default-model')).to_have_value('codex|codex-fixture')
        self.assertFalse(any(method == 'POST' for method, _, _ in self.requests))

    def test_blocked_storage_keeps_current_page_choice_and_warns(self):
        self.context.add_init_script("const original=Storage.prototype.setItem; Storage.prototype.setItem=function(key,...args){if(key==='video-parser:agent-preferences') throw new DOMException('blocked','QuotaExceededError'); return original.call(this,key,...args)}")
        targets = self.settings()
        targets.locator('#learning-default-model').select_option('codex|codex-fixture')
        expect(targets.locator('#learning-default-model')).to_have_value('codex|codex-fixture')
        expect(targets.get_by_role('alert')).to_contain_text('当前页已生效，关闭或刷新后可能丢失')
        self.assertIsNone(self.page.evaluate('(key) => localStorage.getItem(key)', PREFERENCES))

    def test_rename_normalizes_title_preserves_paths_and_keeps_failed_edit(self):
        self.parse()
        button = self.page.get_by_role('button', name='重命名', exact=True)
        button.click()
        title = self.page.get_by_role('textbox', name='项目标题', exact=True)
        title.fill('   ')
        expect(self.page.get_by_role('button', name='保存', exact=True)).to_be_disabled()
        title.press('Escape')
        expect(button).to_be_focused()
        button.click()
        title.fill('  重命名   课程  ')
        self.page.get_by_role('button', name='保存', exact=True).click()
        expect(self.page.get_by_role('heading', name='重命名 课程', exact=True)).to_be_visible()
        expect(self.page.locator('.project-path')).to_have_text(f'outputs/bilibili/{VIDEO_ID}/P003')
        self.assertEqual([body for method, path, body in self.requests if path.endswith('/title')], [{'title': '重命名 课程'}])
        self.rename_failure = True
        button.click()
        title.fill('暂存的未保存标题')
        self.page.get_by_role('button', name='保存', exact=True).click()
        expect(self.page.locator('.project-message')).to_contain_text('模拟保存失败')
        expect(title).to_have_value('暂存的未保存标题')
        expect(self.page.get_by_role('heading', name='重命名 课程', exact=True)).to_be_visible()
        self.page.get_by_role('button', name='取消', exact=True).click()
        self.page.get_by_role('button', name=re.compile('^任务中心')).click()
        expect(self.page.locator('.task-list > li').filter(has_text='P3').locator('strong')).to_contain_text('fixture.wav')
        expect(self.page.locator('.task-list > li').filter(has_text='P3').locator('strong')).not_to_contain_text('重命名 课程')

    def test_generate_subtitles_keeps_archived_video_preselected(self):
        self.with_media = True
        self.parse()
        self.page.get_by_role('tab', name='AI 分析', exact=True).click()
        self.page.get_by_role('button', name='先生成字幕', exact=True).click()
        self.page.wait_for_load_state('networkidle')
        expect(self.page.get_by_role('radiogroup', name='选择视频', exact=True).get_by_role('radio')).to_be_checked()
        expect(self.page.get_by_role('group', name='平台字幕获取确认', exact=True)).to_be_visible()

    def test_generate_subtitles_preserves_pending_source_after_refresh(self):
        self.parse()
        self.page.get_by_role('tab', name='AI 分析', exact=True).click()
        self.page.get_by_role('button', name='先生成字幕', exact=True).click()
        self.page.wait_for_load_state('networkidle')
        expect(self.page.get_by_role('radiogroup', name='选择视频', exact=True).get_by_role('radio')).to_be_checked()
        expect(self.page.get_by_role('group', name='平台字幕获取确认', exact=True)).to_be_visible()

    def test_all_subtitles_uses_catalogued_parts_not_their_count(self):
        self.parts = [3, 4]
        self.archived = {3, 4}
        panel = self.subtitle_panel()
        # Every part archived: no per-episode summary and no disabled "已有字幕" button, only one hint.
        expect(panel.locator('.subtitle-inventory')).to_have_count(0)
        panel.get_by_label('下载范围', exact=True).select_option('all')
        self.expect_nothing_to_download(panel)
        self.assertFalse(any(path == '/subtitles' for _, path, _ in self.requests))

    def expect_nothing_to_download(self, panel):
        expect(panel.get_by_text('所选分集已有可用平台字幕，无需重复下载；可选择批量范围补全缺失分集。', exact=True)).to_be_visible()
        expect(panel.get_by_role('button', name='确认下载', exact=True)).to_be_disabled()
        self.assertEqual(panel.inner_text().count('已有校验通过'), 1)

    def test_subtitle_inventory_and_duplicate_guard(self):
        panel = self.subtitle_panel()
        self.expect_nothing_to_download(panel)
        expect(panel.locator('.subtitle-inventory')).to_have_count(0)
        self.fit('subtitles')
        panel.get_by_label('下载范围', exact=True).select_option('custom')
        panel.get_by_label('指定集数', exact=True).fill('1,3')
        self.expect_nothing_to_download(panel)
        self.assertFalse(any(path == '/subtitles' for _, path, _ in self.requests))
        panel.get_by_label('指定集数', exact=True).fill('1-3')
        panel.get_by_role('button', name='确认下载', exact=True).click()
        expect(panel.get_by_role('status')).to_have_text('本次字幕获取已完成。')
        self.expect_nothing_to_download(panel)
        expect(panel.locator('.subtitle-inventory')).to_have_count(0)
        self.assertEqual(sum(path == '/subtitles' for _, path, _ in self.requests), 1)

    def test_invalid_subtitle_range_is_not_reported_as_already_archived(self):
        panel = self.subtitle_panel()
        panel.get_by_label('下载范围', exact=True).select_option('custom')
        panel.get_by_label('指定集数', exact=True).fill('3,5-2')
        button = panel.get_by_role('button', name='确认下载', exact=True)
        expect(button).to_be_disabled()
        expect(panel.get_by_text('请输入有效集数范围，例如 1-3,8。', exact=True)).to_be_visible()
        self.assertFalse(any(path in {'/subtitles', '/bili-login/status'} for _, path, _ in self.requests))

    def test_task_facets_search_counts_and_recovery_after_clear(self):
        self.page.goto(self.url + '?page=tasks')
        expect(self.page.locator('.task-list > li')).to_have_count(4)
        filters = self.page.get_by_role('group', name='任务筛选', exact=True)
        for label, count in [('全部任务', 4), ('进行中', 1), ('已完成', 2), ('未完成', 1)]:
            expect(filters.get_by_role('button', name=re.compile('^' + label)).locator('.chip-count')).to_have_text(str(count))
        self.fit('tasks')
        search = self.page.get_by_role('searchbox', name='搜索任务', exact=True)
        search.fill('p3')
        expect(self.page.locator('.task-list > li')).to_have_count(1)
        expect(filters.get_by_role('button', name=re.compile('^全部任务')).locator('.chip-count')).to_have_text('1')
        expect(filters.get_by_role('button', name=re.compile('^进行中')).locator('.chip-count')).to_have_text('0')
        search.fill('bbbbbbbb')
        expect(self.page.locator('.task-list > li')).to_have_count(1)
        expect(self.page.locator('.task-list > li strong')).to_contain_text('P1')
        search.press('Escape')
        expect(self.page.locator('.task-list > li')).to_have_count(4)
        self.jobs = [self.jobs[0], self.jobs[2]]
        expect(filters.get_by_role('button', name=re.compile('^全部任务')).locator('.chip-count')).to_have_text('2', timeout=8000)
        kinds = self.page.get_by_role('group', name='按类型筛选', exact=True)
        kinds.get_by_role('button', name=re.compile('^平台字幕')).click()
        expect(self.page.locator('.task-list > li')).to_have_count(1)
        filters.get_by_role('button', name=re.compile('^清理失败记录')).click()
        self.page.get_by_role('button', name='确认清理', exact=True).click()
        expect(self.page.locator('.task-list > li')).to_have_count(0)
        # Clearing the selected type must not remove the only control that resets it.
        expect(kinds.get_by_role('button', name=re.compile('^全部类型'))).to_be_visible()
        kinds.get_by_role('button', name=re.compile('^全部类型')).click()
        expect(self.page.locator('.task-list > li')).to_have_count(1)


if __name__ == '__main__':
    unittest.main(verbosity=2)
