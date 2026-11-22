"""Inline rename: one request should preview and perform the real file move."""
import time
import unittest
from pathlib import Path
import tempfile
from unittest.mock import patch

from video_parser.services import library, naming, resource_renames as names, task_runtime

VIDEO_ID = 'BV1TaqYBcEJc'
MEDIA = {'format': {'duration': '20', 'format_name': 'mov,mp4,m4a,3gp,3g2,mj2'},
         'streams': [{'codec_type': 'video', 'codec_name': 'h264', 'width': 1280, 'height': 720},
                     {'codec_type': 'audio', 'codec_name': 'aac', 'channels': 2}]}
SUBTITLE = '1\n00:00:00,000 --> 00:00:03,000\n合成字幕，用于改名校验。\n'


class InlineRenameTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix='vp-inline-rename-')
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.patch_root = patch.object(library, 'LIBRARY_ROOT', self.root / 'library')
        self.patch_root.start()
        self.addCleanup(self.patch_root.stop)
        project = library.ensure({'platform': 'bilibili', 'video_id': VIDEO_ID,
                                  'source_url': f'https://www.bilibili.com/video/{VIDEO_ID}?p=1',
                                  'title': '完全虚构的测试课程',
                                  'episodes': [{'part': 1, 'title': '合成第一讲', 'duration': 20}]})
        self.pid = project['id']
        source = self.root / 'fixture.mp4'
        source.write_bytes(b'fake-video-' * 24)
        self.resource = library.import_file(project['id'], source, 'videos/original.mp4', 'video',
                                            {'media': MEDIA, 'selected_quality': {'id': '720p', 'label': '720p', 'width': 1280, 'height': 720}})
        subtitle = self.root / 'fixture.srt'
        subtitle.write_text(SUBTITLE, encoding='utf-8')
        self.subtitle = library.import_file(project['id'], subtitle, 'subtitles/fixture.srt', 'subtitle')
        self.jobs = []

    def tearDown(self):
        for identifier in self.jobs:
            naming.jobs.pop(identifier, None)

    def rename(self, title, expect=202):
        from fastapi.testclient import TestClient
        from video_parser.web.app import app
        client = TestClient(app, base_url='http://127.0.0.1:15001', client=('127.0.0.1', 1234))
        try:
            response = client.post(f'/api/workspace/projects/{self.pid}/rename', json={'title': title})
            self.assertEqual(response.status_code, expect, response.text)
            if expect != 202:
                return response
            identifier = response.json()['id']
            self.jobs.append(identifier)
            deadline = time.monotonic() + 10
            while time.monotonic() < deadline:
                current = client.get('/api/workspace/naming/tasks/' + identifier)
                self.assertEqual(current.status_code, 200)
                if current.json()['state'] not in task_runtime.ACTIVE:
                    self.assertEqual(current.json()['state'], 'completed', current.text)
                    return current.json()
                time.sleep(.02)
            self.fail('Inline rename did not finish')
        finally:
            client.close()

    def test_inline_rename_moves_the_files_and_can_rename_back(self):
        video = library.resource(self.pid, self.resource)
        subtitle = library.resource(self.pid, self.subtitle)
        self.assertEqual(video.name, 'original.mp4')

        job = self.rename('全新标题')
        self.assertEqual(job['operation'], 'apply')
        renamed_video = library.resource(self.pid, self.resource)
        self.assertEqual(renamed_video.name, '全新标题.mp4')
        self.assertNotEqual(library.resource(self.pid, self.subtitle), subtitle)
        # The naming flow writes the episode title; the course/source title stays for provenance.
        self.assertEqual(library.get(self.pid)['episode_title'], '全新标题')
        self.assertEqual(library.public(library.get(self.pid))['resources'][0]['filename'], '全新标题.mp4')

        self.rename('还原标题')
        self.assertEqual(library.resource(self.pid, self.resource).name, '还原标题.mp4')
        self.assertEqual(library.resource(self.pid, self.resource).parent, video.parent)

    def test_unsafe_or_unchanged_titles_never_touch_the_files(self):
        for title in ('有/斜杠', 'CON', '结尾句点.'):
            with self.subTest(title=title):
                self.rename(title, expect=400)
                self.assertEqual(library.resource(self.pid, self.resource).name, 'original.mp4')
        missing = self.rename('', expect=422)
        self.assertEqual(missing.status_code, 422)


if __name__ == '__main__':
    unittest.main(verbosity=2)
