"""Regression for real user feedback: empty notes and chapters without a separate summary.

The browser uses the real availability predicate with synthetic dependencies;
TestClient/offline tests independently cover file-backed create/answer behavior.
No platform, credential, model or real course write is permitted.
"""
import unittest
from urllib.parse import parse_qs, urlsplit
from unittest.mock import patch
from playwright.sync_api import expect
from video_parser.services import conversations
from tests.browser import browser_ux_improvements as ux


def fixture_type():
    Parent = ux.fixture_type()
    class Fixture(Parent):
        def setUp(self):
            super().setUp()
            self.notes = {}
            self.subtitles = True
            self.sessions = []
            self.records = [{'id': '1' * 32, 'created_at': '', 'template': 'chapters', 'template_name': '章节时间轴',
                'source': 'transcript', 'source_label': '字幕', 'prompt': '', 'model': 'fixture', 'provider': 'custom',
                'transcript_label': '原字幕', 'transcript_merged': False, 'video_resource': self.resource,
                'frame_count': 0, 'frames': [], 'usage': {}, 'text': '## [00:00] 已有章节\n本讲概念与学习目标。\n## [00:00.3] 第二章\n应用与练习。',
                'followups': [], 'legacy': False}]

        def readiness(self, resource):
            transcripts = [{'resource': 'subtitles/fixture.srt'}] if self.subtitles else []
            with patch.object(conversations.library, 'get', return_value={}), patch.object(conversations.analysis, 'video_path'), \
                 patch.object(conversations.analysis, 'transcript_sources', return_value=transcripts), \
                 patch.object(conversations.analysis, 'records', return_value=self.records):
                return conversations.availability('fixture', resource)

        def route(self, route):
            url = urlsplit(route.request.url)
            if url.hostname == '127.0.0.1' and url.port == self.server.server_port:
                path, method = url.path, route.request.method
                if path.endswith('/analyses') and method == 'GET':
                    return route.fulfill(json={'records': self.records})
                if path.endswith('/conversations/context') and method == 'GET':
                    resource = parse_qs(url.query)['resource'][0]
                    return route.fulfill(json=self.readiness(resource))
                if (path.endswith('/conversations') and method == 'GET') or (path.endswith('/conversations/migrate') and method == 'POST'):
                    return route.fulfill(json={'conversations': self.sessions})
                if path.endswith('/conversations') and method == 'POST':
                    self.requests.append((method, path.removeprefix('/api/workspace'), route.request.post_data_json))
                    if not self.readiness(route.request.post_data_json['resource'])['available']:
                        return route.fulfill(status=422, json={'detail': '没有对话所需资料'})
                    value = {'id': 'f' * 32, 'title': '新对话', 'video_resource': self.resource, 'origin_record': None,
                             'agent': {'engine': 'api', 'model': '', 'effort': ''}, 'messages': [], 'created_at': '', 'updated_at': ''}
                    self.sessions.append(value)
                    return route.fulfill(json={'conversation': value})
                if '/study/notes' in path:
                    if method == 'GET':
                        return route.fulfill(json={'notes': list(self.notes.values())})
                    if method == 'POST' and not path.endswith('/delete'):
                        body = route.request.post_data_json
                        self.assertEqual(body['resource'], self.resource)
                        identifier = path.rsplit('/', 1)[1]
                        self.assertRegex(identifier, '^[a-f0-9]{32}$')
                        self.notes[identifier] = {'id': identifier, 'time': body['time'], 'text': body['text'].strip(),
                                                  'revision': body['expected_revision'] + 1}
                        return route.fulfill(json=self.notes[identifier])
            return super().route(route)
    return Fixture


class LearningFeedbackBrowserTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.Fixture = fixture_type()
        cls.Fixture.setUpClass()

    @classmethod
    def tearDownClass(cls):
        cls.Fixture.tearDownClass()

    def setUp(self):
        self.f = self.Fixture('test_settings_cross_tab_updates')
        self.f.setUp()
        self.page = self.f.page

    def tearDown(self):
        self.f.tearDown()

    def learn(self):
        self.f.parse()
        self.page.get_by_role('button', name='学习视图', exact=True).click()
        expect(self.page.locator('.study-sidebar')).to_be_visible()

    def test_empty_notes_can_create_save_edit_and_restore(self):
        self.learn()
        self.page.get_by_role('button', name='切换到深色主题', exact=True).click()
        self.page.get_by_role('tab', name='个人笔记', exact=True).click()
        panel = self.page.locator('#study-panel-notes')
        expect(panel).to_contain_text('还没有个人笔记')
        create = panel.get_by_role('button', name='记下当前时刻', exact=True)
        expect(create).to_be_enabled()
        self.page.screenshot(path=str(self.f.output / 'feedback-notes-empty.png'), full_page=False)
        create.click()
        editor = panel.locator('textarea')
        expect(editor).to_be_focused()
        editor.fill('从空白笔记开始的测试内容')
        expect(panel.locator('.timestamp-notes-list')).to_contain_text('从空白笔记开始的测试内容')
        panel.get_by_role('button', name='关闭编辑', exact=True).click()
        panel.get_by_role('button', name='编辑 00:00 的笔记', exact=True).click()
        editor.fill('修改后的内容仍可保存')
        expect(panel.locator('.timestamp-notes-list')).to_contain_text('修改后的内容仍可保存')
        self.page.reload()
        self.page.get_by_role('tab', name='个人笔记', exact=True).click()
        expect(panel.locator('.timestamp-notes-list')).to_contain_text('修改后的内容仍可保存')
        expect(panel.get_by_role('button', name='记下当前时刻', exact=True)).to_be_enabled()

    def test_existing_chapters_without_summary_allows_chat_and_no_duplicate_header(self):
        self.learn()
        expect(self.page.locator('.chapter-timeline-player')).to_be_visible()
        expect(self.page.locator('.video-context-actions').get_by_role('button', name='AI 对话', exact=True)).to_have_count(0)
        self.page.get_by_role('tab', name='AI 对话', exact=True).click()
        chat = self.page.locator('#study-panel-chat')
        expect(chat.locator('#ai-chat-question')).to_be_enabled()
        expect(chat.get_by_role('button', name='准备学习材料', exact=True)).to_have_count(0)
        expect(chat.get_by_role('button', name='生成章节时间轴和总结', exact=True)).to_have_count(0)
        chat.locator('#ai-chat-question').fill('直接使用已有章节提问，不补做摘要')
        expect(chat.get_by_role('button', name='提问', exact=True)).to_be_enabled()
        self.page.screenshot(path=str(self.f.output / 'feedback-chapters-chat.png'), full_page=False)
        self.assertFalse(any(method == 'POST' and (path.startswith('/ai/') or path.endswith('/analysis-task'))
                             for method, path, _ in self.f.requests))

    def test_first_question_from_empty_chat_uses_chapters_without_summary(self):
        self.learn()
        self.page.get_by_role('tab', name='AI 对话', exact=True).click()
        panel = self.page.locator('#study-panel-chat')
        expect(panel.locator('.ai-chat-session-heading strong')).to_have_count(0)
        expect(panel.locator('.ai-chat-log > li')).to_have_count(0)
        field = panel.locator('#ai-chat-question')
        field.fill('第一次对已有章节提问')
        panel.get_by_role('button', name='提问', exact=True).click()
        # Answer transport is deliberately mocked as 503; prove creation/submission and recovery without inference.
        expect(panel.get_by_role('alert').filter(has_text='合成的发送失败')).to_be_visible()
        expect(field).to_have_value('第一次对已有章节提问')
        self.assertEqual(len(self.f.sessions), 1)
        self.assertTrue(any(path.endswith('/ask-task') for _, path, _ in self.f.requests))

    def test_no_material_still_explains_and_never_generates_implicitly(self):
        self.f.records = []
        self.learn()
        self.page.get_by_role('tab', name='AI 对话', exact=True).click()
        chat = self.page.locator('#study-panel-chat')
        expect(chat).to_contain_text('已找到字幕；还需生成章节时间轴或内容摘要后才能对话。')
        expect(chat.get_by_role('button', name='准备学习材料', exact=True)).to_be_visible()
        expect(chat.locator('textarea')).to_have_count(0)

    def test_subtitles_missing_does_not_request_chapters_again(self):
        self.f.subtitles = False
        self.learn()
        self.page.get_by_role('tab', name='AI 对话', exact=True).click()
        chat = self.page.locator('#study-panel-chat')
        expect(chat).to_contain_text('当前视频缺少字幕')
        expect(chat.get_by_role('button', name='生成字幕', exact=True)).to_be_visible()
        expect(chat.get_by_role('button', name='准备学习材料', exact=True)).to_have_count(0)


if __name__ == '__main__':
    unittest.main(verbosity=2)
