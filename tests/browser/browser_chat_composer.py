"""The Q&A list owns its scrolling; the composer stays at the rail's bottom edge.

Synthetic sessions only: no model call, workspace write or download leaves this fixture.
"""
import os
from pathlib import Path
import unittest
from playwright.sync_api import expect
from tests.browser import browser_ux_improvements as ux

GEOMETRY = '''() => {
  const log = document.querySelector('.ai-chat-log');
  const form = document.querySelector('#study-panel-chat .ai-chat-form');
  const body = document.querySelector('.study-sidebar-body');
  const box = element => { const value = element.getBoundingClientRect(); return { top: value.top, bottom: value.bottom, height: value.height }; };
  return {
    aligned: document.querySelector('.study-sidebar').hasAttribute('data-aligned'),
    log: box(log), form: box(form), body: box(body),
    logOverflowY: getComputedStyle(log).overflowY,
    logClient: log.clientHeight, logScroll: log.scrollHeight, logTop: log.scrollTop,
    lastItemBottom: log.lastElementChild.getBoundingClientRect().bottom,
    bodyTop: body.scrollTop,
    horizontalOverflow: document.documentElement.scrollWidth > innerWidth,
  };
}'''


class ChatComposerLayoutTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.Fixture = ux.fixture_type()
        cls.Fixture.setUpClass()

    @classmethod
    def tearDownClass(cls):
        cls.Fixture.tearDownClass()

    def setUp(self):
        self.f = self.Fixture('test_settings_cross_tab_updates')
        self.f.setUp()
        self.page = self.f.page
        self.f.parse()
        self.page.get_by_role('button', name='学习视图', exact=True).click()
        self.page.get_by_role('tab', name='AI 对话', exact=True).click()
        expect(self.page.locator('.ai-chat-log > li')).to_have_count(3)

    def tearDown(self):
        self.f.tearDown()

    def geometry(self):
        return self.page.evaluate(GEOMETRY)

    def assert_composer_at_bottom(self, data):
        self.assertGreaterEqual(data['form']['top'], data['log']['bottom'] - 1, data)
        self.assertAlmostEqual(data['form']['bottom'], data['body']['bottom'], delta=1, msg=data)

    def test_message_list_scrolls_under_a_pinned_composer(self):
        out = Path(os.environ.get('VIDEO_PARSER_TEST_OUTPUT', '.local/chat-composer'))
        out.mkdir(parents=True, exist_ok=True)
        data = self.geometry()
        self.assertTrue(data['aligned'], data)
        self.assertIn(data['logOverflowY'], ('auto', 'scroll'), data)
        self.assertGreater(data['logScroll'], data['logClient'], data)
        self.assertGreaterEqual(data['logTop'] + data['logClient'], data['logScroll'] - 1, data)
        self.assertLessEqual(data['lastItemBottom'], data['log']['bottom'] + 1, data)
        self.assertLess(data['bodyTop'], 2, data)
        self.assert_composer_at_bottom(data)
        self.page.locator('.study-sidebar').screenshot(path=str(out / 'chat-composer-1440.png'))
        # Narrow layouts scroll the shared sidebar body instead; the composer is still visible by default.
        for width in (1024, 390, 280):
            self.page.set_viewport_size({'width': width, 'height': 900})
            self.page.get_by_role('tab', name='章节', exact=True).click()
            self.page.get_by_role('tab', name='AI 对话', exact=True).click()
            data = self.geometry()
            self.assertFalse(data['aligned'], (width, data))
            self.assertFalse(data['horizontalOverflow'], (width, data))
            self.assertEqual(data['logClient'], data['logScroll'], (width, data))
            self.assert_composer_at_bottom(data)
            self.assertGreaterEqual(data['form']['top'], data['body']['top'] - 1, (width, data))
        self.page.set_viewport_size({'width': 1440, 'height': 1000})

    def test_returning_to_the_chat_tab_shows_the_latest_answer(self):
        self.page.locator('.ai-chat-log').evaluate('log => { log.scrollTop = 0; }')
        self.assertLess(self.page.locator('.ai-chat-log').evaluate('log => log.scrollTop'), 2)
        self.page.get_by_role('tab', name='章节', exact=True).click()
        self.page.get_by_role('tab', name='AI 对话', exact=True).click()
        self.page.wait_for_function("() => { const log = document.querySelector('.ai-chat-log'); return Math.abs(log.scrollTop + log.clientHeight - log.scrollHeight) <= 1; }")
        data = self.geometry()
        self.assertLessEqual(data['lastItemBottom'], data['log']['bottom'] + 1, data)
        self.assert_composer_at_bottom(data)
