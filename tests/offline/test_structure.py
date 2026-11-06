"""Offline contracts for the package migration and its public entrypoints."""
import ast
from importlib import resources
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from video_parser.core.paths import ROOT, FRONTEND_DIR, CACHE_DIR, LOG_DIR, project_root
from video_parser.services import videos
from video_parser import cli


class StructureTests(unittest.TestCase):
    def test_root_and_default_paths_stay_in_repository(self):
        self.assertEqual(ROOT, Path(__file__).resolve().parents[2])
        self.assertEqual(FRONTEND_DIR, ROOT / 'frontend' / 'dist')
        self.assertEqual(videos.ROOT, ROOT)
        from video_parser.services import transcription
        self.assertEqual(transcription.OUTPUT_ROOT, ROOT / 'outputs/transcriptions')
        self.assertEqual(transcription.UPLOAD_ROOT, CACHE_DIR / 'react-workspace/transcription-inputs')

    def test_consolidated_source_and_data_defaults(self):
        from video_parser.services import library, ai_startup
        self.assertTrue(Path(videos.__file__).is_relative_to(ROOT / 'backend'))
        resolved_root, source, _ = library.resolve_library_root()
        self.assertEqual(library.LIBRARY_ROOT.resolve(), resolved_root)
        self.assertIn(source, {'environment', 'settings', 'legacy', 'default'})
        self.assertEqual(CACHE_DIR, ROOT / '.local/work/cache')
        self.assertEqual(LOG_DIR, ROOT / '.local/work/logs')
        self.assertTrue(ai_startup.DEFAULTS['model_path'].startswith('runtimes/models/'))
        self.assertTrue(all(path.parent.parent != library.LIBRARY_ROOT / 'transcriptions'
                            for path in library.manifests()))

    def test_root_is_found_by_markers_not_fixed_parent_count(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / 'configs').mkdir()
            (root / 'configs/business_config.json').write_text('{}', encoding='utf-8')
            (root / 'pyproject.toml').write_text('', encoding='utf-8')
            nested = root / 'backend/video_parser/arbitrary/deeper/core'
            nested.mkdir(parents=True)
            self.assertEqual(project_root(nested / 'paths.py'), root.resolve())
            (root / 'pyproject.toml').unlink()
            with self.assertRaises(RuntimeError):
                project_root(nested / 'paths.py')

    def test_module_and_legacy_cli_work_from_another_directory(self):
        commands = [[sys.executable, '-m', 'video_parser'], [sys.executable, str(ROOT / 'cli.py')]]
        results = []
        with tempfile.TemporaryDirectory(prefix='video-parser-entry-') as directory:
            for command in commands:
                with self.subTest(command=command):
                    result = subprocess.run([*command, 'capabilities', '--json'], cwd=directory,
                                            capture_output=True, text=True, encoding='utf-8', timeout=30)
                    self.assertEqual(result.returncode, 0, result.stderr)
                    results.append(json.loads(result.stdout))
        self.assertEqual(results[0], results[1])
        self.assertEqual(len(results[0]['platforms']), 4)
        self.assertIn('YouTube', results[0]['platforms'])

    def test_web_and_services_do_not_depend_on_command_adapter(self):
        source = ROOT / 'backend/video_parser'
        for scope in ('web', 'services'):
            for path in (source / scope).rglob('*.py'):
                for node in ast.walk(ast.parse(path.read_text(encoding='utf-8'))):
                    names = [a.name for a in node.names] if isinstance(node, ast.Import) else []
                    if isinstance(node, ast.ImportFrom):
                        names += [node.module or '']
                        names += [(node.module or '') + '.' + a.name for a in node.names]
                    self.assertNotIn('cli', names, str(path))
                    self.assertNotIn('video_parser.cli', names, str(path))

    def test_shared_video_operations_do_not_import_argparse_or_fastapi(self):
        tree = ast.parse(Path(videos.__file__).read_text(encoding='utf-8'))
        for node in ast.walk(tree):
            names = [a.name for a in node.names] if isinstance(node, ast.Import) else []
            if isinstance(node, ast.ImportFrom):
                names.append(node.module or '')
            self.assertFalse(any(n.split('.')[0] in {'argparse', 'fastapi'} for n in names))
        request = videos.VideoRequest(command='capabilities')
        self.assertEqual(videos.execute(request), cli.execute(cli.build_parser().parse_args(['capabilities'])))

    def test_douyin_javascript_is_available_as_package_data(self):
        package = resources.files('video_parser.platforms.douyin_utils')
        for filename in ('a_bogus.js', 'x_bogus.js'):
            with self.subTest(filename=filename):
                self.assertGreater(len(package.joinpath(filename).read_bytes()), 100)
        from video_parser.platforms.douyin_utils.bogus_sign_utils import CommonUtils
        signer = CommonUtils()
        self.assertTrue(Path(signer.x_bogus_js_path).is_file())
        self.assertTrue(Path(signer.a_bogus_js_path).is_file())

    def test_compatibility_asgi_entry_uses_same_application(self):
        import api
        from video_parser.web.app import app
        self.assertIs(api.app, app)

    def test_legacy_business_modules_are_no_longer_at_root(self):
        for name in ('workspace.py', 'library_store.py', 'subtitles.py', 'transcription.py', 'qwen3vl.py', 'history_import.py'):
            self.assertFalse((ROOT / name).exists(), name)


if __name__ == '__main__':
    unittest.main()
