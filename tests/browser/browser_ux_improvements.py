"""Synthetic-only regression tests for the six UX changes. Never contacts real services."""
import json
import os
from urllib.parse import quote, urlsplit
import unittest
from playwright.sync_api import expect
from tests.browser import browser_product_improvements as harness


def fixture_type():
    class Fixture(harness.ProductImprovementsBrowserTests):
        def setUp(self):
            super().setUp()
            self.with_media = True
            self.resource = 'video/fixture.wav'
            self.conversation_id = 'f' * 32

        def media(self):
            value = super().media()
            if value:
                value['resource'] = self.resource
            return value

        def project(self):
            value = super().project()
            value['resources'] = [{'path': self.resource, 'kind': 'video', 'size_bytes': len(self.audio),
                                   'url': '/product-fixture.wav', 'filename': 'fixture.wav'}]
            return value

        def parsed(self):
            value = super().parsed()
            value['episodes'] = [{'part': p, 'title': f'第{p}讲：示例知识点', 'duration': 180,
                                  'archived': p <= 3, 'learned': False, 'project_id': f'{p:032x}' if p <= 3 else None,
                                  'source_url': f'https://www.bilibili.com/video/{harness.VIDEO_ID}?p={p}'}
                                 for p in range(1, 71)]
            return value

        def route(self, route):
            url = urlsplit(route.request.url)
            if url.hostname == '127.0.0.1' and url.port == self.server.server_port:
                path, method = url.path, route.request.method
                if method == 'POST' and path.endswith('/ask-task'):
                    self.requests.append((method, path.removeprefix('/api/workspace'), route.request.post_data_json))
                    return route.fulfill(status=503, json={'detail': '合成的发送失败，请重试。'})
                if path.endswith('/conversations/context') and method == 'GET':
                    return route.fulfill(json={'available': True, 'missing': []})
                if (path.endswith('/conversations') and method == 'GET') or (path.endswith('/conversations/migrate') and method == 'POST'):
                    return route.fulfill(json={'conversations': [{'id': self.conversation_id, 'title': '合成课程讨论',
                        'video_resource': self.resource, 'origin_record': None, 'agent': {'engine': 'api', 'model': '', 'effort': ''},
                        'messages': [{'id': str(i), 'question': '如何理解？', 'answer': '合成回答，仅供测试滚动。' * 100,
                                      'position': 0, 'created_at': ''} for i in range(3)],
                        'created_at': '', 'updated_at': ''}]})
                if '/study/' in path and path.endswith('/notes') and method == 'GET':
                    return route.fulfill(json={'notes': [{'id': str(i) * 32, 'time': 0.1 * i,
                        'text': '合成的个人笔记，应该直接可见。', 'revision': 1} for i in range(1, 4)]})
            return super().route(route)
    return Fixture


