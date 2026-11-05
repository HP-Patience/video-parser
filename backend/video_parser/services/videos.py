#!/usr/bin/env python3
"""Shared video operations. No HTTP framework or command-line parser dependency."""
from dataclasses import dataclass, field, replace
import importlib
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
from video_parser.services import library as library
from urllib.parse import parse_qs, urlencode, urlparse, urlunparse

from video_parser.core.paths import ROOT, CACHE_DIR
DEFAULT_DOWNLOADS = ROOT / 'downloads'
CONFIG = json.loads((ROOT / 'configs' / 'business_config.json').read_text(encoding='utf-8'))
COMMANDS = {
    'capabilities': '列出功能与平台', 'doctor': '检查运行环境',
    'parse': '解析标题、封面和视频直链', 'download': '下载视频（支持 B站、抖音与 YouTube；B站与 YouTube 合并音视频）',
    'cover': '下载封面', 'frames': '提取本地视频画面',
    'analyze': '使用 Qwen3-VL 分析画面（链接或本地视频）',
    'transcribe': '使用本机 Whisper 将音视频转为文字和字幕',
    'transcribe-batch': '批量本机转录：复用 CUDA 模型，单个失败不影响后续文件',
    'list': '列出本地视频', 'play': '调用系统播放器打开本地视频',
}
SHORT_HOSTS = {'b23.tv', 'v.douyin.com', 'xhslink.com', 'www.xhslink.com'}
VIDEO_EXTENSIONS = {'.mp4', '.mkv', '.webm', '.mov', '.avi', '.m4v'}


@dataclass
class ParsedVideo:
    metadata: dict
    headers: dict
    video_urls: list
    audio_urls: list
    variants: dict = field(default_factory=dict)


def load_environment():
    try:
        from dotenv import load_dotenv
    except ImportError:
        return
    load_dotenv(ROOT / '.env')
    os.environ.setdefault('PLAYWRIGHT_BROWSERS_PATH', str(ROOT / 'runtimes/browsers'))
    if os.getenv('VIDEO_LIBRARY_ROOT'):
        library.LIBRARY_ROOT = Path(os.environ['VIDEO_LIBRARY_ROOT']).expanduser().resolve()


def capabilities():
    return {
        'commands': COMMANDS, 'platforms': list(CONFIG['PLATFORM_MAP']),
        'speech_transcription': True, 'subtitle_download': False,
        'analysis': '仅分析抽样画面，不读取音频，不保证完整理解长视频。画面会发送给配置的模型服务。',
        'platform_note': '代码中已实现的平台；实际可用性受站点风控、登录要求及页面变化影响。',
        'default_download_directory': str(library.LIBRARY_ROOT),
    }


def doctor():
    from video_parser.services import transcription,diagnostics
    dependencies = {}
    for module in ('requests', 'bs4', 'lxml', 'dotenv', 'py_mini_racer', 'PIL', 'openai', 'yt_dlp'):
        try:
            importlib.import_module(module)
            dependencies[module] = {'available': True}
        except Exception as error:
            dependencies[module] = {'available': False, 'error': type(error).__name__}
    tools = {tool: shutil.which(tool) for tool in ('ffmpeg', 'ffprobe')}
    return {
        'python': sys.executable, 'python_version': sys.version.split()[0],
        'dependencies': dependencies, 'tools': tools,
        'parser_ready': all(dependencies[name]['available'] for name in ('requests', 'bs4', 'lxml', 'dotenv', 'py_mini_racer')),
        'media_ready': all(tools.values()),
        'transcription_ready': transcription.available(),
        'environment_checks':diagnostics.checks(),
        'ai_configured': bool(os.getenv('QWEN_API_KEY', '').strip()),
        'ai_model': os.getenv('QWEN_MODEL_ID', 'Qwen/Qwen3-VL-8B-Instruct'),
        'note': '缺少 AI 密钥不影响解析、下载、封面和画面提取。doctor 不发网络请求。',
    }


