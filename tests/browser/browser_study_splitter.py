"""The player/rail divider: pointer drag, keyboard, persistence and responsive hiding.

Synthetic sessions only: no model call, workspace write or download leaves this fixture.
"""
import os
from pathlib import Path
import unittest
from playwright.sync_api import expect
from tests.browser import browser_ux_improvements as ux

GEOMETRY = '''() => {
  const workspace = document.querySelector('.study-workspace');
  const rail = document.querySelector('.study-context-rail');
  const divider = document.querySelector('.study-splitter');
  const preview = document.querySelector('.preview-panel');
  const sidebar = document.querySelector('.study-sidebar');
  const bar = document.querySelector('.playback-resume');
  const box = element => { const value = element.getBoundingClientRect(); return { left: value.left, right: value.right, top: value.top, bottom: value.bottom, width: value.width, height: value.height }; };
  return {
    divider: divider ? box(divider) : null,
    dividerDisplay: divider ? getComputedStyle(divider).display : null,
    rail: box(rail), preview: box(preview), sidebar: box(sidebar), bar: box(bar), workspace: box(workspace),
    gap: rail.getBoundingClientRect().left - preview.getBoundingClientRect().right,
    railWidth: getComputedStyle(workspace).getPropertyValue('--study-rail-width').trim(),
    aligned: sidebar.hasAttribute('data-aligned'),
    overflow: document.documentElement.scrollWidth > innerWidth,
  };
}'''

ALIGNED = "() => { const sidebar = document.querySelector('.study-sidebar').getBoundingClientRect(); const bar = document.querySelector('.playback-resume').getBoundingClientRect(); return Math.abs(sidebar.bottom - bar.bottom) <= 1; }"


class StudySplitterTests(unittest.TestCase):
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
        self.out = Path(os.environ.get('VIDEO_PARSER_TEST_OUTPUT', '.local/study-splitter'))
        self.out.mkdir(parents=True, exist_ok=True)
        self.f.parse()
        self.page.get_by_role('button', name='学习视图', exact=True).click()
        expect(self.page.locator('.study-sidebar')).to_be_visible()
        expect(self.page.locator('.study-splitter')).to_be_visible()

    def tearDown(self):
        self.f.tearDown()

    def geometry(self):
        return self.page.evaluate(GEOMETRY)

    def drag(self, pixels):
        box = self.page.locator('.study-splitter').bounding_box()
        x, y = box['x'] + box['width'] / 2, box['y'] + box['height'] / 2
        self.page.mouse.move(x, y)
        self.page.mouse.down()
        self.page.mouse.move(x + pixels, y, steps=6)
        self.page.mouse.up()

    def test_dragging_the_divider_resizes_both_columns(self):
        before = self.geometry()
        self.assertTrue(before['aligned'], before)
        self.assertAlmostEqual(before['rail']['width'], 336, delta=2, msg=before)
        self.assertAlmostEqual(before['gap'], 20, delta=1, msg=before)
        # The handle fills the gap exactly: it never covers the player or the rail.
        self.assertAlmostEqual(before['divider']['left'], before['preview']['right'], delta=1, msg=before)
        self.assertAlmostEqual(before['divider']['right'], before['rail']['left'], delta=1, msg=before)
        self.drag(-120)
        after = self.geometry()
        self.assertAlmostEqual(after['rail']['width'] - before['rail']['width'], 120, delta=2, msg=after)
        self.assertAlmostEqual(before['preview']['width'] - after['preview']['width'], 120, delta=2, msg=after)
        self.assertLess(after['preview']['height'], before['preview']['height'])
        self.assertAlmostEqual(after['gap'], 20, delta=1, msg=after)
        self.assertAlmostEqual(after['divider']['left'], after['preview']['right'], delta=1, msg=after)
        # The rail keeps hugging the bar under the resized player.
        self.assertTrue(after['aligned'], after)
        self.page.wait_for_function(ALIGNED)
        self.assertFalse(after['overflow'], after)
        self.page.screenshot(path=str(self.out / 'study-splitter-1440.png'))

        divider = self.page.locator('.study-splitter')
        divider.focus()
        divider.press('ArrowRight')
        narrowed = self.geometry()
        self.assertAlmostEqual(narrowed['rail']['width'], after['rail']['width'] - 24, delta=2, msg=narrowed)
        divider.press('ArrowLeft')
        self.assertAlmostEqual(self.geometry()['rail']['width'], after['rail']['width'], delta=2)
        divider.press('Home')
        reset = self.geometry()
        self.assertAlmostEqual(reset['rail']['width'], 336, delta=2, msg=reset)
        self.assertAlmostEqual(float(self.page.evaluate("() => localStorage.getItem('video-parser:study-rail-width')")), 336, delta=1)

    def test_the_rail_stops_at_its_limits(self):
        self.drag(-400)
        wide = self.geometry()
        self.assertGreaterEqual(wide['rail']['width'], 280, wide)
        self.assertLessEqual(wide['rail']['width'], wide['workspace']['width'] * 0.45 + 1, wide)
        self.assertGreater(wide['preview']['width'], 0, wide)
        self.assertFalse(wide['overflow'], wide)
        self.drag(400)
        narrow = self.geometry()
        self.assertGreaterEqual(narrow['rail']['width'], 280, narrow)
        self.assertLess(narrow['rail']['width'], wide['rail']['width'], narrow)
        self.assertTrue(narrow['aligned'], narrow)
        self.assertFalse(narrow['overflow'], narrow)

    def test_width_survives_reload_and_hides_in_narrow_or_collapsed_layouts(self):
        self.drag(-100)
        widened = self.geometry()
        self.assertAlmostEqual(widened['rail']['width'], 436, delta=2, msg=widened)
        self.assertAlmostEqual(float(self.page.evaluate("() => localStorage.getItem('video-parser:study-rail-width')")), widened['rail']['width'], delta=2)
        self.page.reload()
        self.page.get_by_role('button', name='学习视图', exact=True).click()
        expect(self.page.locator('.study-sidebar')).to_be_visible()
        restored = self.geometry()
        self.assertAlmostEqual(restored['rail']['width'], widened['rail']['width'], delta=2, msg=restored)
        self.assertAlmostEqual(restored['divider']['left'], restored['preview']['right'], delta=1, msg=restored)

        # A collapsed rail is a fixed strip; expanding it restores the dragged width.
        self.page.get_by_role('button', name='收起学习侧栏', exact=True).click()
        expect(self.page.locator('.study-splitter')).to_be_hidden()
        self.assertAlmostEqual(self.geometry()['rail']['width'], 48, delta=2)
        self.page.get_by_role('button', name='展开学习侧栏', exact=True).click()
        expect(self.page.locator('.study-splitter')).to_be_visible()
        self.assertAlmostEqual(self.geometry()['rail']['width'], widened['rail']['width'], delta=2)

        # Stacked layouts have nothing to divide: the handle leaves the page entirely.
        for width in (1024, 390):
            self.page.set_viewport_size({'width': width, 'height': 900})
            data = self.geometry()
            self.assertEqual(data['dividerDisplay'], 'none', (width, data))
            self.assertFalse(data['overflow'], (width, data))
            self.assertGreaterEqual(data['rail']['top'], data['preview']['bottom'] - 1, (width, data))
            self.page.screenshot(path=str(self.out / f'study-splitter-{width}.png'))
        self.page.set_viewport_size({'width': 1440, 'height': 1000})
        self.assertEqual(self.geometry()['dividerDisplay'], 'block')
