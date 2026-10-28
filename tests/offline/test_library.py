"""Persistent project storage regressions, all resources are isolated in temporary roots."""
from video_parser import cli as cli_entry
from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from zipfile import ZipFile

from video_parser.services import library as store
class LibraryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.addCleanup(patch.stopall)
        patch.object(store, 'LIBRARY_ROOT', self.root / 'library').start()
        self.meta = {'platform': '哔哩哔哩', 'video_id': 'BV16NG96QEmA', 'title': '原标题',
                     'source_url': 'https://www.bilibili.com/video/BV16NG96QEmA?p=3&token=private'}

    def test_current_project_completion_without_multi_catalog_persists_only_learned(self):
        variants = [
            {**self.meta, 'video_id': 'BV1TaqYBcEJc', 'source_url': 'https://www.bilibili.com/video/BV1TaqYBcEJc', 'episodes': [{'part': 1, 'title': '单集', 'duration': 60}]},
            self.meta,  # Unknown catalog: allow only current P3, without declaring the course single.
            {'platform': 'YouTube', 'video_id': 'abcdefghijk', 'title': '单条视频', 'source_url': 'https://www.youtube.com/watch?v=abcdefghijk'},
        ]
        for meta in variants:
            with self.subTest(platform=meta['platform'], source=meta['source_url']):
                project = store.ensure(meta)
                path = project['directory'] / 'project.json'
                before = json.loads(path.read_text(encoding='utf-8'))
                others = {p: p.read_bytes() for p in store.manifests() if p != path}
                for learned in (True, False):
                    self.assertEqual(store.set_learning_state(project['id'], project.get('part') or 1, learned), [])
                    self.assertEqual(store.public(store.get(project['id']))['learned'], learned)
                    after = json.loads(path.read_text(encoding='utf-8'))
                    self.assertEqual({k:v for k,v in after.items() if k != 'learned'}, {k:v for k,v in before.items() if k != 'learned'})
                    self.assertEqual(others, {p:p.read_bytes() for p in store.manifests() if p != path})
                    self.assertEqual(store.get(project['id'])['directory'], project['directory'])

    def test_current_project_completion_rejects_other_parts_and_invalid_values(self):
        project = store.ensure(self.meta)
        path = project['directory'] / 'project.json'; before = path.read_bytes()
        for part, learned in [(1, True), (4, True), (0, True), (True, True), (3, 'true')]:
            with self.subTest(part=part, learned=learned), self.assertRaises(ValueError):
                store.set_learning_state(project['id'], part, learned)
        self.assertEqual(path.read_bytes(), before)
        self.assertEqual(len(store.manifests()), 1)

    def test_single_completion_api_returns_current_project_state_without_fake_catalog(self):
        from video_parser.web.routes import LearningBody, update_learning
        project = store.ensure({**self.meta, 'source_url': 'https://www.bilibili.com/video/BV16NG96QEmA'})
        for learned in (True, False):
            result = update_learning(project['id'], LearningBody(part=1, learned=learned))
            self.assertEqual(result, {'episodes': [], 'learned': learned})
            self.assertEqual(store.public(store.get(project['id']))['learned'], learned)

    def test_single_part_video_ignores_the_platform_p1_title(self):
        # Bilibili P1 titles are often the uploader's raw file name; a one-part video needs no label.
        project = store.ensure({**self.meta, 'source_url': 'https://www.bilibili.com/video/BV1JZpd6nEjF',
                                'episodes': [{'part': 1, 'title': 'video_final', 'duration': 181}]})
        self.assertIsNone(store.episode_title(project))
        self.assertIsNone(store.public(project)['episode_title'])
        self.assertEqual(store.public(project)['title'], '原标题')
        # A user rename always wins.
        self.assertEqual(store.episode_title({**project, 'episode_title': '用户自定义章节'}), '用户自定义章节')

    def test_episode_title_falls_back_to_selected_catalog_part(self):
        catalog = [{'part': 3, 'title': '3. 向量空间', 'duration': 60}, {'part': 4, 'title': '4. 子空间', 'duration': 80}]
        project = store.ensure({**self.meta, 'episodes': catalog})
        before = (project['directory'] / 'project.json').read_bytes()
        self.assertEqual(store.public(project)['episode_title'], '3. 向量空间')
        self.assertEqual(store.public(project)['title'], '原标题')
        self.assertEqual((project['directory'] / 'project.json').read_bytes(), before)
        self.assertEqual(store.episode_title({**project, 'episode_title': '用户自定义章节'}), '用户自定义章节')
        self.assertIsNone(store.episode_title({**project, 'part': 9}))
        self.assertIsNone(store.episode_title({**project, 'platform': 'local'}))

    def test_identity_title_stability_and_parts(self):
        one = store.ensure(self.meta)
        again = store.ensure({**self.meta, 'title': '新标题', 'source_url': self.meta['source_url'] + '&other=1'})
        other = store.ensure({**self.meta, 'source_url': 'https://www.bilibili.com/video/BV16NG96QEmA?p=4'})
        self.assertEqual(one['id'], again['id'])
        self.assertEqual(one['directory'], again['directory'])
        self.assertNotEqual(one['id'], other['id'])
        self.assertIn('P003', str(one['directory']))
        text = (one['directory'] / 'project.json').read_text(encoding='utf-8')
        self.assertNotIn('private', text)
        self.assertNotIn('token=', text)

    def test_import_conflict_no_overwrite_and_export(self):
        project = store.ensure(self.meta)
        source = self.root / 'source.txt'
        source.write_text('first')
        first = store.import_file(project['id'], source, 'subtitles/subtitle.md', 'subtitle')
        self.assertEqual(store.import_file(project['id'], source, 'subtitles/subtitle.md', 'subtitle'), first)
        source.write_text('second')
        second = store.import_file(project['id'], source, 'subtitles/subtitle.md', 'subtitle')
        self.assertNotEqual(first, second)
        self.assertEqual(store.resource(project['id'], first).read_text(), 'first')
        self.assertEqual(store.resource(project['id'], second).read_text(), 'second')
        with ZipFile(store.archive(project['id'])) as zipped:
            self.assertIn('project.json', zipped.namelist())
            self.assertIn(first, zipped.namelist())
            self.assertIn(second, zipped.namelist())
            self.assertFalse(any(name.startswith('.') for name in zipped.namelist()))

    def test_reopen_and_bad_file_not_valid(self):
        p = store.ensure(self.meta)
        source = self.root / 'source'; source.write_bytes(b'content')
        name = store.import_file(p['id'], source, 'video.mp4', 'video')
        self.assertEqual(store.get(p['id'])['id'], p['id'])
        (p['directory'] / name).write_bytes(b'changed')
        with self.assertRaises(ValueError): store.resource(p['id'], name)
        with self.assertRaises(ValueError): store.import_file(p['id'], source, '../escape', 'video')
        with self.assertRaises((KeyError, ValueError)): store.resource(p['id'], '../escape')

    def test_concurrent_same_identity_and_resources(self):
        with ThreadPoolExecutor(max_workers=4) as pool:
            projects = list(pool.map(lambda _: store.ensure(self.meta), range(4)))
        self.assertEqual(len({p['id'] for p in projects}), 1)
        p=projects[0]
        source=self.root/'source';source.write_text('one')
        with ThreadPoolExecutor(max_workers=4) as pool:
            list(pool.map(lambda i: store.import_file(p['id'], source, f'analysis/{i}.md', 'analysis'), range(4)))
        self.assertEqual(len(store.get(p['id'])['resources']),4)

    def test_local_content_identity_and_batch_reports(self):
        source=self.root/'local.mp4';source.write_bytes(b'local content')
        one=store.local_project(source)
        copy=self.root/'renamed.mp4';copy.write_bytes(source.read_bytes())
        self.assertEqual(one['id'],store.local_project(copy)['id'])
        first=store.save_text(one['id'],'analysis','first')
        second=store.save_text(one['id'],'analysis','second')
        self.assertNotEqual(first,second)
        self.assertEqual(store.owner(store.resource(one['id'],first))['id'],one['id'])

    def test_learning_state_is_per_episode_without_automatic_download(self):
        episodes = [
            {'part': 1, 'title': '开场', 'duration': 60},
            {'part': 2, 'title': '核心内容', 'duration': 120},
            {'part': 3, 'title': '总结', 'duration': 90},
        ]
        project = store.ensure({**self.meta, 'episodes': episodes})
        self.assertIsNone(store.normalize_episodes([{"part": 1, "duration": 10 ** 400}])[0]["duration"])
        self.assertEqual(project['part'], 3)
        rows = store.learning_episodes(project['id'])
        self.assertEqual([row['part'] for row in rows], [1, 2, 3])
        self.assertEqual(rows[2]['project_id'], project['id'])
        self.assertFalse(any(row['archived'] or row['learned'] for row in rows))

        # Clearing an uncreated episode must not create a project directory.
        rows = store.set_learning_state(project['id'], 2, False)
        self.assertIsNone(rows[1]['project_id'])
        self.assertEqual(len(store.manifests()), 1)

        video = self.root / 'episode-three.mp4'
        video.write_bytes(b'archived episode three')
        store.import_file(project['id'], video, 'video.mp4', 'video')
        self.assertTrue(store.learning_episodes(project['id'])[2]['archived'])

        rows = store.set_learning_state(project['id'], 2, True)
        episode_two = rows[1]
        self.assertTrue(episode_two['learned'])
        self.assertFalse(episode_two['archived'])
        self.assertIsNotNone(episode_two['project_id'])
        saved = store.get(episode_two['project_id'])
        self.assertTrue(saved['learned'])
        self.assertEqual(saved['episodes'], episodes)
        self.assertEqual(saved['resources'], {})

        rows = store.set_learning_state(project['id'], 3, True)
        self.assertTrue(rows[2]['learned'])
        rows = store.set_learning_state(project['id'], 2, False)
        self.assertFalse(rows[1]['learned'])
        with self.assertRaises(ValueError):
            store.set_learning_state(project['id'], 4, True)


    def test_legacy_part_borrows_full_catalog_without_rewriting_manifests(self):
        episodes = [{'part': i, 'title': f'第{i}讲', 'duration': 60} for i in range(1, 4)]
        full = store.ensure({**self.meta, 'episodes': episodes})
        legacy = store.ensure({**self.meta, 'source_url': 'https://www.bilibili.com/video/BV16NG96QEmA?p=2'})
        before = {p: p.read_bytes() for p in store.manifests()}
        rows = store.learning_episodes(legacy['id'])
        self.assertEqual([row['part'] for row in rows], [1, 2, 3])
        self.assertEqual(rows[1]['project_id'], legacy['id'])
        self.assertEqual(rows[2]['project_id'], full['id'])
        self.assertEqual(before, {p: p.read_bytes() for p in store.manifests()})
        self.assertNotIn('episodes', store.get(legacy['id']))

    def test_legacy_catalog_fallback_keeps_course_and_part_boundaries(self):
        legacy = store.ensure(self.meta)
        episodes = [{'part': i, 'title': f'第{i}讲', 'duration': 60} for i in range(1, 4)]
        store.ensure({**self.meta, 'video_id': 'BV1TaqYBcEJc',
                      'source_url': 'https://www.bilibili.com/video/BV1TaqYBcEJc', 'episodes': episodes})
        self.assertEqual(store.learning_episodes(legacy['id']), [])
        with self.assertRaises(ValueError): store.set_learning_state(legacy['id'], 2, True)
        store.ensure({**self.meta, 'source_url': 'https://www.bilibili.com/video/BV16NG96QEmA?p=1', 'episodes': episodes})
        self.assertEqual(len(store.learning_episodes(legacy['id'])), 3)
        with self.assertRaises(ValueError): store.set_learning_state(legacy['id'], 99, True)

    def test_learning_changes_from_legacy_part_use_shared_catalog(self):
        episodes = [{'part': i, 'title': f'第{i}讲', 'duration': 60} for i in range(1, 4)]
        store.ensure({**self.meta, 'source_url': 'https://www.bilibili.com/video/BV16NG96QEmA?p=1', 'episodes': episodes})
        legacy = store.ensure(self.meta)
        rows = store.set_learning_state(legacy['id'], 2, True)
        self.assertTrue(rows[1]['learned'])
        saved = store.get(rows[1]['project_id'])
        self.assertEqual(saved['resources'], {})
        self.assertEqual(saved['episodes'], episodes)
        self.assertNotIn('episodes', store.get(legacy['id']))
        rows = store.set_learning_state(legacy['id'], 2, False)
        self.assertFalse(rows[1]['learned'])

    def test_rename_is_display_only_and_shared_by_course_parts(self):
        episodes = [{'part': 3, 'title': '总结', 'duration': 90}, {'part': 4, 'title': '附录', 'duration': 30}]
        three = store.ensure({**self.meta, 'episodes': episodes})
        four = store.ensure({**self.meta, 'source_url': 'https://www.bilibili.com/video/BV16NG96QEmA?p=4', 'episodes': episodes})
        other = store.ensure({**self.meta, 'video_id': 'BV1TaqYBcEJc', 'source_url': 'https://www.bilibili.com/video/BV1TaqYBcEJc?p=1'})
        video = self.root / 'part.mp4'
        video.write_bytes(b'part three')
        relative = store.import_file(three['id'], video, 'video.mp4', 'video')
        project, renamed = store.rename(three['id'], '  我的\n 线性代数  ')
        self.assertEqual(project['title'], '我的 线性代数')
        self.assertEqual(sorted(renamed), sorted([three['id'], four['id']]))
        self.assertEqual((project['id'], project['directory']), (three['id'], three['directory']))
        self.assertEqual(store.resource(three['id'], relative).read_bytes(), b'part three')
        self.assertEqual(store.get(four['id'])['title'], '我的 线性代数')
        self.assertEqual(store.get(other['id'])['title'], '原标题')
        # Re-parsing the same link must not restore the platform title.
        self.assertEqual(store.ensure({**self.meta, 'title': '平台标题'})['title'], '我的 线性代数')
        for bad in ['', '   ', 'x' * 201, 'a\x00b', None]:
            with self.assertRaises(ValueError):
                store.rename(three['id'], bad)
        with self.assertRaises(KeyError):
            store.rename('0' * 32, '标题')

    def test_subtitle_inventory_counts_only_verified_platform_subtitles(self):
        episodes = [{'part': 3, 'title': '总结', 'duration': 90}, {'part': 4, 'title': '附录', 'duration': 30}]
        three = store.ensure({**self.meta, 'episodes': episodes})
        four = store.ensure({**self.meta, 'source_url': 'https://www.bilibili.com/video/BV16NG96QEmA?p=4', 'episodes': episodes})
        self.assertEqual(store.subtitle_inventory('BV16NG96QEmA')['archived'], [])
        subtitle = self.root / 'p3.srt'
        subtitle.write_text('1\n00:00:00,000 --> 00:00:01,000\n字幕', encoding='utf-8')
        store.import_file(three['id'], subtitle, 'subtitles/p3.平台字幕.srt', 'subtitle')
        transcript = self.root / 'p4.srt'
        transcript.write_text('1\n00:00:00,000 --> 00:00:01,000\n转写', encoding='utf-8')
        changed = store.import_file(four['id'], transcript, 'subtitles/p4.转写.srt', 'transcription')
        inventory = store.subtitle_inventory('BV16NG96QEmA')
        self.assertEqual((inventory['total'], inventory['archived']), (2, [3]))
        self.assertEqual([row['subtitles'] for row in inventory['episodes']], [True, True])
        self.assertEqual(inventory['available'], [3, 4])
        self.assertEqual([row['platform_subtitles'] for row in inventory['episodes']], [True, False])
        # A registered file that no longer matches its hash is not counted.
        (four['directory'] / changed).write_text('changed', encoding='utf-8')
        store.import_file(four['id'], subtitle, 'subtitles/p4.平台字幕.srt', 'subtitle')
        (four['directory'] / 'subtitles/p4.平台字幕.srt').write_text('tampered', encoding='utf-8')
        self.assertEqual(store.subtitle_inventory('BV16NG96QEmA')['archived'], [3])
        with self.assertRaises(ValueError):
            store.subtitle_inventory('../BV16NG96QEmA')