def resolve_url(text):
    from video_parser.platforms.web_fetcher import UrlParser, WebFetcher
    url = UrlParser.get_url(text.strip())
    if not url:
        raise ValueError('没有找到 http(s) 视频链接，请传入链接或完整分享文本。')
    parsed = urlparse(url)
    host = (parsed.hostname or '').lower()
    if parsed.username or parsed.password or parsed.port not in (None, 80, 443):
        raise ValueError('视频链接不能包含认证信息或自定义端口。')
    domains = CONFIG['DOMAIN_TO_NAME']
    aliases = {name[4:]: name for name in domains if name.startswith('www.')}
    aliases['m.bilibili.com'] = 'www.bilibili.com'
    if host in SHORT_HOSTS:
        url = WebFetcher.fetch_redirect_url(url, preserve_query=True)
        if not url:
            raise RuntimeError('短链接跳转失败，请提供平台的完整视频链接。')
        parsed = urlparse(url)
        host = (parsed.hostname or '').lower()
    if parsed.scheme not in ('https', 'http') or parsed.username or parsed.password or parsed.port not in (None, 80, 443):
        raise ValueError('视频链接跳转不能包含认证信息或自定义端口。')
    host = aliases.get(host, host)
    platform = domains.get(host)
    if not platform:
        raise ValueError('该链接的平台尚未支持。运行 capabilities 查看支持列表。')
    if platform == '抖音':
        return UrlParser.douyin_video_address(url), platform
    if platform == 'YouTube':
        from video_parser.platforms.youtube_links import canonical_url
        return canonical_url(url), platform
    query = parse_qs(parsed.query)
    keys = {'哔哩哔哩': ('p',), '小红书': ('xsec_token', 'xsec_source'), 'YouTube': ('v',)}.get(platform, ())
    kept = {key: query[key][0] for key in keys if key in query}
    url = urlunparse(('https', host, parsed.path.rstrip('/'), '', urlencode(kept), ''))
    return url, platform


def track_urls(track):
    urls = [track.get('baseUrl') or track.get('base_url')]
    urls += track.get('backupUrl') or track.get('backup_url') or []
    urls = list(dict.fromkeys(url for url in urls if url))
    # B站 mcdn 的非标准端口在部分网络不可达，先尝试标准 CDN。
    return sorted(urls, key=lambda url: (urlparse(url).port not in (None, 443), 'mcdn' in (urlparse(url).hostname or '')))


