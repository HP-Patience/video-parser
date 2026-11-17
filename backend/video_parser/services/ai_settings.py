"""AI model settings kept on this machine (.local/ai/settings.json).

The settings page writes here, so changing the model or key needs no restart. The API key
never leaves this module through the API: callers only learn whether one is saved.
Without a settings file the older QWEN_* variables from .env are used.
"""
from concurrent.futures import ThreadPoolExecutor
import json
import os
import secrets
from threading import RLock
from pathlib import Path
import time
from urllib.parse import urlsplit
from uuid import uuid4

from video_parser.core.paths import ROOT
from video_parser.services import ai_startup

SETTINGS_PATH = Path(os.getenv('VIDEO_PARSER_AI_SETTINGS', ROOT / '.local/ai/settings.json'))
_LOCK = RLock()
_MAX_SETTINGS_BYTES = 2_000_000
_UNSET = object()

PRESETS = {
    'modelscope': {
        'name': '魔搭社区 ModelScope', 'base_url': 'https://api-inference.modelscope.cn/v1',
        'model': 'Qwen/Qwen3-VL-8B-Instruct', 'key_required': True,
        'models': ['Qwen/Qwen3-VL-8B-Instruct', 'Qwen/Qwen3-VL-30B-A3B-Instruct', 'Qwen/Qwen3-VL-235B-A22B-Instruct'],
        'hint': '在 modelscope.cn 的“访问令牌”页面创建密钥；有每日免费调用额度。',
    },
    'dashscope': {
        'name': '阿里云百炼', 'base_url': 'https://dashscope.aliyuncs.com/compatible-mode/v1',
        'model': 'qwen3-vl-plus', 'key_required': True,
        'models': ['qwen3-vl-plus', 'qwen3-vl-flash', 'qwen-vl-max'],
        'hint': '在百炼控制台创建 API Key；按调用量计费。',
    },
    'ollama': {
        'name': '本机 Ollama', 'base_url': 'http://127.0.0.1:11434/v1',
        'model': 'qwen2.5vl:7b', 'key_required': False,
        'models': ['qwen2.5vl:7b', 'qwen3-vl:8b'],
        'hint': '模型在本机运行，画面和字幕不会发到外部；需先安装 Ollama 并下载模型。',
    },
    'llamacpp': {
        'name': '本机 llama.cpp', 'base_url': 'http://127.0.0.1:8888/v1',
        'model': 'Qwen3-VL-8B-Instruct-Q4_K_M', 'key_required': False,
        'models': ['Qwen3-VL-8B-Instruct-Q4_K_M', 'local-model'],
        'hint': '在本机运行 GGUF 模型，画面和字幕不发到外部。启动配置在下方；保存后双击 start-ai.cmd。模型 ID 会自动作为启动时的名称。',
    },
    'custom': {
        'name': '其他 OpenAI 兼容服务', 'base_url': '', 'model': '', 'key_required': False, 'models': [],
        'hint': '填写兼容 OpenAI Chat Completions 接口的地址；分析画面需要支持图片输入的模型。',
    },
}


def is_local(url):
    try:
        return (urlsplit(url).hostname or '').lower() in {'127.0.0.1', 'localhost', '::1'}
    except ValueError:
        return False


def _from_env():
    key = os.getenv('QWEN_API_KEY', '').strip()
    base = os.getenv('QWEN_API_BASE_URL', '').strip() or PRESETS['modelscope']['base_url']
    model = os.getenv('QWEN_MODEL_ID', '').strip() or PRESETS['modelscope']['model']
    provider = next((name for name, item in PRESETS.items() if item['base_url'] == base), 'custom')
    source = 'env' if key or (os.getenv('QWEN_API_BASE_URL', '').strip() and is_local(base)) else 'none'
    return {'provider': provider, 'base_url': base, 'model': model, 'api_key': key,
            'price_input': None, 'price_output': None, 'consented_endpoint': '', 'source': source}


def _empty():
    return {'provider': 'custom', 'provider_id': '', 'name': '', 'base_url': '', 'model': '',
            'models': [], 'api_key': '', 'price_input': None, 'price_output': None,
            'consented_endpoint': '', 'source': 'none', 'startup': None}


