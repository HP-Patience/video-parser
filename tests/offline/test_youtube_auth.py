import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient

from video_parser.services import youtube_auth
from video_parser.web.app import app

NETSCAPE = '\n'.join([
    '# Netscape HTTP Cookie File',
    '.youtube.com\tTRUE\t/\tTRUE\t1893456000\tSID\tabc123',
    '#HttpOnly_.youtube.com\tTRUE\t/\tTRUE\t1893456000\tHSID\thidden',
    '.evil.example\tTRUE\t/\tFALSE\t0\tTRACK\t1',
    'garbage line',
]) + '\n'

JSON_EXPORT = json.dumps([
    {'domain': '.youtube.com', 'path': '/', 'secure': True, 'expirationDate': 1893456000, 'name': 'SAPISID', 'value': 'v1'},
    {'domain': '.example.com', 'path': '/', 'secure': False, 'expirationDate': 0, 'name': 'OTHER', 'value': 'v2'},
])


class YoutubeAuthTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name).resolve()
        self.enterContext(patch.object(youtube_auth, 'ROOT', self.root))
        self.client = TestClient(app, base_url='http://127.0.0.1:15001', client=('127.0.0.1', 12345))

    def test_empty_status_and_no_cookie_args(self):
        self.assertEqual(youtube_auth.status(), {'configured': False, 'cookies': 0, 'browser': None,
                                                'updated_at': None, 'source': None})
        self.assertEqual(youtube_auth.cookie_args(), [])
        self.assertEqual(youtube_auth.cookie_options(), {})

    def test_netscape_import_keeps_only_supported_domains(self):
        status = youtube_auth.save_text(NETSCAPE)
        self.assertTrue(status['configured'])
        self.assertEqual(status['cookies'], 2)
        self.assertEqual(status['source'], 'file')
        saved = (self.root / '.local/credentials/youtube-cookies.txt').read_text(encoding='utf-8')
        self.assertIn('SID', saved)
        self.assertIn('HSID', saved)
        self.assertNotIn('TRACK', saved)
        self.assertTrue(saved.startswith('# Netscape HTTP Cookie File'))
        self.assertEqual(youtube_auth.cookie_args(),
                         ['--cookies', str(self.root / '.local/credentials/youtube-cookies.txt')])
        self.assertEqual(youtube_auth.cookie_options(),
                         {'cookiefile': str(self.root / '.local/credentials/youtube-cookies.txt')})

    def test_json_export_is_accepted(self):
        status = youtube_auth.save_text(JSON_EXPORT)
        self.assertEqual(status['cookies'], 1)

    def test_unrelated_cookies_are_rejected(self):
        with self.assertRaises(RuntimeError):
            youtube_auth.save_text('# Netscape HTTP Cookie File\n.example.com\tTRUE\t/\tFALSE\t0\tA\tb\n')
        with self.assertRaises(RuntimeError):
            youtube_auth.save_text('   ')

    def test_browser_preference_is_used_without_a_file(self):
        status = youtube_auth.choose_browser('chrome')
        self.assertEqual((status['source'], status['browser'], status['configured']), ('browser', 'chrome', True))
        self.assertEqual(youtube_auth.cookie_args(), ['--cookies-from-browser', 'chrome'])
        self.assertEqual(youtube_auth.cookie_options(), {'cookiesfrombrowser': ('chrome', None, None, None)})
        with self.assertRaises(RuntimeError):
            youtube_auth.choose_browser('netscape')

    def test_clear_removes_both_files(self):
        youtube_auth.save_text(NETSCAPE)
        youtube_auth.choose_browser('edge')
        self.assertFalse(youtube_auth.clear()['configured'])
        self.assertEqual(youtube_auth.cookie_args(), [])

    def test_status_endpoint_never_returns_cookie_values(self):
        response = self.client.post('/api/workspace/youtube-auth/cookies', json={'text': NETSCAPE})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['cookies'], 2)
        self.assertNotIn('abc123', response.text)
        self.assertEqual(self.client.get('/api/workspace/youtube-auth/status').json()['cookies'], 2)
        self.assertEqual(self.client.post('/api/workspace/youtube-auth/cookies',
                                          json={'text': '# nothing here'}).status_code, 400)
        self.assertEqual(self.client.post('/api/workspace/youtube-auth/browser',
                                          json={'browser': 'netscape'}).status_code, 400)
        self.assertFalse(self.client.post('/api/workspace/youtube-auth/clear', json={}).json()['configured'])

    def test_failure_messages_are_actionable(self):
        from video_parser.platforms.downloaders.youtube_downloader import describe_failure
        bot = describe_failure('ERROR: [youtube] x: Sign in to confirm you’re not a bot. Use --cookies-from-browser')
        self.assertIn('cookies', bot)
        self.assertIn('账号与登录', bot)
        self.assertIn('JavaScript', describe_failure('n challenge solving failed'))
        # A 403 from googlevideo means the exit IP is blocked, not that parsing failed.
        self.assertIn('节点', describe_failure('unable to download video data: HTTP Error 403: Forbidden'))
        # Once cookies exist the bot wall is an IP problem, so the hint must not loop back to cookies.
        with_cookies = describe_failure('ERROR: [youtube] x: Sign in to confirm you’re not a bot.', logged_in=True)
        self.assertIn('节点', with_cookies)
        self.assertNotIn('先导入', with_cookies)
        self.assertIn('11 位', describe_failure('ERROR: Unsupported URL: https://www.youtube.com/watch?v=short'))
        self.assertIn('代理', describe_failure("Unable to connect to proxy: Tunnel connection failed: 502"))
        self.assertIn('已不可用', describe_failure('ERROR: [youtube] x: Video unavailable'))
        self.assertIn('地区', describe_failure('The uploader has not made this video available in your country'))
        self.assertIn('检查网络', describe_failure('something unexpected happened'))

    def test_locked_browser_database_explains_how_to_recover(self):
        message = youtube_auth.import_failure('chrome', ['ERROR: Could not copy Chrome cookie database.'])
        self.assertIn('Chrome', message)
        self.assertIn('登录窗口', message)   # Points at the path that works despite v20 encryption.
        self.assertIn('cookies.txt', message)
        self.assertIn('加密', youtube_auth.import_failure('chrome', ['Failed to decrypt with DPAPI']))
        self.assertIn('已登录', youtube_auth.import_failure('firefox', []))

    def test_browser_profiles_are_detected_and_ordered(self):
        import os
        base = self.root / 'Google/Chrome/User Data'
        for name, stamp, cookie in (('Default', 300, False), ('Profile 10', 100, True), ('Profile 11', 200, True)):
            folder = base / name
            (folder / 'Network').mkdir(parents=True)
            if cookie:
                database = folder / 'Network/Cookies'
                database.write_bytes(b'x')
                os.utime(database, (stamp, stamp))
        with patch.dict(os.environ, {'LOCALAPPDATA': str(self.root)}):
            self.assertEqual(youtube_auth.browser_profiles('chrome'), ['Profile 11', 'Profile 10'])
            self.assertEqual(youtube_auth.browser_profiles('firefox'), [])  # Non-Chromium layout.


if __name__ == '__main__':
    unittest.main()
