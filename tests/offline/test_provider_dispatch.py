"""Explicit provider dispatch tests; no network, model process, or saved credentials."""
from pathlib import Path
from unittest import TestCase
from unittest.mock import patch

from video_parser.services import local_agents, model_calls


class ProviderDispatchTests(TestCase):
    def setUp(self):
        self.settings = {
            'p1': {'provider_id': 'p1', 'provider': 'custom', 'name': '供应商一',
                   'base_url': 'https://one.invalid/v1', 'model': 'same-model',
                   'api_key': 'secret-one', 'startup': {}},
            'p2': {'provider_id': 'p2', 'provider': 'custom', 'name': '供应商二',
                   'base_url': 'https://two.invalid/v1', 'model': 'same-model',
                   'api_key': 'secret-two', 'startup': {}},
        }

    def resolve(self, target=None):
        if target and target.get('provider_id'):
            return self.settings[target['provider_id']]
        return self.settings['p1']

    def test_explicit_suppliers_with_same_model_are_dispatched_independently(self):
        calls = []

        def fake_model(messages, settings, **kwargs):
            calls.append(settings)
            return 'ok', {}

        with patch.object(model_calls.ai_settings, 'resolve', side_effect=self.resolve), \
             patch.object(model_calls.ai_settings, 'configured', return_value=True), \
             patch('video_parser.services.ai_analysis.call_model', side_effect=fake_model):
            first = model_calls.call([], {}, {'engine': 'api', 'provider_id': 'p1', 'model': 'same-model', 'effort': ''}, Path('.'))
            second = model_calls.call([], {}, {'engine': 'api', 'provider_id': 'p2', 'model': 'same-model', 'effort': ''}, Path('.'))

        self.assertEqual([item['provider_id'] for item in calls], ['p1', 'p2'])
        self.assertEqual([item['base_url'] for item in calls], ['https://one.invalid/v1', 'https://two.invalid/v1'])
        self.assertEqual(first.model, second.model, 'same model names remain valid across suppliers')
        self.assertNotEqual(model_calls.api_binding(self.settings['p1']), model_calls.api_binding(self.settings['p2']))
        self.assertNotIn('api_key', model_calls.api_binding(self.settings['p1']))

    def test_explicit_selection_ignores_global_current_switch(self):
        observed = []
        selected = {'engine': 'api', 'provider_id': 'p2', 'model': 'same-model', 'effort': ''}

        with patch.object(model_calls.ai_settings, 'resolve', side_effect=self.resolve), \
             patch.object(model_calls.ai_settings, 'configured', return_value=True), \
             patch('video_parser.services.ai_analysis.call_model', side_effect=lambda messages, settings, **kwargs: (observed.append(settings) or ('ok', {}))):
            with patch.object(model_calls.ai_settings, 'current', return_value=self.settings['p1']):
                model_calls.call([], {}, selected, Path('.'))

        self.assertEqual(observed[0]['provider_id'], 'p2')
        self.assertEqual(observed[0]['base_url'], 'https://two.invalid/v1')

    def test_deleted_or_modified_queued_supplier_fails_before_model_call(self):
        selected = {'engine': 'api', 'provider_id': 'p1', 'model': 'same-model', 'effort': ''}
        binding = model_calls.api_binding(self.settings['p1'])
        called = []
        with patch.object(model_calls.ai_settings, 'resolve', side_effect=ValueError('供应商已删除')), \
             patch.object(model_calls.ai_settings, 'configured', return_value=True), \
             patch('video_parser.services.ai_analysis.call_model', side_effect=lambda *args, **kwargs: called.append(True)):
            with model_calls.bound_api(binding), self.assertRaises(local_agents.AgentError):
                model_calls.call([], self.settings['p1'], selected, Path('.'))
        self.assertEqual(called, [])

        changed = {**self.settings['p1'], 'base_url': 'https://changed.invalid/v1'}
        with patch.object(model_calls.ai_settings, 'resolve', return_value=changed), \
             patch.object(model_calls.ai_settings, 'configured', return_value=True), \
             patch('video_parser.services.ai_analysis.call_model', side_effect=lambda *args, **kwargs: called.append(True)):
            with model_calls.bound_api(binding), self.assertRaises(model_calls.ConfigurationChanged):
                model_calls.call([], self.settings['p1'], selected, Path('.'))
        self.assertEqual(called, [])

    def test_normalize_preserves_legacy_shape_and_validates_explicit_api_target(self):
        self.assertEqual(local_agents.normalize({'engine': 'api'}), {'engine': 'api', 'model': '', 'effort': ''})
        self.assertEqual(local_agents.normalize({'engine': 'api', 'provider_id': None}),
                         {'engine': 'api', 'model': '', 'effort': ''})
        self.assertEqual(local_agents.normalize({'engine': 'codex', 'provider_id': None}),
                         {'engine': 'codex', 'model': '', 'effort': ''})
        selected = local_agents.normalize({'engine': 'api', 'provider_id': 'p1', 'model': 'same-model', 'effort': ''})
        self.assertEqual(selected['provider_id'], 'p1')
        api_model = 'vendor/model~variant'
        self.assertEqual(local_agents.normalize({'engine': 'api', 'provider_id': 'p1', 'model': api_model})['model'], api_model)
        from video_parser.web.analysis_routes import AgentTargetBody
        self.assertEqual(AgentTargetBody(engine='api', provider_id='p1', model=api_model).model, api_model)
        with self.assertRaises(ValueError):
            local_agents.normalize({'engine': 'codex', 'model': api_model})
        for target in (
            {'engine': 'api', 'provider_id': 'p1', 'model': '', 'effort': ''},
            {'engine': 'api', 'provider_id': 'p1', 'model': 'same-model', 'effort': 'high'},
            {'engine': 'api', 'provider_id': 'bad id', 'model': 'same-model', 'effort': ''},
            {'engine': 'codex', 'provider_id': 'p1'},
        ):
            with self.subTest(target=target), self.assertRaises(ValueError):
                local_agents.normalize(target)


if __name__ == '__main__':
    import unittest
    unittest.main()
