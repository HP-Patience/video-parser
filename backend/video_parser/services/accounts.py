"""Per-platform saved logins (browser cookies) kept only on this machine.

Cookie values never leave this module through the API: callers get presence,
expiry and a short status message only. Downloaders read the cookies locally.
"""
import base64
import json
import os
from pathlib import Path
import threading
import time
from uuid import uuid4

from video_parser.core.paths import ROOT

CREDENTIALS = ROOT / '.local/credentials'
LOGIN_TIMEOUT = 15 * 60
QR_TIMEOUT = 5 * 60

PLATFORMS = {
    'bilibili': {
        'name': '哔哩哔哩', 'login_url': 'https://passport.bilibili.com/login',
        'domains': ('bilibili.com',), 'keys': ('SESSDATA',), 'qr': 'bili',
        'benefit': '登录后可下载 1080p 及以上画质，并可获取平台字幕；未登录最高约 480p。',
    },
    'douyin': {
        'name': '抖音', 'login_url': 'https://www.douyin.com/',
        'domains': ('douyin.com',), 'keys': ('sessionid', 'sessionid_ss'),
        # Headless QR: the homepage opens its login dialog by itself; the QR is a data: PNG.
        'qr': 'browser', 'qr_url': 'https://www.douyin.com/', 'qr_selector': '#animate_qrcode_container img',
        'qr_app': '抖音 App', 'session_cookie': 'sessionid',
        'benefit': '减少验证码和风控；部分视频需要登录才能打开。',
    },
    'xiaohongshu': {
        'name': '小红书', 'login_url': 'https://www.xiaohongshu.com/explore',
        'domains': ('xiaohongshu.com',), 'keys': ('web_session',), 'manual': True,
        # Guests also get a web_session, so a login is detected by that cookie changing value.
        'qr': 'browser', 'qr_url': 'https://www.xiaohongshu.com/explore', 'qr_selector': 'img.qrcode-img',
        'qr_app': '小红书 App 或微信', 'session_cookie': 'web_session',
        'benefit': '小红书视频基本需要登录后才能解析。',
    },
    'youtube': {
        # Sign-in happens in the app's own browser window backed by a persistent profile, so the
        # session lives in the browser instead of an export (Chrome 127+ makes SID undecryptable
        # outside its own profile). An exported cookies.txt is still accepted for parsing.
        'name': 'YouTube', 'login_url': 'https://www.youtube.com/',
        # SID / __Secure-1PSID / __Secure-3PSID are the session cookies; HSID/SAPISID alone mean nothing.
        'domains': ('youtube.com', 'google.com'),
        'keys': ('SID', '__Secure-1PSID', '__Secure-3PSID'),
        'import_cookies': True,
        'benefit': 'YouTube 会对陌生 IP 要求“确认你不是机器人”；登录一次或导入 cookies 后，解析与下载会复用该会话。',
    },
}

FIELDS = ('name', 'value', 'domain', 'path', 'expires', 'httpOnly', 'secure', 'sameSite')


def spec(platform):
    if platform not in PLATFORMS:
        raise KeyError(platform)
    return PLATFORMS[platform]


def cookie_path(platform):
    spec(platform)
    if platform == 'bilibili':
        from video_parser.services import bili_auth
        return bili_auth.cookie_path()
    # The *-cookies.json name also matches the repository's *cookie*.json ignore rule.
    return CREDENTIALS / f'{platform}-cookies.json'


def matches(domain, domains):
    domain = str(domain or '').lstrip('.').lower()
    return any(domain == item or domain.endswith('.' + item) for item in domains)