def parse_link(text, require_video=True):
    from video_parser.platforms.factory import DownloaderFactory
    from video_parser.platforms.web_fetcher import UrlParser
    url, platform = resolve_url(text)
    for attempt in range(5 if platform == '小红书' else 1):
        downloader = DownloaderFactory.create_downloader(platform, url)
        title = downloader.get_title_content()
        video_url = downloader.get_real_video_url() if require_video else None
        cover_url = downloader.get_cover_photo_url()
        if video_url or not require_video:
            break
    metadata = {
        'source_url': url, 'platform': platform, 'video_id': UrlParser.get_video_id(url),
        'title': title or UrlParser.get_video_id(url) or 'video',
        'video_url': video_url, 'cover_url': cover_url,
    }
    if platform == 'YouTube':
        details = downloader.metadata()
        identifier = parse_qs(urlparse(url).query)['v'][0]
        if details['id'] != identifier:
            raise ValueError('YouTube 返回的视频标识与请求不符。')
        metadata.update(video_id=identifier, duration=details.get('duration'))
    video_urls = [video_url] if video_url else []
    audio_urls = []
    variants = {}
    if platform == 'YouTube' and require_video:
        # yt-dlp already listed every rendition in the same dump; expose one entry
        # per resolution instead of the fixed 1080p cap the downloader used to apply.
        # YouTube renditions are pinned by format id, so no direct URL is stored.
        get_qualities = getattr(downloader, 'qualities', None)
        for item in get_qualities() if callable(get_qualities) else []:
            variants[item['id']] = {**item, 'video_urls': []}
    if platform == '哔哩哔哩':
        metadata['bilibili_cookie_status'] = getattr(downloader, 'cookie_status', 'not_checked')
        get_episodes = getattr(downloader, 'get_episodes', None)
        metadata['episodes'] = get_episodes() if callable(get_episodes) else []
        # A season is not a page list: it is a separate set of BV ids the user may want to import.
        get_collection = getattr(downloader, 'get_collection', None)
        metadata['collection'] = get_collection() if callable(get_collection) else None
    if platform == '哔哩哔哩' and require_video:
        play = json.loads(downloader.data or '{}').get('data', {})
        dash = play.get('dash', {})
        usable = [track for track in dash.get('video', []) if track.get('height') and track_urls(track)]
        heights = sorted({track['height'] for track in usable}, reverse=True)
        for height in heights:
            same_height = [track for track in usable if track['height'] == height]
            # AVC is broadly playable in browsers; select bitrate within the compatible codec first.
            track = max(same_height, key=lambda item: (str(item.get('codecs', '')).startswith('avc1'), item.get('bandwidth', 0)))
            key = f'{height}p'
            # Label by the short side so a 1080x1920 portrait video reads as 1080p, not 1920p.
            width = track.get('width')
            label = f'{min(width, height)}p' if width else key
            public = {'id': key, 'label': label, 'width': track.get('width'), 'height': height,
                      'codec': track.get('codecs'), 'bandwidth': track.get('bandwidth'), 'track_id': track.get('id')}
            variants[key] = {**public, 'video_urls': track_urls(track)}
        if variants:
            first = next(iter(variants.values()))
            video_urls = first['video_urls']
            metadata.update(width=first['width'], height=first['height'], duration=dash.get('duration'))
        if dash.get('audio'):
            audio_urls = track_urls(max(dash['audio'], key=lambda item: item.get('bandwidth', 0)))
        elif hasattr(downloader, 'get_audio_url'):
            audio_url = downloader.get_audio_url()
            audio_urls = [audio_url] if audio_url else []
        metadata['audio_url'] = audio_urls[0] if audio_urls else None
    if platform == '小红书' and require_video and hasattr(downloader, 'streams'):
        found = downloader.streams()
        audio = found['audio_urls']
        # Without the separate AAC track only H.264 (sound inside) is usable.
        usable = [item for item in found['videos'] if audio or item['has_audio']]
        for height in sorted({min(item['width'], item['height']) for item in usable}, reverse=True):
            same = [item for item in usable if min(item['width'], item['height']) == height]
            item = max(same, key=lambda entry: (entry['codec'] == 'H.264', entry['bitrate']))
            key = f'{height}p'
            label = key if item['codec'] == 'H.264' else f'{key} · H.265'
            public = {'id': key, 'label': label, 'width': item['width'], 'height': item['height'],
                      'codec': item['codec'], 'bandwidth': item['bitrate']}
            variants[key] = {**public, 'video_urls': item['urls']}
        if variants:
            first = next(iter(variants.values()))
            video_urls = first['video_urls']
            audio_urls = audio
            metadata.update(width=first['width'], height=first['height'], duration=found['duration'])
            metadata['audio_url'] = audio_urls[0] if audio_urls else None
    if platform == '抖音' and isinstance(getattr(downloader, 'data', None), dict):
        video = (downloader.data.get('aweme_detail') or {}).get('video') or {}
        duration = video.get('duration')
        if type(duration) in (int, float) and duration > 0:
            metadata.update(width=video.get('width'), height=video.get('height'), duration=duration / 1000)
        if downloader.data.get('_browser_user_agent'):
            video_urls = (video.get('play_addr') or {}).get('url_list') or video_urls
    if require_video and not video_urls:
        from video_parser.platforms.downloaders.base_downloader import PlatformAccessError
        from video_parser.services import accounts
        account = {'小红书': 'xiaohongshu', '抖音': 'douyin', '哔哩哔哩': 'bilibili'}.get(platform)
        hint = ''
        if account and accounts.load(account):
            hint = '已使用本机登录态仍失败，登录可能已失效，可在“账号与登录”页重新登录。'
        elif account:
            hint = '可以到“账号与登录”页登录该平台后再试。'
        raise PlatformAccessError('未获取到视频直链；可能是图文内容、登录限制或平台风控。' + hint)
    metadata['video_url'] = video_urls[0] if video_urls else None
    headers = dict(downloader.headers or {})
    if platform == '小红书':
        headers.pop('Cookie', None)  # The login is for the page only; the CDN does not need it.
    parsed = ParsedVideo(metadata, headers, video_urls, audio_urls, variants)
    return select_quality(parsed, 'best') if require_video else parsed


def quality_options(parsed):
    if parsed.variants:
        return [{key: value for key, value in item.items() if key != 'video_urls'} for item in parsed.variants.values()]
    label = '最高 1080p · MP4' if parsed.metadata['platform'] == 'YouTube' else '平台提供的默认画质'
    return [{'id': 'default', 'label': label, 'width': None, 'height': None, 'codec': None}]


