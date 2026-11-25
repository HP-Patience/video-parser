"""Offline checks for the only supported HTTP entrypoint."""
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient

from video_parser.web import app as api
class APIEntrypointTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.frontend = Path(temporary.name) / 'frontend'
        self.frontend.mkdir()
        patcher = patch.object(api, 'FRONTEND_DIR', self.frontend)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.client = TestClient(api.app, base_url='http://127.0.0.1:15001',
                                 client=('127.0.0.1', 12345))

    def test_health_remains_available(self):
        response = self.client.get('/health')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['status'], 'healthy')
        self.assertIsInstance(response.json()['timestamp'], int)

    def test_workspace_routes_are_registered(self):
        paths = set(api.app.openapi()['paths'])
        self.assertIn('/api/workspace/config', paths)
        self.assertIn('/api/workspace/parse', paths)

    def test_signed_legacy_routes_are_removed(self):
        for endpoint in ('/api/parse', '/api/download', '/api/login', '/api/ranking'):
            with self.subTest(endpoint=endpoint):
                self.assertEqual(self.client.post(endpoint, json={'text': 'invalid'}).status_code, 404)

    def test_root_reports_missing_frontend_without_starting_old_ui(self):
        response = self.client.get('/')
        self.assertEqual(response.status_code, 200)
        self.assertIn('frontend not built', response.json()['message'])

    def test_root_serves_built_frontend_with_no_store(self):
        (self.frontend / 'index.html').write_text('<!doctype html><title>workspace</title>', encoding='utf-8')
        response = self.client.get('/')
        self.assertEqual(response.status_code, 200)
        self.assertIn('<title>workspace</title>', response.text)
        self.assertEqual(response.headers['cache-control'], 'no-store')

    def test_workspace_keeps_cross_origin_rejection(self):
        response = self.client.get('/api/workspace/config', headers={'Origin': 'https://external.invalid'})
        self.assertEqual(response.status_code, 403)
        self.assertNotIn('access-control-allow-origin', response.headers)


if __name__ == '__main__':
    unittest.main()


class OpenBrowserTests(unittest.TestCase):
    """The launcher's browser hand-off must stay opt-in and never open a window during tests."""

    def test_disabled_without_the_launcher_flag(self):
        from video_parser.web import app as api
        with patch.object(api.webbrowser, 'open') as opened:
            self.assertFalse(api.maybe_open_browser({'VIDEO_PARSER_OPEN_URL': 'http://127.0.0.1:15001'}))
            opened.assert_not_called()

    def test_opens_the_requested_address(self):
        import time
        from video_parser.web import app as api
        with patch.object(api, 'open_in_default_browser') as opened:
            self.assertTrue(api.maybe_open_browser({'VIDEO_PARSER_OPEN_BROWSER': '1',
                                                    'VIDEO_PARSER_OPEN_URL': 'http://127.0.0.1:15123',
                                                    'VIDEO_PARSER_OPEN_DELAY': '0'}))
            for _ in range(50):
                if opened.called:
                    break
                time.sleep(0.02)
        opened.assert_called_once_with('http://127.0.0.1:15123')

    def test_bad_delay_falls_back_instead_of_raising(self):
        from video_parser.web import app as api
        with patch.object(api.webbrowser, 'open') as opened:
            self.assertTrue(api.maybe_open_browser({'VIDEO_PARSER_OPEN_BROWSER': '1',
                                                    'VIDEO_PARSER_OPEN_DELAY': 'soon'}))
            opened.assert_not_called()  # Still sleeping when the check runs; no crash is what matters.

    def test_windows_uses_cmd_start_without_extra_quotes(self):
        """An extra pair of quotes reaches `start` verbatim and silently drops the address."""
        from video_parser.web import app as api
        with patch.object(api.subprocess, 'Popen') as popen, patch.object(api, 'os') as fake_os:
            fake_os.name = 'nt'
            self.assertTrue(api.open_in_default_browser('http://127.0.0.1:15001'))
        command = popen.call_args.args[0]
        self.assertEqual(command, ['cmd', '/c', 'start', '', 'http://127.0.0.1:15001'])
