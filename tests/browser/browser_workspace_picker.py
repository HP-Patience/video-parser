"""Archived switching on the workbench, isolated APIs/storage, no downloads or models."""
import unittest
from tests.browser.project_picker import select_project
from urllib.parse import urlsplit
from playwright.sync_api import expect
from tests.browser import browser_product_improvements as harness

FIRST=f'{3:032x}'
SECOND=f'{4:032x}'
JOB='f'*32

def fixture_type():
    class Fixture(harness.ProductImprovementsBrowserTests):
        def setUp(self):
            super().setUp(); self.with_media=True; self.empty=False; self.fail_resume=False; self.fail_list=False; self.job=None; self.hold_resume=False; self.held_resume=None
        def parsed_for(self, identifier):
            value=super().parsed(); part=1 if identifier==FIRST else 2
            media=value['media']
            if media:
                media={**media,'resource':f'video/{identifier}.wav','preview_url':f'/product-fixture.wav?id={identifier}'}
            value.update(id=identifier,media=media,source_url=f'https://www.bilibili.com/video/{harness.VIDEO_ID}?p={part}')
            value['qualities']=[{**value['qualities'][0],'media':media}]
            value['project']={**value['project'],'id':identifier,'part':part,'title':'示例课程',
                'video_id':harness.VIDEO_ID,'is_single':False,'episode_title':'第一讲' if part==1 else '第二讲','resources':[{'kind':'video','resource':f'video/{identifier}.wav'}]}
            return value
        def route(self, route):
            req=route.request;url=urlsplit(req.url)
            if url.hostname=='127.0.0.1' and url.port==self.server.server_port:
                path=url.path.removeprefix('/api/workspace')
                if path=='/projects':
                    self.requests.append((req.method,path,None))
                    if self.fail_list:return route.fulfill(status=503,json={'detail':'模拟项目列表读取失败'})
                    return route.fulfill(json={'projects':[] if self.empty else [
                        {**self.parsed_for(id)['project'],'archived':True} for id in (FIRST,SECOND)]})
                if path.endswith('/resume'):
                    self.requests.append((req.method,path,None));id=path.split('/')[2]
                    if self.hold_resume:
                        self.held_resume=route;return
                    if self.fail_resume and id==SECOND:return route.fulfill(status=503,json={'detail':'模拟归档读取失败'})
                    return route.fulfill(json=self.parsed_for(id))
                if path.endswith('/prepare-task'):
                    self.requests.append((req.method,path,req.post_data_json))
                    self.job={'id':JOB,'kind':'video','project_id':FIRST,'state':'running','phase':'downloading',
                        'message':'模拟后台下载仍在进行','title':'示例课程','progress':{},'result':None}
                    return route.fulfill(json=self.job)
                if path=='/tasks' or path=='/tasks/'+JOB:
                    self.requests.append((req.method,path,None))
                    return route.fulfill(json={'jobs':[self.job] if self.job else []} if path=='/tasks' else self.job)
            return super().route(route)
    return Fixture

class WorkspacePickerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):harness.ProductImprovementsBrowserTests.setUpClass()
    @classmethod
    def tearDownClass(cls):harness.ProductImprovementsBrowserTests.tearDownClass()
    def setUp(self):
        self.f=fixture_type()('test_settings_cross_tab_updates');self.f.setUp();self.page=self.f.page
    def tearDown(self):self.f.tearDown()
    def open(self):
        self.page.goto(self.f.url+'/?project='+FIRST)
        expect(self.page.get_by_role('combobox',name='已归档视频',exact=True)).to_be_enabled()
        expect(self.page.locator('.info-title')).to_have_text('第一讲')
    def picker(self):return self.page.get_by_role('combobox',name='已归档视频',exact=True)
    def test_grouped_popup_keyboard_search_width_and_single_identity(self):
        self.open(); original = self.f.parsed_for
        self.page.route('**/api/workspace/projects', lambda route: route.fulfill(json={'projects':[
            {**original(SECOND)['project'], 'archived':True},
            {**original(FIRST)['project'], 'archived':True},
            {**original(FIRST)['project'], 'id':'a'*32, 'video_id':'single-video', 'is_single':True, 'title':'独立视频标题', 'episode_title':'独立视频标题', 'archived':True},
        ]}))
        with self.page.expect_response(lambda response: urlsplit(response.url).path == '/api/workspace/projects'):
            self.page.locator('.learning-project-refresh').first.click()
        expect(self.page.locator('.workspace-toolbar .learning-project-picker')).to_have_attribute('aria-busy','false')
        expect(self.picker()).to_be_enabled(); self.picker().press('ArrowDown')
        popup = self.page.locator('.project-picker-popup')
        expect(popup.locator('.project-picker-group')).to_have_text(['示例课程','单个视频'])
        expect(popup.get_by_role('option')).to_have_text(['P1 第一讲✓','P2 第二讲','独立视频标题'])
        expect(popup.locator('[title]')).to_have_count(0)
        field = self.page.get_by_role('searchbox',name='筛选视频列表',exact=True)
        expect(field).to_be_focused(); field.press('ArrowDown'); field.press('Enter')
        expect(self.picker()).to_have_attribute('data-value',SECOND)
        expect(popup).to_have_count(0); expect(self.picker()).to_be_focused()
        self.picker().click(); field.fill('P001')
        expect(popup.get_by_role('option')).to_have_count(2)  # match plus preserved current item
        field.press('Escape'); expect(self.picker()).to_be_focused()
        self.picker().click(); field.fill('')
        for width in (1440,760,390,280):
            self.page.set_viewport_size({'width':width,'height':800})
            expect(popup).to_be_visible()
            self.page.wait_for_timeout(40)
            box=popup.bounding_box(); self.assertGreaterEqual(box['x'],15); self.assertLessEqual(box['x']+box['width'],width-15)
            self.assertLessEqual(box['width'],420); self.assertLessEqual(box['height'],460)
            self.assertTrue(self.page.evaluate('document.documentElement.scrollWidth<=innerWidth'))
        self.page.screenshot(path=str(self.f.output/'grouped-picker-mobile.png'))
        field.press('Escape')
        self.assertFalse(any(method=='POST' and path=='/parse' for method,path,_ in self.f.requests))

    def test_initial_restore_preserves_subtitle_deep_link(self):
        self.page.goto(self.f.url+'/?page=transcription&project='+FIRST)
        expect(self.picker()).to_have_attribute('data-value', FIRST)
        expect(self.picker()).to_be_enabled()
        expect(self.page.get_by_role('tab',name='字幕与转写',exact=True)).to_have_attribute('aria-selected','true')
    def test_late_initial_restore_preserves_new_tab_selection(self):
        self.f.hold_resume=True
        self.page.goto(self.f.url+'/?project='+FIRST)
        expect(self.picker()).to_be_disabled()
        self.page.get_by_role('tab',name='字幕与转写',exact=True).click()
        self.assertIsNotNone(self.f.held_resume)
        self.f.hold_resume=False;self.f.held_resume.fulfill(json=self.f.parsed_for(FIRST))
        expect(self.picker()).to_have_attribute('data-value', FIRST)
        expect(self.picker()).to_be_enabled()
        expect(self.page.get_by_role('tab',name='字幕与转写',exact=True)).to_have_attribute('aria-selected','true')
    def test_switch_keeps_workbench_tab_draft_and_search(self):
        self.open();self.picker().click();expect(self.page.locator('.project-picker-popup [role=option]')).to_have_text(['P1 第一讲✓','P2 第二讲']); self.page.get_by_role('searchbox',name='筛选视频列表',exact=True).press('Escape')
        self.page.get_by_role('tab',name='AI 分析',exact=True).click();self.page.locator('#prompt').fill('保留分析要求')
        select_project(self.picker(), SECOND)
        expect(self.picker()).to_have_attribute('data-value', SECOND)
        expect(self.page.get_by_role('tab',name='AI 分析',exact=True)).to_have_attribute('aria-selected','true')
        expect(self.page.locator('#prompt')).to_have_value('保留分析要求')
        self.assertNotIn('page=learning',self.page.url)
        self.picker().click()
        self.page.get_by_role('searchbox',name='筛选视频列表',exact=True).fill('第一讲')
        expect(self.page.locator('.project-picker-popup [role=option]')).to_have_count(2); self.page.get_by_role('searchbox',name='筛选视频列表',exact=True).press('Escape')
        select_project(self.picker(), FIRST);expect(self.picker()).to_have_attribute('data-value', FIRST)
        self.picker().click()
        self.page.get_by_role('searchbox',name='筛选视频列表',exact=True).fill('不存在的分集')
        self.page.get_by_role('searchbox',name='筛选视频列表',exact=True).press('Escape')
        expect(self.page.locator('.workspace-toolbar')).to_contain_text('当前视频保持不变')
        self.page.get_by_role('button',name='学习视图',exact=True).click()
        expect(self.page.get_by_role('combobox',name='学习项目',exact=True)).to_have_attribute('data-value', FIRST)
        self.assertFalse(any(method=='POST' and (path=='/parse' or path.endswith('/prepare-task')) for method,path,_ in self.f.requests))
    def test_failed_restore_keeps_current_video_and_reports_error(self):
        self.open();self.f.fail_resume=True;select_project(self.picker(), SECOND)
        expect(self.page.get_by_role('alert').filter(has_text='模拟归档读取失败')).to_be_visible()
        expect(self.picker()).to_have_attribute('data-value', FIRST);expect(self.picker()).to_be_enabled()
        expect(self.page.locator('.info-title')).to_have_text('第一讲')
    def test_running_task_does_not_block_switch_or_get_cancelled(self):
        self.f.with_media=False;self.open()
        self.page.locator('.download-button').click()
        expect(self.page.locator('#video-stage')).to_contain_text('模拟后台下载仍在进行')
        expect(self.picker()).to_be_enabled();select_project(self.picker(), SECOND)
        expect(self.page.locator('.info-title')).to_have_text('第二讲')
        select_project(self.picker(), FIRST)
        expect(self.page.locator('#video-stage')).to_contain_text('模拟后台下载仍在进行')
        self.assertFalse(any(path.endswith('/cancel') for _,path,_ in self.f.requests))
        self.assertEqual(sum(path.endswith('/prepare-task') for _,path,_ in self.f.requests),1)
    def test_empty_and_failed_list_are_safe_and_refreshable(self):
        self.f.empty=True;self.page.goto(self.f.url)
        expect(self.picker()).to_be_disabled()
        expect(self.page.locator('.workspace-toolbar')).to_contain_text('暂无已归档视频')
        self.f.fail_list=True
        self.page.locator('.learning-project-refresh').first.click()
        expect(self.page.locator('.workspace-toolbar [role=alert]')).to_contain_text('模拟项目列表读取失败')
        self.f.fail_list=False;self.f.empty=False
        self.page.locator('.learning-project-refresh').first.click()
        expect(self.picker()).to_be_enabled();select_project(self.picker(), SECOND)
        expect(self.page.locator('.info-title')).to_have_text('第二讲')
    def test_general_alignment_labels_compatibility_and_responsive_picker(self):
        self.page.add_init_script("localStorage.setItem('video-parser:settings',JSON.stringify({qualityPreference:'1080p'}))")
        self.page.goto(self.f.url+'/?page=settings')
        expect(self.page.locator('#default-quality')).to_have_value('1080p')
        expect(self.page.locator('#default-quality option')).to_have_text(['最高可用','1080P 高清','720P 准高清','480P 标清','360P 流畅'])
        bounds=self.page.evaluate("Array.from(document.querySelectorAll('.settings-general-grid > section'),e=>e.getBoundingClientRect().toJSON())")
        self.assertAlmostEqual(bounds[1]['bottom'],bounds[2]['bottom'],delta=1)
        self.assertAlmostEqual(bounds[1]['y'],bounds[2]['y'],delta=1)
        self.page.screenshot(path=str(self.f.output/'general-aligned.png'),full_page=True)
        self.page.locator('.language-switch label',has_text='English').click()
        expect(self.page.locator('#default-quality option[value="1080p"]')).to_have_text('1080P HD')
        expect(self.page.locator('#default-quality option[value="720p"]')).to_have_text('720P HD Ready')
        self.page.goto(self.f.url+'/?project='+FIRST)
        selector=self.page.get_by_role('combobox',name='Archived videos',exact=True)
        expect(selector).to_be_enabled()
        for width in (1440,760,390,320,280):
            self.page.set_viewport_size({'width':width,'height':1000})
            expect(selector).to_be_visible()
            self.assertTrue(self.page.evaluate('document.documentElement.scrollWidth <= innerWidth'))
        self.page.screenshot(path=str(self.f.output/'workspace-picker-en-mobile.png'),full_page=True)
