"""Offline: per-platform saved logins (账号与登录). No browser, no network."""
import json
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import patch

from video_parser.services import accounts, bili_auth
from video_parser.web import account_routes


def cookie(name, value='v', domain='.douyin.com', expires=None):
    return {'name': name, 'value': value, 'domain': domain, 'path': '/',
            'expires': time.time() + 86400 if expires is None else expires, 'httpOnly': True, 'secure': True, 'sameSite': 'Lax'}


class AccountTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        patch.object(accounts, 'CREDENTIALS', self.root / 'credentials').start()
        patch.object(bili_auth, 'cookie_path', lambda: self.root / 'credentials/bilibili-cookies.json').start()
        from video_parser.services import youtube_auth
        patch.object(youtube_auth, 'ROOT', self.root).start()  # Never touch the real .local/credentials.
        self.addCleanup(patch.stopall)

    def test_missing_then_saved_without_exposing_values(self):
        self.assertEqual(accounts.public('douyin')['state'], 'missing')
        accounts.save('douyin', [cookie('sessionid', 'SECRET-VALUE'), cookie('other', 'x', domain='.example.com')])
        public = accounts.public('douyin')
        self.assertEqual(public['state'], 'saved')
        self.assertNotIn('SECRET-VALUE', json.dumps(accounts.overview(), ensure_ascii=False))
        saved = json.loads(accounts.cookie_path('douyin').read_text(encoding='utf-8'))
        self.assertEqual([item['name'] for item in saved], ['sessionid'])  # Foreign domains are dropped.

    def test_save_requires_login_cookie(self):
        with self.assertRaises(RuntimeError):
            accounts.save('xiaohongshu', [cookie('a1', domain='.xiaohongshu.com')])
        self.assertFalse(accounts.cookie_path('xiaohongshu').exists())

    def test_expired_cookies_are_ignored(self):
        accounts.save('xiaohongshu', [cookie('web_session', '1', domain='.xiaohongshu.com'), cookie('old', 'x', domain='.xiaohongshu.com', expires=time.time() - 10)])
        self.assertEqual(accounts.cookie_header('xiaohongshu'), 'web_session=1')
        data = [cookie('web_session', '1', domain='.xiaohongshu.com', expires=time.time() - 10)]
        accounts.cookie_path('xiaohongshu').write_text(json.dumps(data), encoding='utf-8')
        self.assertEqual(accounts.public('xiaohongshu')['state'], 'expired')
        self.assertEqual(accounts.cookie_header('xiaohongshu'), '')

    def test_bilibili_shares_the_qr_login_file(self):
        self.assertEqual(accounts.cookie_path('bilibili'), bili_auth.cookie_path())

    def test_playwright_cookie_shape(self):
        accounts.save('douyin', [cookie('sessionid', 'abc')])
        item = accounts.playwright_cookies('douyin')[0]
        self.assertEqual(set(item) - {'expires', 'httpOnly', 'secure', 'sameSite'}, {'name', 'value', 'domain', 'path'})

    def test_unknown_platform_is_404(self):
        from fastapi import HTTPException
        for removed in ('weibo', 'kuaishou'):
            with self.assertRaises(HTTPException) as caught:
                account_routes.account_detail(removed)
            self.assertEqual(caught.exception.status_code, 404)

    def test_second_login_window_is_refused(self):
        with patch.object(accounts, '_run', lambda session: time.sleep(0.3)):
            accounts.start_login('douyin')
            from fastapi import HTTPException
            with self.assertRaises(HTTPException) as caught:
                account_routes.account_qr('xiaohongshu')
            self.assertEqual(caught.exception.status_code, 409)
        accounts._session.thread.join()
        accounts._session.state = 'cancelled'

    def test_qr_modes(self):
        self.assertEqual({item['id']: item['qr_mode'] for item in accounts.overview()['platforms']},
                         {'bilibili': 'bili', 'douyin': 'browser', 'xiaohongshu': 'browser', 'youtube': None})
        with self.assertRaises(RuntimeError):
            accounts.start_qr('bilibili')  # B站 keeps its own API-based QR login.

    def test_youtube_shares_the_yt_dlp_cookie_file(self):
        from video_parser.services import youtube_auth
        rows = '# Netscape HTTP Cookie File\n.youtube.com\tTRUE\t/\tTRUE\t1893456000\tSID\tabc\n'
        self.assertEqual(accounts.public('youtube')['state'], 'missing')
        self.assertTrue(accounts.public('youtube')['import_cookies'])
        with self.assertRaises(RuntimeError):
            accounts.start_qr('youtube')  # No QR login for YouTube.
        youtube_auth.save_text(rows)
        public = accounts.public('youtube')
        self.assertEqual(public['state'], 'saved')
        self.assertNotIn('abc', json.dumps(public, ensure_ascii=False))
        self.assertIn('以实际解析结果为准', accounts.check('youtube')['message'])
        self.assertEqual(accounts.logout('youtube')['state'], 'missing')

    def test_youtube_companion_cookies_alone_do_not_mean_signed_in(self):
        from video_parser.services import youtube_auth
        # Anonymous visitors also receive HSID/SSID/SAPISID, so they cannot stand in for a session.
        youtube_auth.save_text('# Netscape HTTP Cookie File\n'
                               '.youtube.com\tTRUE\t/\tTRUE\t1893456000\tHSID\tabc\n'
                               '.youtube.com\tTRUE\t/\tTRUE\t1893456000\tSAPISID\tabc\n')
        public = accounts.public('youtube')
        self.assertEqual(public['state'], 'saved')
        self.assertFalse(public['complete'])
        self.assertIn('不完整', public['message'])
        self.assertIn('不完整', accounts.check('youtube')['message'])

    def test_youtube_window_login_saves_a_netscape_file(self):
        saved = accounts.save('youtube', [
            {'name': 'SID', 'value': 'v1', 'domain': '.youtube.com', 'path': '/', 'expires': 1893456000, 'secure': True},
            {'name': 'SAPISID', 'value': 'v2', 'domain': '.google.com', 'path': '/', 'expires': 0},
            {'name': 'OTHER', 'value': 'v3', 'domain': '.example.com', 'path': '/', 'expires': 0}])
        self.assertEqual(saved, 2)  # Foreign domains never reach the file.
        text = (self.root / '.local/credentials/youtube-cookies.txt').read_text(encoding='utf-8')
        self.assertIn('SID', text)
        self.assertIn('SAPISID', text)
        self.assertNotIn('OTHER', text)
        self.assertEqual(accounts.public('youtube')['state'], 'saved')
        with self.assertRaises(RuntimeError):
            accounts.save('youtube', [{'name': 'OTHER', 'value': 'v', 'domain': '.youtube.com', 'path': '/'}])

    def test_qr_login_detection_needs_a_new_session_cookie(self):
        guest = [cookie('web_session', 'guest', domain='.xiaohongshu.com')]
        self.assertFalse(accounts.logged_in('xiaohongshu', guest, None))  # QR not shown yet.
        self.assertFalse(accounts.logged_in('xiaohongshu', guest, 'guest'))
        self.assertTrue(accounts.logged_in('xiaohongshu', [cookie('web_session', 'user', domain='.xiaohongshu.com')], 'guest'))
        self.assertFalse(accounts.logged_in('douyin', [], ''))
        self.assertTrue(accounts.logged_in('douyin', [cookie('sessionid', 's')], ''))
        self.assertFalse(accounts.logged_in('douyin', [cookie('sessionid', 's', domain='.example.com')], ''))

    def test_qr_phase_and_user_agent(self):
        self.assertEqual(accounts.qr_phase('二维码已失效 点击刷新'), 'expired')
        self.assertEqual(accounts.qr_phase('扫码成功，请在手机上确认'), 'scanned')
        self.assertEqual(accounts.qr_phase('打开抖音APP 扫一扫'), 'waiting')
        agent = accounts.user_agent('141.0.3537.71', True)
        self.assertIn('Chrome/141.0.0.0', agent)
        self.assertTrue(agent.endswith('Edg/141.0.3537.71'))
        self.assertNotIn('Headless', agent)

    def test_qr_session_exposes_image_only_while_waiting(self):
        with patch.object(accounts, '_run_qr', lambda session: time.sleep(0.3)):
            accounts.start_qr('douyin')
            session = accounts._session
            session.qr, session.state = 'data:image/png;base64,AAAA', 'waiting'
            self.assertEqual(account_routes.account_detail('douyin')['login']['qr'], 'data:image/png;base64,AAAA')
            session.state = 'scanned'
            self.assertIsNone(account_routes.account_detail('douyin')['login']['qr'])
            self.assertEqual(accounts.start_qr('douyin')['id'], session.id)  # Same platform: reuse.
            from fastapi import HTTPException
            with self.assertRaises(HTTPException) as caught:
                account_routes.account_login('douyin')  # A window login while scanning is refused.
            self.assertEqual(caught.exception.status_code, 409)
        session.thread.join()
        session.state = 'cancelled'

    def test_logout_deletes_file(self):
        accounts.save('xiaohongshu', [cookie('web_session', 'w', domain='.xiaohongshu.com')])
        self.assertEqual(accounts.logout('xiaohongshu')['state'], 'missing')
        self.assertFalse(accounts.cookie_path('xiaohongshu').exists())


if __name__ == '__main__':
    unittest.main()