def load(platform):
    """Saved, unexpired cookies for local use only (never return these through the API)."""
    path = cookie_path(platform)
    try:
        if not path.is_file() or path.is_symlink() or path.stat().st_size > 2_000_000:
            return []
        data = json.loads(path.read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return []
    if not isinstance(data, list):
        return []
    now = time.time()
    domains = spec(platform)['domains']
    result = []
    for item in data:
        if not isinstance(item, dict) or not isinstance(item.get('name'), str) or not isinstance(item.get('value'), str):
            continue
        if not matches(item.get('domain'), domains):
            continue
        expires = item.get('expires', -1)
        if isinstance(expires, (int, float)) and 0 < expires < now:
            continue
        result.append(item)
    return result


def cookie_header(platform):
    try:
        return '; '.join(f"{item['name']}={item['value']}" for item in load(platform) if item['value'])
    except KeyError:
        return ''


def playwright_cookies(platform):
    cookies = []
    for item in load(platform):
        cookie = {'name': item['name'], 'value': item['value'], 'domain': item['domain'], 'path': item.get('path') or '/'}
        if isinstance(item.get('expires'), (int, float)) and item['expires'] > 0:
            cookie['expires'] = float(item['expires'])
        for flag in ('httpOnly', 'secure'):
            if isinstance(item.get(flag), bool):
                cookie[flag] = item[flag]
        if item.get('sameSite') in ('Strict', 'Lax', 'None'):
            cookie['sameSite'] = item['sameSite']
        cookies.append(cookie)
    return cookies


def atomic_write(path, text):
    path = Path(path)
    if path.is_symlink():
        raise RuntimeError('登录态文件路径不安全，未保存。')
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name('.' + uuid4().hex + '-cookies.tmp')
    try:
        fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, 'w', encoding='utf-8', newline='\n') as stream:
            stream.write(text)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    except OSError:
        raise RuntimeError('无法安全保存本机登录态，请检查工具目录写入权限。') from None
    finally:
        try:
            temporary.unlink(missing_ok=True)
        except OSError:
            pass


def has_key(platform, cookies):
    keys = spec(platform)['keys']
    return any(item.get('name') in keys and item.get('value') for item in cookies)


def save(platform, cookies):
    domains = spec(platform)['domains']
    kept = [{key: item.get(key) for key in FIELDS if key in item}
            for item in cookies if isinstance(item, dict) and matches(item.get('domain'), domains)]
    if not has_key(platform, kept):
        raise RuntimeError('没有检测到登录成功的凭据，请在弹出的窗口里完成登录后再保存。')
    if platform == 'youtube':
        # yt-dlp reads a Netscape file, not this module's JSON shape.
        from video_parser.services import youtube_auth
        youtube_auth.save_cookies(kept)
        return len(kept)
    atomic_write(cookie_path(platform), json.dumps(kept, ensure_ascii=False))
    return len(kept)


def local_status(platform):
    if platform == 'youtube':
        # The yt-dlp cookie file is the only source; it is a Netscape file, not this module's JSON.
        from video_parser.services import youtube_auth
        state = youtube_auth.status()
        configured = bool(state.get('configured'))
        complete = bool(state.get('complete'))
        # Exporting only the youtube.com domain leaves out the Google identity cookies, so parsing
        # works while a browser still shows the signed-out wall. Say that instead of "signed in".
        message = None
        if configured and not complete:
            message = ('登录态不完整：缺少 SID / __Secure-1PSID 等 Google 身份 cookie，'
                       '解析可能可用，但浏览器里仍是未登录。导出扩展通常读不到这几项，'
                       '需要在应用内用「登录窗口」登录一次。')
        return {'state': 'saved' if configured else 'missing',
                'saved_at': state.get('updated_at'), 'expires_at': None,
                'complete': complete, 'message': message}
    path = cookie_path(platform)
    cookies = load(platform)
    keys = spec(platform)['keys']
    present = [item for item in cookies if item['name'] in keys and item['value']]
    saved_at = None
    try:
        if path.is_file():
            saved_at = int(path.stat().st_mtime)
    except OSError:
        pass
    if present:
        expiries = [item['expires'] for item in present if isinstance(item.get('expires'), (int, float)) and item['expires'] > 0]
        return {'state': 'saved', 'saved_at': saved_at, 'expires_at': int(min(expiries)) if expiries else None}
    if saved_at:
        return {'state': 'expired', 'saved_at': saved_at, 'expires_at': None}
    return {'state': 'missing', 'saved_at': None, 'expires_at': None}


def public(platform):
    item = spec(platform)
    session = current_session(platform)
    return {'id': platform, 'name': item['name'], 'benefit': item['benefit'],
            'qr_login': bool(item.get('qr')), 'qr_mode': item.get('qr'), 'manual_finish': bool(item.get('manual')),
            'import_cookies': bool(item.get('import_cookies')),
            **local_status(platform), 'login': session}


def overview():
    return {'platforms': [public(platform) for platform in PLATFORMS], 'storage': '.local/credentials（仅保存在本机，不会显示 Cookie 内容）'}


