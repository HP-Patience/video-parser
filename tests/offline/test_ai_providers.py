"""Provider storage and discovery; isolated settings and loopback HTTP only."""
from concurrent.futures import ThreadPoolExecutor
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import tempfile
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from video_parser.services import ai_settings, ai_startup


class ProviderTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.path = Path(temporary.name) / 'settings.json'
        for context in (patch.object(ai_settings, 'SETTINGS_PATH', self.path),
                        patch.dict(os.environ, {'QWEN_API_KEY': '', 'QWEN_API_BASE_URL': '', 'QWEN_MODEL_ID': ''})):
            context.start()
            self.addCleanup(context.stop)

    def create(self, name='one', **options):
        data = {'name': name, 'base_url': 'https://fixture.invalid/v1',
                'models': ['m1', 'm2'], 'api_key': f'fixture-secret-{name}'}
        data.update(options)
        public = ai_settings.save_provider(**data)
        return public['providers'][-1]['id']

    def test_empty_and_env_projection_migrate_only_on_write(self):
        self.assertEqual(ai_settings.public()['providers'], [])
        with patch.dict(os.environ, {'QWEN_API_KEY': 'env-fixture-key'}):
            public = ai_settings.public()
            self.assertEqual(public['active_provider_id'], 'default')
            self.assertEqual(public['providers'][0]['source'], 'env')
            self.assertFalse(self.path.exists())
            self.create()
            self.assertEqual(len(ai_settings.public()['providers']), 2)
            self.assertEqual(ai_settings.current()['api_key'], 'env-fixture-key')
        self.assertEqual(ai_settings.current()['provider_id'], 'default')

    def test_legacy_projection_preserves_key_consent_pricing_startup(self):
        legacy = {'provider': 'llamacpp', 'base_url': 'http://127.0.0.1:8888/v1', 'model': 'old',
                  'api_key': 'legacy-fixture-token', 'consented_endpoint': 'http://127.0.0.1:8888/v1',
                  'price_input': 1.2, 'price_output': 3.4, 'startup': ai_startup.DEFAULTS}
        self.path.write_text(json.dumps(legacy), encoding='utf-8')
        before = self.path.read_bytes()
        self.assertEqual(ai_settings.public()['providers'][0]['id'], 'default')
        self.assertEqual(self.path.read_bytes(), before)
        self.create()
        saved = ai_settings.resolve({'provider_id': 'default', 'model': 'old'})
        for field, value in legacy.items():
            self.assertEqual(saved[field], value)
        self.assertEqual(len(json.loads(self.path.read_text(encoding='utf-8'))['providers']), 2)

    def test_active_legacy_save_and_independent_deletion(self):
        first = self.create()
        second = self.create('two')
        self.assertEqual(ai_settings.public()['active_provider_id'], first)
        ai_settings.save('custom', 'https://fixture.invalid/v1', 'new-default')
        self.assertEqual(ai_settings.current()['model'], 'new-default')
        self.assertEqual(ai_settings.resolve({'provider_id': second})['model'], 'm1')
        ai_settings.delete_provider(first)
        self.assertEqual(ai_settings.public()['active_provider_id'], '')
        self.assertFalse(ai_settings.configured())
        self.assertEqual(ai_settings.public()['providers'][0]['id'], second)
        ai_settings.save_provider(id=second, base_url='https://fixture.invalid/v1', activate=True)
        self.assertEqual(ai_settings.current()['provider_id'], second)
        ai_settings.delete_provider(second)
        with patch.dict(os.environ, {'QWEN_API_KEY': 'should-not-fallback'}):
            self.assertEqual(ai_settings.public()['providers'], [])
            self.assertFalse(ai_settings.configured())

    def test_public_default_name_prefers_saved_provider_name(self):
        self.assertEqual(ai_settings.public()['provider_name'], ai_settings.PRESETS['modelscope']['name'])
        identifier = self.create('My provider', api_key='fixture-name-key')
        self.assertEqual(ai_settings.public()['provider_name'], 'My provider')
        ai_settings.save_provider(id=identifier, name='Renamed provider', base_url='https://fixture.invalid/v1')
        self.assertEqual(ai_settings.public()['provider_name'], 'Renamed provider')

    def test_provider_prices_omitted_preserve_and_null_clears(self):
        identifier = self.create(price_input=1.2, price_output=3.4)
        ai_settings.save_provider(id=identifier, base_url='https://fixture.invalid/v1')
        self.assertEqual(ai_settings.current()['price_input'], 1.2)
        self.assertEqual(ai_settings.current()['price_output'], 3.4)
        ai_settings.save_provider(id=identifier, base_url='https://fixture.invalid/v1', price_input=None)
        self.assertIsNone(ai_settings.current()['price_input'])
        self.assertEqual(ai_settings.current()['price_output'], 3.4)
        ai_settings.save_provider(id=identifier, base_url='https://fixture.invalid/v1', price_input=2, price_output=None)
        self.assertEqual(ai_settings.current()['price_input'], 2)
        self.assertIsNone(ai_settings.current()['price_output'])
        ai_settings.save('custom', 'https://fixture.invalid/v1', 'm1')
        self.assertIsNone(ai_settings.current()['price_input'])
        self.assertIsNone(ai_settings.current()['price_output'])

    def test_same_endpoint_keys_consent_and_updates_are_isolated(self):
        first = self.create()
        second = self.create('two', price_input=2, price_output=4)
        ai_settings.remember_consent({'provider_id': second, 'model': 'm2'})
        self.assertFalse(ai_settings.public()['providers'][0]['consented'])
        self.assertTrue(ai_settings.public()['providers'][1]['consented'])
        ai_settings.save_provider(id=second, name='renamed', base_url='https://fixture.invalid/v1')
        self.assertEqual(ai_settings.resolve({'provider_id': second})['api_key'], 'fixture-secret-two')
        self.assertEqual(ai_settings.resolve({'provider_id': second})['price_input'], 2)
        observed = []
        with patch.object(ai_settings, 'test', side_effect=lambda settings: observed.append(settings.copy())):
            result = ai_settings.test_targets([{'provider_id': item, 'model': 'new-draft-model'} for item in (first, second)])
        self.assertTrue(all(row['ok'] for row in result['results']))
        self.assertTrue(all(row['model'] == 'new-draft-model' for row in observed))
        self.assertCountEqual([row['api_key'] for row in observed], ['fixture-secret-one', 'fixture-secret-two'])
        public = ai_settings.public()
        for secret in ('fixture-secret-one', 'fixture-secret-two', 'api_key'):
            self.assertNotIn(secret, json.dumps(public))
        ai_settings.save_provider(id=second, base_url='https://other.invalid/v1')
        self.assertFalse(ai_settings.public()['providers'][1]['key_saved'])
        self.assertFalse(ai_settings.public()['providers'][1]['consented'])
        self.assertEqual(ai_settings.resolve({'provider_id': first})['api_key'], 'fixture-secret-one')

    def test_new_draft_explicit_empty_key_does_not_inherit_default(self):
        self.create()
        calls = []
        with patch.object(ai_settings, 'client', side_effect=lambda *args, **kwargs: calls.append(args)):
            result = ai_settings.test_targets([{'base_url': 'https://fixture.invalid/v1', 'model': 'm1',
                                               'api_key': ''}])
        self.assertFalse(result['results'][0]['ok'])
        self.assertEqual(calls, [])

    def test_resolve_legacy_seam_and_explicit_failures(self):
        legacy = {'model': 'patched'}
        with patch.object(ai_settings, 'current', return_value=legacy):
            self.assertIs(ai_settings.resolve(), legacy)
            self.assertIs(ai_settings.resolve({'engine': 'api', 'model': 'ignored'}), legacy)
        identifier = self.create()
        for target in ({'provider_id': ''}, {'provider_id': 'missing'}, {'provider_id': []},
                       {'provider_id': identifier, 'model': 'not-owned'}, {'provider_id': identifier, 'model': ''}):
            with self.subTest(target=target), self.assertRaises(ValueError):
                ai_settings.resolve(target)
        self.assertEqual(ai_settings.resolve({'provider_id': identifier, 'model': 'm2'})['model'], 'm2')

    def test_bad_ids_and_endpoint_mismatch_never_call_or_write(self):
        identifier = self.create()
        before = self.path.read_bytes()
        with patch.object(ai_settings, 'client') as connect:
            for bad in ('missing', '', [], 'has space'):
                for function in (lambda: ai_settings.delete_provider(bad),
                                 lambda: ai_settings.save_provider(id=bad, base_url='https://fixture.invalid/v1', models=['m']),
                                 lambda: ai_settings.discover_models(id=bad, base_url='https://fixture.invalid/v1')):
                    with self.subTest(bad=bad), self.assertRaises(ValueError):
                        function()
            for target in ({'provider_id': identifier, 'base_url': 'https://other.invalid/v1', 'model': 'm1'},
                           {'provider_id': 'missing', 'model': 'm1'}):
                with self.assertRaises(ValueError):
                    ai_settings.test_targets([target])
            connect.assert_not_called()
        self.assertEqual(before, self.path.read_bytes())

    def test_boundaries_do_not_overwrite(self):
        identifier = self.create()
        before = self.path.read_bytes()
        invalid = [{'name': 'x' * 101}, {'name': 'bad\nname'}, {'name': []}, {'models': []},
                   {'models': ['x'] * 101}, {'models': ['bad model']}, {'models': ['m'], 'model': 'other'},
                   {'model': 0}, {'model': False}, {'price_input': []}, {'price_output': 'invalid-number'},
                   {'api_key': 'bad key'}, {'api_key': 1}, {'api_key': 'x' * 501},
                   {'price_input': float('nan')}, {'price_output': float('inf')},
                   *[{'base_url': url} for url in ('https://host/v1?key=private', 'https://host/v1#private',
                       'https://user:private@host/v1', 'https://@host/v1', 'http://remote.invalid/v1', 'http://localhost:bad/v1',
                       'https://host/white space', 'https://host/\npath', 'https://host/v1?', 'https://host\\path/v1')]]
        for values in invalid:
            options = {'id': identifier, 'base_url': 'https://fixture.invalid/v1'}
            options.update(values)
            with self.subTest(values=values), self.assertRaises(ValueError):
                ai_settings.save_provider(**options)
            self.assertEqual(before, self.path.read_bytes())
        ai_settings.save_provider(id=identifier, base_url='https://fixture.invalid/v1', models=['m2', 'm1', 'm2'])
        self.assertEqual(ai_settings.current()['models'], ['m2', 'm1'])
        self.assertEqual(ai_settings.current()['model'], 'm2')

    def test_provider_limit_and_parallel_writes(self):
        with ThreadPoolExecutor(max_workers=8) as pool:
            list(pool.map(lambda index: self.create(str(index)), range(20)))
        self.assertEqual(len(ai_settings.public()['providers']), 20)
        before = self.path.read_bytes()
        with self.assertRaises(ValueError):
            self.create('overflow')
        self.assertEqual(before, self.path.read_bytes())

    def test_corrupt_settings_are_not_overwritten(self):
        self.path.write_text('{broken', encoding='utf-8')
        with self.assertRaises(RuntimeError):
            self.create()
        self.assertEqual(self.path.read_text(), '{broken')

    def test_discovery_reuses_only_same_id_and_endpoint_and_closes(self):
        identifier = self.create()
        before = self.path.read_bytes()
        page = SimpleNamespace(data=[SimpleNamespace(id=value) for value in ('m2', 'm1', 'm2', '', 'bad\nid')])
        connection = SimpleNamespace(models=SimpleNamespace(list=Mock(return_value=page)), close=Mock())
        with patch.object(ai_settings, 'client', return_value=connection) as connect:
            self.assertEqual(ai_settings.discover_models(id=identifier, base_url='https://fixture.invalid/v1'),
                             {'models': ['m2', 'm1']})
            self.assertEqual(connect.call_args.args[0]['api_key'], 'fixture-secret-one')
            connection.close.assert_called_once()
            for options in ({}, {'id': identifier, 'base_url': 'https://other.invalid/v1'}):
                arguments = {'base_url': 'https://fixture.invalid/v1', **options}
                with self.assertRaises(ValueError):
                    ai_settings.discover_models(**arguments)
            self.assertEqual(connect.call_count, 1)
        self.assertEqual(before, self.path.read_bytes())

    def test_discovery_first_page_limit_and_key_free_errors(self):
        class Page:
            data = [SimpleNamespace(id=''), SimpleNamespace(id='bad\nid'),
                    *[SimpleNamespace(id=f'm{index}') for index in range(1050)]]

            def __iter__(self):
                raise AssertionError('must not auto paginate')

        connection = SimpleNamespace(models=SimpleNamespace(list=Mock(return_value=Page())), close=Mock())
        with patch.object(ai_settings, 'client', return_value=connection):
            result = ai_settings.discover_models(base_url='http://localhost:8888/v1')
            self.assertEqual(len(result['models']), 998)
            self.assertIn('m100', result['models'])
            self.assertEqual(result['models'][-1], 'm997')
            self.assertNotIn('bad\nid', result['models'])
            connection.models.list.side_effect = RuntimeError('private-fixture-key')
            with self.assertRaises(RuntimeError) as caught:
                ai_settings.discover_models(base_url='http://localhost:8888/v1')
            self.assertNotIn('private-fixture-key', str(caught.exception))
            self.assertEqual(connection.close.call_count, 2)
        self.assertFalse(self.path.exists())

    def test_discovery_real_sdk_http_stub(self):
        requests = []

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def do_GET(self):
                requests.append((self.path, self.headers.get('authorization')))
                body = json.dumps({'object': 'list', 'data': [{'id': 'alpha', 'object': 'model'},
                                   {'id': 'beta', 'object': 'model'}, {'id': 'alpha', 'object': 'model'}],
                                   'has_more': True, 'next_page': 'do-not-follow'}).encode()
                self.send_response(200)
                self.send_header('content-type', 'application/json')
                self.send_header('content-length', str(len(body)))
                self.end_headers()
                self.wfile.write(body)

        server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            result = ai_settings.discover_models(base_url=f'http://127.0.0.1:{server.server_port}/v1')
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=5)
        self.assertEqual(result, {'models': ['alpha', 'beta']})
        self.assertEqual(requests, [('/v1/models', 'Bearer local')])
        self.assertFalse(self.path.exists())


if __name__ == '__main__':
    unittest.main()
