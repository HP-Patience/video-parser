"""Cloud placeholders must not look like broken local files during archive audits."""
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from video_parser.services import archive_health, library


class CloudArchiveHealthTests(unittest.TestCase):
    def test_pending_cloud_image_is_not_missing_but_real_missing_file_is(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            directory = root / 'project'
            directory.mkdir()
            project = {'id': 'a' * 32, 'title': 'Cloud project', 'platform': 'cloud',
                       'directory': directory, 'resources': {
                           'image/remote.png': {'kind': 'image', 'size_bytes': 20, 'cloud_missing': True},
                           'subtitle/lost.srt': {'kind': 'subtitle', 'size_bytes': 20},
                       }}
            with patch.object(library, 'LIBRARY_ROOT', root):
                row, missing, changed, _, unsafe, registered = archive_health._scan(project)
            self.assertEqual([item['resource'] for item in missing], ['subtitle/lost.srt'])
            self.assertEqual(row['missing'], 1)
            self.assertEqual(changed, [])
            self.assertEqual(unsafe, [])
            self.assertFalse(registered['image/remote.png']['present'])

    def test_cloud_flag_does_not_hide_corrupt_present_cache(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            directory = root / 'project'
            directory.mkdir()
            (directory / 'image.png').write_bytes(b'bad')
            project = {'id': 'b' * 32, 'directory': directory, 'resources': {
                'image.png': {'kind': 'image', 'size_bytes': 20, 'cloud_missing': True},
            }}
            with patch.object(library, 'LIBRARY_ROOT', root):
                _, missing, changed, _, _, _ = archive_health._scan(project)
            self.assertEqual(missing, [])
            self.assertEqual([item['resource'] for item in changed], ['image.png'])


if __name__ == '__main__':
    unittest.main()
