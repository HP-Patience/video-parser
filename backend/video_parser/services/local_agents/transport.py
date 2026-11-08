"""Owned UTF-8 stdio transports; no shell interpolation, credentials or raw CLI logs in API errors."""
import json
import os
from pathlib import Path
from queue import Queue, Empty, Full
import shutil
import signal
import subprocess
from threading import Event, Lock, Thread
import time

from video_parser.services import task_runtime

MAX_LINE = 16 * 1024 * 1024
MAX_INPUT = 32 * 1024 * 1024
PACKAGES = {'claude': '@anthropic-ai/claude-code', 'codex': '@openai/codex', 'opencode': 'opencode-ai',
            'dsh': '@deepseek-ai/dsh'}
NAMES = {'claude': 'Claude Code', 'codex': 'Codex', 'opencode': 'OpenCode', 'dsh': 'DeepSeek Harness'}


class AgentError(RuntimeError):
    code = 'local_agent_error'

    def __init__(self, message='本地 agent 调用失败，请检查本机登录、模型及版本；不会自动重试。'):
        super().__init__(message)
        self.user_message = message


class RpcError(AgentError):
    def __init__(self, code, detail=''):
        text = str(detail).lower()
        self.diagnostic_code = 'unknown'
        message = None
        if ('image' in text or 'vision' in text) and any(word in text for word in
                ('not support', 'unsupported', 'does not declare')):
            self.diagnostic_code = 'image_unsupported'
            message = '这个本地 agent 不支持图片输入，请选择支持画面的模型。'
        elif any(word in text for word in ('insufficient', 'credit', 'balance', 'billing', 'quota', 'usage limit', 'usagelimitexceeded')):
            self.diagnostic_code = 'quota'
            message = '本地 agent 的模型服务额度不足，请在原 agent 中检查账户或选择可用模型。'
        elif code in (401, 403) or any(word in text for word in ('unauthorized', 'authentication', 'not logged in', 'please run /login', 'api key', 'api_key', '401', '403')):
            self.diagnostic_code = 'authentication'
            message = '本地 agent 的模型服务未授权，请在原 agent 中检查登录和模型权限。'
        elif code == 404 or any(word in text for word in ('model not found', 'modelnotfound', 'unknown model')):
            self.diagnostic_code = 'model_not_found'
            message = '本地 agent 的服务找不到所选模型，请刷新或选择其他已配置模型。'
        elif any(word in text for word in ('context length', 'context window', 'contextwindowexceeded', 'input too long', 'too many tokens')):
            self.diagnostic_code = 'context_limit'
            message = '完整课程上下文超出所选模型的容量；未静默截断，请选择更大容量的模型。'
        elif any(word in text for word in ('rate limit', '429')):
            self.diagnostic_code = 'rate_limit'
            message = '本地 agent 的模型服务限流，请稍后手动重试。'
        elif any(word in text for word in ('fetch failed', 'econnrefused', 'econnreset', 'server overloaded', 'serveroverloaded',
                                          'stream disconnected', 'streamdisconnected', 'internal server error',
                                          'internalservererror', '502', '503', '504')):
            self.diagnostic_code = 'upstream_unavailable'
            message = '本地 agent 的上游模型服务暂不可用；没有自动切换模型或重发。'
        super().__init__(message or '本地 agent 调用失败，请检查本机登录、模型及版本；不会自动重试。')
        if self.diagnostic_code != 'unknown':
            # The class-level code hides the cause in task logs; the diagnosis itself is safe to expose.
            self.code = self.diagnostic_code
        self.rpc_code = code
        # Used only to classify a failed metadata/session operation. Never returned or logged.
        self.detail = str(detail)

    def missing_session(self):
        return self.rpc_code in (-32000, -32001, -32602, 404) and any(
            word in self.detail.lower() for word in ('not found', 'unknown session', 'unknown thread', 'expired', 'does not exist'))


def upstream_failure(value):
    """An in-band agent failure (stream error or failed turn) diagnosed like an RPC error.

    The raw payload is only classified; it never reaches the user or the logs.
    """
    return RpcError(None, json.dumps(value, ensure_ascii=False, default=str))


