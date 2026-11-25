"""Native Chromium IME regressions, not fill-only typing. All APIs/inference are synthetic.

Input.imeSetComposition updates the browser's real composition range; a delayed
React controlled-value echo cancels it and leaves pinyin behind (the original bug).
"""
import json
import unittest
from urllib.parse import quote, urlsplit
from playwright.sync_api import expect
from tests.browser import browser_learning_feedback as feedback
from tests.browser import browser_product_improvements as harness


def fixture_type():
    Parent = feedback.fixture_type()
    class Fixture(Parent):
        def setUp(self):
            super().setUp()
            self.fail_send = False
            self.queued = False
            self.task_failed = False
            self.poll_count = 0
            self.pending_answer = None

        def route(self, route):
            url = urlsplit(route.request.url)
            if url.hostname == '127.0.0.1' and url.port == self.server.server_port:
                path, method = url.path, route.request.method
                if path.endswith('/tasks/' + 'ab' * 16) and method == 'GET' and self.pending_answer:
                    self.poll_count += 1
                    if self.poll_count == 1:
                        return route.fulfill(json={'id': 'ab' * 16, 'state': 'running', 'message': '合成任务处理中'})
                    if self.task_failed:
                        return route.fulfill(json={'id': 'ab' * 16, 'state': 'failed', 'message': '合成任务失败，请重试。'})
                    session, answer = self.pending_answer
                    if not session['messages']:
                        session['messages'].append(answer)
                    return route.fulfill(json={'id': 'ab' * 16, 'state': 'completed', 'message': '模拟回答完成',
                        'result': {'conversation': session, 'mode': 'conversation', 'project': self.project()}})
                if path.endswith('/conversations') and method == 'POST':
                    body = route.request.post_data_json
                    self.requests.append((method, path.removeprefix('/api/workspace'), body))
                    self.assertTrue(self.readiness(body['resource'])['available'])
                    value = self.session(f'{15-len(self.sessions):x}' * 32, '新对话', body.get('agent'))
                    self.sessions.insert(0, value)
                    return route.fulfill(json={'conversation': value})
                if path.endswith('/ask-task') and method == 'POST':
                    body = route.request.post_data_json
                    self.requests.append((method, path.removeprefix('/api/workspace'), body))
                    self.assertTrue(body['consent'])
                    self.assertEqual(body['resource'], self.resource)
                    if self.fail_send:
                        return route.fulfill(status=503, json={'detail': '合成的发送失败，请重试。'})
                    session = next(item for item in self.sessions if item['id'] == path.split('/')[-2])
                    self.assertEqual(body['agent'], session['agent'])
                    answer = {'id': str(len(session['messages']) + 1), 'question': body['question'],
                              'answer': '模拟回答：中文问题已完整收到。', 'position': body['position'], 'created_at': ''}
                    if self.queued:
                        self.pending_answer = (session, answer)
                        return route.fulfill(status=202, json={'id': 'ab' * 16, 'state': 'queued', 'message': '合成任务排队中'})
                    session['messages'].append(answer)
                    return route.fulfill(status=202, json={'id': 'e' * 32, 'state': 'completed', 'message': '模拟回答完成',
                        'result': {'conversation': session, 'mode': 'conversation', 'project': self.project()}})
            return super().route(route)

        def session(self, identifier, title, agent=None):
            return {'id': identifier, 'title': title, 'video_resource': self.resource, 'origin_record': None,
                    'agent': agent or {'engine': 'api', 'model': '', 'effort': ''}, 'messages': [], 'created_at': '', 'updated_at': ''}
    return Fixture


class ChatIMEBrowserTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.Fixture = fixture_type()
        cls.Fixture.setUpClass()

    @classmethod
    def tearDownClass(cls):
        cls.Fixture.tearDownClass()

    def setUp(self):
        self.f = self.Fixture('test_settings_cross_tab_updates')
        self.f.setUp()
        self.page = self.f.page
        self.cdp = self.f.context.new_cdp_session(self.page)

    def tearDown(self):
        self.f.tearDown()

    def chat(self):
        self.page.get_by_role('tab', name='AI 对话', exact=True).click()
        field = self.page.locator('#ai-chat-question')
        expect(field).to_be_enabled()
        return field

    def learn(self):
        self.page.goto(self.f.url)
        self.page.get_by_role('textbox', name='视频链接或分享文本').fill(self.f.parsed()['source_url'])
        self.page.get_by_role('button', name='解析视频', exact=True).click()
        expect(self.page.locator('.preview-panel video')).to_be_visible()
        self.page.get_by_role('button', name='学习视图', exact=True).click()
        self.field = self.chat()
        self.field.focus()
        self.page.evaluate('''()=>{window.imeEvents=[];const e=document.querySelector('#ai-chat-question');
          for(const t of ['compositionstart','compositionupdate','compositionend','input'])
          e.addEventListener(t,x=>window.imeEvents.push({type:x.type,data:x.data,value:e.value,isComposing:x.isComposing}));}''')
        return self.field

    def storage(self):
        key = 'video-parser:chat-drafts:v1:' + harness.PROJECT_ID + ':' + quote(self.f.resource, safe='')
        return self.page.evaluate('(k)=>JSON.parse(localStorage.getItem(k))', key)

    def compose_part(self, text):
        self.cdp.send('Input.imeSetComposition', {'text': text, 'selectionStart': len(text), 'selectionEnd': len(text)})
        # Let passive effects/parent polling run: a fill-only test hides the original corruption.
        self.page.wait_for_timeout(35)

    def compose(self, parts, final, left='', right=''):
        for text in parts:
            self.compose_part(text)
            expect(self.field).to_have_value(left + text + right)
            self.assertEqual(self.field.evaluate('e=>e.selectionStart'), len(left + text))
        self.cdp.send('Input.insertText', {'text': final})
        expect(self.field).to_have_value(left + final + right)

    def asks(self):
        return [body for _, path, body in self.f.requests if path.endswith('/ask-task')]

    def send_and_expect(self, question, keyboard=False):
        if keyboard:
            self.field.press('Enter')
        else:
            self.page.locator('#study-panel-chat').get_by_role('button', name='提问', exact=True).click()
        expect(self.page.locator('.ai-chat-answer')).to_contain_text('中文问题已完整收到')
        expect(self.field).to_have_value('')
        expect(self.page.locator('.ai-chat-log > .is-pending')).to_have_count(0)
        self.assertEqual([item['question'] for item in self.asks()], [question])
        self.assertIsNone(self.storage())

    def test_native_candidates_commit_then_first_question_and_successful_answer(self):
        self.learn()
        self.compose(['z', 'zh', 'zhe'], '这')
        self.compose(['g', 'ge'], '个', left='这')
        self.compose(['x', 'xiang', 'xiangmu'], '项目', left='这个')
        self.cdp.send('Input.insertText', {'text': '，怎样理解？'})
        text = '这个项目，怎样理解？'
        expect(self.field).to_have_value(text)
        self.assertEqual(self.storage(), {'new': text})
        events = self.page.evaluate('window.imeEvents')
        self.assertEqual(sum(x['type'] == 'compositionstart' for x in events), 3)
        self.assertEqual(sum(x['type'] == 'compositionend' for x in events), 3)
        (self.f.output / 'native-ime-events.json').write_text(json.dumps(events, ensure_ascii=False, indent=2), encoding='utf-8')
        self.send_and_expect(text)
        self.assertEqual(len(self.f.sessions), 1)
        self.page.screenshot(path=str(self.f.output / 'native-ime-sent.png'))

    def test_incremental_english_cursor_replacement_chinese_punctuation_and_reload(self):
        self.learn()
        self.field.press_sequentially('ABCD', delay=20)
        expect(self.field).to_have_value('ABCD')
        self.field.evaluate('e=>e.setSelectionRange(1,2)')
        self.compose(['z', 'zh', 'zhong'], '中', left='A', right='CD')
        self.compose(['w', 'wen'], '文', left='A中', right='CD')
        self.cdp.send('Input.insertText', {'text': '，'})
        expect(self.field).to_have_value('A中文，CD')
        self.field.press('Backspace')
        self.field.press('ArrowLeft')
        self.compose(['n', 'ni'], '你', left='A中', right='文CD')
        self.page.reload()
        expect(self.chat()).to_have_value('A中你文CD')

    def test_composition_blocks_form_ctrl_meta_enter_and_keycode229_then_sends_once(self):
        self.learn()
        self.compose_part('fa')
        expect(self.field).to_have_value('fa')
        expect(self.page.locator('.ai-chat-actions button[type=submit]')).to_be_disabled()
        self.assertIsNone(self.storage(), 'Never persist unconfirmed pinyin')
        self.field.dispatch_event('keydown', {'key': 'Enter', 'keyCode': 13, 'isComposing': False})
        for modifier in ('ctrlKey', 'metaKey'):
            self.field.dispatch_event('keydown', {'key': 'Enter', modifier: True, 'keyCode': 13, 'isComposing': False})
        self.field.evaluate('e=>e.form.requestSubmit()')
        self.page.wait_for_timeout(70)
        self.assertEqual(self.asks(), [])
        self.assertEqual(self.f.sessions, [])
        self.cdp.send('Input.insertText', {'text': '发送'})
        expect(self.field).to_have_value('发送')
        self.field.dispatch_event('keydown', {'key': 'Enter', 'keyCode': 229, 'isComposing': False})
        self.page.wait_for_timeout(70)
        self.assertEqual(self.asks(), [])
        self.send_and_expect('发送', keyboard=True)

    def test_composition_is_not_truncated_at_question_limit_until_commit(self):
        self.learn()
        base = '甲' * 1999
        self.field.fill(base)
        self.compose_part('zhongwen')
        expect(self.field).to_have_value(base + 'zhongwen')
        self.assertEqual(self.storage(), {'new': base})
        self.cdp.send('Input.insertText', {'text': '中文'})
        expect(self.field).to_have_value(base + '中')
        self.assertEqual(self.storage(), {'new': base + '中'})

    def test_cancel_composition_does_not_leave_pinyin(self):
        self.learn()
        self.field.press_sequentially('ABC', delay=15)
        self.compose_part('zhong')
        expect(self.field).to_have_value('ABCzhong')
        self.cdp.send('Input.imeSetComposition', {'text': '', 'selectionStart': 0, 'selectionEnd': 0})
        expect(self.field).to_have_value('ABC')
        self.assertEqual(self.storage(), {'new': 'ABC'})
        expect(self.page.locator('.ai-chat-actions button[type=submit]')).to_be_enabled()

    def test_external_api_sends_without_repeated_consent_or_footer_hints(self):
        self.f.ai.update(local=False, consented=False, base_url='https://fixture.invalid/v1')
        self.learn()
        self.compose(['w', 'wen'], '问')
        expect(self.page.locator('.ai-chat-actions button[type=submit]')).to_be_enabled()
        expect(self.page.locator('.ai-chat-consent')).to_have_count(0)
        expect(self.page.locator('.ai-chat-hint')).to_have_count(0)
        self.send_and_expect('问', keyboard=True)

    def test_native_model_selection_sends_without_consent_checkbox(self):
        self.f.ai['configured'] = False
        self.f.context.add_init_script("localStorage.setItem('video-parser:agent-preferences',JSON.stringify({enabled:null,target:{engine:'codex',model:'codex-fixture',effort:''}}))")
        self.learn()
        self.page.get_by_role('combobox', name='引擎与模型', exact=True).select_option('codex|codex-fixture')
        self.page.get_by_role('combobox', name='推理强度', exact=True).select_option('high')
        self.field.focus()
        self.compose(['w', 'wen'], '问')
        expect(self.page.locator('.ai-chat-actions button[type=submit]')).to_be_enabled()
        expect(self.page.locator('.ai-chat-consent')).to_have_count(0)
        self.send_and_expect('问')
        self.assertEqual(self.asks()[0]['agent'], {'engine': 'codex', 'model': 'codex-fixture', 'effort': 'high'})

    def test_native_committed_question_recovers_after_failed_send_and_reload(self):
        self.f.fail_send = True
        self.learn()
        self.compose(['c', 'ce', 'ceshi'], '测试')
        self.page.locator('.ai-chat-actions button[type=submit]').click()
        expect(self.page.get_by_role('alert').filter(has_text='合成的发送失败')).to_be_visible()
        expect(self.field).to_have_value('测试')
        self.assertEqual([item['question'] for item in self.asks()], ['测试'])
        self.page.reload()
        expect(self.chat()).to_have_value('测试')
        self.assertEqual(self.storage(), {'f' * 32: '测试'})

    def test_queued_send_polls_to_success_without_duplicate_submission(self):
        self.f.queued = True
        self.learn()
        self.compose(['w', 'wen'], '问')
        self.page.locator('.ai-chat-actions button[type=submit]').click()
        expect(self.page.locator('.ai-chat-log > .is-pending')).to_have_count(1)
        expect(self.page.get_by_role('button', name='停止回答', exact=True)).to_be_visible()
        self.field.evaluate("e=>e.form.dispatchEvent(new SubmitEvent('submit', {bubbles:true,cancelable:true}))")
        expect(self.page.locator('.ai-chat-answer')).to_contain_text('中文问题已完整收到')
        expect(self.page.locator('.ai-chat-log > .is-pending')).to_have_count(0)
        expect(self.field).to_have_value('')
        self.assertEqual([item['question'] for item in self.asks()], ['问'])
        self.assertGreaterEqual(self.f.poll_count, 2)

    def test_queued_task_failure_restores_confirmed_chinese_question(self):
        self.f.queued = True
        self.f.task_failed = True
        self.learn()
        self.compose(['w', 'wen'], '问')
        self.page.locator('.ai-chat-actions button[type=submit]').click()
        expect(self.page.get_by_role('alert').filter(has_text='合成任务失败')).to_be_visible()
        expect(self.field).to_have_value('问')
        self.page.reload()
        expect(self.chat()).to_have_value('问')
        self.assertEqual([item['question'] for item in self.asks()], ['问'])
        self.assertGreaterEqual(self.f.poll_count, 2)

    def test_ctrl_and_meta_enter_insert_newlines_at_selection_then_enter_sends(self):
        self.learn()
        self.field.press_sequentially('ABCD', delay=10)
        self.field.evaluate('e=>e.setSelectionRange(1,3)')
        self.field.press('Control+Enter')
        expect(self.field).to_have_value('A\nD')
        self.assertEqual(self.field.evaluate('e=>e.selectionStart'), 2)
        self.field.press('Meta+Enter')
        expect(self.field).to_have_value('A\n\nD')
        self.assertEqual(self.field.evaluate('e=>e.selectionStart'), 3)
        self.assertEqual(self.asks(), [])
        self.assertEqual(self.storage(), {'new': 'A\n\nD'})
        self.send_and_expect('A\n\nD', keyboard=True)

    def test_shift_enter_retains_native_newline_and_empty_enter_never_sends(self):
        self.learn()
        self.field.press('Enter')
        expect(self.field).to_have_value('')
        self.assertEqual(self.asks(), [])
        self.field.press_sequentially('AB', delay=10)
        self.field.press('Shift+Enter')
        self.field.press_sequentially('CD', delay=10)
        expect(self.field).to_have_value('AB\nCD')
        self.assertEqual(self.asks(), [])
        self.send_and_expect('AB\nCD', keyboard=True)

    def test_ctrl_enter_at_length_limit_does_not_truncate_tail(self):
        self.learn()
        text = '甲' * 1999 + '尾'
        self.field.fill(text)
        self.field.evaluate('e=>e.setSelectionRange(1,1)')
        self.field.press('Control+Enter')
        expect(self.field).to_have_value(text)
        self.assertEqual(self.field.evaluate('e=>e.selectionStart'), 1)
        self.assertEqual(self.asks(), [])
        self.field.evaluate('e=>e.setSelectionRange(1,2)')
        self.field.press('Control+Enter')
        expect(self.field).to_have_value('甲\n' + text[2:])
        self.assertEqual(self.field.evaluate('e=>e.selectionStart'), 2)

    def test_live_conversation_switch_preserves_each_committed_chinese_draft(self):
        self.f.sessions = [self.f.session('f' * 32, '对话一'), self.f.session('d' * 32, '对话二')]
        self.learn()
        self.compose(['y', 'yi'], '一')
        self.page.get_by_role('button', name='选择或新建对话', exact=True).click()
        self.page.get_by_role('button', name='对话二', exact=True).click()
        expect(self.field).to_have_value('')
        self.field.focus()
        self.compose(['e', 'er'], '二')
        self.page.get_by_role('button', name='选择或新建对话', exact=True).click()
        self.page.get_by_role('button', name='对话一', exact=True).click()
        expect(self.field).to_have_value('一')
        self.assertEqual(self.storage(), {'f' * 32: '一', 'd' * 32: '二'})

    def test_storage_failure_does_not_break_native_composition_or_confirmed_text(self):
        self.f.context.add_init_script("const original=Storage.prototype.setItem;Storage.prototype.setItem=function(k,...a){if(k.startsWith('video-parser:chat-drafts:'))throw new DOMException('blocked','QuotaExceededError');return original.call(this,k,...a)}")
        self.learn()
        self.compose(['z', 'zh', 'zhong'], '中')
        expect(self.page.get_by_role('status').filter(has_text='草稿暂时无法保存')).to_be_visible()
        expect(self.field).to_have_value('中')


if __name__ == '__main__':
    unittest.main(verbosity=2)