def select_quality(parsed, quality='best'):
    options = quality_options(parsed)
    key = options[0]['id'] if quality == 'best' else quality
    if quality.isdecimal():
        # Numeric requests mean "this resolution"; high frame-rate renditions win.
        exact = quality + 'p'
        key = exact if any(item['id'] == exact for item in options) else next(
            (item['id'] for item in options if item.get('height') == int(quality)), exact)
    choice = next((item for item in options if item['id'] == key), None)
    if choice is None: raise ValueError('该清晰度不在本次可用列表中，请重新解析或选择可用画质。')
    metadata = {**parsed.metadata, 'quality_id': key, 'selected_quality': choice, 'qualities': options}
    if parsed.variants:
        metadata.update(width=choice['width'], height=choice['height'])
    # YouTube variants carry no direct URL: the download is pinned by format id.
    video_urls = (parsed.variants[key]['video_urls'] if parsed.variants else []) or parsed.video_urls
    metadata['video_url'] = video_urls[0] if video_urls else None
    return replace(parsed, metadata=metadata, video_urls=video_urls)


def archived_videos(identifier, *, use_validation_cache=False):
    from video_parser.services import media_validation
    project = library.get(identifier)
    videos = []
    for relative, item in project['resources'].items():
        if item['kind'] != 'video': continue
        try:
            path = media_validation.resource(project, relative) if use_validation_cache else library.resource(identifier, relative)
            media = item.get('media') or probe(path)
            check_video(media, require_audio=project['platform'] == 'bilibili')
            track = next(stream for stream in media['streams'] if stream.get('codec_type') == 'video')
            height, width = track.get('height'), track.get('width')
            selected = item.get('selected_quality')
            key = 'saved-' + hashlib.sha256(relative.encode()).hexdigest()[:16]
            short = min(width, height) if width and height else height
            videos.append({'id': key, 'label': (f'{short}p' if short else '原有视频（尺寸未知）') + ' · 已归档',
                           'width': width, 'height': height, 'codec': track.get('codec_name'),
                           'path': relative, 'media': media, 'size_bytes': path.stat().st_size,
                           'selected_quality': selected})
        except (OSError, ValueError, KeyError, RuntimeError, StopIteration):
            continue
    return videos


def codec_matches(codec, saved_codec):
    """Compare a platform codec string with ffprobe's codec_name."""
    value = re.sub(r'[^a-z0-9]', '', str(codec or '').lower())
    if not value:
        return True
    if value.startswith(('avc1', 'h264')):
        return saved_codec == 'h264'
    if value.startswith(('hev1', 'hvc1', 'h265', 'hevc')):
        return saved_codec == 'hevc'
    if value.startswith(('av01', 'av1')):
        return saved_codec == 'av1'
    if value.startswith(('vp9', 'vp09')):
        return saved_codec == 'vp9'
    return False


def saved_quality(identifier, selected):
    for saved in archived_videos(identifier):
        previous = saved['selected_quality']
        if previous:
            if all(previous.get(key) == selected.get(key) for key in ('id', 'width', 'height', 'codec', 'track_id')):
                return saved
            if previous.get('height'):
                continue
        # Records archived before a platform exposed real renditions carry no
        # measured size, so fall back to the archived dimensions and codec.
        if selected.get('height'):
            if ((saved['height'], saved['width']) == (selected.get('height'), selected.get('width'))
                    and codec_matches(selected.get('codec'), saved['codec'])):
                return saved
        elif selected['id'] == 'default' and library.get(identifier)['platform'] != 'bilibili':
            return saved
    return None