def _dsh_desktop_shim():
    """Locate the Desktop install's own CLI shim without guessing install paths."""
    if os.name != 'nt':
        return None
    try:
        import winreg
    except ImportError:
        return None
    for hive, view in ((winreg.HKEY_CURRENT_USER, 0), (winreg.HKEY_LOCAL_MACHINE, winreg.KEY_WOW64_64KEY),
                       (winreg.HKEY_LOCAL_MACHINE, winreg.KEY_WOW64_32KEY)):
        try:
            root = winreg.OpenKey(hive, r'SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall', 0,
                                  winreg.KEY_READ | view)
        except OSError:
            continue
        try:
            for index in range(min(winreg.QueryInfoKey(root)[0], 512)):
                try:
                    with winreg.OpenKey(root, winreg.EnumKey(root, index)) as item:
                        name, _ = winreg.QueryValueEx(item, 'DisplayName')
                        if not isinstance(name, str) or 'DeepSeek Harness' not in name:
                            continue
                        location, _ = winreg.QueryValueEx(item, 'InstallLocation')
                        if not isinstance(location, str) or not location:
                            continue
                        shim = Path(location) / 'resources' / 'runtime' / 'cli' / 'bin' / 'dsh.cmd'
                        if shim.is_file() and not shim.is_symlink():
                            return shim
                except OSError:
                    continue
        finally:
            winreg.CloseKey(root)
    return None


def _dsh_command():
    """The CLI shim owns its Node/Electron runtime; never spell that launch line here."""
    located = shutil.which('dsh')
    if located:
        path = Path(located).resolve()
        if os.name == 'nt' and path.suffix.lower() in ('.cmd', '.bat'):
            return [os.environ.get('COMSPEC', 'cmd.exe'), '/d', '/c', str(path)]
        return [str(path)]
    shim = _dsh_desktop_shim()
    if shim is None:
        raise AgentError('未找到本地 DeepSeek Harness，请先安装桌面版或命令行版并完成登录/模型设置。')
    if os.name == 'nt':
        return [os.environ.get('COMSPEC', 'cmd.exe'), '/d', '/c', str(shim)]
    return [str(shim)]


def executable(engine):
    """Resolve the installed program; npm package bin metadata handles JS AND native Windows shims."""
    if engine not in PACKAGES:
        raise ValueError('未知本地 agent。')
    if engine == 'dsh':
        return _dsh_command()
    located = shutil.which(engine)
    if not located:
        for directory in (Path.home() / '.local/bin', Path.home() / 'AppData/Roaming/npm'):
            candidate = directory / (engine + ('.exe' if os.name == 'nt' else ''))
            if candidate.is_file():
                located = str(candidate)
                break
    if not located:
        raise AgentError('未找到本地 agent，请先在本机安装并登录，再刷新模型列表。')
    path = Path(located).resolve()
    if os.name != 'nt' or path.suffix.lower() == '.exe':
        return [str(path)]
    package = path.parent / 'node_modules' / PACKAGES[engine]
    manifest = package / 'package.json'
    try:
        if manifest.is_file() and manifest.stat().st_size < 128000:
            metadata = json.loads(manifest.read_text(encoding='utf-8'))
            entry = metadata.get('bin', {})
            entry = entry.get(engine) if isinstance(entry, dict) else entry
            if isinstance(entry, str):
                program = (package / entry).resolve()
                if program.is_relative_to(package.resolve()) and program.is_file():
                    if program.suffix.lower() == '.exe':
                        return [str(program)]
                    node = shutil.which('node')
                    if node and program.suffix.lower() in ('.js', '.mjs', '.cjs'):
                        return [node, str(program)]
    except (OSError, ValueError, TypeError):
        pass
    raise AgentError('无法安全启动这个 Windows agent 入口，请检查安装；不会用 shell 拼接问题。')


def available(engine):
    try:
        executable(engine)
        return True
    except AgentError:
        return False