def check(platform):
    result = public(platform)
    if platform == 'youtube':
        result['verified'] = None
        if result['state'] != 'saved':
            result['message'] = '尚未导入 YouTube 登录态。'
        elif not result.get('complete'):
            # Do not claim success here: HSID/SSID alone look like a session but sign nothing in.
            result['message'] = ('登录态不完整：缺少 SID / __Secure-1PSID 等身份 cookie，'
                                 '浏览器里仍是未登录。请在「登录窗口」里手动登录一次。')
        else:
            result['message'] = '已导入浏览器登录态；是否仍被 YouTube 认可，以实际解析结果为准。'
        return result
    if platform == 'bilibili' and result['state'] == 'saved':
        from video_parser.services import bili_auth
        try:
            online = bili_auth.status()
        except (RuntimeError, OSError):
            result['verified'] = None
            result['message'] = '无法连接 B站验证登录状态，本机凭据仍保留。'
            return result
        result['verified'] = bool(online.get('authenticated'))
        if result['verified']:
            result['message'] = 'B站登录有效。'
        else:
            result['message'] = 'B站已不认可这份登录态，请重新登录。'
        return result
    result['verified'] = None
    messages = {'saved': '本机凭据存在且未过期（平台是否仍认可要以实际解析为准）。',
                'expired': '本机凭据已过期，请重新登录。', 'missing': '尚未登录。'}
    result['message'] = messages[result['state']]
    return result


def logout(platform):
    spec(platform)
    if platform == 'youtube':
        from video_parser.services import youtube_auth
        youtube_auth.clear()
        return public(platform)
    # Older versions also wrote a Netscape copy for yt-dlp; remove it with the JSON file.
    for path in (cookie_path(platform), CREDENTIALS / f'{platform}-cookies.txt'):
        try:
            if path.is_file() and not path.is_symlink():
                path.unlink()
        except OSError:
            raise RuntimeError('无法删除本机登录态文件，请检查文件是否被占用。') from None
    return public(platform)


# ---- Browser login window -------------------------------------------------

class LoginSession:
    def __init__(self, platform, mode='window'):
        self.id = uuid4().hex
        self.platform = platform
        self.mode = mode
        self.qr = None
        self.state = 'starting'
        self.message = '正在准备二维码…' if mode == 'qr' else '正在打开登录窗口…'
        self.started = time.time()
        self.finish = threading.Event()
        self.cancel = threading.Event()
        self.thread = None

    def public(self):
        result = {'id': self.id, 'mode': self.mode, 'state': self.state, 'message': self.message, 'started_at': int(self.started)}
        if self.mode == 'qr':
            result['qr'] = self.qr if self.state == 'waiting' else None
        return result


_lock = threading.Lock()
_session = None
ACTIVE = {'starting', 'waiting', 'scanned', 'saving'}


def current_session(platform):
    with _lock:
        if _session is not None and _session.platform == platform:
            return _session.public()
    return None


def start_login(platform, mode='window'):
    global _session
    item = spec(platform)
    if mode == 'qr' and item.get('qr') != 'browser':
        raise RuntimeError('该平台不支持这种扫码登录。')
    with _lock:
        if _session is not None and _session.state in ACTIVE:
            if _session.platform == platform and _session.mode == mode:
                return _session.public()
            what = '扫码登录' if _session.mode == 'qr' else '登录窗口'
            raise BlockingIOError(f'{PLATFORMS[_session.platform]["name"]} 的{what}还在进行，请先完成或取消它。')
        session = LoginSession(platform, mode)
        _session = session
    target = _run_qr if mode == 'qr' else _run
    session.thread = threading.Thread(target=target, args=(session,), name=f'login-{mode}-{platform}', daemon=True)
    session.thread.start()
    return session.public()


def start_qr(platform):
    return start_login(platform, 'qr')


def finish_login(platform):
    with _lock:
        session = _session if _session is not None and _session.platform == platform else None
    if session is None or session.state not in ACTIVE:
        raise KeyError(platform)
    session.finish.set()
    if session.thread is not None:
        session.thread.join(timeout=20)
    return session.public()


def cancel_login(platform):
    with _lock:
        session = _session if _session is not None and _session.platform == platform else None
    if session is None:
        raise KeyError(platform)
    session.cancel.set()
    if session.thread is not None:
        session.thread.join(timeout=20)
    return session.public()


