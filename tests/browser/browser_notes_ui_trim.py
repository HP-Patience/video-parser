"""Only the three requested UI changes; real courses and business APIs are never used."""
import io
import json
import re
import unittest
import wave
from urllib.parse import quote, urlsplit
from playwright.sync_api import expect
from tests.browser import browser_learning_feedback as feedback
from tests.browser import browser_product_improvements as harness


class NotesUiTrimTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        Parent = feedback.fixture_type()
        class Fixture(Parent):
            def setUp(self):
                super().setUp(); self.fail_notes = False
                audio = io.BytesIO()
                with wave.open(audio, 'wb') as clip:
                    clip.setnchannels(1); clip.setsampwidth(2); clip.setframerate(8000)
                    clip.writeframes(b'\0\0' * 8000 * 20)
                self.audio = audio.getvalue()
            def media(self):
                value = super().media()
                if value: value['duration'] = 20
                return value
            def route(self, route):
                url = urlsplit(route.request.url); path = url.path
                if url.hostname != '127.0.0.1' or url.port != self.server.server_port:
                    return super().route(route)
                if path == '/product-fixture.wav':
                    data = self.audio; headers = {'Content-Type': 'audio/wav', 'Accept-Ranges': 'bytes'}
                    match = re.fullmatch(r'bytes=(\d+)-(\d*)', route.request.headers.get('range', ''))
                    if match:
                        start = int(match[1]); end = min(int(match[2]) if match[2] else len(data)-1, len(data)-1)
                        if start > end:
                            return route.fulfill(status=416, headers={'Content-Range': f'bytes */{len(data)}'}, body=b'')
                        headers['Content-Range'] = f'bytes {start}-{end}/{len(data)}'
                        return route.fulfill(status=206, headers=headers, body=data[start:end+1])
                    return route.fulfill(headers=headers, body=data)
                if route.request.method == 'POST' and '/study/notes/' in path and self.fail_notes:
                    return route.fulfill(status=503, json={'detail': '模拟保存失败，草稿保留'})
                if route.request.method == 'POST' and path.endswith('/study/progress'):
                    body = route.request.post_data_json
                    return route.fulfill(json={'position': body['position'], 'duration': body['duration'], 'revision': body['expected_revision'] + 1})
                return super().route(route)
        cls.Fixture = Fixture
        Fixture.setUpClass()

    @classmethod
    def tearDownClass(cls):
        cls.Fixture.tearDownClass()

    def setUp(self):
        self.f = self.Fixture('test_settings_cross_tab_updates'); self.f.setUp()
        self.page = self.f.page; self.dialogs = []
        def dialog(value):
            self.dialogs.append(value.message); value.dismiss()
        self.page.on('dialog', dialog)

    def tearDown(self):
        self.assertEqual(self.dialogs, [], 'Closing editing must not invoke a native dialog')
        self.f.tearDown()

    def notes(self):
        self.f.parse()
        self.page.get_by_role('button', name='学习视图', exact=True).click()
        expect(self.page.locator('.study-sidebar')).to_be_visible()
        expect(self.page.locator('.playback-resume').get_by_role('button', name='记下当前时刻', exact=True)).to_have_count(0)
        self.page.get_by_role('tab', name='个人笔记', exact=True).click()
        self.panel = self.page.locator('#study-panel-notes')
        self.create = self.panel.get_by_role('button', name='记下当前时刻', exact=True)
        expect(self.create).to_be_enabled()

    def test_timestamp_action_is_inside_notes_and_uses_current_time(self):
        self.notes()
        self.page.wait_for_function('document.querySelector("video").readyState >= 1')
        self.page.locator('video').evaluate('v=>{v.pause();v.currentTime=7}')
        self.page.wait_for_function('!document.querySelector("video").seeking && document.querySelector("video").currentTime >= 6.95')
        self.create.click()
        editor = self.panel.locator('textarea'); expect(editor).to_be_focused()
        editor.fill('时间点笔记')
        expect(self.panel.locator('.timestamp-notes-list')).to_contain_text('时间点笔记')
        self.assertAlmostEqual(next(iter(self.f.notes.values()))['time'], 7, delta=.05)

    def test_typing_without_pressing_the_button_saves_at_the_current_time(self):
        # The box is always on screen now, so the first keystroke is enough to start a note.
        self.notes()
        self.page.wait_for_function('document.querySelector("video").readyState >= 1')
        self.page.locator('video').evaluate('v=>{v.pause();v.currentTime=7}')
        self.page.wait_for_function('!document.querySelector("video").seeking && document.querySelector("video").currentTime >= 6.95')
        self.panel.locator('textarea').fill('直接写下的笔记')
        expect(self.panel.locator('.timestamp-notes-list')).to_contain_text('直接写下的笔记')
        self.assertAlmostEqual(next(iter(self.f.notes.values()))['time'], 7, delta=.05)

    def test_close_without_popup_keeps_failed_save_and_restores_after_reload(self):
        self.notes(); self.f.fail_notes = True; self.create.click()
        self.panel.locator('textarea').fill('关闭也不能丢失的草稿')
        self.panel.get_by_role('button', name='关闭编辑', exact=True).click()
        # A failed save keeps the draft inside the always-visible box instead of hiding it.
        expect(self.panel.locator('textarea')).to_have_value('关闭也不能丢失的草稿')
        expect(self.panel.get_by_role('alert')).to_contain_text('模拟保存失败')
        key = 'video-parser:study:' + harness.PROJECT_ID + ':' + quote(self.f.resource, safe='') + ':notes'
        draft = self.page.evaluate('(key)=>JSON.parse(localStorage.getItem(key))', key)
        self.assertEqual(draft['text'], '关闭也不能丢失的草稿')
        self.page.reload()
        self.page.get_by_role('tab', name='个人笔记', exact=True).click()
        expect(self.panel.locator('textarea')).to_have_value('关闭也不能丢失的草稿')

    def test_saved_editor_stays_closed_and_another_note_can_start(self):
        self.notes(); self.create.click()
        self.panel.locator('textarea').fill('第一条保留')
        self.panel.get_by_role('button', name='关闭编辑', exact=True).click()
        expect(self.panel.locator('.timestamp-notes-list')).to_contain_text('第一条保留')
        expect(self.panel.locator('textarea')).to_have_value('')
        self.create.click()
        expect(self.panel.locator('textarea')).to_have_value('')
        self.panel.locator('textarea').fill('第二条保留')
        expect(self.panel.locator('.timestamp-notes-list li')).to_have_count(2)

    def test_url_bar_removed_without_losing_new_import_entry(self):
        self.f.parse()
        expect(self.page.locator('.workspace-source-editor,.workspace-source-url')).to_have_count(0)
        expect(self.page.get_by_role('textbox', name='视频链接或分享文本')).to_be_visible()
        expect(self.page.get_by_role('textbox', name='视频链接或分享文本')).to_have_value(self.f.parsed()['source_url'])
        count = sum(method == 'POST' and path == '/parse' for method, path, _ in self.f.requests)
        self.page.get_by_role('button', name='清空链接', exact=True).click()
        expect(self.page.get_by_role('textbox', name='视频链接或分享文本')).to_have_value('')
        self.assertEqual(sum(method == 'POST' and path == '/parse' for method, path, _ in self.f.requests), count)
