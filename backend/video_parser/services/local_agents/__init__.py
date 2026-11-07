"""Local-agent registry and capability discovery. Only public model/command metadata is cached.

Protocol architecture reference: https://github.com/YishenTu/claudian (MIT, Yishen Tu).
Adapters are independently implemented against each installed program's structured protocol.
No Obsidian bundle, credentials, SDK dependency tree or global settings are copied.
"""
import concurrent.futures
import json
from pathlib import Path
import re
from threading import Lock
import time
import tempfile

from video_parser.core.paths import ROOT
from video_parser.services import task_runtime, agent_controls
from .transport import AgentError, NAMES, available
from .types import Result, IDENTIFIER, prepare, valid_session

CACHE_PATH = ROOT / '.local/ai/agent-catalog.json'
_lock = Lock()


def normalize(target=None):
    target = target or {}
    if not isinstance(target, dict):
        raise ValueError('无效 agent 选择。')
    engine = target.get('engine', 'api')
    if engine not in ('api', *NAMES):
        raise ValueError('未知本地 agent。')
    result = {'engine': engine, 'model': '' if target.get('model') is None else target['model'],
              'effort': '' if target.get('effort') is None else target['effort']}
    if not all(isinstance(value, str) for value in result.values()):
        raise ValueError('无效模型或推理强度。')
    if result['model'] and not IDENTIFIER.fullmatch(result['model']):
        raise ValueError('无效模型 ID。')
    if result['effort'] and not re.fullmatch(r'[a-z][a-z0-9_-]{0,31}', result['effort']):
        raise ValueError('无效推理强度。')
    if engine == 'api' and (result['model'] or result['effort']):
        raise ValueError('API 模型请在模型设置中选择。')
    return result


def require(target=None):
    target = normalize(target)
    if not agent_controls.enabled(target['engine']):
        raise AgentError('所选 Agent 已关闭，请在设置中开启或明确选择其他工具。')
    if target['engine'] != 'api' and not available(target['engine']):
        raise AgentError('未找到本地 agent，请先在本机安装并登录，再刷新模型列表。')
    return target


def _cache():
    try:
        if CACHE_PATH.is_file() and not CACHE_PATH.is_symlink() and CACHE_PATH.stat().st_size <= 2_000_000:
            data = json.loads(CACHE_PATH.read_text(encoding='utf-8'))
            if isinstance(data, dict):
                return data
    except (ValueError, OSError):
        pass
    return {}


def inventory():
    """Read-only: GET does not spawn an agent, contact a model or read authentication files."""
    cache = _cache()
    engines = []
    for engine, name in NAMES.items():
        entry = cache.get(engine, {})
        engines.append({'id': engine, 'name': name, 'installed': available(engine), 'enabled': agent_controls.enabled(engine),
            'discovered': bool(entry.get('updated_at')), 'updated_at': entry.get('updated_at'),
            'models': entry.get('models', []), 'commands': entry.get('commands', []),
            'error': entry.get('error', '')})
    return {'engines': engines, 'permission': 'restricted-learning'}


def _runtime(engine, workspace, target=None, turn=None, session_id=None, persistent=False):
    if engine == 'claude':
        from .claude import Runtime
        return Runtime(workspace, target, turn, session_id, persistent)
    if engine == 'codex':
        from .codex import Runtime
        return Runtime(workspace)
    if engine == 'opencode':
        from .opencode import Runtime
        return Runtime(workspace, turn)
    if engine == 'dsh':
        from .dsh import Runtime
        return Runtime(workspace, turn)
    raise ValueError('未知本地 agent。')


def _discover(engine, model=''):
    target = normalize({'engine': engine, 'model': model})
    if not agent_controls.enabled(engine):
        return {'models': [], 'commands': [], 'error': 'Agent 已关闭。'}
    if not available(engine):
        return {'models': [], 'commands': [], 'error': '未找到本地 agent，请先安装并登录。'}
    runtime = None
    try:
        with tempfile.TemporaryDirectory(prefix='video-parser-agent-discovery-') as directory:
            runtime = _runtime(engine, Path(directory))
            data = runtime.catalog(target) if engine == 'opencode' else runtime.catalog()
            runtime.close()
            runtime = None
        # Public response is deliberately not the raw initialization result (which can include account details).
        models = []
        for item in data.get('models', []):
            identifier = item.get('id', '')
            if isinstance(identifier, str) and IDENTIFIER.fullmatch(identifier):
                models.append({'id': identifier, 'label': str(item.get('label') or identifier)[:200],
                    'efforts': [value for value in item.get('efforts', [])
                                if isinstance(value, str) and re.fullmatch(r'[a-z][a-z0-9_-]{0,31}', value)],
                    'images': item.get('images') if isinstance(item.get('images'), bool) else None})
        return {'models': models[:100], 'commands': data.get('commands', [])[:200], 'error': '', 'updated_at': time.time()}
    except (AgentError, ValueError, OSError, KeyError, TypeError):
        return {'models': [], 'commands': [], 'error': '无法读取本地 agent 的模型或命令，请检查登录、安装和版本。'}
    finally:
        if runtime:
            runtime.close()


def discover(engine='all', model=''):
    if engine not in ('all', *NAMES):
        raise ValueError('未知本地 agent。')
    targets = [name for name in NAMES if agent_controls.enabled(name)] if engine == 'all' else [engine]
    if engine != 'all' and not agent_controls.enabled(engine):
        raise AgentError('所选 Agent 已关闭。')
    with concurrent.futures.ThreadPoolExecutor(max_workers=3) as pool:
        results = list(pool.map(lambda value: _discover(value, model if value == engine else ''), targets))
    with _lock:
        cache = _cache()
        cache.update(zip(targets, results))
        task_runtime.atomic(CACHE_PATH, cache)
    return inventory()


def complete(messages, target, directory, workspace=None, session_id=None, rebuild_messages=None, question='', purpose='chat'):
    target = require(target)
    engine = target['engine']
    if engine == 'api':
        raise ValueError('API 请求应使用现有模型客户端。')
    turn = prepare(messages, question, purpose)
    rebuild = prepare(rebuild_messages, question, purpose) if rebuild_messages is not None else None
    session_id = valid_session(session_id)
    # An advertised text-only model must not silently discard course images.
    entry = _cache().get(engine, {})
    model = next((item for item in entry.get('models', []) if item['id'] == target['model']), None)
    if turn.images and model and model.get('images') is False:
        raise AgentError('这个本地 agent 不支持图片输入，请选择支持画面的模型。')
    workspace = Path(workspace or directory)
    workspace.mkdir(parents=True, exist_ok=True)
    runtime = None
    try:
        try:
            runtime = _runtime(engine, workspace, target, turn, session_id, purpose == 'chat')
        except AgentError:
            if engine != 'claude' or not session_id:
                raise
            # Initialization has sent NO user prompt. Rebuilding here cannot replay a paid turn.
            session_id = None
            turn = rebuild or turn
            runtime = _runtime(engine, workspace, target, turn, None, purpose == 'chat')
        result = runtime.run(turn, target, directory, session_id, rebuild)
        if not isinstance(result.text, str) or not result.text.strip():
            raise AgentError('本地 agent 没有返回可保存的回答，请检查模型能力。')
        try:
            result.session_id = valid_session(result.session_id)
        except ValueError:
            # Preserve a paid answer; a later turn can rebuild from canonical local history.
            result.session_id = None
        return result
    finally:
        if runtime:
            runtime.close()
