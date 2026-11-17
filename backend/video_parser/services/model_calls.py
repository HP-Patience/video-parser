"""One dispatch seam for course materials, planning, answering and naming; legacy API stays unchanged."""
from contextlib import contextmanager
from contextvars import ContextVar
from pathlib import Path
from video_parser.core.paths import ROOT
import hashlib
import json
import os
import re

from video_parser.services import ai_settings, local_agents, task_runtime
from video_parser.services.local_agents.types import Result


def target(value=None):
    return local_agents.normalize(value)


def require(value=None):
    value = local_agents.require(value)
    if value['engine'] == 'api':
        try:
            settings = ai_settings.resolve(value)
        except (KeyError, TypeError, ValueError) as error:
            raise local_agents.AgentError(str(error) or '所选 AI 供应商或模型不可用。') from None
        if not ai_settings.configured(settings):
            raise local_agents.AgentError('尚未配置所选 AI 模型，请在“设置 → AI 模型”里填写。')
    return value


def consent(value=None):
    value = target(value)
    if value['engine'] == 'api':
        if value.get('provider_id'):
            ai_settings.remember_consent(value)
        else:
            ai_settings.remember_consent()


def provider(value, settings):
    value = target(value)
    if value['engine'] != 'api':
        return local_agents.NAMES[value['engine']]
    return (settings.get('name') or settings.get('provider_name') or
            ai_settings.PRESETS.get(settings['provider'], ai_settings.PRESETS['custom'])['name'])


_api_binding = ContextVar('api_config_binding', default=None)


class ConfigurationChanged(RuntimeError):
    code = 'model_config_changed'
    user_message = '模型服务或配置自提交后已变化，未发送本次请求；请核对模型后重新提交。'


def api_binding(settings):
    """No key, signed URL or subtitle content is persisted in this fingerprint."""
    fields = {key: settings.get(key) for key in ('provider_id', 'provider', 'base_url', 'model', 'startup')}
    return hashlib.sha256(json.dumps(fields, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def check_api_binding(expected, settings):
    if expected and expected != api_binding(settings):
        raise ConfigurationChanged(ConfigurationChanged.user_message)


@contextmanager
def bound_api(expected):
    token = _api_binding.set(expected)
    try: yield
    finally: _api_binding.reset(token)


def call(messages, settings, value, directory, *, purpose='material', workspace=None,
         session_id=None, rebuild_messages=None, question='', max_tokens=None):
    value = target(value)
    if value['engine'] == 'api':
        if value.get('provider_id'):
            try:
                settings = ai_settings.resolve(value)
            except (KeyError, TypeError, ValueError) as error:
                raise local_agents.AgentError(str(error) or '所选 AI 供应商或模型不可用。') from None
            check_api_binding(_api_binding.get(), settings)
            if not ai_settings.configured(settings):
                raise local_agents.AgentError('尚未配置所选 AI 模型，请在“设置 → AI 模型”里填写。')
        else:
            # Legacy jobs carry their captured settings separately; do not redirect them to current().
            check_api_binding(_api_binding.get(), settings)
        from video_parser.services.ai_analysis import call_model
        # Only translation batches set an output budget; other callers keep the provider default.
        text, usage = call_model(messages, settings, **({'max_tokens': max_tokens} if max_tokens else {}))
        return Result(text, usage=usage, model=settings['model'])
    return local_agents.complete(messages, value, Path(directory) / ('agent-' + purpose),
        workspace=workspace, session_id=session_id, rebuild_messages=rebuild_messages,
        question=question, purpose=purpose)


WORKSPACE_ROOT = Path(os.environ.get('LOCALAPPDATA') or Path.home() / '.cache') / 'video-parser/agent-workspaces'


def workspace(project_directory, conversation_id):
    """Stable, empty application-owned cwd, not the code repository or the user's note vault."""
    if not re.fullmatch(r'[a-f0-9]{32}', conversation_id):
        raise ValueError('无效课程对话标识。')
    identity = Path(project_directory).resolve()
    try:
        # Keep the established hash namespace after library -> outputs migration.
        identity = ROOT / 'library' / identity.relative_to((ROOT / 'outputs').resolve())
    except ValueError:
        pass
    key = hashlib.sha256(str(identity).encode('utf-8')).hexdigest()[:24]
    path = WORKSPACE_ROOT / key / conversation_id
    for item in (WORKSPACE_ROOT, WORKSPACE_ROOT / key, path):
        if item.is_symlink():
            raise ValueError('本地 agent 工作目录不安全，未启动。')
        item.mkdir(parents=True, exist_ok=True)
    return path.resolve()



def history_fingerprint(history):
    return hashlib.sha256(json.dumps(history,ensure_ascii=False,sort_keys=True).encode('utf-8')).hexdigest()


def bound_session(directory, engine, history, candidate):
    """Imported/stale headers cannot point a course at unrelated or invisible native history."""
    path=Path(directory)/'.video-parser-session.json'
    try:
        if path.is_file() and not path.is_symlink() and path.stat().st_size<4096:
            data=json.loads(path.read_text(encoding='utf-8'))
            if data.get('engine')==engine and data.get('history')==history_fingerprint(history) and data.get('id')==candidate:
                return local_agents.valid_session(candidate)
    except (OSError,ValueError,TypeError):
        pass
    return None


def bind_session(directory, engine, history, identifier):
    task_runtime.atomic(Path(directory)/'.video-parser-session.json',{'engine':engine,'history':history_fingerprint(history),'id':identifier})
