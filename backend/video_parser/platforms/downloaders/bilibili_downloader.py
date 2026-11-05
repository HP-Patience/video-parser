import re
import json
import math
import random
from video_parser.platforms.downloaders.base_downloader import BaseDownloader
from video_parser.core.settings import USER_AGENT_PC
from video_parser.core.logging import logger
from video_parser.platforms.bilibili_session import fetch_page


def _positive_duration(value):
    if type(value) not in (int, float): return None
    try:
        return value if math.isfinite(value) and value > 0 else None
    except (OverflowError, TypeError, ValueError):
        return None


class BilibiliDownloader(BaseDownloader):
    def __init__(self, real_url):
        super().__init__(real_url)
        self.headers = {
            "content-type": "application/json; charset=UTF-8",
            'User-Agent': random.choice(USER_AGENT_PC),
            'referer': self.real_url
        }
        self.data, self.data2 = self.fetch_html_data()

    def fetch_html_content(self):
        self.html_content, self.cookie_status = fetch_page(self.real_url, self.headers)
        return self.html_content

    def fetch_html_data(self):
        self.html_content = self.fetch_html_content()
        pattern_playinfo = re.compile(r'window\.__playinfo__\s*=\s*(\{.*\})', re.DOTALL)
        json_data = BaseDownloader.parse_html_data(self.html_content, pattern_playinfo)
        pattern_initial = re.compile(r'window\.__INITIAL_STATE__\s*=\s*(\{.*\});', re.DOTALL)
        json_data2 = BaseDownloader.parse_html_data(self.html_content, pattern_initial)
        return json_data, json_data2

    def get_real_video_url(self):
        try:
            data_dict = json.loads(self.data)
            video_url = data_dict['data']['dash']['video'][0]['baseUrl']
            return video_url
        except (KeyError, json.JSONDecodeError) as e:
            logger.warning(f"Failed to parse video URL: {e}")

    def get_audio_url(self):
        """获取音频URL"""
        try:
            data_dict = json.loads(self.data)
            audio_url = data_dict['data']['dash']['audio'][0]['baseUrl']
            return audio_url
        except (KeyError, json.JSONDecodeError) as e:
            logger.warning(f"Failed to parse audio URL: {e}")
            return None

    def get_title_content(self):
        try:
            data_dict = json.loads(self.data2)
            title_content = data_dict['videoData']['title']
            return title_content
        except (KeyError, json.JSONDecodeError) as e:
            logger.warning(f"Failed to parse title content:: {e}")

    def get_episodes(self):
        try:
            state = json.loads(self.data2) if isinstance(self.data2, str) else self.data2
            pages = state.get('videoData', {}).get('pages', [])
        except (AttributeError, TypeError, json.JSONDecodeError):
            return []
        if not isinstance(pages, list):
            return []
        episodes = {}
        for page in pages[:500]:
            if not isinstance(page, dict):
                continue
            number = page.get('page')
            if type(number) is not int or not 1 <= number <= 100000:
                continue
            title = page.get('part')
            title = ' '.join(title.split())[:200] if isinstance(title, str) else ''
            duration = _positive_duration(page.get('duration'))
            episodes[number] = {'part': number, 'title': title or f'P{number}', 'duration': duration}
        return [episodes[number] for number in sorted(episodes)]

    def get_collection(self):
        """The uploader's season (合集) this video belongs to, or None when it is not in one.

        A season is a list of separate BV ids; it is not the same thing as the pages (分P)
        handled by get_episodes, and one video can have pages:1 while sitting in a 37-item season.
        """
        try:
            state = json.loads(self.data2) if isinstance(self.data2, str) else self.data2
            video = state.get('videoData') or {}
            season = video.get('ugc_season')
            current = video.get('bvid')
        except (AttributeError, TypeError, json.JSONDecodeError):
            return None
        if not isinstance(season, dict):
            return None
        items, seen = [], set()
        for section in season.get('sections') or []:
            if not isinstance(section, dict):
                continue
            for episode in section.get('episodes') or []:
                if not isinstance(episode, dict):
                    continue
                bvid = episode.get('bvid')
                if not isinstance(bvid, str) or not re.fullmatch(r'BV[A-Za-z0-9]{10}', bvid) or bvid in seen:
                    continue
                seen.add(bvid)
                arc = episode.get('arc') or {}
                title = episode.get('title') or arc.get('title') or bvid
                items.append({'bvid': bvid, 'title': ' '.join(str(title).split())[:200],
                              'duration': _positive_duration(arc.get('duration')),
                              'current': bvid == current})
        if len(items) < 2:
            return None
        name = season.get('title')
        return {'title': ' '.join(name.split())[:120] if isinstance(name, str) and name.strip() else '合集',
                'total': len(items), 'items': items}

    def get_cover_photo_url(self):
        try:
            data_dict = json.loads(self.data2)
            cover_url = data_dict['videoData']['pic']
            return cover_url
        except (KeyError, json.JSONDecodeError) as e:
            logger.warning(f"Failed to parse cover URL: {e}")


if __name__ == '__main__':
    real_url = 'https://www.bilibili.com/video/BV1df421v7xm/?share_source=copy_web&vd_source=5ac2e55972f5e2fd96b63d01ee42ff01'
    bilibili_dl = BilibiliDownloader(real_url)
    print(bilibili_dl.get_title_content())
    print(bilibili_dl.get_cover_photo_url())
    print(bilibili_dl.get_real_video_url())
