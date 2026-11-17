"""Update UI regression with fail-closed API fixtures; never installs or quits an app."""
import os
from pathlib import Path
import socket
import tempfile
import threading
import time
from urllib.parse import urlsplit

from playwright.sync_api import expect, sync_playwright
import uvicorn

ROOT = Path(__file__).resolve().parents[2]


def check_updates(browser, base_url):
    context = browser.new_context(viewport={'width': 1280, 'height': 900})
    context.add_init_script("localStorage.setItem('vp-language','zh')")
    installs, external, errors = [], [], []
    progress = {'state': 'idle', 'version': None, 'downloaded_bytes': 0, 'total_bytes': None, 'error': None}
    status = {'current_version': '3.4.1', 'latest_version': '3.5.0', 'release_found': True,
              'available': True, 'can_install': True, 'install_reason': 'portable',
              'release_url': 'https://github.com/HP-Patience/video-parser/releases/tag/v3.5.0'}

    def route_request(route):
        request = route.request
        url = urlsplit(request.url)
        if not request.url.startswith(base_url + '/'):
            external.append(request.url)
            route.abort()
            return
        if not url.path.startswith('/api/'):
            route.continue_()
            return
        path = url.path.removeprefix('/api/workspace')
        if path == '/updates/check':
            route.fulfill(json=status)
        elif path == '/updates/status':
            route.fulfill(json=progress)
        elif path == '/updates/install' and request.method == 'POST':
            installs.append(request.post_data_json)
            progress.update(state='downloading', version='3.5.0', downloaded_bytes=50, total_bytes=100, error=None)
            route.fulfill(json={'started': True, 'version': None, 'state': 'checking'})
        elif path == '/config':
            route.fulfill(json={'version': '3.4.1', 'platforms': [], 'ai_configured': False,
                                'media_ready': True, 'subtitles_ready': True, 'transcription_ready': False,
                                'archive_root': 'fixture', 'transcription_upload_limit': 1024})
        elif path == '/ai/agents':
            route.fulfill(json={'engines': [], 'permission': 'restricted-learning'})
        elif path == '/ai/settings':
            route.fulfill(json={'provider': 'custom', 'provider_name': 'Fixture', 'base_url': '', 'model': '',
                                'configured': False, 'local': False, 'consented': False, 'presets': {}, 'providers': []})
        elif path in ('/projects', '/tasks', '/folders', '/transcriptions/sources'):
            key = {'/projects': 'projects', '/tasks': 'jobs', '/folders': 'folders', '/transcriptions/sources': 'sources'}[path]
            route.fulfill(json={key: []})
        elif request.method == 'GET':
            route.fulfill(json={})
        else:
            route.fulfill(status=409, json={'detail': 'Update browser fixture refuses real writes'})

    context.route('**/*', route_request)
    page = context.new_page()
    page.on('pageerror', lambda error: errors.append(str(error)))
    try:
        page.goto(base_url)
        page.get_by_role('button', name='设置', exact=True).click()
        page.get_by_role('button', name='存储与维护', exact=True).click()
        panel = page.locator('.update-panel')
        panel.get_by_role('button', name='检查更新', exact=True).click()
        expect(panel.get_by_role('link', name='前往发布页')).to_have_count(0)
        panel.get_by_role('button', name='更新到 3.5.0', exact=True).click()
        expect(panel.get_by_role('progressbar')).to_have_attribute('aria-valuenow', '50')
        expect(panel).to_contain_text('正在下载更新')
        expect(panel.get_by_role('button', name='检查更新', exact=True)).to_be_disabled()
        assert installs == [{'confirm': True}], installs
        page.reload()
        page.get_by_role('button', name='设置', exact=True).click()
        page.get_by_role('button', name='存储与维护', exact=True).click()
        expect(panel.get_by_role('progressbar')).to_have_attribute('aria-valuenow', '50')
        assert len(installs) == 1, 'Reload must resume progress, never resubmit install'
        progress.update(state='verifying', downloaded_bytes=100)
        expect(panel).to_contain_text('正在校验更新包')
        progress.update(state='failed', error='测试校验失败，原文件未改动。')
        expect(panel).to_contain_text('测试校验失败，原文件未改动。')
        panel.get_by_role('button', name='检查更新', exact=True).click()
        panel.get_by_role('button', name='更新到 3.5.0', exact=True).click()
        expect(panel.get_by_role('progressbar')).to_have_attribute('aria-valuenow', '50')
        assert len(installs) == 2
        progress.update(state='applying', downloaded_bytes=100)
        expect(panel).to_contain_text('正在安装并重启')
        page.set_viewport_size({'width': 390, 'height': 844})
        assert page.evaluate('document.documentElement.scrollWidth <= innerWidth'), 'Update panel overflow at 390px'
        assert external == [], external
        assert errors == [], errors
    finally:
        context.close()


def main():
    with tempfile.TemporaryDirectory(prefix='workspace-updates-') as directory:
        os.environ['VIDEO_LIBRARY_ROOT'] = str(Path(directory) / 'library')
        from video_parser.web import app as api
        sock = socket.socket()
        sock.bind(('127.0.0.1', 0))
        base_url = f'http://127.0.0.1:{sock.getsockname()[1]}'
        server = uvicorn.Server(uvicorn.Config(api.app, host='127.0.0.1', log_level='error'))
        thread = threading.Thread(target=server.run, kwargs={'sockets': [sock]}, daemon=True)
        thread.start()
        try:
            deadline = time.monotonic() + 15
            while not server.started and thread.is_alive() and time.monotonic() < deadline:
                time.sleep(.05)
            assert server.started
            with sync_playwright() as playwright:
                browser = playwright.chromium.launch(channel='msedge', headless=True)
                try:
                    check_updates(browser, base_url)
                finally:
                    browser.close()
        finally:
            server.should_exit = True
            thread.join(timeout=10)
            sock.close()
    print('Update browser checks passed: one-click progress, reload recovery, failure/retry, applying, no external navigation, mobile fit.')


if __name__ == '__main__':
    main()
