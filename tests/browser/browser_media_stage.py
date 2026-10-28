"""Unified cover/progress/player UI regression. Isolated mock APIs; no real downloads or inference."""
import base64
import io
import json
import os
import threading
import time
import wave
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit
from playwright.sync_api import sync_playwright, expect

ROOT = Path(__file__).resolve().parents[2]
PROJECT = 'e' * 32


def main():
    dist = Path(os.environ.get('VIDEO_PARSER_TEST_DIST', ROOT / 'frontend/dist'))
    assert (dist / 'index.html').is_file()
    state = {'job': None, 'count': 0, 'archived': False, 'requests': [], 'unexpected': []}
    image = 'data:image/svg+xml;base64,' + base64.b64encode(b'<svg xmlns="http://www.w3.org/2000/svg" width="800" height="450"><rect width="800" height="450" fill="#203328"/><text x="240" y="230" fill="white" font-size="32">VIDEO COVER</text></svg>').decode()
    sound = io.BytesIO()
    with wave.open(sound, 'wb') as clip:
        clip.setnchannels(1); clip.setsampwidth(2); clip.setframerate(8000); clip.writeframes(b'\0\0' * 8000 * 20)
    media = {'resource': 'video/fixture.wav', 'quality_id': '1080p', 'preview_url': '/media-fixture.wav',
             'download_url': '/media-fixture.wav', 'filename': 'fixture.wav', 'duration': 20,
             'width': None, 'height': None, 'size_bytes': len(sound.getvalue())}
    project = {'id': PROJECT, 'title': '统一视频区域测试', 'platform': '哔哩哔哩', 'part': 1,
               'location': 'fixture/P1', 'identity_status': 'verified', 'resources': [], 'archive_url': '/fixture.zip'}

    def parsed():
        return {'id': PROJECT, 'project': project, 'title': project['title'], 'platform': '哔哩哔哩',
                'source_url': 'https://www.bilibili.com/video/FIXTURE', 'cover_url': image, 'cover_download_url': image,
                'default_quality_id': '1080p', 'qualities': [{'id': '1080p', 'label': '1080P', 'media': media if state['archived'] else None}],
                'duration': 20, 'episodes': [], 'warning': None}

    class Handler(SimpleHTTPRequestHandler):
        def __init__(self, *args, **kwargs): super().__init__(*args, directory=str(dist), **kwargs)
        def log_message(self, *_): pass

    server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True); thread.start()

    def api(route):
        request = route.request
        path = urlsplit(request.url).path.split('/api/workspace', 1)[-1]
        state['requests'].append((request.method, path))
        if path == '/config': return route.fulfill(json={'platforms': ['哔哩哔哩'], 'ai_configured': False, 'media_ready': True, 'transcription_ready': True, 'subtitles_ready': True})
        if path == '/parse' or path == f'/projects/{PROJECT}/resume': return route.fulfill(json=parsed())
        if path == '/tasks': return route.fulfill(json={'jobs': [state['job']] if state['job'] else []})
        if path.endswith('/subtitle-cues'): return route.fulfill(json={'sources': [], 'resource': '', 'cues': []})
        if path == '/embedding/settings': return route.fulfill(json={'base_url': '', 'model': '', 'key_saved': False, 'configured': False})
        if path == '/ai/agents': return route.fulfill(json={'permission': 'restricted-learning', 'engines': []})
        if path == f'/projects/{PROJECT}/conversations/context' and request.method == 'GET':
            return route.fulfill(json={'available': False, 'missing': ['subtitles', 'chapters', 'summary']})
        if path == f'/projects/{PROJECT}/conversations/migrate' and request.method == 'POST':
            assert request.post_data_json == {}
            return route.fulfill(json={'conversations': []})
        if path == '/transcriptions/sources': return route.fulfill(json={'sources': []})
        if path == '/projects': return route.fulfill(json={'projects': []})
        if path == '/folders': return route.fulfill(json={'folders': []})
        if path.endswith('/analyses'): return route.fulfill(json={'records': []})
        if path.endswith('/analysis/context'): return route.fulfill(json={'transcripts': [], 'templates': [], 'duration': 20, 'auto_frame_count': 4, 'image_tokens': 800, 'prompt_tokens': 700, 'output_tokens': 1000, 'sources': {}})
        if path.endswith('/study/progress'): return route.fulfill(json={'revision': 0, 'position': 0, 'duration': 20})
        if path.endswith('/study/notes'): return route.fulfill(json={'notes': []})
        if path.endswith('/prepare-task'):
            assert request.post_data_json == {'quality_id': '1080p'}
            state['count'] += 1
            state['job'] = {'id': f'{state["count"]:032x}', 'kind': 'video', 'project_id': PROJECT,
                            'state': 'queued', 'phase': 'queued', 'message': '等待下载队列', 'title': project['title'], 'result': None}
            return route.fulfill(status=202, json=state['job'])
        job = state['job']
        if job and path == '/tasks/' + job['id']: return route.fulfill(json=job)
        if job and path == '/tasks/video/' + job['id'] + '/cancel':
            job.update(state='cancelling', phase='cancelling', message='正在停止下载')
            return route.fulfill(json=job)
        state['unexpected'].append((request.method, path))
        return route.fulfill(status=400, json={'detail': 'Unexpected test request'})

    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True, channel=os.environ.get('VIDEO_PARSER_BROWSER_CHANNEL'))
            page = browser.new_page(viewport={'width': 1440, 'height': 1000})
            errors = []; page.on('pageerror', lambda error: errors.append(str(error)))
            page.route('**/api/workspace/**', api)
            page.route('**/media-fixture.wav', lambda route: route.fulfill(content_type='audio/wav', body=sound.getvalue()))
            page.goto(f'http://127.0.0.1:{server.server_port}', wait_until='networkidle')
            page.get_by_role('textbox', name='视频链接或分享文本', exact=True).fill('https://www.bilibili.com/video/FIXTURE')
            page.get_by_role('button', name='解析视频', exact=True).click()
            frame = page.locator('#video-stage')
            expect(frame).to_have_attribute('data-state', 'cover')
            expect(frame.locator('img')).to_be_visible()
            assert page.get_by_role('tab', name='视频预览', exact=True).count() == 0
            assert page.locator('.media-stage').count() == 1
            page.evaluate('window.originalMediaStage = document.querySelector("#video-stage")')
            before = frame.bounding_box()
            page.get_by_role('button', name='下载并归档', exact=True).click()
            expect(frame).to_have_attribute('data-state', 'progress')
            expect(frame.locator('img')).to_have_count(0)
            expect(frame).to_contain_text('等待下载队列')
            assert not frame.get_by_role('progressbar').count()

            def update(**kwargs): state['job'].update(**kwargs)
            def phase(name, message):
                update(state='running', phase=name, message=message)
                expect(frame.locator('.download-preview-message')).to_have_text(message)

            phase('downloading', '未知总大小，正在下载')
            assert not frame.get_by_role('progressbar').count()
            update(progress={'bytes': 1024})
            expect(frame.get_by_role('progressbar')).to_be_visible()
            assert frame.get_by_role('progressbar').get_attribute('aria-valuenow') is None
            update(progress={'bytes': 16*1024*1024, 'total_bytes': 100*1024*1024, 'track': 1, 'tracks': 2,
                             'speed_bytes_per_second': 2*1024*1024, 'eta_seconds': 42, 'sampled_at': time.time()})
            expect(frame.get_by_role('progressbar')).to_have_attribute('aria-valuenow', '16')
            expect(frame).to_contain_text('轨道 1/2')
            expect(frame).to_contain_text('MB/s')
            expect(frame).to_contain_text('剩余')
            update(progress={'bytes': 50, 'total_bytes': 100, 'track': 2, 'tracks': 2})
            expect(frame).to_contain_text('轨道 2/2')
            for width in (1920, 1440, 1171, 1170, 1024, 760, 390, 320, 280):
                page.set_viewport_size({'width': width, 'height': 900})
                assert page.evaluate('document.documentElement.scrollWidth<=innerWidth'), width
                expect(frame.get_by_role('button', name='取消下载', exact=True)).to_be_enabled()
            page.set_viewport_size({'width': 1440, 'height': 1000})
            phase('merging', '合并音视频中')
            assert not frame.get_by_role('progressbar').count(), 'Do not show stale byte percentage during merging'
            phase('archiving', '校验并归档中')
            assert not frame.get_by_role('progressbar').count()
            out = ROOT / '.local'; out.mkdir(exist_ok=True)
            frame.screenshot(path=str(out / 'unified-media-progress.png'))
            update(state='failed', phase='failed', message='模拟下载失败，请重试')
            expect(frame).to_have_attribute('data-state', 'failure')
            expect(frame.get_by_role('alert')).to_have_text('模拟下载失败，请重试')
            expect(frame.locator('img')).to_have_count(0)
            frame.get_by_role('button', name='重新下载并归档', exact=True).click()
            expect(frame).to_have_attribute('data-state', 'progress')
            frame.get_by_role('button', name='取消下载', exact=True).click()
            expect(frame.get_by_role('button', name='正在取消…', exact=True)).to_be_disabled()
            update(state='cancelled', phase='cancelled', message='下载已取消，未完成归档')
            expect(frame).to_have_attribute('data-state', 'failure')
            expect(frame.locator('.download-preview-heading')).to_have_text('下载已取消')
            frame.get_by_role('button', name='重新下载并归档', exact=True).click()
            expect(frame).to_have_attribute('data-state', 'progress')
            state['archived'] = True
            update(state='completed', phase='completed', message='已完成归档', result=media)
            expect(frame).to_have_attribute('data-state', 'player')
            expect(frame.locator('video')).to_be_visible()
            # The custom bar hides as soon as the pointer leaves the video area (paused included).
            controls = frame.locator('.video-controls')
            box = frame.bounding_box()
            center = (box['x'] + box['width'] / 2, box['y'] + box['height'] / 2)
            page.mouse.move(*center); page.wait_for_timeout(250)
            assert 'is-hidden' not in (controls.get_attribute('class') or ''), 'hovering the video must show the bar'
            page.mouse.move(2, 2); page.wait_for_timeout(350)
            assert 'is-hidden' in (controls.get_attribute('class') or ''), 'leaving the video must hide the bar'
            page.mouse.move(*center); page.wait_for_timeout(250)
            assert 'is-hidden' not in (controls.get_attribute('class') or ''), 'coming back must show the bar again'
            assert page.evaluate('window.originalMediaStage===document.querySelector("#video-stage")')
            after = frame.bounding_box()
            assert abs(before['height'] - after['height']) < 1 and abs(before['width'] - after['width']) < 1, (before, after)
            expect(page.get_by_role('complementary', name='学习侧栏', exact=True)).to_be_hidden()
            page.get_by_role('button', name='学习视图', exact=True).click()
            expect(page.get_by_role('complementary', name='学习侧栏', exact=True)).to_be_visible()
            assert state['count'] == 3
            page.reload(wait_until='networkidle')
            expect(frame).to_have_attribute('data-state', 'player')
            assert state['count'] == 3, 'Reload must not download again'
            assert not errors and not state['unexpected'], (errors, state['unexpected'])
            assert not any('analyze' in path for _, path in state['requests'])
            print('PASS: unified frame cover/queue/unknown or multi-track progress/merge/archive/failure/cancel/retry/player/reload; nine widths, stable frame, no AI')
            browser.close()
    finally:
        server.shutdown(); server.server_close(); thread.join(timeout=5)


if __name__ == '__main__': main()