def _load(strict=False):
    """Project legacy settings without writing; never overwrite unreadable saved data."""
    data = None
    try:
        if SETTINGS_PATH.is_symlink():
            raise ValueError('unsafe settings path')
        if SETTINGS_PATH.is_file():
            if SETTINGS_PATH.stat().st_size >= _MAX_SETTINGS_BYTES:
                raise ValueError('settings too large')
            data = json.loads(SETTINGS_PATH.read_text(encoding='utf-8'))
            if not isinstance(data, dict):
                raise ValueError('invalid settings')
            if 'providers' in data:
                providers = data['providers']
                if (not isinstance(providers, list) or len(providers) > 20
                        or not all(isinstance(item, dict) and item.get('provider_id') for item in providers)
                        or not isinstance(data.get('active_provider_id'), str)):
                    raise ValueError('invalid providers')
                return {'providers': [{**item, 'source': 'settings'} for item in providers],
                        'active_provider_id': data['active_provider_id']}
            if not data.get('base_url'):
                raise ValueError('invalid legacy settings')
    except (OSError, ValueError):
        if strict:
            raise RuntimeError('无法读取现有 AI 设置，未保存；请检查设置文件。') from None
        data = None
    legacy = _from_env()
    if data is not None:
        legacy.update({key: data.get(key) for key in (
            'provider', 'base_url', 'model', 'api_key', 'price_input', 'price_output', 'consented_endpoint', 'startup')})
        legacy['api_key'] = legacy.get('api_key') or ''
        legacy['source'] = 'settings'
    if legacy['source'] == 'none':
        return {'providers': [], 'active_provider_id': ''}
    legacy.update(provider_id='default', name=PRESETS.get(legacy['provider'], PRESETS['custom'])['name'],
                  models=[legacy['model']])
    return {'providers': [legacy], 'active_provider_id': 'default'}


def current():
    """Effective default service, including the key (local use only)."""
    with _LOCK:
        document = _load()
        for item in document['providers']:
            if item['provider_id'] == document['active_provider_id']:
                return dict(item)
        # With no saved service preserve the legacy preset defaults on the settings page.
        return _from_env() if not SETTINGS_PATH.exists() and not document['providers'] else _empty()


def _identifier(value):
    if (not isinstance(value, str) or not 1 <= len(value) <= 80
            or any(ch.isspace() or ord(ch) < 32 or ord(ch) == 127 for ch in value)):
        raise ValueError('供应商 ID 格式不对。')
    return value


def _find(document, identifier):
    _identifier(identifier)
    for item in document['providers']:
        if item['provider_id'] == identifier:
            return item
    raise ValueError('找不到所选供应商，请重新选择。')


def resolve(target=None):
    """Resolve an explicit provider/model, or keep the legacy current() seam."""
    if target is None or isinstance(target, dict) and 'provider_id' not in target:
        return current()
    if not isinstance(target, dict):
        raise ValueError('模型目标格式不对。')
    with _LOCK:
        item = _find(_load(), target['provider_id'])
        model = _model(target.get('model', item['model']))
        if model not in item['models']:
            raise ValueError('所选模型不属于该供应商。')
        return {**item, 'model': model}


def configured(settings=None):
    settings = settings or current()
    return bool(settings['base_url'] and settings['model'] and (settings['api_key'] or is_local(settings['base_url'])))


def consented(settings=None):
    settings = settings or current()
    return is_local(settings['base_url']) or settings.get('consented_endpoint') == settings['base_url']


def _public_provider(settings):
    key = settings.get('api_key') or ''
    provider = settings['provider'] if settings['provider'] in PRESETS else 'custom'
    try:
        startup = ai_startup.effective(settings)
    except ValueError:
        startup = dict(ai_startup.DEFAULTS)
    return {'id': settings['provider_id'], 'name': settings['name'], 'provider': provider,
            'base_url': settings['base_url'], 'model': settings['model'], 'models': list(settings['models']),
            'key_saved': bool(key), 'key_hint': ('••••' + key[-4:]) if len(key) >= 8 else ('已保存' if key else ''),
            'price_input': settings.get('price_input'), 'price_output': settings.get('price_output'),
            'configured': configured(settings), 'local': is_local(settings['base_url']),
            'consented': consented(settings), 'source': settings['source'], 'startup': startup}


def public():
    with _LOCK:
        return _public()


def _public():
    settings = current()
    key = settings['api_key']
    provider = settings['provider'] if settings['provider'] in PRESETS else 'custom'
    startup_error = ''
    try:
        startup = ai_startup.effective(settings)
    except ValueError as error:
        startup, startup_error = dict(ai_startup.DEFAULTS), str(error)
    return {'provider': provider, 'provider_name': settings.get('name') or PRESETS[provider]['name'], 'base_url': settings['base_url'],
            'model': settings['model'], 'key_saved': bool(key), 'key_hint': ('••••' + key[-4:]) if len(key) >= 8 else ('已保存' if key else ''),
            'price_input': settings.get('price_input'), 'price_output': settings.get('price_output'),
            'configured': configured(settings), 'local': is_local(settings['base_url']),
            'consented': consented(settings), 'source': settings['source'],
            'storage': '.local/ai/settings.json（仅保存在本机，页面不会显示密钥）',
            'startup': startup, 'startup_defaults': dict(ai_startup.DEFAULTS),
            'startup_command': ai_startup.launcher_command(), 'startup_error': startup_error,
            'providers': [_public_provider(item) for item in _load()['providers']],
            'active_provider_id': _load()['active_provider_id'],
            'presets': {name: {key: value for key, value in item.items()} for name, item in PRESETS.items()}}


