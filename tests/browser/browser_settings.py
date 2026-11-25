"""Focused browser checks for settings accessibility, persistence, and mobile fit."""
import copy
import os
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import threading
import time

from playwright.sync_api import sync_playwright, expect
import uvicorn

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))


def check_quality_selection():
    with tempfile.TemporaryDirectory(prefix='settings-ts-') as output:
        subprocess.run(['node', str(ROOT / 'frontend/node_modules/typescript/bin/tsc'),
            'src/features/settings/settings.ts', '--ignoreConfig', '--target', 'es2022', '--module', 'esnext', '--outDir', output,
            '--skipLibCheck'], cwd=ROOT / 'frontend', check=True)
        module = Path(output) / 'features/settings/settings.js'
        script = r"""import fs from 'node:fs'; import assert from 'node:assert/strict';
const source = fs.readFileSync(process.argv[1], 'utf8');
const { preferredQualityId, normalizeSettings } = await import('data:text/javascript;base64,' + Buffer.from(source).toString('base64'));
const q = (id, height) => ({ id, label: id, height, width: null, codec: null, media: null });
for (const preference of ['best','1080p','720p','480p','360p']) {
  assert.equal(normalizeSettings({qualityPreference: preference}).qualityPreference, preference);
}
for (const invalid of ['240p','480P',480,null,{},[]]) {
  assert.equal(normalizeSettings({qualityPreference: invalid}).qualityPreference, 'best');
}
const all = [q('1080',1080),q('720',720),q('480',480),q('360',360)];
assert.equal(preferredQualityId(all, '1080', '480p'), '480');
assert.equal(preferredQualityId(all, '1080', '360p'), '360');
assert.equal(preferredQualityId([q('720',720),q('360',360)], '720', '480p'), '360');
assert.equal(preferredQualityId([q('720',720),q('480',480)], '720', '360p'), '480');
assert.equal(preferredQualityId([q('unknown',null)], 'unknown', '360p'), 'unknown');
assert.equal(preferredQualityId([], null, '480p'), '');
assert.equal(preferredQualityId([q('720',720)], '720', '1080p'), '720');
assert.equal(preferredQualityId([q('1080',1080)], '1080', '720p'), '1080');
assert.equal(preferredQualityId([q('unknown',null)], 'unknown', '720p'), 'unknown');
assert.equal(preferredQualityId([], 'missing', 'best'), '');
assert.equal(preferredQualityId([q('first',720),q('second',720)], 'second', '720p'), 'first');"""
        subprocess.run(['node', '--input-type=module', '-e', script, str(module)], cwd=ROOT, check=True)