def archive_video(parsed, staging_root=None):
    parsed = select_quality(parsed, parsed.metadata.get('quality_id', 'best'))
    project = library.ensure(parsed.metadata)
    selected = parsed.metadata['selected_quality']
    with library.operation(project['id']):
        saved = saved_quality(project['id'], selected)
        if saved:
            return {'path': str(library.resource(project['id'], saved['path'])), 'relative': saved['path'],
                    'size_bytes': saved['size_bytes'], 'media': saved['media'], 'title': project['title'],
                    'project_id': project['id'], 'project_directory': str(project['directory'])}
        from video_parser.services import task_runtime
        with tempfile.TemporaryDirectory(prefix='.download-', dir=staging_root or project['directory']) as directory:
            staged = Path(directory) / 'video.mp4'
            result = download_video(parsed, staged)
            task_runtime.progress('validating','正在校验音视频完整性与清晰度…')
            check_video(result['media'], require_audio=bool(parsed.audio_urls))
            actual = next(stream for stream in result['media']['streams'] if stream.get('codec_type') == 'video')
            if selected.get('height') and (actual.get('height'), actual.get('width')) != (selected['height'], selected['width']):
                raise RuntimeError('下载文件尺寸与所选清晰度不符，不归档为成功。')
            identity = hashlib.sha256(json.dumps(selected, sort_keys=True).encode()).hexdigest()[:12]
            task_runtime.progress('archiving','正在归档已校验的视频…')
            relative = library.import_file(project['id'], staged, f"videos/{selected['id']}_{identity}.mp4", 'video',
                                           {'media': result['media'], 'selected_quality': selected})
            result['path'] = str(library.resource(project['id'], relative))
    return {**result, 'relative': relative, 'project_id': project['id'], 'project_directory': str(project['directory'])}


def safe_name(title, fallback='video'):
    name = re.sub(r'[<>:"/\\|?*\x00-\x1f]', '_', str(title or '')).strip(' .')[:100].rstrip(' .')
    if not name:
        name = re.sub(r'[^\w-]', '_', str(fallback))[:80] or 'video'
    if re.match(r'^(CON|PRN|AUX|NUL|COM[1-9]|LPT[1-9])(?:\.|$)', name, re.I):
        name = '_' + name
    return name


def publish(staged, target):
    # 同目录临时文件保证同一文件系统；硬链接原子发布，绝不覆盖已存在的文件。
    os.link(staged, target)


def download_resource(urls, path, headers, track=1, tracks=1):
    import requests
    from video_parser.services import task_runtime
    from video_parser.services.download_progress import TransferProgress
    if not urls:
        raise RuntimeError('没有可下载的资源链接。')
    errors = []
    attempts = [url for url in urls for _ in range(2)]  # One quick retry per link for transient CDN errors.
    for index, url in enumerate(attempts):
        task_runtime.checkpoint()
        if index and attempts[index - 1] == url:
            import time
            time.sleep(2)
        try:
            received = 0
            with requests.get(url, headers=headers, stream=True, timeout=(15, 60)) as response:
                response.raise_for_status()
                expected = int(response.headers.get('Content-Length', 0))
                if response.headers.get('Content-Encoding', 'identity').lower() != 'identity':
                    expected = 0  # iter_content decodes compressed bodies.
                meter = TransferProgress(track, tracks)
                meter.report(0, expected)
                with Path(path).open('wb') as output:
                    for chunk in response.iter_content(chunk_size=256 * 1024):
                        if chunk:
                            output.write(chunk)
                            received += len(chunk)
                            meter.report(received, expected)
                if not received or (expected and received != expected):
                    raise RuntimeError(f'下载不完整：收到 {received} 字节，预期 {expected} 字节。')
                meter.report(received, expected, finished=True)
            return
        except task_runtime.Cancelled:
            raise
        except (requests.RequestException, RuntimeError) as error:
            errors.append(type(error).__name__)
            print(f'资源下载失败（{type(error).__name__}），尝试下一个备用链接。', file=sys.stderr)
    raise RuntimeError('资源下载失败：' + ', '.join(errors) + '。请检查网络或重新解析链接。')


def require_tools(*tools):
    for tool in tools:
        if not shutil.which(tool):
            raise RuntimeError(f'缺少 {tool}，请安装 ffmpeg 并将其 bin 目录加入 PATH。')


def probe(path):
    require_tools('ffprobe')
    result = subprocess.run(['ffprobe', '-v', 'error', '-show_entries',
                             'format=duration,size:stream=codec_type,codec_name,width,height',
                             '-of', 'json', str(path)], capture_output=True, text=True, timeout=30)
    if result.returncode:
        raise RuntimeError('无法读取媒体文件：' + result.stderr.strip()[-800:])
    info = json.loads(result.stdout)
    if float(info.get('format', {}).get('duration', 0)) <= 0:
        raise RuntimeError('媒体文件没有有效时长。')
    return info


