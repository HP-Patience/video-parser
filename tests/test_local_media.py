"""Local video association validation and task contract tests."""
from __future__ import annotations

import hashlib
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from video_parser.services import local_media


class LocalMediaTests(unittest.TestCase):
    def test_picker_requires_local_video_project_and_returns_path(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'clip.mp4'
            path.write_bytes(b'video')
            project = {'id': 'p', 'platform': 'local', 'source_url': '', 'video_id': hashlib.sha256(b'video').hexdigest(),
                       'directory': Path(directory), 'resources': {}}
            with patch.object(local_media.library, 'get', return_value=project):
                result = local_media.picker('p', str(path))
            self.assertEqual(result['path'], str(path.resolve()))
            self.assertFalse(result['cancelled'])

    def test_picker_null_is_cancelled_without_source_access(self):
        project = {'id': 'p', 'platform': 'local', 'source_url': '', 'video_id': 'a' * 64,
                   'directory': Path('.'), 'resources': {}}
        with patch.object(local_media.library, 'get', return_value=project), \
             patch.object(local_media, '_pick_native', return_value=None):
            result = local_media.picker('p')
        self.assertTrue(result['cancelled'])
        self.assertIsNone(result['path'])

    def test_non_local_or_linked_project_rejected(self):
        for project in ({'platform': 'youtube', 'source_url': '', 'video_id': 'a' * 64},
                        {'platform': 'local', 'source_url': 'https://example.test/x', 'video_id': 'a' * 64}):
            with patch.object(local_media.library, 'get', return_value=project):
                with self.assertRaises(local_media.LocalMediaError):
                    local_media.picker('p')

    def test_source_extension_and_absolute_path_are_checked(self):
        project = {'id': 'p', 'platform': 'local', 'source_url': '', 'video_id': 'a' * 64,
                   'directory': Path('.'), 'resources': {}}
        with patch.object(local_media.library, 'get', return_value=project):
            with self.assertRaises(local_media.LocalMediaError):
                local_media.picker('p', 'relative.mp4')
            with tempfile.TemporaryDirectory() as directory:
                path = Path(directory) / 'clip.txt'
                path.write_text('x')
                with self.assertRaises(local_media.LocalMediaError):
                    local_media.picker('p', str(path))

    def test_hash_mismatch_never_probes_or_registers(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / 'clip.mp4'
            source.write_bytes(b'wrong-video')
            project = {'id': 'p', 'platform': 'local', 'source_url': '', 'video_id': '0' * 64,
                       'directory': root, 'resources': {}}
            job = local_media.LocalMediaJob(project_id='p', source=str(source), root=str(root.resolve()))
            with patch.object(local_media.library, 'LIBRARY_ROOT', root), \
                 patch.object(local_media.library, 'get', return_value=project), \
                 patch.object(local_media, '_account_scope', return_value=''), \
                 patch.object(local_media.videos, 'probe') as probe, \
                 patch.object(local_media.library, 'import_file') as publish:
                with self.assertRaises(local_media.LocalMediaError):
                    local_media._worker(job)
            probe.assert_not_called()
            publish.assert_not_called()
            self.assertFalse(any(root.glob('.local-video-*')))

    def test_cancellation_during_copy_stops_before_registration(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / 'clip.mp4'
            source.write_bytes(b'video' * 100)
            job = local_media.LocalMediaJob(root=str(root.resolve()))
            with patch.object(local_media.library, 'LIBRARY_ROOT', root), \
                 patch.object(local_media, '_account_scope', return_value=''), \
                 patch.object(local_media.task_runtime, 'checkpoint', side_effect=local_media.task_runtime.Cancelled()):
                with self.assertRaises(local_media.task_runtime.Cancelled):
                    local_media._copy_and_hash(source, root / 'staged', job)
            self.assertEqual((root / 'staged').stat().st_size, 0)

    def test_changed_library_rejects_queued_job(self):
        job = local_media.LocalMediaJob(root='different-library')
        with self.assertRaises(local_media.LocalMediaError):
            local_media._checkpoint(job)

    def test_valid_video_registers_only_verified_staging_bytes(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / 'clip.mp4'
            content = b'original-video'
            source.write_bytes(content)
            digest = hashlib.sha256(content).hexdigest()
            project = {'id': 'p', 'platform': 'local', 'source_url': '', 'video_id': digest,
                       'directory': root, 'resources': {}}
            job = local_media.LocalMediaJob(project_id='p', source=str(source), root=str(root.resolve()))
            def publish(identifier, staged, relative, kind, details, **options):
                self.assertTrue(options['no_replace'])
                self.assertEqual(options['expected_sha256'], digest)
                options['checkpoint']()
                self.assertNotEqual(Path(staged), source)
                self.assertEqual(Path(staged).read_bytes(), content)
                project['resources'][relative] = {'sha256': digest, 'size_bytes': len(content)}
                return relative
            media = {'format': {'duration': '1'}, 'streams': [{'codec_type': 'video'}]}
            with patch.object(local_media.library, 'LIBRARY_ROOT', root), \
                 patch.object(local_media.library, 'get', return_value=project), \
                 patch.object(local_media, '_account_scope', return_value=''), \
                 patch.object(local_media.videos, 'probe', return_value=media), \
                 patch.object(local_media.library, 'import_file', side_effect=publish):
                local_media._worker(job)
            self.assertEqual(job.state, 'completed')
            self.assertEqual(job.result['sha256'], digest)

    def test_audio_disguised_as_video_is_not_registered(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / 'clip.mp4'
            source.write_bytes(b'audio')
            project = {'id': 'p', 'platform': 'local', 'source_url': '',
                       'video_id': hashlib.sha256(b'audio').hexdigest(), 'directory': root, 'resources': {}}
            job = local_media.LocalMediaJob(project_id='p', source=str(source), root=str(root.resolve()))
            media = {'format': {'duration': '1'}, 'streams': [{'codec_type': 'audio'}]}
            with patch.object(local_media.library, 'LIBRARY_ROOT', root), \
                 patch.object(local_media.library, 'get', return_value=project), \
                 patch.object(local_media, '_account_scope', return_value=''), \
                 patch.object(local_media.videos, 'probe', return_value=media), \
                 patch.object(local_media.library, 'import_file') as publish:
                with self.assertRaises(RuntimeError):
                    local_media._worker(job)
            publish.assert_not_called()

    def test_existing_video_resource_is_not_overwritten(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / 'clip.mp4'
            source.write_bytes(b'video')
            project = {'id': 'p', 'platform': 'local', 'source_url': '',
                       'video_id': hashlib.sha256(b'video').hexdigest(), 'directory': root,
                       'resources': {'videos/clip.mp4': {'kind': 'subtitle', 'sha256': 'old'}}}
            job = local_media.LocalMediaJob(project_id='p', source=str(source), root=str(root.resolve()))
            with patch.object(local_media.library, 'LIBRARY_ROOT', root), \
                 patch.object(local_media.library, 'get', return_value=project), \
                 patch.object(local_media, '_account_scope', return_value=''), \
                 patch.object(local_media.library, 'import_file') as publish:
                with self.assertRaises(local_media.LocalMediaError):
                    local_media._worker(job)
            publish.assert_not_called()
            self.assertEqual(project['resources']['videos/clip.mp4']['sha256'], 'old')

    def test_other_account_project_and_task_are_hidden(self):
        project = {'platform': 'local', 'source_url': '', 'video_id': 'a' * 64,
                   'cloud_scope': 'other', 'resources': {}}
        with patch.object(local_media.library, 'get', return_value=project), \
             patch.object(local_media, '_account_scope', return_value='current'):
            with self.assertRaises(local_media.LocalMediaError):
                local_media._project('p')
        job = local_media.LocalMediaJob(root=str(local_media._root()), account_scope='other')
        local_media._jobs[job.id] = job
        try:
            with patch.object(local_media, '_account_scope', return_value='current'), \
                 patch.object(local_media.task_runtime, 'cancel') as cancel:
                with self.assertRaises(KeyError):
                    local_media.cancel(job.id)
                cancel.assert_not_called()
        finally:
            local_media._jobs.pop(job.id, None)

    def test_real_library_import_and_publication_cancellation(self):
        import json
        for cancel_publication in (False, True):
            with self.subTest(cancel_publication=cancel_publication), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                project_dir = root / 'project'
                project_dir.mkdir()
                content = b'video' * (local_media.CHUNK_SIZE // 5 + 1)
                source = root / 'clip.mp4'
                source.write_bytes(content)
                digest = hashlib.sha256(content).hexdigest()
                project = {'schema_version': 1, 'id': 'a' * 32, 'platform': 'local', 'source_url': '',
                           'video_id': digest, 'resources': {}}
                manifest = project_dir / 'project.json'
                manifest.write_text(json.dumps(project), encoding='utf-8')
                def get(identifier):
                    return {**json.loads(manifest.read_text(encoding='utf-8')), 'directory': project_dir}
                def progress(phase, *args, **kwargs):
                    if cancel_publication and phase == 'publishing':
                        raise local_media.task_runtime.Cancelled()
                job = local_media.LocalMediaJob(project_id=project['id'], source=str(source), root=str(root.resolve()))
                media = {'format': {'duration': '1'}, 'streams': [{'codec_type': 'video'}]}
                with patch.object(local_media.library, 'LIBRARY_ROOT', root), \
                     patch.object(local_media.library, 'get', side_effect=get), \
                     patch.object(local_media, '_account_scope', return_value=''), \
                     patch.object(local_media.videos, 'probe', return_value=media), \
                     patch.object(local_media.task_runtime, 'progress', side_effect=progress):
                    if cancel_publication:
                        with self.assertRaises(local_media.task_runtime.Cancelled):
                            local_media._worker(job)
                    else:
                        local_media._worker(job)
                saved = json.loads(manifest.read_text(encoding='utf-8'))
                if cancel_publication:
                    self.assertEqual(saved['resources'], {})
                    self.assertFalse((project_dir / 'videos' / source.name).exists())
                else:
                    self.assertEqual(saved['resources']['videos/clip.mp4']['sha256'], digest)
                    self.assertEqual((project_dir / 'videos/clip.mp4').read_bytes(), content)
                self.assertFalse(any(project_dir.glob('.local-video-*')))
                self.assertFalse(any(project_dir.glob('.publish-*')))

    def test_start_persists_task_before_queueing(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'clip.mp4'
            path.write_bytes(b'video')
            project = {'id': 'p', 'platform': 'local', 'source_url': '',
                       'video_id': hashlib.sha256(b'video').hexdigest(),
                       'directory': Path(directory), 'resources': {}}
            captured = {}
            def submit(lane, job, worker, persist, **kwargs):
                captured.update(lane=lane, job=job)
                persist()
            with patch.object(local_media.library, 'get', return_value=project), \
                 patch.object(local_media.task_runtime, 'submit', side_effect=submit), \
                 patch.object(local_media, 'JOURNAL_ROOT', Path(directory) / 'tasks'):
                result = local_media.start('p', str(path))
            self.assertEqual(result['state'], 'queued')
            self.assertEqual(captured['lane'], 'local-media')
            self.assertTrue((Path(directory) / 'tasks' / (captured['job'].id + '.json')).is_file())


if __name__ == '__main__':
    unittest.main()
