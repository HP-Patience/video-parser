"""Local-only cloud client. Credentials are ephemeral; durable work is account/library scoped."""
from contextlib import contextmanager
import hashlib
import json
import logging
from pathlib import Path
import random
import sqlite3
import threading
import time
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import Request, build_opener, HTTPRedirectHandler
from uuid import uuid4

from video_parser.services import library, cloud_materials as materials

logger = logging.getLogger(__name__)


class CloudError(ValueError):
    def __init__(self, message, status=400):
        super().__init__(message)
        self.status = status


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise CloudError('云服务器发生重定向，请直接填写最终地址。')


def normalize_server(value):
    if not isinstance(value, str):
        raise CloudError('请填写云服务器地址，例如 https://cloud.example.com 或 http://203.0.113.10:8090。')
    url = urlsplit(value.strip())
    if (url.scheme not in ('https', 'http')) or not url.hostname or url.username or url.password or url.query or url.fragment or url.path not in ('', '/'):
        raise CloudError('云服务器地址须以 http:// 或 https:// 开头，且不能带账号、路径或参数。')
    try:
        port = url.port
    except ValueError:
        raise CloudError('云服务器端口无效。') from None
    host = url.hostname.lower()
    host = '[' + host + ']' if ':' in host else host
    return url.scheme + '://' + host + (':' + str(port) if port else '')


def version_batches(versions):
    batches, batch = [], []
    for version in versions:
        candidate = batch + [version]
        if len(candidate) > 200 or len(json.dumps(candidate, ensure_ascii=False).encode('utf-8')) > 800000:
            if not batch:
                raise CloudError('单条材料描述超过云端限制，请精简材料说明。')
            batches.append(batch)
            batch = [version]
        else:
            batch = candidate
    return batches + [batch] if batch or not batches else batches