def check_video(info, require_audio=False, expected_duration=None):
    types = {stream.get('codec_type') for stream in info.get('streams', [])}
    if 'video' not in types or (require_audio and 'audio' not in types):
        raise RuntimeError('下载结果缺少视频轨或所需音轨，不保存不完整成品。')
    if expected_duration:
        actual = float(info['format']['duration'])
        if abs(actual - float(expected_duration)) > max(5, float(expected_duration) * .01):
            raise RuntimeError('下载结果时长与源视频不符，不保存不完整成品。')


def download_video(parsed, target):
    target = Path(target).resolve()
    if target.exists():
        raise FileExistsError(f'文件已存在，拒绝覆盖：{target}')
    platform = parsed.metadata['platform']
    from video_parser.services import task_runtime
    task_runtime.checkpoint()
    if platform == 'YouTube':
        from video_parser.services import task_runtime
        from video_parser.platforms.youtube import download_youtube
        selected_quality = parsed.metadata.get('selected_quality') or {}
        require_tools('ffmpeg', 'ffprobe')
        target.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(prefix='.video-download-', dir=target.parent) as directory:
            staged = Path(directory) / 'output.mp4'

            def _report_progress(done, total, finished=False, **values):
                import time
                task_runtime.progress('downloading', '正在下载 YouTube 当前轨道…', force=finished,
                                      bytes=done, total_bytes=total, sampled_at=time.time(), **values)

            download_youtube(parsed.metadata['source_url'], staged, _report_progress,
                             format_id=selected_quality.get('format_id'),
                             has_audio=selected_quality.get('has_audio'),
                             tracks=1 if selected_quality.get('has_audio') else 2)
            task_runtime.progress('validating', '正在校验下载结果…', force=True)
            info = probe(staged)
            check_video(info, require_audio=True, expected_duration=parsed.metadata.get('duration'))
            task_runtime.checkpoint()
            publish(staged, target)
        return {'path': str(target), 'size_bytes': target.stat().st_size, 'media': info, 'title': parsed.metadata['title']}
    needs_audio = platform == '哔哩哔哩'
    if needs_audio and not parsed.audio_urls:
        raise RuntimeError('未获取到 B站音轨，停止下载，避免生成无声视频。')
    require_tools('ffprobe', *(['ffmpeg'] if parsed.audio_urls else []))
    target.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='.video-download-', dir=target.parent) as directory:
        temporary = Path(directory)
        staged = temporary / 'output.mp4'
        if parsed.audio_urls:
            video, audio = temporary / 'video.m4s', temporary / 'audio.m4s'
            download_resource(parsed.video_urls, video, parsed.headers, track=1, tracks=2)
            download_resource(parsed.audio_urls, audio, parsed.headers, track=2, tracks=2)
            task_runtime.progress('merging', '正在合并音视频；剩余时间未知。', force=True)
            result = subprocess.run(['ffmpeg', '-hide_banner', '-loglevel', 'error', '-nostdin', '-n',
                                     '-i', str(video), '-i', str(audio), '-map', '0:v:0', '-map', '1:a:0',
                                     '-c', 'copy', '-movflags', '+faststart', str(staged)],
                                    capture_output=True, text=True, timeout=1800)
            if result.returncode:
                raise RuntimeError('音视频合并失败：' + result.stderr.strip()[-800:])
        else:
            download_resource(parsed.video_urls, staged, parsed.headers)
        task_runtime.progress('validating', '正在校验下载结果…', force=True)
        info = probe(staged)
        check_video(info, require_audio=bool(parsed.audio_urls), expected_duration=parsed.metadata.get('duration'))
        task_runtime.checkpoint()
        publish(staged, target)
    return {'path': str(target), 'size_bytes': target.stat().st_size, 'media': info, 'title': parsed.metadata['title']}


def local_video(source):
    path = Path(source).expanduser().resolve()
    if not path.is_file():
        raise FileNotFoundError(f'本地视频不存在：{path}')
    return path


def output_file(args, metadata, extension):
    if args.output and args.command != 'analyze':
        return Path(args.output).expanduser().resolve()
    title = safe_name(metadata['title'], metadata.get('video_id') or 'video')
    identifier = safe_name(metadata.get('video_id') or 'video')
    part = parse_qs(urlparse(metadata['source_url']).query).get('p', ['1'])[0]
    suffix = f'_p{safe_name(part)}' if part != '1' else ''
    if args.output_dir:
        return Path(args.output_dir).expanduser().resolve() / f'{title}_{identifier}{suffix}{extension}'
    project = library.ensure(metadata)
    return project['directory'] / ('video.mp4' if extension == '.mp4' else 'cover' + extension)