BROWSER_ARGS = ['--disable-blink-features=AutomationControlled', '--no-first-run', '--no-default-browser-check']


def _launch(playwright, headless=False):
    """Returns (browser, is_edge)."""
    # A real Edge install looks least like automation to login pages; fall back to the bundled Chromium.
    try:
        return playwright.chromium.launch(channel='msedge', headless=headless, args=BROWSER_ARGS, timeout=20000), True
    except Exception:
        return playwright.chromium.launch(headless=headless, args=BROWSER_ARGS, timeout=20000), False


def user_agent(version, edge):
    """Headless browsers announce themselves as Headless*; present the same browser as a normal desktop one."""
    version = str(version or '140.0.0.0')
    major = version.split('.')[0] if version.split('.')[0].isdigit() else '140'
    agent = f'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/{major}.0.0.0 Safari/537.36'
    return agent + (f' Edg/{version}' if edge else '')


def _import_playwright(session):
    try:
        if (ROOT / 'runtimes/browsers').is_dir():
            os.environ.setdefault('PLAYWRIGHT_BROWSERS_PATH', str(ROOT / 'runtimes/browsers'))
        from playwright.sync_api import sync_playwright
        return sync_playwright
    except ImportError:
        session.state, session.message = 'failed', '缺少 Playwright，无法登录。'
        return None


def _run(session):
    platform = session.platform
    item = PLATFORMS[platform]
    if platform == 'youtube':
        # YouTube needs a persistent profile; the shared flow below always starts from scratch.
        _run_youtube(session, item)
        return
    browser = None
    snapshot = []
    sync_playwright = _import_playwright(session)
    if sync_playwright is None:
        return
    try:
        with sync_playwright() as playwright:
            browser, _ = _launch(playwright)
            context = browser.new_context(locale='zh-CN', no_viewport=True)
            context.add_init_script("Object.defineProperty(navigator,'webdriver',{get:()=>undefined})")
            page = context.new_page()
            try:
                page.goto(item['login_url'], wait_until='domcontentloaded', timeout=45000)
            except Exception:
                pass  # Slow pages still let the user log in; the window stays open.
            session.state = 'waiting'
            session.message = ('请在弹出的窗口中完成登录，然后点“完成登录”。' if item.get('manual')
                               else '请在弹出的窗口中完成登录；检测到登录后会自动保存并关闭窗口。')
            detected_at = None
            while True:
                if session.cancel.is_set():
                    session.state, session.message = 'cancelled', '已取消登录，未保存任何内容。'
                    return
                if time.time() - session.started > LOGIN_TIMEOUT:
                    session.state, session.message = 'failed', '登录超时（15 分钟），窗口已关闭；可以重新开始。'
                    return
                try:
                    if not browser.is_connected() or not context.pages:
                        break  # User closed the window: save whatever the last snapshot holds.
                    snapshot = context.cookies()
                except Exception:
                    break
                if session.finish.is_set():
                    break
                if not item.get('manual') and has_key(platform, snapshot):
                    # Let the site finish writing its remaining cookies after the redirect.
                    detected_at = detected_at or time.time()
                    if time.time() - detected_at > 3:
                        break
                try:
                    context.pages[0].wait_for_timeout(1000)
                except Exception:
                    time.sleep(1)
            session.state, session.message = 'saving', '正在保存登录状态…'
            if has_key(platform, snapshot):
                save(platform, snapshot)
                session.state, session.message = 'saved', '登录成功，已保存到本机。'
            else:
                session.state, session.message = 'failed', '没有检测到登录成功，未保存。请重新登录。'
    except RuntimeError as error:
        session.state, session.message = 'failed', str(error)
    except Exception:
        session.state, session.message = 'failed', '无法打开登录窗口，请确认本机已安装 Edge 或项目自带浏览器。'
    finally:
        if browser is not None:
            try:
                browser.close()
            except Exception:
                pass