class Wire:
    """One caller, one owned process. Drain stderr without exposing local configuration or tokens."""
    def __init__(self, engine, cwd, arguments, env=None):
        self.engine = engine
        self.closed = Event()
        self.events = Queue(maxsize=512)
        self.lock = Lock()
        self.sequence = 0
        self.on_event = lambda message: None
        self.on_request = self.reject_request
        options = {'creationflags': subprocess.CREATE_NO_WINDOW | subprocess.CREATE_NEW_PROCESS_GROUP} if os.name == 'nt' else {'start_new_session': True}
        try:
            self.process = subprocess.Popen(executable(engine) + list(arguments), cwd=str(cwd),
                env={**os.environ, **(env or {})}, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                stderr=subprocess.PIPE, **options)
        except OSError:
            raise AgentError('无法启动本地 agent，请检查安装及目录权限。') from None
        self.reader = Thread(target=self._read, daemon=True)
        self.stderr_reader = Thread(target=self._drain_errors, daemon=True)
        self.reader.start()
        self.stderr_reader.start()

    def _put(self, message):
        while not self.closed.is_set():
            try:
                self.events.put(message, timeout=.1)
                return
            except Full:
                continue

    def _read(self):
        try:
            for raw in iter(lambda: self.process.stdout.readline(MAX_LINE + 1), b''):
                if len(raw) > MAX_LINE:
                    self._put({'_wire_error': 'oversize'})
                    break
                try:
                    message = json.loads(raw.decode('utf-8'))
                    if isinstance(message, dict):
                        self._put(message)
                except (ValueError, UnicodeError):
                    # Some installations print a startup banner; it is not a protocol event.
                    continue
        except (OSError, ValueError):
            pass
        finally:
            self._put({'_wire_error': 'closed'})

    def _drain_errors(self):
        try:
            while self.process.stderr.read(8192):
                pass
        except (OSError, ValueError):
            pass

    def send(self, message):
        data = (json.dumps(message, ensure_ascii=False) + '\n').encode('utf-8')
        if len(data) > MAX_INPUT:
            raise AgentError('课程上下文超出本地 agent 的传输上限，未静默截断或发送。')
        try:
            with self.lock:
                self.process.stdin.write(data)
                self.process.stdin.flush()
        except (OSError, ValueError):
            raise AgentError('本地 agent 已断开，未自动重发问题；请检查登录和任务状态。') from None

    def read(self, deadline):
        while True:
            task_runtime.checkpoint()
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise AgentError('等待本地 agent 超时，已停止本次调用；不会自动重发。')
            try:
                message = self.events.get(timeout=min(.2, remaining))
            except Empty:
                continue
            if '_wire_error' in message:
                raise AgentError('本地 agent 的结构化连接已中断，请检查本机登录和版本。')
            return message

    @staticmethod
    def reject_request(method, params):
        raise RpcError(-32601, 'Tool is not permitted in this learning client')

    def dispatch(self, message):
        if message.get('method') and 'id' in message:
            try:
                result = self.on_request(message['method'], message.get('params', {}))
                self.send({'jsonrpc': '2.0', 'id': message['id'], 'result': result})
            except AgentError:
                self.send({'jsonrpc': '2.0', 'id': message['id'],
                           'error': {'code': -32601, 'message': 'Operation not permitted in read-only learning mode'}})
        else:
            self.on_event(message)

    def request(self, method, params, timeout=30):
        self.sequence += 1
        identifier = self.sequence
        self.send({'jsonrpc': '2.0', 'id': identifier, 'method': method, 'params': params})
        deadline = time.monotonic() + timeout
        while True:
            message = self.read(deadline)
            if message.get('id') == identifier and not message.get('method'):
                if 'error' in message:
                    error = message['error']
                    raise RpcError(error.get('code'), error.get('message', ''))
                return message.get('result', {})
            self.dispatch(message)

    def notify(self, method, params=None):
        message = {'jsonrpc': '2.0', 'method': method}
        if params is not None:
            message['params'] = params
        self.send(message)

    def close(self):
        if self.closed.is_set():
            return
        self.closed.set()
        try:
            if self.process.poll() is None:
                # EOF lets a successful agent flush its native conversation journal before exit.
                try:
                    self.process.stdin.close()
                    self.process.wait(timeout=2)
                except (OSError, ValueError, subprocess.TimeoutExpired):
                    if self.process.poll() is None:
                        if os.name == 'nt':
                            task_runtime.stop_process(self.process)
                        else:
                            os.killpg(self.process.pid, signal.SIGKILL)
                            self.process.wait(timeout=5)
        except (OSError, ProcessLookupError, subprocess.TimeoutExpired):
            if self.process.poll() is None:
                self.process.kill()
                self.process.wait(timeout=5)
        finally:
            for stream in (self.process.stdin, self.process.stdout, self.process.stderr):
                try:
                    stream.close()
                except (OSError, ValueError):
                    pass
            self.reader.join(timeout=1)
            self.stderr_reader.join(timeout=1)

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()
