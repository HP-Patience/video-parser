"""Offline contract for the machine-wide shared video library setting."""
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from video_parser.services import library


class LibrarySettingsTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.config = self.root / 'shared' / 'library.json'
        self.default = self.root / 'shared' / 'Library'
        self.legacy = self.root / 'legacy'
        self.patches = [
            patch.object(library, 'CONFIG_PATH', self.config),
            patch.object(library, 'DEFAULT_LIBRARY_ROOT', self.default),
            patch.object(library, 'LEGACY_LIBRARY_ROOT', self.legacy),
            patch.object(library, 'LIBRARY_ROOT', self.default),
            patch.object(library, '_LIBRARY_SOURCE', 'default'),
            patch.object(library, '_LIBRARY_SIGNATURE', 'default:' + str(self.default)),
            patch.dict(os.environ, {'VIDEO_LIBRARY_ROOT': ''}),
        ]
        for item in self.patches:
            item.start()
            self.addCleanup(item.stop)

    def test_set_library_root_persists_and_updates_process(self):
        target = self.root / 'video-library'
        result = library.set_library_root(target)
        self.assertEqual(result, target.resolve())
        self.assertEqual(library.LIBRARY_ROOT, target.resolve())
        self.assertEqual(library.library_root_source(), 'settings')
        self.assertEqual(json.loads(self.config.read_text(encoding='utf-8'))['library_root'], str(target.resolve()))
        self.assertTrue(target.is_dir())
        self.assertEqual(list(target.glob('.video-parser-write-*')), [])

    def test_refresh_reads_config_written_by_another_process(self):
        first = self.root / 'first'
        second = self.root / 'second'
        library.set_library_root(first)
        self.config.write_text(json.dumps({'version': 1, 'library_root': str(second)}), encoding='utf-8')
        self.assertEqual(library.refresh_library_root(force=True), second.resolve())

    def test_environment_override_cannot_be_changed_in_app(self):
        with patch.dict(os.environ, {'VIDEO_LIBRARY_ROOT': str(self.root / 'env-library')}):
            with self.assertRaises(RuntimeError):
                library.set_library_root(self.root / 'other')

    def test_relative_path_is_rejected(self):
        with self.assertRaises(ValueError):
            library.set_library_root('relative-library')

    def test_legacy_library_is_used_before_empty_default(self):
        project = self.legacy / 'bilibili' / 'video' / 'project.json'
        project.parent.mkdir(parents=True)
        project.write_text('{}', encoding='utf-8')
        root, source, _ = library.resolve_library_root()
        self.assertEqual(root, self.legacy.resolve())
        self.assertEqual(source, 'legacy')


if __name__ == '__main__':
    unittest.main()