def check_ai_model_controls(browser, base_url):
    """Keep cached discovery failures passive; test only user-selected API targets."""
    context = browser.new_context(viewport={'width': 1280, 'height': 900})
    context.add_init_script("localStorage.setItem('vp-language','zh'); if (!localStorage.getItem('video-parser:agent-preferences')) localStorage.setItem('video-parser:agent-preferences', JSON.stringify({enabled:null,target:{engine:'api',model:'',effort:''}}))")
    cached_error = '缓存读取错误：不应自动显示'
    discovery_error = '主动读取失败：请检查本机登录'
    catalog = {'engines': [
        {'id': engine, 'name': name, 'installed': True, 'enabled': engine != 'claude', 'discovered': True,
         'models': [{'id': engine + '-fixture', 'label': engine + ' fixture', 'efforts': [], 'images': False}],
         'commands': [], 'error': cached_error, 'updated_at': 1}
        for engine, name in [('claude', 'Claude Code'), ('codex', 'Codex'), ('opencode', 'OpenCode'), ('dsh', 'DeepSeek Harness')]],
        'permission': 'restricted-learning'}
    ai = {'provider': 'custom', 'provider_name': '其他 OpenAI 兼容服务', 'base_url': 'https://fixture.invalid/v1',
          'model': 'saved-fixture', 'key_saved': True, 'key_hint': '已保存', 'price_input': None, 'price_output': None,
          'configured': True, 'local': False, 'consented': True, 'source': 'settings', 'storage': 'fixture only',
          'presets': {'custom': {'name': '其他 OpenAI 兼容服务', 'base_url': '', 'model': '', 'key_required': False,
                                 'models': [], 'hint': ''},
                      'modelscope': {'name': '魔搭社区 ModelScope', 'base_url': 'https://unused.invalid/v1',
                                     'model': 'never-selected', 'key_required': True, 'models': ['never-selected'], 'hint': ''}}}
    ai['providers'] = [{**ai, 'id': 'fixture-provider', 'name': 'Fixture supplier', 'models': ['saved-fixture']}]
    ai['active_provider_id'] = 'fixture-provider'
    discoveries, toggles, tests, unexpected_writes = [], [], [], []
    errors = []

    def route_api(route):
        request = route.request
        path = request.url.split('/api/workspace', 1)[1].split('?', 1)[0]
        if path == '/config':
            route.fulfill(json={'platforms': [], 'ai_configured': True, 'media_ready': True, 'subtitles_ready': True,
                                'transcription_ready': True, 'transcription_upload_limit': 1024, 'archive_root': 'fixture', 'ai': ai})
        elif path == '/ai/settings' and request.method == 'GET':
            route.fulfill(json=ai)
        elif path == '/ai/agents' and request.method == 'GET':
            route.fulfill(json=catalog)
        elif path == '/ai/agents/enabled' and request.method == 'POST':
            body = request.post_data_json
            toggles.append(body)
            next(item for item in catalog['engines'] if item['id'] == body['engine'])['enabled'] = body['enabled']
            route.fulfill(json=catalog)
        elif path == '/ai/agents/discover' and request.method == 'POST':
            body = request.post_data_json
            discoveries.append(body)
            assert body == {'confirm': True, 'engine': 'claude', 'model': ''}, body
            engine = next(item for item in catalog['engines'] if item['id'] == 'claude')
            assert engine['enabled'], 'Discovery must never run while disabled'
            engine['error'] = discovery_error
            route.fulfill(json=catalog)
        elif path == '/ai/settings/test' and request.method == 'POST':
            body = request.post_data_json
            tests.append(copy.deepcopy(body))
            route.fulfill(json={'results': [{'id': target['id'], 'provider': target['provider'], 'model': target['model'],
                                            'ok': True, 'latency_ms': 1, 'error': ''} for target in body['targets']]})
        elif request.method != 'GET':
            unexpected_writes.append((request.method, path))
            route.fulfill(status=409, json={'detail': 'Browser fixture refuses real writes.'})
        elif path == '/ai/embedding/settings':
            route.fulfill(json={'base_url': '', 'model': '', 'key_saved': False, 'configured': False})
        else:
            route.continue_()

    context.route('**/api/workspace/**', route_api)
    page = context.new_page()
    page.on('pageerror', lambda error: errors.append(str(error)))

    def no_discovery(expected, stage):
        page.wait_for_load_state('networkidle')
        assert len(discoveries) == expected, f'{stage}: expected {expected} explicit discovery calls, got {discoveries}'

    def open_ai():
        page.get_by_role('button', name='设置', exact=True).click()
        page.get_by_role('button', name='AI 模型', exact=True).click()
        expect(page.get_by_role('switch', name='Claude Code', exact=True)).to_be_enabled()

    try:
        page.goto(base_url)
        no_discovery(0, 'application mount')
        open_ai()
        no_discovery(0, 'opening AI settings')
        default_model = page.locator('#learning-default-model')
        expect(default_model.locator('optgroup[label="Claude Code"]')).to_have_count(0)
        expect(default_model.locator('option[value="codex|codex-fixture"]')).to_have_count(1)
        expect(page.get_by_text(cached_error, exact=True)).to_have_count(0)
        claude = page.locator('section.model-source').filter(has=page.locator('#source-claude'))
        codex = page.locator('section.model-source').filter(has=page.locator('#source-codex'))
        claude.get_by_role('button', name='管理 Claude Code 模型', exact=True).click()
        codex.get_by_role('button', name='管理 Codex 模型', exact=True).click()
        no_discovery(0, 'opening disabled and enabled engine panels')
        expect(page.get_by_text(cached_error, exact=True)).to_have_count(0)
        read = claude.get_by_role('button', name='读取本机模型', exact=True)
        expect(read).to_be_disabled()
        for width in (1280, 390):
            page.set_viewport_size({'width': width, 'height': 900})
            default = claude.get_by_role('checkbox', name='跟随工具默认模型', exact=True)
            show_id = claude.get_by_role('checkbox', name='显示调用 ID', exact=True)
            first, second = default.bounding_box(), show_id.bounding_box()
            assert first and second, 'Alignment controls must be visible'
            assert abs(first['x'] - second['x']) <= 1, f'{width}px checkbox alignment: {first["x"]} != {second["x"]}'
        page.set_viewport_size({'width': 1280, 'height': 900})
        switch = claude.get_by_role('switch', name='Claude Code', exact=True)
        switch.check()
        expect(read).to_be_enabled()
        no_discovery(0, 'enabling engine')
        expect(page.get_by_text(cached_error, exact=True)).to_have_count(0)
        read.click()
        expect(page.get_by_text(discovery_error, exact=True).first).to_be_visible()
        no_discovery(1, 'explicit failed discovery')
        switch.uncheck()
        expect(read).to_be_disabled()
        expect(page.get_by_text(discovery_error, exact=True)).to_have_count(0)
        no_discovery(1, 'disabling engine after failure')
        page.reload()
        open_ai()
        no_discovery(1, 'reload while disabled')
        expect(page.get_by_text(discovery_error, exact=True)).to_have_count(0)
        expect(page.get_by_text(cached_error, exact=True)).to_have_count(0)
        claude.get_by_role('button', name='管理 Claude Code 模型', exact=True).click()
        switch.check()
        expect(read).to_be_enabled()
        no_discovery(1, 'enabling cached failed engine')
        expect(page.get_by_text(discovery_error, exact=True)).to_have_count(0)
        read.click()
        expect(page.get_by_text(discovery_error, exact=True).first).to_be_visible()
        no_discovery(2, 'second explicit discovery')
        page.reload()
        open_ai()
        expect(page.get_by_role('switch', name='Claude Code', exact=True)).to_be_checked()
        expect(page.get_by_text(discovery_error, exact=True)).to_have_count(0)
        expect(page.get_by_text(cached_error, exact=True)).to_have_count(0)
        no_discovery(2, 'reload while enabled with cached failure')

        page.get_by_role('button', name='编辑供应商 Fixture supplier', exact=True).click()
        models = page.locator('.ai-provider-models')
        expect(page.locator('.ai-test-targets')).to_have_count(0)
        expect(page.get_by_role('button', name='测试选中模型', exact=True)).to_have_count(0)
        expect(page.get_by_role('button', name='测试全部', exact=True)).to_be_enabled()
        expect(models.get_by_role('button', name='测试模型 saved-fixture', exact=True)).to_be_visible()
        expect(models.get_by_role('button', name='移除模型 saved-fixture', exact=True)).to_be_visible()
        page.get_by_role('button', name='测试模型 saved-fixture', exact=True).click()
        expect(page.get_by_role('status', name='模型测试结果', exact=True)).to_be_visible()
        assert len(tests) == 1 and len(tests[0]['targets']) == 1, tests
        saved_target = tests[0]['targets'][0]
        assert saved_target['provider_id'] == 'fixture-provider' and saved_target['model'] == 'saved-fixture', tests
        page.get_by_role('button', name='移除模型 saved-fixture', exact=True).click()
        page.get_by_label('手动添加模型 ID', exact=True).fill('draft-fixture')
        page.get_by_role('button', name='添加模型', exact=True).click()
        page.get_by_role('button', name='测试模型 draft-fixture', exact=True).click()
        expect(page.get_by_role('status', name='模型测试结果', exact=True)).to_contain_text('draft-fixture')
        assert len(tests) == 2, tests
        assert [target['model'] for target in tests[-1]['targets']] == ['draft-fixture'], tests
        assert tests[-1]['targets'][0]['id'] != saved_target['id'], tests
        assert all(target['provider_id'] == 'fixture-provider' for target in tests[-1]['targets']), tests
        page.get_by_role('button', name='移除模型 draft-fixture', exact=True).click()
        page.get_by_label('手动添加模型 ID', exact=True).fill('saved-fixture')
        page.get_by_role('button', name='添加模型', exact=True).click()
        page.get_by_role('button', name='测试全部', exact=True).click()
        no_discovery(2, 'testing API models')
        assert toggles == [{'engine': 'claude', 'enabled': True}, {'engine': 'claude', 'enabled': False},
                           {'engine': 'claude', 'enabled': True}], toggles
        assert unexpected_writes == [], unexpected_writes

        codex.get_by_role('button', name='管理 Codex 模型', exact=True).click()
        codex_model = codex.get_by_role('checkbox', name='codex fixture', exact=True)
        codex_default = codex.get_by_role('checkbox', name='跟随工具默认模型', exact=True)
        codex_model.uncheck()
        expect(default_model.locator('option[value="codex|codex-fixture"]')).to_have_count(0)
        expect(codex_model).to_be_visible()
        codex_default.uncheck()
        expect(default_model.locator('optgroup[label="Codex"]')).to_have_count(0)
        codex_model.check()
        expect(default_model.locator('option[value="codex|codex-fixture"]')).to_have_count(1)
        expect(default_model.locator('option[value="codex|"]')).to_have_count(0)
        default_model.select_option('codex|codex-fixture')
        codex_switch = codex.get_by_role('switch', name='Codex', exact=True)
        codex_switch.uncheck()
        expect(default_model.locator('optgroup[label="Codex"]')).to_have_count(0)
        expect(default_model).to_have_value('')
        stored_target = page.evaluate("JSON.parse(localStorage.getItem('video-parser:agent-preferences')).target")
        assert stored_target['engine'] == 'codex' and stored_target['model'] == 'codex-fixture', stored_target
        codex_switch.check()
        expect(default_model).to_have_value('codex|codex-fixture')
        page.reload()
        open_ai()
        expect(default_model).to_have_value('codex|codex-fixture')
        expect(default_model.locator('option[value="codex|"]')).to_have_count(0)
        no_discovery(2, 'filtering visible default models')
        assert unexpected_writes == [], unexpected_writes
        assert not errors, errors
    finally:
        context.close()


