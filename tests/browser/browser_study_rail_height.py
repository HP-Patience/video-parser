"""The rail's bottom edge: drag the sidebar past the player alignment, and reset to it.

Synthetic sessions only: no model call, workspace write or download leaves this fixture.
"""
import os
from pathlib import Path
import unittest
from playwright.sync_api import expect
from tests.browser import browser_ux_improvements as ux

GEOMETRY = '''() => {
  const q = selector => document.querySelector(selector);
  const box = element => { const value = element.getBoundingClientRect(); return { top: value.top, bottom: value.bottom, left: value.left, right: value.right, width: value.width, height: value.height }; };
  const sidebar = q('.study-sidebar'), resizer = q('.study-sidebar-resizer');
  return {
    sidebar: box(sidebar), resizer: resizer ? box(resizer) : null,
    resizerDisplay: resizer ? getComputedStyle(resizer).display : null,
    body: box(q('.study-sidebar-body')), bar: box(q('.playback-resume')),
    form: box(q('#study-panel-chat .ai-chat-form')), details: box(q('#project-context')),
    aligned: sidebar.hasAttribute('data-aligned'),
    stored: localStorage.getItem('video-parser:study-sidebar-height'),
    overflow: document.documentElement.scrollWidth > innerWidth,
  };
}'''

ALIGNED = "() => { const sidebar = document.querySelector('.study-sidebar').getBoundingClientRect(); const bar = document.querySelector('.playback-resume').getBoundingClientRect(); return Math.abs(sidebar.bottom - bar.bottom) <= 1; }"


class StudySidebarHeightTests(unittest.TestCase):
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
        self.out = Path(os.environ.get('VIDEO_PARSER_TEST_OUTPUT', '.local/sidebar-resizer'))
        self.out.mkdir(parents=True, exist_ok=True)
        self.f.parse()
        self.page.get_by_role('button', name='学习视图', exact=True).click()
        self.page.get_by_role('tab', name='AI 对话', exact=True).click()
        expect(self.page.locator('.study-sidebar-resizer')).to_be_visible()
        expect(self.page.locator('.ai-chat-log > li')).to_have_count(3)

    def tearDown(self):
        self.f.tearDown()

    def geometry(self):
        return self.page.evaluate(GEOMETRY)

    def drag(self, pixels):
        box = self.page.locator('.study-sidebar-resizer').bounding_box()
        x, y = box['x'] + box['width'] / 2, box['y'] + box['height'] / 2
        self.page.mouse.move(x, y)
        self.page.mouse.down()
        self.page.mouse.move(x, y + pixels, steps=6)
        self.page.mouse.up()

    def test_dragging_the_bottom_edge_grows_the_panel(self):
        before = self.geometry()
        self.assertTrue(before['aligned'], before)
        self.assertTrue(self.page.evaluate(ALIGNED), before)
        self.assertAlmostEqual(before['sidebar']['width'], 336, delta=2, msg=before)
        self.assertAlmostEqual(before['resizer']['bottom'], before['sidebar']['bottom'], delta=2, msg=before)
        # A plain click on the grip is not a resize: the rail stays aligned with the player bar.
        self.page.locator('.study-sidebar-resizer').click()
        self.assertIsNone(self.geometry()['stored'], 'a click must not pin the height')
        self.assertTrue(self.page.evaluate(ALIGNED))
        self.drag(150)
        after = self.geometry()
        self.assertAlmostEqual(after['sidebar']['height'] - before['sidebar']['height'], 150, delta=2, msg=after)
        self.assertGreater(after['body']['height'], before['body']['height'], after)
        self.assertGreater(after['details']['top'], before['details']['top'], after)
        self.assertGreater(after['details']['top'], before['sidebar']['bottom'], after)
        self.assertAlmostEqual(after['form']['bottom'], after['sidebar']['bottom'] - 1, delta=2, msg=after)
        self.assertFalse(after['overflow'], after)
        self.page.locator('.study-sidebar').screenshot(path=str(self.out / 'sidebar-dragged.png'))

    def test_the_floor_and_the_reset_to_alignment(self):
        before = self.geometry()
        self.drag(-400)
        low = self.geometry()
        self.assertAlmostEqual(low['sidebar']['height'], 340, delta=1, msg=low)
        self.assertGreater(low['body']['height'], 0)
        self.assertAlmostEqual(low['form']['bottom'], low['sidebar']['bottom'] - 1, delta=2, msg=low)
        self.page.locator('.study-sidebar').screenshot(path=str(self.out / 'sidebar-minimum.png'))
        # Double-click returns to the height the player alignment produces.
        self.page.locator('.study-sidebar-resizer').dblclick()
        self.page.wait_for_function(ALIGNED)
        reset = self.geometry()
        self.assertAlmostEqual(reset['sidebar']['height'], before['sidebar']['height'], delta=1, msg=reset)
        self.assertIsNone(reset['stored'], reset)
        # Keyboard resizing follows the same rules, and Home resets again.
        resizer = self.page.locator('.study-sidebar-resizer')
        resizer.focus()
        resizer.press('ArrowDown')
        resizer.press('ArrowDown')
        grown = self.geometry()
        self.assertAlmostEqual(grown['sidebar']['height'], reset['sidebar']['height'] + 48, delta=2, msg=grown)
        resizer.press('ArrowUp')
        self.assertAlmostEqual(self.geometry()['sidebar']['height'], grown['sidebar']['height'] - 24, delta=2)
        resizer.press('Home')
        self.page.wait_for_function(ALIGNED)
        self.assertAlmostEqual(self.geometry()['sidebar']['height'], before['sidebar']['height'], delta=1)

    def test_height_is_remembered_and_hidden_when_stacked(self):
        self.drag(120)
        dragged = self.geometry()
        self.assertAlmostEqual(float(dragged['stored']), dragged['sidebar']['height'], delta=2)
        self.page.reload()
        self.page.get_by_role('button', name='学习视图', exact=True).click()
        expect(self.page.locator('.study-sidebar')).to_be_visible()
        restored = self.geometry()
        self.assertAlmostEqual(restored['sidebar']['height'], dragged['sidebar']['height'], delta=2, msg=restored)

        self.page.get_by_role('button', name='收起学习侧栏', exact=True).click()
        expect(self.page.locator('.study-sidebar-resizer')).to_be_hidden()
        self.page.get_by_role('button', name='展开学习侧栏', exact=True).click()
        expect(self.page.locator('.study-sidebar-resizer')).to_be_visible()
        self.assertAlmostEqual(self.geometry()['sidebar']['height'], dragged['sidebar']['height'], delta=2)

        for width in (1024, 390):
            self.page.set_viewport_size({'width': width, 'height': 900})
            data = self.geometry()
            self.assertEqual(data['resizerDisplay'], 'none', (width, data))
            self.assertFalse(data['aligned'], (width, data))
            self.assertFalse(data['overflow'], (width, data))
        self.page.set_viewport_size({'width': 1440, 'height': 1000})
        self.assertEqual(self.geometry()['resizerDisplay'], 'block')
