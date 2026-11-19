"""Bounded, cached YouTube metadata lookup; no credentials or signed URLs exposed."""
import json
import subprocess
import sys
from video_parser.platforms.downloaders.base_downloader import BaseDownloader
from video_parser.platforms.youtube_formats import renditions
from video_parser.services import youtube_auth


class YoutubeAccessError(RuntimeError):
    """Safe user-facing error; never include cookies or extractor response bodies."""


LOGIN_HINT = 'YouTube 要求登录校验（机器人验证）。请在 设置 → 登录与云端存储 里导入 YouTube cookies 或选择浏览器后重试。'


def describe_failure(stderr):
    """Map yt-dlp's stderr to one actionable sentence; never echo URLs or cookies."""
    text = (stderr or '').lower()
    if ('confirm you' in text and 'not a bot' in text) or 'sign in to confirm' in text:
        return LOGIN_HINT
    if 'cookies' in text and ('could not' in text or 'failed' in text or 'unable' in text):
        return '无法读取 YouTube 登录态（cookies），请在 设置 → 登录与云端存储 里重新导入。'
    if 'unable to connect to proxy' in text or 'tunnel connection failed' in text or 'proxyerror' in text:
        return 'YouTube 请求被代理拦截：请确认本地代理正在运行，且端口与系统代理一致。'
    if 'failed to resolve' in text or 'name or service not known' in text:
        return '域名解析失败，请检查网络或代理设置后重试。'
    if 'timed out' in text or 'timeout' in text or 'connection reset' in text or 'connection aborted' in text:
        return '连接 YouTube 超时，请检查网络后重试。'
    if 'video unavailable' in text or 'removed by the uploader' in text:
        return '该 YouTube 视频已不可用（可能已删除或设为私享）。'
    if 'members-only' in text or 'join this channel' in text:
        return '该视频为频道会员专属内容，需要对应会员账号的 cookies 才能解析。'
    if 'age' in text and ('restrict' in text or 'confirm your age' in text):
        return '该视频有年龄限制，需要已登录的 cookies 才能解析。'
    if 'not made this video available in your country' in text or ('geo' in text and 'block' in text):
        return '该视频在当前网络所在地区不可用，请更换节点后重试。'
    return '无法解析该 YouTube 视频；请检查网络或地区限制；年龄限制、会员专属视频不支持。'


class YoutubeDownloader(BaseDownloader):
    def __init__(self, real_url):
        super().__init__(real_url)
        self.headers = {}
        self._metadata = None
        self._dump = None

    def _data(self):
        if self._dump is None:
            command = [sys.executable, '-X', 'utf8', '-m', 'yt_dlp', '--ignore-config',
                       '--dump-single-json', '--skip-download', '--no-playlist', '--no-warnings',
                       '--quiet', '--socket-timeout', '15', '--retries', '1', '--extractor-retries', '1',
                       *youtube_auth.cookie_args(), '--', self.real_url]
            try:
                result = subprocess.run(command, capture_output=True, text=True, encoding='utf-8', timeout=60)
                if result.returncode:
                    raise YoutubeAccessError(describe_failure(result.stderr))
                data = json.loads(result.stdout)
                if data.get('_type') in {'playlist', 'multi_video'} or not data.get('id'):
                    raise ValueError('Expected one video')
                self._dump = data
            except subprocess.TimeoutExpired:
                raise YoutubeAccessError('YouTube 解析超时，请稍后重试。') from None
            except (ValueError, TypeError, AttributeError):
                raise YoutubeAccessError('YouTube 返回的信息无效，请重新解析。') from None
        return self._dump

    def metadata(self):
        if self._metadata is None:
            data = self._data()
            self._metadata = {key: data.get(key) for key in ('id', 'title', 'thumbnail', 'duration')}
        return self._metadata

    def qualities(self):
        """Every downloadable rendition, without signed URLs or extractor internals."""
        return renditions(self._data().get('formats'))

    def get_title_content(self):
        return self.metadata().get('title') or 'YouTube video'

    def get_cover_photo_url(self):
        return self.metadata().get('thumbnail')

    def get_real_video_url(self):
        return self.real_url
