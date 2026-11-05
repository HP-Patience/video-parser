"""Scratch cleanup must never demote an already archived result."""
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import MagicMock, patch

from video_parser.services import task_runtime, workflows


class WorkflowCleanupTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory(); self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        patched = patch.object(workflows, 'CACHE_ROOT', self.root / 'managed-jobs'); patched.start(); self.addCleanup(patched.stop)

    def run_worker(self, cleanup_error):
        job = workflows.Job(id='a' * 32, kind='analysis', project_id='b' * 32, title='t', payload={'x': 1})
        directory = workflows.CACHE_ROOT / job.id
        directory.mkdir(parents=True)
        (directory / 'result.json').write_text(json.dumps({'ok': True, 'result': {
            'text': '摘要', 'record': {'template': 'summary', 'source': 'both', 'model': 'm', 'frame_count': 16}}}), encoding='utf-8')
        process = MagicMock(); process.returncode = 0
        process.__enter__.return_value = process
        with patch.object(workflows.subprocess, 'Popen', return_value=process), \
             patch.object(task_runtime, 'communicate', return_value=(None, None)), \
             patch.object(task_runtime, 'progress'), patch.object(task_runtime, 'checkpoint'), \
             patch.object(task_runtime, 'own_directory'), \
             patch.object(workflows.shutil, 'rmtree', side_effect=cleanup_error):
            workflows.worker(job)
        return job

    def test_cleanup_failure_keeps_the_archived_result(self):
        # Some environments wrap shutil.rmtree with a guard that raises SystemExit, not OSError.
        job = self.run_worker(SystemExit(1))
        self.assertEqual(job.state, 'completed')
        self.assertEqual(job.error_code, '')
        self.assertIn('临时副本清理失败', job.message)
        self.assertEqual(job.result['record']['frame_count'], 16)
        self.assertEqual(job.details['count'], 16)

    def test_oserror_during_cleanup_also_keeps_the_result(self):
        job = self.run_worker(PermissionError('locked'))
        self.assertEqual(job.state, 'completed')
        self.assertIn('临时副本清理失败', job.message)

    def test_successful_cleanup_leaves_the_message_clean(self):
        job = self.run_worker(None)
        self.assertEqual(job.state, 'completed')
        self.assertNotIn('清理失败', job.message)


if __name__ == '__main__':
    unittest.main()
