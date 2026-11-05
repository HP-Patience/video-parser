"""All-video overview must expose archive, subtitle and AI-material status in one list."""
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from video_parser.services import library
from video_parser.web import routes


class ProjectOverviewTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.addCleanup(patch.stopall)
        patch.object(library, 'LIBRARY_ROOT', self.root / 'library').start()

    def project(self, identifier):
        return library.ensure({'platform': 'YouTube', 'video_id': identifier, 'title': identifier,
                               'source_url': f'https://www.youtube.com/watch?v={identifier}'})

    def test_projects_include_subtitle_and_material_inventory(self):
        project = self.project('abcdefghijk')
        video = self.root / 'video.mp4'
        video.write_bytes(b'video')
        library.import_file(project['id'], video, 'video/video.mp4', 'video')
        subtitle = self.root / 'subtitle.srt'
        subtitle.write_text('1\n00:00:00,000 --> 00:00:01,000\n字幕', encoding='utf-8')
        library.import_file(project['id'], subtitle, 'subtitles/original.srt', 'subtitle', {'language': 'zh'})
        library.save_text(project['id'], 'analysis', '# 摘要', {'template': 'summary', 'created_at': '2026-01-01T00:00:00+00:00'})
        library.save_text(project['id'], 'analysis', '# 笔记', {'template': 'notes', 'created_at': '2026-01-02T00:00:00+00:00'})
        empty = self.project('lmnopqrstuv')

        projects = {row['id']: row for row in routes.list_projects()['projects']}
        row = projects[project['id']]
        self.assertTrue(row['archived'])
        self.assertTrue(row['has_subtitles'])
        self.assertEqual(row['subtitle_count'], 1)
        self.assertEqual(row['analysis_count'], 2)
        self.assertEqual([item['name'] for item in row['materials']], ['学习笔记', '内容摘要'])
        self.assertEqual({item['template'] for item in row['materials']}, {'summary', 'notes'})
        self.assertFalse(projects[empty['id']]['archived'])
        self.assertFalse(projects[empty['id']]['has_subtitles'])
        self.assertEqual(projects[empty['id']]['materials'], [])


if __name__ == '__main__':
    unittest.main()