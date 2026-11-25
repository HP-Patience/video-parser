"""Storage layout geometry, responsive fit, status semantics and write-free rendering."""
import unittest
from urllib.parse import urlsplit
from playwright.sync_api import expect
from tests.browser import browser_product_improvements as harness


def fixture_type():
    class Fixture(harness.ProductImprovementsBrowserTests):
        def setUp(self):
            super().setUp(); self.version = '3.0.1'; self.ready = True
        def route(self, route):
            url = urlsplit(route.request.url)
            if url.hostname == '127.0.0.1' and url.port == self.server.server_port and url.path == '/api/workspace/config':
                self.requests.append((route.request.method, '/config', None))
                return route.fulfill(json={'platforms': [], 'media_ready': self.ready, 'subtitles_ready': self.ready,
                    'transcription_ready': self.ready, 'ai_configured': self.ready, 'version': self.version,
                    'archive_root': 'D:/项目管理/非常长的归档文件夹/' + 'course-' * 35 + '/outputs'})
            return super().route(route)
    return Fixture


class StorageAlignmentTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls): harness.ProductImprovementsBrowserTests.setUpClass()
    @classmethod
    def tearDownClass(cls): harness.ProductImprovementsBrowserTests.tearDownClass()
    def setUp(self):
        self.f = fixture_type()('test_settings_cross_tab_updates'); self.f.setUp(); self.page = self.f.page
    def tearDown(self):
        self.assertFalse(any(method == 'POST' for method, _, _ in self.f.requests))
        self.f.tearDown()
    def open(self):
        self.page.goto(self.f.url + '?page=settings&settings_section=maintenance')
        expect(self.page.locator('.service-status dd')).to_have_count(5 if self.f.version else 4)
    def test_moved_sections_share_general_including_legacy_links(self):
        for section in ('', 'cloud', 'health', 'maintenance'):
            self.page.goto(self.f.url + '?page=settings' + (f'&settings_section={section}' if section else ''))
            nav = self.page.get_by_role('navigation', name='设置分类')
            expect(nav.get_by_role('button')).to_have_text(['常用设置', 'AI 模型', '用量看板'])
            expect(nav.get_by_role('button', name='常用设置')).to_have_attribute('aria-current', 'page')
            for heading in ('登录与云端存储', '归档体检', '存储与维护'):
                expect(self.page.get_by_role('heading', name=heading, exact=True)).to_be_visible()
        nav.get_by_role('button', name='AI 模型').click()
        expect(self.page.get_by_role('heading', name='存储与维护', exact=True)).to_be_hidden()
        self.page.go_back()
        expect(self.page.get_by_role('heading', name='存储与维护', exact=True)).to_be_visible()
    def geometry(self):
        return self.page.evaluate('''() => {
          const grid=document.querySelector('.settings-maintenance-grid');
          const rects=s=>Array.from(grid.querySelectorAll(s), e=>e.getBoundingClientRect().toJSON());
          return {cards:rects(':scope > .settings-card'),headings:rects('h3'),
            terms:rects('dt'),values:rects('dd'),overflow:document.documentElement.scrollWidth>innerWidth};
        }''')
    def test_desktop_cards_and_status_columns_align(self):
        self.open()
        expect(self.page.locator('.service-status dd')).to_have_text(['就绪','就绪','就绪','已配置','3.0.1'])
        g=self.geometry();a,b,c,d=g['cards']
        for key in ('y','bottom','height'):
            self.assertAlmostEqual(a[key],b[key],delta=1)
        self.assertAlmostEqual(a['width'],b['width'],delta=1)
        self.assertAlmostEqual(c['x'],a['x'],delta=1);self.assertAlmostEqual(c['right'],b['right'],delta=1)
        self.assertAlmostEqual(d['x'],a['x'],delta=1);self.assertAlmostEqual(d['right'],b['right'],delta=1)
        self.assertAlmostEqual(c['y']-a['bottom'],b['x']-a['right'],delta=1)
        self.assertAlmostEqual(g['headings'][0]['y'],g['headings'][1]['y'],delta=1)
        for term,value in zip(g['terms'],g['values']):
            self.assertAlmostEqual(term['x'],g['terms'][0]['x'],delta=1)
            self.assertAlmostEqual(value['x'],g['values'][0]['x'],delta=1)
            self.assertAlmostEqual(term['y'],value['y'],delta=1)
        self.assertFalse(g['overflow'])
        self.page.screenshot(path=str(self.f.output/'storage-desktop.png'),full_page=True)
    def test_mobile_english_dark_layout_and_keyboard(self):
        self.open()
        self.page.get_by_role('button',name='常用设置',exact=True).click()
        self.page.locator('.language-switch label',has_text='English').click()
        expect(self.page.get_by_role('heading',name='Storage & maintenance',exact=True)).to_be_visible()
        for width in (760,390,320,280):
            self.page.set_viewport_size({'width':width,'height':1000})
            g=self.geometry();a,b,c,d=g['cards']
            self.assertFalse(g['overflow']);self.assertGreaterEqual(b['y'],a['bottom']);self.assertGreaterEqual(c['y'],b['bottom'])
            self.assertGreaterEqual(d['y'],c['bottom'])
            self.assertAlmostEqual(a['x'],b['x'],delta=1);self.assertAlmostEqual(a['width'],b['width'],delta=1)
        leftover=self.page.locator('.service-status').inner_text()
        self.assertNotRegex(leftover,r'[\u3400-\u9fff]')
        self.page.locator('#settings-group-maintenance').focus();self.page.keyboard.press('Tab')
        expect(self.page.locator('.settings-maintenance-grid button').first).to_be_focused()
        self.page.locator('.theme-toggle').click()
        expect(self.page.locator('html')).to_have_attribute('data-theme', 'dark')
        self.page.set_viewport_size({'width':390,'height':1000})
        self.page.screenshot(path=str(self.f.output/'storage-en-dark-mobile.png'),full_page=True,animations='disabled')
    def test_unavailable_states_and_optional_version(self):
        self.f.ready=False;self.f.version=None;self.open()
        expect(self.page.locator('.service-status dd')).to_have_text(['未就绪','未就绪','未就绪','未配置'])
        self.assertFalse(self.geometry()['overflow'])

    def test_cache_button_aligns_with_heading(self):
        self.open()
        cache = self.page.get_by_role('region', name='临时缓存', exact=True)
        for width in (1490, 390, 280):
            self.page.set_viewport_size({'width': width, 'height': 852})
            heading = cache.get_by_role('heading').bounding_box()
            button = cache.get_by_role('button', name='缓存管理', exact=True).bounding_box()
            section = cache.bounding_box()
            self.assertAlmostEqual(heading['y'] + heading['height'] / 2, button['y'] + button['height'] / 2, delta=1)
            self.assertGreaterEqual(button['x'], heading['x'] + heading['width'])
            self.assertAlmostEqual(button['x'] + button['width'], section['x'] + section['width'], delta=1)
