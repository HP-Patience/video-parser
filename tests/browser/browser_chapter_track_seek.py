"""Actual mouse/keyboard timeline seeking, fixture media only; no model or data writes."""
import re,unittest
from playwright.sync_api import expect
from tests.browser import browser_markdown_math as math

TIMES=(0,10,20,30,40,50,60,64,68)
TEXT='\n'.join(f'## [{s//60:02d}:{s%60:02d}] 第{i+1}章\n第{i+1}章的介绍。' for i,s in enumerate(TIMES))
class ChapterTrackSeekTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):math.MarkdownMathBrowserTests.setUpClass()
    @classmethod
    def tearDownClass(cls):math.MarkdownMathBrowserTests.tearDownClass()
    def setUp(self):
        self.h=math.MarkdownMathBrowserTests('test_all_four_delimiters_and_multiline_matrix');self.h.setUp();self.addCleanup(self.h.tearDown);self.page=self.h.page
        self.h.record.update(template='chapters',template_name='章节时间轴')
        self.page.route('**/api/workspace/projects/*/subtitle-cues*',lambda r:r.fulfill(json={'resource':'subtitles/fixture.srt','sources':[{'resource':'subtitles/fixture.srt','label':'测试字幕'}],'cues':[{'time':i*.5,'text':f'字幕第{i}行：这是合成字幕。'} for i in range(140)]}))
        def media(r):
            data=self.h.f.audio;headers={'Content-Type':'audio/wav','Accept-Ranges':'bytes'};m=re.fullmatch(r'bytes=(\d+)-(\d*)',r.request.headers.get('range',''))
            if m:
                start=int(m[1]);end=min(int(m[2]) if m[2] else len(data)-1,len(data)-1)
                if start>end:return r.fulfill(status=416,headers={'Content-Range':f'bytes */{len(data)}'},body=b'')
                headers['Content-Range']=f'bytes {start}-{end}/{len(data)}';return r.fulfill(status=206,headers=headers,body=data[start:end+1])
            r.fulfill(headers=headers,body=data)
        self.page.route('**/product-fixture.wav',media);self.h.open(TEXT,learning=True)
        self.video=self.page.locator('video');self.timeline=self.page.locator('.chapter-timeline-player');self.slider=self.page.get_by_role('slider',name='视频时间定位')
        self.page.wait_for_function('()=>{const v=document.querySelector("video");return v.seekable.length&&v.seekable.end(0)>69}')
        self.video.evaluate('(v)=>v.pause()');expect(self.slider).to_have_attribute('aria-disabled','false')
    def at(self,target,tolerance=.4):
        self.page.wait_for_function('([t,d])=>{const v=document.querySelector("video");return !v.seeking&&Math.abs(v.currentTime-t)<d}',arg=[target,tolerance])
        self.video.evaluate('(v)=>v.pause()');self.assertAlmostEqual(self.video.evaluate('(v)=>v.currentTime'),target,delta=tolerance)
    def click_track(self,fraction,below_dot=False):
        self.slider.scroll_into_view_if_needed();b=self.slider.bounding_box()
        self.slider.click(position={'x':max(.1,min(b['width']-.1,b['width']*fraction)),'y':b['height']/2+(12 if below_dot else 0)})
        # Mouse coordinates can be quantized by up to one CSS pixel on narrow rails.
        self.at(fraction*70,70/b['width']+.35)
    def test_arbitrary_positions_and_former_marker_hit_area(self):
        for theme in ('dark','light'):
            self.page.evaluate('(x)=>document.documentElement.dataset.theme=x',theme)
            for width in (1440,768,390,280):
                self.page.set_viewport_size({'width':width,'height':1000})
                self.click_track(.37);expect(self.timeline.locator('.chapter-timeline-intro-meta')).to_have_text('当前章节 · 3 / 9')
                # This is inside an old 32px marker box but outside the visible circle.
                self.click_track(.93,below_dot=True)
                expect(self.timeline.locator('.chapter-timeline-intro-meta')).to_have_text('当前章节 · 8 / 9')
                self.assertTrue(self.page.evaluate('document.documentElement.scrollWidth<=innerWidth'))
    def test_close_markers_are_mouse_clickable_without_neighbor_interception(self):
        for width in (390,280):
            self.page.set_viewport_size({'width':width,'height':1000})
            for i in (7,8,7):
                marker=self.timeline.locator('.chapter-timeline-marker').nth(i)
                marker.click();self.at(TIMES[i]);expect(marker).to_have_attribute('aria-current','true')
        marker=self.timeline.locator('.chapter-timeline-marker').nth(6);marker.focus();marker.press('Enter');self.at(60)
    def test_slider_keyboard_and_mouse_bounds(self):
        self.click_track(0,below_dot=True);self.click_track(1,below_dot=True)
        self.slider.focus();self.slider.press('Home');self.at(0)
        self.slider.press('ArrowRight');self.at(5)
        self.slider.press('ArrowDown');self.at(0)
        self.slider.press('End');self.at(70)
        expect(self.slider).to_have_attribute('aria-valuemax','70')
    def test_seek_updates_intro_and_restores_subtitle_follow(self):
        self.page.get_by_role('tab',name='字幕',exact=True).click();lst=self.page.locator('.subtitle-scroll-list');toggle=self.page.locator('.subtitle-toolbar button').first
        expect(lst.locator('button')).to_have_count(140);lst.hover();self.page.mouse.wheel(0,140);expect(toggle).to_have_attribute('aria-pressed','false')
        self.click_track(.37);expect(toggle).to_have_attribute('aria-pressed','true')
        expect(self.timeline.locator('h4')).to_have_text('第3章');details=self.timeline.locator('details');expect(details).to_have_attribute('open','')
        self.page.wait_for_function('()=>{const l=document.querySelector(".subtitle-scroll-list"),r=l.querySelector("[aria-current=true]");if(!r)return false;const a=l.getBoundingClientRect(),b=r.getBoundingClientRect();return b.top>=a.top-1&&b.bottom<=a.bottom+1}')
        details.locator('summary').click();self.click_track(.5);expect(details.locator('p')).to_be_hidden()
        # The text below the rail is not a seek surface.
        before=self.video.evaluate('(v)=>v.currentTime');self.timeline.locator('h4').click();self.assertAlmostEqual(self.video.evaluate('(v)=>v.currentTime'),before,delta=.05)
    def test_unknown_duration_and_foreign_material_do_not_seek(self):
        original=self.h.f.media;self.h.f.media=lambda:{**original(),'duration':0}
        self.page.add_init_script('Object.defineProperty(HTMLMediaElement.prototype,"duration",{get:()=>NaN,configurable:true})')
        self.h.open(TEXT,learning=True);expect(self.slider).to_have_attribute('aria-disabled','true');expect(self.slider).to_have_attribute('tabindex','-1')
        self.video.evaluate('(v)=>v.pause()');before=self.video.evaluate('(v)=>v.currentTime');self.slider.scroll_into_view_if_needed();b=self.slider.bounding_box();self.page.mouse.click(b['x']+b['width']*.37,b['y']+b['height']/2)
        self.slider.focus();self.slider.press('End');self.assertAlmostEqual(self.video.evaluate('(v)=>v.currentTime'),before,delta=.05)
        self.h.record['video_resource']='videos/other.mp4';self.h.f.parse();self.page.get_by_role('button',name='学习视图',exact=True).click()
        expect(self.page.locator('.learning-materials')).to_contain_text('还没有当前视频的 AI 学习材料');expect(self.slider).to_have_count(0)