def save_cover(parsed, target):
    from PIL import Image
    url = parsed.metadata.get('cover_url')
    if not url:
        raise RuntimeError('未获取到封面链接。')
    target = Path(target).resolve()
    if target.exists():
        raise FileExistsError(f'文件已存在，拒绝覆盖：{target}')
    target.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='.video-cover-', dir=target.parent) as directory:
        staged = Path(directory) / 'cover'
        download_resource([url], staged, parsed.headers)
        with Image.open(staged) as image:
            image.verify()
        publish(staged, target)
    return {'path': str(target), 'size_bytes': target.stat().st_size}


def save_frames(source, target, count):
    from video_parser.services.analysis import extract_frames
    source = local_video(source)
    target = Path(target).expanduser().resolve()
    if target.exists():
        raise FileExistsError(f'画面目录已存在，拒绝覆盖：{target}')
    require_tools('ffmpeg', 'ffprobe')
    check_video(probe(source))
    target.parent.mkdir(parents=True, exist_ok=True)
    frames = extract_frames(str(source), num_frames=count)
    try:
        if len(frames) != count:
            raise RuntimeError(f'画面提取不完整：请求 {count} 帧，得到 {len(frames)} 帧。')
        with tempfile.TemporaryDirectory(prefix='.video-frames-', dir=target.parent) as directory:
            staged = Path(directory) / 'frames'
            staged.mkdir()
            for frame in frames:
                shutil.copyfile(frame, staged / Path(frame).name)
            staged.rename(target)
    finally:
        for frame in frames:
            Path(frame).unlink(missing_ok=True)
        if frames:
            Path(frames[0]).parent.rmdir()
    return {'directory': str(target), 'frames': [str(target / Path(frame).name) for frame in frames]}


@dataclass(frozen=True)
class VideoRequest:
    """Transport-neutral options shared by command adapters and application callers."""
    command: str
    source: str | None = None
    output: str | None = None
    output_dir: str | None = None
    quality: str = 'best'
    language: str = 'auto'
    timestamps: bool = False
    count: int = 8
    frames: int | None = None
    prompt: str | None = None
    directory: str | None = None


