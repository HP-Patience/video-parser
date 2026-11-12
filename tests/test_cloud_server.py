"""Cloud isolation, transaction, retry, lifecycle and upload trust-boundary checks."""
from __future__ import annotations

import asyncio
from concurrent.futures import ThreadPoolExecutor
from hashlib import sha256
import io
import json
from pathlib import Path
import tempfile
from threading import Barrier
import unittest
from uuid import uuid4

from fastapi import HTTPException
from PIL import Image

from video_parser.cloud.app import create_app
from video_parser.cloud.store import RETENTION, SESSION_TTL, Store


class CloudServerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.now = [1_800_000_000.0]
        self.store = Store(Path(self.temp.name), clock=lambda: self.now[0])
        self.alice = self.store.create_user("alice@example.test", "alice-password-123")["id"]
        self.bob = self.store.create_user("bob@example.test", "bob-password-456")["id"]
        self.project_id = "project-one"
        self.commit(self.alice, 0, metadata={"title": "Example", "source_key": "bilibili:BV123:p1", "platform": "bilibili", "video_id": "BV123", "part": 1})

    def body(self, revision, **changes):
        return {"operation_id": uuid4().hex, "base_revision": revision, **changes}

    def commit(self, owner, revision, **changes):
        return self.store.mutate(owner, self.project_id, "commit", self.body(revision, **changes))

    def upload(self, content=b"subtitle text", mime="text/plain", owner=None, store=None):
        store = store or self.store
        path = Path(self.temp.name) / (uuid4().hex + ".upload")
        path.write_bytes(content)
        digest = sha256(content).hexdigest()
        return store.publish_blob(owner or self.alice, self.project_id, digest, mime, path, len(content))

    def version(self, blob, version_id="v1", material_id="subtitle", kind="subtitle"):
        return {"material_id": material_id, "version_id": version_id, "kind": kind, **blob}

    def assert_error(self, status, code, function, *args, **kwargs):
        with self.assertRaises(HTTPException) as raised:
            function(*args, **kwargs)
        self.assertEqual(raised.exception.status_code, status)
        self.assertEqual(raised.exception.detail["code"], code)

    def test_existing_versions_do_not_count_twice_at_project_limit(self):
        blob = self.upload()
        project = self.commit(self.alice, 1, versions=[self.version(blob)])
        original = project["materials"][0]["versions"][0]
        project["materials"][0]["versions"] = [
            {**original, "version_id": f"v{index}"} for index in range(10000)
        ]
        with self.store.connect(True) as db:
            db.execute("UPDATE projects SET document=? WHERE owner=? AND id=?",
                       (json.dumps(project), self.alice, self.project_id))
        repeated = self.commit(self.alice, 2, versions=[self.version(blob, "v0")])
        self.assertEqual(len(repeated["materials"][0]["versions"]), 10000)
        self.assert_error(413, "version_limit", self.commit, self.alice, 3,
                          versions=[self.version(blob, "new-version")])
        self.assertEqual(self.store.get(self.alice, self.project_id)["revision"], 3)

    def test_accounts_sessions_are_hashed_expiring_and_revocable(self):
        self.assert_error(401, "invalid_credentials", self.store.login, "alice@example.test", "wrong", "client")
        session = self.store.login("ALICE@example.test", "alice-password-123", "client")
        token = session["token"]
        self.assertEqual(session["user"]["login"], "alice@example.test")
        with self.store.connect() as db:
            row = db.execute("SELECT digest FROM sessions").fetchone()
            user = db.execute("SELECT salt,password FROM users WHERE id=?", (self.alice,)).fetchone()
        self.assertNotEqual(row[0], token)
        self.assertEqual(row[0], sha256(token.encode()).hexdigest())
        self.assertNotIn("alice-password-123", user["password"])
        self.assertEqual(self.store.session(token)["user"]["id"], self.alice)
        self.store.logout(token)
        self.assert_error(401, "authentication_required", self.store.session, token)
        second = self.store.login("alice@example.test", "alice-password-123", "client")["token"]
        self.now[0] += SESSION_TTL
        self.assert_error(401, "authentication_required", self.store.session, second)

    def test_login_rate_limit_and_no_default_account(self):
        for _ in range(10):
            self.assert_error(401, "invalid_credentials", self.store.login, "missing@example.test", "wrong", "host")
        self.assert_error(429, "login_rate_limited", self.store.login, "missing@example.test", "wrong", "host")
        self.now[0] += 61
        self.assert_error(401, "invalid_credentials", self.store.login, "missing@example.test", "wrong", "host")

    def test_plain_user_names_are_case_insensitive_and_renameable(self):
        user = self.store.create_user("Celyn", "celyn-password-123")
        self.assertEqual(user["login"], "Celyn")
        self.assert_error(409, "login_exists", self.store.create_user, "celyn", "celyn-password-123")
        self.assert_error(422, "invalid_login", self.store.create_user, "a b", "celyn-password-123")
        token = self.store.login("celyn", "celyn-password-123", "client")["token"]
        self.assertEqual(self.store.session(token)["user"]["id"], user["id"])
        self.assertEqual(self.store.session(token)["user"]["login"], "Celyn")
        self.store.rename_user("celyn", "Celyn2")
        self.assert_error(401, "invalid_credentials", self.store.login, "celyn", "celyn-password-123", "client")
        self.assertEqual(self.store.session(token)["user"]["login"], "Celyn2")
        self.store.logout(token)

    def test_existing_email_accounts_survive_the_login_column_migration(self):
        path = Path(self.temp.name) / "legacy"
        path.mkdir()
        store = Store(path)
        store.create_user("alice@example.test", "alice-password-123")
        with store.connect(True) as db:
            db.execute("DROP INDEX IF EXISTS users_login_lower")
            db.execute("ALTER TABLE users RENAME COLUMN login TO email")
        migrated = Store(path)
        self.assertEqual(migrated.login("alice@example.test", "alice-password-123", "client")["user"]["login"], "alice@example.test")

    def test_cross_account_projects_blobs_operations_and_versions(self):
        blob = self.upload()
        self.commit(self.alice, 1, versions=[self.version(blob)])
        self.assertEqual(self.store.list(self.bob), {"projects": []})
        self.assert_error(404, "project_not_found", self.store.get, self.bob, self.project_id)
        self.assert_error(404, "project_not_found", self.store.read_blob, self.bob, self.project_id, blob["sha256"])
        self.commit(self.bob, 0)
        self.assert_error(422, "blob_not_uploaded", self.commit, self.bob, 1, versions=[self.version(blob)])
        self.assert_error(404, "blob_not_found", self.store.read_blob, self.bob, self.project_id, blob["sha256"])
        self.assertEqual(self.store.read_blob(self.alice, self.project_id, blob["sha256"])[0], b"subtitle text")

    def test_idempotency_result_survives_restart_and_rejects_changed_request(self):
        operation = self.body(1, metadata={"title": "Updated"})
        original = self.store.mutate(self.alice, self.project_id, "commit", operation)
        restarted = Store(Path(self.temp.name), clock=lambda: self.now[0])
        self.assertEqual(restarted.mutate(self.alice, self.project_id, "commit", operation), original)
        operation["metadata"]["title"] = "Different"
        self.assert_error(409, "operation_id_reused", restarted.mutate, self.alice, self.project_id, "commit", operation)
        self.assertEqual(self.store.get(self.alice, self.project_id)["revision"], 2)

    def test_three_concurrent_devices_cas_rebase_preserves_each_version(self):
        blobs = [self.upload(f"device {index}".encode()) for index in range(3)]
        stores = [Store(Path(self.temp.name), clock=lambda: self.now[0]) for _ in range(3)]
        gate = Barrier(3)
        bodies = [self.body(1, versions=[self.version(blob, f"v{index}")]) for index, blob in enumerate(blobs)]

        def race(index):
            gate.wait()
            try:
                return stores[index].mutate(self.alice, self.project_id, "commit", bodies[index])
            except HTTPException as error:
                return error

        with ThreadPoolExecutor(max_workers=3) as pool:
            results = list(pool.map(race, range(3)))
        winners = [result for result in results if isinstance(result, dict)]
        self.assertEqual(len(winners), 1)
        initial_selection = winners[0]["materials"][0]["current_version_id"]
        for index, result in enumerate(results):
            if isinstance(result, HTTPException):
                self.assertEqual(result.detail["code"], "revision_conflict")
                revision = self.store.get(self.alice, self.project_id)["revision"]
                bodies[index]["base_revision"] = revision
                self.store.mutate(self.alice, self.project_id, "commit", bodies[index])
        project = self.store.get(self.alice, self.project_id)
        self.assertEqual(project["revision"], 4)
        self.assertEqual(len(project["materials"][0]["versions"]), 3)
        self.assertEqual(project["materials"][0]["current_version_id"], initial_selection)
        for blob in blobs:
            self.assertTrue(self.store.read_blob(self.alice, self.project_id, blob["sha256"])[0].startswith(b"device"))

    def test_concurrent_same_operation_commits_once(self):
        body = self.body(1, metadata={"learned": True})
        gate = Barrier(3)
        def race(_):
            gate.wait()
            return self.store.mutate(self.alice, self.project_id, "commit", body)
        with ThreadPoolExecutor(max_workers=3) as pool:
            results = list(pool.map(race, range(3)))
        self.assertEqual(results[0], results[1])
        self.assertEqual(results[1], results[2])
        self.assertEqual(self.store.get(self.alice, self.project_id)["revision"], 2)

    def test_version_immutable_and_current_requires_explicit_selection(self):
        first = self.upload(b"first")
        second = self.upload(b"second")
        details = {"filename": "subtitle.srt", "language": "en", "links": [], "template": {"id": "plain", "name": "Plain"}}
        version = {**self.version(first), "details": details}
        self.commit(self.alice, 1, versions=[version])
        project = self.commit(self.alice, 2, versions=[self.version(second, "v2")])
        self.assertEqual(project["materials"][0]["current_version_id"], "v1")
        self.assertEqual(project["materials"][0]["versions"][0]["details"], details)
        self.assert_error(409, "version_immutable", self.commit, self.alice, 3, versions=[self.version(second, "v1")])
        project = self.commit(self.alice, 3, selections={"subtitle": "v2"})
        self.assertEqual(project["materials"][0]["current_version_id"], "v2")
        self.assertEqual(len(project["materials"][0]["versions"]), 2)

    def test_delete_restore_retention_and_permanent_tombstone(self):
        blob = self.upload()
        self.commit(self.alice, 1, versions=[self.version(blob)])
        deleted = self.store.mutate(self.alice, self.project_id, "delete", self.body(2))
        self.assertEqual(deleted["revision"], 3)
        self.assertEqual(self.store.list(self.alice)["projects"], [])
        self.assertEqual(len(self.store.list(self.alice, True)["projects"]), 1)
        self.assert_error(410, "project_deleted", self.commit, self.alice, 2)
        self.assert_error(410, "project_deleted", self.upload)
        self.assert_error(410, "project_deleted", self.store.read_blob, self.alice, self.project_id, blob["sha256"])
        restored = self.store.mutate(self.alice, self.project_id, "restore", self.body(3))
        self.assertIsNone(restored["deleted_at"])
        self.assertEqual(self.store.read_blob(self.alice, self.project_id, blob["sha256"])[0], b"subtitle text")
        self.store.mutate(self.alice, self.project_id, "delete", self.body(4))
        self.now[0] += RETENTION
        self.assert_error(410, "retention_expired", self.store.mutate, self.alice, self.project_id, "restore", self.body(5))
        self.assertEqual(self.store.collect(), 1)
        self.assertFalse(self.store.blob_path(self.alice, self.project_id, blob["sha256"]).exists())
        self.assert_error(410, "project_deleted", self.commit, self.alice, 0)
        self.assertEqual(self.store.get(self.alice, self.project_id)["materials"], [])

    def test_upload_delete_race_cannot_resurrect_project(self):
        gate = Barrier(2)
        def upload():
            gate.wait()
            try:
                return self.upload(b"racing")
            except HTTPException as error:
                return error
        def delete():
            gate.wait()
            return self.store.mutate(self.alice, self.project_id, "delete", self.body(1))
        with ThreadPoolExecutor(max_workers=2) as pool:
            upload_future = pool.submit(upload)
            deletion = pool.submit(delete).result()
            uploaded = upload_future.result()
        self.assertIsNotNone(deletion["deleted_at"])
        if isinstance(uploaded, HTTPException):
            self.assertEqual(uploaded.detail["code"], "project_deleted")
        self.assert_error(410, "project_deleted", self.commit, self.alice, 2)

    def test_upload_validation_hash_mime_size_image_and_quota(self):
        path = Path(self.temp.name) / "bad"
        path.write_bytes(b"wrong")
        self.assert_error(422, "sha256_mismatch", self.store.publish_blob, self.alice, self.project_id, "0" * 64, "text/plain", path, 5)
        self.assert_error(422, "invalid_text", self.upload, b"\xff\x00")
        self.assert_error(422, "invalid_text", self.upload, b"{broken", "application/json")
        self.assert_error(415, "unsupported_mime", self.upload, b"video", "video/mp4")
        self.assert_error(422, "invalid_image", self.upload, b"not a png", "image/png")
        image = io.BytesIO()
        Image.new("RGB", (2, 2), "red").save(image, format="PNG")
        self.upload(image.getvalue(), "image/png")
        self.assert_error(422, "invalid_image", self.upload, image.getvalue(), "image/jpeg")
        limited = Store(Path(self.temp.name), max_blob_bytes=3, clock=lambda: self.now[0])
        self.assert_error(413, "blob_too_large", self.upload, b"four", store=limited)
        quota = Store(Path(self.temp.name), account_quota_bytes=1, clock=lambda: self.now[0])
        self.assert_error(413, "account_quota_exceeded", self.upload, b"quota", store=quota)

    def test_metadata_rejects_secrets_paths_and_nonportable_url(self):
        for metadata in ({"api_key": "secret"}, {"title": "C:\\Users\\private"}, {"source_url": "https://example.test/v?token=secret"}, {"source_url": "https://user:pass@example.test/v"}, {"title": "/home/private"}, {"learned": "true"}):
            self.assert_error(422, "invalid_fields" if "api_key" in metadata else "local_path_metadata" if "title" in metadata else "invalid_source_url" if "source_url" in metadata else "invalid_metadata", self.commit, self.alice, 1, metadata=metadata)
        blob = self.upload()
        for details in ({"api_key": "secret"}, {"filename": "../private.txt"}, {"source": "C:\\private.txt"}):
            version = {**self.version(blob), "details": details}
            with self.assertRaises(HTTPException):
                self.commit(self.alice, 1, versions=[version])
        self.assertEqual(self.store.get(self.alice, self.project_id)["revision"], 1)

    def test_portable_identity_and_rich_native_details(self):
        for url in ("https://www.bilibili.com/video/BV123?p=2", "https://www.youtube.com/watch?v=video_123"):
            current = self.store.get(self.alice, self.project_id)["revision"]
            self.commit(self.alice, current, metadata={"source_url": url, "part": 100000, "created_at": self.now[0]})
        blob = self.upload(b"AI conversation")
        details = {"filename": "conversation.md", "position": 12.5, "created_at": self.now[0], "generated_at": self.now[0], "question": "First line\nSecond line", "context_after": "Context\tkept", "template": "review", "frames": [{"material_id": "frame", "version_id": "frame-v1", "time": 12.5}], "images": [{"material_id": "image", "version_id": "image-v1"}], "links": [{"role": "source_resource", "material_id": "subtitle", "version_id": "subtitle-v1"}]}
        current = self.store.get(self.alice, self.project_id)["revision"]
        project = self.commit(self.alice, current, versions=[{**self.version(blob, kind="conversation-message"), "details": details}])
        self.assertEqual(project["materials"][0]["versions"][0]["details"], details)
        self.assert_error(422, "secret_metadata", self.commit, self.alice, project["revision"], metadata={"title": "api_key=hidden"})

    def test_duplicate_blob_deduplicates_and_immutable_mime(self):
        first = self.upload(b"same bytes")
        self.assertEqual(self.upload(b"same bytes"), first)
        with self.store.connect() as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM blobs").fetchone()[0], 1)
        self.assert_error(409, "blob_metadata_immutable", self.upload, b"same bytes", "text/markdown")

    def test_corrupt_published_blob_is_never_served_or_overwritten(self):
        blob = self.upload(b"original")
        self.commit(self.alice, 1, versions=[self.version(blob)])
        path = self.store.blob_path(self.alice, self.project_id, blob["sha256"])
        path.write_bytes(b"tampered")
        self.assert_error(500, "stored_blob_corrupt", self.store.read_blob, self.alice, self.project_id, blob["sha256"])
        self.assert_error(500, "stored_blob_corrupt", self.upload, b"original")

    def request(self, app, method, path, body=b"", token=None, mime="application/json"):
        async def run():
            messages = []
            sent = False
            headers = [(b"content-type", mime.encode()), (b"content-length", str(len(body)).encode())]
            if token:
                headers.append((b"authorization", ("Bearer " + token).encode()))
            scope = {"type": "http", "asgi": {"version": "3.0"}, "http_version": "1.1", "method": method, "scheme": "http", "path": path, "raw_path": path.encode(), "query_string": b"", "headers": headers, "client": ("127.0.0.1", 12345), "server": ("127.0.0.1", 8090), "root_path": ""}
            async def receive():
                nonlocal sent
                if not sent:
                    sent = True
                    return {"type": "http.request", "body": body, "more_body": False}
                await asyncio.Event().wait()
            async def send(message):
                messages.append(message)
            await app(scope, receive, send)
            status = next(message["status"] for message in messages if message["type"] == "http.response.start")
            content = b"".join(message.get("body", b"") for message in messages if message["type"] == "http.response.body")
            return status, content
        return asyncio.run(run())

    def test_http_auth_and_streamed_upload_contract(self):
        app = create_app(Path(self.temp.name), clock=lambda: self.now[0])
        status, _ = self.request(app, "GET", "/v1/projects")
        self.assertEqual(status, 401)
        status, content = self.request(app, "POST", "/v1/auth/login", json.dumps({"login": "alice@example.test", "password": "alice-password-123"}).encode())
        self.assertEqual(status, 200)
        token = json.loads(content)["token"]
        legacy = self.request(app, "POST", "/v1/auth/login", json.dumps({"email": "alice@example.test", "password": "alice-password-123"}).encode())
        self.assertEqual(legacy[0], 200)
        self.assertEqual(json.loads(legacy[1])["user"]["login"], "alice@example.test")
        data = b"HTTP subtitle"
        digest = sha256(data).hexdigest()
        path = f"/v1/projects/{self.project_id}/blobs/{digest}"
        self.assertEqual(self.request(app, "PUT", path, b"bad", token, "text/plain")[0], 422)
        self.assertEqual(self.request(app, "PUT", path, data, token, "text/plain")[0], 200)
        self.assertEqual(self.request(app, "GET", path, token=token)[0], 404)
        blob = {"sha256": digest, "size": len(data), "mime": "text/plain"}
        body = self.body(1, versions=[self.version(blob)])
        self.assertEqual(self.request(app, "POST", f"/v1/projects/{self.project_id}/commit", json.dumps(body).encode(), token)[0], 200)
        self.assertEqual(self.request(app, "GET", path, token=token), (200, data))
        self.assertEqual(list(self.store.temp_root.iterdir()), [])
        self.assertEqual(self.request(app, "DELETE", "/v1/auth/session", token=token)[0], 200)
        self.assertEqual(self.request(app, "GET", path, token=token)[0], 401)


if __name__ == "__main__":
    unittest.main()