def _price(value):
    if value in (None, ''):
        return None
    try:
        if isinstance(value, bool):
            raise ValueError
        number = float(value)
    except (TypeError, ValueError, OverflowError):
        raise ValueError('价格应在 0 到 10000 元之间。') from None
    if not 0 <= number <= 10_000:
        raise ValueError('价格应在 0 到 10000 元之间。')
    return round(number, 4)


def _write(data):
    SETTINGS_PATH.parent.mkdir(parents=True, exist_ok=True)
    if SETTINGS_PATH.is_symlink():
        raise RuntimeError('设置文件路径不安全，未保存。')
    temporary = SETTINGS_PATH.with_name('.' + uuid4().hex + '.tmp')
    try:
        fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, 'w', encoding='utf-8', newline='\n') as stream:
            json.dump(data, stream, ensure_ascii=False, indent=2)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, SETTINGS_PATH)
    except OSError:
        raise RuntimeError('无法保存 AI 设置，请检查工具目录写入权限。') from None
    finally:
        temporary.unlink(missing_ok=True)


def _endpoint(value):
    if not isinstance(value, str) or not 1 <= len(value) <= 500:
        raise ValueError('请填写有效的接口地址。')
    if any(ch.isspace() or ord(ch) < 32 or ord(ch) == 127 for ch in value):
        raise ValueError('接口地址不能包含空白或控制字符。')
    value = value.rstrip('/')
    try:
        parts = urlsplit(value)
        valid = (parts.scheme in ('http', 'https') and parts.hostname and parts.username is None
                 and parts.password is None and not parts.query and not parts.fragment
                 and '?' not in value and '#' not in value and '\\' not in value
                 and (parts.port is None or 1 <= parts.port <= 65535))
    except ValueError:
        valid = False
    if not valid:
        raise ValueError('接口地址需要为不含凭据、查询参数的 http:// 或 https:// 地址。')
    if parts.scheme == 'http' and not is_local(value):
        raise ValueError('外部服务请使用 https 地址，避免密钥明文传输。')
    return value


def _model(value):
    if (not isinstance(value, str) or not value.strip() or len(value) > 200
            or any(ch.isspace() or ord(ch) < 32 or ord(ch) == 127 for ch in value)):
        raise ValueError('请填写有效的模型 ID。')
    return value


def _key(value):
    if value is not None and (not isinstance(value, str) or len(value) > 500
                             or any(ch.isspace() or ord(ch) < 32 or ord(ch) == 127 for ch in value)):
        raise ValueError('密钥格式不对：不能包含空格或换行。')
    return value


