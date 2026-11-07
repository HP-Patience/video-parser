"""DeepSeek Harness ACP adapter over the harness's own automation-only `acp` profile.

The harness ships an Agent Client Protocol server (`dsh --profile acp`); this adapter speaks only
that protocol. It reads the installed Desktop profile's public model routing to reuse the same
providers, but it never edits a harness profile, imports credentials or starts a shell itself.
"""
import json
import os
import re
import tempfile
from pathlib import Path

from video_parser.core.paths import ROOT
from .transport import Wire, AgentError, RpcError
from .types import Result, Progress, valid_session

PROFILE = 'acp'
OVERLAY_ROOT = ROOT / '.local/ai'
MODEL = 'model'
EFFORT = 'reasoning_effort'
# A learning turn may read the provided context only: shell, file, network, skill and delegation
# tools stay unmounted for this process, mirroring the other restricted local agents.
DISABLED_TOOLS = (
    ('tool-working-directory', '@deepseek-ai/dsh-tool-working-directory'),
    ('tool-bash', '@deepseek-ai/dsh-tool-bash'),
    ('tool-pwsh', '@deepseek-ai/dsh-tool-pwsh'),
    ('tool-jobs', '@deepseek-ai/dsh-tool-jobs'),
    ('tool-fs', '@deepseek-ai/dsh-tool-fs'),
    ('tool-fs-search', '@deepseek-ai/dsh-tool-fs-search'),
    ('tool-skill', '@deepseek-ai/dsh-tool-skill'),
    ('tool-subagent-control', '@deepseek-ai/dsh-tool-subagent-control'),
    ('tool-subagent', '@deepseek-ai/dsh-tool-subagent'),
    ('tool-subagent-fork', '@deepseek-ai/dsh-tool-subagent-fork'),
    ('tool-workflow', '@deepseek-ai/dsh-tool-workflow'),
    ('tool-todo', '@deepseek-ai/dsh-tool-todo'),
    ('tool-goal', '@deepseek-ai/dsh-tool-goal'),
    ('tool-web', '@deepseek-ai/dsh-tool-web'),
)
BLOCK = re.compile(r'^- id:\s*([A-Za-z0-9_.-]+)\s*$')


def home():
    value = os.environ.get('DSH_HOME')
    return Path(value) if value else Path.home() / '.dsh'


def _entries(text):
    """Split a profile patch into top-level entries by their `- id:` header line."""
    lines = text.splitlines()
    starts = [index for index, line in enumerate(lines) if line.startswith('- ')]
    result = {}
    for position, start in enumerate(starts):
        end = starts[position + 1] if position + 1 < len(starts) else len(lines)
        match = BLOCK.match(lines[start])
        if match:
            result[match.group(1)] = '\n'.join(lines[start:end]).rstrip() + '\n'
    return result


def _value(block, key):
    match = re.search(r'^    ' + re.escape(key) + r':\s*(.+?)\s*$', block, re.M)
    if not match:
        return ''
    value = re.sub(r'\s+#.*$', '', match.group(1)).strip()
    if len(value) >= 2 and value[0] == value[-1] and value[0] in '"\'':
        value = value[1:-1]
    return value


def routing():
    """Reuse the Desktop profile's provider routes so this process sees the user's models."""
    patch = home() / 'profiles/desktop/cordis.patch.yml'
    try:
        if patch.is_symlink() or not patch.is_file() or patch.stat().st_size > 2_000_000:
            return ''
        blocks = _entries(patch.read_text(encoding='utf-8'))
    except OSError:
        return ''
    providers = blocks.get('llm-pi-ai', '')
    default = blocks.get('agent-default-model', '')
    provider, model = _value(default, 'provider'), _value(default, 'model')
    if not providers or not provider or not model:
        return ''
    if not re.search(r'^      ' + re.escape(provider) + r':\s*$', providers, re.M):
        return ''
    return (providers.rstrip('\n') + '\n- id: acp\n  name: "@deepseek-ai/dsh-acp"\n  config:\n'
            f'    provider: {json.dumps(provider)}\n    model: {json.dumps(model)}\n')