class UXImprovementsBrowserTests(unittest.TestCase):
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

    def chat(self):
        self.page.get_by_role('tab', name='AI 对话', exact=True).click()
        field = self.page.locator('#ai-chat-question')
        expect(field).to_be_enabled()
        return field

    def storage_key(self, resource=None):
        return 'video-parser:chat-drafts:v1:' + harness.PROJECT_ID + ':' + quote(resource or self.f.resource, safe='')

    def test_draft_reload_navigation_resource_isolation_and_clear(self):
        self.learn()
        question = self.chat()
        question.fill('课程 A 的未发送问题')
        self.assertEqual(self.page.evaluate('(k)=>JSON.parse(localStorage.getItem(k))', self.storage_key()),
                         {'f' * 32: '课程 A 的未发送问题'})
        self.page.locator('.sidebar-settings button').click()
        self.page.locator('.app-sidebar').get_by_role('button', name='学习视图', exact=True).click()
        expect(question).to_have_value('课程 A 的未发送问题')
        self.page.reload()
        expect(self.chat()).to_have_value('课程 A 的未发送问题')
        self.f.resource = 'video/other.wav'
        self.page.reload()
        expect(self.chat()).to_have_value('')
        question.fill('课程 B 的未发送问题')
        self.f.resource = 'video/fixture.wav'
        self.page.reload()
        expect(self.chat()).to_have_value('课程 A 的未发送问题')
        question.fill('')
        self.assertIsNone(self.page.evaluate('(k)=>localStorage.getItem(k)', self.storage_key()))
        self.page.reload()
        expect(self.chat()).to_have_value('')
        self.assertEqual(self.page.evaluate('(k)=>JSON.parse(localStorage.getItem(k))', self.storage_key('video/other.wav')),
                         {'f' * 32: '课程 B 的未发送问题'})

    def test_conversation_drafts_are_isolated(self):
        self.learn()
        self.chat().fill('对话一草稿')
        self.f.conversation_id = 'd' * 32
        self.page.reload()
        expect(self.chat()).to_have_value('')
        self.chat().fill('对话二草稿')
        self.f.conversation_id = 'f' * 32
        self.page.reload()
        expect(self.chat()).to_have_value('对话一草稿')

    def test_failed_send_restores_persistent_draft(self):
        self.learn()
        self.chat().fill('发送失败应该恢复的问题')
        self.page.locator('#study-panel-chat').get_by_role('button', name='提问', exact=True).click()
        expect(self.page.locator('#study-panel-chat').get_by_role('alert').filter(has_text='合成的发送失败')).to_be_visible()
        expect(self.chat()).to_have_value('发送失败应该恢复的问题')
        self.page.reload()
        expect(self.chat()).to_have_value('发送失败应该恢复的问题')

    def test_draft_storage_failure_keeps_input_and_warns(self):
        self.f.context.add_init_script("const original=Storage.prototype.setItem; Storage.prototype.setItem=function(k,...a){if(k.startsWith('video-parser:chat-drafts:'))throw new DOMException('blocked','QuotaExceededError');return original.call(this,k,...a)}")
        self.learn()
        field = self.chat()
        field.fill('存储失败仍保留在当前页面')
        expect(field).to_have_value('存储失败仍保留在当前页面')
        expect(self.page.get_by_role('status').filter(has_text='草稿暂时无法保存')).to_be_visible()

    def test_malformed_draft_recovers_without_crashing(self):
        self.f.context.add_init_script("if(!sessionStorage.getItem('corrupt-seeded')){localStorage.setItem(" + json.dumps(self.storage_key()) + ",'{broken');sessionStorage.setItem('corrupt-seeded','1')}")
        self.learn()
        expect(self.chat()).to_have_value('')
        self.chat().fill('损坏草稿后的新输入')
        self.page.reload()
        expect(self.chat()).to_have_value('损坏草稿后的新输入')

    def test_notes_chat_scroll_keyboard_and_video_identity(self):
        self.learn()
        self.page.evaluate('window.originalVideo=document.querySelector("video")')
        self.page.get_by_role('tab', name='个人笔记', exact=True).click()
        notes = self.page.locator('.timestamp-notes')
        expect(notes).to_be_visible()
        expect(self.page.locator('.ai-chat')).to_be_hidden()
        self.assertTrue(notes.locator('li').first.evaluate("e=>{const a=e.getBoundingClientRect(),b=e.closest('.study-sidebar-body').getBoundingClientRect();return a.top>=b.top&&a.top<b.bottom}"))
        field = self.chat()
        field.fill('切换标签应保留')
        body = self.page.locator('.study-sidebar-body')
        body.evaluate('e=>e.scrollTop=120')
        self.page.wait_for_function("Math.abs(document.querySelector('.study-sidebar-body').scrollTop-120)<2")
        self.page.wait_for_timeout(80)
        self.page.get_by_role('tab', name='个人笔记', exact=True).click()
        self.assertLess(body.evaluate('e=>e.scrollTop'), 2)
        self.chat()
        self.assertAlmostEqual(body.evaluate('e=>e.scrollTop'), 120, delta=2)
        self.page.get_by_role('tab', name='AI 对话', exact=True).press('Home')
        expect(self.page.get_by_role('tab', name='章节', exact=True)).to_be_focused()
        self.page.keyboard.press('ArrowRight')
        expect(self.page.get_by_role('tab', name='个人笔记', exact=True)).to_be_focused()
        self.page.keyboard.press('End')
        expect(self.page.get_by_role('tab', name='字幕', exact=True)).to_be_focused()
        self.page.keyboard.press('ArrowLeft')
        expect(self.page.get_by_role('tab', name='AI 对话', exact=True)).to_be_focused()
        self.page.get_by_role('button', name='收起学习侧栏', exact=True).click()
        self.page.get_by_role('button', name='展开学习侧栏', exact=True).click()
        self.assertAlmostEqual(body.evaluate('e=>e.scrollTop'), 120, delta=2)
        expect(field).to_have_value('切换标签应保留')
        self.assertTrue(self.page.evaluate('window.originalVideo===document.querySelector("video")'))
        expect(self.page.locator('.video-context-actions').get_by_role('button', name='AI 对话', exact=True)).to_have_count(0)
        expect(self.page.get_by_role('tab', name='AI 对话', exact=True)).to_have_attribute('aria-selected', 'true')

    def test_progress_wording_and_exact_episode_search(self):
        self.learn()
        summary = self.page.locator('.learning-progress-summary')
        expect(summary).to_contain_text('当前播放：P3')
        expect(summary).to_contain_text('下一未完成：P1')
        expect(summary).not_to_contain_text('学到 P1')
        search = self.page.get_by_role('searchbox', name='定位分集', exact=True)
        search.fill('P42')
        expect(self.page.locator('.learning-episode-list > li')).to_have_count(1)
        expect(self.page.locator('.learning-episode-list')).to_contain_text('P42')
        search.fill('2')
        expect(self.page.locator('.learning-episode-list > li')).to_have_count(1)
        expect(self.page.locator('.learning-episode-number')).to_have_text('P2')
        search.fill('不存在的标题')
        expect(self.page.get_by_text('没有匹配分集。', exact=True)).to_be_visible()
        search.press('Escape')
        expect(self.page.locator('.learning-episode-list > li')).to_have_count(70)
        self.assertFalse(any(path.endswith(('/prepare', '/prepare-task')) for _, path, _ in self.f.requests))

    def test_learning_actions_and_narrow_first_screen(self):
        self.learn()
        expect(self.page.locator('.video-context-more')).to_have_count(0)
        expect(self.page.get_by_role('button', name='视频工作台', exact=True)).to_be_visible()
        # The separate find/refresh panel is gone; its reload lives inside the picker popup.
        expect(self.page.locator('.learning-project-tools')).to_have_count(0)
        for width in (1440, 1024, 390, 320, 280):
            self.page.set_viewport_size({'width': width, 'height': 900})
            self.assertTrue(self.page.evaluate('document.documentElement.scrollWidth<=innerWidth'), width)
            if width == 390:
                self.page.evaluate('window.scrollTo(0,0)')
                top = self.page.locator('video').bounding_box()['y']
                self.assertLess(top, 350, ('player first-screen top', top))
                self.page.screenshot(path=str(self.f.output / 'ux-learning-390.png'), full_page=True)

    def test_ai_summaries_advanced_controls_and_unsaved_status(self):
        self.page.goto(self.f.url + '?page=settings&settings_section=ai')
        targets = self.page.locator('.learning-targets')
        expect(targets.get_by_role('combobox', name='默认模型', exact=True)).to_be_visible()
        expect(targets.locator('.learning-target-group').first).to_be_hidden()
        connection = self.page.locator('.settings-ai')
        expect(self.page.locator('#ai-model')).to_be_hidden()
        connection.get_by_role('button', name='配置 API 连接', exact=True).click()
        self.page.locator('#ai-model').fill('not-saved-fixture')
        expect(self.page.locator('.settings-ai')).to_contain_text('有未保存的连接修改')
        connection.get_by_role('button', name='配置 API 连接', exact=True).click()
        expect(self.page.locator('.settings-ai')).to_contain_text('有未保存的连接修改')
        targets.locator('button[aria-controls="models-codex"]').click()
        expect(targets.locator('.learning-target-group')).to_have_count(3)
        targets.locator('#learning-default-model').select_option('codex|codex-fixture')
        expect(targets.locator('#learning-default-model option:checked')).to_contain_text('Codex fixture')
        for width in (1440, 390, 320, 280):
            self.page.set_viewport_size({'width': width, 'height': 900})
            self.assertTrue(self.page.evaluate('document.documentElement.scrollWidth<=innerWidth'), width)
        self.assertFalse(any(method == 'POST' and path in {'/ai/settings', '/ai/runtime/start', '/ai/settings/test', '/ai/agents/discover'}
                             for method, path, _ in self.f.requests))

    def test_enter_in_episode_search_uses_existing_navigation(self):
        self.learn()
        search = self.page.get_by_role('searchbox', name='定位分集', exact=True)
        search.fill('P42')
        with self.page.expect_request(lambda r: r.method == 'POST' and urlsplit(r.url).path.endswith('/parse')) as navigation:
            search.press('Enter')
        self.assertIn('p=42', navigation.value.post_data_json['text'])
        self.assertFalse(any(path.endswith(('/prepare', '/prepare-task')) for _, path, _ in self.f.requests))

    def test_chat_settings_shortcut_opens_configuration_and_focuses_provider(self):
        self.f.ai['configured'] = False
        self.learn()
        self.page.get_by_role('tab', name='AI 对话', exact=True).click()
        self.page.locator('#study-panel-chat').get_by_role('button', name='打开设置', exact=True).click()
        expect(self.page.locator('#ai-provider')).to_be_focused()
        self.assertFalse(any(method == 'POST' and path.startswith('/ai/') for method, path, _ in self.f.requests))

    def test_unconfigured_connection_is_collapsed_without_automatic_save(self):
        self.f.ai['configured'] = False
        self.page.goto(self.f.url + '?page=settings&settings_section=ai')
        expect(self.page.locator('#ai-model')).to_be_hidden()
        self.page.get_by_role('button', name='配置 API 连接', exact=True).click()
        expect(self.page.locator('#ai-model')).to_be_visible()
        self.assertFalse(any(method == 'POST' for method, _, _ in self.f.requests))


if __name__ == '__main__':
    unittest.main(verbosity=2)
