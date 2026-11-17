"""Standalone multi-provider UI regression; all API and external requests are mocked.

Run from the repository root after building frontend/dist:
    $env:PYTHONPATH='backend'; .venv/Scripts/python.exe tests/browser/browser_ai_providers.py
"""
import copy
import os
from pathlib import Path
import re
import socket
import tempfile
import threading
import time
from urllib.parse import urlsplit

from playwright.sync_api import expect, sync_playwright
import uvicorn

ROOT = Path(__file__).resolve().parents[2]
PREFERENCES = 'video-parser:agent-preferences'


def check_providers(browser, base_url, screenshot):
    shared_url = 'https://shared.fixture.invalid/v1'
    cached_error = '缓存读取失败，不应自动展示'
    first_key = 'fixture-first-draft-key'
    providers = [
        {'id': identifier, 'name': name, 'provider': 'custom', 'base_url': shared_url,
         'model': 'shared-model~variant', 'models': ['shared-model~variant'], 'key_saved': True,
         'key_hint': hint, 'configured': True, 'local': False, 'consented': True,
         'price_input': None, 'price_output': None, 'source': 'settings'}
        for identifier, name, hint in [('provider-a', '供应商甲', '甲已保存'),
                                      ('provider-b', '供应商乙', '乙已保存')]]
    original = copy.deepcopy(providers)
    catalog = {'engines': [
        {'id': identifier, 'name': name, 'installed': True, 'enabled': False,
         'discovered': True, 'models': [], 'commands': [], 'error': cached_error, 'updated_at': 1}
        for identifier, name in [('claude', 'Claude Code'), ('codex', 'Codex'),
                                 ('opencode', 'OpenCode'), ('dsh', 'DeepSeek Harness')]],
        'permission': 'restricted-learning'}
    saves, discoveries, tests, deletions, unexpected = [], [], [], [], []
    errors = []

    def settings():
        return {**providers[0], 'provider_name': providers[0]['name'], 'providers': providers,
                'active_provider_id': 'provider-a', 'storage': 'browser fixture only',
                'presets': {'custom': {'name': '其他 OpenAI 兼容服务', 'base_url': '',
                                      'model': '', 'models': [], 'key_required': False, 'hint': ''}}}

    def route_request(route):
        request = route.request
        url = urlsplit(request.url)
        if not request.url.startswith(base_url + '/'):
            unexpected.append(('external', request.url))
            route.abort()
            return
        if not url.path.startswith('/api/'):
            if request.method == 'GET' and (url.path == '/' or url.path.startswith('/assets/') or url.path == '/favicon.ico'):
                route.continue_()
            else:
                unexpected.append((request.method, url.path))
                route.abort()
            return
        path = url.path.removeprefix('/api/workspace')
        method = request.method
        if method == 'GET' and path == '/config':
            route.fulfill(json={'platforms': [], 'ai_configured': True, 'media_ready': True,
                                'subtitles_ready': True, 'transcription_ready': True,
                                'transcription_upload_limit': 1024, 'archive_root': 'fixture'})
        elif method == 'GET' and path == '/ai/settings':
            route.fulfill(json=settings())
        elif method == 'GET' and path == '/ai/agents':
            route.fulfill(json=catalog)
        elif method == 'GET' and path == '/embedding/settings':
            route.fulfill(json={'base_url': '', 'model': '', 'key_saved': False, 'configured': False})
        elif method == 'GET' and path in ('/projects', '/tasks', '/folders', '/transcriptions/sources'):
            field = {'/projects': 'projects', '/tasks': 'jobs', '/folders': 'folders',
                     '/transcriptions/sources': 'sources'}[path]
            route.fulfill(json={field: []})
        elif method == 'POST' and path == '/ai/providers/models':
            discoveries.append(copy.deepcopy(request.post_data_json))
            route.fulfill(json={'models': ['model-a', 'model-b', 'model-a']})
        elif method == 'POST' and path == '/ai/providers':
            body = request.post_data_json
            saves.append(copy.deepcopy(body))
            identifier = body.get('id', 'provider-c')
            previous = next((item for item in providers if item['id'] == identifier), {})
            item = {**previous, **{key: value for key, value in body.items()
                                  if key not in ('api_key', 'clear_key', 'activate')},
                    'id': identifier, 'key_saved': bool(body.get('api_key') or previous.get('key_saved')),
                    'key_hint': 'fixture saved', 'configured': True, 'local': False,
                    'consented': False, 'source': 'settings'}
            providers[:] = [item if current['id'] == identifier else current for current in providers]
            if not previous:
                providers.append(item)
            route.fulfill(json=settings())
        elif method == 'POST' and path == '/ai/settings/test':
            body = copy.deepcopy(request.post_data_json)
            tests.append(body)
            route.fulfill(json={'results': [
                {'id': target['id'], 'provider': target['provider'], 'model': target['model'],
                 'ok': target.get('provider_id') != 'provider-b',
                 'latency_ms': {'provider-a': 11, 'provider-b': 22, 'provider-c': 33}.get(target.get('provider_id'), 99),
                 'error': '第二家连接失败' if target.get('provider_id') == 'provider-b' else ''}
                for target in reversed(body['targets'])]})
        elif method == 'POST' and path == '/ai/providers/provider-c/delete':
            deletions.append((path, request.post_data_json))
            providers[:] = [item for item in providers if item['id'] != 'provider-c']
            route.fulfill(json=settings())
        else:
            # Fail closed: no API, including Agent launch/discovery, reaches the backend.
            unexpected.append((method, path))
            route.fulfill(status=409, json={'detail': '浏览器测试拒绝未声明的请求。'})

    context = browser.new_context(viewport={'width': 1280, 'height': 900}, service_workers='block')
    context.add_init_script("""localStorage.setItem('vp-language','zh');
        if (!localStorage.getItem('video-parser:agent-preferences'))
          localStorage.setItem('video-parser:agent-preferences', JSON.stringify({enabled:null,
            target:{engine:'api',model:'',effort:''}}));""")
    context.route('**/*', route_request)
    page = context.new_page()
    page.on('pageerror', lambda error: errors.append(str(error)))

    def settled():
        page.wait_for_load_state('networkidle')
        assert not unexpected, unexpected
        assert not errors, errors

    def open_ai():
        page.get_by_role('button', name='设置', exact=True).click()
        page.get_by_role('button', name='AI 模型', exact=True).click()
        expect(page.get_by_role('heading', name='模型供应商', level=3, exact=True)).to_be_visible()

    def no_overflow(stage):
        page.set_viewport_size({'width': 390, 'height': 844})
        assert page.evaluate('document.documentElement.scrollWidth <= innerWidth'), f'390px overflow: {stage}'
        page.set_viewport_size({'width': 1280, 'height': 900})

    try:
        page.goto(base_url)
        settled()
        open_ai()
        settled()
        assert not saves and not discoveries and not tests and not deletions
        for heading in ('模型供应商', '本机 Agent 工具', '检索模型'):
            expect(page.get_by_role('heading', name=heading, level=3, exact=True)).to_be_visible()
        expect(page.get_by_text('模型来源', exact=True)).to_have_count(0)
        expect(page.locator('.ai-test-targets')).to_have_count(0)
        expect(page.get_by_role('button', name='测试选中模型', exact=True)).to_have_count(0)
        expect(page.get_by_text(cached_error, exact=True)).to_have_count(0)
        claude = page.locator('section.model-source').filter(has=page.locator('#source-claude'))
        claude.get_by_role('button', name='管理 Claude Code 模型', exact=True).click()
        expect(claude.get_by_role('button', name='读取本机模型', exact=True)).to_be_disabled()
        settled()
        expect(page.get_by_text(cached_error, exact=True)).to_have_count(0)
        default = page.locator('#learning-default-model')
        first = default.locator('optgroup[label="API · 供应商甲"] option')
        second = default.locator('optgroup[label="API · 供应商乙"] option')
        expect(first).to_have_count(1)
        expect(second).to_have_count(1)
        assert 'shared-model~variant' in first.inner_text() and 'shared-model~variant' in second.inner_text()
        first_value, second_value = first.get_attribute('value'), second.get_attribute('value')
        assert first_value and second_value and first_value != second_value
        default.select_option(second_value)
        stored = page.evaluate(f"JSON.parse(localStorage.getItem('{PREFERENCES}')).target")
        assert stored == {'engine': 'api', 'provider_id': 'provider-b', 'model': 'shared-model~variant', 'effort': ''}, stored
        page.reload()
        open_ai()
        expect(default).to_have_value(second_value)
        settled()
        assert not discoveries and not saves and not tests
        no_overflow('provider inventory')

        page.get_by_role('button', name='新增供应商', exact=True).click()
        editor = page.locator('#ai-provider-editor')
        expect(editor.locator('.ai-test-targets')).to_have_count(0)
        expect(editor.locator('.ai-test-targets input[type=checkbox]')).to_have_count(0)
        expect(editor.get_by_role('status', name='模型测试结果', exact=True)).to_have_count(0)
        editor.get_by_role('button', name='关闭编辑', exact=True).click()
        expect(page.locator('.ai-test-targets')).to_have_count(0)
        expect(page.get_by_role('button', name='测试选中模型', exact=True)).to_have_count(0)
        page.get_by_role('button', name='新增供应商', exact=True).click()
        expect(page.get_by_role('button', name='新增供应商', exact=True)).to_have_count(0)
        expect(page.get_by_text('尚未添加供应商，请先添加接口和模型。', exact=True)).to_have_count(0)
        expect(editor.locator('.ai-provider-footer > button')).to_have_count(1)
        expect(editor.locator('.ai-provider-advanced')).to_have_count(0)
        expect(editor.get_by_label('输入单价', exact=True)).to_be_visible()
        expect(editor.get_by_label('输出单价', exact=True)).to_be_visible()
        expect(editor.get_by_role('button', name='设为默认 API', exact=True)).to_have_count(0)
        expect(editor.get_by_text('配置保存位置', exact=True)).to_have_count(0)
        expect(editor.locator('.archive-root')).to_have_count(0)
        expect(editor.locator('.ai-provider-connection > legend, .ai-provider-models > legend')).to_have_count(0)
        expect(editor.locator('.ai-provider-model-tools .model-source-state')).to_have_count(0)
        expect(page.locator('.ai-test-targets input[type=checkbox]')).to_have_count(0)
        models = editor.locator('.ai-provider-models')
        expect(models.locator('.ai-test-targets')).to_have_count(0)
        expect(models.locator('.ai-provider-footer > button')).to_have_count(0)
        expect(editor.get_by_role('button', name='保存并测试', exact=True)).to_have_count(0)
        expect(models.locator('.ai-provider-model-add > button')).to_have_count(3)
        model_input = editor.get_by_label('手动添加模型 ID', exact=True).bounding_box()
        fetch_button = editor.get_by_role('button', name='获取模型列表', exact=True).bounding_box()
        assert model_input and fetch_button and abs(model_input['y'] - fetch_button['y']) <= 1, (model_input, fetch_button)
        assert abs(model_input['height'] - fetch_button['height']) <= 1, (model_input, fetch_button)
        connection = editor.locator('.ai-provider-connection').bounding_box()
        model_group = editor.locator('.ai-provider-models').bounding_box()
        assert connection and model_group and connection['y'] + connection['height'] <= model_group['y'], (connection, model_group)
        assert abs(connection['x'] - model_group['x']) <= 1 and abs(connection['width'] - model_group['width']) <= 1, (connection, model_group)
        editor.screenshot(path=str(screenshot.with_name('provider-editor-desktop.png')))
        page.set_viewport_size({'width': 390, 'height': 844})
        connection = editor.locator('.ai-provider-connection').bounding_box()
        model_group = editor.locator('.ai-provider-models').bounding_box()
        assert connection and model_group and connection['y'] + connection['height'] <= model_group['y'], (connection, model_group)
        assert page.evaluate('document.documentElement.scrollWidth <= innerWidth'), '390px empty editor overflow'
        editor.screenshot(path=str(screenshot.with_name('provider-editor-mobile.png')))
        page.set_viewport_size({'width': 1280, 'height': 900})
        editor.get_by_label('供应商名称', exact=True).fill('供应商丙')
        editor.get_by_label('接口地址', exact=True).fill('https://third.fixture.invalid/v1')
        editor.get_by_label('API Key', exact=True).fill('fixture-third-key')
        settled()
        assert not discoveries, 'Opening/filling a provider must not fetch models'
        editor.get_by_role('button', name='获取模型列表', exact=True).click()
        discovery = editor.locator('.ai-provider-discovery')
        expect(discovery.locator('legend')).to_have_count(0)
        background = discovery.locator(':scope > ul').evaluate("node => getComputedStyle(node).backgroundColor")
        assert background not in ('transparent', 'rgba(0, 0, 0, 0)'), background
        expect(discovery.get_by_role('checkbox')).to_have_count(2)
        assert discoveries == [{'provider': 'custom', 'base_url': 'https://third.fixture.invalid/v1',
                                'api_key': 'fixture-third-key'}], discoveries
        added = editor.locator('.ai-provider-models')
        expect(added.get_by_role('radio')).to_have_count(0)
        discovery.get_by_role('checkbox', name='model-a', exact=True).check()
        discovery.get_by_role('checkbox', name='model-b', exact=True).check()
        expect(added.get_by_role('radio')).to_have_count(0)
        editor.get_by_role('button', name='添加选中模型（2）', exact=True).click()
        expect(added.get_by_role('radio')).to_have_count(2)
        expect(added.locator(':scope > ul').get_by_text('model-a', exact=True)).to_have_count(1)
        expect(added.locator(':scope > ul').get_by_text('model-b', exact=True)).to_have_count(1)
        radio = added.get_by_role('radio').first.bounding_box()
        assert radio and radio['width'] <= 20 and radio['height'] <= 20, radio
        expect(editor.get_by_role('button', name='设为默认 API', exact=True)).to_have_count(0)
        expect(editor.locator('.archive-root')).to_have_count(0)
        no_overflow('model discovery editor')
        editor.get_by_role('button', name='保存供应商', exact=True).click()
        expect(page.get_by_role('heading', name='供应商丙', level=4, exact=True)).to_be_visible()
        assert len(saves) == 1, saves
        assert saves[0]['models'] == ['model-a', 'model-b'] and saves[0]['model'] == 'model-a', saves
        assert saves[0]['activate'] is False and 'id' not in saves[0], saves
        assert saves[0]['name'] == '供应商丙' and saves[0]['api_key'] == 'fixture-third-key', saves
        assert providers[:2] == original, 'Adding a provider must preserve both existing providers'
        expect(default.locator('optgroup[label="API · 供应商丙"] option')).to_have_count(2)
        expect(default).to_have_value(second_value)

        page.get_by_role('button', name='编辑供应商 供应商甲', exact=True).click()
        editor.get_by_label('API Key', exact=True).fill(first_key)
        page.get_by_role('button', name='编辑供应商 供应商乙', exact=True).click()
        expect(editor.get_by_label('API Key', exact=True)).to_have_value('')
        expect(editor.get_by_label('API Key', exact=True)).to_have_attribute('placeholder', re.compile('乙已保存'))
        editor.get_by_role('button', name='获取模型列表', exact=True).click()
        expect(discovery.get_by_role('checkbox')).to_have_count(2)
        assert len(discoveries) == 2 and discoveries[-1] == {
            'id': 'provider-b', 'provider': 'custom', 'base_url': shared_url, 'api_key': None}, discoveries
        editor.get_by_label('供应商名称', exact=True).fill('供应商乙编辑')
        editor.get_by_label('手动添加模型 ID', exact=True).fill('second-model~variant')
        editor.get_by_role('button', name='添加模型', exact=True).click()
        targets = models = editor.locator('.ai-provider-models')
        expect(page.locator('.ai-model-tests')).to_have_count(0)
        expect(targets).to_be_visible()
        expect(targets.locator(':scope > ul > li .ai-provider-model-actions button[aria-label^="测试模型"]')).to_have_count(2)
        expect(targets.locator(':scope > ul > li .ai-provider-model-actions button[aria-label^="测试模型"]')).to_have_count(2)
        results = page.get_by_role('status', name='模型测试结果', exact=True)
        editor.get_by_role('button', name='测试模型 shared-model~variant', exact=True).click()
        expect(results.locator('li')).to_have_count(1)
        assert len(tests) == 1, tests
        editor.get_by_role('button', name='测试全部', exact=True).click()
        expect(results.locator('li')).to_have_count(2)
        assert len(tests) == 2, tests
        payload = tests[1]['targets']
        ids = [target['id'] for target in payload]
        assert len(ids) == len(set(ids)) and all(re.fullmatch(r'target-\d+', identifier) for identifier in ids), ids
        assert [target['provider_id'] for target in payload] == ['provider-b', 'provider-b'], payload
        assert [target['model'] for target in payload] == ['shared-model~variant', 'second-model~variant'], payload
        assert all(not target.get('api_key') for target in payload), payload
        assert first_key not in str(payload), 'The first provider draft key leaked into another provider'
        rows = results.locator('li')
        for index, model in enumerate(['shared-model~variant', 'second-model~variant']):
            expect(rows.nth(index)).to_contain_text('供应商乙编辑（未保存）')
            expect(rows.nth(index).locator('code')).to_have_text(model)
            expect(rows.nth(index).locator('strong')).to_have_text('失败')
            expect(rows.nth(index)).to_contain_text('延迟 22 毫秒')
        expect(rows.nth(1)).to_contain_text('第二家连接失败')
        editor.get_by_label('供应商名称', exact=True).fill('供应商乙再编辑')
        expect(results).to_have_count(0)
        editor.get_by_role('button', name='测试全部', exact=True).click()
        expect(results).to_contain_text('供应商乙再编辑（未保存）')
        assert len(tests) == 3 and [target['id'] for target in tests[2]['targets']] == ids, tests
        assert len(saves) == 1, 'Batch testing must not save or activate the draft'
        no_overflow('batch test results')
        page.screenshot(path=str(screenshot), full_page=True)

        page.get_by_role('button', name='编辑供应商 供应商甲', exact=True).click()
        expect(results).to_have_count(0)
        expect(models.locator(':scope > ul > li .ai-provider-model-actions button[aria-label^="测试模型"]')).to_have_count(1)
        editor.get_by_role('button', name='关闭编辑', exact=True).click()
        page.get_by_role('button', name='新增供应商', exact=True).click()
        expect(results).to_have_count(0)
        expect(models.locator(':scope > ul > li .ai-provider-model-actions button[aria-label^="测试模型"]')).to_have_count(0)
        expect(editor.get_by_label('接口地址', exact=True)).to_have_value('')
        expect(editor.get_by_label('API Key', exact=True)).to_have_value('')
        expect(editor.get_by_label('API Key', exact=True)).to_have_attribute('autocomplete', 'new-password')
        editor.get_by_label('供应商名称', exact=True).fill('全新供应商')
        editor.get_by_label('接口地址', exact=True).fill('https://new.fixture.invalid/v1')
        editor.get_by_label('API Key', exact=True).fill('fixture-new-key')
        for model in ['new-first', 'new-second']:
            editor.get_by_label('手动添加模型 ID', exact=True).fill(model)
            editor.get_by_role('button', name='添加模型', exact=True).click()
        expect(targets.locator(':scope > ul > li .ai-provider-model-actions button[aria-label^="测试模型"]')).to_have_count(2)
        expect(editor.get_by_role('button', name='测试全部', exact=True)).to_be_enabled()
        editor.get_by_role('button', name='测试全部', exact=True).click()
        expect(results.locator('li')).to_have_count(2)
        assert [target['model'] for target in tests[-1]['targets']] == ['new-first', 'new-second'], tests
        assert all('provider_id' not in target and target['api_key'] == 'fixture-new-key' for target in tests[-1]['targets']), tests
        expect(results).to_contain_text('全新供应商（未保存）')
        editor.get_by_label('接口地址', exact=True).fill('https://changed.fixture.invalid/v1')
        expect(results).to_have_count(0)
        expect(editor.get_by_label('API Key', exact=True)).to_have_value('')
        expect(editor.get_by_role('button', name='测试全部', exact=True)).to_be_disabled()

        page.get_by_role('button', name='删除供应商 供应商丙', exact=True).click()
        confirmation = page.get_by_role('group', name='确认删除 供应商丙', exact=True)
        confirmation.get_by_role('button', name='取消', exact=True).click()
        settled()
        assert deletions == [], deletions
        expect(page.get_by_role('heading', name='供应商丙', level=4, exact=True)).to_be_visible()
        page.get_by_role('button', name='删除供应商 供应商丙', exact=True).click()
        confirmation.get_by_role('button', name='确认删除', exact=True).click()
        expect(page.get_by_role('heading', name='供应商丙', level=4, exact=True)).to_have_count(0)
        assert deletions == [('/ai/providers/provider-c/delete', {})], deletions
        for name in ('供应商甲', '供应商乙'):
            expect(page.get_by_role('heading', name=name, level=4, exact=True)).to_be_visible()
        assert providers == original, providers
        expect(default).to_have_value(second_value)
        expect(page.get_by_text(cached_error, exact=True)).to_have_count(0)
        settled()
        assert len(discoveries) == 2 and len(saves) == 1 and len(tests) == 4
    finally:
        context.close()