def save_provider(id=None, name='', provider='custom', base_url='', models=None, model='', api_key=None,
                  clear_key=False, price_input=_UNSET, price_output=_UNSET, startup=None, activate=False):
    with _LOCK:
        document = _load(strict=True)
        previous = _find(document, id) if id is not None else _empty()
        if id is None and len(document['providers']) >= 20:
            raise ValueError('最多保存 20 家供应商。')
        if not isinstance(provider, str) or provider not in PRESETS:
            raise ValueError('请选择支持的模型供应商。')
        if (not isinstance(name, str) or len(name) > 100
                or any(ord(ch) < 32 or ord(ch) == 127 for ch in name)):
            raise ValueError('供应商名称需要为不含控制字符的 100 字以内文本。')
        if type(clear_key) is not bool or type(activate) is not bool:
            raise ValueError('供应商保存选项格式不对。')
        base_url = _endpoint(base_url)
        _key(api_key)
        if not isinstance(model, str):
            raise ValueError('请填写有效的模型 ID。')
        if models is None:
            models = list(previous['models'])
            if model and model not in models:
                models.append(model)
        if not isinstance(models, list) or not 1 <= len(models) <= 100:
            raise ValueError('每家供应商需要 1 到 100 个模型。')
        models = list(dict.fromkeys(_model(value) for value in models))
        model = _model(model) if model else models[0]
        if model not in models:
            raise ValueError('首选模型需要属于该供应商的模型列表。')
        launch = previous.get('startup')
        if startup is not None:
            launch = ai_startup.normalize(startup)
            if provider != 'llamacpp' or base_url != ai_startup.endpoint(launch):
                raise ValueError('启动配置仅用于本机 llama.cpp，接口地址需要与服务端口一致。')
            ai_startup.arguments(launch, model)
        key = previous['api_key'] if previous['base_url'] == base_url else ''
        if api_key:
            key = api_key
        if clear_key:
            key = ''
        elif startup is not None and not key:
            key = secrets.token_urlsafe(32)
        identifier = id if id is not None else uuid4().hex
        item = {'provider_id': identifier, 'name': name.strip() or previous['name'] or PRESETS[provider]['name'],
                'provider': provider, 'base_url': base_url, 'models': models, 'model': model, 'api_key': key,
                'price_input': previous.get('price_input') if price_input is _UNSET else _price(price_input),
                'price_output': previous.get('price_output') if price_output is _UNSET else _price(price_output),
                'consented_endpoint': previous.get('consented_endpoint') if previous['base_url'] == base_url else '',
                'startup': launch, 'updated_at': int(time.time())}
        if id is None:
            document['providers'].append(item)
            if len(document['providers']) == 1:
                document['active_provider_id'] = identifier
        else:
            document['providers'] = [item if old['provider_id'] == id else old for old in document['providers']]
        if activate:
            document['active_provider_id'] = identifier
        _write(document)
        return public()


def delete_provider(id):
    with _LOCK:
        document = _load(strict=True)
        _find(document, id)
        document['providers'] = [item for item in document['providers'] if item['provider_id'] != id]
        if document['active_provider_id'] == id:
            document['active_provider_id'] = ''
        _write(document)
        return public()


def save(provider, base_url, model, api_key=None, clear_key=False, price_input=None, price_output=None, startup=None):
    """Legacy form edits only the default provider, retaining all other services."""
    with _LOCK:
        if not isinstance(provider, str):
            raise ValueError('请选择支持的模型供应商。')
        identifier = _load(strict=True)['active_provider_id'] or None
        return save_provider(id=identifier, provider=provider if provider in PRESETS else 'custom',
                             base_url=base_url, model=model, api_key=api_key, clear_key=clear_key,
                             price_input=price_input, price_output=price_output, startup=startup, activate=True)


def remember_consent(target=None):
    with _LOCK:
        settings = resolve(target)
        if consented(settings):
            return
        document = _load(strict=True)
        identifier = settings.get('provider_id') or document['active_provider_id']
        item = _find(document, identifier)
        if item['base_url'] != settings['base_url']:
            raise ValueError('供应商接口已改变，请重新选择。')
        item['consented_endpoint'] = item['base_url']
        item['updated_at'] = int(time.time())
        _write(document)


def client(settings=None, timeout=None):
    from openai import OpenAI
    settings = settings or current()
    # Models on this machine read long prompts (subtitles plus frames) slower than cloud services.
    timeout = timeout or (900 if is_local(settings['base_url']) else 180)
    if not configured(settings):
        raise RuntimeError('尚未配置 AI 模型，请在“设置 → AI 模型”里填写。')
    extra = {}
    if is_local(settings['base_url']):
        # A system proxy must not intercept calls to a model running on this machine.
        from openai import DefaultHttpxClient
        extra['http_client'] = DefaultHttpxClient(trust_env=False, timeout=timeout)
    return OpenAI(base_url=settings['base_url'], api_key=settings['api_key'] or 'local', max_retries=0, timeout=timeout, **extra)


def explain(error):
    """A short, key-free message for a failed model call."""
    name = type(error).__name__
    status = getattr(error, 'status_code', None)
    if status in (401, 403) or name in ('AuthenticationError', 'PermissionDeniedError'):
        return '模型服务拒绝了请求：密钥无效或没有该模型的权限。'
    if status == 404 or name == 'NotFoundError':
        return '找不到这个模型或接口地址，请检查模型 ID 和地址。'
    if status == 429 or name == 'RateLimitError':
        return '调用太频繁或额度已用完，请稍后再试。'
    if status == 400 or name == 'BadRequestError':
        return '模型服务不接受这次请求（可能不支持图片输入，或内容太长）。'
    if name in ('APIConnectionError', 'APITimeoutError', 'ConnectError', 'Timeout'):
        return '连不上模型服务，请检查网络或接口地址。'
    if isinstance(status, int):
        return f'模型服务返回错误（HTTP {status}）。'
    return '模型调用失败，请检查 AI 设置后重试。'