def execute(args: VideoRequest):
    if args.command == 'capabilities':
        return capabilities()
    if args.command == 'transcribe':
        from video_parser.services import transcription
        return transcription.transcribe(args.source, args.language, args.output_dir, args.timestamps)
    if args.command == 'doctor':
        return doctor()
    if args.command == 'list':
        directories = [Path(args.directory).expanduser().resolve()] if args.directory else [DEFAULT_DOWNLOADS, CACHE_DIR, ROOT / 'static' / 'videos']
        if args.directory and not directories[0].is_dir():
            raise FileNotFoundError(f'目录不存在：{directories[0]}')
        files = sorted({path.resolve() for directory in directories for path in directory.glob('*')
                        if path.is_file() and path.suffix.lower() in VIDEO_EXTENSIONS})
        if not args.directory:
            for manifest in library.manifests():
                project = library.load(manifest)
                for name, item in project['resources'].items():
                    if item['kind'] == 'video':
                        try: files.append(library.resource(project['id'], name))
                        except (OSError, ValueError): pass
            files = sorted(set(files))
        return {'files': [{'path': str(path), 'size_bytes': path.stat().st_size} for path in files]}
    if args.command in ('parse', 'download', 'cover'):
        parsed = parse_link(args.source, require_video=args.command != 'cover')
        if args.command in ('parse', 'download'): parsed = select_quality(parsed, args.quality)
        if args.command == 'parse':
            return parsed.metadata
        automatic = not args.output and not args.output_dir
        if args.command == 'download':
            if not automatic:
                return download_video(parsed, output_file(args, parsed.metadata, '.mp4'))
            return archive_video(parsed)
        extension = Path(urlparse(parsed.metadata.get('cover_url') or '').path).suffix.lower()
        if extension not in {'.jpg', '.jpeg', '.png', '.webp', '.avif'}:
            extension = '.jpg'
        if not automatic:
            return save_cover(parsed, output_file(args, parsed.metadata, extension))
        project = library.ensure(parsed.metadata)
        with library.operation(project['id']):
            saved = library.find(project['id'], 'cover')
            if not saved:
                with tempfile.TemporaryDirectory(prefix='.cover-', dir=project['directory']) as temporary:
                    target = Path(temporary) / 'cover'
                    save_cover(parsed, target)
                    from PIL import Image
                    with Image.open(target) as image:
                        extension = { 'JPEG': '.jpg', 'PNG': '.png', 'WEBP': '.webp', 'AVIF': '.avif'}[image.format]
                    name = library.import_file(project['id'], target, 'cover' + extension, 'cover')
                    saved = (name, library.resource(project['id'], name))
        return {'path': str(saved[1]), 'size_bytes': saved[1].stat().st_size, 'project_id': project['id'], 'project_directory': str(project['directory'])}
    if args.command == 'frames':
        source = local_video(args.source)
        if args.output: return save_frames(source, args.output, args.count)
        project = library.local_project(source)
        if not library.owner(source):
            media = probe(source); check_video(media)
            library.import_file(project['id'], source, 'video' + source.suffix, 'video', {'media': media})
        with tempfile.TemporaryDirectory(prefix='.frames-', dir=project['directory']) as directory:
            result = save_frames(source, Path(directory) / 'frames', args.count)
            names = library.import_frames(project['id'], result['directory'])
        paths = [str(library.resource(project['id'], name)) for name in names]
        return {'directory': str(Path(paths[0]).parent), 'frames': paths, 'project_id': project['id']}
    if args.command == 'play':
        source = local_video(args.source)
        if sys.platform == 'win32':
            os.startfile(str(source))
        else:
            subprocess.run(['open' if sys.platform == 'darwin' else 'xdg-open', str(source)], check=True, timeout=30)
        return {'path': str(source), 'message': '已请求系统播放器打开；不代表已验证播放成功。'}
    if args.command == 'analyze':
        if not os.getenv('QWEN_API_KEY', '').strip():
            raise RuntimeError('AI 分析需要 QWEN_API_KEY；请在项目 .env 或环境变量中配置。')
        require_tools('ffmpeg', 'ffprobe')
        target = Path(args.output).expanduser().resolve() if args.output else None
        if target and target.exists():
            raise FileExistsError(f'分析报告已存在，拒绝覆盖：{target}')
        if re.search(r'https?://', args.source):
            parsed = select_quality(parse_link(args.source), args.quality)
            if args.output_dir:
                source = Path(download_video(parsed, output_file(args, parsed.metadata, '.mp4'))['path'])
            else:
                source = Path(archive_video(parsed)['path'])
        else:
            source = local_video(args.source)
        check_video(probe(source))
        from video_parser.services.analysis import analyze_video
        frames = args.frames or int(os.getenv('MAX_FRAMES', '8'))
        if frames < 1:
            raise ValueError('需要正整数。')
        project = library.owner(source)
        if not target and not args.output_dir:
            project = project or library.local_project(source)
            if not library.owner(source): library.import_file(project['id'], source, 'video' + source.suffix, 'video', {'media': probe(source)})
            with tempfile.TemporaryDirectory(prefix='.analysis-', dir=project['directory']) as directory:
                text = analyze_video(str(source), prompt=args.prompt, stream=False, num_frames=frames, keep_frames_dir=directory)
                library.import_frames(project['id'], directory)
            name = library.save_text(project['id'], 'analysis', text, {'frame_count': frames, 'prompt': args.prompt, 'video_resource': library.logical_resource(project, source)})
            target = library.resource(project['id'], name)
            return {'video_path': str(source), 'analysis': text, 'report_path': str(target), 'project_id': project['id'],
                    'note': '基于抽样画面，不是字幕或音频转录。'}
        text = analyze_video(str(source), prompt=args.prompt, stream=False, num_frames=frames)
        if not text or not text.strip():
            raise RuntimeError('模型返回空内容，未生成分析报告。')
        if target:
            target.parent.mkdir(parents=True, exist_ok=True)
            with tempfile.TemporaryDirectory(prefix='.video-analysis-', dir=target.parent) as directory:
                staged = Path(directory) / 'report'
                staged.write_text(text, encoding='utf-8')
                publish(staged, target)
        return {'video_path': str(source), 'analysis': text, 'report_path': str(target) if target else None,
                'note': '基于抽样画面，不是字幕或音频转录。'}
    raise ValueError('未知命令。')
