import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient

from video_parser.services import transcription, transcription_install as install
from video_parser.web.app import app


class TranscriptionInstallTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        root = Path(temp.name).resolve()
        managed = root / 'managed'
        self.marker = managed / 'cuda-verified'
        self.executable = managed / 'venv/Scripts/python.exe'
        self.models = root / 'models'
        self.models.mkdir()
        self.stage = root / 'stage'
        self.stage.mkdir()
        self.tool = root / 'whisper'
        self.tool.mkdir()
        (self.tool / 'cli.py').touch()
        self.entries = [{'path': name, 'size': len(name), 'sha256': hashlib.sha256(name.encode()).hexdigest()}
                        for name in sorted(install.MODEL_FILES)]
        (self.tool / 'model-manifest.json').write_text(json.dumps({'repo_id': 'example/model',
            'revision': 'test', 'files': self.entries}), encoding='utf-8')
        self.progress = {'state': 'idle', 'downloaded_bytes': 0, 'total_bytes': None, 'error': None}
        for owner, fields in ((install, {'ROOT': root, '_progress': self.progress}),
                              (transcription, {'ROOT': root, 'BUNDLED_ROOT': self.tool, 'TOOL_ROOT': self.tool,
                                               'MANAGED_PYTHON': self.executable, 'WHISPER_READY': self.marker,
                                               'TRANSCRIPTION_LOCK': root / 'transcription.lock'})):
            for key, value in fields.items():
                self.enterContext(patch.object(owner, key, value))
        self.enterContext(patch.object(transcription, 'model_directory', return_value=self.models))
        self.enterContext(patch.object(install, 'supported', return_value=True))

    def write_models(self):
        for name in install.MODEL_FILES:
            (self.models / name).write_bytes(name.encode())

    def test_reuses_valid_files_and_downloads_only_missing(self):
        self.write_models()
        missing = self.models / 'model.bin'
        missing.unlink()
        def download(url, path, size, sha, progress):
            self.assertTrue(url.endswith('/resolve/test/model.bin'))
            path.write_bytes(b'model.bin')
            progress(size)
        with patch.object(install, '_download', side_effect=download) as fetch:
            install._models(self.models, self.stage)
            install._models(self.models, self.stage)
        fetch.assert_called_once()
        self.assertEqual(missing.read_bytes(), b'model.bin')
        self.assertEqual(self.progress['downloaded_bytes'], len('model.bin'))

    def test_corrupt_file_is_not_overwritten(self):
        bad = self.models / 'model.bin'
        bad.write_bytes(b'bad')
        with patch.object(install, '_download') as fetch, self.assertRaisesRegex(install.InstallError, '校验失败'):
            install._models(self.models, self.stage)
        fetch.assert_not_called()
        self.assertEqual(bad.read_bytes(), b'bad')

    def test_failed_cuda_gate_and_retry_use_actual_readiness(self):
        self.write_models()
        self.executable.parent.mkdir(parents=True)
        self.executable.touch()
        (self.executable.parent.parent / 'pyvenv.cfg').write_text(f'home = {self.executable.parent}', encoding='utf-8')
        self.assertFalse(transcription.available())
        with patch.object(install, '_runtime', return_value=(self.executable, {})), \
                patch.object(install, '_probe', side_effect=[install.InstallError('CUDA failed'), None]):
            install._worker()
            self.assertEqual(install.status()['state'], 'failed')
            self.assertFalse(install.status()['ready'])
            self.assertFalse(self.marker.exists())
            install._worker()
        self.assertTrue(self.marker.is_file())
        self.assertTrue(install.status()['ready'])
        self.assertEqual(transcription.runtime_python(), self.executable)
        self.assertIsNone(install.status()['error'])

    def test_stale_windows_venv_does_not_report_ready(self):
        self.write_models()
        legacy = transcription.ROOT / 'runtimes/whisper'
        script = legacy / 'Scripts/python.exe'
        script.parent.mkdir(parents=True)
        script.touch()
        (legacy / 'pyvenv.cfg').write_text(f'home = {transcription.ROOT / "missing"}', encoding='utf-8')
        self.assertFalse(transcription.available())

    def test_local_route_requires_explicit_opt_in_and_deduplicates(self):
        path = '/api/workspace/transcriptions/install'
        client = TestClient(app, base_url='http://127.0.0.1:15001', client=('127.0.0.1', 12345))
        self.addCleanup(client.close)
        with patch.object(install, 'Thread') as worker:
            self.assertEqual(client.get(path).json()['state'], 'idle')
            for body in ({}, {'confirm': False}, {'confirm': 'true'}, {'confirm': 1}):
                self.assertIn(client.post(path, json=body).status_code, (400, 422))
            self.assertEqual(client.post(path, json={'confirm': True}, headers={'Origin': 'https://example.org'}).status_code, 403)
            worker.assert_not_called()
            for _ in range(2):
                self.assertEqual(client.post(path, json={'confirm': True}).json()['state'], 'preparing')
            worker.return_value.start.assert_called_once()
