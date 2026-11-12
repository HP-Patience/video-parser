"""Cloud boundary regression tests; all network I/O uses an in-memory /v1 peer."""
import copy
import json
from pathlib import Path
import tempfile
import threading
import time
import unittest
from unittest.mock import patch
from urllib.parse import parse_qs, urlsplit

from video_parser.services import attachments, cloud_materials as materials, cloud_sync, library


class Peer:
    def __init__(self):
        self.projects = {}
        self.blobs = {}
        self.replays = {}
        self.calls = []
        self.lose_commit = False

    def __call__(self, auth, method, path, payload=None, raw=None, mime=None):
        self.calls.append((method, path, copy.deepcopy(payload), raw))
        if path == '/v1/auth/login':
            owner = materials.sha(payload['login'].encode())[:32]
            return {'token': 'private-token-' + owner, 'expires_at': time.time() + 3600,
                    'user': {'id': owner, 'login': payload['login']}}
        if path == '/v1/auth/session':
            return {'ok': True}
        owner = auth['owner']
        parts = urlsplit(path)
        if parts.path == '/v1/projects':
            deleted = parse_qs(parts.query).get('deleted', ['false'])[0] == 'true'
            rows = [copy.deepcopy(p) for (user, _), p in self.projects.items()
                    if user == owner and (p['deleted_at'] is not None) == deleted]
            offset = int(parse_qs(parts.query).get('offset', ['0'])[0])
            return {'projects': rows[offset:offset + 100]}
        segments = parts.path.split('/')
        project_id = segments[3]
        key = (owner, project_id)
        project = self.projects.get(key)
        if len(segments) == 4 and method == 'GET':
            if project is None:
                raise cloud_sync.CloudError('不存在', 404)
            return copy.deepcopy(project)
        if len(segments) > 5 and segments[4] == 'blobs':
            blobkey = (*key, segments[5])
            if method == 'PUT':
                assert materials.sha(raw) == segments[5]
                self.blobs[blobkey] = raw
                return {'ok': True}
            return self.blobs[blobkey]
        replay_key = (*key, payload['operation_id'])
        frozen = json.dumps(payload, sort_keys=True)
        if replay_key in self.replays:
            body, reply = self.replays[replay_key]
            if body != frozen:
                raise cloud_sync.CloudError('operation_conflict', 409)
            return copy.deepcopy(reply)
        if (project['revision'] if project else 0) != payload['base_revision']:
            raise cloud_sync.CloudError('revision_conflict', 409)
        if project is None:
            project = {'project_id': project_id, 'revision': 0, 'metadata': {}, 'materials': [], 'deleted_at': None}
            self.projects[key] = project
        endpoint = segments[4]
        assert len(payload.get('versions', [])) <= 200
        assert len(json.dumps(payload).encode()) <= 1024 * 1024
        if endpoint == 'delete':
            project['deleted_at'] = time.time()
        elif endpoint == 'restore':
            project['deleted_at'] = None
        elif project['deleted_at'] is not None:
            raise cloud_sync.CloudError('tombstone', 410)
        else:
            project['metadata'].update(payload.get('metadata', {}))
            for row in payload.get('versions', []):
                assert set(row) <= {'material_id', 'version_id', 'kind', 'sha256', 'size', 'mime', 'details'}
                material = next((m for m in project['materials'] if m['material_id'] == row['material_id']), None)
                if material is None:
                    material = {'material_id': row['material_id'], 'kind': row['kind'],
                                'current_version_id': row['version_id'], 'versions': []}
                    project['materials'].append(material)
                version = {k: v for k, v in row.items() if k not in ('material_id', 'kind')}
                previous = next((v for v in material['versions'] if v['version_id'] == row['version_id']), None)
                if previous is not None and previous != version:
                    raise cloud_sync.CloudError('version_immutable', 409)
                if previous is None:
                    assert (*key, row['sha256']) in self.blobs
                    material['versions'].append(version)
            for mid, vid in payload.get('selections', {}).items():
                material = next(m for m in project['materials'] if m['material_id'] == mid)
                assert any(v['version_id'] == vid for v in material['versions'])
                material['current_version_id'] = vid
        project['revision'] += 1
        self.replays[replay_key] = (frozen, copy.deepcopy(project))
        if self.lose_commit and endpoint == 'commit' and payload.get('versions'):
            self.lose_commit = False
            raise cloud_sync.CloudError('响应丢失', 503)
        return copy.deepcopy(project)


class CloudSyncTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name) / 'library'
        self.root.mkdir()
        self.root_patch = patch.object(library, 'LIBRARY_ROOT', self.root)
        self.root_patch.start()
        self.addCleanup(self.root_patch.stop)
        self.peer = Peer()
        self.client = cloud_sync.CloudSync(transport=self.peer)
        self.client.start = lambda: None
        self.addCleanup(self.client.stop)
        self.login()
        self.project = library.ensure({'platform': 'youtube', 'video_id': 'cloud-test1',
                                       'title': '测试课程', 'source_url': 'https://www.youtube.com/watch?v=cloud-test1'})

    def login(self, login='a@example.com'):
        self.client.login('https://cloud.example.com', login, 'never-persist-this-password')
        self.client.run_once()

    def add(self, data=b'original text', relative='subtitles/a.srt', kind='subtitle', details=None):
        source = Path(self.temporary.name) / 'source'
        source.write_bytes(data)
        return library.import_file(self.project['id'], source, relative, kind, details)

    def drain(self):
        for _ in range(30):
            if not self.client.run_once():
                return
        self.fail('unexpected non-terminating queue')

    def remote(self):
        return self.peer.projects[(self.client.auth['owner'], self.project['id'])]

    def test_server_allows_http_and_rejects_credentials_paths_and_other_schemes(self):
        self.assertEqual(cloud_sync.normalize_server('http://cloud.example.com'), 'http://cloud.example.com')
        self.assertEqual(cloud_sync.normalize_server('http://203.0.113.10:8090/'), 'http://203.0.113.10:8090')
        for value in ('https://u:p@cloud.example.com', 'https://cloud.example.com/path',
                      'https://cloud.example.com?token=secret', 'file:///etc/passwd', 'cloud.example.com',
                      'http:///nohost', 'https://cloud.example.com#frag'):
            with self.subTest(value=value), self.assertRaises(ValueError):
                cloud_sync.normalize_server(value)
        self.assertEqual(cloud_sync.normalize_server('http://127.0.0.1:8888/'), 'http://127.0.0.1:8888')
        self.assertEqual(cloud_sync.normalize_server('http://[::1]:8888'), 'http://[::1]:8888')

    def test_login_by_user_name_and_wrong_password_message(self):
        self.client.login('http://cloud.example.com', 'Celyn', 'never-persist-this-password')
        self.assertEqual(self.client.session()['login'], 'Celyn')

        def reject(*args):
            raise cloud_sync.CloudError('invalid_credentials', 401)

        self.client.transport = reject
        with self.assertRaisesRegex(cloud_sync.CloudError, '用户名或密码不正确'):
            self.client.login('http://cloud.example.com', 'Celyn', 'wrong-password')

    def test_session_and_restart_never_expose_or_restore_token(self):
        self.assertNotIn('token', json.dumps(self.client.session()))
        self.assertFalse(cloud_sync.CloudSync(self.root, self.peer).session()['logged_in'])
        for path in self.client.state_dir.glob('queue.sqlite3*'):
            raw = path.read_bytes()
            self.assertNotIn(b'private-token-', raw)
            self.assertNotIn(b'never-persist-this-password', raw)

    def test_allowlist_excludes_media_configuration_and_metadata_secrets(self):
        self.add()
        self.add(b'video bytes', 'video/input.mp4', 'video')
        self.add(b'audio bytes', 'audio/input.wav', 'audio')
        self.add(b'{"api_key":"sensitive"}', 'config/settings.json', 'config')
        self.add(b'report', 'analysis/report.md', 'analysis', {'prompt': 'SECRET', 'api_key': 'SECRET',
                 'agent': 'SECRET', 'transcript_resource': 'subtitles/a.srt', 'question': '问题', 'model': 'safe'})
        snap = materials.snapshot(self.project['id'])
        public = json.dumps([materials.portable_version(v) for v in snap['versions']], ensure_ascii=False)
        self.assertEqual({v['kind'] for v in snap['versions']}, {'subtitle', 'analysis'})
        for forbidden in ('SECRET', 'sensitive', 'settings.json', 'input.mp4', str(self.root)):
            self.assertNotIn(forbidden, public)
        analysis = next(v for v in snap['versions'] if v['kind'] == 'analysis')
        self.assertNotIn('transcript_resource', analysis['details'])
        self.assertEqual(analysis['details']['links'][0]['role'], 'transcript_resource')

    def test_queued_metadata_changes_create_distinct_immutable_versions(self):
        subtitle = self.add()
        report = self.add(b'report', 'analysis/report.md', 'analysis', {'transcript_resource': subtitle})
        first = materials.snapshot(self.project['id'])
        self.client.upload(self.project['id'])
        library.update_details(self.project['id'], subtitle, {'label': 'updated label'})
        second = materials.snapshot(self.project['id'])
        before = {v['kind']: v for v in first['versions']}
        after = {v['kind']: v for v in second['versions']}
        for kind in ('subtitle', 'analysis'):
            self.assertNotEqual(before[kind]['version_id'], after[kind]['version_id'])
        self.assertEqual(after['analysis']['details']['links'][0]['version_id'], after['subtitle']['version_id'])
        self.client.upload(self.project['id'])
        self.drain()
        self.assertTrue(all(t['status'] == 'completed' for t in self.client.transfers()['transfers']))
        self.assertEqual([len(m['versions']) for m in self.remote()['materials']], [2, 2])

    def test_cyclic_native_references_fail_before_upload(self):
        first = self.add(b'one', 'analysis/one.md', 'analysis')
        second = self.add(b'two', 'analysis/two.md', 'analysis', {'source_resource': first})
        library.update_details(self.project['id'], first, {'source_resource': second})
        with self.assertRaisesRegex(ValueError, '循环'):
            materials.snapshot(self.project['id'])

    def test_details_limit_uses_server_ascii_encoding(self):
        from video_parser.cloud.store import canonical
        details = {'question': '问' * 4000, 'frames': [
            {'material_id': str(i).zfill(128), 'version_id': 'v' * 128, 'time': i} for i in range(150)]}
        self.assertLess(len(json.dumps(details, ensure_ascii=False).encode('utf-8')), 65536)
        self.assertGreater(len(canonical(details).encode('utf-8')), 65536)
        self.add(b'report', 'analysis/report.md', 'analysis')
        # Preserved cloud descriptors must receive the same preflight as new ones.
        project = library.get(self.project['id'])
        relative = next(iter(project['resources']))
        info = project['resources'][relative]
        library.update_details(project['id'], relative, {'cloud_portable': {
            'material_id': 'm', 'version_id': 'v', 'kind': 'analysis', 'sha256': info['sha256'],
            'size': info['size_bytes'], 'mime': 'text/markdown', 'details': details}})
        with self.assertRaisesRegex(ValueError, '超过云端限制'):
            materials.snapshot(project['id'])

    def test_registered_path_escape_is_rejected(self):
        self.add()
        project = library.get(self.project['id'])
        project['resources']['subtitles/a.srt']['storage_path'] = '../../outside.srt'
        library.atomic_json(project['directory'] / 'project.json', {k: v for k, v in project.items() if k != 'directory'})
        with self.assertRaises(ValueError):
            materials.snapshot(project['id'])

    def test_snapshot_is_immutable_after_local_file_changes(self):
        name = self.add(b'old')
        task = self.client.upload(self.project['id'])
        library.resource_path(library.get(self.project['id']), name).write_bytes(b'new')
        with self.client.db() as db:
            snap = db.execute('SELECT data FROM snapshots WHERE task_id=?', (task['task_id'],)).fetchone()
        self.assertEqual(bytes(snap['data']), b'old')

    def test_import_never_overwrites_original_and_rejects_bad_hash(self):
        name = self.add(b'local', 'subtitle/a.srt')
        row = {'version_id': 'version1', 'sha256': materials.sha(b'cloud'), 'size': 5,
               'mime': 'application/x-subrip', 'details': {'filename': 'a.srt'}}
        target = materials.import_version(self.project['id'], {'material_id': 'material1', 'kind': 'subtitle'}, row, b'cloud', 'scope')
        self.assertEqual(library.resource_path(library.get(self.project['id']), name).read_bytes(), b'local')
        self.assertEqual(library.resource_path(library.get(self.project['id']), target).read_bytes(), b'cloud')
        with self.assertRaises(ValueError):
            materials.import_version(self.project['id'], {'material_id': 'material1', 'kind': 'subtitle'}, row, b'wrong', 'scope')

    def test_default_identity_reused_and_independent_upload_explicit(self):
        self.add()
        self.client.upload(self.project['id'])
        self.drain()
        self.assertEqual(self.client._local_mapping(self.client.auth, self.project['id'])['cloud_id'], self.project['id'])
        self.client.upload(self.project['id'], independent=True)
        self.drain()
        self.assertEqual(len(self.peer.projects), 2)
        self.assertEqual(self.client._local_mapping(self.client.auth, self.project['id'])['cloud_id'], self.project['id'])

    def test_lost_commit_response_replays_same_operation_once(self):
        self.add()
        task = self.client.upload(self.project['id'])
        self.peer.lose_commit = True
        self.client.run_once()
        revision = self.remote()['revision']
        self.client.task_control(task['task_id'])
        self.drain()
        self.assertEqual(self.remote()['revision'], revision)
        self.assertEqual(len(self.remote()['materials'][0]['versions']), 1)

    def test_tasks_and_downloads_are_scoped_to_owner(self):
        self.add()
        task = self.client.upload(self.project['id'])
        oldscope = self.client.auth['scope']
        self.login('b@example.com')
        self.assertNotIn(task['task_id'], [t['id'] for t in self.client.transfers()['transfers']])
        with self.assertRaises(cloud_sync.CloudError):
            self.client.task_control(task['task_id'])
        self.assertNotEqual(oldscope, self.client.auth['scope'])
        self.assertFalse(self.peer.projects)

    def test_failure_uses_backoff_and_cancel_is_durable(self):
        task = self.client.upload(self.project['id'])
        original = self.client.transport
        self.client.transport = lambda *args: (_ for _ in ()).throw(cloud_sync.CloudError('离线', 503))
        self.client.run_once()
        with self.client.db() as db:
            row = db.execute('SELECT * FROM tasks WHERE id=?', (task['task_id'],)).fetchone()
        self.assertEqual(row['status'], 'retry')
        self.assertGreater(row['next_at'], time.time())
        self.client.task_control(task['task_id'], True)
        self.client.transport = original
        self.assertFalse(self.client.run_once())
        self.assertEqual(self.client.transfers()['transfers'][0]['status'], 'cancelled')

    def test_metadata_remote_refresh_does_not_overwrite_dirty_local(self):
        self.add()
        self.client.upload(self.project['id'])
        self.drain()
        local = library.get(self.project['id'])
        local['title'] = '本机编辑'
        library.atomic_json(local['directory'] / 'project.json', {k: v for k, v in local.items() if k != 'directory'})
        remote = copy.deepcopy(self.remote())
        remote['metadata']['title'] = '另一设备编辑'
        self.client._pull(self.client.auth, remote)
        refreshed = library.get(local['id'])
        self.assertEqual(refreshed['title'], '本机编辑')
        self.assertEqual(refreshed['cloud_conflicts']['title']['cloud'], '另一设备编辑')

    def test_unmodified_metadata_follows_cloud_without_false_dirty_upload(self):
        self.add()
        self.client.upload(self.project['id'])
        self.drain()
        remote = copy.deepcopy(self.remote())
        remote['metadata'].update(title='云端重命名', learned=True)
        self.client._pull(self.client.auth, remote)
        self.assertEqual(library.get(self.project['id'])['title'], '云端重命名')
        task = self.client.upload(self.project['id'])
        with self.client.db() as db:
            payload = json.loads(db.execute('SELECT payload FROM tasks WHERE id=?', (task['task_id'],)).fetchone()['payload'])
        self.assertNotIn('title', payload['metadata_patch'])

    def test_lazy_image_has_no_fake_file_and_roundtrip_keeps_immutable_details(self):
        png = b'\x89PNG\r\n\x1a\n' + b'image'
        self.add(png, 'frames/batch/001.png', 'frame')
        self.add(b'report', 'analysis/report.md', 'analysis', {'frames': [{'path': 'frames/batch/001.png', 'time': 1.5}]})
        original = materials.snapshot(self.project['id'])
        self.client.upload(self.project['id'])
        self.drain()
        remote = copy.deepcopy(self.remote())
        # Simulate a fresh, separate local archive without reusing the native mapping.
        other_id = materials.sha(b'fresh-device')[:32]
        with self.client.db() as db:
            db.execute('UPDATE projects SET local_id=? WHERE scope=? AND cloud_id=?', (other_id, self.client.auth['scope'], remote['project_id']))
        self.client._pull(self.client.auth, remote)
        local = library.get(other_id)
        image_name, image = next((n, r) for n, r in local['resources'].items() if r['kind'] == 'frame')
        self.assertTrue(image['cloud_missing'])
        self.assertFalse(library.resource_path(local, image_name).exists())
        exported = materials.snapshot(other_id)
        report = next(v for v in exported['versions'] if v['kind'] == 'analysis')
        expected = next(materials.portable_version(v) for v in original['versions'] if v['kind'] == 'analysis')
        self.assertEqual(materials.portable_version(report), expected)
        self.client._pull(self.client.auth, remote, full=True)
        self.assertEqual(library.resource_path(library.get(other_id), image_name).read_bytes(), png)

    def test_explicit_selection_changes_native_current_without_removing_versions(self):
        self.add(b'old')
        self.client.upload(self.project['id'])
        self.drain()
        remote = copy.deepcopy(self.remote())
        material = remote['materials'][0]
        old = material['versions'][0]
        new = {**old, 'version_id': 'new-version', 'sha256': materials.sha(b'new'), 'size': 3}
        material['versions'].append(new)
        material['current_version_id'] = new['version_id']
        self.peer.blobs[(self.client.auth['owner'], remote['project_id'], new['sha256'])] = b'new'
        self.client._pull(self.client.auth, remote)
        self.assertEqual(library.find(self.project['id'], 'subtitle')[1].read_bytes(), b'old')
        self.client._pull(self.client.auth, remote, True, (material['material_id'], new['version_id']), select=True)
        self.assertEqual(library.find(self.project['id'], 'subtitle')[1].read_bytes(), b'new')
        self.assertGreaterEqual(len(library.get(self.project['id'])['resources']), 2)

    def test_account_change_during_download_prevents_publication(self):
        auth = dict(self.client.auth)
        original = self.client.transport
        def changing(*args):
            reply = original(*args)
            self.client.auth = None
            return reply
        self.client.transport = changing
        with self.assertRaises(cloud_sync.CloudError):
            self.client.request(auth, 'GET', '/v1/projects?deleted=false')

    def test_slow_network_does_not_block_session_or_cancellation(self):
        task = self.client.upload(self.project['id'])
        entered, release = threading.Event(), threading.Event()
        original = self.client.transport
        def slow(*args):
            entered.set()
            release.wait(2)
            return original(*args)
        self.client.transport = slow
        worker = threading.Thread(target=self.client.run_once)
        worker.start()
        try:
            self.assertTrue(entered.wait(1))
            self.assertTrue(self.client.session()['logged_in'])
            self.client.task_control(task['task_id'], True)
        finally:
            release.set()
            worker.join(3)
        self.assertFalse(worker.is_alive())
        self.assertEqual(self.client.transfers()['transfers'][0]['status'], 'cancelled')

    def test_metadata_conflict_is_sticky_and_cloud_resolution_is_explicit(self):
        self.add()
        self.client.upload(self.project['id'])
        self.drain()
        local = library.get(self.project['id'])
        local['title'] = '保留本机名称'
        library.atomic_json(local['directory'] / 'project.json', {k: v for k, v in local.items() if k != 'directory'})
        remote = self.remote()
        remote['metadata']['title'] = '云端名称'
        remote['revision'] += 1
        self.client._pull(self.client.auth, copy.deepcopy(remote))
        self.client._pull(self.client.auth, copy.deepcopy(remote))
        self.assertIn('title', library.get(local['id'])['cloud_conflicts'])
        self.client.resolve_metadata(local['id'], 'title', 'cloud')
        self.drain()
        local = library.get(local['id'])
        self.assertEqual(local['title'], '云端名称')
        self.assertNotIn('title', local['cloud_conflicts'])

    def test_native_same_bytes_reused_and_roundtrip_descriptor_stable(self):
        self.add(b'one', 'subtitle/a.srt')
        before = materials.snapshot(self.project['id'])
        self.client.upload(self.project['id'])
        self.drain()
        self.assertEqual(len(library.get(self.project['id'])['resources']), 1)
        self.assertEqual([materials.portable_version(v) for v in materials.snapshot(self.project['id'])['versions']],
                         [materials.portable_version(v) for v in before['versions']])

    def test_distinct_versions_same_filename_keep_cloud_current_on_fresh_import(self):
        self.add(b'old', 'subtitle/a.srt')
        self.client.upload(self.project['id'])
        self.drain()
        remote = copy.deepcopy(self.remote())
        material = remote['materials'][0]
        new = {**material['versions'][0], 'version_id': 'version-two', 'sha256': materials.sha(b'new'), 'size': 3}
        material['versions'].append(new)
        material['current_version_id'] = new['version_id']
        self.peer.blobs[(self.client.auth['owner'], remote['project_id'], new['sha256'])] = b'new'
        other = materials.sha(b'another-device')[:32]
        with self.client.db() as db:
            db.execute('UPDATE projects SET local_id=? WHERE scope=? AND cloud_id=?', (other, self.client.auth['scope'], remote['project_id']))
        self.client._pull(self.client.auth, remote)
        self.assertEqual(library.find(other, 'subtitle')[1].read_bytes(), b'new')
        exported = [materials.portable_version(v) for v in materials.snapshot(other)['versions']]
        original = [{'material_id': material['material_id'], 'kind': material['kind'], **v} for v in material['versions']]
        self.assertCountEqual(exported, original)

    def test_root_switch_after_response_prevents_local_publication(self):
        self.add()
        self.client.upload(self.project['id'])
        self.drain()
        remote = copy.deepcopy(self.remote())
        material = remote['materials'][0]
        version = {**material['versions'][0], 'version_id': 'root-race', 'sha256': materials.sha(b'race'), 'size': 4}
        material['versions'].append(version)
        self.peer.blobs[(self.client.auth['owner'], remote['project_id'], version['sha256'])] = b'race'
        original = self.client.transport
        other_root = Path(self.temporary.name) / 'other-library'
        other_root.mkdir()
        def switch(*args):
            result = original(*args)
            if '/blobs/' in args[2]:
                library.LIBRARY_ROOT = other_root
            return result
        self.client.transport = switch
        try:
            with self.assertRaises(cloud_sync.CloudError):
                self.client._pull(self.client.auth, remote, True, (material['material_id'], version['version_id']))
            self.assertFalse(list(other_root.rglob('*.srt')))
        finally:
            library.LIBRARY_ROOT = self.root

    def test_association_rejects_different_source_without_changing_mapping(self):
        self.add()
        self.client.upload(self.project['id'])
        self.drain()
        foreign = copy.deepcopy(self.remote())
        foreign['project_id'] = 'unrelated'
        foreign['metadata']['source_key'] = 'youtube:unrelated'
        self.peer.projects[(self.client.auth['owner'], 'unrelated')] = foreign
        task = self.client.associate(self.project['id'], 'unrelated')
        self.drain()
        row = next(t for t in self.client.transfers()['transfers'] if t['id'] == task['task_id'])
        self.assertEqual(row['status'], 'conflict')
        self.assertEqual(self.client._local_mapping(self.client.auth, self.project['id'])['cloud_id'], self.project['id'])

    def test_tombstone_disables_sync_without_deleting_local_files(self):
        name = self.add()
        self.client.upload(self.project['id'])
        self.drain()
        self.client.sync_mode(self.project['id'], True)
        self.client.action('delete', self.project['id'])
        self.drain()
        self.client._pull(self.client.auth, copy.deepcopy(self.remote()))
        self.assertFalse(self.client._local_mapping(self.client.auth, self.project['id'])['enabled'])
        self.assertTrue(library.resource_path(library.get(self.project['id']), name).is_file())
        self.client.action('restore', self.project['id'])
        self.drain()
        self.assertIsNone(self.remote()['deleted_at'])

    def test_identity_query_preserved_but_auth_tracking_removed(self):
        project = {**self.project, 'source_url': 'https://www.youtube.com/watch?v=abc&token=SECRET#frag'}
        self.assertEqual(materials.project_metadata(project)['source_url'], 'https://www.youtube.com/watch?v=abc')
        project['source_url'] = 'https://www.bilibili.com/video/BV1?p=2&cookie=SECRET'
        self.assertEqual(materials.project_metadata(project)['source_url'], 'https://www.bilibili.com/video/BV1?p=2')

    def test_pasted_images_only_explicit_references_exported(self):
        png = b'\x89PNG\r\n\x1a\n' + b'attachment'
        name = materials.sha(png) + '.png'
        directory = library.get(self.project['id'])['directory'] / 'attachments'
        directory.mkdir()
        (directory / name).write_bytes(png)
        (directory / 'private-secret.txt').write_text('do not export', encoding='utf-8')
        self.add(b'answer', 'conversation-message/msg.md', 'conversation-message', {'images': [name], 'question': '问题'})
        versions = materials.snapshot(self.project['id'])['versions']
        image = next(v for v in versions if v['kind'] == 'image')
        message = next(v for v in versions if v['kind'] == 'conversation-message')
        self.assertEqual(image['_data'], png)
        self.assertEqual(message['details']['images'][0]['material_id'], image['material_id'])
        self.assertNotIn('private-secret', json.dumps([materials.portable_version(v) for v in versions]))

    def test_conversation_header_drops_local_config_and_keeps_history(self):
        header = {'messages': [{'id': 'm1', 'question': '你好', 'answer': '回答', 'images': [],
                                'agent': {'api_key': 'PRIVATE'}, 'context': {'path': 'C:/secret'}}],
                  'credentials': 'PRIVATE'}
        self.add(json.dumps(header).encode(), 'conversations/chat.json', 'conversation')
        row = materials.snapshot(self.project['id'])['versions'][0]
        body = json.loads(row['_data'])
        self.assertEqual(body['messages'][0]['answer'], '回答')
        self.assertNotIn(b'PRIVATE', row['_data'])
        self.assertNotIn(b'C:/secret', row['_data'])

    def test_restored_default_project_reused_by_native_ensure(self):
        self.add()
        self.client.upload(self.project['id'])
        self.drain()
        other_root = Path(self.temporary.name) / 'second-device'
        other_root.mkdir()
        with patch.object(library, 'LIBRARY_ROOT', other_root):
            fresh = cloud_sync.CloudSync(transport=self.peer)
            fresh.start = lambda: None
            try:
                with patch.object(cloud_sync, '_client', fresh):
                    fresh.login('https://cloud.example.com', 'a@example.com', 'password')
                    fresh.run_once()
                    restored = fresh._mapping(fresh.auth, self.project['id'])['local_id']
                    self.assertNotEqual(restored, self.project['id'])
                    self.assertEqual(cloud_sync.reusable_project(self.project['id'], self.project['key']), restored)
                    ensured = library.ensure({'platform': 'youtube', 'video_id': 'cloud-test1',
                        'source_url': 'https://www.youtube.com/watch?v=cloud-test1', 'title': '再次下载'})
                    self.assertEqual(ensured['id'], restored)
                    self.assertEqual(len(list(library.manifests())), 1)
                    self.assertTrue(ensured['resources'])
                    fresh.logout()
                    self.assertIsNone(cloud_sync.reusable_project(self.project['id'], self.project['key']))
            finally:
                fresh.stop()

    def test_batched_upload_persists_replay_and_respects_server_limits(self):
        self.add()
        snap = materials.snapshot(self.project['id'])
        seed = snap['versions'][0]
        rows = [{**seed, 'material_id': materials.sha(('slot' + str(i)).encode()),
                 'version_id': materials.sha(('version' + str(i)).encode())} for i in range(201)]
        with patch.object(materials, 'snapshot', return_value={**snap, 'versions': rows}):
            task = self.client.upload(self.project['id'])
        self.peer.lose_commit = True
        with patch.object(self.client, '_pull'):
            self.client.run_once()
            self.client.task_control(task['task_id'])
            self.drain()
        commits = [call[2] for call in self.peer.calls if call[0] == 'POST' and call[2] and call[2].get('versions')]
        self.assertEqual([len(body['versions']) for body in commits], [200, 200, 1])
        self.assertEqual(commits[0], commits[1])
        self.assertEqual(len(self.remote()['materials']), 201)
        self.assertEqual(self.client.transfers()['transfers'][0]['status'], 'completed')
        large = [{'details': {'question': 'x' * 4000}, 'version_id': str(i)} for i in range(200)]
        batches = cloud_sync.version_batches(large)
        self.assertEqual(sum(map(len, batches)), 200)
        self.assertTrue(all(len(json.dumps(batch).encode()) <= 800000 for batch in batches))

    def test_poll_isolates_damaged_project_and_still_refreshes(self):
        other = library.ensure({'platform': 'youtube', 'video_id': 'healthy1234', 'title': '健康项目',
                                'source_url': 'https://www.youtube.com/watch?v=healthy1234'})
        with self.client.db() as db:
            for project in (self.project, other):
                db.execute('INSERT INTO projects(scope,cloud_id,local_id,enabled) VALUES(?,?,?,1)',
                           (self.client.auth['scope'], project['id'], project['id']))
        snapshot = materials.snapshot

        def read_snapshot(project_id):
            if project_id == self.project['id']:
                raise ValueError('damaged project')
            return snapshot(project_id)

        with (
            patch.object(materials, 'snapshot', side_effect=read_snapshot),
            patch.object(self.client, 'upload') as upload,
        ):
            with self.assertRaisesRegex(ValueError, 'damaged project'):
                self.client.poll()
        upload.assert_called_once_with(other['id'])
        self.assertTrue(any(task['action'] == 'refresh' and task['status'] == 'queued'
                            for task in self.client.transfers()['transfers']))

    def test_poll_failure_does_not_starve_queued_work(self):
        self.client.last_poll = 0
        # Stop after one loop even on regression, so a failing assertion cannot hang.
        with patch.object(cloud_sync.time, 'monotonic', return_value=100), \
                patch.object(self.client, 'poll', side_effect=ValueError('damaged project')) as poll, \
                patch.object(self.client, 'run_once', return_value=False) as run, \
                patch.object(self.client.stop_event, 'wait', side_effect=lambda _: self.client.stop_event.set()):
            self.client._loop()
        poll.assert_called_once()
        run.assert_called_once()
        self.assertEqual(self.client.last_poll, 100)

    def test_text_preflight_rejects_server_oversize(self):
        with self.assertRaisesRegex(ValueError, '4 MB'):
            materials.validate_bytes(b'x' * (4 * 1024 * 1024 + 1), 'subtitle', 'text/plain')

    def test_attachment_path_downloads_only_registered_missing_cloud_image(self):
        name = materials.sha(b'png') + '.png'
        local = library.get(self.project['id'])
        local['resources']['attachments/' + name] = {'kind': 'image', 'cloud_missing': True}
        library.atomic_json(local['directory'] / 'project.json', {k: v for k, v in local.items() if k != 'directory'})
        target = Path(self.temporary.name) / 'download.png'
        target.write_bytes(b'png')
        with patch.object(cloud_sync, 'ensure_resource', return_value=target) as download:
            self.assertEqual(attachments.path(local['id'], name), target)
            download.assert_called_once()
        with self.assertRaises(KeyError):
            attachments.path(local['id'], materials.sha(b'unknown') + '.png')


    def test_transfers_report_titles_timestamps_and_summaries(self):
        data = b'original text'
        self.add(data)
        self.client.upload(self.project['id'])
        self.drain()
        row = self.client.transfers()['transfers'][0]
        self.assertEqual(row['status'], 'completed')
        self.assertEqual(row['project_title'], '测试课程')
        self.assertEqual(row['attempts'], 1)
        self.assertGreater(row['created_at'], 0)
        self.assertGreaterEqual(row['started_at'], row['created_at'])
        self.assertGreaterEqual(row['finished_at'], row['started_at'])
        self.assertEqual(row['summary'], {'version_count': 1, 'byte_count': len(data), 'revision_base': 0})
        self.assertNotIn('payload', row)
        # Details must never leak local paths or the session token.
        rendered = json.dumps(row)
        self.assertNotIn(str(self.root), rendered)
        self.assertNotIn('private-token-', rendered)

    def test_transfers_report_backoff_after_a_failed_attempt(self):
        self.client.upload(self.project['id'])
        original = self.client.transport
        self.client.transport = lambda *args: (_ for _ in ()).throw(cloud_sync.CloudError('离线', 503))
        self.client.run_once()
        self.client.transport = original
        row = self.client.transfers()['transfers'][0]
        self.assertEqual(row['status'], 'retry')
        self.assertEqual(row['error'], '离线')
        self.assertEqual(row['attempts'], 1)
        self.assertGreater(row['next_at'], time.time())
        self.assertGreater(row['finished_at'], 0)

    def test_repeated_poll_refreshes_do_not_flood_the_history(self):
        self.add()
        self.client.upload(self.project['id'])
        self.drain()
        for _ in range(4):
            self.client.enqueue('refresh', '', {})
            self.drain()
        rows = self.client.transfers()['transfers']
        self.assertEqual(len([row for row in rows if row['action'] == 'refresh']), 1)
        self.assertTrue(any(row['action'] == 'upload' for row in rows))

    def test_refresh_failure_disappears_after_a_later_success(self):
        task = self.client.enqueue('refresh', '', {})
        original = self.client.transport
        self.client.transport = lambda *args: (_ for _ in ()).throw(cloud_sync.CloudError('离线', 503))
        self.client.run_once()
        self.client.transport = original
        self.client.task_control(task['task_id'])
        self.client.enqueue('refresh', '', {})
        self.drain()
        rows = self.client.transfers()['transfers']
        self.assertEqual([row['status'] for row in rows if row['action'] == 'refresh'], ['completed'])

    def test_refresh_reports_how_many_projects_it_pulled(self):
        self.add()
        self.client.upload(self.project['id'])
        self.drain()
        self.client.enqueue('refresh', '', {})
        self.drain()
        row = next(t for t in self.client.transfers()['transfers'] if t['action'] == 'refresh')
        self.assertEqual(row['status'], 'completed')
        self.assertEqual(row['summary'], {'project_count': 1})

    def test_old_queue_schema_migrates_timestamps_without_losing_rows(self):
        import sqlite3
        root = Path(self.temporary.name) / 'oldlibrary'
        root.mkdir()
        path = root / '.cloud-sync' / 'queue.sqlite3'
        path.parent.mkdir(parents=True)
        connection = sqlite3.connect(path)
        with connection:
            connection.execute("CREATE TABLE tasks (id TEXT PRIMARY KEY, scope TEXT NOT NULL, project_id TEXT NOT NULL,"
                               " action TEXT NOT NULL, payload TEXT NOT NULL, status TEXT NOT NULL,"
                               " attempts INTEGER DEFAULT 0, next_at REAL DEFAULT 0, error TEXT DEFAULT '')")
            connection.execute("INSERT INTO tasks VALUES('old','scope','p','refresh','{}','retry',2,1.0,'boom')")
        connection.close()
        with patch.object(library, 'LIBRARY_ROOT', root):
            migrated = cloud_sync.CloudSync(root, self.peer)
        with migrated.db() as db:
            row = db.execute("SELECT * FROM tasks WHERE id='old'").fetchone()
        self.assertEqual(row['error'], 'boom')
        self.assertEqual(row['status'], 'retry')
        self.assertEqual(row['created_at'], 0)
        self.assertEqual(row['started_at'], 0)
        self.assertEqual(row['finished_at'], 0)

    def test_refresh_pulls_all_remote_text_without_uploading_unselected_local(self):
        # Seed a remote project that has no local mapping and a local project that has
        # never been selected for sync. Refresh must only download the former.
        remote_id = 'remote-project-1'
        data = b'remote subtitle'
        digest = materials.sha(data)
        remote = {'project_id': remote_id, 'revision': 1,
                  'metadata': {'title': '云端未关联', 'source_key': 'youtube:remote01',
                               'platform': 'youtube', 'video_id': 'remote01',
                               'source_url': 'https://www.youtube.com/watch?v=remote01', 'learned': False},
                  'deleted_at': None,
                  'materials': [{'material_id': 'remote-material', 'kind': 'subtitle',
                                 'current_version_id': 'remote-version',
                                 'versions': [{'version_id': 'remote-version', 'sha256': digest,
                                               'size': len(data), 'mime': 'text/plain',
                                               'details': {'filename': 'remote.txt'}}]}]}
        owner = self.client.auth['owner']
        self.peer.projects[(owner, remote_id)] = remote
        self.peer.blobs[(owner, remote_id, digest)] = data
        self.add(b'local-only', 'subtitles/local.srt')
        self.client.enqueue('refresh', '', {})
        self.drain()
        imported = self.peer.projects[(owner, remote_id)]
        self.assertEqual(imported['revision'], 1)
        rows = self.client.projects()['cloud']
        mapping = next(row for row in rows if row['id'] == remote_id)
        local = library.get(mapping['local_id'])
        self.assertEqual(library.find(local['id'], 'subtitle')[1].read_bytes(), data)
        self.assertFalse(any(row['id'] == self.project['id'] for row in rows))
        # A later remote revision is discovered by the same refresh path.
        newer = b'remote subtitle v2'
        newer_digest = materials.sha(newer)
        remote['revision'] = 2
        remote['materials'][0]['versions'].append({'version_id': 'remote-version-2', 'sha256': newer_digest,
                                                    'size': len(newer), 'mime': 'text/plain',
                                                    'details': {'filename': 'remote-v2.txt'}})
        remote['materials'][0]['current_version_id'] = 'remote-version-2'
        self.peer.blobs[(owner, remote_id, newer_digest)] = newer
        self.client.enqueue('refresh', '', {})
        self.drain()
        local = library.get(mapping['local_id'])
        self.assertEqual(library.find(local['id'], 'subtitle')[1].read_bytes(), newer)
        self.assertEqual(len([info for info in local['resources'].values() if info.get('kind') == 'subtitle']), 2)


if __name__ == '__main__':
    unittest.main()
