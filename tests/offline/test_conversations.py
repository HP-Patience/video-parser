"""Independent course conversations; all model calls and frame reads are mocked."""
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from fastapi.testclient import TestClient
from video_parser.services import ai_analysis, ai_settings, conversations, library, workflows, local_agents, model_calls
from video_parser.web.app import app
from video_parser.services.local_agents.types import Result


class ConversationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.addCleanup(patch.stopall)
        self.root = Path(self.temp.name)
        patch.object(library, 'LIBRARY_ROOT', self.root / 'library').start()
        patch.object(ai_settings, 'SETTINGS_PATH', self.root / 'settings.json').start()
        patch.object(ai_analysis, '_progress').start()
        ai_settings.save('custom', 'https://fixture.invalid/v1', 'fixture', 'fake-key')
        self.pid = library.ensure({'platform': 'local', 'video_id': 'fixture', 'title': '课程'})['id']
        source = self.root / 'video.mp4'; source.write_bytes(b'fake-video')
        self.video = library.import_file(self.pid, source, 'video/course.mp4', 'video')
        source = self.root / 'sub.srt'
        source.write_text('1\n00:00:02,000 --> 00:00:20,000\n第一句\n\n2\n00:00:20,000 --> 00:00:30,000\n第二句\n', encoding='utf-8')
        self.sub = library.import_file(self.pid, source, 'subtitles/course.srt', 'subtitle')
        for template in ['chapters', 'summary']:
            library.save_text(self.pid, 'analysis', '[00:02] 课程资料', {'template': template, 'video_resource': self.video})
        patch.object(ai_analysis, 'video_info', return_value={'duration': 100, 'width': 320, 'height': 180}).start()
        def frames(video, times, directory):
            directory = Path(directory); directory.mkdir(parents=True, exist_ok=True)
            result = []
            for i, moment in enumerate(times):
                name = f'frame_{i:03d}.jpg'; (directory / name).write_bytes(b'fixture-jpg')
                result.append({'name': name, 'time': moment})
            return result
        patch.object(ai_analysis, 'extract_frames', side_effect=frames).start()
        self.work = self.root / 'work'; self.work.mkdir()
        self.client = TestClient(app, base_url='http://127.0.0.1:15001', client=('127.0.0.1', 1234))

    def test_english_transcription_is_valid_for_conversation_context(self):
        library.remove(self.pid, [self.sub])
        self.assertIn('subtitles', conversations.availability(self.pid, self.video)['missing'])
        source = self.root / 'english.srt'
        source.write_text('1\n00:00:02,000 --> 00:00:20,000\nThis lecture explains linear algebra.\n', encoding='utf-8')
        resource = library.import_file(self.pid, source, 'subtitles/english.srt', 'transcription', {'language': 'en', 'video_resource': self.video})
        result = self.client.get(f'/api/workspace/projects/{self.pid}/conversations/context', params={'resource': self.video})
        self.assertEqual(result.status_code, 200)
        self.assertTrue(result.json()['available'])
        self.assertEqual(result.json()['missing'], [])
        self.assertEqual(result.json()['transcript_resource'], resource)
        with patch.object(ai_analysis, 'records', return_value=[]):
            self.assertEqual(conversations.availability(self.pid, self.video)['missing'], ['chapters', 'summary'])

    def test_requires_subtitles_and_at_least_one_material(self):
        self.assertTrue(conversations.availability(self.pid, self.video)['available'])
        project = library.get(self.pid)
        summary = next(n for n, v in project['resources'].items() if v.get('template') == 'summary')
        library.remove(self.pid, [summary])
        self.assertEqual(conversations.availability(self.pid, self.video)['missing'], [])
        conversations.create(self.pid, self.video)
        library.remove(self.pid, [self.sub])
        self.assertEqual(conversations.availability(self.pid, self.video)['missing'], ['subtitles'])
        with self.assertRaises(ai_analysis.AnalysisError): conversations.create(self.pid, self.video)

    def test_chapters_only_and_summary_only_can_create_and_answer(self):
        records = ai_analysis.records(self.pid)
        for retained in ('chapters', 'summary'):
            with self.subTest(retained=retained), patch.object(ai_analysis, 'records', return_value=[r for r in records if r['template'] == retained]):
                response = self.client.get(f'/api/workspace/projects/{self.pid}/conversations/context', params={'resource': self.video})
                self.assertTrue(response.json()['available'])
                created = self.client.post(f'/api/workspace/projects/{self.pid}/conversations', json={'resource': self.video})
                self.assertEqual(created.status_code, 200)
                identifier = created.json()['conversation']['id']
                with patch.object(ai_analysis, 'call_model', side_effect=[('{"frame_times":[],"subtitle_ranges":[]}', {}), ('回答', {}), ('标题', {})]) as model:
                    conversations.ask(self.pid, identifier, '解释这个概念', self.video, 24, self.work)
                content = json.dumps(model.call_args_list[1].args[0], ensure_ascii=False)
                self.assertIn('参考资料', content)
                self.assertEqual(len(conversations.get(self.pid, identifier)['messages']), 1)

    def test_no_material_or_foreign_video_material_still_blocks(self):
        records = ai_analysis.records(self.pid)
        for rows in ([], [{**r, 'video_resource': 'video/other.mp4'} for r in records]):
            with self.subTest(rows=len(rows)), patch.object(ai_analysis, 'records', return_value=rows):
                status = conversations.availability(self.pid, self.video)
                self.assertFalse(status['available'])
                self.assertEqual(status['missing'], ['chapters', 'summary'])
                with self.assertRaises(ai_analysis.AnalysisError): conversations.create(self.pid, self.video)

    def test_new_sessions_are_independent_and_persistent(self):
        a = conversations.create(self.pid, self.video)
        b = conversations.create(self.pid, self.video)
        self.assertNotEqual(a['id'], b['id'])
        with patch.object(ai_analysis, 'call_model', side_effect=[('{"frame_times":[],"subtitle_ranges":[]}', {}), ('独立回答', {}), ('空间的封闭性', {})]):
            result = conversations.ask(self.pid, a['id'], '第一问', self.video, 24, self.work)
        self.assertEqual(result['title'], '空间的封闭性')
        self.assertEqual(len(conversations.get(self.pid, a['id'])['messages']), 1)
        self.assertEqual(conversations.get(self.pid, b['id'])['messages'], [])
        self.assertEqual(len(conversations.list_sessions(self.pid)), 2)

    def test_migration_is_idempotent_and_originals_survive(self):
        rec = ai_analysis.records(self.pid)[0]
        library.save_text(self.pid, 'analysis-followup', '旧回答', {'record': rec['id'], 'question': '旧问题'})
        conversations.migrate(self.pid)
        conversations.migrate(self.pid)
        sessions = conversations.list_sessions(self.pid)
        self.assertEqual(len(sessions), 1)
        self.assertEqual(sessions[0]['messages'][0]['answer'], '旧回答')
        self.assertEqual(ai_analysis.record(self.pid, rec['id'])['followups'][0]['answer'], '旧回答')
        ai_analysis.delete(self.pid, rec['id'])
        self.assertEqual(conversations.get(self.pid, sessions[0]['id'])['messages'][0]['answer'], '旧回答')

    def test_current_time_history_and_automatic_supplement(self):
        a = conversations.create(self.pid, self.video)
        calls = []
        def model(messages, settings=None):
            calls.append(messages)
            if len(calls) == 1: return '{"frame_times":[40,55],"subtitle_ranges":[[1,25]]}', {}
            if len(calls) == 2: return '补取后的回答', {}
            return '自动命名', {}
        with patch.object(ai_analysis, 'call_model', side_effect=model):
            conversations.ask(self.pid, a['id'], '看看 40 秒和 55 秒的画面', self.video, 24, self.work)
        text = json.dumps(calls[1], ensure_ascii=False)
        for value in ['00:24', '00:40', '00:55', '章节时间轴', '内容摘要', '第一句', '第二句']:
            self.assertIn(value, text)
        self.assertIn('发送时刻', text)

    def test_full_subtitle_file_is_not_silently_sampled(self):
        library.remove(self.pid, [self.sub])
        source = self.root / 'long.srt'
        raw = '1\n00:00:00,000 --> 00:00:30,000\n' + '完整课程字幕' * 11000 + '结尾必须保留\n'
        source.write_text(raw, encoding='utf-8')
        library.import_file(self.pid, source, 'subtitles/long.srt', 'subtitle')
        a = conversations.create(self.pid, self.video)
        with patch.object(ai_analysis, 'call_model', side_effect=[('{"frame_times":[],"subtitle_ranges":[]}',{}),('回答',{}),('标题',{})]) as model:
            conversations.ask(self.pid,a['id'],'解释课程',self.video,24,self.work)
        parts = model.call_args_list[1].args[0][1]['content']
        full = next(part['text'] for part in parts if part.get('text','').startswith('完整字幕'))
        self.assertIn(raw, full)

    def test_planner_rejects_unbounded_or_nonfinite_input(self):
        for value in ['{"frame_times":[1e999],"subtitle_ranges":[]}', '{"frame_times":["../../x"],"subtitle_ranges":[]}', '{"frame_times":[],"subtitle_ranges":[[30,10]]}']:
            with self.assertRaises(ValueError): conversations.parse_plan(value, 100)
        times, ranges = conversations.parse_plan('{"frame_times":[2,2,4],"subtitle_ranges":[[0,10]]}', 100)
        self.assertEqual(times, [2,4])
        self.assertEqual(ranges, [[0,10]])

    def test_subtitle_window_respects_cue_end_and_gaps(self):
        cues = conversations._cue_spans('1\n00:00:02,000 --> 00:00:04,000\n第一句\n\n2\n00:00:20,000 --> 00:00:30,000\n第二句\n')
        self.assertIn('第一句', conversations._subtitle_window(cues, 3, 3.5))
        self.assertNotIn('第一句', conversations._subtitle_window(cues, 10, 15))

    def test_history_stays_in_its_own_conversation(self):
        a = conversations.create(self.pid, self.video)
        b = conversations.create(self.pid, self.video)
        plan = ('{"frame_times":[],"subtitle_ranges":[]}', {})
        with patch.object(ai_analysis, 'call_model', side_effect=[plan, ('A的回答', {}), ('A的标题', {})]):
            conversations.ask(self.pid, a['id'], 'A专属问题', self.video, 24, self.work)
        calls = []
        def model(messages, settings=None):
            calls.append(messages)
            return plan if len(calls) == 1 else ('B的回答', {})
        with patch.object(ai_analysis, 'call_model', side_effect=model):
            conversations.ask(self.pid, b['id'], 'B的问题', self.video, 24, self.work)
        self.assertNotIn('A专属问题', json.dumps(calls, ensure_ascii=False))
        with patch.object(ai_analysis, 'call_model', side_effect=[plan, ('A的后续回答', {})]) as model:
            conversations.ask(self.pid, a['id'], 'A后续问题', self.video, 24, self.work)
        self.assertIn('A专属问题', json.dumps(model.call_args_list[1].args[0], ensure_ascii=False))
        self.assertEqual([m['question'] for m in conversations.get(self.pid,a['id'])['messages']], ['A专属问题','A后续问题'])

    def test_planner_failure_still_answers_with_the_gathered_material(self):
        a = conversations.create(self.pid, self.video)
        with patch.object(ai_analysis, 'call_model', side_effect=[
                local_agents.AgentError('fixture planner failure'), ('已保存的回答', {}), ('标题', {})]) as model:
            result = conversations.ask(self.pid, a['id'], '问题', self.video, 24, self.work)
        self.assertEqual(result['messages'][0]['answer'], '已保存的回答')
        self.assertEqual([message['question'] for message in result['messages']], ['问题'])
        content = json.dumps(model.call_args_list[1].args[0], ensure_ascii=False)
        self.assertIn('取材规划未能完成', content)
        self.assertNotIn('fixture planner failure', content)

    def test_failed_title_does_not_lose_answer(self):
        a = conversations.create(self.pid, self.video)
        with patch.object(ai_analysis, 'call_model', side_effect=[('{"frame_times":[],"subtitle_ranges":[]}', {}), ('已保存的回答', {}), ai_analysis.AnalysisError('命名失败')]):
            result = conversations.ask(self.pid, a['id'], '问题', self.video, 24, self.work)
        self.assertEqual(result['messages'][0]['answer'], '已保存的回答')
        self.assertFalse(result['title_generated'])

    def test_api_validation_and_queued_payload(self):
        base = f'/api/workspace/projects/{self.pid}/conversations'
        self.assertEqual(self.client.get(base).json()['conversations'], [])
        created = self.client.post(base, json={'resource': self.video})
        self.assertEqual(created.status_code, 200)
        cid = created.json()['conversation']['id']
        endpoint = f'{base}/{cid}/ask-task'
        with patch.object(workflows, 'start', return_value={'id': 'a'*32}) as start:
            for change in [{'position': -1}, {'position': float('inf')}, {'question': '  '}, {'consent': False}]:
                body = {'resource': self.video, 'question': '问题', 'position': 24, 'consent': True, **change}
                if change.get('position') == float('inf'): continue
                self.assertIn(self.client.post(endpoint, json=body).status_code, [400,422])
            body = {'resource': self.video, 'question': '问题', 'position': 24, 'consent': True}
            self.assertEqual(self.client.post(endpoint, json=body).status_code, 202)
            self.assertEqual(start.call_args.args[3]['mode'], 'conversation')
            self.assertEqual(start.call_args.args[3]['position'], 24)
        self.assertEqual(self.client.get(base).json()['conversations'][0]['id'], cid)


    def test_native_sessions_isolate_planning_title_and_resume_history(self):
        agent = {'engine': 'codex', 'model': 'fixture', 'effort': ''}
        a = conversations.create(self.pid, self.video, agent)
        plan = Result('{"frame_times":[],"subtitle_ranges":[]}', 'planner-session')
        with patch.object(model_calls, 'WORKSPACE_ROOT', self.root / 'workspaces'), \
             patch.object(ai_settings, 'current') as api, \
             patch.object(local_agents, 'complete', side_effect=[plan, Result('Native answer', 'main-session'), Result('Native title', 'title-session')]) as complete:
            conversations.ask(self.pid, a['id'], 'native-only-first', self.video, 24, self.work, agent)
        api.assert_not_called()
        self.assertEqual([call.kwargs['purpose'] for call in complete.call_args_list], ['planner','chat','title'])
        header = library.get(self.pid)['resources'][f"conversations/{a['id']}.json"]
        self.assertEqual(header['native_session_id'], 'main-session')
        self.assertNotIn('native_session_id', conversations.get(self.pid,a['id']))
        with patch.object(model_calls, 'WORKSPACE_ROOT', self.root / 'workspaces'), \
             patch.object(local_agents, 'complete', side_effect=[plan, Result('Next answer', 'main-session')]) as complete:
            conversations.ask(self.pid, a['id'], 'native-second', self.video, 24, self.work, agent)
        call = complete.call_args_list[1]
        self.assertEqual(call.kwargs['session_id'], 'main-session')
        self.assertNotIn('native-only-first', json.dumps(call.args[0]))
        self.assertIn('native-only-first', json.dumps(call.kwargs['rebuild_messages']))
        b = conversations.create(self.pid,self.video,agent)
        with patch.object(model_calls,'WORKSPACE_ROOT',self.root/'workspaces'), \
             patch.object(local_agents,'complete',side_effect=[plan,Result('Other answer','other-session'),Result('Other title')]) as complete:
            conversations.ask(self.pid,b['id'],'other-question',self.video,24,self.work,agent)
        self.assertNotIn('native-only-first',json.dumps(complete.call_args_list[1].args[0]))
        self.assertIsNone(complete.call_args_list[1].kwargs['session_id'])

    def test_failed_native_turn_cannot_resume_invisible_partial_history(self):
        agent={'engine':'codex'}
        a=conversations.create(self.pid,self.video,agent)
        library.update_details(self.pid,f"conversations/{a['id']}.json",{'native_session_id':'previous-session'})
        with patch.object(model_calls,'WORKSPACE_ROOT',self.root/'workspaces'), \
             patch.object(local_agents,'complete',side_effect=[Result('{"frame_times":[],"subtitle_ranges":[]}'),local_agents.AgentError('stopped')]):
            with self.assertRaises(local_agents.AgentError):
                conversations.ask(self.pid,a['id'],'question',self.video,24,self.work,agent)
        self.assertIsNone(library.get(self.pid)['resources'][f"conversations/{a['id']}.json"]['native_session_id'])
        self.assertEqual(conversations.get(self.pid,a['id'])['messages'],[])

    def test_engine_switch_requires_new_conversation_and_media_cannot_be_substituted(self):
        a=conversations.create(self.pid,self.video,{'engine':'codex'})
        library.save_text(self.pid,'conversation-message','Answer',{'conversation':a['id'],'question':'Q'})
        with patch.object(local_agents,'complete') as complete:
            with self.assertRaises(ValueError):
                conversations.ask(self.pid,a['id'],'Q2',self.video,24,self.work,{'engine':'claude'})
        complete.assert_not_called()
        source=self.root/'second.mp4';source.write_bytes(b'fake')
        other=library.import_file(self.pid,source,'video/other.mp4','video')
        with self.assertRaises(ValueError):
            conversations.ask(self.pid,a['id'],'Q2',other,24,self.work,{'engine':'codex'})

    def test_native_api_queue_needs_consent_but_not_api_keys(self):
        with patch.object(local_agents,'available',return_value=True), patch.object(ai_settings,'configured',return_value=False), \
             patch.object(ai_settings,'remember_consent') as consent, patch.object(workflows,'start',return_value={'id':'b'*32}) as start:
            base=f'/api/workspace/projects/{self.pid}/conversations'
            created=self.client.post(base,json={'resource':self.video,'agent':{'engine':'codex'}})
            self.assertEqual(created.status_code,200)
            endpoint=base+'/'+created.json()['conversation']['id']+'/ask-task'
            body={'resource':self.video,'position':24,'question':'Question','consent':True,'agent':{'engine':'codex'}}
            self.assertEqual(self.client.post(endpoint,json={**body,'consent':False}).status_code,422)
            self.assertEqual(self.client.post(endpoint,json={**body,'agent':{'engine':'codex','api_key':'never-accept'}}).status_code,422)
            self.assertEqual(self.client.post(endpoint,json={**body,'agent':{'engine':'codex','model':'x;calc'}}).status_code,422)
            self.assertEqual(self.client.post(endpoint,json=body).status_code,202)
            self.assertEqual(start.call_args.args[3]['agent']['engine'],'codex')
            consent.assert_not_called()

    def test_material_generation_also_uses_native_adapter_without_api_settings(self):
        with patch.object(ai_settings,'current') as api, patch.object(local_agents,'complete',return_value=Result('Native material',model='actual-native-model')) as complete:
            result=ai_analysis.run(self.pid,{'resource':self.video,'template':'summary','source':'transcript','agent':{'engine':'codex'}},self.work)
        api.assert_not_called()
        self.assertEqual(result['text'],'Native material')
        self.assertEqual(result['model'],'actual-native-model')
        self.assertEqual(result['provider'],'Codex')
        self.assertEqual(complete.call_args.kwargs['purpose'],'material')


if __name__ == '__main__': unittest.main()
