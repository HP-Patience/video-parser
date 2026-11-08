"""Codex App Server adapter. Protocol semantics informed by Claudian; implementation uses Python stdlib."""
import json
import re
import time

from .transport import Wire, AgentError, RpcError, upstream_failure
from .types import Result, Progress, command_entries, image_files, valid_session


class Runtime:
    def __init__(self, workspace):
        self.workspace = workspace
        self.wire = Wire('codex', workspace, ['app-server', '--listen', 'stdio://',
            '-c', 'mcp_servers={}', '-c', 'features.shell_tool=false', '-c', 'features.unified_exec=false',
            '-c', 'features.view_image=false', '-c', 'features.plugins=false', '-c', 'project_doc_max_bytes=0'])
        try:
            self.wire.request('initialize', {'clientInfo': {'name': 'video_parser', 'version': '3.0.0'},
                                             'capabilities': {'experimentalApi': True}})
            self.wire.notify('initialized')
        except BaseException:
            self.wire.close()
            raise

    def close(self):
        self.wire.close()

    def catalog(self):
        models = []
        data = self.wire.request('model/list', {'limit': 100})
        for item in data.get('data', [])[:100]:
            identifier = item.get('model') or item.get('id')
            if identifier:
                models.append({'id': identifier, 'label': item.get('displayName') or identifier,
                    'efforts': [entry['reasoningEffort'] for entry in item.get('supportedReasoningEfforts', [])
                                if isinstance(entry.get('reasoningEffort'), str)],
                    'images': ('image' in item['inputModalities']) if 'inputModalities' in item else None})
        skills = self.wire.request('skills/list', {'cwds': [str(self.workspace)]})
        entries = [skill for row in skills.get('data', []) for skill in row.get('skills', []) if skill.get('enabled')]
        return {'models': models, 'commands': command_entries(entries, '$')}

    def run(self, turn, target, directory, session_id=None, rebuild=None):
        session_id = valid_session(session_id)
        params = {'cwd': str(self.workspace), 'sandbox': 'read-only', 'approvalPolicy': 'never',
                  'baseInstructions': turn.instructions}
        if target.get('model'):
            params['model'] = target['model']
        if turn.purpose != 'chat':
            params['ephemeral'] = True
        if session_id:
            try:
                started = self.wire.request('thread/resume', {**params, 'threadId': session_id, 'excludeTurns': True})
            except RpcError as error:
                if not error.missing_session():
                    raise
                session_id = None
                turn = rebuild or turn
                params['baseInstructions'] = turn.instructions
        if not session_id:
            started = self.wire.request('thread/start', params)
        thread_id = valid_session(started['thread']['id'])
        model = started.get('model') or target.get('model', '')
        segments, phases, usage = {}, {}, {}
        finished = []
        progress = Progress('Codex')

        def event(message):
            method, value = message.get('method', ''), message.get('params', {})
            if value.get('threadId') not in (None, thread_id):
                return
            if method == 'item/agentMessage/delta':
                key = value.get('itemId', 'assistant')
                delta = value.get('delta', '')
                segments[key] = segments.get(key, '') + delta
                progress.text(delta)
            elif method == 'item/completed':
                item = value.get('item', {})
                if item.get('type') == 'agentMessage':
                    key = item.get('id', 'assistant')
                    segments[key] = item.get('text', segments.get(key, ''))
                    phases[key] = item.get('phase')
            elif method == 'thread/tokenUsage/updated':
                total = value.get('tokenUsage', {}).get('last', {})
                usage.update(prompt_tokens=total.get('inputTokens'), completion_tokens=total.get('outputTokens'))
            elif method == 'turn/completed':
                finished.append(value.get('turn', {}))
            elif method == 'error' and value.get('willRetry') is not True:
                # Keep the agent's own reason: quota/auth/limit problems must not all read the same.
                raise upstream_failure(value.get('error') or value)

        def request(method, params):
            if method in ('item/commandExecution/requestApproval', 'item/fileChange/requestApproval'):
                return {'decision': 'decline'}
            if method == 'item/tool/requestUserInput':
                return {'answers': {}}
            raise RpcError(-32601)

        self.wire.on_event, self.wire.on_request = event, request
        inputs = [{'type': 'text', 'text': turn.text, 'text_elements': []}]
        inputs.extend({'type': 'localImage', 'path': path} for path in image_files(turn, directory))
        if turn.purpose == 'chat':
            names = set(re.findall(r'(?:^|\s)\$([A-Za-z0-9_:-]+)', turn.question))
            if names:
                skills = self.wire.request('skills/list', {'cwds': [str(self.workspace)]})
                for row in skills.get('data', []):
                    for skill in row.get('skills', []):
                        if skill.get('enabled') and skill.get('name') in names:
                            inputs.append({'type': 'skill', 'name': skill['name'], 'path': skill['path']})
        options = {'threadId': thread_id, 'input': inputs, 'approvalPolicy': 'never',
                   'sandboxPolicy': {'type': 'readOnly', 'networkAccess': False}}
        if target.get('model'):
            options['model'] = target['model']
        if target.get('effort'):
            options['effort'] = target['effort']
        # Only a method-not-found failure is safe to retry elsewhere; paid turns are never replayed.
        response = self.wire.request('turn/start', options, timeout=60)
        turn_id = response['turn']['id']
        deadline = time.monotonic() + 900
        try:
            while not any(item.get('id') == turn_id for item in finished):
                self.wire.dispatch(self.wire.read(deadline))
        except BaseException:
            try:
                self.wire.notify('turn/interrupt', {'threadId': thread_id, 'turnId': turn_id})
            except AgentError:
                pass
            raise
        completed = next(item for item in finished if item.get('id') == turn_id)
        if completed.get('status') not in ('completed', None) or completed.get('error'):
            raise upstream_failure(completed.get('error') or {'status': completed.get('status')})
        final_keys = [key for key in segments if phases.get(key) == 'final_answer']
        text = '\n\n'.join(segments[key] for key in (final_keys or list(segments))).strip()
        if not text:
            raise AgentError('本地 agent 没有返回可保存的回答，请检查模型能力。')
        return Result(text, thread_id, usage, model)
