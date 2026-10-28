"""Project root changes in disposable libraries only; model calls are mocked."""
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
import unittest

from tests.offline import test_archive_titles_layout as base
from video_parser.services import project_folder_naming as folders, library, library_layout, resource_renames as names
from video_parser.services import library_migration, model_calls, naming, archive_layout


class ProjectFolderNamingTests(unittest.TestCase):
    setUp = base.ArchiveTitlesLayoutTests.setUp
    project = base.ArchiveTitlesLayoutTests.project
    files = base.ArchiveTitlesLayoutTests.files

    def test_folder_display_title_follows_confirmed_move_without_rewriting_source(self):
        first, second = self.files(self.project()), self.files(self.project(2))
        source = first['title']; episode = library.episode_title(first)
        plan = folders.preview(first['id'], '课程__新名称')
        self.assertEqual(library.public(first)['display_title'], source)
        folders.apply(plan['id'], plan['preview_token'])
        for identifier in (first['id'], second['id']):
            current = library.get(identifier); public = library.public(current)
            self.assertEqual(public['display_title'], '课程__新名称')
            self.assertEqual(public['title'], source)
        self.assertEqual(library.episode_title(library.get(first['id'])), episode)
        from video_parser.web.routes import list_projects
        rows = list_projects()['projects']
        self.assertEqual({row['display_title'] for row in rows}, {'课程__新名称'})
        # A proposed replacement must not leak into the name before explicit apply.
        folders.preview(first['id'], '尚未确认')
        self.assertEqual(library.public(library.get(first['id']))['display_title'], '课程__新名称')

    def test_explicit_title_edit_still_works_until_next_folder_rename(self):
        first, second = self.files(self.project()), self.files(self.project(2))
        plan = folders.preview(first['id'], '文件夹标题'); folders.apply(plan['id'], plan['preview_token'])
        # Explicitly choosing the source title must work even when raw metadata already equals it.
        library.rename(first['id'], first['title'])
        for identifier in (first['id'], second['id']):
            self.assertEqual(library.public(library.get(identifier))['display_title'], first['title'])
        library.rename(first['id'], '手动显示名称')
        self.assertEqual(library.public(library.get(second['id']))['display_title'], '手动显示名称')
        next_plan = folders.preview(first['id'], '再次修改文件夹'); folders.apply(next_plan['id'], next_plan['preview_token'])
        self.assertEqual(library.public(library.get(second['id']))['display_title'], '再次修改文件夹')
        self.assertEqual(library.get(second['id'])['title'], '手动显示名称')

    def test_failed_or_restored_folder_move_does_not_change_display_title(self):
        p = self.files(self.project()); plan = folders.preview(p['id'], '未生效')
        original = library_migration._move
        def fail_after(a,b): original(a,b); raise OSError('fixture interruption')
        with patch.object(library_migration, '_move', side_effect=fail_after):
            with self.assertRaises(OSError): folders.apply(plan['id'], plan['preview_token'])
        self.assertEqual(library.public(library.get(p['id']))['display_title'], p['title'])
        folders.apply(plan['id'], undo=True)
        self.assertEqual(library.public(library.get(p['id']))['display_title'], p['title'])

    def test_unrelated_or_malformed_naming_plans_do_not_break_display(self):
        p = self.files(self.project()); plan = folders.preview(p['id'], '别的目录')
        root = library.LIBRARY_ROOT / '.naming-plans'; root.mkdir(exist_ok=True)
        (root / ('e'*32+'.json')).write_text('[]', encoding='utf-8')
        self.assertEqual(library.public(p)['display_title'], p['title'])
        saved=names.load_plan(plan['id']); saved['state']='applied'; names.save_plan(saved)
        self.assertEqual(library.public(p)['display_title'], p['title'])

    def test_multi_root_move_preserves_all_bytes_ids_and_old_paths(self):
        first, second = self.files(self.project()), self.files(self.project(2))
        old = first['directory'].parent
        before = library_migration.inventory(old)
        saved = str(library.resource(first['id'], 'videos/original.mp4'))
        plan = folders.preview(first['id'], '线性代数')
        self.assertTrue(old.is_dir())
        self.assertEqual(plan['folder_preview']['projects'], 2)
        self.assertEqual(names.history(first['id']), [])
        folders.apply(plan['id'], plan['preview_token'])
        current = library.get(first['id'])
        self.assertFalse(old.exists())
        self.assertEqual(library_migration.inventory(current['directory'].parent), before)
        self.assertEqual(current['title'], first['title'])
        self.assertEqual(library.get(second['id'])['directory'].parent, current['directory'].parent)
        self.assertEqual(library_layout.restore_task_paths({'path': saved})['path'], str(library.resource(first['id'], 'videos/original.mp4')))
        self.assertEqual(self.project(2)['directory'], library.get(second['id'])['directory'])

    def test_single_flat_root_and_platform_identity_suffix(self):
        p = self.files(self.project(count=1))
        before = library_migration.inventory(p['directory'])
        plan = folders.preview(p['id'], '独立讲座')
        folders.apply(plan['id'], plan['preview_token'])
        current = library.get(p['id'])
        self.assertEqual(current['directory'].name, '独立讲座__BV16NG96QEmA')
        self.assertEqual(len(current['directory'].relative_to(library.LIBRARY_ROOT).parts), 2)
        self.assertEqual(before, library_migration.inventory(current['directory']))

    def test_other_platform_and_no_cross_platform_alias(self):
        p = library.ensure({'platform': 'youtube', 'video_id': 'aqz-KE-bpKQ', 'title': '合成演示', 'source_url': 'https://www.youtube.com/watch?v=aqz-KE-bpKQ'})
        p = self.files(p)
        plan = folders.preview(p['id'], '演示')
        folders.apply(plan['id'], plan['preview_token'])
        self.assertTrue(library.resource(p['id'], 'videos/original.mp4').exists())
        with self.assertRaises(ValueError): library_layout.validate_move({'project_id': p['id'], 'old': 'youtube/a', 'new': 'bilibili/b'})

    def test_unpreviewed_stale_edit_conflict_and_busy_are_refused(self):
        p = self.files(self.project())
        plan = folders.preview(p['id'], '简短名称')
        with self.assertRaises(ValueError): folders.apply(plan['id'], '0' * 32)
        (p['directory'] / 'personal-learning/new-note.txt').write_text('new learning')
        with self.assertRaises(ValueError): folders.apply(plan['id'], plan['preview_token'])
        self.assertTrue(p['directory'].exists())
        from video_parser.services import task_runtime
        with task_runtime.exclusive_operation():
            with self.assertRaises(BlockingIOError): folders.apply(plan['id'], plan['preview_token'])
        target = library.LIBRARY_ROOT / plan['folder_preview']['new']; target.mkdir()
        with self.assertRaises(FileExistsError): folders.preview(p['id'], '简短名称')
        for title in ['../escape', 'bad/name', 'CON', 'name.', ' hidden', 'a\u202eb']:
            with self.subTest(title=title), self.assertRaises(ValueError): folders.preview(p['id'], title)

    def test_crash_after_atomic_move_can_recover_without_losing_materials(self):
        p = self.files(self.project())
        old = p['directory'].parent; before = library_migration.inventory(old)
        plan = folders.preview(p['id'], '恢复案例')
        actual_move = library_migration._move
        def interrupted(a, b):
            actual_move(a, b)
            raise OSError('simulated process failure after rename')
        with patch.object(library_migration, '_move', side_effect=interrupted):
            with self.assertRaises(OSError): folders.apply(plan['id'], plan['preview_token'])
        self.assertEqual(names.load_plan(plan['id'])['state'], 'recovery_required')
        with self.assertRaises(ValueError): folders.preview(p['id'], '另一次')
        folders.apply(plan['id'], undo=True)
        self.assertEqual(library_migration.inventory(old), before)
        self.assertEqual(library.get(p['id'])['directory'], p['directory'])

    def test_pre_move_access_failure_unlocks_without_claiming_acl_or_touching_files(self):
        p = self.files(self.project()); old = p['directory'].parent
        before = library_migration.inventory(old); plan = folders.preview(p['id'], '访问失败')
        error = PermissionError(13, 'fixture access denied'); error.winerror = 5
        with patch.object(library_migration, '_move', side_effect=error):
            with self.assertRaises(folders.FolderOperationError) as caught: folders.apply(plan['id'], plan['preview_token'])
        saved = names.load_plan(plan['id'])
        self.assertEqual(saved['state'], 'failed')
        self.assertEqual(saved['error_detail']['winerror'], 5)
        self.assertEqual(saved['error_detail']['stage'], 'rename')
        self.assertIn('可能被占用或权限受限', str(caught.exception))
        self.assertIn('改名未执行', saved['last_error'])
        self.assertEqual(before, library_migration.inventory(old))
        self.assertEqual(folders.preview(p['id'], '重新预览')['state'], 'validated')

    @unittest.skipUnless(__import__('os').name == 'nt', 'Windows filesystem sharing semantics')
    def test_windows_open_media_blocks_rename_but_closing_it_allows_fresh_preview(self):
        p = self.files(self.project()); old = p['directory'].parent
        before = library_migration.inventory(old); plan = folders.preview(p['id'], 'Windows占用案例')
        with library.resource(p['id'], 'videos/original.mp4').open('rb'):
            with self.assertRaises(folders.FolderOperationError): folders.apply(plan['id'], plan['preview_token'])
        failed = names.load_plan(plan['id'])
        self.assertEqual(failed['state'], 'failed')
        self.assertIn(failed['error_detail']['winerror'], (5,32,33))
        self.assertEqual(library_migration.inventory(old), before)
        next_plan = folders.preview(p['id'], 'Windows占用案例')
        folders.apply(next_plan['id'], next_plan['preview_token'])
        self.assertEqual(library_migration.inventory(library.get(p['id'])['directory'].parent), before)

    def test_sharing_violation_has_distinct_code(self):
        from video_parser.services import task_runtime
        error = PermissionError(13, 'fixture sharing violation'); error.winerror = 32
        code, message = task_runtime.classify(error)
        self.assertEqual(code, 'file_in_use'); self.assertIn('正在被占用', message)

    def test_historical_unmoved_failure_clears_only_journal_and_requires_new_preview(self):
        p = self.files(self.project()); old = p['directory'].parent
        before = library_migration.inventory(old); plan = folders.preview(p['id'], '历史失败')
        saved = names.load_plan(plan['id']); saved.update(state='recovery_required', last_error='旧错误'); names.save_plan(saved)
        self.assertEqual(folders.public(saved)['recovery_status'], 'not_moved')
        with patch.object(library_migration, '_move') as move, patch.object(archive_layout, 'publish_aliases') as aliases, patch.object(model_calls, 'call') as call:
            folders.apply(plan['id'], undo=True)
        move.assert_not_called(); aliases.assert_not_called(); call.assert_not_called()
        self.assertEqual(names.load_plan(plan['id'])['state'], 'rolled_back')
        self.assertEqual(names.load_plan(plan['id'])['recovery_outcome'], 'not_moved')
        self.assertEqual(library_migration.inventory(old), before)
        with self.assertRaises(ValueError): folders.apply(plan['id'], plan['preview_token'])
        self.assertEqual(folders.preview(p['id'], '新的预览')['state'], 'validated')

    def test_restore_job_message_does_not_claim_a_rename(self):
        p = self.files(self.project()); plan = folders.preview(p['id'], '失败计划')
        saved = names.load_plan(plan['id']); saved['state'] = 'recovery_required'; names.save_plan(saved)
        job = naming.Job('f'*32, p['id'], p['title'], operation='folder_restore', payload={'mode':'folder_restore','plan_id':plan['id']})
        naming.worker(job)
        self.assertEqual(job.state, 'completed')
        self.assertIn('没有重新改名', job.message)
        self.assertEqual(names.load_plan(plan['id'])['state'], 'rolled_back')

    def test_historical_recovery_refuses_changed_files(self):
        p = self.files(self.project()); plan = folders.preview(p['id'], '旧计划')
        saved = names.load_plan(plan['id']); saved['state'] = 'recovery_required'; names.save_plan(saved)
        (p['directory'] / 'personal-learning/note.json').write_text('new personal note')
        with self.assertRaises(ValueError): folders.apply(plan['id'], undo=True)
        self.assertEqual(names.load_plan(plan['id'])['state'], 'recovery_required')
        self.assertEqual((p['directory'] / 'personal-learning/note.json').read_text(), 'new personal note')

    def test_public_single_flag_uses_catalog_not_download_count(self):
        p = self.files(self.project(count=3))
        self.assertFalse(library.public(p)['is_single'])
        p['episodes'] = [{'part':1,'title':'唯一视频'}]
        self.assertTrue(library.public(p)['is_single'])
        p['episodes'] = []
        self.assertFalse(library.public(p)['is_single'])

    def test_model_suggestion_sends_only_titles_and_does_not_rename(self):
        p = self.files(self.project())
        before = library_migration.inventory(p['directory'].parent)
        plan = folders.new_plan(p['id'], 'analyzing')
        plan['agent'] = {'engine': 'codex', 'model': 'fixture', 'effort': ''}; names.save_plan(plan)
        with patch.object(model_calls, 'call', return_value=SimpleNamespace(text='{"title":"简明课程"}')) as call, patch.object(model_calls, 'workspace', return_value=self.root / 'empty'):
            folders.propose(plan, self.root / 'task')
        content = json.dumps(call.call_args.args[0], ensure_ascii=False)
        self.assertIn('合成课程', content)
        self.assertNotIn('"note"', content)
        self.assertEqual(names.load_plan(plan['id'])['suggestion'], '简明课程')
        self.assertEqual(library_migration.inventory(p['directory'].parent), before)
        self.assertEqual(call.call_count, 1)

    def test_single_part_video_uses_video_title_instead_of_raw_p1_title(self):
        p = self.files(self.project(count=1))
        self.assertTrue(library.public(p)['is_single'])
        # B站 reports the uploader's raw file name as the P1 title of a one-part video.
        p = library.ensure({**p, 'episodes': [{'part': 1, 'title': 'video_final'}]})
        self.assertEqual(p['episodes'][0]['title'], 'video_final')
        # Rename preview must not offer the raw file name as the new filename stem.
        self.assertEqual(names.inventory(p['id'])['entries'][0]['title'], '合成课程')
        # Folder naming must not send the raw file name to the model as an episode name.
        plan = folders.new_plan(p['id'], 'analyzing')
        plan['agent'] = {'engine': 'codex', 'model': 'fixture', 'effort': ''}; names.save_plan(plan)
        with patch.object(model_calls, 'call', return_value=SimpleNamespace(text='{"title":"简明课程"}')) as call, patch.object(model_calls, 'workspace', return_value=self.root / 'empty'):
            folders.propose(plan, self.root / 'task')
        content = json.dumps(call.call_args.args[0], ensure_ascii=False)
        self.assertIn('合成课程', content)
        self.assertNotIn('video_final', content)

    def test_naming_job_uses_folder_worker_and_safe_task_presentation(self):
        p = self.files(self.project())
        plan = folders.preview(p['id'], '命名任务')
        job = naming.Job('f'*32, p['id'], p['title'], operation='folder_apply', payload={'mode':'folder_apply', 'plan_id':plan['id'], 'token':plan['preview_token']})
        naming.worker(job)
        self.assertEqual(job.state, 'completed')
        from video_parser.services import task_files
        resolve = task_files.filename_labels()
        result = resolve(job, 'naming', with_presentation=True)
        self.assertEqual(len(result['subjects']), 1)
        self.assertEqual(result['file_details'], [])


if __name__ == '__main__': unittest.main()