def persona(instructions):
    if not instructions:
        return ''
    lines = instructions.replace('\r\n', '\n').replace('\r', '\n').split('\n')
    # A block scalar stays valid only with printable content and spaces; control characters become spaces.
    lines = [''.join(character if character >= ' ' or character == '\t' else ' ' for character in line)
             for line in lines]
    body = ''.join(('      ' + line).rstrip() + '\n' for line in lines)
    return ('- id: system-prompt\n  name: "@deepseek-ai/dsh-system-prompt"\n  config:\n'
            '    personaPrefix: |-\n' + body)


def overlay(turn=None):
    parts = ['# Generated for one video-parser DeepSeek Harness ACP process; not a user setting file.\n',
             routing()]
    if turn is not None:
        parts.append(persona(turn.instructions))
    parts.extend(f'- id: {identifier}\n  name: "{name}"\n  disabled: true\n' for identifier, name in DISABLED_TOOLS)
    text = ''.join(part for part in parts if part)
    return text or None


def write_overlay(turn=None):
    text = overlay(turn)
    if text is None:
        return None
    try:
        OVERLAY_ROOT.mkdir(parents=True, exist_ok=True)
        handle = tempfile.NamedTemporaryFile('w', encoding='utf-8', suffix='.yml', prefix='dsh-acp-',
                                             dir=OVERLAY_ROOT, delete=False)
        try:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        finally:
            handle.close()
        return handle.name
    except OSError:
        return None


def _choices(option):
    values = []
    for entry in option.get('options', []):
        values.extend(entry['options'] if isinstance(entry.get('options'), list) else [entry])
    return values


def pair(value):
    """Decode one ACP model selector (`["provider","model"]`) into its two display-safe parts."""
    if not isinstance(value, str):
        return None
    try:
        item = json.loads(value)
    except ValueError:
        return None
    if not isinstance(item, list) or len(item) != 2 or not all(isinstance(part, str) and part for part in item):
        return None
    return item[0], item[1]


def identifier(value):
    item = pair(value)
    return item[0] + '/' + item[1] if item else ''


def pair_from_identifier(value):
    provider, _, model = value.partition('/')
    return (provider, model) if provider and model else None


