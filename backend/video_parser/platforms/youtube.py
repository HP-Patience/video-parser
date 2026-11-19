"""YouTube-only yt-dlp adapter. Existing platform engines remain unchanged."""
from pathlib import Path
from video_parser.services import task_runtime, youtube_auth
from video_parser.platforms.downloaders.youtube_downloader import describe_failure

DEFAULT_FORMAT = 'bv*[ext=mp4][vcodec^=avc1][height<=1080]+ba[ext=m4a]/b[ext=mp4][height<=1080]'


def format_selector(format_id=None, has_audio=None):
    """Pin one enumerated rendition, merging the best AAC track when it is video-only."""
    if not format_id:
        return DEFAULT_FORMAT
    identifier = str(format_id)
    if has_audio:
        return identifier
    return f'{identifier}+ba[ext=m4a]/{identifier}+ba[acodec^=mp4a]/{identifier}'


class QuietLogger:
    # Extractor errors can contain signed URLs: keep them out of logs/API responses.
    def debug(self, message): pass
    def warning(self, message): pass
    def error(self, message): pass


def download_youtube(url, target, progress_cb=None, format_id=None, has_audio=None, tracks=None):
    try:
        import yt_dlp
    except ImportError:
        raise RuntimeError('缺少 yt-dlp，请安装项目依赖后重试。') from None
    from yt_dlp.utils import DownloadError
    target = Path(target)
    target.parent.mkdir(parents=True, exist_ok=True)
    # A video-only rendition downloads two files (video, then audio) before the merge.
    track_count = max(1, int(tracks or (1 if has_audio else 2)))
    track = 0
    previous = None

    def hook(data):
        nonlocal track, previous
        task_runtime.checkpoint()
        if data.get('status') not in {'downloading', 'finished'}:
            return
        filename = data.get('filename')
        if previous != filename or track == 0:
            previous = filename
            track += 1
        estimated = not data.get('total_bytes') and bool(data.get('total_bytes_estimate'))
        total = data.get('total_bytes') or data.get('total_bytes_estimate') or 0
        done = data.get('downloaded_bytes') or 0
        values = {'track': min(track, track_count), 'tracks': track_count,
                  'estimated_total': int(estimated)}
        # Avoid a wildly unstable ETA from the first few packets.
        if data.get('status') == 'downloading' and done >= 64 * 1024 and (data.get('elapsed') or 0) >= 1:
            if data.get('speed') is not None:
                values['speed_bytes_per_second'] = data['speed']
            if total > done and data.get('eta') is not None:
                values['eta_seconds'] = data['eta']
        if progress_cb:
            progress_cb(done, total, finished=data.get('status') == 'finished', **values)

    def postprocess(data):
        task_runtime.checkpoint()
        task_runtime.progress('merging', '正在合并或整理音视频；剩余时间未知。', force=True)

    options = {'format': format_selector(format_id, has_audio), 'outtmpl': str(target), 'merge_output_format': 'mp4',
               'quiet': True, 'no_warnings': True, 'noprogress': True, 'noplaylist': True,
               'retries': 2, 'fragment_retries': 2, 'socket_timeout': 15,
               'logger': QuietLogger(), 'progress_hooks': [hook], 'postprocessor_hooks': [postprocess]}
    options.update(youtube_auth.cookie_options())
    try:
        task_runtime.progress('reading', '正在读取 YouTube 下载信息；剩余时间未知。', force=True)
        with yt_dlp.YoutubeDL(options) as ydl:
            ydl.download([url])
    except DownloadError as error:
        raise RuntimeError(describe_failure(str(error))) from None
    task_runtime.checkpoint()
    # A codec/container mismatch may change the extension. Never publish an absent file.
    if not target.is_file() or not target.stat().st_size:
        raise RuntimeError('YouTube 未生成所需 MP4 文件，不发布不完整结果。')
