"""Loopback HTTP integration: real server, urllib client and isolated device processes."""
from __future__ import annotations

import base64
from contextlib import contextmanager
import hashlib
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

import uvicorn

from video_parser.cloud.app import create_app
from video_parser.services import cloud_sync, library

EMAIL = 'integration@example.test'
PASSWORD = 'integration-password-123'
SOURCE = {'platform': 'youtube', 'video_id': 'real-http01', 'title': 'HTTP integration',
          'source_url': 'https://www.youtube.com/watch?v=real-http01'}
PNG = base64.b64decode('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII=')


def drain(client):
    """Run the durable queue synchronously; fail on errors, including deferred retries."""
    for _ in range(100):
        if not client.run_once():
            break
    else:
        raise AssertionError('Queue did not drain')
    rows = client.transfers()['transfers']
    unfinished = [r for r in rows if r['status'] != 'completed']
    if unfinished:
        raise AssertionError('Unfinished transfers: ' + json.dumps(unfinished, ensure_ascii=True))


def make_client(url, root):
    client = cloud_sync.CloudSync(root=root)  # Default transport is the real urllib implementation.
    # Schedule deterministically in the test, keeping real HTTP and durable queue execution.
    client.start = lambda: None
    client.login(url, EMAIL, PASSWORD)
    drain(client)
    return client


def import_bytes(project_id, root, data, relative, kind):
    source = root / 'import-source'
    source.write_bytes(data)
    return library.import_file(project_id, source, relative, kind)


def remote(client, project_id):
    return client.request(client.auth, 'GET', '/v1/projects/' + project_id)


def mapped_local(client, project_id):
    rows = client.projects()['cloud']
    return library.get(next(r['local_id'] for r in rows if r['id'] == project_id))


def wait_until(predicate, timeout=20):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(0.02)
    raise AssertionError('Timed out waiting for test synchronization')


def device_upload(url, root, barrier, index):
    """One interpreter per device: library globals and sqlite queues are independent."""
    root, barrier = Path(root), Path(barrier)
    root.mkdir(parents=True, exist_ok=True)
    with patch.object(library, 'LIBRARY_ROOT', root), patch.object(cloud_sync, '_client', None):
        client = make_client(url, root)
        try:
            project = library.ensure(SOURCE)
            import_bytes(project['id'], root, ('device-' + index).encode(), 'subtitles/main.srt', 'subtitle')
            client.upload(project['id'])  # Freeze all three snapshots before releasing any upload.
            (barrier / ('ready-' + index)).write_text('ready', encoding='utf-8')
            wait_until(lambda: (barrier / 'go').exists())
            drain(client)
            (barrier / ('result-' + index)).write_text(json.dumps({'project_id': project['id']}), encoding='utf-8')
        finally:
            client.stop()


class RealCloudIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.app = create_app(self.root / 'server')
        self.app.state.store.create_user(EMAIL, PASSWORD)
        self.app.state.store.create_user('other@example.test', PASSWORD)
        # Keep the bound socket: no free-port lookup/bind race.
        self.sock = socket.socket()
        self.sock.bind(('127.0.0.1', 0))
        self.sock.listen(128)
        self.url = 'http://127.0.0.1:' + str(self.sock.getsockname()[1])
        self.server = uvicorn.Server(uvicorn.Config(self.app, log_level='error', access_log=False))
        self.thread = threading.Thread(target=self.server.run, kwargs={'sockets': [self.sock]}, daemon=True)
        self.thread.start()
        self.addCleanup(self.stop_server)
        wait_until(lambda: self.server.started)

    def stop_server(self):
        self.server.should_exit = True
        self.thread.join(timeout=10)
        self.sock.close()
        self.assertFalse(self.thread.is_alive(), 'Uvicorn did not stop')

    @contextmanager
    def device(self, name):
        root = self.root / name
        root.mkdir()
        with patch.object(library, 'LIBRARY_ROOT', root), patch.object(cloud_sync, '_client', None):
            client = make_client(self.url, root)
            try:
                yield client, root
            finally:
                client.stop()

    def test_upload_repeat_selection_new_device_lazy_images_and_logout_isolation(self):
        with self.device('first') as (first, root):
            project = library.ensure(SOURCE)
            project_id = project['id']
            import_bytes(project_id, root, b'first version', 'subtitles/main.srt', 'subtitle')
            import_bytes(project_id, root, PNG, 'images/cover.png', 'image')
            first.upload(project_id)
            drain(first)
            initial = remote(first, project_id)
            subtitle = next(m for m in initial['materials'] if m['kind'] == 'subtitle')
            mid, original = subtitle['material_id'], subtitle['current_version_id']
            image = next(m for m in initial['materials'] if m['kind'] == 'image')
            image_hash = image['versions'][0]['sha256']
            first.upload(project_id)
            drain(first)
            self.assertEqual(remote(first, project_id)['materials'], initial['materials'])
            # Simulate an editor updating the registered source on another device.
            # Native writers normally publish a new resource; this exercises the same material's history.
            local = library.get(project_id)
            path = library.resource_path(local, 'subtitles/main.srt')
            path.write_bytes(b'second version')
            with library.file_lock(local['directory'] / '.manifest.lock'):
                local = library.get(project_id)
                info = local['resources']['subtitles/main.srt']
                info.update(sha256=hashlib.sha256(b'second version').hexdigest(), size_bytes=len(b'second version'))
                local.pop('directory')
                library.atomic_json(path.parent.parent / 'project.json', local)
            first.upload(project_id)
            drain(first)
            updated = remote(first, project_id)
            subtitle = next(m for m in updated['materials'] if m['material_id'] == mid)
            self.assertEqual(len(subtitle['versions']), 2)
            chosen = next(v['version_id'] for v in subtitle['versions'] if v['version_id'] != original)
            self.assertEqual(subtitle['current_version_id'], original)
            first.action('select-version', project_id, material_id=mid, version_id=chosen)
            drain(first)
            self.assertEqual(next(m for m in remote(first, project_id)['materials'] if m['material_id'] == mid)['current_version_id'], chosen)
            token = first.auth['token']
            self.assertNotIn(token, json.dumps(first.session()))
            first.logout()
            self.assertEqual(first.session()['state'], 'logged_out')
            with self.assertRaises(cloud_sync.CloudError):
                first.projects()
            with self.assertRaises(cloud_sync.CloudError) as revoked:
                first._http({'server': self.url, 'token': token}, 'GET', '/v1/auth/session')
            self.assertEqual(revoked.exception.status, 401)

        with self.device('second') as (second, root):
            # Login alone automatically imports texts and registers honest missing images.
            restored = mapped_local(second, project_id)
            self.assertEqual(library.find(restored['id'], 'subtitle')[1].read_bytes(), b'second version')
            texts = [library.resource_path(restored, name).read_bytes() for name, info in restored['resources'].items() if info['kind'] == 'subtitle']
            self.assertCountEqual(texts, [b'first version', b'second version'])
            name, info = next((n, v) for n, v in restored['resources'].items() if v['kind'] == 'image')
            self.assertTrue(info['cloud_missing'])
            self.assertFalse(library.resource_path(restored, name).exists())
            second.action('image', project_id, material_id=image['material_id'], version_id=image['current_version_id'])
            drain(second)
            restored = library.get(restored['id'])
            downloaded = next((n, v) for n, v in restored['resources'].items() if v['kind'] == 'image' and not v.get('cloud_missing'))
            self.assertEqual(hashlib.sha256(library.resource_path(restored, downloaded[0]).read_bytes()).hexdigest(), image_hash)
            second.upload(restored['id'])
            drain(second)  # Imported descriptors must re-upload without version_immutable.
            second.action('pull', project_id, full=True)
            drain(second)
            pending = second.upload(restored['id'])
            second.logout()
            second.login(self.url, 'other@example.test', PASSWORD)
            drain(second)
            self.assertEqual(second.projects(), {'local': [], 'cloud': []})
            self.assertNotIn(pending['task_id'], [t['id'] for t in second.transfers()['transfers']])
            with self.assertRaises(cloud_sync.CloudError):
                second.upload(restored['id'])
            self.assertEqual(cloud_sync.CloudSync(root).session()['state'], 'logged_out')

    def test_three_device_processes_concurrently_append_same_material(self):
        barrier = self.root / 'barrier'
        barrier.mkdir()
        processes = []
        try:
            for index in range(3):
                env = {**os.environ, 'VIDEO_LIBRARY_ROOT': str(self.root / ('device-' + str(index)))}
                processes.append(subprocess.Popen(
                    [sys.executable, str(Path(__file__).resolve()), '--device', self.url,
                     env['VIDEO_LIBRARY_ROOT'], str(barrier), str(index)], env=env))
            wait_until(lambda: all((barrier / ('ready-' + str(i))).exists() for i in range(3)), timeout=40)
            (barrier / 'go').write_text('go', encoding='utf-8')
            for process in processes:
                self.assertEqual(process.wait(timeout=40), 0)
            project_ids = [json.loads((barrier / ('result-' + str(i))).read_text())['project_id'] for i in range(3)]
            self.assertEqual(len(set(project_ids)), 1)
            with self.device('observer') as (observer, _):
                project_id = project_ids[0]
                manifest = remote(observer, project_id)
                self.assertEqual(len(manifest['materials']), 1)
                material = manifest['materials'][0]
                self.assertEqual(len(material['versions']), 3)
                expected = {hashlib.sha256(('device-' + str(i)).encode()).hexdigest() for i in range(3)}
                self.assertEqual({v['sha256'] for v in material['versions']}, expected)
                restored = mapped_local(observer, project_id)
                self.assertEqual({library.resource_path(restored, n).read_bytes() for n in restored['resources']},
                                 {('device-' + str(i)).encode() for i in range(3)})
                current = next(v for v in material['versions'] if v['version_id'] == material['current_version_id'])
                self.assertEqual(hashlib.sha256(library.find(restored['id'], 'subtitle')[1].read_bytes()).hexdigest(), current['sha256'])
        finally:
            for process in processes:
                if process.poll() is None:
                    process.terminate()
                    process.wait(timeout=10)


if __name__ == '__main__':
    if len(sys.argv) > 1 and sys.argv[1] == '--device':
        device_upload(*sys.argv[2:])
    else:
        unittest.main()