def _run_youtube(session, item):
    """Sign in once inside a persistent profile.

    Chrome 127+ encrypts session cookies against the install and profile path, so an exported file
    can never carry SID. Keeping the profile means the browser owns the session and every later
    launch is already signed in — no export, no re-login.
    """
    from video_parser.services import youtube_auth
    sync_playwright = _import_playwright(session)
    if sync_playwright is None:
        return
    directory = youtube_auth.browser_profile()
    try:
        directory.mkdir(parents=True, exist_ok=True)
    except OSError:
        session.state, session.message = 'failed', '无法创建浏览器配置目录，请检查磁盘权限。'
        return
    context = None
    cookies = []
    try:
        with sync_playwright() as playwright:
            options = dict(user_data_dir=str(directory), locale='zh-CN', no_viewport=True,
                           args=BROWSER_ARGS, timeout=20000)
            try:
                context = playwright.chromium.launch_persistent_context(channel='msedge', headless=False, **options)
            except Exception:
                context = playwright.chromium.launch_persistent_context(headless=False, **options)
            context.add_init_script("Object.defineProperty(navigator,'webdriver',{get:()=>undefined})")
            page = context.pages[0] if context.pages else context.new_page()
            try:
                page.goto(item['login_url'], wait_until='domcontentloaded', timeout=45000)
            except Exception:
                pass  # A slow page still lets the user sign in; the window stays open.
            session.state = 'waiting'
            session.message = '请在弹出的窗口中登录 YouTube；检测到登录后会自动保存并关闭窗口。'
            detected_at = None
            nudged = False
            while True:
                if session.cancel.is_set():
                    session.state, session.message = 'cancelled', '已取消登录，未保存任何内容。'
                    return
                if time.time() - session.started > LOGIN_TIMEOUT:
                    session.state, session.message = 'failed', '登录超时（15 分钟），窗口已关闭；可以重新开始。'
                    return
                try:
                    if not context.pages:
                        break  # User closed the window: keep whatever the profile already holds.
                    cookies = context.cookies()
                except Exception:
                    break
                if session.finish.is_set():
                    break
                # SID/__Secure-1PSID/__Secure-3PSID appear only after a real sign-in.
                if any(entry.get('name') in youtube_auth.IDENTITY_COOKIES for entry in cookies):
                    detected_at = detected_at or time.time()
                    if time.time() - detected_at > 3:
                        break
                elif not nudged and time.time() - session.started > 25:
                    # A fresh profile has no session to inherit, which is easy to miss.
                    nudged = True
                    session.message = ('还没检测到登录：这个窗口是干净的，请在窗口里输入账号密码完成登录，'
                                       '不会自动带上你浏览器的登录态。')
                try:
                    context.pages[0].wait_for_timeout(1000)
                except Exception:
                    time.sleep(1)
            signed_in = any(entry.get('name') in youtube_auth.IDENTITY_COOKIES for entry in cookies)
            if not signed_in:
                session.state, session.message = 'failed', '没有检测到 YouTube 登录成功，未保存。请重新登录。'
                return
            session.state, session.message = 'saving', '正在保存登录状态…'
            # Keep the browser profile (it owns the session) and mirror the cookies for yt-dlp.
            youtube_auth.save_cookies(cookies)
            session.state, session.message = 'saved', '登录成功：浏览器配置与 cookies 都已保存在本机。'
    except Exception:
        session.state, session.message = 'failed', '无法打开登录窗口，请确认本机已安装 Edge 或项目自带浏览器。'
    finally:
        if context is not None:
            try:
                context.close()  # Closing flushes the persistent profile to disk.
            except Exception:
                pass


# ---- Headless QR login (抖音 / 小红书) --------------------------------------
SCANNED_WORDS = ('扫码成功', '已扫码', '请在手机上确认', '在手机上确认')
EXPIRED_WORDS = ('已失效', '已过期', '二维码失效', '二维码过期')
VERIFY_WORDS = ('身份验证', '安全验证', '验证身份', '短信验证', '手机号验证')


def cookie_value(cookies, name):
    for item in cookies:
        if item.get('name') == name and matches(item.get('domain'), ('xiaohongshu.com', 'douyin.com')):
            return item.get('value') or ''
    return ''


def logged_in(platform, cookies, baseline):
    """True once the session cookie appears or changes after the QR code was shown."""
    if baseline is None:
        return False
    value = cookie_value(cookies, spec(platform)['session_cookie'])
    return bool(value) and value != baseline


def qr_phase(text):
    if any(word in text for word in EXPIRED_WORDS):
        return 'expired'
    if any(word in text for word in SCANNED_WORDS):
        return 'scanned'
    return 'waiting'


