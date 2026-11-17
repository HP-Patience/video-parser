from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import zipfile

from fastapi.testclient import TestClient

from video_parser.web import app as api
from video_parser.web import routes
from video_parser.services import update


def release(tag='v3.0.2', *, with_assets=True):
    assets = [
        {'name': 'VideoParser-3.0.2-win64-setup.exe', 'size': 12,
         'browser_download_url': 'https://github.com/HP-Patience/video-parser/releases/download/v3.0.2/VideoParser-3.0.2-win64-setup.exe'},
        {'name': 'SHA256SUMS.txt', 'size': 100,
         'browser_download_url': 'https://github.com/HP-Patience/video-parser/releases/download/v3.0.2/SHA256SUMS.txt'},
    ]
    return {'tag_name': tag, 'html_url': 'https://github.com/HP-Patience/video-parser/releases/tag/v3.0.2',
            'published_at': '2026-11-06T00:00:00Z', 'assets': assets if with_assets else []}


class UpdateTests(unittest.TestCase):
    def setUp(self):
        self.client = TestClient(api.app, base_url='http://127.0.0.1:15001', client=('127.0.0.1', 12345))
        self.addCleanup(self.client.close)

    def test_version_comparison_is_numeric(self):
        self.assertEqual(update.version_tuple('v3.0.10'), (3, 0, 10))
        self.assertGreater(update.version_tuple('3.0.10'), update.version_tuple('3.0.9'))
        self.assertIsNone(update.version_tuple('3.0'))

    def test_release_result_requires_installer_and_installed_copy(self):
        with patch.object(update, 'installation_kind', return_value='installed'):
            result = update._release_result('3.0.1', release())
        self.assertTrue(result['available'])
        self.assertTrue(result['can_install'])
        self.assertEqual(result['install_reason'], 'installed')
        with patch.object(update, 'installation_kind', return_value='installed'):
            result = update._release_result('3.0.1', release(with_assets=False))
        self.assertTrue(result['available'])
        self.assertFalse(result['can_install'])
        self.assertEqual(result['install_reason'], 'assets_missing')

    def test_checksum_requires_exact_asset_name(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'SHA256SUMS.txt'
            path.write_text('a' * 64 + '  VideoParser-3.0.2-win64-setup.exe\r\n' + 'b' * 64 + '  other.exe\r\n', encoding='utf-8')
            self.assertEqual(update._expected_checksum(path, 'VideoParser-3.0.2-win64-setup.exe'), 'a' * 64)
            with self.assertRaises(update.UpdateError):
                update._expected_checksum(path, 'missing.exe')

    def test_helper_waits_for_parent_and_restarts_installed_app(self):
        with tempfile.TemporaryDirectory() as directory, patch.object(update, 'UPDATE_DIR', Path(directory)):
            helper = update._write_helper(Path(directory) / 'setup.exe', Path('C:/VideoParser'), Path(directory) / 'update.log')
            text = helper.read_text(encoding='utf-8')
        self.assertIn('$ParentId', text)
        self.assertIn("'/VERYSILENT'", text)
        self.assertIn("'video_parser.desktop'", text)

    def _portable_fixture(self, directory, version='3.5.0'):
        root = Path(directory) / 'VideoParser'
        (root / 'python').mkdir(parents=True)
        (root / 'backend' / 'video_parser').mkdir(parents=True)
        (root / 'runtimes' / 'whisper').mkdir(parents=True)
        (root / '.local').mkdir()
        (root / 'outputs').mkdir()
        (root / '.env').write_text('keep', encoding='utf-8')
        (root / 'runtimes' / 'whisper' / 'keep.txt').write_text('keep', encoding='utf-8')
        (root / 'python' / 'python.exe').write_bytes(b'old-python')
        (root / 'backend' / 'video_parser' / 'old.py').write_text('old', encoding='utf-8')
        (root / 'pyproject.toml').write_text(f'[project]\nversion = "{version}"\n', encoding='utf-8')
        return root

    def _portable_zip(self, directory, version='3.5.1'):
        archive = Path(directory) / 'update.zip'
        with zipfile.ZipFile(archive, 'w') as zipped:
            zipped.writestr('python/python.exe', b'new-python')
            zipped.writestr('backend/video_parser/new.py', 'new', compress_type=zipfile.ZIP_DEFLATED)
            zipped.writestr('pyproject.toml', f'[project]\nversion = "{version}"\n')
            zipped.writestr('runtimes/ffmpeg/bin/ffmpeg.exe', b'ffmpeg')
        return archive

    def test_portable_transaction_preserves_user_data_and_other_runtimes(self):
        with tempfile.TemporaryDirectory() as directory, patch.object(update, 'UPDATE_DIR', Path(directory) / 'work'):
            root = self._portable_fixture(directory)
            archive = self._portable_zip(directory)
            with patch.object(update, '_healthcheck'):
                update._apply_portable(archive, root, '3.5.1')
            self.assertEqual((root / 'python' / 'python.exe').read_bytes(), b'new-python')
            self.assertEqual((root / 'backend' / 'video_parser' / 'new.py').read_text(encoding='utf-8'), 'new')
            self.assertEqual((root / '.env').read_text(encoding='utf-8'), 'keep')
            self.assertEqual((root / 'runtimes' / 'whisper' / 'keep.txt').read_text(encoding='utf-8'), 'keep')

    def test_portable_transaction_rolls_back_on_healthcheck_failure(self):
        with tempfile.TemporaryDirectory() as directory, patch.object(update, 'UPDATE_DIR', Path(directory) / 'work'):
            root = self._portable_fixture(directory)
            archive = self._portable_zip(directory, '3.5.2')
            with self.assertRaises(update.UpdateError):
                update._apply_portable(archive, root, '3.5.1')
            self.assertEqual((root / 'python' / 'python.exe').read_bytes(), b'old-python')
            self.assertFalse((root / 'backend' / 'video_parser' / 'new.py').exists())
            self.assertEqual((root / 'runtimes' / 'whisper' / 'keep.txt').read_text(encoding='utf-8'), 'keep')

    def test_update_routes_are_manual_and_return_release_status(self):
        status = update._release_result('3.0.1', release())
        with patch.object(routes.update, 'check', return_value=status):
            response = self.client.get('/api/workspace/updates/check')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.headers.get('cache-control'), 'no-store')
        self.assertTrue(response.json()['available'])
        with patch.object(routes.update, 'install', return_value={'started': True, 'version': '3.0.2', 'installer': 'setup.exe'}):
            response = self.client.post('/api/workspace/updates/install', json={'confirm': True})
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json()['started'])
        self.assertEqual(self.client.post('/api/workspace/updates/install', json={'confirm': False}).status_code, 422)

    def test_install_maps_unsupported_copy_to_conflict(self):
        with patch.object(routes.update, 'install', side_effect=update.UpdateNotSupported('not installed')):
            response = self.client.post('/api/workspace/updates/install', json={'confirm': True})
        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.json()['detail'], 'not installed')


if __name__ == '__main__':
    unittest.main()