def main():
    check_quality_selection()
    with tempfile.TemporaryDirectory(prefix='workspace-settings-') as directory:
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
                check_ai_model_controls(browser, f'http://127.0.0.1:{port}')
                if '--ai-models-only' in sys.argv:
                    browser.close()
                    print('AI settings browser checks passed: passive inventory, explicit discovery, error visibility, checkbox alignment, and eligible target selection; all configuration writes mocked.')
                    return
                page = browser.new_page(viewport={'width': 1280, 'height': 900})
                page.add_init_script("localStorage.setItem('video-parser:last-project','keep-project'); localStorage.setItem('video-parser:last-quality','keep-quality'); if (!sessionStorage.getItem('settings-corrupt-tested')) { localStorage.setItem('video-parser:settings','{bad json'); sessionStorage.setItem('settings-corrupt-tested','1'); }")
                errors = []
                page.on('pageerror', lambda error: errors.append(str(error)))
                page.goto(f'http://127.0.0.1:{port}')
                button = page.get_by_role('button', name='设置', exact=True)
                button.click()
                settings_page = page.get_by_test_id('page-settings')
                expect(settings_page).to_be_visible()
                expect(page.get_by_role('dialog', name='设置', exact=True)).to_have_count(0)
                expect(page.locator('.sidebar-settings').get_by_role('button', name='设置', exact=True)).to_have_attribute('aria-current', 'page')
                expect(page.get_by_label('默认画质', exact=True)).to_have_value('best')
                page.get_by_label('默认画质', exact=True).select_option('720p')
                page.get_by_label('归档完成提醒', exact=True).check()
                page.locator('.app-sidebar').get_by_role('button', name='视频工作台', exact=True).click()
                expect(settings_page).to_be_hidden()
                expect(page.locator('.app-sidebar').get_by_role('button', name='视频工作台', exact=True)).to_be_focused()
                page.reload()
                button = page.get_by_role('button', name='设置', exact=True)
                button.click()
                expect(page.get_by_label('默认画质', exact=True)).to_have_value('720p')
                expect(page.get_by_label('归档完成提醒', exact=True)).to_be_checked()
                page.get_by_role('button', name='恢复常用默认', exact=True).click()
                expect(page.get_by_label('默认画质', exact=True)).to_have_value('best')
                expect(page.get_by_label('归档完成提醒', exact=True)).not_to_be_checked()
                assert page.evaluate("localStorage.getItem('video-parser:last-project')") == 'keep-project'
                assert page.evaluate("localStorage.getItem('video-parser:last-quality')") == 'keep-quality'
                expect(page.locator('.archive-root')).to_contain_text(str(Path(directory) / 'library'))
                page.set_viewport_size({'width': 390, 'height': 844})
                assert page.evaluate('document.documentElement.scrollWidth <= innerWidth'), 'Settings overflow at 390px'
                page.set_viewport_size({'width': 1280, 'height': 900})
                page.get_by_role('button', name='常用设置', exact=True).click()
                page.locator('.language-switch label', has_text='English').click()
                expect(page.get_by_role('radio', name='English', exact=True)).to_be_checked()
                expect(page.locator('html')).to_have_attribute('lang', 'en')
                expect(page.get_by_role('heading', name='Interface language', exact=True)).to_be_visible()
                expect(page.get_by_label('Default quality', exact=True)).to_have_value('best')
                expect(page.locator('.app-sidebar').get_by_role('button', name='Video workbench', exact=True)).to_be_visible()
                assert page.evaluate("localStorage.getItem('vp-language')") == 'en'
                page.reload()
                page.get_by_role('button', name='Settings', exact=True).click()
                expect(page.get_by_role('radio', name='English', exact=True)).to_be_checked()
                expect(page.get_by_role('button', name='Restore general defaults', exact=True)).to_be_visible()
                assert page.title() == 'Video Parser · Video workbench', page.title()
                leftover = page.evaluate(r"""() => [...document.querySelectorAll('.app-sidebar *, .settings-page *')]
                    .filter(node => node.offsetParent && !node.closest('[translate="no"]')
                        && [...node.childNodes].some(child => child.nodeType === 3 && /[\u3400-\u9fff]/.test(child.data)))
                    .map(node => node.textContent.trim())""")
                assert leftover == [], leftover
                page.locator('.language-switch label', has_text='中文').click()
                expect(page.locator('html')).to_have_attribute('lang', 'zh-CN')
                expect(page.get_by_label('默认画质', exact=True)).to_have_value('best')
                assert page.evaluate("localStorage.getItem('vp-language')") == 'zh'
                assert not errors, errors
                blocked = browser.new_page()
                blocked.add_init_script("const original=Storage.prototype.setItem; Storage.prototype.setItem=function(key,...args){if(key==='video-parser:settings') throw new DOMException('blocked','QuotaExceededError'); return original.call(this,key,...args)}")
                blocked.goto(f'http://127.0.0.1:{port}')
                blocked.get_by_role('button', name='设置', exact=True).click()
                blocked.get_by_label('默认画质', exact=True).select_option('1080p')
                expect(blocked.locator('.settings-save-status')).to_contain_text('当前页已生效，关闭或刷新后可能丢失')
                assert not errors, errors
                browser.close()
        finally:
            server.should_exit = True
            thread.join(timeout=10)
            sock.close()
    print('Settings browser checks passed: malformed preference fallback, save/reload/reset isolation, sidebar/page keyboard focus and grouped maintenance, archive path, 390px layout, and the English/Chinese interface switch.')


if __name__ == '__main__':
    main()
