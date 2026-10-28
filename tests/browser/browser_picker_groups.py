"""Grouped picker disclosure tests; every API is synthetic, no live jobs."""
import unittest
from urllib.parse import urlsplit
from playwright.sync_api import expect
from tests.browser import browser_workspace_picker as picker

THIRD='c'*32
SINGLE='a'*32
class PickerGroupsTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):picker.WorkspacePickerTests.setUpClass()
    @classmethod
    def tearDownClass(cls):picker.WorkspacePickerTests.tearDownClass()
    def setUp(self):
        self.h=picker.WorkspacePickerTests('test_initial_restore_preserves_subtitle_deep_link');self.h.setUp();self.addCleanup(self.h.tearDown)
        self.page=self.h.page;self.f=self.h.f
        self.course='第二课程：带有很长项目名称以验证换行和标题字重的课程'
        self.rows=[{**self.f.parsed_for(i)['project'],'archived':True} for i in (picker.FIRST,picker.SECOND)]
        self.rows += [{**self.rows[0],'id':THIRD,'video_id':'other-course','display_title':self.course,'title':self.course,'episode_title':'独立课程第一讲'},
                      {**self.rows[0],'id':SINGLE,'video_id':'single','is_single':True,'title':'单视频示例','episode_title':'单视频示例'}]
        self.page.route('**/api/workspace/projects',lambda route:route.fulfill(json={'projects':self.rows}))
        self.h.open();self.trigger=self.h.picker();self.trigger.click();self.popup=self.page.locator('.project-picker-popup');self.field=self.popup.get_by_role('searchbox',name='筛选视频列表')
    def headers(self):return self.popup.locator('.project-picker-group')
    def assert_no_submission(self):
        self.assertFalse(any(m=='POST' and (p in ('/parse','/subtitles','/transcriptions','/translations') or p.endswith('/prepare-task') or p.endswith('/cancel')) for m,p,_ in self.f.requests))
    def test_default_titles_and_independent_disclosures(self):
        expect(self.headers()).to_have_text(['示例课程',self.course,'单个视频'])
        for h in self.headers().all():
            expect(h).to_have_attribute('aria-expanded','true');expect(h).to_have_css('font-size','15px');expect(h).to_have_css('font-weight','700');expect(h).to_have_css('justify-content','flex-start')
            self.assertTrue(h.evaluate('(e)=>!!document.getElementById(e.getAttribute("aria-controls"))'))
        expect(self.popup.get_by_role('option')).to_have_count(4)
        self.headers().first.click();expect(self.headers().first).to_have_attribute('aria-expanded','false');expect(self.popup.get_by_role('option')).to_have_count(2)
        expect(self.trigger).to_have_attribute('data-value',picker.FIRST)
        self.headers().first.click();expect(self.popup.get_by_role('option')).to_have_count(4);self.assert_no_submission()
    def test_all_toggle_and_reopen_preserve_current_video_and_fold_state(self):
        self.popup.get_by_role('button',name='全部收起',exact=True).click();expect(self.popup.get_by_role('option')).to_have_count(0)
        expect(self.popup.locator('.project-picker-group[aria-expanded=true]')).to_have_count(0)
        self.field.press('ArrowDown');self.field.press('Enter');expect(self.popup).to_be_visible();expect(self.trigger).to_have_attribute('data-value',picker.FIRST)
        self.field.press('Escape');expect(self.trigger).to_be_focused();self.trigger.click()
        expect(self.popup.get_by_role('button',name='全部展开')).to_be_visible();expect(self.popup.get_by_role('option')).to_have_count(0)
        self.popup.get_by_role('button',name='全部展开').click();expect(self.popup.get_by_role('option')).to_have_count(4)
        self.headers().first.click();self.popup.get_by_role('button',name='全部收起').click();expect(self.popup.get_by_role('option')).to_have_count(0)
        self.assert_no_submission()
    def test_search_temporarily_reveals_collapsed_groups_and_restores_after_clear(self):
        self.popup.get_by_role('button',name='全部收起').click()
        self.field.fill('第二课程');expect(self.popup.get_by_role('option')).to_have_count(2)
        self.popup.get_by_role('button',name='全部收起').click();expect(self.popup.get_by_role('option')).to_have_count(0)
        self.field.fill('');expect(self.popup.get_by_role('option')).to_have_count(0)
        self.field.fill('第二课程');expect(self.popup.get_by_role('option')).to_have_count(2)
        self.field.fill('没有匹配项');expect(self.popup.get_by_role('option')).to_have_count(1) # current video remains available
        self.field.fill('');expect(self.popup.get_by_role('option')).to_have_count(0)
        self.assert_no_submission()
    def test_keyboard_controls_do_not_select_and_navigation_skips_hidden_items(self):
        # The collapse toggle sits on the first heading row, so it follows that heading in tab order.
        self.field.press('Tab');expect(self.popup.get_by_role('listbox')).to_be_focused()
        self.page.keyboard.press('Tab');expect(self.headers().first).to_be_focused()
        self.page.keyboard.press('Tab')
        all_button=self.popup.get_by_role('button',name='全部收起');expect(all_button).to_be_focused()
        all_button.press('Enter');expect(self.popup.get_by_role('option')).to_have_count(0);expect(self.trigger).to_have_attribute('data-value',picker.FIRST)
        expand=self.popup.get_by_role('button',name='全部展开')
        expand.focus();self.page.keyboard.press('Space');expect(self.popup.get_by_role('option')).to_have_count(4)
        self.page.keyboard.press('Tab');expect(self.headers().nth(1)).to_be_focused()
        # Heading reachability is checked directly; its tab position relative to the toggle is covered above.
        self.headers().first.focus();self.headers().first.press('Enter')
        expect(self.headers().first).to_have_attribute('aria-expanded','false');expect(self.trigger).to_have_attribute('data-value',picker.FIRST)
        self.field.focus();self.field.press('ArrowDown');active=self.field.get_attribute('aria-activedescendant')
        self.assertEqual(self.page.locator('[id="'+active+'"]').get_attribute('data-project-id'),THIRD)
        self.field.press('Enter');expect(self.trigger).to_have_attribute('data-value',THIRD);expect(self.popup).to_have_count(0)
        self.assert_no_submission()
    def test_geometry_long_headings_both_themes_and_all_collapsed(self):
        for theme in ('dark','light'):
            self.page.evaluate('(x)=>document.documentElement.dataset.theme=x',theme)
            for width in (1440,760,390,280):
                self.page.set_viewport_size({'width':width,'height':800});self.page.wait_for_timeout(50)
                self.assertTrue(self.page.evaluate('document.documentElement.scrollWidth<=innerWidth'))
                self.assertTrue(self.popup.evaluate('(e)=>e.scrollWidth<=e.clientWidth+1'))
                for h in self.headers().all():self.assertTrue(h.evaluate('(e)=>e.scrollWidth<=e.clientWidth+1'))
                box=self.popup.bounding_box();self.assertGreaterEqual(box['x'],15);self.assertLessEqual(box['x']+box['width'],width-15)
                self.popup.get_by_role('button',name='全部收起').click();expect(self.popup.get_by_role('option')).to_have_count(0)
                self.popup.get_by_role('button',name='全部展开').click();expect(self.popup.get_by_role('option')).to_have_count(4)
        self.assert_no_submission()
    def test_new_group_is_expanded_and_learning_picker_has_same_controls(self):
        self.popup.get_by_role('button',name='全部收起').click();self.field.press('Escape')
        self.rows.append({**self.rows[0],'id':'d'*32,'video_id':'new-course','display_title':'新出现的课程','title':'新出现的课程'})
        self.page.locator('.learning-project-refresh').first.click();expect(self.trigger).to_be_enabled()
        self.trigger.click();expect(self.popup.get_by_role('button',name='新出现的课程',exact=True)).to_have_attribute('aria-expanded','true')
        expect(self.popup.get_by_role('option')).to_have_count(1);self.field.press('Escape')
        self.page.get_by_role('button',name='学习视图',exact=True).click();learning=self.page.get_by_role('combobox',name='学习项目',exact=True);expect(learning).to_be_enabled();learning.click()
        expect(self.popup.get_by_role('button',name='全部收起')).to_be_visible();expect(self.popup.get_by_role('option')).to_have_count(5)
        self.popup.get_by_role('button',name='全部收起').click();expect(self.popup.get_by_role('option')).to_have_count(0)
        self.assert_no_submission()
    def test_english_bulk_controls_and_untranslated_course_name(self):
        self.field.press('Escape');self.page.goto(self.f.url+'/?page=settings')
        self.page.locator('.language-switch label',has_text='English').click();self.page.goto(self.f.url+'/?project='+picker.FIRST)
        trigger=self.page.get_by_role('combobox',name='Archived videos',exact=True);expect(trigger).to_be_enabled();trigger.click()
        expect(self.popup.get_by_role('button',name='Collapse all',exact=True)).to_be_visible()
        expect(self.popup.get_by_role('button',name=self.course,exact=True)).to_be_visible()
        self.popup.get_by_role('button',name='Collapse all',exact=True).click();expect(self.popup.get_by_role('button',name='Expand all',exact=True)).to_be_visible();expect(self.popup.get_by_role('option')).to_have_count(0)
        self.assert_no_submission()
