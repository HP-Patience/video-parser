"""Proofreading in the learning panel: double-click, draft, publish a new version. Fixture-only."""
import unittest
from urllib.parse import parse_qs, urlsplit
from playwright.sync_api import expect
from tests.browser import browser_markdown_math as math

ORIGINAL = [{'time': index * 5, 'text': f'原始字幕第{index}条'} for index in range(6)]
CORRECTED = 'subtitles/线性代数 · 合成测试课程-中文-校对.srt'


class SubtitleProofreadBrowserTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls): math.MarkdownMathBrowserTests.setUpClass()
    @classmethod
    def tearDownClass(cls): math.MarkdownMathBrowserTests.tearDownClass()

    def setUp(self):
        self.h = math.MarkdownMathBrowserTests('test_all_four_delimiters_and_multiline_matrix')
        self.h.setUp(); self.addCleanup(self.h.tearDown); self.page = self.h.page
        self.saved = []
        self.cues = [dict(row) for row in ORIGINAL]
        self.labels = {'subtitles/fixture.srt': '平台字幕'}
        def cues(route):
            chosen = parse_qs(urlsplit(route.request.url).query).get('resource', [''])[0] or 'subtitles/fixture.srt'
            route.fulfill(json={'resource': chosen, 'cues': self.cues, 'sources': [
                {'resource': key, 'label': label, 'kind': 'correction' if key == CORRECTED else 'subtitle', 'language': 'zh'}
                for key, label in self.labels.items()]})
        def edits(route):
            body = route.request.post_data_json
            self.saved.append(body)
            self.cues = [dict(row, text=next((edit['text'] for edit in body['edits'] if edit['time'] == row['time']), row['text']))
                         for row in self.cues]
            self.labels[CORRECTED] = '人工校对（中文）'
            route.fulfill(json={'resource': CORRECTED, 'filename': '线性代数 · 合成测试课程-中文-校对.srt',
                                'edited': len(body['edits']), 'cues': len(self.cues)})
        self.page.route('**/api/workspace/projects/*/subtitle-cues*', cues)
        self.page.route('**/api/workspace/projects/*/subtitle-edits', edits)
        self.h.open(learning=True)
        self.page.get_by_role('tab', name='字幕', exact=True).click()
        self.list = self.page.locator('.subtitle-scroll-list')
        self.editor = self.page.locator('.subtitle-cue-editor')
        expect(self.list.locator('button')).to_have_count(6)
        self.page.locator('video').evaluate('(v) => v.pause()')

    def test_double_click_opens_the_cue_and_escape_cancels(self):
        self.list.locator('button').first.dblclick()
        expect(self.editor).to_have_count(1)
        expect(self.editor.locator('textarea')).to_have_value('原始字幕第0条')
        expect(self.editor.locator('textarea')).to_be_focused()
        self.editor.locator('textarea').press('Escape')
        expect(self.editor).to_have_count(0)
        expect(self.list.locator('button').first).to_contain_text('原始字幕第0条')

    def test_keyboard_f2_opens_the_same_editor(self):
        first = self.list.locator('button').first
        first.focus(); first.press('F2')
        expect(self.editor.locator('textarea')).to_be_focused()
        self.editor.locator('textarea').press('Escape')

    def test_help_button_replaces_the_hint_line(self):
        # The permanent hint line moved into a popover, so the toolbar and list start higher.
        expect(self.page.locator('.subtitle-edit-hint')).to_have_count(0)
        trigger = self.page.get_by_role('button', name='校对说明', exact=True)
        expect(trigger).to_have_attribute('aria-expanded', 'false')
        trigger.click()
        popover = self.page.locator('#subtitle-help')
        expect(popover).to_be_visible()
        expect(popover).to_contain_text('双击（或按 F2）任意条目可校对')
        expect(trigger).to_have_attribute('aria-expanded', 'true')
        self.page.keyboard.press('Escape')
        expect(popover).to_have_count(0)
        self.assertEqual(self.saved, [])

    def test_draft_publishes_one_new_version_and_keeps_the_rest(self):
        self.list.locator('button').first.dblclick()
        self.editor.locator('textarea').fill('校对后的第一条（Transformer）')
        self.editor.locator('textarea').press('Control+Enter')
        expect(self.editor).to_have_count(0)
        expect(self.list.locator('button').first).to_contain_text('校对后的第一条（Transformer）')
        expect(self.list.locator('.subtitle-draft-mark')).to_have_count(1)
        self.page.get_by_role('button', name='保存为新版本（1 处修改）', exact=True).click()
        expect(self.page.locator('.subtitle-edit-notice')).to_contain_text('已另存校对版本')
        self.assertEqual(self.saved, [{'resource': 'subtitles/fixture.srt', 'edits': [{'time': 0, 'text': '校对后的第一条（Transformer）'}]}])
        # The panel switches to the published version, which is offered next to the original.
        expect(self.page.get_by_label('字幕版本')).to_have_value(CORRECTED)
        expect(self.page.get_by_label('字幕版本').locator('option')).to_have_count(2)
        expect(self.list.locator('.subtitle-draft-mark')).to_have_count(0)
        expect(self.list.locator('button').first).to_contain_text('校对后的第一条（Transformer）')
        expect(self.list.locator('button').nth(1)).to_contain_text('原始字幕第1条')

    def test_discarding_drafts_restores_the_original_text(self):
        self.list.locator('button').first.dblclick()
        self.editor.locator('textarea').fill('临时修改')
        self.editor.locator('textarea').press('Control+Enter')
        self.page.get_by_role('button', name='放弃修改', exact=True).click()
        expect(self.list.locator('button').first).to_contain_text('原始字幕第0条')
        expect(self.list.locator('.subtitle-draft-mark')).to_have_count(0)
        self.assertEqual(self.saved, [])

    def test_empty_cue_is_refused_before_any_request(self):
        self.list.locator('button').first.dblclick()
        self.editor.locator('textarea').fill('   ')
        self.editor.get_by_role('button', name='完成', exact=True).click()
        expect(self.page.get_by_role('alert')).to_contain_text('不能为空')
        expect(self.editor).to_have_count(1)
        self.assertEqual(self.saved, [])


if __name__ == '__main__': unittest.main()