class CloudSync:
    def __init__(self, root=None, transport=None):
        self.root = Path(root or library.LIBRARY_ROOT).resolve()
        self.state_dir = library.contained(self.root, '.cloud-sync')
        self.state_dir.mkdir(parents=True, exist_ok=True)
        self.db_path = library.contained(self.state_dir, 'queue.sqlite3')
        if self.db_path.is_symlink():
            raise CloudError('同步数据库路径不安全。')
        self.lock = threading.RLock()
        self.stop_event = threading.Event()
        self.thread = None
        self.auth = None
        self.transport = transport or self._http
        self.last_poll = 0
        self.active = threading.local()
        with self.db() as db:
            db.executescript('''
                PRAGMA journal_mode=WAL;
                CREATE TABLE IF NOT EXISTS projects (
                    scope TEXT, cloud_id TEXT, local_id TEXT, enabled INTEGER DEFAULT 0,
                    fingerprint TEXT DEFAULT '', manifest TEXT, PRIMARY KEY(scope,cloud_id));
                CREATE TABLE IF NOT EXISTS tasks (
                    id TEXT PRIMARY KEY, scope TEXT NOT NULL, project_id TEXT NOT NULL,
                    action TEXT NOT NULL, payload TEXT NOT NULL, status TEXT NOT NULL,
                    attempts INTEGER DEFAULT 0, next_at REAL DEFAULT 0, error TEXT DEFAULT '',
                    created_at REAL DEFAULT 0, started_at REAL DEFAULT 0, finished_at REAL DEFAULT 0);
                CREATE TABLE IF NOT EXISTS snapshots (
                    task_id TEXT, hash TEXT, data BLOB, PRIMARY KEY(task_id,hash));
            ''')
            columns = {row['name'] for row in db.execute('PRAGMA table_info(tasks)')}
            for name in ('created_at', 'started_at', 'finished_at'):
                if name not in columns:
                    # Older queues keep their rows; the missing timestamps stay unavailable.
                    db.execute('ALTER TABLE tasks ADD COLUMN ' + name + ' REAL DEFAULT 0')
            db.execute("UPDATE tasks SET status='retry' WHERE status='running'")

    @contextmanager
    def db(self):
        db = sqlite3.connect(self.db_path, timeout=10)
        db.row_factory = sqlite3.Row
        try:
            with db:
                yield db
        finally:
            db.close()

    def _check_root(self):
        if Path(library.LIBRARY_ROOT).resolve() != self.root:
            raise CloudError('视频库已切换，请重新登录云端。')

    def session(self):
        with self.lock:
            if self.auth and (Path(library.LIBRARY_ROOT).resolve() != self.root or self.auth['expires_at'] <= time.time()):
                self.auth = None
            return {'logged_in': bool(self.auth), 'state': 'logged_in' if self.auth else 'logged_out',
                    'server': self.auth['server'] if self.auth else '', 'login': self.auth['login'] if self.auth else ''}

    def _authenticated(self):
        with self.lock:
            self._check_root()
            if not self.session()['logged_in']:
                raise CloudError('请先登录云端账号。', 401)
            return dict(self.auth)

    def _http(self, auth, method, path, payload=None, raw=None, mime=None):
        headers = {'Accept': 'application/json'}
        if auth.get('token'):
            headers['Authorization'] = 'Bearer ' + auth['token']
        data = raw
        if payload is not None:
            data = json.dumps(payload, ensure_ascii=False).encode('utf-8')
            headers['Content-Type'] = 'application/json'
        if raw is not None:
            headers['Content-Type'] = mime
        try:
            with build_opener(NoRedirect()).open(Request(auth['server'] + path, data=data, headers=headers, method=method), timeout=20) as response:
                body = response.read(materials.MAX_FILE + 1)
                if len(body) > materials.MAX_FILE:
                    raise CloudError('云端响应超过安全限制。')
                return body if '/blobs/' in path and method == 'GET' else json.loads(body or b'{}')
        except HTTPError as error:
            messages = {401: '云端登录已过期，请重新登录。', 403: '无权访问该云端材料。',
                        404: '云端项目或材料不存在。', 409: '云端已有更新，请重新同步后处理版本。',
                        410: '项目已进入云端回收站，本机修改已保留。', 413: '材料超过云端容量限制。',
                        422: '云端拒绝了材料格式，请检查项目。', 429: '云端请求过于频繁，稍后重试。'}
            raise CloudError(messages.get(error.code, '云服务器暂时不可用，请稍后重试。'), error.code) from None
        except (URLError, TimeoutError, OSError, json.JSONDecodeError):
            raise CloudError('无法连接云服务器，请检查地址和网络。', 503) from None

    def checkpoint(self, auth):
        with self.lock:
            current = self._authenticated()
            if current['scope'] != auth['scope'] or current['token'] != auth['token']:
                raise CloudError('账号已切换，原同步任务已暂停。', 401)
            task_id = getattr(self.active, 'task_id', None)
            if task_id:
                with self.db() as db:
                    task = db.execute('SELECT status FROM tasks WHERE id=?', (task_id,)).fetchone()
                if self.stop_event.is_set() or not task or task['status'] != 'running':
                    raise CloudError('同步任务已暂停。', 408)

    def request(self, auth, method, path, payload=None, raw=None, mime=None):
        self.checkpoint(auth)
        try:
            result = self.transport(auth, method, path, payload, raw, mime)
        except CloudError as error:
            if error.status == 401:
                with self.lock:
                    if self.auth and self.auth['token'] == auth['token']:
                        self.auth = None
            raise
        self.checkpoint(auth)
        return result

    def login(self, server, login, password):
        server = normalize_server(server)
        if not isinstance(login, str) or not login.strip() or not isinstance(password, str) or not password or len(password) > 4096:
            raise CloudError('请输入用户名和密码。')
        with self.lock:
            self._check_root()
            self.auth = None
            try:
                reply = self.transport({'server': server}, 'POST', '/v1/auth/login', {'login': login.strip(), 'password': password}, None, None)
            except CloudError as error:
                if error.status == 401:
                    raise CloudError('用户名或密码不正确。', 401) from None
                raise
            user = reply['user']
            owner = materials.identifier(user['id'])
            scope = hashlib.sha256((server + '\n' + owner + '\n' + str(self.root)).encode()).hexdigest()
            if not isinstance(reply.get('token'), str) or not reply['token'] or type(reply.get('expires_at')) not in (int, float):
                raise CloudError('云服务器登录响应无效。')
            self.auth = {'server': server, 'token': reply['token'], 'login': user.get('login') or user.get('email') or '', 'owner': owner,
                         'scope': scope, 'expires_at': reply['expires_at']}
            self.enqueue('refresh', '', {})
        self.start()
        return self.session()

    def logout(self):
        with self.lock:
            old, self.auth = self.auth, None
        if old:
            try:
                self.transport(old, 'DELETE', '/v1/auth/session', None, None, None)
            except (CloudError, OSError):
                pass
        return self.session()

    def enqueue(self, action, project_id, payload):
        auth = self._authenticated()
        task_id = uuid4().hex
        with self.db() as db:
            if action == 'refresh':
                existing = db.execute("SELECT id FROM tasks WHERE scope=? AND action='refresh' AND status IN ('queued','running','retry') LIMIT 1", (auth['scope'],)).fetchone()
                if existing:
                    return {'task_id': existing['id'], 'status': 'queued'}
            db.execute('INSERT INTO tasks(id,scope,project_id,action,payload,status,created_at) VALUES(?,?,?,?,?,?,?)',
                       (task_id, auth['scope'], project_id, action, json.dumps(payload), 'queued', time.time()))
        return {'task_id': task_id, 'status': 'queued'}

    def _mapping(self, auth, cloud_id):
        with self.db() as db:
            row = db.execute('SELECT * FROM projects WHERE scope=? AND cloud_id=?', (auth['scope'], cloud_id)).fetchone()
        return dict(row) if row else None

    def _local_mapping(self, auth, local_id):
        with self.db() as db:
            row = db.execute('SELECT * FROM projects WHERE scope=? AND local_id=?', (auth['scope'], local_id)).fetchone()
        return dict(row) if row else None

    def upload(self, project_id, independent=False):
        with self.lock:
            auth = self._authenticated()
            project_id = materials.identifier(project_id)
            snap = materials.snapshot(project_id)
            project = library.get(project_id)
            if project.get('cloud_scope') not in (None, auth['scope']) or any(
                    r.get('cloud_scope') not in (None, auth['scope']) for r in project['resources'].values()):
                raise CloudError('此项目属于另一云端账号，请切回原账号。')
            mapping = None if independent else self._local_mapping(auth, project_id)
            cloud_id = uuid4().hex if independent else mapping['cloud_id'] if mapping else project_id
            manifest = json.loads(mapping['manifest']) if mapping and mapping['manifest'] else None
            baseline = project.get('cloud_baseline', {})
            conflicts = project.get('cloud_conflicts', {})
            patch = {k: v for k, v in snap['metadata'].items() if k not in conflicts and (not manifest or k not in baseline or baseline[k] != v)}
            payload = {'metadata': snap['metadata'], 'metadata_patch': patch,
                       'metadata_base': manifest.get('metadata', {}) if manifest else {},
                       'versions': [materials.portable_version(v) for v in snap['versions']],
                       'base_revision': manifest['revision'] if manifest else 0}
            task_id = uuid4().hex
            fingerprint = materials.sha(json.dumps(payload['versions'], sort_keys=True).encode() + json.dumps(snap['metadata'], sort_keys=True).encode())
            with self.db() as db:
                local_id = hashlib.sha256((auth['scope'] + ':' + cloud_id).encode()).hexdigest()[:32] if independent else project_id
                db.execute('INSERT OR IGNORE INTO projects(scope,cloud_id,local_id) VALUES(?,?,?)', (auth['scope'], cloud_id, local_id))
                db.execute('INSERT INTO tasks(id,scope,project_id,action,payload,status,created_at) VALUES(?,?,?,?,?,?,?)',
                           (task_id, auth['scope'], cloud_id, 'upload', json.dumps(payload), 'queued', time.time()))
                for row in snap['versions']:
                    db.execute('INSERT OR IGNORE INTO snapshots VALUES(?,?,?)', (task_id, row['sha256'], row['_data']))
                db.execute('UPDATE projects SET fingerprint=? WHERE scope=? AND cloud_id=?', (fingerprint, auth['scope'], cloud_id))
            return {'task_id': task_id, 'status': 'queued'}

    def resolve_metadata(self, project_id, field, choice):
        if field not in ('title', 'learned') or choice not in ('local', 'cloud'):
            raise CloudError('请选择有效的冲突处理方式。')
        auth = self._authenticated()
        project = library.get(materials.identifier(project_id))
        mapping = self._local_mapping(auth, project_id)
        if not mapping or field not in project.get('cloud_conflicts', {}):
            raise CloudError('该项目没有待处理的字段冲突。')
        return self.enqueue('resolve-metadata', mapping['cloud_id'],
                            {'field': field, 'choice': choice, 'value': project[field]})

    def associate(self, local_project_id, cloud_project_id):
        materials.identifier(local_project_id)
        materials.identifier(cloud_project_id)
        auth = self._authenticated()
        local = library.get(local_project_id)
        if local.get('cloud_scope') not in (None, auth['scope']) or any(
                r.get('cloud_scope') not in (None, auth['scope']) for r in local['resources'].values()):
            raise CloudError('不能关联另一账号的本机项目。')
        return self.enqueue('associate', cloud_project_id, {'local_project_id': local_project_id})

    def sync_mode(self, project_id, enabled):
        if type(enabled) is not bool:
            raise CloudError('同步开关无效。')
        auth = self._authenticated()
        mapping = self._local_mapping(auth, project_id)
        if not mapping:
            self.upload(project_id)
            mapping = self._local_mapping(auth, project_id)
        with self.db() as db:
            db.execute('UPDATE projects SET enabled=? WHERE scope=? AND cloud_id=?', (int(enabled), auth['scope'], mapping['cloud_id']))
        return {'project_id': project_id, 'enabled': enabled}

    def projects(self):
        auth = self._authenticated()
        with self.db() as db:
            rows = [dict(r) for r in db.execute('SELECT * FROM projects WHERE scope=?', (auth['scope'],))]
        local = []
        for path in library.manifests():
            try:
                project = library.load(path)
                if project.get('cloud_scope') not in (None, auth['scope']):
                    continue
                local.append({'id': project['id'], 'title': project['title'],
                              'metadata_conflicts': project.get('cloud_conflicts', {}),
                               'sync_enabled': any(r['local_id'] == project['id'] and r['enabled'] for r in rows)})
            except (OSError, ValueError, KeyError):
                continue
        cloud = []
        for row in rows:
            if row['manifest']:
                project = json.loads(row['manifest'])
                cloud.append({'id': row['cloud_id'], 'title': project.get('metadata', {}).get('title', '云端项目'),
                              'local_id': row['local_id'], **{k: project.get(k) for k in ('revision', 'deleted_at', 'materials')}})
        return {'local': local, 'cloud': cloud}

    def transfers(self):
        auth = self._authenticated()
        with self.db() as db:
            # The poller refreshes the project list every 30 seconds. Only the newest finished
            # refresh is history worth showing, and a later success supersedes older failures;
            # otherwise refreshes would fill the whole list.
            rows = [dict(r) for r in db.execute(
                "SELECT id,project_id,action,status,attempts,error,payload,next_at,created_at,started_at,finished_at "
                "FROM tasks t WHERE scope=? AND NOT (action='refresh' AND ("
                "  (status IN ('completed','cancelled') AND EXISTS (SELECT 1 FROM tasks n WHERE n.scope=t.scope"
                "     AND n.action='refresh' AND n.status IN ('completed','cancelled') AND n.rowid>t.rowid))"
                "  OR (status='failed' AND EXISTS (SELECT 1 FROM tasks n WHERE n.scope=t.scope"
                "     AND n.action='refresh' AND n.status='completed' AND n.rowid>t.rowid)))) "
                'ORDER BY rowid DESC LIMIT 100', (auth['scope'],))]
            known = {row['cloud_id']: row for row in db.execute('SELECT cloud_id,local_id,manifest FROM projects WHERE scope=?', (auth['scope'],))}
        titles = {}
        for path in library.manifests():
            try:
                project = library.load(path)
            except (OSError, ValueError, KeyError):
                continue
            if project.get('cloud_scope') in (None, auth['scope']):
                titles[project['id']] = project.get('title', '')
        for row in rows:
            mapping = known.get(row['project_id'])
            if mapping and mapping['manifest']:
                cloud_project = json.loads(mapping['manifest'])
                titles.setdefault(row['project_id'], cloud_project.get('metadata', {}).get('title', ''))
            row['project_title'] = ((mapping and titles.get(mapping['local_id']))
                                    or titles.get(row['project_id'])
                                    or (mapping['local_id'] if mapping else row['project_id']))
            row['summary'] = self._task_summary(row['action'], row['payload'])
            row.pop('payload', None)
        return {'transfers': rows}

    @staticmethod
    def _task_summary(action, payload):
        """Human-sized facts about what a queued task is about; never local paths or tokens."""
        try:
            data = json.loads(payload or '{}')
        except ValueError:
            return {}
        summary = {}
        if action == 'upload':
            versions = data.get('versions') or []
            summary['version_count'] = len(versions)
            summary['byte_count'] = sum(int(v.get('size') or 0) for v in versions)
            summary['revision_base'] = data.get('base_revision')
        elif action in ('pull', 'image'):
            summary['full'] = data.get('full') is True
            if data.get('material_id'):
                summary['material_id'] = data['material_id']
                summary['version_id'] = data.get('version_id')
        elif action == 'select-version':
            summary['material_id'] = data.get('material_id')
            summary['version_id'] = data.get('version_id')
        elif action == 'resolve-metadata':
            summary['field'] = data.get('field')
            summary['choice'] = data.get('choice')
        elif action == 'associate':
            summary['local_project_id'] = data.get('local_project_id')
        progress = data.get('_progress') or {}
        if progress.get('project_count') is not None:
            summary['project_count'] = progress['project_count']
        return {k: v for k, v in summary.items() if v is not None}

    def task_control(self, task_id, cancel=False):
        with self.lock:
            auth = self._authenticated()
            with self.db() as db:
                row = db.execute('SELECT status,action,payload FROM tasks WHERE id=? AND scope=?', (task_id, auth['scope'])).fetchone()
                if not row:
                    raise CloudError('同步任务不存在。', 404)
                if row['status'] == 'completed':
                    raise CloudError('该任务已经完成。')
                if not cancel and row['status'] == 'conflict' and row['action'] in ('delete', 'restore', 'select-version', 'resolve-metadata'):
                    payload = json.loads(row['payload'])
                    payload.pop('_body', None)
                    payload['_operation_id'] = uuid4().hex
                    db.execute('UPDATE tasks SET payload=? WHERE id=?', (json.dumps(payload), task_id))
                db.execute('UPDATE tasks SET status=?,next_at=0,error=?,finished_at=? WHERE id=? AND scope=?',
                           ('cancelled' if cancel else 'queued', '', time.time() if cancel else 0, task_id, auth['scope']))
            return {'task_id': task_id, 'status': 'cancelled' if cancel else 'queued'}

    def versions(self, project_id):
        auth = self._authenticated()
        row = self._mapping(auth, materials.identifier(project_id))
        return {'materials': json.loads(row['manifest'])['materials'] if row and row['manifest'] else []}

    def action(self, action, project_id, **kwargs):
        materials.identifier(project_id)
        if 'material_id' in kwargs:
            materials.identifier(kwargs['material_id'])
            materials.identifier(kwargs['version_id'])
        return self.enqueue(action, project_id, kwargs)

    def _save_manifest(self, auth, project):
        with self.lock:
            self.checkpoint(auth)
            project_id = materials.identifier(project['project_id'])
            mapping = self._mapping(auth, project_id)
            local_id = mapping['local_id'] if mapping else hashlib.sha256((auth['scope'] + ':' + project_id).encode()).hexdigest()[:32]
            with self.db() as db:
                db.execute('INSERT INTO projects(scope,cloud_id,local_id,manifest) VALUES(?,?,?,?) '
                           'ON CONFLICT(scope,cloud_id) DO UPDATE SET manifest=excluded.manifest',
                           (auth['scope'], project_id, local_id, json.dumps(project)))
            return local_id

    def _ensure_local(self, auth, project):
        local_id = self._save_manifest(auth, project)
        try:
            local = library.get(local_id)
            if local.get('cloud_scope') not in (None, auth['scope']):
                raise CloudError('本机项目账号不匹配，未合并。')
            return local
        except KeyError:
            pass
        meta = project.get('metadata', {})
        directory = library.contained(self.root, 'cloud/' + local_id)
        with library.file_lock(self.root / '.library.lock'):
            if directory.exists():
                raise CloudError('本机目标目录已存在，未覆盖。')
            directory.mkdir(parents=True)
            local = {'schema_version': 1, 'id': local_id, 'platform': meta.get('platform', 'unknown'),
                     'video_id': meta.get('video_id', project['project_id']), 'part': meta.get('part'),
                     'key': meta.get('source_key', ''), 'source_url': meta.get('source_url', ''),
                     'title': meta.get('title', '云端项目'), 'created_at': meta.get('created_at', ''),
                     'learned': meta.get('learned') is True, 'identity_status': 'confirmed', 'resources': {},
                     'cloud_scope': auth['scope'], 'cloud_project_id': project['project_id']}
            library.atomic_json(directory / 'project.json', local)
        return library.get(local_id)

    def _reconcile_metadata(self, local, remote):
        baseline = local.get('cloud_baseline', {})
        previous_conflicts = local.get('cloud_conflicts', {})
        conflicts = {}
        for field in ('title', 'learned'):
            if field not in remote:
                continue
            current = local.get(field)
            if ((field in previous_conflicts and current != remote[field])
                    or (field in baseline and current != baseline[field] and remote[field] != baseline[field] and current != remote[field])
                    or (field not in baseline and local['resources'] and current != remote[field])):
                conflicts[field] = {'local': current, 'cloud': remote[field]}
            elif field not in baseline or current == baseline[field]:
                local[field] = remote[field]
        local['cloud_baseline'] = remote
        local['cloud_conflicts'] = conflicts
        library.atomic_json(local['directory'] / 'project.json', {k: v for k, v in local.items() if k != 'directory'})

    def _existing(self, local, auth, material, version):
        for name, info in local['resources'].items():
            exact = (info.get('cloud_scope') == auth['scope'] and info.get('cloud_material_id') == material['material_id']
                     and info.get('cloud_version_id') == version['version_id'])
            native = (info.get('cloud_scope') in (None, auth['scope']) and info.get('kind') == material['kind']
                      and info.get('sha256') == version['sha256']
                      and materials.material_id(name, info) == material['material_id'])
            if exact or native:
                return name, info
        return None

    def _pull(self, auth, project, full=False, only=None, select=False):
        with self.lock:
            self.checkpoint(auth)
            if project.get('deleted_at') is not None:
                self._save_manifest(auth, project)
                with self.db() as db:
                    db.execute('UPDATE projects SET enabled=0 WHERE scope=? AND cloud_id=?', (auth['scope'], project['project_id']))
                return
            local = self._ensure_local(auth, project)
            with library.file_lock(library.contained(local['directory'], '.manifest.lock')):
                local = library.get(local['id'])
                self._reconcile_metadata(local, project.get('metadata', {}))
            names = {}
            for material in project['materials']:
                ordered = sorted(material['versions'], key=lambda v: v['version_id'] != material['current_version_id'])
                used = set()
                for version in ordered:
                    known = self._existing(local, auth, material, version)
                    existing = known[0] if known else None
                    name = existing or materials.resource_name(material, version)
                    if name in used:
                        path = Path(name)
                        name = str(path.with_name(path.stem + '__' + version['version_id'][:10] + path.suffix)).replace('\\', '/')
                    used.add(name)
                    names[(material['material_id'], version['version_id'])] = name
                    if version['version_id'] == material['current_version_id']:
                        names[material['material_id']] = name
        for material in project['materials']:
            ordered = sorted(material['versions'], key=lambda v: v['version_id'] != material['current_version_id'])
            for version in ordered:
                if only and (material['material_id'], version['version_id']) != only:
                    continue
                with self.lock:
                    self.checkpoint(auth)
                    local = library.get(local['id'])
                    existing = self._existing(local, auth, material, version)
                    if existing and not existing[1].get('cloud_portable'):
                        with library.file_lock(library.contained(local['directory'], '.manifest.lock')):
                            local = library.get(local['id'])
                            local['resources'][existing[0]].update(cloud_scope=auth['scope'], cloud_missing=False,
                                cloud_material_id=material['material_id'], cloud_version_id=version['version_id'],
                                cloud_portable={'material_id': material['material_id'], 'kind': material['kind'], **version})
                            library.atomic_json(local['directory'] / 'project.json', {k: v for k, v in local.items() if k != 'directory'})
                    relative = names[(material['material_id'], version['version_id'])]
                    image = material['kind'] in materials.IMAGE_KINDS
                    missing = not existing or existing[1].get('cloud_missing')
                    if missing and image and not full and not only:
                        if version['version_id'] != material['current_version_id'] or existing:
                            continue
                        with library.file_lock(library.contained(local['directory'], '.manifest.lock')):
                            local = library.get(local['id'])
                            if relative not in local['resources']:
                                local['resources'][relative] = {'kind': material['kind'], 'size_bytes': version['size'],
                                    'sha256': version['sha256'], 'cloud_missing': True, 'cloud_scope': auth['scope'],
                                    'cloud_portable': {'material_id': material['material_id'], 'kind': material['kind'], **version},
                                    'cloud_material_id': material['material_id'], 'cloud_version_id': version['version_id']}
                                library.atomic_json(local['directory'] / 'project.json', {k: v for k, v in local.items() if k != 'directory'})
                        continue
                if missing:
                    data = self.request(auth, 'GET', '/v1/projects/' + project['project_id'] + '/blobs/' + version['sha256'])
                    with self.lock:
                        self.checkpoint(auth)
                        relative = materials.import_version(local['id'], material, version, data, auth['scope'], names, target=relative)
                        names[(material['material_id'], version['version_id'])] = relative
                        if version['version_id'] == material['current_version_id']:
                            names[material['material_id']] = relative
                if select and version['version_id'] == material['current_version_id']:
                    with self.lock:
                        self.checkpoint(auth)
                        with library.file_lock(library.contained(local['directory'], '.manifest.lock')):
                            local = library.get(local['id'])
                            # Native library.find consumes insertion order. Move chosen version first; keep every old file.
                            local['resources'] = {relative: local['resources'][relative], **{k: v for k, v in local['resources'].items() if k != relative}}
                            library.atomic_json(local['directory'] / 'project.json', {k: v for k, v in local.items() if k != 'directory'})

    def _execute(self, auth, task):
        payload = json.loads(task['payload'])
        project_id = task['project_id']
        path = '/v1/projects/' + project_id
        if task['action'] == 'associate':
            remote = self.request(auth, 'GET', path)
            with self.lock:
                self.checkpoint(auth)
                local = library.get(payload['local_project_id'])
                if materials.project_metadata(local)['source_key'] != remote.get('metadata', {}).get('source_key'):
                    raise CloudError('两个项目来源不同，请先检查视频和分 P。', 409)
                if local.get('cloud_scope') not in (None, auth['scope']) or any(
                        r.get('cloud_scope') not in (None, auth['scope']) for r in local['resources'].values()):
                    raise CloudError('不能关联另一账号的本机项目。')
                with self.db() as db:
                    db.execute('DELETE FROM projects WHERE scope=? AND local_id=? AND cloud_id!=?',
                               (auth['scope'], local['id'], project_id))
                    db.execute('INSERT INTO projects(scope,cloud_id,local_id,manifest) VALUES(?,?,?,?) '
                               'ON CONFLICT(scope,cloud_id) DO UPDATE SET local_id=excluded.local_id,manifest=excluded.manifest',
                               (auth['scope'], project_id, local['id'], json.dumps(remote)))
            self._pull(auth, remote)
            self.upload(local['id'])
            return
        if task['action'] == 'refresh':
            pulled = 0
            for deleted in ('false', 'true'):
                offset = 0
                while True:
                    result = self.request(auth, 'GET', '/v1/projects?deleted=' + deleted + '&limit=100&offset=' + str(offset))
                    for project in result['projects']:
                        self._pull(auth, project, select=True)
                        pulled += 1
                    if len(result['projects']) < 100:
                        break
                    offset += 100
            payload['_progress'] = {'project_count': pulled}
            with self.db() as db:
                db.execute('UPDATE tasks SET payload=? WHERE id=?', (json.dumps(payload), task['id']))
            return
        if task['action'] == 'upload':
            base = payload['base_revision']
            include_metadata = True
            if base == 0:
                try:
                    created = self.request(auth, 'GET', path)
                    include_metadata = False
                except CloudError as error:
                    if error.status != 404:
                        raise
                    try:
                        created = self.request(auth, 'POST', path + '/commit',
                                               {'operation_id': task['id'] + '_create', 'base_revision': 0, 'metadata': payload['metadata']})
                    except CloudError as race:
                        if race.status != 409:
                            raise
                        created = self.request(auth, 'GET', path)
                        include_metadata = False
                if created.get('metadata', {}).get('source_key') != payload['metadata'].get('source_key'):
                    raise CloudError('云端项目来源不同，请先检查关联关系。', 409)
                if created.get('deleted_at') is not None:
                    raise CloudError('项目已进入云端回收站，本机修改已保留。', 410)
                base = created['revision']
            for version in payload['versions']:
                with self.db() as db:
                    row = db.execute('SELECT data FROM snapshots WHERE task_id=? AND hash=?', (task['id'], version['sha256'])).fetchone()
                if row is None:
                    raise CloudError('上传快照缺失，请重新上传项目。')
                self.request(auth, 'PUT', path + '/blobs/' + version['sha256'], raw=bytes(row['data']), mime=version['mime'])
            batches = version_batches(payload['versions'])
            index = payload.get('_batch_index', 0)
            result = None
            while index < len(batches):
                body = payload.get('_body') or {'operation_id': task['id'] + '_b' + str(index),
                                                'base_revision': base, 'versions': batches[index]}
                if not payload.get('_body') and include_metadata and index == 0:
                    body['metadata'] = payload.get('metadata_patch', payload['metadata'])
                for rebase in range(4):
                    payload['_body'] = body
                    payload['_batch_index'] = index
                    with self.db() as db:
                        db.execute('UPDATE tasks SET payload=? WHERE id=?', (json.dumps(payload), task['id']))
                    try:
                        result = self.request(auth, 'POST', path + '/commit', body)
                        break
                    except CloudError as error:
                        if error.status != 409 or rebase == 3:
                            raise
                        current = self.request(auth, 'GET', path)
                        if current.get('deleted_at') is not None:
                            raise CloudError('项目已进入云端回收站，本机修改已保留。', 410)
                        body = {'operation_id': task['id'] + '_' + uuid4().hex[:8],
                                'base_revision': current['revision'], 'versions': batches[index]}
                        patch = {k: v for k, v in payload.get('metadata_patch', {}).items()
                                 if current.get('metadata', {}).get(k) == payload.get('metadata_base', {}).get(k)}
                        if patch and index == 0:
                            body['metadata'] = patch
                base = result['revision']
                index += 1
                payload['_batch_index'] = index
                payload.pop('_body', None)
                with self.db() as db:
                    db.execute('UPDATE tasks SET payload=? WHERE id=?', (json.dumps(payload), task['id']))
            self._pull(auth, result or self.request(auth, 'GET', path))
        elif task['action'] in ('delete', 'restore', 'select-version', 'resolve-metadata'):
            project = self.request(auth, 'GET', path)
            if task['action'] == 'resolve-metadata' and payload['choice'] == 'cloud':
                with self.lock:
                    self.checkpoint(auth)
                    local = self._ensure_local(auth, project)
                    with library.file_lock(library.contained(local['directory'], '.manifest.lock')):
                        local = library.get(local['id'])
                        local[payload['field']] = project['metadata'][payload['field']]
                        library.atomic_json(local['directory'] / 'project.json', {k: v for k, v in local.items() if k != 'directory'})
                self._pull(auth, project)
                return
            body = {'operation_id': payload.get('_operation_id', task['id']), 'base_revision': project['revision']}
            endpoint = '/' + task['action']
            if task['action'] == 'resolve-metadata':
                endpoint = '/commit'
                body['metadata'] = {payload['field']: payload['value']}
            if task['action'] == 'select-version':
                endpoint = '/commit'
                body['selections'] = {payload['material_id']: payload['version_id']}
            # Persist the CAS payload before transmission so a lost response replays identically.
            if payload.get('_body'):
                body = payload['_body']
            else:
                payload['_body'] = body
                with self.db() as db:
                    db.execute('UPDATE tasks SET payload=? WHERE id=?', (json.dumps(payload), task['id']))
            result = self.request(auth, 'POST', path + endpoint, body)
            self._save_manifest(auth, result)
            if task['action'] == 'resolve-metadata':
                self._pull(auth, result)
            if task['action'] == 'select-version':
                self._pull(auth, result, True, (payload['material_id'], payload['version_id']), select=True)
        else:
            project = self.request(auth, 'GET', path)
            self._pull(auth, project, payload.get('full') is True,
                       (payload['material_id'], payload['version_id']) if task['action'] == 'image' else None)

    def run_once(self):
        with self.lock:
            if not self.session()['logged_in']:
                return False
            auth = self._authenticated()
            with self.db() as db:
                task = db.execute("SELECT * FROM tasks WHERE scope=? AND status IN ('queued','retry') AND next_at<=? ORDER BY rowid LIMIT 1",
                                  (auth['scope'], time.time())).fetchone()
                if not task:
                    return False
                task = dict(task)
                db.execute("UPDATE tasks SET status='running',attempts=attempts+1,started_at=CASE WHEN started_at>0 THEN started_at ELSE ? END,finished_at=0 WHERE id=?",
                           (time.time(), task['id']))
            self.active.task_id = task['id']
        try:
            self._execute(auth, task)
        except Exception as error:
            status = getattr(error, 'status', 400)
            retry = status in (401, 408, 429, 500, 502, 503, 504)
            message = str(error) if isinstance(error, CloudError) else '同步材料处理失败，请检查本机资源后重试。'
            if not isinstance(error, CloudError):
                logger.exception('cloud sync task %s (%s) failed', task['id'], task['action'])
            state = 'retry' if retry else 'conflict' if status in (409, 410) else 'failed'
            with self.db() as db:
                db.execute("UPDATE tasks SET status=?,next_at=?,error=?,finished_at=? WHERE id=? AND status='running'",
                           (state, time.time() + min(300, 2 ** min(task['attempts'] + 1, 8)) + random.random(), message, time.time(), task['id']))
        else:
            with self.db() as db:
                db.execute("UPDATE tasks SET status='completed',error='',finished_at=? WHERE id=? AND status='running'",
                           (time.time(), task['id']))
                db.execute('DELETE FROM snapshots WHERE task_id=? AND EXISTS(SELECT 1 FROM tasks WHERE id=? AND status=?)',
                           (task['id'], task['id'], 'completed'))
        finally:
            self.active.task_id = None
        return True

    def poll(self):
        with self.lock:
            auth = self._authenticated()
            with self.db() as db:
                rows = [dict(r) for r in db.execute('SELECT * FROM projects WHERE scope=? AND enabled=1', (auth['scope'],))]
            first_error = None
            for row in rows:
                with self.db() as db:
                    pending = db.execute("SELECT 1 FROM tasks WHERE scope=? AND project_id=? AND status IN ('queued','retry','running','conflict') LIMIT 1",
                                         (auth['scope'], row['cloud_id'])).fetchone()
                if pending:
                    continue
                try:
                    snap = materials.snapshot(row['local_id'])
                    fingerprint = materials.sha(json.dumps([materials.portable_version(v) for v in snap['versions']], sort_keys=True).encode()
                                                + json.dumps(snap['metadata'], sort_keys=True).encode())
                    if fingerprint != row['fingerprint']:
                        self.upload(row['local_id'])
                except (OSError, ValueError, KeyError) as error:
                    # Finish the other projects and remote refresh before reporting a local failure.
                    if first_error is None:
                        first_error = error
            self.enqueue('refresh', '', {})
            if first_error is not None:
                raise first_error

    def _loop(self):
        while not self.stop_event.is_set():
            try:
                if self.session()['logged_in']:
                    if time.monotonic() - self.last_poll > 30:
                        # A damaged auto-sync project must not starve queued work.
                        self.last_poll = time.monotonic()
                        try:
                            self.poll()
                        except (CloudError, OSError, ValueError, KeyError, sqlite3.Error):
                            pass
                    if self.run_once():
                        continue
            except (CloudError, OSError, ValueError, sqlite3.Error):
                pass
            self.stop_event.wait(1)

    def start(self):
        with self.lock:
            if self.thread and self.thread.is_alive():
                return
            self.stop_event.clear()
            self.thread = threading.Thread(target=self._loop, name='cloud-sync', daemon=True)
            self.thread.start()

    def stop(self):
        self.stop_event.set()
        if self.thread:
            self.thread.join(timeout=2)
        # Session is deliberately not persisted across process restarts.