class CLIProjectTests(unittest.TestCase):
    setUp = LibraryTests.setUp
    def test_cli_default_cover_and_download_share_project_and_explicit_directory_stays_flat(self):
        from video_parser.services import videos as cli
        parsed = cli.ParsedVideo(self.meta, {}, ['https://fixture/video'], ['https://fixture/audio'])
        media={'streams':[{'codec_type':'video'},{'codec_type':'audio'}],'format':{'duration':2}}
        def download(parsed, target):
            Path(target).write_bytes(b'media')
            return {'path':str(target),'size_bytes':5,'media':media,'title':self.meta['title']}
        def cover(parsed, target):
            from PIL import Image
            Image.new('RGB',(8,8),'green').save(target,format='PNG')
            return {'path':str(target),'size_bytes':Path(target).stat().st_size}
        with patch.object(cli,'parse_link',return_value=parsed), patch.object(cli,'download_video',side_effect=download) as dl, patch.object(cli,'save_cover',side_effect=cover), patch.object(cli,'probe',return_value=media):
            first=cli_entry.execute(cli_entry.build_parser().parse_args(['download','url']))
            second=cli_entry.execute(cli_entry.build_parser().parse_args(['download','url']))
            covered=cli_entry.execute(cli_entry.build_parser().parse_args(['cover','url']))
            self.assertEqual(first['project_id'],covered['project_id'])
            self.assertEqual(first['path'],second['path'])
            self.assertEqual(dl.call_count,1)
            explicit=self.root/'explicit';explicit.mkdir()
            flat=cli_entry.execute(cli_entry.build_parser().parse_args(['download','url','--output-dir',str(explicit)]))
            self.assertEqual(Path(flat['path']).parent,explicit)
            listed=cli_entry.execute(cli_entry.build_parser().parse_args(['list']))
            self.assertIn(first['path'],[item['path'] for item in listed['files']])


if __name__ == '__main__': unittest.main()
