"""Run the generated Windows updater on disposable packages, never a real installation."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import patch
import zipfile

from video_parser.services import update


@unittest.skipUnless(os.name == 'nt' and shutil.which('powershell.exe'), 'Windows PowerShell required')
class PortableHelperTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = tempfile.TemporaryDirectory(prefix='vp helper fixtures ')
        cls.base = Path(cls.temporary.name)
        cls.executable = cls.base / 'desktop-fixture.exe'
        compiler = cls.base / 'compile.ps1'
        compiler.write_text(r'''param([string]$OutputFile)
Add-Type -Language CSharp -OutputType ConsoleApplication -OutputAssembly $OutputFile -TypeDefinition @'
using System;
using System.IO;
public class DesktopFixture {
    public static int Main(string[] args) {
        string root = Directory.GetParent(AppDomain.CurrentDomain.BaseDirectory.TrimEnd('\\')).FullName;
        string log = Environment.GetEnvironmentVariable("VP_UPDATE_FIXTURE_LOG");
        if (!String.IsNullOrEmpty(log)) File.AppendAllText(log, root + "|" + String.Join(" ", args) + Environment.NewLine);
        return Array.IndexOf(args, "--check") >= 0 && File.Exists(Path.Combine(root, "backend", "video_parser", "bad-build")) ? 9 : 0;
    }
}
'@
''', encoding='utf-8-sig')
        result = subprocess.run(['powershell.exe', '-NoProfile', '-NonInteractive', '-File', str(compiler),
                                 '-OutputFile', str(cls.executable)], capture_output=True, text=True, timeout=30)
        if result.returncode or not cls.executable.is_file():
            raise AssertionError(result.stdout + result.stderr)

    @classmethod
    def tearDownClass(cls):
        cls.temporary.cleanup()

    def package(self, root, version):
        (root / 'python').mkdir(parents=True)
        (root / 'backend' / 'video_parser' / 'core').mkdir(parents=True)
        (root / 'configs').mkdir()
        (root / 'pyproject.toml').write_text('[project]\nversion = "' + version + '"\n', encoding='utf-8')
        (root / 'configs' / 'business_config.json').write_text('{}', encoding='utf-8')
        for name in ('python.exe', 'pythonw.exe'):
            shutil.copyfile(self.executable, root / 'python' / name)
        for name in ('__init__.py', 'core/__init__.py'):
            (root / 'backend' / 'video_parser' / name).write_text('', encoding='utf-8')
        (root / 'backend' / 'video_parser' / 'core' / 'paths.py').write_text(
            'from pathlib import Path\nROOT = Path(__file__).resolve().parents[3]\n'
            'def canonical(path):\n    return Path(path).resolve()\n', encoding='utf-8')
        # A released package can predate the updater: the runner must use its frozen transaction.
        (root / 'backend' / 'video_parser' / 'services').mkdir()
        (root / 'backend' / 'video_parser' / 'services' / '__init__.py').write_text('', encoding='utf-8')
        (root / 'backend' / 'video_parser' / 'services' / 'update.py').write_text(
            'raise RuntimeError("must not import updater from downloaded package")\n', encoding='utf-8')

    def exercise(self, bad_build=False):
        with tempfile.TemporaryDirectory(prefix='vp update 空格 ') as temporary:
            folder = Path(temporary)
            root, source = (folder / name for name in ('Old App', 'New App'))
            stage = root / '.local' / 'work' / 'updates' / ('.stage-' + 'a' * 32)
            self.package(root, '1.0.0')
            self.package(stage, '2.0.0')
            self.package(source, '2.0.0')
            if bad_build:
                (source / 'backend' / 'video_parser' / 'bad-build').write_text('bad', encoding='utf-8')
            sentinels = {'.local/ai/settings.json': 'private-config', 'outputs/keep.txt': 'media',
                         '.env': 'private-env', 'runtimes/whisper/keep.txt': 'model'}
            for name, value in sentinels.items():
                path = root / name
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(value, encoding='utf-8')
            archive = folder / 'new package.zip'
            with zipfile.ZipFile(archive, 'w') as bundle:
                for path in source.rglob('*'):
                    if path.is_file():
                        bundle.write(path, 'VideoParser/' + path.relative_to(source).as_posix())
                bundle.writestr('VideoParser/THIRD-PARTY-NOTICES.txt', 'fixture')
            workspace = root / '.local' / 'work' / 'updates'
            log = workspace / 'update.log'
            restart_log = folder / 'desktop-invocations.txt'
            with patch.object(update, 'UPDATE_DIR', workspace), patch.object(update, 'RESULT_FILE', workspace / 'update-result.json'):
                helper, runner = update._write_portable_helper(archive, root, stage, log)
                environment = os.environ.copy()
                environment['VP_UPDATE_FIXTURE_LOG'] = str(restart_log)
                result = subprocess.run(['powershell.exe', '-NoProfile', '-NonInteractive', '-ExecutionPolicy', 'Bypass',
                    '-File', str(helper), '-ParentId', '2147483647', '-Python', sys.executable, '-Runner', str(runner),
                    '-Archive', str(archive), '-InstallRoot', str(root), '-Version', '2.0.0',
                    '-StageRoot', str(stage), '-LogPath', str(log)], env=environment,
                    capture_output=True, text=True, timeout=60)
                expected_version = '1.0.0' if bad_build else '2.0.0'
                self.assertIn('"' + expected_version + '"', (root / 'pyproject.toml').read_text(encoding='utf-8'),
                              result.stdout + result.stderr + (log.read_text(encoding='utf-8-sig') if log.exists() else ''))
                self.assertFalse(stage.exists(), 'Stage runtime should be removed after helper exit')
                for name, value in sentinels.items():
                    self.assertEqual((root / name).read_text(encoding='utf-8'), value)
                deadline = time.monotonic() + 5
                while time.monotonic() < deadline:
                    calls = restart_log.read_text(encoding='utf-8-sig') if restart_log.exists() else ''
                    if '|-m video_parser.desktop\n' in calls.replace('\r\n', '\n'):
                        break
                    time.sleep(.05)
                self.assertIn('|-m video_parser.desktop\n', calls.replace('\r\n', '\n'), result.stdout + result.stderr)
                self.assertIn('--check', calls)
                time.sleep(.2)
                if bad_build:
                    saved = json.loads((workspace / 'update-result.json').read_text(encoding='utf-8-sig'))
                    self.assertEqual(saved['state'], 'failed')
                    with patch.dict(update._update_snapshot, {'state': 'idle', 'version': None,
                                    'downloaded_bytes': 0, 'total_bytes': None, 'error': None}, clear=True):
                        self.assertEqual(update.status()['state'], 'failed')

    def test_external_helper_updates_path_with_spaces_and_restarts(self):
        self.exercise()

    def test_external_helper_rolls_back_bad_startup_and_restarts_old_app(self):
        self.exercise(bad_build=True)


class UpdateRootTests(unittest.TestCase):
    def test_source_checkout_stays_unsupported_with_stale_install_registration(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / '.git').mkdir()
            with patch.object(update, '_installed_root', return_value=root):
                self.assertEqual(update.installation_kind(root, platform='nt'), 'source')


if __name__ == '__main__':
    unittest.main()