_client = None
_client_lock = threading.RLock()


def client():
    global _client
    with _client_lock:
        if _client is None or _client.root != Path(library.LIBRARY_ROOT).resolve():
            if _client:
                _client.stop()
                _client.auth = None
            _client = CloudSync()
        return _client


def reusable_project(default_cloud_id, source_key):
    service = _client
    if service is None:
        return None
    with service.lock:
        if not service.session()['logged_in']:
            return None
        auth = service._authenticated()
        mapping = service._mapping(auth, default_cloud_id)
        remote = json.loads(mapping['manifest']) if mapping and mapping['manifest'] else None
        if not remote or remote.get('deleted_at') is not None or remote.get('metadata', {}).get('source_key') != source_key:
            return None
        try:
            local = library.get(mapping['local_id'])
        except (KeyError, OSError, ValueError):
            return None
        return local['id'] if local.get('cloud_scope') in (None, auth['scope']) else None


@contextmanager
def library_change():
    """Coordinate the library's root assignment with local cloud publication."""
    service = _client
    if service is None:
        yield
        return
    with service.lock:
        service.stop_event.set()
        service.auth = None
        yield


def start():
    if _client:
        _client.start()


def session():
    return _client.session() if _client else {'logged_in': False, 'state': 'logged_out', 'server': '', 'login': ''}


def stop():
    if _client:
        _client.stop()


def ensure_resource(project, relative):
    service = client()
    with service.lock:
        auth = service._authenticated()
        info = project['resources'].get(relative) or {}
        if info.get('cloud_scope') != auth['scope'] or not info.get('cloud_missing'):
            raise CloudError('此图片尚未下载，请登录原云端账号后重试。')
        mapping = service._local_mapping(auth, project['id'])
        if not mapping:
            raise CloudError('未找到该图片的云端项目。')
    remote = service.request(auth, 'GET', '/v1/projects/' + mapping['cloud_id'])
    service._pull(auth, remote, True, (info['cloud_material_id'], info['cloud_version_id']))
    with service.lock:
        service.checkpoint(auth)
        refreshed = library.get(project['id'])
        project['resources'] = refreshed['resources']
        found = next((name for name, item in refreshed['resources'].items()
                      if item.get('cloud_scope') == auth['scope'] and item.get('cloud_material_id') == info['cloud_material_id']
                      and item.get('cloud_version_id') == info['cloud_version_id'] and not item.get('cloud_missing')), None)
        if not found:
            raise CloudError('图片尚未下载，请稍后重试。')
        return library.resource_path(refreshed, found)