def test(settings=None):
    settings = settings or current()
    if not configured(settings):
        raise RuntimeError('尚未配置 AI 模型，请在“设置 → AI 模型”里填写。')
    started = time.monotonic()
    connection = None
    try:
        connection = client(settings, timeout=120 if is_local(settings['base_url']) else 30)
        response = connection.chat.completions.create(
            model=settings['model'], messages=[{'role': 'user', 'content': '只回复两个字母：OK'}], max_tokens=8)
        reply = (response.choices[0].message.content or '').strip()
    except Exception as error:
        raise RuntimeError(explain(error)) from None
    finally:
        if connection is not None:
            try:
                connection.close()
            except Exception:
                pass
    if settings['api_key']:
        reply = reply.replace(settings['api_key'], '[已隐藏]')
    return {'ok': True, 'latency_ms': int((time.monotonic() - started) * 1000), 'reply': reply[:40], 'model': settings['model']}


def discover_models(id=None, provider='custom', base_url='', api_key=None):
    """Fetch one SDK page explicitly; a draft never borrows another provider's key."""
    if not isinstance(provider, str) or provider not in PRESETS:
        raise ValueError('请选择支持的模型供应商。')
    base_url = _endpoint(base_url)
    _key(api_key)
    key = api_key or ''
    if id is not None:
        with _LOCK:
            saved = _find(_load(), id)
            if saved['base_url'] == base_url and not key:
                key = saved['api_key']
    if not key and not is_local(base_url):
        raise ValueError('远程模型服务需要 API Key。')
    settings = {'provider': provider, 'base_url': base_url, 'model': 'discovery', 'api_key': key}
    connection = None
    try:
        connection = client(settings, timeout=30)
        page = connection.models.list()
        models = []
        # Inspect data, not the SDK page iterator: iteration can fetch unbounded remote pages.
        for item in page.data[:1000]:
            value = getattr(item, 'id', None)
            try:
                value = _model(value)
            except ValueError:
                continue
            if value not in models and (not key or key not in value):
                models.append(value)
        return {'models': models}
    except Exception as error:
        raise RuntimeError(explain(error)) from None
    finally:
        if connection is not None:
            try:
                connection.close()
            except Exception:
                pass


def test_targets(targets):
    """Test an unsaved selection without exposing credentials or changing settings."""
    if not isinstance(targets, list) or not 1 <= len(targets) <= 20:
        raise ValueError('请选择 1 到 20 个模型进行连接测试。')
    with _LOCK:
        saved = current()
        document = _load()
    selections = []
    identifiers = set()
    for index, target in enumerate(targets):
        if not isinstance(target, dict) or target.keys() - {'id', 'provider_id', 'provider', 'base_url', 'model', 'api_key'}:
            raise ValueError('模型测试目标包含不支持的字段。')
        identifier = target.get('id', str(index))
        if not isinstance(identifier, str) or not 1 <= len(identifier) <= 80 or any(ord(ch) < 32 or ord(ch) == 127 for ch in identifier):
            raise ValueError('模型测试目标 ID 格式不对。')
        if identifier in identifiers:
            raise ValueError('模型测试目标 ID 不能重复。')
        identifiers.add(identifier)
        selected = _find(document, target['provider_id']) if 'provider_id' in target else None
        provider = target.get('provider', selected['provider'] if selected else 'custom')
        if not isinstance(provider, str) or provider not in PRESETS:
            raise ValueError('请选择支持的模型供应商。')
        base_url = _endpoint(target.get('base_url', selected['base_url'] if selected else None))
        model = _model(target.get('model'))
        key = _key(target.get('api_key'))
        if selected is not None:
            if base_url != selected['base_url'] or provider != selected['provider']:
                raise ValueError('模型测试地址与所选供应商不一致。')
            key = key or selected['api_key']
        else:
            # Only the old omitted-key protocol inherits the default endpoint's token.
            if 'api_key' not in target:
                key = saved['api_key'] if saved['base_url'] == base_url else ''
            key = key or ''
        selections.append({'id': identifier, 'provider': provider, 'base_url': base_url,
                           'model': model, 'api_key': key})

    def run(settings):
        started = time.monotonic()
        result = {field: settings[field] for field in ('id', 'provider', 'model')}
        try:
            test(settings)
            result.update(ok=True, error='')
        except RuntimeError as error:
            result.update(ok=False, error=str(error))
        except Exception as error:
            result.update(ok=False, error=explain(error))
        result['latency_ms'] = int((time.monotonic() - started) * 1000)
        return result

    with ThreadPoolExecutor(max_workers=3) as pool:
        return {'results': list(pool.map(run, selections))}