def _qr_image(page, selector):
    element = page.query_selector(selector)
    if element is None or not element.is_visible():
        return None
    source = element.get_attribute('src') or ''
    if source.startswith('data:image/') and len(source) < 500_000:
        return source
    try:
        return 'data:image/png;base64,' + base64.b64encode(element.screenshot(timeout=5000)).decode('ascii')
    except Exception:
        return None


def _qr_text(page, selector):
    """Text around the QR code (expiry and scanned overlays live a few levels up)."""
    return page.evaluate("""selector => {
        let node = document.querySelector(selector);
        for (let i = 0; i < 4 && node && node.parentElement; i += 1) node = node.parentElement;
        return node ? node.innerText.slice(0, 2000) : '';
    }""", selector) or ''


def _open_login_dialog(page):
    for locator in (page.get_by_role('button', name='登录', exact=True), page.get_by_text('登录', exact=True)):
        try:
            if locator.count():
                locator.first.click(timeout=3000)
                return
        except Exception:
            continue


def _run_qr(session):
    platform = session.platform
    item = PLATFORMS[platform]
    selector = item['qr_selector']
    browser = None
    sync_playwright = _import_playwright(session)
    if sync_playwright is None:
        return
    try:
        with sync_playwright() as playwright:
            browser, edge = _launch(playwright, headless=True)
            context = browser.new_context(locale='zh-CN', viewport={'width': 1280, 'height': 800},
                                          user_agent=user_agent(browser.version, edge))
            context.add_init_script("Object.defineProperty(navigator,'webdriver',{get:()=>undefined})")
            page = context.new_page()
            try:
                page.goto(item['qr_url'], wait_until='domcontentloaded', timeout=45000)
            except Exception:
                pass  # The QR may still render on a slow page; the loop below decides.
            baseline = None
            detected_at = None
            clicked = False
            while True:
                if session.cancel.is_set():
                    session.state, session.message = 'cancelled', '已取消扫码登录，未保存任何内容。'
                    return
                elapsed = time.time() - session.started
                if elapsed > QR_TIMEOUT:
                    session.state, session.message = 'expired', '二维码已过期，请刷新后重新扫码。'
                    return
                cookies = context.cookies()
                if logged_in(platform, cookies, baseline):
                    # Let the site finish writing its remaining cookies after the redirect.
                    detected_at = detected_at or time.time()
                    session.state, session.message = 'saving', '登录成功，正在保存…'
                    if time.time() - detected_at > 3:
                        break
                else:
                    image = _qr_image(page, selector)
                    if image:
                        if baseline is None:
                            baseline = cookie_value(cookies, item['session_cookie'])
                        session.qr = image
                        phase = qr_phase(_qr_text(page, selector))
                        if phase == 'expired':
                            session.state, session.message = 'expired', '二维码已过期，请刷新后重新扫码。'
                            return
                        if phase == 'scanned' or session.state == 'scanned':
                            session.state, session.message = 'scanned', '扫码成功，请在手机上确认登录。'
                        else:
                            session.state, session.message = 'waiting', f'请用{item["qr_app"]} 扫描二维码。'
                    elif baseline is not None:
                        # The QR left the page without a login: a scan was made and the site wants more.
                        session.state, session.message = 'scanned', '扫码成功，请在手机上确认登录。'
                        if any(word in page.inner_text('body')[:20000] for word in VERIFY_WORDS):
                            session.state = 'verify'
                            session.message = f'{item["name"]}要求进一步验证（例如短信验证码），扫码登录无法完成。请改用“浏览器登录”。'
                            return
                    elif elapsed > 15 and not clicked:
                        clicked = True
                        _open_login_dialog(page)
                    elif elapsed > 45:
                        session.state = 'failed'
                        session.message = f'没能获取到{item["name"]}的登录二维码（可能被风控拦下），请改用“浏览器登录”。'
                        return
                page.wait_for_timeout(1000)
            save(platform, context.cookies())
            session.state, session.message = 'saved', '登录成功，已保存到本机。'
    except RuntimeError as error:
        session.state, session.message = 'failed', str(error)
    except Exception:
        session.state, session.message = 'failed', '扫码登录出错，请重试或改用“浏览器登录”。'
    finally:
        session.qr = None
        if browser is not None:
            try:
                browser.close()
            except Exception:
                pass