def main():
    assert (ROOT / 'frontend/dist/index.html').is_file(), 'Build frontend/dist before running this script'
    with tempfile.TemporaryDirectory(prefix='workspace-ai-providers-') as directory:
        previous_library = os.environ.get('VIDEO_LIBRARY_ROOT')
        os.environ['VIDEO_LIBRARY_ROOT'] = str(Path(directory) / 'library')
        from video_parser.web import app as api
        sock = socket.socket()
        sock.bind(('127.0.0.1', 0))
        port = sock.getsockname()[1]
        server = uvicorn.Server(uvicorn.Config(api.app, host='127.0.0.1', port=port, log_level='error'))
        thread = threading.Thread(target=server.run, kwargs={'sockets': [sock]}, daemon=True)
        thread.start()
        try:
            deadline = time.monotonic() + 15
            while not server.started and thread.is_alive() and time.monotonic() < deadline:
                time.sleep(.05)
            assert server.started, 'FastAPI failed to start'
            with sync_playwright() as playwright:
                browser = playwright.chromium.launch(headless=True)
                try:
                    check_providers(browser, f'http://127.0.0.1:{port}', Path(directory) / 'providers-1280.png')
                finally:
                    browser.close()
        finally:
            server.should_exit = True
            thread.join(timeout=10)
            sock.close()
            if previous_library is None:
                os.environ.pop('VIDEO_LIBRARY_ROOT', None)
            else:
                os.environ['VIDEO_LIBRARY_ROOT'] = previous_library
    print('AI provider browser checks passed: grouped persistent defaults, explicit model discovery/addition, '
          'non-activating multi-model save, isolated saved keys, stable test IDs with reversed results, '
          'cancel/confirm deletion, passive Agent inventory, and 390px layout; all APIs mocked.')


if __name__ == '__main__':
    main()
