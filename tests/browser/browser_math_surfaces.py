"""Every new math display path, using synthetic media/APIs only; no paid/model calls."""
import unittest
import re,json
from urllib.parse import parse_qs,urlsplit
from playwright.sync_api import expect
from tests.browser import browser_markdown_math as harness
from tests.browser import browser_subtitle_local_default as subtitles

CHAPTERS=r'''## [00:00] 对所有 \(b\) 是否都有解？ — 求解 \(Ax=b\)。
## [00:02] 分量 \(x_1\) — 保留所有下标。
## [00:04] 绝对值 \(|x|\) — 不按公式内的竖线分栏。
## [00:06] 映射 \(f:A\to B\) — 介绍包含 \(\frac{1}{2}\)。
## [00:08] 矩阵 \(\begin{bmatrix}1&2\\3&4\end{bmatrix}\)
'''

class MathSurfacesBrowserTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls): harness.MarkdownMathBrowserTests.setUpClass()
    @classmethod
    def tearDownClass(cls): harness.MarkdownMathBrowserTests.tearDownClass()
    def setUp(self):
        self.h=harness.MarkdownMathBrowserTests('test_all_four_delimiters_and_multiline_matrix');self.h.setUp()
        self.f=self.h.f;self.page=self.h.page
        self.h.record.update(template='chapters',template_name='章节时间轴')
    def tearDown(self):self.h.tearDown()
    def learn(self):self.h.open(CHAPTERS,learning=True)
    def shot(self,name):
        self.page.evaluate('window.scrollTo({top:0,behavior:"instant"})')
        self.page.screenshot(path=str(self.f.output/(name+'.png')),full_page=True,animations='disabled')
    def test_chapters_preserve_math_keyboard_seek_and_current_intro(self):
        self.learn();self.page.get_by_role('tab',name='章节',exact=True).click()
        directory=self.page.locator('.chapter-timeline-directory')
        expect(directory.locator('.katex')).to_have_count(5)
        expect(directory.locator('button button, button a')).to_have_count(0)
        annotations=directory.locator('annotation').all_text_contents()
        self.assertIn('x_1',annotations);self.assertIn('|x|',annotations);self.assertIn(r'f:A\to B',annotations)
        target=directory.locator('.chapter-timeline-list button').nth(3)
        target.focus();self.page.keyboard.press('Enter')
        # A paused player stays paused after a seek by design, and the WAV lands ~80 ms before the
        # boundary, so start playback from the player to cross into the chapter, then stop again.
        self.page.wait_for_function('document.querySelector("video").currentTime >= 5.8')
        self.page.locator('.video-controls .vc-button').first.click()
        self.page.wait_for_function('document.querySelector("video").currentTime >= 6.1')
        self.page.locator('video').evaluate('(v)=>v.pause()')
        expect(target).to_have_attribute('aria-current','true')
        intro=self.page.locator('.chapter-timeline-player .chapter-timeline-intro')
        expect(intro.locator('h4 .katex')).to_have_count(1)
        expect(intro.locator('details')).to_have_attribute('open','');expect(intro.locator('.chapter-timeline-intro-body .katex')).to_have_count(1)
        for theme in ('light','dark'):
            self.page.evaluate('(v)=>document.documentElement.dataset.theme=v',theme)
            for width in (1440,390):
                self.page.set_viewport_size({'width':width,'height':1000})
                self.assertTrue(self.page.evaluate('document.documentElement.scrollWidth<=innerWidth'))
                self.shot(f'chapters-math-{theme}-{width}')
    def test_note_reading_and_editor_preserve_different_representations(self):
        source=r'自己的理解：\(x_1^2+\frac{1}{2}\)。费用 $20 和 $30；`$literal$`。'
        self.f.notes={'1'*32:{'id':'1'*32,'text':source,'time':2,'revision':1}}
        self.learn();self.page.get_by_role('tab',name='个人笔记',exact=True).click()
        notes=self.page.locator('.timestamp-notes-list');expect(notes.locator('.katex')).to_have_count(1)
        notes.get_by_role('button',name='编辑 00:02 的笔记',exact=True).click()
        expect(self.page.locator('#personal-note-text')).to_have_value(source)
        self.assertEqual(self.f.notes['1'*32]['text'],source);self.shot('notes-math-and-source')
    def test_subtitle_rows_formula_seek_long_formula_and_literal_html(self):
        long='+'.join('x_{'+str(i)+'}' for i in range(100))
        cues=[{'time':0,'text':r'分量 \(x_1\)'},{'time':2,'text':'长公式 $'+long+'$'},{'time':4,'text':r'<img src=x onerror=alert(1)> \(\badcommand\)'}]
        self.page.route('**/subtitle-cues?*',lambda route:route.fulfill(json={'resource':'subtitles/fixture.srt','sources':[{'resource':'subtitles/fixture.srt','label':'本地转写'}],'cues':cues}))
        self.learn();self.page.get_by_role('tab',name='字幕',exact=True).click()
        rows=self.page.locator('.subtitle-scroll-list');expect(rows.locator('.katex')).to_have_count(2)
        expect(rows.locator('.md-math-fallback')).to_have_count(1);expect(rows.locator('img,script,button button')).to_have_count(0)
        for width in (1440,768,390,280):
            self.page.set_viewport_size({'width':width,'height':1000})
            self.assertTrue(self.page.evaluate('document.documentElement.scrollWidth<=innerWidth'))
        rows.locator('button').nth(1).click();self.page.wait_for_function('document.querySelector("video").currentTime>=1.9')
        self.page.locator('video').evaluate('(v)=>v.pause()');self.shot('subtitle-math-narrow')
    def test_conversation_heading_and_picker_render_but_input_keeps_tex(self):
        self.f.sessions=[{'id':'f'*32,'title':r'讨论 \(Ax=b\)','title_generated':True,'video_resource':self.f.resource,'origin_record':None,'agent':{'engine':'api','model':'','effort':''},'messages':[],'created_at':'','updated_at':''}]
        self.learn();self.page.get_by_role('tab',name='AI 对话',exact=True).click()
        expect(self.page.locator('.ai-chat-session-heading .katex')).to_have_count(1)
        picker=self.page.get_by_role('button',name='选择或新建对话',exact=True)
        if picker.count():picker.click()
        else:self.page.get_by_role('button',name='选择对话',exact=True).click()
        expect(self.page.locator('.conversation-list .katex')).to_have_count(1)
        field=self.page.get_by_role('textbox',name='向 AI 提问',exact=True)
        field.fill(r'为什么 \(x_1\)？');expect(field).to_have_value(r'为什么 \(x_1\)？')
        self.shot('conversation-math-title')
    def test_semantic_result_title_and_excerpt_math(self):
        self.page.route('**/api/workspace/embedding/settings',lambda route:route.fulfill(json={'base_url':'http://fixture.invalid/v1','model':'fixture','configured':True,'key_saved':False}))
        self.page.route('**/embedding/search',lambda route:route.fulfill(json={'results':[{'time':2,'score':.9,'part':3,'title':r'求解 \(Ax=b\)','text':r'答案包括 \(x_1\)。','resource':'subtitles/fixture.srt'}]}))
        self.learn();self.page.get_by_role('button',name=re.compile(r'^任务中心')).click()
        area=self.page.locator('.task-semantic-search')
        area.get_by_label('检索项目',exact=True).select_option(self.f.project()['id'])
        area.locator('details > summary').click();area.get_by_role('checkbox').check()
        area.get_by_label('语义检索问题').fill('合成查询')
        area.get_by_role('button',name='语义检索',exact=True).click()
        expect(area.locator('ol .katex')).to_have_count(2)
        expect(area.locator('ol button button,ol a')).to_have_count(0)
        self.shot('semantic-math-result')

class SubtitleMathReadingTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):subtitles.SubtitleLocalDefaultBrowserTests.setUpClass()
    @classmethod
    def tearDownClass(cls):subtitles.SubtitleLocalDefaultBrowserTests.tearDownClass()
    def setUp(self):
        self.case=subtitles.SubtitleLocalDefaultBrowserTests('test_local_is_default_on_entry_and_page_reload');self.case.setUp();self.page=self.case.page
        self.source=r'第1行：\(x_1^2\)。'+ '\n'+r'矩阵 \(\begin{bmatrix}1&2\\3&4\end{bmatrix}\)。'
        def response(route):
            query=parse_qs(urlsplit(route.request.url).query);part=int(urlsplit(route.request.url).path.split('/')[4],16)
            versions=[{'resource':f'subtitles/p{part}-local.srt','filename':r'原始文件_x_1.srt','origin':'local','kind':'transcription','language':'en','url':'/fixture'},
                {'resource':f'subtitles/p{part}-translation.srt','filename':'译文.srt','origin':'translation','kind':'translation','source_origin':'local','language':'zh','url':'/fixture'}]
            selected=next((v for v in versions if v['resource']==query.get('subtitle_resource',[''])[0]),versions[0])
            route.fulfill(json={'project_id':f'{part:032x}','has_subtitles':True,'files':versions[:1],'versions':versions,'selected':selected,'text':self.source,'translations':[]})
        self.page.route('**/api/workspace/projects/*/subtitles?*',response)
    def tearDown(self):self.case.tearDown()
    def test_original_and_translation_reading_source_toggle_and_reload(self):
        self.case.open();card=self.case.card
        expect(card.locator('.math-reading-body .katex')).to_have_count(2)
        expect(card.locator('textarea')).not_to_be_visible()
        card.get_by_role('button',name='查看原始文本',exact=True).click()
        expect(card.get_by_label('字幕原文',exact=True)).to_be_visible();expect(card.get_by_label('字幕原文',exact=True)).to_have_value(self.source)
        card.get_by_role('button',name='返回公式阅读',exact=True).click()
        expect(card.locator('.math-reading-body')).to_be_visible()
        expect(card.locator('.subtitle-version-file')).to_contain_text('原始文件_x_1.srt')
        card.get_by_role('button',name='AI翻译字幕',exact=True).click()
        expect(card.locator('.math-reading-body .katex')).to_have_count(2)
        for theme in ('light','dark'):
            self.page.evaluate('(v)=>document.documentElement.dataset.theme=v',theme)
            self.page.set_viewport_size({'width':390,'height':1000});self.assertTrue(self.page.evaluate('document.documentElement.scrollWidth<=innerWidth'))
            self.page.screenshot(path=str(self.case.fixture.base.fixture.output/f'translation-math-{theme}.png'),full_page=True,animations='disabled')
        self.case.reload_archived();expect(card.locator('.math-reading-body .katex')).to_have_count(2)

if __name__=='__main__':unittest.main()
