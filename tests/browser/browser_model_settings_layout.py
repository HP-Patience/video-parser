"""Settings layout and save-boundary regressions. All API/model traffic is intercepted."""
import json
import unittest
from urllib.parse import urlsplit
from playwright.sync_api import expect
from tests.browser import browser_product_improvements as harness


def fixture_type():
    class Fixture(harness.ProductImprovementsBrowserTests):
        def setUp(self):
            super().setUp()
            self.embedding = {'base_url': 'http://127.0.0.1:19998/v1', 'model': 'retrieval-fixture', 'key_saved': False, 'configured': True}
            self.fail_save = False
            self.fail_agents = False
            self.fail_embedding_load = False

        def route(self, route):
            req = route.request; url = urlsplit(req.url)
            if url.hostname == '127.0.0.1' and url.port == self.server.server_port:
                path = url.path.removeprefix('/api/workspace')
                if path == '/embedding/settings':
                    self.requests.append((req.method, path, req.post_data_json if req.post_data else None))
                    if req.method == 'GET':
                        if self.fail_embedding_load: return route.fulfill(status=503, json={'detail': '检索设置读取失败（合成）'})
                        return route.fulfill(json=self.embedding)
                    if self.fail_save: return route.fulfill(status=503, json={'detail': '保存失败（合成）'})
                    body = req.post_data_json
                    self.embedding.update(base_url=body['base_url'], model=body['model'], configured=True,
                        key_saved=False if body.get('clear_key') else bool(body.get('api_key')) or self.embedding['key_saved'])
                    return route.fulfill(json=self.embedding)
                if path == '/ai/settings' and req.method == 'POST':
                    self.requests.append((req.method, path, req.post_data_json))
                    if self.fail_save: return route.fulfill(status=503, json={'detail': '保存失败（合成）'})
                    body = req.post_data_json
                    self.ai.update({key: body[key] for key in ('provider', 'base_url', 'model', 'price_input', 'price_output')})
                    return route.fulfill(json=self.ai)
                if path == '/ai/settings/test':
                    self.requests.append((req.method, path, req.post_data_json))
                    return route.fulfill(json={'latency_ms': 42, 'model': self.ai['model']})
                if path == '/ai/agents/enabled':
                    self.requests.append((req.method, path, req.post_data_json))
                    if self.fail_agents: return route.fulfill(status=503, json={'detail': '工具开关保存失败（合成）'})
                    body = req.post_data_json
                    next(item for item in self.catalog['engines'] if item['id'] == body['engine'])['enabled'] = body['enabled']
                    return route.fulfill(json=self.catalog)
            return super().route(route)
    return Fixture


class ModelSettingsLayoutTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls): harness.ProductImprovementsBrowserTests.setUpClass()
    @classmethod
    def tearDownClass(cls): harness.ProductImprovementsBrowserTests.tearDownClass()
    def setUp(self):
        self.f = fixture_type()('test_settings_cross_tab_updates'); self.f.setUp(); self.page = self.f.page
    def tearDown(self): self.f.tearDown()
    def open(self):
        self.page.goto(self.f.url + '?page=settings&settings_section=ai')
        expect(self.page.locator('#learning-default-model')).to_be_visible()
        expect(self.page.locator('.embedding-settings')).to_contain_text('retrieval-fixture')
    def posts(self): return [(path, body) for method, path, body in self.f.requests if method == 'POST']
    def api(self): self.page.locator('button[aria-controls="api-source-config"]').click()
    def embedding(self): self.page.locator('button[aria-controls="embedding-source-config"]').click()
    def manager(self, name='codex'): self.page.locator(f'button[aria-controls="models-{name}"]').click()

    def test_summary_order_and_no_automatic_writes(self):
        self.open()
        expect(self.page.locator('.model-sources > .model-source')).to_have_count(5)
        for selector in ('#api-source-config', '#models-claude', '#models-codex', '#models-opencode', '#models-dsh', '#embedding-source-config'):
            expect(self.page.locator(selector)).to_be_hidden()
        default, sources, embedding = [self.page.locator(s).bounding_box() for s in ('.learning-default-control', '.model-sources', '.embedding-settings')]
        self.assertLess(default['y'], sources['y']); self.assertGreater(embedding['y'], sources['y'] + sources['height'])
        self.assertEqual(self.posts(), [])
        row = self.page.locator('.model-source').filter(has=self.page.locator('#source-codex'))
        switch_box = row.locator('.agent-tool-switch').bounding_box()
        button_box = row.get_by_role('button').bounding_box()
        self.assertLess(abs(switch_box['y'] + switch_box['height']/2 - button_box['y'] - button_box['height']/2), 1)
        self.f.fit('model-settings-summary')

    def test_default_cannot_be_hidden_and_disabling_tool_does_not_fallback(self):
        self.open(); self.page.locator('#learning-default-model').select_option('codex|codex-fixture'); self.manager()
        checkbox = self.page.locator('#models-codex li').filter(has_text='Codex fixture').get_by_role('checkbox')
        expect(checkbox).to_be_checked(); expect(checkbox).to_be_disabled()
        self.page.get_by_role('switch', name='Codex', exact=True).uncheck()
        expect(self.page.locator('#learning-default-model')).to_have_value('codex|codex-fixture')
        expect(self.page.locator('#learning-default-state')).to_contain_text('不会自动切换模型')
        other = self.page.locator('#models-codex li').filter(has_text='跟随工具默认模型').get_by_role('checkbox')
        other.uncheck()
        self.assertEqual(self.posts(), [('/ai/agents/enabled', {'engine': 'codex', 'enabled': False})])
        self.page.get_by_role('switch', name='Codex', exact=True).check()
        expect(other).not_to_be_checked()
        expect(self.page.locator('#learning-default-model')).to_have_value('codex|codex-fixture')

    def test_tool_error_rolls_back_and_remains_visible(self):
        self.f.fail_agents = True; self.open()
        self.page.get_by_role('switch', name='Codex', exact=True).uncheck()
        expect(self.page.get_by_role('alert')).to_contain_text('工具开关保存失败')
        expect(self.page.get_by_role('switch', name='Codex', exact=True)).to_be_checked()

    def test_discovery_is_explicit_and_scoped_to_one_tool(self):
        self.open(); self.manager('claude')
        self.assertEqual(self.posts(), [])
        self.page.locator('#models-claude').get_by_role('button', name='读取本机模型', exact=True).click()
        expect(self.page.locator('.learning-targets > [role=status]')).to_contain_text('已读取本机模型列表')
        self.assertEqual(self.posts(), [('/ai/agents/discover', {'confirm': True, 'engine': 'claude', 'model': ''})])

    def test_api_draft_survives_collapse_and_save_does_not_start_or_test(self):
        self.open(); self.api(); self.page.locator('#ai-model').fill('new-fixture')
        self.api(); expect(self.page.locator('.settings-ai')).to_contain_text('有未保存的连接修改')
        self.api(); expect(self.page.locator('#ai-model')).to_have_value('new-fixture')
        expect(self.page.get_by_role('button', name='保存并测试', exact=True)).to_be_visible()
        self.page.locator('.settings-ai').get_by_role('button', name='保存', exact=True).click()
        expect(self.page.locator('.settings-ai > [role=status]')).to_contain_text('已保存')
        self.assertEqual([path for path, _ in self.posts()], ['/ai/settings'])
        self.assertEqual(self.f.embedding['model'], 'retrieval-fixture')

    def test_api_failed_save_does_not_test_and_error_survives_collapse(self):
        self.f.fail_save = True; self.open(); self.api(); self.page.locator('#ai-model').fill('unsaved-fixture')
        self.page.get_by_role('button', name='保存并测试', exact=True).click()
        expect(self.page.get_by_role('alert')).to_contain_text('保存失败')
        self.api(); expect(self.page.get_by_role('alert')).to_be_visible()
        self.assertEqual([path for path, _ in self.posts()], ['/ai/settings'])
        self.api(); expect(self.page.locator('#ai-model')).to_have_value('unsaved-fixture')

    def test_api_save_and_test_has_explicit_order(self):
        self.open(); self.api(); self.page.locator('#ai-model').fill('test-fixture')
        self.page.get_by_role('button', name='保存并测试', exact=True).click()
        expect(self.page.locator('.settings-ai > [role=status]')).to_contain_text('连接正常')
        self.assertEqual([path for path, _ in self.posts()], ['/ai/settings', '/ai/settings/test'])

    def test_embedding_key_clear_is_conditional_confirmed_and_independent(self):
        self.f.embedding['key_saved'] = True; self.open(); self.embedding()
        self.page.get_by_role('button', name='清除已保存的密钥', exact=True).click()
        self.assertEqual(self.posts(), [])
        self.page.locator('.embedding-clear-confirm').get_by_role('button', name='取消', exact=True).click()
        self.assertEqual(self.posts(), [])
        self.page.get_by_role('button', name='清除已保存的密钥', exact=True).click()
        self.page.get_by_role('button', name='确认清除并保存', exact=True).click()
        expect(self.page.locator('.embedding-settings')).to_contain_text('密钥已清除')
        expect(self.page.get_by_role('button', name='清除已保存的密钥', exact=True)).to_have_count(0)
        self.assertEqual(len(self.posts()), 1); self.assertEqual(self.posts()[0][0], '/embedding/settings')
        self.assertTrue(self.posts()[0][1]['clear_key']); self.assertEqual(self.f.ai['model'], 'llama-fixture')

    def test_embedding_save_keeps_api_draft_and_never_builds_index(self):
        self.open(); self.api(); self.page.locator('#ai-model').fill('api-draft'); self.embedding()
        expect(self.page.get_by_role('button', name='清除已保存的密钥', exact=True)).to_have_count(0)
        self.page.locator('#embedding-model').fill('new-retrieval')
        self.embedding(); expect(self.page.locator('.embedding-settings')).to_contain_text('有未保存的检索配置')
        self.embedding(); self.page.get_by_role('button', name='保存检索配置', exact=True).click()
        expect(self.page.locator('.embedding-settings > [role=status]')).to_contain_text('已保存')
        expect(self.page.locator('#ai-model')).to_have_value('api-draft')
        self.assertEqual([path for path, _ in self.posts()], ['/embedding/settings'])
        self.assertEqual(self.f.ai['model'], 'llama-fixture')

    def test_failed_embedding_load_cannot_overwrite_saved_settings(self):
        self.f.fail_embedding_load = True
        self.page.goto(self.f.url + '?page=settings&settings_section=ai')
        expect(self.page.locator('.embedding-settings [role=alert]')).to_contain_text('读取失败')
        self.embedding(); expect(self.page.locator('#embedding-model')).to_have_count(0)
        expect(self.page.get_by_role('button', name='保存检索配置', exact=True)).to_have_count(0)
        self.assertEqual(self.posts(), [])

    def test_model_picker_trim_and_settings_navigation_preserve_materials(self):
        self.f.with_media = True; self.f.parse()
        expect(self.page.get_by_role('button', name='返回学习', exact=True)).to_have_count(0)
        self.page.get_by_role('tab', name='AI 分析', exact=True).click()
        expect(self.page.locator('.independent-conversations')).to_have_count(0)
        expect(self.page.locator('.analysis-form .agent-model-row button, .analysis-form .agent-mode')).to_have_count(0)
        self.page.get_by_role('button', name='设置', exact=True).click()
        self.page.get_by_role('button', name='AI 模型', exact=True).click()
        expect(self.page.locator('#model-sources-title')).to_be_visible()
        expect(self.page.locator('#ai-provider')).to_be_hidden()
        self.page.get_by_role('button', name='学习视图', exact=True).click()
        expect(self.page.locator('.learning-materials')).to_be_visible()
        self.page.get_by_role('tab', name='AI 对话', exact=True).click()
        expect(self.page.locator('#study-panel-chat .ai-chat')).to_be_visible()

    def test_english_keyboard_and_dark_responsive_layout(self):
        self.f.context.add_init_script("localStorage.setItem('vp-language','en')")
        self.open()
        expect(self.page.get_by_role('combobox', name='Default model', exact=True)).to_be_visible()
        expect(self.page.get_by_role('heading', name='Model sources', exact=True)).to_be_visible()
        button = self.page.locator('button[aria-controls="api-source-config"]'); button.focus(); button.press('Enter')
        expect(button).to_have_attribute('aria-expanded', 'true')
        self.page.get_by_role('button', name='Switch to dark theme', exact=True).click()
        expect(self.page.locator('html')).to_have_attribute('data-theme', 'dark')
        self.page.wait_for_function("getComputedStyle(document.body).backgroundColor === 'rgb(10, 10, 10)' && getComputedStyle(document.body).color === 'rgb(250, 250, 250)'")
        self.f.fit('model-settings-en-dark')
        self.assertEqual(self.posts(), [])


if __name__ == '__main__': unittest.main(verbosity=2)
