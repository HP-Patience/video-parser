"""Native Chromium media controls and explicit subtitle follow intent; fixture-only."""
import re,unittest
from playwright.sync_api import expect
from tests.browser import browser_markdown_math as math

class SubtitleFollowSeekTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):math.MarkdownMathBrowserTests.setUpClass()
    @classmethod
    def tearDownClass(cls):math.MarkdownMathBrowserTests.tearDownClass()
    def setUp(self):
        self.h=math.MarkdownMathBrowserTests('test_all_four_delimiters_and_multiline_matrix');self.h.setUp();self.addCleanup(self.h.tearDown);self.page=self.h.page
        self.page.route('**/api/workspace/projects/*/subtitle-cues*',lambda r:r.fulfill(json={'resource':'subtitles/fixture.srt','sources':[{'resource':'subtitles/fixture.srt','label':'测试字幕'}],'cues':[{'time':i*.5,'text':f'字幕第{i}行：'+('较长字幕，用于验证换行之后仍然定位在可见区域。'*2 if i%4==1 else '这是合成的字幕测试内容。')} for i in range(140)]}))
        def media(r):
            data=self.h.f.audio;headers={'Content-Type':'audio/wav','Accept-Ranges':'bytes'};m=re.fullmatch(r'bytes=(\d+)-(\d*)',r.request.headers.get('range',''))
            if m:
                start=int(m[1]);end=min(int(m[2]) if m[2] else len(data)-1,len(data)-1)
                if start>end:return r.fulfill(status=416,headers={'Content-Range':f'bytes */{len(data)}'},body=b'')
                headers['Content-Range']=f'bytes {start}-{end}/{len(data)}';return r.fulfill(status=206,headers=headers,body=data[start:end+1])
            r.fulfill(headers=headers,body=data)
        self.page.route('**/product-fixture.wav',media)
        self.h.open(learning=True);self.page.get_by_role('tab',name='字幕',exact=True).click()
        self.list=self.page.locator('.subtitle-scroll-list');self.toggle=self.page.locator('.subtitle-toolbar button').first;self.video=self.page.locator('video')
        expect(self.list.locator('button')).to_have_count(140);self.page.wait_for_function('()=>{const v=document.querySelector("video");return v.seekable.length&&v.seekable.end(0)>65}')
        self.video.evaluate('(v)=>v.pause()')
    def on(self):expect(self.toggle).to_have_attribute('aria-pressed','true')
    def off(self):expect(self.toggle).to_have_attribute('aria-pressed','false')
    def visible_current(self):
        self.page.wait_for_function('()=>{const l=document.querySelector(".subtitle-scroll-list"),r=l.querySelector("[aria-current=true]");if(!r)return false;const a=l.getBoundingClientRect(),b=r.getBoundingClientRect();return b.top>=a.top-1&&b.bottom<=a.bottom+1}')
    def pause_by_wheel(self):
        self.list.hover();self.page.mouse.wheel(0,170);self.off()
    def timeline(self):
        # The custom bar replaced the native controls, so the seek surface is .vc-track now.
        box=self.page.locator('.vc-track').bounding_box()
        return box['x']+6,(box['y']+box['height']/2),box['width']-12
    def native_click(self,fraction):
        x,y,w=self.timeline();self.page.mouse.click(x+w*fraction,y)
        self.page.wait_for_function('()=>!document.querySelector("video").seeking&&document.querySelector("video").currentTime>5')
        self.video.evaluate('(v)=>v.pause()')
    def test_native_progress_click_resumes_manual_pause(self):
        self.pause_by_wheel();self.native_click(.65)
        self.assertGreater(self.video.evaluate('(v)=>v.currentTime'),30);self.on();self.visible_current()
    def test_native_drag_and_video_arrow_keys_keep_follow_enabled(self):
        self.pause_by_wheel();x,y,w=self.timeline();self.page.mouse.move(x+w*.2,y);self.page.mouse.down();self.page.mouse.move(x+w*.75,y,steps=8);self.page.mouse.up()
        self.page.wait_for_function('()=>!document.querySelector("video").seeking&&document.querySelector("video").currentTime>40');self.video.evaluate('(v)=>v.pause()');self.on();self.visible_current()
        self.pause_by_wheel();before=self.video.evaluate('(v)=>v.currentTime');self.video.focus();self.video.press('ArrowLeft')
        self.page.wait_for_function('(t)=>Math.abs(document.querySelector("video").currentTime-(t-5))<.5',arg=before);self.on();self.visible_current()
    def test_manual_browsing_stays_paused_during_normal_playback(self):
        self.video.evaluate('(v)=>{v.currentTime=20;v.pause()}');self.page.wait_for_function('!document.querySelector("video").seeking');self.pause_by_wheel()
        self.video.evaluate('(v)=>v.play()');self.page.wait_for_function('document.querySelector("video").currentTime>21');self.video.evaluate('(v)=>v.pause()');self.off()
        self.toggle.click();self.on();self.visible_current()
        self.list.focus();self.list.press('PageDown');self.off();self.toggle.click();self.on()
        self.list.dispatch_event('touchmove');self.off();self.toggle.click();self.on()
    def test_programmatic_scroll_seek_settled_and_responsive_alignment(self):
        self.on();self.list.evaluate('(e)=>{e.scrollTop=120;e.dispatchEvent(new Event("scroll",{bubbles:true}))}');self.on()
        for width in (1440,390,280):
            self.page.set_viewport_size({'width':width,'height':1000})
            self.video.evaluate('(v)=>{v.currentTime=32;v.dispatchEvent(new Event("seeking"));}');self.page.wait_for_function('!document.querySelector("video").seeking');self.on();self.visible_current()
            self.assertTrue(self.page.evaluate('document.documentElement.scrollWidth<=innerWidth'))
        # A manual pause made after seeking begins must not be undone by seeked.
        self.video.dispatch_event('seeking');self.on();self.list.dispatch_event('wheel',{'deltaY':80});self.off();self.video.dispatch_event('seeked');self.off()
        self.page.get_by_role('tab',name='个人笔记',exact=True).click();self.video.evaluate('(v)=>{v.currentTime=48;v.dispatchEvent(new Event("seeking"));}')
        self.page.get_by_role('tab',name='字幕',exact=True).click();self.on();self.visible_current()
