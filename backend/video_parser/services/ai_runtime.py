"""Idempotent local model launch from saved settings. Never executes browser commands."""
import os
import subprocess
from subprocess import Popen
from threading import RLock
import time
from uuid import uuid4

from video_parser.services import ai_settings, ai_startup

_lock = RLock()
_process = None
_identity = None
_started = 0.0
_log_path = None
LOG_ROOT = ai_startup.ROOT / '.local/ai/logs'


def _configuration(settings=None):
    settings = ai_settings.current() if settings is None else settings
    config = ai_startup.effective(settings)
    if settings.get('provider') != 'llamacpp' or settings.get('base_url', '').rstrip('/') != ai_startup.endpoint(config):
        raise ValueError('请先保存本机 llama.cpp 配置，接口地址须与启动端口一致。')
    token = settings.get('api_key')
    if not isinstance(token, str) or not token or len(token) > 500 or any(ch.isspace() for ch in token):
        raise ValueError('请先保存启动配置，程序会自动准备本机访问令牌。')
    ai_startup.arguments(config, settings.get('model', ''))
    return settings, config


def _signature(settings, config):
    return settings['model'], tuple(sorted((key, value) for key, value in config.items() if key != 'show_console'))


def _status(settings, config):
    alive = _process is not None and _process.poll() is None
    elapsed = max(0, int(time.monotonic() - _started)) if alive else 0
    result = {'owned': alive, 'elapsed_seconds': elapsed, 'model': settings['model'], 'base_url': ai_startup.endpoint(config)}
    if alive and _identity != _signature(settings, config):
        return {**result, 'state': 'occupied', 'message': '之前启动的模型仍在运行。请先停止之前的模型进程（显示窗口时可按 Ctrl+C），再启动新配置；不会自动停止或额外启动。'}
    if ai_startup.occupied(config['port']):
        if ai_startup.same_running_model(config['port'], settings['model'], settings['api_key'], timeout=1):
            return {**result, 'state': 'running', 'message': '本地模型已就绪，可以进行分析。'}
        if alive:
            message = '正在加载模型，请稍候；离开此页不会中断加载。'
            if elapsed > 300:
                message = f'模型加载较慢，请查看后台日志：{_log_path}' if _log_path else '模型加载较慢，请查看模型窗口中的输出；不会重复启动另一个进程。'
            return {**result, 'state': 'starting', 'message': message}
        return {**result, 'state': 'occupied', 'message': '端口正在使用，但模型未就绪或访问令牌不匹配。请检查现有模型窗口；不会停止其他服务。'}
    if alive:
        return {**result, 'state': 'starting', 'message': '启动请求已接收，正在加载模型；这可能需要几分钟。'}
    if _process is not None and _identity == _signature(settings, config):
        detail = f'后台运行日志：{_log_path}' if _log_path else '请查看模型窗口输出。'
        return {**result, 'state': 'failed', 'message': f'模型进程已退出（代码 {_process.returncode}）。请检查启动配置或显存后重试。{detail}'}
    return {**result, 'state': 'stopped', 'message': '本地模型未启动，点击启动即可；不需要先测试连接。'}


def status(settings=None):
    with _lock:
        try:
            settings, config = _configuration(settings)
        except ValueError as error:
            return {'state': 'unconfigured', 'message': str(error), 'owned': False, 'elapsed_seconds': 0}
        return _status(settings, config)


def start(settings=None):
    global _process, _identity, _started, _log_path
    with _lock:
        settings, config = _configuration(settings)
        previous = _status(settings, config)
        if previous['state'] in ('running', 'starting'):
            return previous
        if previous['state'] == 'occupied':
            raise BlockingIOError(previous['message'])
        checked = ai_startup.inspect(config, settings['model'])
        if not checked['ok']:
            raise ValueError('；'.join(row['label'] + '：' + row['message'] for row in checked['checks'] if not row['ok']))
        # Check again immediately before spawn. A service may have appeared during preflight.
        if ai_startup.occupied(config['port']):
            raise BlockingIOError('模型端口刚被占用，请刷新状态后重试；未停止任何进程。')
        environment = os.environ.copy()
        environment['LLAMA_API_KEY'] = settings['api_key']
        options = {'cwd': ai_startup.ROOT, 'env': environment, 'shell': False, 'close_fds': True}
        output = None
        log_path = None
        try:
            if os.name == 'nt' and config['show_console']:
                options['creationflags'] = subprocess.CREATE_NEW_CONSOLE
            else:
                LOG_ROOT.mkdir(parents=True, exist_ok=True)
                log_path = LOG_ROOT / (time.strftime('%Y%m%d-%H%M%S') + '-' + uuid4().hex[:8] + '.log')
                output = log_path.open('xb')
                options.update(stdin=subprocess.DEVNULL, stdout=output, stderr=subprocess.STDOUT)
                if os.name == 'nt': options['creationflags'] = subprocess.CREATE_NO_WINDOW
                else: options['start_new_session'] = True
            child = Popen([str(ai_startup.local_path(config['server_path'])), *ai_startup.arguments(config, settings['model'])], **options)
        except OSError:
            raise RuntimeError('无法启动本机模型程序，请检查运行程序路径、日志目录权限。') from None
        finally:
            if output is not None: output.close()
        _log_path = log_path
        _process, _identity, _started = child, _signature(settings, config), time.monotonic()
        message = '启动请求已接收。' + ('模型在独立窗口加载' if config['show_console'] and os.name == 'nt' else '模型在后台加载，不会弹出命令行窗口') + '，就绪后会自动更新状态。'
        return {'state': 'starting', 'message': message,
                'owned': True, 'elapsed_seconds': 0, 'model': settings['model'], 'base_url': ai_startup.endpoint(config)}