class Runtime:
    def __init__(self, workspace, turn=None):
        self.workspace = Path(workspace)
        self.session_id = None
        self.config = []
        self.parts = []
        self.accept_text = False
        self.overlay = write_overlay(turn)
        # The process-local persona is only in force when its overlay was actually written.
        self.persona = bool(turn is not None and turn.instructions and self.overlay)
        self.wire = None
        arguments = ['--profile', PROFILE]
        if self.overlay:
            arguments += ['--patch', self.overlay]
        try:
            self.wire = Wire('dsh', workspace, arguments, {'DSH_PERMISSION_MODE': 'read-only'})
            self.wire.on_event = self.event
            self.wire.on_request = self.request
            self.progress = Progress('DeepSeek Harness')
            self.init = self.wire.request('initialize', {'protocolVersion': 1,
                'clientInfo': {'name': 'video-parser', 'version': '3.0.0'},
                'clientCapabilities': {'fs': {'readTextFile': False, 'writeTextFile': False}, 'terminal': False}},
                timeout=60)
        except BaseException:
            self.close()
            raise

    def close(self):
        if self.wire is not None:
            self.wire.close()
            self.wire = None
        if self.overlay:
            try:
                Path(self.overlay).unlink(missing_ok=True)
            except OSError:
                pass
            self.overlay = None

    def rpc(self, method, params, timeout=30):
        return self.wire.request(method, params, timeout)

    def event(self, message):
        if message.get('method') not in ('session/update', 'sessionUpdate'):
            return
        params = message.get('params', {})
        if self.session_id and params.get('sessionId') not in (None, self.session_id):
            return
        update = params.get('update', {})
        kind = update.get('sessionUpdate')
        if kind == 'agent_message_chunk' and self.accept_text:
            content = update.get('content', {})
            if content.get('type') == 'text':
                text = content.get('text', '')
                self.parts.append(text)
                self.progress.text(text)
        elif kind == 'config_option_update':
            self.config = update.get('configOptions', self.config)

    @staticmethod
    def request(method, params):
        if method in ('session/request_permission', 'request_permission', 'session/requestPermission'):
            return {'outcome': {'outcome': 'cancelled'}}
        raise RpcError(-32601)

    def option(self, *categories):
        return next((item for item in self.config
                     if item.get('id') in categories or item.get('category') in categories), None)

    def set_option(self, categories, value):
        option = self.option(*categories)
        if not option or value not in {entry.get('value') for entry in _choices(option)}:
            raise AgentError('所选模型或推理强度不在本地 agent 提供的能力列表中，请刷新。')
        result = self.rpc('session/set_config_option',
                          {'sessionId': self.session_id, 'configId': option['id'], 'value': value})
        self.config = result.get('configOptions', self.config)

    @staticmethod
    def _missing(error):
        if error.missing_session():
            return True
        return error.rpc_code == -32602 and any(word in error.detail.lower() for word in
                                                ('not resumable', 'cwd does not match', 'already active'))

    def ensure_session(self, session_id=None):
        session_id = valid_session(session_id)
        self.accept_text = False
        response = None
        if session_id:
            try:
                response = self.rpc('session/resume',
                                    {'sessionId': session_id, 'cwd': str(self.workspace), 'mcpServers': []}, timeout=60)
            except RpcError as error:
                if not self._missing(error):
                    raise
                session_id = None
        if not session_id:
            response = self.rpc('session/new', {'cwd': str(self.workspace), 'mcpServers': []}, timeout=60)
        self.session_id = valid_session(response.get('sessionId') or session_id)
        if not self.session_id:
            raise AgentError()
        self.config = response.get('configOptions', self.config)
        return bool(session_id and session_id == self.session_id)

    def catalog(self):
        self.ensure_session()
        model_option = self.option(MODEL)
        effort_option = self.option(EFFORT, 'thought_level')
        efforts = [entry.get('value') for entry in _choices(effort_option)] if effort_option else []
        efforts = [value for value in efforts if isinstance(value, str) and re.fullmatch(r'[a-z][a-z0-9_-]{0,31}', value)]
        rows = []
        for entry in (model_option or {}).get('options', []):
            if isinstance(entry.get('options'), list):
                group = str(entry.get('name') or entry.get('group') or '')
                rows.extend((group, item) for item in entry['options'])
            else:
                rows.append(('', entry))
        models, labels = [], {}
        for group, entry in rows:
            pair_value = pair(entry.get('value'))
            if not pair_value:
                continue
            label = str(entry.get('name') or pair_value[1])[:200]
            labels.setdefault(label, set()).add(group)
            models.append({'id': pair_value[0] + '/' + pair_value[1], 'label': label,
                           'efforts': efforts, 'images': None, 'group': group})
        for item in models:
            if len(labels.get(item['label'], ())) > 1 and item['group']:
                item['label'] = item['group'] + ' · ' + item['label']
            item.pop('group', None)
        return {'models': models[:100], 'commands': []}

    def run(self, turn, target, directory, session_id=None, rebuild=None):
        resumed = self.ensure_session(session_id)
        if session_id and not resumed:
            turn = rebuild or turn
        if target.get('model'):
            item = pair_from_identifier(target['model'])
            if not item:
                raise AgentError('所选模型标识无效，请刷新本地模型列表。')
            self.set_option((MODEL,), json.dumps(list(item), ensure_ascii=False, separators=(',', ':')))
        if target.get('effort'):
            self.set_option((EFFORT, 'thought_level'), target['effort'])
        if turn.images and not self.init.get('agentCapabilities', {}).get('promptCapabilities', {}).get('image'):
            raise AgentError('这个本地 agent 不支持图片输入，请选择支持画面的模型。')
        text = turn.text if self.persona or not turn.instructions else turn.instructions + '\n\n' + turn.text
        blocks = [{'type': 'text', 'text': text}]
        blocks.extend({'type': 'image', 'data': image['data'], 'mimeType': image['mime']} for image in turn.images)
        self.parts = []
        self.accept_text = True
        try:
            response = self.rpc('session/prompt', {'sessionId': self.session_id, 'prompt': blocks}, timeout=900)
        except BaseException:
            try:
                self.wire.notify('session/cancel', {'sessionId': self.session_id})
            except AgentError:
                pass
            raise
        finally:
            self.accept_text = False
        text = ''.join(self.parts).strip()
        if not text or response.get('stopReason') in ('cancelled', 'refusal'):
            raise AgentError('本地 agent 没有返回可保存的回答，请检查模型能力。')
        if response.get('stopReason') not in ('end_turn', None):
            text += '\n\n（本次回答因模型限制提前结束。）'
        option = self.option(MODEL)
        return Result(text, self.session_id, {}, identifier(option.get('currentValue')) if option else
                      target.get('model', ''))
