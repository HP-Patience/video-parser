"""Synthetic translation and archive-status tests; model boundary only is mocked."""
import json
from pathlib import Path
import tempfile
import time
from threading import Event, Lock
from types import SimpleNamespace
import unittest
from unittest.mock import patch
from uuid import uuid4
from fastapi import FastAPI
from fastapi.testclient import TestClient
from video_parser.services import ai_settings, library, model_calls, task_runtime, transcription, translations
from video_parser.web import routes


class TranslationStatusTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory(prefix='vp-translation-fixture-')
        self.addCleanup(temp.cleanup); self.root = Path(temp.name)
        self.addCleanup(patch.stopall)
        patch.object(library, 'LIBRARY_ROOT', self.root / 'library').start()
        patch.object(transcription, 'OUTPUT_ROOT', self.root / 'outputs').start()
        self.project = library.ensure({'platform': 'bilibili', 'video_id': 'BV1TaqYBcEJc', 'source_url': 'https://www.bilibili.com/video/BV1TaqYBcEJc?p=1',
            'title': '合成课程', 'episodes': [{'part': 1, 'title': '一'}, {'part': 2, 'title': '二'}]})
        self.pid = self.project['id']
        video = self.root / 'fake.mp4'; video.write_bytes(b'not-real-media')
        self.video = library.import_file(self.pid, video, 'videos/test.mp4', 'video')
        self.raw = '1\n00:00:01,100 --> 00:00:02,200\nHello world.\n\n2\n00:00:03,300 --> 00:00:04,400\nHello world.\n'
        self.subtitle = self.root / 'original.srt'; self.subtitle.write_text(self.raw, encoding='utf-8')
        self.resource = library.import_file(self.pid, self.subtitle, 'subtitles/original.srt', 'transcription', {'video_resource': self.video})
        self.reference = {'project_id': self.pid, 'resource': self.resource, 'video_resource': self.video}
        self.require = patch.object(model_calls, 'require', return_value={'engine': 'api', 'model': '', 'effort': ''}).start()
        patch.object(ai_settings, 'current', return_value={'model': 'fake', 'provider': 'custom', 'startup': {'context_size': 32768}}).start()
        self.model = patch.object(model_calls, 'call', side_effect=self.reply).start()
        app = FastAPI(); app.include_router(routes.router)
        self.client = TestClient(app, base_url='http://127.0.0.1:15001', client=('127.0.0.1', 1234)); self.addCleanup(self.client.close)
        self.ids = []
        self.addCleanup(lambda: [transcription.jobs.pop(identifier, None) for identifier in self.ids])

    def reply(self, messages, *args, **kwargs):
        rows = json.loads(messages[1]['content'])['items']
        chinese = '简体中文' in messages[0]['content']
        return SimpleNamespace(text=json.dumps({'items': [{'index': row['index'], 'text': '你好，世界。' if chinese else 'Hello world.'} for row in rows]}), model='synthetic')

    def finish(self, initial):
        identifier = initial['id']; self.ids.append(identifier)
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            job = transcription.get(identifier)
            if job.state not in task_runtime.ACTIVE and not task_runtime.active_id(job.id): return job
            time.sleep(.01)
        self.fail('synthetic translation did not finish')

    def test_versions_use_manifest_origin_and_select_translations_without_nesting(self):
        platform = library.import_file(self.pid, self.subtitle, 'subtitles/platform.srt', 'subtitle',
                                       {'video_resource': self.video, 'language': 'zh'})
        job = self.finish(translations.start(self.reference, 'zh', True))
        self.assertEqual(job.state, 'completed')
        response = self.client.get(f'/api/workspace/projects/{self.pid}/subtitles', params={'video_resource': self.video})
        self.assertEqual(response.status_code, 200)
        data = response.json()
        originals = {row['resource']: row for row in data['versions'] if row['kind'] != 'translation'}
        self.assertEqual(originals[self.resource]['origin'], 'local')
        self.assertEqual(originals[platform]['origin'], 'platform')
        translated = next(row for row in data['versions'] if row['kind'] == 'translation')
        self.assertEqual(translated['source_origin'], 'local')
        self.assertEqual(translated['source_resource'], self.resource)
        viewed = self.client.get(f'/api/workspace/projects/{self.pid}/subtitles',
                                params={'video_resource': self.video, 'subtitle_resource': translated['resource']}).json()
        self.assertIn('你好', viewed['text'])
        self.assertEqual(viewed['selected']['kind'], 'translation')
        self.assertEqual(viewed['translations'], [])
        library.update_details(self.pid, translated['resource'], {'source_resource': None})
        legacy = next(row for row in library.translation_records(library.get(self.pid), self.video) if row['resource'] == translated['resource'])
        # Both originals have identical bytes: do not guess which source was used.
        self.assertEqual(legacy['source_origin'], 'unknown')
        self.assertEqual(legacy['source_resolution'], 'unknown')
        self.assertEqual(self.client.get(f'/api/workspace/projects/{self.pid}/subtitles', params={'subtitle_resource': '../private'}).status_code, 404)
        self.model.assert_called_once()

    def test_archived_translation_records_batch_usage_once(self):
        def reply(messages, *args, **kwargs):
            rows = json.loads(messages[1]['content'])['items']
            return SimpleNamespace(text=json.dumps({'items': [{'index': row['index'], 'text': '你好，世界。'} for row in rows]}),
                                   model='synthetic', usage={'prompt_tokens': 100, 'completion_tokens': 20})
        with patch.object(model_calls, 'call', side_effect=reply):
            job = self.finish(translations.start(self.reference, 'zh', True))
        self.assertEqual(job.state, 'completed')
        resources = [info for info in library.get(self.pid)['resources'].values()
                     if info['kind'] == 'translation' and info.get('usage')]
        self.assertEqual(len(resources), 1, 'the srt copy must not double-count the batch sum')
        self.assertEqual(resources[0]['usage'], {'prompt_tokens': 100, 'completion_tokens': 20})
        from video_parser.services import usage_report
        report = usage_report.collect(0)
        translation = next(row for row in report['by_kind'] if row['kind'] == 'translation')
        self.assertEqual((translation['calls'], translation['prompt_tokens'], translation['completion_tokens']), (1, 100, 20))

    def test_archive_restores_translation_without_task_and_learning_lists_chinese(self):
        from video_parser.services import ai_analysis
        from video_parser.web import analysis_routes
        job = self.finish(translations.start(self.reference, 'zh', True))
        self.assertEqual(job.state, 'completed')
        transcription.jobs.pop(job.id, None)
        result = self.client.get(f'/api/workspace/projects/{self.pid}/subtitles', params={'video_resource': self.video}).json()
        self.assertEqual(result['text'], 'Hello world.\nHello world.')
        self.assertEqual(len(result['translations']), 2)
        self.assertTrue(all(row['language'] == 'zh' and '你好' in row['text'] for row in result['translations']))
        cues = analysis_routes.subtitle_cues(self.pid)
        self.assertEqual(len(cues['cues']), 2)
        self.assertIn('你好', cues['cues'][0]['text'])
        self.assertTrue(any(row['label'] == 'AI 翻译（中文）' for row in cues['sources']))
        # AI analysis still defaults to the original; playback allows switching back.
        self.assertTrue(all(row['kind'] != 'translation' for row in ai_analysis.transcript_sources(library.get(self.pid))))
        original = analysis_routes.subtitle_cues(self.pid, self.resource)
        self.assertIn('Hello', original['cues'][0]['text'])
        self.model.assert_called_once()

    def test_legacy_translation_hash_matches_but_wrong_original_does_not(self):
        job = self.finish(translations.start(self.reference, 'zh', True))
        project = library.get(self.pid)
        for name, info in project['resources'].items():
            if info['kind'] == 'translation':
                # Older releases recorded the hash, not the original resource path.
                library.update_details(self.pid, name, {'source_resource': None})
        self.assertEqual(len(library.translation_records(library.get(self.pid), self.video, self.resource)), 2)
        other = self.root / 'other.srt'; other.write_text(self.raw.replace('Hello world.', 'Different original.'), encoding='utf-8')
        key = library.import_file(self.pid, other, 'subtitles/other.srt', 'subtitle', {'video_resource': self.video})
        result = self.client.get(f'/api/workspace/projects/{self.pid}/subtitles', params={'video_resource': self.video, 'subtitle_resource': key}).json()
        self.assertEqual(result['translations'], [])
        # Invalid published file is not displayed as a finished translation.
        Path(job.files['srt']['path']).write_text('corrupt', encoding='utf-8')
        self.assertEqual(len(library.translation_records(library.get(self.pid), self.video, self.resource)), 1)

    def test_generated_filenames_identify_source_and_download_name(self):
        from video_parser.services import subtitles
        self.assertEqual(subtitles.platform_name('8.08可解性和解的结构', '.srt'), '8.08可解性和解的结构-B站平台字幕.srt')
        project = library.get(self.pid)
        project['episode_title'] = '8.08可解性和解的结构'
        library.atomic_json(project['directory'] / 'project.json', {k: v for k, v in project.items() if k != 'directory'})
        job = transcription.Job('a' * 32, self.subtitle, '过时的课程长标题', 'auto', project_id=self.pid, resource=self.video)
        job.detected_language = 'en'
        self.assertEqual(transcription.output_name(job, 'srt'), '8.08可解性和解的结构-英文-本地转写.srt')
        job.files = {'srt': {'path': self.root / transcription.output_name(job, 'srt')}}
        self.assertEqual(transcription.view(job)['files'][0]['name'], transcription.output_name(job, 'srt'))

    def test_translation_keeps_duplicate_cues_timestamps_original_and_persists(self):
        job = self.finish(translations.start(self.reference, 'zh', True))
        self.assertEqual(job.state, 'completed', job.message)
        self.assertEqual(job.operation, 'translation')
        self.assertEqual(job.resource, self.video)
        self.assertEqual(job.text, '你好，世界。\n你好，世界。')
        translated = transcription.download_file(job.id, 'srt').read_text(encoding='utf-8')
        self.assertIn('00:00:01,100 --> 00:00:02,200', translated)
        self.assertIn('00:00:03,300 --> 00:00:04,400', translated)
        self.assertEqual(translated.count('你好，世界。'), 2)
        self.assertEqual(library.resource(self.pid, self.resource).read_text(encoding='utf-8'), self.raw)
        self.assertTrue(all(info['kind'] != 'transcription' for name, info in library.get(self.pid)['resources'].items() if '-AI译文-' in name))
        self.assertEqual(len(library.subtitle_records(library.get(self.pid), self.video)), 1)
        task_runtime.save(transcription.OUTPUT_ROOT / '.tasks', job)
        transcription.jobs.pop(job.id)
        restored = transcription.get(job.id)
        self.assertEqual(restored.operation, 'translation'); self.assertEqual(restored.text, job.text)
        response = self.client.get(f'/api/workspace/transcriptions/{job.id}/files/srt')
        self.assertEqual(response.status_code, 200)
        self.assertIn('AI', response.headers['content-disposition'])
        self.model.assert_called_once()

    def test_chinese_to_english_and_plain_text_upload_result(self):
        path = self.root / 'parent' / 'source.txt'; path.parent.mkdir(); path.write_text('你好，世界。', encoding='utf-8')
        parent = transcription.Job(uuid4().hex, self.subtitle, '上传合成音频.wav', 'auto', owned_input=True)
        parent.state = 'completed'; parent.text = '你好，世界。'; parent.detected_language = 'zh'
        # Completed upload outputs are contained beneath their own output directory.
        folder = transcription.OUTPUT_ROOT / parent.id; folder.mkdir(parents=True)
        output = folder / 'source.txt'; output.write_bytes(path.read_bytes())
        parent.files = {'txt': {'path': output, 'sha256': library.digest(output), 'relative': None, 'project_id': None}}
        transcription.jobs[parent.id] = parent; self.ids.append(parent.id)
        job = self.finish(translations.start({'job_id': parent.id}, 'en', True))
        self.assertEqual(job.state, 'completed', job.message)
        self.assertEqual(job.text, 'Hello world.')
        self.assertEqual(list(job.files), ['txt'])
        self.assertEqual(output.read_text(encoding='utf-8'), '你好，世界。')

    def test_consent_and_source_validation_precede_model_call(self):
        for consent in (False, 1, 'true', None):
            with self.assertRaises(ValueError): translations.start(self.reference, 'zh', consent)
        for ref in ({}, {'job_id': uuid4().hex, **self.reference}, {**self.reference, 'video_resource': 'videos/not-this.mp4'}, {**self.reference, 'resource': '../outside.srt'}):
            with self.assertRaises((ValueError, KeyError)): translations.start(ref, 'zh', True)
        self.require.assert_not_called(); self.model.assert_not_called()

    def test_http_strict_consent_csrf_and_verified_preview_status(self):
        endpoint = '/api/workspace/translations'
        for consent in (False, 1, 'true'):
            self.assertEqual(self.client.post(endpoint, json={**self.reference, 'target': 'zh', 'consent': consent}).status_code, 422)
        self.assertEqual(self.client.post(endpoint, headers={'Origin': 'https://foreign.invalid'}, json={**self.reference, 'target': 'zh', 'consent': True}).status_code, 403)
        self.assertEqual(self.client.post(endpoint, json={**self.reference, 'target': 'zh', 'consent': True, 'agent': {'engine': 'api', 'model': 'x', 'unexpected': 'y'}}).status_code, 422)
        self.model.assert_not_called()
        result = self.client.post(endpoint, json={**self.reference, 'target': 'zh', 'consent': True})
        self.assertEqual(result.status_code, 202, result.text)
        self.assertEqual(self.finish(result.json()).state, 'completed')
        sources = self.client.get('/api/workspace/transcriptions/sources').json()['sources']
        self.assertTrue(sources[0]['has_subtitles'])
        response = self.client.get(f'/api/workspace/projects/{self.pid}/subtitles', params={'video_resource': self.video})
        self.assertEqual(response.status_code, 200); self.assertTrue(response.json()['has_subtitles'])
        self.assertIn('Hello world.', response.json()['text'])

    def test_local_transcription_counts_available_but_not_platform_downloaded(self):
        inventory = library.subtitle_inventory('BV1TaqYBcEJc')
        self.assertEqual(inventory['archived'], [])
        self.assertEqual(inventory['available'], [1])
        self.assertTrue(inventory['episodes'][0]['subtitles'])
        self.assertFalse(inventory['episodes'][0]['platform_subtitles'])
        self.assertFalse(inventory['episodes'][1]['subtitles'])
        library.resource(self.pid, self.resource).write_text('tampered', encoding='utf-8')
        self.assertFalse(routes.transcription_sources()['sources'][0]['has_subtitles'])
        self.assertEqual(library.subtitle_inventory('BV1TaqYBcEJc')['available'], [])

    def test_platform_subtitles_are_available_and_unrelated_video_bindings_are_filtered(self):
        other = library.import_file(self.pid, self.subtitle, 'subtitles/other.srt', 'transcription', {'video_resource': 'videos/other.mp4'})
        self.assertNotIn(other, [row['resource'] for row in library.subtitle_records(library.get(self.pid), self.video)])
        library.import_file(self.pid, self.subtitle, 'subtitles/platform.srt', 'subtitle')
        self.assertEqual(library.subtitle_inventory('BV1TaqYBcEJc')['archived'], [1])

    def test_bad_model_reply_has_no_retry_no_overwrite_and_no_downloadable_result(self):
        self.model.side_effect = lambda *args, **kwargs: SimpleNamespace(text='{"items": [{"index": 0, "text": "partial"}]}')
        job = self.finish(translations.start(self.reference, 'zh', True))
        self.assertEqual(job.state, 'failed'); self.assertFalse(job.files)
        self.model.assert_called_once()
        self.assertEqual(library.resource(self.pid, self.resource).read_text(encoding='utf-8'), self.raw)
        self.assertEqual(len(library.get(self.pid)['resources']), 2)

    def test_batched_translation_keeps_all_cues_including_duplicates(self):
        raw = '\n\n'.join(f'{n+1}\n00:00:01,100 --> 00:00:02,200\nHello world.' for n in range(50))
        source = self.root / 'fifty.srt'; source.write_text(raw, encoding='utf-8')
        resource = library.import_file(self.pid, source, 'subtitles/fifty.srt', 'subtitle')
        job = self.finish(translations.start({**self.reference, 'resource': resource}, 'zh', True))
        self.assertEqual(job.state, 'completed', job.message)
        self.assertEqual(self.model.call_count, 3)
        self.assertEqual(transcription.download_file(job.id, 'srt').read_text(encoding='utf-8').count('-->'), 50)

    def test_api_translation_batches_overlap_with_bounded_executor(self):
        raw = '\n\n'.join(f'{n+1}\n00:00:01,100 --> 00:00:02,200\nHello world.' for n in range(50))
        source = self.root / 'parallel.srt'; source.write_text(raw, encoding='utf-8')
        resource = library.import_file(self.pid, source, 'subtitles/parallel.srt', 'subtitle')
        entered = Event(); lock = Lock()
        active = 0; maximum = 0

        def reply(messages, *args, **kwargs):
            nonlocal active, maximum
            with lock:
                active += 1; maximum = max(maximum, active)
                if active >= 2: entered.set()
            if not entered.wait(3): self.fail('parallel translation did not overlap')
            try: return self.reply(messages, *args, **kwargs)
            finally:
                with lock: active -= 1

        self.model.side_effect = reply
        job = self.finish(translations.start({**self.reference, 'resource': resource}, 'zh', True))
        self.assertEqual(job.state, 'completed', job.message)
        self.assertGreaterEqual(maximum, 2)
        self.assertEqual(self.model.call_count, 3)
        self.assertEqual(transcription.download_file(job.id, 'srt').read_text(encoding='utf-8').count('-->'), 50)

    def test_vtt_conversion_preserves_exact_times_and_drops_cue_settings(self):
        items, timed = translations.segments('WEBVTT\n\n00:01.100 --> 00:02.200 align:start\nHello\n\n00:03.300 --> 00:04.400\nHello', '.vtt')
        self.assertTrue(timed)
        self.assertEqual(items[0]['time'], '00:00:01,100 --> 00:00:02,200')
        self.assertEqual(len(items), 2)
        with self.assertRaises(ValueError): translations.segments('1\n00:00:01,100 --> 00:00:02,200\n逗号是句号, 但这里没有时间\n\n2\n不是时间\n正文', '.srt')

    def long_items(self, count=1090, text='x' * 70):
        return [{'text': text} for _ in range(count)]

    def test_batch_profiles_compatible_cloud_and_local_budgets(self):
        items = self.long_items()
        api, agent = {'engine': 'api', 'model': '', 'effort': ''}, {'engine': 'codex', 'model': 'm', 'effort': ''}
        compatible = translations.plan(api, {'provider': 'custom'}, items)
        self.assertEqual((compatible['profile'], compatible['limit'], compatible['max_items'], compatible['max_tokens']), ('compatible', 2200, 24, None))
        self.assertEqual(len(translations.batches(items, 2200)), len(translations.batches(items, **{k: compatible[k] for k in ('limit', 'max_items', 'output', 'ratio')})))
        cloud = translations.plan(api, {'provider': 'custom'}, items, True)
        groups = translations.batches(items, cloud['limit'], cloud['max_items'], cloud['output'], cloud['ratio'])
        self.assertEqual((cloud['profile'], cloud['max_tokens']), ('cloud', 32768))
        self.assertEqual(len(groups), 4); self.assertEqual(sum(map(len, groups)), 1090)
        self.assertIsNone(translations.plan(agent, {}, items, True)['max_tokens'])
        local = translations.plan(api, {'provider': 'llamacpp', 'startup': {}}, items)
        self.assertEqual((local['profile'], local['max_tokens'], local['max_items']), ('local', 4096, 80))
        for group in translations.batches(items, local['limit'], local['max_items'], local['output'], local['ratio']):
            size = sum(len(row['text']) for row in group)
            self.assertLessEqual(size * local['ratio'] + 10 * len(group) + 64, 4096 * .75)
        # CJK sources are budgeted at a higher reply-token ratio than Latin text.
        self.assertEqual(translations.output_ratio([{'text': '你好世界'}]), 1.0)
        self.assertEqual(translations.output_ratio([{'text': 'Hello world'}]), 0.5)
        with self.assertRaises(ValueError): translations.batches([{'text': '字' * 3000}], 6000, 80, 4096, 1.0)

    def test_confirmed_cloud_task_passes_explicit_output_budget(self):
        job = self.finish(translations.start(self.reference, 'zh', True, output_confirmed=True))
        self.assertEqual(job.state, 'completed', job.message)
        self.assertEqual(job.translation['profile'], 'cloud')
        self.assertEqual(self.model.call_args.kwargs['max_tokens'], 32768)
        with self.assertRaises(ValueError): translations.start(self.reference, 'zh', True, output_confirmed='yes')
        response = self.client.post('/api/workspace/translations', json={**self.reference, 'target': 'zh', 'consent': True, 'output_confirmed': 'yes'})
        self.assertEqual(response.status_code, 422)

    def test_legacy_task_record_without_profile_regroups_as_2200_24(self):
        with patch.object(task_runtime, 'submit'):
            initial = translations.start(self.reference, 'zh', True)
        job = transcription.get(initial['id']); self.ids.append(job.id)
        job.translation = {key: value for key, value in job.translation.items() if key in ('reference', 'sha256', 'target', 'agent', 'limit', 'batches', 'timed')}
        translations.worker(job)
        self.assertEqual(job.state, 'completed', job.message)
        self.assertIsNone(self.model.call_args.kwargs['max_tokens'])

    def test_explicit_budget_rejects_length_truncated_reply(self):
        from video_parser.services import ai_analysis
        choice = SimpleNamespace(finish_reason='length', message=SimpleNamespace(content='{"items": ['))
        client = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=lambda **kwargs: SimpleNamespace(choices=[choice], usage=None))))
        with patch.object(ai_settings, 'configured', return_value=True), patch.object(ai_settings, 'client', return_value=client):
            with self.assertRaises(ai_analysis.AnalysisError): ai_analysis.call_model([], {'model': 'fake'}, max_tokens=4096)

    def test_changed_queued_source_fails_before_model(self):
        with patch.object(task_runtime, 'submit'):
            initial = translations.start(self.reference, 'zh', True)
        job = transcription.get(initial['id']); self.ids.append(job.id)
        library.resource(self.pid, self.resource).write_text('changed', encoding='utf-8')
        translations.worker(job)
        self.assertEqual(job.state, 'failed'); self.model.assert_not_called()

    def test_cancelled_before_model_keeps_original(self):
        from threading import Event
        with patch.object(task_runtime, 'submit'):
            initial = translations.start(self.reference, 'zh', True)
        job = transcription.get(initial['id']); self.ids.append(job.id)
        event = Event(); event.set(); token = task_runtime._context.set({'job': job, 'cancel': event})
        try:
            with self.assertRaises(task_runtime.Cancelled): translations.worker(job)
        finally: task_runtime._context.reset(token)
        self.model.assert_not_called()
        self.assertEqual(library.resource(self.pid, self.resource).read_text(encoding='utf-8'), self.raw)


if __name__ == '__main__': unittest.main(verbosity=2)
