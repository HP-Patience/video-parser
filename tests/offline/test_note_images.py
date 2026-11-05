"""Pasted images: stored beside the project, referenced by name only."""
import base64
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from fastapi import FastAPI
from fastapi.testclient import TestClient

from video_parser.services import attachments, conversations, library, study
from video_parser.web.study_routes import router

PNG = ('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg==')
DATA_URL = 'data:image/png;base64,' + PNG


class NoteImageTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory(); self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        mock = patch.object(library, 'LIBRARY_ROOT', self.root / 'library'); mock.start(); self.addCleanup(mock.stop)
        project = library.ensure({'platform': 'bilibili', 'video_id': 'BV1TaqYBcEJc', 'title': '学习',
            'source_url': 'https://www.bilibili.com/video/BV1TaqYBcEJc?p=1'})
        source = self.root / 'clip'; source.write_bytes(b'isolated media fixture')
        self.resource = 'video/test.mp4'
        library.import_file(project['id'], source, self.resource, 'video', {'media': {'format': {'duration': '20'}}})
        self.identifier = project['id']; self.note = 'a' * 32
        app = FastAPI(); app.include_router(router)
        self.client = TestClient(app, base_url='http://127.0.0.1:15001', client=('127.0.0.1', 12345))
        self.base = f'/api/workspace/projects/{self.identifier}'

    def test_pasted_image_uploads_and_serves_back(self):
        stored = attachments.save(self.identifier, DATA_URL)
        self.assertTrue(stored['name'].endswith('.png'))
        self.assertEqual(stored['size'], len(base64.b64decode(PNG)))
        response = self.client.get(f"{self.base}/attachments/{stored['name']}")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.headers['content-type'], 'image/png')
        self.assertEqual(response.content, base64.b64decode(PNG))

    def test_identical_bytes_share_one_file(self):
        first = attachments.save(self.identifier, DATA_URL)
        second = attachments.save(self.identifier, DATA_URL)
        self.assertEqual(first['name'], second['name'])
        folder = library.contained(library.get(self.identifier)['directory'], 'attachments')
        self.assertEqual(len(list(folder.iterdir())), 1)

    def test_note_keeps_images_and_allows_image_only_notes(self):
        image = attachments.save(self.identifier, DATA_URL)
        saved = study.save_note(self.identifier, self.resource, self.note, 5, '', 0, [image])
        self.assertEqual(saved['text'], '')
        self.assertEqual([item['name'] for item in saved['images']], [image['name']])
        self.assertEqual(study.notes(self.identifier, self.resource)[0]['images'][0]['name'], image['name'])
        # An image-only note survives a reload of the sidecar.
        self.assertEqual(study.load(self.identifier, self.resource)['notes'][self.note]['images'][0]['name'], image['name'])

    def test_empty_note_without_images_is_rejected(self):
        with self.assertRaises(ValueError):
            study.save_note(self.identifier, self.resource, self.note, 5, '   ', 0, [])

    def test_unknown_or_malformed_references_are_rejected(self):
        for bad in ([{'name': 'f' * 64 + '.png'}], [{'name': '../../project.json'}], [{'name': 'x'}], 'not-a-list'):
            with self.assertRaises(ValueError):
                study.save_note(self.identifier, self.resource, self.note, 5, '文字', 0, bad)

    def test_too_many_images_and_oversized_payloads_are_rejected(self):
        image = attachments.save(self.identifier, DATA_URL)
        with self.assertRaises(ValueError):
            study.save_note(self.identifier, self.resource, self.note, 5, '文字', 0, [image] * (attachments.MAX_COUNT + 1))
        with self.assertRaises(ValueError):
            attachments.save(self.identifier, 'data:image/svg+xml;base64,' + PNG)
        with self.assertRaises(ValueError):
            attachments.save(self.identifier, 'data:image/png;base64,not base64!!')

    def test_notes_written_before_images_still_read(self):
        _, path, fingerprint, _ = study.context(self.identifier, self.resource)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({'version': 1, 'project_id': self.identifier, 'resource': self.resource,
            'fingerprint': fingerprint, 'progress': {'position': 0, 'duration': 20.0, 'revision': 0},
            'notes': {self.note: {'id': self.note, 'time': 1, 'text': '旧笔记', 'revision': 1}}}), encoding='utf-8')
        rows = study.notes(self.identifier, self.resource)
        self.assertEqual(rows[0]['text'], '旧笔记')
        self.assertNotIn('images', rows[0])

    def test_markdown_export_links_the_stored_image(self):
        image = attachments.save(self.identifier, DATA_URL)
        study.save_note(self.identifier, self.resource, self.note, 5, '看图', 0, [image])
        markdown, _ = study.export_markdown(self.identifier, self.resource)
        self.assertIn(f"![图片](attachments/{image['name']})", markdown)

    def test_conversation_turns_pasted_images_into_data_urls(self):
        image = attachments.save(self.identifier, DATA_URL)
        names, parts = conversations._pasted_images(self.identifier, [image])
        self.assertEqual(names, [image['name']])
        self.assertEqual(parts[0]['type'], 'image_url')
        self.assertEqual(parts[0]['image_url']['url'], DATA_URL)


if __name__ == '__main__':
    unittest.main()
