"""Windows launcher contracts plus real PowerShell parsing/failure checks."""
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import shutil
import subprocess
import tempfile
import threading
import unittest

ROOT = Path(__file__).resolve().parents[2]


class WebLauncherTests(unittest.TestCase):
    def test_double_click_wrapper_uses_quoted_project_path_and_keeps_failures_visible(self):
        path = ROOT / 'start-web.cmd'
        self.assertTrue(path.is_file(), '尚无双击启动入口')
        text = path.read_text(encoding='utf-8')
        self.assertIn('cd /d "%~dp0"', text)
        self.assertIn('-File "%~dp0scripts\\start-web.ps1" -OpenBrowser', text)
        self.assertIn('-ExecutionPolicy Bypass', text)
        self.assertIn('pause', text)
        self.assertIn('exit /b', text)

    def test_existing_powershell_script_reuses_dependencies_and_opens_only_when_healthy(self):
        text = (ROOT / 'scripts/start-web.ps1').read_text(encoding='utf-8')
        self.assertIn('[switch]$OpenBrowser', text)
        self.assertIn("node_modules\\.bin\\vite.cmd", text)
        self.assertIn('/health', text)
        self.assertIn("'healthy'", text)
        self.assertIn('Start-Process', text)
        self.assertIn("$env:VIDEO_PARSER_OPEN_BROWSER = '1'", text)
        self.assertIn('$env:VIDEO_PARSER_OPEN_URL = $url', text)
        self.assertIn('--host 127.0.0.1', text)
        self.assertNotIn('Set-ExecutionPolicy', text)

    @unittest.skipUnless(shutil.which('powershell.exe'), 'Windows PowerShell required')
    def test_powershell_syntax_and_missing_environment_failure(self):
        script = """$tokens=$null; $errors=$null;
[System.Management.Automation.Language.Parser]::ParseFile($args[0],[ref]$tokens,[ref]$errors) | Out-Null;
if ($errors.Count) { $errors | ForEach-Object { Write-Error $_ }; exit 1 }
"""
        with tempfile.TemporaryDirectory(prefix='launcher-中文 空格-') as directory:
            root = Path(directory)
            check = root / 'parse.ps1'
            check.write_text(script, encoding='utf-8-sig')
            checked = subprocess.run(['powershell.exe', '-NoProfile', '-File', str(check), str(ROOT / 'scripts/start-web.ps1')], capture_output=True)
            self.assertEqual(checked.returncode, 0, checked.stderr)
            launcher = root / 'start-web.ps1'
            shutil.copyfile(ROOT / 'scripts/start-web.ps1', launcher)
            failed = subprocess.run(['powershell.exe', '-NoProfile', '-File', str(launcher)], capture_output=True)
            self.assertNotEqual(failed.returncode, 0)
            self.assertIn(b'Project .venv not found', failed.stderr)

    @unittest.skipUnless(shutil.which('powershell.exe'), 'Windows PowerShell required')
    def test_existing_workspace_is_reused_but_unrelated_port_is_not_touched(self):
        from video_parser.core.paths import instance_id
        state = {'workspace': True, 'instance': instance_id()}
        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                data = {'status': 'healthy', 'instance': state['instance']} if self.path == '/health' else (
                    {'transcription_ready': True} if state['workspace'] else {'other': True})
                payload = json.dumps(data).encode()
                self.send_response(200)
                self.send_header('Content-Type', 'application/json')
                self.send_header('Content-Length', str(len(payload)))
                self.end_headers()
                self.wfile.write(payload)
            def log_message(self, *args): pass
        server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            args = ['powershell.exe', '-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', str(ROOT / 'scripts/start-web.ps1'),
                    '-Port', str(server.server_port)]
            result = subprocess.run(args, capture_output=True, timeout=10)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn(b'already running', result.stdout)
            state['instance'] = 'different-project'
            result = subprocess.run(args, capture_output=True, timeout=10)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn(b'occupied by another service', result.stderr)
            state['workspace'] = False
            result = subprocess.run(args, capture_output=True, timeout=10)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn(b'occupied by another service', result.stderr)
            self.assertTrue(thread.is_alive())
        finally:
            server.shutdown()
            server.server_close()
            thread.join(5)



if __name__ == '__main__':
    unittest.main()
