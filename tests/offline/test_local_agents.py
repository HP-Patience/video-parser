"""Local agent tests: protocol fixtures only, never a real model or authentication file."""
import io
import json
import os
from pathlib import Path
from queue import Queue
import tempfile
from threading import Event
import time
import unittest
from unittest.mock import Mock, patch

from video_parser.services import local_agents, model_calls, ai_settings, task_runtime, agent_controls
from video_parser.services.local_agents import claude, codex, dsh, opencode, transport
from video_parser.services.local_agents.transport import AgentError, RpcError, Wire
from video_parser.services.local_agents.types import Result, Turn, prepare, command_entries


class LocalAgentTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.addCleanup(patch.stopall)
        self.root = Path(self.temp.name)
        patch.object(agent_controls, 'PATH', self.root / 'agent-controls.json').start()
        patch.object(local_agents, 'CACHE_PATH', self.root / 'catalog.json').start()
        patch.object(local_agents, 'available', return_value=True).start()

    def test_target_validation_has_no_shell_or_arbitrary_engine(self):
        for target in [{'engine': 'powershell'}, {'engine': 'codex', 'model': 'x; calc.exe'},
                       {'engine': 'claude', 'model': '--dangerous'}, {'engine': 'api', 'model': 'other'},
                       {'engine': 'codex', 'effort': 'high\ncommand'}, {'engine': 'codex', 'model': 1}]:
            with self.subTest(target=target), self.assertRaises(ValueError):
                local_agents.normalize(target)
        self.assertEqual(local_agents.normalize()['engine'], 'api')
        self.assertEqual(local_agents.normalize({'engine': 'opencode', 'model': 'provider/model'})['model'], 'provider/model')
        self.assertEqual(local_agents.normalize({'engine': 'dsh', 'model': 'fixture/model'})['engine'], 'dsh')

    def test_inventory_does_not_launch_programs_or_expose_initialization_accounts(self):
        with patch.object(local_agents, '_runtime') as runtime:
            inventory = local_agents.inventory()
        runtime.assert_not_called()
        self.assertEqual([item['id'] for item in inventory['engines']], ['claude', 'codex', 'opencode', 'dsh'])
        self.assertNotIn('api_key', json.dumps(inventory))

    def test_discovery_sanitizes_native_initialization_and_closes_process(self):
        runtime = Mock()
        runtime.catalog.return_value = {'models': [{'id': 'fixture', 'label': 'Fixture', 'efforts': ['high']}],
                                        'commands': [], 'account': {'api_key': 'do-not-return'}}
        with patch.object(local_agents, '_runtime', return_value=runtime):
            result = local_agents._discover('claude')
        self.assertEqual(result['models'][0]['id'], 'fixture')
        self.assertNotIn('do-not-return', json.dumps(result))
        runtime.close.assert_called_once()

    def test_agent_does_not_require_separate_api_configuration(self):
        with patch.object(ai_settings, 'configured', return_value=False):
            self.assertEqual(model_calls.require({'engine': 'codex'})['engine'], 'codex')
            with self.assertRaises(AgentError):
                model_calls.require()

    def test_course_context_is_complete_and_external_images_are_not_fetched(self):
        text = '完整字幕' * 15000 + '结尾'
        turn = prepare([{'role': 'system', 'content': 'Trusted planner'}, {'role': 'user', 'content': text}])
        self.assertIn(text, turn.text)
        self.assertIn('Trusted planner', turn.instructions)
        self.assertNotIn(text, turn.instructions)
        with self.assertRaises(AgentError):
            prepare([{'role': 'user', 'content': [{'type': 'image_url', 'image_url': {'url': 'https://fixture.invalid/key'}}]}])

    def test_material_primary_task_is_not_mislabelled_as_quoted_course_data(self):
        turn=prepare([{'role':'user','content':'quoted subtitle'}],question='Generate chapter timeline',purpose='material')
        self.assertTrue(turn.text.startswith('当前任务：\nGenerate chapter timeline'))
        self.assertIn('quoted subtitle',turn.text)
        self.assertNotIn('Generate chapter timeline',turn.instructions)

    def test_thinking_and_raw_error_details_are_not_public_output(self):
        error = RpcError(-32000, 'api_key=do-not-print')
        self.assertNotIn('do-not-print', str(error))
        wire = object.__new__(Wire)
        wire.sequence = 0
        wire.send = Mock()
        wire.read = Mock(side_effect=[{'method': 'reasoning', 'params': {'text': 'not assistant text'}},
            {'id': 1, 'error': {'code': -32000, 'message': 'api_key=do-not-print'}}])
        wire.on_event = Mock()
        with self.assertRaises(RpcError) as caught:
            wire.request('fixture', {})
        self.assertNotIn('do-not-print', str(caught.exception))
        wire.on_event.assert_called_once()

    def test_rpc_services_requests_before_returning_its_own_response(self):
        wire = object.__new__(Wire)
        wire.sequence = 0
        wire.send = Mock()
        wire.read = Mock(side_effect=[{'jsonrpc': '2.0', 'id': 'server', 'method': 'forbidden'}, {'id': 1, 'result': {'ok': True}}])
        wire.on_request = Wire.reject_request
        wire.on_event = Mock()
        self.assertEqual(wire.request('fixture', {}), {'ok': True})
        reply = wire.send.call_args_list[1].args[0]
        self.assertIn('error', reply)
        self.assertNotIn('result', reply)

    def test_prepare_preserves_click_time_image_and_rejects_invalid_data(self):
        turn = prepare([{'role': 'user', 'content': [
            {'type': 'text', 'text': '[00:24] 当前时刻'},
            {'type': 'image_url', 'image_url': {'url': 'data:image/jpeg;base64,YQ=='}}]}])
        self.assertEqual(turn.images[0]['mime'], 'image/jpeg')
        self.assertIn('[00:24]', turn.text)
        with self.assertRaises(AgentError):
            prepare([{'role': 'user', 'content': [{'type': 'image_url', 'image_url': {'url': 'file:///credential'}}]}])

    def test_native_completion_is_closed_on_success_and_failure(self):
        runtime = Mock()
        runtime.run.return_value = Result('answer', 'native-session')
        with patch.object(local_agents, '_runtime', return_value=runtime):
            result = local_agents.complete([{'role': 'user', 'content': 'fixture'}], {'engine': 'codex'}, self.root)
        self.assertEqual(result.text, 'answer')
        runtime.close.assert_called_once()
        runtime.reset_mock()
        runtime.run.side_effect = task_runtime.Cancelled('cancelled')
        with patch.object(local_agents, '_runtime', return_value=runtime), self.assertRaises(task_runtime.Cancelled):
            local_agents.complete([{'role': 'user', 'content': 'fixture'}], {'engine': 'codex'}, self.root)
        runtime.close.assert_called_once()

    def test_open_code_overrides_only_child_permissions_and_keeps_auth_routing_private(self):
        original = {'provider': {'fixture': {'options': {'apiKey': 'private'}}}}
        with patch.dict('os.environ', {'OPENCODE_CONFIG_CONTENT': json.dumps(original)}):
            environment = opencode.launch_environment('trusted instruction')
            self.assertEqual(json.loads(__import__('os').environ['OPENCODE_CONFIG_CONTENT']), original)
        child = json.loads(environment['OPENCODE_CONFIG_CONTENT'])
        self.assertEqual(child['provider'], original['provider'])
        self.assertEqual(child['permission']['*'], 'deny')
        self.assertEqual(child['default_agent'], opencode.ROLE)
        self.assertFalse(child['tools']['*'])

    def test_open_code_fs_and_permissions_are_fail_closed(self):
        self.assertEqual(opencode.Runtime.request('session/request_permission', {}), {'outcome': {'outcome': 'cancelled'}})
        for name in ['fs/write_text_file', 'terminal/create', 'fs/read_text_file']:
            with self.assertRaises(RpcError):
                opencode.Runtime.request(name, {})

    def test_dsh_selector_round_trips_a_provider_route(self):
        selector = '["command-code","deepseek/deepseek-v4.1-flash"]'
        self.assertEqual(dsh.pair(selector), ('command-code', 'deepseek/deepseek-v4.1-flash'))
        self.assertEqual(dsh.identifier(selector), 'command-code/deepseek/deepseek-v4.1-flash')
        self.assertEqual(dsh.pair_from_identifier('command-code/deepseek/deepseek-v4.1-flash'),
                         ('command-code', 'deepseek/deepseek-v4.1-flash'))
        self.assertIsNone(dsh.pair('default'))
        self.assertIsNone(dsh.pair_from_identifier('missing-separator'))

    def test_dsh_overlay_reuses_public_desktop_routing_without_credentials(self):
        home = self.root / 'dsh-home'
        patch_path = home / 'profiles/desktop/cordis.patch.yml'
        patch_path.parent.mkdir(parents=True)
        patch_path.write_text(
            '- id: llm-pi-ai\n  name: "@deepseek-ai/dsh-llm-pi-ai"\n  config:\n    providers:\n'
            '      fixture:\n        displayName: Fixture\n        apiKeyEnv: FIXTURE_KEY\n'
            '        baseURL: https://fixture.invalid/v1\n        models:\n          - id: model-one\n'
            '            name: Model One\n            input:\n              - text\n'
            '- id: agent-default-model\n  name: "@deepseek-ai/dsh-agent-default-model"\n  config:\n'
            '    provider: fixture\n    model: model-one\n    reasoningEffort: high\n',
            encoding='utf-8')
        with patch.dict(os.environ, {'DSH_HOME': str(home)}):
            text = dsh.overlay(Turn('trusted instruction\nsecond line', 'body', []))
        self.assertIn('fixture:', text)
        self.assertIn('provider: "fixture"', text)
        self.assertIn('model: "model-one"', text)
        self.assertIn('personaPrefix: |-', text)
        self.assertIn('trusted instruction', text)
        self.assertIn('disabled: true', text)

    def test_dsh_defaults_to_its_own_profile_without_desktop_routing(self):
        with patch.dict(os.environ, {'DSH_HOME': str(self.root / 'empty-home')}):
            self.assertEqual(dsh.routing(), '')

    def test_dsh_permissions_and_unknown_requests_are_fail_closed(self):
        self.assertEqual(dsh.Runtime.request('session/request_permission', {}), {'outcome': {'outcome': 'cancelled'}})
        for name in ['fs/read_text_file', 'terminal/create', 'session/set_mode']:
            with self.assertRaises(RpcError):
                dsh.Runtime.request(name, {})

    def test_dsh_catalog_projects_provider_grouped_models(self):
        runtime = object.__new__(dsh.Runtime)
        runtime.ensure_session = Mock()
        runtime.config = [
            {'id': 'model', 'category': 'model', 'options': [
                {'group': 'fixture', 'name': 'Fixture', 'options': [
                    {'value': '["fixture","model-one"]', 'name': 'Model One'},
                    {'value': '["fixture","model-two"]', 'name': 'Model Two'}]}]},
            {'id': 'reasoning_effort', 'category': 'thought_level', 'options': [{'value': ''}, {'value': 'high'}]}]
        data = runtime.catalog()
        self.assertEqual([item['id'] for item in data['models']], ['fixture/model-one', 'fixture/model-two'])
        self.assertEqual(data['models'][0]['efforts'], ['high'])
        self.assertEqual(data['models'][0]['label'], 'Model One')
        self.assertEqual(data['commands'], [])

    def test_dsh_executable_uses_the_local_shim_or_the_desktop_install(self):
        shim = Path('C:/Program Files/DeepSeek Harness/resources/runtime/cli/bin/dsh.cmd')
        with patch.object(transport.shutil, 'which', return_value=None), \
             patch.object(transport, '_dsh_desktop_shim', return_value=shim), \
             patch.object(transport.os, 'name', 'nt'), patch.dict(os.environ, {'COMSPEC': 'cmd.exe'}):
            self.assertEqual(transport.executable('dsh'), ['cmd.exe', '/d', '/c', str(shim)])
        with patch.object(transport.shutil, 'which', return_value=None), \
             patch.object(transport, '_dsh_desktop_shim', return_value=None):
            with self.assertRaises(AgentError):
                transport.executable('dsh')

    def test_commands_are_real_provider_specific_entries_not_cli_controls(self):
        entries = command_entries([{'name': 'fixture'}, {'name': 'clear'}, {'name': 'heapdump'}, {'name': '../../bad'}, {'name': 'fixture'}], '$')
        self.assertEqual(entries, [{'name': 'fixture', 'prefix': '$', 'description': ''}])

    def test_claude_permission_callback_does_not_allow_shell_or_arbitrary_skills(self):
        runtime = object.__new__(claude.Runtime)
        runtime.wire = Mock()
        runtime.turn = prepare([{'role': 'user', 'content': 'fixture'}])
        runtime.metadata = {'commands': [{'name': 'fixture', 'description': 'Fixture (user)'}]}
        for name, value in [('Bash', {'command': 'touch file'}), ('Skill', {'skill': 'uninstalled'})]:
            runtime.control({'type': 'control_request', 'request_id': 'request', 'request': {
                'subtype': 'can_use_tool', 'tool_name': name, 'input': value}})
            self.assertEqual(runtime.wire.send.call_args.args[0]['response']['response']['behavior'], 'deny')
        runtime.control({'type': 'control_request', 'request_id': 'request', 'request': {
            'subtype': 'can_use_tool', 'tool_name': 'Skill', 'input': {'skill': 'fixture'}}})
        self.assertEqual(runtime.wire.send.call_args.args[0]['response']['response']['behavior'], 'allow')


    def test_claude_user_frame_matches_native_session_contract(self):
        runtime=object.__new__(claude.Runtime)
        runtime.wire=Mock()
        runtime.wire.read.return_value={'type':'result','subtype':'success','result':'OK','session_id':'fixture-session'}
        runtime.metadata={}
        turn=prepare([{'role':'user','content':'Synthetic contract check'}],purpose='smoke')
        result=runtime.run(turn,{},self.root)
        frame=runtime.wire.send.call_args_list[0].args[0]
        self.assertEqual(frame['session_id'],'')
        self.assertIsInstance(frame['message']['content'],str)
        __import__('uuid').UUID(frame['uuid'])
        self.assertEqual(result.text,'OK')

    def test_upstream_error_categories_never_echo_private_server_details(self):
        cases=[('401 api_key=secret-value','authentication'),('502 endpoint failed','upstream_unavailable'),
               ('ModelNotFoundError: unknown model','model_not_found'),('Insufficient credits','quota')]
        for detail,category in cases:
            error=RpcError(-32000,detail)
            self.assertEqual(error.diagnostic_code,category)
            self.assertNotIn(detail,str(error))


    def test_codex_inband_failures_keep_their_upstream_diagnosis(self):
        cases = [
            ({'method': 'error', 'params': {'threadId': 'thread-1', 'willRetry': False,
                                            'error': {'message': '401 Unauthorized api_key=do-not-print'}}}, 'authentication'),
            ({'method': 'turn/completed', 'params': {'threadId': 'thread-1', 'turn': {
                'id': 'turn-1', 'status': 'failed',
                'error': {'message': 'Your input exceeds the context window of this model'}}}}, 'context_limit'),
            ({'method': 'error', 'params': {'threadId': 'thread-1', 'willRetry': False,
                                            'error': {'codexErrorInfo': 'serverOverloaded'}}}, 'upstream_unavailable'),
        ]
        for message, category in cases:
            with self.subTest(category=category):
                runtime = object.__new__(codex.Runtime)
                runtime.workspace = self.root
                runtime.wire = Mock()
                runtime.wire.request.side_effect = [{'thread': {'id': 'thread-1'}, 'model': 'fixture'},
                                                   {'turn': {'id': 'turn-1'}}]
                runtime.wire.read.return_value = message
                runtime.wire.dispatch.side_effect = lambda value: runtime.wire.on_event(value)
                turn = prepare([{'role': 'user', 'content': 'fixture'}])
                with self.assertRaises(AgentError) as caught:
                    runtime.run(turn, {}, self.root)
                self.assertEqual(caught.exception.diagnostic_code, category)
                self.assertEqual(caught.exception.code, category)
                self.assertNotIn('do-not-print', str(caught.exception))

    def test_native_binding_matches_course_engine_and_saved_history(self):
        history=[{'role':'user','content':'saved-question'},{'role':'assistant','content':'saved-answer'}]
        model_calls.bind_session(self.root,'codex',history,'owned-session')
        self.assertEqual(model_calls.bound_session(self.root,'codex',history,'owned-session'),'owned-session')
        self.assertIsNone(model_calls.bound_session(self.root,'claude',history,'owned-session'))
        self.assertIsNone(model_calls.bound_session(self.root,'codex',[], 'owned-session'))
        self.assertIsNone(model_calls.bound_session(self.root,'codex',history,'foreign-session'))
        model_calls.bind_session(self.root,'codex',[],None)
        self.assertIsNone(model_calls.bound_session(self.root,'codex',history,'owned-session'))


    def test_successful_process_shutdown_allows_native_history_to_flush(self):
        wire=object.__new__(Wire);wire.closed=Event();wire.process=Mock();wire.reader=Mock();wire.stderr_reader=Mock()
        wire.process.poll.side_effect=[None]
        wire.process.wait.return_value=0
        with patch.object(task_runtime,'stop_process') as stop:
            wire.close()
        wire.process.stdin.close.assert_called()
        wire.process.wait.assert_called_once_with(timeout=2)
        stop.assert_not_called()
        self.assertTrue(wire.closed.is_set())

    def test_stuck_agent_shutdown_stops_only_the_owned_process(self):
        wire=object.__new__(Wire);wire.closed=Event();wire.process=Mock();wire.reader=Mock();wire.stderr_reader=Mock()
        wire.process.poll.return_value=None
        wire.process.wait.side_effect=__import__('subprocess').TimeoutExpired('owned',2)
        with patch.object(task_runtime,'stop_process') as stop, patch('video_parser.services.local_agents.transport.os.name','nt'):
            wire.close()
        stop.assert_called_once_with(wire.process)


    def test_acp_load_response_need_not_repeat_accepted_session_id(self):
        runtime=object.__new__(opencode.Runtime)
        runtime.workspace=self.root
        runtime.rpc=Mock(return_value={'configOptions':[{'id':'mode'}]})
        runtime.set_option=Mock()
        self.assertTrue(runtime.ensure_session('existing-native-session'))
        self.assertEqual(runtime.session_id,'existing-native-session')
        self.assertEqual(runtime.rpc.call_args.args[0],'session/load')
        runtime.set_option.assert_called_once_with('mode',opencode.ROLE)


if __name__ == '__main__':
    unittest.main()
