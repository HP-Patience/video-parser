"""Slash completion uses synthetic agent catalogs, never real tools or model calls."""
import json
from pathlib import Path
import unittest
from playwright.sync_api import expect
from tests.browser import browser_chat_ime as ime
from tests.browser import browser_product_improvements as harness

VISIBLE_OPTIONS = '''() => {
  const list = document.querySelector('.slash-command-list').getBoundingClientRect();
  return [...document.querySelectorAll('.slash-command-list [role=option]')].filter(option => {
    const box = option.getBoundingClientRect();
    return box.top >= list.top - 1 && box.bottom <= list.bottom + 1;
  }).length;
}'''

TOP_MOST = '([x, y]) => !!document.elementFromPoint(x, y)?.closest(".slash-command-menu")'


class SlashCommandBrowserTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.Fixture = ime.fixture_type(); cls.Fixture.setUpClass()

    @classmethod
    def tearDownClass(cls):
        cls.Fixture.tearDownClass()

    def setUp(self):
        self.f = self.Fixture('test_settings_cross_tab_updates'); self.f.setUp(); self.page = self.f.page
        self.out = Path('.local/slash-menu'); self.out.mkdir(parents=True, exist_ok=True)
        self.f.catalog['engines'][0]['commands'] = [
            {'prefix': '/', 'name': 'review', 'description': '检查代码与说明'},
            {'prefix': '/', 'name': 'rewrite', 'description': '改写内容'},
            {'prefix': '/', 'name': 'explain', 'description': '解释课程概念'},
        ]
        self.f.catalog['engines'][1]['commands'] = [{'prefix': '$', 'name': 'study', 'description': '学习 Skill'}]
        preference = {'enabled': None, 'target': {'engine': 'claude', 'model': 'claude-fixture', 'effort': ''}}
        self.page.add_init_script(f"localStorage.setItem('{harness.PREFERENCES}',JSON.stringify({json.dumps(preference)}))")

    def tearDown(self):
        self.f.tearDown()

    def open(self):
        self.f.parse(); self.page.get_by_role('button', name='学习视图', exact=True).click()
        self.page.get_by_role('tab', name='AI 对话', exact=True).click()
        self.panel = self.page.locator('#study-panel-chat')
        self.field = self.panel.locator('textarea'); expect(self.field).to_be_enabled(); self.field.focus()
        self.menu = self.panel.locator('.slash-command-menu')
        expect(self.panel.locator('.agent-custom')).to_have_count(0)
        expect(self.panel.get_by_role('combobox', name='插入命令或技能')).to_have_count(0)
        expect(self.panel.get_by_role('textbox', name='自定义模型 ID')).to_have_count(0)

    def assert_not_dispatched(self):
        self.assertFalse(any(method == 'POST' and (path.endswith('/ask-task') or path.endswith('/conversations') or path == '/ai/agents/discover') for method, path, _ in self.f.requests))

    def mode(self):
        classes = self.menu.get_attribute('class') or ''
        return 'below' if 'is-below' in classes else 'overlay' if 'is-overlay' in classes else 'above'

    def assert_anchored(self, mode, menu, field):
        """The list floats above, below, or — in a full-height composer — inside the composer."""
        if mode == 'below':
            self.assertGreaterEqual(menu['y'], field['y'] + field['height'])
        elif mode == 'overlay':
            self.assertGreaterEqual(menu['y'], field['y'] - 1)
            self.assertLessEqual(menu['y'] + menu['height'], field['y'] + field['height'] + 1)
        else:
            self.assertLessEqual(menu['y'] + menu['height'], field['y'])

    def test_menu_floats_above_composer_without_layout_shift(self):
        self.f.catalog['engines'][0]['commands'] = [
            {'prefix': '/', 'name': f'skill-{i:03d}', 'description': f'测试 Skill {i}'} for i in range(53)]
        self.open()
        shots = Path('.local/work/artifacts/slash-overlay'); shots.mkdir(parents=True, exist_ok=True)
        for block in ('end', 'start'):
            self.field.evaluate(f"el => el.scrollIntoView({{block: '{block}'}})"); self.field.fill('hello')
            before = self.field.bounding_box()
            self.field.fill('/'); expect(self.menu).to_be_visible(); self.page.wait_for_timeout(50)
            after, menu = self.field.bounding_box(), self.menu.bounding_box()
            self.assertAlmostEqual(before['y'], after['y'], delta=1)
            self.assertAlmostEqual(before['height'], after['height'], delta=1)
            mode = self.mode()
            self.assert_anchored(mode, menu, after)
            if block == 'end' and after['y'] > 420: self.assertNotEqual(mode, 'below', 'enough room above, menu should float above')
            # Top edge, centre and bottom edge of the menu must be topmost (not under the top bar or clipped).
            for y in (menu['y'] + 4, menu['y'] + menu['height'] / 2, menu['y'] + menu['height'] - 4):
                hit = self.page.evaluate(TOP_MOST, [menu['x'] + menu['width'] / 2, y])
                self.assertTrue(hit, f'menu is not topmost at y={y} ({block}, mode={mode})')
            self.page.screenshot(path=str(shots / f'slash-overlay-{block}.png'))
            self.field.fill('hello'); expect(self.menu).to_have_count(0)
            self.assertAlmostEqual(self.field.bounding_box()['y'], before['y'], delta=1)
        # With chat history above (simulated by a spacer) the menu must float above the composer.
        self.field.evaluate("el => { const s = document.createElement('div'); s.id = 'test-spacer'; s.style.height = '420px'; el.closest('form').before(s); }")
        self.field.evaluate("el => el.scrollIntoView({block: 'end'})")
        before = self.field.bounding_box(); self.field.fill('/'); expect(self.menu).to_be_visible(); self.page.wait_for_timeout(50)
        menu, after = self.menu.bounding_box(), self.field.bounding_box()
        # The composer is deliberately tall, so in a short rail the room below can exceed the room
        # above and the menu legitimately flips. What must hold either way: no layout shift, no
        # clipping, and a still-usable list.
        mode = self.mode()
        self.assert_anchored(mode, menu, after)
        if mode == 'below':
            rail_bottom = self.page.evaluate("() => document.querySelector('.study-sidebar-body').getBoundingClientRect().bottom")
            self.assertLessEqual(menu['y'] + menu['height'], rail_bottom + 1)
        self.assertGreaterEqual(menu['height'], 100, 'the flipped menu must stay usable')
        self.assertAlmostEqual(before['y'], after['y'], delta=1)
        for y in (menu['y'] + 4, menu['y'] + menu['height'] / 2, menu['y'] + menu['height'] - 4):
            self.assertTrue(self.page.evaluate(TOP_MOST, [menu['x'] + menu['width'] / 2, y]))
        self.page.screenshot(path=str(shots / 'slash-overlay-above.png'))
        self.assert_not_dispatched()

    def test_tall_composer_shows_several_skills_instead_of_one_row(self):
        # The empty conversation's composer grows to fill the rail, which used to leave the list
        # 76px tall: exactly one skill visible for a 102-entry catalog.
        self.f.catalog['engines'][0]['commands'] = [
            {'prefix': '$', 'name': f'skill-{i:03d}',
             'description': 'Ask which skill or flow fits your situation. A router over the skills in this repo.'}
            for i in range(102)]
        self.open()
        resizer = self.page.locator('.study-sidebar-resizer')
        resizer.focus()
        for _ in range(10): resizer.press('ArrowDown')
        self.field.fill('/'); expect(self.menu).to_be_visible(); self.page.wait_for_timeout(80)
        expect(self.menu.locator('.slash-command-heading')).to_contain_text('102 项')
        self.assertEqual(self.mode(), 'overlay', 'a composer that owns the rail hosts the list')
        self.assertGreaterEqual(self.page.evaluate(VISIBLE_OPTIONS), 3, 'the list must stay usable, not collapse to one row')
        self.assertGreaterEqual(self.menu.bounding_box()['height'], 200)
        menu, field = self.menu.bounding_box(), self.field.bounding_box()
        self.assert_anchored('overlay', menu, field)
        for y in (menu['y'] + 4, menu['y'] + menu['height'] / 2, menu['y'] + menu['height'] - 4):
            self.assertTrue(self.page.evaluate(TOP_MOST, [menu['x'] + menu['width'] / 2, y]), 'the list must not be clipped')
        self.page.locator('.study-sidebar').screenshot(path=str(self.out / 'tall-composer.png'))
        # Filtering still narrows the same list, and choosing works from the overlaid panel.
        self.field.fill('/skill-05'); expect(self.menu.get_by_role('option')).to_have_count(10)
        self.field.press('Enter'); expect(self.field).to_have_value('$skill-050 ')
        self.assert_not_dispatched()

    def test_slash_shows_all_53_entries_and_can_select_the_last(self):
        self.f.catalog['engines'][0]['commands'] = [
            {'prefix': '/', 'name': f'skill-{i:03d}', 'description': f'测试 Skill {i}'} for i in range(53)]
        self.open(); self.field.fill('/')
        expect(self.menu.get_by_role('option')).to_have_count(53)
        expect(self.menu.locator('.slash-command-heading')).to_contain_text('53 项')
        self.field.press('ArrowUp')
        expect(self.menu.get_by_role('option').last).to_have_attribute('aria-selected', 'true')
        self.field.press('Enter'); expect(self.field).to_have_value('/skill-052 ')
        expect(self.menu).to_have_count(0)
        self.field.fill('/skill-052')
        expect(self.menu.get_by_role('option')).to_have_count(1)
        self.field.press('Enter'); expect(self.field).to_have_value('/skill-052 ')
        self.assert_not_dispatched()

    def test_filter_keyboard_selection_does_not_send(self):
        self.open(); self.field.fill('/')
        expect(self.menu.get_by_role('option')).to_have_count(3)
        self.field.fill('/re'); expect(self.menu.get_by_role('option')).to_have_count(2)
        self.field.press('ArrowDown'); self.field.press('Enter')
        expect(self.field).to_have_value('/rewrite '); expect(self.field).to_be_focused()
        expect(self.menu).to_have_count(0); self.assert_not_dispatched()
        self.field.fill('/解释'); expect(self.menu.get_by_role('option')).to_have_count(1)
        self.field.press('Tab'); expect(self.field).to_have_value('/explain ')
        self.assert_not_dispatched()

    def test_mouse_completion_preserves_arguments_and_caret(self):
        self.open(); self.field.fill('/re 保留后面的文字')
        self.field.press('Home')
        for _ in range(3): self.field.press('ArrowRight')
        self.menu.get_by_role('option').filter(has_text='/review').click()
        expect(self.field).to_have_value('/review 保留后面的文字'); expect(self.field).to_be_focused()
        self.page.wait_for_function('document.querySelector("#ai-chat-question").selectionStart===8', timeout=5000)
        self.assertEqual(self.field.evaluate('e=>e.selectionStart'), len('/review '))
        self.assert_not_dispatched()

    def test_escape_urls_empty_results_and_engine_scope(self):
        self.open(); self.field.fill('https://example.com/review')
        expect(self.menu).to_have_count(0)
        self.field.fill('/re'); self.field.press('Escape'); expect(self.menu).to_have_count(0)
        self.field.fill('/nothing-matches'); expect(self.menu.get_by_role('option')).to_have_count(0)
        self.field.press('Enter'); expect(self.field).to_have_value('/nothing-matches'); self.assert_not_dispatched()
        model = self.panel.get_by_role('combobox', name='引擎与模型', exact=True)
        model.focus(); model.select_option('codex|codex-fixture')
        self.field.fill('/'); expect(self.menu.get_by_role('option')).to_have_count(1)
        expect(self.menu).to_contain_text('$study'); expect(self.menu).not_to_contain_text('/review')
        self.field.press('Enter'); expect(self.field).to_have_value('$study '); self.assert_not_dispatched()

    def test_completion_never_truncates_existing_text(self):
        self.open()
        limit = int(self.field.get_attribute('maxlength'))
        original = '/re ' + 'x' * (limit - 4)
        self.field.fill(original); self.field.press('Control+Home')
        for _ in range(3): self.field.press('ArrowRight')
        self.menu.get_by_role('option').filter(has_text='/review').click()
        expect(self.field).to_have_value(original)
        expect(self.panel.get_by_role('alert')).to_contain_text('长度上限')
        self.assert_not_dispatched()

    def test_composition_is_not_used_for_completion_or_send(self):
        self.open(); self.field.fill('/')
        self.field.dispatch_event('compositionstart')
        self.field.dispatch_event('keydown', {'key': 'Enter', 'keyCode': 229, 'isComposing': True})
        expect(self.menu).to_have_count(0); expect(self.field).to_have_value('/')
        self.field.dispatch_event('compositionend')
        self.field.fill('/review'); self.field.press('Enter')
        expect(self.field).to_have_value('/review '); self.assert_not_dispatched()
        self.page.set_viewport_size({'width': 390, 'height': 844})
        self.field.fill('/'); expect(self.menu).to_be_visible()
        self.assertTrue(self.page.evaluate('document.documentElement.scrollWidth<=innerWidth'))
        self.page.screenshot(path=str(self.f.output / 'slash-commands-mobile.png'), full_page=False)
